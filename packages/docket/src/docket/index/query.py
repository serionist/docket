"""Search: one query over the index, keyword-only, answered in well under a second.

1. Read the query: quoted phrases, words, time words (a range), and names or aliases found in it (its entities).
2. Each word becomes an OR of its forms across the columns; each entity an OR of its aliases' forms.
3. FTS candidates, top 200 by BM25, filters applied inside the query: all words required, however common (DR-0230); if
   fewer than k, any of them; if still fewer, word prefixes. In each, the best chunk of each group whose title's lead
   holds most of the query's terms goes first (DR-0231); in the any-word stage the rest go by how many terms their text
   holds (DR-0232), then by BM25. The any-word and prefix stages leave out words in half the chunks or more, which
   BM25 gives no weight; they still count in the order, and the least common joins one more any-word query when the
   stages leave fewer than k.
4. Entity candidates, top 200: chunks mentioning the query's or the boosts' entities.
5. Fuse the two lists by reciprocal rank, so a boost lifts a chunk and never drops one.
6. Collapse hits on the same stretch, group them by recording or thread, attach each group's summary.

The parameters follow the design's MCP `search` tool, which the service (#8) maps one to one (DR-0117).
"""

from __future__ import annotations

import functools
import json
import re
import sqlite3
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from docket.errors import DocketError
from docket.index import matching
from docket.index.build import languages_of, open_index
from docket.index.languages import Languages
from docket.index.terms import Checker, Word, phrase_of, tokens
from docket.index.text import WORD, Analyzer, any_of, dedupe, fold, in_columns, phrase, prefix, words
from docket.index.timewords import Range, read_time
from docket.vault.vault import Vault

CANDIDATES = 200
ENTITY_ROWS = 2000
RRF = 60
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
QUOTED = re.compile(r'"([^"]*)"')
# The open ends of a range given only one side: far enough, and still printable in any time zone.
FAR_PAST = datetime(1900, 1, 1, tzinfo=UTC)
FAR_FUTURE = datetime(9000, 1, 1, tzinfo=UTC)


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


class SearchResult(BaseModel):
    query: QueryReading
    groups: list[Group] = []
    facets: dict[str, dict[str, int]] = {}
    candidates: int = 0


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


