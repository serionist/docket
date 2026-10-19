"""The ledger in the index (DR-0616): what each table holds, and an update that follows the op files as they grow,
merge and change, leaving what a rebuild leaves every time, a clash and an undone add's number included."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from ledger_kit import ADD_FIELDS, fresh_tables, meeting, op, point, point_set, rebuilt, tables, ulid, write_ops

from docket.index import ledger as ledger_index
from docket.index.ledger import RACY_NS, Held, rebuild, settled, update
from docket.ledger import fold, load, ops
from docket.ledger.ops import Op

DESK = "ledger/ops/2026-10-desk.jsonl"
LAPTOP = "ledger/ops/2026-10-laptop.jsonl"


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = fresh_tables()
    yield c
    c.close()


def same_as_rebuild(conn: sqlite3.Connection, root: Path, change: Callable[[], object]) -> int:
    """Make the change, update, and hold the tables to a rebuild's; how many lines the update read."""
    change()
    read = update(conn, root)
    assert tables(conn) == rebuilt(root)
    return read


def laptop(o: Op) -> Op:
    return replace(o, machine="laptop")


def append(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as f:
        f.write(text.encode("utf-8"))


def rows(conn: sqlite3.Connection, sql: str, *args: object) -> list[tuple[Any, ...]]:
    return conn.execute(sql, args).fetchall()


def test_a_rebuild_holds_each_item_every_op_each_number_and_each_file(tmp_path: Path, conn: sqlite3.Connection) -> None:
    write_ops(tmp_path, [
        op("add", 1, fields={**ADD_FIELDS, "notes": "line one\nline two"}),
        op("triage", 2, 1, verdict="chase"),
        op("set", 3, 1, fields={"due": "2026-10-21"}),
        op("material", 4, 1, ref="docs/plan.md", label="the plan"),
        op("material", 5, 1, ref="https://example.invalid/x"),
        op("undo", 6, 1, of=ulid(3)),
        op("add", 7, number=2),
        op("done", 8, 7, note="answered"),
        op("done", 9, 7, note="again"),
        op("set", 10, 7, fields={"outcome": "sent"}, via="import"),
        op("log", 11, 40, note="no add"),
        op("add", 12, number=3),
        op("log", 13, 12, note="before the undo"),
        op("undo", 14, 12, of=ulid(12)),
    ])  # fmt: skip
    append(tmp_path, DESK, json.dumps({"schema_version": 2, "id": ulid(50)}) + "\n")
    assert rebuild(conn, tmp_path) == 14
    items = {r[0]: r for r in rows(conn, "SELECT * FROM items")}
    assert set(items) == {ulid(1), ulid(7)}
    assert items[ulid(1)] == (
        ulid(1), 1, 1, "reply", "Send Ada the dates", "Ada Example", "pilot", ADD_FIELDS["source"], None, "2026-10-18",
        None, None, "line one\nline two", None, "open", None, None, "chase", 0,
        '[{"ref":"docs/plan.md","label":"the plan"},{"ref":"https://example.invalid/x"}]', "claude", ulid(6),
    )  # fmt: skip
    assert items[ulid(7)][14:19] == ("done", None, "2026-10-18", None, 0)
    log = {r[1]: r for r in rows(conn, "SELECT * FROM item_log")}
    assert len(log) == 14
    fate = {op_id: (r[11], r[12], r[13]) for op_id, r in log.items()}
    assert fate[ulid(2)] == ("triage: chase", None, "applied")
    assert fate[ulid(3)] == ("due: 2026-10-21", ulid(6), "undone")
    assert fate[ulid(9)] == ("done: again (nothing to do)", None, "idle")
    assert fate[ulid(10)] == (None, None, "applied")
    assert fate[ulid(11)] == (None, None, "left out")
    assert fate[ulid(12)] == (None, ulid(14), "undone")
    assert fate[ulid(13)] == (None, None, "hidden")
    assert log[ulid(2)][:11] == (ulid(1), ulid(2), 0, None, "2026-10-18", None, "claude", "cli", "triage", None,
                                 op("triage", 2, 1, verdict="chase").text())  # fmt: skip
    assert rows(conn, "SELECT * FROM item_numbers ORDER BY number") == [
        (1, ulid(1), ulid(1), 0), (2, ulid(7), ulid(7), 0), (3, ulid(12), ulid(12), 1),
    ]  # fmt: skip
    data = (tmp_path / DESK).read_bytes()
    # Written a moment ago, so racily clean: its time is kept as 0 until it is older than RACY_NS.
    assert rows(conn, "SELECT path, size, mtime_ns, sha256, highest FROM ledger_files") == [
        (DESK, len(data), 0, hashlib.sha256(data).hexdigest(), ulid(50))
    ]
    assert Held(conn).highest_id() == load(tmp_path).highest_id == ulid(50)


def test_the_tables_answer_what_a_write_asks_as_the_fold_does(tmp_path: Path, conn: sqlite3.Connection) -> None:
    write_ops(tmp_path, [
        op("add", 1), op("add", 2, number=1), op("add", 3, number=2), op("renumber", 4, 3, number=3),
        op("log", 5, 1, note="x", batch=ulid(5)), op("log", 6, 3, note="y", batch=ulid(5)),
        op("add", 7, number=9), op("undo", 8, 7, of=ulid(7)),
    ])  # fmt: skip
    write_ops(tmp_path, [laptop(op("add", 10, number=4))])
    rebuild(conn, tmp_path)
    ledger = load(tmp_path)
    held = Held(conn)
    assert (held.highest_id(), held.highest_number()) == (ledger.highest_id, ledger.highest_number) == (ulid(10), 9)
    assert held.clashing() == [i.id for i in ledger.clashes()] == [ulid(2)]
    for number in range(11):
        assert held.holders(number) == list(ledger.holders.get(number, ()))
    assert held.holders(1) == [ulid(1), ulid(2)]
    assert sorted(line.text for line in held.lines(ulid(2))) == sorted(line.text for line in ledger.lines[ulid(2)])
    assert held.items_of(ulid(6)) == [ulid(3)]
    assert held.in_batch(ulid(5)) == [ulid(1), ulid(3)]
    assert [line.rel for line in held.lines(ulid(10))] == [LAPTOP]


def test_an_update_with_nothing_new_reads_nothing(tmp_path: Path, conn: sqlite3.Connection) -> None:
    write_ops(tmp_path, [op("add", 1)])
    rebuild(conn, tmp_path)
    assert same_as_rebuild(conn, tmp_path, lambda: None) == 0
    path = tmp_path / DESK
    st = path.stat()
    # Written again with the same bytes, as a checkout can leave it: its new time is kept, nothing is read.
    assert same_as_rebuild(conn, tmp_path, lambda: os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))) == 0


