"""A ledger of a few years: 5,000 items and 50,000 ops, and 2,000 meetings with 20,000 point ops, read and folded by
`load` in under a second; all of it folded into the index's tables in under two seconds; one op more, on an item or
a meeting, as a write leaves it, read into them in a tenth of a second; and a write touching one meeting planned in a
tenth of a second, reading only its part (DR-0616, DR-0628, DR-0633, DR-0635).

The ops are written as the writer writes them, a month file per machine, each item an add and nine changes, each
meeting four points answered and a point rolled from the meeting before it in its weekly series of fifty. Wall time
is asserted only when no tracer runs; the files are built, and read once, before the clock starts, so a virus scanner
reading a file new to it on its first open isn't timed.
"""

from __future__ import annotations

import gc
import os
import sqlite3
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from docket_testkit import empty_vault, make_config, traced
from ledger_kit import ADD_FIELDS

from docket.index import build as index_build
from docket.index import ledger as ledger_index
from docket.index.ledger import Held
from docket.index.schema import LEDGER_TABLES
from docket.ledger import load, ops, store
from docket.ledger.ops import Op

ITEMS = 5_000
MEETINGS = 2_000
# Meetings a series holds: a weekly meeting's year. A roll ties a series' meetings into one part (DR-0633).
SERIES = 50
FOLD_SECONDS = 1.0
# A full rebuild runs only on a schema bump or a forget, never on the daily path (DR-0635).
REBUILD_SECONDS = 2.0
CATCH_UP_SECONDS = 0.1
WRITE_SECONDS = 0.1
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


def one_more(root: Path, last: Op, kind: str, **keys: object) -> Op:
    """One op more after `last`, as a write leaves it: appended to its month file."""
    (op_id,) = ops.mint(last.id, datetime(2026, 12, 1, tzinfo=UTC), 1)
    more = replace(last, **{"id": op_id, "op": kind, "after": last.id, "point": None, "fields": None, **keys})
    with (root / ops.rel_of(more)).open("a", encoding="utf-8", newline="\n") as f:
        f.write(more.text() + "\n")
    return more


