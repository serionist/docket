"""Search: one query over the index, answered in well under a second.

1. Read the query: quoted phrases, words, time words (a range), and names or aliases found in it (its entities).
2. Each word becomes an OR of its forms across the columns, its joined words' forms (two query words in a row, the
   stretches of a hyphenated one) and its splits, two words in a row the index holds, leaving out a joined word in half
   the chunks or more and a split of two such words (DR-0281); each entity an OR of its aliases' forms.
3. FTS candidates, top 200 by BM25, filters applied inside the query: all words required, however common (DR-0230); if
   fewer than k, any of them; if still fewer, word prefixes. In each, the best chunk of each group whose title's lead
   holds most of the query's terms goes first (DR-0231); in the any-word stage the rest go by how many terms their text
   holds (DR-0232), then by BM25; a chunk holding a word only through a split goes after the rest (DR-0282). The
   any-word and prefix stages leave out words in half the chunks or more, which BM25 gives no weight; they still count
   in the order, and the least common joins one more any-word query when the stages leave fewer than k.
4. Entity candidates, top 200: chunks mentioning the query's or the boosts' entities.
5. With models on, vector candidates: the query's vector's top 200 (DR-0338).
6. Fuse the lists by reciprocal rank, the vectors' at half weight, so a boost lifts a chunk and never drops one; a chunk
   only the vectors found goes after every all-words hit.
7. With a reranker on, rerank the top N within a budget: the chunks behind the k hits step 6 gives, then the fusion at
   vector weight 1 from its top, in two tiers, all-words hits first (DR-0452, DR-0453, DR-0454); skipped, the order
   stays step 6's and the result says why.
8. Collapse hits on the same stretch, group them by recording or thread, attach each group's summary.

The parameters follow the design's MCP `search` tool, which the service (#8) maps one to one (DR-0117).
"""

from __future__ import annotations

import functools
import json
import re
import sqlite3
import threading
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from docket.errors import DocketError
from docket.index import matching, rerank, vectors
from docket.index.build import db_path, languages_of, open_index
from docket.index.languages import Language, Languages
from docket.index.terms import Checker, Word, phrase_of, tokens
from docket.index.text import (
    HYPHENS,
    WORD,
    Analyzer,
    Joined,
    any_of,
    dedupe,
    fold,
    has_letter,
    in_columns,
    phrase,
    prefix,
    words,
    words_and_compounds,
)
from docket.index.timewords import Range, read_time
from docket.models.catalogue import input_hash, rerank_passage
from docket.models.provider import RERANK_SECONDS, Off
from docket.vault.vault import Vault

CANDIDATES = 200
ENTITY_ROWS = 2000
RRF = 60
# The vector list counts half in the fusion: on the invented set that kept the paraphrase and typo lift and every kind's
# MRR within 0.01 of keyword search's, where a full weight cost names and inflections (DR-0338).
VECTOR_WEIGHT = 0.5
# The reranker's candidates come from the fusion at full weight, which fed it better (DR-0453).
RERANK_WEIGHT = 1.0
# The query's vector waits at most 2 s in the worker's client; the scan itself is bounded by this.
VECTOR_SECONDS = 10.0
MAX_K = 100
STEMMED_WEIGHT = 0.6
PREFIX_MIN = 3
SNIPPET_BEFORE = 15
SNIPPET_WORDS = 40
TITLE_WORDS = 8
# A title's lead: its text up to the first `: ` after at least three words, else its first 12 words (DR-0231).
LEAD_END = ": "
LEAD_MIN = 3
LEAD_WORDS = 12
SUMMARY_TYPES = ("minutes", "recording-note", "thread-summary")
# A split's halves (DR-0281): a first half of two letters is a contraction's or a function word's (`we're`, `in to`),
# while `up`, `in` and `on` end compounds (`follow up`, `log in`).
SPLIT_FIRST = 3
SPLIT_SECOND = 2
QUOTED = re.compile(r'"([^"]*)"')
# The open ends of a range given only one side: far enough, and still printable in any time zone.
FAR_PAST = datetime(1900, 1, 1, tzinfo=UTC)
FAR_FUTURE = datetime(9000, 1, 1, tzinfo=UTC)
# Query words, folded, to the entities they name; a case form, folded, to each entity, column and stem form the stemmer
# cuts it to, one entry per stem form.
Names = dict[tuple[str, ...], set[str]]
Reached = dict[tuple[str, ...], list[tuple[str, str, tuple[str, ...]]]]


class EntityFound(BaseModel):
    id: str
    words: str


class QueryReading(BaseModel):
    """The query as search read it."""

    text: str
    words: list[str] = []
    phrases: list[str] = []
    entities: list[EntityFound] = []
    boosts: list[str] = []
    unknown: list[str] = []
    # Always empty: no word leaves a query any more (DR-0230), and DR-0150 keeps every field a result has.
    left_out: list[str] = []
    # The words no chunk holds in any form, so no hit can hold every word.
    absent: list[str] = []
    since: datetime | None = None
    until: datetime | None = None
    time_words: list[str] = []


class DocRef(BaseModel):
    id: str
    type: str
    rev: int
    title: str


class Hit(BaseModel):
    """A chunk found: where it is (its locator), what it says around the match, and why it was found."""

    locator: str
    kind: str
    rid: str | None = None
    doc: str | None = None
    ts: datetime | None = None
    start: int | None = None
    end: int | None = None
    lines: tuple[int, int] | None = None
    speakers: list[str] = []
    authors: list[str] = []
    title: str | None = None
    snippet: str = ""
    rank: int = 0
    found_by: str = ""
    entities: list[str] = []
    anchors: list[str] = []
    also: list[str] = []


class Group(BaseModel):
    """The hits in one recording, thread, record or doc, with its summary when a current doc covers it."""

    key: str
    kind: str
    title: str
    source: str | None = None
    first: datetime | None = None
    last: datetime | None = None
    summary: DocRef | None = None
    hits: list[Hit] = []


class VectorsRun(BaseModel):
    """Whether the vector stage ran: off (no models here), on, or skipped with why."""

    state: Literal["off", "on", "skipped"] = "off"
    model: str | None = None
    why: str | None = None


class RerankRun(BaseModel):
    """Whether the reranker ran (DR-0459): off (none here, or off on this machine, with why), on with the pairs it
    reranked, or skipped with why."""

    state: Literal["off", "on", "skipped"] = "off"
    model: str | None = None
    pairs: int = 0
    why: str | None = None


