"""The day pages' parts (DR-0664 to DR-0666, DR-0710): the Ledger's list in its groups, a row's quick buttons, the item
panel, a material's or a source's link, the notice after a write and the snooze choices, each a model the app's JSON
reads answer as it is (DR-0709).

Each builder takes what the ledger's views read (`views.read`, `views.matching`, `views.batch_lines`) and does no
I/O, so every rule here is tested on an invented ledger. Nothing from a record shows here: only what the views give,
an item's own text. Values come ready to show, times as text in the owner's zone, so the app formats none. A snoozed
item whose day has come is open because the views mark it `woke`; nothing here has a rule of its own about it
(DR-0666).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from datetime import date, timedelta
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from docket.ledger import ops
from docket.ledger.views import BatchLine, Change, Detail, ItemRow, LedgerRead, MeetingRow, cut
from docket.vault.anchors import AnchorError, parse_locator
from docket.vault.processed import DOC_TYPES
from docket.vault.records import RID
from docket.web.day_state import MORE, DayState
from docket.web.display import at
from docket.web.recording_state import RecordingState, time_at
from docket.web.state import SearchState

VERDICTS: dict[str, tuple[str, str, str]] = {
    "discuss": ("Discuss", "talk it through with Claude", "1"),
    "claude": ("Claude", "Claude drafts or does it", "2"),
    "me": ("Me", "on you, no Claude", "3"),
    "chase": ("Chase", "nudge the other side", "4"),
}
KIND_LABELS = {
    "reply": "Replies owed",
    "decision": "Decisions",
    "task": "Tasks",
    "invite": "Invites",
    "waiting": "Waiting on others",
}
# A text file the panel's viewer shows (DR-0664): the old tasks app's.
TEXT_SUFFIXES = (".md", ".txt", ".json", ".yaml", ".yml")
FAR = date.max
SOON = 7
# A locator's scheme, as a source's name or a URL's starts it; a one-letter one is a Windows drive.
SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*")
EMPTY = {"open": "Nothing is open.", "snoozed": "Nothing is snoozed.", "closed": "Nothing is closed."}
STATE_CHIPS = (("open", "Open"), ("snoozed", "Snoozed"), ("closed", "Closed"), ("all", "All"))

# How a due or a group reads: late in red ink, today's and tomorrow's marked, the rest plain.
Tone = Literal["", "late", "today", "soon"]


class Button(BaseModel):
    """A verdict's button, with the key that presses it. It carries what it writes, decided before the post by the old
    tasks app's bulk rule: `writes` is its verdict, or None when every row it acts on holds it already, so pressing it
    again clears it and no write toggles (DR-0710)."""

    verdict: str
    label: str
    writes: str | None
    key: str
    title: str
    pressed: bool


class Snooze(BaseModel):
    """A snooze choice: the day it snoozes to, and how its button reads."""

    label: str
    until: date
    title: str


class Due(BaseModel):
    text: str = ""
    title: str = ""
    tone: Tone = ""


class Row(BaseModel):
    """A row of the list: an item as it reads at a glance, with its quick buttons when a call takes it."""

    ref: str
    id: str
    kind: str
    title: str
    who: str
    stream: str
    meeting: str | None
    state: str
    triage: str | None
    woke: bool
    archived: bool
    due: Due
    age: int
    callable: bool
    quick: list[Button]


class Group(BaseModel):
    key: str
    label: str
    tone: Tone
    rows: list[Row]
    total: int
    selectable: bool
    # The call every row of the group holds, for the Queue's groups by verdict: its head shows the call's stamp.
    verdict: str | None = None


class Chip(BaseModel):
    """A state the list shows: open, snoozed, closed or all, with how many items it holds."""

    key: str
    label: str
    count: int
    on: bool


class LedgerList(BaseModel):
    groups: list[Group]
    counts: str
    chips: list[Chip]
    shown: int
    total: int
    # The k that shows the next 100 rows, when there are more.
    more: int | None
    empty: str
    streams: list[str]


Kind = Literal["url", "record", "recording", "query", "path", "text", "unsafe"]


class Link(BaseModel):
    """A material or a source as the panel links it (DR-0664): a URL, a record's raw on the Search page, a recording
    at its time, a text locator as a search, or a path from the host repo's top that opens through the service, a text
    file in the panel's viewer too."""

    ref: str
    label: str
    kind: Kind
    href: str = ""


