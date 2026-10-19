"""The owner's day (DR-0670 to DR-0672): todo.py's dashboard, built from one snapshot read of the ledger's views
(DR-0638) and the Sync page's due rule (DR-0304), for the session start's hook, `docket ledger day` and the Today page.

    day Tue 10-20; sync calls 3h, chat 2d, mail never; due: chat, mail (ask Claude to sync them)
    queue: 3 untriaged; discuss 1, me 1
    discuss
      A-0003 [task] Cleo Example: Book the room (2d, due 10-22) [ops] <discuss>
    me: A-0007
    meetings today 10-20
    M-0001 10-20 14:00-14:30 Pilot sync, Ada Example [pilot]; 1 open; items A-0001
      * The pilot's dates
      A-0001 Send Ada the dates (due 10-18)
    overdue or due today (1)
    A-0001 [reply] Ada Example: Send Ada the dates (10d, due 10-18, 2d late) [pilot] @M-0001
    streams (1)
    pilot: Waiting on the venue -> Book the room (10-30)

`Day` holds what each section lists, uncut, for a page; `lines` says it in at most `room` lines, giving rows back from
the streams up when it must, each cut section keeping its count and the read that lists it. An item is listed once
among overdue, replies, decisions and tasks, invites and waiting, in the first that holds it, a section's cap aside;
the queue and a meeting may repeat it. Rows are the ledger tool's own lines, the source left out. Nothing here writes,
and a read takes no lock of the vault's.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from docket.errors import DocketError
from docket.index.build import ledger_tables
from docket.ledger import ops, status, store, views
from docket.ledger.views import ItemRow, LedgerRead, MeetingRow, PointRow, StreamRow, item_line, meeting_line
from docket.vault import cursors, rendered
from docket.vault.vault import Vault

# The most lines the session start says, its status lines and the day's together (DR-0670).
ROOM = 45
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
# A meeting the owner won't be in: it leaves the day.
GONE = ("cancelled", "declined")
# A wait on someone listed once it is this many days old, as todo.py listed it.
WAITING_DAYS = 3
WEEK = timedelta(days=7)
Section = Literal["overdue", "replies", "decisions", "invites", "waiting"]
# Each section's heading, its cap, the read that lists it whole, and its turn to be cut (None: never), in todo.py's
# order. Rows a cap leaves out stay in the section's count.
SECTIONS: dict[Section, tuple[str, int, str, int | None]] = {
    "overdue": ("overdue or due today", 10, "the ledger tool's snapshot", None),
    "replies": ("replies owed", 10, "the ledger tool's list, kind reply", 5),
    "decisions": ("decisions and tasks due within 7 days", 8, "the ledger tool's list, kind task or decision", 4),
    "invites": ("invites", 5, "the ledger tool's list, kind invite", 3),
    "waiting": (f"waiting on others over {WAITING_DAYS} days", 6, "the ledger tool's list, kind waiting", 2),
}
STREAMS_CUT = 1
QUEUE_CUT = 6
# A meeting's points and items to the counts its line says, then the next working day's meetings to a count.
POINTS_CUT = 7
NEXT_DAY_CUT = 8
QUEUE_ROWS = 6
MEETINGS_A_DAY = 6
POINTS_A_MEETING = 4
ITEMS_A_MEETING = 3
WHERE = 70
QUEUE_READ = "the ledger tool's queue"
MEETINGS_READ = "the ledger tool's meetings"
STREAMS_READ = "the ledger tool's snapshot"
LAST = (
    "more: the ledger tool reads the ledger (snapshot, queue, list, show, meetings, suggest), ledger_batch changes "
    "it, run_open syncs a source"
)


class SyncMark(BaseModel):
    """A source that syncs live: the newest mark any machine has of it, which machine's, and whether it is due a sync
    (DR-0304). A source with a run open is never due."""

    source: str
    machine: str | None
    mark: datetime | None
    due: bool


class DayMeeting(BaseModel):
    """A meeting of the day, with its open points and its open items."""

    meeting: MeetingRow
    points: list[PointRow]
    items: list[ItemRow]


class MeetingDay(BaseModel):
    day: date
    meetings: list[DayMeeting]


class ItemSection(BaseModel):
    """A section's every item, its cap aside."""

    name: Section
    items: list[ItemRow]


class StatusFile(BaseModel):
    """A stream's STATUS.md `docket vault check` names: `owner` when the render leaves it, a hand edit among them,
    which only the owner settles; else `docket render` settles it (DR-0675)."""

    path: str
    what: str
    owner: bool