class SearchResult(BaseModel):
    query: QueryReading
    groups: list[Group] = []
    facets: dict[str, dict[str, int]] = {}
    candidates: int = 0
    models: VectorsRun = VectorsRun()
    rerank: RerankRun = RerankRun()


@dataclass
class _Row:
    id: int
    kind: str
    grp: str
    source: str | None
    ts: str | None
    start_ms: int | None
    end_ms: int | None
    line_from: int | None
    line_to: int | None
    rid: str | None
    doc: str | None
    rids: list[str]
    speakers: list[str]
    authors: list[str]
    title: str | None
    locator: str
    anchors: list[str]
    text: str
    also: list[str] = field(default_factory=list[str])


def parse_when(text: str | None, what: str) -> date | datetime | None:
    """`since` or `until` as given on the command line or to a tool: a date (the owner's whole day), or a time, with its
    offset or else in the owner's time zone. Anything else is refused, naming what it was given for."""
    if text is None:
        return None
    try:
        return date.fromisoformat(text) if len(text) == len("2026-10-14") else datetime.fromisoformat(text)
    except ValueError:
        raise DocketError(f"{what} is a date or a time, such as 2026-10-14 or 2026-10-14T09:30") from None


def _time(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value.replace("Z", "+00:00"))


def _instant(value: datetime | date, tz: ZoneInfo, end: bool) -> datetime:
    """A datetime as it is; a date as the start of that day in the owner's zone, or of the next day for an end."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=tz)
    day = date.fromordinal(value.toordinal() + 1) if end else value
    return datetime(day.year, day.month, day.day, tzinfo=tz)


def _span(row: _Row) -> tuple[float, float]:
    """A transcript chunk's stretch in absolute milliseconds: its time is the record's plus the window's start."""
    at = _time(row.ts)
    start = at.timestamp() * 1000 if at is not None else float(row.start_ms or 0)
    return start, start + (row.end_ms or 0) - (row.start_ms or 0)


def _overlaps(a: _Row, b: _Row) -> bool:
    """Two hits on the same stretch: two transcripts whose times overlap by more than half the shorter (a record's
    own windows share an utterance, and stay apart), or a message inside a window. Times are absolute, so two
    recordings in one thread don't hide each other."""
    if a.kind == b.kind == "transcript" and a.rid != b.rid:
        (a0, a1), (b0, b1) = _span(a), _span(b)
        overlap = min(a1, b1) - max(a0, b0)
        shorter = min(a1 - a0, b1 - b0)
        return overlap > shorter / 2 if shorter else overlap >= 0
    if {a.kind, b.kind} == {"thread", "message"}:
        window, message = (a, b) if a.kind == "thread" else (b, a)
        return message.rid in window.rids
    return False


@functools.lru_cache(maxsize=8192)
def _cases(text: str, lang: Language) -> matching.Cases:
    """An alias's case forms, folded: every search reads them, and the service keeps them between searches."""
    return matching.cases_of(Analyzer(Languages((lang,), ())), words(text), lang)


