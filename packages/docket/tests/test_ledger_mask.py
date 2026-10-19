"""A forget's mask on the ledger's op lines (DR-0693, DR-0695): a golden mask per kind, read as its kind and masked
again unchanged; a string under a name this Docket doesn't know masked at any depth; the adds of one event key still
one meeting; a material's remove still paired with its add; a line a merge brought back beside its mask one op; an op
file's text as a forget leaves it; and every reader of the ledger reading a listed op as its mask while the op file
still holds its text. Invented people, events and recordings only."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import empty_vault, make_config
from forget_kit import forget
from forget_kit import line as forget_line
from ledger_kit import ADD_FIELDS, NOW, meeting, op, point, point_set, ulid, write_ops

from docket.cli import main
from docket.index import build, rebuild
from docket.index import ledger as ledger_index
from docket.ledger import check, load, ops, store, views
from docket.ledger.ops import MARK, Op
from docket.vault import forgotten
from docket.vault.staging import digest
from docket.vault.vault import Vault

SOURCE = ADD_FIELDS["source"]
EVENT = "cal:E0001/2026-10-19T09:00"
SERIES = "cal:E0001"
REF = "plaud:rec_0001"


def located(value: str) -> str:
    return f"[forgotten:{digest(value)}]"


def test_a_locator_is_masked_as_staging_s_digest_of_it() -> None:
    assert located(SOURCE) == "[forgotten:301ad39d76ee82abf380411fa42aeff1]"
    assert ops.MARKED.fullmatch(located(SOURCE)) and ops.MARKED.fullmatch(MARK)


# Each kind as the writer writes it, and its mask as the writer would write those values.
GOLDEN: list[tuple[Op, Op]] = [
    (
        op("add", 1, fields={**ADD_FIELDS, "meeting": ulid(20), "opened": "2026-10-18", "due": "2026-10-30",
                             "description": "Dates for the pilot", "notes": "Ask twice", "outcome": "Sent"}),
        op("add", 1, fields={"kind": "reply", "title": MARK, "who": MARK, "stream": "pilot", "source": located(SOURCE),
                             "meeting": ulid(20), "opened": "2026-10-18", "due": "2026-10-30", "description": MARK,
                             "notes": MARK, "outcome": MARK}),
    ),
    (
        op("set", 2, 1, fields={"title": "Send Ada the new dates", "due": "2026-11-02", "notes": None,
                                "source": "chat:C0TEST/2"}),
        op("set", 2, 1, fields={"title": MARK, "due": "2026-11-02", "notes": None,
                                "source": located("chat:C0TEST/2")}),
    ),
    (op("log", 3, 1, note="Chased in the thread"), op("log", 3, 1, note=MARK)),
    (op("triage", 4, 1, verdict="chase", note="Her call"), op("triage", 4, 1, verdict="chase", note=MARK)),
    (op("triage", 5, 1, verdict=None), op("triage", 5, 1, verdict=None)),
    (op("snooze", 6, 1, until=date(2026, 10, 25), note="Back Monday"),
     op("snooze", 6, 1, until=date(2026, 10, 25), note=MARK)),
    (op("done", 7, 1, note="Sent on Monday"), op("done", 7, 1, note=MARK)),
    (op("drop", 8, 1, note="No longer needed"), op("drop", 8, 1, note=MARK)),
    (op("reopen", 9, 1), op("reopen", 9, 1)),
    (op("material", 10, 1, ref=REF, label="The call"), op("material", 10, 1, ref=located(REF), label=MARK)),
    (op("material", 11, 1, ref=REF, remove=True), op("material", 11, 1, ref=located(REF), remove=True)),
    (op("archive", 12, 1), op("archive", 12, 1)),
    (op("undo", 13, 1, of=ulid(4), of_batch=ulid(4), note="The wrong one"),
     op("undo", 13, 1, of=ulid(4), of_batch=ulid(4), note=MARK)),
    (op("renumber", 14, 1, number=7), op("renumber", 14, 1, number=7)),
    (
        meeting(20, EVENT, series=SERIES, continues=ulid(19), organiser="Ada Example", where="Room 4",
                response="accepted", attendees=["Ada Example", "Ben Sample"], stream="pilot", notes="Agenda first",
                outcome="Agreed"),
        meeting(20, located(EVENT), subject=MARK, series=located(SERIES), continues=ulid(19), organiser=MARK,
                where=MARK, response="accepted", attendees=[], stream="pilot", notes=MARK, outcome=MARK),
    ),
    (
        op("set", 21, 20, fields={"subject": "Pilot sync, moved", "start": "2026-10-19T10:00+02:00",
                                  "attendees": ["Cy Person"], "where": None}),
        op("set", 21, 20, fields={"subject": MARK, "start": "2026-10-19T10:00+02:00", "attendees": [],
                                  "where": None}),
    ),
    (op("log", 22, 20, note="Moved to ten"), op("log", 22, 20, note=MARK)),
    (point(23, 20, "Ada's dates", item=ulid(1), proposed=True), point(23, 20, MARK, item=ulid(1), proposed=True)),
    (point(24, 25, "Ada's dates", **{"from": ulid(23)}), point(24, 25, MARK, **{"from": ulid(23)})),
    (point_set(26, 20, 23, text="Ada's new dates", state="answered", answer="Monday"),
     point_set(26, 20, 23, text=MARK, state="answered", answer=MARK)),
    (point_set(27, 20, 23, state="rolled", to=ulid(24)), point_set(27, 20, 23, state="rolled", to=ulid(24))),
    (
        op("stream", 30, fields={"name": "pilot", "status": "active", "where": "Shared drive",
                                 "next": "Ship the dates", "deadline": "2026-12-01"}),
        op("stream", 30, fields={"name": "pilot", "status": "active", "where": MARK, "next": MARK,
                                 "deadline": "2026-12-01"}),
    ),
    (op("set", 31, 30, fields={"status": "waiting", "next": "Wait for Ada"}),
     op("set", 31, 30, fields={"status": "waiting", "next": MARK})),
]  # fmt: skip


@pytest.mark.parametrize(("raw", "golden"), GOLDEN, ids=[f"{n}-{raw.op}" for n, (raw, _) in enumerate(GOLDEN)])
def test_each_kind_masks_to_its_golden_line_reads_as_its_kind_and_masks_again_unchanged(raw: Op, golden: Op) -> None:
    masked = ops.mask(raw.text())
    assert masked == golden.text()
    assert ops.mask(masked) == masked
    read = ops.read_op(masked)
    assert (read.id, read.item, read.op, read.machine, read.day) == (raw.id, raw.item, raw.op, raw.machine, raw.day)
    # Nothing but the masked values moved: every key stays, in its place.
    assert list(json.loads(masked)) == list(json.loads(raw.text()))


def test_a_string_under_a_name_this_docket_doesn_t_know_is_masked_at_any_depth() -> None:
    later: dict[str, Any] = {
        "schema_version": 1, "id": ulid(40), "item": ulid(40), "op": "pin", "day": "2026-10-18", "machine": "desk",
        "by": "claude", "via": "cli", "colour": "Ada's red", "weight": 3,
        "fields": {"title": "Pinned by Ada", "kind": "reply", "tags": ["Ada", "pilot"], "ref": REF,
                   "deep": {"who": "Ada Example", "n": 2, "ok": True, "none": None},
                   "source": ["chat:C0TEST/3"], "attendees": "Ada Example"},
    }  # fmt: skip
    masked = json.loads(ops.mask(json.dumps(later)))
    assert masked == {
        **later, "colour": MARK,
        "fields": {"title": MARK, "kind": "reply", "tags": [MARK, MARK], "ref": MARK,
                   "deep": {"who": MARK, "n": 2, "ok": True, "none": None}, "source": [MARK], "attendees": MARK},
    }  # fmt: skip
    assert ops.read_op(ops.mask(json.dumps(later))).op == "pin"


def test_a_marker_already_written_stays_wherever_it_is() -> None:
    marked: dict[str, Any] = {
        "schema_version": 1, "id": ulid(41), "item": ulid(1), "op": "material", "day": "2026-10-18", "machine": "desk",
        "by": "claude", "via": "cli", "ref": located(REF), "label": MARK, "kept": located(EVENT), "fields": None,
    }  # fmt: skip
    text = json.dumps(marked, separators=(",", ":"))
    assert ops.mask(text) == text


# -- what the fold makes of masked ops ------------------------------------------------------------------------------


def folded(root: Path, masked: frozenset[str]) -> tuple[Any, ...]:
    ledger = load(root, masked=masked)
    meetings = sorted((m.number, m.event, m.subject) for m in ledger.meetings.values())
    items = {i.number: (i.title, tuple(m.ref for m in i.materials)) for i in ledger.items.values()}
    return meetings, items, ledger.problems


def test_the_adds_of_one_event_masked_still_make_one_meeting_and_two_events_two(tmp_path: Path) -> None:
    write_ops(tmp_path, [
        meeting(1, EVENT), meeting(2, EVENT, number=1, subject="Weekly pilot sync, moved"), meeting(3, "cal:E0002/x"),
    ])  # fmt: skip
    every = frozenset(ulid(n) for n in (1, 2, 3))
    plain, masked = folded(tmp_path, frozenset()), folded(tmp_path, every)
    assert [n for n, _, _ in plain[0]] == [n for n, _, _ in masked[0]] == [1, 3]
    assert masked[0] == [(1, located(EVENT), MARK), (3, located("cal:E0002/x"), MARK)]
    assert masked[2] == plain[2] == ()


def test_a_material_s_remove_masked_still_finds_its_add(tmp_path: Path) -> None:
    write_ops(tmp_path, [
        op("add", 1), op("material", 2, 1, ref=REF, label="The call"), op("material", 3, 1, ref=REF, remove=True),
        op("material", 4, 1, ref="plaud:rec_0002", label="The other call"),
    ])  # fmt: skip
    masked = folded(tmp_path, frozenset(ulid(n) for n in (1, 2, 3, 4)))
    assert masked[1] == {1: (MARK, (located("plaud:rec_0002"),))}
    assert folded(tmp_path, frozenset())[1] == {1: ("Send Ada the dates", ("plaud:rec_0002",))}


def test_a_line_a_merge_brought_back_beside_its_mask_reads_as_one_op(tmp_path: Path) -> None:
    (rel,) = write_ops(tmp_path, [op("add", 1), op("log", 2, 1, note="Asked Ada in the thread")])
    path = tmp_path / rel
    raw = op("log", 2, 1, note="Asked Ada in the thread").text()
    path.write_text(path.read_text(encoding="utf-8") + ops.mask(raw) + "\n", encoding="utf-8", newline="\n")
    ledger = load(tmp_path, masked=frozenset({ulid(2)}))
    (item,) = ledger.items.values()
    assert [line.op.note for line in ledger.folds[ulid(1)].lines if line.op.op == "log"] == [MARK]
    assert item.title == "Send Ada the dates"
    assert ledger.problems == ()
    # Read raw, the two texts of one id are both kept and named, as before a forget.
    assert any("on 2 texts" in what for _, what in load(tmp_path, masked=frozenset()).problems)


def test_the_list_s_ops_mask_every_read_a_folder_of_op_files_alone_reading_its_disk_s_list(tmp_path: Path) -> None:
    write_ops(tmp_path, [op("add", 1), op("log", 2, 1, note="Asked Ada")])
    assert ops.masked_ids(tmp_path) == frozenset()
    state = tmp_path / forgotten.FORGOTTEN
    state.parent.mkdir(parents=True)
    state.write_text(forget_line(ops=[ulid(2)]).line() + "\n", encoding="utf-8")
    assert ops.masked_ids(tmp_path) == {ulid(2)}
    assert [line.op.note for line in load(tmp_path).folds[ulid(1)].lines if line.op.op == "log"] == [MARK]


# -- an op file as a forget leaves it -------------------------------------------------------------------------------


def test_an_op_file_s_listed_lines_are_masked_in_place_and_a_twin_masking_made_dropped() -> None:
    add, log, other = op("add", 1).text(), op("log", 2, 1, note="Asked Ada").text(), op("log", 3, 1, note="x").text()
    text = f"{add}\n{log}\n{other}\n{ops.mask(log)}\n"
    listed = frozenset({ulid(2)})
    assert ops.masked_file(text, listed) == f"{add}\n{ops.mask(log)}\n{other}\n"
    assert [(n, raw) for n, raw, _ in ops.listed_lines(text, listed)] == [(2, log), (4, ops.mask(log))]
    # The mask first and the text after it: the first one stays.
    assert ops.masked_file(f"{ops.mask(log)}\n{log}\n", listed) == f"{ops.mask(log)}\n"
    # Nothing to change, or nothing listed: None.
    assert ops.masked_file(f"{add}\n{ops.mask(log)}\n", listed) is None
    assert ops.masked_file(text, frozenset()) is None
    assert list(ops.listed_lines(text, frozenset())) == []


def test_an_op_line_is_found_by_its_own_id_whatever_its_key_order_and_never_by_an_id_inside_it() -> None:
    log = op("log", 2, 1, note="Asked Ada")
    data = json.loads(log.text())
    reordered = json.dumps({"note": data["note"], **{k: v for k, v in data.items() if k != "note"}})
    nested = op("set", 5, 1, fields={"later": {"id": ulid(2), "said": "Asked Ada"}}).text()
    cut = log.text()[:60]
    text = f"{reordered}\n{nested}\n{cut}\nnot json\n[1]\n"
    assert [n for n, _, _ in ops.listed_lines(text, frozenset({ulid(2)}))] == [1]
    assert ops.masked_file(text, frozenset({ulid(2)})) == f"{ops.mask(reordered)}\n{nested}\n{cut}\nnot json\n[1]\n"


# -- every reader -------------------------------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Vault:
    v = empty_vault(tmp_path, make_config())
    monkeypatch.setenv("DOCKET_VAULT", str(v.root))
    monkeypatch.setattr(views, "now", lambda: NOW)
    return v


def rows(vault: Vault) -> str:
    conn = sqlite3.connect(build.db_path(vault))
    try:
        return repr([conn.execute(f"SELECT * FROM {t}").fetchall() for t in ledger_index.TABLES])  # noqa: S608
    finally:
        conn.close()


def test_every_reader_of_the_ledger_reads_a_listed_op_as_its_mask_while_its_file_holds_the_text(
    vault: Vault, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    said = "Ada said Tuesday at the venue"
    rebuild(vault)
    store.write(vault, [{"op": "add", "fields": dict(ADD_FIELDS)}], now=NOW)
    (log,) = store.write(vault, [{"op": "log", "item": "A-0001", "note": said}], now=NOW).ops
    assert main(["index", "update", "--no-models"]) == 0
    assert said in rows(vault)
    forget(vault, ops=[log.id])
    path = vault.root / ops.rel_of(log)
    # The apply hasn't run: the file holds the text, and vault check names the line.
    assert said in path.read_text(encoding="utf-8")
    assert check.unmasked(vault.root, {log.id}) == [(f"{ops.rel_of(log)}:2", check.UNMASKED)]
    assert [line.op.note for line in load(vault.root).folds[log.item].lines if line.op.op == "log"] == [MARK]
    # The index was built before the list moved: the write folds in memory, masked, and goes through.
    store.write(vault, [{"op": "log", "item": "A-0001", "note": "Booked"}], now=NOW)
    with build.ledger_tables(vault) as conn:
        history = [c.line for c in ledger_index.find(conn, "A-0001").history]
    assert said not in repr(history) and MARK in repr(history) and "Booked" in repr(history)
    assert main(["ledger", "show", "A-0001"]) == 0
    out = capsys.readouterr().out
    assert said not in out and MARK in out
    assert main(["index", "update", "--no-models"]) == 0
    assert said not in rows(vault) and "Booked" in rows(vault)
    # As the apply leaves the file, and then with a pull bringing the text back after its mask: the update reads only
    # the line the file grew by, as that op's mask.
    path.write_text(path.read_text(encoding="utf-8").replace(log.text(), ops.mask(log.text())), encoding="utf-8",
                    newline="\n")  # fmt: skip
    assert main(["index", "update", "--no-models"]) == 0
    path.write_text(path.read_text(encoding="utf-8") + log.text() + "\n", encoding="utf-8", newline="\n")

    def whole(*_: object) -> int:
        raise AssertionError("the update folded every op file again")

    monkeypatch.setattr(ledger_index, "rebuild", whole)
    assert main(["index", "update", "--no-models"]) == 0
    assert said not in rows(vault)
