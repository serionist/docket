"""Meetings, points and streams in the index (DR-0628): what each table holds, an update that folds only the parts its
lines touch (DR-0633), and update equal to rebuild as lines arrive in any order, with keys two clones added, continued
meetings, M- clashes, rolls and undos mixed in."""

from __future__ import annotations

import random
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from ledger_kit import ADD_FIELDS, fresh_tables, meeting, op, point, point_set, rebuilt, roll, tables, ulid, write_ops

from docket.errors import DocketError
from docket.index import ledger as ledger_index
from docket.index.ledger import Held, rebuild, update
from docket.ledger import load, ops
from docket.ledger.meetings import fold_objects
from docket.ledger.ops import Op

BASE = ops.decode(ulid(0))


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = fresh_tables()
    yield c
    c.close()


def rows(conn: sqlite3.Connection, sql: str, *args: object) -> list[tuple[Any, ...]]:
    return conn.execute(sql, args).fetchall()


def laptop(o: Op) -> Op:
    return replace(o, machine="laptop")


def n_of(op_id: str) -> int:
    return ops.decode(op_id) - BASE


def same_as_rebuild(conn: sqlite3.Connection, root: Path, made: list[Op]) -> int:
    write_ops(root, made)
    read = update(conn, root)
    assert tables(conn) == rebuilt(root)
    return read


