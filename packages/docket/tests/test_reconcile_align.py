"""Aligning the recorder's transcript with Whisper's, per recorder line (DR-0583): words at their places, Whisper's
words timed and given to the lines, crosstalk, the edge rule both ways, widening to a text that stands once, NFC,
fillers, case and accents, and cuts by segments' own times. The transcripts are invented."""

from __future__ import annotations

import unicodedata

from reconcile_kit import heard, segment, transcript

from docket.reconcile import align
from docket.reconcile.align import Aligned, Stretch
from docket.vault.cuts import CUT_MARKER, Cut
from docket.vault.records import Segment


def aligned(mine: list[tuple[int, int, str]], theirs: list[tuple[int, int, str]], lang: str | None = "en") -> Aligned:
    """The recorder's lines and Whisper's segments, each (start, end, text)."""
    left = transcript([segment(a, b, text, "Ben Sample") for a, b, text in mine], lang=lang)
    right = heard([segment(a, b, text) for a, b, text in theirs], lang=lang)
    return align.align(left, right)


def texts(found: Aligned) -> list[tuple[str, str]]:
    return [found.texts(s) for s in align.stretches(found)[0]]


def test_words_keep_their_places_in_nfc_and_compare_casefolded_with_accents_kept() -> None:
    line = unicodedata.normalize("NFD", "Um, Dóra's offer")
    found = align.words(unicodedata.normalize("NFC", line), "en")
    assert [(w.text, w.key, w.start, w.end) for w in found] == [
        ("Dóra", "dóra", 4, 8),
        ("s", "s", 9, 10),
        ("offer", "offer", 11, 16),
    ]
    # A filler of another language stays a word in this one; one with no language leaves out every language's.
    assert [w.text for w in align.words("öö yes", "en")] == ["öö", "yes"]
    assert [w.text for w in align.words("öö yes um", None)] == ["yes"]


def test_a_text_stands_where_no_letter_or_digit_is_either_side_and_compares_in_nfc() -> None:
    line = "the offer is ten and the deadline is Friday"
    assert align.places("the", line) == [(0, 3), (21, 24)]
    assert align.place("the", line) is None
    assert align.place("the offer", line) == (0, 9)
    # "he" sits inside "the": not at word boundaries.
    assert align.places("he", line) == []
    assert align.places("", line) == []
    decomposed = unicodedata.normalize("NFD", "Dóra Minta joined")
    assert align.place("Dóra Minta", decomposed) == (0, 10)


def test_case_alone_agrees_and_accents_differ() -> None:
    found = aligned([(0, 3000, "We met kovács today")], [(0, 3000, "We met Kovács today")])
    assert texts(found) == []
    found = aligned([(0, 3000, "We met Kovacs today")], [(0, 3000, "We met Kovács today")])
    assert texts(found) == [("Kovacs", "Kovács")]


def test_filler_sounds_are_left_out_of_the_comparison() -> None:
    found = aligned([(0, 3000, "The offer, um, came today")], [(0, 3000, "The offer came today")])
    assert texts(found) == []
    found = aligned([(0, 3000, "öö a tíz millió")], [(0, 3000, "a tízmillió")], lang="hu")
    assert texts(found) == [("tíz millió", "tízmillió")]


def test_a_whisper_word_is_timed_by_its_share_of_its_segment_s_characters() -> None:
    found = aligned([(0, 1000, "one"), (1000, 2000, "two")], [(0, 2000, "aaaa bbbb")])
    # aaaa's middle is at 2 of 9 characters, bbbb's at 7: 444 ms and 1555 ms.
    assert [w.at for w in found.heard.words] == [444, 1555]
    assert found.given == [[0], [1]]


def test_a_word_in_a_gap_goes_to_the_nearer_line_within_a_second() -> None:
    mine = [(0, 2000, "first line"), (5000, 7000, "second line")]
    # Words at 2400 and 4800 (each 0.4 s and 0.2 s from a line), and one at 3600: 1.4 s from either.
    theirs = [(2300, 2500, "late"), (3500, 3700, "lost"), (4700, 4900, "early")]
    found = aligned(mine, theirs)
    assert [[found.heard.words[k].text for k in ks] for ks in found.given] == [["late"], ["early"]]
    # Equally far from both, it goes to the one before.
    found = aligned([(0, 2000, "a"), (3000, 5000, "b")], [(2400, 2600, "between")])
    assert found.given == [[0], []]
    # Before the first line and past the last, within a second.
    found = aligned([(2000, 3000, "a")], [(1500, 1700, "early"), (3500, 3700, "late"), (6000, 6200, "gone")])
    assert found.given == [[0, 1]]


