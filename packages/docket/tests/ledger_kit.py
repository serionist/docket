"""Builders the ledger's tests share: ops made by hand, in the shape the writer makes them, op files on disk, and the
ledger's tables in the index as rows to compare."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from docket.index import ledger as ledger_index
from docket.index.build import db_path
from docket.index.schema import LEDGER_TABLES
from docket.ledger import ops
from docket.ledger.ops import Line, Op
from docket.vault.vault import Vault

NOW = datetime(2026, 10, 18, 9, 14, 2, tzinfo=UTC)
DAY = date(2026, 10, 18)
ADD_FIELDS = {"kind": "reply", "title": "Send Ada the dates", "who": "Ada Example", "stream": "pilot",
              "source": "chat:C0TEST/1791000001.000100"}  # fmt: skip
Rows = dict[str, list[tuple[Any, ...]]]
MEETING = {"subject": "Weekly pilot sync", "start": "2026-10-19T09:00+02:00", "end": "2026-10-19T09:30+02:00"}
STREAM = {"name": "pilot", "status": "active"}


def ulid(n: int, ms: int = 1_790_000_000_000) -> str:
    """An id with a known order: the nth one of a millisecond."""
    return ops.encode((ms << ops.RANDOM_BITS) + n)


def op(kind: str, n: int, item: int | None = None, **keys: Any) -> Op:
    """The op with id ulid(n), on the item whose add is ulid(item), by Claude on desk. An add, a meeting or a stream
    gets fields that make one, and a meeting the number n."""
    base: dict[str, Any] = {
        "schema_version": 1, "id": ulid(n), "item": ulid(n if item is None else item), "op": kind, "day": DAY,
        "machine": "desk", "by": "claude", "via": "cli",
    }  # fmt: skip
    if kind in ("add", "meeting"):
        base |= {"number": n, "fields": dict(ADD_FIELDS if kind == "add" else MEETING)}
    if kind == "stream":
        base |= {"fields": dict(STREAM)}
    return Op(**(base | keys))


OP_KEYS = ("machine", "day", "by", "via")


def meeting(n: int, event: str | None = None, **fields: Any) -> Op:
    """A meeting's add, ulid(n) numbered n unless `number` says, with its event and any other fields; a machine, a
    day, by or via is the op's."""
    number = fields.pop("number", n)
    keys = {key: fields.pop(key) for key in OP_KEYS if key in fields}
    keyed = {"event": event} if event else {}
    return op("meeting", n, number=number, fields={**MEETING, **keyed, **fields}, **keys)


def point(n: int, on: int, text: str = "The pilot's dates", **fields: Any) -> Op:
    """A point ulid(n) on the meeting whose add is ulid(on)."""
    return op("point", n, on, fields={"text": text, **fields})


def point_set(n: int, on: int, which: int, **fields: Any) -> Op:
    """A change to point ulid(which) on the meeting whose add is ulid(on)."""
    return op("point_set", n, on, point=ulid(which), fields=fields)


def roll(n: int, source_on: int, source: int, copy_on: int) -> tuple[Op, Op]:
    """A roll as the writer writes it: the copy ulid(n) on ulid(copy_on), then ulid(n + 1) marking the source rolled
    to it."""
    return (
        point(n, copy_on, **{"from": ulid(source)}),
        point_set(n + 1, source_on, source, state="rolled", to=ulid(n)),
    )


def line(o: Op, n: int = 1) -> Line:
    return Line(o, o.text(), ops.rel_of(o), n)


def lines(*made: Op) -> list[Line]:
    return [line(o, n) for n, o in enumerate(made, start=1)]


def write_ops(root: Path, made: Iterable[Op]) -> list[str]:
    """Append ops to their files under root, as the writer lays them out; the files written."""
    by_file: dict[str, list[str]] = {}
    for o in made:
        by_file.setdefault(ops.rel_of(o), []).append(o.text())
    for rel, texts in by_file.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        path.write_text(before + "".join(f"{t}\n" for t in texts), encoding="utf-8", newline="\n")
    return sorted(by_file)


def fresh_tables() -> sqlite3.Connection:
    """The ledger's tables, empty, in memory."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript(LEDGER_TABLES)
    return conn


def tables(conn: sqlite3.Connection) -> Rows:
    """The ledger's tables' rows, in key order; an op file's without its time, which is 0 or not by when it was read
    (`RACY_NS`), so two reads a moment apart can differ there and nowhere else."""
    rows = {t: sorted(conn.execute(f"SELECT * FROM {t}").fetchall()) for t in ledger_index.TABLES}  # noqa: S608
    rows["ledger_files"] = [(path, size, sha, highest) for path, size, _, sha, highest in rows["ledger_files"]]
    return rows


def rebuilt(root: Path) -> Rows:
    """What a rebuild of the ledger's tables gives for the op files under root."""
    conn = fresh_tables()
    try:
        ledger_index.rebuild(conn, root)
        return tables(conn)
    finally:
        conn.close()


