"""Folding meetings, their points and streams (DR-0620 to DR-0625): keys, a continued meeting, numbers, copies and
their chains, archive, and what is left out."""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest
from ledger_kit import DAY, MEETING, STREAM, lines, meeting, op, point, point_set, roll, ulid

from docket.ledger import ops
from docket.ledger.fold import Ledger, describe, fold
from docket.ledger.meetings import Meeting, Point, describe_point
from docket.ledger.ops import Line, Op


def folded(*made: Op) -> Ledger:
    return fold(lines(*made))


def only(ledger: Ledger) -> Meeting:
    (m,) = ledger.meetings.values()
    return m


def messages(ledger: Ledger) -> list[str]:
    return [message for _, message in ledger.problems]


def test_a_meeting_s_add_holds_its_fields_and_number() -> None:
    fields = {"event": "calendar:E1", "series": "calendar:S1", "organiser": "Ada Example", "where": "Room 1",
              "response": "accepted", "attendees": ["Ada Example", "Ben Example"], "stream": "pilot",
              "notes": "bring the dates", "outcome": "agreed"}  # fmt: skip
    ledger = folded(meeting(1, **fields))
    m = only(ledger)
    assert (m.id, m.number, m.claimed, m.subject, m.event, m.series, m.adds) == (
        ulid(1), 1, 1, "Weekly pilot sync", "calendar:E1", "calendar:S1", (ulid(1),),
    )  # fmt: skip
    assert m.start == datetime(2026, 10, 19, 9, tzinfo=timezone(timedelta(hours=2)))
    assert (m.day, m.end - m.start, m.attendees, m.where, m.response) == (
        date(2026, 10, 19), timedelta(minutes=30), ("Ada Example", "Ben Example"), "Room 1", "accepted",
    )  # fmt: skip
    assert (m.organiser, m.stream, m.notes, m.outcome, m.archived, m.points) == (
        "Ada Example", "pilot", "bring the dates", "agreed", False, [],
    )  # fmt: skip
    assert [e.line for e in m.log] == ["added"]
    assert (ledger.items, ledger.problems, ledger.objects.holders, ledger.objects.highest_number) == (
        {},
        (),
        {1: (ulid(1),)},
        1,
    )
    assert ledger.objects.events == {"calendar:E1": ulid(1)}


def test_sets_materials_and_logs_change_a_meeting_and_its_offset_counts() -> None:
    m = only(folded(
        meeting(1),
        op("set", 2, 1, fields={"subject": "Pilot sync", "start": "2026-10-19T08:00+01:00", "attendees": ["Cy Example"],
                                "notes": "x", "event": "calendar:E9", "continues": ulid(1), "later": 5}),
        op("set", 3, 1, fields={"notes": None, "subject": None, "end": "2026-10-19T09:30+02:00"}),
        op("set", 4, 1, fields={"attendees": None}),
        op("material", 5, 1, ref="notes/pilot.md", label="the notes"),
        op("material", 6, 1, ref="notes/pilot.md", remove=True),
        op("log", 7, 1, note="moved to the morning"),
        op("pin", 9, 1),
        op("renumber", 10, 1, number=4),
    ))  # fmt: skip
    # The same instant at another offset is another local time: the set applies (DR-0622).
    assert (m.subject, m.start.isoformat(timespec="minutes"), m.notes, m.attendees, m.event, m.number) == (
        "Pilot sync", "2026-10-19T08:00+01:00", None, (), None, 4,
    )  # fmt: skip
    assert [e.applied for e in m.log] == [True, True, True, True, True, True, True, False, True]
    assert m.log[1].line == (
        "subject: Pilot sync; start: 2026-10-19T08:00+01:00; attendees: Cy Example; notes edited; "
        f"event: calendar:E9; continues: {ulid(1)}; later: 5"
    )
    assert m.materials == []
    assert describe(op("set", 2, 1, fields={"attendees": ["Ada Example", "Ben Example"]})) == (
        "attendees: Ada Example, Ben Example"
    )


