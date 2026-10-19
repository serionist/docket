"""The ledger's one writer (DR-0610, DR-0612, DR-0613, DR-0614, DR-0618): what a write writes and commits, what it
refuses, the text rules, undo, renumbering a clash, and putting things back when a commit fails. Each runs twice:
with no index, the writer folding the op files in memory, and with one, the writer reading its ledger tables
brought up to date (DR-0616, DR-0619)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import commit_config, empty_vault, folder_vault, make_config, make_record, sh
from ledger_kit import ADD_FIELDS, NOW, caught_up, rebuilt

from docket.errors import DocketError
from docket.index import build as index_build
from docket.index import rebuild
from docket.ledger import imports, load, ops, store
from docket.ledger.fold import Item, Ledger, fold
from docket.ledger.ops import Op
from docket.ledger.store import LedgerError, material_ref, write
from docket.vault import forgotten
from docket.vault.config import RedactionPattern, SourceConfig, VaultConfig
from docket.vault.gate import Gate
from docket.vault.vault import UnmergedError, Vault

FILE = "ledger/ops/2026-10-test-box.jsonl"
# Forced: these tests add the one ask more than once on purpose, past the look-alike check (DR-0642).
ADD: dict[str, Any] = {"op": "add", "fields": dict(ADD_FIELDS), "force": True}


@pytest.fixture(params=["memory", "index"])
def vault(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[Vault]:
    """A new vault: with no index, or with one, whose ledger tables then end as a fold of the op files leaves them."""
    v = empty_vault(tmp_path, make_config())
    if request.param == "memory":
        yield v
        return
    rebuild(v)
    yield v
    assert caught_up(v) == rebuilt(v.root)


def ledger(vault: Vault) -> Ledger:
    return fold(ops.read_all(vault.root).lines)


def item(vault: Vault, number: int) -> Item:
    return next(i for i in ledger(vault).items.values() if i.number == number)


def message(vault: Vault) -> str:
    return sh(vault.root, "git", "log", "-1", "--format=%B").strip()


def later(minutes: int) -> datetime:
    return NOW + timedelta(minutes=minutes)


def test_an_add_is_one_line_in_this_machine_s_month_file_committed_with_its_number(vault: Vault) -> None:
    written = write(vault, [ADD, {"op": "triage", "item": "A-0001", "verdict": "chase", "note": " asked\nagain "}],
                    now=NOW)  # fmt: skip
    add, triage = written.ops
    assert (add.op, add.number, add.item, add.batch, add.after, add.by, add.via) == (
        "add", 1, add.id, add.id, None, "claude", "cli",
    )  # fmt: skip
    assert (triage.item, triage.batch, triage.after, triage.note) == (add.id, add.id, add.id, "asked again")
    assert (add.day, add.at, add.machine) == (date(2026, 10, 18), NOW, "test-box")
    assert (vault.root / FILE).read_text(encoding="utf-8") == f"{add.text()}\n{triage.text()}\n"
    assert written.commit is not None
    assert written.numbers == {add.id: 1}
    assert message(vault) == "Ledger: add A-0001, triage A-0001 on test-box"
    assert sh(vault.root, "git", "status", "--porcelain").strip() == ""
    assert item(vault, 1).triage == "chase"


def test_a_write_s_day_is_the_owner_s_in_vault_yaml_s_zone(vault: Vault) -> None:
    late = datetime(2026, 10, 31, 23, 30, tzinfo=UTC)
    (add,) = write(vault, [ADD], now=late).ops
    assert add.day == date(2026, 11, 1)
    assert (vault.root / "ledger/ops/2026-11-test-box.jsonl").is_file()


def test_changes_name_an_item_by_its_number_or_its_id(vault: Vault) -> None:
    (add,) = write(vault, [ADD], now=NOW).ops
    for ref in ("A-0001", "a-1", "1", add.id):
        write(vault, [{"op": "log", "item": ref, "note": f"by {ref}"}], now=NOW)
    assert [e.line for e in item(vault, 1).log][1:] == ["by A-0001", "by a-1", "by 1", f"by {add.id}"]


def test_a_dry_run_says_what_it_would_write_and_writes_nothing(vault: Vault) -> None:
    head = sh(vault.root, "git", "rev-parse", "HEAD")
    written = write(vault, [ADD], now=NOW, dry_run=True)
    assert [o.op for o in written.ops] == ["add"]
    assert (written.commit, (vault.root / FILE).exists()) == (None, False)
    assert sh(vault.root, "git", "rev-parse", "HEAD") == head


def test_a_change_that_changes_nothing_writes_no_op(vault: Vault) -> None:
    write(vault, [ADD, {**ADD, "op": "add"}, {"op": "triage", "item": "A-0001", "verdict": "me"}], now=NOW)
    write(vault, [{"op": "snooze", "item": "A-0002", "until": "2026-10-25"}], now=NOW)
    nothing = write(
        vault,
        [
            {"op": "set", "item": "A-0001", "fields": {"title": ADD_FIELDS["title"], "due": None}},
            {"op": "triage", "item": "A-0001", "verdict": "me"},
            {"op": "snooze", "item": "A-0002", "until": "2026-10-25"},
        ],
        now=NOW,
    )
    assert (nothing.ops, nothing.commit, nothing.numbers, nothing.changes) == ((), None, {}, ((), (), ()))
    assert write(vault, [], now=NOW).ops == ()
    (later_day,) = write(vault, [{"op": "snooze", "item": "A-0002", "until": "2026-10-26"}], now=NOW).ops
    assert later_day.until == date(2026, 10, 26)


def test_each_change_is_checked_against_the_items_as_the_write_leaves_them(vault: Vault) -> None:
    write(
        vault,
        [
            ADD,
            {"op": "set", "item": "A-0001", "fields": {"due": "2026-10-21", "notes": "line one\r\n\nline two  \n"}},
            {"op": "snooze", "item": "A-0001", "until": "2026-10-25"},
            {"op": "triage", "item": "A-0001", "verdict": "claude"},
            {"op": "triage", "item": "A-0001", "verdict": "-"},
            {"op": "material", "item": "A-0001", "ref": "docs/plan.md", "label": "the plan"},
            {"op": "material", "item": "A-0001", "ref": "docs/plan.md", "label": "the plan, v2"},
            {"op": "material", "item": "A-0001", "ref": "https://example.invalid/x"},
            {"op": "material", "item": "A-0001", "ref": "https://example.invalid/x", "remove": True},
            {"op": "done", "item": "A-0001", "note": "answered"},
            {"op": "archive", "item": "A-0001"},
            {"op": "reopen", "item": "A-0001"},
            {"op": "drop", "item": "A-0001", "note": "not ours after all"},
        ],
        now=NOW,
    )
    it = item(vault, 1)
    assert (it.state, it.archived, it.due, it.notes) == ("dropped", False, date(2026, 10, 21), "line one\n\nline two")
    assert [(m.ref, m.label) for m in it.materials] == [("docs/plan.md", "the plan, v2")]
    assert message(vault) == "Ledger: add A-0001 and 12 more on test-box"


REFUSALS: list[tuple[dict[str, Any], str]] = [
    ({"op": "nope"}, "op is one of add, set"),
    ({"op": 5}, "op is one of"),
    ({"op": "renumber", "item": "A-0001", "number": 9}, "op is one of"),
    ({"op": "log", "item": "A-0001", "note": "x", "colour": "red"}, "log takes only item, note, by, not colour"),
    ({"op": "log", "item": "A-0001", "note": 5}, "note is text"),
    ({"op": "log", "item": "A-0001"}, "a log needs a note"),
    ({"op": "log", "item": "A-0001", "note": "  \n "}, "a log needs a note"),
    ({"op": "log", "note": "x"}, "it needs item"),
    ({"op": "log", "item": "B-1", "note": "x"}, "item is an A- number or an item's id"),
    ({"op": "log", "item": "A-0099", "note": "x"}, "no item A-0099"),
    ({"op": "log", "item": "01JAB3W8Q4X5M2Z7K9R0T6YVNC", "note": "x"}, "no item 01JAB3W8Q4X5M2Z7K9R0T6YVNC"),
    ({"op": "add"}, "it needs fields"),
    ({"op": "add", "fields": {"title": "x"}}, "an add needs kind"),
    ({"op": "add", "fields": {**ADD_FIELDS, "colour": "red"}}, "there is no field colour"),
    ({"op": "add", "fields": {**ADD_FIELDS, "kind": "chore"}}, "kind is one of reply, decision"),
    ({"op": "add", "fields": {**ADD_FIELDS, "stream": "Pilot X"}}, "stream is a slug"),
    ({"op": "add", "fields": {**ADD_FIELDS, "due": "21 Oct"}}, "due is a day"),
    ({"op": "add", "fields": {**ADD_FIELDS, "title": 5}}, "title is text"),
    ({"op": "set", "item": "A-0001", "fields": {}}, "it needs fields"),
    ({"op": "set", "item": "A-0001", "fields": {"title": "-"}}, "title can't be cleared"),
    ({"op": "set", "item": "A-0001", "fields": {"opened": ""}}, "opened can't be cleared"),
    ({"op": "triage", "item": "A-0001", "verdict": "soon"}, "verdict is one of discuss, claude, me, chase"),
    ({"op": "triage", "item": "A-0002", "verdict": "me"}, "A-0002 is done; reopen it first"),
    ({"op": "snooze", "item": "A-0001", "until": "2026-10-18"}, "a day after today"),
    ({"op": "snooze", "item": "A-0001", "until": 5}, "a day after today"),
    ({"op": "snooze", "item": "A-0001", "until": "2026-13-01"}, "a day after today"),
    ({"op": "snooze", "item": "A-0002", "until": "2026-11-01"}, "A-0002 is done; reopen it first"),
    ({"op": "done", "item": "A-0002"}, "A-0002 is already done"),
    ({"op": "drop", "item": "A-0001"}, "a drop needs a note"),
    ({"op": "reopen", "item": "A-0001"}, "A-0001 is already open"),
    ({"op": "archive", "item": "A-0001"}, "an archive takes a closed item that isn't archived, and A-0001 is open"),
    ({"op": "archive", "item": "A-0003"}, "and A-0003 is archived"),
    ({"op": "material", "item": "A-0001"}, "a material needs ref"),
    ({"op": "material", "item": "A-0001", "ref": "a.md", "remove": "yes"}, "remove is true or false"),
    ({"op": "material", "item": "A-0001", "ref": "a.md", "label": 5}, "remove is true or false"),
    ({"op": "material", "item": "A-0001", "ref": "a.md", "remove": True}, "has no material at that ref"),
    ({"op": "material", "item": "A-0001", "ref": "./docs\\plan.md", "label": "plan"}, "has that material already"),
    ({"op": "undo"}, "an undo needs of, an op's id, or batch"),
    ({"op": "undo", "of": "x", "batch": "x"}, "an undo needs of"),
    ({"op": "undo", "of": 5}, "an undo needs of"),
    ({"op": "undo", "of": "01JAB3W8Q4X5M2Z7K9R0T6YVNC"}, "no op 01JAB3W8Q4X5M2Z7K9R0T6YVNC"),
    ({"op": "undo", "batch": "01JAB3W8Q4X5M2Z7K9R0T6YVNC"}, "no op of batch 01JAB3W8Q4X5M2Z7K9R0T6YVNC to undo"),
]


def test_a_change_that_can_t_be_made_refuses_the_whole_write(vault: Vault) -> None:
    write(
        vault,
        [
            ADD,
            {**ADD, "op": "add"},
            {"op": "done", "item": "A-0002"},
            {**ADD, "op": "add"},
            {"op": "done", "item": "A-0003"},
            {"op": "archive", "item": "A-0003"},
            {"op": "material", "item": "A-0001", "ref": "docs/plan.md", "label": "plan"},
        ],
        now=NOW,
    )
    head = sh(vault.root, "git", "rev-parse", "HEAD")
    before = (vault.root / FILE).read_bytes()
    for change, why in REFUSALS:
        kind = change.get("op")
        with pytest.raises(LedgerError, match=rf"^change 2 \({kind}\): .*{_re(why)}.*; nothing written$"):
            write(vault, [{"op": "log", "item": "A-0001", "note": "first"}, change], now=NOW)
    with pytest.raises(LedgerError, match="by is one of user, claude, sync"):
        write(vault, [ADD], by="me", now=NOW)  # type: ignore[arg-type]
    assert (vault.root / FILE).read_bytes() == before
    assert sh(vault.root, "git", "rev-parse", "HEAD") == head


def _re(text: str) -> str:
    return "".join(f"\\{c}" if c in "().*+?[]\\^$|{}" else c for c in text)


def configured(vault: Vault, **never: list[str]) -> Vault:
    path = vault.root / "vault.yaml"
    config = VaultConfig.load(path)
    for name, values in never.items():
        setattr(config.never_store, name, values)
    path.write_text(config.dump(), encoding="utf-8", newline="\n")
    commit_config(vault.root)
    return Vault.open(vault.root)


def test_text_is_redacted_and_a_locator_a_keyword_or_a_listed_person_refuses_the_write(vault: Vault) -> None:
    vault = configured(vault, keywords=["Project Nebula"], people=["Dora Example"])
    fields = {**ADD_FIELDS, "title": "password: hunter2-Example", "notes": "the key: password=hunter2-Example"}
    (add,) = write(vault, [{"op": "add", "fields": fields}], now=NOW).ops
    assert add.fields == {**ADD_FIELDS, "title": "password: [redacted:password]",
                          "notes": "the key: password=[redacted:password]"}  # fmt: skip
    assert "hunter2" not in (vault.root / FILE).read_text(encoding="utf-8")
    refused = [
        ({"op": "add", "fields": {**ADD_FIELDS, "source": "https://ada:hunter2-Example@example.invalid/x"}},
         "source matches the password redaction rule, and a locator can't hold a marker"),
        ({"op": "material", "item": "A-0001", "ref": "https://example.invalid/x?token=hunter2-Example"},
         "ref matches the api-key redaction rule"),
        ({"op": "log", "item": "A-0001", "note": "about project nebula"},
         "note says something on the never-store keywords list"),
        ({"op": "add", "fields": {**ADD_FIELDS, "who": "Dora Example"}},
         "who names someone on the never-store people list"),
        ({"op": "material", "item": "A-0001", "ref": "a.md", "label": "Project Nebula deck"}, "label says something"),
    ]  # fmt: skip
    for change, why in refused:
        with pytest.raises(LedgerError, match=_re(why)) as caught:
            write(vault, [change], now=NOW)
        assert "hunter2" not in str(caught.value) and "ebula" not in str(caught.value)


# -- never-store containers (DR-0690) -----------------------------------------------------------------------------

INTO = "points into a never-store container; leave it out"
# A DM and a chat by their ids, and a thread by its parent's rid, as a never-store list names them.
CONTAINERS = ["D0PRIVATE", "teams:19:private@thread.v2", "slack:C0PILOT/1791000001.000100"]


def test_a_source_or_a_ref_in_a_never_store_container_refuses_the_write_naming_the_field(vault: Vault) -> None:
    """A Slack or Teams rid carries its conversation: found by the rid's text up to each slash, after `r:` and an
    anchor come off, never naming the value."""
    vault = configured(vault, containers=CONTAINERS)
    write(vault, [ADD], now=NOW)
    refused = [
        ({**ADD, "fields": {**ADD_FIELDS, "source": "slack:D0PRIVATE/1791000005.000100"}}, "source"),
        ({**ADD, "fields": {**ADD_FIELDS, "source": "r:slack:D0PRIVATE/1791000005.000100#2"}}, "source"),
        ({"op": "set", "item": "A-0001", "fields": {"source": "[r:teams:19:private@thread.v2/1791000005000#1]"}},
         "source"),
        ({"op": "material", "item": "A-0001", "ref": "teams:19:private@thread.v2/1791000005000"}, "ref"),
        ({"op": "material", "item": "A-0001", "ref": "r:slack:C0PILOT/1791000001.000100@00:12"}, "ref"),
    ]  # fmt: skip
    for change, name in refused:
        with pytest.raises(LedgerError, match=_re(f"{name} {INTO}")) as caught:
            write(vault, [change], now=NOW)
        assert not any(part in str(caught.value) for part in ("PRIVATE", "private", "1791"))
    (dry,) = write(vault, [refused[0][0]], dry_run=True, now=NOW).refused
    assert (dry.n, dry.why) == (1, f"source {INTO}")
    # Another chat, a reply whose rid doesn't carry its listed thread, and a channel that only starts like a DM.
    written = write(vault, [
        {**ADD, "fields": {**ADD_FIELDS, "source": "teams:19:public@thread.v2/1791000005000"}},
        {"op": "material", "item": "A-0001", "ref": "slack:C0PILOT/1791000002.000100"},
        {"op": "material", "item": "A-0001", "ref": "slack:D0PRIVATE2/1791000005.000100"},
    ], now=later(1))  # fmt: skip
    assert len(written.ops) == 3


def test_a_locator_s_rid_comes_without_r_its_brackets_and_its_position() -> None:
    assert store.locator_rid(" [r:slack:D0X/1791000005.000100#2-3] ") == "slack:D0X/1791000005.000100"
    assert store.locator_rid("r:plaud:rec-01/transcript@12:08-12:40") == "plaud:rec-01/transcript"
    assert store.locator_rid("r:mail:a%40b") == "mail:a@b"
    assert store.locator_rid("teams:19:x@thread.v2/1791000005000") == "teams:19:x@thread.v2/1791000005000"
    assert store.locator_rid("the pilot channel, 14 Oct") == "the pilot channel, 14 Oct"


def kept_and_dropped(tmp_path: Path) -> Vault:
    """A vault whose mail source kept one conversation and dropped a listed one at the gate, with a Slack thread in
    raw: the index built, the kept conversation and the thread listed afterwards."""
    config = make_config()
    config.sources |= {"mail": SourceConfig(), "slack": SourceConfig()}
    config.never_store.containers = ["AAMkADconv9"]
    vault = empty_vault(tmp_path, config)
    mails = [
        make_record(rid=f"mail:AAMkADmsg{n}", source="mail", kind="mail", container=None,
                    thread=f"mail:AAMkADconv{n}", title="The pilot's dates", author="Ben Sample <ben@example.invalid>")
        for n in (1, 9)
    ]  # fmt: skip
    staged = Gate(vault).stage(mails)
    assert (staged.ready, staged.dropped["never-store"]) == (1, 1)
    Gate(vault).promote()
    parent = make_record(1, rid="slack:C0PILOT/1791000001.000100", source="slack", container="slack:C0PILOT")
    reply = make_record(2, rid="slack:C0PILOT/1791000002.000100", source="slack", container="slack:C0PILOT",
                        thread=parent.rid)  # fmt: skip
    vault.write_run("slack", [parent, reply], NOW)
    rebuild(vault)
    return configured(vault, containers=["AAMkADconv9", "AAMkADconv1", parent.rid])


def test_the_index_names_the_container_or_thread_a_rid_doesn_t_carry_and_a_dropped_mail_is_written(
    tmp_path: Path,
) -> None:
    """For any source, the container and thread the index holds for the rid: a mail rid doesn't carry its
    conversation, nor a reply its thread. A mail the gate dropped never reached the index, so its locator names
    nothing Docket keeps and is written (DR-0690)."""
    vault = kept_and_dropped(tmp_path)
    assert index_build.locate(vault, "mail:AAMkADmsg9") is None
    write(vault, [ADD], now=NOW)
    for change, name in (
        ({**ADD, "fields": {**ADD_FIELDS, "source": "mail:AAMkADmsg1"}}, "source"),
        ({"op": "material", "item": "A-0001", "ref": "[r:slack:C0PILOT/1791000002.000100#1]"}, "ref"),
    ):
        with pytest.raises(LedgerError, match=_re(f"{name} {INTO}")):
            write(vault, [change], now=NOW)
    (add,) = write(vault, [{**ADD, "fields": {**ADD_FIELDS, "source": "mail:AAMkADmsg9"}}], now=later(1)).ops
    assert add.fields is not None and add.fields["source"] == "mail:AAMkADmsg9"


def test_the_import_s_dry_run_names_each_line_it_would_refuse_by_its_field(tmp_path: Path) -> None:
    vault = kept_and_dropped(tmp_path)
    lines = []
    for n, source in enumerate(("slack:D0PRIVATE/1791000005.000100", "mail:AAMkADmsg1", "mail:AAMkADmsg9"), start=1):
        add_id = ops.encode(((imports.ADDS_AT + n) << ops.RANDOM_BITS) | 7)
        fields = {**ADD_FIELDS, "title": f"Ask {n}", "source": source, "opened": "2026-10-14"}
        lines.append({"schema_version": 1, "id": add_id, "item": add_id, "op": "add", "day": "2026-10-14",
                      "by": "claude", "via": "import", "number": n, "fields": fields})  # fmt: skip
    vault = configured(vault, containers=[*vault.config.never_store.containers, "D0PRIVATE"])
    text = "".join(json.dumps(line) + "\n" for line in lines)
    said = imports.run(vault, text, "export.jsonl", dry_run=True).plan.refusals
    assert said == (f"export.jsonl:1: source {INTO}", f"export.jsonl:2: source {INTO}")


def test_an_index_that_doesn_t_answer_refuses_the_locator_it_can_t_check(tmp_path: Path) -> None:
    vault = kept_and_dropped(tmp_path)
    conn = sqlite3.connect(":memory:")
    conn.close()
    with pytest.raises(store.TextRefusedError, match="source can't be checked against the never-store containers"):
        store.TextRules(vault, conn).screen("source", "mail:AAMkADmsg1")


def test_an_undo_leaves_out_one_op_and_an_undo_of_an_undo_puts_it_back(vault: Vault) -> None:
    write(vault, [ADD], now=NOW)
    (set_due,) = write(vault, [{"op": "set", "item": "A-0001", "fields": {"due": "2026-10-21"}}], now=later(1)).ops
    (undo,) = write(vault, [{"op": "undo", "of": set_due.id, "note": "wrong item"}], now=later(2)).ops
    assert (undo.item, undo.of, undo.note) == (set_due.item, set_due.id, "wrong item")
    assert item(vault, 1).due is None
    write(vault, [{"op": "undo", "of": undo.id}], now=later(3))
    assert item(vault, 1).due == date(2026, 10, 21)
    with pytest.raises(LedgerError, match="is undone already"):
        write(vault, [{"op": "undo", "of": undo.id}], now=later(4))


def laptop_op(vault: Vault, kind: str, item_id: str, **keys: Any) -> Op:
    """An op another machine wrote and the user's git merged in: on the laptop, above every op here."""
    highest = ledger(vault).highest_id
    o = Op(schema_version=1, id=ops.mint(highest, later(30), 1)[0], item=item_id, op=kind, day=date(2026, 10, 18),
           machine="laptop", by="user", via="page", **keys)  # fmt: skip
    path = vault.root / ops.rel_of(o)
    path.write_text((path.read_text(encoding="utf-8") if path.exists() else "") + o.text() + "\n", encoding="utf-8",
                    newline="\n")  # fmt: skip
    vault.commit([ops.rel_of(o)], "Merge the laptop's ops")
    return o