class _Search:
    def __init__(self, conn: sqlite3.Connection, vault: Vault, langs: Languages) -> None:
        self.conn = conn
        self.db = db_path(vault)
        self.langs = langs
        self.analyzer = Analyzer(langs)
        self.tz = ZoneInfo(vault.config.owner.time_zone)
        self.terms: set[str] = set()
        self.alias_langs = {lang.column: lang for lang in matching.alias_langs(self.analyzer)}
        self.lookup, self.alone, self.reached, self.longest = self._lookups()
        # Per query word, the most chunks any of its forms is in.
        self.reach: dict[str, int] = {}
        # How many chunks hold a split's half, and the admitted splits' column terms, where a snippet may start.
        self.halves: dict[str, int] = {}
        self.split_terms: set[tuple[str, str, str]] = set()

    @functools.cached_property
    def total(self) -> int:
        """How many chunks there are, read once, and only for a query that has an any-word stage."""
        return int(self.conn.execute("SELECT count(*) FROM chunks").fetchone()[0])

    def common(self, chunks: int) -> bool:
        """Whether a term in so many chunks is one BM25 doesn't weigh: FTS5 gives one in half the chunks or more no
        weight at all."""
        return chunks * 2 >= self.total

    def weighs(self, word: str) -> bool:
        """Whether BM25 still weighs a query word, by the most chunks any of its terms is in."""
        return not self.common(self.reach[word])

    # -- reading the query --------------------------------------------------------------------------------------

    def _lookups(self) -> tuple[Names, Names, Reached, int]:
        """Each alias as written, folded, to its entities, and its case forms but the common ones in a language where
        one of its stem forms is kept: those the stemmer leaves whole to their entities, the others to their entities,
        columns and the stem forms each is cut to, one of which a query word must stem to as well (DR-0350). Aliases
        with `needs` left out, they count only in chunks."""
        lookup: Names = {}
        kept: dict[tuple[str, int, str, str], None] = {}
        common: set[tuple[str, int, tuple[str, ...]]] = set()
        for entity, ordinal, col, term, how, text in self.conn.execute(
            "SELECT t.entity, t.ordinal, t.col, t.term, t.how, a.text FROM alias_terms t JOIN aliases a "
            "ON a.entity = t.entity AND a.ordinal = t.ordinal WHERE t.word = -1 AND a.needs = '[]' "
            "AND (t.how IN ('written', 'stem') OR (t.col = 'folded' AND t.how = 'common'))"
        ):
            if how == "written":
                lookup.setdefault(tuple(term.split()), set()).add(entity)
            elif how == "common":
                common.add((entity, ordinal, tuple(term.split())))
            else:
                kept[(entity, ordinal, col, text)] = None
        alone: Names = {}
        reached: Reached = {}
        for entity, ordinal, col, text in kept:
            cases = _cases(text, self.alias_langs[col])
            for form in cases.alone:
                if (entity, ordinal, form) not in common:
                    alone.setdefault(form, set()).add(entity)
            for form, stem in cases.reached:
                if (entity, ordinal, form) not in common:
                    reached.setdefault(form, []).append((entity, col, stem))
        return lookup, alone, reached, max((len(form) for form in lookup), default=0)

    def _names(self, run: list[str]) -> set[str]:
        """The entities a run of query words names: an alias as written, or in one of its case forms as a chunk must
        hold it, stemming to a stem form the case form is cut to. `same` stems like Sam and is none of his case forms;
        `benne` folds like Ben's `benné` and stems to `benn`, where `benné` is cut to `ben`."""
        folded = tuple(fold(t) for t in run)
        hits = self.lookup.get(folded, set()) | self.alone.get(folded, set())
        for entity, col, stem in self.reached.get(folded, []):
            if tuple(self.analyzer.stem(t, self.alias_langs[col]) for t in run) == stem:
                hits.add(entity)
        return hits

    def entity_spans(self, tokens: list[str]) -> list[tuple[int, int, set[str]]]:
        """The runs of words that are a name or an alias, longest first, with the entities each names."""
        spans: list[tuple[int, int, set[str]]] = []
        i = 0
        while i < len(tokens):
            for size in range(min(self.longest, len(tokens) - i), 0, -1):
                hits = self._names(tokens[i : i + size])
                if hits:
                    spans.append((i, i + size, hits))
                    i += size
                    break
            else:
                i += 1
        return spans

    def find_entities(self, tokens: list[str]) -> tuple[list[EntityFound], list[int]]:
        """Names and aliases in the query, longest first; the places of the words left over."""
        found: list[EntityFound] = []
        rest: list[int] = []
        at = 0
        for start, end, hits in self.entity_spans(tokens):
            rest += range(at, start)
            found += [EntityFound(id=e, words=" ".join(tokens[start:end])) for e in sorted(hits)]
            at = end
        return found, [*rest, *range(at, len(tokens))]

    def resolve(self, names: Sequence[str], person: bool) -> tuple[list[str], list[str]]:
        """Boosts by id, name or alias; what resolves to nothing comes back as unknown."""
        types = dict(self.conn.execute("SELECT id, type FROM entities").fetchall())
        ids: list[str] = []
        unknown: list[str] = []
        for name in names:
            written = tuple(fold(w) for w in words(name))
            matches = {name} if name in types else self.lookup.get(written, set())
            matches = {m for m in matches if not person or types.get(m) == "person"}
            if matches:
                ids += sorted(matches)
            else:
                unknown.append(name)
        return ids, unknown

    # -- expressions --------------------------------------------------------------------------------------------

    def _docs(self, col: str, term: str) -> int:
        row = self.conn.execute("SELECT doc FROM vocab WHERE col = ? AND term = ?", (col, term)).fetchone()
        return 0 if row is None else row[0]

    def word_forms(self, word: str) -> list[tuple[str, str]]:
        forms = [("folded", fold(word))]
        for lang in self.langs.languages:
            forms += [(lang.column, f) for f in self.analyzer.forms(word, lang)]
            twice = self.analyzer.restem(word, lang)
            # Only when the stem is a word the index holds as written: a bare word's stem is no word, and its second
            # cut (`forint` to `forin` to `for`) would meet every English `for`. And not when the second cut is itself
            # a word written at least as often (`form` to `for`, `márka` to `mar`): it would bring every `for`.
            if twice is not None and len(twice[1]) >= matching.SHORT:
                written = self._docs("folded", twice[0])
                if written and self._docs("folded", twice[1]) < written:
                    forms.append((lang.column, twice[1]))
        return forms

    def _half(self, half: str) -> int:
        """How many chunks hold a split's half, as written or through a stem: the most in any column."""
        known = self.halves.get(half)
        if known is None:
            counts = [self._docs("folded", fold(half))]
            counts += [self._docs(lang.column, self.analyzer.stem(half, lang)) for lang in self.langs.languages]
            known = self.halves[half] = max(counts)
        return known

    def splits(self, word: str) -> list[tuple[str, str, int]]:
        """The two words in a row a query word also matches as (DR-0281), with the fewer chunks its halves are in: the
        first half of three letters or more and the second of two, each holding a letter, both words the index holds,
        and not both in half the chunks or more, where the phrase would cost a pass over most chunks."""
        out: list[tuple[str, str, int]] = []
        for at in range(SPLIT_FIRST, len(word) - SPLIT_SECOND + 1):
            first, second = word[:at], word[at:]
            if not (has_letter(first) and has_letter(second)):
                continue
            a = self._half(first)
            b = self._half(second) if a else 0
            if a and b and not (self.common(a) and self.common(b)):
                out.append((first, second, min(a, b)))
        return out

    def said(self, first: str, second: str) -> bool:
        """Whether some chunk says a split's halves in a row: both halves being words doesn't make the phrase one."""
        row = self.conn.execute(
            "SELECT 1 FROM chunks_fts WHERE chunks_fts MATCH ? LIMIT 1", (self._phrase([first, second]),)
        ).fetchone()
        return row is not None

    def word_groups(
        self, tokens: list[str], joins: dict[str, list[str]]
    ) -> tuple[list[tuple[str, str]], list[Word], list[str]]:
        """(word, expression) per word, with every form however common (DR-0230), its joined words' forms and its
        splits (DR-0281); each word with its forms and splits, for the word check, which also reads titles no chunk
        holds; and the words no chunk holds in any form or split.

        The expression leaves out a form its column doesn't hold, which matches nothing, and puts a term several
        columns hold in one phrase: a common word's every phrase costs BM25 a pass over its rows. A joined word in half
        the chunks or more is left out, as a split of two such words is: BM25 gives it no weight, and matching it would
        score every chunk it is in. The word itself stays."""
        groups: list[tuple[str, str]] = []
        kept: list[Word] = []
        absent: list[str] = []
        for word in dedupe(tokens):
            forms = self.word_forms(word)
            for joined in dedupe(joins.get(word, [])):
                theirs = self.word_forms(joined)
                if not self.common(max(self._docs(col, term) for col, term in theirs)):
                    forms += theirs
            forms = list(dict.fromkeys(forms))
            self.terms |= {term for _, term in forms}
            counts = [self._docs(col, term) for col, term in forms]
            held = [form for form, n in zip(forms, counts, strict=True) if n]
            found = self.splits(word)
            splits = tuple(phrase_of(self.analyzer, f"{first} {second}") for first, second, _ in found)
            self.split_terms |= {(col, terms[0], terms[1]) for split in splits for col, terms in split.columns}
            kept.append(Word(word, frozenset(forms), splits))
            self.reach[word] = max([*counts, *(n for *_, n in found)])
            # Checked only for a word no form of which the index holds: one lookup per split, at most a few.
            if not held and not any(self.said(first, second) for first, second, _ in found):
                absent.append(word)
            columns: dict[str, list[str]] = {}
            for col, term in held or ([] if found else forms[:1]):
                columns.setdefault(term, []).append(col)
            parts = [in_columns(cols, term) for term, cols in columns.items()]
            parts += [self._phrase([first, second]) for first, second, _ in found]
            groups.append((word, any_of(parts)))
        return groups, kept, absent

    def _phrase(self, ws: list[str]) -> str:
        parts = [phrase("folded", [fold(w) for w in ws])]
        parts += [phrase(lang.column, [self.analyzer.stem(w, lang) for w in ws]) for lang in self.langs.languages]
        return any_of(parts)

    def phrase_group(self, text: str) -> str:
        ws = words(text)
        self.terms |= {fold(w) for w in ws}
        return self._phrase(ws)

    def entity_group(self, entity: str) -> str | None:
        exprs = list(matching.expressions(self.conn, self.analyzer, [entity], needs=False).values())
        for (term,) in self.conn.execute(
            "SELECT term FROM alias_terms WHERE entity = ? AND how IN ('written', 'stem', 'asr')", (entity,)
        ):
            self.terms |= set(term.split())
        return any_of(exprs) if exprs else None

    # -- candidates ---------------------------------------------------------------------------------------------

    def filters(self, span: Range | None, sources: Sequence[str]) -> tuple[str, list[object]]:
        sql = ""
        args: list[object] = []
        if span is not None:
            sql += " AND c.ts >= ? AND c.ts < ?"
            args += [_iso(span.since), _iso(span.until)]
        if sources:
            sql += f" AND c.source IN ({', '.join('?' * len(sources))})"
            args += list(sources)
        return sql, args

    def fts(self, expr: str, filters: tuple[str, list[object]], limit: int) -> list[int]:
        weights = ", ".join(["1.0", *[str(STEMMED_WEIGHT)] * len(self.langs.languages)])
        sql = (
            f"SELECT c.id FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid WHERE chunks_fts MATCH ?"  # noqa: S608 - the filters are fixed SQL with placeholders
            f"{filters[0]} ORDER BY bm25(chunks_fts, {weights}), c.id LIMIT ?"
        )
        return [r[0] for r in self.conn.execute(sql, [expr, *filters[1], limit])]

    def entity_list(self, entities: list[str], filters: tuple[str, list[object]], rank: dict[int, int]) -> list[int]:
        marks = ", ".join("?" * len(entities))
        rows = self.conn.execute(
            "SELECT m.chunk, count(*), min(CASE WHEN m.how IN ('asr', 'needs') THEN 1 ELSE 0 END) "  # noqa: S608 - placeholders only
            f"FROM mentions m JOIN chunks c ON c.id = m.chunk WHERE m.entity IN ({marks}){filters[0]} "
            "GROUP BY m.chunk ORDER BY count(*) DESC, 3, c.ts DESC, m.chunk LIMIT ?",
            [*entities, *filters[1], ENTITY_ROWS],
        ).fetchall()
        ordered = sorted(rows, key=lambda r: (-r[1], r[2], rank.get(r[0], CANDIDATES + 1)))
        return [r[0] for r in ordered[:CANDIDATES]]

    def newest(self, filters: tuple[str, list[object]]) -> list[int]:
        sql = f"SELECT c.id FROM chunks c WHERE 1 = 1{filters[0]} ORDER BY c.ts DESC, c.id LIMIT ?"  # noqa: S608 - placeholders only
        return [r[0] for r in self.conn.execute(sql, [*filters[1], CANDIDATES])]

    # -- the order inside a stage -------------------------------------------------------------------------------

    def titles(self, ids: list[int]) -> dict[int, tuple[str, str | None]]:
        """Each chunk's group and title: its doc's title, else its own (a record's), else the title of the record its
        group is named after (a transcript's recording, a thread window's parent)."""
        rows = self.conn.execute(
            "SELECT c.id, c.grp, CASE WHEN c.doc IS NULL THEN coalesce(c.title, g.title) ELSE d.title END "  # noqa: S608 - placeholders only
            "FROM chunks c LEFT JOIN docs d ON d.id = c.doc LEFT JOIN records g ON g.rid = c.grp "
            f"WHERE c.id IN ({', '.join('?' * len(ids))})",
            ids,
        )
        return {r[0]: (r[1], r[2]) for r in rows}

    def lead_held(self, text: str, q: _Terms) -> tuple[int, float]:
        """How many of the query's terms a title's lead holds, and the share of its words that are query terms."""
        known = q.leads.get(text)
        if known is not None:
            return known
        ws = tokens(text)
        held = q.checker.held(text)
        positions = set(held.positions)
        named: set[str] = set()
        for start, end, ids in self.entity_spans(ws):
            for said, wanted in q.entities.items():
                if ids & wanted:
                    named.add(said)
                    positions.update(range(start, end))
        known = (len(held.words) + len(held.phrases) + len(named), len(positions) / max(len(ws), 1))
        q.leads[text] = known
        return known

    def text_terms(self, ids: list[int], q: _Terms) -> dict[int, tuple[int, bool]]:
        """How many of the query's terms each chunk's text holds, words and phrases by the word check and names by the
        chunk's mentions; and whether it holds a word only through a split."""
        if not ids:
            return {}
        marks = ", ".join("?" * len(ids))
        texts = dict(self.conn.execute(f"SELECT id, text FROM chunks WHERE id IN ({marks})", ids).fetchall())  # noqa: S608 - placeholders only
        named: dict[int, set[str]] = {}
        for chunk, entity in self.conn.execute(f"SELECT chunk, entity FROM mentions WHERE chunk IN ({marks})", ids):  # noqa: S608 - placeholders only
            named.setdefault(chunk, set()).add(entity)
        out: dict[int, tuple[int, bool]] = {}
        for chunk in ids:
            held, by_split = q.checker.examine(texts[chunk])
            out[chunk] = (held + sum(1 for wanted in q.entities.values() if wanted & named.get(chunk, set())), by_split)
        return out

    def order(self, ids: list[int], q: _Terms, *, by_text: bool) -> list[int]:
        """A stage's candidates, BM25 order given: first the best chunk of each group whose title's lead holds more
        than half the query's terms, by the terms its lead holds, then the share of its words they are, then BM25
        (DR-0231); then the rest, in the any-word stage by how many terms their text holds, then BM25 (DR-0232). A
        chunk holding a word only through a split goes there after the chunks holding as many terms, and above those
        holding fewer; in the all-words and prefix stages, after all the others, each part in its order (DR-0282)."""
        info = self.titles(ids)
        lifted: list[tuple[int, float, int, int]] = []
        groups: set[str] = set()
        for at, chunk in enumerate(ids):
            grp, title = info[chunk]
            if title is None or grp in groups:
                continue
            held, share = self.lead_held(lead(title), q)
            if held * 2 > q.total:
                lifted.append((-held, -share, at, chunk))
                groups.add(grp)
        first = [chunk for *_, chunk in sorted(lifted)]
        moved = set(first)
        rest = [chunk for chunk in ids if chunk not in moved]
        if not by_text and not q.checker.splits:
            return first + rest
        held_by = self.text_terms(rest if by_text else ids, q)
        if by_text:
            rest.sort(key=lambda chunk: (-held_by[chunk][0], held_by[chunk][1]))
            return first + rest
        ordered = first + rest
        return [c for c in ordered if not held_by[c][1]] + [c for c in ordered if held_by[c][1]]

    # -- hits ---------------------------------------------------------------------------------------------------

    def rows(self, ids: list[int]) -> dict[int, _Row]:
        out: dict[int, _Row] = {}
        for start in range(0, len(ids), 500):
            part = ids[start : start + 500]
            for r in self.conn.execute(
                "SELECT id, kind, grp, source, ts, start_ms, end_ms, line_from, line_to, rid, doc, rids, speakers, "  # noqa: S608 - placeholders only
                f"authors, title, locator, anchors, text FROM chunks WHERE id IN ({', '.join('?' * len(part))})",
                part,
            ):
                out[r[0]] = _Row(
                    id=r[0], kind=r[1], grp=r[2], source=r[3], ts=r[4], start_ms=r[5], end_ms=r[6], line_from=r[7],
                    line_to=r[8], rid=r[9], doc=r[10], rids=json.loads(r[11]), speakers=json.loads(r[12]),
                    authors=json.loads(r[13]), title=r[14], locator=r[15], anchors=json.loads(r[16]), text=r[17],
                )  # fmt: skip
        return out

    def _matches(self, word: str) -> bool:
        if fold(word) in self.terms:
            return True
        return any(self.analyzer.stem(word.lower(), lang) in self.terms for lang in self.langs.languages)

    def _starts(self, text: str, found: list[re.Match[str]], i: int) -> bool:
        """Whether a snippet may start at a text's word i: a query word, or the first of two words in a row that are a
        hyphenated pair joining into one or one of its admitted splits (DR-0282)."""
        if self._matches(found[i].group(0)):
            return True
        if i + 1 == len(found):
            return False
        a, b = found[i].group(0), found[i + 1].group(0)
        if text[found[i].end() : found[i + 1].start()] in HYPHENS and self._matches(a + b):
            return True
        if not self.split_terms:
            return False
        a, b = a.lower(), b.lower()
        stem = self.analyzer.stem
        pairs = [("folded", fold(a), fold(b))]
        pairs += [(lang.column, stem(a, lang), stem(b, lang)) for lang in self.langs.languages]
        return any(pair in self.split_terms for pair in pairs)

    def snippet(self, text: str) -> str:
        found = list(WORD.finditer(text))
        if not found:
            return " ".join(text.split())
        at = next((i for i in range(len(found)) if self._starts(text, found, i)), 0)
        lo = max(0, at - SNIPPET_BEFORE)
        hi = min(len(found), lo + SNIPPET_WORDS)
        body = " ".join(text[found[lo].start() : found[hi - 1].end()].split())
        return ("..." if lo else "") + body + ("..." if hi < len(found) else "")

    def group(self, key: str, hits: list[_Row]) -> Group:
        record = self.conn.execute("SELECT title, source FROM records WHERE rid = ?", (key,)).fetchone()
        doc = self.conn.execute("SELECT title FROM docs WHERE id = ?", (key,)).fetchone()
        if record is not None:
            if self.conn.execute("SELECT 1 FROM segments WHERE rid = ? LIMIT 1", (key,)).fetchone():
                kind = "recording"
            elif self.conn.execute("SELECT count(*) FROM records WHERE thread = ?", (key,)).fetchone()[0] > 1:
                kind = "thread"
            else:
                kind = "record"
        else:
            kind = "doc" if doc is not None else "thread"
        first = self.conn.execute("SELECT text FROM chunks WHERE owner = ? AND ordinal = 0", (f"r {key}",)).fetchone()
        opening = " ".join((first[0] if first else hits[0].text).split()[:TITLE_WORDS])
        title = (record and record[0]) or (doc and doc[0]) or opening
        times = [t for t in (_time(h.ts) for h in hits) if t is not None]
        rids = sorted({rid for h in hits for rid in h.rids} | {key})
        summary = None if kind == "doc" else _summary(self.conn, rids, key)
        return Group(
            key=key,
            kind=kind,
            title=title,
            source=(record and record[1]) or hits[0].source,
            first=min(times, default=None),
            last=max(times, default=None),
            summary=summary,
        )


