"""Which of a query's words and phrases a text holds, matched as the index matches them: a word through its forms in
`folded` and in each stemmed column, a phrase through its words in order in one column. Search orders candidates by it:
a title's lead (DR-0231) and the any-word stage (DR-0232).

A text's words are stemmed only when they start, once folded, with a letter some form starts with, and only when every
language's stemmer keeps a word's start (`Language.keeps_start`). That gives exactly what stemming every word gives, at
a fraction of the cost; a test holds the two to the same answer.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from docket.index.text import WORD, Analyzer, fold, nfc


@dataclass(frozen=True)
class Word:
    text: str
    forms: frozenset[tuple[str, str]]


@dataclass(frozen=True)
class Phrase:
    text: str
    # The phrase's terms per column: its words folded, then their stems in each language.
    columns: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True)
class Held:
    words: frozenset[int]
    phrases: frozenset[int]
    # The positions of the text's words that are a query word or sit in a phrase the text holds.
    positions: frozenset[int]


def tokens(text: str) -> list[str]:
    """A text's words as the index cuts them: `text.words`, lowercased with their accents."""
    return [w.lower() for w in WORD.findall(nfc(text))]


def phrase_of(analyzer: Analyzer, text: str) -> Phrase:
    ws = tokens(text)
    columns = [("folded", tuple(fold(w) for w in ws))]
    columns += [(lang.column, tuple(analyzer.stem(w, lang) for w in ws)) for lang in analyzer.languages]
    return Phrase(text, tuple(columns))


class Checker:
    """The query's words and phrases, and what a text holds of them. Built once per search; it remembers each word's
    column terms, so a word seen in one candidate costs nothing in the next."""

    def __init__(self, analyzer: Analyzer, words: Sequence[Word], phrases: Sequence[Phrase]) -> None:
        self.analyzer = analyzer
        self.words = list(words)
        self.phrases = list(phrases)
        self.wanted: dict[tuple[str, str], list[int]] = {}
        for i, word in enumerate(self.words):
            for form in word.forms:
                self.wanted.setdefault(form, []).append(i)
        firsts = {term[:1] for word in self.words for _, term in word.forms}
        firsts |= {term[:1] for phrase in self.phrases for _, terms in phrase.columns for term in terms}
        keeps = all(lang.keeps_start for lang in analyzer.languages)
        self.firsts: frozenset[str] | None = frozenset(firsts) if keeps else None
        self._terms: dict[str, frozenset[tuple[str, str]]] = {}
        self._first: dict[str, str] = {}

    def terms(self, word: str) -> frozenset[tuple[str, str]]:
        """A text word's terms, one per column: folded as written, then its stem in each language."""
        found = self._terms.get(word)
        if found is None:
            stems = [(lang.column, self.analyzer.stem(word, lang)) for lang in self.analyzer.languages]
            found = frozenset([("folded", fold(word)), *stems])
            self._terms[word] = found
        return found

    def _may_match(self, word: str) -> bool:
        if self.firsts is None:
            return True
        head = word[:1]
        first = self._first.get(head)
        if first is None:
            first = fold(head)[:1]
            self._first[head] = first
        return first in self.firsts

    def _has(self, word: str, term: tuple[str, str]) -> bool:
        return self._may_match(word) and term in self.terms(word)

    def _words_in(self, distinct: Iterable[str]) -> set[int]:
        found: set[int] = set()
        for word in distinct:
            if self._may_match(word):
                for term in self.terms(word):
                    found.update(self.wanted.get(term, ()))
        return found

    def _phrase_at(self, ws: list[str], phrase: Phrase) -> range | None:
        for col, terms in phrase.columns:
            n = len(terms)
            for start in range(len(ws) - n + 1):
                if all(self._has(ws[start + k], (col, terms[k])) for k in range(n)):
                    return range(start, start + n)
        return None

    def count(self, text: str) -> int:
        """How many of the query's words and phrases a text holds."""
        ws = tokens(text)
        found = len(self._words_in(set(ws)))
        return found + sum(1 for phrase in self.phrases if self._phrase_at(ws, phrase) is not None)

    def held(self, text: str) -> Held:
        """Which words and phrases a short text, such as a title's lead, holds, and at which of its words."""
        ws = tokens(text)
        words: set[int] = set()
        positions: set[int] = set()
        for at, word in enumerate(ws):
            got = self._words_in([word])
            if got:
                words |= got
                positions.add(at)
        phrases: set[int] = set()
        for i, phrase in enumerate(self.phrases):
            span = self._phrase_at(ws, phrase)
            if span is not None:
                phrases.add(i)
                positions.update(span)
        return Held(frozenset(words), frozenset(phrases), frozenset(positions))
