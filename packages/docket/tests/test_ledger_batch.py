"""A write as a batch (#201): `as` and `$name` (DR-0640), `by` on any change and the batch's ops (DR-0639), an
outcome and notes, the archives due and what a rollover couldn't roll (DR-0641), look-alikes and double meetings
(DR-0642), a point's n as the batch began, and a dry run that lists every refusal; then reading a batch's JSON and
saying what it wrote."""

from __future__ import annotations

import io
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import empty_vault, make_config
from ledger_kit import ADD_FIELDS, MEETING, NOW

from docket.cli import main
from docket.index import rebuild
from docket.ledger import batch, ops
from docket.ledger.fold import Item, Ledger, fold
from docket.ledger.meetings import Meeting
from docket.ledger.ops import Op
from docket.ledger.store import BATCH, CHANGES, LedgerError, Written, write
from docket.vault.vault import Vault

GOLDEN = Path(__file__).parent / "fixtures" / "ledger-batch.json"
ADD: dict[str, Any] = {"op": "add", "fields": dict(ADD_FIELDS)}
OTHER: dict[str, Any] = {"op": "add", "fields": {**ADD_FIELDS, "title": "Book the pilot room", "who": "Ben Example",
                                                 "source": "chat:C0TEST/1791000002.000100"}}  # fmt: skip


@pytest.fixture(params=["memory", "index"])
def vault(tmp_path: Path, request: pytest.FixtureRequest) -> Vault:
    v = empty_vault(tmp_path, make_config())
    if request.param == "index":
        rebuild(v)
    return v


def ledger(vault: Vault) -> Ledger:
    return fold(ops.read_all(vault.root).lines)


def item(vault: Vault, number: int) -> Item:
    return next(i for i in ledger(vault).items.values() if i.number == number)


def meeting(vault: Vault, number: int) -> Meeting:
    return next(m for m in ledger(vault).meetings.values() if m.number == number)


def later(days: float) -> datetime:
    return NOW + timedelta(days=days)


def at(day: int, hour: int) -> str:
    return f"2026-10-{day:02d}T{hour:02d}:00+02:00"


def refused(vault: Vault, changes: list[dict[str, Any]], match: str, **keys: Any) -> None:
    with pytest.raises(LedgerError, match=match):
        write(vault, changes, now=keys.pop("now", NOW), **keys)


# -- the door ---------------------------------------------------------------------------------------------------------


def test_the_batch_s_ops_and_keys_match_the_golden_file_and_hold_the_writer_s() -> None:
    assert {kind: list(keys) for kind, keys in BATCH.items()} == json.loads(GOLDEN.read_text(encoding="utf-8"))
    for kind, keys in CHANGES.items():
        assert set(keys) <= set(BATCH[kind]), kind
    assert set(BATCH) - set(CHANGES) == {"notes"}


# -- as and $name -----------------------------------------------------------------------------------------------------


