"""The ledger in the index (DR-0616, DR-0628): each item, meeting and stream as its ops leave it, every op with its log
line, each number's claimants, and the op files read, so a write and `docket ledger show` ask the index instead of
folding every op.

| Table | What |
|---|---|
| `items` | each item whose add stands: its fields, its meeting, state, number (null while a clash waits), last op |
| `item_log` | every item's op line, by item and id: its keys, text, log line, fate, and the undo that left it out |
| `item_numbers` | each number and every item that claimed it, by its first claim; an undone add's item is hidden |
| `meetings` | each meeting that stands: its number, fields, start in UTC, day, materials, archived, adds, by, last op |
| `points` | each point of a meeting that stands, idle copies too: its fields, n while it stands, where its ops go |
| `meeting_log` | every op line of a meeting, its points' too, as `item_log` has them, with the object, key and link |
| `meeting_numbers` | each M- number and the meetings that claimed or held it; one only an undone add holds is hidden |
| `streams`, `stream_log` | each stream that stands, with its updated day, and every line of each, with its key |
| `ledger_files` | each op file read: its size, time (0 while racily clean), sha256, the highest op id a line names |

`item_log` also keeps a later Docket's object's lines, fate `later`.

`rebuild` folds every op file through `ledger.load`. `update` follows a file that only grew, its old bytes the same up
to a line's end: it reads the lines after them and folds again, from the tables' rows, the items they name and the
meetings and streams they touch, then gives each number those claim, now or before, to its first claimant again, so a
clash or an undone add moves a number as a rebuild would. Anything else (a file changed, gone, or no longer UTF-8)
folds every file again. Nothing here is a door: the tables are rebuilt from the op files, and a write that can't have
them folds the files in memory (DR-0619).

A meeting's fold reads other meetings' (DR-0623): a copy stands while its source's roll names it, and a roll lands while
its copy or meeting stands. What an op touches is its part: the adds of one key, the meeting a continues names and every
add continuing it, the meetings a point's from or a roll's to names and every meeting naming one of theirs, followed
until nothing new comes. Links are set by lines alone, so a part only ever grows; `part` finds it from the rows, and an
update and a write fold only the parts their lines touch (DR-0633).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from operator import attrgetter
from pathlib import Path
from typing import Any, NamedTuple

from docket.errors import DocketError
from docket.ledger import ops
from docket.ledger.fold import NUMBER, Entry, Folded, Item, Material, fold_item, load, made, number_text, paused
from docket.ledger.meetings import Meeting, ObjectFold, Objects, Point, Stream, fold_objects
from docket.ledger.ops import Line, Op

# A line as its log row says it: its log line, the undo that left it out, its fate.
Fate = tuple[Line, str | None, str | None, str]

TABLES = (
    "items", "item_log", "item_numbers", "meetings", "points", "meeting_log", "meeting_numbers", "streams",
    "stream_log", "ledger_files",
)  # fmt: skip
ITEM_COLUMNS = (
    "id", "number", "claimed", "kind", "title", "who", "stream", "source", "meeting", "opened", "due", "description",
    "notes", "outcome", "state", "until", "closed", "triage", "archived", "materials", "added_by", "last_op",
)  # fmt: skip
LOG_COLUMNS = ("item", "id", "version", "after", "day", "at", "by", "via", "kind", "batch", "keys", "line", "undone_by",
               "fate")  # fmt: skip
MEETING_COLUMNS = (
    "id", "number", "claimed", "event", "series", "start", "end", "utc", "day", "subject", "organiser", "where",
    "response", "attendees", "stream", "notes", "outcome", "materials", "archived", "adds", "added_by", "last_op",
)  # fmt: skip
POINT_COLUMNS = ("id", "meeting", "n", "text", "state", "answer", "item", "from", "to", "proposed", "moved_to", "day",
                 "by")  # fmt: skip
STREAM_COLUMNS = ("id", "name", "status", "where", "next", "deadline", "updated", "adds", "added_by", "last_op")
NEWLINE = 0x0A
# Lines as the fold orders them: by id, and one id's texts by their text.
TEXT_ORDER = attrgetter("op.id", "text")
# The fate of a later Docket's object's line, which folds into nothing.
LATER = "later"
# Git's rule for a racily clean file: a file written this close to its read could be written again within the same
# tick of the file clock, keeping its size and time. Its time is stored as 0, so the next update hashes it again.
RACY_NS = 2_000_000_000
# How many values one `IN (...)` takes: SQLite before 3.32 allows 999 host parameters to a statement.
CHUNK = 500
# The ops a meeting's line links to another's by: a continued meeting, a copy's source, a roll's copy (DR-0633).
LINKS = {"meeting": "continues", "point": "from", "point_set": "to"}


def _insert(table: str, columns: Sequence[str], verb: str = "INSERT") -> str:
    # Table and column names are the module's own, never a user's; quoted, since `end` and `where` are SQL's words.
    names = ", ".join(f'"{c}"' for c in columns)
    return f"{verb} INTO {table} ({names}) VALUES ({', '.join('?' * len(columns))})"


INSERT_ITEM = _insert("items", ITEM_COLUMNS)
INSERT_LOG = _insert("item_log", LOG_COLUMNS)
INSERT_NUMBER = _insert("item_numbers", ("number", "item", "claim", "hidden"))
INSERT_MEETING = _insert("meetings", MEETING_COLUMNS)
INSERT_POINT = _insert("points", POINT_COLUMNS)
INSERT_MEETING_LOG = _insert("meeting_log", (*LOG_COLUMNS[:3], "meeting", *LOG_COLUMNS[3:], "key", "link"))
INSERT_MEETING_NUMBER = _insert("meeting_numbers", ("number", "meeting", "claim", "held", "hidden"))
INSERT_STREAM = _insert("streams", STREAM_COLUMNS)
INSERT_STREAM_LOG = _insert("stream_log", (*LOG_COLUMNS[:3], "stream", *LOG_COLUMNS[3:], "key"))
INSERT_FILE = _insert("ledger_files", ("path", "size", "mtime_ns", "sha256", "highest"), "INSERT OR REPLACE")
# Each number given to the item whose claim of it sorts first among the items that stand, as the fold gives it.
ASSIGN = (
    "UPDATE items SET number = CASE WHEN id = (SELECT item FROM item_numbers WHERE number = ? AND hidden = 0 "
    "ORDER BY claim, item LIMIT 1) THEN claimed END WHERE claimed = ?"
)
# And each M- number to the meeting whose claim sorts first; a number a meeting's other add held never wins (DR-0621).
ASSIGN_MEETING = (
    "UPDATE meetings SET number = CASE WHEN id = (SELECT meeting FROM meeting_numbers WHERE number = ? AND hidden = 0 "
    "AND claim IS NOT NULL ORDER BY claim, meeting LIMIT 1) THEN claimed END WHERE claimed = ?"
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def settled(mtime_ns: int, read_ns: int) -> int:
    """A file's time as it is stored: 0 while it is too close to the read for a rewrite to change it."""
    return 0 if read_ns - mtime_ns < RACY_NS else mtime_ns