class Day(BaseModel):
    """The owner's day at `at`: the live sources' syncs, or why this machine's cursor file doesn't read; the queue,
    every open item with a snooze whose day has come, untriaged first, then by verdict, each by due; today's meetings
    not yet ended and the next working day's; the item sections; the decisions and tasks listed nowhere above, not due
    this week; the streams not closed; the status files the check names; the calendar records a stopped step left
    pending (DR-0647)."""

    today: date
    at: datetime
    syncs: list[SyncMark] = []
    sync_problem: str | None = None
    queue: list[ItemRow] = []
    meetings: list[MeetingDay] = []
    sections: list[ItemSection] = []
    later: int = 0
    streams: list[StreamRow] = []
    status: list[StatusFile] = []
    calendar_pending: int = 0


class DayLine(BaseModel):
    """A line of the day; `owner` when it asks something of the owner: a sync due, a status file a hand edit holds."""

    text: str
    owner: bool = False


class DayAnswer(BaseModel):
    """What `GET /api/day` answers: the day's lines in at most the room asked; the one line of counts a session
    outside the vault's repo gets instead (DR-0671); and that repo, the folder the hook compares its own with."""

    today: date
    repo: str
    counts: str
    lines: list[DayLine]


# -- reading it -------------------------------------------------------------------------------------------------------


def next_working_day(day: date) -> date:
    """The next day from Monday to Friday: a Friday's, a Saturday's and a Sunday's is Monday."""
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def read(vault: Vault, at: datetime, running: Iterable[str] = ()) -> Day:
    """The day at `at` from one snapshot read, today to the next working day, with the streams' whole fields from the
    same tables; then the cursor files, the status files and the pending calendar records. `running` names the sources
    with a run open, which the service knows."""
    tz = ZoneInfo(vault.config.owner.time_zone)
    today = at.astimezone(tz).date()
    ask = views.Ask(view="snapshot", since=today.isoformat(), until=next_working_day(today).isoformat())
    with ledger_tables(vault) as conn:
        found = views.read(conn, ask, tz, at)
        fields = status.stream_fields(conn)
    syncs, problem = sync_marks(vault, at, running)
    files = [
        StatusFile(path=path, what=what, owner=what not in (status.STALE, status.NOT_YET))
        for path, what in status.checked(vault, fields)
    ]
    return build(found, at, syncs=syncs, sync_problem=problem, files=files,
                 pending=len(store.calendar_pending(vault)))  # fmt: skip


def sync_marks(vault: Vault, now: datetime, running: Iterable[str] = ()) -> tuple[list[SyncMark], str | None]:
    """Each source that syncs live, by name (tools listed, on, not private), with its newest mark and whether it is
    due; or none, and why this machine's cursor file doesn't read. Another machine's file that doesn't read is left
    out, as the Sync page leaves it."""
    live = sorted(
        name for name, source in vault.config.sources.items()
        if source.tools and source.enabled and source.sensitivity != "private"
    )  # fmt: skip
    if not live:
        return [], None
    try:
        own = cursors.read(vault.root, vault.machine)
    except DocketError as e:
        return [], str(e)
    others, _ = cursors.read_others(vault.root, vault.machine)
    busy = set(running)
    out: list[SyncMark] = []
    for name in live:
        found = cursors.marks(name, own, others, vault.machine, now)
        machine, mark = found[0] if found else (None, None)
        out.append(SyncMark(source=name, machine=machine, mark=mark,
                            due=name not in busy and cursors.due(found, now)))  # fmt: skip
    return out, None


def build(
    found: LedgerRead,
    at: datetime,
    *,
    syncs: Sequence[SyncMark] = (),
    sync_problem: str | None = None,
    files: Sequence[StatusFile] = (),
    pending: int = 0,
) -> Day:
    """The day from a snapshot read whose window starts today: the sections worked out, nothing cut."""
    today = found.today
    after = next_working_day(today)
    # Open now: an open item, or a snooze whose day has come (DR-0660).
    shown = [i for i in found.items if i.state == "open" or i.woke]
    rank = {verdict: k for k, verdict in enumerate((None, *ops.VERDICTS))}
    queue = sorted(shown, key=lambda i: (rank.get(i.triage, len(rank)), i.triage or "", i.due or date.max))
    by_ref = {i.ref: i for i in found.items}
    points: dict[str, list[PointRow]] = {}
    for point in found.points:
        if point.state == "open":
            points.setdefault(point.meeting, []).append(point)
    days: list[MeetingDay] = []
    for day in (today, after):
        meetings = [
            DayMeeting(meeting=m, points=points.get(m.ref, []), items=[by_ref[r] for r in m.items if r in by_ref])
            for m in found.meetings
            if m.start.date() == day and m.response not in GONE and (day != today or m.end > at)
        ]
        if meetings:
            days.append(MeetingDay(day=day, meetings=meetings))
    week = today + WEEK
    rules: dict[Section, Callable[[ItemRow], bool]] = {
        "overdue": lambda i: i.woke or (i.due is not None and i.due <= today),
        "replies": lambda i: i.kind == "reply",
        "decisions": lambda i: i.kind in ("decision", "task") and i.due is not None and i.due <= week,
        "invites": lambda i: i.kind == "invite",
        "waiting": lambda i: i.kind == "waiting" and i.days > WAITING_DAYS,
    }
    listed: set[str] = set()
    sections: list[ItemSection] = []
    for name, rule in rules.items():
        rows = [i for i in shown if i.id not in listed and rule(i)]
        listed.update(i.id for i in rows)
        sections.append(ItemSection(name=name, items=rows))
    later = sum(1 for i in shown if i.id not in listed and i.kind in ("decision", "task"))
    return Day(
        today=today, at=at, syncs=list(syncs), sync_problem=sync_problem, queue=queue, meetings=days,
        sections=sections, later=later, streams=[s for s in found.streams if s.status != "closed"],
        status=list(files), calendar_pending=pending,
    )  # fmt: skip