def test_crosstalk_gives_a_word_to_the_line_its_segment_matches_more_words_of() -> None:
    mine = [(0, 6000, "the crane passed the load test"), (2000, 5000, "I can rebook the night shift")]
    theirs = [(2000, 4000, "I can rebook")]
    found = aligned(mine, theirs)
    assert found.given == [[], [0, 1, 2]]
    # A tie goes to the earlier line.
    found = aligned([(0, 4000, "alpha beta"), (1000, 4000, "gamma delta")], [(1000, 3000, "omega")])
    assert found.given == [[0], []]


def test_a_disagreement_at_a_line_s_end_stays_a_spot() -> None:
    found = aligned(
        [(0, 3000, "So the offer is ten"), (4000, 6000, "Agreed then")],
        [(0, 3000, "So the offer is ten million"), (4000, 6000, "Agreed then")],
    )
    assert texts(found) == [("ten", "ten million")]


def test_words_whisper_put_across_a_line_s_edge_make_no_spot() -> None:
    mine = [(0, 3000, "We sign the offer"), (3000, 6000, "on Friday next week")]
    # Whisper's segment boundary is a word late: "on" is timed into the first line.
    theirs = [(0, 3100, "We sign the offer on"), (3100, 6000, "Friday next week")]
    found = aligned(mine, theirs)
    assert [[found.heard.words[k].text for k in ks] for ks in found.given] == [
        ["We", "sign", "the", "offer", "on"],
        ["Friday", "next", "week"],
    ]
    assert texts(found) == []
    # A word late the other way: the first line's last word timed into the second.
    theirs = [(0, 2800, "We sign the"), (2800, 6000, "offer on Friday next week")]
    found = aligned(mine, theirs)
    assert [[found.heard.words[k].text for k in ks] for ks in found.given] == [
        ["We", "sign", "the"],
        ["offer", "on", "Friday", "next", "week"],
    ]
    assert texts(found) == []


def test_an_edge_word_that_is_not_the_neighbour_s_stays() -> None:
    mine = [(0, 3000, "We sign the offer"), (3000, 6000, "on Friday next week")]
    theirs = [(0, 3000, "We sign the offer now"), (3000, 6000, "Friday next week")]
    found = aligned(mine, theirs)
    assert texts(found) == [("offer", "offer now"), ("on Friday", "Friday")]


def test_a_spot_widens_both_texts_until_the_recorder_s_stands_once() -> None:
    found = aligned(
        [(0, 4000, "the offer is ten and the deadline is Friday")],
        [(0, 4000, "the new offer is ten and the deadline is Friday")],
    )
    assert texts(found) == [("the offer", "the new offer")]
    found = aligned([(0, 3000, "ten units, ten each")], [(0, 3000, "ten units, two each")])
    assert texts(found) == [("units, ten each", "units, two each")]
    # A text that stands once isn't widened.
    found = aligned([(0, 3000, "The offer from Nortwin came")], [(0, 3000, "The offer from Northwood came")])
    assert texts(found) == [("Nortwin", "Northwood")]


def test_past_six_words_it_was_heard_differently() -> None:
    found = aligned([(0, 5000, "one two three four five six seven")], [(0, 5000, "a b c d e f g")])
    assert align.stretches(found) == ([], 1)
    # A word said once more in a line of that word alone never stands once within six.
    found = aligned([(0, 5000, "so so so so so so so so")], [(0, 5000, "so so so so so so so")])
    assert align.stretches(found) == ([], 1)


def test_a_word_both_spell_alike_can_be_marked_and_widens_like_any() -> None:
    mine = [(0, 3000, "Nortwin said Nortwin will sign")]
    found = aligned(mine, mine)

    def marked(line: align.Line, word: align.Word) -> bool:
        return word.text == "Nortwin"

    stretches, differently = align.stretches(found, alike=marked)
    # Each stands twice, so each widens: the first to "Nortwin said", the second both ways, and the two overlap.
    assert [(found.texts(s), s.alike) for s in stretches] == [
        (("Nortwin said Nortwin will", "Nortwin said Nortwin will"), True)
    ]
    assert differently == 0
    # Twice in a line Whisper heard once, the first has nothing to widen to: heard differently, beside the delete.
    found = aligned([(0, 3000, "Nortwin and Nortwin")], [(0, 1000, "Nortwin")])
    stretches, differently = align.stretches(found, alike=marked)
    assert ([found.texts(s) for s in stretches], differently) == ([("Nortwin and Nortwin", "Nortwin")], 1)


