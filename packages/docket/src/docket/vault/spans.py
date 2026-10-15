"""Resolving anchors against raw records: whether an anchor points at something real, and the raw it points at.

doc_put and verify share this, so what put accepted, verify can show, and the index (#7) fills its doc_anchors table
from the same answers. Records are looked up by reading only the rid's source folder, once per lookup: rids start with
their source, and run files are written once.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from docket.vault.anchors import Anchor, encode_rid, format_time, normalize_newlines, paragraphs
from docket.vault.rawlog import parse_run_file
from docket.vault.records import RID, RawRecord
from docket.vault.segments import Segment, segments, time_range

# How far past a point in time verify and read show by default, in seconds.
WINDOW = 60


class Span(BaseModel):
    """The raw an anchor points at, as data: the record's header, the segments or text, and a problem when it
    doesn't resolve. `versions` says how many versions the record has, so a reader knows when a summary may predate
    an edit."""

    rid: str
    anchor: str
    kind: str = ""
    ts: datetime | None = None
    author: str | None = None
    title: str | None = None
    versions: int = 0
    start_ms: int | None = None
    end_ms: int | None = None
    paragraphs: tuple[int, int] | None = None
    segments: list[Segment] = Field(default_factory=list[Segment])
    text: str = ""
    problem: str | None = None


class RawLookup:
    """Records by rid, read from their source's run files the first time that source is asked for."""

    def __init__(self, root: Path) -> None:
        self.raw_dir = root / "raw"
        self._sources: dict[str, dict[str, list[RawRecord]]] = {}

    def _source(self, source: str) -> dict[str, list[RawRecord]]:
        if source in self._sources:
            return self._sources[source]
        folder = self.raw_dir / source
        runs = [parse_run_file(self.raw_dir, p) for p in folder.rglob("*.jsonl")] if folder.is_dir() else []
        by_rid: dict[str, list[RawRecord]] = {}
        for run in sorted((r for r in runs if r is not None), key=lambda r: r.order):
            with run.path.open(encoding="utf-8", newline="\n") as f:
                for line in f:
                    if line.strip():
                        record = RawRecord.model_validate(json.loads(line))
                        by_rid.setdefault(record.rid, []).append(record)
        self._sources[source] = by_rid
        return by_rid

    def versions(self, rid: str) -> list[RawRecord]:
        """Every stored version of a record, in run order."""
        m = RID.match(rid)
        return list(self._source(m.group(1)).get(rid, [])) if m else []

    def latest(self, rid: str) -> RawRecord | None:
        versions = self.versions(rid)
        return versions[-1] if versions else None

    def under(self, rid: str) -> list[RawRecord]:
        """The other records of rid's source whose thread or container is rid, each at its latest version, by rid: a
        recording's transcripts and notes, which name the recording as their thread (DR-0117)."""
        m = RID.match(rid)
        by_rid = self._source(m.group(1)) if m else {}
        latest = (v[-1] for other, v in by_rid.items() if other != rid)
        return sorted((r for r in latest if rid in (r.thread, r.container)), key=lambda r: r.rid)

    def neighbours(self, record: RawRecord, n: int) -> list[RawRecord]:
        """The record and up to n messages either side in its thread (its container when it has none), in time
        order, each at its latest version."""
        field, value = ("thread", record.thread) if record.thread else ("container", record.container)
        if value is None or n <= 0:
            return [record]
        group = [v[-1] for v in self._source(record.source).values() if getattr(v[-1], field) == value]
        group.sort(key=lambda r: (r.ts, r.rid))
        i = next(i for i, r in enumerate(group) if r.rid == record.rid)
        return group[max(0, i - n) : i + n + 1]


def problem(anchor: Anchor, record: RawRecord) -> str | None:
    """Why an anchor doesn't resolve against a record, or None when it does."""
    if anchor.kind == "record":
        return None
    if anchor.kind == "part":
        count = len(paragraphs(record.text))
        if anchor.last > count:
            return f"{record.rid} has {count} paragraph(s), so #{anchor.last} isn't one"
        return None
    bounds = time_range(segments(record))
    if bounds is None:
        return f"{record.rid} has no transcript segments; anchor the whole record: [r:{encode_rid(record.rid)}]"
    lo, hi = bounds
    for t in (anchor.start, anchor.last):
        if not lo <= t <= hi:
            return f"{format_time(t)} is outside {record.rid}, which runs from {format_time(lo)} to {format_time(hi)}"
    return None


def span(
    anchor: Anchor, record: RawRecord, versions: int, *, window: int = WINDOW, around: int = 0, written: str = ""
) -> Span:
    """The raw an anchor points at. A point in time shows the segments overlapping the next `window` seconds, a
    stretch the segments overlapping it, a paragraph its text; `around` widens a time by minutes and paragraphs by
    paragraphs, either side."""
    head = Span(
        rid=record.rid,
        anchor=written or anchor.token or anchor.text,
        kind=record.kind,
        ts=record.ts,
        author=record.author,
        title=record.title,
        versions=versions,
    )
    why = problem(anchor, record)
    if why is not None:
        return head.model_copy(update={"problem": why})
    if anchor.kind == "part":
        paras = paragraphs(record.text)
        first = max(1, anchor.start - around)
        last = min(len(paras), anchor.last + around)
        return head.model_copy(update={"paragraphs": (first, last), "text": "\n\n".join(paras[first - 1 : last])})
    if anchor.kind == "record":
        return head.model_copy(update={"segments": segments(record), "text": normalize_newlines(record.text)})
    lo = max(0, anchor.start - around * 60) * 1000
    hi = ((anchor.last + 1) if anchor.end is not None else (anchor.start + window)) * 1000 + around * 60_000
    chosen = [s for s in segments(record) if s.start_ms < hi and (s.end_ms > lo or s.start_ms >= lo)]
    return head.model_copy(update={"start_ms": lo, "end_ms": hi, "segments": chosen})
