"""The ledger's writer on the index and `docket ledger show` (DR-0616, DR-0619): a write reads the index's ledger
tables brought up to date and folds no op file whole; an index it can't have soon, or one of another build, leaves
it to a fold in memory; `show` names an item by its number or its id with its fields and its history."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import empty_vault, make_config
from ledger_kit import ADD_FIELDS, NOW, caught_up, op, rebuilt, tables, ulid, write_ops

from docket.cli import main
from docket.errors import DocketError
from docket.index import build, rebuild
from docket.index import ledger as ledger_index
from docket.index.schema import SCHEMA_VERSION
from docket.ledger import fold, store, views
from docket.vault import forgotten
from docket.vault.vault import Vault

# Forced: these tests add the one ask more than once on purpose, past the look-alike check (DR-0642).
ADD: dict[str, Any] = {"op": "add", "fields": dict(ADD_FIELDS), "force": True}


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Vault:
    v = empty_vault(tmp_path, make_config())
    monkeypatch.setenv("DOCKET_VAULT", str(v.root))
    return v


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """A read's today, so an item's days open don't hang on the day the tests run."""
    monkeypatch.setattr(views, "now", lambda: NOW)


@pytest.fixture
def indexed(vault: Vault) -> Vault:
    rebuild(vault)
    return vault


def show(capsys: pytest.CaptureFixture[str], ref: str) -> tuple[int, list[str], str]:
    code = main(["ledger", "show", ref])
    out, err = capsys.readouterr()
    return code, out.splitlines(), err.strip()


def index_rows(vault: Vault) -> dict[str, list[tuple[Any, ...]]]:
    conn = sqlite3.connect(build.db_path(vault))
    try:
        return tables(conn)
    finally:
        conn.close()


# -- the writer on the index ------------------------------------------------------------------------------------


