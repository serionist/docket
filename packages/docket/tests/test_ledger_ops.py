"""The ledger's op line (DR-0611, DR-0612): its golden text per kind, how it reads, where it goes, and the ids."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from ledger_kit import ADD_FIELDS, DAY, MEETING, op, ulid
from pydantic import ValidationError

from docket.errors import DocketError
from docket.ledger import ops
from docket.ledger.ops import Op

A = "01JAB3W8Q4X5M2Z7K9R0T6YVNC"
B = "01JAB3W8Q4X5M2Z7K9R0T6YVND"
AT = datetime(2026, 10, 18, 9, 14, 2, tzinfo=UTC)
HEAD = '{"schema_version":1,"id":"%s","item":"%s","op":"%s","day":"2026-10-18","at":"2026-10-18T09:14:02Z",'
HEAD += '"machine":"desk","by":"claude","via":"cli","batch":"%s"'


def made(kind: str, **keys: Any) -> Op:
    item = keys.pop("item", A)
    base: dict[str, Any] = {
        "schema_version": 1, "id": B if kind != "add" else A, "item": item, "op": kind, "day": DAY, "at": AT,
        "machine": "desk", "by": "claude", "via": "cli", "batch": A,
    }  # fmt: skip
    return Op(**(base | keys))


def test_an_add_is_its_golden_line_and_reads_back_the_same() -> None:
    fields = {"title": "Send Ada the dates", "source": "chat:C0X/1", "kind": "reply", "stream": "pilot", "who": "Ada"}
    add = made("add", number=302, fields=fields)
    want = HEAD % (A, A, "add", A) + (
        ',"number":302,"fields":{"kind":"reply","title":"Send Ada the dates","who":"Ada","stream":"pilot",'
        '"source":"chat:C0X/1"}}'
    )
    assert add.text() == want
    assert Op.model_validate_json(want) == add


@pytest.mark.parametrize(
    ("kind", "keys", "tail"),
    [
        ("set", {"fields": {"later": "x", "due": "2026-10-21", "notes": None}},
         ',"after":"@","fields":{"due":"2026-10-21","notes":null,"later":"x"}}'),
        ("log", {"note": "asked again\u2028in the thread"}, ',"after":"@","note":"asked again\\u2028in the thread"}'),
        ("triage", {"verdict": None}, ',"after":"@","verdict":null}'),
        ("triage", {"verdict": "chase"}, ',"after":"@","verdict":"chase"}'),
        ("snooze", {"until": date(2026, 10, 25)}, ',"after":"@","until":"2026-10-25"}'),
        ("material", {"ref": "docs/plan.md", "label": "the plan"},
         ',"after":"@","ref":"docs/plan.md","label":"the plan"}'),
        ("material", {"ref": "docs/plan.md", "remove": True}, ',"after":"@","ref":"docs/plan.md","remove":true}'),
        ("undo", {"of": A}, ',"after":"@","of":"@"}'),
        ("renumber", {"number": 305}, ',"after":"@","number":305}'),
        ("done", {"note": "answered\u2029in chat"}, ',"after":"@","note":"answered\\u2029in chat"}'),
        ("drop", {"note": "not ours"}, ',"after":"@","note":"not ours"}'),
        ("reopen", {}, ',"after":"@"}'),
        ("archive", {}, ',"after":"@"}'),
    ],
)  # fmt: skip
def test_each_kind_writes_its_own_keys_in_order(kind: str, keys: dict[str, Any], tail: str) -> None:
    written = made(kind, after=A, **keys)
    # @ stands for the add the op names.
    assert written.text() == HEAD % (B, A, kind, A) + tail.replace("@", A)
    assert Op.model_validate_json(written.text()) == written


def test_a_key_a_field_or_a_kind_this_docket_doesnt_know_reads_and_is_kept() -> None:
    data = json.loads(made("set", fields={"due": "2026-10-21"}).text())
    data |= {"op": "pin_item", "colour": "red", "fields": {"later": "x"}, "by": "scheduler", "via": "timer"}
    read = Op.model_validate_json(json.dumps(data))
    assert (read.op, read.fields, read.by, read.via) == ("pin_item", {"later": "x"}, "scheduler", "timer")
    assert "colour" not in read.text()


def test_a_later_docket_s_field_may_hold_any_value_and_the_add_still_reads() -> None:
    """A name this Docket doesn't know holds whatever a later Docket put there; only the names it knows must be text
    or null, so an add carrying one still makes its item (DR-0611)."""
    data = json.loads(made("add", number=1, fields=dict(ADD_FIELDS)).text())
    data["fields"] |= {"estimate": 30, "tags": ["a", "b"], "parts": {"x": 1}}
    read = Op.model_validate_json(json.dumps(data))
    assert read.fields is not None and read.fields["estimate"] == 30
    assert json.loads(read.text())["fields"]["tags"] == ["a", "b"]


@pytest.mark.parametrize(
    ("kind", "reads"), [("pin_item", True), ("pin2", False), ("pin-item", False), ("x" * 33, False)]
)
def test_an_op_s_kind_is_a_lowercase_word_of_letters_and_underscores(kind: str, reads: bool) -> None:
    data = json.loads(made("done").text()) | {"op": kind}
    if reads:
        assert Op.model_validate_json(json.dumps(data)).op == kind
    else:
        with pytest.raises(ValidationError):
            Op.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": 2},
        {"number": "302"},
        {"day": 20261018},
        {"day": "18 Oct"},
        {"at": "2026-10-18T09:14:02+02:00"},
        {"at": "2026-10-18T09:14:02"},
        {"id": "01jab3w8q4x5m2z7k9r0t6yvnc"},
        {"id": "81JAB3W8Q4X5M2Z7K9R0T6YVNC"},
        {"op": "Add"},
        {"machine": "Desk"},
        {"by": "Me"},
        {"via": ""},
        {"fields": {**ADD_FIELDS, "title": 5}},
        {"fields": {**ADD_FIELDS, "notes": ["a"]}},
        {"fields": {**ADD_FIELDS, "title": None}},
        {"fields": {**ADD_FIELDS, "due": "2026-13-01"}},
        {"fields": {**ADD_FIELDS, "due": "20261018"}},
        {"fields": {**ADD_FIELDS, "opened": 3}},
        {"item": B},
        {"number": 0},
        {"remove": "yes"},
    ],
)
def test_a_line_that_breaks_the_format_doesnt_read(change: dict[str, Any]) -> None:
    data = json.loads(made("add", number=1, fields=dict(ADD_FIELDS)).text()) | change
    with pytest.raises(ValidationError):
        Op.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    ("kind", "drop"),
    [
        ("set", "fields"),
        ("log", "note"),
        ("snooze", "until"),
        ("material", "ref"),
        ("undo", "of"),
        ("renumber", "number"),
    ],
)
def test_a_known_kind_without_what_it_needs_doesnt_read(kind: str, drop: str) -> None:
    full = {"fields": {"due": "2026-10-21"}, "note": "x", "until": "2026-10-25", "ref": "a.md", "of": A, "number": 3}
    data = json.loads(made("done").text()) | {"op": kind} | full
    Op.model_validate_json(json.dumps(data))
    data.pop(drop)
    with pytest.raises(ValidationError):
        Op.model_validate_json(json.dumps(data))


def test_a_meeting_a_stream_and_their_points_are_their_golden_lines() -> None:
    """Each kind's fields in its own order (DR-0622, DR-0623, DR-0625): a time with its offset, attendees as a list,
    `point` naming the point a point_set changes."""
    cases = [
        (made("meeting", item=B, number=40, fields={
            "end": MEETING["end"], "subject": "Weekly pilot sync", "attendees": ["Ada", "Ben"], "event": "calendar:E1",
            "start": MEETING["start"]}),
         ',"number":40,"fields":{"subject":"Weekly pilot sync","start":"2026-10-19T09:00+02:00",'
         '"end":"2026-10-19T09:30+02:00","event":"calendar:E1","attendees":["Ada","Ben"]}}'),
        (made("stream", item=B, fields={"status": "active", "deadline": "2026-11-02", "name": "pilot"}),
         ',"fields":{"name":"pilot","status":"active","deadline":"2026-11-02"}}'),
        (made("point", after=A, fields={"proposed": True, "text": "Dates", "from": A}),
         ',"after":"@","fields":{"text":"Dates","from":"@","proposed":true}}'),
        (made("point_set", after=A, point=A, fields={"to": A, "state": "rolled"}),
         ',"after":"@","point":"@","fields":{"state":"rolled","to":"@"}}'),
    ]  # fmt: skip
    for written, tail in cases:
        assert written.text() == HEAD % (B, written.item, written.op, A) + tail.replace("@", A)
        assert Op.model_validate_json(written.text()) == written


@pytest.mark.parametrize(
    ("kind", "keys"),
    [
        ("meeting", {"item": B, "fields": dict(MEETING)}),
        ("meeting", {"number": 1, "fields": dict(MEETING)}),
        ("meeting", {"item": B, "number": 1, "fields": {**MEETING, "start": "2026-10-19T09:00"}}),
        ("meeting", {"item": B, "number": 1, "fields": {**MEETING, "end": "2026-10-19T25:00+02:00"}}),
        ("meeting", {"item": B, "number": 1, "fields": {**MEETING, "attendees": "Ada"}}),
        ("meeting", {"item": B, "number": 1, "fields": {**MEETING, "attendees": ["Ada", 5]}}),
        ("meeting", {"item": B, "number": 1, "fields": {**MEETING, "continues": "M-0001"}}),
        ("meeting", {"item": B, "number": 1, "fields": {"subject": "x", "start": MEETING["start"]}}),
        ("stream", {"item": B, "fields": {"name": "pilot"}}),
        ("stream", {"item": B, "fields": {"name": "pilot", "status": "active", "deadline": "soon"}}),
        ("point", {"item": B, "fields": {"text": "x"}}),
        ("point", {"fields": {"item": "A-0001", "text": "x"}}),
        ("point", {"fields": {"text": "x", "proposed": "yes"}}),
        ("point", {"fields": {}}),
        ("point_set", {"fields": {"state": "answered"}}),
        ("point_set", {"point": A, "fields": {"state": "rolled"}}),
        ("point_set", {"point": A, "fields": {}}),
        ("add", {"number": 1, "fields": {**ADD_FIELDS, "meeting": "M-0001"}}),
    ],
)
def test_a_line_that_breaks_its_object_s_rules_doesnt_read(kind: str, keys: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        made(kind, **keys)


def test_a_set_reads_with_any_values_since_its_object_s_rules_hold_when_it_is_folded() -> None:
    """A set names no object kind on its line, and a later object may give one of an item's names another meaning:
    its fields are checked against its object's rules in the fold (DR-0620)."""
    assert made("set", fields={"due": 5, "start": "Monday"}).fields == {"due": 5, "start": "Monday"}


