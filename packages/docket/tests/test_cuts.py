from __future__ import annotations

from typing import Any

import pytest
from docket_testkit import make_record
from pydantic import ValidationError

from docket.errors import DocketError
from docket.vault.cuts import CUT_MARKER, Cut, apply, cut_text, merge, parse
from docket.vault.records import RawRecord

SAID: list[dict[str, Any]] = [
    {"start_ms": 0, "end_ms": 4000, "speaker": "Ben Sample", "text": "Morning, Cleo."},
    {"start_ms": 3000, "end_ms": 9000, "speaker": "Cleo Test", "text": "Morning. About my dentist on Friday."},
    {"start_ms": 9000, "end_ms": 12000, "speaker": "Ben Sample", "text": "The offer is ten million."},
    {"start_ms": 12000, "end_ms": 12000, "speaker": None, "text": "(a beep)"},
    {"start_ms": 20000, "end_ms": 26000, "speaker": "Cleo Test", "text": "Send it by Friday."},
]


def transcript(**fields: Any) -> RawRecord:
    base: dict[str, Any] = {
        "rid": "calls:rec-1/transcript",
        "source": "calls",
        "kind": "transcript",
        "segments": SAID,
        "native": {"utterances": "the stretch again, in the vendor's shape"},
    }
    return make_record(**{**base, **fields})


def texts(record: RawRecord) -> list[str]:
    return [s.text for s in record.segments or []]


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


def test_a_text_cut_leaves_its_marker_clears_native_and_marks_the_record_cut() -> None:
    record = make_record(text="work part. private part. work again.", native={"text": "private part"})
    out = apply(record, [Cut(start=11, end=25)])
    assert out.text == f"work part. {CUT_MARKER}work again."
    assert out.native == {}
    assert out.fidelity == "cut"
    assert apply(record, []) is record


def test_a_cut_past_the_end_is_refused() -> None:
    with pytest.raises(DocketError, match="past the end"):
        cut_text(make_record(text="short"), [Cut(start=2, end=50)])


def test_a_time_cut_drops_whole_segments_and_widens_its_marker() -> None:
    out = apply(transcript(), [Cut(start=5000, end=6000, unit="ms")])
    # The cut touched only Cleo's line, 0:03 to 0:09, so the marker spans that whole line.
    assert [(s.start_ms, s.end_ms, s.text) for s in out.segments or [] if s.text == CUT_MARKER] == [
        (3000, 9000, "[cut]")
    ]
    assert "dentist" not in out.model_dump_json()
    assert texts(out) == ["Morning, Cleo.", CUT_MARKER, "The offer is ten million.", "(a beep)", "Send it by Friday."]
    assert out.text == "\n".join(
        [
            "Ben Sample: Morning, Cleo.",
            CUT_MARKER,
            "Ben Sample: The offer is ten million.",
            "(a beep)",
            "Cleo Test: Send it by Friday.",
        ]
    )
    assert out.native == {}
    assert out.fidelity == "cut"


def test_cuts_that_touch_after_widening_become_one_marker() -> None:
    out = apply(transcript(), [Cut(start=5000, end=6000, unit="ms"), Cut(start=10000, end=11000, unit="ms")])
    # 0:03-0:09 and 0:09-0:12 widened touch at 0:09; the beep at 0:12 sits at the second cut's widened end, not in it.
    marks = [(s.start_ms, s.end_ms) for s in out.segments or [] if s.text == CUT_MARKER]
    assert marks == [(3000, 12000)]
    assert texts(out) == ["Morning, Cleo.", CUT_MARKER, "(a beep)", "Send it by Friday."]


def test_a_cut_over_silence_still_leaves_its_marker_and_a_moment_inside_a_cut_goes() -> None:
    out = apply(transcript(), [Cut(start=14000, end=16000, unit="ms")])
    assert [(s.start_ms, s.end_ms) for s in out.segments or [] if s.text == CUT_MARKER] == [(14000, 16000)]
    assert len(texts(out)) == len(SAID) + 1
    beep = apply(transcript(), [Cut(start=12000, end=13000, unit="ms")])
    assert "(a beep)" not in texts(beep)


def test_a_derived_record_stays_derived_when_cut() -> None:
    out = apply(transcript(fidelity="derived"), [Cut(start=5000, end=6000, unit="ms")])
    assert out.fidelity == "derived"
    assert CUT_MARKER in out.text
    assert out.native == {}
    assert apply(make_record(fidelity="derived", text="abc def"), [Cut(start=0, end=3)]).fidelity == "derived"


@pytest.mark.parametrize(
    ("record", "cuts", "match"),
    [
        (
            transcript(),
            [Cut(start=0, end=5)],
            r"calls:rec-1/transcript is a transcript.*cut a time, such as 12:30-14:05",
        ),
        (transcript(), [Cut(start=0, end=1000, unit="ms"), Cut(start=0, end=5)], "cut a time"),
        (transcript(), [Cut(start=26000, end=30000, unit="ms")], r"a cut from 0:26 starts after the transcript ends"),
        (make_record(text="a recording"), [Cut(start=0, end=1000, unit="ms")], "has no segments to cut by time"),
    ],
)
def test_what_can_t_be_cut_is_refused(record: RawRecord, cuts: list[Cut], match: str) -> None:
    with pytest.raises(DocketError, match=match):
        apply(record, cuts)


def test_a_long_transcript_names_hours_in_its_refusal() -> None:
    long = transcript(segments=[{"start_ms": 0, "end_ms": 3_700_000, "text": "a long call"}])
    with pytest.raises(DocketError, match=r"from 1:02:00 starts after the transcript ends \(1:01:40\)"):
        apply(long, [Cut(start=3_720_000, end=3_800_000, unit="ms")])


def test_an_empty_segment_list_is_no_transcript() -> None:
    plain = transcript(source="notes", rid="notes:n-1", segments=[], text="private note")
    assert apply(plain, [Cut(start=0, end=7)]).text == f"{CUT_MARKER} note"


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
