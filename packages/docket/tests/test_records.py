from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from docket.vault.records import Attachment, RawRecord, canonical_json, register_projection, version

Make = Callable[..., RawRecord]

GOLDEN = {
    "rid": "chat:C0TEST/1791000001.000100",
    "source": "chat",
    "kind": "message",
    "container": "chat:C0TEST",
    "ts": datetime(2026, 10, 14, 9, 30, 1, tzinfo=UTC),
    "author": "U0ADA",
    "to": ["U0BEN", "U0CAT"],
    "text": 'Árvíztűrő "tükörfúrógép"\nsecond line',
    "attachments": [Attachment(name="b.pdf", kind="file", ref="F2"), Attachment(name="a.png", kind="image", ref="F1")],
    "fetched": datetime(2026, 10, 14, 12, 0, tzinfo=UTC),
    "via": "test",
}
# Pinned: if this changes, every record in every vault gets a new version. Change it only with a migration.
GOLDEN_VERSION = "sha256:fd1ea8553b0375dd1dc06dc77cab86c78a48032f48d694384a560180bdb845b4"
GOLDEN_SEGMENTS = [
    {"start_ms": 0, "end_ms": 4000, "speaker": "Ben Sample", "text": "Árvíztűrő, said Ben."},
    {"start_ms": 3000, "end_ms": 9000, "text": "Talking over, no speaker."},
]
# Pinned the same way: the golden record with a transcript's segments.
GOLDEN_SEGMENTS_VERSION = "sha256:650ea17ac32b07d07a24f1cbb475f21c2f9c90132e6a4fbb472506fe88f49e0a"
# The raw log line Docket wrote for the golden record before segments existed, byte for byte: an older machine refuses
# fields it doesn't know, so a record without segments must still write exactly this.
GOLDEN_LINE = (
    '{"rid":"chat:C0TEST/1791000001.000100","v":"","source":"chat","kind":"message","container":"chat:C0TEST",'
    '"thread":null,"ts":"2026-10-14T09:30:01Z","author":"U0ADA","to":["U0BEN","U0CAT"],"title":null,'
    '"text":"Árvíztűrő \\"tükörfúrógép\\"\\nsecond line","attachments":[{"name":"b.pdf","kind":"file","ref":"F2"},'
    '{"name":"a.png","kind":"image","ref":"F1"}],"native":{},"fetched":"2026-10-14T12:00:00Z","via":"test",'
    '"fidelity":"verbatim","run":"","engine":null,"lang":null}'
)


def test_the_golden_record_hashes_to_its_pinned_version() -> None:
    assert version(RawRecord.model_validate(GOLDEN)) == GOLDEN_VERSION


def test_a_record_without_segments_hashes_as_before_they_existed() -> None:
    for none in (None, []):
        assert version(RawRecord.model_validate({**GOLDEN, "segments": none})) == GOLDEN_VERSION


def test_a_record_without_segments_writes_the_line_it_wrote_before_them() -> None:
    for none in (None, []):
        assert RawRecord.model_validate({**GOLDEN, "segments": none}).to_json() == GOLDEN_LINE
    with_segments = RawRecord.model_validate({**GOLDEN, "segments": GOLDEN_SEGMENTS})
    line = with_segments.to_json()
    assert '"segments":[{"start_ms":0,"end_ms":4000,"speaker":"Ben Sample",' in line
    assert RawRecord.model_validate_json(line) == with_segments


def test_segments_are_in_the_version_in_their_order() -> None:
    with_segments = RawRecord.model_validate({**GOLDEN, "segments": GOLDEN_SEGMENTS})
    assert version(with_segments) == GOLDEN_SEGMENTS_VERSION
    reordered = RawRecord.model_validate({**GOLDEN, "segments": list(reversed(GOLDEN_SEGMENTS))})
    assert version(reordered) != GOLDEN_SEGMENTS_VERSION
    retimed = [{**GOLDEN_SEGMENTS[0], "end_ms": 4001}, GOLDEN_SEGMENTS[1]]
    assert version(RawRecord.model_validate({**GOLDEN, "segments": retimed})) != GOLDEN_SEGMENTS_VERSION


def test_fields_outside_the_projection_never_change_the_version() -> None:
    other = RawRecord.model_validate(
        {
            **GOLDEN,
            "native": {"reactions": 3, "reply_count": 2},
            "fetched": datetime(2027, 1, 1, tzinfo=UTC),
            "via": "another-route",
            "engine": "x",
            "lang": "hu",
            "fidelity": "derived",
            "run": "2026-10-14T1550-test-box",
        }
    )
    assert version(other) == GOLDEN_VERSION


def test_attachment_order_milliseconds_and_empty_values_never_change_the_version() -> None:
    reordered = RawRecord.model_validate({**GOLDEN, "attachments": list(reversed(GOLDEN["attachments"]))})  # type: ignore[arg-type]
    precise = RawRecord.model_validate({**GOLDEN, "ts": GOLDEN["ts"] + timedelta(milliseconds=120)})  # type: ignore[operator]
    with_empty = RawRecord.model_validate({**GOLDEN, "title": "", "thread": None})
    assert version(reordered) == version(precise) == version(with_empty) == GOLDEN_VERSION


def test_content_changes_the_version(records: Make) -> None:
    assert version(records(1)) != version(records(1, text="edited"))
    assert version(records(1)) != version(records(1, attachments=[Attachment(name="x")]))


def test_the_same_instant_in_another_zone_is_the_same_version(records: Make) -> None:
    budapest = timezone(timedelta(hours=2))
    a = records(1, ts=datetime(2026, 10, 14, 9, 30, 1, tzinfo=UTC))
    b = records(1, ts=datetime(2026, 10, 14, 11, 30, 1, tzinfo=budapest))
    assert version(a) == version(b)
    assert b.ts.tzinfo == UTC


def test_a_naive_timestamp_is_refused(records: Make) -> None:
    with pytest.raises(ValidationError, match="time zone"):
        records(1, ts=datetime(2026, 10, 14, 9, 30))


@pytest.mark.parametrize(
    ("rid", "source"), [("chat:has space", "chat"), ("nocolon", "chat"), ("calls:x", "chat"), ("Chat:x", "Chat")]
)
def test_bad_rids_and_sources_are_refused(records: Make, rid: str, source: str) -> None:
    with pytest.raises(ValidationError):
        records(1, rid=rid, source=source)


def test_unknown_fields_are_refused(records: Make) -> None:
    with pytest.raises(ValidationError):
        records(1, mood="happy")


def test_canonical_json_is_ours() -> None:
    assert (
        canonical_json({"b": [1, True, None], "a": 'q"\\\n\t\x01é'})
        == '{"a":"q\\"\\\\\\n\\t\\u0001é","b":[1,true,null]}'
    )
    assert canonical_json(False) == "false"
    with pytest.raises(TypeError):
        canonical_json(1.5)


def test_a_source_can_register_its_own_projection(records: Make) -> None:
    register_projection("notes", lambda r: {"text": r.text})
    try:
        a = records(1, rid="notes:1", source="notes", author="U0ADA")
        b = records(1, rid="notes:1", source="notes", author="U0BEN")
        assert version(a) == version(b)
    finally:
        register_projection("notes", lambda r: {"text": r.text, "author": r.author})
