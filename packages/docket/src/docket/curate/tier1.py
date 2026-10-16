"""Tier 1 (DR-0469, DR-0470): facts the index holds that need no judgement, applied by themselves when a round starts
on the vault's primary machine, before the miner, in one commit.

- address: an author written `Name <address>` gives `ids.email` to the one person or org whose full name or plain
  alias, two words or more, the name folds to;
- chat id: an author written `Name <id>`, the id not an address, gives `ids.<source>` to that one entity;
- shown name: the name a system shows for an address or id an entity already lists, not yet one of its names, not
  another entity's, and like one of its own, becomes its plain alias, a trailing `(...)` dropped;
- engines: one spelling, capitalised alike in two engines' transcripts on overlapping utterances of a recording,
  becomes an `asr` alias of the one entity the recording mentions that it is within matching's fuzzy distance of.

An address or id shown with names of more than one person is a shared sender, a service's notification address or a
team's: it gives nothing, and since the index keeps every chunk, it stays shared at every later round. A fact that
matches two entities, a one-word name, and a spelling the miner's own screens catch (a common word or one only at a
sentence's start, a case form of the name it is near, a word of another entity's name) are cards, never facts.

Authors only: the index keeps no recipients. Tier 1 never creates an entity, and never applies a key the log shows it
applied before, whatever happened to it since, nor one the stoplist holds (DR-0461). A fact the store refuses (a
never-store value, a redaction rule, a file it can't rewrite) is skipped and listed with the round; the rest go in. It
works out its facts beside the service's writer and writes them on it. It doesn't run on another machine, where two
machines changing one entity file would conflict in git, nor while a line of the curate log doesn't read (DR-0460).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from itertools import combinations

from docket.curate.propose import COMMON_SHARE, Proposal
from docket.curate.spellings import SENTENCE_END
from docket.errors import DocketError
from docket.index.build import open_index
from docket.index.languages import Language, resolve
from docket.index.matching import EMAIL, max_distance, within
from docket.index.text import phrase
from docket.plaintext import WORD, fold
from docket.vault import curatelog
from docket.vault.curatelog import MAX_LOCATORS, AliasFields, Change, Evidence, IdFields, Via, folded, key_of
from docket.vault.entitystore import EntityStore, Planned, RefusedError, Written, screen
from docket.vault.vault import Vault

Rule = str
# An author as a system writes a person beside their address or id.
AUTHOR = re.compile(r"^\s*(?P<name>[^<>]*?)\s*<(?P<id>[^<>\s]+)>\s*$")
# What a system adds after a shown name: `Ada Example (Northwind)`.
TAIL = re.compile(r"\s*\([^()]*\)\s*$")
PEOPLE = ("person", "org")
# A word of a name stands for another when it is it, its initial, or a start of it this long or longer.
PREFIX = 3
Segment = tuple[int, int, str]


@dataclass(frozen=True)
class Plan:
    """What tier 1 found, worked out beside the writer: the changes to apply, the facts that are cards, and why it
    doesn't run, when it doesn't."""

    planned: tuple[Planned, ...] = ()
    proposals: tuple[Proposal, ...] = ()
    note: str | None = None


@dataclass(frozen=True)
class Tier1:
    """What tier 1 did: its commit, the facts the store refused (named by rule and entity, never by value), the facts
    that are cards, for the miner, and why it didn't run, when it didn't."""

    written: Written | None = None
    skipped: tuple[str, ...] = ()
    proposals: tuple[Proposal, ...] = ()
    note: str | None = None


@dataclass
class _Found:
    rule: Rule
    entities: tuple[str, ...]
    text: str
    trust: str
    alias: AliasFields | None = None
    id: IdFields | None = None
    # A fact one of the screens caught: a card even when one entity matches.
    card: bool = False
    chunks: set[int] = field(default_factory=set[int])


@dataclass
class _Entities:
    """What the index holds of the entities: each one's names and aliases, folded; the people and orgs by a full name
    of two words or more and by a one-word one; who lists each id; every word of every name and alias; each one-word
    name and alias as written, for its case forms; and what aliases hold already."""

    names: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    full: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    one: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    ids: dict[tuple[str, str], set[str]] = field(default_factory=dict[tuple[str, str], set[str]])
    words: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    single: list[tuple[str, str, str]] = field(default_factory=list[tuple[str, str, str]])
    common: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    known: set[str] = field(default_factory=set[str])

    @classmethod
    def read(cls, conn: sqlite3.Connection) -> _Entities:
        out = cls()
        sql = "SELECT a.entity, e.type, a.text, a.kind FROM aliases a JOIN entities e ON e.id = a.entity"
        for entity, kind, text, how in conn.execute(sql):
            name = folded(text)
            out.names.setdefault(entity, set()).add(name)
            for word in name.split():
                out.words.setdefault(word, set()).add(entity)
            if kind in PEOPLE and how in ("name", "plain"):
                (out.full if " " in name else out.one).setdefault(name, set()).add(entity)
            if name and " " not in name:
                out.single.append((entity, name, text))
        for entity, key, value in conn.execute("SELECT entity, key, value FROM external_ids"):
            out.ids.setdefault((key, value.casefold()), set()).add(entity)
        common = "SELECT entity, term FROM alias_terms WHERE col = 'folded' AND how = 'common' AND word = -1"
        for entity, term in conn.execute(common):
            out.common.setdefault(entity, set()).add(term)
        # What aliases hold already, as written or as a misspelling an asr alias matches, as the miner reads it.
        terms = "SELECT term FROM alias_terms WHERE col = 'folded' AND (how = 'written' OR (word >= 0 AND how = 'asr'))"
        out.known = {r[0] for r in conn.execute(terms)}
        return out

    def other(self, name: str, entity: str) -> bool:
        """Whether a name is another entity's name or plain alias."""
        return bool((self.full.get(name, set()) | self.one.get(name, set())) - {entity})