def test_an_op_goes_in_its_machine_s_file_for_the_month_of_its_day() -> None:
    assert ops.rel_of(op("add", 1)) == "ledger/ops/2026-10-desk.jsonl"
    assert (
        ops.rel_of(op("log", 2, 1, note="x", day=date(2026, 9, 30), machine="laptop"))
        == "ledger/ops/2026-09-laptop.jsonl"
    )


def test_a_file_s_lines_read_by_number_and_those_that_dont_are_problems() -> None:
    rel = "ledger/ops/2026-10-desk.jsonl"
    texts = [
        op("add", 1).text(),
        "",
        "not json",
        op("log", 2, 1, note="x", day=date(2026, 9, 30)).text(),
        op("log", 3, 1, note="x", machine="laptop").text(),
        op("log", 4, 1, note="x", day=date(2025, 10, 30)).text(),
        op("log", 5, 1, note="kept").text(),
    ]
    read = ops.parse("\n".join(texts) + "\n", rel)
    assert [(line.n, line.where, line.op.id) for line in read.lines] == [
        (1, f"{rel}:1", ulid(1)),
        (7, f"{rel}:7", ulid(5)),
    ]
    assert [where for where, _ in read.problems] == [f"{rel}:{n}" for n in (3, 4, 5, 6)]
    assert "belongs in ledger/ops/2026-09-desk.jsonl" in read.problems[1][1]
    assert ops.parse(op("add", 1).text(), "ledger/ops/notes.txt").problems[0][1].startswith("belongs in")


