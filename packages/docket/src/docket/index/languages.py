"""The languages the index stems: each one's Snowball stemmer, the extra word forms it needs, the case forms of a name
(which make aliases match on its stems), and its time words.

English is always one; the others come from `vault.yaml`'s `owner.languages`, which is free text, so a code is matched
in any case and Snowball's own names are taken too. A language Docket can't place gets no column and is a problem: its
words are still found as written.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

# ISO 639-1 codes to the Snowball stemmers snowballstemmer ships (its porter and dutch_porter variants left out).
SNOWBALL: dict[str, str] = {
    "ar": "arabic", "hy": "armenian", "eu": "basque", "ca": "catalan", "cs": "czech", "da": "danish", "nl": "dutch",
    "en": "english", "eo": "esperanto", "et": "estonian", "fi": "finnish", "fr": "french", "de": "german",
    "el": "greek", "hi": "hindi", "hu": "hungarian", "id": "indonesian", "ga": "irish", "it": "italian",
    "lt": "lithuanian", "ne": "nepali", "no": "norwegian", "fa": "persian", "pl": "polish", "pt": "portuguese",
    "ro": "romanian", "ru": "russian", "sr": "serbian", "st": "sesotho", "es": "spanish", "sv": "swedish",
    "ta": "tamil", "tr": "turkish", "yi": "yiddish",
}  # fmt: skip
# Bokmål and Nynorsk share Snowball's Norwegian stemmer.
SAME_AS = {"nb": "no", "nn": "no"}

# What a time word means; `days` takes the number in the phrase.
SPANS = ("today", "yesterday", "this-week", "last-week", "this-month", "last-month", "this-year", "last-year", "days")


@dataclass(frozen=True)
class TimeWord:
    """A phrase in folded words, with `#` standing for a number, and the span it means."""

    words: tuple[str, ...]
    span: str


@dataclass(frozen=True)
class Language:
    code: str
    snowball: str
    # More words to stem for a word (lowercased, as written), because the stemmer cuts a bare word shorter than an
    # inflected one.
    more: Callable[[str], list[str]] | None = None
    # A query word's stem is stemmed again, because the stemmer cuts a bare word further than the same word inflected
    # (`robot` to `rob`, `robotot` to `robot`): the second cut meets the bare word.
    restem: bool = False
    time_words: tuple[TimeWord, ...] = ()
    # The stemmer keeps a word's first letter once folded, so the word check need not stem a word no form starts like.
    # Only the first: English changes the third (`dry` to `dri`) and its exceptions the second (`dying` to `die`). Some
    # Snowball stemmers cut prefixes (Indonesian, Irish), so a language says so only once someone has checked it.
    keeps_start: bool = False
    # A name's case forms (lowercased, as written). Aliases match on this language's stems only where it gives them,
    # since a stem form counts only beside the alias as written or in one of them: an ordinary word that stems like a
    # name (`same` for Sam) is none of its case forms (DR-0350).
    cases: Callable[[str], list[str]] | None = None

    @property
    def column(self) -> str:
        return f"stem_{self.code}"


def _phrases(span: str, *phrases: str) -> list[TimeWord]:
    return [TimeWord(tuple(p.split()), span) for p in phrases]


ENGLISH_TIME = (
    *_phrases("today", "today"),
    *_phrases("yesterday", "yesterday"),
    *_phrases("this-week", "this week"),
    *_phrases("last-week", "last week"),
    *_phrases("this-month", "this month"),
    *_phrases("last-month", "last month"),
    *_phrases("this-year", "this year"),
    *_phrases("last-year", "last year"),
    *_phrases("days", "last # days", "past # days"),
)

HUNGARIAN_TIME = (
    *_phrases("today", "ma"),
    *_phrases("yesterday", "tegnap"),
    *_phrases("this-week", "ezen a heten", "a heten", "e heten"),
    *_phrases("last-week", "mult heten", "mult het", "elozo heten"),
    *_phrases("this-month", "ebben a honapban"),
    *_phrases("last-month", "mult honapban", "elozo honapban"),
    *_phrases("this-year", "iden"),
    *_phrases("last-year", "tavaly"),
    *_phrases("days", "az elmult # napban", "elmult # napban", "elmult # nap"),
)

_LENGTHENED = {"a": "á", "e": "é", "o": "ó", "ö": "ő"}
_BACK = frozenset("aáoóuú")


def hungarian_dative(word: str) -> list[str]:
    """The word with a dative ending, its final short vowel lengthened first (`Dóra` gives `Dórának`): Snowball cuts a
    final `t`, `n`, `ra` or short vowel from a bare word but only the case ending from an inflected one, so the stem of
    the dative is the stem every inflected form shares."""
    if not word[-1:].isalpha():
        return []
    base = word[:-1] + _LENGTHENED[word[-1]] if word[-1] in _LENGTHENED else word
    # Vowel harmony: a word with a back vowel takes -nak (Bálintnak), one with only front vowels -nek (Péternek).
    return [base + ("nak" if any(ch in _BACK for ch in base) else "nek")]


_FRONT = frozenset("eéiíöőüű")
# Per side of vowel harmony, the case endings every word takes, those after a vowel, those after a consonant, and
# those that double a final consonant in place of their v (Markkal, Markká).
_ENDINGS = {
    "back": (
        ("nak", "nál", "hoz", "ban", "ba", "ból", "ra", "ról", "tól", "ig", "ért", "ként", "ul"),
        ("t", "n", "val", "vá"),
        ("t", "ot", "at", "on"),
        ("al", "á"),
    ),
    "front": (
        ("nek", "nél", "hez", "höz", "ben", "be", "ből", "re", "ről", "től", "ig", "ért", "ként", "ül"),
        ("t", "n", "vel", "vé"),
        ("t", "et", "öt", "en", "ön"),
        ("el", "é"),
    ),
}
# A two-letter consonant doubles its first letter (Kovács gives Kováccsal); dzs comes before zs, which it ends with.
_DIGRAPHS = ("dzs", "cs", "dz", "gy", "ly", "ny", "sz", "ty", "zs")
# Old spellings and foreign letters keep their letters, and the doubling writes the sound after them: Babitscsal,
# Ráczcal, Madáchcsal, Kossuthtal, Maxszal.
_OLD_SOUNDS = {"ts": "cs", "cz": "c", "ch": "cs", "th": "t", "x": "sz"}


def hungarian_cases(word: str) -> list[str]:
    """A name's case forms: a final vowel lengthened as for the dative, then each case ending. Back endings for a word
    with a back vowel, front for one with a front vowel or none, and both for one with both, since foreign names go
    either way (Acménak, Acmének). A final y takes the endings after a vowel as well, as foreign names and old spellings
    read it (Tonyval, Szalayval), and those after a consonant, as ny, ly, gy and ty do (Báránnyal)."""
    if not word[-1:].isalpha():
        return []
    back = any(ch in _BACK for ch in word)
    front = not back or any(ch in _FRONT for ch in word)
    out: list[str] = []
    for side in [side for side, takes in (("back", back), ("front", front)) if takes]:
        every, after_vowel, after_consonant, doubling = _ENDINGS[side]
        if word[-1] in _BACK | _FRONT:
            base = word[:-1] + _LENGTHENED.get(word[-1], word[-1])
            out += [base + e for e in (*every, *after_vowel)]
            continue
        if word[-1] == "y":
            out += [word + e for e in (*every, *after_vowel)]
        old = next((spelling for spelling in _OLD_SOUNDS if word.endswith(spelling)), None)
        if old is not None:
            doubled = word + _OLD_SOUNDS[old]
        else:
            last = next((d for d in _DIGRAPHS if word.endswith(d)), word[-1])
            doubled = word if word.endswith(last[0] + last) else word[: -len(last)] + last[0] + last
        out += [word + e for e in (*every, *after_consonant)] + [doubled + e for e in doubling]
    return list(dict.fromkeys(out))


KNOWN: dict[str, Language] = {
    "en": Language("en", "english", time_words=ENGLISH_TIME, keeps_start=True),
    "hu": Language(
        "hu",
        "hungarian",
        more=hungarian_dative,
        restem=True,
        time_words=HUNGARIAN_TIME,
        keeps_start=True,
        cases=hungarian_cases,
    ),
}


def language(code: str) -> Language:
    return KNOWN.get(code) or Language(code, SNOWBALL[code])


def code_of(name: str) -> str | None:
    """A language's code from what a user wrote: a code in any case, `nb` or `nn`, or Snowball's name for it."""
    key = name.strip().lower()
    key = SAME_AS.get(key, key)
    if key in SNOWBALL:
        return key
    return next((code for code, snowball in SNOWBALL.items() if snowball == key), None)


@dataclass(frozen=True)
class Languages:
    """The languages an index stems, English first and the rest in code order, and what couldn't be placed."""

    languages: tuple[Language, ...]
    problems: tuple[str, ...]

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(lang.code for lang in self.languages)

    @property
    def columns(self) -> tuple[str, ...]:
        return ("folded", *(lang.column for lang in self.languages))


def resolve(names: Sequence[str]) -> Languages:
    codes = {"en"}
    problems: list[str] = []
    for name in names:
        code = code_of(name)
        if code is None:
            problems.append(
                f"vault.yaml's language {name!r} has no stemmer (or isn't a language code such as en or hu): "
                "its words are found only as written"
            )
        else:
            codes.add(code)
    ordered = ["en", *sorted(codes - {"en"})]
    return Languages(tuple(language(code) for code in ordered), tuple(problems))
