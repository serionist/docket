"""The content check: names that must never appear in this public repo.

Docket is built from its maintainer's own working life, so the names of the people, customers and places in it must
not leak into code, docs, tests, commit messages or PR text. The list of those names is private and lives outside the
repo: `DOCKET_DENYLIST` names the file, else `~/.config/docket/denylist.txt`. One name a line, an optional ` # why`
after it; `#` lines are comments. A name matches ignoring case, as a whole word.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path.home() / ".config" / "docket" / "denylist.txt"


@dataclass(frozen=True)
class Name:
    number: int
    text: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class Hit:
    where: str
    name: Name

    def show(self, *, masked: bool) -> str:
        """In CI the log is public, so a hit names the list's line, never the name itself."""
        label = f"deny-list entry {self.name.number}" if masked else repr(self.name.text)
        return f"{self.where}: {label}"


def list_path() -> Path | None:
    env = os.environ.get("DOCKET_DENYLIST")
    if env:
        return Path(env)
    return DEFAULT_PATH if DEFAULT_PATH.is_file() else None


def parse(text: str) -> list[Name]:
    names: list[Name] = []
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        pattern = re.compile(rf"(?<![\w]){re.escape(line)}(?![\w])", re.IGNORECASE)
        names.append(Name(n, line, pattern))
    return names


def scan_text(text: str, names: list[Name], where: str) -> list[Hit]:
    hits: list[Hit] = []
    for n, line in enumerate(text.split("\n"), start=1):
        for name in names:
            if name.pattern.search(line):
                hits.append(Hit(f"{where}:{n}", name))
    return hits


def scan_files(root: Path, files: list[str], names: list[Name]) -> list[Hit]:
    hits: list[Hit] = []
    for rel in files:
        for name in names:
            if name.pattern.search(rel):
                hits.append(Hit(f"{rel} (its path)", name))
        try:
            data = (root / rel).read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue
        hits.extend(scan_text(data.decode("utf-8", errors="replace"), names, rel))
    return hits