def catch_up(vault: Vault) -> int:
    """Bring the index's ledger tables up to date from the op files, as a write brings them; how many lines it read."""
    conn = sqlite3.connect(db_path(vault), isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        read = ledger_index.update(conn, vault.root)
        conn.execute("COMMIT")
        return read
    finally:
        conn.close()


def caught_up(vault: Vault) -> Rows:
    """The index's ledger tables, brought up to date from the op files."""
    catch_up(vault)
    conn = sqlite3.connect(db_path(vault))
    try:
        return tables(conn)
    finally:
        conn.close()


# -- #201's perf ledger, which the ledger's and the day pages' timed tests share (DR-0635, DR-0668) ----------------

ITEMS = 5_000
MEETINGS = 2_000
# Meetings a series holds: a weekly meeting's year. A roll ties a series' meetings into one part (DR-0633).
SERIES = 50
CHANGES = ("log", "triage", "set", "log", "snooze", "triage", "log", "done", "reopen")


def build(root: Path, *, meetings: bool = True) -> None:
    """The items' ops, and with `meetings` the meetings' too, in their month files under root, each read once."""
    files: dict[str, list[str]] = {}
    highest: str | None = None
    start = datetime(2026, 1, 5, 8, tzinfo=UTC)
    for n in range(ITEMS):
        when = start + timedelta(minutes=n * 90)
        machine = ("desk", "laptop")[n % 2]
        ids = ops.mint(highest, when, 1 + len(CHANGES))
        highest = ids[-1]
        keys = {
            "log": {"note": "chased in the thread again, no answer yet"},
            "triage": {"verdict": "chase"},
            "set": {"fields": {"due": "2026-12-01", "notes": "ask twice, then call"}},
            "snooze": {"until": when.date() + timedelta(days=3)},
        }
        made = [Op(schema_version=1, id=ids[0], item=ids[0], op="add", day=when.date(), at=when.replace(microsecond=0),
                   machine=machine, by="claude", via="cli", batch=ids[0], number=n + 1,
                   fields={**ADD_FIELDS, "title": f"Send Ada the dates, round {n}"})]  # fmt: skip
        made += [
            Op(schema_version=1, id=ids[k], item=ids[0], op=kind, day=when.date(), machine=machine, by="sync",
               via="tool", batch=ids[0], after=ids[k - 1], **keys.get(kind, {}))
            for k, kind in enumerate(CHANGES, start=1)
        ]  # fmt: skip
        for o in made:
            files.setdefault(ops.rel_of(o), []).append(o.text())
    if meetings:
        add_meetings(files, start)
    for rel, texts in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(texts) + "\n", encoding="utf-8", newline="\n")
    for path in (root / ops.OPS_DIR).iterdir():
        path.read_bytes()


def add_meetings(files: dict[str, list[str]], start: datetime) -> None:
    """Each meeting: its add, the dates point rolled in from the one before in its series and that one's roll, four
    points and their answers: ten point ops a meeting."""
    highest: str | None = None
    before: tuple[str, str] | None = None
    for n in range(MEETINGS):
        when = start + timedelta(hours=n * 4)
        machine = ("desk", "laptop")[n % 2]
        ids = ops.mint(highest, when, 11)
        highest = ids[-1]
        base = {"schema_version": 1, "day": when.date(), "machine": machine, "by": "sync", "via": "tool",
                "batch": ids[0]}  # fmt: skip
        slot = f"{when:%Y-%m-%dT%H:%M}+00:00"
        made = [Op(**base, id=ids[0], item=ids[0], op="meeting", number=n + 1,
                   fields={"subject": f"Weekly pilot sync {n // SERIES}", "start": slot, "end": slot})]  # fmt: skip
        if n % SERIES == 0:
            before = None
        dates = {"text": "The pilot's dates"} | ({"from": before[1]} if before else {})
        made.append(Op(**base, id=ids[1], item=ids[0], op="point", fields=dates))
        if before is not None:
            made.append(Op(**base, id=ids[2], item=before[0], op="point_set", point=before[1],
                           fields={"state": "rolled", "to": ids[1]}))  # fmt: skip
        made += [Op(**base, id=ids[3 + k], item=ids[0], op="point", fields={"text": f"Point {k}"}) for k in range(4)]
        made += [
            Op(**base, id=ids[7 + k], item=ids[0], op="point_set", point=ids[3 + k],
               fields={"state": "answered", "answer": "agreed in the call"})
            for k in range(4)
        ]  # fmt: skip
        before = (ids[0], ids[1])
        for o in made:
            files.setdefault(ops.rel_of(o), []).append(o.text())
