"""Raw records: what a source gave us, kept as it came, and the version that tells one state of it from another.

The version is a hash of a canonical projection of the record, never of the whole record: a reaction, a reply count or
a vendor adding its summary later must not make a new version, and neither must a new optional field in this model or
a change in a library's JSON output. So the projection is an explicit allow-list, and the JSON it is hashed as is
written here, by hand.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RID = re.compile(r"^([a-z0-9][a-z0-9._-]*):(\S+)$")
SOURCE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

Projection = Callable[["RawRecord"], dict[str, Any]]


class Attachment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    kind: str = "file"
    ref: str | None = None


class Segment(BaseModel):
    """One utterance of a transcript: where it starts and ends in whole milliseconds from the record's start, who
    spoke, and what was said. A source's mapper fills a record's segments in this shape (DR-0110)."""

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
    fidelity: Literal["verbatim", "derived"] = "verbatim"
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
        """The record as one line of the raw log."""
        return self.model_dump_json(exclude_none=False)


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


_PROJECTIONS: dict[str, Projection] = {}


def register_projection(source: str, projection: Projection) -> None:
    """A connector whose source needs a different idea of "the same content" registers its own projection."""
    _PROJECTIONS[source] = projection


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
    projection = _PROJECTIONS.get(record.source, default_projection)
    digest = hashlib.sha256(canonical_json(projection(record)).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
