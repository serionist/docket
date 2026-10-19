"""The writer on meetings, points and streams (DR-0620 to DR-0627): adds, the event's updates and a continued meeting,
points one change at a time, a point turned into an item, streams by name, rollover, undo, and the text rules."""

from __future__ import annotations

import gc
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import commit_config, empty_vault, make_config, sh
from ledger_kit import ADD_FIELDS, MEETING, NOW

from docket.index.ledger import Held
from docket.ledger import ops, store
from docket.ledger.fold import Ledger, fold
from docket.ledger.meetings import Meeting, fold_objects
from docket.ledger.ops import Op
from docket.ledger.store import LedgerError, write
from docket.vault.config import RedactionPattern, VaultConfig
from docket.vault.vault import Vault

# Forced: these tests add the one ask more than once on purpose, past the look-alike check (DR-0642).
ADD: dict[str, Any] = {"op": "add", "fields": dict(ADD_FIELDS), "force": True}
PILOT: dict[str, Any] = {"op": "stream", "fields": {"name": "pilot", "status": "active"}}


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return empty_vault(tmp_path, make_config())


def ledger(vault: Vault) -> Ledger:
    return fold(ops.read_all(vault.root).lines)


def meeting(vault: Vault, number: int) -> Meeting:
    return next(m for m in ledger(vault).meetings.values() if m.number == number)


def message(vault: Vault) -> str:
    return sh(vault.root, "git", "log", "-1", "--format=%B").strip()


def later(minutes: int) -> datetime:
    return NOW + timedelta(minutes=minutes)


def add_meeting(**fields: Any) -> dict[str, Any]:
    return {"op": "meeting", "fields": {**MEETING, **fields}}


def laptop(vault: Vault, kind: str, item: str, **keys: Any) -> Op:
    """An op another machine wrote and the user's git merged in: on the laptop, above every op here. An add, with
    item "", names itself."""
    (op_id,) = ops.mint(ledger(vault).highest_id, later(30), 1)
    o = Op(schema_version=1, id=op_id, item=item or op_id, op=kind, day=date(2026, 10, 18), machine="laptop",
           by="user", via="page", **keys)  # fmt: skip
    path = vault.root / ops.rel_of(o)
    path.write_text((path.read_text(encoding="utf-8") if path.exists() else "") + o.text() + "\n", encoding="utf-8",
                    newline="\n")  # fmt: skip
    vault.commit([ops.rel_of(o)], "Merge the laptop's ops")
    return o


def test_a_meeting_is_added_with_its_number_and_its_times_carry_their_offset(vault: Vault) -> None:
    written = write(vault, [{"op": "meeting", "fields": {
        "subject": " Weekly  pilot sync ", "start": "2026-10-19 09:00", "end": "2026-10-19T09:30:15+02:00",
        "attendees": ["Ada Example", "  Ben  Example ", " "], "organiser": "Ada Example", "event": "calendar:E1",
        "series": "calendar:S1", "response": "accepted", "where": "Room 1", "notes": "bring\r\nthe dates\n",
        "outcome": None, "stream": "", "continues": None,
    }}], now=NOW)  # fmt: skip
    (add,) = written.ops
    assert (add.op, add.number, add.item) == ("meeting", 1, add.id)
    assert add.fields == {
        "subject": "Weekly pilot sync", "start": "2026-10-19T09:00+02:00", "end": "2026-10-19T09:30+02:00",
        "event": "calendar:E1", "series": "calendar:S1", "organiser": "Ada Example", "where": "Room 1",
        "response": "accepted", "attendees": ["Ada Example", "Ben Example"], "notes": "bring\nthe dates",
    }  # fmt: skip
    assert (written.numbers, message(vault)) == ({add.id: 1}, "Ledger: meeting M-0001 on test-box")
    (winter, west) = write(vault, [add_meeting(start="2026-11-02T09:00", end="2026-11-02T10:00"),
                                   add_meeting(start="2026-10-19T09:00-05:00", end="2026-10-19T10:00-05:00")],
                           now=later(1)).ops  # fmt: skip
    assert (winter.number, winter.fields["start"], west.fields["end"]) == (  # type: ignore[index]
        2, "2026-11-02T09:00+01:00", "2026-10-19T10:00-05:00",
    )  # fmt: skip
    m = meeting(vault, 1)
    assert (m.subject, m.attendees, m.day) == ("Weekly pilot sync", ("Ada Example", "Ben Example"), date(2026, 10, 19))


def test_a_meeting_with_an_event_the_ledger_holds_sets_what_changed_on_the_add_carrying_it(vault: Vault) -> None:
    (add,) = write(vault, [add_meeting(event="calendar:E1", where="Room 1")], now=NOW).ops
    moved = add_meeting(event="calendar:E1", where="Room 2", start="2026-10-19T10:00+02:00",
                        end="2026-10-19T10:30+02:00", continues="")  # fmt: skip
    (set_op,) = write(vault, [moved], now=later(1)).ops
    assert (set_op.op, set_op.item, set_op.fields) == ("set", add.id, {
        "start": "2026-10-19T10:00+02:00", "end": "2026-10-19T10:30+02:00", "where": "Room 2",
    })  # fmt: skip
    assert write(vault, [moved], now=later(2)).ops == ()
    write(vault, [{"op": "archive", "item": "M-0001"}], now=later(3))
    # A set on an archived meeting is idle: the writer writes nothing and refuses nothing, so it checks no field of
    # it, and an event's update with an end before its start still fails no write (DR-0624).
    for idle in (
        add_meeting(event="calendar:E1", where="Room 3"),
        add_meeting(event=" calendar:E1 ", end="2026-10-19T08:00+02:00"),
        {"op": "meeting", "fields": {"event": "calendar:E1", "subject": None}},
        {"op": "set", "item": "M-0001", "fields": {"where": "Room 3"}},
        {"op": "set", "item": "M-0001", "fields": {"start": "Monday"}},
    ):
        assert write(vault, [idle], now=later(4)).ops == ()
    assert meeting(vault, 1).where == "Room 2"
    assert write(vault, [add_meeting(event="  ")], now=later(6)).ops[0].number == 2


