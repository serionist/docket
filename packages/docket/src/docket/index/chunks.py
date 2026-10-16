"""Chunks: the units search finds, cut per kind of record, each with the locator `read` and `verify` take.

| Kind | Chunk |
|---|---|
| a record with segments | consecutive utterances: grow to 60 s, stop before 90 s or 350 words, overlap one |
| a message | the message alone; its thread gets windows of the parent and up to 10 replies, sharing one reply |
| any other record | the title on top, then the text; above 500 words, split between paragraphs |
| a processed doc | a section per heading, with the doc's title; anchors cut from the text and kept on the chunk |

Speakers go into the text once per turn as their raw labels; times and entity names never do, so editing an entity
never re-chunks.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from docket.index.text import dedupe
from docket.plaintext import nfc, words
from docket.vault.anchors import Anchor, format_anchor, paragraphs, trailing_anchors
from docket.vault.processed import DATED, ProcessedDoc
from docket.vault.records import RawRecord
from docket.vault.segments import Segment

WINDOW_MIN_MS = 60_000
WINDOW_MAX_MS = 90_000
WINDOW_WORDS = 350
RECORD_WORDS = 500
THREAD_REPLIES = 10

HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")


def iso(ts: datetime) -> str:
    """A UTC time with milliseconds, fixed width, so times compare as text."""
    at = ts.astimezone(UTC)
    return f"{at:%Y-%m-%dT%H:%M:%S}.{at.microsecond // 1000:03d}Z"


def locator(rid: str, kind: str = "record", start: int = 0, end: int | None = None) -> str:
    """A record's locator in the anchor grammar, without the brackets: `r:<rid>`, `r:<rid>@12:08-12:40`, `r:<rid>#3`."""
    if end == start:
        end = None
    return format_anchor(rid, "time" if kind == "time" else "part" if kind == "part" else "record", start, end)[1:-1]


@dataclass
class Chunk:
    owner: str
    ordinal: int
    kind: str
    grp: str
    text: str
    locator: str
    source: str | None = None
    ts: str | None = None
    rid: str | None = None
    doc: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    line_from: int | None = None
    line_to: int | None = None
    para_from: int | None = None
    para_to: int | None = None
    rids: list[str] = field(default_factory=list[str])
    speakers: list[str] = field(default_factory=list[str])
    authors: list[str] = field(default_factory=list[str])
    title: str | None = None
    anchors: list[str] = field(default_factory=list[str])

    def row(self) -> tuple[object, ...]:
        return (
            self.owner, self.ordinal, self.kind, self.grp, self.source, self.ts, self.start_ms, self.end_ms,
            self.line_from, self.line_to, self.para_from, self.para_to, self.rid, self.doc, json.dumps(self.rids),
            json.dumps(self.speakers, ensure_ascii=False), json.dumps(self.authors, ensure_ascii=False), self.title,
            self.locator, json.dumps(self.anchors, ensure_ascii=False), self.text,
            hashlib.sha256(self.text.encode("utf-8")).hexdigest(),
        )  # fmt: skip


COLUMNS = (
    "owner, ordinal, kind, grp, source, ts, start_ms, end_ms, line_from, line_to, para_from, para_to, rid, doc, rids, "
    "speakers, authors, title, locator, anchors, text, text_hash"
)


def group_of(record: RawRecord) -> str:
    return record.thread or record.rid


def record_owner(rid: str) -> str:
    return f"r {rid}"


def thread_owner(thread: str) -> str:
    return f"t {thread}"


def doc_owner(doc_id: str) -> str:
    return f"d {doc_id}"


# -- transcripts --------------------------------------------------------------------------------------------------


def _turns(segs: list[Segment]) -> str:
    """Utterances as turns: one line per run of a speaker, with the speaker's label once."""
    lines: list[str] = []
    last: object = object()
    for seg in segs:
        text = " ".join(seg.text.split())
        if seg.speaker == last and lines:
            lines[-1] += " " + text
        else:
            lines.append(f"{seg.speaker}: {text}" if seg.speaker else text)
        last = seg.speaker
    return "\n".join(lines)


