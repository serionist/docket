"""Numbers in what two transcripts heard (DR-0583): digits and number words per language and their values, so one
number written two ways reads alike and two numbers don't."""

from __future__ import annotations

from fractions import Fraction

import pytest

from docket.reconcile import numbers


@pytest.mark.parametrize(
    ("text", "lang", "value"),
    [
        ("42", "en", 42),
        ("10,000", "en", 10_000),
        ("10 000", "hu", 10_000),
        ("10.000", "hu", 10_000),
        ("1,5", "hu", Fraction(3, 2)),
        ("3.14159", "en", Fraction(314159, 100000)),
        ("forty two", "en", 42),
        ("forty-two", "en", 42),
        ("ten thousand", "en", 10_000),
        ("two hundred thousand", "en", 200_000),
        ("one million two hundred thousand", "en", 1_200_000),
        ("5 million", "en", 5_000_000),
        ("tízmillió", "hu", 10_000_000),
        ("tíz millió", "hu", 10_000_000),
        ("1,5 millió", "hu", 1_500_000),
        ("huszonöt", "hu", 25),
        ("tizenkettő", "hu", 12),
        ("kétszázezer", "hu", 200_000),
        ("ezerkilencszáznyolcvannégy", "hu", 1984),
        ("Tízmillió", None, 10_000_000),
    ],
)
def test_digits_and_number_words_read_as_their_value(text: str, lang: str | None, value: int | Fraction) -> None:
    assert numbers.values(text, lang) == (Fraction(value),)
    assert numbers.holds(text, lang)


def test_one_number_written_two_ways_reads_alike_and_two_numbers_do_not() -> None:
    assert numbers.reading("forty two", "en") == numbers.reading("42", "en")
    assert numbers.reading("tízmillió", "hu") == numbers.reading("tíz millió", "hu")
    assert numbers.reading("ten thousand", "en") != numbers.reading("ten million", "en")
    # The words around a number are read too: only the number may be written another way.
    assert numbers.reading("the offer is ten", "en") == ("the", "offer", "is", Fraction(10))
    assert numbers.reading("ten units", "en") != numbers.reading("ten items", "en")


def test_numbers_apart_stay_apart() -> None:
    # A unit after a unit, a number after digits, or a comma between them starts another number.
    assert numbers.values("two forty", "en") == (2, 40)
    assert numbers.values("10 two", "en") == (10, 2)
    assert numbers.values("ten, twenty", "en") == (10, 20)
    assert numbers.values("ten units, ten each", "en") == (10, 10)


def test_egy_and_one_alone_hold_no_number() -> None:
    assert not numbers.holds("egy", "hu")
    assert not numbers.holds("one of them", "en")
    assert numbers.values("egy", "hu") == (1,)
    assert numbers.holds("egy millió", "hu")
    assert numbers.holds("egymillió", "hu")
    assert numbers.holds("one and two", "en")


def test_a_word_that_is_not_wholly_number_words_is_a_word() -> None:
    # tízmilliót has a case ending: read as a word until the typed extractors come.
    for text, lang in (("tízmilliót", "hu"), ("hétfő", "hu"), ("often", "en"), ("someone", "en"), ("hundreds", "en")):
        assert numbers.values(text, lang) == (), text
        assert not numbers.holds(text, lang), text
    assert numbers.holds("B2", "en")
    assert numbers.reading("B2 units", "en") == ("b2", "units")


def test_filler_sounds_are_skipped_inside_a_number() -> None:
    assert numbers.reading("ten um thousand", "en", frozenset({"um"})) == (Fraction(10_000),)
