from __future__ import annotations

from typing import Any

import pytest
from docket_testkit import make_record
from pydantic import ValidationError

from docket.errors import DocketError
from docket.vault import segments as seg
from docket.vault.records import RawRecord
from docket.vault.segments import Segment, register_segments, segments, time_range


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test registers into a registry of its own, so no reader outlives the test that made it."""
    monkeypatch.setattr(seg, "_READERS", {})


def recording(native_segments: Any, source: str = "calls") -> RawRecord:
    return make_record(rid=f"{source}:rec-0001", source=source, kind="recording", native={"segments": native_segments})


def test_the_default_reader_sorts_and_keeps_overlaps() -> None:
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


def test_no_segments_key_means_no_segments() -> None:
    assert segments(make_record()) == []


@pytest.mark.parametrize(
    ("bad", "says"),
    [
        ({"start_ms": 2000, "end_ms": 1000, "text": "x"}, "ends"),
        ({"start_ms": 0, "end_ms": 1000}, "text"),
        ({"start": 0.5, "end": 1.5, "text": "seconds, not ms"}, "start_ms"),
        ({"start_ms": 0.5, "end_ms": 1000, "text": "a fraction"}, "start_ms"),
        ({"start_ms": -1, "end_ms": 1000, "text": "before the start"}, "start_ms"),
    ],
)
def test_a_malformed_segment_names_the_record(bad: dict[str, Any], says: str) -> None:
    with pytest.raises(DocketError, match=rf"calls:rec-0001: segment 2 .*{says}"):
        segments(recording([{"start_ms": 0, "end_ms": 10, "text": "fine"}, bad]))


def test_segments_that_arent_a_list_name_the_record() -> None:
    with pytest.raises(DocketError, match="calls:rec-0001: native segments isn't a list"):
        segments(recording({"start_ms": 0}))


def test_a_registered_reader_wins_for_its_own_source_only() -> None:
    def vendor(record: RawRecord) -> list[Segment]:
        utterances = record.native["utt"]
        return [Segment(start_ms=int(u["t"] * 1000), end_ms=int(u["t"] * 1000) + 500, text=u["w"]) for u in utterances]

    register_segments("vendor", vendor)
    theirs = make_record(rid="vendor:r1", source="vendor", kind="recording", native={"utt": [{"t": 1.5, "w": "hello"}]})
    assert segments(theirs) == [Segment(start_ms=1500, end_ms=2000, text="hello")]
    ours = recording([{"start_ms": 0, "end_ms": 10, "text": "default"}])
    assert [s.text for s in segments(ours)] == ["default"]


def test_a_segment_is_frozen_and_refuses_unknown_fields() -> None:
    s = Segment(start_ms=0, end_ms=0, text="")
    with pytest.raises(ValidationError):
        s.text = "changed"  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(ValidationError):
        Segment.model_validate({"start_ms": 0, "end_ms": 1, "text": "x", "confidence": 0.9})


def test_the_time_range_rounds_out_and_takes_the_greatest_end() -> None:
    assert time_range([]) is None
    overlapping = [
        Segment(start_ms=1500, end_ms=60_000, text="long"),
        Segment(start_ms=50_000, end_ms=55_200, text="starts last, ends first"),
    ]
    assert time_range(overlapping) == (1, 60)
    assert time_range([Segment(start_ms=999, end_ms=1001, text="x")]) == (0, 2)
