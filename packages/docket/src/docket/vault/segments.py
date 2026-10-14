"""Transcript segments: the contract every transcript meets, so an anchor's time resolves the same way for any source.

A segment is one utterance: where it starts and ends in milliseconds from the record's start, who spoke, and what was
said. A connector whose source keeps its transcript in its own shape registers a reader for it; everything else is read
from `native["segments"]`, the shape Docket's own transcripts are written in. A reader is pure: the index stores what
it returns, and two rebuilds must agree. The contract is a one-way door (DR-0026).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from docket.errors import DocketError
from docket.vault.records import RawRecord


class Segment(BaseModel):
    """One utterance of a transcript."""

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


SegmentReader = Callable[[RawRecord], list[Segment]]

_READERS: dict[str, SegmentReader] = {}


def register_segments(source: str, reader: SegmentReader) -> None:
    """A connector whose source keeps its transcript in a shape of its own registers how to read it."""
    _READERS[source] = reader


def default_segments(record: RawRecord) -> list[Segment]:
    """`native["segments"]` as a list of `{start_ms, end_ms, speaker, text}`; no such key, no segments.

    A list that is there but malformed is an error naming the record: an empty list would make a broken connector look
    like a record without a transcript."""
    raw: Any = record.native.get("segments")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise DocketError(f"{record.rid}: native segments isn't a list")
    out: list[Segment] = []
    for n, item in enumerate(cast(list[Any], raw), start=1):
        try:
            out.append(Segment.model_validate(item))
        except ValidationError as e:
            reason = e.errors()[0]
            where = ".".join(str(p) for p in reason["loc"]) or "segment"
            raise DocketError(
                f"{record.rid}: segment {n} isn't {{start_ms, end_ms, speaker, text}}: {where}: {reason['msg']}"
            ) from e
    return out


def segments(record: RawRecord) -> list[Segment]:
    """A record's segments in time order, overlaps (two people talking at once) kept."""
    reader = _READERS.get(record.source, default_segments)
    return sorted(reader(record), key=lambda s: (s.start_ms, s.end_ms))


def time_range(segs: list[Segment]) -> tuple[int, int] | None:
    """The whole seconds a transcript spans: the smallest start rounded down to the greatest end rounded up. Segments
    overlap, so the last one to start isn't always the last to end."""
    if not segs:
        return None
    start = min(s.start_ms for s in segs) // 1000
    end = -(-max(s.end_ms for s in segs) // 1000)
    return start, end