def test_as_names_what_a_change_made_and_a_later_change_s_dollar_name_stands_for_its_id(vault: Vault) -> None:
    written = write(vault, [
        {**ADD, "as": "ask"},
        {"op": "meeting", "fields": dict(MEETING), "as": "sync"},
        {"op": "point", "item": "$sync", "fields": {"text": "Ada: the dates", "item": "$ask"}, "as": "dates"},
        {"op": "point", "item": "$sync", "fields": {"text": "Ben: the room"}, "as": "room"},
        {"op": "point_set", "item": "$sync", "point": "$dates", "fields": {"answer": "the 21st"}},
        {"op": "point_item", "item": "$sync", "point": "$room", "fields": {"stream": "pilot"}, "as": "booking"},
        {"op": "set", "item": "$booking", "fields": {"meeting": "$sync", "title": "$5 for the room"}},
        {"op": "log", "item": "$ask", "note": "asked again"},
        {"op": "meeting", "fields": {**MEETING, "start": at(26, 9), "end": at(26, 10), "event": "calendar:E2",
                                     "continues": "$sync"}, "as": "keyed"},
        {"op": "undo", "of": "$room"},
    ], now=NOW)  # fmt: skip
    m = meeting(vault, 1)
    ask, booking = item(vault, 1), item(vault, 2)
    assert written.names == {"ask": ask.id, "sync": m.id, "dates": written.changes[2][0].id,
                             "room": written.changes[3][0].id, "booking": booking.id, "keyed": m.id}  # fmt: skip
    assert {name: written.labels[i] for name, i in written.names.items()} == {
        "ask": "A-0001", "sync": "M-0001", "dates": "M-0001#1", "room": "M-0001", "booking": "A-0002",
        "keyed": "M-0001",
    }  # fmt: skip
    assert (booking.meeting, booking.title, m.event) == (m.id, "$5 for the room", "calendar:E2")
    assert [(p.text, p.answer, p.item) for p in m.points] == [("Ada: the dates", "the 21st", ask.id)]
    # A meeting its event names, set or left as it is, is the one `as` names.
    set_again = write(vault, [{"op": "meeting", "fields": {**MEETING, "event": "calendar:E2", "where": "Room 2"},
                               "as": "m"}], now=later(1))  # fmt: skip
    unchanged = write(vault, [{"op": "meeting", "fields": {**MEETING, "event": "calendar:E2"}, "as": "m"}],
                      now=later(1))  # fmt: skip
    assert (set_again.names, unchanged.names, unchanged.ops) == ({"m": m.id}, {"m": m.id}, ())


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ([{"op": "log", "item": "$nope", "note": "x"}], r"change 1 \(log\): \$nope isn't given by an earlier change"),
        ([{**ADD, "as": "x"}, {**OTHER, "as": "x"}], r"change 2 \(add\): as x is given twice in the batch"),
        ([{**ADD, "as": "Big"}], "as is a name: a lowercase letter"),
        ([{**ADD, "as": 5}], "as is a name"),
        ([{"op": "log", "item": "A-0001", "note": "x", "as": "y"}], "log takes only item, note, by, not as"),
        ([{"op": "colour"}], r"change 1 \(colour\): op is one of add, set, log"),
        ([{**ADD, "by": "someone"}], "by is one of user, claude, sync"),
        ([{**ADD, "force": "yes"}], "force is true or false"),
    ],
)
def test_a_name_or_a_key_that_doesn_t_fit_refuses_the_batch(
    vault: Vault, changes: list[dict[str, Any]], match: str
) -> None:
    refused(vault, changes, match)
    assert not (vault.root / "ledger/ops").exists() or not list((vault.root / "ledger/ops").glob("*.jsonl"))


def test_each_change_says_who_unless_the_batch_does(vault: Vault) -> None:
    written = write(vault, [
        {**ADD, "by": "user"}, {"op": "log", "item": "A-0001", "note": "seen"},
        {"op": "triage", "item": "A-0001", "verdict": "chase", "by": "sync"},
    ], by="claude", now=NOW)  # fmt: skip
    assert [o.by for o in written.ops] == ["user", "claude", "sync"]


# -- the batch's own changes ------------------------------------------------------------------------------------------


def test_done_and_drop_take_an_outcome_as_a_set_before_the_close(vault: Vault) -> None:
    write(vault, [ADD, OTHER], now=NOW)
    written = write(vault, [
        {"op": "done", "item": "A-0001", "note": "sent", "outcome": "the 21st it is"},
        {"op": "drop", "item": "A-0002", "note": "not ours", "outcome": ""},
    ], now=later(1))  # fmt: skip
    assert [(o.op, o.fields) for o in written.ops] == [
        ("set", {"outcome": "the 21st it is"}), ("done", None), ("drop", None),
    ]  # fmt: skip
    assert written.changes == ((written.ops[0], written.ops[1]), (written.ops[2],))
    assert (item(vault, 1).state, item(vault, 1).outcome, item(vault, 2).outcome) == ("done", "the 21st it is", None)
    refused(vault, [{"op": "done", "item": "A-0001", "outcome": "again"}], r"change 1 \(done\): A-0001 is already done")
    assert item(vault, 1).outcome == "the 21st it is"


