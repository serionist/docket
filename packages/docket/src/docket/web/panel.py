"""The panel beside the results: a hit's summary next to the raw it came from, or a summary verified line by line.

| The open locator | Shows |
|---|---|
| a record, `r:<rid>@12:08` | its group's summary, the lines pointing here marked, next to its raw read with `around` |
| a record, with `verify=1` | that summary verified: each line next to the raw its anchors point at |
| a doc id | the doc verified, its hit lines only when `lines` names them |
| a legacy note, or a doc's path | the note, next to the raw it covers |

Everything goes through `docket.service.calls`, so the panel reads what the MCP tools read. Text from records and
docs is data: the templates escape it, and no link is made from it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal
from zoneinfo import ZoneInfo

from docket import index
from docket.errors import DocketError
from docket.recall import DocView
from docket.service import calls
from docket.service.state import Service
from docket.vault.anchors import Anchor, AnchorError, format_anchor, paragraphs, parse_locator, trailing_anchors
from docket.vault.processed import DOC_TYPES
from docket.vault.spans import Span
from docket.web.state import MAX_AROUND, SearchState
from docket.web.templating import at, clock
from docket.web.views import Link, panel_link

Mode = Literal["raw", "verify", "error"]
UNITS = {"time": "min", "part": "paragraph", "record": "message"}


@dataclass(frozen=True)
class AnchorLink:
    text: str
    link: Link | None


@dataclass(frozen=True)
class LineView:
    n: int
    body: str
    style: Literal["heading", "bullet", "text", "blank"]
    anchors: list[AnchorLink]
    cited: bool = False


@dataclass(frozen=True)
class SegmentView:
    at: str
    locator: str
    speaker: str
    text: str
    mark: bool


@dataclass(frozen=True)
class ParagraphView:
    n: int
    text: str
    mark: bool


@dataclass(frozen=True)
class SpanView:
    rid: str
    anchor: str
    head: str
    versions: str
    problem: str | None
    segments: list[SegmentView]
    paragraphs: list[ParagraphView]
    mark: bool
    # Said in place of the versions badge when the span is an earlier version than the newest (DR-0491).
    note: str | None = None


@dataclass(frozen=True)
class PairView:
    line: LineView
    spans: list[SpanView]
    problems: list[str]

    @property
    def flagged(self) -> bool:
        """A line whose anchors don't all resolve: a problem of its own, or one in the raw it points at."""
        return bool(self.problems) or any(s.problem for s in self.spans)


@dataclass(frozen=True)
class SummaryView:
    id: str
    type: str
    rev: int
    title: str
    lines: list[LineView]


@dataclass
class PanelView:
    mode: Mode
    locator: str
    title: str = ""
    close: Link | None = None
    resolved: str | None = None
    problems: list[str] = field(default_factory=list[str])
    summary: SummaryView | None = None
    no_summary: str = ""
    citing: list[str] = field(default_factory=list[str])
    legacy: list[str] = field(default_factory=list[str])
    spans: list[SpanView] = field(default_factory=list[SpanView])
    pairs: list[PairView] = field(default_factory=list[PairView])
    around: int = 0
    unit: str = ""
    less: Link | None = None
    more: Link | None = None
    verify: Link | None = None
    verify_label: str = ""
    error: str = ""


def _frontmatter_end(doc: DocView) -> int:
    """The number of the frontmatter's closing line: every processed doc opens with its frontmatter (DR-0109)."""
    return next((line.n for line in doc.lines[1:] if line.text.strip() == "---"), 0)


def _anchor_locator(anchor: Anchor, covers: list[str]) -> str | None:
    rid = anchor.rid or (covers[0] if len(covers) == 1 else None)
    return None if rid is None else format_anchor(rid, anchor.kind, anchor.start, anchor.end)[1:-1]


