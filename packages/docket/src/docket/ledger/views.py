"""The ledger's reads (DR-0638, DR-0643, DR-0644, DR-0660): snapshot, show, queue, list, meetings and suggest, each
asked of the index's tables (DR-0616, DR-0628) and given as the `ledger` tool's result, `LedgerRead`, and as lines.
Nothing here writes or folds: the caller opens the tables with `ledger_tables`, which folds the op files in memory only
when the index can't be had (DR-0619).

    3 open, 1 snoozed, 1 overdue; 1 meeting, 1 point open; 1 stream
    items
    A-0012 [reply] Ada Example: Send the dates (12d, due 10-21, 2d late) [pilot] @M-0003 <chase> src chat:C0X/1
    meetings 10-22 to 10-29, or with an open point
    M-0003 10-23 10:00-10:30 Pilot sync, Ada Example; 1 open; items A-0012
      #1 open The pilot's dates -> A-0012 01JAB3W8Q4X5M2Z7K9R0T6YVNC
    streams
    pilot active; where: Waiting on the venue; next: Book the room; deadline 10-30

A row's text is the ledger's own: a title, a subject, a point's text and a stream's where and next cut at 72 characters
but in `show`; a source never cut, since it is a locator `read` opens. Notes, descriptions, outcomes, attendees,
answers and the history come only in `show`, and no read quotes a record. A snooze whose day has come is open again,
as todo.py showed it: open in every count and filter, untriaged in the queue, its row marked `woke`. An item's counts
come from its ops, never its log's words (DR-0644): it is chased once per triage to chase that stands, an idle one
too.

`every` gives each item, meeting and stream as show gives it, in a few queries for all of them, for `docket ledger show
--all --json` (DR-0659, DR-0684): the same code makes each result from rows read together.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, cast, get_args
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from docket.errors import DocketError
from docket.index import ledger as index_ledger
from docket.index.ledger import Held, utc_text
from docket.index.query import parse_when
from docket.ledger import ops
from docket.ledger.fold import CLOSED, NUMBER, Item, number_text
from docket.ledger.meetings import meeting_text
from docket.ledger.store import MEETING_NUMBER
from docket.plaintext import fold, words

View = Literal["snapshot", "show", "queue", "list", "meetings", "suggest"]
State = Literal["open", "snoozed", "done", "dropped", "all"]
Kind = Literal["reply", "decision", "task", "invite", "waiting"]
VIEWS: tuple[str, ...] = get_args(View)
STATES: tuple[str, ...] = get_args(State)
KINDS: tuple[str, ...] = get_args(Kind)
K = 50
MAX_K = 500
TITLE = 72
OPEN = ("open", "snoozed")
# What each view takes besides itself. Anything else given is refused, so a filter is never dropped unseen.
TAKES: dict[str, tuple[str, ...]] = {
    "snapshot": ("since", "until"),
    "show": ("ref",),
    "queue": (),
    "list": ("who", "stream", "kind", "state", "since", "until", "k"),
    "meetings": ("since", "until"),
    "suggest": ("ref",),
}
# Where a snapshot's meetings start, from today, and how many days they span; the same for the meetings view.
SNAPSHOT_WINDOW = (-1, 8)
MEETINGS_WINDOW = (0, 7)
# A response a meeting's line says: the ones that change whether the owner goes.
SAID = ("tentative", "declined", "cancelled")
ITEM_COLUMNS = ("id", "number", "kind", "title", "who", "stream", "source", "meeting", "opened", "due", "state",
                "until", "closed", "triage", "archived")  # fmt: skip
MEETING_COLUMNS = ("id", "number", "subject", "start", "end", "organiser", "stream", "response", "archived", "adds")
STREAM_COLUMNS = ("id", "name", "status", "where", "next", "deadline", "updated")
# How many values one `IN (...)` takes: SQLite before 3.32 allows 999 host parameters to a statement.
CHUNK = 500


def _columns(names: Sequence[str]) -> str:
    # The module's own names, quoted, since `end` and `where` are SQL's words.
    return ", ".join(f'"{name}"' for name in names)


# The module's own table and column names, never a user's.
ITEMS = f"SELECT {_columns(ITEM_COLUMNS)} FROM items"  # noqa: S608
MEETINGS = f"SELECT {_columns(MEETING_COLUMNS)} FROM meetings"  # noqa: S608
STREAMS = f"SELECT {_columns(STREAM_COLUMNS)} FROM streams"  # noqa: S608
# A meeting's row and what show gives of it besides.
MEETINGS_WHOLE = (
    f'SELECT {_columns(MEETING_COLUMNS)}, attendees, "where", series, event, notes, outcome, materials, added_by '  # noqa: S608
    "FROM meetings"
)
# A meeting's or a stream's history, in the order show lists it.
OBJECT_HISTORY = "SELECT id, day, at, by, via, line, undone_by, kind, item FROM {table}"
BY_ID = " ORDER BY id, item, version"
BY_OPENED = " ORDER BY opened, claimed, id"
NO_COUNTS = (0, 0, 0, 0)
# Open now: an open item, or a snoozed one whose day, the value given, has come.
AWAKE = "(state = 'open' OR (state = 'snoozed' AND until <= ?))"
# A triage to chase and a set of the due, in an op line as the writer writes it. A quoted text holds none of these
# unescaped, so finding one in a plain line finds the op's own key.
CHASE = '"verdict":"chase"'
VERDICT = '"verdict"'
DUE = '"due":'
FIELDS = '"fields":{'
STANDS = "item IN ({marks}) AND fate IN ('applied', 'idle') AND kind IN ('triage', 'snooze', 'set', 'reopen')"


def _times(text: str) -> str:
    """SQL: how many times an op line holds `text`."""
    return f"(length(keys) - length(replace(keys, '{text}', ''))) / {len(text)}"


# What each standing line counts as, one CASE a line, so each test runs once: a snooze (s), a reopen (r), a chase (c)
# or a due moved (m) its text shows, a line to read whole (w), or nothing (''). A line with a backslash-u escape, which
# can spell chase or due, is read whole. A triage counts by its text when it is the only object in its line and names
# one verdict, since an object inside could hold a verdict of its own and a second verdict would win; a set when its
# fields are its only other object and its last key, the due inside them. Any other triage naming chase, or set naming
# due, is read whole, as the fold reads it (DR-0660).
CODE = (
    "CASE WHEN kind = 'snooze' THEN 's' WHEN kind = 'reopen' THEN 'r' WHEN instr(keys, '\\u') > 0 THEN 'w' "
    "WHEN kind = 'triage' THEN CASE WHEN instr(keys, '\"chase\"') = 0 THEN '' "
    f"WHEN instr(keys, '{CHASE}') > 0 AND {_times('{')} = 1 AND {_times(VERDICT)} = 1 THEN 'c' ELSE 'w' END "
    "ELSE CASE WHEN instr(keys, '\"due\"') = 0 THEN '' "
    f"WHEN {_times('{')} = 2 AND substr(keys, -2) = '}}}}' AND instr(keys, '{FIELDS}') > 0 "
    f"AND instr(keys, '{DUE}') > instr(keys, '{FIELDS}') THEN 'm' ELSE 'w' END END"
)
# The module's own SQL, never a user's text.
COUNTS = f"SELECT item, {CODE} FROM item_log WHERE {STANDS}"  # noqa: S608
READ_WHOLE = f"SELECT item, kind, keys FROM item_log WHERE {STANDS} AND {CODE} = 'w'"  # noqa: S608
# Every item's, in one pass, for show --all.
EVERY_COUNT = COUNTS.replace("item IN ({marks}) AND ", "")
# Where each code adds, in an item's counts: times chased, snoozes, due changes, reopens.
SLOT = {"c": 0, "s": 1, "m": 2, "r": 3}
# What q is matched in (DR-0665), each text column after the number, the subject first.
MATCH_MEETINGS = 'SELECT id, number, subject, organiser, attendees, "where", notes, outcome, adds FROM meetings'
MATCH_ITEMS = (
    "SELECT id, number, title, who, stream, kind, source, description, notes, outcome, materials, meeting FROM items"
)
MATCH_LOG = "SELECT item, group_concat(line, ' ') FROM item_log WHERE line IS NOT NULL GROUP BY item"
# A write's lines in a log table; a stream's ref is its name, or its add's id once it is undone.
BATCH_LINES = "SELECT id, version, item, kind, line FROM {table} WHERE batch = ? AND line IS NOT NULL"
BATCH_STREAM_LINES = (
    "SELECT l.id, l.version, coalesce(s.name, l.item), l.kind, l.line FROM stream_log l "
    "LEFT JOIN streams s ON s.id = l.stream WHERE l.batch = ? AND l.line IS NOT NULL"
)


class ItemRow(BaseModel):
    """An item as a read's line gives it. `ref` is its A- number, or its id while that names nothing (DR-0613); its
    title is cut at 72 characters but in show; its meeting is the M- number of the meeting it hangs on; `days` is how
    long it has been open, or was until it closed, `late` how many days today is past its due while it is open; `woke`
    marks a snoozed item whose day has come, its state still `snoozed` as the ledger holds it, open to every read and
    untriaged in the queue (DR-0660); `why` is suggest's."""

    ref: str
    id: str
    number: int | None
    kind: str
    title: str
    who: str
    stream: str
    source: str
    meeting: str | None
    opened: date
    due: date | None
    state: str
    until: date | None
    closed: date | None
    triage: str | None
    archived: bool
    days: int
    late: int | None
    why: str | None = None
    woke: bool = False