def test_a_keyed_add_continues_an_unkeyed_meeting_and_its_undo_splits_the_pair(vault: Vault) -> None:
    write(vault, [add_meeting(subject="Pilot sync"), {"op": "point", "item": "M-0001", "fields": {"text": "Dates"}}],
          now=NOW)  # fmt: skip
    hand = meeting(vault, 1)
    (join,) = write(vault, [add_meeting(event="calendar:E1", continues="M-0001")], now=later(1)).ops
    assert (join.number, join.fields["continues"]) == (1, hand.id)  # type: ignore[index]
    joined = meeting(vault, 1)
    assert (joined.id, joined.event, joined.subject, [p.text for p in joined.points]) == (
        hand.id, "calendar:E1", "Weekly pilot sync", ["Dates"],
    )  # fmt: skip
    # The calendar's next update names the add carrying the event, so it follows the event if the join is split.
    (update,) = write(vault, [add_meeting(event="calendar:E1", where="Room 2")], now=later(2)).ops
    assert update.item == join.id
    write(vault, [add_meeting(subject="Other")], now=later(3))
    for change, why in [
        (add_meeting(continues="M-0002"), "continues needs an event"),
        (add_meeting(event="calendar:E2", continues="M-0001"), "M-0001 has an event; only a meeting without one"),
        (add_meeting(event="calendar:E1", continues="M-0002"), "that event continues M-0001 already"),
    ]:
        with pytest.raises(LedgerError, match=why):
            write(vault, [change], now=later(4))
    write(vault, [{"op": "undo", "of": join.id}], now=later(5))
    split = ledger(vault)
    assert (split.meetings[hand.id].event, split.meetings[hand.id].number, split.meetings[hand.id].where) == (
        None, 1, None,
    )  # fmt: skip
    assert split.objects.events == {}


def test_removing_a_meeting_undoes_every_add_of_it(vault: Vault) -> None:
    (add,) = write(vault, [add_meeting(event="calendar:E1")], now=NOW).ops
    other = laptop(vault, "meeting", "", number=7, fields={**MEETING, "event": "calendar:E1"})
    assert ledger(vault).meetings[add.id].adds == (add.id, other.id)
    undos = write(vault, [{"op": "undo", "of": add.id, "note": "not a meeting of mine"}], now=later(40)).ops
    assert [(o.of, o.note) for o in undos] == [(add.id, "not a meeting of mine"), (other.id, None)]
    assert (ledger(vault).meetings, message(vault)) == ({}, "Ledger: undo M-0001 on test-box")
    with pytest.raises(LedgerError, match=f"{add.id} was undone"):
        write(vault, [{"op": "log", "item": add.id, "note": "x"}], now=later(41))


def test_a_meeting_clash_is_renumbered_and_a_number_two_meetings_held_names_both(vault: Vault) -> None:
    write(vault, [add_meeting(event="calendar:E1")], now=NOW)
    theirs = laptop(vault, "meeting", "", number=1, fields={**MEETING, "event": "calendar:E2"})
    assert [m.id for m in ledger(vault).objects.clashes()] == [theirs.id]
    (renumber, log) = write(vault, [{"op": "log", "item": theirs.id, "note": "first after the merge"}],
                            now=later(40)).ops  # fmt: skip
    assert (renumber.op, renumber.item, renumber.number, log.batch) == ("renumber", theirs.id, 2, renumber.id)
    assert message(vault) == "Ledger: renumber M-0002, log M-0002 on test-box"
    with pytest.raises(LedgerError, match="M-0001 has been held by more than one meeting; name it by its id"):
        write(vault, [{"op": "log", "item": "M-0001", "note": "x"}], now=later(41))
    # A later add of one event, numbered on another clone, holds its number for that meeting too (DR-0621).
    later_add = laptop(vault, "meeting", "", number=5, fields={**MEETING, "event": "calendar:E1"})
    write(vault, [{"op": "log", "item": "M-0005", "note": "by its other number"}], now=later(60))
    assert ledger(vault).objects.owner[later_add.id] != later_add.id
    assert write(vault, [add_meeting(subject="Next")], now=later(61)).ops[0].number == 6


def test_streams_are_added_and_set_by_name_and_items_name_one_once_one_exists(vault: Vault) -> None:
    write(vault, [ADD], now=NOW)
    (stream,) = write(vault, [PILOT], now=later(1)).ops
    assert (stream.op, stream.fields, message(vault)) == (
        "stream", {"name": "pilot", "status": "active"}, "Ledger: stream pilot on test-box",
    )  # fmt: skip
    (set_op,) = write(vault, [{"op": "stream", "fields": {"name": "pilot", "where": "dates sent", "next": "-"}}],
                      now=later(2)).ops  # fmt: skip
    assert (set_op.item, set_op.fields) == (stream.id, {"where": "dates sent"})
    write(vault, [{"op": "set", "item": "pilot", "fields": {"deadline": "2026-11-02", "status": "waiting"}},
                  {"op": "log", "item": "pilot", "note": "quiet week"}], now=later(3))  # fmt: skip
    assert write(vault, [{"op": "set", "item": "pilot", "fields": {"status": "waiting"}}], now=later(4)).ops == ()
    (s,) = ledger(vault).streams.values()
    assert (s.status, s.where, s.deadline) == ("waiting", "dates sent", date(2026, 11, 2))
    write(vault, [ADD], now=later(5))
    with pytest.raises(LedgerError, match="no stream other; add it first, or name one of pilot"):
        write(vault, [{"op": "add", "fields": {**ADD_FIELDS, "stream": "other"}}], now=later(6))
    assert write(vault, [{"op": "set", "item": "pilot", "fields": {"where": "x"}}], now=later(7)).numbers == {}