class HistoryLine(BaseModel):
    # When, as the page shows it: the time in the owner's zone, or the day alone for a line with no time.
    when: str
    # The same as an ISO date or time, for a time element.
    stamp: str
    by: str
    via: str
    line: str
    undone_by: str | None
    # Undo, an undo by `of` of this line; Redo on an undo's own line; none on an add, a renumber or an undone line.
    button: Literal["Undo", "Redo"] | None
    op: str


class Point(BaseModel):
    n: int
    state: str
    text: str
    answer: str | None
    item: str | None
    proposed: bool


class MeetingBox(BaseModel):
    ref: str
    subject: str
    when: str
    points: list[Point]


class ItemPanel(BaseModel):
    """An item as its panel shows it (DR-0664): its call, its fields, the meeting it hangs on, its free text, its
    materials, where it came from, its counts and its history."""

    ref: str
    id: str
    kind: str
    title: str
    who: str
    stream: str
    state: str
    triage: str | None
    woke: bool
    archived: bool
    # The due day as a date field holds it, or "" for none.
    due: str
    state_line: str
    # When it was opened and by whom, then its counts.
    facts: str
    description: str
    notes: str
    outcome: str
    source: Link
    meeting: MeetingBox | None
    materials: list[Link]
    history: list[HistoryLine]
    verdicts: list[Button]
    snooze: list[Snooze]
    # The first day the snooze's own date may take.
    tomorrow: date
    callable: bool
    closed: bool


class Notice(BaseModel):
    """What the last write did (DR-0710): its words, the batch's lines, and Undo or Redo by its batch."""

    kind: Literal["info", "warn"]
    text: str
    lines: list[str] = []
    button: Literal["Undo", "Redo"] | None = None
    batch: str | None = None
    # What the button's write says it did, `undo` or `redo`, so the next notice reads right.
    then: Literal["undo", "redo"] | None = None


# -- days -------------------------------------------------------------------------------------------------------------


def day_text(day: date, today: date) -> str:
    """`Tue 21 Oct`, the year added when it isn't this year's."""
    text = f"{day:%a} {day.day} {day:%b}"
    return text if day.year == today.year else f"{text} {day.year}"


def next_working_day(today: date) -> date:
    """The next day from Monday to Friday, as todo.py's."""
    day = today + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def snooze_choices(today: date) -> list[Snooze]:
    """The snooze buttons: the next working day, Monday, a week and two weeks on; a day of its own is a date field."""
    working = next_working_day(today)
    monday = today + timedelta(days=7 - today.weekday())
    choices = [
        (f"{working:%a}", working, "the next working day"),
        (f"Mon {monday.day} {monday:%b}", monday, "Monday"),
        ("+1 week", today + timedelta(days=7), "a week"),
        ("+2 weeks", today + timedelta(days=14), "two weeks"),
    ]
    seen: set[date] = set()
    out: list[Snooze] = []
    for label, day, what in choices:
        if day not in seen:
            seen.add(day)
            out.append(Snooze(label=label, until=day, title=f"Snooze to {what}, {day_text(day, today)}"))
    return out


