"""Running a source's mapper: found from vault.yaml, its path checked, started in its own process with limits.

The mapper is the consuming project's code. Its real path must sit under that repo's
`.claude/skills/sources/<source>/`, so a `.py` that arrived as data (a download, a vault file) never runs. Its process
gets Docket's own interpreter in isolated mode, an environment holding only what Python needs to start, a time limit
and an output cap. Its stderr is kept for the user who asks, never logged: a traceback can quote a payload.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import IO, Any, cast

from docket.errors import DocketError
from docket.vault.git import toplevel
from docket.vault.records import source_name_problem
from docket.vault.vault import Vault

SKILLS = (".claude", "skills", "sources")
TIME_LIMIT = 300.0
OUTPUT_CAP = 256 * 1024 * 1024
STDERR_KEEP = 64 * 1024
# What Python needs to start on each system, and nothing the service holds in its own environment.
KEEP_ENV = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LANG", "LC_ALL")
_CHUNK = 64 * 1024


class MapperFailedError(DocketError):
    """The mapper didn't give a usable answer. Its stderr rides along for `--mapper-errors`, never in the message."""

    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


def consuming_repo(vault_root: Path) -> Path:
    """The git repo that holds the vault's folder: the project whose skills and mappers the vault runs."""
    repo = toplevel(vault_root.parent)
    if repo is None:
        raise DocketError(
            f"{vault_root} isn't inside a git repo, so it has no consuming project and runs no mapper; keep the vault "
            "in the repo whose .claude/skills/sources/ holds the mappers"
        )
    return repo


def find_mapper(vault: Vault, source: str) -> Path:
    """A source's mapper, its real path checked against the folder built from the checked name."""
    problem = source_name_problem(source)
    if problem is not None:
        raise DocketError(problem)
    config = vault.config.sources.get(source)
    if config is None:
        raise DocketError(f"source {source!r} isn't in vault.yaml; add it with its sensitivity and its mapper")
    if config.mapper is None:
        raise DocketError(
            f"source {source!r} has no mapper in vault.yaml; set sources.{source}.mapper to "
            f".claude/skills/sources/{source}/mapper.py"
        )
    repo = consuming_repo(vault.root)
    inside = vault.root.resolve()
    folder = repo.joinpath(*SKILLS, source).resolve()
    path = (repo / config.mapper).resolve()
    if (
        not folder.is_relative_to(repo)
        or folder.is_relative_to(inside)
        or not path.is_relative_to(folder)
        or path == folder
        or path.is_relative_to(inside)
    ):
        raise DocketError(
            f"{source}'s mapper must be a file under .claude/skills/sources/{source}/ in {repo}, outside the vault, "
            f"after links are followed; {config.mapper} isn't"
        )
    if not path.is_file():
        raise DocketError(f"no mapper at {config.mapper} in {repo}")
    return path


def run(path: Path, payload: dict[str, Any], *, time_limit: float = TIME_LIMIT, output_cap: int = OUTPUT_CAP) -> Any:
    """Start the mapper, hand it the run, and return the JSON it writes. Every way it can fail is a
    MapperFailedError, and then nothing of the run is staged."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    env = {name: os.environ[name] for name in KEEP_ENV if name in os.environ}
    command = [sys.executable, "-I", "-X", "utf8", "-m", "docket.sources.child", str(path)]
    out, err = bytearray(), bytearray()
    over = threading.Event()
    # The context closes all three pipes however the run ends.
    with subprocess.Popen(  # noqa: S603 - Docket's own interpreter and module; find_mapper checked the path
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=path.parent, env=env
    ) as proc:

        def feed(pipe: IO[bytes]) -> None:
            # A mapper that stops early closes its end; what it didn't read doesn't matter then.
            with contextlib.suppress(OSError):
                pipe.write(data)
                pipe.close()

        def drain(pipe: IO[bytes]) -> None:
            while chunk := pipe.read(_CHUNK):
                out.extend(chunk)
                if len(out) > output_cap:
                    over.set()
                    proc.kill()
                    return

        def keep_tail(pipe: IO[bytes]) -> None:
            while chunk := pipe.read(_CHUNK):
                err.extend(chunk)
                del err[:-STDERR_KEEP]

        # All three are pipes, since PIPE was asked for each.
        pipes = cast(tuple[IO[bytes], IO[bytes], IO[bytes]], (proc.stdin, proc.stdout, proc.stderr))
        threads = [
            threading.Thread(target=target, args=(pipe,), daemon=True)
            for target, pipe in zip((feed, drain, keep_tail), pipes, strict=True)
        ]
        for thread in threads:
            thread.start()
        try:
            code = proc.wait(timeout=time_limit)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            code = None
        for thread in threads:
            thread.join()
    stderr = err.decode("utf-8", "replace")
    if code is None:
        raise MapperFailedError(f"the mapper ran past its time limit of {time_limit:g} s and was stopped", stderr)
    if over.is_set():
        raise MapperFailedError(f"the mapper's output passed the cap of {output_cap} bytes and it was stopped", stderr)
    if code != 0:
        raise MapperFailedError(f"the mapper failed (exit {code})", stderr)
    try:
        return json.loads(out.decode("utf-8"))
    except ValueError:
        raise MapperFailedError("the mapper's output isn't JSON", stderr) from None