def test_notes_appends_a_line_or_sets_the_whole_text(vault: Vault) -> None:
    write(vault, [ADD], now=NOW)
    write(vault, [{"op": "notes", "item": "A-0001", "append": "drafted"}], now=NOW)
    write(vault, [{"op": "notes", "item": "A-0001", "append": "waiting for Ada  ", "note": "nudged"}], now=NOW)
    assert item(vault, 1).notes == "drafted\nwaiting for Ada"
    assert item(vault, 1).log[-1].op.note == "nudged"
    write(vault, [{"op": "notes", "item": "A-0001", "set": "all new"}], now=NOW)
    assert item(vault, 1).notes == "all new"
    write(vault, [{"op": "notes", "item": "A-0001", "set": None}], now=NOW)
    assert item(vault, 1).notes is None
    write(vault, [{"op": "meeting", "fields": dict(MEETING)}], now=NOW)
    for change, match in (
        ({"item": "A-0001"}, "notes takes append, a line to add, or set, the whole text"),
        ({"item": "A-0001", "append": "x", "set": "y"}, "notes takes append"),
        ({"item": "A-0001", "append": " \n "}, "append is text"),
        ({"item": "A-0001", "append": 5}, "append is text"),
        ({"item": "M-0001", "append": "x"}, r"change 1 \(notes\): item is an A- number or an item's id"),
        ({"item": "A-0001", "set": 5}, r"change 1 \(notes\): notes is text"),
    ):
        refused(vault, [{"op": "notes", **change}], match)


def test_archive_with_no_item_archives_what_is_due(vault: Vault) -> None:
    """Items closed 14 days back or more and meetings started 14 days back or more with no open point; not one whose
    archive was undone, one reopened and closed again in the batch, or one whose add the batch undid."""
    long_ago = NOW - timedelta(days=20)
    early = write(vault, [
        ADD, OTHER, {**ADD, "fields": {**ADD_FIELDS, "title": "Third ask", "source": "chat:C0TEST/3"}, "force": True},
        {**ADD, "fields": {**ADD_FIELDS, "title": "Fourth ask", "source": "chat:C0TEST/4"}, "force": True},
        {**ADD, "fields": {**ADD_FIELDS, "title": "Fifth ask", "source": "chat:C0TEST/5"}, "force": True},
        {"op": "meeting", "fields": {**MEETING, "start": "2026-09-28T09:00+02:00", "end": "2026-09-28T10:00+02:00"}},
        {"op": "meeting", "fields": {"subject": "Hiring", "start": "2026-09-28T11:00+02:00",
                                     "end": "2026-09-28T12:00+02:00"}},
        {"op": "point", "item": "M-0002", "fields": {"text": "Still open"}},
        {"op": "meeting", "fields": {"subject": "Budget", "start": "2026-09-29T11:00+02:00",
                                     "end": "2026-09-29T12:00+02:00"}},
    ], now=long_ago)  # fmt: skip
    for n in (1, 2, 3, 5):
        write(vault, [{"op": "done", "item": f"A-000{n}"}], now=long_ago)
    write(vault, [{"op": "done", "item": "A-0004"}], now=NOW - timedelta(days=10))
    first = write(vault, [{"op": "archive", "item": "A-0002"}, {"op": "archive", "item": "M-0003"}], now=NOW)
    write(vault, [{"op": "undo", "of": first.ops[0].id}, {"op": "undo", "of": first.ops[1].id}], now=NOW)
    written = write(vault, [
        {"op": "reopen", "item": "A-0003"}, {"op": "done", "item": "A-0003"}, {"op": "undo", "of": early.ops[4].id},
        {"op": "archive", "note": "the sync's"},
    ], now=NOW)  # fmt: skip
    archived = [(written.labels[o.item], o.note) for o in written.changes[3]]
    assert archived == [("A-0001", "the sync's"), ("M-0001", "the sync's")]
    assert write(vault, [{"op": "archive"}], now=NOW).ops == ()


