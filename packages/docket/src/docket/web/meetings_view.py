"""The Meetings page's parts (DR-0666, DR-0667, DR-0764, DR-0765): the list by day and the meeting panel, each a model
the app's JSON reads answer as it is (DR-0709).

Each builder takes what the ledger's views read (the snapshot, a meeting's show, its suggest) and the briefs its
people have, and does no I/O, so every rule here is tested on an invented ledger. Values come ready to show, times as
text in the owner's zone. The list goes by day from today, then the earlier days, newest first, each day by start; a
cancelled or declined meeting, and one with no points and no open items, is dimmed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from docket.ledger.views import Detail, LedgerRead, MeetingRow, PointRow
from docket.plaintext import fold, words
from docket.vault.anchors import AnchorError, parse_locator
from docket.vault.processed import DOC_TYPES
from docket.web import day_view
from docket.web.briefs_state import BriefsState
from docket.web.day_state import DayState
from docket.web.day_view import HistoryLine, Link, Row
from docket.web.recording_state import RecordingState

# The responses a meeting's badge says: the ones that change whether the owner goes (views.SAID).
BADGED = ("tentative", "declined", "cancelled")
DIMMED = ("declined", "cancelled")
RESPONSE_WORDS = {
    "accepted": "accepted",
    "tentative": "tentative",
    "declined": "declined",
    "none": "not answered yet",
    "organiser": "yours: you organise it",
    "cancelled": "cancelled",
}
# The label the sync gives a recording it links to its meeting by time (DR-0651).
RECORDING = "recording"


class MeetingLine(BaseModel):
    """A meeting as the list reads at a glance: its time, subject and badge, its points and its open items."""

    ref: str
    id: str
    subject: str
    # Its start and end as the list's margin writes them, `10:00-10:30`, and whole for a tooltip.
    time: str
    when: str
    # Its start, as an ISO time for a time element.
    start: str
    organiser: str
    stream: str
    # tentative, declined or cancelled, for its badge; None for any other response.
    response: str | None
    dim: bool
    over: bool
    # Under way now.
    now: bool
    # Its standing points, rolled ones aside, and how many of them are open.
    points: int
    points_open: int
    # Its open items.
    items: int


class Day(BaseModel):
    key: str
    label: str
    # Today or a day after it; the earlier days come after them, newest first.
    ahead: bool
    meetings: list[MeetingLine]


class MeetingsList(BaseModel):
    days: list[Day]
    counts: str
    shown: int
    empty: str
    # The ledger's streams, for the filter and a point's item.
    streams: list[str]


class Attendee(BaseModel):
    name: str
    # The person's brief, or "" when no person with a brief goes by the name (DR-0765).
    href: str


class MeetingLink(BaseModel):
    ref: str
    href: str


class PointLine(BaseModel):
    """A talking point as the panel shows it, named by its id, never its n (DR-0667)."""

    id: str
    n: int
    ref: str
    state: str
    text: str
    answer: str
    proposed: bool
    # The item it is about, and that item's panel on the Ledger.
    item: MeetingLink | None
    # The meeting a roll carried it from, and the one it rolled to, each that meeting's panel.
    came_from: MeetingLink | None
    went_to: MeetingLink | None


class Linked(BaseModel):
    """An item on the meeting, or one worth raising there with why, and its panel on the Ledger."""

    row: Row
    href: str
    why: str = ""


class MeetingPanel(BaseModel):
    """A meeting as its panel shows it (DR-0667): when and where, who, the recording, the talking points, the outcome
    and notes, its items, the suggestions when asked, its materials and its history."""

    ref: str
    id: str
    subject: str
    when: str
    start: str
    end: str
    organiser: Attendee | None
    where: str
    response: str | None
    # What the owner's response says, in words, for every response.
    response_words: str
    stream: str
    # No calendar event carries it: a meeting added by hand, which Claude joins to one (DR-0621).
    keyless: bool
    archived: bool
    over: bool
    # Once over with open points: that they roll on at the next sync (DR-0624).
    rolls: str
    attendees: list[Attendee]
    recordings: list[Link]
    points: list[PointLine]
    open_points: int
    proposed: int
    # The open points as text, for Copy agenda.
    agenda: str
    outcome: str
    notes: str
    items: list[Linked]
    # The items worth raising in it, once asked for (`suggest=1`); None until then.
    suggestions: list[Linked] | None
    materials: list[Link]
    history: list[HistoryLine]
    facts: str


# -- times ------------------------------------------------------------------------------------------------------------


def span(start: datetime, end: datetime, today: date) -> str:
    """A meeting's start and end in the list's margin: `10:00-10:30`, the end's day too when it ends on another."""
    until = f"{end:%H:%M}" if end.date() == start.date() else f"{day_view.day_text(end.date(), today)} {end:%H:%M}"
    return f"{start:%H:%M}-{until}"