def _summary(conn: sqlite3.Connection, rids: Sequence[str], key: str) -> DocRef | None:
    """A group's current summary: a minutes, recording note or thread summary covering one of its records or a record
    of its thread, minutes first."""
    marks = ", ".join("?" * len(rids))
    row = conn.execute(
        "SELECT DISTINCT d.id, d.type, d.rev, d.title FROM docs d JOIN doc_covers c ON c.doc = d.id "  # noqa: S608 - placeholders only
        f"WHERE d.superseded_by IS NULL AND d.type IN ('minutes', 'recording-note', 'thread-summary') AND "
        f"(c.rid IN ({marks}) OR c.rid IN (SELECT rid FROM records WHERE thread = ?)) ORDER BY "
        "CASE d.type WHEN 'minutes' THEN 0 WHEN 'recording-note' THEN 1 ELSE 2 END, d.id LIMIT 1",
        [*rids, key],
    ).fetchone()
    return None if row is None else DocRef(id=row[0], type=row[1], rev=row[2], title=row[3])


def summary_for(vault: Vault, rid: str) -> DocRef | None:
    """The summary search attaches to the group a record is in, or None when no current doc summarizes it."""
    conn = open_index(vault)
    try:
        row = conn.execute("SELECT grp FROM records WHERE rid = ?", (rid,)).fetchone()
        key = rid if row is None else str(row[0])
        return _summary(conn, sorted({rid, key}), key)
    finally:
        conn.close()