def test_the_highest_id_counts_every_line_one_this_docket_can_t_read_included() -> None:
    """A later Docket's line doesn't read here, yet its id still sets how low this clock may mint (DR-0612)."""
    newer = json.loads(op("log", 9, 1, note="x").text()) | {"schema_version": 2}
    texts = [op("add", 1).text(), json.dumps(newer), "[1]", '{"id": 5}', '{"id": "not an id"}', "{"]
    assert ops.parse("\n".join(texts), "ledger/ops/2026-10-desk.jsonl").highest == ulid(9)
    assert ops.parse("not json\n", "ledger/ops/2026-10-desk.jsonl").highest is None
    misplaced = op("log", 12, 1, note="x", machine="laptop").text()
    assert ops.parse(misplaced, "ledger/ops/2026-10-desk.jsonl").highest == ulid(12)
    assert [ops.id_of(t) for t in texts[2:]] == [None, None, None, None]


def test_every_machine_s_files_are_read_and_the_rest_are_problems(tmp_path: Path) -> None:
    folder = tmp_path / ops.OPS_DIR
    assert ops.read_all(tmp_path).lines == ()
    folder.mkdir(parents=True)
    (folder / ".gitkeep").write_text("", encoding="utf-8")
    (folder / "2026-10-desk.jsonl").write_text(op("add", 1).text() + "\nnot json\n", encoding="utf-8")
    (folder / "2026-10-laptop.jsonl").write_text(op("add", 2, machine="laptop").text() + "\n", encoding="utf-8")
    (folder / "2026-10-box-2.jsonl").write_text("", encoding="utf-8")
    (folder / "notes.txt").write_text("", encoding="utf-8")
    (folder / "2026-11-desk.jsonl").mkdir()
    (folder / "2026-12-desk.jsonl").write_bytes(b"\xff\xfe")
    (folder / "2026-09-desk.jsonl").write_text("\n", encoding="utf-8")
    read = ops.read_all(tmp_path)
    assert [line.op.id for line in read.lines] == [ulid(1), ulid(2)]
    assert read.highest == ulid(2)
    assert sorted(read.problems) == [
        ("ledger/ops/2026-10-box-2.jsonl", "isn't an op file (<yyyy-mm>-<machine>.jsonl); skipped"),
        ("ledger/ops/2026-10-desk.jsonl:2", "isn't a ledger op of schema 1; skipped"),
        ("ledger/ops/2026-11-desk.jsonl", "isn't an op file (<yyyy-mm>-<machine>.jsonl); skipped"),
        ("ledger/ops/2026-12-desk.jsonl", "isn't UTF-8; skipped"),
        ("ledger/ops/notes.txt", "isn't an op file (<yyyy-mm>-<machine>.jsonl); skipped"),
    ]