def test_stretches_whose_places_overlap_are_one() -> None:
    # Each "a" stands twice: the first widens to "a b", the second to "b a c", and the two share a "b".
    found = aligned([(0, 3000, "a b a c")], [(0, 3000, "x b y c")])
    stretches, _ = align.stretches(found)
    assert [found.texts(s) for s in stretches] == [("a b a c", "x b y c")]
    assert [found.texts(core) for core in stretches[0].cores] == [("a", "x"), ("a", "y")]
    # Apart, they stay two.
    found = aligned([(0, 3000, "a b c d a e f g h")], [(0, 3000, "x b c d y e f g h")])
    assert texts(found) == [("a b", "x b"), ("d a e", "d y e")]
    # Made one past six words, it was heard differently.
    found = aligned([(0, 3000, "a b a c a d a e")], [(0, 3000, "x b y c z d w e")])
    assert align.stretches(found) == ([], 1)


def test_a_kept_candidate_is_one_keep_says_yes_to() -> None:
    found = aligned([(0, 3000, "Forty two units and ten more")], [(0, 3000, "42 units and two more")])

    def numbers_only(s: Stretch) -> bool:
        return found.texts(s)[0] == "ten"

    assert [found.texts(s) for s in align.stretches(found, keep=numbers_only)[0]] == [("ten", "two")]


def test_a_line_given_no_words_and_a_line_of_no_words_make_none() -> None:
    found = aligned([(0, 2000, "something said"), (9000, 9500, "...")], [(9000, 9500, "but heard")])
    assert found.given == [[], [0, 1]]
    assert align.stretches(found) == ([], 0)


def test_cuts_are_taken_by_segments_own_times() -> None:
    mine = [(0, 5000, "Before the cut"), (5000, 731_400, "A line ending at 12:11.4"), (731_600, 740_000, CUT_MARKER)]
    theirs = [(0, 5000, "Before the cat"), (5000, 731_400, "A line ending at 12:11.4"), (731_500, 735_000, "Inside it")]
    found = aligned(mine, theirs)
    # The line beside the cut is kept; Whisper's segment overlapping it is skipped whole, its text still joined.
    assert [line.text for line in found.lines] == ["Before the cut", "A line ending at 12:11.4"]
    assert "Inside" not in [w.text for w in found.heard.words]
    assert found.heard.text.endswith("Inside it")
    assert texts(found) == [("cut", "cat")]


def test_a_cut_either_transcript_holds_skips_the_other_s_segments_too() -> None:
    record = transcript([segment(0, 3000, "Kept"), segment(3000, 9000, CUT_MARKER), segment(9000, 12000, "Kept too")])
    other = heard([segment(0, 3000, "Kept"), segment(3500, 8000, "said in the cut"), segment(9000, 12000, "Kept too")])
    spans = align.cut_spans(record, other)
    assert spans == [Cut(start=3000, end=9000, unit="ms")]
    assert align.in_cut(Segment(start_ms=3500, end_ms=8000, text="x"), spans)
    assert not align.in_cut(Segment(start_ms=9000, end_ms=9000, text="x"), spans)
    found = align.align(record, other)
    assert [w.text for w in found.heard.words] == ["Kept", "Kept", "too"]
    # Its own [cut] is a line of neither: not a line, not a word, not in the joined text.
    assert [line.index for line in found.lines] == [0, 1]
    assert "[cut]" not in found.heard.text


def test_a_recorder_line_in_whisper_s_cut_is_skipped_whole() -> None:
    record = transcript([segment(0, 3000, "Kept"), segment(3000, 9000, "Said where Whisper was cut")])
    other = heard([segment(0, 3000, "Kept"), segment(3000, 9000, CUT_MARKER)])
    found = align.align(record, other)
    assert [line.text for line in found.lines] == ["Kept"]
    # A long line before a short one still holds a word the short one ended before.
    found = aligned([(0, 9000, "a long first line"), (1000, 2000, "short")], [(5000, 6000, "line")])
    assert found.given == [[0], []]


def test_whisper_s_text_shows_a_segment_a_cut_overlaps_as_cut() -> None:
    record = transcript([segment(0, 3000, "Kept"), segment(3000, 9000, CUT_MARKER)])
    other = heard([segment(0, 3000, "We sign"), segment(3500, 8000, "secret"), segment(9000, 9900, "deal")])
    found = align.align(record, other).heard
    assert found.cut == ((8, 14),)
    assert found.shown((0, len(found.text))) == "We sign [cut] deal"
    assert found.shown((3, 10)) == "sign [cut]"
    assert found.shown((0, 7)) == "We sign"
    assert found.shown(None) == ""
