"""Folding ops into items (DR-0611 to DR-0615): each op's effect, idle ops, undo, archive, duplicates and numbers."""

from __future__ import annotations

import gc
import json
from datetime import date
from pathlib import Path

import pytest
from ledger_kit import ADD_FIELDS, DAY, line, lines, op, ulid

from docket.ledger import fold as fold_module
from docket.ledger import load
from docket.ledger.fold import Item, Ledger, Material, describe, fold, fold_item
from docket.ledger.ops import Op, parse


def one(*made: Op) -> tuple[Item, Ledger]:
    """The one item the ops make, and the ledger."""
    ledger = fold(lines(*made))
    return next(iter(ledger.items.values())), ledger


def test_an_add_is_an_open_item_holding_every_field_it_names() -> None:
    fields = {**ADD_FIELDS, "due": "2026-10-21", "description": "the pilot's dates", "notes": "ask twice",
              "outcome": "sent", "opened": "2026-09-14"}  # fmt: skip
    item, ledger = one(op("add", 1, fields=fields))
    assert (item.id, item.number, item.claimed, item.state, item.added_by) == (ulid(1), 1, 1, "open", "claude")
    assert (item.kind, item.title, item.who, item.stream) == ("reply", "Send Ada the dates", "Ada Example", "pilot")
    assert (item.opened, item.due, item.description, item.notes, item.outcome) == (
        date(2026, 9, 14), date(2026, 10, 21), "the pilot's dates", "ask twice", "sent",
    )  # fmt: skip
    assert one(op("add", 1))[0].opened == DAY
    assert (ledger.highest_id, ledger.highest_number, ledger.problems) == (ulid(1), 1, ())


def test_each_op_changes_the_item_as_todo_py_s_verbs_do() -> None:
    item, _ = one(
        op("add", 1),
        op("set", 2, 1, fields={"due": "2026-10-21", "title": "Send Ada the new dates", "notes": "x", "later": "y"}),
        op("set", 3, 1, fields={"notes": None, "kind": None, "opened": None, "due": None}),
        op("triage", 4, 1, verdict="chase"),
        op("snooze", 5, 1, until=date(2026, 10, 25)),
        op("triage", 6, 1, verdict="claude"),
        op("material", 7, 1, ref="docs/plan.md"),
        op("material", 8, 1, ref="docs/plan.md", label="the plan"),
        op("material", 9, 1, ref="https://example.invalid/x"),
        op("material", 10, 1, ref="https://example.invalid/x", remove=True),
        op("done", 11, 1, note="answered in the thread"),
        op("archive", 12, 1),
        op("reopen", 13, 1),
        op("drop", 14, 1, note="not ours"),
        op("log", 15, 1, note="Ada said thanks"),
    )
    assert (item.title, item.kind, item.due, item.notes, item.opened) == (
        "Send Ada the new dates",
        "reply",
        None,
        None,
        DAY,
    )
    assert item.materials == [Material("docs/plan.md", "the plan")]
    assert (item.state, item.closed, item.archived, item.triage, item.until) == ("dropped", DAY, False, None, None)
    assert item.last == ulid(15)
    assert [e.line for e in item.log] == [
        "added",
        "due: 2026-10-21; title: Send Ada the new dates; notes edited; later: y",
        "notes cleared; kind: -; opened: -; due: -",
        "triage: chase",
        "snoozed to 2026-10-25",
        "triage: claude",
        "material added: docs/plan.md",
        "material added: the plan",
        "material added: https://example.invalid/x",
        "material removed: https://example.invalid/x",
        "done: answered in the thread",
        "archived",
        "reopened",
        "dropped: not ours",
        "Ada said thanks",
    ]


def test_a_triage_wakes_a_snoozed_item_and_a_snooze_clears_its_triage() -> None:
    item, _ = one(op("add", 1), op("triage", 2, 1, verdict="me"), op("snooze", 3, 1, until=date(2026, 11, 1)))
    assert (item.state, item.until, item.triage) == ("snoozed", date(2026, 11, 1), None)
    woken, _ = one(op("add", 1), op("snooze", 2, 1, until=date(2026, 11, 1)), op("triage", 3, 1, verdict=None))
    assert (woken.state, woken.until, woken.triage) == ("open", None, None)