def test_a_meeting_takes_materials_and_its_attendees_and_a_stream_is_named_by_its_id_too(vault: Vault) -> None:
    (stream,) = write(vault, [PILOT], now=NOW).ops
    (log,) = write(vault, [{"op": "log", "item": stream.id, "note": "by its id"}], now=later(1)).ops
    assert log.item == stream.id
    write(vault, [add_meeting(attendees=["Ada Example"])], now=later(2))
    (material,) = write(vault, [{"op": "material", "item": "M-0001", "ref": "notes/pilot.md", "label": "notes"}],
                        now=later(3)).ops  # fmt: skip
    assert (material.ref, material.label) == ("notes/pilot.md", "notes")
    assert write(vault, [{"op": "set", "item": "M-0001", "fields": {"attendees": ["Ada Example"]}}],
                 now=later(4)).ops == ()  # fmt: skip
    (changed,) = write(vault, [{"op": "set", "item": "M-0001", "fields": {"attendees": None}}], now=later(5)).ops
    assert changed.fields == {"attendees": None}
    m = meeting(vault, 1)
    assert (m.attendees, [x.ref for x in m.materials]) == ((), ["notes/pilot.md"])


def test_an_item_hangs_on_a_meeting_by_its_id(vault: Vault) -> None:
    (m,) = write(vault, [add_meeting()], now=NOW).ops
    (add,) = write(vault, [{"op": "add", "fields": {**ADD_FIELDS, "meeting": "M-0001"}}], now=later(1)).ops
    assert add.fields["meeting"] == m.id  # type: ignore[index]
    (clear,) = write(vault, [{"op": "set", "item": "A-0001", "fields": {"meeting": None}}], now=later(2)).ops
    assert clear.fields == {"meeting": None}
    assert next(iter(ledger(vault).items.values())).meeting is None


def test_points_change_one_op_at_a_time_with_what_todo_py_infers_written_out(vault: Vault) -> None:
    write(vault, [
        ADD,
        add_meeting(),
        {"op": "point", "item": "M-0001", "fields": {"text": " Dates ", "proposed": True}},
        {"op": "point", "item": "M-0001", "fields": {"text": "Budget", "item": "A-0001"}},
        {"op": "point", "item": "M-0001", "fields": {"text": "Room", "proposed": True}},
    ], now=NOW)  # fmt: skip
    dates, budget, room = meeting(vault, 1).points
    assert (dates.text, dates.proposed, budget.item) == ("Dates", True, ledger(vault).items.popitem()[0])
    (answer,) = write(vault, [{"op": "point_set", "item": "M-0001", "point": 1, "fields": {"answer": "the 21st"}}],
                      now=later(1)).ops  # fmt: skip
    assert (answer.point, answer.fields) == (dates.id, {"answer": "the 21st", "state": "answered", "proposed": None})
    (unlink,) = write(vault, [{"op": "point_set", "item": "M-0001", "point": budget.id,
                               "fields": {"item": "-", "state": "dropped", "proposed": False, "answer": None}}],
                      now=later(2)).ops  # fmt: skip
    assert unlink.fields == {"item": None, "state": "dropped"}
    (kept,) = write(vault, [{"op": "keep", "item": "M-0001"}], now=later(3)).ops
    assert (kept.point, kept.fields) == (room.id, {"proposed": None})
    assert write(vault, [{"op": "point_set", "item": "M-0001", "point": "#3", "fields": {"text": "Room"}}],
                 now=later(4)).ops == ()  # fmt: skip
    (relink,) = write(vault, [{"op": "point_set", "item": "M-0001", "point": "2",
                               "fields": {"item": "A-0001", "proposed": True}}], now=later(5)).ops  # fmt: skip
    assert relink.fields == {"item": budget.item, "proposed": True}
    assert [p.state for p in meeting(vault, 1).points] == ["answered", "dropped", "open"]


def test_a_point_becomes_an_item_on_its_meeting(vault: Vault) -> None:
    write(vault, [PILOT, add_meeting(stream="pilot", organiser="Ben Example")], now=NOW)
    write(vault, [{"op": "point", "item": "M-0001", "fields": {"text": "Ada: send the dates", "proposed": True}},
                  {"op": "point", "item": "M-0001", "fields": {"text": "Budget"}}], now=later(1))  # fmt: skip
    m = meeting(vault, 1)
    (add, link) = write(vault, [{"op": "point_item", "item": "M-0001", "point": 1}], now=later(2)).ops
    assert add.fields == {"kind": "waiting", "title": "Ada: send the dates", "who": "Ada", "stream": "pilot",
                          "source": f"meeting:{m.id}", "meeting": m.id}  # fmt: skip
    assert (add.note, link.point, link.fields) == ("from a talking point of M-0001", m.points[0].id,
                                                    {"item": add.id, "proposed": None})  # fmt: skip
    (task, _) = write(vault, [{"op": "point_item", "item": "M-0001", "point": 2, "note": "after the call"}],
                      now=later(3)).ops  # fmt: skip
    assert (task.fields["kind"], task.fields["who"], task.note) == ("task", "Ben Example", "after the call")  # type: ignore[index]
    write(vault, [add_meeting(subject="No organiser"), {"op": "point", "item": "M-0002", "fields": {"text": "x"}}],
          now=later(4))  # fmt: skip
    with pytest.raises(LedgerError, match="M-0002 has no stream; give the item one"):
        write(vault, [{"op": "point_item", "item": "M-0002", "point": 1}], now=later(5))
    (mine, _) = write(vault, [{"op": "point_item", "item": "M-0002", "point": 1,
                               "fields": {"stream": "pilot", "kind": "decision"}}], now=later(6)).ops  # fmt: skip
    assert (mine.fields["who"], mine.fields["kind"]) == ("me", "decision")  # type: ignore[index]
    with pytest.raises(LedgerError, match="that point is on an item already"):
        write(vault, [{"op": "point_item", "item": "M-0002", "point": 1}], now=later(7))