def due_of(row: ItemRow, today: date) -> Due:
    if row.state in ("done", "dropped") and row.closed is not None:
        return Due(text=f"{row.state} {row.closed.day} {row.closed:%b}", title=row.closed.isoformat())
    if row.woke:
        return Due(text="snooze ended", title=f"back since {row.until}", tone="today")
    if row.state == "snoozed" and row.until is not None:
        return Due(text=f"back {row.until.day} {row.until:%b}", title=row.until.isoformat())
    if row.due is None:
        return Due()
    days = (row.due - today).days
    title = f"due {row.due.isoformat()}"
    if days < 0:
        return Due(text=f"{-days}d late", title=title, tone="late")
    if days == 0:
        return Due(text="today", title=title, tone="today")
    if days == 1:
        return Due(text="tomorrow", title=title, tone="soon")
    return Due(text=f"{row.due:%a}" if days < SOON else f"{row.due.day} {row.due:%b}", title=title)


# -- buttons ----------------------------------------------------------------------------------------------------------


def verdict_buttons(held: Sequence[str | None]) -> list[Button]:
    """The four verdicts as the rows they act on hold them: one clears its verdict when every row holds it, and sets it
    on all of them otherwise, as the old tasks app's bulk did, so no write toggles (DR-0710)."""
    out: list[Button] = []
    for verdict, (label, hint, key) in VERDICTS.items():
        every = bool(held) and all(h == verdict for h in held)
        title = f"{label}: {hint} ({key}){'; again clears it' if every else ''}"
        out.append(
            Button(verdict=verdict, label=label, writes=None if every else verdict, key=key, title=title, pressed=every)
        )
    return out


def callable_row(row: ItemRow) -> bool:
    """An item a call takes: open or snoozed, and not archived."""
    return row.state in ("open", "snoozed") and not row.archived


# -- the Ledger's list ------------------------------------------------------------------------------------------------


def _shown(row: ItemRow, state: DayState) -> bool:
    """Whether the state chip takes a row; archived ones are left out before, unless asked for."""
    if state.state == "open":
        return row.state == "open" or row.woke
    if state.state == "snoozed":
        return row.state == "snoozed" and not row.woke
    if state.state == "closed":
        return row.state in ("done", "dropped")
    return True


def _order(row: ItemRow) -> tuple[date, date, int, str]:
    """Within a group: by due, those with none last, then opened and number (DR-0666)."""
    return (row.due or FAR, row.opened, row.number or 0, row.id)


Grouped = list[tuple[str, str, Tone, list[ItemRow]]]


def _due_groups(rows: Sequence[ItemRow], today: date) -> Grouped:
    week = today + timedelta(days=SOON)
    buckets: list[tuple[str, str, Tone, Callable[[ItemRow], bool]]] = [
        ("woke", "Snooze ended", "today", lambda r: r.woke),
        ("overdue", "Overdue", "late", lambda r: r.due is not None and r.due < today),
        ("today", "Due today", "today", lambda r: r.due == today),
        ("week", "Due this week", "", lambda r: r.due is not None and today < r.due <= week),
        ("later", "Due later", "", lambda r: r.due is not None and r.due > week),
        ("none", "No due date", "", lambda r: r.due is None),
    ]
    out: Grouped = []
    left = list(rows)
    for key, label, tone, test in buckets:
        taken = [r for r in left if test(r)]
        left = [r for r in left if not test(r)]
        out.append((key, label, tone, sorted(taken, key=_order)))
    return out


def _by(rows: Sequence[ItemRow], name: str, labels: Callable[[str], str], order: Callable[[str], Any]) -> Grouped:
    keys = sorted({getattr(r, name) or "" for r in rows}, key=order)
    return [
        (f"{name}-{k or 'none'}", labels(k), "", sorted((r for r in rows if (getattr(r, name) or "") == k), key=_order))
        for k in keys
    ]


def _snoozed_groups(rows: Sequence[ItemRow], today: date) -> Grouped:
    days = sorted({r.until or today for r in rows})
    return [
        (
            f"back-{d}",
            f"Back {day_text(d, today)}",
            "",
            sorted((r for r in rows if (r.until or today) == d), key=_order),
        )
        for d in days
    ]


