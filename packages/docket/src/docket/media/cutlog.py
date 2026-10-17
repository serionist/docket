"""Which ranges each stored media file was cut by (DR-0562): `state/media-cuts/<source>.jsonl`.

The media commit that stores files appends one line for each, in the same commit: schema_version 1, the rid's hash,
its ranges (none for audio stored as it was put), when and on which machine. Times and hashes only, never content, as
the release log holds. It merges by union (the vault's `*.jsonl merge=union`); a file stored without a line counts as
cut by none. A stored file whose family's ranges have outgrown its line is not played, and is put again, cut
(DR-0562).

It is written through a temp file in Docket's private folder and a rename, never through a link, as the release log
is (DR-0200).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from docket.errors import DocketError
from docket.media.layout import Span
from docket.vault.home import Home
from docket.vault.records import source_name_problem
from docket.vault.staging import write_replacing

FOLDER = "state/media-cuts"
SUFFIX = ".jsonl"
HASH = re.compile(r"^[0-9a-f]{32}$")


class Line(BaseModel):
    """One stored file's cuts."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    rid: str
    cuts: list[Span] = Field(default_factory=list[Span])
    at: datetime
    machine: str = Field(min_length=1)

    @field_validator("rid")
    @classmethod
    def _hash(cls, value: str) -> str:
        if HASH.match(value) is None:
            raise ValueError("a rid's hash is 32 hex")
        return value

    @field_validator("at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("at needs its time zone")
        return value.astimezone(UTC)


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


def logged(root: Path, source: str) -> dict[str, tuple[tuple[int, int], ...]]:
    """What each rid's stored file was cut by, by the rid's hash: its latest line, by time and then by place. A rid with
    no line was stored as it was put."""
    found: dict[str, tuple[datetime, tuple[tuple[int, int], ...]]] = {}
    for line in read(root, source):
        before = found.get(line.rid)
        if before is None or line.at >= before[0]:
            found[line.rid] = (line.at, tuple((c.start, c.end) for c in line.cuts))
    return {digest: cuts for digest, (_, cuts) in found.items()}


def append(
    home: Home, root: Path, source: str, files: Sequence[tuple[str, Sequence[Span]]], machine: str
) -> bytes | None:
    """Append a line for each (rid hash, cuts), and return what the log held before, None if there was none, so a
    commit that fails can put it back."""
    path = log_path(root, source)
    before = path.read_bytes() if path.exists() else None
    read(root, source)
    text = (before or b"").decode("utf-8-sig")
    if text and not text.endswith("\n"):
        text += "\n"
    at = datetime.now(UTC)
    for digest, cuts in files:
        text += Line(rid=digest, cuts=list(cuts), at=at, machine=machine).model_dump_json() + "\n"
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
