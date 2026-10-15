"""Matching entities into chunks: each alias's forms, the judging of stem forms, the fuzzy walk, and mentions.

An alias matches as written in `folded`, and in its case forms in languages that give them (Hungarian), matched
folded, each beside the stem form the stemmer cuts it to in that language's column: a stem form alone meets ordinary
words that stem the same (`same` for Sam), and a case form alone ordinary words that fold the same (`benne` for Ben's
`benné`). A case form the stemmer leaves whole (`Nimishsel`) counts on its own. Each stem form is judged on its own:
one under three letters, or one the index holds in over ten times as many chunks as the alias as written (counting that
as at least five), is dropped and kept in `alias_terms` as dropped. The case forms count while one stem form is kept,
whichever they are cut to (Mark's `Markkal` is cut to `mar`, which `már` crowds out, while his `mark` is kept); with
every one dropped they don't (Rob's `rob` while `robot`, Rob with -ot, is everywhere). An `asr` alias also matches
through vocabulary words within one edit of a word of 5 to 7 letters, two of 8 or more, found per word by a bounded
Levenshtein walk (DR-0350).
"""

from __future__ import annotations

import itertools
import json
import re
import sqlite3
from bisect import bisect_left
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import NamedTuple

from docket.index.languages import Language
from docket.index.text import Analyzer, any_of, fold, phrase, words
from docket.vault.entities import Entities, Entity

SHORT = 3
COMMON_TIMES = 10
COMMON_FLOOR = 5
TERMS_SQL = (
    "INSERT OR IGNORE INTO alias_terms (entity, ordinal, col, word, term, how, distance) VALUES (?, ?, ?, ?, ?, ?, ?)"
)
FUZZY_MIN = 5
FUZZY_TWO = 8
MAX_PHRASES = 20
# A mention's kinds, strongest first.
STRENGTH = ("text", "speaker", "author", "tagged", "asr", "needs")
EMAIL = re.compile(r"<([^<>@\s]+@[^<>\s]+)>|([^<>@\s]+@[^<>\s]+)")


@dataclass(frozen=True)
class AliasRow:
    entity: str
    type: str
    ordinal: int
    text: str
    kind: str
    lang: str | None
    needs: tuple[str, ...]


def alias_rows(entity: Entity) -> list[AliasRow]:
    """An entity's names (one per distinct spelling) and its aliases, numbered."""
    rows: list[AliasRow] = []
    seen: set[str] = set()
    for lang, name in sorted(entity.names.items()):
        if fold(name) not in seen:
            seen.add(fold(name))
            rows.append(AliasRow(entity.id, entity.type, len(rows), name, "name", lang, ()))
    for alias in entity.aliases:
        rows.append(AliasRow(entity.id, entity.type, len(rows), alias.text, alias.kind, None, alias.needs))
    return rows


def write_entities(conn: sqlite3.Connection, found: Entities, hashes: dict[str, str]) -> None:
    """The keyword layer's own tables, from the files."""
    for table in ("entities", "entity_files", "aliases", "entity_links", "external_ids"):
        conn.execute(f"DELETE FROM {table}")  # noqa: S608 - table names from the list above
    conn.execute("DELETE FROM problems WHERE kind = 'entity'")
    for e in found.entities:
        names = json.dumps(e.names, ensure_ascii=False, sort_keys=True)
        conn.execute(
            "INSERT INTO entities (id, type, path, names, status, role, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (e.id, e.type, e.path, names, e.status, e.role, e.note),
        )
        conn.executemany(
            "INSERT INTO aliases (entity, ordinal, text, kind, lang, needs, folded) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    a.entity,
                    a.ordinal,
                    a.text,
                    a.kind,
                    a.lang,
                    json.dumps(a.needs),
                    " ".join(fold(w) for w in words(a.text)),
                )
                for a in alias_rows(e)
            ],
        )
        conn.executemany(
            "INSERT INTO entity_links (entity, type, target) VALUES (?, ?, ?)",
            sorted({(e.id, t, target) for t, ids in e.related.items() for target in ids}),
        )
        conn.executemany(
            "INSERT INTO external_ids (entity, key, value) VALUES (?, ?, ?)",
            sorted({(e.id, k, v) for k, values in e.ids.items() for v in values}),
        )
    conn.executemany("INSERT INTO entity_files (path, sha256) VALUES (?, ?)", sorted(hashes.items()))
    conn.executemany(
        "INSERT OR IGNORE INTO problems (kind, place, message) VALUES ('entity', ?, ?)",
        sorted({(p.path, p.message) for p in found.problems}),
    )


