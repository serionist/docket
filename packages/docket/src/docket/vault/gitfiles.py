"""What git keeps as plain files in a git dir, read without starting git (DR-0528): whether HEAD is on a branch,
HEAD's commit id, and a new commit's parents.

Only a plain checkout's git dir is read, the one `home.is_git_dir` vouches for, with no `commondir` file: the same
rule `home` uses before it trusts a repo it found. Anything else answers `UNKNOWN`, and the caller asks git as before:
a git dir whose refs aren't files (reftable), a file that doesn't read as git writes it, any error reading one.
"""

from __future__ import annotations

import re
import zlib
from pathlib import Path
from typing import Final

from docket.vault.home import is_git_dir


class Unknown:
    """The files couldn't answer; ask git."""

    def __repr__(self) -> str:
        return "UNKNOWN"


UNKNOWN: Final = Unknown()
_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_REF = "ref: "
# What git writes in HEAD when the refs live in a reftable, so that an older git sees a repo it can't read.
_REFTABLE_HEAD = "refs/heads/.invalid"
_SYMREF_DEPTH = 5


def head_ref(git_dir: Path) -> str | Unknown | None:
    """The ref HEAD names, such as `refs/heads/main`; None when HEAD holds a commit id (detached)."""
    text = _head(git_dir)
    if isinstance(text, Unknown):
        return UNKNOWN
    if text.startswith(_REF):
        ref = text.removeprefix(_REF).strip()
        return UNKNOWN if not ref.startswith("refs/") or ref == _REFTABLE_HEAD else ref
    return None if _ID.match(text) else UNKNOWN


def head_id(git_dir: Path) -> str | Unknown | None:
    """HEAD's commit id; None when HEAD names a branch with no commit yet: its ref has no file, and `packed-refs` is
    absent or was read whole without it."""
    text = _head(git_dir)
    if isinstance(text, Unknown):
        return UNKNOWN
    for _ in range(_SYMREF_DEPTH):
        if _ID.match(text):
            return text
        if not text.startswith(_REF):
            return UNKNOWN
        ref = text.removeprefix(_REF).strip()
        if not ref.startswith("refs/") or ref == _REFTABLE_HEAD:
            return UNKNOWN
        loose = git_dir.joinpath(*ref.split("/"))
        try:
            if loose.is_file():
                text = loose.read_text(encoding="utf-8").strip()
                continue
            if loose.exists():
                return UNKNOWN
        except (OSError, UnicodeDecodeError):
            return UNKNOWN
        return _packed(git_dir, ref)
    return UNKNOWN


def commit_parents(git_dir: Path, commit: str) -> tuple[str, ...] | Unknown:
    """A commit's parents from its loose object, as git writes one the moment a commit is made: zlib, then `commit
    <size>`, a NUL and the headers, `tree` first and the `parent` lines after it. A packed or missing object, or one
    that doesn't read as a commit, is unknown."""
    if not _ID.match(commit) or isinstance(_head(git_dir), Unknown):
        return UNKNOWN
    try:
        data = zlib.decompress((git_dir / "objects" / commit[:2] / commit[2:]).read_bytes())
    except (OSError, zlib.error):
        return UNKNOWN
    kind, _, body = data.partition(b"\0")
    if not kind.startswith(b"commit "):
        return UNKNOWN
    parents: list[str] = []
    for line in body.split(b"\n")[1:]:
        if not line.startswith(b"parent "):
            break
        found = line.removeprefix(b"parent ").decode("ascii", "replace")
        if not _ID.match(found):
            return UNKNOWN
        parents.append(found)
    return tuple(parents)


def _head(git_dir: Path) -> str | Unknown:
    if not is_git_dir(git_dir) or (git_dir / "commondir").exists():
        return UNKNOWN
    try:
        return (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return UNKNOWN


def _packed(git_dir: Path, ref: str) -> str | Unknown | None:
    """A ref's id in `packed-refs`: None when the file is absent or read whole without it; any line that isn't one git
    writes is unknown."""
    path = git_dir / "packed-refs"
    try:
        if not path.exists():
            return None
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return UNKNOWN
    found: str | None = None
    for line in lines:
        if not line or line.startswith("#"):
            continue
        if line.startswith("^"):
            if not _ID.match(line[1:]):
                return UNKNOWN
            continue
        sha, sep, name = line.partition(" ")
        if not sep or not _ID.match(sha) or not name:
            return UNKNOWN
        if name == ref:
            found = sha
    return found
