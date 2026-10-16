"""Words, folding, stems and FTS5 strings: how text becomes the index's columns and a query's terms.

A word is a run of Unicode letters and digits in NFC text, close to how FTS5's `unicode61` tokenizer splits. A chunk
goes into the index as written (the tokenizer folds case and accents) and once per language as its words' stems, each
followed by its hyphenated compounds joined (DR-0280). A query word's forms in a stemmed column are its stem, the word
as written, and what the language's hook adds.
"""

from __future__ import annotations

import importlib
import re
import threading
from collections.abc import Iterable, Sequence
from importlib import metadata
from typing import Any, NamedTuple

from docket.index.languages import Language, Languages
from docket.plaintext import fold, nfc

# The words and, between them, what separates them: re.split keeps the captured words at the odd places.
PIECES = re.compile(r"([^\W_]+)")
# A hyphen, the Unicode hyphen and the non-breaking one join two words; a dash or two hyphens don't.
HYPHENS = frozenset("-\u2010\u2011")
# A run of up to this many words joins every stretch of two or more (`e-mail-cím` gives `email`); a longer run, an id
# or `state-of-the-art`, joins only whole.
STRETCHES_UP_TO = 3


class Joined(NamedTuple):
    """A hyphenated compound joined (`kickoff` for `kick-off`), and the words it joins: `words[start:end]`."""

    word: str
    start: int
    end: int


def has_letter(run: Iterable[str]) -> bool:
    return any(ch.isalpha() for word in run for ch in word)


def _joins(ws: list[str], start: int, end: int) -> list[Joined]:
    if end - start < 2 or not has_letter(ws[start:end]):
        return []
    if end - start > STRETCHES_UP_TO:
        return [Joined("".join(ws[start:end]), start, end)]
    return [Joined("".join(ws[i:j]), i, j) for i in range(start, end) for j in range(i + 2, end + 1)]


def words_and_compounds(text: str) -> tuple[list[str], list[Joined]]:
    """A text's words, as `words()` gives them, and its hyphenated compounds joined: a run of words with one hyphen
    between each two and no space, one of them holding a letter (DR-0280). One pass, since a regex for compounds costs
    five times the words' own."""
    pieces = PIECES.split(nfc(text))
    ws = [w.lower() for w in pieces[1::2]]
    joined: list[Joined] = []
    start = 0
    for k in range(1, len(ws) + 1):
        # pieces[2k] is what lies between word k-1 and word k.
        if k < len(ws) and pieces[2 * k] in HYPHENS:
            continue
        joined += _joins(ws, start, k)
        start = k
    return ws, joined


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
        """A chunk's column values: the text as written, then each language's stems, each followed by the text's
        hyphenated compounds joined. After the words, not between them, so every word keeps its position and phrases
        match as before; the chunk's own text never holds them (DR-0280)."""
        ws, joined = words_and_compounds(text)
        extra = [j.word for j in joined]
        every = ws + extra
        folded = f"{text} {' '.join(extra)}" if extra else text
        return [folded, *(" ".join(stemmer(lang).stem(w) for w in every) for lang in self.languages)]

    def stem(self, word: str, lang: Language) -> str:
        return fold(stemmer(lang).stem(word))

    def forms(self, word: str, lang: Language) -> list[str]:
        """A word's forms in a language's column, folded: its stem, the word as written, then the hook's stems."""
        s = stemmer(lang)
        extra = lang.more(word) if lang.more is not None else []
        return dedupe(fold(x) for x in [s.stem(word), word, *(s.stem(x) for x in extra)])

    def restem(self, word: str, lang: Language) -> tuple[str, str] | None:
        """A word's stem and that stem stemmed again, folded, when the language stems twice and the second cut
        differs. For an inflected word the stem is the bare word, and the second cut is what the bare word holds in
        the stemmed column; search keeps it only when the index holds the bare word as written."""
        if not lang.restem:
            return None
        s = stemmer(lang)
        once = s.stem(word)
        again = fold(s.stem(once))
        return None if again == fold(once) else (fold(once), again)

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


def in_columns(columns: Sequence[str], term: str) -> str:
    """One term in several columns as one phrase: FTS5 reads its rows once, where a phrase per column reads them per
    column, and BM25 counts each phrase's rows once more."""
    return phrase(columns[0], [term]) if len(columns) == 1 else f"{{{' '.join(columns)}}} : {quote(term)}"


def prefix(column: str, term: str) -> str:
    return f"{column} : {quote(term)} *"


def any_of(parts: Iterable[str]) -> str:
    items = list(parts)
    return items[0] if len(items) == 1 else "(" + " OR ".join(items) + ")"