def test_an_op_that_meets_a_state_it_can_t_change_is_idle_and_stays_in_the_log() -> None:
    made = [
        op("add", 1),
        op("set", 2, 1, fields={"title": ADD_FIELDS["title"]}),
        op("triage", 3, 1, verdict=None),
        op("archive", 4, 1),
        op("reopen", 5, 1),
        op("material", 6, 1, ref="a.md", remove=True),
        op("material", 7, 1, ref="a.md", label="a"),
        op("material", 8, 1, ref="a.md", label="a"),
        op("snooze", 9, 1, until=date(2026, 11, 1)),
        op("snooze", 10, 1, until=date(2026, 11, 1)),
        op("done", 11, 1, machine="laptop"),
        op("drop", 12, 1, note="the other machine closed it too"),
        op("triage", 13, 1, verdict="chase"),
        op("snooze", 14, 1, until=date(2026, 11, 2)),
        op("archive", 15, 1),
        op("archive", 16, 1),
        op("pin", 17, 1),
    ]
    folded = fold_item(ulid(1), lines(*made))
    assert folded.idle == {ulid(n) for n in (2, 3, 4, 5, 6, 8, 10, 12, 13, 14, 16, 17)}
    assert folded.item is not None
    assert (folded.item.state, folded.item.archived) == ("done", True)
    log = {e.op.id: e.line for e in folded.item.log}
    assert log[ulid(12)] == "dropped: the other machine closed it too (nothing to do)"
    assert log[ulid(17)] == "pin (nothing to do)"


def test_an_undo_leaves_out_one_op_and_an_undone_undo_puts_it_back() -> None:
    made = [
        op("add", 1),
        op("set", 2, 1, fields={"due": "2026-10-21"}),
        op("set", 3, 1, fields={"title": "Later title"}),
        op("undo", 4, 1, of=ulid(2)),
        op("done", 5, 1),
        op("undo", 6, 1, of=ulid(5), note="clicked by mistake"),
        op("undo", 7, 1, of=ulid(6)),
    ]
    item, _ = one(*made[:4])
    assert (item.due, item.title) == (None, "Later title")
    item, _ = one(*made)
    assert item.state == "done"
    entries = {e.op.id: e for e in item.log}
    assert (entries[ulid(2)].applied, entries[ulid(2)].undone_by) == (False, ulid(4))
    assert entries[ulid(2)].line == "due: 2026-10-21"
    assert entries[ulid(4)].line == "undo: due: 2026-10-21"
    assert entries[ulid(6)].line == "undo: done: clicked by mistake"
    assert entries[ulid(6)].undone_by == ulid(7)
    assert entries[ulid(7)].line == "undo: undo"


def test_an_undone_add_hides_its_item_but_keeps_its_number_held() -> None:
    ledger = fold(lines(op("add", 1), op("log", 2, 1, note="x"), op("undo", 3, 1, of=ulid(1)), op("add", 4)))
    assert list(ledger.items) == [ulid(4)]
    assert ledger.folds[ulid(1)].item is None
    assert ledger.folds[ulid(1)].undone == {ulid(1): ulid(3)}
    assert ledger.highest_number == 4


def test_an_undo_of_anything_but_an_earlier_op_of_its_item_is_left_out() -> None:
    made = [
        op("add", 1),
        op("renumber", 2, 1, number=9),
        op("undo", 3, 1, of=ulid(2)),
        op("undo", 4, 1, of=ulid(40)),
        op("undo", 5, 1, of=ulid(6)),
        op("done", 6, 1),
        op("add", 7),
        op("undo", 8, 1, of=ulid(7)),
    ]
    folded = fold_item(ulid(1), lines(*made[:6], made[7]))
    # Every undo's target is checked first, in order.
    assert [where.rsplit(":", 1)[1] for where, _ in folded.problems] == ["3", "4", "5", "7"]
    assert folded.item is not None
    assert (folded.item.state, folded.item.number, folded.undone) == ("done", None, {})
    assert ulid(3) not in [ln.op.id for ln in folded.lines]


