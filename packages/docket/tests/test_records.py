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


def test_the_golden_record_hashes_to_its_pinned_version() -> None:
    assert version(RawRecord.model_validate(GOLDEN)) == GOLDEN_VERSION


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
