"""Names that must never appear in this public repo.

Docket is built from its maintainer's own working life, so the people, customers and places in it must not leak into
the repo or its commit messages. The list is private and lives outside the repo: `DOCKET_DENYLIST` names the file, else
`~/.config/docket-dev/denylist.txt`. One name a line, an optional ` # why` after it; `#` lines are comments. A name
matches ignoring case, as a whole word, across a line break too. A hit names the list's line, never the name, so the
output can be shown anywhere.

    check_names.py --staged          the files staged for commit, as staged
    check_names.py --message FILE    a commit message, with tool attribution lines taken out first
    check_names.py PATH...           files and folders as they are on disk
"""

from __future__ import annotations

import codecs
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_LIST = Path.home() / ".config" / "docket-dev" / "denylist.txt"
# The maintainer's rule is no tool attribution anywhere, and some tools add these lines on their own.
ATTRIBUTION = re.compile(r"^\s*(co-authored-by:.*(claude|anthropic)|.*generated with \[?claude code).*$", re.IGNORECASE)

Names = list[tuple[int, re.Pattern[str]]]


def load_names() -> Names:
    path = Path(os.environ.get("DOCKET_DENYLIST") or DEFAULT_LIST)
    if not path.is_file():
        sys.exit(f"check_names: no deny list at {path}; copy it from another machine or set DOCKET_DENYLIST")
    names: Names = []
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        # Prose is wrapped, so the words of a name may be split by any whitespace, a line break too.
        words = r"\s+".join(re.escape(word) for word in line.split())
        names.append((number, re.compile(rf"(?<!\w){words}(?!\w)", re.IGNORECASE)))
    return names


def decode(data: bytes) -> str | None:
    """A file's text, or None for a binary file. UTF-16 text has NUL bytes but starts with a byte-order mark."""
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", errors="replace")
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8-sig", errors="replace")


def scan(label: str, text: str, names: Names) -> list[str]:
    hits = []
    for number, pattern in names:
        if pattern.search(label):
            hits.append(f"{label} (its path): deny-list entry {number}")
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            hits.append(f"{label}:{line}: deny-list entry {number}")
    return hits


def staged(names: Names) -> list[str]:
    listed = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"], capture_output=True, check=True
    ).stdout.decode("utf-8")
    hits = []
    for path in filter(None, listed.split("\0")):
        text = decode(subprocess.run(["git", "show", f":{path}"], capture_output=True, check=True).stdout)
        hits.extend(scan(path, text or "", names))
    return hits


def message(path: Path, names: Names) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if not ATTRIBUTION.match(line)]
    while kept and not kept[-1].strip():
        kept.pop()
    if kept != lines:
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    text = "\n".join(line for line in kept if not line.startswith("#"))
    return scan("commit message", text, names)


def on_disk(paths: list[str], names: Names) -> list[str]:
    hits = []
    for arg in paths:
        root = Path(arg)
        files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]
        for file in files:
            hits.extend(scan(file.as_posix(), decode(file.read_bytes()) or "", names))
    return hits


def main(argv: list[str]) -> int:
    names = load_names()
    if argv[:1] == ["--staged"]:
        hits = staged(names)
    elif argv[:1] == ["--message"] and len(argv) == 2:
        hits = message(Path(argv[1]), names)
    elif argv:
        hits = on_disk(argv, names)
    else:
        print(__doc__)
        return 2
    for hit in hits:
        print(hit, file=sys.stderr)
    if hits:
        print(f"check_names: {len(hits)} private name(s); take them out before committing", file=sys.stderr)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