def test_one_id_on_two_texts_keeps_both_by_text_and_two_adds_are_one_item() -> None:
    add = op("add", 1)
    other = op("add", 1, fields={**ADD_FIELDS, "title": "Another title"})
    log = op("log", 2, 1, note="x")
    twice = fold([line(add, 1), line(add, 2), line(log, 3), line(log, 4)])
    assert twice.problems == ()
    assert len(twice.items[ulid(1)].log) == 2
    clash = fold([line(add, 1), line(other, 2), line(log, 3)])
    first = min(add, other, key=lambda o: o.text())
    assert clash.items[ulid(1)].title == first.fields["title"]  # type: ignore[index]
    assert clash.problems[0][1] == f"op {ulid(1)} is on 2 texts; each is kept, ordered by its text"
    assert [e.applied for e in clash.items[ulid(1)].log] == [True, False, True]


def test_an_op_with_no_add_or_sorting_before_its_add_is_left_out() -> None:
    ledger = fold(lines(op("log", 1, 9, note="orphan"), op("log", 2, 3, note="early"), op("add", 3)))
    assert list(ledger.items) == [ulid(3)]
    assert [message for _, message in ledger.problems] == [
        "sorts before its item's add; left out",
        "names an item no add made; left out",
    ]
    assert len(ledger.items[ulid(3)].log) == 1


def test_the_claim_that_sorts_first_keeps_a_number_and_a_renumber_gives_the_other_one() -> None:
    a, b = op("add", 1, number=301), op("add", 2, number=301)
    ledger = fold(lines(a, b))
    assert (ledger.items[ulid(1)].number, ledger.items[ulid(2)].number) == (301, None)
    assert [item.id for item in ledger.clashes()] == [ulid(2)]
    assert ledger.holders == {301: (ulid(1), ulid(2))}
    assert ledger.problems == (("A-0301", f"held by two items; the next write gives {ulid(2)} a new number"),)
    fixed = fold(lines(a, b, op("renumber", 3, 2, number=302)))
    assert (fixed.items[ulid(2)].number, fixed.items[ulid(2)].claimed, fixed.problems) == (302, 302, ())
    assert fixed.holders == {301: (ulid(1), ulid(2)), 302: (ulid(2),)}
    renumber_clash = fold(lines(a, b, op("renumber", 3, 2, number=303), op("add", 4, number=303)))
    assert (renumber_clash.items[ulid(2)].number, renumber_clash.items[ulid(4)].number) == (303, None)


def test_a_log_line_says_the_op_in_a_few_words_and_an_import_s_shows_none() -> None:
    long = "x " * 200
    assert describe(op("set", 2, 1, fields={"outcome": long, "description": None})).startswith("outcome: x x")
    assert len(describe(op("set", 2, 1, fields={"outcome": long}))) == len("outcome: ") + 160
    assert describe(op("set", 2, 1, fields={"outcome": "short"})) == "outcome: short"
    assert describe(op("renumber", 2, 1, number=7)) == "renumbered to A-0007"
    item, _ = one(
        op("add", 1, via="import"), op("log", 2, 1, note="old entry", via="import"), op("done", 3, 1, via="import")
    )
    assert [e.line for e in item.log] == [None, "old entry", None]


def test_a_bad_undo_is_named_even_when_a_later_undo_undid_it() -> None:
    """The undo of another item's op is bad, and so is the undo that undid it: both are named and left out."""
    made = [op("add", 1), op("add", 2), op("undo", 3, 1, of=ulid(2)), op("undo", 4, 1, of=ulid(3))]
    ledger = fold(lines(*made))
    assert [where.rsplit(":", 1)[1] for where, _ in ledger.problems] == ["3", "4"]
    assert set(ledger.items) == {ulid(1), ulid(2)}
    assert ledger.folds[ulid(1)].undone == {}


def test_an_add_carrying_a_later_docket_s_field_still_makes_its_item() -> None:
    data = json.loads(op("add", 1).text())
    data["fields"]["estimate"] = 30
    read = parse(json.dumps(data) + "\n" + op("log", 2, 1, note="x").text() + "\n", "ledger/ops/2026-10-desk.jsonl")
    ledger = fold(read.lines)
    assert (read.problems, ledger.problems) == ((), ())
    assert ledger.items[ulid(1)].title == ADD_FIELDS["title"]
    assert describe(op("set", 3, 1, fields={"estimate": 30})) == "estimate: 30"