def load_aliases(conn: sqlite3.Connection) -> list[AliasRow]:
    rows = conn.execute(
        "SELECT a.entity, e.type, a.ordinal, a.text, a.kind, a.lang, a.needs FROM aliases a "
        "JOIN entities e ON e.id = a.entity ORDER BY a.entity, a.ordinal"
    ).fetchall()
    return [AliasRow(r[0], r[1], r[2], r[3], r[4], r[5], tuple(json.loads(r[6]))) for r in rows]


# -- the fuzzy walk -----------------------------------------------------------------------------------------------


def max_distance(word: str) -> int:
    """How many edits a misspelling of a word may be: none under five letters, one to seven, two from eight."""
    return 0 if len(word) < FUZZY_MIN else 1 if len(word) < FUZZY_TWO else 2


def _after(prefix: str) -> str:
    """The first string after every string that starts with prefix."""
    return prefix[:-1] + chr(ord(prefix[-1]) + 1)


def within(word: str, limit: int, vocab: Sequence[str], work: list[int] | None = None) -> list[tuple[str, int]]:
    """The words of a sorted vocabulary within `limit` edits of word, itself left out. A Levenshtein row per prefix,
    reused for the next word that shares it; every word under a prefix already too far is skipped. `work` counts the
    rows computed."""
    found: list[tuple[str, int]] = []
    n = len(word)
    rows = [list(range(n + 1))]
    prev = ""
    i = 0
    while i < len(vocab):
        term = vocab[i]
        shared = 0
        top = min(len(prev), len(term), len(rows) - 1)
        while shared < top and prev[shared] == term[shared]:
            shared += 1
        del rows[shared + 1 :]
        skipped = False
        for k in range(shared, len(term)):
            above = rows[k]
            row = [above[0] + 1]
            for j in range(1, n + 1):
                row.append(min(row[j - 1] + 1, above[j] + 1, above[j - 1] + (word[j - 1] != term[k])))
            rows.append(row)
            if work is not None:
                work[0] += 1
            if min(row) > limit:
                prev = term[: k + 1]
                i = bisect_left(vocab, _after(prev), i + 1)
                skipped = True
                break
        if skipped:
            continue
        distance = rows[len(term)][n]
        if 0 < distance <= limit:
            found.append((term, distance))
        prev = term
        i += 1
    return found


def vocabulary(conn: sqlite3.Connection, col: str) -> list[str]:
    """A column's words as FTS5 stores them, sorted, from three letters (the shortest a misspelling can be)."""
    rows = conn.execute("SELECT term FROM vocab WHERE col = ? AND length(term) >= 3 ORDER BY term", (col,))
    return [r[0] for r in rows]


# -- forms --------------------------------------------------------------------------------------------------------


def _count(conn: sqlite3.Connection, expr: str, cache: dict[str, int]) -> int:
    if expr not in cache:
        cache[expr] = conn.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH ?", (expr,)).fetchone()[0]
    return cache[expr]


def _stem_forms(analyzer: Analyzer, ws: list[str], lang: Language) -> list[tuple[str, ...]]:
    head = tuple(analyzer.stem(w, lang) for w in ws[:-1])
    return [(*head, last) for last in analyzer.forms(ws[-1], lang)]


def alias_langs(analyzer: Analyzer) -> list[Language]:
    """The languages aliases match in through their case forms, judged by their stem forms: those that give them."""
    return [lang for lang in analyzer.languages if lang.cases is not None]


class Cases(NamedTuple):
    """An alias's case forms in a language, folded: each one the stemmer cuts to one of its stem forms with that stem
    form, and those it cuts to none."""

    reached: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]
    alone: tuple[tuple[str, ...], ...]