def test_links_are_never_read_or_written_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / ops.OPS_DIR
    folder.mkdir(parents=True)
    (folder / "2026-10-desk.jsonl").write_text(op("add", 1).text() + "\n", encoding="utf-8")
    monkeypatch.setattr(ops, "linked", lambda path: path.name == "2026-10-desk.jsonl")
    assert ops.read_all(tmp_path).problems == (
        ("ledger/ops/2026-10-desk.jsonl", "isn't an op file (<yyyy-mm>-<machine>.jsonl); skipped"),
    )
    monkeypatch.setattr(ops, "linked", lambda path: path.name == "ops")
    assert ops.read_all(tmp_path) == ops.Read((), (("ledger/ops", "is a link; nothing in it is read"),))
    with pytest.raises(DocketError, match="is a link"):
        ops.folder(tmp_path)


def test_a_file_that_isnt_utf8_is_refused_for_a_write(tmp_path: Path) -> None:
    path = tmp_path / "2026-10-desk.jsonl"
    path.write_bytes(b"\xff")
    with pytest.raises(DocketError, match="isn't UTF-8"):
        ops.text_of(path, "ledger/ops/2026-10-desk.jsonl")
    path.write_bytes(b"ok\n")
    assert ops.text_of(path, "x") == "ok\n"