def find(conn: sqlite3.Connection, vault: Vault) -> list[_Found]:
    """Every fact the index holds, each with the entities it matched, in a stable order."""
    entities = _Entities.read(conn)
    found: dict[tuple[str, tuple[str, ...], str], _Found] = {}

    def note(fact: _Found, chunks: set[int]) -> None:
        part = fact.id or fact.alias
        key = (fact.rule, fact.entities, part.model_dump_json() if part is not None else "")
        kept = found.setdefault(key, fact)
        kept.chunks.update(chunks)
        kept.card = kept.card or fact.card

    for fact, chunks in _from_authors(conn, entities):
        note(fact, chunks)
    for fact, chunks in _from_engines(conn, vault, entities):
        note(fact, chunks)
    return sorted(found.values(), key=lambda f: (f.rule, f.entities, f.text))


# -- authors ----------------------------------------------------------------------------------------------------------


def _from_authors(conn: sqlite3.Connection, entities: _Entities) -> Iterator[tuple[_Found, set[int]]]:
    """The facts each address or id gives, from every name it is shown with: none from a shared sender."""
    shown: dict[tuple[str, str, str], dict[str, set[int]]] = {}
    sql = "SELECT id, source, authors FROM chunks WHERE authors != '[]' AND source IS NOT NULL"
    for chunk, source, authors in conn.execute(sql):
        for author in json.loads(authors):
            m = AUTHOR.match(author)
            name = TAIL.sub("", m.group("name")).strip() if m is not None else ""
            if m is None or not folded(name):
                continue
            ident = m.group("id")
            address = EMAIL.fullmatch(f"<{ident}>") is not None
            key = "email" if address else source
            value = ident.casefold() if address else ident
            shown.setdefault((key, value, "address" if address else "chat id"), {}).setdefault(name, set()).add(chunk)
    for (key, value, rule), names in sorted(shown.items()):
        if _shared(list(names)):
            continue
        holders = sorted(entities.ids.get((key, value.casefold()), set()))
        for name, chunks in sorted(names.items()):
            fact = _shown_name(name, holders, entities) if holders else _named(name, key, value, rule, entities)
            if fact is not None:
                yield fact, chunks


def _shared(names: Sequence[str]) -> bool:
    """Whether the names an address or id is shown with are more than one person's: two of them unalike."""
    words = [folded(n).split() for n in names]
    return any(not _alike(a, b) for a, b in combinations(words, 2))


def _alike(a: list[str], b: list[str]) -> bool:
    """Whether two names may be one person's: every word of the shorter stands for a word of the other, in any order
    (`Cleo T.` and `Cleo Test`, `Example, Ada` and `Ada Example`)."""
    short, long_ = sorted((a, b), key=len)
    return all(any(_stands_for(w, v) for v in long_) for w in short)


def _stands_for(w: str, v: str) -> bool:
    if w == v:
        return True
    if len(w) == 1 or len(v) == 1:
        return w[0] == v[0]
    return min(len(w), len(v)) >= PREFIX and (w.startswith(v) or v.startswith(w))


