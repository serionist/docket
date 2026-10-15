"""The content check: names that must never appear in this public repo.

Docket is built from its maintainer's own working life, so the names of the people, customers and places in it must
not leak into code, docs, tests, commit messages or PR text. The list of those names is private and lives outside the
repo: `DOCKET_DENYLIST` names the file, else `~/.config/docket-dev/denylist.txt`. One name a line, an optional ` # why`
after it; `#` lines are comments. A name matches ignoring case, as a whole word, across a line break too.
"""

from __future__ import annotations

import bisect
import codecs
import importlib
import os
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

# Not ~/.config/docket: on Linux that is the app's own config folder.
DEFAULT_PATH = Path.home() / ".config" / "docket-dev" / "denylist.txt"


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
    for n, raw in enumerate(text.lstrip("﻿").splitlines(), start=1):
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        # The words of a name may be split by any whitespace: prose here is wrapped at 120 columns, commits at 72.
        words = r"\s+".join(re.escape(w) for w in line.split())
        pattern = re.compile(rf"(?<![\w]){words}(?![\w])", re.IGNORECASE)
        names.append(Name(n, line, pattern))
    return names


def load(path: Path) -> list[Name]:
    """The deny list from a file; a byte-order mark, which Windows editors add, is not part of the first name."""
    return parse(path.read_text(encoding="utf-8-sig"))


def scan_text(text: str, names: list[Name], where: str) -> list[Hit]:
    starts = [0, *[i + 1 for i, c in enumerate(text) if c == "\n"]]
    hits: list[Hit] = []
    seen: set[tuple[int, int]] = set()
    for name in names:
        for m in name.pattern.finditer(text):
            line = bisect.bisect_right(starts, m.start())
            if (line, name.number) not in seen:
                seen.add((line, name.number))
                hits.append(Hit(f"{where}:{line}", name))
    return sorted(hits, key=lambda h: (int(h.where.rsplit(":", 1)[1]), h.name.number))


def decode(data: bytes) -> str | None:
    """A file's text, or None for a binary file. UTF-16 text is text: it has NUL bytes but a byte-order mark."""
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", errors="replace")
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8-sig", errors="replace")


@cache
def _case_table(module: str = "re._casefix") -> dict[int, str] | None:
    """Each character `re.IGNORECASE` counts as the same as another lowercase one (dotless i and i, long s and s),
    mapped to one of them; None where this Python's `re` doesn't say, and then every name's pattern runs on every
    file."""
    try:
        extra: Any = importlib.import_module(module)._EXTRA_CASES
    except (ImportError, AttributeError):
        return None
    group: dict[int, set[int]] = {}
    for low, others in extra.items():
        members = {low, *others}
        for code in list(members):
            members |= group.get(code, set())
        for code in members:
            group[code] = members
    return {code: chr(min(members)) for code, members in group.items()}


def fold(text: str) -> str | None:
    """Text with each character folded the way `re.IGNORECASE` compares it: its simple lowercase (capital dotted I is
    the one character whose full lowercase is two), then one of each group the regex counts as equal. One character
    in, one out, so a name's regex can match only where its first word, folded, is in the folded text."""
    table = _case_table()
    return None if table is None else text.replace("\u0130", "i").lower().translate(table)


def scan_files(root: Path, files: list[str], names: list[Name]) -> list[Hit]:
    hits: list[Hit] = []
    keys = [fold(name.text.split()[0]) for name in names]
    for rel in files:
        for name in names:
            if name.pattern.search(rel):
                hits.append(Hit(f"{rel} (its path)", name))
        try:
            text = decode((root / rel).read_bytes())
        except OSError:
            continue
        if text is None:
            continue
        # One pass over the folded text decides which names could be in it; only their patterns run.
        folded = fold(text)
        maybe = [n for n, k in zip(names, keys, strict=True) if folded is None or k is None or k in folded]
        if maybe:
            hits.extend(scan_text(text, maybe, rel))
    return hits