def whole_when(start: datetime, end: datetime, today: date) -> str:
    """`Wed 21 Oct 10:00-10:30`, the year added when it isn't this year's."""
    return f"{day_view.day_text(start.date(), today)} {span(start, end, today)}"


def day_label(day: date, today: date) -> str:
    text = day_view.day_text(day, today)
    near = {0: "Today", 1: "Tomorrow", -1: "Yesterday"}.get((day - today).days)
    return f"{near}, {text}" if near else text


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


# -- the list ---------------------------------------------------------------------------------------------------------


def line_of(row: MeetingRow, points: Sequence[PointRow], today: date, at: datetime) -> MeetingLine:
    standing = [p for p in points if p.state != "rolled"]
    response = row.response if row.response in BADGED else None
    return MeetingLine(
        ref=row.ref,
        id=row.id,
        subject=row.subject,
        time=span(row.start, row.end, today),
        when=whole_when(row.start, row.end, today),
        start=row.start.isoformat(),
        organiser=row.organiser or "",
        stream=row.stream or "",
        response=response,
        dim=row.response in DIMMED or (not standing and not row.items),
        over=row.end <= at,
        now=row.start <= at < row.end,
        points=len(standing),
        points_open=row.points_open,
        items=len(row.items),
    )


def build_meetings(found: LedgerRead, state: DayState, matched: set[str] | None, at: datetime) -> MeetingsList:
    """Meetings' list (DR-0666): the snapshot's meetings, those `q` matches and in `stream`, by day from today, then
    the earlier days, newest first, each day by start."""
    today = found.today
    points: dict[str, list[PointRow]] = {}
    for p in found.points:
        points.setdefault(p.meeting, []).append(p)
    chosen = [
        m
        for m in found.meetings
        if (matched is None or m.id in matched) and (state.stream is None or m.stream == state.stream)
    ]
    by_day: dict[date, list[MeetingRow]] = {}
    for m in sorted(chosen, key=lambda m: (m.start, m.ref)):
        by_day.setdefault(m.start.date(), []).append(m)
    ahead = sorted(d for d in by_day if d >= today)
    earlier = sorted((d for d in by_day if d < today), reverse=True)
    days = [
        Day(
            key=d.isoformat(),
            label=day_label(d, today),
            ahead=d >= today,
            meetings=[line_of(m, points.get(m.ref, []), today, at) for m in by_day[d]],
        )
        for d in [*ahead, *earlier]
    ]
    shown = len(chosen)
    open_points = sum(m.points_open for m in chosen)
    window = ""
    if found.since is not None and found.until is not None:
        last = (found.until - timedelta(microseconds=1)).date()
        window = f" from {day_view.day_text(found.since.date(), today)} to {day_view.day_text(last, today)}"
    counts = f"{_plural(shown, 'meeting', 'meetings')}{window}; {_plural(open_points, 'point', 'points')} open"
    narrowed = matched is not None or state.stream is not None
    empty = ""
    if not chosen:
        empty = "Nothing here matches." if narrowed else "No meetings in these four weeks."
    streams = sorted({s.name for s in found.streams} | {i.stream for i in found.items})
    return MeetingsList(days=days, counts=counts, shown=shown, empty=empty, streams=streams)


# -- the panel --------------------------------------------------------------------------------------------------------


def meeting_href(ref: str) -> str:
    return DayState(page="meetings", open=ref).url()


def item_href(ref: str) -> str:
    return DayState(page="ledger", open=ref).url()


