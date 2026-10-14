"""Words, folding, stems and FTS5 strings: how text becomes the index's columns and a query's terms.

A word is a run of Unicode letters and digits in NFC text, close to how FTS5's `unicode61` tokenizer splits. A chunk
goes into the index as written (the tokenizer folds case and accents) and once per language as its words' stems. A
query word's forms in a stemmed column are its stem, the word as written, and what the language's hook adds.
"""

from __future__ import annotations

import importlib
import re
import threading
import unicodedata
from collections.abc import Iterable
from importlib import metadata
from typing import Any

from docket.index.languages import Language, Languages

WORD = re.compile(r"[^\W_]+")


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def words(text: str) -> list[str]:
    """The words of a text, lowercased, accents kept: the stemmers need them."""
    return [w.lower() for w in WORD.findall(nfc(text))]


def fold(word: str) -> str:
    """A word as FTS5 stores it: lowercased, diacritics removed."""
    decomposed = unicodedata.normalize("NFD", word)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def snowball_version() -> str:
    return metadata.version("snowballstemmer")


class Stemmer:
    """One language's Snowball stemmer, cached per word. The pure-Python class is imported directly: the package's
    `stemmer()` switches to PyStemmer when that is installed, which can stem differently."""

    def __init__(self, lang: Language) -> None:
        module = importlib.import_module(f"snowballstemmer.{lang.snowball}_stemmer")
        cls: Any = getattr(module, f"{lang.snowball.capitalize()}Stemmer")
        self._impl: Any = cls()
        self._cache: dict[str, str] = {}
        self._lock = threading.Lock()

    def stem(self, word: str) -> str:
        hit = self._cache.get(word)
        if hit is None:
            # The stemmer keeps its word in itself while it works, so two threads must not share it at once.
            with self._lock:
                hit = str(self._impl.stemWord(word))
            self._cache[word] = hit
        return hit


_STEMMERS: dict[str, Stemmer] = {}


def stemmer(lang: Language) -> Stemmer:
    if lang.code not in _STEMMERS:
        _STEMMERS[lang.code] = Stemmer(lang)
    return _STEMMERS[lang.code]


class Analyzer:
    """The index's columns for a set of languages, and the forms a word takes in each."""

    def __init__(self, langs: Languages) -> None:
        self.langs = langs
        self.languages = langs.languages
        self.columns = langs.columns

    def index_values(self, text: str) -> list[str]:
        """A chunk's column values: the text as written, then each language's stems."""
        ws = words(text)
        return [text, *(" ".join(stemmer(lang).stem(w) for w in ws) for lang in self.languages)]

    def stem(self, word: str, lang: Language) -> str:
        return fold(stemmer(lang).stem(word))

    def forms(self, word: str, lang: Language) -> list[str]:
        """A word's forms in a language's column, folded: its stem, the word as written, then the hook's stems."""
        s = stemmer(lang)
        extra = lang.more(word) if lang.more is not None else []
        return dedupe(fold(x) for x in [s.stem(word), word, *(s.stem(x) for x in extra)])

    def seeds(self, word: str, lang: Language) -> list[str]:
        """What a misspelling is looked for from in a stemmed vocabulary: the hook's stems and the word as written,
        not Snowball's cut stem, which is shorter than the word."""
        s = stemmer(lang)
        extra = lang.more(word) if lang.more is not None else []
        return dedupe(fold(x) for x in [*(s.stem(x) for x in extra), word])


def quote(term: str) -> str:
    """A string FTS5 reads as plain words, whatever it holds."""
    return '"' + term.replace('"', '""') + '"'


def phrase(column: str, terms: Iterable[str]) -> str:
    return f"{column} : {quote(' '.join(terms))}"


def prefix(column: str, term: str) -> str:
    return f"{column} : {quote(term)} *"


def any_of(parts: Iterable[str]) -> str:
    items = list(parts)
    return items[0] if len(items) == 1 else "(" + " OR ".join(items) + ")"