def _closed_groups(rows: Sequence[ItemRow], today: date) -> Grouped:
    days = sorted({r.closed or r.opened for r in rows}, reverse=True)
    return [
        (
            f"closed-{d}",
            "Closed today" if d == today else f"Closed {day_text(d, today)}",
            "",
            sorted((r for r in rows if (r.closed or r.opened) == d), key=_order),
        )
        for d in days
    ]


def _grouped(rows: Sequence[ItemRow], state: DayState, today: date) -> Grouped:
    if state.state == "snoozed":
        return _snoozed_groups(rows, today)
    if state.state == "closed":
        return _closed_groups(rows, today)
    if state.group == "stream":
        return _by(rows, "stream", lambda k: k, lambda k: k)
    if state.group == "kind":
        kinds = list(ops.KINDS)
        return _by(rows, "kind", lambda k: KIND_LABELS.get(k, k), lambda k: kinds.index(k) if k in kinds else 99)
    if state.group == "who":
        return _by(rows, "who", lambda k: k, lambda k: (k.casefold(), k))
    if state.group == "meeting":
        return _by(rows, "meeting", lambda k: k or "No meeting", lambda k: (k == "", k))
    awake = [r for r in rows if r.state == "open" or r.woke]
    asleep = [r for r in rows if r.state == "snoozed" and not r.woke]
    closed = [r for r in rows if r.state in ("done", "dropped")]
    return _due_groups(awake, today) + _snoozed_groups(asleep, today) + _closed_groups(closed, today)


def build_list(found: LedgerRead, state: DayState, matched: set[str] | None) -> LedgerList:
    """The Ledger's list (DR-0666): `list` read whole, narrowed by the state chip, `q`'s matches and `archived`,
    grouped, cut at `k` across the groups; the counts over every row the filters keep."""
    today = found.today
    every = [r for r in found.items if matched is None or r.id in matched]
    visible = [r for r in every if not r.archived or state.archived]
    chosen = [r for r in visible if _shown(r, state)]
    groups: list[Group] = []
    shown = 0
    for key, label, tone, members in _grouped(chosen, state, today):
        rows = [row_of(r, today) for r in members[: state.k - shown]]
        if rows:
            shown += len(rows)
            selectable = any(r.callable for r in rows)
            groups.append(Group(key=key, label=label, tone=tone, rows=rows, total=len(members), selectable=selectable))
    awake = sum(1 for r in visible if r.state == "open" or r.woke)
    woke = sum(1 for r in visible if r.woke)
    asleep = sum(1 for r in visible if r.state == "snoozed" and not r.woke)
    closed = sum(1 for r in visible if r.state in ("done", "dropped"))
    late = sum(1 for r in visible if r.late is not None)
    counted = {"open": awake, "snoozed": asleep, "closed": closed, "all": len(visible)}
    chips = [Chip(key=key, label=label, count=counted[key], on=state.state == key) for key, label in STATE_CHIPS]
    said = f"{awake} open" + (f" ({woke} with their snooze ended)" if woke else "")
    counts = f"{said}, {asleep} snoozed, {closed} closed; {late} overdue"
    more = state.k + MORE if len(chosen) > state.k else None
    narrowed = matched is not None or bool(state.who or state.stream or state.kind)
    empty = ""
    if not chosen:
        empty = EMPTY.get(state.state, "Nothing here.")
        if narrowed:
            empty = "Nothing here matches."
        elif not found.items:
            empty = "The ledger is empty. Claude adds what you owe and await as it syncs, or add an item here."
    streams = sorted({r.stream for r in found.items})
    return LedgerList(
        groups=groups,
        counts=counts,
        chips=chips,
        shown=shown,
        total=len(chosen),
        more=more,
        empty=empty,
        streams=streams,
    )


# -- the Queue's list -------------------------------------------------------------------------------------------------


def _narrowed(rows: Sequence[ItemRow], state: DayState, matched: set[str] | None) -> list[ItemRow]:
    """The rows the Queue's filters keep, as the list view keeps them: `q`'s matches, the stream and kind as given,
    who a part of it with case aside."""
    who = (state.who or "").casefold()
    return [
        r
        for r in rows
        if (matched is None or r.id in matched)
        and (state.stream is None or r.stream == state.stream)
        and (state.kind is None or r.kind == state.kind)
        and who in r.who.casefold()
    ]