def test_the_adds_of_one_event_are_one_meeting_and_the_first_holds_it() -> None:
    """Two clones apart made one calendar event's meeting each: the first add by id is the meeting, the later one
    folds in as a set, and the ops naming either are the meeting's (DR-0621)."""
    ledger = folded(
        meeting(1, "calendar:E1"),
        meeting(2, "calendar:E1", subject="Weekly pilot sync, moved", number=7),
        point(3, 2, "Ask about the dates"),
        op("log", 4, 1, note="seen"),
    )
    m = only(ledger)
    assert (m.id, m.adds, m.subject, m.number, [p.text for p in m.points]) == (
        ulid(1), (ulid(1), ulid(2)), "Weekly pilot sync, moved", 1, ["Ask about the dates"],
    )  # fmt: skip
    assert ledger.objects.owner == {ulid(1): ulid(1), ulid(2): ulid(1)}
    # The later add's number is one the meeting held: it finds the meeting, and wins nothing.
    assert ledger.objects.holders == {1: (ulid(1),), 7: (ulid(1),)}
    assert (ledger.problems, ledger.objects.highest_number) == ((), 7)
    assert [e.line for e in m.log] == ["added", "added", "point added: Ask about the dates", "seen"]


def week(first: int, second: int) -> list[Op]:
    """One week added on two clones apart, numbering shifted by one: the second clone (ids from `second`) adds E0, E1
    and E2 as M-0041 to M-0043; the first (ids from `first`) adds E1 and E2 as M-0041 and M-0042."""
    return [
        meeting(second, "calendar:E0", number=41),
        meeting(second + 1, "calendar:E1", number=42),
        meeting(second + 2, "calendar:E2", number=43),
        meeting(first, "calendar:E1", number=41),
        meeting(first + 1, "calendar:E2", number=42),
    ]


def test_a_week_two_clones_numbered_apart_is_one_meeting_per_event_in_either_order() -> None:
    """Every shifted number names each meeting it named before the merge, so a write by it is refused rather than
    landing on another meeting (DR-0613, DR-0621); a later add never renumbers anything."""
    ledger = folded(*week(first=20, second=10))
    events = {m.event: m for m in ledger.meetings.values()}
    assert {e: m.number for e, m in events.items()} == {"calendar:E0": 41, "calendar:E1": 42, "calendar:E2": 43}
    e0, e1, e2 = (events[f"calendar:E{k}"].id for k in range(3))
    assert ledger.objects.holders == {41: (e0, e1), 42: (e1, e2), 43: (e2,)}
    assert (ledger.problems, ledger.objects.clashes()) == ((), [])
    # The other way round: the first clone's adds sort first, so E1 and E2 stand on its numbers and E0's claim of
    # M-0041 loses to E1's, waiting for a renumber.
    other = folded(*week(first=10, second=20))
    events = {m.event: m for m in other.meetings.values()}
    assert {e: m.number for e, m in events.items()} == {"calendar:E0": None, "calendar:E1": 41, "calendar:E2": 42}
    e0, e1, e2 = (events[f"calendar:E{k}"].id for k in range(3))
    assert other.objects.holders == {41: (e1, e0), 42: (e2, e1), 43: (e2,)}
    assert [m.id for m in other.objects.clashes()] == [e0]
    assert other.problems == (("M-0041", f"held by two meetings; the next write gives {e0} a new number"),)


def test_a_keyed_add_continues_an_unkeyed_meeting_whose_id_and_number_hold() -> None:
    """A meeting added by hand, then the calendar's add for its event continuing it: one meeting, the hand-made one's
    id and number, its points kept, the event's fields and key on it (DR-0621)."""
    ledger = folded(
        meeting(1, subject="Pilot sync", number=5),
        point(2, 1, "Bring the dates"),
        meeting(3, "calendar:E1", subject="Weekly pilot sync", number=5, continues=ulid(1)),
        meeting(4, "calendar:E1", number=9),
        meeting(5, "calendar:E1", number=5, continues=ulid(1)),
        op("set", 6, 3, fields={"where": "Room 2"}),
    )
    m = only(ledger)
    assert (m.id, m.number, m.event, m.subject, m.where, m.adds) == (
        ulid(1), 5, "calendar:E1", "Weekly pilot sync", "Room 2", (ulid(1), ulid(3), ulid(4), ulid(5)),
    )  # fmt: skip
    assert [p.text for p in m.points] == ["Bring the dates"]
    assert ledger.objects.holders == {5: (ulid(1),), 9: (ulid(1),)}
    assert ledger.objects.events == {"calendar:E1": ulid(1)}
    # The same join made again on another clone agrees with the first: no problem.
    assert ledger.problems == ()