def test_an_id_is_its_128_bits_in_crockford_s_base32_and_sorts_as_its_number() -> None:
    for n in (0, 1, 31, 32, 2**80 - 1, 2**128 - 1):
        assert ops.decode(ops.encode(n)) == n
        assert ops.ULID.match(ops.encode(n))
    assert ops.encode(0) == "0" * 26
    assert sorted(ops.encode(n) for n in (5, 2**90, 40, 2**81)) == [ops.encode(n) for n in (5, 40, 2**81, 2**90)]


def bits(value: int) -> Callable[[int], bytes]:
    """A fake os.urandom that always gives one number."""
    return lambda size: value.to_bytes(size, "big")


def test_ids_are_minted_on_the_clock_and_a_batch_counts_up() -> None:
    first, second = ops.mint(None, AT, 2, rand=bits(7))
    assert ops.decode(first) == (int(AT.timestamp() * 1000) << 80) + 7
    assert ops.decode(second) == ops.decode(first) + 1
    behind = ops.encode(int((AT - timedelta(minutes=1)).timestamp() * 1000) << 80)
    assert ops.mint(behind, AT, 1, rand=bits(7)) == [first]


def test_a_clock_behind_mints_after_the_highest_id_with_fresh_random_bits() -> None:
    """Two clones on one host, one machine name, both pull an op from a laptop whose clock runs three minutes ahead,
    then each writes before pulling the other: their ids differ, and both sort after the laptop's (DR-0612)."""
    ahead = int((AT + timedelta(minutes=3)).timestamp() * 1000)
    laptop = ops.encode((ahead << 80) | 2**79)
    desk = ops.mint(laptop, AT, 2, rand=bits(11))
    live = ops.mint(laptop, AT + timedelta(seconds=1), 2, rand=bits(2**70))
    assert ops.decode(desk[0]) == ((ahead + 1) << 80) + 11
    assert ops.decode(live[0]) == ((ahead + 1) << 80) + 2**70
    assert min(desk + live) > laptop
    assert not set(desk) & set(live)
    same_ms = ops.mint(ops.encode(int(AT.timestamp() * 1000) << 80 | 50), AT, 1, rand=bits(60))
    assert ops.decode(same_ms[0]) == (int(AT.timestamp() * 1000) << 80) + 60


@pytest.mark.parametrize("key", ["id", "item", "op", "by", "via", "machine", "batch"])
def test_a_trailing_newline_never_passes_an_anchored_pattern(key: str) -> None:
    data = json.loads(made("done").text())
    data[key] += "\n"
    with pytest.raises(ValidationError):
        Op.model_validate_json(json.dumps(data))
    assert ops.id_of(json.dumps({"id": A + "\n"})) is None
    assert ops.FILE_NAME.fullmatch("2026-10-desk.jsonl\n") is None