def windows(segs: list[Segment]) -> list[list[Segment]]:
    """Consecutive utterances: grow until 60 s, stop before 90 s or 350 words; the next window starts on the last
    utterance of the one before. An utterance over 350 words is a window of its own (split later)."""
    out: list[list[Segment]] = []
    i = 0
    while i < len(segs):
        window = [segs[i]]
        count = len(words(segs[i].text))
        start, end = segs[i].start_ms, segs[i].end_ms
        j = i + 1
        while j < len(segs) and count <= WINDOW_WORDS and end - start < WINDOW_MIN_MS:
            n = len(words(segs[j].text))
            if max(end, segs[j].end_ms) - start > WINDOW_MAX_MS or count + n > WINDOW_WORDS:
                break
            window.append(segs[j])
            count += n
            end = max(end, segs[j].end_ms)
            j += 1
        out.append(window)
        if j >= len(segs):
            break
        i = j - 1 if len(window) > 1 else j
    return out


def transcript_chunks(record: RawRecord, segs: list[Segment]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for window in windows(segs):
        start = min(s.start_ms for s in window)
        end = max(s.end_ms for s in window)
        pieces = [_turns(window)]
        single = window[0]
        if len(window) == 1 and len(ws := single.text.split()) > WINDOW_WORDS:
            parts = [" ".join(ws[k : k + WINDOW_WORDS]) for k in range(0, len(ws), WINDOW_WORDS)]
            pieces = [_turns([single.model_copy(update={"text": part})]) for part in parts]
        for text in pieces:
            chunks.append(
                Chunk(
                    owner=record_owner(record.rid),
                    ordinal=len(chunks),
                    kind="transcript",
                    grp=group_of(record),
                    text=nfc(text),
                    locator=locator(record.rid, "time", start // 1000, -(-end // 1000)),
                    source=record.source,
                    ts=iso(record.ts + timedelta(milliseconds=start)),
                    rid=record.rid,
                    start_ms=start,
                    end_ms=end,
                    rids=[record.rid],
                    speakers=sorted({s.speaker for s in window if s.speaker}),
                    title=record.title,
                )
            )
    return chunks


# -- other records ------------------------------------------------------------------------------------------------


def _with_title(title: str | None, text: str) -> str:
    return nfc(f"{title}\n\n{text}" if title and text else title or text)


def record_chunks(record: RawRecord, segs: list[Segment]) -> list[Chunk]:
    """A record's own chunks: its transcript when it has segments, else its text."""
    if segs:
        return transcript_chunks(record, segs)
    paras = paragraphs(record.text)
    if not paras and not record.title:
        return []
    base = Chunk(
        owner=record_owner(record.rid),
        ordinal=0,
        kind="message" if record.kind == "message" else "record",
        grp=group_of(record),
        text="",
        locator=locator(record.rid),
        source=record.source,
        ts=iso(record.ts),
        rid=record.rid,
        rids=[record.rid],
        authors=[record.author] if record.author else [],
        title=record.title,
    )
    sizes = [len(words(p)) for p in paras]
    if record.kind == "message" or sum(sizes) <= RECORD_WORDS:
        base.text = _with_title(record.title, "\n\n".join(paras))
        return [base]
    chunks: list[Chunk] = []
    first = 0
    while first < len(paras):
        last, count = first, sizes[first]
        while last + 1 < len(paras) and count + sizes[last + 1] <= RECORD_WORDS:
            last += 1
            count += sizes[last]
        chunks.append(
            replace(
                base,
                ordinal=len(chunks),
                text=_with_title(record.title, "\n\n".join(paras[first : last + 1])),
                locator=locator(record.rid, "part", first + 1, last + 1),
                para_from=first + 1,
                para_to=last + 1,
            )
        )
        first = last + 1
    return chunks


@dataclass(frozen=True)
class Message:
    rid: str
    ts: str
    text: str
    author: str | None


def thread_chunks(thread: str, source: str, messages: list[Message]) -> list[Chunk]:
    """A thread's windows: the parent (the message whose rid is the thread's) and up to 10 replies, consecutive
    windows sharing one reply; none for a thread of one message."""
    if len(messages) < 2:
        return []
    parent = next((m for m in messages if m.rid == thread), None)
    replies = sorted((m for m in messages if m is not parent), key=lambda m: (m.ts, m.rid))
    chunks: list[Chunk] = []
    start = 0
    while True:
        part = replies[start : start + THREAD_REPLIES]
        members = ([parent] if parent else []) + part
        first = members[0] if start == 0 else part[0]
        chunks.append(
            Chunk(
                owner=thread_owner(thread),
                ordinal=len(chunks),
                kind="thread",
                grp=thread,
                text="\n".join(m.text for m in members),
                locator=locator(first.rid),
                source=source,
                ts=part[0].ts,
                rid=first.rid,
                rids=[m.rid for m in members],
                authors=sorted({m.author for m in members if m.author}),
            )
        )
        if start + THREAD_REPLIES >= len(replies):
            return chunks
        start += THREAD_REPLIES - 1


# -- processed docs -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RecordInfo:
    ts: str
    grp: str
    source: str


@dataclass(frozen=True)
class AnchorRow:
    line: int
    ordinal: int
    rid: str
    kind: str
    start: int
    end: int | None


@dataclass
class _Section:
    heading: str | None
    lines: list[tuple[int, str]] = field(default_factory=list[tuple[int, str]])
    anchors: list[tuple[int, Anchor]] = field(default_factory=list[tuple[int, Anchor]])


def heading_text(line: str) -> str | None:
    m = HEADING.match(line)
    return None if m is None else (m.group(2) or "").strip()


def doc_title(doc: ProcessedDoc) -> str:
    for _, line, role in doc.roles():
        m = HEADING.match(line)
        if role == "free" and m and len(m.group(1)) == 1 and m.group(2):
            return m.group(2).strip()
    return doc.meta.id.partition(":")[2]


def _resolved(anchor: Anchor, covers: list[str]) -> Anchor | None:
    if anchor.rid is not None:
        return anchor
    if len(covers) != 1:
        return None
    return Anchor(covers[0], anchor.kind, anchor.start, anchor.end, anchor.token)


def _sections(doc: ProcessedDoc) -> tuple[list[_Section], list[AnchorRow]]:
    legacy = doc.meta.type == "legacy-note"
    sections = [_Section(None)]
    rows: list[AnchorRow] = []
    for n, line, role in doc.roles():
        heading = heading_text(line) if role == "free" else None
        if heading is not None:
            sections.append(_Section(heading))
            continue
        if role != "content" or legacy:
            sections[-1].lines.append((n, line))
            continue
        cut = trailing_anchors(line)
        sections[-1].lines.append((n, cut.text))
        for k, anchor in enumerate(a for a in (_resolved(a, doc.meta.covers) for a in cut.anchors) if a):
            sections[-1].anchors.append((n, anchor))
            rows.append(AnchorRow(n, k, anchor.rid or "", anchor.kind, anchor.start, anchor.end))
    return sections, rows


def _slug_day(doc_id: str, tz: ZoneInfo) -> str | None:
    slug = doc_id.partition(":")[2]
    if not DATED.match(slug):
        return None
    try:
        day = date.fromisoformat(slug[:10])
    except ValueError:
        return None
    return iso(datetime(day.year, day.month, day.day, tzinfo=tz))


def doc_chunks(
    doc: ProcessedDoc, info: Callable[[str], RecordInfo | None], tz: ZoneInfo
) -> tuple[list[Chunk], list[AnchorRow]]:
    """A doc's sections as chunks, and every content line's anchors as rows."""
    meta = doc.meta
    title = doc_title(doc)
    covered = [i for i in (info(rid) for rid in meta.covers) if i is not None]
    groups = {i.grp for i in covered}
    sources = {i.source for i in covered}
    grp = groups.pop() if len(groups) == 1 and len(covered) == len(meta.covers) else meta.id
    source = sources.pop() if len(sources) == 1 else None
    fallback = min((i.ts for i in covered), default=None) or _slug_day(meta.id, tz)
    sections, rows = _sections(doc)
    chunks: list[Chunk] = []
    for section in sections:
        body = [(n, text) for n, text in section.lines if text.strip()]
        if not any(words(text) for _, text in body):
            continue
        head = [title] if section.heading in (None, title) else [title, section.heading or ""]
        pieces: list[list[tuple[int, str]]] = [[]]
        count = 0
        for n, text in body:
            size = len(words(text))
            if pieces[-1] and count + size > RECORD_WORDS:
                pieces.append([])
                count = 0
            pieces[-1].append((n, text))
            count += size
        for piece in pieces:
            anchors = [a for n, a in section.anchors if piece[0][0] <= n <= piece[-1][0] and a.rid]
            times = [i.ts for i in (info(a.rid or "") for a in anchors) if i is not None]
            chunks.append(
                Chunk(
                    owner=doc_owner(meta.id),
                    ordinal=len(chunks),
                    kind="doc",
                    grp=grp,
                    text=nfc("\n".join([*head, *(text for _, text in piece)])),
                    locator=meta.id,
                    source=source,
                    ts=min(times, default=None) or fallback,
                    doc=meta.id,
                    line_from=piece[0][0],
                    line_to=piece[-1][0],
                    rids=sorted({a.rid or "" for a in anchors}),
                    title=section.heading or title,
                    anchors=dedupe(a.text[1:-1] for a in anchors),
                )
            )
    return chunks, rows
