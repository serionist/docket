"""Raw records: what a source gave us, kept as it came, and the version that tells one state of it from another.

The version is a hash of a canonical projection of the record, never of the whole record: a reaction, a reply count or
a vendor adding its summary later must not make a new version, and neither must a new optional field in this model or
a change in a library's JSON output. So the projection is an explicit allow-list, and the JSON it is hashed as is
written here, by hand.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RID = re.compile(r"^([a-z0-9][a-z0-9._-]*):(\S+)$")
SOURCE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
NATIVE_ID_MAX = 512
_CONTROL = re.compile("[\\x00-\\x1f\\x7f-\\x9f]")


def source_name_problem(name: str) -> str | None:
    """Why a name can't be a source's, or None. A source's name builds folder and file names (its skill's folder, its
    hold list), so it is the rid's source pattern and doesn't end in a dot, which Windows drops from a file name."""
    if not SOURCE.match(name) or name.endswith("."):
        return (
            f"{name!r} isn't a source name: lowercase letters, digits, dots, underscores and hyphens, starting with a "
            "letter or digit and not ending in a dot"
        )
    return None


def is_native_id(value: str) -> bool:
    """A source's own id, the part of a rid after `<source>:`: no whitespace or control characters, at most
    NATIVE_ID_MAX characters, and no empty part between slashes, so `<id>/` always names what is under `<id>`."""
    return (
        0 < len(value) <= NATIVE_ID_MAX
        and not any(ch.isspace() for ch in value)
        and not _CONTROL.search(value)
        and "" not in value.split("/")
    )


def owning_id(ref: str | None, source: str, ids: frozenset[str]) -> str | None:
    """The id among a source's `ids` that a rid is, or sits under: `plaud:<id>` and `plaud:<id>/summary`, never
    `plaud:<id>x`. Hold lists and release logs both cover records this way."""
    prefix = f"{source}:"
    if ref is None or not ref.startswith(prefix) or not ids:
        return None
    parts = ref[len(prefix) :].split("/")
    return next(("/".join(parts[:n]) for n in range(1, len(parts) + 1) if "/".join(parts[:n]) in ids), None)


class Attachment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    kind: str = "file"
    ref: str | None = None


class Segment(BaseModel):
    """One utterance of a transcript: where it starts and ends in whole milliseconds from the record's start, who
    spoke, and what was said. A source's mapper fills a record's segments in this shape (DR-0111)."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    speaker: str | None = None
    text: str

    @model_validator(mode="after")
    def _ordered(self) -> Segment:
        if self.end_ms < self.start_ms:
            raise ValueError(f"it ends ({self.end_ms} ms) before it starts ({self.start_ms} ms)")
        return self


class RawRecord(BaseModel):
    """One item from a source: a message, an event, a recording, a transcript, a vendor's summary."""

    model_config = ConfigDict(extra="forbid")

    rid: str
    v: str = ""
    source: str
    kind: str
    container: str | None = None
    thread: str | None = None
    ts: datetime
    author: str | None = None
    to: list[str] = Field(default_factory=list[str])
    title: str | None = None
    text: str = ""
    attachments: list[Attachment] = Field(default_factory=list[Attachment])
    segments: list[Segment] | None = None
    native: dict[str, Any] = Field(default_factory=dict[str, Any])
    fetched: datetime
    via: str
    # verbatim: as the source gave it; derived: its id built from its content; cut: verbatim, then cut by hand.
    fidelity: Literal["verbatim", "derived", "cut"] = "verbatim"
    run: str = ""
    engine: str | None = None
    lang: str | None = None

    @field_validator("source")
    @classmethod
    def _source(cls, value: str) -> str:
        if not SOURCE.match(value):
            raise ValueError("a source is lowercase letters, digits, dots, underscores and hyphens")
        return value

    @field_validator("ts", "fetched")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("a timestamp needs its time zone; a source's local time is ambiguous")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _rid(self) -> RawRecord:
        m = RID.match(self.rid)
        if not m:
            raise ValueError(f"rid {self.rid!r} must be <source>:<id> with no whitespace")
        if m.group(1) != self.source:
            raise ValueError(f"rid {self.rid!r} doesn't start with its source {self.source!r}")
        return self

    def to_json(self) -> str:
        """The record as one line of the raw log. A record without segments leaves the field out, so its line is the
        one Docket wrote before segments existed, and an older machine, which refuses fields it doesn't know, reads
        it."""
        return self.model_dump_json(exclude_none=False, exclude=None if self.segments else {"segments"})


def utc_seconds(value: datetime) -> str:
    """An instant as whole UTC seconds, so two routes that give it with or without milliseconds agree."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_projection(record: RawRecord) -> dict[str, Any]:
    """The fields that make a record's content, and nothing else; empty and absent are the same."""
    attachments = sorted(
        ({"name": a.name, "kind": a.kind, "ref": a.ref} for a in record.attachments),
        key=lambda a: (a["name"], a["kind"], a["ref"] or ""),
    )
    fields: dict[str, Any] = {
        "kind": record.kind,
        "container": record.container,
        "thread": record.thread,
        "ts": utc_seconds(record.ts),
        "author": record.author,
        "to": list(record.to),
        "title": record.title,
        "text": record.text,
        "attachments": [{k: v for k, v in a.items() if v not in (None, "")} for a in attachments],
        # Named one by one, like everything here, and kept in their order: a transcript in another order is another
        # transcript. A record without segments projects as it did before they existed.
        "segments": [
            {
                k: v
                for k, v in (("start_ms", s.start_ms), ("end_ms", s.end_ms), ("speaker", s.speaker), ("text", s.text))
                if v not in (None, "")
            }
            for s in record.segments or []
        ],
    }
    return {k: v for k, v in fields.items() if v not in (None, "", [])}


def _escape(text: str) -> str:
    out = ['"']
    for ch in text:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def canonical_json(value: Any) -> str:
    """JSON with sorted keys, no spaces and unescaped UTF-8, written here so no library upgrade can change it."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _escape(value)
    if isinstance(value, list):
        return "[" + ",".join(canonical_json(x) for x in cast(list[Any], value)) + "]"
    if isinstance(value, dict):
        items = sorted((str(k), v) for k, v in cast(dict[object, Any], value).items())
        return "{" + ",".join(f"{_escape(k)}:{canonical_json(v)}" for k, v in items) + "}"
    raise TypeError(f"a projection holds text, numbers, lists and maps, not {type(value).__name__}")


def version(record: RawRecord) -> str:
    """The same projection for every source (DR-0020): no source or mapper decides what counts as new content."""
    digest = hashlib.sha256(canonical_json(default_projection(record)).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