def test_an_undo_also_undoes_the_close_it_left_idle_so_another_machine_s_close_can_t_take_over(vault: Vault) -> None:
    (add, done) = write(vault, [ADD, {"op": "done", "item": "A-0001", "note": "answered"}], now=NOW).ops
    drop = laptop_op(vault, "drop", add.id, note="closed here too")
    assert drop.id in ledger(vault).folds[add.id].idle
    undos = write(vault, [{"op": "undo", "of": done.id}], now=later(40)).ops
    assert [(o.op, o.of) for o in undos] == [("undo", done.id), ("undo", drop.id)]
    assert undos[1].after == undos[0].id
    assert item(vault, 1).state == "open"


def test_a_sync_s_write_is_undone_whole_by_its_batch(vault: Vault) -> None:
    synced = write(vault, [ADD, {"op": "triage", "item": "A-0001", "verdict": "chase"}], by="sync", via="tool", now=NOW)
    write(vault, [{"op": "log", "item": "A-0001", "note": "seen"}], by="user", via="page", now=later(1))
    undos = write(vault, [{"op": "undo", "batch": synced.ops[0].batch}], by="user", via="page", now=later(2)).ops
    assert [o.of for o in undos] == [synced.ops[1].id, synced.ops[0].id]
    folded = ledger(vault)
    assert folded.items == {}
    assert folded.highest_number == 1
    with pytest.raises(LedgerError, match="no item A-0001"):
        write(vault, [{"op": "log", "item": "A-0001", "note": "x"}], now=later(3))
    (again,) = write(vault, [ADD], now=later(4)).ops
    assert again.number == 2
    with pytest.raises(LedgerError, match=r"change 2 \(log\): A-0002 was undone"):
        write(vault, [{"op": "undo", "of": again.id}, {"op": "log", "item": "A-0002", "note": "x"}], now=later(5))