class PointRow(BaseModel):
    """A meeting's standing point: `ref` is `M-0040#2`, its place now; `id` names it for good, as a batch's point
    takes it; `item` is the item it is about; `answer` comes in show only. `from` and `to` are the meetings, by ref,
    a roll carried it from and to: a copy's `from`, a rolled point's `to`, None for a point no roll touched
    (DR-0660)."""

    # `from` is Python's word: the field is `from_`, read and written as `from`, as the points table names it.
    model_config = ConfigDict(validate_by_name=True, serialize_by_alias=True)

    ref: str
    id: str
    meeting: str
    n: int
    state: str
    text: str
    item: str | None
    proposed: bool
    answer: str | None = None
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None


class MeetingRow(BaseModel):
    """A meeting as a read's line gives it: its start and end in the owner's zone, its subject cut at 72 characters
    but in show, how many of its points are open, and its open items by ref."""

    ref: str
    id: str
    number: int | None
    subject: str
    start: datetime
    end: datetime
    organiser: str | None
    stream: str | None
    response: str | None
    archived: bool
    points_open: int
    items: list[str]


class StreamRow(BaseModel):
    """A stream: `ref` is its name; `where` is where it stands and `next` what comes next, todo.py's `where -> next`,
    both status text cut at 72 characters but in show."""

    ref: str
    id: str
    name: str
    status: str
    next: str | None
    deadline: date | None
    where: str | None
    updated: date


class Counts(BaseModel):
    """A read's counts, each where its view gives it: items by state and overdue (snapshot, list, a stream's show),
    untriaged and by triage (queue), chased items and times and the median days from open to done (list), meetings
    and their open points (snapshot, meetings), streams (snapshot). `items` counts every item that matched, `k` or
    not. `open` counts the snoozes whose day has come, and `woke` how many of them; `snoozed` the ones still asleep."""

    items: int = 0
    open: int = 0
    woke: int = 0
    snoozed: int = 0
    done: int = 0
    dropped: int = 0
    overdue: int = 0
    untriaged: int = 0
    triage: dict[str, int] = {}
    chased_items: int = 0
    chased: int = 0
    median_days_to_done: float | None = None
    meetings: int = 0
    points_open: int = 0
    streams: int = 0


class Change(BaseModel):
    """One op in a history: its log line, the undo that left it out, and its kind, the op's name (add, set, triage,
    renumber, undo and the rest), so a page can offer an undo only where one applies (DR-0660)."""

    id: str
    day: date
    at: datetime | None
    by: str
    via: str
    line: str
    undone_by: str | None
    kind: str


class MaterialRow(BaseModel):
    ref: str
    label: str | None


class Detail(BaseModel):
    """What show gives of its object beyond its row: who added it, the number an item claimed when a clash left it
    none, its free text, a meeting's attendees, where, series and event, its materials, the others that held the
    number it was named by, an item's counts from its ops (DR-0644), and its history, oldest first."""

    kind: Literal["item", "meeting", "stream"]
    added_by: str
    claimed: int | None = None
    description: str | None = None
    notes: str | None = None
    outcome: str | None = None
    attendees: list[str] = []
    where: str | None = None
    series: str | None = None
    event: str | None = None
    materials: list[MaterialRow] = []
    also: list[str] = []
    chased: int = 0
    snoozes: int = 0
    due_moves: int = 0
    reopens: int = 0
    history: list[Change] = []


class BatchLine(BaseModel):
    """One line of a write, as a page's notice says it: what the op changed by its ref (an item's, a meeting's or a
    stream's), the op's kind and its log line."""

    ref: str
    kind: str
    line: str


class LedgerRead(BaseModel):
    """What the `ledger` tool read (DR-0638, DR-0660): its view, today as the owner's day the counts and lines are
    made from, the window its meetings or items were read in, its counts, and its rows; show's object in `detail`."""

    view: View
    today: date
    since: datetime | None = None
    until: datetime | None = None
    counts: Counts
    items: list[ItemRow] = []
    meetings: list[MeetingRow] = []
    points: list[PointRow] = []
    streams: list[StreamRow] = []
    detail: Detail | None = None


@dataclass(frozen=True)
class Ask:
    """What a read asks: a view and the `ledger` tool's parameters, with its defaults. `k` None gives list every row,
    as the command's --json and the pages read it (DR-0645); the tool's own k stops at 500."""

    view: str = "snapshot"
    ref: str | None = None
    since: str | None = None
    until: str | None = None
    who: str | None = None
    stream: str | None = None
    kind: str | None = None
    state: str = "open"
    k: int | None = K

    def check(self) -> None:
        """Refused: a view, state or kind this Docket doesn't have, a k out of bounds, a ref missing where one is
        needed, and a parameter given to a view that doesn't take it."""
        if self.view not in VIEWS:
            raise DocketError(f"view is one of {', '.join(VIEWS)}, not {self.view!r}")
        defaults = Ask()
        takes = TAKES[self.view]
        for f in fields(self):
            if f.name not in ("view", *takes) and getattr(self, f.name) != getattr(defaults, f.name):
                raise DocketError(f"{self.view} takes {', '.join(takes) or 'nothing more'}, not {f.name}")
        if self.state not in STATES:
            raise DocketError(f"state is one of {', '.join(STATES)}, not {self.state!r}")
        if self.kind is not None and self.kind not in KINDS:
            raise DocketError(f"kind is one of {', '.join(KINDS)}, not {self.kind!r}")
        if self.k is not None and not 1 <= self.k <= MAX_K:
            raise DocketError(f"k is 1 to {MAX_K}, not {self.k}")
        if "ref" in takes and not (self.ref or "").strip():
            what = "a meeting's M- number or id" if self.view == "suggest" else "an A- or M- number, an id or a stream"
            raise DocketError(f"{self.view} needs ref, {what}")


def now() -> datetime:
    """The clock a read's today comes from."""
    return datetime.now(UTC)