def test_archive_and_rollover_with_no_meeting_in_the_ledger_write_nothing(vault: Vault) -> None:
    write(vault, [ADD], now=NOW)
    written = write(vault, [{"op": "archive"}, {"op": "rollover"}], now=later(30))
    assert (written.ops, written.remarks) == ((), ())


def test_a_point_on_a_meeting_the_batch_took_off_reads_as_its_meeting(vault: Vault) -> None:
    written = write(vault, [
        {"op": "meeting", "fields": dict(MEETING), "as": "m"},
        {"op": "point", "item": "$m", "fields": {"text": "Gone"}},
        {"op": "undo", "of": "$m"},
    ], now=NOW)  # fmt: skip
    assert written.labels[written.changes[1][0].id] == "M-0001"
    assert ledger(vault).meetings == {}


def test_rollover_names_the_meetings_it_couldn_t_roll(vault: Vault) -> None:
    write(vault, [
        {"op": "meeting", "fields": {"subject": "Budget", "start": at(19, 11), "end": at(19, 12)}},
        {"op": "meeting", "fields": {"subject": "Weekly pilot sync", "start": at(19, 9), "end": at(19, 10)}},
        {"op": "meeting", "fields": {"subject": "Weekly pilot sync", "start": at(26, 9), "end": at(26, 10)}},
        {"op": "point", "item": "M-0001", "fields": {"text": "Nowhere to go"}},
        {"op": "point", "item": "M-0002", "fields": {"text": "Rolls on"}},
        {"op": "meeting", "fields": {"subject": "Retro", "start": at(20, 9), "end": at(20, 10)}},
        {"op": "point", "item": "M-0004", "fields": {"text": "Not due for a day"}},
    ], now=NOW)  # fmt: skip
    # Budget is due with nowhere to go; the Retro ended two hours ago, so it isn't due yet and isn't named.
    written = write(vault, [{"op": "rollover"}], now=datetime(2026, 10, 20, 12, tzinfo=UTC))
    assert [o.op for o in written.ops] == ["point", "point_set"]
    assert written.remarks == ((1, "M-0001 Budget: 1 open point(s), no next meeting"),)


# -- look-alikes and doubles (DR-0642) --------------------------------------------------------------------------------


def test_an_add_that_looks_like_an_open_item_is_refused_unless_forced(vault: Vault) -> None:
    write(vault, [ADD], now=NOW)
    same_who = {**ADD, "fields": {**ADD_FIELDS, "title": "send ADA the DATES, please", "source": "chat:C0TEST/9"}}
    refused(vault, [same_who], r"change 1 \(add\): looks like an open item already: A-0001 Send Ada the dates; log")
    # The same who with another title, or a title alike from someone else elsewhere, is a new ask.
    write(vault, [{**ADD, "fields": {**ADD_FIELDS, "title": "Book the room", "source": "chat:C0TEST/9"}}], now=NOW)
    write(vault, [{**ADD, "fields": {**ADD_FIELDS, "who": "Ben Example", "source": "chat:C0TEST/10"}}], now=NOW)
    forced = write(vault, [{**same_who, "force": True}], now=NOW)
    assert forced.ops[0].number == 4
    # An earlier add of the batch counts, and a closed item doesn't.
    refused(vault, [{**OTHER, "as": "x"}, {**OTHER, "fields": {**OTHER["fields"], "source": "chat:C0TEST/11"}}],
            r"change 2 \(add\): looks like an open item already: A-0005 Book the pilot room")  # fmt: skip
    write(vault, [{"op": "done", "item": f"A-000{n}"} for n in (1, 4)], now=NOW)
    assert write(vault, [same_who], now=NOW).ops[0].number == 5
    # The import brings the old ledger as it was, look-alikes and all.
    assert write(vault, [same_who], now=NOW, via="import").ops[0].number == 6


