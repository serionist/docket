"""The Today page's parts (DR-0669, DR-0773): the owner's day as `docket ledger day` reads it, a model the app's JSON
read answers as it is (DR-0709).

The day comes from `ledger.day.build` over the snapshot from today to the next working day, so the page and the
session start's lines agree: the meetings left today and the next working day's, not cancelled or declined, each with
its open points and open items; what is overdue, due today or back from a snooze; the items closed today; and the
last sync per live source with its due mark, the sidebar's. It reads at a glance in that order: the next meeting with
its prep first, then the items due, then the rest.

Each builder takes what was read and does no I/O, so every rule here is tested on an invented ledger. Values come
ready to show, times as text in the owner's zone; nothing here counts down, so the list goes out of date only when a
meeting starts or ends, a sync falls due, or the day turns (`until`).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from docket.ledger import day as ledger_day
from docket.ledger.day import DayMeeting, SyncMark
from docket.ledger.views import ItemRow, LedgerRead, PointRow
from docket.vault.cursors import DUE_AFTER
from docket.web import day_view, meetings_view
from docket.web.day_state import DEFAULT_K
from docket.web.day_view import Group, Row, Tone
from docket.web.meetings_view import MeetingLine
from docket.web.sync_view import SyncState

# The prep a meeting after the next one shows in the list, as `docket ledger day` cuts it; its panel has the rest.
POINTS_SHOWN = ledger_day.POINTS_A_MEETING
ITEMS_SHOWN = ledger_day.ITEMS_A_MEETING
# The due groups in the order the morning reads them: late first, then today's, then those back from a snooze.
DUE_GROUPS = ("overdue", "today", "woke")
# The most due rows the list draws, across its groups, as the Ledger's first page; the rest are on the Ledger.
DUE_SHOWN = DEFAULT_K


class Prep(BaseModel):
    """An open talking point as the list shows it: read only, its buttons on the meeting's panel."""

    id: str
    text: str
    answer: str
    # Claude proposed it and it isn't kept yet.
    proposed: bool


class DayMeetingLine(BaseModel):
    """A meeting of the day: its line as Meetings shows it, where and with whom, and its prep, the open points and the
    open items on it, each to the count the list shows and how many more."""

    line: MeetingLine
    where: str
    # Its attendees as a sentence, for the next meeting's; "" for the others, whose show isn't read.
    people: str
    points: list[Prep]
    more_points: int
    items: list[Row]
    more_items: int


class MeetingGroup(BaseModel):
    key: str
    label: str
    meetings: list[DayMeetingLine]


class Lead(BaseModel):
    """The next meeting: under way now, or the first still to start today, else the next working day's first."""

    label: str
    meeting: DayMeetingLine


class SyncLine(BaseModel):
    """A live source's last sync: when, in words that never count down, and whether it is due one, as the sidebar's
    Sync mark says (DR-0304); one with a run open is syncing, never due."""

    source: str
    said: str
    title: str
    due: bool
    running: bool
    href: str


class TodayList(BaseModel):
    # The day in words, `Tuesday 20 October`.
    day: str
    lead: Lead | None
    # Overdue, due today and back from a snooze, as the Ledger's due groups, each by due, opened and number.
    due: list[Group]
    # The meetings after the next: later today, then the next working day's.
    meetings: list[MeetingGroup]
    closed: Group | None
    syncs: list[SyncLine]
    # Why this machine's cursor file doesn't read, in its own words.
    sync_problem: str
    counts: str
    empty: str
    streams: list[str]
    # When the list goes out of date with no write: a meeting starts or ends, a sync falls due, or the day turns.
    until: str


# -- words ------------------------------------------------------------------------------------------------------------


def long_day(day: date) -> str:
    """`Tuesday 20 October`, the year added when it isn't this year's."""
    return f"{day:%A} {day.day} {day:%B}"


