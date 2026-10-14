"""The contract's shapes and the core's checks on what a mapper returns: each refusal fails the whole run and names a
record by its position and field, never by a value; transcripts come out in the core's order and text."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from docket.sources.contract import (
    CONTRACT,
    Capture,
    Manifest,
    MapperRefusedError,
    SetAside,
    Waiting,
    check_output,
    mapper_input,
)
from docket.vault.records import RawRecord, version
from docket.vault.segments import normalize, render

PRIVATE_NAME = "Dentist with Cleo"
FILE = "of_00c0ffee00c0ffee00c0ffee00c0ffee"
SEGMENTS: list[dict[str, Any]] = [
    {"start_ms": 0, "end_ms": 4000, "speaker": "Ben Sample", "text": "Morning, Cleo."},
    {"start_ms": 3000, "end_ms": 9000, "speaker": "Cleo Test", "text": "Morning. Shall we start?"},
    {"start_ms": 9000, "end_ms": 9500, "speaker": None, "text": "(laughter)"},
]


def record(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "rid": f"plaud:{FILE}/transaction_polish",
        "source": "plaud",
        "kind": "transcript",
        "container": f"plaud:{FILE}",
        "thread": f"plaud:{FILE}",
        "ts": "2026-10-14T09:30:00Z",
        "segments": SEGMENTS,
        "fetched": "2026-10-14T12:00:00Z",
        "via": "plaud-mcp",
        "engine": "plaud/transaction_polish",
    }
    base.update(fields)
    return base


def output(*records: Any, **rest: Any) -> dict[str, Any]:
    return {"contract": CONTRACT, "records": list(records), **rest}


def refused(data: Any, match: str) -> str:
    with pytest.raises(MapperRefusedError, match=match) as e:
        check_output("plaud", data)
    return str(e.value)


def test_a_good_output_is_checked_and_its_transcript_ordered_and_rendered() -> None:
    shuffled = [SEGMENTS[2], SEGMENTS[0], SEGMENTS[1], SEGMENTS[0]]
    mapped = check_output(
        "plaud",
        output(
            record(segments=shuffled),
            record(rid=f"plaud:{FILE}", kind="recording", segments=None, title="Supplier call", engine=None),
            set_aside=[{"id": "of_bad", "reason": "no-meta", "tool": "get_file", "key": "start_at"}, {"reason": "x"}],
            waiting=[{"id": "of_later", "for": "summary"}],
            held=["of_held"],
        ),
    )
    transcript, recording = mapped.records
    assert [s.text for s in transcript.segments or []] == ["Morning, Cleo.", "Morning. Shall we start?", "(laughter)"]
    assert transcript.text == "Ben Sample: Morning, Cleo.\nCleo Test: Morning. Shall we start?\n(laughter)"
    assert recording.segments is None
    assert recording.text == ""
    assert mapped.set_aside == [SetAside("of_bad", "no-meta", "get_file", "start_at"), SetAside(None, "x")]
    assert mapped.waiting == [Waiting("of_later", "summary")]
    assert mapped.held == ["of_held"]


def test_two_page_orders_of_one_transcript_get_one_version() -> None:
    first = check_output("plaud", output(record(segments=SEGMENTS))).records[0]
    pages = [SEGMENTS[1], SEGMENTS[2], SEGMENTS[0], SEGMENTS[1]]
    second = check_output("plaud", output(record(segments=pages))).records[0]
    assert version(first) == version(second)


def test_normalize_and_render() -> None:
    segs = RawRecord.model_validate({**record(), "segments": SEGMENTS}).segments or []
    tie = segs[0].model_copy(update={"speaker": None})
    assert normalize([segs[1], tie, segs[0], segs[0]]) == [tie, segs[0], segs[1]]
    assert render([]) == ""
    assert render([segs[0].model_copy(update={"speaker": ""})]) == "Morning, Cleo."


@pytest.mark.parametrize(
    ("data", "match"),
    [
        ([], "isn't an object"),
        ({"contract": CONTRACT, "records": [], "extra": 1}, "a key contract 1 doesn't know"),
        ({"contract": 2, "records": []}, "another contract than 1"),
        ({"records": []}, "another contract than 1"),
        ({"contract": CONTRACT}, "no records list"),
        (output("a record as a string"), "record 1 isn't an object"),
        (output(record(source="calls")), "record 1: its source isn't the run's source, plaud"),
        (output(record(rid=f"calls:{FILE}")), "record 1: its rid isn't plaud:<native id>"),
        (output(record(rid="plaud:two words")), "its rid isn't"),
        (output(record(rid=f"plaud:{FILE}//x")), "its rid isn't"),
        (output(record(rid="plaud:" + "x" * 513)), "its rid isn't"),
        (output(record(container="calls:x")), "record 1: its container isn't a rid of plaud"),
        (output(record(thread="plaud:a\x01b")), "its thread isn't a rid of plaud"),
        (output(record(v="sha256:abc")), "the mapper set v or run"),
        (output(record(run="2026-10-14T1000-test-box")), "the mapper set v or run"),
        (output(record(fidelity="cut")), "the mapper set fidelity cut"),
        (output(record(), record()), "record 2: its rid came earlier"),
        (output(record(text="Ben Sample: Morning, Cleo.")), "a record with segments leaves text empty"),
        (output(record(), set_aside="no"), "set_aside isn't a list"),
        (output(record(), set_aside=[{"id": "of_x"}]), r"set_aside 1 isn't \{id, reason, tool, key\}"),
        (output(record(), set_aside=[{"reason": "no meta"}]), "set_aside 1"),
        (output(record(), set_aside=[{"reason": "x", "tool": "list files"}]), "set_aside 1"),
        (output(record(), set_aside=[{"reason": "x", "title": "y"}]), "set_aside 1"),
        (output(record(), set_aside=["x"]), "set_aside 1"),
        (output(record(), waiting=[{"id": "of_x"}]), r"waiting 1 isn't \{id, for\}"),
        (output(record(), waiting=[{"id": "two words", "for": "summary"}]), "waiting 1"),
        (output(record(), held=["two words"]), "held 1 isn't an id"),
    ],
)
def test_what_breaks_the_contract_fails_the_run(data: Any, match: str) -> None:
    refused(data, match)


@pytest.mark.parametrize(
    ("fields", "match"),
    [
        ({"to": PRIVATE_NAME}, r"record 1: to isn't valid \(list_type\)"),
        ({"ts": "2026-10-14T09:30:00"}, r"record 1: ts isn't valid \(value_error\)"),
        ({"ts": PRIVATE_NAME}, r"record 1: ts isn't valid \(datetime_from_date_parsing\)"),
        ({"title": [PRIVATE_NAME]}, r"record 1: title isn't valid \(string_type\)"),
        ({PRIVATE_NAME: 1}, r"record 1: \? isn't valid \(extra_forbidden\)"),
        ({"segments": [{**SEGMENTS[0], "speaker": [PRIVATE_NAME]}]}, r"segments\.0\.speaker isn't valid"),
    ],
)
def test_a_shape_error_names_the_field_never_the_value(fields: dict[str, Any], match: str) -> None:
    message = refused(output(record(**fields)), match)
    assert PRIVATE_NAME not in message
    assert "Dentist" not in message


def test_the_mapper_s_input() -> None:
    captured = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
    capture = Capture(seq=0, run="r1", captured=captured, tool="list_files", arguments={"page": 1}, result={"x": 1})
    manifest = Manifest(run="r1", source="plaud", captures=1, final=True)
    run = mapper_input("plaud", manifest, [capture], {"of_b", "of_a"})
    assert run["contract"] == CONTRACT
    assert run["holds"] == ["of_a", "of_b"]
    assert run["manifest"]["final"] is True
    assert run["captures"][0]["captured"] == "2026-10-14T12:00:00Z"
    assert run["captures"][0]["file"] is None


@pytest.mark.parametrize(
    "bad",
    [
        {"run": "two words", "source": "plaud", "captures": 0},
        {"run": "r1", "source": "../..", "captures": 0},
        {"run": "r1", "source": "plaud", "captures": -1},
        {"run": "r1", "source": "plaud", "captures": 0, "window": {"since": "2026-10-14T00:00:00"}},
    ],
)
def test_a_manifest_that_doesn_t_hold_is_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Manifest.model_validate(bad)


def test_a_capture_needs_its_time_zone() -> None:
    with pytest.raises(ValidationError):
        Capture(seq=0, run="r1", captured=datetime(2026, 10, 14, 12, 0))
    window = Manifest.model_validate(
        {"run": "r1", "source": "plaud", "captures": 0, "window": {"since": "2026-10-14T00:00:00+02:00"}}
    ).window
    assert window is not None
    assert window.since == datetime(2026, 10, 13, 22, 0, tzinfo=UTC)
