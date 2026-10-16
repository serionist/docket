from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from docket.errors import DocketError
from docket.vault import curatelog
from docket.vault.curatelog import (
    LOG_DIR,
    AliasFields,
    Change,
    EntityFields,
    IdFields,
    Line,
    NameFields,
    RelatedFields,
    fold,
    key_of,
    new_ids,
    parse,
    read_all,
)

T0 = datetime(2026, 10, 16, 9, 14, 2, tzinfo=UTC)
CLEO = EntityFields(id="cleo-test", type="person", name={"en": "Cleo Test"}, aliases=(AliasFields(text="Cleo"),))
NORTWIN = Change(alias=AliasFields(text="Nortwin", kind="asr"))


def line(op: str = "add", n: int = 1, at: datetime = T0, machine: str = "desk", **fields: Any) -> Line:
    base: dict[str, Any] = {"entity": "northwind", "change": NORTWIN, "key": "alias:northwind:nortwin"}
    if op == "create":
        base = {"entity": "cleo-test", "change": Change(entity=CLEO), "key": "new:-:cleo test"}
    elif op in ("reject", "lift"):
        base = {"key": "new:-:nortwin"}
    elif op == "undo":
        base = {"entity": "northwind", "key": "alias:northwind:nortwin", "of": f"20261016T091402-{machine}-1"}
    stamp = f"{at:%Y%m%dT%H%M%S}"
    return Line.model_validate(
        {
            "schema_version": 1,
            "id": f"{stamp}-{machine}-{n}",
            "op": op,
            "at": at,
            "machine": machine,
            "by": "user",
            "via": "cli",
            **base,
            **fields,
        }
    )


def write_log(root: Path, name: str, lines: list[Line], extra: str = "") -> None:
    folder = root / LOG_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text("".join(f"{x.text()}\n" for x in lines) + extra, encoding="utf-8", newline="\n")


def test_each_op_reads_back_as_written_with_by_and_via() -> None:
    evidence = curatelog.Evidence(chunks=9, containers=4, locators=("r:plaud:rec-0001/transaction@12:08",))
    cases = [
        line("create"),
        line("add", round="2026-10-16T0912-desk", card="3f2a1c", evidence=evidence, by="tier1", rule="engines"),
        line("add", change=Change(name=NameFields(lang="hu", text="Minta Dóra")), key="name:dora-minta:hu=minta dora"),
        line("add", change=Change(id=IdFields(key="email", value="Ada@Example.com")), key="id:x:email=ada@example.com"),
        line("add", change=Change(related=RelatedFields(type="person", target="ben-sample")), key="related:x:p=b"),
        line("reject", via="page"),
        line("undo"),
        line("lift", of="20261016T091402-desk-3"),
    ]
    for case in cases:
        text = case.text()
        assert "\n" not in text
        assert Line.model_validate_json(text) == case
        assert '"via"' in text and '"by"' in text
        assert "null" not in text
    assert json.loads(cases[1].text())["at"] == "2026-10-16T09:14:02Z"
    assert cases[0].rel == "entities/_log/2026-10-desk.jsonl"


def test_a_time_is_kept_in_utc() -> None:
    there = datetime(2026, 10, 16, 11, 14, 2, tzinfo=timezone(timedelta(hours=2)))
    assert line(at=there).at == T0


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": 2},
        {"change": {"alias": {"text": "x", "colour": "blue"}}},
        {"id": "16-10-2026-desk-1"},
        {"at": "2026-10-16T09:14:02"},
        {"machine": "Desk Top"},
        {"machine": "desk-2"},
        {"op": "create"},
        {"op": "add", "entity": None},
        {"op": "undo", "of": None},
        {"op": "reject", "of": "20261016T091402-desk-1"},
        {"op": "lift", "change": {"alias": {"text": "x"}}},
        {"key": ""},
        {"change": {"alias": {"text": "x"}, "name": {"lang": "en", "text": "x"}}},
        {"change": {}},
    ],
)
def test_a_line_of_another_shape_doesn_t_read(change: dict[str, Any]) -> None:
    data = json.loads(line().text()) | change
    with pytest.raises(ValidationError):
        Line.model_validate(data)


def test_a_line_that_doesn_t_read_is_a_problem_named_by_its_number_and_skipped() -> None:
    good = line().text()
    found, problems = parse(f"{good}\n\nnot json\n{good.replace('"schema_version":1', '"schema_version":2')}\n", "x")
    assert found == [line()]
    assert problems == [
        "x: line 3 isn't a curate log line of schema 1; skipped",
        "x: line 4 isn't a curate log line of schema 1; skipped",
    ]


