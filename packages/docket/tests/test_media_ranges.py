"""Which stretches of a recording's audio are cut (DR-0561): a release's cuts, a transcript's `[cut]` segments, none
from a record cut by characters. Invented records only."""

from __future__ import annotations

from typing import Any

from docket_testkit import make_record
from media_kit import CALL, POLISHED, SAID

from docket.media import ranges
from docket.vault import cuts
from docket.vault.cuts import Cut
from docket.vault.records import RawRecord


def transcript(**over: Any) -> RawRecord:
    lines = "\n".join(f"{s['speaker']}: {s['text']}" for s in SAID)
    return make_record(
        rid=POLISHED, source="plaud", kind="transcript", container=CALL, segments=SAID, text=lines, **over
    )


def test_ranges_merge_and_cover() -> None:
    assert ranges.merge([(5, 9), (0, 3), (3, 4), (8, 12)]) == ((0, 4), (5, 12))
    assert ranges.covers([(0, 10)], [(2, 5), (6, 10)])
    assert ranges.covers([(0, 4), (4, 10)], [(2, 8)]), "touching ranges cover what spans them"
    assert not ranges.covers([(0, 4)], [(2, 5)])
    assert not ranges.covers([], [(0, 1)])
    assert ranges.covers([], [])


def test_a_transcript_cut_by_time_gives_what_it_removed() -> None:
    cut = cuts.apply(transcript(), [Cut(start=2000, end=3000, unit="ms")])
    assert ranges.marks(cut) == [(0, 9000)], "widened to the utterance it took"
    assert not ranges.by_characters(cut)
    found = ranges.needed([cut], [(15_000, 16_000)])
    assert found == ranges.Needed(((0, 9000), (15_000, 16_000)), first_ms=0, last_ms=19_000)
    assert ranges.needed([], []) == ranges.Needed()


def test_a_record_cut_by_characters_has_no_time() -> None:
    note = make_record(rid=f"{CALL}/note", source="plaud", text="the budget, and the rest")
    assert not ranges.by_characters(note)
    cut = cuts.apply(note, [Cut(start=0, end=10)])
    assert ranges.by_characters(cut)
    assert ranges.by_characters(note.model_copy(update={"fidelity": "cut"}))


def test_what_was_cut_by_hand_is_what_the_release_doesn_t_hold() -> None:
    released = cuts.apply(transcript(), [Cut(start=0, end=9000, unit="ms")])
    assert ranges.by_hand(released, [(0, 9000)]) == []
    both = cuts.apply(released, [Cut(start=12_000, end=13_000, unit="ms")])
    assert ranges.by_hand(both, [(0, 9000)]) == [(10_000, 19_000)]
    assert ranges.by_hand(transcript(), [(0, 9000)]) == []