@pytest.mark.perf
def test_a_few_thousand_items_and_meetings_read_and_fold_in_under_a_second(tmp_path: Path) -> None:
    build(tmp_path)
    began = time.perf_counter()
    ledger = load(tmp_path)
    elapsed = time.perf_counter() - began
    print(f"ledger fold: {elapsed:.2f} s for {ITEMS:,} items and {MEETINGS:,} meetings")
    assert (len(ledger.items), len(ledger.meetings)) == (ITEMS, MEETINGS)
    assert {item.state for item in ledger.items.values()} == {"open"}
    open_points = [p.text for m in ledger.meetings.values() for p in m.open_points()]
    assert open_points == ["The pilot's dates"] * (MEETINGS // SERIES)
    assert ledger.problems == ()
    if not traced():
        assert elapsed < FOLD_SECONDS, f"reading and folding the ledger took {elapsed:.2f} s, over {FOLD_SECONDS} s"


def caught_up(conn: sqlite3.Connection, root: Path) -> tuple[int, float]:
    began = time.perf_counter()
    conn.execute("BEGIN IMMEDIATE")
    grown = ledger_index.update(conn, root)
    conn.execute("COMMIT")
    return grown, time.perf_counter() - began


@pytest.mark.perf
def test_a_few_thousand_items_and_meetings_fold_into_the_index_in_under_two_seconds(tmp_path: Path) -> None:
    """The rebuild's ledger part, on a file set up as a rebuild sets its new index up; then a write's catch-up, of one
    op on an item and one on a meeting in the middle of its series, which folds that series again."""
    root = tmp_path / "vault"
    build(root)
    conn = sqlite3.connect(tmp_path / "index.sqlite", isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA synchronous = OFF")
        conn.executescript(LEDGER_TABLES)
        # What the tests before left for the collector is collected before the clock starts, not by the rebuild.
        gc.collect()
        began = time.perf_counter()
        conn.execute("BEGIN")
        read = ledger_index.rebuild(conn, root)
        conn.execute("COMMIT")
        elapsed = time.perf_counter() - began
        counts = [
            conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: S608 - the tables' own names
            for t in ("items", "item_log", "meetings", "points", "meeting_log")
        ]
        (text,) = conn.execute("SELECT keys FROM item_log ORDER BY id DESC LIMIT 1").fetchone()
        logged = one_more(root, ops.read_op(text), "log", note="one more")
        item_grown, item_caught = caught_up(conn, root)
        item_line = conn.execute("SELECT line FROM item_log WHERE id = ?", (logged.id,)).fetchone()
        (meeting_id,) = conn.execute("SELECT id FROM meetings WHERE number = ?", (MEETINGS - SERIES // 2,)).fetchone()
        (point_id,) = conn.execute("SELECT id FROM points WHERE meeting = ? AND n = 2", (meeting_id,)).fetchone()
        (text,) = conn.execute("SELECT keys FROM meeting_log ORDER BY id DESC LIMIT 1").fetchone()
        last = replace(ops.read_op(text), item=meeting_id)
        answered = one_more(root, last, "point_set", point=point_id, fields={"answer": "the 21st"})
        meeting_grown, meeting_caught = caught_up(conn, root)
        answer = conn.execute("SELECT answer FROM points WHERE id = ?", (point_id,)).fetchone()
        fate = conn.execute("SELECT fate FROM meeting_log WHERE id = ?", (answered.id,)).fetchone()
    finally:
        conn.close()
    print(
        f"ledger into the index: {elapsed:.2f} s for {ITEMS:,} items and {MEETINGS:,} meetings; one op more: "
        f"{item_caught * 1000:.0f} ms on an item, {meeting_caught * 1000:.0f} ms on a meeting"
    )
    assert read == ITEMS * (1 + len(CHANGES)) + MEETINGS * 11 - MEETINGS // SERIES
    assert counts == [ITEMS, ITEMS * (1 + len(CHANGES)), MEETINGS, MEETINGS * 5, MEETINGS * 11 - MEETINGS // SERIES]
    assert (item_grown, item_line) == (1, ("one more",))
    assert (meeting_grown, answer, fate) == (1, ("the 21st",), ("applied",))
    if not traced():
        assert elapsed < REBUILD_SECONDS, (
            f"folding the ledger into the index took {elapsed:.2f} s, over {REBUILD_SECONDS} s"
        )
        assert item_caught < CATCH_UP_SECONDS, (
            f"reading one op more on an item took {item_caught:.2f} s, over {CATCH_UP_SECONDS} s"
        )
        assert meeting_caught < CATCH_UP_SECONDS, (
            f"reading one op more on a meeting took {meeting_caught:.2f} s, over {CATCH_UP_SECONDS} s"
        )


@pytest.mark.perf
def test_a_write_touching_one_meeting_reads_only_its_series(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A point added to a meeting of a ledger that size, planned against the index as a write is: it reads the
    fifty meetings of that meeting's series and nothing else, in a tenth of a second. Dry, so git's commit isn't
    timed; the files' times set an hour back, as files a write didn't just write are."""
    vault = empty_vault(tmp_path, make_config())
    build(vault.root)
    hour_ago = time.time_ns() - 3600 * 10**9
    for path in (vault.root / ops.OPS_DIR).iterdir():
        os.utime(path, ns=(hour_ago, hour_ago))
    index_build.rebuild(vault)
    read: list[int] = []
    real = Held.object_lines
    monkeypatch.setattr(Held, "object_lines", lambda self, adds: read.append(len(adds)) or real(self, adds))
    number = MEETINGS - SERIES // 2
    change = {"op": "point", "item": f"M-{number:04d}", "fields": {"text": "Bring the dates"}}
    began = time.perf_counter()
    written = store.write(vault, [change], dry_run=True, now=datetime(2026, 12, 1, tzinfo=UTC))
    elapsed = time.perf_counter() - began
    print(f"a write on one meeting: {elapsed * 1000:.0f} ms, reading {sum(read)} of {MEETINGS:,} meetings")
    assert ([o.op for o in written.ops], list(written.numbers.values()), read) == (["point"], [number], [SERIES])
    if not traced():
        assert elapsed < WRITE_SECONDS, f"planning a write on one meeting took {elapsed:.2f} s, over {WRITE_SECONDS} s"