def test_an_op_the_fold_left_out_can_t_be_undone(vault: Vault) -> None:
    write(vault, [ADD], now=NOW)
    orphan = laptop_op(vault, "log", "01JAB3W8Q4X5M2Z7K9R0T6YVNC", note="no add")
    with pytest.raises(LedgerError, match="is left out of the fold, so there is nothing to undo"):
        write(vault, [{"op": "undo", "of": orphan.id}], now=later(40))


def test_a_clash_is_renumbered_by_the_next_write_and_its_number_names_neither_item(vault: Vault) -> None:
    (ours,) = write(vault, [ADD], now=NOW).ops
    highest = ledger(vault).highest_id
    theirs_id = ops.mint(highest, later(30), 1)[0]
    theirs = Op(schema_version=1, id=theirs_id, item=theirs_id, op="add", day=date(2026, 10, 18), machine="laptop",
                by="user", via="page", number=1, fields=dict(ADD_FIELDS))  # fmt: skip
    (vault.root / ops.rel_of(theirs)).write_text(theirs.text() + "\n", encoding="utf-8", newline="\n")
    vault.commit([ops.rel_of(theirs)], "Merge the laptop's ops")
    assert [i.id for i in ledger(vault).clashes()] == [theirs_id]
    written = write(vault, [{"op": "log", "item": ours.id, "note": "first after the merge"}], now=later(40))
    renumber, log = written.ops
    assert (renumber.op, renumber.item, renumber.number, log.batch) == ("renumber", theirs_id, 2, renumber.id)
    assert message(vault) == "Ledger: renumber A-0002, log A-0001 on test-box"
    assert item(vault, 2).id == theirs_id
    with pytest.raises(LedgerError, match="A-0001 has been held by more than one item; name the item by its id"):
        write(vault, [{"op": "log", "item": "A-0001", "note": "x"}], now=later(41))
    write(vault, [{"op": "log", "item": "A-0002", "note": "the laptop's"}], now=later(42))
    with pytest.raises(LedgerError, match="a renumber can't be undone"):
        write(vault, [{"op": "undo", "of": renumber.id}], now=later(43))
    (third,) = write(vault, [ADD], now=later(44)).ops
    assert third.number == 3
    # Undoing the write whole leaves its renumber standing: a number once given stays given.
    (undo,) = write(vault, [{"op": "undo", "batch": renumber.batch}], now=later(45)).ops
    assert undo.of == log.id
    assert item(vault, 2).id == theirs_id