def line_view(n: int, text: str, covers: list[str], state: SearchState, cited: bool = False) -> LineView:
    """A doc line: its words, and its anchors as links that open their raw in the panel."""
    trailing = trailing_anchors(text)
    body = trailing.text.strip()
    style: Literal["heading", "bullet", "text", "blank"] = "text"
    if not body:
        style = "blank"
    elif body.startswith("#"):
        style, body = "heading", body.lstrip("#").strip()
    elif body.startswith(("- ", "* ")):
        style, body = "bullet", body[2:]
    anchors: list[AnchorLink] = []
    for anchor in trailing.anchors:
        target = _anchor_locator(anchor, covers)
        to = None if target is None else panel_link(state.search_only().but(hit=target))
        anchors.append(AnchorLink(anchor.token, to))
    return LineView(n, body, style, anchors, cited)


def summary_view(doc: DocView, state: SearchState, cited: set[int] | None = None) -> SummaryView:
    start = _frontmatter_end(doc)
    lines = [line_view(line.n, line.text, doc.covers, state, line.n in (cited or set())) for line in doc.lines]
    body = [line for line in lines if line.n > start]
    title = next((line.body for line in body if line.style == "heading"), doc.id)
    return SummaryView(doc.id, doc.type, doc.rev, title, body)


def span_view(span: Span, tz: ZoneInfo, hit: Anchor | None = None, whole: bool = False) -> SpanView:
    """The raw an anchor points at; the hit's own stretch marked, or the whole span when it is the hit's message."""
    head = " - ".join(p for p in (span.kind, at(span.ts, tz), span.author or "", span.title or "") if p)
    versions = f"{span.versions} versions; this is the latest" if span.versions > 1 and span.note is None else ""
    segments: list[SegmentView] = []
    for seg in span.segments:
        mark = False
        if hit is not None and hit.kind == "time":
            mark = seg.start_ms < (hit.last + 1) * 1000 and seg.end_ms > hit.start * 1000
        second = seg.start_ms // 1000
        locator = format_anchor(span.rid, "time", second)[1:-1]
        segments.append(SegmentView(clock(second), locator, seg.speaker or "?", seg.text, mark))
    paras: list[ParagraphView] = []
    if not segments and span.text:
        first = span.paragraphs[0] if span.paragraphs else 1
        for i, text in enumerate(paragraphs(span.text)):
            n = first + i
            mark = hit is not None and hit.kind == "part" and hit.start <= n <= hit.last
            paras.append(ParagraphView(n, text, mark))
    return SpanView(span.rid, span.anchor, head, versions, span.problem, segments, paras, whole, span.note)


def _around(state: SearchState, kind: str, view: PanelView) -> None:
    view.around = state.around
    view.unit = UNITS[kind]
    if state.around > 0:
        view.less = panel_link(state.but(around=state.around - 1))
    if state.around < MAX_AROUND:
        view.more = panel_link(state.but(around=state.around + 1))


async def build(service: Service, state: SearchState, tz: ZoneInfo) -> PanelView:
    """The panel for the open locator; a locator that doesn't resolve is the panel's own error."""
    target = state.open_hit or ""
    view = PanelView(mode="raw", locator=target, close=panel_link(state.search_only()))
    try:
        locator = parse_locator(target, DOC_TYPES)
        if locator.kind == "doc":
            await _doc(service, state, locator.value, view)
        elif locator.kind == "path":
            await _note(service, state, target, view, tz)
        else:
            await _record(service, state, locator.value, locator.anchor, view=view, tz=tz)
    except AnchorError as e:
        view.mode, view.error = "error", str(e)
    except DocketError as e:
        view.mode, view.error = "error", str(e)
    return view


async def _note(service: Service, state: SearchState, target: str, view: PanelView, tz: ZoneInfo) -> None:
    """A legacy note, or a doc by its path: the note next to the raw it covers, or the raw at its time."""
    found, _ = await calls.read(service, target, view="both", around=state.around)
    view.resolved = found.resolved
    view.problems = list(found.problems)
    if found.doc is not None:
        view.summary = summary_view(found.doc, state, set(state.line_numbers()))
        view.title = view.summary.title
    view.spans = [span_view(s, tz) for s in found.spans]