def build_queue(found: LedgerRead, state: DayState, matched: set[str] | None) -> LedgerList:
    """The Queue's list (DR-0666): the queue view's open items, the untriaged first, those whose snooze ended leading
    them, then each verdict in the views' order and any other call after, each by due, none last; cut at `k` across
    the groups, the counts over every row the filters keep."""
    today = found.today
    chosen = _narrowed(found.items, state, matched)
    woke = [r for r in chosen if r.woke]
    untriaged = [r for r in chosen if r.triage is None and not r.woke]
    calls = list(dict.fromkeys([*ops.VERDICTS, *sorted({r.triage for r in chosen if r.triage is not None})]))
    grouped: list[tuple[str, str, Tone, str | None, list[ItemRow]]] = [
        ("woke", "Snooze ended", "today", None, woke),
        ("untriaged", "Untriaged", "", None, untriaged),
    ]
    for call in calls:
        label = VERDICTS[call][0] if call in VERDICTS else call
        grouped.append((f"verdict-{call}", label, "", call, [r for r in chosen if r.triage == call and not r.woke]))
    groups: list[Group] = []
    shown = 0
    for key, label, tone, verdict, members in grouped:
        rows = [row_of(r, today) for r in sorted(members, key=_order)[: state.k - shown]]
        if rows:
            shown += len(rows)
            groups.append(
                Group(key=key, label=label, tone=tone, rows=rows, total=len(members), selectable=True, verdict=verdict)
            )
    waiting = len(woke) + len(untriaged)
    said = f"{waiting} untriaged" + (f" ({len(woke)} with their snooze ended)" if woke else "")
    held = [f"{n} {call}" for call in calls if (n := sum(1 for r in chosen if r.triage == call and not r.woke))]
    late = sum(1 for r in chosen if r.late is not None)
    counts = f"{len(chosen)} open: {', '.join([said, *held])}; {late} overdue"
    narrowed = matched is not None or bool(state.who or state.stream or state.kind)
    empty = ""
    if not chosen:
        empty = "Nothing here matches." if narrowed else "Nothing is open: the queue is empty."
    return LedgerList(
        groups=groups,
        counts=counts,
        chips=[],
        shown=shown,
        total=len(chosen),
        more=state.k + MORE if len(chosen) > state.k else None,
        empty=empty,
        streams=sorted({r.stream for r in found.items}),
    )


def row_of(row: ItemRow, today: date) -> Row:
    can = callable_row(row)
    triage = row.triage if row.state == "open" else None
    return Row(
        ref=row.ref,
        id=row.id,
        kind=row.kind,
        title=row.title,
        who=row.who,
        stream=row.stream,
        meeting=row.meeting,
        state=row.state,
        triage=triage,
        woke=row.woke,
        archived=row.archived,
        due=due_of(row, today),
        age=row.days,
        callable=can,
        quick=verdict_buttons([triage]) if can else [],
    )


# -- materials and the source ----------------------------------------------------------------------------------------


def link_of(ref: str, label: str | None, sources: Iterable[str]) -> Link:
    """How the panel links a material or a source (DR-0664): a URL as a link; a locator of a record of the vault's
    sources on the Search page's panel, or at a time on the Recording page; a doc's id on the Search page; any other
    text locator as a search for its text; a path from the host repo's top through the service, unless its text is
    one the checks refuse, which shows as text and is never looked up."""
    text = ref.strip()
    shown = label or text
    low = text.lower()
    if low.startswith(("http://", "https://")):
        return Link(ref=ref, label=shown, kind="url", href=text)
    head, colon, _ = text.partition(":")
    if text.startswith(("r:", "[")) or (colon and SCHEME.fullmatch(head) and len(head) > 1):
        return _locator(ref, shown, text, set(sources))
    if path_problem(text) is not None:
        return Link(ref=ref, label=shown, kind="unsafe")
    return Link(ref=ref, label=shown, kind="text" if low.endswith(TEXT_SUFFIXES) else "path")