def test_a_continues_naming_another_pair_or_no_unkeyed_meeting_is_left_out_and_named() -> None:
    ledger = folded(
        meeting(1, subject="One", number=1),
        meeting(2, subject="Two", number=2),
        meeting(3, "calendar:E1", number=1, continues=ulid(1)),
        meeting(4, "calendar:E1", number=2, continues=ulid(2)),
        meeting(5, "calendar:E2", number=15, continues=ulid(1)),
        meeting(6, "calendar:E3", number=6, continues=ulid(3)),
        meeting(7, number=7, continues=ulid(2)),
        meeting(8, "calendar:E4", number=8, continues=ulid(99)),
    )
    joined = ledger.meetings[ulid(1)]
    assert (joined.event, joined.adds) == ("calendar:E1", (ulid(1), ulid(3), ulid(4)))
    assert ledger.meetings[ulid(2)].event is None
    assert ledger.meetings[ulid(5)].adds == (ulid(5),)
    left = "continues another meeting than its key does, or one another key continues; left out"
    assert messages(ledger) == [
        left,
        left,
        "continues no meeting without an event; its continues is left out",
        "continues a meeting but has no event; its continues is left out",
        "continues no meeting without an event; its continues is left out",
    ]


def test_undoing_the_continuing_add_splits_the_pair_and_undoing_every_add_hides_the_meeting() -> None:
    made = [
        meeting(1, number=5),
        meeting(2, "calendar:E1", number=5, continues=ulid(1)),
        op("undo", 3, 2, of=ulid(2)),
    ]
    ledger = folded(*made)
    assert set(ledger.meetings) == {ulid(1)}
    assert (ledger.meetings[ulid(1)].event, ledger.meetings[ulid(1)].number) == (None, 5)
    assert ledger.objects.folds[ulid(2)].value is None
    gone = folded(meeting(1), op("log", 2, 1, note="x"), op("undo", 3, 1, of=ulid(1)))
    assert (gone.meetings, gone.objects.folds[ulid(1)].value, gone.objects.highest_number) == ({}, None, 1)
    first_undone = folded(
        meeting(1, "calendar:E1"), meeting(2, "calendar:E1", subject="Moved"), op("undo", 3, 1, of=ulid(1))
    )
    m = only(first_undone)
    # The meeting stands on its next add; its id stays its first add's.
    assert (m.id, m.subject, m.number) == (ulid(1), "Moved", 2)


def test_points_fold_in_order_with_one_op_per_change() -> None:
    m = only(folded(
        meeting(1),
        point(2, 1, "Dates", proposed=True),
        point(3, 1, "Budget", item=ulid(40)),
        point_set(4, 1, 2, answer="the 21st", state="answered", proposed=None),
        point_set(5, 1, 3, text="Budget for the pilot", item=None),
        point_set(6, 1, 3, state="dropped"),
        point_set(7, 1, 3, state="dropped"),
        point(8, 1, "Room", proposed=True),
        point_set(9, 1, 8, proposed=True),
    ))  # fmt: skip
    dates, budget, room = m.points
    assert (dates.state, dates.answer, dates.proposed) == ("answered", "the 21st", False)
    assert (budget.text, budget.item, budget.state) == ("Budget for the pilot", None, "dropped")
    assert (room.proposed, room.state, m.open_points()) == (True, "open", [room])
    assert [e.applied for e in m.log] == [True, True, True, True, True, True, False, True, False]
    assert [e.line for e in m.log[1:4]] == [
        "point proposed: Dates", "point added: Budget", "point answer: the 21st, answered, kept",
    ]  # fmt: skip


def test_a_point_set_naming_no_point_of_its_meeting_is_left_out_and_one_naming_a_removed_point_is_idle() -> None:
    ledger = folded(
        meeting(1),
        meeting(2, subject="Another"),
        point(3, 1),
        point(4, 2),
        point_set(5, 1, 4, state="answered"),
        point_set(6, 1, 77, state="answered"),
        op("undo", 7, 1, of=ulid(3)),
        point_set(8, 1, 3, state="answered"),
    )
    assert messages(ledger) == ["names no point of its meeting; left out"] * 2
    m = ledger.meetings[ulid(1)]
    assert (m.points, [e.op.id for e in m.log if not e.applied and e.undone_by is None]) == ([], [ulid(8)])