def test_only_the_add_an_item_stands_on_claims_a_number() -> None:
    """Two adds with one id on two texts and two numbers: the first by text is the item and holds its number; the
    other is idle and claims none."""
    a = op("add", 1, number=5, fields={**ADD_FIELDS, "title": "A title"})
    b = op("add", 1, number=7, fields={**ADD_FIELDS, "title": "B title"})
    first = min(a, b, key=lambda o: o.text())
    ledger = fold([line(a, 1), line(b, 2)])
    item = ledger.items[ulid(1)]
    assert (item.title, item.number) == (first.fields["title"], first.number)  # type: ignore[index]
    assert ledger.holders == {first.number: (ulid(1),)}


def test_a_redone_add_takes_its_number_back_from_an_item_another_clone_gave_it() -> None:
    """An add undone, its number given again on a clone that never saw it held, then the add redone: the first claim
    keeps the number, and the other item waits for its renumber."""
    x, undo, y = op("add", 1, number=5), op("undo", 2, 1, of=ulid(1)), op("add", 3, number=5)
    assert fold(lines(x, undo, y)).items[ulid(3)].number == 5
    ledger = fold(lines(x, undo, y, op("undo", 4, 1, of=ulid(2))))
    assert (ledger.items[ulid(1)].number, ledger.items[ulid(3)].number) == (5, None)
    assert [i.id for i in ledger.clashes()] == [ulid(3)]


def test_an_op_of_a_kind_this_docket_doesnt_know_makes_a_later_object_and_an_op_naming_nothing_is_named() -> None:
    """An op of an unknown kind whose item is its own id is a later Docket's add: it and the ops naming it are kept,
    claim no number and fold into nothing. An op naming an id no op made, or a known kind that makes no object naming
    itself, is left out and named (DR-0620)."""
    later = fold(lines(op("outbox", 1, number=7), op("log", 2, 1, note="x"), op("pin", 3, 1)))
    assert (later.items, later.meetings, later.problems, later.highest_number) == ({}, {}, (), 0)
    assert set(later.lines) == {ulid(1)}
    named = fold(lines(op("pin", 1, 9), op("log", 2, note="names itself")))
    assert [message for _, message in named.problems] == ["names an item no add made; left out"] * 2


def test_a_set_whose_fields_don_t_hold_what_an_item_s_hold_is_left_out_and_named() -> None:
    """A set's fields are checked against its object's rules when it is folded, since its line names no kind of
    object (DR-0620)."""
    item, ledger = one(op("add", 1), op("set", 2, 1, fields={"due": "soon"}), op("set", 3, 1, fields={"title": 5}))
    assert (item.due, item.title, len(item.log)) == (None, ADD_FIELDS["title"], 1)
    assert [message for _, message in ledger.problems] == [
        "a set whose fields don't hold what an item's hold; left out"
    ] * 2


def test_load_reads_and_folds_the_vault_s_ledger_with_the_cyclic_collector_paused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    newer = json.loads(op("log", 9, 1, note="x").text()) | {"schema_version": 2}
    path = tmp_path / "ledger/ops/2026-10-desk.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(f"{op('add', 1).text()}\n{json.dumps(newer)}\n", encoding="utf-8", newline="\n")
    seen: list[bool] = []
    real = fold_module.fold
    monkeypatch.setattr(fold_module, "fold", lambda found: seen.append(gc.isenabled()) or real(found))
    ledger = load(tmp_path)
    assert (seen, gc.isenabled()) == ([False], True)
    assert ledger.highest_id == ulid(9)
    assert ledger.problems == (("ledger/ops/2026-10-desk.jsonl:2", "isn't a ledger op of schema 1; skipped"),)
    gc.disable()
    try:
        load(tmp_path)
        assert not gc.isenabled()
    finally:
        gc.enable()
    assert fold_module.load(tmp_path / "none").highest_id is None