def people_of(names: Sequence[str]) -> str:
    """Attendees as a sentence: `Ben Sample, Ada Example and Cleo Example`."""
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def lead_label(m: DayMeeting, at: datetime, today: date) -> str:
    """What the next meeting is to the owner now: under way until its end, next today, or next on a later day."""
    row = m.meeting
    if row.start <= at < row.end:
        return f"Now, until {row.end:%H:%M}"
    day = row.start.date()
    if day == today:
        return "Next"
    near = "tomorrow" if (day - today).days == 1 else f"on {day_view.day_text(day, today)}"
    return f"Next, {near}"


def synced(mark: SyncMark, at: datetime, tz: ZoneInfo, running: bool) -> SyncLine:
    """A source's line: `synced 09:10`, `synced yesterday 17:40`, `synced Sun 18 Oct`, or never; the machine whose
    mark it is in the title."""
    href = SyncState(source=mark.source).url()
    if running:
        return SyncLine(source=mark.source, said="syncing now", title="A run of it is open", due=False,
                        running=True, href=href)  # fmt: skip
    if mark.mark is None:
        title = "No machine has synced it yet"
        return SyncLine(source=mark.source, said="never synced", title=title, due=mark.due, running=False, href=href)
    local = mark.mark.astimezone(tz)
    today = at.astimezone(tz).date()
    gap = (today - local.date()).days
    when = day_view.day_text(local.date(), today)
    if gap in (0, 1):
        when = f"{'yesterday ' if gap else ''}{local:%H:%M}"
    whole = f"{day_view.day_text(local.date(), today)} {local:%H:%M}"
    title = f"Synced on {mark.machine}, {whole}" if mark.machine else f"Synced {whole}"
    return SyncLine(source=mark.source, said=f"synced {when}", title=title, due=mark.due, running=False, href=href)


# -- the list ---------------------------------------------------------------------------------------------------------


def _meeting_line(
    m: DayMeeting, points: Sequence[PointRow], today: date, at: datetime, *, whole: bool, people: str = ""
) -> DayMeetingLine:
    shown_points = m.points if whole else m.points[:POINTS_SHOWN]
    shown_items = m.items if whole else m.items[:ITEMS_SHOWN]
    return DayMeetingLine(
        line=meetings_view.line_of(m.meeting, points, today, at),
        where="",
        people=people,
        points=[Prep(id=p.id, text=p.text, answer=p.answer or "", proposed=p.proposed) for p in shown_points],
        more_points=len(m.points) - len(shown_points),
        items=[day_view.row_of(i, today) for i in shown_items],
        more_items=len(m.items) - len(shown_items),
    )


def due_groups(rows: Sequence[ItemRow], today: date, shown: int | None = None) -> list[Group]:
    """The Ledger's due groups for the items `docket ledger day` lists as overdue or due today, in the morning's order,
    each with its quick buttons and Select all; at most `shown` rows across them, each group's total over all."""
    grouped: dict[str, tuple[str, Tone, list[ItemRow]]] = {
        key: (label, tone, members) for key, label, tone, members in day_view.due_groups(rows, today)
    }
    out: list[Group] = []
    left = DUE_SHOWN if shown is None else shown
    for key in DUE_GROUPS:
        label, tone, members = grouped[key]
        drawn = [day_view.row_of(r, today) for r in members[: max(left, 0)]]
        left -= len(drawn)
        if members:
            out.append(Group(key=key, label=label, tone=tone, rows=drawn, total=len(members),
                             selectable=any(r.callable for r in drawn)))  # fmt: skip
    return out


def next_change(day: ledger_day.Day, syncs: Iterable[SyncMark], at: datetime, tz: ZoneInfo) -> datetime:
    """The first moment after `at` the list reads differently with no write: Meetings' rule over the day's meetings,
    or a sync falling due a day after its mark."""
    meetings = [dm.meeting for d in day.meetings for dm in d.meetings]
    turns = meetings_view.next_change(meetings, at, day.today, tz)
    due = [s.mark + DUE_AFTER for s in syncs if s.mark is not None and not s.due and s.mark + DUE_AFTER > at]
    return min([turns, *due])