def _locator(ref: str, shown: str, text: str, sources: set[str]) -> Link:
    try:
        found = parse_locator(text, DOC_TYPES)
    except AnchorError:
        found = None
    if found is not None and found.kind == "doc":
        return Link(ref=ref, label=shown, kind="record", href=SearchState(hit=text).url())
    rid = found.value if found is not None and found.kind == "record" else ""
    m = RID.match(rid)
    if m is None or m.group(1) not in sources:
        return Link(ref=ref, label=shown, kind="query", href=SearchState(query=text).url())
    anchor = found.anchor if found is not None else None
    if anchor is not None and anchor.kind == "time":
        href = RecordingState(rid=rid, at=time_at(anchor.start, anchor.end)).link()
        return Link(ref=ref, label=shown, kind="recording", href=href)
    hit = text if text.startswith(("r:", "[")) else f"r:{rid}"
    return Link(ref=ref, label=shown, kind="record", href=SearchState(hit=hit).url())


def path_problem(ref: str) -> str | None:
    """Why a material's ref can't be a path from the host repo's top, judged on its text alone before any filesystem
    call (DR-0664): absolute, drive-relative or UNC, a `..` part, a colon, an empty part, or a part that ends in a dot
    or a space, which Windows would drop. None when it can be one."""
    text = ref.strip()
    if not text:
        return "it names no path"
    if text.startswith(("/", "\\")):
        return "it is an absolute path or a network share's"
    if ":" in text:
        return "it holds a colon: a drive, a stream or another scheme"
    parts = text.replace("\\", "/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        return "it has an empty, . or .. part"
    if any(p.endswith((".", " ")) for p in parts):
        return "a part of it ends in a dot or a space"
    if any(p.lower() == ".git" for p in parts):
        return "it is inside .git"
    return None


# -- the item panel ---------------------------------------------------------------------------------------------------


def history_lines(history: Sequence[Change], tz: ZoneInfo) -> list[HistoryLine]:
    """Newest first; Undo only on a line no undo has left out, never on the add of an item, a meeting or a stream, nor
    on a renumber; Redo on an undo's own line (DR-0664)."""
    out: list[HistoryLine] = []
    for change in reversed(history):
        button: Literal["Undo", "Redo"] | None = None
        if change.undone_by is None and change.kind not in (*ops.MAKES, "renumber"):
            button = "Redo" if change.kind == "undo" else "Undo"
        when = at(change.at, tz) if change.at is not None else change.day.isoformat()
        stamp = (change.at or change.day).isoformat()
        out.append(
            HistoryLine(
                when=when,
                stamp=stamp,
                by=change.by,
                via=change.via,
                line=change.line,
                undone_by=change.undone_by,
                button=button,
                op=change.id,
            )
        )
    return out


def _state_line(row: ItemRow, today: date) -> str:
    if row.state == "open":
        return f"open, {VERDICTS[row.triage][0].lower()}" if row.triage in VERDICTS else "open, no call yet"
    if row.state == "snoozed":
        until = day_text(row.until or today, today)
        return f"snooze ended {until}, open again" if row.woke else f"snoozed to {until}"
    closed = day_text(row.closed or today, today)
    return f"{row.state} {closed}" + (", archived" if row.archived else "")


def _counts(row: ItemRow, detail: Detail) -> str:
    span = f"open {row.days} day{'s' if row.days != 1 else ''}"
    if row.state in ("done", "dropped"):
        span = f"{row.state} after {row.days} day{'s' if row.days != 1 else ''}"
    parts = [
        (detail.chased, "chased"),
        (detail.snoozes, "snoozed"),
        (detail.due_moves, "due moved"),
        (detail.reopens, "reopened"),
    ]
    said = ", ".join(f"{word} {n} time{'s' if n != 1 else ''}" for n, word in parts if n)
    return f"{span}; {said}" if said else span


def _meeting_box(meeting: LedgerRead | None) -> MeetingBox | None:
    if meeting is None or not meeting.meetings:
        return None
    row: MeetingRow = meeting.meetings[0]
    start, end = row.start, row.end
    until = f"{end:%H:%M}" if end.date() == start.date() else f"{end.day} {end:%b} {end:%H:%M}"
    when = f"{start:%a} {start.day} {start:%b} {start:%H:%M}-{until}"
    points = [
        Point(n=p.n, state=p.state, text=p.text, answer=p.answer, item=p.item, proposed=p.proposed)
        for p in meeting.points
        if p.state != "rolled"
    ]
    return MeetingBox(ref=row.ref, subject=row.subject, when=when, points=points)


def build_panel(shown: LedgerRead, meeting: LedgerRead | None, sources: Iterable[str], tz: ZoneInfo) -> ItemPanel:
    """The item panel from `show` (DR-0664): the call, the fields, the meeting it hangs on with its points, the free
    text, the materials, where it came from, the counts and the history."""
    row = shown.items[0]
    # Show always gives its object's detail.
    detail = cast(Detail, shown.detail)
    today = shown.today
    named = list(sources)
    can = callable_row(row)
    triage = row.triage if row.state == "open" else None
    return ItemPanel(
        ref=row.ref,
        id=row.id,
        kind=row.kind,
        title=row.title,
        who=row.who,
        stream=row.stream,
        state=row.state,
        triage=triage,
        woke=row.woke,
        archived=row.archived,
        due=row.due.isoformat() if row.due else "",
        state_line=_state_line(row, today),
        facts=f"opened {row.opened.isoformat()} by {detail.added_by}; {_counts(row, detail)}",
        description=detail.description or "",
        notes=detail.notes or "",
        outcome=detail.outcome or "",
        source=link_of(row.source, None, named),
        meeting=_meeting_box(meeting),
        materials=[link_of(m.ref, m.label, named) for m in detail.materials],
        history=history_lines(detail.history, tz),
        verdicts=verdict_buttons([triage]) if can else [],
        snooze=snooze_choices(today) if can else [],
        tomorrow=today + timedelta(days=1),
        callable=can,
        closed=row.state in ("done", "dropped"),
    )


# -- the notice -------------------------------------------------------------------------------------------------------


def build_notice(state: DayState, lines: Sequence[BatchLine]) -> Notice | None:
    """The notice for a URL that still names what a write did, or None when it names nothing."""
    return None if state.done is None else notice_of(state.done, state.said, state.skipped, lines)


def notice_of(done: str, said: str | None, skipped: Sequence[str], lines: Sequence[BatchLine]) -> Notice:
    """What the last write did (DR-0710): the batch's lines and its Undo, a Redo after an Undo; Nothing changed when a
    write wrote nothing; the rows a bulk's op didn't take. Undone and Redone are what the write said it was, never read
    from its lines: a batch of undos can be a change of its own, such as removing every proposed point."""
    skips = f" Skipped {', '.join(skipped)}: the change doesn't take them as they stand." if skipped else ""
    if done == "nothing":
        return Notice(kind="info", text="Nothing changed." + skips)
    if done == "opened":
        return Notice(kind="info", text="Opened it with its app.")
    if done == "shown":
        return Notice(kind="info", text="Shown in its folder.")
    if not lines:
        return Notice(kind="info", text="That change is no longer in the ledger's history." + skips)
    shown = [f"{line.ref}: {cut(line.line)}" for line in lines]
    if said == "undo":
        return Notice(kind="info", text=f"Undone.{skips}", lines=shown, button="Redo", batch=done, then="redo")
    head = "Redone." if said == "redo" else "Saved."
    return Notice(kind="info", text=f"{head}{skips}", lines=shown, button="Undo", batch=done, then="undo")
