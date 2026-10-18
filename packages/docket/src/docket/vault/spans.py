"""Resolving anchors against raw records: whether an anchor points at something real, and the raw it points at.

doc_put and verify share this, so what put accepted, verify can show, and the index (#7) fills its doc_anchors table
from the same answers. Records are looked up by reading only the rid's source folder, once per lookup: rids start with
their source, and run files are written once. Given the places a current index keeps (DR-0446), a lookup reads only the
lines it needs instead, and today's code decides on them (DR-0447).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import NamedTuple, Protocol

from pydantic import BaseModel, Field

from docket.vault import forgotten
from docket.vault.anchors import Anchor, encode_rid, format_anchor, format_time, normalize_newlines, paragraphs
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
    # Set when the span is read from an earlier version than the latest: a folder record that went (DR-0491).
    note: str | None = None


def over(record: RawRecord) -> list[str]:
    """The rids of its own source a record names as its thread or its container, other than its own, sorted: the
    records it is under (DR-0117)."""
    named = {rid for rid in (record.thread, record.container) if rid and rid != record.rid}
    return sorted(rid for rid in named if (m := RID.match(rid)) and m.group(1) == record.source)


def is_transcript(record: RawRecord) -> bool:
    """A record a time on the record it is under is placed on: of kind transcript, with segments (DR-0370)."""
    return record.kind == "transcript" and bool(segments(record))


class Place(NamedTuple):
    """Where a version of a record is in the raw log, as a current index keeps it (DR-0445)."""

    path: str
    line: int
    offset: int
    v: str


class Places(Protocol):
    """What a lookup asks a current index about a source's raw: where its records' lines are (DR-0446, DR-0447)."""

    def raw_current(self, source: str) -> bool: ...

    def places(self, rid: str, source: str) -> list[Place]: ...

    def has(self, rid: str, source: str) -> bool: ...

    def under(self, rid: str, source: str, kind: str | None) -> list[str]: ...

    def group(self, field: str, value: str, source: str) -> list[tuple[str, str]]: ...

    def nested(self, rid: str, source: str) -> list[str]: ...

    def of_kind(self, source: str, kind: str) -> list[str]: ...


class _MovedError(Exception):
    """A line isn't what the index says is there: its file changed since the check."""


def _window(group: list[RawRecord], record: RawRecord, n: int) -> list[RawRecord]:
    group = sorted(group, key=lambda r: (r.ts, r.rid))
    i = next(i for i, r in enumerate(group) if r.rid == record.rid)
    return group[max(0, i - n) : i + n + 1]


def _under(latest: Iterable[RawRecord], rid: str, kind: str | None) -> list[RawRecord]:
    return sorted((r for r in latest if rid in over(r) and (kind is None or r.kind == kind)), key=lambda r: r.rid)