def test_the_same_source_with_another_title_adds_and_names_the_other_item(vault: Vault) -> None:
    write(vault, [ADD], now=NOW)
    written = write(vault, [{**ADD, "fields": {**ADD_FIELDS, "title": "Also book the room", "who": "Ben Example"}}],
                    now=NOW)  # fmt: skip
    assert [o.number for o in written.ops] == [2]
    assert written.remarks == ((1, "A-0001 comes from the same source: Send Ada the dates"),)


def test_a_keyless_meeting_with_the_start_and_subject_of_one_that_stands_is_refused(vault: Vault) -> None:
    write(vault, [{"op": "meeting", "fields": dict(MEETING)}], now=NOW)
    double = {"op": "meeting", "fields": {**MEETING, "subject": " weekly PILOT  sync", "start": "2026-10-19T09:00"}}
    refused(vault, [double], r"change 1 \(meeting\): M-0001 has that start and subject already; change it there")
    refused(vault, [{"op": "meeting", "fields": {**MEETING, "end": "2026-10-19T08:00+02:00"}}], "end is before start")
    keyed = {**MEETING, "event": "calendar:E7", "end": "2026-10-19T08:00+02:00"}
    refused(vault, [{"op": "meeting", "fields": keyed}], "end is before start")
    refused(vault, [{"op": "meeting", "fields": {"subject": "x", "start": at(19, 9)}}], "a meeting needs end")
    # Another start, or an event, is another meeting; one added twice in a batch is a double too.
    write(vault, [{"op": "meeting", "fields": {**MEETING, "start": at(19, 10), "end": at(19, 11)}}], now=NOW)
    write(vault, [{"op": "meeting", "fields": {**MEETING, "event": "calendar:E1"}}], now=NOW)
    later_one = {"op": "meeting", "fields": {**MEETING, "start": at(26, 9), "end": at(26, 10)}}
    refused(vault, [later_one, later_one], r"change 2 \(meeting\): M-0004 has that start")


# -- a point's n as the batch began -----------------------------------------------------------------------------------


def test_a_point_s_n_is_its_place_when_the_batch_began(vault: Vault) -> None:
    write(vault, [{"op": "meeting", "fields": dict(MEETING)}] + [
        {"op": "point", "item": "M-0001", "fields": {"text": text}} for text in ("One", "Two", "Three")
    ], now=NOW)  # fmt: skip
    two = meeting(vault, 1).points[1].id
    write(vault, [
        {"op": "undo", "of": two},
        {"op": "point_set", "item": "M-0001", "point": "#3", "fields": {"answer": "done"}},
        {"op": "point", "item": "M-0001", "fields": {"text": "Four"}},
    ], now=NOW)  # fmt: skip
    assert [(p.text, p.answer) for p in meeting(vault, 1).points] == [("One", None), ("Three", "done"), ("Four", None)]
    refused(vault, [{"op": "point_set", "item": "M-0001", "point": 4, "fields": {"answer": "x"}}],
            "M-0001 had 3 point.s. when the batch began; no point 4")  # fmt: skip
    first = meeting(vault, 1).points[0].id
    refused(vault, [{"op": "undo", "of": first}, {"op": "point_item", "item": "M-0001", "point": 1}],
            r"change 2 \(point_item\): M-0001's point 1 was taken off earlier in the batch")  # fmt: skip
    # A meeting the batch added numbers its points as they stand.
    write(vault, [{"op": "meeting", "fields": {**MEETING, "start": at(26, 9), "end": at(26, 10)}, "as": "m"},
                  {"op": "point", "item": "$m", "fields": {"text": "New"}},
                  {"op": "point_set", "item": "$m", "point": 1, "fields": {"answer": "yes"}}], now=NOW)  # fmt: skip
    assert meeting(vault, 2).points[0].answer == "yes"
    refused(vault, [{"op": "meeting", "fields": {**MEETING, "start": at(27, 9), "end": at(27, 10)}, "as": "m"},
                    {"op": "point_set", "item": "$m", "point": 2, "fields": {"answer": "x"}}],
            "M-0003 has 0 point.s.; no point 2")  # fmt: skip