def _shown_name(name: str, holders: Sequence[str], entities: _Entities) -> _Found | None:
    """The name a system shows beside an id an entity lists: its alias, when it isn't one of its names yet, isn't
    another entity's, and is like one of its own."""
    key = folded(name)
    new = tuple(
        h
        for h in holders
        if key not in entities.names.get(h, set())
        and not entities.other(key, h)
        and any(_alike(key.split(), own.split()) for own in entities.names.get(h, set()))
    )
    if not new:
        return None
    return _Found("shown name", new, name, "typed", alias=AliasFields(text=name, kind="plain"))


def _named(name: str, key: str, value: str, rule: Rule, entities: _Entities) -> _Found | None:
    """The address or id of the entity a name names: a fact for a full name of two words or more, a card for a
    one-word one, which two people may share."""
    folded_name = folded(name)
    full = tuple(sorted(entities.full.get(folded_name, set())))
    one = tuple(sorted(entities.one.get(folded_name, set())))
    if not (full or one):
        return None
    return _Found(rule, full or one, name, "typed", id=IdFields(key=key, value=value), card=not full)


# -- two engines ------------------------------------------------------------------------------------------------------


def _from_engines(conn: sqlite3.Connection, vault: Vault, entities: _Entities) -> list[tuple[_Found, set[int]]]:
    """A spelling two engines agree on: capitalised alike in their transcripts' overlapping utterances of a recording,
    near one name or alias of an entity the recording mentions. One the miner's screens catch is a card."""
    sql = (
        "SELECT r.grp, r.engine, s.start_ms, s.end_ms, s.text FROM segments s JOIN records r ON r.rid = s.rid "
        "WHERE r.kind = 'transcript' AND r.engine IS NOT NULL ORDER BY r.grp, s.start_ms"
    )
    heard: dict[str, dict[str, list[Segment]]] = {}
    for grp, engine, start, end, text in conn.execute(sql):
        heard.setdefault(grp, {}).setdefault(engine, []).append((start, end, text))
    langs = [lang for lang in resolve(vault.config.owner.languages).languages if lang.cases is not None]
    out: list[tuple[_Found, set[int]]] = []
    for grp, engines in sorted(heard.items()):
        agreed: set[str] = set()
        for a, b in combinations(sorted(engines), 2):
            for text, other in _overlapping(engines[a], engines[b]):
                agreed |= _capitalised(text) & _capitalised(other)
        if not agreed:
            continue
        mentioned = {r[0] for r in conn.execute(
            "SELECT DISTINCT m.entity FROM mentions m JOIN chunks c ON c.id = m.chunk WHERE c.grp = ?", (grp,)
        )}  # fmt: skip
        chunks = list(conn.execute("SELECT id, text FROM chunks WHERE grp = ?", (grp,)))
        for spelling in sorted(agreed):
            word = fold(spelling)
            if word in entities.known:
                continue
            near = tuple(sorted({
                e for e, name, _ in entities.single
                if e in mentioned and name != word and max_distance(name) and within(name, max_distance(name), [word])
            }))  # fmt: skip
            if not near:
                continue
            others = entities.words.get(word, set()) - set(near)
            screened = word in _forms(near, entities, langs) or bool(others) or _common(conn, word)
            holding = {c for c, text in chunks if spelling in _capitalised(text)}
            alias = AliasFields(text=spelling, kind="asr")
            out.append((_Found("engines", near, spelling, "heard", alias=alias, card=screened), holding))
    return out


def _forms(near: Sequence[str], entities: _Entities, langs: Sequence[Language]) -> set[str]:
    """The case forms of the near entities' one-word names and aliases, folded, and those matching judged common: a
    case form is the name, never a misspelling of it."""
    out = {term for e in near for term in entities.common.get(e, set())}
    for e, _, text in entities.single:
        for lang in langs if e in near else ():
            cases = lang.cases
            out |= {fold(case) for case in cases(text.lower())} if cases is not None else set()
    return out


def _overlapping(a: list[Segment], b: list[Segment]) -> Iterator[tuple[str, str]]:
    """The utterances of two engines that overlap in time, in one pass over both, each sorted by its start."""
    first = 0
    for start, end, text in a:
        while first < len(b) and b[first][1] <= start:
            first += 1
        k = first
        while k < len(b) and b[k][0] < end:
            if b[k][1] > start:
                yield text, b[k][2]
            k += 1


def _capitalised(text: str) -> set[str]:
    return {w for w in WORD.findall(text) if w[:1].isupper()}


