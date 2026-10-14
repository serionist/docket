"""Transcript segments: what an anchor's time resolves against, the same way for any source.

A transcript's segments are a field of its raw record, in the core's shape (`Segment`: start and end in milliseconds
from the record's start, speaker, text), filled by the source's mapper. Nothing reads them from `native`, and the core
keeps no per-source code. This is a one-way door (DR-0110).
"""

from __future__ import annotations

from docket.vault.records import RawRecord, Segment

__all__ = ["Segment", "segments", "time_range"]


def segments(record: RawRecord) -> list[Segment]:
    """A record's segments in time order, overlaps (two people talking at once) kept; none when it has none."""
    return sorted(record.segments or [], key=lambda s: (s.start_ms, s.end_ms))


def time_range(segs: list[Segment]) -> tuple[int, int] | None:
    """The whole seconds a transcript spans: the smallest start rounded down to the greatest end rounded up. Segments
    overlap, so the last one to start isn't always the last to end."""
    if not segs:
        return None
    start = min(s.start_ms for s in segs) // 1000
    end = -(-max(s.end_ms for s in segs) // 1000)
    return start, end