# -- a dry run --------------------------------------------------------------------------------------------------------


def test_a_dry_run_lists_every_refusal_and_marks_what_hangs_on_one(vault: Vault) -> None:
    write(vault, [ADD, OTHER, {"op": "done", "item": "A-0002"}, {"op": "meeting", "fields": dict(MEETING)}], now=NOW)
    changes: list[dict[str, Any]] = [
        {**ADD, "as": "x"},
        {"op": "log", "item": "$x", "note": "hangs"},
        {"op": "point", "item": "M-0001", "fields": {"text": "On x", "item": "$x"}, "as": "p"},
        {"op": "point_set", "item": "M-0001", "point": "$p", "fields": {"answer": "hangs too"}},
        {"op": "done", "item": "A-0002", "outcome": "taken back"},
        {"op": "triage", "item": "A-0001", "verdict": "chase"},
        {"op": "colour"},
    ]
    written = write(vault, changes, now=later(1), dry_run=True)
    assert [(r.n, r.op, r.on) for r in written.refused] == [
        (1, "add", None), (2, "log", 1), (3, "point", 1), (4, "point_set", 3), (5, "done", None), (7, "colour", None),
    ]  # fmt: skip
    assert written.refused[1].why == "it names $x, which change 1 would give"
    assert [o.op for o in written.ops] == ["triage"]
    assert written.changes[5] == written.ops
    assert (written.commit, item(vault, 2).outcome) == (None, None)
    with pytest.raises(LedgerError, match=r"change 1 \(add\): looks like"):
        write(vault, changes, now=later(1))


def test_a_dry_run_with_nothing_refused_says_what_it_would_write(vault: Vault) -> None:
    written = write(vault, [{**ADD, "as": "x"}, {"op": "log", "item": "$x", "note": "seen"}], now=NOW, dry_run=True)
    assert ([o.op for o in written.ops], written.refused, written.commit) == (["add", "log"], (), None)
    assert written.names == {"x": written.ops[0].id}


# -- reading a batch and saying what it wrote -------------------------------------------------------------------------


def test_a_batch_reads_as_a_json_array_or_json_lines() -> None:
    assert batch.parse('﻿ [{"op": "log"}, {"op": "done"}] ') == [{"op": "log"}, {"op": "done"}]
    assert batch.parse('# the sync\n{"op": "log"}\n\n  # more\n{"op": "done"}\n') == [{"op": "log"}, {"op": "done"}]
    assert batch.parse("  \n") == []
    with pytest.raises(LedgerError, match="the batch isn't a JSON array: Expecting value at line 1"):
        batch.parse("[nope]")
    with pytest.raises(LedgerError, match="line 2 of the batch isn't a JSON object"):
        batch.parse('{"op": "log"}\n{op}')
    with pytest.raises(LedgerError, match="every op of a batch is a JSON object"):
        batch.parse('[{"op": "log"}, 5]')
    with pytest.raises(LedgerError, match="every op of a batch is a JSON object"):
        batch.parse('"log"')