@pytest.mark.parametrize(
    ("fields", "says"),
    [
        ({"id": "Cleo Test"}, "an id: lowercase letters"),
        ({"id": "con"}, "a name Windows keeps"),
        ({"type": "pet"}, "one of person, org"),
        ({"name": {}}, "at least 1 item"),
        ({"name": {"english": "Cleo"}}, "a language code"),
        ({"name": {"en": "--"}}, "at least one letter or digit"),
        ({"ids": {"E-Mail": "x"}}, "a key of lowercase letters"),
        ({"ids": {"email": []}}, "at least 1 item"),
        ({"related": {"person": "Ben Sample"}}, "an id: lowercase letters"),
        ({"aliases": [{"text": "Clio", "kind": "typo"}]}, "'plain' or 'asr'"),
        ({"aliases": [{"text": "Clio", "needs": "Ben Sample"}]}, "an id: lowercase letters"),
    ],
)
def test_an_entity_s_fields_are_checked_as_its_file_is_without_repeating_the_value(
    fields: dict[str, Any], says: str
) -> None:
    data = {"id": "cleo-test", "type": "person", "name": {"en": "Cleo Test"}} | fields
    with pytest.raises(ValidationError) as e:
        EntityFields.model_validate(data)
    messages = " ".join(err["msg"] for err in e.value.errors())
    assert says in messages
    for value in ("Cleo Test", "Ben Sample", "english", "E-Mail"):
        assert value not in messages


def test_an_entity_s_fields_and_its_entity_go_both_ways() -> None:
    fields = EntityFields.model_validate(
        {
            "id": "dora-minta",
            "type": "person",
            "name": {"en": " Dóra Minta ", "hu": "Minta Dóra"},
            "aliases": [{"text": "Dóra"}, {"text": "Dora", "kind": "asr", "needs": "ben-sample"}],
            "related": {"person": "ben-sample"},
            "ids": {"email": ["dora@example.com", "d@example.com"], "chat": "U0DORA"},
            "status": "archived",
            "role": " editor ",
            "note": "x",
        }
    )
    entity = fields.entity()
    assert entity.path == "entities/person/dora-minta.yaml"
    assert entity.names == {"en": "Dóra Minta", "hu": "Minta Dóra"}
    assert entity.aliases[1].needs == ("ben-sample",)
    assert entity.ids == {"email": ("dora@example.com", "d@example.com"), "chat": ("U0DORA",)}
    assert entity.role == "editor"
    assert EntityFields.of(entity) == fields


def test_keys_fold_text_as_aliases_folded_is_and_ids_casefold() -> None:
    for text in ("Dóra  Minta", "Dóra-Minta", "dora minta", "DORA MINTA"):
        assert key_of("dora-minta", Change(alias=AliasFields(text=text))) == "alias:dora-minta:dora minta"
    assert key_of("x", Change(entity=CLEO)) == "new:-:cleo test"
    two = EntityFields(id="dora-minta", type="person", name={"hu": "Minta Dóra", "en": "Dóra Minta"})
    assert key_of("dora-minta", Change(entity=two)) == "new:-:dora minta"
    assert key_of("d", Change(name=NameFields(lang="hu", text="Minta Dóra"))) == "name:d:hu=minta dora"
    assert key_of("a", Change(id=IdFields(key="email", value="Ada@Example.COM"))) == "id:a:email=ada@example.com"
    related = Change(related=RelatedFields(type="person", target="ben-sample"))
    assert key_of("n", related) == "related:n:person=ben-sample"


def test_read_all_reads_every_machine_s_files_in_the_order_they_happened(tmp_path: Path) -> None:
    write_log(tmp_path, "2026-10-laptop-1x.jsonl", [line(at=T0 + timedelta(minutes=1), machine="laptop-1x")])
    write_log(tmp_path, "2026-10-desk.jsonl", [line(), line("reject", at=T0 + timedelta(minutes=2))])
    found = read_all(tmp_path)
    assert [x.machine for x in found.lines] == ["desk", "laptop-1x", "desk"]
    assert found.problems == ()


def test_read_all_skips_what_isn_t_a_log_file_with_a_problem(tmp_path: Path) -> None:
    assert read_all(tmp_path) == curatelog.LogRead((), ())
    folder = tmp_path / LOG_DIR
    write_log(tmp_path, "2026-10-desk.jsonl", [line()], extra="nope\n")
    (folder / "notes.txt").write_text("x", encoding="utf-8")
    (folder / "2026-13-desk.jsonl").write_text("", encoding="utf-8")
    (folder / "2026-10-Desk-Top.jsonl").write_text("", encoding="utf-8")
    (folder / "2026-11-desk.jsonl").mkdir()
    (folder / "2026-09-desk.jsonl").write_bytes(b"\xff\xfe")
    found = read_all(tmp_path)
    assert found.lines == (line(),)
    assert found.problems == (
        "entities/_log/2026-09-desk.jsonl: isn't UTF-8; skipped",
        "entities/_log/2026-10-Desk-Top.jsonl: isn't a log file (<yyyy-mm>-<machine>.jsonl); skipped",
        "entities/_log/2026-10-desk.jsonl: line 2 isn't a curate log line of schema 1; skipped",
        "entities/_log/2026-11-desk.jsonl: isn't a log file (<yyyy-mm>-<machine>.jsonl); skipped",
        "entities/_log/2026-13-desk.jsonl: isn't a log file (<yyyy-mm>-<machine>.jsonl); skipped",
        "entities/_log/notes.txt: isn't a log file (<yyyy-mm>-<machine>.jsonl); skipped",
    )


