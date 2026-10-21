"""What git keeps as plain files in a git dir, read without starting git (DR-0765): whether HEAD is on a branch,
HEAD's commit id, a new commit's parents, and the index's unmerged paths.

Only a plain checkout's git dir is read, the one `home.is_git_dir` vouches for, with no `commondir` file: the same
rule `home` uses before it trusts a repo it found. Anything else answers `UNKNOWN`, and the caller asks git as before:
a git dir whose refs aren't files (reftable), a file that doesn't read as git writes it, any error reading one.
"""

from __future__ import annotations

import hashlib
import os
import re
import struct
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
# Past this size the index is left to git: version 4 compresses each name against the one before it, so finding an
# unmerged entry means reading every entry, about 0.8 µs each here, and a 4 MB index holds some 40,000 (DR-0765).
INDEX_CAP = 4 * 1024 * 1024
# An entry's fixed part before its object id: two times, then dev, ino, mode, uid, gid and size.
_STAT_BYTES = 40
_EXTENDED = 0x4000
_NAME_MASK = 0xFFF
# An extension whose signature starts with a capital letter is optional: git itself reads past one it doesn't know
# (TREE, REUC, UNTR, FSMN, EOIE, IEOT). Any other, such as a split index's `link` or a sparse one's `sdir`, changes
# what the entries mean, and git is asked.
_OPTIONAL = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ")


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


def unmerged(git_dir: Path, prefix: str) -> list[str] | Unknown:
    """The paths under `prefix` the index holds above stage 0, made relative to it and sorted: what `git ls-files -u`
    lists, run from the vault's folder. Read from the index file of versions 2 to 4, its checksum held, below
    `INDEX_CAP`; unknown for another index (GIT_INDEX_FILE), a size past the cap, a version or extension this doesn't
    read, a missing index, a repo with no commit (whose object ids' length HEAD can't tell), or any read error."""
    head = head_id(git_dir)
    if not isinstance(head, str) or os.environ.get("GIT_INDEX_FILE"):
        return UNKNOWN
    try:
        path = git_dir / "index"
        if path.stat().st_size > INDEX_CAP:
            return UNKNOWN
        data = path.read_bytes()
    except OSError:
        return UNKNOWN
    found = _stages(data, len(head) // 2)
    if found is None:
        return UNKNOWN
    want = prefix.encode("utf-8")
    names = {name[len(want) :] for name, stage in found if stage and name.startswith(want)}
    return sorted(name.decode("utf-8", errors="replace") for name in names)


def _stages(data: bytes, id_size: int) -> list[tuple[bytes, int]] | None:
    """Each entry's name and stage, in the index's order; None for anything that doesn't read as git writes it."""
    end = len(data) - id_size
    if end < 12 or data[:4] != b"DIRC":
        return None
    version, count = struct.unpack_from(">II", data, 4)
    if version not in (2, 3, 4):
        return None
    # The file ends with its own hash, all zeros where index.skipHash leaves it out.
    trailer = data[end:]
    algorithm = "sha1" if id_size == 20 else "sha256"
    if trailer != bytes(id_size) and hashlib.new(algorithm, data[:end]).digest() != trailer:
        return None
    at, flags_at, previous = 12, _STAT_BYTES + id_size, b""
    out: list[tuple[bytes, int]] = []
    try:
        for _ in range(count):
            (flags,) = struct.unpack_from(">H", data, at + flags_at)
            fixed = flags_at + 2
            if flags & _EXTENDED:
                if version == 2:
                    return None
                fixed += 2
            length = flags & _NAME_MASK
            start = at + fixed
            if version == 4:
                # The name is the previous one less its last `strip` bytes, then the bytes up to a NUL.
                strip, start = _varint(data, start)
                stop = data.index(b"\0", start)
                if strip > len(previous):
                    return None
                name = previous[: len(previous) - strip] + data[start:stop]
                at, previous = stop + 1, name
            else:
                # A name of 0xFFF bytes or more says only that; it runs to its NUL. The entry is padded with one to
                # eight NULs to a multiple of eight bytes.
                stop = data.index(b"\0", start + length) if length == _NAME_MASK else start + length
                if data[stop] != 0:
                    return None
                name = data[start:stop]
                at += (fixed + len(name) + 8) & ~7
            if length not in (_NAME_MASK, len(name)):
                return None
            out.append((name, (flags >> 12) & 3))
        while at < end:
            signature = data[at : at + 4]
            (size,) = struct.unpack_from(">I", data, at + 4)
            if len(signature) < 4 or signature[0] not in _OPTIONAL:
                return None
            at += 8 + size
    except (struct.error, ValueError, IndexError):
        return None
    return out if at == end else None


def _varint(data: bytes, at: int) -> tuple[int, int]:
    """Git's offset varint: seven bits a byte, high bit set on all but the last, each continuation adding one."""
    byte = data[at]
    at += 1
    value = byte & 0x7F
    while byte & 0x80:
        byte = data[at]
        at += 1
        value = ((value + 1) << 7) | (byte & 0x7F)
    return value, at


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