def test_a_file_read_close_to_its_time_is_hashed_again_so_a_rewrite_at_its_size_and_time_is_seen(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """Git's rule for racily clean files (RACY_NS): read within two seconds of its time, a file is stored with time 0.
    Rewritten at its size within one tick of the file clock, as a checkout of another clone's line leaves it, it keeps
    its size and time, and the next update hashes it, sees the change and folds it whole. Once its time is older, the
    time is stored, and the file is left alone while it stays as it is."""
    path = tmp_path / DESK
    base, ours, theirs = (op("add", 1).text(), op("add", 10, number=2).text(), op("add", 11, number=3).text())
    assert len(ours) == len(theirs)
    path.parent.mkdir(parents=True)
    path.write_text(f"{base}\n{ours}\n", encoding="utf-8", newline="\n")
    rebuild(conn, tmp_path)
    assert rows(conn, "SELECT mtime_ns FROM ledger_files") == [(0,)]
    st = path.stat()

    def rewrite() -> None:
        path.write_text(f"{base}\n{theirs}\n", encoding="utf-8", newline="\n")
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))

    assert same_as_rebuild(conn, tmp_path, rewrite) == 2
    assert rows(conn, "SELECT id FROM items ORDER BY id") == [(ulid(1),), (ulid(11),)]
    old = time.time_ns() - 10 * RACY_NS
    os.utime(path, ns=(old, old))
    assert update(conn, tmp_path) == 0
    assert rows(conn, "SELECT mtime_ns FROM ledger_files") == [(old,)]
    assert settled(old, old + RACY_NS) == old