def repo_of(vault: Vault) -> Path:
    """The vault's repo, resolved: the consuming repo, found by a stat; a vault in no repo is its own (DR-0671)."""
    return rendered.consuming_repo(vault) or vault.root.resolve()


# -- saying it --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Block:
    """A part of the day's text: its lines, and its lines once each cut that reaches it is made, by the cut's turn."""

    lines: list[DayLine]
    cuts: dict[int, list[DayLine]]

    def at(self, made: int) -> list[DayLine]:
        reached = [turn for turn in self.cuts if turn <= made]
        return self.cuts[max(reached)] if reached else self.lines


def _said(texts: Iterable[str]) -> list[DayLine]:
    return [DayLine(text=text) for text in texts]


def lines(day: Day, room: int = ROOM) -> list[DayLine]:
    """The day in at most `room` lines: past it, sections give their rows back in the cut order, streams first, each
    keeping its count and the read that lists it. The sync line, the status files, overdue, today's meetings, the
    calendar's and the last line are never cut, so they pass `room` only on an extreme day."""
    blocks = [_Block([_sync_line(day)], {})]
    blocks += [_Block([DayLine(text=f"status: {f.path}: {f.what}", owner=f.owner)], {}) for f in day.status]
    if day.calendar_pending:
        # Worded as `docket vault check` words it.
        said = f"{day.calendar_pending} calendar record(s) wait for their meetings; `docket ledger calendar`"
        blocks.append(_Block(_said([said]), {}))
    blocks += _queue(day)
    blocks += _meetings(day)
    blocks += _sections(day)
    blocks += _streams(day)
    blocks.append(_Block(_said([LAST]), {}))
    made = 0
    out = [line for block in blocks for line in block.at(made)]
    while len(out) > room and made < NEXT_DAY_CUT:
        made += 1
        out = [line for block in blocks for line in block.at(made)]
    return out


def counts(day: Day) -> str:
    """The one line a session outside the vault's repo gets: counts and source names only, never a title, a name or a
    subject (DR-0671)."""
    overdue = sum(1 for i in day.queue if i.late is not None)
    due_today = sum(1 for i in day.queue if i.due == day.today)
    untriaged = sum(1 for i in day.queue if i.triage is None)
    today = next((len(d.meetings) for d in day.meetings if d.day == day.today), 0)
    text = f"day: {overdue} overdue, {due_today} due today, {_n(today, 'meeting')} today, {untriaged} untriaged"
    if day.sync_problem is not None:
        return f"{text}; sync: {day.sync_problem}"
    due = [s.source for s in day.syncs if s.due]
    return text + (f"; sync due: {', '.join(due)}" if due else "")


def answer(day: Day, room: int, repo: Path) -> DayAnswer:
    return DayAnswer(today=day.today, repo=str(repo), counts=counts(day), lines=lines(day, room))


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _date(day: date, today: date) -> str:
    return f"{day.month:02d}-{day.day:02d}" if day.year == today.year else day.isoformat()


def ago(then: datetime | None, now: datetime) -> str:
    """How long ago a sync reached, todo.py's way: minutes, hours under two days, then days; `never`."""
    if then is None:
        return "never"
    seconds = max(0, int((now - then).total_seconds()))
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 48 * 3600:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _sync_line(day: Day) -> DayLine:
    head = f"day {WEEKDAYS[day.today.weekday()]} {_date(day.today, day.today)}"
    if day.sync_problem is not None:
        return DayLine(text=f"{head}; sync: {day.sync_problem}", owner=True)
    if not day.syncs:
        return DayLine(text=head)
    text = f"{head}; sync " + ", ".join(f"{s.source} {ago(s.mark, day.at)}" for s in day.syncs)
    due = [s.source for s in day.syncs if s.due]
    if due:
        text += f"; due: {', '.join(due)} (ask Claude to sync them)"
    return DayLine(text=text, owner=bool(due))