class EntityLabel(BaseModel):
    id: str
    type: str
    name: str


def labels(vault: Vault, ids: Sequence[str]) -> dict[str, EntityLabel]:
    """Each known entity's type and the name to show: in the owner's first language that has one, else English, else
    the first given. Ids the index doesn't know are left out."""
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    order = [*vault.config.owner.languages, "en"]
    conn = open_index(vault)
    try:
        rows = conn.execute(
            f"SELECT id, type, names FROM entities WHERE id IN ({', '.join('?' * len(wanted))})",  # noqa: S608 - placeholders only
            wanted,
        ).fetchall()
    finally:
        conn.close()
    out: dict[str, EntityLabel] = {}
    for entity, kind, names in rows:
        given: dict[str, str] = json.loads(names)
        name = next((given[lang] for lang in order if given.get(lang)), next(iter(given.values()), entity))
        out[entity] = EntityLabel(id=entity, type=kind, name=name)
    return out


@dataclass
class _Terms:
    """The query's terms that titles and texts are checked for: its words and phrases, and the names found in it, one
    term per run of words that named them."""

    checker: Checker
    entities: dict[str, set[str]]
    leads: dict[str, tuple[int, float]] = field(default_factory=dict[str, tuple[int, float]])

    @property
    def total(self) -> int:
        return len(self.checker.words) + len(self.checker.phrases) + len(self.entities)