class _Search:
    def __init__(self, conn: sqlite3.Connection, vault: Vault, langs: Languages) -> None:
        self.conn = conn
        self.langs = langs
        self.analyzer = Analyzer(langs)
        self.tz = ZoneInfo(vault.config.owner.time_zone)
        self.terms: set[str] = set()
        self.lookup, self.longest = self._lookups()
        # Per query word, the most chunks any of its forms is in.
        self.reach: dict[str, int] = {}

    @functools.cached_property
    def total(self) -> int:
        """How many chunks there are, read once, and only for a query that has an any-word stage."""
        return int(self.conn.execute("SELECT count(*) FROM chunks").fetchone()[0])

    def weighs(self, word: str) -> bool:
        """Whether BM25 still weighs a word: FTS5 gives a term in half the chunks or more no weight at all."""
        return self.reach[word] * 2 < self.total

    # -- reading the query --------------------------------------------------------------------------------------

    def _lookups(self) -> tuple[dict[tuple[str, tuple[str, ...]], set[str]], int]:
        """Each alias form (column, words) to its entities; aliases with `needs` left out, they count only in chunks."""
        lookup: dict[tuple[str, tuple[str, ...]], set[str]] = {}
        longest = 0
        for entity, col, term in self.conn.execute(
            "SELECT t.entity, t.col, t.term FROM alias_terms t JOIN aliases a ON a.entity = t.entity "
            "AND a.ordinal = t.ordinal WHERE t.word = -1 AND t.how IN ('written', 'stem') AND a.needs = '[]'"
        ):
            key: tuple[str, tuple[str, ...]] = (col, tuple(term.split()))
            lookup.setdefault(key, set()).add(entity)
            longest = max(longest, len(key[1]))
        return lookup, longest

    def _candidates_at(self, tokens: list[str]) -> list[tuple[str, tuple[str, ...]]]:
        """The forms a run of query words takes: as written, and stemmed per language whose aliases use stems."""
        out = [("folded", tuple(fold(t) for t in tokens))]
        for lang in matching.alias_langs(self.analyzer):
            head = tuple(self.analyzer.stem(t, lang) for t in tokens[:-1])
            out += [(lang.column, (*head, form)) for form in self.analyzer.forms(tokens[-1], lang)]
        return out

    def entity_spans(self, tokens: list[str]) -> list[tuple[int, int, set[str]]]:
        """The runs of words that are a name or an alias, longest first, with the entities each names."""
        spans: list[tuple[int, int, set[str]]] = []
        i = 0
        while i < len(tokens):
            for size in range(min(self.longest, len(tokens) - i), 0, -1):
                hits = set[str]().union(*(self.lookup.get(c, set()) for c in self._candidates_at(tokens[i : i + size])))
                if hits:
                    spans.append((i, i + size, hits))
                    i += size
                    break
            else:
                i += 1
        return spans

    def find_entities(self, tokens: list[str]) -> tuple[list[EntityFound], list[str]]:
        """Names and aliases in the query, longest first; the words left over."""
        found: list[EntityFound] = []
        rest: list[str] = []
        at = 0
        for start, end, hits in self.entity_spans(tokens):
            rest += tokens[at:start]
            found += [EntityFound(id=e, words=" ".join(tokens[start:end])) for e in sorted(hits)]
            at = end
        return found, rest + tokens[at:]

    def resolve(self, names: Sequence[str], person: bool) -> tuple[list[str], list[str]]:
        """Boosts by id, name or alias; what resolves to nothing comes back as unknown."""
        types = dict(self.conn.execute("SELECT id, type FROM entities").fetchall())
        ids: list[str] = []
        unknown: list[str] = []
        for name in names:
            written = ("folded", tuple(fold(w) for w in words(name)))
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

    def word_groups(self, tokens: list[str]) -> tuple[list[tuple[str, str]], list[Word], list[str]]:
        """(word, expression) per word, with every form however common (DR-0230); each word with its forms, for the
        word check, which also reads titles no chunk holds; and the words no chunk holds in any form.

        The expression leaves out a form its column doesn't hold, which matches nothing, and puts a term several
        columns hold in one phrase: a common word's every phrase costs BM25 a pass over its rows."""
        groups: list[tuple[str, str]] = []
        kept: list[Word] = []
        absent: list[str] = []
        for word in dedupe(tokens):
            forms = self.word_forms(word)
            self.terms |= {term for _, term in forms}
            kept.append(Word(word, frozenset(forms)))
            counts = [self._docs(col, term) for col, term in forms]
            held = [form for form, n in zip(forms, counts, strict=True) if n]
            self.reach[word] = max(counts)
            if not held:
                absent.append(word)
            columns: dict[str, list[str]] = {}
            for col, term in held or forms[:1]:
                columns.setdefault(term, []).append(col)
            groups.append((word, any_of(in_columns(cols, term) for term, cols in columns.items())))
        return groups, kept, absent

    def phrase_group(self, text: str) -> str:
        ws = words(text)
        self.terms |= {fold(w) for w in ws}
        parts = [phrase("folded", [fold(w) for w in ws])]
        parts += [phrase(lang.column, [self.analyzer.stem(w, lang) for w in ws]) for lang in self.langs.languages]
        return any_of(parts)

    def entity_group(self, entity: str) -> str | None:
        exprs = list(matching.expressions(self.conn, [entity], needs=False).values())
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

    def text_terms(self, ids: list[int], q: _Terms) -> dict[int, int]:
        """How many of the query's terms each chunk's text holds: words and phrases by the word check, names by the
        chunk's mentions."""
        marks = ", ".join("?" * len(ids))
        texts = dict(self.conn.execute(f"SELECT id, text FROM chunks WHERE id IN ({marks})", ids).fetchall())  # noqa: S608 - placeholders only
        named: dict[int, set[str]] = {}
        for chunk, entity in self.conn.execute(f"SELECT chunk, entity FROM mentions WHERE chunk IN ({marks})", ids):  # noqa: S608 - placeholders only
            named.setdefault(chunk, set()).add(entity)
        return {
            chunk: q.checker.count(texts[chunk])
            + sum(1 for wanted in q.entities.values() if wanted & named.get(chunk, set()))
            for chunk in ids
        }

    def order(self, ids: list[int], q: _Terms, *, by_text: bool) -> list[int]:
        """A stage's candidates, BM25 order given: first the best chunk of each group whose title's lead holds more
        than half the query's terms, by the terms its lead holds, then the share of its words they are, then BM25
        (DR-0231); then the rest, in the any-word stage by how many terms their text holds, then BM25 (DR-0232)."""
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
        if by_text:
            held_by = self.text_terms(rest, q)
            rest.sort(key=lambda chunk: -held_by[chunk])
        return first + rest

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

    def snippet(self, text: str) -> str:
        found = list(WORD.finditer(text))
        if not found:
            return " ".join(text.split())
        at = next((i for i, m in enumerate(found) if self._matches(m.group(0))), 0)
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
) -> SearchResult:
    """Find chunks for a query; see the module's docstring for how."""
    if not 1 <= k <= MAX_K:
        raise DocketError(f"k is from 1 to {MAX_K}")
    conn = open_index(vault)
    try:
        s = _Search(conn, vault, languages_of(vault))
        return _run(s, query, people=people, entities=entities, sources=sources, since=since, until=until, k=k, now=now)
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
) -> SearchResult:
    reading = QueryReading(text=query)
    reading.phrases = [p.strip() for p in QUOTED.findall(query) if words(p)]
    time = read_time(QUOTED.sub(" ", query).replace('"', " "), s.langs.languages, now or datetime.now(UTC), s.tz)
    reading.time_words = time.words
    found, rest = s.find_entities(words(time.rest))
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
    phrase_groups = [s.phrase_group(p) for p in reading.phrases]
    word_groups, word_terms, reading.absent = s.word_groups(rest)
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
    score: dict[int, float] = {}
    for n, chunk in enumerate(order, start=1):
        score[chunk] = score.get(chunk, 0.0) + 1 / (RRF + n)
    for n, chunk in enumerate(by_entity, start=1):
        score[chunk] = score.get(chunk, 0.0) + 1 / (RRF + n)
        found_by.setdefault(chunk, "entity")
    ranked = sorted(score, key=lambda c: (-score[c], c))
    rows = s.rows(ranked)
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
    result = SearchResult(query=reading, candidates=len(ranked))
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
    candidates = [rows[c] for c in ranked]
    result.facets = {
        "sources": dict(sorted(Counter(r.source for r in candidates if r.source).items())),
        "entities": dict(sorted(Counter(e for r in candidates for e in mentioned.get(r.id, set())).items())),
        "months": dict(sorted(Counter(r.ts[:7] for r in candidates if r.ts).items())),
    }
    return result