def test_what_a_batch_wrote_reads_a_line_or_more_per_change(vault: Vault) -> None:
    vendor = {**ADD_FIELDS, "title": "Chase the vendor", "who": "Cy Example", "source": "chat:C0TEST/7"}
    write(vault, [ADD, {"op": "add", "fields": vendor}, {"op": "meeting", "fields": dict(MEETING)},
                  {"op": "point", "item": "M-0001", "fields": {"text": "Old"}}], now=NOW)  # fmt: skip
    changes: list[dict[str, Any]] = [
        {**OTHER, "as": "x"},
        {"op": "stream", "fields": {"name": "pilot", "status": "active"}},
        {"op": "log", "item": "$x", "note": "a long note " * 10},
        {"op": "done", "item": "A-0001", "outcome": "the 21st"},
        {"op": "point", "item": "M-0001", "fields": {"text": "New"}},
        {"op": "point_set", "item": "M-0001", "point": 1, "fields": {"state": "dropped"}},
        {"op": "undo", "of": "$x"},
        {"op": "set", "item": "A-0001", "fields": {"title": ADD_FIELDS["title"]}},
        {"op": "add", "fields": {**vendor, "title": "Room for Ben", "who": "Ben Example"}},
    ]
    written = write(vault, changes, now=NOW)
    found = batch.result(changes, written, dry_run=False)
    x = written.names["x"]
    assert (found.ops, found.batch, found.commit, found.ids, found.names) == (
        9, written.ops[0].id, written.commit, {"x": x}, {"x": "A-0003"},
    )  # fmt: skip
    lines = batch.lines(found)
    assert lines[:-1] == [
        "1. add: A-0003 added: Book the pilot room",
        "2. stream: pilot added: pilot",
        f"3. log: A-0003 log: {('a long note ' * 10)[:59].strip()}…",
        "4. done: A-0001 outcome: the 21st",
        "   A-0001 done",
        "5. point: M-0001#2 point added: New",
        "6. point_set: M-0001#1 point dropped",
        f"7. undo: A-0003 undo of {x}",
        "8. set: nothing to change",
        "9. add: A-0004 added: Room for Ben",
        "   A-0002 comes from the same source: Chase the vendor",
        "$x = A-0003",
    ]
    assert lines[-1] == f"9 op(s) written, batch {written.ops[0].id}, commit {str(written.commit)[:7]}"
    idle = [{"op": "triage", "item": "A-0002", "verdict": None}]
    assert batch.lines(batch.result(idle, write(vault, idle, now=NOW), dry_run=False)) == [
        "1. triage: nothing to change", "nothing to write",
    ]  # fmt: skip


def test_a_dry_run_s_lines_say_what_would_be_written_or_what_is_refused(vault: Vault) -> None:
    good = [ADD]
    found = batch.result(good, write(vault, good, now=NOW, dry_run=True), dry_run=True)
    assert batch.lines(found)[-1] == "dry run: 1 op(s) would be written; nothing written"
    bad: list[dict[str, Any]] = [{"op": "log", "item": "A-0009", "note": "x"}, {"op": "log", "item": "$y"}]
    found = batch.result(bad, write(vault, bad, now=NOW, dry_run=True), dry_run=True)
    assert batch.lines(found) == [
        "1. log: refused, no item A-0009",
        "2. log: refused, $y isn't given by an earlier change's as",
        "dry run: 2 change(s) refused; nothing written",
    ]
    assert (found.changes[0].refused, found.changes[0].hangs_on) == ("no item A-0009", None)