def test_ops_of_two_months_or_two_machines_aren_t_one_write(vault: Vault) -> None:
    october = Op(schema_version=1, id=ops.mint(None, NOW, 1)[0], item=ops.mint(None, NOW, 1)[0], op="log",
                 day=date(2026, 10, 31), machine="test-box", by="user", via="import", note="x")  # fmt: skip
    november = replace(october, day=date(2026, 11, 1))
    with pytest.raises(LedgerError, match="a write's ops fall in one month, and these span 2 files"):
        store.commit_ops(vault, [october, november], "Ledger: import")


def test_lines_a_stopped_write_left_go_in_with_the_next_write(vault: Vault) -> None:
    september = datetime(2026, 9, 30, 9, tzinfo=UTC)
    (add,) = write(vault, [ADD], now=september).ops
    grown, new = (
        Op(schema_version=1, id=op_id, item=add.id, op="log", day=day, machine="test-box", by="claude", via="cli",
           note="a write that stopped before its commit")
        for op_id, day in zip(ops.mint(add.id, september, 2), (date(2026, 9, 30), date(2026, 8, 31)), strict=True)
    )  # fmt: skip
    for left in (grown, new):
        path = vault.root / ops.rel_of(left)
        path.write_text((path.read_text(encoding="utf-8") if path.exists() else "") + left.text() + "\n",
                        encoding="utf-8", newline="\n")  # fmt: skip
    write(vault, [{"op": "log", "item": "A-0001", "note": "next"}], now=NOW)
    assert sh(vault.root, "git", "status", "--porcelain").strip() == ""
    committed = sh(vault.root, "git", "show", "--name-only", "--format=", "HEAD").split()
    assert committed == ["ledger/ops/2026-08-test-box.jsonl", "ledger/ops/2026-09-test-box.jsonl", FILE]
    assert len(item(vault, 1).log) == 4