def cases_of(analyzer: Analyzer, ws: list[str], lang: Language) -> Cases:
    """An alias's case forms in a language, folded; only its last word inflects. One the stemmer cuts to one of the
    alias's stem forms counts only beside that stem form: an ordinary word can fold to it and stem elsewhere (`benné`,
    Ben's translative, folds to the Hungarian `benne`, which stems to `benn`), and the stem form it is cut to may be
    crowded out while another is kept (Mark's `mar` by `már`, his `mark` kept). A form two endings fold to is listed
    with each stem form they are cut to. One the stemmer leaves whole (`Nimishsel`) counts on its own (DR-0350)."""
    head = tuple(fold(w) for w in ws[:-1])
    stem_forms = {form[-1]: form for form in _stem_forms(analyzer, ws, lang)}
    cut_to: dict[tuple[str, ...], list[tuple[str, ...]]] = {}
    for case in lang.cases(ws[-1]) if lang.cases is not None else []:
        stems = cut_to.setdefault((*head, fold(case)), [])
        stem = stem_forms.get(analyzer.stem(case, lang))
        if stem is not None and stem not in stems:
            stems.append(stem)
    return Cases(
        tuple((form, stem) for form, stems in cut_to.items() for stem in stems),
        tuple(form for form, stems in cut_to.items() if not stems),
    )


def judge_forms(conn: sqlite3.Connection, analyzer: Analyzer, aliases: list[AliasRow]) -> None:
    """Every alias's forms as written and its stem forms, kept or dropped, in alias_terms (word -1)."""
    conn.execute("DELETE FROM alias_terms WHERE word = -1")
    cache: dict[str, int] = {}
    rows: set[tuple[str, int, str, int, str, str, int]] = set()
    for alias in aliases:
        ws = words(alias.text)
        written = tuple(fold(w) for w in ws)
        rows.add((alias.entity, alias.ordinal, "folded", -1, " ".join(written), "written", 0))
        floor = max(_count(conn, phrase("folded", written), cache), COMMON_FLOOR)
        for lang in alias_langs(analyzer):
            for form in _stem_forms(analyzer, ws, lang):
                expr = phrase(lang.column, form)
                count = _count(conn, expr, cache)
                if len(form[-1]) < SHORT:
                    how = "short"
                elif count > COMMON_TIMES * floor:
                    how = "common"
                else:
                    how = "stem"
                rows.add((alias.entity, alias.ordinal, lang.column, -1, " ".join(form), how, 0))
    conn.executemany(
        TERMS_SQL,
        sorted(rows),
    )


def _seeds(analyzer: Analyzer, alias: AliasRow) -> list[tuple[str, int, str, int]]:
    """Where an `asr` alias's misspellings are looked for: (column, word position, seed, edits)."""
    ws = words(alias.text)
    out = [("folded", i, fold(w), max_distance(w)) for i, w in enumerate(ws) if max_distance(w)]
    if ws and max_distance(ws[-1]):
        for lang in alias_langs(analyzer):
            out += [(lang.column, len(ws) - 1, seed, max_distance(ws[-1])) for seed in analyzer.seeds(ws[-1], lang)]
    return out


def walk(
    conn: sqlite3.Connection,
    analyzer: Analyzer,
    aliases: list[AliasRow],
    vocab: dict[str, Sequence[str]] | None = None,
) -> None:
    """The `asr` aliases' misspellings in the vocabularies, per word (word >= 0): the whole vocabulary when vocab is
    None, else only the words given (new words, on an update)."""
    found: set[tuple[str, int, str, int, str, str, int]] = set()
    loaded: dict[str, Sequence[str]] = dict(vocab or {})
    for alias in (a for a in aliases if a.kind == "asr"):
        for col, position, seed, limit in _seeds(analyzer, alias):
            if col not in loaded:
                loaded[col] = vocabulary(conn, col) if vocab is None else []
            for term, distance in within(seed, limit, loaded[col]):
                found.add((alias.entity, alias.ordinal, col, position, term, "asr", distance))
    conn.executemany(
        TERMS_SQL,
        sorted(found),
    )


def prune(conn: sqlite3.Connection) -> None:
    """Misspellings no chunk holds any longer."""
    rows = conn.execute("SELECT DISTINCT col, term FROM alias_terms WHERE word >= 0").fetchall()
    gone = [(col, term) for col, term in rows if not conn.execute(
        "SELECT 1 FROM vocab WHERE col = ? AND term = ?", (col, term)
    ).fetchone()]  # fmt: skip
    conn.executemany("DELETE FROM alias_terms WHERE word >= 0 AND col = ? AND term = ?", gone)


# -- expressions and mentions -------------------------------------------------------------------------------------