async def _doc(service: Service, state: SearchState, doc_id: str, view: PanelView) -> None:
    read, _ = await calls.read(service, doc_id, view="summary")
    doc = read.doc
    if doc is None:  # pragma: no cover - a doc id always reads as a doc, or raises
        raise DocketError(f"no doc {doc_id}")
    summary = summary_view(doc, state, set(state.line_numbers()))
    view.title = summary.title
    if doc.type == "legacy-note":
        view.summary = summary
        view.no_summary = "A legacy note has no anchors, so its lines can't be verified."
        return
    lines = None if state.verify or not state.lines else state.line_numbers()
    await _pairs(service, doc_id, doc, lines=lines, state=state, view=view)
    if state.lines:
        view.verify = panel_link(state.but(verify=not state.verify))
        view.verify_label = "Only the hit's lines" if state.verify else "Verify every line"


async def _pairs(
    service: Service, doc_id: str, doc: DocView, *, lines: list[int] | None, state: SearchState, view: PanelView
) -> None:
    checked, _ = await calls.verify(service, doc_id, lines=lines)
    view.mode = "verify"
    tz = ZoneInfo(service.vault().config.owner.time_zone)
    for line in checked.lines:
        spans = [span_view(s, tz) for s in line.spans]
        view.pairs.append(PairView(line_view(line.n, line.text, doc.covers, state), spans, list(line.problems)))


async def _record(
    service: Service, state: SearchState, rid: str, anchor: Anchor | None, *, view: PanelView, tz: ZoneInfo
) -> None:
    vault = service.vault()
    # The summary comes from the index, so it waits for an update as a search does: a panel opened first thing, from
    # a bookmark, finds the index built and current.
    await service.updater.fresh()
    ref = await asyncio.to_thread(index.summary_for, vault, rid)
    if state.verify:
        view.verify = panel_link(state.but(verify=False))
        view.verify_label = "Back to the raw"
        if ref is None:
            view.title = rid
            view.no_summary = "No summary covers this record yet, so there is nothing to verify."
            return
        read, _ = await calls.read(service, ref.id, view="summary")
        if read.doc is None:  # pragma: no cover - a doc id always reads as a doc, or raises
            raise DocketError(f"no doc {ref.id}")
        view.title = ref.title
        await _pairs(service, ref.id, read.doc, lines=None, state=state, view=view)
        return
    found, _ = await calls.read(service, state.open_hit or rid, view="both", around=state.around)
    view.problems = list(found.problems)
    neighbours = len(found.spans) > 1
    view.spans = [span_view(s, tz, anchor, whole=neighbours and s.rid == rid) for s in found.spans]
    view.title = next((s.title for s in found.spans if s.rid == rid and s.title), "") or rid
    kind = anchor.kind if anchor is not None else "record"
    if kind != "record" or not any(s.segments for s in found.spans):
        _around(state, kind, view)
    cited = {line.n for line in found.lines if ref is not None and line.doc == ref.id}
    view.citing = [f"{c.doc} rev {c.rev}, line {c.n}" for c in found.lines if ref is None or c.doc != ref.id]
    view.legacy = [f"{note.id}: {', '.join(note.old_paths)}" for note in found.legacy]
    if ref is None:
        view.no_summary = "No summary covers this yet."
        return
    read, _ = await calls.read(service, ref.id, view="summary")
    if read.doc is None:  # pragma: no cover - a doc id always reads as a doc, or raises
        raise DocketError(f"no doc {ref.id}")
    view.summary = summary_view(read.doc, state, cited)
    view.verify = panel_link(state.but(verify=True))
    view.verify_label = "Verify the summary"
