"""The day pages' parts (DR-0661 to DR-0666): the Ledger's list in its groups, a row's quick buttons, the item panel,
a material's or a source's link, the notice after a write and the snooze choices.

Each builder takes what the ledger's views read (`views.read`, `views.matching`, `views.batch_lines`) and does no
I/O, so every rule here is tested on an invented ledger. Nothing from a record shows here: only what the views give,
an item's own text. A snoozed item whose day has come is open because the views mark it `woke`; nothing here has a rule
of its own about it (DR-0666).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Any, Literal, cast

from docket.ledger import ops
from docket.ledger.views import BatchLine, Change, Detail, ItemRow, LedgerRead, MeetingRow, PointRow, cut
from docket.vault.anchors import AnchorError, parse_locator
from docket.vault.processed import DOC_TYPES
from docket.vault.records import RID
from docket.web.day_state import DEFAULT_K, MORE, PANEL, TEXT, DayState
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


@dataclass(frozen=True)
class Button:
    """A button that posts `op` with its one value, such as `triage:chase` (DR-0661), with the key that presses it."""

    label: str
    value: str
    key: str = ""
    title: str = ""
    pressed: bool | None = None
    verdict: str = ""


@dataclass(frozen=True)
class Due:
    text: str = ""
    title: str = ""
    cls: str = ""


@dataclass
class Row:
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
    url: str
    fragment: str
    drop_url: str
    drop_fragment: str
    open: bool
    callable: bool
    quick: list[Button] = field(default_factory=list[Button])
    # The row after this one as the list shows it: where the selection goes after a call on this row.
    next: str = ""


@dataclass
class Group:
    key: str
    label: str
    cls: str
    rows: list[Row]
    total: int
    selectable: bool


@dataclass(frozen=True)
class Chip:
    label: str
    url: str
    on: bool


@dataclass
class LedgerList:
    groups: list[Group]
    counts: str
    chips: list[Chip]
    shown: int
    total: int
    more: str | None
    empty: str
    streams: list[str]
    rows: dict[str, Row]


Kind = Literal["url", "record", "recording", "query", "path", "text", "unsafe"]


@dataclass(frozen=True)
class Link:
    """A material or a source as the panel links it (DR-0664): a URL, a record's raw on the Search page, a recording
    at its time, a text locator as a search, or a path from the host repo's top that opens through the service."""

    ref: str
    label: str
    kind: Kind
    href: str = ""
    fragment: str = ""


@dataclass(frozen=True)
class HistoryLine:
    day: date
    at: datetime | None
    by: str
    via: str
    line: str
    undone_by: str | None
    # Undo, an undo by `of` of this line; Redo on an undo's own line; none on an add, a renumber or an undone line.
    button: str | None
    op: str


@dataclass(frozen=True)
class MeetingBox:
    ref: str
    subject: str
    when: str
    points: list[PointRow]


@dataclass
class ItemPanel:
    row: ItemRow
    detail: Detail
    title: str
    state_line: str
    due: str
    source: Link
    meeting: MeetingBox | None
    materials: list[Link]
    counts: str
    history: list[HistoryLine]
    verdicts: list[Button]
    snooze: list[Button]
    tomorrow: str
    callable: bool
    closed: bool
    archived: bool
    drop: bool
    next: str
    url: str
    close_url: str
    close_fragment: str


@dataclass(frozen=True)
class Notice:
    kind: Literal["info", "warn"]
    text: str
    lines: list[str] = field(default_factory=list[str])
    button: str | None = None
    batch: str | None = None
    # Whether the button's write is itself an undo, so the next notice offers Redo.
    then_redo: bool = False


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


def snooze_choices(today: date) -> list[Button]:
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
    out: list[Button] = []
    for label, day, what in choices:
        if day not in seen:
            seen.add(day)
            out.append(Button(label, f"snooze:{day.isoformat()}", title=f"Snooze to {what}, {day_text(day, today)}"))
    return out


def due_of(row: ItemRow, today: date) -> Due:
    if row.state in ("done", "dropped") and row.closed is not None:
        return Due(f"{row.state} {row.closed.day} {row.closed:%b}", row.closed.isoformat())
    if row.woke:
        return Due("snooze ended", f"back since {row.until}", "today")
    if row.state == "snoozed" and row.until is not None:
        return Due(f"back {row.until.day} {row.until:%b}", row.until.isoformat())
    if row.due is None:
        return Due()
    days = (row.due - today).days
    title = f"due {row.due.isoformat()}"
    if days < 0:
        return Due(f"{-days}d late", title, "late")
    if days == 0:
        return Due("today", title, "today")
    if days == 1:
        return Due("tomorrow", title, "soon")
    return Due(f"{row.due:%a}" if days < SOON else f"{row.due.day} {row.due:%b}", title)