def build_today(
    found: LedgerRead,
    closed: Sequence[ItemRow],
    lead_show: LedgerRead | None,
    *,
    at: datetime,
    tz: ZoneInfo,
    syncs: Sequence[SyncMark],
    sync_problem: str | None,
    running: Iterable[str],
) -> TodayList:
    """Today's list from the snapshot today to the next working day, the items closed today, the next meeting's show
    for where it is and who is in it, and the live sources' marks."""
    day = ledger_day.build(found, at, syncs=syncs, sync_problem=sync_problem)
    today = day.today
    points: dict[str, list[PointRow]] = {}
    for p in found.points:
        points.setdefault(p.meeting, []).append(p)
    days = [(d.day, d.meetings) for d in day.meetings]
    lead: Lead | None = None
    if days:
        first = days[0][1][0]
        detail = lead_show.detail if lead_show is not None else None
        people = people_of(detail.attendees) if detail is not None else ""
        line = _meeting_line(first, points.get(first.meeting.ref, []), today, at, whole=True, people=people)
        line.where = (detail.where or "") if detail is not None else ""
        lead = Lead(label=lead_label(first, at, today), meeting=line)
        days[0] = (days[0][0], days[0][1][1:])
    groups: list[MeetingGroup] = []
    for on, meetings in days:
        if not meetings:
            continue
        label = "Later today" if on == today else meetings_view.day_label(on, today)
        groups.append(
            MeetingGroup(
                key=on.isoformat(),
                label=label,
                meetings=[_meeting_line(m, points.get(m.meeting.ref, []), today, at, whole=False) for m in meetings],
            )
        )
    overdue = next(s for s in day.sections if s.name == "overdue").items
    due = due_groups(overdue, today)
    ended = sorted((r for r in closed if r.closed == today), key=lambda r: (r.state, r.number or 0, r.id))
    closed_group = None
    if ended:
        rows = [day_view.row_of(r, today) for r in ended]
        closed_group = Group(key="closed", label="Closed today", tone="", rows=rows, total=len(rows), selectable=False)
    busy = set(running)
    empty = ""
    if lead is None and not due and closed_group is None:
        empty = "Nothing on today"
    return TodayList(
        day=long_day(today),
        lead=lead,
        due=due,
        meetings=groups,
        closed=closed_group,
        syncs=[synced(s, at, tz, s.source in busy) for s in syncs],
        sync_problem=sync_problem or "",
        counts=counts_of(day, overdue),
        empty=empty,
        streams=sorted({s.name for s in found.streams} | {i.stream for i in found.items}),
        until=next_change(day, syncs, at, tz).isoformat(),
    )


def counts_of(day: ledger_day.Day, overdue: Sequence[ItemRow]) -> str:
    """The day in one line: what is overdue and due today, the meetings left today and on the next working day, and
    the snoozes that ended; a part with nothing in it says so in words, never as a row of noughts."""
    today = day.today
    late = sum(1 for r in overdue if r.due is not None and r.due < today)
    due_today = sum(1 for r in overdue if r.due == today)
    due = f"{late} overdue, {due_today} due today" if late or due_today else "nothing due"
    left = sum(len(d.meetings) for d in day.meetings if d.day == today)
    meetings = f"{_plural(left, 'meeting', 'meetings')} left today" if left else "no meeting left today"
    after = next((d for d in day.meetings if d.day != today), None)
    if after is not None:
        near = "tomorrow" if (after.day - today).days == 1 else f"on {day_view.day_text(after.day, today)}"
        meetings += f", {len(after.meetings)} {near}"
    said = f"{due}; {meetings}"
    woke = sum(1 for r in overdue if r.woke)
    return said + (f"; {_plural(woke, 'snooze', 'snoozes')} ended" if woke else "")
