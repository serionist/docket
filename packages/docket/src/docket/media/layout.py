"""Where media lives (DR-0547): one file per rid, waiting in staging beside its record, stored in `media/` in LFS.

| Where | Path |
|---|---|
| waiting | `staging/_media/<source>/<rid hash>.<ext>.age`, and `<rid hash>.json`, its sidecar |
| stored | `media/<source>/<yyyy>/<mm>/<rid hash>.<ext>.age`, the month fixed at the promotion that stored it |

The rid hash is staging's: 32 hex of sha256. Stored media is found by `media/<source>/*/*/<rid hash>.*.age`, any month
and any extension, so nothing computes a month to find a file. The extension comes from the file's first bytes: a cut
recording's is `webm` (DR-0560).

The sidecar's schema 2 adds the ranges the file was cut by (DR-0561); a schema 1 sidecar reads as cut by none.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from docket.errors import DocketError
from docket.vault.records import source_name_problem
from docket.vault.staging import STAGING, rid_hash

STAGED = "_media"
STORED = "media"
SUFFIX = ".age"
SIDECAR_VERSION = 2
# The types `media_put` knows by their first bytes: what the extension and a served type say, nothing more.
AUDIO = frozenset({"mp3", "m4a", "ogg", "opus", "wav", "flac", "webm", "aac"})
TYPES = {
    "mp3": "audio/mpeg",
    "aac": "audio/aac",
    "m4a": "audio/mp4",
    "ogg": "audio/ogg",
    "opus": "audio/ogg",
    "wav": "audio/wav",
    "flac": "audio/flac",
    "webm": "audio/webm",
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "bin": "application/octet-stream",
}
# How many first bytes the sniff reads.
HEAD = 64


class Span(BaseModel):
    """A stretch silenced, in milliseconds on the recording's timeline, as the transcript or the release has it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: int = Field(ge=0)
    end: int

    @model_validator(mode="after")
    def _ordered(self) -> Span:
        if self.end <= self.start:
            raise ValueError("a cut ends after it starts")
        return self


class Sidecar(BaseModel):
    """What staging keeps beside a waiting media file: its rid, which the hash can't give back, its key's id, and the
    ranges it was cut by. No content."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1, 2] = SIDECAR_VERSION
    rid: str
    key: str
    ext: str
    bytes: int
    put_at: datetime
    problem: str | None = None
    cuts: list[Span] = Field(default_factory=list[Span])

    @property
    def ranges(self) -> tuple[tuple[int, int], ...]:
        return tuple((c.start, c.end) for c in self.cuts)

    @model_validator(mode="after")
    def _version(self) -> Sidecar:
        if self.schema_version == 1 and self.cuts:
            raise ValueError("a schema 1 sidecar holds no cuts")
        return self

    @field_validator("put_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("put_at needs its time zone")
        return value.astimezone(UTC)


def sniff(head: bytes) -> str:
    """A file's extension from its first bytes, `bin` when it is none Docket knows."""
    if head.startswith(b"ID3"):
        return "mp3"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if len(head) >= 2 and head[0] == 0xFF and head[1] & 0xF6 == 0xF0:
        return "aac"
    if len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0:
        return "mp3"
    if head[4:8] == b"ftyp":
        return "m4a"
    if head.startswith(b"OggS"):
        return "opus" if b"OpusHead" in head else "ogg"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "wav"
    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "webm"
    if head.startswith(b"%PDF-"):
        return "pdf"
    return "bin"


def source_of(rid: str) -> str:
    """A rid's source, checked as every path built from it is (DR-0125)."""
    source = rid.partition(":")[0]
    problem = source_name_problem(source)
    if problem is not None:
        raise DocketError(problem)
    return source


def staged_dir(root: Path, source: str) -> Path:
    return root / STAGING / STAGED / source


def sidecar_path(root: Path, rid: str) -> Path:
    return staged_dir(root, source_of(rid)) / f"{rid_hash(rid)}.json"


def staged_file(root: Path, rid: str, ext: str) -> Path:
    return staged_dir(root, source_of(rid)) / f"{rid_hash(rid)}.{ext}{SUFFIX}"


def staged_files(root: Path, rid: str) -> list[Path]:
    """The rid's waiting media files: one, or none; more is a leftover the sweep clears."""
    folder = staged_dir(root, source_of(rid))
    return sorted(folder.glob(f"{rid_hash(rid)}.*{SUFFIX}")) if folder.is_dir() else []


def stored_files(root: Path, rid: str) -> list[Path]:
    """The rid's stored media, in any month and with any extension."""
    folder = root / STORED / source_of(rid)
    return sorted(folder.glob(f"*/*/{rid_hash(rid)}.*{SUFFIX}")) if folder.is_dir() else []


def stored_path(root: Path, rid: str, ts: datetime, ext: str) -> Path:
    """Where a promotion stores the rid's media: the month of the version in raw then, in UTC, kept for good."""
    when = ts.astimezone(UTC)
    return root / STORED / source_of(rid) / f"{when:%Y}" / f"{when:%m}" / f"{rid_hash(rid)}.{ext}{SUFFIX}"


def ext_of(path: Path) -> str:
    """The extension a media file's name carries: `<hash>.<ext>.age`."""
    return path.name.removesuffix(SUFFIX).rpartition(".")[2]


def owners(rid: str) -> list[str]:
    """The rid and each rid it sits under by nesting: `plaud:x/polished` and `plaud:x`. A drop of it reaches media put
    on any of them, and only by nesting: never through a thread or a container (DR-0547)."""
    source, _, native = rid.partition(":")
    parts = native.split("/")
    return [f"{source}:{'/'.join(parts[:n])}" for n in range(len(parts), 0, -1)]


def in_family(rid: str, other: str) -> bool:
    """Whether `other` is one of a media file's records: its rid's own record, or one nested under `<rid>/`."""
    return other == rid or other.startswith(f"{rid}/")
