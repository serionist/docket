"""What the Briefs page shows, worked out from the stale report and the brief as read and verified (DR-0780).

| The URL | Shows |
|---|---|
| no brief | the briefs by entity type: rev, how long since, flagged lines, new records; an orphan's survivor |
| a brief | its lines, flagged ones marked, beside the open line's raw and the doc lines it cites, one level |
| a brief a never-store list holds | that it is hidden, and nothing of it |
| a brief that isn't there | that there is none |
| a project, topic or stream with decisions and no brief | that it has no brief yet, then its decisions |

A line a never-store list now names is shown as hidden and opens nothing (DR-0558). `build` takes the facts and does no
I/O, so every rule here is tested on what the facts hold. The models are what `GET /api/web/briefs` answers (DR-0709):
values ready to show, links as the server's URLs. Text from records and docs is data: the app shows it as text, and no
link is made from it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from docket.briefs import BriefsReport, BriefState, State
from docket.recall import DocLine, DocView, VerifiedLine
from docket.vault.anchors import Anchor, format_anchor, trailing_anchors
from docket.vault.decision_form import NOT_A_DECISION, NOT_CONFIRMED
from docket.vault.spans import Span
from docket.web.briefs_state import ORPHANED, TYPES, BriefsState
from docket.web.day_state import DayState
from docket.web.panel import SpanView, Style, anchored_rid, frontmatter_end, line_parts, span_view
from docket.web.recording_state import RecordingState, time_at
from docket.web.state import SearchState
from docket.web.sync_view import ago

Mode = Literal["list", "brief", "entity", "hidden", "missing"]
HIDDEN_LINE = "a line hidden: it names a never-store value"


@dataclass(frozen=True)
class Facts:
    """What the page shows, read from the vault: the stale report, and for an open brief its state in it, the brief as
    read, the lines a list now names, and the open line verified with which of its cited lines a list now names."""

    now: datetime
    tz: ZoneInfo
    report: BriefsReport
    found: BriefState | None = None
    hidden: bool = False
    doc: DocView | None = None
    hidden_lines: frozenset[int] = frozenset()
    line: VerifiedLine | None = None
    hidden_refs: frozenset[int] = frozenset()
    # A Decisions line citing no confirmed decision, from a brief stored before decisions came (DR-0565).
    unconfirmed: frozenset[int] = frozenset()
    # A project, topic or stream with decisions and no brief: its name (DR-0781).
    entity_name: str | None = None
    # The list's waiting decisions: (entity id, name, how many), by name.
    waiting: tuple[tuple[str, str, int], ...] = ()


class BriefLink(BaseModel):
    text: str
    # The link's URL, written by the server (DR-0704); "" for an anchor that names no record.
    url: str
    title: str = ""


class BriefRow(BaseModel):
    id: str
    name: str
    url: str
    state: State
    rev: int
    age: str
    new: int
    flagged: int
    # Why it is stale beyond new records and flagged lines, as the stale report says it.
    why: str | None
    orphan: str


class BriefGroup(BaseModel):
    type: str
    rows: list[BriefRow]


class TypeChoice(BaseModel):
    """A tab narrowing the list to one entity type, or to every brief; `name` is "All" for every brief."""

    name: str
    count: int
    url: str
    on: bool


class BriefLine(BaseModel):
    n: int
    body: str
    style: Style
    # The line's own URL, opening its raw, ending in #raw; None for a line that cites nothing.
    url: str | None
    anchors: list[BriefLink]
    refs: list[BriefLink]
    flagged: bool
    hidden: bool
    open: bool
    unconfirmed: bool = False


class RawSpan(BaseModel):
    span: SpanView
    link: BriefLink | None


class CitedLine(BaseModel):
    """A doc line the open line was built from, as it reads now, with its own raw."""

    doc: str
    n: int | None
    link: BriefLink
    text: str
    hidden: bool
    problem: str | None
    spans: list[RawSpan]


class RawView(BaseModel):
    n: int
    body: str = ""
    hidden: bool = False
    problems: list[str] = []
    flags: list[str] = []
    spans: list[RawSpan] = []
    cited: list[CitedLine] = []
    empty: str = ""
    # The brief with no line open: where the panel's close goes.
    close: str = ""


class BriefsView(BaseModel):
    mode: Mode
    title: str = "Briefs"
    message: str = ""
    link: BriefLink | None = None
    # The list.
    summary: str = ""
    types: list[TypeChoice] = []
    groups: list[BriefGroup] = []
    hidden_note: str = ""
    # What an empty list says, and what to do about it.
    empty: str = ""
    empty_how: str = ""
    # The entities with decisions waiting for the owner, linked to their page.
    to_decide: list[BriefLink] = []
    # An open brief.
    id: str = ""
    state: State | None = None
    # Its entity type, rev and age, each said on its own.
    meta: list[str] = []
    # The list, with the brief's type kept.
    back: str = ""
    notices: list[str] = []
    new: list[BriefLink] = []
    lines: list[BriefLine] = []
    raw: RawView | None = None
    choose: str = ""
    # A person's items on the Ledger (DR-0669).
    ledger: BriefLink | None = None


def build(facts: Facts, state: BriefsState) -> BriefsView:
    if state.brief is None:
        return _list(facts, state)
    if facts.hidden:
        return BriefsView(
            mode="hidden",
            title="A hidden brief",
            message="A never-store list holds this brief's entity, so the page shows nothing of it.",
            link=BriefLink(text="All briefs", url=state.but(brief=None, line=None).url()),
        )
    if facts.found is None and facts.entity_name is not None:
        return BriefsView(
            mode="entity",
            title=facts.entity_name,
            message="No brief yet. The brief skill writes one when you ask Claude for it.",
            back=state.but(brief=None, line=None).url(),
        )
    if facts.found is None or facts.doc is None:
        return BriefsView(
            mode="missing",
            title=state.brief,
            message=f"No brief {state.brief}. The brief skill writes one when you ask Claude for it.",
            link=BriefLink(text="All briefs", url=state.but(brief=None, line=None).url()),
        )
    return _brief(facts, state, facts.found, facts.doc)


# -- the list --------------------------------------------------------------------------------------------------------


def _group(brief: BriefState) -> str:
    return ORPHANED if brief.state == "orphaned" else brief.type


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _age(brief: BriefState, now: datetime) -> str:
    return ago(datetime.fromisoformat(brief.rev_time), now) if brief.rev_time else ""


def _orphan(brief: BriefState) -> str:
    if brief.state != "orphaned":
        return ""
    if brief.orphaned:
        return f"its entity is gone; {brief.orphaned} holds its name now"
    return "its entity is gone, and no one entity holds its name"


def _list(facts: Facts, state: BriefsState) -> BriefsView:
    every = facts.report.briefs
    view = BriefsView(mode="list")
    present = sorted({_group(b) for b in every}, key=TYPES.index)
    counts = {kind: sum(1 for b in every if _group(b) == kind) for kind in present}
    view.types = [TypeChoice(name="All", count=len(every), url=state.but(type=None).url(), on=state.type is None)]
    view.types += [
        TypeChoice(name=kind, count=counts[kind], url=state.but(type=kind).url(), on=state.type == kind)
        for kind in present
    ]
    shown = [b for b in every if state.type is None or _group(b) == state.type]
    for kind in sorted({_group(b) for b in shown}, key=TYPES.index):
        rows = sorted((b for b in shown if _group(b) == kind), key=lambda b: (b.name.casefold(), b.id))
        view.groups.append(BriefGroup(type=kind, rows=[_row(b, facts.now, state) for b in rows]))
    view.to_decide = [
        BriefLink(text=f"{name}: {_plural(n, 'decision', 'decisions')}", url=state.but(brief=f"brief:{entity}").url())
        for entity, name, n in facts.waiting
    ]
    by_state = [(sum(1 for b in every if b.state == s), s) for s in ("stale", "current", "orphaned")]
    said = ", ".join(f"{n} {s}" for n, s in by_state if n)
    view.summary = f"{_plural(len(every), 'brief', 'briefs')}: {said}" if every else ""
    hidden = facts.report.hidden
    if hidden:
        view.hidden_note = (
            "1 brief hidden: a never-store list holds its entity."
            if hidden == 1
            else f"{hidden} briefs hidden: a never-store list holds their entities."
        )
    if not every:
        view.empty = "No briefs yet."
        view.empty_how = "Ask Claude for a brief on someone or something, and the brief skill writes one."
        view.types = []
    elif not shown:
        view.empty = f"No {state.type} briefs."
    return view


def _row(brief: BriefState, now: datetime, state: BriefsState) -> BriefRow:
    return BriefRow(
        id=brief.id,
        name=brief.name,
        url=state.but(brief=brief.id, line=None).url(),
        state=brief.state,
        rev=brief.rev,
        age=_age(brief, now),
        new=brief.new.count,
        flagged=brief.flagged,
        why=brief.why,
        orphan=_orphan(brief),
    )


# -- an open brief ---------------------------------------------------------------------------------------------------


def _brief(facts: Facts, state: BriefsState, found: BriefState, doc: DocView) -> BriefsView:
    view = BriefsView(mode="brief", title=found.name, id=found.id, state=found.state)
    age = _age(found, facts.now)
    view.meta = [p for p in (found.type or ORPHANED, f"rev {found.rev}", age) if p]
    view.back = state.but(brief=None, line=None).url()
    if found.type == "person":
        view.ledger = BriefLink(text=f"{found.name}'s items on the Ledger", url=DayState(who=found.name).url())
    for why in found.why.split("; ") if found.why else []:
        if why == NOT_CONFIRMED:
            view.notices.append(f"Stale: {why}; marked below. The brief skill's next rev cites the log's decisions.")
        else:
            view.notices.append(f"Stale: {why}, so Docket can't tell which records it hasn't seen.")
    if found.new.count:
        newest = " (the newest five)" if found.new.count > len(found.new.newest) else ""
        view.notices.append(
            f"{_plural(found.new.count, 'record', 'records')} about {found.name} since rev {found.rev}{newest}:"
        )
        view.new = [
            BriefLink(text=rid, url=SearchState(hit=f"r:{rid}").url(), title=f"Open {rid} on the Search page")
            for rid in found.new.newest
        ]
    if found.flagged:
        cites = _plural(found.flagged, "line cites", "lines cite")
        view.notices.append(f"{cites} a line that changed since; marked below.")
    if found.state == "orphaned":
        view.notices.append(_orphan(found)[0].upper() + _orphan(found)[1:] + ".")
    start = frontmatter_end(doc)
    for line in doc.lines:
        if line.n > start:
            view.lines.append(_line(line, doc, facts, state))
    if state.line is None:
        view.choose = "Choose a line to see the raw it cites and the doc lines it was built from."
    else:
        flags = next((line.flags for line in doc.lines if line.n == state.line), list[str]())
        view.raw = _raw(facts, state.line, list(flags))
        view.raw.close = state.but(line=None).url()
    return view


def _line(line: DocLine, doc: DocView, facts: Facts, state: BriefsState) -> BriefLine:
    # The page marks a Decisions line no confirmed decision backs on its own; any other flag is a cited line's.
    n, text, flagged = line.n, line.text, any(f != NOT_A_DECISION for f in line.flags)
    if n in facts.hidden_lines:
        return BriefLine(
            n=n,
            body=HIDDEN_LINE,
            style="text",
            url=None,
            anchors=[],
            refs=[],
            flagged=False,
            hidden=True,
            open=state.line == n,
        )
    style, body, anchors = line_parts(text)
    trailing = trailing_anchors(text)
    links = [anchor_link(anchored_rid(a, doc.covers), a) for a in anchors]
    refs = [doc_link(r.doc, r.line) for r in trailing.refs]
    url = state.but(line=n).link() if anchors or trailing.refs else None
    open_ = state.line == n
    unconfirmed = n in facts.unconfirmed
    return BriefLine(
        n=n,
        body=body,
        style=style,
        url=url,
        anchors=links,
        refs=refs,
        flagged=flagged,
        hidden=False,
        open=open_,
        unconfirmed=unconfirmed,
    )


def anchor_link(rid: str | None, anchor: Anchor) -> BriefLink:
    """Where an anchor links: a time to the Recording page at it, anything else to the Search page."""
    if rid is None:
        return BriefLink(text=anchor.token, url="")
    if anchor.kind == "time":
        url = RecordingState(rid=rid, at=time_at(anchor.start, anchor.end)).link()
        return BriefLink(text=anchor.token, url=url, title=f"{anchor.token}: the recording at this time")
    hit = format_anchor(rid, anchor.kind, anchor.start, anchor.end)[1:-1]
    return BriefLink(text=anchor.token, url=SearchState(hit=hit).url(), title=f"Open {hit} on the Search page")


def doc_link(doc: str, n: int | None) -> BriefLink:
    """A doc line a line cites, on the Search page with that line marked."""
    lines = (str(n),) if n is not None else ()
    text = f"{doc}, line {n}" if n is not None else doc
    return BriefLink(text=text, url=SearchState(hit=doc, lines=lines).url(), title=f"Open {doc} on the Search page")


def _span(span: Span, tz: ZoneInfo) -> RawSpan:
    found = trailing_anchors(span.anchor).anchors
    link = None
    if span.rid and found:
        anchor = found[0]
        target = anchor_link(span.rid, anchor)
        label = "On the Recording page" if anchor.kind == "time" else "On the Search page"
        link = BriefLink(text=label, url=target.url, title=target.title)
    return RawSpan(span=span_view(span, tz), link=link)


def _raw(facts: Facts, n: int, flags: list[str]) -> RawView:
    if n in facts.hidden_lines:
        said = f"Line {n} is hidden: it names a never-store value, so nothing of it opens."
        return RawView(n=n, hidden=True, empty=said)
    line = facts.line
    if line is None:  # pragma: no cover - gather verifies the open line whenever it isn't hidden
        return RawView(n=n, empty=f"Line {n} has nothing to open.")
    # A mark read already gives the line is said once, among its flags.
    problems = [p for p in line.problems if p not in flags]
    view = RawView(n=n, body=line_parts(line.text)[1], problems=problems, flags=flags)
    view.spans = [_span(s, facts.tz) for s in line.spans]
    for i, ref in enumerate(line.refs):
        hidden = i in facts.hidden_refs
        spans = [] if hidden else [_span(s, facts.tz) for s in ref.spans]
        text = HIDDEN_LINE if hidden else line_parts(ref.text)[1]
        cited = CitedLine(
            doc=ref.doc,
            n=ref.n,
            link=doc_link(ref.doc, ref.n),
            text=text,
            hidden=hidden,
            problem=ref.problem,
            spans=spans,
        )
        view.cited.append(cited)
    if not (view.spans or view.cited or view.problems):
        view.empty = f"Line {n} cites nothing: it has no anchors or refs."
    return view
