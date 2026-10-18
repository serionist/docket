"""The ledger in the index (DR-0616): each item as its ops leave it, every op with its log line, each number's
claimants, and the op files read, so a write and `docket ledger show` ask the index instead of folding every op.

| Table | What |
|---|---|
| `items` | each item whose add stands: its fields, state, number (null while a clash waits), latest claim, last op |
| `item_log` | every op line, by item and id: its keys, its text, its log line, its fate, the undo that left it out |
| `item_numbers` | each number and every item that claimed it, by its first claim; an undone add's item is hidden |
| `ledger_files` | each op file read: its size, modification time, sha256 and the highest op id a line names |

`rebuild` folds every op file through `ledger.load`. `update` follows a file that only grew, its old bytes the same up
to a line's end: it reads the lines after them, folds again from `item_log` the items they name, then gives each
number those items claim, now or before, to its first claimant again, so a clash or an undone add moves a number as a
rebuild would. Anything else (a file changed, gone, or no longer UTF-8) folds every file again. Nothing here is a
door: the tables are rebuilt from the op files, and a write that can't have them folds the files in memory (DR-0619).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from operator import attrgetter
from pathlib import Path
from typing import Any

from docket.errors import DocketError
from docket.ledger import ops
from docket.ledger.fold import NUMBER, Entry, Folded, Item, Material, fold_item, load, number_text, paused
from docket.ledger.ops import Line, Op

TABLES = ("items", "item_log", "item_numbers", "ledger_files")
ITEM_COLUMNS = (
    "id", "number", "claimed", "kind", "title", "who", "stream", "source", "opened", "due", "description", "notes",
    "outcome", "state", "until", "closed", "triage", "archived", "materials", "added_by", "last_op",
)  # fmt: skip
LOG_COLUMNS = ("item", "id", "version", "after", "day", "at", "by", "via", "kind", "batch", "keys", "line", "undone_by",
               "fate")  # fmt: skip
NEWLINE = 0x0A
# Lines as the fold orders them: by id, and one id's texts by their text.
TEXT_ORDER = attrgetter("op.id", "text")


def _insert(table: str, columns: Sequence[str], verb: str = "INSERT") -> str:
    # Table and column names are the module's own, never a user's.
    return f"{verb} INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})"


INSERT_ITEM = _insert("items", ITEM_COLUMNS)
INSERT_LOG = _insert("item_log", LOG_COLUMNS)
INSERT_NUMBER = _insert("item_numbers", ("number", "item", "claim", "hidden"))
INSERT_FILE = _insert("ledger_files", ("path", "size", "mtime_ns", "sha256", "highest"), "INSERT OR REPLACE")
# Each number given to the item whose claim of it sorts first among the items that stand, as the fold gives it.
ASSIGN = (
    "UPDATE items SET number = CASE WHEN id = (SELECT item FROM item_numbers WHERE number = ?1 AND hidden = 0 "
    "ORDER BY claim, item LIMIT 1) THEN claimed END WHERE claimed = ?1"
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iso(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _line(keys: str) -> Line:
    """A line from its item_log row: the op as its text reads, in the file it belongs in."""
    op = Op.model_validate_json(keys)
    return Line(op, keys, ops.rel_of(op), 0)


def held_lines(conn: sqlite3.Connection, item_id: str) -> list[Line]:
    """Every line of an item the tables hold, in id order, each id's texts in text order."""
    sql = "SELECT keys FROM item_log WHERE item = ? ORDER BY id, version"
    return [_line(keys) for (keys,) in conn.execute(sql, (item_id,))]


class _Rows:
    """The rows a fold gives, gathered for one insert per table. A rebuild makes 50,000 log rows in one go, so the
    loop over them does as little as it can."""

    def __init__(self) -> None:
        self.items: list[tuple[Any, ...]] = []
        self.log: list[tuple[Any, ...]] = []
        self.numbers: list[tuple[Any, ...]] = []
        self.days: dict[date, str] = {}

    def add(self, item_id: str, lines: Iterable[Line], folded: Folded, number: int | None) -> None:
        """An item's rows: every line it has, the item when its add stands, holding `number`, and its claims."""
        item = folded.item
        # Each line the fold kept, with its log entry when the item stands.
        kept: dict[int, Entry | None] = (
            {id(line): entry for line, entry in zip(folded.lines, item.log, strict=True)}
            if item is not None
            else dict.fromkeys(map(id, folded.lines))
        )
        days, append, undone_of = self.days, self.log.append, folded.undone
        previous, version = "", 0
        for line in sorted(lines, key=TEXT_ORDER):
            op = line.op
            op_id = op.id
            version = version + 1 if op_id == previous else 0
            previous = op_id
            words = undone = None
            if id(line) not in kept:
                fate = "left out"
            elif (entry := kept[id(line)]) is not None:
                undone, words = entry.undone_by, entry.line
                fate = "undone" if undone else "applied" if entry.applied else "idle"
            else:
                undone = undone_of.get(op_id)
                fate = "undone" if undone else "hidden"
            day = days.get(op.day) or days.setdefault(op.day, op.day.isoformat())
            when = None if op.at is None else op.at.isoformat()
            append((item_id, op_id, version, op.after, day, when, op.by, op.via, op.op, op.batch, line.text, words,
                    undone, fate))  # fmt: skip
        first: dict[int, str] = {}
        for op_id, claimed in folded.claims:
            first[claimed] = min(first.get(claimed, op_id), op_id)
        self.numbers += [(n, item_id, op_id, int(item is None)) for n, op_id in first.items()]
        if item is not None:
            self.items.append(_item_row(item, folded.claims[-1][1], number))

    def write(self, conn: sqlite3.Connection) -> None:
        conn.executemany(INSERT_LOG, self.log)
        conn.executemany(INSERT_NUMBER, self.numbers)
        conn.executemany(INSERT_ITEM, self.items)