def _queue(day: Day) -> list[_Block]:
    """The untriaged count and each verdict's, then the discuss and claude rows and the me and chase refs; cut, its
    counts alone."""
    if not day.queue:
        return []
    by: dict[str | None, list[ItemRow]] = {}
    for item in day.queue:
        by.setdefault(item.triage, []).append(item)
    verdicts = ", ".join(f"{v} {len(by[v])}" for v in ops.VERDICTS if v in by)
    head = f"queue: {len(by.get(None, []))} untriaged" + (f"; {verdicts}" if verdicts else "")
    rows: list[str] = []
    for verdict in ("discuss", "claude"):
        group = by.get(verdict, [])
        if group:
            rows += [verdict, *(f"  {item_line(i, day.today)}" for i in group[:QUEUE_ROWS])]
            rows += [f"  +{len(group) - QUEUE_ROWS} more: {QUEUE_READ}"] if len(group) > QUEUE_ROWS else []
    rows += [f"{v}: {', '.join(i.ref for i in by[v])}" for v in ("me", "chase") if v in by]
    cut = _said([f"{head}; {QUEUE_READ} lists them"])
    return [_Block(_said([head, *rows]) if rows else cut, {QUEUE_CUT: cut})]


def _meetings(day: Day) -> list[_Block]:
    """A block a day: its heading, each meeting's line with its open points (`?` proposed) and open items; cut, the
    meeting lines alone, whose counts stay; the next working day's then to a count."""
    blocks: list[_Block] = []
    for on in day.meetings:
        label = "today" if on.day == day.today else WEEKDAYS[on.day.weekday()]
        head = f"meetings {label} {_date(on.day, day.today)}"
        full, bare = [head], [head]
        for dm in on.meetings[:MEETINGS_A_DAY]:
            m = dm.meeting
            full.append(meeting_line(m, day.today))
            bare.append(full[-1])
            full += [f"  {'?' if p.proposed else '*'} {p.text}" for p in dm.points[:POINTS_A_MEETING]]
            if len(dm.points) > POINTS_A_MEETING:
                full.append(f"  +{len(dm.points) - POINTS_A_MEETING} more points: the ledger tool's show {m.ref}")
            for i in dm.items[:ITEMS_A_MEETING]:
                full.append(f"  {i.ref} {i.title}" + (f" (due {_date(i.due, day.today)})" if i.due else ""))
            if len(dm.items) > ITEMS_A_MEETING:
                full.append(f"  +{len(dm.items) - ITEMS_A_MEETING} more items: the ledger tool's show {m.ref}")
        if len(on.meetings) > MEETINGS_A_DAY:
            more = f"+{len(on.meetings) - MEETINGS_A_DAY} more: {MEETINGS_READ}"
            full.append(more)
            bare.append(more)
        cuts = {POINTS_CUT: _said(bare)}
        if on.day != day.today:
            cuts[NEXT_DAY_CUT] = _said([f"{head}: {_n(len(on.meetings), 'meeting')}; {MEETINGS_READ} lists them"])
        blocks.append(_Block(_said(full), cuts))
    return blocks


def _sections(day: Day) -> list[_Block]:
    """Overdue, replies, decisions and tasks, invites and waiting: a heading with the count, the rows to the cap and
    how many more; cut, the count and the read. The decisions and tasks not due this week are a count beneath."""
    blocks: list[_Block] = []
    for section in day.sections:
        title, cap, read_whole, turn = SECTIONS[section.name]
        rows = section.items
        later = day.later if section.name == "decisions" else 0
        if not rows and not later:
            continue
        beyond = f"{later} more not due this week" if later else ""
        more = ", ".join(p for p in (f"+{len(rows) - cap} more" if len(rows) > cap else "", beyond) if p)
        full = [f"{title} ({len(rows)})", *(item_line(i, day.today) for i in rows[:cap])]
        full += [f"  {more}: {read_whole}"] if more else []
        cut = f"{title} ({len(rows)})" + (f", {beyond}" if beyond else "") + f": {read_whole}"
        blocks.append(_Block(_said(full), {turn: _said([cut])} if turn is not None else {}))
    return blocks


def _streams(day: Day) -> list[_Block]:
    """Each stream not closed: todo.py's `pilot [waiting]: where -> next (deadline)`; cut, the count."""
    if not day.streams:
        return []
    rows: list[str] = []
    for s in day.streams:
        text = s.name + (" [waiting]" if s.status == "waiting" else "")
        text += f": {views.cut(s.where, WHERE)}" if s.where else ""
        text += f" -> {views.cut(s.next, WHERE)}" if s.next else ""
        rows.append(text + (f" ({_date(s.deadline, day.today)})" if s.deadline else ""))
    head = f"streams ({len(rows)})"
    return [_Block(_said([head, *rows]), {STREAMS_CUT: _said([f"{head}: {STREAMS_READ}"])})]
