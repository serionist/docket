"""Which ranges each stored media file was cut by (DR-0562): `state/media-cuts/<source>.jsonl`.

The media commit that stores files appends one line for each, in the same commit: schema_version 2, the rid's hash,
its file's key id (the sha256 of its age header), its ranges (none for audio stored as it was put), when and on which
machine. A file's cuts are the line whose rid and key match the file stored now, so two machines' lines for one rid
never vouch for each other's file; a schema 1 line names no key, and matches none. Times and hashes only, never
content, as the release log holds. It merges by union (the vault's `*.jsonl merge=union`); a file stored without a
line counts as cut by none. A stored file whose family's ranges have outgrown its line is not played, and is put
again, cut (DR-0562).

It is written through a temp file in Docket's private folder and a rename, never through a link, as the release log
is (DR-0200).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from docket.errors import DocketError
from docket.media import crypto
from docket.media.layout import KEY, Span
from docket.vault.home import Home
from docket.vault.records import source_name_problem
from docket.vault.staging import write_replacing

FOLDER = "state/media-cuts"
SUFFIX = ".jsonl"
HASH = re.compile(r"^[0-9a-f]{32}$")


class Line(BaseModel):
    """One stored file's cuts."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1, 2] = 2
    rid: str
    key: str | None = None
    cuts: list[Span] = Field(default_factory=list[Span])
    at: datetime
    machine: str = Field(min_length=1)

    @field_validator("rid")
    @classmethod
    def _hash(cls, value: str) -> str:
        if HASH.match(value) is None:
            raise ValueError("a rid's hash is 32 hex")
        return value

    @model_validator(mode="after")
    def _key(self) -> Line:
        if self.schema_version == 1 and self.key is not None:
            raise ValueError("a schema 1 line names no key")
        if self.schema_version == 2 and (self.key is None or KEY.match(self.key) is None):
            raise ValueError("a schema 2 line names its file's key, 64 hex")
        return self

    @field_validator("at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("at needs its time zone")
        return value.astimezone(UTC)


def key_of(path: Path) -> str | None:
    """A stored file's key id from its age header, or None when it doesn't read (an LFS pointer, a torn file)."""
    try:
        return crypto.key_id(path)
    except (OSError, DocketError):
        return None


def _linked(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def rel(source: str) -> str:
    return f"{FOLDER}/{source}{SUFFIX}"


def log_path(root: Path, source: str) -> Path:
    """A source's log, from a checked name, never through a link."""
    problem = source_name_problem(source)
    if problem is not None:
        raise DocketError(problem)
    folder = root / FOLDER
    path = folder / f"{source}{SUFFIX}"
    if _linked(folder) or _linked(path):
        raise DocketError(f"{rel(source)} or its folder is a link; the media cut log is a plain file in the vault")
    return path


def read(root: Path, source: str) -> list[Line]:
    """A source's lines; a line that isn't one is named by its number only, and stops the reader (fails closed)."""
    path = log_path(root, source)
    if not path.exists():
        return []
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise DocketError(f"{rel(source)}: can't read it ({type(e).__name__})") from None
    out: list[Line] = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            out.append(Line.model_validate_json(line))
        except ValidationError:
            raise DocketError(f"{rel(source)}: line {n} isn't a media cut line; fix or delete it") from None
    return out


def logged(root: Path, source: str) -> dict[tuple[str, str], tuple[tuple[int, int], ...]]:
    """What each stored file was cut by, by its rid's hash and its key id: the latest line for the pair. Lines that
    name no key are left out: they match no file."""
    found: dict[tuple[str, str], tuple[datetime, tuple[tuple[int, int], ...]]] = {}
    for line in read(root, source):
        if line.key is None:
            continue
        before = found.get((line.rid, line.key))
        if before is None or line.at >= before[0]:
            found[(line.rid, line.key)] = (line.at, tuple((c.start, c.end) for c in line.cuts))
    return {pair: cuts for pair, (_, cuts) in found.items()}


def cut_by(
    lines: dict[tuple[str, str], tuple[tuple[int, int], ...]], digest: str, path: Path | None
) -> tuple[tuple[int, int], ...]:
    """What the stored file at `path` was cut by: its line's ranges, matched by its key; none when no line matches, or
    its key doesn't read (an LFS pointer), so a family that needs a cut reads it as needing one."""
    key = key_of(path) if path is not None else None
    return lines.get((digest, key), ()) if key is not None else ()


def append(
    home: Home, root: Path, source: str, files: Sequence[tuple[str, str, Sequence[Span]]], machine: str
) -> bytes | None:
    """Append a line for each (rid hash, key id, cuts), and return what the log held before, None if there was none, so
    a commit that fails can put it back."""
    path = log_path(root, source)
    before = path.read_bytes() if path.exists() else None
    read(root, source)
    text = (before or b"").decode("utf-8-sig")
    if text and not text.endswith("\n"):
        text += "\n"
    at = datetime.now(UTC)
    for digest, key, cuts in files:
        text += Line(rid=digest, key=key, cuts=list(cuts), at=at, machine=machine).model_dump_json() + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_replacing(home, path, text, f"media-cuts-{source}")
    return before


def put_back(root: Path, source: str, before: bytes | None) -> None:
    """The log as it was before `append`: after a media commit that didn't land."""
    path = log_path(root, source)
    if before is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(before)