# -- buttons ----------------------------------------------------------------------------------------------------------


def verdict_buttons(held: Sequence[str | None]) -> list[Button]:
    """The four verdicts as the rows they act on hold them: one clears its verdict when every row holds it, and sets it
    on all of them otherwise, as the old tasks app's bulk did, so no write toggles (DR-0661)."""
    out: list[Button] = []
    for verdict, (label, hint, key) in VERDICTS.items():
        every = bool(held) and all(h == verdict for h in held)
        value = "triage:" if every else f"triage:{verdict}"
        title = f"{label}: {hint} ({key}){'; again clears it' if every else ''}"
        out.append(Button(label, value, key, title, pressed=every, verdict=verdict))
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


def _due_groups(rows: Sequence[ItemRow], today: date) -> Grouped:
    week = today + timedelta(days=SOON)
    buckets: list[tuple[str, str, str, Callable[[ItemRow], bool]]] = [
        ("woke", "Snooze ended", "today", lambda r: r.woke),
        ("overdue", "Overdue", "late", lambda r: r.due is not None and r.due < today),
        ("today", "Due today", "today", lambda r: r.due == today),
        ("week", "Due this week", "", lambda r: r.due is not None and today < r.due <= week),
        ("later", "Due later", "", lambda r: r.due is not None and r.due > week),
        ("none", "No due date", "", lambda r: r.due is None),
    ]
    out: Grouped = []
    left = list(rows)
    for key, label, cls, test in buckets:
        taken = [r for r in left if test(r)]
        left = [r for r in left if not test(r)]
        out.append((key, label, cls, sorted(taken, key=_order)))
    return out


Grouped = list[tuple[str, str, str, list[ItemRow]]]


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
    order: list[Row] = []
    for key, label, cls, members in _grouped(chosen, state, today):
        shown = [row_of(r, state, today) for r in members[: state.k - len(order)]]
        if shown:
            order += shown
            groups.append(Group(key, label, cls, shown, len(members), any(r.callable for r in shown)))
    # After a call the selection goes to the next row, or to the one before when it was the last (DR-0662).
    for here, after in pairwise(order):
        here.next = after.ref
    if len(order) > 1:
        order[-1].next = order[-2].ref
    rows = {r.ref: r for r in order}
    awake = sum(1 for r in visible if r.state == "open" or r.woke)
    woke = sum(1 for r in visible if r.woke)
    asleep = sum(1 for r in visible if r.state == "snoozed" and not r.woke)
    closed = sum(1 for r in visible if r.state in ("done", "dropped"))
    late = sum(1 for r in visible if r.late is not None)
    chips = [
        Chip(f"{label} {n}", state.quiet().but(state=key, k=DEFAULT_K).url(), state.state == key)
        for key, label, n in (
            ("open", "Open", awake),
            ("snoozed", "Snoozed", asleep),
            ("closed", "Closed", closed),
            ("all", "All", len(visible)),
        )
    ]
    said = f"{awake} open" + (f" ({woke} with their snooze ended)" if woke else "")
    counts = f"{said}, {asleep} snoozed, {closed} closed; {late} overdue"
    more = state.quiet().but(k=state.k + MORE).url() if len(chosen) > state.k else None
    narrowed = matched is not None or bool(state.who or state.stream or state.kind)
    empty = ""
    if not chosen:
        empty = EMPTY.get(state.state, "Nothing here.")
        if narrowed:
            empty = "Nothing here matches."
        elif not found.items:
            empty = "The ledger is empty. Claude adds what you owe and await as it syncs, or add an item here."
    streams = sorted({r.stream for r in found.items})
    return LedgerList(groups, counts, chips, len(order), len(chosen), more, empty, streams, rows)