def _iso(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


# One encoder for the rows' JSON, made once: `json.dumps` with these settings makes a new one on every call.
_json = json.JSONEncoder(ensure_ascii=False, separators=(",", ":")).encode


def _names(values: Sequence[str]) -> str:
    return _json(list(values)) if values else "[]"


def _ids(values: Sequence[str]) -> str:
    """Op ids as a JSON list: ULIDs, which need no escaping."""
    return '["' + '","'.join(values) + '"]' if values else "[]"


def _materials(materials: Sequence[Material]) -> str:
    if not materials:
        return "[]"
    return _json([{"ref": m.ref} | ({"label": m.label} if m.label is not None else {}) for m in materials])


def utc_text(moment: datetime) -> str:
    """A meeting's start in UTC, as `meetings.utc` keeps it, so text order is time order."""
    return f"{moment.astimezone(UTC):%Y-%m-%dT%H:%MZ}"


def _line(keys: str) -> Line:
    """A line from its log row: the op as its text reads, in the file it belongs in."""
    op = ops.read_op(keys)
    return Line(op, keys, ops.rel_of(op), 0)


def _chunks(values: Iterable[str]) -> Iterator[list[str]]:
    ordered = sorted(set(values))
    for k in range(0, len(ordered), CHUNK):
        yield ordered[k : k + CHUNK]


def _select(conn: sqlite3.Connection, sql: str, values: Iterable[str]) -> list[tuple[Any, ...]]:
    """The rows `sql` gives for every value, `{marks}` standing for the `IN` list, a chunk at a time."""
    out: list[tuple[Any, ...]] = []
    for chunk in _chunks(values):
        out += conn.execute(sql.format(marks=", ".join("?" * len(chunk))), chunk).fetchall()
    return out


def _delete(conn: sqlite3.Connection, table: str, column: str, values: Iterable[str]) -> None:
    # The module's own table and column names, never a user's.
    for chunk in _chunks(values):
        conn.execute(f"DELETE FROM {table} WHERE {column} IN ({', '.join('?' * len(chunk))})", chunk)  # noqa: S608


def held_lines(conn: sqlite3.Connection, item_id: str) -> list[Line]:
    """Every line of an item the tables hold, in id order, each id's texts in text order."""
    sql = "SELECT keys FROM item_log WHERE item = ? ORDER BY id, version"
    return [_line(keys) for (keys,) in conn.execute(sql, (item_id,))]


def object_lines(conn: sqlite3.Connection, adds: Iterable[str]) -> dict[str, list[Line]]:
    """Every line naming each meeting's or stream's add, as the tables hold them, in id order."""
    out: dict[str, list[Line]] = {}
    for table in ("meeting_log", "stream_log"):
        sql = f"SELECT item, keys FROM {table} WHERE item IN ({{marks}}) ORDER BY item, id, version"  # noqa: S608
        for add_id, keys in _select(conn, sql, adds):
            out.setdefault(add_id, []).append(_line(keys))
    return out


def links(op: Op) -> tuple[str | None, str | None]:
    """The key an object's own op carries, a meeting's event or a stream's name, and the op it links to, a continues,
    a from or a to: what ties a meeting to others (DR-0633)."""
    fields = op.fields
    if not fields:
        return None, None
    kind = op.op
    key = None
    if kind in ("meeting", "stream") and op.id == op.item:
        value = fields.get("event" if kind == "meeting" else "name")
        key = value if isinstance(value, str) and value else None
    name = LINKS.get(kind)
    link = fields.get(name) if name is not None else None
    return key, link if isinstance(link, str) else None


def part(
    conn: sqlite3.Connection, seeds: Iterable[str], fresh: Iterable[Line] = (), known: Collection[str] = ()
) -> set[str]:
    """The adds of every meeting and stream sharing a part with the seeds (DR-0633), found from the tables' rows and
    the fresh lines not in them yet: the adds of one key, the ops a link names, and the rows linking to any op of the
    part, again until nothing new comes. Adds in `known` are a part's already, and not followed."""
    done = set(known)
    meeting_keys: set[str] = set()
    stream_keys: set[str] = set()
    targets: set[str] = set()
    named: set[str] = set()
    for line in fresh:
        key, link = links(line.op)
        if key is not None:
            (stream_keys if line.op.op == "stream" else meeting_keys).add(key)
        if link is not None:
            targets.add(link)
        named.add(line.op.id)
    found: set[str] = set()
    todo = set(seeds) - done
    # Each key, op and link asked once, by the query that asks it; a stream's name and a meeting's event never meet.
    asked: tuple[set[str], set[str], set[str], set[str]] = (set(), set(), set(), set())
    while todo or meeting_keys or stream_keys or targets or named:
        found |= todo
        done |= todo
        rows = _select(
            conn,
            "SELECT id, kind, key, link FROM meeting_log WHERE item IN ({marks}) "
            "AND (key IS NOT NULL OR link IS NOT NULL OR kind = 'point')",
            todo,
        )
        for op_id, kind, key, link in rows:
            if key is not None:
                meeting_keys.add(key)
            if link is not None:
                targets.add(link)
            if kind == "point":
                named.add(op_id)
        stream_keys.update(k for (k,) in _select(conn, "SELECT key FROM stream_log WHERE item IN ({marks}) "
                                                       "AND key IS NOT NULL", todo))  # fmt: skip
        named |= todo
        new: set[str] = set()
        for sql, values, before in zip(
            (
                "SELECT item FROM meeting_log WHERE key IN ({marks})",
                "SELECT item FROM stream_log WHERE key IN ({marks})",
                "SELECT item FROM meeting_log WHERE id IN ({marks})",
                "SELECT item FROM meeting_log WHERE link IN ({marks})",
            ),
            (meeting_keys, stream_keys, targets, named),
            asked,
            strict=True,
        ):
            new.update(item for (item,) in _select(conn, sql, values - before))
            before.update(values)
        meeting_keys, stream_keys, targets, named = set(), set(), set(), set()
        todo = new - done
    return found


class _Rows:
    """The rows a fold gives, gathered for one insert per table. A rebuild makes 70,000 log rows in one go, so the
    loop over them does as little as it can."""

    def __init__(self) -> None:
        self.items: list[tuple[Any, ...]] = []
        self.log: list[tuple[Any, ...]] = []
        self.numbers: list[tuple[Any, ...]] = []
        self.meetings: list[tuple[Any, ...]] = []
        self.points: list[tuple[Any, ...]] = []
        self.meeting_log: list[tuple[Any, ...]] = []
        self.meeting_numbers: list[tuple[Any, ...]] = []
        self.streams: list[tuple[Any, ...]] = []
        self.stream_log: list[tuple[Any, ...]] = []
        self.days: dict[date, str] = {}

    def later(self, item_id: str, lines: Iterable[Line]) -> None:
        """The rows of a later Docket's object, each with fate `later` and no log line."""
        days, append = self.days, self.log.append
        previous, version = "", 0
        for line in sorted(lines, key=TEXT_ORDER):
            op = line.op
            version = version + 1 if op.id == previous else 0
            previous = op.id
            day = days.get(op.day) or days.setdefault(op.day, op.day.isoformat())
            when = None if op.at is None else op.at.isoformat()
            append((item_id, op.id, version, op.after, day, when, op.by, op.via, op.op, op.batch, line.text, None,
                    None, LATER))  # fmt: skip

    def add(self, item_id: str, lines: Sequence[Line], folded: Folded, number: int | None) -> None:
        """An item's rows: every line it has, the item when its add stands, holding `number`, and its claims."""
        item = folded.item
        fates = _fates(lines, folded.lines, item.log if item is not None else None, folded.undone)
        self._log(self.log.append, item_id, None, fates)
        first: dict[int, str] = {}
        for op_id, claimed in folded.claims:
            first[claimed] = min(first.get(claimed, op_id), op_id)
        self.numbers += [(n, item_id, op_id, int(item is None)) for n, op_id in first.items()]
        if item is not None:
            self.items.append(_item_row(item, folded.claims[-1][1], number))

    def _log(self, append: Any, item_id: str, obj: str | None, fates: Iterable[Fate]) -> None:
        """The log rows of one add's lines, as `_fates` gives them; with `obj`, a meeting's or a stream's rows, with
        the object, the key and the link."""
        days = self.days
        previous, version = "", 0
        for line, words, undone, fate in fates:
            op = line.op
            op_id = op.id
            version = version + 1 if op_id == previous else 0
            previous = op_id
            day = days.get(op.day) or days.setdefault(op.day, op.day.isoformat())
            when = None if op.at is None else op.at.isoformat()
            if obj is None:
                append((item_id, op_id, version, op.after, day, when, op.by, op.via, op.op, op.batch, line.text, words,
                        undone, fate))  # fmt: skip
            else:
                key, link = links(op)
                append((item_id, op_id, version, obj, op.after, day, when, op.by, op.via, op.op, op.batch, line.text,
                        words, undone, fate, key, link))  # fmt: skip

    def objects(self, objects: Objects, lines: Mapping[str, Sequence[Line]], *, numbered: bool) -> None:
        """The rows of every meeting and stream the fold made, `lines` holding every line naming each add of them.
        With `numbered` the fold saw every meeting and its numbers are kept; else they are left for `ASSIGN_MEETING`."""
        points: dict[str, list[Point]] = {}
        for point in objects.points.values():
            points.setdefault(point.meeting, []).append(point)
        streams = self.stream_log

        def stream_append(row: tuple[Any, ...]) -> None:
            # A stream's ops link to nothing, so its rows keep no link.
            streams.append(row[:-1])

        for obj_id, folded in objects.folds.items():
            value = folded.value
            log = value.log if value is not None else None
            if folded.kind == "stream":
                alone = len(folded.adds) == 1
                for add_id in folded.adds:
                    fates = _fates(lines[add_id], folded.lines, log, folded.undone, alone=alone)
                    self._log(stream_append, add_id, obj_id, fates)
                if isinstance(value, Stream):
                    self.streams.append(_stream_row(value))
                continue
            alone = len(folded.adds) == 1
            for add_id in folded.adds:
                fates = _fates(lines[add_id], folded.lines, log, folded.undone, alone=alone)
                self._log(self.meeting_log.append, add_id, obj_id, fates)
            self.meeting_numbers += _number_rows(folded)
            if not isinstance(value, Meeting):
                continue
            self.meetings.append(_meeting_row(value, folded.claims[-1][1], value.number if numbered else None))
            places = {p.id: n for n, p in enumerate(value.points, start=1)}
            self.points += [
                (p.id, p.meeting, places.get(p.id), p.text, p.state, p.answer, p.item, p.rolled_from, p.rolled_to,
                 int(p.proposed), p.moved_to, p.day.isoformat(), p.added_by)
                for p in points.get(obj_id, [])
            ]  # fmt: skip

    def write(self, conn: sqlite3.Connection) -> None:
        for sql, rows in (
            (INSERT_LOG, self.log), (INSERT_NUMBER, self.numbers), (INSERT_ITEM, self.items),
            (INSERT_MEETING_LOG, self.meeting_log), (INSERT_MEETING_NUMBER, self.meeting_numbers),
            (INSERT_MEETING, self.meetings), (INSERT_POINT, self.points), (INSERT_STREAM_LOG, self.stream_log),
            (INSERT_STREAM, self.streams),
        ):  # fmt: skip
            if rows:
                conn.executemany(sql, rows)


def _fates(
    lines: Sequence[Line],
    kept: Sequence[Line],
    log: Sequence[Entry] | None,
    undone_of: Mapping[str, str],
    *,
    alone: bool = True,
) -> list[Fate]:
    """One add's lines in the fold's order, each with its log line, the undo that left it out and its fate: `applied`,
    `idle` or `undone` with its log entry while its object stands, `hidden` or `undone` when it doesn't, `left out`
    when the fold left it out. Most adds' lines are all kept and alone in their object, in the fold's order already,
    each beside its entry: those are read straight off the log (#218). `alone` says the object has this add only."""
    if alone and log is not None and len(lines) == len(kept):
        return [
            (line, e.line, e.undone_by, "undone" if e.undone_by else "applied" if e.applied else "idle")
            for line, e in zip(kept, log, strict=True)
        ]
    entries: dict[int, Entry | None] = (
        dict.fromkeys(map(id, kept)) if log is None else {id(line): e for line, e in zip(kept, log, strict=True)}
    )
    out: list[Fate] = []
    for line in sorted(lines, key=TEXT_ORDER):
        if id(line) not in entries:
            out.append((line, None, None, "left out"))
        elif (e := entries[id(line)]) is not None:
            out.append((line, e.line, e.undone_by, "undone" if e.undone_by else "applied" if e.applied else "idle"))
        else:
            undone = undone_of.get(line.op.id)
            out.append((line, None, undone, "undone" if undone else "hidden"))
    return out


def _item_row(item: Item, claimed: int, number: int | None) -> tuple[Any, ...]:
    return (
        item.id, number, claimed, item.kind, item.title, item.who, item.stream, item.source, item.meeting,
        item.opened.isoformat(), _iso(item.due), item.description, item.notes, item.outcome, item.state,
        _iso(item.until), _iso(item.closed), item.triage, int(item.archived), _materials(item.materials),
        item.added_by, item.last,
    )  # fmt: skip


def _meeting_row(meeting: Meeting, claimed: int, number: int | None) -> tuple[Any, ...]:
    return (
        meeting.id, number, claimed, meeting.event, meeting.series, meeting.start.isoformat(timespec="minutes"),
        meeting.end.isoformat(timespec="minutes"), utc_text(meeting.start), meeting.day.isoformat(), meeting.subject,
        meeting.organiser, meeting.where, meeting.response, _names(meeting.attendees), meeting.stream,
        meeting.notes, meeting.outcome, _materials(meeting.materials), int(meeting.archived), _ids(meeting.adds),
        meeting.added_by, meeting.last,
    )  # fmt: skip


def _stream_row(stream: Stream) -> tuple[Any, ...]:
    return (
        stream.id, stream.name, stream.status, stream.where, stream.next, _iso(stream.deadline),
        stream.updated.isoformat(), _ids(stream.adds), stream.added_by, stream.last,
    )  # fmt: skip


def _number_rows(folded: ObjectFold) -> list[tuple[Any, ...]]:
    """A meeting's M- numbers: each one it claims, by its first claim, each one its other adds held, and, hidden, each
    one only an undone add or a hidden meeting carries, so none is given out twice."""
    claims: dict[int, str] = {}
    held: dict[int, str] = {}
    if folded.value is not None:
        for op_id, number in folded.claims:
            claims[number] = min(claims.get(number, op_id), op_id)
        for op_id, number in folded.held:
            held[number] = min(held.get(number, op_id), op_id)
    return [
        (n, folded.id, claims.get(n), held.get(n), int(n not in claims and n not in held))
        for n in sorted({*folded.numbers, *claims, *held})
    ]


# -- building -----------------------------------------------------------------------------------------------------


def rebuild(conn: sqlite3.Connection, root: Path) -> int:
    """Every op file folded into the tables, replacing what they held, inside the caller's transaction; how many op
    lines it read. The collector stays paused from the read to the last row (`ledger.load`), and the fold is let go
    before it runs again: its first pass would walk every object the fold made, still young, for nothing."""
    files: list[ops.FileRead] = []
    # Taken before any file is read, so a file counts as racily clean for at least as long as it can be.
    read_ns = time.time_ns()
    with paused():
        ledger = load(root, files)
        for table in TABLES:
            conn.execute(f"DELETE FROM {table}")  # noqa: S608 - the module's own table names
        conn.executemany(
            INSERT_FILE, [(f.rel, f.size, settled(f.mtime_ns, read_ns), f.sha256, f.highest) for f in files]
        )
        rows = _Rows()
        for item_id, folded in ledger.folds.items():
            item = folded.item
            rows.add(item_id, ledger.lines[item_id], folded, None if item is None else item.number)
        owner = ledger.objects.owner
        for item_id, group in ledger.lines.items():
            if item_id not in ledger.folds and item_id not in owner:
                rows.later(item_id, group)
        rows.objects(ledger.objects, ledger.lines, numbered=True)
        rows.write(conn)
        del ledger, rows
    return sum(f.lines for f in files)


def _grew(data: bytes, size: int, sha: str) -> bool:
    """Whether a file still starts with the bytes it was read with, ending at a line's end, so only lines came after."""
    return len(data) >= size and (size == 0 or data[size - 1] == NEWLINE) and _sha(data[:size]) == sha


def update(conn: sqlite3.Connection, root: Path) -> int:
    """The tables brought up to date with the op files, inside the caller's transaction: the lines each file gained
    since it was read folded into the items they name and the meetings and streams they touch; anything else folds
    every file again. How many op lines it read. A file stored with time 0 (`RACY_NS`) is hashed again, so a rewrite
    that kept its size and time is seen."""
    read_ns = time.time_ns()
    stored: dict[str, tuple[int, int, str, str | None]] = {
        path: (size, mtime, sha, highest)
        for path, size, mtime, sha, highest in conn.execute(
            "SELECT path, size, mtime_ns, sha256, highest FROM ledger_files"
        )
    }
    listed, _ = ops.listing(root)
    if set(stored) - {rel for rel, _ in listed}:
        return rebuild(conn, root)
    fresh: list[Line] = []
    for rel, path in listed:
        st = path.stat()
        old = stored.get(rel)
        if old is not None and (st.st_size, st.st_mtime_ns) == old[:2]:
            continue
        data = path.read_bytes()
        start = 0 if old is None else old[0]
        if old is not None and not _grew(data, start, old[2]):
            return rebuild(conn, root)
        try:
            text = data[start:].decode("utf-8")
        except UnicodeDecodeError:
            if old is not None:
                return rebuild(conn, root)
            # Left out, as every read leaves out a file that isn't UTF-8; the next update reads it again.
            continue
        found = ops.parse(text, rel)
        highest = max((i for i in (old[3] if old else None, found.highest) if i is not None), default=None)
        conn.execute(INSERT_FILE, (rel, len(data), settled(st.st_mtime_ns, read_ns), _sha(data), highest))
        fresh += found.lines
    if fresh:
        with paused():
            refold(conn, fresh)
    return len(fresh)


def _merged(held: Iterable[Line], fresh: Iterable[Line]) -> list[Line]:
    """Lines read twice, as a union merge can leave them, as one op; one id on two texts keeps both (DR-0612)."""
    lines = {(line.op.id, line.text): line for line in held}
    for line in fresh:
        lines.setdefault((line.op.id, line.text), line)
    return list(lines.values())


def _in_objects(conn: sqlite3.Connection, add_id: str) -> bool:
    sql = "SELECT 1 FROM meeting_log WHERE item = ? UNION ALL SELECT 1 FROM stream_log WHERE item = ? LIMIT 1"
    return conn.execute(sql, (add_id, add_id)).fetchone() is not None


def refold(conn: sqlite3.Connection, fresh: Iterable[Line]) -> None:
    """Fold again, from their rows and the new lines, the items the new lines name and the parts of the meetings and
    streams they touch (DR-0633); then give each number those claim, or claimed before, to its first claimant, as the
    fold of the whole ledger gives it."""
    by_item: dict[str, list[Line]] = {}
    for line in fresh:
        by_item.setdefault(line.op.item, []).append(line)
    numbers: set[int] = set()
    rows = _Rows()
    seeds: dict[str, list[Line]] = {}
    for item_id in sorted(by_item):
        lines = _merged(held_lines(conn, item_id), by_item[item_id])
        if _in_objects(conn, item_id) or made(item_id, lines) in ("meeting", "stream"):
            seeds[item_id] = lines
            continue
        numbers |= _refold_item(conn, rows, item_id, lines)
    meeting_numbers: set[int] = set()
    if seeds:
        meeting_numbers = _refold_objects(conn, rows, seeds, numbers)
    rows.write(conn)
    conn.executemany(ASSIGN, [(n, n) for n in sorted(numbers)])
    conn.executemany(ASSIGN_MEETING, [(n, n) for n in sorted(meeting_numbers)])


def _drop_item(conn: sqlite3.Connection, item_id: str) -> set[int]:
    """An id's rows as an item gone: the numbers it claimed, to give again."""
    numbers = {n for (n,) in conn.execute("SELECT number FROM item_numbers WHERE item = ?", (item_id,))}
    for table, key in (("items", "id"), ("item_log", "item"), ("item_numbers", "item")):
        conn.execute(f"DELETE FROM {table} WHERE {key} = ?", (item_id,))  # noqa: S608 - the module's own names
    return numbers


def _refold_item(conn: sqlite3.Connection, rows: _Rows, item_id: str, lines: list[Line]) -> set[int]:
    """An item, or a later Docket's object, folded again from all its lines: the numbers it claims and claimed."""
    numbers = _drop_item(conn, item_id)
    if made(item_id, lines) == LATER:
        rows.later(item_id, lines)
        return numbers
    folded = fold_item(item_id, lines)
    numbers.update(n for _, n in folded.claims)
    rows.add(item_id, lines, folded, None)
    return numbers


def _refold_objects(
    conn: sqlite3.Connection, rows: _Rows, seeds: dict[str, list[Line]], item_numbers: set[int]
) -> set[int]:
    """The parts the seeds' lines touch folded again, from their rows and the seeds' lines, replacing the rows of the
    meetings and streams they held before; the M- numbers to give again. A seed's lines may have sat in `item_log`,
    read before the add that makes its meeting; one whose maker now makes an item goes back there."""
    fresh = [line for lines in seeds.values() for line in lines]
    # A seed's own rows are followed as lines too: a stream's rows keep no link, so a stream's add that a second text
    # turns into a meeting (DR-0612) would hide the links of the points its rows held.
    held = object_lines(conn, seeds)
    adds = part(conn, seeds, [*fresh, *(line for lines in held.values() for line in lines)])
    groups = object_lines(conn, adds)
    for add_id, lines in seeds.items():
        groups[add_id] = _merged(groups.get(add_id, []), lines)
    old_meetings = {m for (m,) in _select(conn, "SELECT DISTINCT meeting FROM meeting_log WHERE item IN ({marks})",
                                          adds)}  # fmt: skip
    old_streams = {s for (s,) in _select(conn, "SELECT DISTINCT stream FROM stream_log WHERE item IN ({marks})", adds)}
    numbers = {n for (n,) in _select(conn, "SELECT number FROM meeting_numbers WHERE meeting IN ({marks})",
                                     old_meetings)}  # fmt: skip
    for table, column, values in (
        ("meeting_log", "item", adds), ("stream_log", "item", adds), ("meetings", "id", old_meetings),
        ("points", "meeting", old_meetings), ("meeting_numbers", "meeting", old_meetings),
        ("streams", "id", old_streams),
    ):  # fmt: skip
        _delete(conn, table, column, values)
    for add_id in sorted(seeds):
        if made(add_id, groups[add_id]) in ("meeting", "stream"):
            item_numbers |= _drop_item(conn, add_id)
        else:
            item_numbers |= _refold_item(conn, rows, add_id, groups.pop(add_id))
    objects = fold_objects(groups)
    rows.objects(objects, groups, numbered=False)
    for folded in objects.folds.values():
        numbers |= folded.numbers
    return numbers


# -- asking -------------------------------------------------------------------------------------------------------


class Light(NamedTuple):
    """A meeting as the next-in-series rule reads it (DR-0624), from its row."""

    id: str
    start: datetime
    series: str | None
    subject: str
    organiser: str | None


class Due(NamedTuple):
    """A meeting with an open point as a rollover reads it (DR-0624), from its row."""

    id: str
    start: datetime
    end: datetime
    outcome: str | None
    response: str | None


class Held:
    """The ledger as the tables hold it, asked the way a write asks (DR-0616, DR-0628)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def highest_id(self) -> str | None:
        """The highest op id any line names, one this Docket can't read included: the clock mints above it."""
        return self.conn.execute("SELECT max(highest) FROM ledger_files").fetchone()[0]

    def highest_number(self) -> int:
        """The highest number any item has claimed, an undone one's included: none is given out twice."""
        return self.conn.execute("SELECT coalesce(max(number), 0) FROM item_numbers").fetchone()[0]

    def clashing(self) -> list[str]:
        """The items whose latest claim lost its number to an earlier one, by id: each waits for a renumber."""
        return [str(r) for (r,) in self.conn.execute("SELECT id FROM items WHERE number IS NULL ORDER BY id")]

    def holders(self, number: int) -> list[str]:
        """The items that stand and have claimed a number, its first claimant first."""
        sql = "SELECT item FROM item_numbers WHERE number = ? AND hidden = 0 ORDER BY claim, item"
        return [str(r) for (r,) in self.conn.execute(sql, (number,))]

    def lines(self, item_id: str) -> list[Line]:
        return held_lines(self.conn, item_id)

    def items_of(self, op_id: str) -> list[str]:
        """The items with a line of this op id: one, unless a fault put the id on two texts."""
        sql = "SELECT DISTINCT item FROM item_log WHERE id = ? ORDER BY item"
        return [str(r) for (r,) in self.conn.execute(sql, (op_id,))]

    def in_batch(self, batch: str) -> list[str]:
        """The items with an op of this write."""
        sql = "SELECT DISTINCT item FROM item_log WHERE batch = ? ORDER BY item"
        return [str(r) for (r,) in self.conn.execute(sql, (batch,))]

    # -- meetings and streams (DR-0628) -------------------------------------------------------------------------

    def is_object(self, add_id: str) -> bool:
        """Whether lines name this id as a meeting's or a stream's add."""
        return _in_objects(self.conn, add_id)

    def part(self, seeds: Iterable[str], known: Collection[str] = ()) -> set[str]:
        return part(self.conn, seeds, known=known)

    def object_lines(self, adds: Iterable[str]) -> dict[str, list[Line]]:
        return object_lines(self.conn, adds)

    def keyed(self, kind: str, key: str) -> list[str]:
        """The adds carrying a meeting's event or a stream's name, undone ones too."""
        table = "stream_log" if kind == "stream" else "meeting_log"
        sql = f"SELECT DISTINCT item FROM {table} WHERE key = ? ORDER BY item"  # noqa: S608 - the module's own names
        return [str(r) for (r,) in self.conn.execute(sql, (key,))]

    def objects_of(self, op_id: str) -> list[str]:
        """The meetings' and streams' adds with a line of this op id."""
        sql = "SELECT item FROM meeting_log WHERE id = ? UNION SELECT item FROM stream_log WHERE id = ? ORDER BY item"
        return [str(r) for (r,) in self.conn.execute(sql, (op_id, op_id))]

    def objects_in_batch(self, batch: str) -> list[str]:
        """The meetings' and streams' adds with an op of this write."""
        sql = (
            "SELECT item FROM meeting_log WHERE batch = ? UNION SELECT item FROM stream_log WHERE batch = ? "
            "ORDER BY item"
        )
        return [str(r) for (r,) in self.conn.execute(sql, (batch, batch))]

    def streams(self) -> dict[str, str]:
        """Each stream's name, with the id of the stream holding it."""
        return {str(name): str(i) for name, i in self.conn.execute("SELECT name, id FROM streams ORDER BY name")}

    def meeting_holders(self, number: int) -> list[str]:
        """The meetings that stand and have claimed or held an M- number, its first claimant first."""
        sql = (
            "SELECT meeting FROM meeting_numbers WHERE number = ? AND hidden = 0 "
            "ORDER BY claim IS NULL, claim, held, meeting"
        )
        return [str(r) for (r,) in self.conn.execute(sql, (number,))]

    def highest_meeting_number(self) -> int:
        """The highest M- number any meeting's add or renumber carries, an undone one's included."""
        return self.conn.execute("SELECT coalesce(max(number), 0) FROM meeting_numbers").fetchone()[0]

    def meeting_clashes(self) -> list[str]:
        """The meetings whose latest claim lost its number to an earlier one: each waits for a renumber."""
        return [str(r) for (r,) in self.conn.execute("SELECT id FROM meetings WHERE number IS NULL ORDER BY id")]

    def with_open_points(self) -> list[Due]:
        """Each meeting that isn't archived and has an open point, with what says whether it is due: what a
        rollover looks at (DR-0624)."""
        sql = (
            'SELECT id, start, "end", outcome, response FROM meetings m WHERE archived = 0 AND EXISTS (SELECT 1 FROM '
            "points p WHERE p.meeting = m.id AND p.n IS NOT NULL AND p.state = 'open') ORDER BY utc, id"
        )
        return [
            Due(str(i), datetime.fromisoformat(start), datetime.fromisoformat(end), outcome, response)
            for i, start, end, outcome, response in self.conn.execute(sql)
        ]

    def later_meetings(self, after: datetime) -> list[Light]:
        """The meetings starting after a time that aren't archived, cancelled or declined, by start: where a roll may
        go (DR-0624)."""
        sql = (
            "SELECT id, start, series, subject, organiser FROM meetings WHERE utc > ? AND archived = 0 "
            "AND coalesce(response, '') NOT IN ('cancelled', 'declined') ORDER BY utc, id"
        )
        return [
            Light(str(i), datetime.fromisoformat(start), series, subject, organiser)
            for i, start, series, subject, organiser in self.conn.execute(sql, (utc_text(after),))
        ]

    def open_items(self) -> list[tuple[str, str, str, str]]:
        """The open and snoozed items, by id: each one's id, title, who and source, for an add's look-alikes
        (DR-0642)."""
        sql = "SELECT id, title, who, source FROM items WHERE state IN ('open', 'snoozed') ORDER BY id"
        return [(str(i), str(t), str(w), str(s)) for i, t, w, s in self.conn.execute(sql)]

    def closed_by(self, day: date) -> list[str]:
        """The closed items not archived that closed on the day or before, by id: an archive is due for each
        (DR-0641)."""
        sql = "SELECT id FROM items WHERE state IN ('done', 'dropped') AND archived = 0 AND closed <= ? ORDER BY id"
        return [str(r) for (r,) in self.conn.execute(sql, (day.isoformat(),))]

    def meetings_at(self, start: datetime) -> list[str]:
        """The meetings starting at a moment, by id: where a keyless meeting's double would be (DR-0642)."""
        sql = "SELECT id FROM meetings WHERE utc = ? ORDER BY id"
        return [str(r) for (r,) in self.conn.execute(sql, (utc_text(start),))]

    def archivable(self, day: date) -> list[str]:
        """The meetings not archived that started on the day or before and have no open point, by start: an archive
        may be due for each (DR-0641)."""
        sql = (
            "SELECT id FROM meetings m WHERE archived = 0 AND day <= ? AND NOT EXISTS (SELECT 1 FROM points p WHERE "
            "p.meeting = m.id AND p.n IS NOT NULL AND p.state = 'open') ORDER BY utc, id"
        )
        return [str(r) for (r,) in self.conn.execute(sql, (day.isoformat(),))]


@dataclass(frozen=True)
class Change:
    """One op in an item's history as `docket ledger show` lists it: its log line, and the undo that left it out."""

    id: str
    day: date
    at: datetime | None
    by: str
    via: str
    line: str
    undone_by: str | None


@dataclass(frozen=True)
class Shown:
    """An item as `docket ledger show` shows it: its fields, its history, and, when it was named by a number other
    items have held too, those items with the number each holds now."""

    item: Item
    history: tuple[Change, ...]
    named: int | None = None
    also: tuple[tuple[str, int | None], ...] = ()


def find(conn: sqlite3.Connection, ref: str) -> Shown:
    """The item an A- number or an id names (DR-0613): a number finds the item that holds it and names the others
    that held it; one that none holds now, but more than one held, names them and shows none."""
    text = ref.strip()
    if ops.ULID.fullmatch(text):
        return _shown(conn, text)
    m = NUMBER.fullmatch(text)
    if m is None:
        raise DocketError(f"{ref!r} isn't an item: name one by its A- number or its id")
    number = int(m.group(1))
    label = number_text(number)
    holders = Held(conn).holders(number)
    if not holders:
        hidden = conn.execute("SELECT 1 FROM item_numbers WHERE number = ? LIMIT 1", (number,)).fetchone()
        raise DocketError(f"{label} was undone" if hidden else f"no item {label}")
    now = {
        str(item_id): held
        for item_id, held in conn.execute(
            f"SELECT id, number FROM items WHERE id IN ({', '.join('?' * len(holders))})",  # noqa: S608 - marks only
            holders,
        )
    }
    holding = [h for h in holders if now[h] == number]
    if not holding and len(holders) > 1:
        named = ", ".join(f"{h} ({now_held(now[h])})" for h in holders)
        raise DocketError(f"{label} isn't held now; it was held by {named}; name the item by its id")
    chosen = (holding or holders)[0]
    return _shown(conn, chosen, number, tuple((h, now[h]) for h in holders if h != chosen))


def now_held(number: int | None) -> str:
    """The number an item holds now, as a line about another item's number says it."""
    return f"now {number_text(number)}" if number is not None else "waiting for a new number"


def _shown(
    conn: sqlite3.Connection, item_id: str, named: int | None = None, also: tuple[tuple[str, int | None], ...] = ()
) -> Shown:
    sql = f"SELECT {', '.join(ITEM_COLUMNS)} FROM items WHERE id = ?"  # noqa: S608 - the module's own columns
    row = conn.execute(sql, (item_id,)).fetchone()
    if row is None:
        hidden = conn.execute("SELECT 1 FROM item_numbers WHERE item = ? LIMIT 1", (item_id,)).fetchone()
        raise DocketError(f"{item_id} was undone" if hidden else f"no item {item_id}")
    history = tuple(
        Change(op_id, date.fromisoformat(day), at and datetime.fromisoformat(at), by, via, line, undone)
        for op_id, day, at, by, via, line, undone in conn.execute(
            "SELECT id, day, at, by, via, line, undone_by FROM item_log WHERE item = ? AND line IS NOT NULL "
            "ORDER BY id, version",
            (item_id,),
        )
    )
    return Shown(_item(row), history, named, also)


def _item(row: Sequence[Any]) -> Item:
    f: dict[str, Any] = dict(zip(ITEM_COLUMNS, row, strict=True))

    def day(name: str) -> date | None:
        return None if f[name] is None else date.fromisoformat(f[name])

    return Item(
        id=f["id"], number=f["number"], claimed=f["claimed"], kind=f["kind"], title=f["title"], who=f["who"],
        stream=f["stream"], source=f["source"], opened=date.fromisoformat(f["opened"]), added_by=f["added_by"],
        meeting=f["meeting"], due=day("due"), description=f["description"], notes=f["notes"], outcome=f["outcome"],
        state=f["state"], until=day("until"), closed=day("closed"), triage=f["triage"], archived=bool(f["archived"]),
        materials=[Material(m["ref"], m.get("label")) for m in json.loads(f["materials"])], last=f["last_op"],
    )  # fmt: skip


# -- suggest (DR-0629, DR-0634) -----------------------------------------------------------------------------------

SUGGESTED = 15
OPEN = ("open", "snoozed")
ON_PEOPLE = ("reply", "waiting", "decision")


@dataclass(frozen=True)
class Suggestion:
    """An item worth raising in a meeting: its id, the number it holds, its title, and why."""

    item: str
    number: int | None
    title: str
    why: str


def name_tokens(name: str | None) -> set[str]:
    """A name's words of three letters or more, lowercased: todo.py's `name_tokens`, by which an item is on someone in a
    meeting when its who shares one with their name."""
    return {word for word in re.split(r"[^\w]+", (name or "").lower()) if len(word) >= 3}


def suggest(conn: sqlite3.Connection, meeting_ref: str) -> list[Suggestion]:
    """The open items worth raising in a meeting, by todo.py's rules (DR-0629, DR-0634): those linked to it but any
    whose title starts with prep; then replies, waits and decisions on someone in it, its organiser or an attendee, in
    its stream when it has one; then waits in its stream. An item already on one of its points is left out, and at most
    15 come, each once, by the day it opened within its group. `meeting_ref` is the id of any add of the meeting. A
    read: it writes nothing."""
    found = conn.execute(
        "SELECT meeting FROM meeting_log WHERE item = ? LIMIT 1", (meeting_ref.strip(),)
    ).fetchone()  # fmt: skip
    row = (
        conn.execute("SELECT id, organiser, attendees, stream, adds FROM meetings WHERE id = ?", found).fetchone()
        if found
        else None
    )
    if row is None:
        raise DocketError(f"no meeting {meeting_ref}")
    meeting_id, organiser, attendees, stream, adds = row
    people = [(name, name_tokens(name)) for name in [organiser, *json.loads(attendees)] if name]
    on_points = {
        str(item)
        for (item,) in conn.execute(
            "SELECT item FROM points WHERE meeting = ? AND n IS NOT NULL AND item IS NOT NULL", (meeting_id,)
        )
    }  # fmt: skip
    marks = ", ".join("?" * len(OPEN))
    items = conn.execute(
        f"SELECT id, number, kind, title, who, stream, meeting FROM items WHERE state IN ({marks}) "  # noqa: S608
        "ORDER BY opened, claimed, id",
        OPEN,
    ).fetchall()
    held = set(json.loads(adds))
    out: dict[str, Suggestion] = {}

    def take(item_id: str, number: int | None, title: str, why: str) -> None:
        if item_id not in on_points and item_id not in out and len(out) < SUGGESTED:
            out[item_id] = Suggestion(item_id, number, title, why)

    for item_id, number, _, title, _, _, linked in items:
        # A prep item was written to prepare the meeting, not to raise there; on someone in it, it still comes.
        if linked in held and not title.lower().startswith("prep"):
            take(item_id, number, title, "linked to the meeting")
    for item_id, number, kind, title, who, item_stream, _ in items:
        if kind not in ON_PEOPLE or (stream is not None and item_stream != stream):
            continue
        words = name_tokens(who)
        person = next((name for name, tokens in people if tokens & words), None)
        if person is not None:
            take(item_id, number, title, f"on {person}, in the meeting")
    if stream is not None:
        for item_id, number, kind, title, _, item_stream, _ in items:
            if kind == "waiting" and item_stream == stream:
                take(item_id, number, title, f"waiting in {stream}")
    return list(out.values())