def _common(conn: sqlite3.Connection, word: str) -> bool:
    """The miner's lower-case rule for one word (DR-0465): written lower case in over a fifth of its uses away from a
    sentence's start, or capitalised only at one, across the chunks that hold it."""
    lower = mid = 0
    rows = conn.execute("SELECT c.text FROM chunks_fts f JOIN chunks c ON c.id = f.rowid WHERE chunks_fts MATCH ?", (
        phrase("folded", [word]),
    ))  # fmt: skip
    for (text,) in rows:
        for m in WORD.finditer(text):
            if fold(m.group(0)) != word:
                continue
            if m.group(0)[:1].islower():
                lower += 1
            elif not _starts(text, m.start()):
                mid += 1
    return mid == 0 or lower > COMMON_SHARE * (lower + mid)


def _starts(text: str, at: int) -> bool:
    """Whether a word starts a sentence: the text's or a line's first, or after `.`, `!`, `?`, or a turn's label."""
    before = text[:at].rstrip(" \t")
    return not before or before[-1] in "\n:" or bool(SENTENCE_END.fullmatch(before[-1]))


# -- the round's start ------------------------------------------------------------------------------------------------


def plan(vault: Vault) -> Plan:
    """The facts to apply and the cards, read from the log and the index beside the writer; nothing is written."""
    primary = vault.config.primary_machine
    if primary != vault.machine:
        return Plan(note=f"tier 1 runs on the primary machine, {primary}; this is {vault.machine}")
    read = curatelog.read_all(vault.root)
    if read.problems:
        return Plan(note="tier 1 didn't run: a line of the curate log doesn't read; docket curate log names it")
    log = curatelog.fold(read.lines)
    applied_before = {line.key for line in read.lines if line.by == "tier1"}
    try:
        with closing(open_index(vault)) as conn:
            found = find(conn, vault)
            where = {c: (grp, loc) for c, grp, loc in conn.execute("SELECT id, grp, locator FROM chunks")}
    except (DocketError, sqlite3.Error):
        return Plan(note="tier 1 didn't run: there is no index to read yet")
    planned: list[Planned] = []
    proposals: list[Proposal] = []
    for fact in found:
        change = Change(id=fact.id) if fact.id is not None else Change(alias=fact.alias)
        if fact.card or len(fact.entities) > 1:
            proposals += [
                Proposal(e, fact.text, fact.trust, frozenset(fact.chunks), alias=fact.alias, id=fact.id)
                for e in fact.entities
                if key_of(e, change) not in log.stoplist
            ]
            continue
        entity = next(iter(fact.entities))
        key = key_of(entity, change)
        if key in applied_before or key in log.stoplist:
            continue
        held = sorted(fact.chunks)
        evidence = Evidence(
            chunks=len(held),
            containers=len({where[c][0] for c in held if c in where}),
            locators=tuple(where[c][1] for c in held[:MAX_LOCATORS] if c in where),
        )
        planned.append(Planned(len(planned) + 1, entity, change, rule=fact.rule, evidence=evidence))
    # A card for a key a fact applies would ask about what is done already.
    keys = {key_of(p.entity, p.change) for p in planned}
    kept = [p for p in proposals if key_of(p.entity, Change(id=p.id) if p.id else Change(alias=p.alias)) not in keys]
    return Plan(tuple(planned), tuple(kept))


def apply(vault: Vault, found: Plan, *, via: Via = "cli") -> Tier1:
    """The plan's facts in one commit, on the writer: a key the log took since the plan is left out, and each fact
    the store refuses is skipped and listed, by its rule and entity only."""
    if found.note is not None:
        return Tier1(note=found.note)
    lines = curatelog.read_all(vault.root).lines
    taken = {line.key for line in lines if line.by == "tier1"} | curatelog.fold(lines).stoplist
    planned = [p for p in found.planned if key_of(p.entity, p.change) not in taken]
    by_n = {p.n: p for p in planned}
    skipped: list[str] = []

    def skip(problems: Sequence[str]) -> set[int]:
        out: set[int] = set()
        for problem in problems:
            # The store names each change it refuses by its place, never by its value.
            n, _, why = problem.removeprefix("change ").partition(": ")
            p = by_n[int(n)]
            skipped.append(f"{p.rule} for {p.entity}: {why}")
            out.add(p.n)
        return out

    refused = skip(screen(vault, planned))
    kept: list[Planned] = [p for p in planned if p.n not in refused]
    store = EntityStore(vault)
    while kept:
        try:
            return Tier1(store.apply(kept, by="tier1", via=via), tuple(skipped), found.proposals)
        except RefusedError as e:
            gone = skip(e.problems)
            kept = [p for p in kept if p.n not in gone]
    return Tier1(None, tuple(skipped), found.proposals)


def run(vault: Vault, *, via: Via = "cli") -> Tier1:
    """Plan and apply, in one call: the CLI's round start."""
    return apply(vault, plan(vault), via=via)
