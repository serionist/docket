"""The web app's two checks the gate runs without Node (DR-0705, DR-0709).

- The build stamp: the committed build's build.json holds the hash of the sources it was built from, as
  packages/docket/web/scripts/inputs.js computes it; this computes the same hash from the same list and fails when a
  source changed since the build.
- The API document: packages/docket/web/openapi.json is what the /api/web routes' models say, as
  `uv run devkit web-schema` writes it.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from devkit.repo import KitError, git

WEB = "packages/docket/web"
LIST = f"{WEB}/build-inputs.txt"
STAMP = "packages/docket/src/docket/web/app/build.json"
SCHEMA = f"{WEB}/openapi.json"
REBUILD = f"run `npm run build` in {WEB} and commit the build"
SCHEMA_CODE = "import sys; from docket.web.webapi import openapi_text; sys.stdout.write(openapi_text())"


def pathspecs(text: str) -> list[str]:
    """build-inputs.txt's pathspecs: one a line, blank lines and # comments left out."""
    return [s for s in (line.strip() for line in text.splitlines()) if s and not s.startswith("#")]


def input_files(root: Path) -> list[str]:
    """The tracked files the pathspecs match, relative to the web folder, sorted by their UTF-8 bytes."""
    web = root / WEB
    specs = pathspecs((root / LIST).read_text(encoding="utf-8"))
    out = git(web, "ls-files", "-z", "--", *specs)
    return sorted((f for f in out.split("\0") if f), key=lambda f: f.encode("utf-8"))


def inputs_hash(root: Path) -> str:
    """The hash build.json records: each file's path, its length and its bytes with CRLF made LF, in order."""
    digest = hashlib.sha256()
    web = root / WEB
    for name in input_files(root):
        data = (web / name).read_bytes().replace(b"\r\n", b"\n")
        digest.update(f"{name}\n{len(data)}\n".encode())
        digest.update(data)
    return digest.hexdigest()


def stamp_problem(root: Path) -> str | None:
    """Why the committed build doesn't match its sources, or None when it does."""
    path = root / STAMP
    if not path.is_file():
        return f"{STAMP} is missing: {REBUILD}"
    try:
        recorded = str(json.loads(path.read_text(encoding="utf-8"))["inputs"])
    except (ValueError, KeyError):
        return f"{STAMP} doesn't read: {REBUILD}"
    if recorded != inputs_hash(root):
        return f"the web app's sources changed since its build: {REBUILD}"
    return None


def schema_text(root: Path) -> str:
    """The /api/web routes' document, from the app's own code, in a process of its own."""
    proc = subprocess.run(
        [sys.executable, "-c", SCHEMA_CODE],
        cwd=root,
        env={**os.environ, "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise KitError(f"the web app's API document didn't build: {proc.stderr.strip()[-2000:]}")
    return proc.stdout.replace("\r\n", "\n")


def write_schema(root: Path) -> None:
    path = root / SCHEMA
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(schema_text(root), encoding="utf-8", newline="\n")


def schema_problem(root: Path) -> str | None:
    """Why openapi.json isn't what the routes say, or None when it is."""
    path = root / SCHEMA
    current = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.is_file() else ""
    if current != schema_text(root):
        return f"{SCHEMA} isn't what the /api/web routes say: run `uv run devkit web-schema`, then {REBUILD}"
    return None