def test_the_renumbers_a_write_starts_with_are_named_apart_by_the_number_each_lost(vault: Vault) -> None:
    (ours, _) = write(vault, [ADD, {"op": "meeting", "fields": {**MEETING, "event": "calendar:E1"}}], now=NOW).ops
    highest = ledger(vault).highest_id
    item_id, meeting_id = ops.mint(highest, NOW + timedelta(minutes=30), 2)
    base: dict[str, Any] = {"schema_version": 1, "day": date(2026, 10, 18), "machine": "laptop", "by": "user",
                            "via": "page", "number": 1}  # fmt: skip
    theirs = [
        Op(**base, id=item_id, item=item_id, op="add", fields={**ADD_FIELDS, "title": "Another ask"}),
        Op(**base, id=meeting_id, item=meeting_id, op="meeting", fields={**MEETING, "event": "calendar:E2"}),
    ]
    rel = ops.rel_of(theirs[0])
    (vault.root / rel).write_text("".join(f"{o.text()}\n" for o in theirs), encoding="utf-8", newline="\n")
    vault.commit([rel], "Merge the laptop's ops")
    changes: list[dict[str, Any]] = [{"op": "log", "item": ours.id, "note": "after the merge"}]
    written = write(vault, changes, now=NOW + timedelta(minutes=40))
    found = batch.result(changes, written, dry_run=False)
    assert found.renumbered == ["A-0001 renumbered to A-0002", "M-0001 renumbered to M-0002"]
    assert batch.lines(found)[:2] == [f"renumbered: {line}" for line in found.renumbered]
    assert isinstance(written, Written)


# -- docket ledger batch ----------------------------------------------------------------------------------------------


@pytest.fixture
def here(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Vault:
    v = empty_vault(tmp_path / "vault", make_config())
    monkeypatch.setenv("DOCKET_VAULT", str(v.root))
    return v


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, list[str], str]:
    code = main(["ledger", "batch", *args])
    out, err = capsys.readouterr()
    return code, out.splitlines(), err.strip()


def test_the_command_writes_a_file_s_batch_via_cli(
    here: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ops_file = tmp_path / "sync-ops.jsonl"
    rows = ["# the sync's", json.dumps({**ADD, "as": "x"}), json.dumps({"op": "log", "item": "$x", "note": "seen"})]
    ops_file.write_text("\n".join(rows) + "\n", encoding="utf-8")
    code, out, _ = run(capsys, str(ops_file), "--by", "sync", "--dry-run")
    assert (code, out[-1]) == (0, "dry run: 2 op(s) would be written; nothing written")
    code, out, _ = run(capsys, str(ops_file), "--by", "sync")
    assert (code, out[:3]) == (
        0,
        ["1. add: A-0001 added: Send Ada the dates", "2. log: A-0001 log: seen", "$x = A-0001"],
    )
    assert [(e.op.by, e.op.via) for e in item(here, 1).log] == [("sync", "cli"), ("sync", "cli")]


def test_the_command_reads_stdin_and_exits_1_on_a_refusal(
    here: Vault, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    data = ("\ufeff" + json.dumps([{"op": "log", "item": "A-0009", "note": "x"}])).encode("utf-8")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data), encoding="utf-8"))
    code, out, _ = run(capsys, "-", "--dry-run")
    assert (code, out) == (1, ["1. log: refused, no item A-0009", "dry run: 1 change(s) refused; nothing written"])
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data), encoding="utf-8"))
    code, out, err = run(capsys)
    assert (code, out, err) == (1, [], "docket: change 1 (log): no item A-0009; nothing written")
    # A batch that doesn't read is no answer: it fails, exit 2.
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"[nope"), encoding="utf-8"))
    code, _, err = run(capsys)
    assert (code, err.startswith("docket: the batch isn't a JSON array")) == (2, True)


def test_the_command_refuses_a_missing_file_bytes_that_aren_t_utf8_and_a_bad_by(
    here: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(capsys, str(tmp_path / "none.jsonl"))[::2] == (2, f"docket: no file {tmp_path / 'none.jsonl'}")
    latin = tmp_path / "latin.jsonl"
    latin.write_bytes(b'{"op": "log", "note": "caf\xe9"}')
    assert run(capsys, str(latin))[2] == f"docket: {latin} isn't UTF-8; write the batch as UTF-8"
    good = tmp_path / "good.json"
    good.write_text("[]", encoding="utf-8")
    assert run(capsys, str(good), "--by", "me")[2] == "docket: --by is user, claude, sync, not 'me'"
    assert run(capsys, str(good))[:2] == (0, ["nothing to write"])