MONDAY = "2026-10-19T09:00+02:00"


def at(day: int, hour: int) -> str:
    return f"2026-10-{day:02d}T{hour:02d}:00+02:00"


def slot(day: int, **fields: Any) -> dict[str, Any]:
    """A one-hour meeting called Sync at 09:00 local on that day of October."""
    return {"op": "meeting", "fields": {"subject": "Sync", "start": at(day, 9), "end": at(day, 10), **fields}}


def test_rollover_rolls_each_due_meeting_s_open_points_to_the_next_of_its_series(vault: Vault) -> None:
    """todo.py's rules (DR-0624): a cancelled meeting rolls at once, one that is over with an outcome rolls, any
    other a day after its end; a cancelled or declined meeting is never the next one; a point with nowhere to go
    stays open. Folded again later, the meetings are the same."""
    sync = {"subject": "Weekly pilot sync"}
    write(vault, [
        ADD,
        {"op": "meeting", "fields": {**sync, "start": at(19, 9), "end": at(19, 10), "outcome": "agreed"}},
        {"op": "meeting", "fields": {**sync, "start": at(26, 9), "end": at(26, 10), "response": "cancelled"}},
        {"op": "meeting", "fields": {**sync, "start": at(27, 9), "end": at(27, 10), "response": "declined"}},
        {"op": "meeting", "fields": {"subject": "Weekly pilot sync, Q4", "start": at(28, 9), "end": at(28, 10)}},
        {"op": "meeting", "fields": {"subject": "Budget", "start": at(19, 11), "end": at(19, 12),
                                     "response": "cancelled"}},
        {"op": "meeting", "fields": {"subject": "Hiring", "start": at(19, 13), "end": at(19, 14)}},
        {"op": "meeting", "fields": {"subject": "Hiring", "start": at(23, 13), "end": at(23, 14)}},
        {"op": "point", "item": "M-0001", "fields": {"text": "Dates", "item": "A-0001", "proposed": True}},
        {"op": "point", "item": "M-0001", "fields": {"text": "Answered"}},
        {"op": "point_set", "item": "M-0001", "point": 2, "fields": {"answer": "yes"}},
        {"op": "point", "item": "M-0002", "fields": {"text": "Cancelled ahead"}},
        {"op": "point", "item": "M-0005", "fields": {"text": "Nowhere to go"}},
        {"op": "point", "item": "M-0006", "fields": {"text": "Not a day yet"}},
    ], now=NOW)  # fmt: skip
    # Monday 19:00 local: the sync is over with an outcome and next week's is cancelled, so both roll to the Q4 sync
    # past the cancelled and the declined one; Budget, cancelled, has no next; Hiring ended five hours ago.
    monday_evening = datetime(2026, 10, 19, 17, tzinfo=UTC)
    rolled = write(vault, [{"op": "rollover", "note": "end of the sync"}], now=monday_evening, by="sync").ops
    m = {meeting.number: meeting for meeting in ledger(vault).meetings.values()}
    assert [(o.op, o.item) for o in rolled] == [
        ("point", m[4].id), ("point_set", m[1].id), ("point", m[4].id), ("point_set", m[2].id),
    ]  # fmt: skip
    copy, dates = rolled[0], m[1].points[0]
    assert copy.fields == {"text": "Dates", "from": dates.id, "item": dates.item, "proposed": True}
    assert (rolled[1].fields, rolled[1].note, rolled[1].by) == (
        {"state": "rolled", "to": copy.id}, "end of the sync", "sync",
    )  # fmt: skip
    assert [p.text for p in m[4].open_points()] == ["Dates", "Cancelled ahead"]
    assert [p.state for p in m[1].points] == ["rolled", "answered"]
    assert [p.text for p in m[5].open_points()] == ["Nowhere to go"]
    assert [p.text for p in m[6].open_points()] == ["Not a day yet"]
    tuesday = datetime(2026, 10, 20, 12, tzinfo=UTC)
    (hiring_copy, hiring_roll) = write(vault, [{"op": "rollover"}], now=tuesday).ops
    assert (hiring_copy.item, hiring_roll.item) == (m[7].id, m[6].id)
    assert write(vault, [{"op": "rollover"}], now=tuesday).ops == ()
    with pytest.raises(LedgerError, match="that point rolled on"):
        write(vault, [{"op": "point_set", "item": "M-0001", "point": 1, "fields": {"state": "dropped"}}], now=tuesday)
    with pytest.raises(LedgerError, match="that point rolled on"):
        write(vault, [{"op": "point_item", "item": "M-0001", "point": 1}], now=tuesday)
    assert write(vault, [{"op": "keep", "item": "M-0001"}], now=tuesday).ops == ()
    again = fold(ops.read_all(vault.root).lines)
    assert {m.id: [(p.id, p.state) for p in m.points] for m in again.meetings.values()} == {
        m.id: [(p.id, p.state) for p in m.points] for m in ledger(vault).meetings.values()
    }