def expressions(
    conn: sqlite3.Connection, analyzer: Analyzer, entities: Sequence[str] | None = None, *, needs: bool = True
) -> dict[tuple[str, int], str]:
    """Each alias's FTS5 expression: its form as written, while one of its stem forms is kept its case forms beside the
    stem form each is cut to (or alone where the stemmer leaves them whole), and for an `asr` alias up to 20 phrases
    from its misspellings. Only the entities given, when given; without aliases that have `needs`, when needs is
    False."""
    sql = (
        "SELECT t.entity, t.ordinal, t.col, t.word, t.term, t.how, t.distance, a.text FROM alias_terms t "
        "JOIN aliases a ON a.entity = t.entity AND a.ordinal = t.ordinal WHERE (? OR a.needs = '[]')"
    )
    args: list[object] = [needs]
    if entities is not None:
        sql += f" AND t.entity IN ({', '.join('?' * len(entities))})"
        args += list(entities)
    langs = {lang.column: lang for lang in alias_langs(analyzer)}
    forms: dict[tuple[str, int], list[str]] = {}
    kept: dict[tuple[str, int, str], str] = {}
    variants: dict[tuple[str, int, str], dict[int, list[tuple[int, str]]]] = {}
    for entity, ordinal, col, word, term, how, distance, text in conn.execute(
        sql + " ORDER BY t.entity, t.ordinal, t.col, t.word, t.distance, t.term", args
    ):
        key = (entity, ordinal)
        if word < 0 and how in ("written", "stem"):
            if how == "written":
                forms.setdefault(key, []).append(phrase(col, term.split()))
            else:
                kept[(entity, ordinal, col)] = text
            variants.setdefault((entity, ordinal, col), {}).setdefault(-1, []).append((0, term))
        elif word >= 0:
            variants.setdefault((entity, ordinal, col), {}).setdefault(word, []).append((distance, term))
    # A stem form alone meets ordinary words that stem the same (`same` for Sam), and a case form alone ordinary words
    # that fold the same (`benne` for Benné): each case form counts beside the stem form it is cut to, even one crowded
    # out (Mark's `mar` by `már`), and one the stemmer leaves whole (`Nimishsel`) on its own.
    for (entity, ordinal, col), text in kept.items():
        cases = cases_of(analyzer, words(text), langs[col])
        by_stem: dict[tuple[str, ...], list[str]] = {}
        for form, stem in cases.reached:
            by_stem.setdefault(stem, []).append(phrase("folded", form))
        alias = forms.setdefault((entity, ordinal), [])
        alias += [f"({phrase(col, stem)} AND {any_of(parts)})" for stem, parts in by_stem.items()]
        alias += [phrase("folded", form) for form in cases.alone]
    for (entity, ordinal, col), by_word in sorted(variants.items()):
        if not any(p >= 0 for p in by_word):
            continue
        for _, base in by_word.get(-1, []):
            base_words = base.split()
            options = [
                [(0, w), *by_word.get(i, [])] if col == "folded" or i == len(base_words) - 1 else [(0, w)]
                for i, w in enumerate(base_words)
            ]
            combos = sorted(
                (sum(d for d, _ in combo), tuple(t for _, t in combo)) for combo in itertools.product(*options)
            )
            extra = [phrase(col, terms) for total, terms in combos if total > 0][:MAX_PHRASES]
            forms.setdefault((entity, ordinal), []).extend(extra)
    return {key: any_of(sorted(set(parts), key=parts.index)) for key, parts in forms.items()}


def _address(author: str) -> str | None:
    m = EMAIL.search(author)
    return None if m is None else (m.group(1) or m.group(2)).casefold()