class RawLookup:
    """Records by rid, read from their source's run files the first time that source is asked for; or, when `places`
    gives an index current for the source, only the lines asked for, at their places (DR-0446). A line that isn't
    what the index says sends its source back to the whole load for the rest of the lookup."""

    def __init__(self, root: Path, places: Callable[[], Places | None] | None = None) -> None:
        self.root = root
        self.raw_dir = root / "raw"
        self._sources: dict[str, dict[str, list[RawRecord]]] = {}
        self._places = places
        self._placed: dict[str, Places | None] = {}
        self._read: dict[str, list[RawRecord]] = {}

    def _source(self, source: str) -> dict[str, list[RawRecord]]:
        if source in self._sources:
            return self._sources[source]
        folder = self.raw_dir / source
        runs = [parse_run_file(self.raw_dir, p) for p in folder.rglob("*.jsonl")] if folder.is_dir() else []
        by_rid: dict[str, list[RawRecord]] = {}
        # What a forget took reads as gone, wherever a merge left its lines (DR-0579). A current index never holds
        # them, so the placed route has nothing to skip.
        reading = forgotten.at(self.root).reading()
        for run in sorted((r for r in runs if r is not None), key=lambda r: r.order):
            with run.path.open(encoding="utf-8", newline="\n") as f:
                for line in f:
                    if line.strip():
                        record = RawRecord.model_validate(json.loads(line))
                        if not reading.skips(record):
                            by_rid.setdefault(record.rid, []).append(record)
        self._sources[source] = by_rid
        return by_rid

    def _index(self, source: str) -> Places | None:
        """The index to read a source's lines by, when it is current for the source; asked once per source."""
        if source not in self._placed:
            index = self._places() if self._places is not None else None
            self._placed[source] = index if index is not None and index.raw_current(source) else None
        return self._placed[source]

    def _line(self, place: Place, rid: str) -> RawRecord:
        """The record at a place, decoded and parsed as the load route does: strict UTF-8, then JSON from text."""
        try:
            with (self.root / place.path).open("rb") as f:
                f.seek(place.offset)
                data = f.readline()
            record = RawRecord.model_validate(json.loads(data.decode("utf-8")))
        except (OSError, ValueError) as e:
            raise _MovedError from e
        if record.rid != rid or record.v != place.v:
            raise _MovedError
        return record

    def _placed_versions(self, index: Places, rid: str, source: str) -> list[RawRecord]:
        if rid not in self._read:
            self._read[rid] = [self._line(place, rid) for place in index.places(rid, source)]
        return self._read[rid]

    def _latest_placed(self, index: Places, rid: str, source: str) -> RawRecord:
        versions = self._placed_versions(index, rid, source)
        if not versions:
            raise _MovedError
        return versions[-1]

    def versions(self, rid: str) -> list[RawRecord]:
        """Every stored version of a record, in run order."""
        m = RID.match(rid)
        if m is None:
            return []
        source = m.group(1)
        index = self._index(source)
        if index is not None:
            try:
                return list(self._placed_versions(index, rid, source))
            except _MovedError:
                self._placed[source] = None
        return list(self._source(source).get(rid, []))

    def latest(self, rid: str) -> RawRecord | None:
        versions = self.versions(rid)
        return versions[-1] if versions else None

    def has(self, rid: str) -> bool:
        """Whether the raw log holds a record by this rid: a row of a current index, else the loaded source."""
        m = RID.match(rid)
        if m is None:
            return False
        source = m.group(1)
        index = self._index(source)
        return index.has(rid, source) if index is not None else rid in self._source(source)

    def under(self, rid: str, kind: str | None = None) -> list[RawRecord]:
        """The other records of rid's source whose thread or container is rid, of a kind when one is given, each at
        its latest version, by rid: a recording's transcripts and notes, which name the recording as their thread
        (DR-0117)."""
        m = RID.match(rid)
        if m is None:
            return []
        source = m.group(1)
        index = self._index(source)
        if index is not None:
            try:
                latest = [self._latest_placed(index, other, source) for other in index.under(rid, source, kind)]
            except _MovedError:
                self._placed[source] = None
            else:
                return _under(latest, rid, kind)
        return _under((v[-1] for other, v in self._source(source).items() if other != rid), rid, kind)

    def nested(self, rid: str) -> list[str]:
        """The rid and the rids nested under `<rid>/` that the raw log holds, by rid: a media file's records
        (DR-0547). From a current index's rows by prefix, else from the loaded source."""
        m = RID.match(rid)
        if m is None:
            return []
        source = m.group(1)
        index = self._index(source)
        if index is not None:
            return index.nested(rid, source)
        return sorted(other for other in self._source(source) if other == rid or other.startswith(f"{rid}/"))

    def rids(self, source: str) -> list[str]:
        """Every rid of a source the raw log holds, from the loaded source: for a count over a few stored files."""
        return sorted(self._source(source))

    def of_kind(self, source: str, kind: str) -> list[str]:
        """A source's rids whose latest version is of this kind, newest first: its recordings, for the ones whose
        audio can be put again cut (DR-0561). From a current index's rows, else from the loaded source."""
        index = self._index(source)
        if index is not None:
            return index.of_kind(source, kind)
        latest = [versions[-1] for versions in self._source(source).values()]
        found = sorted((r for r in latest if r.kind == kind), key=lambda r: (r.ts, r.rid), reverse=True)
        return [r.rid for r in found]

    def transcripts(self, rid: str) -> list[RawRecord]:
        """The transcripts under rid, by rid: where a time on a record without segments of its own lands (DR-0370)."""
        return [r for r in self.under(rid, "transcript") if is_transcript(r)]

    def timed(self, rids: list[str]) -> list[str]:
        """Where a time on these records can be placed (DR-0370): those with segments of their own; when none has any,
        the transcripts under them, so a time on a recording lands on the transcript its source keeps as a record of
        its own."""
        own = [rid for rid in rids if any(segments(r) for r in self.versions(rid)[-1:])]
        if own:
            return own
        return list(dict.fromkeys(t.rid for rid in rids for t in self.transcripts(rid)))

    def recordings(self, record: RawRecord) -> list[str]:
        """The records in the raw log a transcript is under, by rid: its recording, read as one with it (DR-0391). A
        record that isn't a transcript is under none."""
        if not is_transcript(record):
            return []
        return [rid for rid in over(record) if self.has(rid)]

    def neighbours(self, record: RawRecord, n: int) -> list[RawRecord]:
        """The record and up to n messages either side in its thread (its container when it has none), in time
        order, each at its latest version."""
        field, value = ("thread", record.thread) if record.thread else ("container", record.container)
        if value is None or n <= 0:
            return [record]
        index = self._index(record.source)
        if index is not None:
            try:
                return _window(self._placed_group(index, record, field, value, n), record, n)
            except _MovedError:
                self._placed[record.source] = None
        group = [v[-1] for v in self._source(record.source).values() if getattr(v[-1], field) == value]
        return _window(group, record, n)

    def _placed_group(self, index: Places, record: RawRecord, field: str, value: str, n: int) -> list[RawRecord]:
        """The records the window around a record can hold: the index orders the group by time to the millisecond, so
        the window is taken from the record's own millisecond, n rows either side, and each end widened to its whole
        millisecond; the exact sort then decides within it (DR-0447)."""
        rows = index.group(field, value, record.source)
        at = next((i for i, (rid, _) in enumerate(rows) if rid == record.rid), None)
        if at is None:
            raise _MovedError
        lo = hi = at
        while lo > 0 and rows[lo - 1][1] == rows[at][1]:
            lo -= 1
        while hi < len(rows) - 1 and rows[hi + 1][1] == rows[at][1]:
            hi += 1
        lo, hi = max(0, lo - n), min(len(rows) - 1, hi + n)
        while lo > 0 and rows[lo - 1][1] == rows[lo][1]:
            lo -= 1
        while hi < len(rows) - 1 and rows[hi + 1][1] == rows[hi][1]:
            hi += 1
        return [self._latest_placed(index, rid, record.source) for rid, _ in rows[lo : hi + 1]]


