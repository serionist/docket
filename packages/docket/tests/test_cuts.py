from __future__ import annotations

from collections.abc import Iterator

import pytest
from docket_testkit import make_record
from pydantic import ValidationError

from docket.errors import DocketError
from docket.vault import cuts as cutting
from docket.vault.cuts import CUT_MARKER, Cut, apply, cut_text, merge, parse, register_cutter
from docket.vault.records import RawRecord


@pytest.fixture(autouse=True)
def no_cutters() -> Iterator[None]:
    saved = dict(cutting._CUTTERS)
    yield
    cutting._CUTTERS.clear()
    cutting._CUTTERS.update(saved)


def test_overlapping_and_touching_cuts_are_merged() -> None:
    cuts = [Cut(start=10, end=20), Cut(start=0, end=5), Cut(start=15, end=30), Cut(start=30, end=31)]
    assert merge(cuts) == [Cut(start=0, end=5), Cut(start=10, end=31)]
    assert merge([Cut(start=0, end=5), Cut(start=3, end=9, unit="ms")]) == [
        Cut(start=0, end=5),
        Cut(start=3, end=9, unit="ms"),
    ]


def test_a_cut_must_end_after_it_starts() -> None:
    with pytest.raises(ValidationError):
        Cut(start=5, end=5)


def test_a_text_cut_leaves_its_marker_and_clears_native() -> None:
    record = make_record(text="work part. private part. work again.", native={"text": "private part"})
    out = apply(record, [Cut(start=11, end=25)])
    assert out.text == f"work part. {CUT_MARKER}work again."
    assert out.native == {}
    assert apply(record, []) is record


def test_a_cut_past_the_end_is_refused() -> None:
    with pytest.raises(DocketError, match="past the end"):
        cut_text(make_record(text="short"), [Cut(start=2, end=50)])


def test_time_cuts_need_the_source_s_cutter() -> None:
    record = make_record(text="a recording")
    with pytest.raises(DocketError, match="no cutter for time cuts"):
        apply(record, [Cut(start=0, end=1000, unit="ms")])


def test_a_registered_cutter_cuts_its_own_source_only() -> None:
    def recording_cutter(record: RawRecord, cuts: list[Cut]) -> RawRecord:
        kept = [s for s in record.native["segments"] if not any(c.start <= s["start"] < c.end for c in cuts)]
        return record.model_copy(
            update={"text": " ".join(s["text"] for s in kept) + f" {CUT_MARKER}", "native": {"segments": kept}}
        )

    register_cutter("calls", recording_cutter)
    segments = [{"start": 0, "text": "hello team"}, {"start": 5000, "text": "the private bit"}]
    call = make_record(rid="calls:rec-1", source="calls", kind="recording", text="", native={"segments": segments})
    out = apply(call, [Cut(start=4000, end=6000, unit="ms")])
    assert out.native == {"segments": [{"start": 0, "text": "hello team"}]}
    assert out.text == f"hello team {CUT_MARKER}"
    with pytest.raises(DocketError, match="no cutter for time cuts"):
        apply(make_record(), [Cut(start=0, end=1, unit="ms")])


def test_a_cutter_that_leaves_no_marker_is_refused() -> None:
    register_cutter("calls", lambda record, cuts: record)
    call = make_record(rid="calls:rec-2", source="calls", text="x")
    with pytest.raises(DocketError, match="left no"):
        apply(call, [Cut(start=0, end=1)])


@pytest.mark.parametrize(
    ("spec", "cut"),
    [
        ("120-340", Cut(start=120, end=340)),
        ("12:30-14:05", Cut(start=750_000, end=845_000, unit="ms")),
        ("1:02:03 - 1:04:00", Cut(start=3_723_000, end=3_840_000, unit="ms")),
    ],
)
def test_cut_specs_parse(spec: str, cut: Cut) -> None:
    assert parse(spec) == cut


@pytest.mark.parametrize("spec", ["120", "a-b", "-5", "10-5", "12:30-140", "5:00-4:00", "1-2-3"])
def test_bad_cut_specs_are_refused(spec: str) -> None:
    with pytest.raises(DocketError):
        parse(spec)


def test_a_text_cut_clears_the_segments_that_still_say_the_cut_stretch() -> None:
    record = make_record(
        rid="calls:rec-1",
        source="calls",
        kind="recording",
        text="work part. private part. work again.",
        segments=[
            {"start_ms": 0, "end_ms": 1000, "text": "work part."},
            {"start_ms": 1000, "end_ms": 2000, "text": "private part."},
        ],
    )
    out = apply(record, [Cut(start=11, end=25)])
    assert out.text == f"work part. {CUT_MARKER}work again."
    assert out.segments is None
    assert "private" not in out.model_dump_json()