def lead(title: str) -> str:
    """A title's lead (DR-0231): its text up to the first `: ` that follows at least three words, else its first 12
    words. A long heading that lists everything lifts nothing with the words after its lead, while a label before a
    colon (`RE: `, `Fwd: `, `[EXT] Re: `, `Acme: `) doesn't end one. A time such as 23:44 has no space after its colon,
    and a word is a run of text between spaces holding a letter or a digit, so a date such as 2026-10-12 is one."""
    at = title.find(LEAD_END)
    while at != -1:
        if sum(1 for piece in title[:at].split() if WORD.search(piece)) >= LEAD_MIN:
            return title[:at]
        at = title.find(LEAD_END, at + 1)
    found = list(WORD.finditer(title))
    return title[: found[LEAD_WORDS - 1].end()] if len(found) > LEAD_WORDS else title


def _joins(tokens: list[str], compounds: list[Joined], kept: list[int]) -> dict[str, list[str]]:
    """Each query word's joined words (DR-0281): two words in a row join, and a hyphenated word's parts join as the
    index joins them (DR-0280); never across a name, which left the words."""
    keep = set(kept)
    joins: dict[str, list[str]] = {}
    for i in kept:
        if i + 1 in keep:
            pair = tokens[i] + tokens[i + 1]
            joins.setdefault(tokens[i], []).append(pair)
            joins.setdefault(tokens[i + 1], []).append(pair)
    for compound in compounds:
        if all(i in keep for i in range(compound.start, compound.end)):
            for i in range(compound.start, compound.end):
                joins.setdefault(tokens[i], []).append(compound.word)
    return joins


class _VectorStage:
    """The vector stage, run in a thread beside the FTS stages (DR-0337, DR-0338): the query's vector from the worker,
    then the scan on a connection of its own, which Python's sqlite3 runs while the other holds no GIL."""

    def __init__(self, models: vectors.QueryVectors, db: Path, text: str, filters: tuple[str, list[object]]) -> None:
        self.table = vectors.table_of(models.embedder)
        self.found: list[tuple[int, str]] = []
        self.why: str | None = None
        self._thread = threading.Thread(target=self._run, args=(models, db, text, filters), daemon=True)
        self._thread.start()

    def _run(self, models: vectors.QueryVectors, db: Path, text: str, filters: tuple[str, list[object]]) -> None:
        try:
            vector = models.query_vector(text)
            found = vector if isinstance(vector, str) else vectors.scan(db, self.table, vector, filters)
        except Exception as e:  # the stage only ever skips: keyword search answers whatever happens here
            found = f"the vector stage failed: {type(e).__name__}"
        if isinstance(found, str):
            self.why = found
        else:
            self.found = found

    def ids(self, conn: sqlite3.Connection) -> list[int]:
        """The scan's chunks that this search's connection holds with the input their vector was made from: the
        scan's own connection can see a moment before or after an update that this one doesn't."""
        self._thread.join(VECTOR_SECONDS)
        if self._thread.is_alive():
            self.why = f"the vector stage took more than {VECTOR_SECONDS:g} s"
            return []
        wanted = dict(self.found)
        held: set[int] = set()
        ids = list(wanted)
        for start in range(0, len(ids), 500):
            part = ids[start : start + 500]
            for chunk, title, text in conn.execute(
                f"SELECT id, title, text FROM chunks WHERE id IN ({', '.join('?' * len(part))})",  # noqa: S608 - placeholders only
                part,
            ):
                if input_hash(self.table.input(title, text)) == wanted[chunk]:
                    held.add(chunk)
        return [chunk for chunk, _ in self.found if chunk in held]


def _start_vectors(
    models: vectors.QueryVectors | Off | None, db: Path, text: str, filters: tuple[str, list[object]]
) -> tuple[_VectorStage | None, VectorsRun]:
    if models is None:
        return None, VectorsRun()
    if isinstance(models, Off):
        return None, VectorsRun(state="off" if models.why is None else "skipped", why=models.why)
    run = VectorsRun(state="on", model=models.embedder.name)
    if not text:
        run.state, run.why = "skipped", "nothing to embed: the query is only time words or filters"
        return None, run
    return _VectorStage(models, db, text, filters), run


def _fuse(lists: Sequence[tuple[Sequence[int], float]], found_by: dict[int, str]) -> list[int]:
    """The lists fused by reciprocal rank, each at its weight, then every chunk only the vectors found moved after the
    last all-words hit, at any k (DR-0338): those it passed in the fusion go just after it, in their order."""
    score: dict[int, float] = {}
    for ids, weight in lists:
        for n, chunk in enumerate(ids, start=1):
            score[chunk] = score.get(chunk, 0.0) + weight / (RRF + n)
    return _after_all_words(sorted(score, key=lambda c: (-score[c], c)), found_by)


def _after_all_words(ranked: list[int], found_by: dict[int, str]) -> list[int]:
    """An order with every chunk only the vectors found after the last all-words hit (DR-0338): those before it move to
    just after it, in their order."""
    last_all = max((n for n, chunk in enumerate(ranked) if found_by[chunk] == "all"), default=0)
    early = [chunk for chunk in ranked[:last_all] if found_by[chunk] == "vector"]
    if early:
        ranked = [c for c in ranked[: last_all + 1] if found_by[c] != "vector"] + early + ranked[last_all + 1 :]
    return ranked