def test_the_next_meeting_is_the_same_series_first(vault: Vault) -> None:
    write(vault, [
        {"op": "meeting", "fields": {"subject": "Pilot", "start": at(19, 9), "end": at(19, 10), "series": "s:1",
                                     "response": "cancelled"}},
        {"op": "meeting", "fields": {"subject": "Pilot", "start": at(20, 9), "end": at(20, 10), "series": "s:2"}},
        {"op": "meeting", "fields": {"subject": "Renamed", "start": at(21, 9), "end": at(21, 10), "series": "s:1"}},
        {"op": "meeting", "fields": {"subject": "Weekly pilot sync with Ada", "start": at(22, 9), "end": at(22, 10),
                                     "response": "cancelled", "organiser": "Ada Example"}},
        {"op": "meeting", "fields": {"subject": "Weekly pilot sync with Ada, moved", "start": at(23, 9),
                                     "end": at(23, 10), "organiser": "Ben Example"}},
        {"op": "meeting", "fields": {"subject": "Weekly pilot sync with Ada, moved", "start": at(24, 9),
                                     "end": at(24, 10), "organiser": "Ada Example"}},
        {"op": "meeting", "fields": {"subject": "Lone", "start": at(25, 9), "end": at(25, 10), "series": "s:3",
                                     "response": "cancelled"}},
        {"op": "meeting", "fields": {"subject": "Lone", "start": at(26, 9), "end": at(26, 10), "series": "s:4"}},
        {"op": "meeting", "fields": {"subject": "Lone", "start": at(27, 9), "end": at(27, 10)}},
        {"op": "point", "item": "M-0001", "fields": {"text": "a"}},
        {"op": "point", "item": "M-0004", "fields": {"text": "b"}},
        {"op": "point", "item": "M-0007", "fields": {"text": "c"}},
    ], now=NOW)  # fmt: skip
    rolled = write(vault, [{"op": "rollover"}], now=NOW).ops
    by_number = {m.id: m.number for m in ledger(vault).meetings.values()}
    assert [by_number[o.item] for o in rolled if o.op == "point"] == [3, 6, 9]


def test_undoing_a_roll_or_its_copy_undoes_both_and_is_refused_once_the_copy_rolled_on(vault: Vault) -> None:
    write(vault, [
        slot(19, response="cancelled"),
        slot(20, outcome="done"),
        slot(21),
        {"op": "point", "item": "M-0001", "fields": {"text": "Dates"}},
    ], now=NOW)  # fmt: skip
    # The cancelled meeting rolls at once to the next, which is over with an outcome and rolls on in the same pass.
    (copy, roll, copy2, roll2) = write(vault, [{"op": "rollover"}], now=datetime(2026, 10, 20, 12, tzinfo=UTC)).ops
    for of in (roll.id, copy.id):
        with pytest.raises(LedgerError, match="its copy has rolled on since; undo that roll first"):
            write(vault, [{"op": "undo", "of": of}], now=later(2))
    undos = write(vault, [{"op": "undo", "of": copy2.id}], now=later(3)).ops
    assert [o.of for o in undos] == [copy2.id, roll2.id]
    undos = write(vault, [{"op": "undo", "of": roll.id}], now=later(4)).ops
    assert [o.of for o in undos] == [roll.id, copy.id]
    assert [p.state for p in meeting(vault, 1).points] == ["open"]
    assert meeting(vault, 2).points == meeting(vault, 3).points == []


def test_undoing_a_roll_also_undoes_another_clone_s_roll_it_woke_and_keeps_that_clone_s_answer(
    vault: Vault,
) -> None:
    """Both clones rolled one point; the laptop also answered it after its roll. Undoing this clone's roll wakes the
    laptop's roll and copy, which the undo takes too, as DR-0614 takes another machine's close; the laptop's answer,
    idle while the point was rolled, then applies and stays (DR-0624)."""
    write(vault, [
        slot(19, response="cancelled"),
        slot(20),
        {"op": "point", "item": "M-0001", "fields": {"text": "Dates"}},
    ], now=NOW)  # fmt: skip
    (copy, roll) = write(vault, [{"op": "rollover"}], now=later(1)).ops
    source = roll.point or ""
    theirs = laptop(vault, "point", meeting(vault, 2).id, fields={"text": "Dates", "from": source})
    their_roll = laptop(vault, "point_set", roll.item, point=source, fields={"state": "rolled", "to": theirs.id})
    answer = laptop(vault, "point_set", roll.item, point=source, fields={"state": "answered", "answer": "the 21st"})
    assert ledger(vault).objects.points[theirs.id].stands is False
    undos = write(vault, [{"op": "undo", "of": roll.id}], now=later(40)).ops
    assert [o.of for o in undos] == [roll.id, copy.id, theirs.id, their_roll.id]
    (point,) = meeting(vault, 1).points
    assert (point.state, point.answer) == ("answered", "the 21st")
    assert answer.id not in {o.of for o in undos}
    # Undoing the answer undoes it alone: an answer wakes nothing.
    (undo,) = write(vault, [{"op": "undo", "of": answer.id}], now=later(41)).ops
    assert undo.item == roll.item
    assert meeting(vault, 1).points[0].state == "open"