def test_a_roll_copies_the_point_to_the_next_meeting_and_the_old_one_changes_only_by_an_undo() -> None:
    ledger = folded(
        meeting(1),
        meeting(2, subject="Weekly pilot sync, next"),
        point(3, 1, "Dates", proposed=True, item=ulid(40)),
        *roll(4, 1, 3, 2),
        point_set(6, 1, 3, state="answered"),
    )
    old, new = ledger.meetings[ulid(1)], ledger.meetings[ulid(2)]
    (source,), (copy,) = old.points, new.points
    assert (source.state, source.rolled_to, old.open_points()) == ("rolled", ulid(4), [])
    assert (copy.id, copy.rolled_from, copy.state, copy.stands) == (ulid(4), ulid(3), "open", True)
    assert [e.applied for e in old.log] == [True, True, True, False]
    assert [e.line for e in new.log] == ["added", "point rolled in: The pilot's dates"]
    undone = folded(meeting(1), meeting(2), point(3, 1), *roll(4, 1, 3, 2), op("undo", 6, 1, of=ulid(5)))
    # Its roll undone, the source is open again and its copy, which no roll names, stands nowhere.
    assert undone.meetings[ulid(1)].points[0].state == "open"
    assert (undone.meetings[ulid(2)].points, undone.objects.points[ulid(4)].stands) == ([], False)


def test_two_clones_rolling_one_point_leave_one_copy_and_lose_no_answer() -> None:
    """To one meeting or to two: the copy the winning roll names stands, the other is idle and named, and an answer
    given on the idle copy counts on the one that stands (DR-0623)."""
    for to in (2, 3):
        ledger = folded(
            meeting(1),
            meeting(2, subject="Next"),
            meeting(3, subject="Next, the other clone's"),
            point(4, 1),
            *roll(5, 1, 4, 2),
            *roll(7, 1, 4, to),
            point_set(9, to, 7, answer="the 21st", state="answered"),
        )
        winner, loser = ledger.objects.points[ulid(5)], ledger.objects.points[ulid(7)]
        assert (winner.stands, winner.state, winner.answer) == (True, "answered", "the 21st")
        assert (loser.stands, loser.moved_to) == (False, ulid(5))
        assert ledger.meetings[ulid(1)].points[0].rolled_to == ulid(5)
        assert messages(ledger) == [f"a copy its source's winning roll doesn't name; its ops go to {ulid(5)}"]
        assert [len(m.points) for m in ledger.meetings.values()] == [1, 1, 0]


def test_a_point_the_losing_copy_rolled_on_stands_through_the_winning_copy() -> None:
    """Clone 1 rolls P to c1; clone 2 rolls P to c2, then c2 on to d2. Merged, c1 wins, c2's roll applies to c1, and
    d2, whose from names c2, reads it as c1: the point is open on d2's meeting, not nowhere."""
    ledger = folded(
        meeting(1, subject="Week 1"),
        meeting(2, subject="Week 2"),
        meeting(3, subject="Week 3"),
        point(4, 1, "Dates"),
        *roll(5, 1, 4, 2),
        *roll(7, 1, 4, 2),
        *roll(9, 2, 7, 3),
    )
    c1, c2, d2 = (ledger.objects.points[ulid(k)] for k in (5, 7, 9))
    assert (c1.state, c1.rolled_to, c2.stands, c2.moved_to) == ("rolled", ulid(9), False, ulid(5))
    assert (d2.stands, d2.state, ledger.meetings[ulid(3)].open_points()) == (True, "open", [d2])
    assert [p.id for m in ledger.meetings.values() for p in m.open_points()] == [ulid(9)]


def test_a_point_rolled_into_a_removed_meeting_reads_as_open_and_a_later_roll_wins() -> None:
    """Its copy's meeting removed, here or on another clone, the roll is idle and the point open again, so it is
    never lost; a later roll then wins, and with the meeting brought back the first roll wins again, the later copy's
    answer following it (DR-0623)."""
    made = [
        meeting(1, subject="Week 1"),
        meeting(2, subject="Week 2"),
        meeting(3, subject="Week 3"),
        point(4, 1, "Dates"),
        *roll(5, 1, 4, 2),
        op("undo", 7, 2, of=ulid(2)),
    ]
    removed = folded(*made)
    old = removed.meetings[ulid(1)]
    (source,) = old.points
    assert (source.state, source.rolled_to, old.open_points(), removed.problems) == ("open", None, [source], ())
    assert [e.applied for e in old.log] == [True, True, False]
    again = [*made, *roll(8, 1, 4, 3), point_set(10, 3, 8, answer="the 21st", state="answered")]
    rolled = folded(*again)
    assert (rolled.objects.points[ulid(4)].rolled_to, [p.id for p in rolled.meetings[ulid(3)].points]) == (
        ulid(8), [ulid(8)],
    )  # fmt: skip
    back = folded(*again, op("undo", 11, 2, of=ulid(7)))
    first, later = back.objects.points[ulid(5)], back.objects.points[ulid(8)]
    assert (first.stands, first.answer, later.stands, later.moved_to) == (True, "the 21st", False, ulid(5))
    assert [p.id for m in back.meetings.values() for p in m.points] == [ulid(4), ulid(5)]