def test_a_rebuild_holds_each_meeting_its_points_numbers_and_each_stream(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    write_ops(tmp_path, [
        meeting(1, "calendar:E1", series="calendar:S1", organiser="Ada Example",
                attendees=["Ada Example", "Ben Example"], stream="pilot", start="2026-10-19T09:00+02:00",
                end="2026-10-19T09:30+02:00"),
        point(2, 1, "Dates"),
        point(3, 1, "Budget", proposed=True),
        point_set(4, 1, 2, state="answered", answer="the 21st"),
        op("material", 5, 1, ref="https://example.invalid/rec", label="the recording"),
        meeting(6, number=2, subject="Lone", start="2026-10-20T23:30-05:00", end="2026-10-21T00:30-05:00"),
        *roll(7, 1, 3, 6),
        meeting(9, number=3),
        op("undo", 10, 9, of=ulid(9)),
        op("stream", 11, fields={"name": "pilot", "status": "active", "deadline": "2026-11-01"}),
        op("set", 12, 11, fields={"next": "send the dates"}, day=date(2026, 10, 19)),
        op("add", 13, number=1, fields={**ADD_FIELDS, "meeting": ulid(1)}),
    ])  # fmt: skip
    rebuild(conn, tmp_path)
    assert rows(conn, "SELECT * FROM meetings ORDER BY id") == [
        (ulid(1), 1, 1, "calendar:E1", "calendar:S1", "2026-10-19T09:00+02:00", "2026-10-19T09:30+02:00",
         "2026-10-19T07:00Z", "2026-10-19", "Weekly pilot sync", "Ada Example", None, None,
         '["Ada Example","Ben Example"]', "pilot", None, None,
         '[{"ref":"https://example.invalid/rec","label":"the recording"}]', 0, f'["{ulid(1)}"]', "claude", ulid(8)),
        (ulid(6), 2, 2, None, None, "2026-10-20T23:30-05:00", "2026-10-21T00:30-05:00", "2026-10-21T04:30Z",
         "2026-10-20", "Lone", None, None, None, "[]", None, None, None, "[]", 0, f'["{ulid(6)}"]', "claude", ulid(7)),
    ]  # fmt: skip
    assert rows(conn, "SELECT * FROM points ORDER BY id") == [
        (ulid(2), ulid(1), 1, "Dates", "answered", "the 21st", None, None, None, 0, None, "2026-10-18", "claude"),
        (ulid(3), ulid(1), 2, "Budget", "rolled", None, None, None, ulid(7), 1, None, "2026-10-18", "claude"),
        (ulid(7), ulid(6), 1, "The pilot's dates", "open", None, None, ulid(3), None, 0, None, "2026-10-18", "claude"),
    ]  # fmt: skip
    assert rows(conn, "SELECT id, kind, line, fate, link FROM meeting_log WHERE item = ? ORDER BY id", ulid(1)) == [
        (ulid(1), "meeting", "added", "applied", None),
        (ulid(2), "point", "point added: Dates", "applied", None),
        (ulid(3), "point", "point proposed: Budget", "applied", None),
        (ulid(4), "point_set", "point answered, answer: the 21st", "applied", None),
        (ulid(5), "material", "material added: the recording", "applied", None),
        (ulid(8), "point_set", "point rolled", "applied", ulid(7)),
    ]  # fmt: skip
    assert rows(conn, "SELECT id, link, fate FROM meeting_log WHERE item = ?", ulid(7)) == []
    assert rows(conn, "SELECT id, link, fate FROM meeting_log WHERE id = ?", ulid(7)) == [(ulid(7), ulid(3), "applied")]
    assert rows(conn, "SELECT id, fate, undone_by FROM meeting_log WHERE item = ? ORDER BY id", ulid(9)) == [
        (ulid(9), "undone", ulid(10)), (ulid(10), "hidden", None),
    ]  # fmt: skip
    assert rows(conn, "SELECT * FROM meeting_numbers ORDER BY number") == [
        (1, ulid(1), ulid(1), None, 0), (2, ulid(6), ulid(6), None, 0), (3, ulid(9), None, None, 1),
    ]  # fmt: skip
    assert rows(conn, "SELECT * FROM streams") == [
        (ulid(11), "pilot", "active", None, "send the dates", "2026-11-01", "2026-10-19", f'["{ulid(11)}"]', "claude",
         ulid(12)),
    ]  # fmt: skip
    assert rows(conn, "SELECT meeting FROM items") == [(ulid(1),)]
    ledger = load(tmp_path)
    held = Held(conn)
    assert held.highest_meeting_number() == ledger.objects.highest_number == 3
    assert held.streams() == {"pilot": ulid(11)}
    assert [row.id for row in held.with_open_points()] == [ulid(6)]
    assert [m.id for m in held.later_meetings(ledger.meetings[ulid(1)].start)] == [ulid(6)]
    assert held.keyed("meeting", "calendar:E1") == [ulid(1)]
    assert held.objects_of(ulid(8)) == [ulid(1)]


def test_the_tables_answer_a_write_s_meeting_questions_as_the_fold_does(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """Two clones numbered a week apart: one event added on both, a number two meetings claim, a continued meeting
    whose key's renumber is a number it held, and a meeting another clone renumbered."""
    write_ops(tmp_path, [
        meeting(1, "calendar:E1", number=1), meeting(2, number=2), meeting(3, number=3),
        op("renumber", 4, 3, number=7),
        op("log", 5, 1, note="x", batch=ulid(5)), replace(point(6, 2, "Dates"), batch=ulid(5)),
    ])  # fmt: skip
    write_ops(tmp_path, [
        laptop(meeting(10, "calendar:E1", number=2)), laptop(meeting(11, "calendar:E2", number=3)),
        laptop(op("renumber", 12, 11, number=4)), laptop(meeting(13, "calendar:E2", number=4, continues=ulid(2))),
    ])  # fmt: skip
    rebuild(conn, tmp_path)
    ledger = load(tmp_path)
    held = Held(conn)
    assert held.meeting_clashes() == [m.id for m in ledger.objects.clashes()]
    for number in range(9):
        assert held.meeting_holders(number) == list(ledger.objects.holders.get(number, ())), number
    assert held.highest_meeting_number() == ledger.objects.highest_number == 7
    assert held.objects_in_batch(ulid(5)) == [ulid(1), ulid(2)]
    assert held.keyed("meeting", "calendar:E2") == [ulid(11), ulid(13)]
    assert held.part([ulid(13)]) == {ulid(2), ulid(11), ulid(13)}
    assert held.is_object(ulid(10)) and not held.is_object(ulid(40))


def test_a_key_two_clones_added_a_continued_meeting_and_an_m_clash_update_as_a_rebuild(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """DR-0628's case: the desk's week indexed, then the laptop's pulled, its numbering one ahead: its add of the
    desk's event joins that meeting, its add claiming a number the desk's next meeting holds clashes, and its keyed
    add continuing the desk's unkeyed meeting joins them; then the clash's renumber, and the join split."""
    write_ops(tmp_path, [
        meeting(1, "calendar:E1", number=1), point(2, 1, "Dates"), meeting(3, number=2), meeting(4, number=3),
        point(5, 4, "Budget"),
    ])  # fmt: skip
    rebuild(conn, tmp_path)
    theirs = [
        laptop(meeting(10, "calendar:E1", number=2, where="Room 1")),
        laptop(meeting(11, "calendar:E9", number=3)),
        laptop(meeting(12, "calendar:E2", number=4, continues=ulid(4))),
        laptop(point_set(13, 12, 5, state="answered", answer="agreed")),
    ]
    assert same_as_rebuild(conn, tmp_path, theirs) == 4
    assert rows(conn, "SELECT id, number, claimed, event, \"where\" FROM meetings ORDER BY id") == [
        (ulid(1), 1, 1, "calendar:E1", "Room 1"), (ulid(3), 2, 2, None, None),
        (ulid(4), 3, 3, "calendar:E2", None), (ulid(11), None, 3, "calendar:E9", None),
    ]  # fmt: skip
    assert rows(conn, "SELECT state, answer FROM points WHERE id = ?", ulid(5)) == [("answered", "agreed")]
    assert Held(conn).meeting_clashes() == [ulid(11)]
    assert same_as_rebuild(conn, tmp_path, [op("renumber", 14, 11, number=5)]) == 1
    assert same_as_rebuild(conn, tmp_path, [op("undo", 15, 12, of=ulid(12))]) == 1
    assert rows(conn, "SELECT id, number, event FROM meetings ORDER BY id") == [
        (ulid(1), 1, "calendar:E1"), (ulid(3), 2, None), (ulid(4), 3, None), (ulid(11), 5, "calendar:E9"),
    ]  # fmt: skip
    assert rows(conn, "SELECT meeting, fate FROM meeting_log WHERE id = ?", ulid(12)) == [(ulid(12), "undone")]


def test_an_update_folds_only_the_parts_its_lines_touch(
    tmp_path: Path, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A series whose points rolled from one meeting to the next is one part; a meeting no roll reaches is a part of
    its own, and one more op on it folds it alone."""
    made = [meeting(1), point(2, 1, "Dates"), meeting(3), *roll(4, 1, 2, 3), meeting(6), *roll(7, 3, 4, 6)]
    made += [meeting(20 + k) for k in range(5)]
    write_ops(tmp_path, made)
    rebuild(conn, tmp_path)
    folded: list[set[str]] = []
    real = fold_objects
    monkeypatch.setattr(ledger_index, "fold_objects", lambda groups: folded.append(set(groups)) or real(groups))
    assert same_as_rebuild(conn, tmp_path, [op("log", 30, 22, note="one more")]) == 1
    assert same_as_rebuild(conn, tmp_path, [point_set(31, 6, 7, answer="the 21st")]) == 1
    assert folded == [{ulid(22)}, {ulid(1), ulid(3), ulid(6)}]


def test_a_line_naming_a_point_before_it_arrives_joins_its_part_when_it_comes(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """A roll pulled before its copy: the roll names a copy that isn't there, and is left out; the copy, pulled later
    on another meeting, makes the roll stand, as a rebuild has it."""
    write_ops(tmp_path, [meeting(1), point(2, 1, "Dates"), meeting(3)])
    rebuild(conn, tmp_path)
    copy, rolled = roll(4, 1, 2, 3)
    assert same_as_rebuild(conn, tmp_path, [laptop(rolled)]) == 1
    assert rows(conn, "SELECT state FROM points WHERE id = ?", ulid(2)) == [("open",)]
    assert same_as_rebuild(conn, tmp_path, [laptop(copy)]) == 1
    assert rows(conn, "SELECT id, state FROM points ORDER BY id") == [(ulid(2), "rolled"), (ulid(4), "open")]


def test_an_op_s_add_made_a_meeting_after_its_lines_came_moves_them_and_an_item_s_lines_stay(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    write_ops(tmp_path, [laptop(point(2, 1, "Dates")), laptop(op("log", 3, 1, note="early")), op("add", 5)])
    rebuild(conn, tmp_path)
    assert rows(conn, "SELECT id, fate FROM item_log WHERE item = ? ORDER BY id", ulid(1)) == [
        (ulid(2), "left out"), (ulid(3), "left out"),
    ]  # fmt: skip
    assert same_as_rebuild(conn, tmp_path, [meeting(1)]) == 1
    assert rows(conn, "SELECT id, fate FROM meeting_log ORDER BY id") == [
        (ulid(1), "applied"), (ulid(2), "applied"), (ulid(3), "applied"),
    ]  # fmt: skip
    assert same_as_rebuild(conn, tmp_path, [op("log", 6, 5, note="an item's")]) == 1


def test_a_meeting_s_add_on_a_second_text_that_makes_an_item_folds_as_an_item_as_a_rebuild_has_it(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    """One id on two texts (DR-0612): a meeting's add, then another clone's add of an item on its id, whose text sorts
    first, so the id makes an item. Its lines leave the meeting's log for the item's, and its M- number is let go."""
    write_ops(tmp_path, [meeting(1), point(2, 1, "Dates"), meeting(3, number=2)])
    rebuild(conn, tmp_path)
    assert same_as_rebuild(conn, tmp_path, [laptop(op("add", 1, number=1))]) == 1
    assert rows(conn, "SELECT id FROM items") == [(ulid(1),)]
    assert rows(conn, "SELECT id FROM meetings") == [(ulid(3),)]
    assert rows(conn, "SELECT count(*) FROM meeting_log WHERE item = ?", ulid(1)) == [(0,)]


# -- update equals rebuild, at random ------------------------------------------------------------------------------


def history(seed: int) -> list[Op]:
    """About 170 ops on two machines: meetings with and without an event, some continuing one, numbers that clash,
    items hanging on meetings, points, answers, rolls to a copy or straight to a meeting, sets, materials, archives,
    renumbers, streams, a later Docket's object, and undos of any op."""
    rng = random.Random(seed)  # noqa: S311
    made: list[Op] = []
    meetings: list[int] = []
    events: dict[int, str | None] = {}
    items: list[int] = []
    points: list[tuple[int, int]] = []
    streams: list[int] = []
    n = 0
    for _ in range(170):
        n += 2
        machine = rng.choice(["desk", "laptop"])

        def on(o: Op, machine: str = machine) -> Op:
            return replace(o, machine=machine)

        r = rng.random()
        if not meetings or r < 0.12:
            event = rng.choice([None, None, "calendar:E1", "calendar:E2", "calendar:E3"])
            extra: dict[str, Any] = {}
            alone = [k for k in meetings if events[k] is None]
            if event and alone and rng.random() < 0.35:
                extra["continues"] = ulid(rng.choice(alone))
            if rng.random() < 0.3:
                extra["series"] = rng.choice(["calendar:S1", "calendar:S2"])
            made.append(on(meeting(n, event, number=rng.randint(1, 6), subject=f"S{n}", **extra)))
            meetings.append(n)
            events[n] = event
        elif r < 0.18:
            hang = {"meeting": ulid(rng.choice(meetings))} if rng.random() < 0.5 else {}
            made.append(on(op("add", n, number=rng.randint(1, 8), fields={**ADD_FIELDS, "title": f"T{n}", **hang})))
            items.append(n)
        elif r < 0.24 and items:
            made.append(on(op(rng.choice(["log", "done", "reopen"]), n, rng.choice(items), note=f"n{n}")))
        elif r < 0.36:
            m = rng.choice(meetings)
            source = {"from": ulid(rng.choice(meetings))} if rng.random() < 0.1 else {}
            made.append(on(point(n, m, f"P{n}", **source)))
            points.append((n, m))
        elif r < 0.46 and points:
            p, m = rng.choice(points)
            change = rng.choice([{"answer": f"a{n}", "state": "answered"}, {"state": "dropped"}, {"text": f"t{n}"}])
            made.append(on(point_set(n, m, p, **change)))
        elif r < 0.58 and points:
            p, m = rng.choice(points)
            to = rng.choice(meetings)
            if rng.random() < 0.15:
                made.append(on(point_set(n, m, p, state="rolled", to=ulid(to))))
            else:
                copy, rolled = roll(n, m, p, to)
                made += [on(copy), on(rolled)]
                points.append((n, to))
        elif r < 0.64:
            if streams and rng.random() < 0.4:
                made.append(on(op("set", n, rng.choice(streams), fields={"next": f"x{n}"})))
            else:
                made.append(on(op("stream", n, fields={"name": rng.choice(["pilot", "ops"]), "status": "active"})))
                streams.append(n)
        elif r < 0.67:
            made.append(on(op("outbox", n)))
        elif r < 0.74:
            made.append(on(op("set", n, rng.choice(meetings), fields={"where": f"Room {n}"})))
        elif r < 0.78:
            made.append(on(op("archive", n, rng.choice(meetings))))
        elif r < 0.81:
            made.append(on(op("material", n, rng.choice(meetings), ref=f"rec:{n % 3}")))
        elif r < 0.84:
            made.append(on(op("renumber", n, rng.choice(meetings), number=rng.randint(1, 9))))
        else:
            target = rng.choice(made)
            made.append(on(op("undo", n, n_of(target.item), of=target.id)))
    return made


@pytest.mark.parametrize("seed", range(12))
def test_an_update_equals_a_rebuild_as_lines_arrive_in_any_order(tmp_path: Path, seed: int) -> None:
    """Lines arrive in batches, out of order across machines, as pulls bring another clone's lines late, each file
    only growing, some lines twice as a union merge leaves them: after each batch the update leaves what a rebuild
    of the same files does."""
    made = history(seed)
    rng = random.Random(seed + 99)  # noqa: S311
    order = list(made)
    rng.shuffle(order)
    conn = fresh_tables()
    try:
        head, rest = order[:20], order[20:]
        write_ops(tmp_path, head)
        rebuild(conn, tmp_path)
        while rest:
            k = rng.randint(1, 15)
            batch, rest = rest[:k], rest[k:]
            twice = rng.sample(made[: len(made) - len(rest)], k=1) if rng.random() < 0.2 else []
            write_ops(tmp_path, [*batch, *twice])
            update(conn, tmp_path)
            assert tables(conn) == rebuilt(tmp_path)
        assert load(tmp_path).meetings
    finally:
        conn.close()


# -- suggest (DR-0629, DR-0634) -------------------------------------------------------------------------------------


def item(n: int, number: int, title: str, **fields: Any) -> Op:
    return op("add", n, number=number, fields={**ADD_FIELDS, "title": title, **fields})


def test_suggest_names_what_to_raise_in_a_meeting_and_why(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """Items linked to the meeting, a prep task left out; then replies, waits and decisions on someone in it, by
    name or first name, in its stream; then waits in its stream. Closed items, items on its points and items
    elsewhere are left out; a meeting with no stream asks no stream of the people's items."""
    write_ops(tmp_path, [
        meeting(1, organiser="Ada Example", attendees=["Ada Example", "Ben Example"], stream="pilot"),
        item(2, 1, "Send the dates", meeting=ulid(1)),
        item(3, 2, "Prep the agenda", kind="task", meeting=ulid(1)),
        item(4, 3, "Ben's numbers", kind="waiting", who="Ben"),
        item(5, 4, "The vendor", kind="decision", stream="ops"),
        item(6, 5, "Cleo's quote", kind="waiting", who="Cleo Example"),
        item(7, 6, "On a point", kind="reply"),
        point(8, 1, "The quote", item=ulid(7)),
        item(9, 7, "Done already", meeting=ulid(1)), op("done", 10, 9),
        item(11, 8, "A task on Ada", kind="task"),
        meeting(12, number=2, organiser="Ben Example", start="2026-10-20T09:00+02:00", end="2026-10-20T09:30+02:00"),
    ])  # fmt: skip
    rebuild(conn, tmp_path)
    assert [(s.item, s.number, s.title, s.why) for s in ledger_index.suggest(conn, ulid(1))] == [
        (ulid(2), 1, "Send the dates", "linked to the meeting"),
        (ulid(4), 3, "Ben's numbers", "on Ben Example, in the meeting"),
        (ulid(6), 5, "Cleo's quote", "waiting in pilot"),
    ]  # fmt: skip
    assert [s.item for s in ledger_index.suggest(conn, ulid(12))] == [ulid(4)]
    with pytest.raises(DocketError, match="no meeting"):
        ledger_index.suggest(conn, ulid(2))


def test_suggest_reads_a_joined_meeting_by_any_add_and_brings_fifteen_at_most(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    made = [meeting(1), meeting(2, "calendar:E1", continues=ulid(1))]
    made += [item(10 + k, k + 1, f"Item {k}", meeting=ulid(2 if k % 2 else 1)) for k in range(20)]
    write_ops(tmp_path, made)
    rebuild(conn, tmp_path)
    by_key = ledger_index.suggest(conn, ulid(2))
    assert [s.number for s in by_key] == list(range(1, 16))
    assert ledger_index.suggest(conn, ulid(1)) == by_key