def test_a_undo_of_a_losing_roll_whose_copy_rolled_on(vault: Vault) -> None:
    """Both clones rolled one point and the laptop's copy, which lost, rolled on: a losing copy never folds, so the
    roll naming it says it rolled on, and the owner's undo of the laptop's roll or copy is refused until that later
    roll is undone, as for a copy that stands (DR-0624)."""
    write(vault, [
        slot(19, response="cancelled"),
        slot(20),
        slot(21),
        {"op": "point", "item": "M-0001", "fields": {"text": "Dates"}},
    ], now=NOW)  # fmt: skip
    (copy, roll) = write(vault, [{"op": "rollover"}], now=later(1)).ops
    source = roll.point or ""
    theirs = laptop(vault, "point", meeting(vault, 2).id, fields={"text": "Dates", "from": source})
    their_roll = laptop(vault, "point_set", roll.item, point=source, fields={"state": "rolled", "to": theirs.id})
    on = laptop(vault, "point", meeting(vault, 3).id, fields={"text": "Dates", "from": theirs.id})
    rolled_on = laptop(vault, "point_set", copy.item, point=theirs.id, fields={"state": "rolled", "to": on.id})
    points = ledger(vault).objects.points
    assert (points[theirs.id].stands, points[theirs.id].state, points[copy.id].rolled_to) == (False, "open", on.id)
    for of in (their_roll.id, theirs.id):
        with pytest.raises(LedgerError, match="its copy has rolled on since; undo that roll first"):
            write(vault, [{"op": "undo", "of": of}], now=later(40))
    assert [o.of for o in write(vault, [{"op": "undo", "of": rolled_on.id}], now=later(41)).ops] == [
        rolled_on.id, on.id,
    ]  # fmt: skip
    undos = write(vault, [{"op": "undo", "of": their_roll.id}], now=later(42)).ops
    assert [o.of for o in undos] == [their_roll.id, theirs.id]
    assert [(m.number, p.id) for m in ledger(vault).meetings.values() for p in m.open_points()] == [(2, copy.id)]


@pytest.mark.parametrize("where", ["here", "on the laptop"])
def test_a_point_rolled_into_a_meeting_removed_here_or_on_another_clone_is_open_again_and_rolls_on(
    vault: Vault, where: str
) -> None:
    """The meeting a point rolled into removed, by the owner or by a clone that hadn't pulled the roll: the point is
    open on its own meeting again, nothing is named, and the next rollover rolls it on (DR-0623)."""
    write(vault, [
        slot(19, response="cancelled"),
        slot(20),
        slot(27),
        {"op": "point", "item": "M-0001", "fields": {"text": "Dates"}},
    ], now=NOW)  # fmt: skip
    (_, roll) = write(vault, [{"op": "rollover"}], now=later(1)).ops
    removed = meeting(vault, 2).id
    if where == "here":
        write(vault, [{"op": "undo", "of": removed}], now=later(2))
    else:
        laptop(vault, "undo", removed, of=removed)
    assert [(m.number, p.id, p.state) for m in ledger(vault).meetings.values() for p in m.open_points()] == [
        (1, roll.point, "open"),
    ]  # fmt: skip
    assert [str(p) for p in vault.check() if p.where.startswith(("ledger", "M-"))] == []
    (again, rolled) = write(vault, [{"op": "rollover"}], now=datetime(2026, 10, 25, tzinfo=UTC)).ops
    assert (again.item, rolled.point, rolled.fields) == (meeting(vault, 3).id, roll.point, {"state": "rolled",
                                                                                          "to": again.id})  # fmt: skip
    assert [(m.number, p.text) for m in ledger(vault).meetings.values() for p in m.open_points()] == [(3, "Dates")]