def test_a_roll_an_import_couldn_t_pair_lands_on_its_meeting_while_that_stands() -> None:
    made = [meeting(1), meeting(2, subject="Next"), point(3, 1), point_set(4, 1, 3, state="rolled", to=ulid(2))]
    (rolled,) = folded(*made).meetings[ulid(1)].points
    assert (rolled.state, rolled.rolled_to) == ("rolled", ulid(2))
    assert folded(*made, op("undo", 5, 2, of=ulid(2))).meetings[ulid(1)].points[0].state == "open"


def test_a_copy_whose_source_stands_nowhere_is_idle_and_a_from_naming_a_meeting_or_nothing_is_a_root() -> None:
    ledger = folded(
        meeting(1),
        meeting(2, subject="Next"),
        point(3, 1),
        *roll(4, 1, 3, 2),
        point(6, 2, "From an import", **{"from": ulid(1)}),
        point(7, 2, "From nowhere", **{"from": ulid(88)}),
        point(8, 2, "From later", **{"from": ulid(9)}),
        point(9, 2, "Later"),
        op("undo", 10, 1, of=ulid(1)),
    )
    # The source's meeting removed, its copy stands nowhere; the rest are points of their own.
    assert ledger.objects.points[ulid(4)].stands is False
    assert [(p.text, p.rolled_from) for p in ledger.meetings[ulid(2)].points] == [
        ("From an import", ulid(1)), ("From nowhere", None), ("From later", None), ("Later", None),
    ]  # fmt: skip
    assert messages(ledger) == ["its from names no earlier point and no meeting; read as none"] * 2


def test_an_archive_stands_while_no_point_is_open_and_a_set_after_it_is_idle() -> None:
    m = only(folded(
        meeting(1), op("set", 2, 1, fields={"where": "Room 1"}), op("archive", 3, 1),
        op("set", 4, 1, fields={"where": "Room 2"}), op("archive", 5, 1),
    ))  # fmt: skip
    assert (m.archived, m.where, [e.applied for e in m.log]) == (True, "Room 1", [True, True, True, False, False])
    unarchived = only(folded(
        meeting(1), op("archive", 2, 1), op("set", 3, 1, fields={"where": "Room 2"}), op("undo", 4, 1, of=ulid(2)),
    ))  # fmt: skip
    assert (unarchived.archived, unarchived.where) == (False, "Room 2")
    still = only(folded(meeting(1), point(2, 1), op("archive", 3, 1), op("set", 4, 1, fields={"where": "Room 3"})))
    assert (still.archived, still.where, still.log[2].applied) == (False, "Room 3", False)


def test_a_line_sorting_before_its_add_a_set_that_doesnt_fit_and_a_bad_undo_are_named() -> None:
    ledger = folded(
        op("log", 1, 2, note="early"),
        meeting(2),
        op("set", 3, 2, fields={"start": "Monday"}),
        op("set", 4, 2, fields={"attendees": "Ada"}),
        op("undo", 5, 2, of=ulid(9)),
        op("stream", 6),
        op("set", 7, 6, fields={"deadline": "soon"}),
    )
    assert messages(ledger) == [
        "a set whose fields don't hold what a stream's hold; left out",
        "sorts before its item's add; left out",
        "a set whose fields don't hold what a meeting's hold; left out",
        "a set whose fields don't hold what a meeting's hold; left out",
        "an undo of an op that isn't an earlier one of its item, or of a renumber; left out",
    ]
    assert only(ledger).start.isoformat(timespec="minutes") == "2026-10-19T09:00+02:00"


