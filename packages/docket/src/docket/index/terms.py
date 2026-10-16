"""Which of a query's words and phrases a text holds, matched as the index matches them: a word through its forms in
`folded` and in each stemmed column, a phrase through its words in order in one column. Search orders candidates by it:
a title's lead (DR-0231) and the any-word stage (DR-0232).

A text is read as the index reads it: its words, then its hyphenated compounds joined (DR-0280). A query word's forms
include its joined words' forms, and it is also held through its splits, two words in a row (DR-0281); the check says
which words a text holds only through a split, which rank after the word itself (DR-0282).

A text's words are stemmed only when they start, once folded, with a letter some form starts with, and only when every
language's stemmer keeps a word's start (`Language.keeps_start`). That gives exactly what stemming every word gives, at
a fraction of the cost; a test holds the two to the same answer. A split is scanned for only in a text holding a term of
each of its halves, from the terms that screen already computed.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from docket.index.text import Analyzer, words_and_compounds
from docket.plaintext import WORD, fold, nfc


@dataclass(frozen=True)
class Phrase:
    text: str
    # The phrase's terms per column: its words folded, then their stems in each language.
    columns: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True)
class Word:
    text: str
    # Its forms, and its joined words' forms (DR-0281).
    forms: frozenset[tuple[str, str]]
    # The two words in a row it also matches as, each a phrase (DR-0281).
    splits: tuple[Phrase, ...] = ()


@dataclass(frozen=True)
class Held:
    # The words held through their forms or joined words.
    words: frozenset[int]
    phrases: frozenset[int]
    # The positions of the text's words that are a query word, join into one, or sit in a phrase the text holds.
    positions: frozenset[int]
    # The words held only through a split, which a title's lead doesn't count (DR-0282).
    split_only: frozenset[int] = frozenset()


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
        self.splits = [(i, split) for i, word in enumerate(self.words) for split in word.splits]
        firsts = {term[:1] for word in self.words for _, term in word.forms}
        firsts |= {term[:1] for phrase in self.phrases for _, terms in phrase.columns for term in terms}
        firsts |= {term[:1] for _, split in self.splits for _, terms in split.columns for term in terms}
        keeps = all(lang.keeps_start for lang in analyzer.languages)
        self.firsts: frozenset[str] | None = frozenset(firsts) if keeps else None
        self._terms: dict[str, frozenset[tuple[str, str]]] = {}
        self._first: dict[str, str] = {}
        # How many times a text was scanned for a split: the perf test holds it to the texts the gate lets through.
        self.scans = 0

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

    def _present(self, distinct: Iterable[str]) -> set[tuple[str, str]]:
        """The column terms of a text's words that pass the first-letter screen: what a query term can be."""
        present: set[tuple[str, str]] = set()
        for word in distinct:
            if self._may_match(word):
                present |= self.terms(word)
        return present

    def _words_in(self, present: Iterable[tuple[str, str]]) -> set[int]:
        found: set[int] = set()
        for term in present:
            found.update(self.wanted.get(term, ()))
        return found

    def _phrase_at(self, ws: list[str], phrase: Phrase) -> range | None:
        for col, terms in phrase.columns:
            n = len(terms)
            for start in range(len(ws) - n + 1):
                if all(self._has(ws[start + k], (col, terms[k])) for k in range(n)):
                    return range(start, start + n)
        return None

    def _split_held(self, ws: list[str], present: set[tuple[str, str]], skip: set[int]) -> set[int]:
        """The words a text holds through a split, of those not in skip. A split is scanned for only when the text
        holds a term of each half in one column; any other text costs a set test (DR-0282)."""
        found: set[int] = set()
        for i, split in self.splits:
            if i in skip or i in found:
                continue
            if not any(all((col, term) in present for term in terms) for col, terms in split.columns):
                continue
            self.scans += 1
            if self._phrase_at(ws, split) is not None:
                found.add(i)
        return found

    def examine(self, text: str) -> tuple[int, bool]:
        """How many of the query's words and phrases a text holds, through forms, joined words or splits, and whether
        it holds one of the words only through a split."""
        ws, joined = words_and_compounds(text)
        present = self._present({*ws, *(j.word for j in joined)})
        held = self._words_in(present)
        by_split = self._split_held(ws, present, held) if self.splits else set[int]()
        phrases = sum(1 for phrase in self.phrases if self._phrase_at(ws, phrase) is not None)
        return len(held) + len(by_split) + phrases, bool(by_split)

    def count(self, text: str) -> int:
        """How many of the query's words and phrases a text holds."""
        return self.examine(text)[0]

    def held(self, text: str) -> Held:
        """Which words and phrases a short text, such as a title's lead, holds, and at which of its words; a word held
        only through a split is set apart."""
        ws, joined = words_and_compounds(text)
        words: set[int] = set()
        positions: set[int] = set()
        for at, word in enumerate(ws):
            got = self._words_in(self._present([word]))
            if got:
                words |= got
                positions.add(at)
        for compound in joined:
            got = self._words_in(self._present([compound.word]))
            if got:
                words |= got
                positions.update(range(compound.start, compound.end))
        phrases: set[int] = set()
        for i, phrase in enumerate(self.phrases):
            span = self._phrase_at(ws, phrase)
            if span is not None:
                phrases.add(i)
                positions.update(span)
        split_only: set[int] = set()
        if self.splits:
            split_only = self._split_held(ws, self._present({*ws, *(j.word for j in joined)}), words)
        return Held(frozenset(words), frozenset(phrases), frozenset(positions), frozenset(split_only))