def test_the_writer_folds_the_meetings_again_with_the_collector_paused(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each op on a meeting folds every meeting again, so the collector is paused as `load` pauses it."""
    seen: list[bool] = []
    real = fold_objects
    monkeypatch.setattr(store, "fold_objects", lambda groups: seen.append(gc.isenabled()) or real(groups))
    write(vault, [add_meeting(), {"op": "point", "item": "M-0001", "fields": {"text": "Dates"}}], now=NOW)
    assert (bool(seen), any(seen), gc.isenabled()) == (True, False, True)


def test_a_write_reads_only_the_meetings_it_touches(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each meeting a change names is read from the tables with the rest of its part, and no other (DR-0633): a point
    on one meeting reads that one; a rollover reads the meetings due with an open point and those they roll to; an
    event's update reads the meeting holding it; an undo reads the meeting its op is on."""
    write(vault, [
        slot(19), slot(20), slot(26, subject="Hiring"), slot(27, event="calendar:E1"),
        {"op": "point", "item": "M-0001", "fields": {"text": "Dates"}},
        {"op": "point", "item": "M-0003", "fields": {"text": "Not due yet"}},
    ], now=NOW)  # fmt: skip
    ids = {m.number: m.id for m in ledger(vault).meetings.values()}
    read: list[list[str]] = []
    real = Held.object_lines
    monkeypatch.setattr(Held, "object_lines", lambda self, adds: read.append(sorted(adds)) or real(self, adds))
    (budget,) = write(vault, [{"op": "point", "item": "M-0002", "fields": {"text": "Budget"}}], now=later(1)).ops
    assert read == [[ids[2]]]
    read.clear()
    # A day and a half after the 19th's sync: it rolls to the 20th's, which isn't due; Hiring isn't due either.
    rolled = write(vault, [{"op": "rollover"}], now=datetime(2026, 10, 21, tzinfo=UTC)).ops
    assert ([o.item for o in rolled], read) == ([ids[2], ids[1]], [[ids[1]], [ids[2]]])
    read.clear()
    write(vault, [{"op": "meeting", "fields": {"event": "calendar:E1", "where": "Room 2"}}], now=later(3))
    write(vault, [{"op": "undo", "of": budget.id}], now=later(4))
    assert read == [[ids[4]], [ids[1], ids[2]]]


def test_a_rollover_after_a_change_in_the_same_write_sees_the_point_it_added(vault: Vault) -> None:
    """A meeting the write has read already is taken from its fold, a point added a change before included."""
    write(vault, [slot(19), slot(20)], now=NOW)
    (point, copy, rolled) = write(vault, [
        {"op": "point", "item": "M-0001", "fields": {"text": "Dates"}}, {"op": "rollover"},
    ], now=datetime(2026, 10, 21, tzinfo=UTC)).ops  # fmt: skip
    assert (copy.fields, rolled.point, rolled.fields) == (
        {"text": "Dates", "from": point.id}, point.id, {"state": "rolled", "to": copy.id},
    )  # fmt: skip


def test_an_undo_of_a_meeting_s_op_takes_what_it_woke_of_its_kind(vault: Vault) -> None:
    (add, archive) = write(vault, [add_meeting(), {"op": "archive", "item": "M-0001"}], now=NOW).ops
    theirs = laptop(vault, "archive", add.id)
    undos = write(vault, [{"op": "undo", "of": archive.id}], now=later(40)).ops
    assert [o.of for o in undos] == [archive.id, theirs.id]
    assert meeting(vault, 1).archived is False


def test_a_rollover_is_undone_whole_by_its_batch(vault: Vault) -> None:
    write(vault, [
        slot(19, response="cancelled"),
        slot(20),
        {"op": "point", "item": "M-0001", "fields": {"text": "Dates"}},
        {"op": "point", "item": "M-0001", "fields": {"text": "Budget"}},
    ], now=NOW)  # fmt: skip
    rolled = write(vault, [{"op": "rollover"}], by="sync", via="tool", now=later(1))
    undos = write(vault, [{"op": "undo", "batch": rolled.ops[0].batch}], by="user", via="page", now=later(2)).ops
    assert sorted(o.of or "" for o in undos) == sorted(o.id for o in rolled.ops)
    assert [p.state for p in meeting(vault, 1).points] == ["open", "open"]


REFUSALS: list[tuple[dict[str, Any], str]] = [
    ({"op": "meeting"}, "it needs fields"),
    ({"op": "meeting", "fields": {"colour": "red"}}, "there is no meeting field colour"),
    ({"op": "meeting", "fields": {"start": MONDAY, "end": MONDAY}}, "a meeting needs subject"),
    (add_meeting(start="Monday"), "start is a time, YYYY-MM-DDTHH:MM with or without its offset"),
    (add_meeting(end="2026-10-19T08:00+02:00"), "end is before start"),
    (add_meeting(response="maybe"), "response is one of accepted"),
    (add_meeting(attendees="Ada"), "attendees is a list of names"),
    (add_meeting(attendees=[5]), "attendees is a list of names"),
    (add_meeting(subject=5), "subject is text"),
    (add_meeting(stream="Pilot X"), "stream is a slug"),
    (add_meeting(stream="other"), "no stream other"),
    (add_meeting(event="calendar:E9", continues="M-0099"), "no meeting M-0099"),
    ({"op": "set", "item": "M-0001", "fields": {"event": "calendar:E9"}}, "event and continues are set only"),
    ({"op": "set", "item": "M-0001", "fields": {"subject": None}}, "subject can't be cleared"),
    ({"op": "set", "item": "M-0001", "fields": {"end": "2026-10-19T08:00+02:00"}}, "end is before start"),
    ({"op": "material", "item": "M-0002", "ref": "a.md"}, "M-0002 is archived; undo its archive first"),
    ({"op": "archive", "item": "M-0001"}, "an archive takes a meeting with no open point, and M-0001 has 1"),
    ({"op": "archive", "item": "M-0002"}, "M-0002 is archived"),
    ({"op": "triage", "item": "M-0001", "verdict": "me"}, "item is an A- number or an item's id"),
    ({"op": "log", "item": "M-0001"}, "a log needs a note"),
    ({"op": "log", "item": "pilot"}, "a log needs a note"),
    ({"op": "material", "item": "pilot", "ref": "a.md"}, "material takes an item or a meeting, and pilot is a stream"),
    ({"op": "log", "item": "pilots", "note": "x"}, "no stream pilots"),
    ({"op": "log", "item": "M-0099", "note": "x"}, "no meeting M-0099"),
    ({"op": "log", "item": 5, "note": "x"}, "it needs item"),
    ({"op": "log", "item": "Pilot X", "note": "x"}, "item is an A- number or an item's id, an M- number"),
    ({"op": "stream"}, "it needs fields"),
    ({"op": "stream", "fields": {"status": "active"}}, "a stream needs name"),
    ({"op": "stream", "fields": {"name": "other"}}, "a stream needs status"),
    ({"op": "stream", "fields": {"name": "Other X", "status": "active"}}, "name is a slug"),
    ({"op": "stream", "fields": {"name": "other", "status": "paused"}}, "status is one of active, waiting, closed"),
    ({"op": "stream", "fields": {"name": "other", "status": "active", "deadline": "soon"}}, "deadline is a day"),
    ({"op": "stream", "fields": {"colour": "red"}}, "there is no stream field colour"),
    ({"op": "stream", "fields": {"name": "other", "where": 5}}, "where is text"),
    ({"op": "set", "item": "pilot", "fields": {"name": "other"}}, "a stream's name is its key and never changes"),
    ({"op": "set", "item": "pilot", "fields": {"status": None}}, "status can't be cleared"),
    ({"op": "point", "item": "M-0001"}, "it needs fields"),
    ({"op": "point", "item": "M-0001", "fields": {"text": "x", "state": "open"}}, "takes text, item and proposed"),
    ({"op": "point", "item": "M-0001", "fields": {"text": "  "}}, "a point needs text"),
    ({"op": "point", "item": "M-0001", "fields": {"text": "Dates"}}, "M-0001 has that point open already"),
    ({"op": "point", "item": "M-0001", "fields": {"text": "x", "proposed": "yes"}}, "proposed is true or false"),
    ({"op": "point", "item": "M-0001", "fields": {"text": "x", "item": "A-0099"}}, "no item A-0099"),
    ({"op": "point", "item": "M-0002", "fields": {"text": "x"}}, "M-0002 is archived; undo its archive first"),
    ({"op": "point", "item": 5, "fields": {"text": "x"}}, "it needs item, a meeting's M- number or id"),
    ({"op": "point", "item": "X-1", "fields": {"text": "x"}}, "a meeting is named by its M- number or its id"),
    ({"op": "point", "item": "01JAB3W8Q4X5M2Z7K9R0T6YVNC", "fields": {"text": "x"}}, "no meeting 01JAB3W8"),
    ({"op": "point_set", "item": "M-0001", "point": 1}, "it needs fields"),
    ({"op": "point_set", "item": "M-0001", "point": 1, "fields": {"colour": "red"}}, "a point's fields are text"),
    ({"op": "point_set", "item": "M-0001", "point": 1, "fields": {"state": "rolled"}}, "the rollover rolls one"),
    ({"op": "point_set", "item": "M-0001", "point": 1, "fields": {"text": None}}, "text can't be cleared"),
    ({"op": "point_set", "item": "M-0001", "point": 1, "fields": {"answer": 5}}, "answer is text"),
    ({"op": "point_set", "item": "M-0001", "point": 1, "fields": {"proposed": "yes"}}, "proposed is true or false"),
    (
        {"op": "point_set", "item": "M-0001", "point": 9, "fields": {"state": "open"}},
        "M-0001 had 1 point(s) when the batch began; no point 9",
    ),
    ({"op": "point_set", "item": "M-0001", "point": "nope", "fields": {"state": "open"}}, "M-0001 has no point nope"),
    ({"op": "point_set", "item": "M-0001", "point": True, "fields": {"state": "open"}}, "M-0001 has no point -"),
    ({"op": "point_set", "item": "M-0002", "point": 1, "fields": {"state": "open"}}, "M-0002 is archived"),
    ({"op": "point_item", "item": "M-0001", "point": 1, "fields": "x"}, "fields is kind, who or stream"),
    ({"op": "point_item", "item": "M-0001", "point": 1, "fields": {"title": "x"}}, "takes kind, who and stream"),
    ({"op": "keep", "item": "M-0002"}, "M-0002 is archived"),
    ({"op": "rollover", "item": "M-0001"}, "rollover takes only note, by, not item"),
    ({"op": "triage", "item": 5, "verdict": "me"}, "it needs item, an A- number or an item's id"),
    ({"op": "add", "fields": {**ADD_FIELDS, "meeting": "M-0099"}}, "no meeting M-0099"),
]


def test_a_meeting_point_or_stream_change_that_can_t_be_made_refuses_the_whole_write(vault: Vault) -> None:
    write(vault, [
        PILOT,
        add_meeting(event="calendar:E1"),
        {"op": "point", "item": "M-0001", "fields": {"text": "Dates", "proposed": True}},
        add_meeting(subject="Archived"),
        {"op": "point", "item": "M-0002", "fields": {"text": "Old"}},
        {"op": "point_set", "item": "M-0002", "point": 1, "fields": {"state": "dropped"}},
        {"op": "archive", "item": "M-0002"},
    ], now=NOW)  # fmt: skip
    head = sh(vault.root, "git", "rev-parse", "HEAD")
    for change, why in REFUSALS:
        kind = change.get("op")
        with pytest.raises(LedgerError, match=rf"^change 1 \({kind}\): .*{_re(why)}.*; nothing written$"):
            write(vault, [change], now=later(1))
    assert sh(vault.root, "git", "rev-parse", "HEAD") == head


def _re(text: str) -> str:
    return "".join(f"\\{c}" if c in "().*+?[]\\^$|{}" else c for c in text)


def test_meetings_points_and_streams_pass_the_gate_s_text_rules(vault: Vault) -> None:
    path = vault.root / "vault.yaml"
    config = VaultConfig.load(path)
    config.never_store.people = ["Dora Example"]
    config.never_store.keywords = ["Project Nebula"]
    config.redaction.patterns = [RedactionPattern(name="codename", pattern="kepler"),
                                 RedactionPattern(name="october", pattern="2026-10-2")]  # fmt: skip
    path.write_text(config.dump(), encoding="utf-8", newline="\n")
    commit_config(vault.root)
    vault = Vault.open(vault.root)
    (add, stream) = write(vault, [
        add_meeting(subject="The kepler sync", attendees=["Ada kepler"], notes="password: hunter2-Example"),
        {"op": "stream", "fields": {"name": "pilot", "status": "active", "next": "ask about kepler"}},
    ], now=NOW).ops  # fmt: skip
    assert add.fields is not None and stream.fields is not None
    assert (add.fields["subject"], add.fields["attendees"], add.fields["notes"], stream.fields["next"]) == (
        "The [redacted:codename] sync", ["Ada [redacted:codename]"], "password: [redacted:password]",
        "ask about [redacted:codename]",
    )  # fmt: skip
    for change, why in [
        (add_meeting(attendees=["Dora Example"]), "attendee names someone on the never-store people list"),
        (add_meeting(organiser="Dora Example"), "organiser names someone on the never-store people list"),
        (
            add_meeting(event="calendar:E1?token=hunter2-Example"),
            "event matches the api-key redaction rule, and a locator",
        ),
        (
            add_meeting(start="2026-10-21T09:00+02:00", end="2026-10-21T10:00+02:00"),
            "start matches the october redaction rule, and a time can't hold a marker",
        ),
        ({"op": "point", "item": "M-0001", "fields": {"text": "about project nebula"}}, "text says something"),
        ({"op": "stream", "fields": {"name": "kepler", "status": "active"}}, "name matches the codename"),
    ]:
        with pytest.raises(LedgerError, match=_re(why)) as caught:
            write(vault, [change], now=later(1))
        assert "hunter2" not in str(caught.value) and "ebula" not in str(caught.value)
