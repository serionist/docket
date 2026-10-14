from __future__ import annotations

from pathlib import Path

import pytest
from docket_testkit import MAIL, REC, SIDE, THREAD, VENDOR, make_record, vendor_segments, write_raw

from docket.vault import segments as seg
from docket.vault.anchors import Anchor, parse_anchor
from docket.vault.records import RawRecord
from docket.vault.spans import RawLookup, problem, span


@pytest.fixture(scope="module")
def raw_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The fixture's raw log without git, written once for the module: these tests only read it."""
    root = tmp_path_factory.mktemp("raw-only")
    write_raw(root)
    return root


@pytest.fixture
def lookup(raw_root: Path, monkeypatch: pytest.MonkeyPatch) -> RawLookup:
    monkeypatch.setattr(seg, "_READERS", {"vendor": vendor_segments})
    return RawLookup(raw_root)


def record(lookup: RawLookup, rid: str) -> RawRecord:
    found = lookup.latest(rid)
    assert found is not None
    return found


def test_versions_come_in_run_order_and_unknown_rids_have_none(lookup: RawLookup) -> None:
    versions = lookup.versions(THREAD[2])
    assert [v.text for v in versions] == ["thread message 3", "thread message 3, edited"]
    assert lookup.versions("chat:nothing-here") == []
    assert lookup.versions("not a rid") == []
    assert lookup.versions("nosuch:source") == []
    assert lookup.latest("chat:nothing-here") is None


def test_only_the_rid_s_source_folder_is_read(tmp_path: Path) -> None:
    write_raw(tmp_path)
    broken = tmp_path / "raw" / "mail" / "2026" / "10" / "2026-10-14T1200-test-box.jsonl"
    broken.write_text("not json\n", encoding="utf-8")
    lookup = RawLookup(tmp_path)
    assert lookup.latest(THREAD[0]) is not None
    with pytest.raises(ValueError, match="Expecting value"):
        lookup.latest(MAIL)


def test_neighbours_are_the_thread_then_the_container(lookup: RawLookup) -> None:
    def rids(rid: str, n: int) -> list[str]:
        return [r.rid for r in lookup.neighbours(record(lookup, rid), n)]

    assert rids(THREAD[1], 1) == THREAD
    assert rids(THREAD[0], 1) == THREAD[:2]
    assert rids(THREAD[1], 0) == [THREAD[1]]
    assert rids(SIDE[0], 5) == SIDE
    assert rids(REC, 3) == [REC]
    assert [r.text for r in lookup.neighbours(record(lookup, THREAD[1]), 1)][2] == "thread message 3, edited"


@pytest.mark.parametrize(
    ("rid", "token", "says"),
    [
        (REC, "@12:08", None),
        (REC, "@0:15", None),  # in the gap between utterances
        (REC, "@1:02:10", None),  # the greatest end, rounded up
        (REC, "@1:02:11", "1:02:11 is outside calls:rec-0042, which runs from 00:00 to 1:02:10"),
        (REC, "@14:30-14:52", None),
        (REC, "@1:02:00-1:03:00", "1:03:00 is outside calls:rec-0042, which runs from 00:00 to 1:02:10"),
        (VENDOR, "@0:00", "00:00 is outside vendor:rec-0007, which runs from 00:01 to 00:05"),
        (VENDOR, "@0:04", None),
        (MAIL, "#2", None),
        (MAIL, "#2-3", None),
        (MAIL, "#4", f"{MAIL} has 3 paragraph(s), so #4 isn't one"),
        (
            MAIL,
            "@0:01",
            f"{MAIL} has no transcript segments; anchor the whole record: [r:mail:<50%25off%5Dx@example.invalid>]",
        ),
        (THREAD[0], "", None),
    ],
)
def test_whether_an_anchor_resolves(lookup: RawLookup, rid: str, token: str, says: str | None) -> None:
    if token.startswith("#"):
        first, _, last = token[1:].partition("-")
        anchor = Anchor(rid, "part", int(first), int(last) if last else None)
    else:
        anchor = parse_anchor(token) if token else Anchor(rid, "record")
    assert problem(anchor, record(lookup, rid)) == says


def test_a_malformed_transcript_is_the_reason_a_time_doesn_t_resolve() -> None:
    broken = make_record(rid="calls:rec-bad", source="calls", native={"segments": [{"start_ms": 5}]})
    assert "calls:rec-bad: segment 1" in (problem(Anchor(None, "time", 1), broken) or "")
    whole = span(Anchor("calls:rec-bad", "record"), broken, 1)
    assert (whole.problem, whole.segments, whole.text) == (None, [], "message 1")


def test_a_point_shows_the_next_window_and_around_widens_it(lookup: RawLookup) -> None:
    call = record(lookup, REC)
    point = span(parse_anchor("@12:08"), call, 1)
    assert [s.text for s in point.segments] == ["So ten million, for the whole quarter."]
    assert (point.start_ms, point.end_ms, point.anchor, point.kind, point.title) == (
        728_000,
        788_000,
        "@12:08",
        "recording",
        "Supplier call",
    )
    wider = span(parse_anchor("@12:08"), call, 1, around=2)
    assert [s.speaker for s in wider.segments] == ["Ben Sample", "Cleo Test"]
    gap = span(parse_anchor("@0:15"), call, 1, window=10)
    assert [s.text for s in gap.segments] == ["The consultant is 10 million forints."]
    start = span(parse_anchor("@0:04"), call, 1, window=1)
    assert [s.speaker for s in start.segments] == ["Cleo Test"]


def test_a_stretch_shows_what_overlaps_it(lookup: RawLookup) -> None:
    got = span(parse_anchor("[r:calls:rec-0042@14:30-14:52]"), record(lookup, REC), 1, written="as written")
    assert [s.text for s in got.segments] == ["Please send the offer in writing by Friday."]
    assert (got.start_ms, got.end_ms, got.anchor) == (870_000, 893_000, "as written")


def test_paragraphs_and_whole_records_show_their_text(lookup: RawLookup) -> None:
    mail = record(lookup, MAIL)
    two = span(Anchor(MAIL, "part", 2), mail, 1)
    assert (two.paragraphs, two.text) == ((2, 2), "The offer stands at 10 million.\nUntil Friday.")
    assert span(Anchor(MAIL, "part", 2), mail, 1, around=5).paragraphs == (1, 3)
    whole = span(Anchor(MAIL, "record"), mail, 1)
    assert "\r" not in whole.text
    assert whole.anchor == "[r:mail:<50%25off%5Dx@example.invalid>]"
    edited = span(Anchor(THREAD[2], "record"), record(lookup, THREAD[2]), 2)
    assert (edited.versions, edited.text) == (2, "thread message 3, edited")
    call = span(Anchor(REC, "record"), record(lookup, REC), 1)
    assert len(call.segments) == 7


def test_an_anchor_that_doesnt_resolve_comes_back_with_its_problem(lookup: RawLookup) -> None:
    got = span(parse_anchor("@2:00:00"), record(lookup, REC), 1)
    assert got.problem is not None
    assert got.segments == []