def problem(anchor: Anchor, record: RawRecord, lookup: RawLookup | None = None) -> str | None:
    """Why an anchor doesn't resolve against a record, or None when it does. With the lookup, a time on a record
    without segments names the transcripts under it (DR-0391)."""
    if anchor.kind == "record":
        return None
    if anchor.kind == "part":
        count = len(paragraphs(record.text))
        if anchor.last > count:
            return f"{record.rid} has {count} paragraph(s), so #{anchor.last} isn't one"
        return None
    bounds = time_range(segments(record))
    if bounds is None:
        return _no_segments(anchor, record, lookup)
    lo, hi = bounds
    for t in (anchor.start, anchor.last):
        if not lo <= t <= hi:
            return f"{format_time(t)} is outside {record.rid}, which runs from {format_time(lo)} to {format_time(hi)}"
    return None


def _no_segments(anchor: Anchor, record: RawRecord, lookup: RawLookup | None) -> str:
    """Why a time on a record without segments doesn't resolve: the transcripts under it that hold the time, each with
    the anchor to write, else why the time falls outside each of them, so a hint never suggests a refused anchor."""
    head = f"{record.rid} has no transcript segments"
    under = lookup.transcripts(record.rid) if lookup is not None else []
    if not under:
        return f"{head}; anchor the whole record: [r:{encode_rid(record.rid)}]"
    why = {t.rid: problem(Anchor(t.rid, "time", anchor.start, anchor.end), t) for t in under}
    fits = [rid for rid, outside in why.items() if outside is None]
    if not fits:
        return "; ".join([head, *(outside for outside in why.values() if outside)])
    written = " or ".join(format_anchor(rid, "time", anchor.start, anchor.end) for rid in fits)
    if len(fits) == 1:
        return f"{head}; the time is on its transcript {fits[0]}: write {written}, with {fits[0]} in covers"
    return f"{head}; the time is on its transcripts {', '.join(fits)}: write {written}, with that one in covers"


def span(
    anchor: Anchor,
    record: RawRecord,
    versions: int,
    *,
    window: int = WINDOW,
    around: int = 0,
    written: str = "",
    lookup: RawLookup | None = None,
) -> Span:
    """The raw an anchor points at. A point in time shows the segments overlapping the next `window` seconds, a
    stretch the segments overlapping it, a paragraph its text; `around` widens a time by minutes and paragraphs by
    paragraphs, either side. The lookup lets a problem name the transcripts under a record (DR-0391)."""
    head = Span(
        rid=record.rid,
        anchor=written or anchor.token or anchor.text,
        kind=record.kind,
        ts=record.ts,
        author=record.author,
        title=record.title,
        versions=versions,
    )
    why = problem(anchor, record, lookup)
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
