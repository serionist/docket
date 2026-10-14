"""Search: one query over the index, keyword-only, answered in well under a second.

1. Read the query: quoted phrases, words, time words (a range), and names or aliases found in it (its entities).
2. Each word becomes an OR of its forms across the columns; each entity an OR of its aliases' forms.
3. FTS candidates, top 200 by BM25, filters applied inside the query: all words required; if fewer than k, any of them;
   if still fewer, word prefixes. Forms found in over a fifth of all chunks, and over 100, are left out.
4. Entity candidates, top 200: chunks mentioning the query's or the boosts' entities.
5. Fuse the two lists by reciprocal rank, so a boost lifts a chunk and never drops one.
6. Collapse hits on the same stretch, group them by recording or thread, attach each group's summary.

The parameters follow the design's MCP `search` tool, which the service (#8) maps one to one (DR-0117).
"""

from __future__ import annotations

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
from docket.index.text import WORD, Analyzer, any_of, dedupe, fold, phrase, prefix, words
from docket.index.timewords import Range, read_time
from docket.vault.vault import Vault

CANDIDATES = 200
ENTITY_ROWS = 2000
RRF = 60
MAX_K = 100
STEMMED_WEIGHT = 0.6
COMMON_SHARE = 5
COMMON_MIN = 100
PREFIX_MIN = 3
SNIPPET_BEFORE = 15
SNIPPET_WORDS = 40
TITLE_WORDS = 8
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
    left_out: list[str] = []
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
        self.total = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        self.terms: set[str] = set()
        self.lookup, self.longest = self._lookups()

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

    def find_entities(self, tokens: list[str]) -> tuple[list[EntityFound], list[str]]:
        """Names and aliases in the query, longest first; the words left over."""
        found: list[EntityFound] = []
        rest: list[str] = []
        i = 0
        while i < len(tokens):
            for size in range(min(self.longest, len(tokens) - i), 0, -1):
                span = tokens[i : i + size]
                hits = set[str]().union(*(self.lookup.get(c, set()) for c in self._candidates_at(span)))
                if hits:
                    found += [EntityFound(id=e, words=" ".join(span)) for e in sorted(hits)]
                    i += size
                    break
            else:
                rest.append(tokens[i])
                i += 1
        return found, rest

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

    def _common(self, col: str, term: str) -> bool:
        n = self._docs(col, term)
        return n * COMMON_SHARE > self.total and n > COMMON_MIN

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

    def word_groups(self, tokens: list[str]) -> tuple[list[tuple[str, str]], list[str]]:
        """(word, expression) per word, common forms left out; words with every form common leave, unless all do."""
        kept: list[tuple[str, str]] = []
        left: list[str] = []
        everything: list[tuple[str, str]] = []
        for word in tokens:
            forms = self.word_forms(word)
            self.terms |= {term for _, term in forms}
            everything.append((word, any_of(phrase(col, [term]) for col, term in forms)))
            rare = [(col, term) for col, term in forms if not self._common(col, term)]
            if rare:
                kept.append((word, any_of(phrase(col, [term]) for col, term in rare)))
            else:
                left.append(word)
        return (kept, left) if kept else (everything, [])

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
        summary = None
        if kind != "doc":
            marks = ", ".join("?" * len(rids))
            row = self.conn.execute(
                "SELECT DISTINCT d.id, d.type, d.rev, d.title FROM docs d JOIN doc_covers c ON c.doc = d.id "  # noqa: S608 - placeholders only
                f"WHERE d.superseded_by IS NULL AND d.type IN ('minutes', 'recording-note', 'thread-summary') AND "
                f"(c.rid IN ({marks}) OR c.rid IN (SELECT rid FROM records WHERE thread = ?)) ORDER BY "
                "CASE d.type WHEN 'minutes' THEN 0 WHEN 'recording-note' THEN 1 ELSE 2 END, d.id LIMIT 1",
                [*rids, key],
            ).fetchone()
            summary = None if row is None else DocRef(id=row[0], type=row[1], rev=row[2], title=row[3])
        return Group(
            key=key,
            kind=kind,
            title=title,
            source=(record and record[1]) or hits[0].source,
            first=min(times, default=None),
            last=max(times, default=None),
            summary=summary,
        )


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
    word_groups, reading.left_out = s.word_groups(rest)
    reading.words = [w for w, _ in word_groups]
    query_entities = sorted({e.id for e in found})
    entity_groups = [g for g in (s.entity_group(e) for e in query_entities) if g]
    groups = [*phrase_groups, *(g for _, g in word_groups), *entity_groups]
    # A name typed as written is searched as written first, so its exact matches lead its aliases' matches.
    literal = [
        *phrase_groups,
        *(g for _, g in word_groups),
        *(s.phrase_group(w) for w in dedupe(e.words for e in found)),
    ]
    widened = [
        *phrase_groups,
        *(any_of([g, prefix("folded", fold(w))]) if len(w) >= PREFIX_MIN else g for w, g in word_groups),
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
            take(s.fts(" AND ".join(literal), filters, CANDIDATES), "all")
        take(s.fts(" AND ".join(groups), filters, CANDIDATES), "all")
        if len(order) < k and len(groups) > 1:
            take(s.fts(" OR ".join(groups), filters, CANDIDATES), "any")
        if len(order) < k and widened != groups:
            take(s.fts(" OR ".join(widened), filters, CANDIDATES), "prefix")
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