def folded_name(text: str) -> str:
    """A name as an attendee is matched to a person: its words folded, as the index folds an alias; an address
    lowercased."""
    if "@" in text and " " not in text.strip():
        return text.strip().casefold()
    return " ".join(fold(w) for w in words(text))


def attendee(name: str, briefs: Mapping[str, str]) -> Attendee:
    entity = briefs.get(folded_name(name))
    return Attendee(name=name, href=BriefsState(brief=f"brief:{entity}").url() if entity else "")


def material_link(ref: str, label: str | None, sources: Iterable[str]) -> Link:
    """A material as day_view links it, but a recording the sync linked by time (DR-0651), labelled so, on the
    Recording page rather than Search's."""
    link = day_view.link_of(ref, label, sources)
    if link.kind != "record" or (label or "").strip().casefold() != RECORDING:
        return link
    try:
        found = parse_locator(ref.strip(), DOC_TYPES)
    except AnchorError:
        return link
    if found.kind != "record":
        return link
    return Link(ref=ref, label=link.label, kind="recording", href=RecordingState(rid=found.value).link())


def point_line(p: PointRow) -> PointLine:
    return PointLine(
        id=p.id,
        n=p.n,
        ref=p.ref,
        state=p.state,
        text=p.text,
        answer=p.answer or "",
        proposed=p.proposed,
        item=MeetingLink(ref=p.item, href=item_href(p.item)) if p.item else None,
        came_from=MeetingLink(ref=p.from_, href=meeting_href(p.from_)) if p.from_ else None,
        went_to=MeetingLink(ref=p.to, href=meeting_href(p.to)) if p.to else None,
    )


def _rolls(over: bool, archived: bool, open_points: int) -> str:
    if not over or archived or not open_points:
        return ""
    said = _plural(open_points, "open point rolls", "open points roll")
    return f"It's over: its {said} on to the next meeting of its series at the next sync."


def build_meeting_panel(
    shown: LedgerRead,
    suggested: LedgerRead | None,
    *,
    briefs: Mapping[str, str],
    sources: Iterable[str],
    tz: ZoneInfo,
    at: datetime,
) -> MeetingPanel:
    """The meeting panel from its show (DR-0667), with its suggestions when asked and its people's briefs."""
    row = shown.meetings[0]
    # Show always gives its object's detail.
    detail = cast(Detail, shown.detail)
    today = shown.today
    named = list(sources)
    points = [point_line(p) for p in shown.points]
    open_points = [p for p in points if p.state == "open"]
    over = row.end <= at
    when = whole_when(row.start, row.end, today)
    agenda = "\n".join([f"{row.subject}, {when}", *(f"- {p.text}" for p in open_points)]) if open_points else ""
    linked = [Linked(row=day_view.row_of(r, today), href=item_href(r.ref)) for r in shown.items]
    suggestions = None
    if suggested is not None:
        suggestions = [
            Linked(row=day_view.row_of(r, today), href=item_href(r.ref), why=r.why or "") for r in suggested.items
        ]
    added = f"added by {detail.added_by}"
    if detail.series:
        added += "; one of a series"
    return MeetingPanel(
        ref=row.ref,
        id=row.id,
        subject=row.subject,
        when=when,
        start=row.start.isoformat(),
        end=row.end.isoformat(),
        organiser=attendee(row.organiser, briefs) if row.organiser else None,
        where=detail.where or "",
        response=row.response if row.response in BADGED else None,
        response_words=RESPONSE_WORDS.get(row.response or "none", row.response or ""),
        stream=row.stream or "",
        keyless=not detail.event,
        archived=row.archived,
        over=over,
        rolls=_rolls(over, row.archived, len(open_points)),
        attendees=[attendee(name, briefs) for name in detail.attendees],
        recordings=[
            link for m in detail.materials if (link := material_link(m.ref, m.label, named)).kind == "recording"
        ],
        points=points,
        open_points=len(open_points),
        proposed=sum(1 for p in points if p.proposed and p.state != "rolled"),
        agenda=agenda,
        outcome=detail.outcome or "",
        notes=detail.notes or "",
        items=linked,
        suggestions=suggestions,
        materials=[material_link(m.ref, m.label, named) for m in detail.materials],
        history=day_view.history_lines(detail.history, tz),
        facts=added,
    )