def _walked(ranked: list[int], rows: dict[int, _Row], k: int) -> list[int]:
    """The chunks behind the first k hits of an order, walked as the search collapses them: the k hits' own chunks
    first, then the chunks folded into their `also`, so every hit is among the reranker's pairs whenever k is N or less
    (DR-0453)."""
    kept: list[_Row] = []
    folded: list[int] = []
    for chunk in ranked:
        row = rows[chunk]
        if any(h.grp == row.grp and _overlaps(h, row) for h in kept):
            folded.append(chunk)
            continue
        if len(kept) == k:
            break
        kept.append(row)
    return [h.id for h in kept] + folded


def _boosted(conn: sqlite3.Connection, chunks: list[int], boosts: Sequence[str]) -> set[int]:
    """The chunks that mention an entity the search was asked to lift, by its people or entities."""
    if not boosts:
        return set()
    marks = ", ".join("?" * len(chunks))
    wanted = ", ".join("?" * len(boosts))
    found = conn.execute(
        f"SELECT DISTINCT chunk FROM mentions WHERE chunk IN ({marks}) AND entity IN ({wanted})",  # noqa: S608 - placeholders only
        [*chunks, *boosts],
    )
    return {int(chunk) for (chunk,) in found}


@dataclass
class _Fused:
    """What the rerank step works from: today's order, the lists to fuse again at full weight, and the rest."""

    today: list[int]
    keyword: list[int]
    by_entity: list[int]
    by_vector: list[int]
    found_by: dict[int, str]
    rows: dict[int, _Row]

    def at(self, vector_weight: float) -> list[int]:
        lists = [(self.keyword, 1.0), (self.by_entity, 1.0), (self.by_vector, vector_weight)]
        return _fuse(lists, self.found_by)


def _rerank(
    s: _Search,
    models: vectors.QueryVectors | Off | None,
    asked: str,
    fused: _Fused,
    *,
    k: int,
    boosts: Sequence[str],
    budget: float | None,
) -> tuple[list[int], RerankRun]:
    """Today's order with its top N reranked, or today's order and why it isn't (DR-0452, DR-0453, DR-0454)."""
    if not isinstance(models, rerank.Reranks):
        return fused.today, RerankRun()
    if models.reranker is None:
        state = "skipped" if models.rerank_said else "off"
        return fused.today, RerankRun(state=state, why=models.rerank_why)
    run = RerankRun(state="on", model=models.reranker.name)
    if not asked:
        run.state, run.why = "skipped", "nothing to compare: the query is only time words or filters"
        return fused.today, run
    top = rerank.candidates(_walked(fused.today, fused.rows, k), fused.at(RERANK_WEIGHT), models.pairs)
    run.pairs = len(top)
    if len(top) < 2:
        return fused.today, run
    texts = [rerank_passage(fused.rows[c].title, fused.rows[c].text) for c in top]
    scores = models.rerank(asked, texts, budget)
    if isinstance(scores, str):
        run.state, run.why, run.pairs = "skipped", scores, 0
        return fused.today, run
    head = rerank.order(top, scores, fused.found_by, _boosted(s.conn, top, boosts))
    chosen = set(head)
    # The tiers keep the all-words hits of the top N above the rest of it; the move keeps them above a chunk only the
    # vectors found anywhere in the order, one folded into a hit included (DR-0338).
    return _after_all_words(head + [c for c in fused.today if c not in chosen], fused.found_by), run


def _iso(value: datetime) -> str:
    at = value.astimezone(UTC)
    return f"{at:%Y-%m-%dT%H:%M:%S}.{at.microsecond // 1000:03d}Z"


def search(
    vault: Vault,
    query: str,
    *,
    people: Sequence[str] = (),
    entities: Sequence[str] = (),
    sources: Sequence[str] = (),
    since: datetime | date | None = None,
    until: datetime | date | None = None,
    k: int = 20,
    now: datetime | None = None,
    models: vectors.QueryVectors | Off | None = None,
    rerank_budget: float | None = RERANK_SECONDS,
) -> SearchResult:
    """Find chunks for a query; see the module's docstring for how. With models, a vector stage runs beside the FTS
    stages, and a reranker after the fusion, each skipped, with the reason in the result, whenever it can't run. A
    rerank has `rerank_budget` seconds, or no limit for None, as `docket eval` asks."""
    if not 1 <= k <= MAX_K:
        raise DocketError(f"k is from 1 to {MAX_K}")
    conn = open_index(vault)
    try:
        s = _Search(conn, vault, languages_of(vault))
        return _run(
            s, query, people=people, entities=entities, sources=sources, since=since, until=until, k=k, now=now,
            models=models, rerank_budget=rerank_budget,
        )  # fmt: skip
    finally:
        conn.close()