def test_a_stream_is_keyed_by_its_name_and_updated_on_its_latest_change() -> None:
    later = DAY + timedelta(days=3)
    ledger = folded(
        op("stream", 1, fields={**STREAM, "where": "kick-off done"}),
        op("set", 2, 1, fields={"next": "send the dates", "deadline": "2026-11-02", "status": None}, day=later),
        op("set", 3, 1, fields={"next": "send the dates"}, day=later + timedelta(days=1)),
        op("stream", 4, fields={**STREAM, "status": "waiting"}, day=later + timedelta(days=2)),
        op("log", 5, 1, note="quiet week"),
        op("stream", 7, fields={"name": "other", "status": "closed"}),
        op("undo", 8, 7, of=ulid(7)),
    )
    (s,) = ledger.streams.values()
    assert (s.id, s.name, s.status, s.where, s.next, s.deadline, s.adds) == (
        ulid(1), "pilot", "waiting", "kick-off done", "send the dates", date(2026, 11, 2), (ulid(1), ulid(4)),
    )  # fmt: skip
    assert s.updated == later + timedelta(days=2)
    assert [e.applied for e in s.log] == [True, True, False, True, True]
    assert (ledger.objects.names, ledger.objects.folds[ulid(7)].value) == ({"pilot": ulid(1)}, None)
    assert describe(op("stream", 1)) == "added"


def test_a_second_text_of_a_meeting_s_add_is_idle_and_a_stream_s_undone_set_is_left_out() -> None:
    twin = meeting(1, where="Room 1")
    other = meeting(1, where="Room 2")
    m = only(fold([*lines(twin), *lines(other)]))
    first = min(twin, other, key=lambda o: o.text())
    assert m.where == (first.fields or {})["where"]
    assert [e.applied for e in m.log] == [True, False]
    ledger = folded(op("stream", 1), op("set", 2, 1, fields={"where": "x"}), op("undo", 3, 1, of=ulid(2)))
    (s,) = ledger.streams.values()
    assert (s.where, [(e.applied, e.undone_by) for e in s.log]) == (
        None,
        [(True, None), (False, ulid(3)), (True, None)],
    )


def test_a_renumber_on_the_key_s_side_before_a_join_is_a_number_the_joined_meeting_held() -> None:
    """An event's meeting renumbered after a clash, then continuing an unkeyed one: the joined meeting keeps the
    continued one's number, and the renumber's still names it (DR-0621). A renumber of the joined meeting claims."""
    made = [
        meeting(1, number=7),
        meeting(2, subject="Another", number=5),
        meeting(3, "calendar:E1", number=5),
        op("renumber", 4, 3, number=8),
        meeting(5, "calendar:E1", number=7, continues=ulid(1)),
    ]
    ledger = folded(*made)
    m = ledger.meetings[ulid(1)]
    assert (m.number, m.claimed, m.adds, ledger.problems) == (7, 7, (ulid(1), ulid(3), ulid(5)), ())
    assert ledger.objects.holders == {5: (ulid(2), ulid(1)), 7: (ulid(1),), 8: (ulid(1),)}
    clash = folded(meeting(0, subject="First", number=7), *made, op("renumber", 6, 1, number=9))
    assert (clash.meetings[ulid(1)].number, clash.objects.holders[7], clash.objects.holders[8]) == (
        9, (ulid(0), ulid(1)), (ulid(1),),
    )  # fmt: skip


def test_an_op_its_object_doesn_t_take_and_a_roll_to_nothing_are_left_out_and_named() -> None:
    """A point on an item, a triage on a meeting, an archive on a stream: each is left out and named (DR-0620). So is
    a roll whose to names no point and no meeting, its point staying open, and a from naming a stream reads as none."""
    ledger = folded(
        op("add", 1),
        point(2, 1, "On an item"),
        point_set(3, 1, 2, answer="x"),
        meeting(4),
        op("triage", 5, 4, verdict="me"),
        op("stream", 6),
        op("archive", 7, 6),
        point(8, 4, "Dates"),
        point_set(9, 4, 8, state="rolled", to=ulid(77)),
        point(10, 4, "From a stream", **{"from": ulid(6)}),
    )
    assert messages(ledger) == [
        "an item takes no point; left out",
        "an item takes no point_set; left out",
        "a stream takes no archive; left out",
        "a meeting takes no triage; left out",
        "a roll whose to names no point and no meeting; left out",
        "its from names no earlier point and no meeting; read as none",
    ]
    m = ledger.meetings[ulid(4)]
    assert ([p.text for p in m.open_points()], [e.op.op for e in m.log]) == (
        ["Dates", "From a stream"], ["meeting", "point", "point"],
    )  # fmt: skip
    assert ([e.op.op for e in ledger.items[ulid(1)].log], len(ledger.streams[ulid(6)].log)) == (["add"], 1)