def test_the_log_folder_is_never_read_through_a_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(curatelog, "_linked", lambda path: path.name == "_log")
    with pytest.raises(DocketError, match="is a link"):
        read_all(tmp_path)


def test_a_field_the_reader_doesn_t_know_is_ignored_so_a_later_writer_can_add_one() -> None:
    data = json.loads(line().text()) | {"hash": "abc", "evidence": {"chunks": 2, "containers": 1, "weight": 3}}
    found, problems = parse(json.dumps(data), "x")
    assert problems == []
    assert found[0].evidence == curatelog.Evidence(chunks=2, containers=1)
    assert "hash" not in found[0].text()


def test_a_reader_keeps_a_long_list_of_locators_first_five() -> None:
    locators = [f"r:plaud:rec-{n:04d}" for n in range(1, 8)]
    data = json.loads(line().text()) | {"evidence": {"chunks": 7, "containers": 7, "locators": locators}}
    found, problems = parse(json.dumps(data), "x")
    assert problems == []
    assert found[0].evidence is not None
    assert found[0].evidence.locators == tuple(locators[:5])


def test_ops_of_one_second_sort_by_their_number_as_a_number() -> None:
    lines = [line(n=n) for n in (10, 2, 1, 11, 9)]
    assert [x.id.rsplit("-", 1)[1] for x in curatelog._ordered(lines)] == ["1", "2", "9", "10", "11"]
    later = line(n=1, at=T0 + timedelta(seconds=1))
    assert curatelog.order(later) > curatelog.order(line(n=11))


def test_new_ids_follow_the_highest_of_their_second_on_their_machine() -> None:
    lines = [line(n=1), line(n=4), line(n=9, machine="laptop-1x"), line(n=7, at=T0 + timedelta(seconds=1))]
    assert new_ids(lines, "desk", T0, 2) == ["20261016T091402-desk-5", "20261016T091402-desk-6"]
    assert new_ids([], "desk", T0, 1) == ["20261016T091402-desk-1"]


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def test_the_stoplist_takes_each_key_s_latest_word() -> None:
    lines = [
        line("reject", n=1, at=at(0)),
        line("lift", n=1, at=at(1), of="20261016T091402-desk-1"),
        line("reject", n=1, at=at(2)),
        line("reject", n=1, at=at(3), key="new:-:clio"),
        line("lift", n=1, at=at(4), key="new:-:clio"),
    ]
    folded = fold(lines)
    assert folded.stoplist == frozenset({"new:-:nortwin"})
    assert folded.lifted_since("new:-:clio", at(3))
    assert not folded.lifted_since("new:-:clio", at(4))
    assert folded.lifted_since("new:-:nortwin", at(0))
    assert not folded.lifted_since("new:-:nortwin", at(1))


def test_an_undo_stoplists_an_add_lifts_a_reject_and_hands_a_create_on() -> None:
    add, create, reject = line(n=1, at=at(0)), line("create", n=2, at=at(0)), line("reject", n=3, at=at(0))
    undos = [line("undo", n=1, at=at(1), of=x.id, key=x.key) for x in (add, create, reject)]
    undos = [u.model_copy(update={"id": f"{u.id[:-1]}{i}"}) for i, u in enumerate(undos, start=1)]
    folded = fold([*undos, add, create, reject])
    assert folded.stoplist == frozenset({"alias:northwind:nortwin"})
    assert folded.undone_creates == (create,)
    assert folded.undone_by == {add.id: undos[0].id, create.id: undos[1].id, reject.id: undos[2].id}
    assert folded.lifted == {"new:-:nortwin": at(1)}


def test_an_undo_in_the_round_its_op_was_decided_in_stoplists_nothing() -> None:
    round_id = "2026-10-16T0912-desk"
    add = line(n=1, round=round_id)
    create = line("create", n=2, round=round_id)
    undo_add = line("undo", n=3, at=at(1), of=add.id, round=round_id)
    undo_create = line("undo", n=4, at=at(1), of=create.id, round=round_id)
    later = line("undo", n=5, at=at(2), of=add.id, round="2026-10-23T0900-desk")
    assert fold([add, create, undo_add, undo_create]).stoplist == frozenset()
    assert fold([add, create, undo_add, undo_create]).undone_creates == ()
    assert fold([add, later]).stoplist == frozenset({add.key})


def test_an_undo_of_an_op_the_log_doesn_t_hold_is_left_alone() -> None:
    folded = fold([line("undo", of="20261016T091402-desk-9")])
    assert folded.undone_by == {}
    assert folded.stoplist == frozenset()


def test_two_machines_files_fold_to_one_stoplist_in_any_order() -> None:
    desk = [line("reject", n=1, at=at(0)), line("reject", n=2, at=at(5), key="new:-:clio")]
    laptop = [line("lift", n=1, at=at(1), machine="laptop-1x"), line("reject", n=2, at=at(3), machine="laptop-1x")]
    assert fold(desk + laptop).stoplist == fold(laptop + desk).stoplist == frozenset({"new:-:nortwin", "new:-:clio"})