def read(conn: sqlite3.Connection, ask: Ask, tz: ZoneInfo, at: datetime, *, chased: bool = True) -> LedgerRead:
    """One read of the ledger's tables as `ask` says, today the owner's day at `at`. It writes nothing. `chased` False
    leaves list's chase counts at 0: the day pages show none, and reading them is most of a whole list's time
    (DR-0668); the tool always reads them."""
    ask.check()
    reader = _Reader(conn, tz, at, chased=chased)
    views: dict[str, Callable[[Ask], LedgerRead]] = {
        "snapshot": reader.snapshot, "show": reader.show, "queue": reader.queue, "list": reader.listing,
        "meetings": reader.meetings, "suggest": reader.suggest,
    }  # fmt: skip
    return views[ask.view](ask)


def matching(conn: sqlite3.Connection, q: str) -> set[str]:
    """The items and meetings, by id, whose text holds every word of `q`, each folded as Search folds text (DR-0665):
    an item's number, title, who, stream, kind, source, description, notes and outcome, its materials' refs and
    labels, its meeting's subject and its log's lines; a meeting's number, subject, organiser, attendees, where, notes,
    outcome and its standing points' text and answers. A `q` with no word matches nothing. The pages' read, off the
    tool."""
    wanted = [fold(w) for w in words(q)]
    if not wanted:
        return set()
    said: dict[str, list[str]] = {}
    subjects: dict[str, str] = {}
    for meeting_id, number, *texts, adds in conn.execute(MATCH_MEETINGS):
        said[meeting_id] = [meeting_text(number) if number is not None else "", *(t or "" for t in texts)]
        subjects |= dict.fromkeys(json.loads(adds), texts[0])
    for meeting_id, text, answer in conn.execute("SELECT meeting, text, answer FROM points WHERE n IS NOT NULL"):
        said.setdefault(meeting_id, []).extend((text, answer or ""))
    for item_id, number, *texts, materials, meeting in conn.execute(MATCH_ITEMS):
        said[item_id] = [number_text(number) if number is not None else "", *(t or "" for t in texts)]
        said[item_id] += [f"{m['ref']} {m.get('label') or ''}" for m in json.loads(materials)]
        said[item_id].append(subjects.get(meeting, ""))
    for item_id, lines in conn.execute(MATCH_LOG):
        if item_id in said:
            said[item_id].append(lines)
    return {key for key, texts in said.items() if all(word in _folded(" ".join(texts)) for word in wanted)}


def _folded(text: str) -> str:
    # fold() reads a character at a time; ASCII text, most of a ledger's, folds as it lowercases.
    return text.lower() if text.isascii() else fold(text)


def batch_lines(conn: sqlite3.Connection, batch: str) -> list[BatchLine]:
    """A write's lines, by its batch (its first op's id), in the ops' order: the ref of what each op changed, its kind
    and its log line; an op that shows no line, such as a renumber, is left out. The pages' notice after a write
    (DR-0710), off the tool."""
    reader = _Reader(conn, ZoneInfo("UTC"), now())
    items = conn.execute(BATCH_LINES.format(table="item_log"), (batch,)).fetchall()
    meetings = conn.execute(BATCH_LINES.format(table="meeting_log"), (batch,)).fetchall()
    item_refs = reader.item_refs(row[2] for row in items)
    meeting_refs = reader.meetings_of(row[2] for row in meetings)
    rows = [(i, v, item_refs[add], kind, line) for i, v, add, kind, line in items]
    rows += [(i, v, meeting_refs[add], kind, line) for i, v, add, kind, line in meetings]
    rows += conn.execute(BATCH_STREAM_LINES, (batch,)).fetchall()
    rows.sort(key=lambda row: (row[0], row[1]))
    return [BatchLine(ref=ref, kind=kind, line=line) for _, _, ref, kind, line in rows]


def cut(text: str, width: int = TITLE) -> str:
    """Text on one line, cut to `width` characters with an ellipsis."""
    one = " ".join(text.split())
    return one if len(one) <= width else one[: width - 1] + "…"


