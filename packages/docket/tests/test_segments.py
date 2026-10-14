from __future__ import annotations

from typing import Any

import pytest
from docket_testkit import make_record
from pydantic import ValidationError

from docket.vault.records import RawRecord
from docket.vault.segments import Segment, segments, time_range


def recording(given: list[dict[str, Any]] | None) -> RawRecord:
    return make_record(rid="calls:rec-0001", source="calls", kind="recording", segments=given)


def test_segments_come_in_time_order_with_overlaps_kept() -> None:
    record = recording(
        [
            {"start_ms": 5000, "end_ms": 9000, "speaker": "Cleo Test", "text": "second"},
            {"start_ms": 0, "end_ms": 6000, "speaker": "Ben Sample", "text": "first, talking over"},
            {"start_ms": 5000, "end_ms": 7000, "text": "no speaker"},
        ]
    )
    got = segments(record)
    assert [s.text for s in got] == ["first, talking over", "no speaker", "second"]
    assert got[1].speaker is None
    # The record keeps them as the mapper gave them; only reading sorts.
    assert [s.text for s in record.segments or []] == ["second", "first, talking over", "no speaker"]


def test_a_record_without_segments_has_none_whatever_its_native_holds() -> None:
    assert segments(recording(None)) == []
    assert segments(recording([])) == []
    native = make_record(native={"segments": [{"start_ms": 0, "end_ms": 10, "text": "not read"}]})
    assert segments(native) == []


@pytest.mark.parametrize(
    ("bad", "says"),
    [
        ({"start_ms": 2000, "end_ms": 1000, "text": "x"}, "ends"),
        ({"start_ms": 0, "end_ms": 1000}, "text"),
        ({"start": 0.5, "end": 1.5, "text": "seconds, not ms"}, "start_ms"),
        ({"start_ms": 0.5, "end_ms": 1000, "text": "a fraction"}, "start_ms"),
        ({"start_ms": -1, "end_ms": 1000, "text": "before the start"}, "start_ms"),
        ({"start_ms": 0, "end_ms": 1, "text": "x", "confidence": 0.9}, "confidence"),
    ],
)
def test_a_record_refuses_a_segment_out_of_shape(bad: dict[str, Any], says: str) -> None:
    with pytest.raises(ValidationError, match=says):
        recording([{"start_ms": 0, "end_ms": 10, "text": "fine"}, bad])


def test_a_segment_is_frozen() -> None:
    s = Segment(start_ms=0, end_ms=0, text="")
    with pytest.raises(ValidationError):
        s.text = "changed"  # pyright: ignore[reportAttributeAccessIssue]


def test_segments_survive_a_line_of_the_raw_log() -> None:
    record = recording([{"start_ms": 0, "end_ms": 4000, "speaker": "Ben Sample", "text": "Morning."}])
    again = RawRecord.model_validate_json(record.to_json())
    assert again.segments == record.segments


def test_the_time_range_rounds_out_and_takes_the_greatest_end() -> None:
    assert time_range([]) is None
    overlapping = [
        Segment(start_ms=1500, end_ms=60_000, text="long"),
        Segment(start_ms=50_000, end_ms=55_200, text="starts last, ends first"),
    ]
    assert time_range(overlapping) == (1, 60)
    assert time_range([Segment(start_ms=999, end_ms=1001, text="x")]) == (0, 2)