def mentions(conn: sqlite3.Connection, analyzer: Analyzer, aliases: list[AliasRow]) -> None:
    """Every chunk's mentions, one row per chunk and entity with the strongest kind, from scratch."""
    conn.execute("DELETE FROM mentions")
    best: dict[tuple[int, str], tuple[int, int | None]] = {}

    def add(chunk: int, entity: str, how: str, alias: int | None) -> None:
        strength = STRENGTH.index(how)
        if (chunk, entity) not in best or strength < best[(chunk, entity)][0]:
            best[(chunk, entity)] = (strength, alias)

    exprs = expressions(conn, analyzer)
    conditional: list[tuple[AliasRow, list[int]]] = []
    for alias in aliases:
        # Every alias has its form as written, so every alias has an expression.
        expr = exprs[(alias.entity, alias.ordinal)]
        hits = [r[0] for r in conn.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", (expr,))]
        if alias.needs:
            conditional.append((alias, hits))
            continue
        for chunk in hits:
            add(chunk, alias.entity, "asr" if alias.kind == "asr" else "text", alias.ordinal)
    speakers = _speakers(aliases)
    ids = conn.execute("SELECT key, value, entity FROM external_ids ORDER BY entity").fetchall()
    tags: dict[str, list[str]] = {r[0]: json.loads(r[1]) for r in conn.execute("SELECT id, entities FROM docs")}
    known = {a.entity for a in aliases}
    for chunk, source, said, wrote, doc in conn.execute("SELECT id, source, speakers, authors, doc FROM chunks"):
        for label in json.loads(said):
            person = speakers.get(_label(label))
            if person is not None:
                add(chunk, person, "speaker", None)
        for author in json.loads(wrote):
            for entity in _authors(author, source, ids):
                add(chunk, entity, "author", None)
        for entity in tags.get(doc, []) if doc else []:
            if entity in known:
                add(chunk, entity, "tagged", None)
    present: dict[int, set[str]] = {}
    for chunk, entity in best:
        present.setdefault(chunk, set()).add(entity)
    for alias, hits in conditional:
        for chunk in hits:
            if present.get(chunk, set()) & set(alias.needs):
                add(chunk, alias.entity, "needs", alias.ordinal)
    conn.executemany(
        "INSERT INTO mentions (chunk, entity, how, alias) VALUES (?, ?, ?, ?)",
        sorted((chunk, entity, STRENGTH[s], alias) for (chunk, entity), (s, alias) in best.items()),
    )


def _label(text: str) -> str:
    return " ".join(fold(w) for w in words(text))


def _speakers(aliases: Iterable[AliasRow]) -> dict[str, str]:
    """A speaker's label, folded, to the person whose name or plain alias it is; the first person by id wins."""
    out: dict[str, str] = {}
    for alias in aliases:
        if alias.type == "person" and alias.kind in ("name", "plain"):
            out.setdefault(_label(alias.text), alias.entity)
    return out


def _authors(author: str, source: str | None, ids: list[tuple[str, str, str]]) -> list[str]:
    """The entities whose ids name an author. Ids are kept as written, for the entity tool, and compared ignoring
    case."""
    address = _address(author)
    domain = address.rpartition("@")[2] if address else None
    out: list[str] = []
    for key, value, entity in ids:
        folded = value.casefold()
        if (
            (key == "email" and folded == address)
            or (key == "email_domain" and folded == domain)
            or (key == source and folded == author.casefold())
        ):
            out.append(entity)
    return out


def persons(conn: sqlite3.Connection, aliases: list[AliasRow]) -> None:
    """Each segment's speaker as a person, where a person's name or plain alias is the speaker's label."""
    speakers = _speakers(aliases)
    labels = [r[0] for r in conn.execute("SELECT DISTINCT speaker FROM segments WHERE speaker IS NOT NULL")]
    conn.executemany(
        "UPDATE segments SET person = ? WHERE speaker = ?",
        [(speakers.get(_label(label)), label) for label in labels],
    )


def tag_problems(conn: sqlite3.Connection, aliases: list[AliasRow]) -> None:
    """A doc's frontmatter naming an entity no file defines."""
    conn.execute("DELETE FROM problems WHERE kind = 'tagged'")
    known = {a.entity for a in aliases}
    rows = {
        (path, f"entities: no entity has the id {entity!r}")
        for path, listed in conn.execute("SELECT path, entities FROM docs")
        for entity in json.loads(listed)
        if entity not in known
    }
    conn.executemany("INSERT INTO problems (kind, place, message) VALUES ('tagged', ?, ?)", sorted(rows))


def refresh(
    conn: sqlite3.Connection, analyzer: Analyzer, *, full: bool, new_words: dict[str, set[str]] | None = None
) -> None:
    """The keyword layer's derived rows after a change: the forms judged again, the misspellings walked (all of them,
    or the new words only), and every mention and speaker from scratch."""
    aliases = load_aliases(conn)
    judge_forms(conn, analyzer, aliases)
    if full:
        conn.execute("DELETE FROM alias_terms WHERE word >= 0")
        walk(conn, analyzer, aliases)
    else:
        prune(conn)
        walk(conn, analyzer, aliases, {col: sorted(ws) for col, ws in (new_words or {}).items()})
    mentions(conn, analyzer, aliases)
    persons(conn, aliases)
    tag_problems(conn, aliases)