def row_of(row: ItemRow, state: DayState, today: date) -> Row:
    opened = state.opened(row.ref)
    can = callable_row(row)
    quick = verdict_buttons([row.triage if row.state == "open" else None]) if can else []
    return Row(
        ref=row.ref,
        id=row.id,
        kind=row.kind,
        title=row.title,
        who=row.who,
        stream=row.stream,
        meeting=row.meeting,
        state=row.state,
        triage=row.triage if row.state == "open" else None,
        woke=row.woke,
        archived=row.archived,
        due=due_of(row, today),
        age=row.days,
        url=opened.url(),
        fragment=opened.url(PANEL),
        drop_url=opened.but(drop=True).url(),
        drop_fragment=opened.but(drop=True).url(PANEL),
        open=state.open == row.ref,
        callable=can,
        quick=quick,
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
        return Link(ref, shown, "url", text)
    head, colon, _ = text.partition(":")
    if text.startswith(("r:", "[")) or (colon and SCHEME.fullmatch(head) and len(head) > 1):
        return _locator(ref, shown, text, set(sources))
    if path_problem(text) is not None:
        return Link(ref, shown, "unsafe")
    return Link(ref, shown, "text" if low.endswith(TEXT_SUFFIXES) else "path")


def _locator(ref: str, shown: str, text: str, sources: set[str]) -> Link:
    try:
        found = parse_locator(text, DOC_TYPES)
    except AnchorError:
        found = None
    if found is not None and found.kind == "doc":
        return Link(ref, shown, "record", SearchState(hit=text).url())
    rid = found.value if found is not None and found.kind == "record" else ""
    m = RID.match(rid)
    if m is None or m.group(1) not in sources:
        return Link(ref, shown, "query", SearchState(query=text).url())
    anchor = found.anchor if found is not None else None
    if anchor is not None and anchor.kind == "time":
        return Link(ref, shown, "recording", RecordingState(rid=rid, at=time_at(anchor.start, anchor.end)).link())
    hit = text if text.startswith(("r:", "[")) else f"r:{rid}"
    return Link(ref, shown, "record", SearchState(hit=hit).url())


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


def _history(history: Sequence[Change]) -> list[HistoryLine]:
    """Newest first; Undo only on a line no undo has left out, never on the add or a renumber; Redo on an undo's own
    line (DR-0664)."""
    out: list[HistoryLine] = []
    for change in reversed(history):
        button = None
        if change.undone_by is None and change.kind not in ("add", "renumber"):
            button = "Redo" if change.kind == "undo" else "Undo"
        out.append(HistoryLine(change.day, change.at, change.by, change.via, change.line, change.undone_by, button,
                               change.id))  # fmt: skip
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
    return MeetingBox(row.ref, row.subject, when, [p for p in meeting.points if p.state != "rolled"])


def build_panel(
    shown: LedgerRead, meeting: LedgerRead | None, state: DayState, sources: Iterable[str], next_ref: str
) -> ItemPanel:
    """The item panel from `show` (DR-0664): the call, the fields, the meeting it hangs on with its points, the free
    text, the materials, where it came from, the counts and the history."""
    row = shown.items[0]
    # Show always gives its object's detail.
    detail = cast(Detail, shown.detail)
    today = shown.today
    named = list(sources)
    closed = row.state in ("done", "dropped")
    can = callable_row(row)
    verdicts = verdict_buttons([row.triage if row.state == "open" else None]) if can else []
    listing = state.opened(None)
    return ItemPanel(
        row=row,
        detail=detail,
        title=row.title,
        state_line=_state_line(row, today),
        due=row.due.isoformat() if row.due else "",
        source=link_of(row.source, None, named),
        meeting=_meeting_box(meeting),
        materials=[viewable(link_of(m.ref, m.label, named), state.opened(row.ref)) for m in detail.materials],
        counts=_counts(row, detail),
        history=_history(detail.history),
        verdicts=verdicts,
        snooze=snooze_choices(today) if can else [],
        tomorrow=(today + timedelta(days=1)).isoformat(),
        callable=can,
        closed=closed,
        archived=row.archived,
        drop=state.drop,
        next=next_ref,
        url=state.opened(row.ref).url(),
        close_url=listing.but(at=row.ref).url(),
        close_fragment=listing.url(PANEL),
    )


def viewable(link: Link, state: DayState) -> Link:
    """A text material with the page that shows it in the panel's viewer, and the viewer's own fragment (DR-0664)."""
    if link.kind != "text":
        return link
    shown = state.quiet().but(text=link.ref)
    return replace(link, href=shown.url(), fragment=shown.url(TEXT))


# -- the notice -------------------------------------------------------------------------------------------------------


def build_notice(state: DayState, lines: Sequence[BatchLine]) -> Notice | None:
    """What the last post did (DR-0661): the batch's lines and its Undo, a Redo after an undo; Nothing changed when a
    post wrote nothing; the rows a bulk's op didn't take."""
    done = state.done
    if done is None:
        return None
    skipped = (
        f" Skipped {', '.join(state.skipped)}: the change doesn't take them as they stand." if state.skipped else ""
    )
    if done == "nothing":
        return Notice("info", "Nothing changed." + skipped)
    if done == "opened":
        return Notice("info", "Opened it with its app.")
    if done == "shown":
        return Notice("info", "Shown in its folder.")
    if not lines:
        return Notice("info", "That change is no longer in the ledger's history." + skipped)
    said = [f"{line.ref}: {cut(line.line)}" for line in lines]
    undo = all(line.kind == "undo" for line in lines)
    redo = state.redo and undo
    head = ("Undone." if redo else "Redone.") if undo else "Saved."
    return Notice("info", f"{head}{skipped}", said, "Redo" if redo else "Undo", done, then_redo=not redo)