def test_a_file_s_time_is_kept_once_settled_and_a_settled_file_is_left_alone(
    tmp_path: Path, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both sides of the rule on every system, on a clock the test sets: a file read within RACY_NS of its time is
    stored with time 0 and read again by each update; read later, its time is stored, by a rebuild or an update, and
    while it keeps its size and time no update reads it."""
    write_ops(tmp_path, [op("add", 1)])
    path = tmp_path / DESK
    when = 1_790_000_000 * 10**9
    os.utime(path, ns=(when, when))
    clock = [when + RACY_NS // 2]
    monkeypatch.setattr(time, "time_ns", lambda: clock[0])
    rebuild(conn, tmp_path)
    assert rows(conn, "SELECT mtime_ns FROM ledger_files") == [(0,)]
    assert update(conn, tmp_path) == 0
    assert rows(conn, "SELECT mtime_ns FROM ledger_files") == [(0,)]
    clock[0] = when + 2 * RACY_NS
    assert update(conn, tmp_path) == 0
    assert rows(conn, "SELECT mtime_ns FROM ledger_files") == [(when,)]
    later = fresh_tables()
    try:
        rebuild(later, tmp_path)
        assert rows(later, "SELECT mtime_ns FROM ledger_files") == [(when,)]
    finally:
        later.close()

    def unread(self: Path) -> bytes:
        raise AssertionError(f"{self} was read")

    monkeypatch.setattr(Path, "read_bytes", unread)
    assert update(conn, tmp_path) == 0


def test_back_to_back_rewrites_at_one_size_never_hide_from_an_update(tmp_path: Path) -> None:
    """Real writes, no time set by hand: a file goes from one clone's line to another's of the same length right after
    a catch-up read it, as a reset or a rebase's checkout leaves it. Most land in the same tick of the file clock."""
    base, ours, theirs = (op("add", 1).text(), op("add", 10, number=2).text(), op("add", 11, number=3).text())
    for n in range(20):
        root = tmp_path / f"r{n}"
        path = root / DESK
        path.parent.mkdir(parents=True)
        path.write_bytes(f"{base}\n{ours}\n".encode())
        conn = fresh_tables()
        try:
            rebuild(conn, root)
            path.write_bytes(f"{base}\n{theirs}\n".encode())
            update(conn, root)
            assert tables(conn) == rebuilt(root), n
        finally:
            conn.close()


def test_a_file_that_grew_is_read_from_where_it_stopped_and_only_its_items_fold_again(
    tmp_path: Path, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_ops(tmp_path, [op("add", 1), op("add", 2), op("triage", 3, 1, verdict="me")])
    rebuild(conn, tmp_path)
    folded: list[str] = []
    real = fold.fold_item
    monkeypatch.setattr(
        ledger_index, "fold_item", lambda item_id, lines: folded.append(item_id) or real(item_id, lines)
    )
    grown = [op("done", 4, 1, note="answered"), op("log", 5, 1, note="thanks")]
    assert same_as_rebuild(conn, tmp_path, lambda: write_ops(tmp_path, grown)) == 2
    assert folded == [ulid(1)]
    assert rows(conn, "SELECT state, last_op FROM items WHERE id = ?", ulid(1)) == [("done", ulid(5))]


def test_another_machine_s_new_file_lines_twice_and_one_id_on_two_texts_fold_as_a_rebuild(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    write_ops(tmp_path, [op("add", 1)])
    rebuild(conn, tmp_path)
    theirs = [laptop(op("triage", 2, 1, verdict="chase")), laptop(op("add", 3, number=2))]
    assert same_as_rebuild(conn, tmp_path, lambda: write_ops(tmp_path, theirs)) == 2
    twice = op("log", 4, 1, note="said twice")
    other = op("log", 4, 1, note="said another way")
    assert same_as_rebuild(conn, tmp_path, lambda: write_ops(tmp_path, [twice, twice, other])) == 3
    assert rows(conn, "SELECT version, line FROM item_log WHERE id = ? ORDER BY version", ulid(4)) == [
        (0, "said another way"), (1, "said twice"),
    ]  # fmt: skip
    assert same_as_rebuild(conn, tmp_path, lambda: write_ops(tmp_path, [twice])) == 1


def test_a_pulled_file_that_grew_with_an_earlier_add_of_a_held_number_takes_it_as_a_rebuild_does(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """The laptop's file is indexed; a pull grows it with an add that sorts before the desk's A-0005 and claims 5 too.
    The laptop's add keeps it, the desk's item waits for a renumber, and the update says so as a rebuild does."""
    write_ops(tmp_path, [laptop(op("add", 1)), op("add", 10, number=5), op("log", 11, 10, note="ours")])
    rebuild(conn, tmp_path)
    assert rows(conn, "SELECT id, number FROM items ORDER BY id") == [(ulid(1), 1), (ulid(10), 5)]
    assert same_as_rebuild(conn, tmp_path, lambda: write_ops(tmp_path, [laptop(op("add", 5, number=5))])) == 1
    assert rows(conn, "SELECT id, number FROM items ORDER BY id") == [(ulid(1), 1), (ulid(5), 5), (ulid(10), None)]
    assert Held(conn).clashing() == [ulid(10)]
    # The next write's renumber, and then the laptop's add undone: A-0005 is held by neither once A-0006 is the desk's.
    assert same_as_rebuild(conn, tmp_path, lambda: write_ops(tmp_path, [op("renumber", 12, 10, number=6)])) == 1
    assert same_as_rebuild(conn, tmp_path, lambda: write_ops(tmp_path, [laptop(op("undo", 13, 5, of=ulid(5)))])) == 1
    assert rows(conn, "SELECT id, number FROM items ORDER BY id") == [(ulid(1), 1), (ulid(10), 6)]


def test_an_undone_add_gives_its_number_to_the_item_waiting_for_it(tmp_path: Path, conn: sqlite3.Connection) -> None:
    write_ops(tmp_path, [op("add", 1, number=5), laptop(op("add", 2, number=5))])
    rebuild(conn, tmp_path)
    assert Held(conn).clashing() == [ulid(2)]
    assert same_as_rebuild(conn, tmp_path, lambda: write_ops(tmp_path, [op("undo", 3, 1, of=ulid(1))])) == 1
    assert rows(conn, "SELECT id, number FROM items") == [(ulid(2), 5)]
    assert same_as_rebuild(conn, tmp_path, lambda: write_ops(tmp_path, [op("undo", 4, 1, of=ulid(3))])) == 1
    assert rows(conn, "SELECT id, number FROM items ORDER BY id") == [(ulid(1), 5), (ulid(2), None)]


def test_a_meeting_s_and_a_stream_s_lines_are_their_logs_and_a_later_object_s_kept_as_a_rebuild_keeps_them(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """A meeting's lines are meeting_log's and a stream's stream_log's, each row with its object, the key its add
    carries and the op it links to; a later Docket's object's stay in item_log, fate later. An update keeps them as a
    rebuild does, a line read before its meeting's add too, which moves to meeting_log once the add comes."""
    write_ops(tmp_path, [
        op("add", 1), meeting(2, "calendar:E1"), point(3, 2), op("stream", 4), op("outbox", 5, number=7),
    ])  # fmt: skip
    rebuild(conn, tmp_path)
    assert rows(conn, "SELECT item, id, meeting, kind, key, link, fate FROM meeting_log ORDER BY id") == [
        (ulid(2), ulid(2), ulid(2), "meeting", "calendar:E1", None, "applied"),
        (ulid(2), ulid(3), ulid(2), "point", None, None, "applied"),
    ]  # fmt: skip
    assert rows(conn, "SELECT item, id, stream, kind, key, fate FROM stream_log") == [
        (ulid(4), ulid(4), ulid(4), "stream", "pilot", "applied"),
    ]  # fmt: skip
    assert rows(conn, "SELECT item, fate FROM item_log WHERE fate = 'later'") == [(ulid(5), "later")]
    early = laptop(point(7, 6, "Read before its meeting's add"))
    grown = [point_set(8, 2, 3, answer="the 21st"), op("log", 9, 5, note="kept"), early]
    assert same_as_rebuild(conn, tmp_path, lambda: write_ops(tmp_path, grown)) == 3
    assert rows(conn, "SELECT fate FROM item_log WHERE id = ?", ulid(7)) == [("left out",)]
    assert same_as_rebuild(conn, tmp_path, lambda: write_ops(tmp_path, [laptop(meeting(6))])) == 1
    held = ledger_index.object_lines(conn, [ulid(2), ulid(4), ulid(6)])
    assert {add_id: [line.op.id for line in lines] for add_id, lines in held.items()} == {
        ulid(2): [ulid(2), ulid(3), ulid(8)], ulid(4): [ulid(4)], ulid(6): [ulid(6), ulid(7)],
    }  # fmt: skip
    assert rows(conn, "SELECT count(*) FROM items") == [(1,)]
    assert rows(conn, "SELECT count(*) FROM item_log WHERE item = ?", ulid(6)) == [(0,)]


def test_ops_read_before_their_add_fold_once_it_comes(tmp_path: Path, conn: sqlite3.Connection) -> None:
    write_ops(tmp_path, [laptop(op("log", 2, 1, note="before the add came")), laptop(op("done", 3, 1))])
    rebuild(conn, tmp_path)
    assert rows(conn, "SELECT count(*) FROM items") == [(0,)]
    assert same_as_rebuild(conn, tmp_path, lambda: write_ops(tmp_path, [op("add", 1)])) == 1
    assert rows(conn, "SELECT state FROM items") == [("done",)]


@pytest.mark.parametrize(
    "change",
    ["rewritten", "shortened", "gone", "no newline at its end", "not UTF-8 now", "the folder a link"],
)
def test_anything_but_a_file_that_grew_folds_every_file_again(
    tmp_path: Path, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    write_ops(tmp_path, [op("add", 1), op("log", 2, 1, note="first"), laptop(op("add", 3, number=2))])
    path = tmp_path / DESK
    if change == "no newline at its end":
        path.write_bytes(path.read_bytes().rstrip(b"\n"))
    rebuild(conn, tmp_path)
    st = path.stat()

    def make() -> None:
        text = path.read_bytes()
        if change == "rewritten":
            path.write_bytes(text.replace(b"first", b"FIRST"))
        elif change == "shortened":
            path.write_bytes(text.splitlines(keepends=True)[0])
        elif change == "gone":
            path.unlink()
        elif change == "no newline at its end":
            path.write_bytes(text + b"\n" + op("log", 4, 1, note="next").text().encode() + b"\n")
        elif change == "not UTF-8 now":
            path.write_bytes(text + b"\xff\n")
        else:
            monkeypatch.setattr(ops, "linked", lambda p: p.name == "ops")
        if path.exists():
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))

    # Every line read again: a whole fold, not the lines after where the file stopped.
    assert same_as_rebuild(conn, tmp_path, make) == len(ops.read_all(tmp_path).lines)


def test_a_new_file_that_isn_t_utf8_is_left_out_until_it_reads(tmp_path: Path, conn: sqlite3.Connection) -> None:
    write_ops(tmp_path, [op("add", 1)])
    rebuild(conn, tmp_path)
    broken = tmp_path / LAPTOP
    assert same_as_rebuild(conn, tmp_path, lambda: broken.write_bytes(b"\xff\n")) == 0

    def fixed() -> None:
        broken.unlink()
        write_ops(tmp_path, [laptop(op("log", 2, 1, note="x"))])

    assert same_as_rebuild(conn, tmp_path, fixed) == 1


def test_an_empty_file_grows_from_its_start(tmp_path: Path, conn: sqlite3.Connection) -> None:
    (tmp_path / DESK).parent.mkdir(parents=True)
    (tmp_path / DESK).write_bytes(b"")
    rebuild(conn, tmp_path)
    assert rows(conn, "SELECT path, size FROM ledger_files") == [(DESK, 0)]
    assert same_as_rebuild(conn, tmp_path, lambda: write_ops(tmp_path, [op("add", 1)])) == 1


def test_an_op_keeps_its_time_and_an_imported_one_its_day_alone(tmp_path: Path, conn: sqlite3.Connection) -> None:
    write_ops(tmp_path, [
        op("add", 1, via="import", day=date(2026, 9, 2)),
        op("log", 2, 1, via="import", note="old"),
        op("log", 3, 1, note="now", at=datetime(2026, 10, 18, 9, 14, 2, tzinfo=UTC)),
    ])  # fmt: skip
    rebuild(conn, tmp_path)
    assert rows(conn, "SELECT day, at, line FROM item_log ORDER BY id") == [
        ("2026-09-02", None, None), ("2026-10-18", None, "old"), ("2026-10-18", "2026-10-18T09:14:02+00:00", "now"),
    ]  # fmt: skip