def test_two_meetings_claiming_one_number_wait_for_a_renumber() -> None:
    ledger = folded(meeting(1, number=3), meeting(2, subject="Other", number=3), op("renumber", 3, 2, number=4))
    assert {m.id: m.number for m in ledger.meetings.values()} == {ulid(1): 3, ulid(2): 4}
    clash = folded(meeting(1, number=3), meeting(2, subject="Other", number=3))
    assert [m.id for m in clash.objects.clashes()] == [ulid(2)]


def test_a_point_op_says_what_it_did_in_a_few_words() -> None:
    assert describe_point(point(2, 1, "Dates", **{"from": ulid(1)})) == "point rolled in: Dates"
    assert describe_point(point_set(3, 1, 2, text="New", item=ulid(40), proposed=True)) == (
        "point text: New, item linked, proposed"
    )
    assert describe_point(point_set(3, 1, 2, answer=None, item=None, state="rolled", to=ulid(4))) == (
        "point answer: -, item cleared, rolled"
    )
    assert isinstance(Point(id="x", meeting="y", text="z", added_by="user", day=DAY), Point)
    assert MEETING["subject"] == only(folded(meeting(1))).subject


def snapshot(ledger: Ledger) -> list[Any]:
    out: list[Any] = [
        (m.id, m.number, m.event, m.subject, m.where, m.archived, m.adds,
         tuple((p.id, p.state, p.answer, p.rolled_to) for p in m.points),
         tuple((e.op.id, e.applied, e.undone_by) for e in m.log))
        for m in sorted(ledger.meetings.values(), key=lambda m: m.id)
    ]  # fmt: skip
    out.append(sorted(ledger.objects.holders.items()))
    out.append(sorted((p.id, p.stands, p.moved_to) for p in ledger.objects.points.values()))
    return out


def history(seed: int) -> list[Op]:
    """Ninety ops at random: meetings with and without an event, some continuing one, points, answers, rolls to any
    meeting, sets and undos of any op."""
    rng = random.Random(seed)  # noqa: S311
    made: list[Op] = []
    keys: dict[int, str | None] = {}
    points: list[tuple[int, int]] = []
    for n in range(2, 182, 2):
        r = rng.random()
        if not keys or r < 0.18:
            event = rng.choice([None, "calendar:E1", "calendar:E2", "calendar:E3"])
            alone = [k for k, key in keys.items() if key is None]
            extra = {"continues": ulid(rng.choice(alone))} if event and alone and rng.random() < 0.3 else {}
            made.append(meeting(n, event, number=rng.randint(1, 6), subject=f"S{n}", **extra))
            keys[n] = event
        elif r < 0.4:
            on = rng.choice(list(keys))
            made.append(point(n, on, f"P{n}"))
            points.append((n, on))
        elif r < 0.6 and points:
            which, on = rng.choice(points)
            made.append(point_set(n, on, which, answer=f"a{n}", state="answered"))
        elif r < 0.75 and points:
            which, on = rng.choice(points)
            to = rng.choice(list(keys))
            made += roll(n, on, which, to)
            points.append((n, to))
        elif r < 0.85:
            made.append(op("set", n, rng.choice(list(keys)), fields={"where": f"Room {n}"}))
        else:
            target = rng.choice(made)
            made.append(op("undo", n, ops.decode(target.item) - ops.decode(ulid(0)), of=target.id))
    return made


@pytest.mark.parametrize("seed", range(8))
def test_any_order_of_the_lines_with_lines_twice_folds_to_one_ledger(seed: int) -> None:
    """Keys, joins, points, rolls and undos fold alike whatever order the files hold the lines in, a line read twice
    being one op (DR-0612)."""
    texts = [(ops.rel_of(o), o.text()) for o in history(seed)]

    def read(order: list[tuple[str, str]]) -> Ledger:
        return fold(Line(ops.read_op(t), t, rel, k) for k, (rel, t) in enumerate(order, start=1))

    base = snapshot(read(texts))
    rng = random.Random(seed + 7)  # noqa: S311
    for _ in range(6):
        shuffled = texts + rng.sample(texts, k=len(texts) // 3)
        rng.shuffle(shuffled)
        assert snapshot(read(shuffled)) == base