def test_a_write_reads_the_index_and_folds_no_op_file_whole(indexed: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    store.write(indexed, [ADD], now=NOW)
    real = fold.load

    def whole(*_: object) -> None:
        raise AssertionError("a write folded every op file")

    monkeypatch.setattr(ledger_index, "load", whole)
    (log,) = store.write(indexed, [{"op": "log", "item": "A-0001", "note": "seen"}], now=NOW + timedelta(1)).ops
    assert index_rows(indexed)["ledger_files"][0][0] == "ledger/ops/2026-10-test-box.jsonl"
    store.write(indexed, [ADD, {"op": "done", "item": "A-0001"}], now=NOW + timedelta(2))
    monkeypatch.setattr(ledger_index, "load", real)
    assert caught_up(indexed) == rebuilt(indexed.root)
    assert log.after == log.item


def test_an_index_of_another_build_or_busy_past_the_wait_leaves_the_write_to_a_fold_in_memory(
    indexed: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build, "LEDGER_WAIT", 0.05)
    store.write(indexed, [ADD], now=NOW)
    before = index_rows(indexed)
    lock = build.index_lock(indexed)
    with lock:
        store.write(indexed, [{"op": "log", "item": "A-0001", "note": "while a rebuild runs"}], now=NOW)
    holder = sqlite3.connect(build.db_path(indexed), isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        store.write(indexed, [{"op": "log", "item": "A-0001", "note": "while an update writes"}], now=NOW)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    conn = sqlite3.connect(build.db_path(indexed))
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION - 1}")
    conn.commit()
    conn.close()
    (done,) = store.write(indexed, [{"op": "done", "item": "A-0001"}], now=NOW).ops
    # None of the four wrote the index, and each read the item it changed from the op files.
    assert index_rows(indexed) == before
    with build.ledger_tables(indexed) as tables_now:
        assert [c.line for c in ledger_index.find(tables_now, "A-0001").history][1:] == [
            "while a rebuild runs", "while an update writes", "done",
        ]  # fmt: skip
    assert done.after is not None


def test_a_forgotten_list_that_doesn_t_read_stops_the_write_and_show_naming_its_line(
    indexed: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every reader of the ledger reads a listed op as its mask (DR-0695), so with the list unread, the index's tables
    and a fold in memory alike could show what a forget masked: the ledger fails closed, as every reader of raw does
    (DR-0579), and nothing is written."""
    store.write(indexed, [ADD], now=NOW)
    before = index_rows(indexed)
    path = indexed.root / "ledger/ops/2026-10-test-box.jsonl"
    text = path.read_text(encoding="utf-8")
    forgotten.list_path(indexed.root).write_text("not a forget\n", encoding="utf-8")
    with pytest.raises(DocketError, match="line 1 isn't a forget"):
        store.write(indexed, [{"op": "log", "item": "A-0001", "note": "never written"}], now=NOW)
    assert path.read_text(encoding="utf-8") == text
    assert index_rows(indexed) == before
    code, _, err = show(capsys, "A-0001")
    assert code != 0 and "line 1 isn't a forget" in err


def test_a_failure_while_the_index_catches_up_lets_its_lock_go(indexed: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_: object) -> int:
        raise RuntimeError("halfway")

    monkeypatch.setattr(ledger_index, "update", broken)
    with pytest.raises(RuntimeError, match="halfway"):
        store.write(indexed, [ADD], now=NOW)
    lock = build.index_lock(indexed)
    lock.acquire(timeout=0)
    lock.release()
    assert index_rows(indexed)["items"] == []


def test_an_index_update_folds_what_the_writes_added(indexed: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    store.write(indexed, [ADD, {"op": "triage", "item": "A-0001", "verdict": "chase"}], now=NOW)
    assert main(["index", "update", "--no-models"]) == 0
    out = capsys.readouterr().out
    assert ": 0 run file(s), 0 doc(s), 2 ledger op(s)" in out and "items 1" in out
    assert main(["index", "rebuild", "--no-models"]) == 0
    assert "items 1" in capsys.readouterr().out


# -- docket ledger show -----------------------------------------------------------------------------------------


@pytest.fixture(params=["memory", "index"])
def where(request: pytest.FixtureRequest, vault: Vault) -> Iterator[str]:
    if request.param == "index":
        rebuild(vault)
    yield request.param


def test_show_names_an_item_by_its_number_or_its_id_with_its_fields_and_history(
    vault: Vault, where: str, capsys: pytest.CaptureFixture[str]
) -> None:
    fields = {"due": "2026-10-21", "description": "the pilot's dates", "notes": "line one\nline two",
              "outcome": "sent them"}  # fmt: skip
    (add, *_) = store.write(vault, [
        ADD,
        {"op": "set", "item": "A-0001", "fields": fields},
        {"op": "triage", "item": "A-0001", "verdict": "chase"},
        {"op": "material", "item": "A-0001", "ref": "docs/plan.md", "label": "the plan"},
        {"op": "material", "item": "A-0001", "ref": "https://example.invalid/x"},
    ], by="user", via="page", now=NOW).ops  # fmt: skip
    code, out, _ = show(capsys, "a-1")
    assert code == 0
    assert out == [
        "A-0001  Send Ada the dates",
        f"id           {add.id}",
        "kind         reply",
        "state        open, triage chase",
        "who          Ada Example",
        "stream       pilot",
        f"source       {ADD_FIELDS['source']}",
        "opened       2026-10-18, by user",
        "due          2026-10-21",
        "description  the pilot's dates",
        "notes        line one",
        "             line two",
        "outcome      sent them",
        "material     docs/plan.md  the plan",
        "material     https://example.invalid/x",
        "counts       open 0 days; chased 1 time, snoozed 0 times, due moved 1 time, reopened 0 times",
        "history      2026-10-18 11:14  user via page  added",
        "             2026-10-18 11:14  user via page  due: 2026-10-21; description edited; notes edited; "
        "outcome: sent them",
        "             2026-10-18 11:14  user via page  triage: chase",
        "             2026-10-18 11:14  user via page  material added: the plan",
        "             2026-10-18 11:14  user via page  material added: https://example.invalid/x",
    ]
    assert show(capsys, add.id)[1] == out


def test_show_says_each_state_and_marks_an_undone_change(
    vault: Vault, where: str, capsys: pytest.CaptureFixture[str]
) -> None:
    store.write(vault, [ADD, ADD, ADD], now=NOW)
    (snooze,) = store.write(vault, [{"op": "snooze", "item": "A-0001", "until": "2026-10-25"}], now=NOW).ops
    store.write(vault, [{"op": "done", "item": "A-0002", "note": "answered"}, {"op": "archive", "item": "A-0002"},
                        {"op": "drop", "item": "A-0003", "note": "not ours"}], now=NOW)  # fmt: skip
    assert show(capsys, "A-0001")[1][3] == "state        snoozed until 2026-10-25"
    assert show(capsys, "A-0002")[1][3] == "state        done on 2026-10-18, archived"
    assert show(capsys, "A-0003")[1][3] == "state        dropped on 2026-10-18"
    (undo,) = store.write(vault, [{"op": "undo", "of": snooze.id, "note": "too early"}],
                          now=NOW + timedelta(minutes=1)).ops  # fmt: skip
    out = show(capsys, "A-0001")[1]
    assert out[3] == "state        open"
    assert out[-2:] == [
        f"             2026-10-18 11:14  claude via cli  snoozed to 2026-10-25  (undone by {undo.id})",
        "             2026-10-18 11:15  claude via cli  undo: snoozed to 2026-10-25: too early",
    ]


def test_show_leaves_out_an_imported_change_s_words_and_a_time_it_never_had(
    vault: Vault, where: str, capsys: pytest.CaptureFixture[str]
) -> None:
    write_ops(vault.root, [
        op("add", 1, via="import", by="user"),
        op("set", 2, 1, via="import", by="user", fields={"due": "2026-10-20"}),
        op("log", 3, 1, via="import", by="user", note="asked in the thread"),
    ])  # fmt: skip
    out = show(capsys, "A-0001")[1]
    assert out[-1] == "history      2026-10-18        user via import  asked in the thread"
    assert "due          2026-10-20" in out


def test_a_number_two_items_held_shows_its_holder_and_names_the_other(
    vault: Vault, where: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Another clone's add sorts before ours and claims A-0001 too: it holds the number, ours waits, and each names the
    other until the next write renumbers ours (DR-0613)."""
    (ours,) = store.write(vault, [ADD], now=NOW).ops
    write_ops(vault.root, [op("add", 5, number=1, machine="laptop", fields={**ADD_FIELDS, "title": "Theirs"})])
    out = show(capsys, "A-0001")[1]
    assert out[0] == "A-0001  Theirs"
    assert f"also held    A-0001 by {ours.id}, waiting for a new number" in out
    mine = show(capsys, ours.id)[1]
    assert mine[0] == "no number  Send Ada the dates"
    assert mine[4] == "number       A-0001 went to an earlier claim; the next write gives a new one"
    store.write(vault, [], now=NOW + timedelta(minutes=1))
    assert f"also held    A-0001 by {ours.id}, now A-0002" in show(capsys, "A-0001")[1]
    assert show(capsys, "A-0002")[1][0] == "A-0002  Send Ada the dates"


def test_a_number_none_holds_now_names_who_held_it(
    vault: Vault, where: str, capsys: pytest.CaptureFixture[str]
) -> None:
    write_ops(vault.root, [
        op("add", 1, number=5), op("renumber", 2, 1, number=9), op("add", 3, number=5),
        op("add", 4, number=7), op("renumber", 6, 4, number=8),
    ])  # fmt: skip
    code, _, err = show(capsys, "A-0005")
    assert code == 2
    assert err == (
        f"docket: A-0005 isn't held now; it was held by {ulid(1)} (now A-0009), {ulid(3)} (waiting for a new "
        "number); name the item by its id"
    )
    assert show(capsys, "A-0007")[1][0] == "A-0008  Send Ada the dates"


@pytest.mark.parametrize(
    ("ref", "said"),
    [
        ("A-0099", "no item A-0099"),
        ("A-0003", "A-0003 was undone"),
        (ulid(3), f"{ulid(3)} was undone"),
        (ulid(40), f"no item {ulid(40)}"),
        (
            "B-1",
            "'B-1' isn't an item, a meeting or a stream: name one by its A- or M- number, its id, or a stream's name",
        ),
    ],
)
def test_show_refuses_what_names_no_item(
    vault: Vault, where: str, capsys: pytest.CaptureFixture[str], ref: str, said: str
) -> None:
    write_ops(vault.root, [op("add", 1), op("add", 3), op("undo", 4, 3, of=ulid(3))])
    assert show(capsys, ref)[::2] == (2, f"docket: {said}")
