"""Which test files can differ by system (DR-0754, DR-0758): `tools/devkit/test-systems.json`, which the gate's
systems step checks and the shard plugin's `--select system` reads.

On every PR Windows and macOS run the files under `system` and every test marked `smoke`; the nightly runs every
file there, and Linux runs every file on every PR. Every test file is named once, under `system` with its kind and
why, or under `same`. The list is per file, so a case added to a file under `same` that depends on the system would
run on Linux alone: the step fails such a file when its code names the system, skips by it, or makes links, file
modes or file times, unless `signals` says why it runs the same everywhere.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from devkit.repo import KitError

SYSTEMS = "tools/devkit/test-systems.json"
# The marker of the smoke set: every surface once, which Windows and macOS run on every PR wherever the test lives.
SMOKE = "smoke"
# pytest's testpaths, and the files it collects there.
TEST_ROOTS = ("packages/", "tools/devkit/tests/", "examples/")
TEST_FILE = re.compile(r"(^|/)test_[^/]*\.py$")

# What a test that can differ by system shows in its code. Reading a file's time or mode isn't here: every system
# answers those the same way; setting one is, since what a system keeps of it differs.
SIGNALS: dict[str, re.Pattern[str]] = {
    "names the system": re.compile(
        r"\b(sys\.platform|os\.name|platform\.system|platform\.win32_ver|platform\.mac_ver|sys\.getwindowsversion)\b"
    ),
    "skips by system": re.compile(
        r"importorskip\(\s*[\"'](?P<what>winreg|msvcrt|_winapi|fcntl|pwd|grp|termios|resource)\b"
    ),
    "makes links": re.compile(r"\b(symlink_to|hardlink_to|os\.symlink|os\.link|mklink)\b"),
    "makes file modes": re.compile(r"\b(chmod|lchmod|umask)\b|\bS_I(READ|WRITE|EXEC|[RWX](USR|GRP|OTH)|RWX[UGO])\b"),
    "makes file times": re.compile(r"\butime\b"),
}


@dataclass(frozen=True)
class Kind:
    kind: str
    why: str


@dataclass(frozen=True)
class Systems:
    system: dict[str, Kind]
    same: tuple[str, ...]
    # A file under same whose code shows a signal, and why it runs the same everywhere all the same.
    signals: dict[str, str]


def is_test_file(path: str) -> bool:
    return path.startswith(TEST_ROOTS) and TEST_FILE.search(path) is not None


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise KitError(f"{SYSTEMS} names {key} twice")
        seen[key] = value
    return seen


def parse(text: str) -> Systems:
    """The file's three parts, each checked for its shape; a key named twice is refused, as JSON would keep the last."""
    try:
        data: Any = json.loads(text, object_pairs_hook=_unique)
    except json.JSONDecodeError as e:
        raise KitError(f"{SYSTEMS} isn't valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise KitError(f"{SYSTEMS} holds an object with system, same and signals")
    top = cast(dict[str, Any], data)
    system: Any = top.get("system", {})
    same: Any = top.get("same", [])
    signals: Any = top.get("signals", {})
    if not isinstance(system, dict) or not isinstance(same, list) or not isinstance(signals, dict):
        raise KitError(f"{SYSTEMS}: system is an object of files, same a list of files, signals an object of files")
    kinds: dict[str, Kind] = {}
    for path, entry in cast(dict[str, Any], system).items():
        fields = cast(dict[str, Any], entry) if isinstance(entry, dict) else {}
        kind, why = fields.get("kind"), fields.get("why")
        if not isinstance(kind, str) or not kind.strip() or not isinstance(why, str) or not why.strip():
            raise KitError(f"{SYSTEMS}: {path} under system needs its kind and why it can differ, as words")
        kinds[path] = Kind(kind, why)
    listed = cast(list[Any], same)
    if not all(isinstance(p, str) for p in listed):
        raise KitError(f"{SYSTEMS}: same lists files by their paths")
    whys = cast(dict[str, Any], signals)
    if not all(isinstance(w, str) and w.strip() for w in whys.values()):
        raise KitError(f"{SYSTEMS}: each file under signals says why it runs the same everywhere, as words")
    return Systems(kinds, tuple(cast(list[str], listed)), cast(dict[str, str], whys))


def load(root: Path) -> Systems:
    path = root / SYSTEMS
    if not path.is_file():
        raise KitError(f"{SYSTEMS} is missing: it names each test file, by whether it can differ by system")
    return parse(path.read_text(encoding="utf-8"))


def signals_in(text: str) -> list[str]:
    """What in a test file's code says it can differ by system: each signal's first line and what it found."""
    found: list[str] = []
    for what, pattern in SIGNALS.items():
        hit = pattern.search(text)
        if hit is not None:
            line = text.count("\n", 0, hit.start()) + 1
            found.append(f"line {line}: {what} ({hit.groupdict().get('what') or hit.group(0)})")
    return found


def problems(root: Path, files: Sequence[str]) -> list[str]:
    """The test files the list misses or names wrongly, and those under same whose code shows a signal."""
    try:
        listed = load(root)
    except KitError as e:
        return [str(e)]
    tests = {f for f in files if is_test_file(f)}
    same = set(listed.same)
    out = [f"{f}: listed under same twice" for f in sorted(same) if listed.same.count(f) > 1]
    unnamed = "put it under system with its kind and why if it can differ by system, else under same"
    out += [f"{f}: not in {SYSTEMS}; {unnamed}" for f in sorted(tests - set(listed.system) - same)]
    out += [f"{f}: in {SYSTEMS}, but no such test file" for f in sorted((set(listed.system) | same) - tests)]
    out += [f"{f}: under system and under same; it is one or the other" for f in sorted(set(listed.system) & same)]
    flagged = "put it under system with why it can differ, or say under signals why it runs the same everywhere"
    for f in sorted(same & tests):
        found = signals_in((root / f).read_text(encoding="utf-8", errors="replace"))
        if found and f not in listed.signals:
            out.append(f"{f}: {'; '.join(found)}: {flagged}")
        elif not found and f in listed.signals:
            out.append(f"{f}: under signals, but its code shows no signal now: take it out of signals")
    out += [f"{f}: under signals, which are for files under same" for f in sorted(set(listed.signals) - same)]
    return out