def _item_row(item: Item, claimed: int, number: int | None) -> tuple[Any, ...]:
    materials = [{"ref": m.ref} | ({"label": m.label} if m.label is not None else {}) for m in item.materials]
    return (
        item.id, number, claimed, item.kind, item.title, item.who, item.stream, item.source, item.opened.isoformat(),
        _iso(item.due), item.description, item.notes, item.outcome, item.state, _iso(item.until), _iso(item.closed),
        item.triage, int(item.archived),
        json.dumps(materials, ensure_ascii=False, separators=(",", ":")) if materials else "[]", item.added_by,
        item.last,
    )  # fmt: skip


# -- building -----------------------------------------------------------------------------------------------------


def rebuild(conn: sqlite3.Connection, root: Path) -> int:
    """Every op file folded into the tables, replacing what they held, inside the caller's transaction; how many op
    lines it read. The collector stays paused from the read to the last row (`ledger.load`)."""
    files: list[ops.FileRead] = []
    with paused():
        ledger = load(root, files)
        for table in TABLES:
            conn.execute(f"DELETE FROM {table}")  # noqa: S608 - the module's own table names
        conn.executemany(INSERT_FILE, [(f.rel, f.size, f.mtime_ns, f.sha256, f.highest) for f in files])
        rows = _Rows()
        for item_id, folded in ledger.folds.items():
            item = folded.item
            rows.add(item_id, ledger.lines[item_id], folded, None if item is None else item.number)
        rows.write(conn)
    return sum(f.lines for f in files)


def _grew(data: bytes, size: int, sha: str) -> bool:
    """Whether a file still starts with the bytes it was read with, ending at a line's end, so only lines came after."""
    return len(data) >= size and (size == 0 or data[size - 1] == NEWLINE) and _sha(data[:size]) == sha


def update(conn: sqlite3.Connection, root: Path) -> int:
    """The tables brought up to date with the op files, inside the caller's transaction: the lines each file gained
    since it was read folded into the items they name; anything else folds every file again. How many op lines it
    read."""
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
        conn.execute(INSERT_FILE, (rel, len(data), st.st_mtime_ns, _sha(data), highest))
        fresh += found.lines
    if fresh:
        refold(conn, fresh)
    return len(fresh)


def refold(conn: sqlite3.Connection, fresh: Iterable[Line]) -> None:
    """Fold again, from their item_log rows and the new lines, the items the new lines name; then give each number
    those items claim, or claimed before, to its first claimant, as the fold of every item gives it."""
    by_item: dict[str, list[Line]] = {}
    for line in fresh:
        by_item.setdefault(line.op.item, []).append(line)
    numbers: set[int] = set()
    rows = _Rows()
    for item_id in sorted(by_item):
        # A line twice, as a union merge can leave it, is one op; one id on two texts keeps both (DR-0612).
        lines = {(line.op.id, line.text): line for line in held_lines(conn, item_id)}
        for line in by_item[item_id]:
            lines.setdefault((line.op.id, line.text), line)
        numbers.update(n for (n,) in conn.execute("SELECT number FROM item_numbers WHERE item = ?", (item_id,)))
        for table, key in (("items", "id"), ("item_log", "item"), ("item_numbers", "item")):
            conn.execute(f"DELETE FROM {table} WHERE {key} = ?", (item_id,))  # noqa: S608 - the module's own names
        folded = fold_item(item_id, list(lines.values()))
        numbers.update(n for _, n in folded.claims)
        rows.add(item_id, lines.values(), folded, None)
    rows.write(conn)
    conn.executemany(ASSIGN, [(n,) for n in sorted(numbers)])


# -- asking -------------------------------------------------------------------------------------------------------


class Held:
    """The ledger as the tables hold it, asked the way a write asks (DR-0616)."""

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
        due=day("due"), description=f["description"], notes=f["notes"], outcome=f["outcome"], state=f["state"],
        until=day("until"), closed=day("closed"), triage=f["triage"], archived=bool(f["archived"]),
        materials=[Material(m["ref"], m.get("label")) for m in json.loads(f["materials"])], last=f["last_op"],
    )  # fmt: skip