def _run(
    s: _Search,
    query: str,
    *,
    people: Sequence[str],
    entities: Sequence[str],
    sources: Sequence[str],
    since: datetime | date | None,
    until: datetime | date | None,
    k: int,
    now: datetime | None,
    models: vectors.QueryVectors | Off | None = None,
    rerank_budget: float | None = RERANK_SECONDS,
) -> SearchResult:
    reading = QueryReading(text=query)
    reading.phrases = [p.strip() for p in QUOTED.findall(query) if words(p)]
    time = read_time(QUOTED.sub(" ", query).replace('"', " "), s.langs.languages, now or datetime.now(UTC), s.tz)
    reading.time_words = time.words
    tokens_read, compounds = words_and_compounds(time.rest)
    found, kept_at = s.find_entities(tokens_read)
    rest = [tokens_read[i] for i in kept_at]
    reading.entities = found
    boosted, unknown_people = s.resolve(people, person=True)
    boosted_too, unknown = s.resolve(entities, person=False)
    reading.boosts = sorted(set(boosted) | set(boosted_too))
    reading.unknown = unknown_people + unknown
    span = time.range
    if since is not None or until is not None:
        lo = _instant(since, s.tz, end=False) if since is not None else FAR_PAST
        hi = _instant(until, s.tz, end=True) if until is not None else FAR_FUTURE
        span = Range(max(lo, span.since), min(hi, span.until)) if span else Range(lo, hi)
    if span is not None:
        reading.since = None if span.since == FAR_PAST else span.since
        reading.until = None if span.until == FAR_FUTURE else span.until
    filters = s.filters(span, sources)
    asked = " ".join([*reading.phrases, *time.rest.split()])
    stage, run = _start_vectors(models, s.db, asked, filters)
    phrase_groups = [s.phrase_group(p) for p in reading.phrases]
    word_groups, word_terms, reading.absent = s.word_groups(rest, _joins(tokens_read, compounds, kept_at))
    reading.words = [w for w, _ in word_groups]
    named: dict[str, set[str]] = {}
    for entity in found:
        named.setdefault(entity.words, set()).add(entity.id)
    q = _Terms(Checker(s.analyzer, word_terms, [phrase_of(s.analyzer, p) for p in reading.phrases]), named)
    query_entities = sorted({e.id for e in found})
    entity_groups = [g for g in (s.entity_group(e) for e in query_entities) if g]
    groups = [*phrase_groups, *(g for _, g in word_groups), *entity_groups]
    # A name typed as written is searched as written first, so its exact matches lead its aliases' matches.
    literal = [
        *phrase_groups,
        *(g for _, g in word_groups),
        *(s.phrase_group(w) for w in dedupe(e.words for e in found)),
    ]
    # The any-word and prefix stages leave out the words BM25 gives no weight, which match half the chunks or more and
    # would cost scoring them all; the word check still counts them in the order (DR-0232). If those stages leave
    # fewer than k, one more any-word query adds the least common of them.
    some = [(w, g) for w, g in word_groups if s.weighs(w)]
    weightless = [g for w, g in sorted(word_groups, key=lambda wg: s.reach[wg[0]]) if not s.weighs(w)]
    any_groups = [*phrase_groups, *(g for _, g in some), *entity_groups]
    widened = [
        *phrase_groups,
        *(any_of([g, prefix("folded", fold(w))]) if len(w) >= PREFIX_MIN else g for w, g in some),
        *entity_groups,
    ]
    all_entities = sorted(set(query_entities) | set(reading.boosts))
    if not groups and not all_entities and span is None and not sources:
        raise DocketError("nothing to search for: give words, a name, a time or a source")
    order: list[int] = []
    found_by: dict[int, str] = {}

    def take(ids: list[int], how: str) -> None:
        for chunk in ids:
            if chunk not in found_by and len(order) < CANDIDATES:
                found_by[chunk] = how
                order.append(chunk)

    if groups:
        if entity_groups:
            take(s.order(s.fts(" AND ".join(literal), filters, CANDIDATES), q, by_text=False), "all")
        take(s.order(s.fts(" AND ".join(groups), filters, CANDIDATES), q, by_text=False), "all")
        if len(order) < k and len(groups) > 1 and any_groups:
            take(s.order(s.fts(" OR ".join(any_groups), filters, CANDIDATES), q, by_text=True), "any")
        if len(order) < k and widened != any_groups:
            take(s.order(s.fts(" OR ".join(widened), filters, CANDIDATES), q, by_text=False), "prefix")
        if len(order) < k and len(groups) > 1 and weightless:
            fallback = " OR ".join([*any_groups, weightless[0]])
            take(s.order(s.fts(fallback, filters, CANDIDATES), q, by_text=True), "any")
    elif not all_entities:
        take(s.newest(filters), "time")
    rank = {chunk: n for n, chunk in enumerate(order, start=1)}
    by_entity = s.entity_list(all_entities, filters, rank) if all_entities else []
    for chunk in by_entity:
        found_by.setdefault(chunk, "entity")
    by_vector = stage.ids(s.conn) if stage is not None else []
    for chunk in by_vector:
        found_by.setdefault(chunk, "vector")
    if stage is not None and stage.why is not None:
        run.state, run.why = "skipped", stage.why
    fused = _Fused([], order, by_entity, by_vector, found_by, {})
    ranked = fused.today = fused.at(VECTOR_WEIGHT)
    rows = fused.rows = s.rows(ranked)
    ranked, reranked = _rerank(s, models, asked, fused, k=k, boosts=reading.boosts, budget=rerank_budget)
    mentioned: dict[int, set[str]] = {}
    for start in range(0, len(ranked), 500):
        part = ranked[start : start + 500]
        for chunk, entity in s.conn.execute(
            f"SELECT chunk, entity FROM mentions WHERE chunk IN ({', '.join('?' * len(part))})",  # noqa: S608 - placeholders only
            part,
        ):
            mentioned.setdefault(chunk, set()).add(entity)
    kept: list[_Row] = []
    for chunk in ranked:
        row = rows[chunk]
        into = next((h for h in kept if h.grp == row.grp and _overlaps(h, row)), None)
        if into is not None:
            into.also.append(row.locator)
        elif len(kept) < k:
            kept.append(row)
        else:
            break
    # The vector list's tail would swamp the counts: a chunk only it found counts when it made the top k, in the facets
    # and in the candidates alike.
    shown = {row.id for row in kept}
    counted = [rows[c] for c in ranked if found_by[c] != "vector" or c in shown]
    result = SearchResult(query=reading, candidates=len(counted), models=run, rerank=reranked)
    hit_rank = {row.id: n for n, row in enumerate(kept, start=1)}
    by_group: dict[str, list[_Row]] = {}
    for row in kept:
        by_group.setdefault(row.grp, []).append(row)
    for key, members in by_group.items():
        group = s.group(key, members)
        for row in sorted(members, key=lambda r: (r.ts or "", r.start_ms or 0, r.line_from or 0, r.id)):
            group.hits.append(
                Hit(
                    locator=row.locator,
                    kind=row.kind,
                    rid=row.rid,
                    doc=row.doc,
                    ts=_time(row.ts),
                    start=None if row.start_ms is None else row.start_ms // 1000,
                    end=None if row.end_ms is None else -(-row.end_ms // 1000),
                    lines=None if row.line_from is None or row.line_to is None else (row.line_from, row.line_to),
                    speakers=row.speakers,
                    authors=row.authors,
                    title=row.title,
                    snippet=s.snippet(row.text),
                    rank=hit_rank[row.id],
                    found_by=found_by[row.id],
                    entities=sorted(mentioned.get(row.id, set()) & set(all_entities)),
                    anchors=row.anchors,
                    also=row.also,
                )
            )
        result.groups.append(group)
    result.facets = {
        "sources": dict(sorted(Counter(r.source for r in counted if r.source).items())),
        "entities": dict(sorted(Counter(e for r in counted for e in mentioned.get(r.id, set())).items())),
        "months": dict(sorted(Counter(r.ts[:7] for r in counted if r.ts).items())),
    }
    return result