def test_a_committed_line_changed_or_gone_refuses_every_write(vault: Vault) -> None:
    write(vault, [ADD], now=NOW)
    path = vault.root / FILE
    path.write_text(path.read_text(encoding="utf-8").replace("Send Ada", "Send Ben"), encoding="utf-8", newline="\n")
    with pytest.raises(LedgerError, match=f"{FILE} lost or changed a committed op line"):
        write(vault, [ADD], now=later(1))
    path.write_bytes(b"\xff")
    with pytest.raises(DocketError, match="isn't UTF-8"):
        write(vault, [ADD], now=later(1))


def test_an_op_file_or_folder_that_is_a_link_is_never_written_through(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ops, "linked", lambda path: path.name == "2026-10-test-box.jsonl")
    with pytest.raises(LedgerError, match="is a link"):
        write(vault, [ADD], now=NOW)
    monkeypatch.setattr(ops, "linked", lambda path: path.name == "ops")
    with pytest.raises(DocketError, match="is a link"):
        write(vault, [ADD], now=NOW)


@pytest.mark.parametrize("failure", [UnmergedError("unmerged paths"), DocketError("git failed")])
def test_a_commit_that_fails_puts_the_op_file_back(
    vault: Vault, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    def fail(*_: object) -> str:
        raise failure

    monkeypatch.setattr(Vault, "commit", fail)
    with pytest.raises(type(failure)):
        write(vault, [ADD], now=NOW)
    assert not (vault.root / FILE).exists()
    monkeypatch.undo()
    write(vault, [ADD], now=NOW)
    before = (vault.root / FILE).read_bytes()
    monkeypatch.setattr(Vault, "commit", fail)
    with pytest.raises(type(failure)):
        write(vault, [ADD], now=later(1))
    assert (vault.root / FILE).read_bytes() == before
    assert sh(vault.root, "git", "diff", "--cached", "--name-only").strip() == ""


def test_a_write_from_claude_s_shell_says_so_in_its_commit(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    write(vault, [ADD], now=NOW)
    assert message(vault).startswith("Ledger: add A-0001 on test-box\n\n")


def test_a_material_s_path_is_kept_from_the_repo_s_top(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    top = vault.home.top
    assert material_ref(vault, "https://example.invalid/x") == "https://example.invalid/x"
    assert material_ref(vault, "slack:C0TEST/1791000001.000100") == "slack:C0TEST/1791000001.000100"
    assert material_ref(vault, f'"{top / "docs" / "plan.md"}"') == "docs/plan.md"
    assert material_ref(vault, ".\\docs\\plan.md") == "docs/plan.md"
    outside = tmp_path.parent / "elsewhere" / "x.md"
    assert material_ref(vault, str(outside)) == outside.as_posix()
    write(vault, [ADD, {"op": "material", "item": "A-0001", "ref": str(top / "notes" / "a.md")}], now=NOW)
    assert item(vault, 1).materials[0].ref == "notes/a.md"


def test_a_value_that_isn_t_text_is_refused_on_a_redaction_match_and_text_is_redacted(vault: Vault) -> None:
    """A kind, a stream, a verdict or a day can't hold a marker, as a locator can't: a pattern of the owner's that
    matches one refuses the write, with the ledger's own refusal (DR-0618)."""
    path = vault.root / "vault.yaml"
    config = VaultConfig.load(path)
    config.redaction.patterns = [
        RedactionPattern(name="codename", pattern="kepler"),
        RedactionPattern(name="october", pattern="2026-10-2"),
        RedactionPattern(name="chasing", pattern="chase"),
        RedactionPattern(name="task", pattern="tas[k]"),
    ]
    path.write_text(config.dump(), encoding="utf-8", newline="\n")
    commit_config(vault.root)
    vault = Vault.open(vault.root)
    (add,) = write(vault, [{"op": "add", "fields": {**ADD_FIELDS, "title": "the kepler dates for Ada"}}], now=NOW).ops
    assert add.fields is not None and add.fields["title"] == "the [redacted:codename] dates for Ada"
    refused = [
        ({"op": "add", "fields": {**ADD_FIELDS, "stream": "kepler-pilot"}}, "stream", "a stream"),
        ({"op": "add", "fields": {**ADD_FIELDS, "due": "2026-10-21"}}, "due", "a day"),
        ({"op": "add", "fields": {**ADD_FIELDS, "kind": "task"}}, "kind", "a kind"),
        ({"op": "triage", "item": "A-0001", "verdict": "chase"}, "verdict", "a verdict"),
        ({"op": "snooze", "item": "A-0001", "until": "2026-10-25"}, "until", "a day"),
    ]
    for change, name, what in refused:
        with pytest.raises(
            LedgerError, match=rf"{name} matches the \w+ redaction rule, and {what} can't hold a marker"
        ):
            write(vault, [change], now=NOW)


def test_a_failure_after_the_commit_landed_leaves_the_ops_where_head_holds_them(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(vault, [ADD], now=NOW)

    def full(*_: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(forgotten, "carry", full)
    with pytest.raises(OSError, match="disk full"):
        write(vault, [{"op": "log", "item": "A-0001", "note": "landed"}], now=later(1))
    monkeypatch.undo()
    monkeypatch.setenv("DOCKET_MACHINE", "test-box")
    assert message(vault) == "Ledger: log A-0001 on test-box"
    assert sh(vault.root, "git", "status", "--porcelain").strip() == ""
    assert [p for p in vault.check() if p.where.startswith("ledger")] == []
    write(vault, [{"op": "log", "item": "A-0001", "note": "next"}], now=later(2))
    assert [e.line for e in item(vault, 1).log] == ["added", "landed", "next"]


def test_a_broken_line_whose_id_ends_in_a_newline_never_becomes_the_highest_id(vault: Vault) -> None:
    """A line that doesn't read, its id a ULID with an escaped newline after it: `$` would match before that newline,
    the clock would mint from it, and every write would fail until the line was fixed by hand."""
    (add,) = write(vault, [ADD], now=NOW).ops
    broken = {"schema_version": 1, "id": "7" + "Z" * 24 + "Y\n", "op": "add"}
    path = vault.root / FILE
    path.write_text(path.read_text(encoding="utf-8") + json.dumps(broken) + "\n", encoding="utf-8", newline="\n")
    vault.commit([FILE], "A broken line")
    assert load(vault.root).highest_id == add.id
    (log,) = write(vault, [{"op": "log", "item": "A-0001", "note": "after"}], now=later(1)).ops
    assert log.id > add.id
    for change, why in [
        ({"op": "add", "fields": {**ADD_FIELDS, "stream": "pilot\n"}}, "stream is a slug"),
        ({"op": "add", "fields": {**ADD_FIELDS, "due": "2026-10-21\n"}}, "due is a day"),
        ({"op": "snooze", "item": "A-0001", "until": "2026-10-25\n"}, "a day after today"),
    ]:
        with pytest.raises(LedgerError, match=why):
            write(vault, [change], now=later(2))
