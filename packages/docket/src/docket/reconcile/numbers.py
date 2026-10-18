"""Numbers in what two transcripts heard (DR-0583): digits and number words per language, and the values they read as,
so a number written two ways (`forty two` and `42`, `tízmillió` and `tíz millió`) makes no spot.

| Reads as a number | Example |
|---|---|
| digits, thousands grouped by a comma, a dot or a space, decimals after a comma or a dot | `42`, `10,000`, `1,5` |
| a word that splits wholly into the language's number words | `forty`, `tízmillió` (`tíz` + `millió`), `huszonöt` |
| number words in a row, a space or a hyphen between them | `ten thousand`, `forty-two`, `5 millió` |

A text with no language reads every language's words. `egy` and `one` alone don't make a text hold a number: one is
Hungarian's article, the other English's pronoun as often as a number. Digits and whole number words only, until the
typed extractors come: a Hungarian number word with a case ending (`tízmilliót`) reads as a word.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache

from docket.plaintext import nfc

WORDS: dict[str, dict[str, int]] = {
    "en": {
        "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
        "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
        "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
        "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000, "million": 10**6,
        "billion": 10**9,
    },
    "hu": {
        "nulla": 0, "egy": 1, "kettő": 2, "két": 2, "három": 3, "négy": 4, "öt": 5, "hat": 6, "hét": 7, "nyolc": 8,
        "kilenc": 9, "tíz": 10, "tizen": 10, "húsz": 20, "huszon": 20, "harminc": 30, "negyven": 40, "ötven": 50,
        "hatvan": 60, "hetven": 70, "nyolcvan": 80, "kilencven": 90, "száz": 100, "ezer": 1000, "millió": 10**6,
        "milliárd": 10**9,
    },
}  # fmt: skip
# Words that only start a number: Hungarian's tizen- and huszon- take a unit after them.
PREFIXES = frozenset({"tizen", "huszon"})
ALONE = frozenset({"egy", "one"})
# Digits with their thousands grouped and a decimal part, else a word of plaintext.WORD's letters and digits.
TOKEN = re.compile(r"(?P<digits>\d+(?:[,. \u00a0\u202f]\d{3}(?!\d))*(?:[.,]\d+)?)(?![^\W_])|[^\W_]+")
GROUPED = re.compile(r"[,. \u00a0\u202f](?=\d{3}(?!\d))")
# What may stand between two number words of one number.
JOINS = frozenset(" -\u00a0\u202f")

Item = str | Fraction


def _vocab(lang: str | None) -> dict[str, int]:
    if lang in WORDS:
        return WORDS[lang]
    return {word: value for words in WORDS.values() for word, value in words.items()}


@lru_cache(maxsize=8192)
def split(word: str, lang: str | None) -> tuple[str, ...] | None:
    """A word, casefolded and in NFC, as the language's number words it is made of; None when it isn't wholly them."""
    vocab = _vocab(lang)
    found: list[tuple[str, ...] | None] = [None] * (len(word) + 1)
    found[0] = ()
    for i in range(len(word)):
        before = found[i]
        if before is None:
            continue
        for part in vocab:
            end = i + len(part)
            if end <= len(word) and found[end] is None and word.startswith(part, i):
                found[end] = (*before, part)
    return found[len(word)] or None


def _literal(digits: str) -> Fraction:
    whole, _, rest = GROUPED.sub("", digits).replace(",", ".").partition(".")
    return Fraction(f"{whole}.{rest}") if rest else Fraction(whole)


@dataclass
class _Number:
    """A number read word by word: what its scales made so far, what is below the next scale, and what its last part
    was, which says whether the next word goes on it or starts another number."""

    total: Fraction = Fraction(0)
    below: Fraction = Fraction(0)
    last: str = ""
    words: int = 0
    alone: bool = False

    def takes(self, value: int) -> bool:
        if value >= 100:
            return True
        return self.last == "scale" or (self.last in ("tens", "prefix") and value < 10)

    def add(self, value: int, part: str) -> None:
        if value >= 1000:
            self.total += Fraction(self.below or 1) * value
            self.below = Fraction(0)
        elif value == 100:
            self.below = Fraction(self.below or 1) * value
        else:
            self.below += value
        if value >= 100:
            self.last = "scale"
        elif part in PREFIXES:
            self.last = "prefix"
        else:
            self.last = "tens" if value >= 20 and value % 10 == 0 else "unit"

    @property
    def value(self) -> Fraction:
        return self.total + self.below


def _items(text: str, lang: str | None, skip: frozenset[str]) -> Iterator[tuple[Item, bool]]:
    """The text's words, casefolded, each number among them as its value, with whether it counts as holding one."""
    text = nfc(text)
    vocab = _vocab(lang)
    number: _Number | None = None
    last_end = 0
    for m in TOKEN.finditer(text):
        token = m.group().casefold()
        joined = number is not None and set(text[last_end : m.start()]) <= JOINS
        last_end = m.end()
        if token in skip:
            continue
        parts = None if m.group("digits") else split(token, lang)
        if number is not None and (parts is None or not joined or not number.takes(vocab[parts[0]])):
            yield number.value, not number.alone
            number = None
        if m.group("digits"):
            number = _Number(below=_literal(m.group("digits")), last="digits")
        elif parts is None:
            yield token, False
        else:
            number = number or _Number()
            for part in parts:
                number.add(vocab[part], part)
            number.words += 1
            number.alone = number.words == 1 and token in ALONE
    if number is not None:
        yield number.value, not number.alone


def reading(text: str, lang: str | None, skip: frozenset[str] = frozenset()) -> tuple[Item, ...]:
    """A text's words, casefolded, each number in it as its value: two texts that read alike say the same thing, a
    number written two ways aside. `skip` are words left out: the filler sounds."""
    return tuple(item for item, _ in _items(text, lang, skip))


def values(text: str, lang: str | None) -> tuple[Fraction, ...]:
    """The numbers a text holds, in order."""
    return tuple(item for item in reading(text, lang) if isinstance(item, Fraction))


def holds(text: str, lang: str | None) -> bool:
    """Whether a text holds a digit, or a number word other than `egy` or `one` alone."""
    return re.search(r"\d", text) is not None or any(counts for _, counts in _items(text, lang, frozenset()))