def _day(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


def _chunks(values: Iterable[str]) -> list[list[str]]:
    ordered = sorted(set(values))
    return [ordered[k : k + CHUNK] for k in range(0, len(ordered), CHUNK)]


class _Reader:
    """The tables asked for one read, with the numbers that name nothing (DR-0613) read once, when a row needs them."""

    def __init__(self, conn: sqlite3.Connection, tz: ZoneInfo, at: datetime, *, chased: bool = True) -> None:
        self.conn = conn
        self.tz = tz
        self.chased = chased
        self.today = at.astimezone(tz).date()
        self._item_clashes: set[int] | None = None
        self._meeting_clashes: set[int] | None = None

    def select(self, sql: str, values: Iterable[str]) -> list[tuple[Any, ...]]:
        """The rows `sql` gives for every value, `{marks}` standing for the `IN` list, a chunk at a time. Replaced, not
        formatted: the counts' SQL searches op lines for a brace."""
        out: list[tuple[Any, ...]] = []
        for chunk in _chunks(values):
            out += self.conn.execute(sql.replace("{marks}", ", ".join("?" * len(chunk))), chunk).fetchall()
        return out

    # -- refs ---------------------------------------------------------------------------------------------------------

    def item_ref(self, item_id: str, number: int | None) -> str:
        """An item's A- number, or its id while no number names it alone: during a clash, and for a number another
        item that stands has claimed too, which a batch refuses (DR-0613)."""
        if self._item_clashes is None:
            sql = "SELECT number FROM item_numbers WHERE hidden = 0 GROUP BY number HAVING count(*) > 1"
            self._item_clashes = {n for (n,) in self.conn.execute(sql)}
        return number_text(number) if number is not None and number not in self._item_clashes else item_id

    def meeting_ref(self, meeting_id: str, number: int | None) -> str:
        if self._meeting_clashes is None:
            sql = "SELECT number FROM meeting_numbers WHERE hidden = 0 GROUP BY number HAVING count(*) > 1"
            self._meeting_clashes = {n for (n,) in self.conn.execute(sql)}
        return meeting_text(number) if number is not None and number not in self._meeting_clashes else meeting_id

    def item_refs(self, ids: Iterable[str]) -> dict[str, str]:
        """Each item's ref, by id; an id no item that stands has, as it is."""
        wanted = set(ids)
        found = {i: self.item_ref(i, n) for i, n in self.select("SELECT id, number FROM items WHERE id IN ({marks})",
                                                                wanted)}  # fmt: skip
        return {i: found.get(i, i) for i in wanted}

    def meetings_of(self, adds: Iterable[str]) -> dict[str, str]:
        """The ref of the meeting each add belongs to; an add of no meeting that stands, as it is."""
        wanted = set(adds)
        owners = dict(self.select("SELECT DISTINCT item, meeting FROM meeting_log WHERE item IN ({marks})", wanted))
        refs = {
            i: self.meeting_ref(i, n)
            for i, n in self.select("SELECT id, number FROM meetings WHERE id IN ({marks})", set(owners.values()))
        }
        return {add: refs.get(owners.get(add, ""), add) for add in wanted}

    # -- rows ---------------------------------------------------------------------------------------------------------

    def item_rows(self, raws: Sequence[tuple[Any, ...]], *, whole: bool = False) -> list[ItemRow]:
        meetings = self.meetings_of(r[7] for r in raws if r[7])
        today = self.today
        out: list[ItemRow] = []
        for raw in raws:
            (item_id, number, kind, title, who, stream, source, meeting, opened, due, state, until, closed, triage,
             archived) = raw  # fmt: skip
            opened_day = date.fromisoformat(opened)
            due_day = _day(due)
            closed_day = _day(closed)
            late = (today - due_day).days if due_day is not None and state in OPEN and due_day < today else None
            until_day = _day(until)
            out.append(ItemRow(
                ref=self.item_ref(item_id, number), id=item_id, number=number, kind=kind,
                title=title if whole else cut(title), who=" ".join(who.split()), stream=stream, source=source,
                meeting=meetings[meeting] if meeting else None, opened=opened_day, due=due_day, state=state,
                until=until_day, closed=closed_day, triage=triage, archived=bool(archived),
                days=((closed_day or today) - opened_day).days, late=late,
                woke=state == "snoozed" and until_day is not None and until_day <= today,
            ))  # fmt: skip
        return out

    def items(self, where: str = "", args: Sequence[Any] = (), *, whole: bool = False) -> list[ItemRow]:
        return self.item_rows(self.conn.execute(ITEMS + where + BY_OPENED, args).fetchall(), whole=whole)

    def meeting_rows(self, raws: Sequence[tuple[Any, ...]], *, whole: bool = False) -> list[MeetingRow]:
        """Each meeting's row with its open points counted and its open items by ref, in the owner's zone."""
        ids = [r[0] for r in raws]
        counted = dict(self.select("SELECT meeting, count(*) FROM points WHERE meeting IN ({marks}) AND n IS NOT "
                                   "NULL AND state = 'open' GROUP BY meeting", ids))  # fmt: skip
        owner = {add: r[0] for r in raws for add in json.loads(r[9])}
        linked: dict[str, list[str]] = {}
        if raws:
            sql = "SELECT id, number, meeting FROM items WHERE meeting IS NOT NULL AND state IN ('open', 'snoozed')"
            for item_id, number, add in self.conn.execute(sql + BY_OPENED):
                if add in owner:
                    linked.setdefault(owner[add], []).append(self.item_ref(item_id, number))
        return [
            MeetingRow(
                ref=self.meeting_ref(meeting_id, number), id=meeting_id, number=number,
                subject=subject if whole else cut(subject),
                start=datetime.fromisoformat(start).astimezone(self.tz),
                end=datetime.fromisoformat(end).astimezone(self.tz), organiser=organiser, stream=stream,
                response=response, archived=bool(archived), points_open=counted.get(meeting_id, 0),
                items=linked.get(meeting_id, []),
            )
            for meeting_id, number, subject, start, end, organiser, stream, response, archived, _ in raws
        ]  # fmt: skip

    def point_rows(self, meetings: Sequence[MeetingRow], *, whole: bool = False) -> list[PointRow]:
        """The standing points of the meetings, in the meetings' order and each one's by n."""
        place = {m.id: k for k, m in enumerate(meetings)}
        refs = {m.id: m.ref for m in meetings}
        raws = self.select('SELECT id, meeting, n, text, state, answer, item, proposed, "from", "to" FROM points WHERE '
                           "meeting IN ({marks}) AND n IS NOT NULL", place)  # fmt: skip
        raws.sort(key=lambda r: (place[r[1]], r[2]))
        items = self.item_refs(r[6] for r in raws if r[6])
        ends = self.roll_ends(end for r in raws for end in r[8:] if end)
        return [
            PointRow(
                ref=f"{refs[meeting]}#{n}", id=point_id, meeting=refs[meeting], n=n, state=state,
                text=text if whole else cut(text), item=items[item] if item else None, proposed=bool(proposed),
                answer=answer if whole else None, to=ends[end] if end else None,
                **{"from": ends[start] if start else None},
            )
            for point_id, meeting, n, text, state, answer, item, proposed, start, end in raws
        ]  # fmt: skip

    def roll_ends(self, ends: Iterable[str]) -> dict[str, str]:
        """The meeting, by ref, each end of a roll lies on: a point's meeting, or the meeting an import named when it
        couldn't name the point; an end on no meeting that stands, as it is."""
        wanted = set(ends)
        on: dict[str, str] = dict(self.select("SELECT id, meeting FROM points WHERE id IN ({marks})", wanted))
        refs = self.meetings_of(on.get(end, end) for end in wanted)
        return {end: refs[on.get(end, end)] for end in wanted}

    def stream_rows(self, where: str = "", args: Sequence[Any] = (), *, whole: bool = False) -> list[StreamRow]:
        return [
            StreamRow(
                ref=name, id=stream_id, name=name, status=status, next=nxt if whole or nxt is None else cut(nxt),
                deadline=_day(deadline), where=place if whole or place is None else cut(place),
                updated=date.fromisoformat(updated),
            )
            for stream_id, name, status, place, nxt, deadline, updated in self.conn.execute(
                STREAMS + where + " ORDER BY name", args
            )
        ]  # fmt: skip

    # -- counts -------------------------------------------------------------------------------------------------------

    def by_state(self, items: Sequence[ItemRow]) -> Counts:
        states = Counter(item.state for item in items)
        woke = sum(1 for item in items if item.woke)
        return Counts(
            items=len(items), open=states["open"] + woke, woke=woke, snoozed=states["snoozed"] - woke,
            done=states["done"], dropped=states["dropped"], overdue=sum(1 for item in items if item.late is not None),
        )  # fmt: skip

    def history_counts(self, ids: Iterable[str] | None) -> dict[str, tuple[int, int, int, int]]:
        """Each item's times chased, snoozes, due changes and reopens, from the ops that stand, an idle one too
        (DR-0644): a chase repeated on an item already marked chase is a chase. Items with none are left out; None
        asks every item's.

        SQL codes a line by its text where its text can't mislead (`CODE`); any other line that may chase or move the
        due is read whole, by the reader the fold reads it with, so an escape, a nested object or a key given twice
        counts as the fold takes it."""
        out: dict[str, list[int]] = {}
        whole: set[str] = set()
        coded = self.conn.execute(EVERY_COUNT).fetchall() if ids is None else self.select(COUNTS, ids)
        for item_id, code in coded:
            if code == "w":
                whole.add(item_id)
            elif code:
                out.setdefault(item_id, [0, 0, 0, 0])[SLOT[code]] += 1
        for item_id, kind, keys in self.select(READ_WHOLE, whole):
            op = ops.read_op(keys)
            counts = out.setdefault(item_id, [0, 0, 0, 0])
            if kind == "triage":
                counts[0] += op.verdict == "chase"
            else:
                counts[2] += "due" in (op.fields or {})
        return {item_id: (a, b, c, d) for item_id, (a, b, c, d) in out.items()}

    # -- windows ------------------------------------------------------------------------------------------------------

    def instant(self, value: date | datetime, *, end: bool) -> datetime:
        """A time in the owner's zone, read in it when it has no offset; a date as the start of that day in the owner's
        zone, or of the next day for an end."""
        if isinstance(value, datetime):
            return value.astimezone(self.tz) if value.tzinfo is not None else value.replace(tzinfo=self.tz)
        day = value + timedelta(days=1) if end else value
        return datetime(day.year, day.month, day.day, tzinfo=self.tz)

    def window(self, ask: Ask, default: tuple[int, int]) -> tuple[datetime, datetime]:
        """The window a view's meetings are read in: since and until as given, a date the owner's whole day; by
        default from today plus the first of `default` for its second's days; since alone reads that many days on."""
        start, days = default
        given_since = parse_when(ask.since, "since")
        given_until = parse_when(ask.until, "until")
        since = self.instant(given_since or self.today + timedelta(days=start), end=False)
        if given_until is not None:
            until = self.instant(given_until, end=True)
        elif given_since is not None:
            until = since + timedelta(days=days)
        else:
            until = self.instant(self.today + timedelta(days=start + days - 1), end=True)
        if until <= since:
            said = "" if given_since is not None else f", {since:%Y-%m-%d} by default"
            raise DocketError(f"until is before since{said}: the window is empty")
        return since, until

    def overlapping(self, since: datetime, until: datetime) -> tuple[str, list[str]]:
        """The SQL that keeps the meetings overlapping a window, and its values: started before its end, and ending
        after its start or starting at it. Times are whole minutes, so since is taken down to its minute and until
        up to its."""
        floor = since.astimezone(UTC).replace(second=0, microsecond=0)
        top = until.astimezone(UTC)
        ceiling = top.replace(second=0, microsecond=0) + timedelta(minutes=top.second > 0 or top.microsecond > 0)
        sql = "(utc < ? AND (strftime('%Y-%m-%dT%H:%MZ', \"end\") > ? OR utc >= ?))"
        return sql, [utc_text(ceiling), utc_text(floor), utc_text(floor)]

    # -- the views ----------------------------------------------------------------------------------------------------

    def snapshot(self, ask: Ask) -> LedgerRead:
        """The open and snoozed items; the meetings not archived (DR-0622) in the window or with an open point, their
        standing points and open items; every stream."""
        since, until = self.window(ask, SNAPSHOT_WINDOW)
        items = self.items(" WHERE state IN ('open', 'snoozed')")
        overlap, values = self.overlapping(since, until)
        open_point = "EXISTS (SELECT 1 FROM points p WHERE p.meeting = m.id AND p.n IS NOT NULL AND p.state = 'open')"
        raws = self.conn.execute(
            f"{MEETINGS} m WHERE archived = 0 AND ({overlap} OR {open_point}) ORDER BY utc, id", values
        ).fetchall()
        meetings = self.meeting_rows(raws)
        streams = self.stream_rows()
        counts = self.by_state(items)
        counts.meetings, counts.streams = len(meetings), len(streams)
        counts.points_open = sum(m.points_open for m in meetings)
        return LedgerRead(
            view="snapshot", today=self.today, since=since, until=until, counts=counts, items=items,
            meetings=meetings, points=self.point_rows(meetings), streams=streams,
        )  # fmt: skip

    def queue(self, _: Ask) -> LedgerRead:
        """The open items, a snooze whose day has come among them: the untriaged first, then each verdict in todo.py's
        order and any other after, each by due, those with none last."""
        items = self.items(f" WHERE {AWAKE}", (self.today.isoformat(),))
        rank = {verdict: k for k, verdict in enumerate((None, *ops.VERDICTS))}
        far = date.max
        items.sort(key=lambda i: (rank.get(i.triage, len(rank)), i.triage or "", i.due or far))
        counts = Counts(items=len(items), open=len(items), woke=sum(1 for item in items if item.woke))
        for item in items:
            if item.triage is None:
                counts.untriaged += 1
            else:
                counts.triage[item.triage] = counts.triage.get(item.triage, 0) + 1
        return LedgerRead(view="queue", today=self.today, counts=counts, items=items)

    def listing(self, ask: Ask) -> LedgerRead:
        """The items matching every filter, who a part of it with case aside and the window on the day each opened;
        counts over all of them, then the first k."""
        where: list[str] = []
        args: list[Any] = []
        if ask.state in ("open", "snoozed"):
            where.append(AWAKE if ask.state == "open" else "state = 'snoozed' AND until > ?")
            args.append(self.today.isoformat())
        elif ask.state != "all":
            where.append("state = ?")
            args.append(ask.state)
        for name in ("stream", "kind"):
            if getattr(ask, name) is not None:
                where.append(f"{name} = ?")
                args.append(getattr(ask, name))
        window: list[datetime | None] = []
        for given, test, end in ((ask.since, "opened >= ?", False), (ask.until, "opened <= ?", True)):
            value = parse_when(given, "until" if end else "since")
            if value is None:
                window.append(None)
                continue
            window.append(self.instant(value, end=end))
            # A time's day is the owner's day it falls on.
            day = self.instant(value, end=end).date() if isinstance(value, datetime) else value
            where.append(test)
            args.append(day.isoformat())
        since, until = window
        items = self.items(f" WHERE {' AND '.join(where)}" if where else "", args)
        if ask.who is not None:
            part = ask.who.casefold().strip()
            items = [i for i in items if part in i.who.casefold()]
        counts = self.by_state(items)
        history = self.history_counts(i.id for i in items) if self.chased else {}
        counts.chased_items = sum(1 for c in history.values() if c[0])
        counts.chased = sum(c[0] for c in history.values())
        done = [i.days for i in items if i.state == "done"]
        counts.median_days_to_done = float(statistics.median(done)) if done else None
        return LedgerRead(
            view="list", today=self.today, since=since, until=until, counts=counts,
            items=items if ask.k is None else items[: ask.k],
        )  # fmt: skip

    def meetings(self, ask: Ask) -> LedgerRead:
        """The meetings overlapping the window, by start."""
        since, until = self.window(ask, MEETINGS_WINDOW)
        overlap, values = self.overlapping(since, until)
        raws = self.conn.execute(f"{MEETINGS} WHERE {overlap} ORDER BY utc, id", values).fetchall()
        meetings = self.meeting_rows(raws)
        counts = Counts(meetings=len(meetings), points_open=sum(m.points_open for m in meetings))
        return LedgerRead(view="meetings", today=self.today, since=since, until=until, counts=counts, meetings=meetings)

    def suggest(self, ask: Ask) -> LedgerRead:
        """The meeting, and the open items worth raising in it, each with why (DR-0629, DR-0634)."""
        meeting_id = self.meeting_named(str(ask.ref), "suggest")[0]
        suggested = index_ledger.suggest(self.conn, meeting_id)
        rows = {r.id: r for r in self.items(f" WHERE id IN ({', '.join('?' * len(suggested))})",
                                            [s.item for s in suggested])} if suggested else {}  # fmt: skip
        items = [rows[s.item].model_copy(update={"why": s.why}) for s in suggested]
        meetings = self.meeting_rows(self.conn.execute(f"{MEETINGS} WHERE id = ?", (meeting_id,)).fetchall())
        counts = Counts(items=len(items), meetings=1, points_open=meetings[0].points_open)
        return LedgerRead(view="suggest", today=self.today, counts=counts, items=items, meetings=meetings)

    # -- show ---------------------------------------------------------------------------------------------------------

    def show(self, ask: Ask) -> LedgerRead:
        """One item, meeting or stream whole, named as a batch names it: an id of an item or of a meeting's or
        stream's add, an M- number, a stream's name, an A- number."""
        ref = str(ask.ref)
        text = ref.strip()
        if ops.ULID.fullmatch(text) and not self.conn.execute("SELECT 1 FROM items WHERE id = ?", (text,)).fetchone():
            for table, column in (("meeting_log", "meeting"), ("stream_log", "stream")):
                # The module's own table and column names.
                row = self.conn.execute(f"SELECT {column} FROM {table} WHERE item = ? LIMIT 1", (text,)).fetchone()  # noqa: S608
                if row is None:
                    continue
                if column == "meeting":
                    return self.show_meeting(*self.meeting_named(text, "show"))
                return self.show_stream(self.stream_row(row[0], f"{text} was undone"))
        if MEETING_NUMBER.fullmatch(text):
            return self.show_meeting(*self.meeting_named(text, "show"))
        if not ops.ULID.fullmatch(text):
            found = self.conn.execute("SELECT id FROM streams WHERE name = ?", (text,)).fetchone()
            if found is not None:
                return self.show_stream(self.stream_row(found[0], ""))
            if not NUMBER.fullmatch(text):
                raise DocketError(
                    f"{ref!r} isn't an item, a meeting or a stream: name one by its A- or M- number, its id, or a "
                    "stream's name"
                )
        return self.show_item(index_ledger.find(self.conn, text))

    def show_item(self, shown: index_ledger.Shown) -> LedgerRead:
        item_id = shown.item.id
        (row,) = self.items(" WHERE id = ?", (item_id,), whole=True)
        named = number_text(shown.named or 0)
        history = [Change(id=c.id, day=c.day, at=c.at, by=c.by, via=c.via, line=c.line, undone_by=c.undone_by,
                          kind=c.kind)
                   for c in shown.history]  # fmt: skip
        counted = self.history_counts([item_id]).get(item_id, NO_COUNTS)
        also = [f"{named} by {other}, {index_ledger.now_held(held)}" for other, held in shown.also]
        return self.item_read(shown.item, row, counted, history, also)

    def item_read(
        self, item: Item, row: ItemRow, counted: tuple[int, int, int, int], history: list[Change], also: list[str]
    ) -> LedgerRead:
        """An item's show from its fields, its row read whole, its counts, its history and the others that held the
        number it was named by."""
        chased, snoozes, due_moves, reopens = counted
        detail = Detail(
            kind="item", added_by=item.added_by, claimed=item.claimed if item.number is None else None,
            description=item.description, notes=item.notes, outcome=item.outcome,
            materials=[MaterialRow(ref=m.ref, label=m.label) for m in item.materials], also=also, chased=chased,
            snoozes=snoozes, due_moves=due_moves, reopens=reopens, history=history,
        )  # fmt: skip
        return LedgerRead(view="show", today=self.today, counts=Counts(items=1), items=[row], detail=detail)

    def meeting_named(self, ref: str, view: str) -> tuple[str, int | None, list[tuple[str, int | None]]]:
        """The meeting an M- number or an id of its adds names, the number it was named by, and the other meetings
        that held that number with the number each holds now. A number one meeting holds now finds it; one that none
        holds now, but more than one held, names them and finds none."""
        text = ref.strip()
        if ops.ULID.fullmatch(text):
            row = self.conn.execute("SELECT meeting FROM meeting_log WHERE item = ? LIMIT 1", (text,)).fetchone()
            if row is None or not self.conn.execute("SELECT 1 FROM meetings WHERE id = ?", row).fetchone():
                raise DocketError(f"{text} was undone" if row else f"no meeting {text}")
            return row[0], None, []
        m = MEETING_NUMBER.fullmatch(text)
        if m is None:
            raise DocketError(f"{view} takes a meeting: its M- number or its id, not {ref!r}")
        number = int(m.group(1))
        label = meeting_text(number)
        holders = Held(self.conn).meeting_holders(number)
        if not holders:
            hidden = self.conn.execute("SELECT 1 FROM meeting_numbers WHERE number = ? LIMIT 1", (number,)).fetchone()
            raise DocketError(f"{label} was undone" if hidden else f"no meeting {label}")
        held: dict[str, int | None] = dict(
            self.select("SELECT id, number FROM meetings WHERE id IN ({marks})", holders)
        )  # fmt: skip
        holding = [h for h in holders if held[h] == number]
        if not holding and len(holders) > 1:
            named = ", ".join(f"{h} ({_now_meeting(held[h])})" for h in holders)
            raise DocketError(f"{label} isn't held now; it was held by {named}; name the meeting by its id")
        chosen = (holding or holders)[0]
        return chosen, number, [(h, held[h]) for h in holders if h != chosen]

    def show_meeting(self, meeting_id: str, named: int | None, also: Sequence[tuple[str, int | None]]) -> LedgerRead:
        (raw,) = self.conn.execute(MEETINGS_WHOLE + " WHERE id = ?", (meeting_id,)).fetchall()
        (row,) = self.meeting_rows([raw[:10]], whole=True)
        adds: list[str] = json.loads(raw[9])
        linked = self.item_rows(self.conn.execute(
            f"{ITEMS} WHERE meeting IN ({', '.join('?' * len(adds))}){BY_OPENED}", adds
        ).fetchall())  # fmt: skip
        parts = (linked, self.point_rows([row], whole=True), self.history("meeting_log", adds))
        return self.meeting_read(raw, row, *parts, named=named, also=also)

    def meeting_read(
        self, raw: Sequence[Any], row: MeetingRow, linked: list[ItemRow], points: list[PointRow], history: list[Change],
        *, named: int | None = None, also: Sequence[tuple[str, int | None]] = (),
    ) -> LedgerRead:  # fmt: skip
        """A meeting's show from its row, read whole, its items, its standing points and its history."""
        attendees, place, series, event, notes, outcome, materials, added_by = raw[10:]
        label = meeting_text(named or 0)
        detail = Detail(
            kind="meeting", added_by=added_by, notes=notes, outcome=outcome, attendees=json.loads(attendees),
            where=place, series=series, event=event, materials=_materials(materials),
            also=[f"{label} by {other}, {_now_meeting(held)}" for other, held in also], history=history,
        )  # fmt: skip
        counts = Counts(meetings=1, points_open=row.points_open, items=len(linked))
        return LedgerRead(
            view="show", today=self.today, counts=counts, items=linked, meetings=[row], points=points, detail=detail
        )

    def stream_row(self, stream_id: str, gone: str) -> tuple[StreamRow, list[str], str]:
        rows = self.stream_rows(" WHERE id = ?", (stream_id,), whole=True)
        if not rows:
            raise DocketError(gone)
        adds, added_by = self.conn.execute("SELECT adds, added_by FROM streams WHERE id = ?", (stream_id,)).fetchone()
        return rows[0], json.loads(adds), added_by

    def show_stream(self, found: tuple[StreamRow, list[str], str]) -> LedgerRead:
        row, adds, added_by = found
        every = self.items(" WHERE stream = ?", (row.name,))
        counts = self.by_state(every)
        detail = Detail(kind="stream", added_by=added_by, where=row.where, history=self.history("stream_log", adds))
        items = [i for i in every if i.state in OPEN]
        return LedgerRead(view="show", today=self.today, counts=counts, items=items, streams=[row], detail=detail)

    def history(self, table: str, adds: Sequence[str]) -> list[Change]:
        """A meeting's or a stream's history: the lines of its adds that show, oldest first, two of one id by add
        and version."""
        sql = f"{OBJECT_HISTORY.format(table=table)} WHERE item IN ({{marks}}) AND line IS NOT NULL{BY_ID}"
        return [_change(r) for r in sorted(self.select(sql, adds), key=lambda r: r[0])]

    # -- every object (DR-0659) ---------------------------------------------------------------------------------------

    def every(self) -> tuple[list[LedgerRead], list[LedgerRead], list[LedgerRead]]:
        """Every item, meeting and stream that stands, archived ones too, each as show gives it by its ref, which
        names it alone, so no other holder of its number comes with it: items by the day each opened, meetings by
        start, streams by name. A few queries for all of them, where show asks a few for each."""
        streams = [r for (r,) in self.conn.execute("SELECT id FROM streams ORDER BY name")]
        return self.every_item(), self.every_meeting(), [self.show_stream(self.stream_row(s, "")) for s in streams]

    def every_item(self) -> list[LedgerRead]:
        found = index_ledger.every_item(self.conn)
        history: dict[str, list[Change]] = {}
        for r in self.conn.execute(f"{index_ledger.HISTORY} WHERE line IS NOT NULL{index_ledger.IN_ORDER}"):
            history.setdefault(r[8], []).append(_change(r))
        counted = self.history_counts(None)
        return [
            self.item_read(found[row.id], row, counted.get(row.id, NO_COUNTS), history.get(row.id, []), [])
            for row in self.items(whole=True)
        ]

    def every_meeting(self) -> list[LedgerRead]:
        raws = self.conn.execute(MEETINGS_WHOLE + " ORDER BY utc, id").fetchall()
        rows = self.meeting_rows([raw[:10] for raw in raws], whole=True)
        owner = {add: raw[0] for raw in raws for add in json.loads(raw[9])}
        linked: dict[str, list[ItemRow]] = {}
        on = self.conn.execute(f"{ITEMS} WHERE meeting IS NOT NULL{BY_OPENED}").fetchall()
        for raw, row in zip(on, self.item_rows(on), strict=True):
            if raw[7] in owner:
                linked.setdefault(owner[raw[7]], []).append(row)
        points: dict[str, list[PointRow]] = {}
        for point in self.point_rows(rows, whole=True):
            points.setdefault(point.meeting, []).append(point)
        history: dict[str, list[Change]] = {}
        sql = f"{OBJECT_HISTORY.format(table='meeting_log')} WHERE line IS NOT NULL{BY_ID}"
        for r in self.conn.execute(sql):
            if r[8] in owner:
                history.setdefault(owner[r[8]], []).append(_change(r))
        return [
            self.meeting_read(raw, row, linked.get(row.id, []), points.get(row.ref, []), history.get(row.id, []))
            for raw, row in zip(raws, rows, strict=True)
        ]


def _materials(text: str) -> list[MaterialRow]:
    return [MaterialRow(ref=m["ref"], label=m.get("label")) for m in json.loads(text)]


def _change(row: Sequence[Any]) -> Change:
    i, day, at, by, via, line, undone, kind, _ = row
    return Change(id=i, day=date.fromisoformat(day), at=None if at is None else datetime.fromisoformat(at), by=by,
                  via=via, line=line, undone_by=undone, kind=kind)  # fmt: skip


def every(
    conn: sqlite3.Connection, tz: ZoneInfo, at: datetime
) -> tuple[list[LedgerRead], list[LedgerRead], list[LedgerRead]]:
    """Every item, meeting and stream as show gives it, today the owner's day at `at` (DR-0659). It writes nothing."""
    return _Reader(conn, tz, at).every()


def _now_meeting(number: int | None) -> str:
    return f"now {meeting_text(number)}" if number is not None else "waiting for a new number"


# -- lines ------------------------------------------------------------------------------------------------------------


def lines(found: LedgerRead, tz: ZoneInfo) -> list[str]:
    """A read as the tool's text and the commands' output: its counts first, then a line per row."""
    if found.view == "show":
        return _show_lines(found, tz)
    today = found.today
    c = found.counts
    out: list[str] = []
    if found.view == "snapshot":
        out.append(
            f"{_open(c)}, {c.snoozed} snoozed, {c.overdue} overdue; {_n(c.meetings, 'meeting')}, "
            f"{_n(c.points_open, 'point')} open; {_n(c.streams, 'stream')}"
        )
        out += ["items", *(item_line(i, today, source=True) for i in found.items)]
        out.append(f"meetings {_window(found)}, or with an open point")
        out += _meetings_with_points(found)
        out += ["streams", *(stream_line(s, today) for s in found.streams)]
    elif found.view == "queue":
        verdicts = ", ".join(f"{n} {verdict}" for verdict, n in c.triage.items())
        out.append(f"{_open(c)}: {c.untriaged} untriaged" + (f", {verdicts}" if verdicts else ""))
        group: str | None = ""
        for item in found.items:
            if item.triage != group:
                group = item.triage
                out.append(group or "untriaged")
            out.append("  " + item_line(item, today))
    elif found.view == "list":
        median = c.median_days_to_done
        out.append(
            f"{_n(c.items, 'item')}: {_open(c)}, {c.snoozed} snoozed, {c.done} done, {c.dropped} dropped; "
            f"{c.overdue} overdue; chased {_n(c.chased_items, 'item')}, {_n(c.chased, 'time')}"
            + (f"; median {median:g} days to done" if median is not None else "")
        )
        if len(found.items) < c.items:
            out.append(f"the first {len(found.items)}")
        out += [item_line(i, today) for i in found.items]
    elif found.view == "meetings":
        out.append(f"{_n(c.meetings, 'meeting')}, {_n(c.points_open, 'point')} open; {_window(found)}")
        out += [meeting_line(m, today) for m in found.meetings]
    else:
        out += [meeting_line(m, today) for m in found.meetings]
        out.append(f"{c.items} to raise")
        out += [item_line(i, today) for i in found.items]
    return out


def _open(counts: Counts) -> str:
    """The open items, saying how many of them are snoozes whose day has come."""
    return f"{counts.open} open" + (f" ({_n(counts.woke, 'snooze')} ended)" if counts.woke else "")


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _date(day: date, today: date) -> str:
    """A day as a line says it: MM-DD in today's year, the whole date in another."""
    # By hand, not strftime: a snapshot says thousands.
    return f"{day.month:02d}-{day.day:02d}" if day.year == today.year else day.isoformat()


def _window(found: LedgerRead) -> str:
    # The views that say a window always read in one.
    since, until = cast(datetime, found.since), cast(datetime, found.until)
    midnight = since.timetz().replace(tzinfo=None) == until.timetz().replace(tzinfo=None) == datetime.min.time()
    if midnight:
        return f"{_date(since.date(), found.today)} to {_date((until - timedelta(days=1)).date(), found.today)}"
    return f"{since:%Y-%m-%d %H:%M} to {until:%Y-%m-%d %H:%M}"


def item_line(item: ItemRow, today: date, *, source: bool = False) -> str:
    """`A-0012 [reply] Ada: Send the dates (12d, due 10-21, 2d late) [pilot] @M-0003 <chase>`, with its source in a
    snapshot."""
    facts = [f"{item.days}d"]
    if item.due is not None:
        facts.append(f"due {_date(item.due, today)}")
    if item.late is not None:
        facts.append(f"{item.late}d late")
    if item.state == "snoozed" and item.until is not None:
        facts.append(f"{'snooze ended' if item.woke else 'snoozed to'} {_date(item.until, today)}")
    if item.state in CLOSED and item.closed is not None:
        facts.append(f"{item.state} {_date(item.closed, today)}")
    if item.archived:
        facts.append("archived")
    text = f"{item.ref} [{item.kind}] {item.who}: {item.title} ({', '.join(facts)}) [{item.stream}]"
    text += f" @{item.meeting}" if item.meeting else ""
    text += f" <{item.triage}>" if item.triage else ""
    text += f"; {item.why}" if item.why else ""
    return text + (f" src {item.source}" if source else "")


def meeting_line(meeting: MeetingRow, today: date) -> str:
    """`M-0040 10-20 10:00-10:30 Pilot sync, Ada Example; 2 open; items A-0012 A-0015`."""
    start, end = meeting.start, meeting.end
    until = f"{end:%H:%M}" if end.date() == start.date() else f"{_date(end.date(), today)} {end:%H:%M}"
    text = f"{meeting.ref} {_date(start.date(), today)} {start:%H:%M}-{until} {meeting.subject}"
    text += f", {meeting.organiser}" if meeting.organiser else ""
    text += f" [{meeting.stream}]" if meeting.stream else ""
    text += f" ({meeting.response})" if meeting.response in SAID else ""
    text += f"; {meeting.points_open} open" if meeting.points_open else ""
    text += f"; items {' '.join(meeting.items)}" if meeting.items else ""
    return text + ("; archived" if meeting.archived else "")


def point_line(point: PointRow) -> str:
    """`#2 open The pilot's dates -> A-0012 (proposed) (from M-0039) 01JAB3W8Q4X5M2Z7K9R0T6YVNC`, the answer in
    show."""
    text = f"#{point.n} {point.state} {point.text}"
    text += f" -> {point.item}" if point.item else ""
    text += " (proposed)" if point.proposed else ""
    text += f" (from {point.from_})" if point.from_ else ""
    text += f" (to {point.to})" if point.to else ""
    text += f"; answer: {' '.join(point.answer.split())}" if point.answer else ""
    return f"{text} {point.id}"


def stream_line(stream: StreamRow, today: date) -> str:
    """`pilot active; where: Waiting on the venue; next: Book the room; deadline 10-30`, todo.py's `where -> next`."""
    text = f"{stream.name} {stream.status}"
    text += f"; where: {stream.where}" if stream.where else ""
    text += f"; next: {stream.next}" if stream.next else ""
    return text + (f"; deadline {_date(stream.deadline, today)}" if stream.deadline else "")


def _points_by_meeting(found: LedgerRead) -> dict[str, list[PointRow]]:
    points: dict[str, list[PointRow]] = {}
    for point in found.points:
        points.setdefault(point.meeting, []).append(point)
    return points


def _meetings_with_points(found: LedgerRead) -> list[str]:
    points = _points_by_meeting(found)
    out: list[str] = []
    for meeting in found.meetings:
        out.append(meeting_line(meeting, found.today))
        out += [f"  {point_line(p)}" for p in points.get(meeting.ref, [])]
    return out


# -- the short form (DR-0720) ------------------------------------------------------------------------------------------


def context_lines(found: LedgerRead) -> list[str]:
    """A snapshot in the sync folder's short form, a line a row, its fields split by ` | `: each open item, a snooze
    whose day has come among them, `A-0012 | kind | who | title | source @M-0003`; the snoozed with their day; each
    meeting with its standing points by id; the streams. A field's own `|` reads `/`, so a line splits as it
    reads."""
    today = found.today
    awake = [i for i in found.items if i.state == "open" or i.woke]
    asleep = [i for i in found.items if i.state == "snoozed" and not i.woke]
    out = [f"open items ({len(awake)})", *(short_item(i) for i in awake)]
    out.append(f"snoozed ({len(asleep)})")
    out += [short_item(i) + (f" | until {_date(i.until, today)}" if i.until else "") for i in asleep]
    points = _points_by_meeting(found)
    out.append(f"meetings {_window(found)}, or with an open point ({len(found.meetings)})")
    for m in found.meetings:
        out.append(_short_meeting(m, today))
        out += [
            f"  #{p.n} {p.state} | {as_field(p.text)}" + (f" -> {p.item}" if p.item else "") + f" | {p.id}"
            for p in points.get(m.ref, [])
        ]
    out.append(f"streams ({len(found.streams)})")
    for s in found.streams:
        text = f"{s.name} | {s.status} | {as_field(s.where or '-')} | {as_field(s.next or '-')}"
        out.append(text + (f" | deadline {_date(s.deadline, today)}" if s.deadline else ""))
    return out


def as_field(text: str) -> str:
    """Text as one field of a short-form line: on one line, each `|` read `/`, so no bar, inside or at an end, moves
    into the next field."""
    return " ".join(text.split()).replace("|", "/")


def short_item(item: ItemRow) -> str:
    """`A-0012 | reply | Ada Example | Send the dates | chat:C0X/1 @M-0003`."""
    text = f"{item.ref} | {item.kind} | {as_field(item.who)} | {as_field(item.title)} | {as_field(item.source)}"
    return text + (f" @{item.meeting}" if item.meeting else "")


def _short_meeting(meeting: MeetingRow, today: date) -> str:
    """`M-0040 | 10-20 10:00-10:30 | Pilot sync | Ada Example`, then `stream pilot` and a response that changes
    whether the owner goes, each a field of its own when there is one."""
    start, end = meeting.start, meeting.end
    until = f"{end:%H:%M}" if end.date() == start.date() else f"{_date(end.date(), today)} {end:%H:%M}"
    fields = [meeting.ref, f"{_date(start.date(), today)} {start:%H:%M}-{until}", as_field(meeting.subject),
              as_field(meeting.organiser or "-")]  # fmt: skip
    fields += [f"stream {meeting.stream}"] if meeting.stream else []
    fields += [meeting.response] if meeting.response is not None and meeting.response in SAID else []
    return " | ".join(fields)


def _rows(rows: Iterable[tuple[str, str]]) -> list[str]:
    """Labelled rows, a value's further lines under its first."""
    out: list[str] = []
    for label, value in rows:
        first, *rest = value.split("\n")
        out += [f"{label:<12} {first}", *(f"{'':<12} {row}" for row in rest)]
    return out


def _history(history: Sequence[Change], tz: ZoneInfo) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for n, change in enumerate(history):
        when = f"{change.day} {change.at.astimezone(tz):%H:%M}" if change.at is not None else f"{change.day}      "
        undone = f"  (undone by {change.undone_by})" if change.undone_by else ""
        out.append(("history" if n == 0 else "", f"{when}  {change.by} via {change.via}  {change.line}{undone}"))
    return out


def _show_lines(found: LedgerRead, tz: ZoneInfo) -> list[str]:
    # Show always gives its object's detail.
    detail = cast(Detail, found.detail)
    today = found.today
    rows: list[tuple[str, str]] = []
    if detail.kind == "item":
        item = found.items[0]
        head = f"{number_text(item.number) if item.number is not None else 'no number'}  {item.title}"
        rows += [("id", item.id), ("kind", item.kind), ("state", _item_state(item))]
        if detail.claimed is not None:
            claimed = number_text(detail.claimed)
            rows.append(("number", f"{claimed} went to an earlier claim; the next write gives a new one"))
        rows += [("who", item.who), ("stream", item.stream), ("source", item.source)]
        rows += [("meeting", item.meeting)] if item.meeting else []
        rows.append(("opened", f"{item.opened}, by {detail.added_by}"))
        rows += [("due", str(item.due))] if item.due is not None else []
        rows += [(name, text) for name in ("description", "notes", "outcome") if (text := getattr(detail, name))]
        rows += [("material", m.ref + (f"  {m.label}" if m.label else "")) for m in detail.materials]
        rows += [("also held", also) for also in detail.also]
        span = f"open {_n(item.days, 'day')}" if item.state in OPEN else f"{item.state} after {_n(item.days, 'day')}"
        rows.append((
            "counts",
            f"{span}; chased {_n(detail.chased, 'time')}, snoozed {_n(detail.snoozes, 'time')}, due moved "
            f"{_n(detail.due_moves, 'time')}, reopened {_n(detail.reopens, 'time')}",
        ))  # fmt: skip
    elif detail.kind == "meeting":
        meeting = found.meetings[0]
        head = f"{meeting_text(meeting.number) if meeting.number is not None else 'no number'}  {meeting.subject}"
        start, end = meeting.start, meeting.end
        until = f"{end:%H:%M}" if end.date() == start.date() else f"{end:%Y-%m-%d %H:%M}"
        rows += [("id", meeting.id), ("when", f"{start:%Y-%m-%d %H:%M}-{until}")]
        rows += [(name, value) for name, value in (
            ("organiser", meeting.organiser), ("attendees", ", ".join(detail.attendees)), ("where", detail.where),
            ("response", meeting.response), ("stream", meeting.stream), ("series", detail.series),
            ("event", detail.event), ("notes", detail.notes), ("outcome", detail.outcome),
        ) if value]  # fmt: skip
        rows += [("material", m.ref + (f"  {m.label}" if m.label else "")) for m in detail.materials]
        rows += [("state", "archived")] if meeting.archived else []
        rows += [("also held", also) for also in detail.also]
        rows += [("point", point_line(p)) for p in found.points]
        rows += [("item", item_line(i, today)) for i in found.items]
    else:
        stream = found.streams[0]
        c = found.counts
        head = f"{stream.name}  {stream.status}"
        rows += [("id", stream.id)]
        rows += [(name, value) for name, value in (("where", stream.where), ("next", stream.next)) if value]
        rows += [("deadline", str(stream.deadline))] if stream.deadline else []
        rows.append(("updated", f"{stream.updated}, added by {detail.added_by}"))
        rows.append(("items", f"{_open(c)}, {c.snoozed} snoozed, {c.done} done, {c.dropped} dropped"))
        rows += [("item", item_line(i, today)) for i in found.items]
    return [head, *_rows([*rows, *_history(detail.history, tz)])]


def _item_state(item: ItemRow) -> str:
    if item.state == "open":
        return f"open, triage {item.triage}" if item.triage else "open"
    if item.state == "snoozed":
        return f"snooze ended on {item.until}, open again" if item.woke else f"snoozed until {item.until}"
    return f"{item.state} on {item.closed}" + (", archived" if item.archived else "")
