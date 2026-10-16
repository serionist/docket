"""Tier 1 (DR-0469, DR-0470): facts the index holds that need no judgement, applied by themselves when a round starts
on the vault's primary machine, before the miner, in one commit.

- address: an author written `Name <address>` gives `ids.email` to the one person or org whose name or plain alias
  the name folds to;
- chat id: an author written `Name <id>`, the id not an address, gives `ids.<source>` to that one entity;
- shown name: the name a system shows for an address or id an entity already lists, not yet one of its names or
  aliases, becomes its plain alias, a trailing `(...)` dropped;
- engines: one spelling, capitalised alike in two engines' transcripts on overlapping utterances of a recording,
  becomes an `asr` alias of the one entity the recording mentions that it is within matching's fuzzy distance of.

Authors only: the index keeps no recipients. A fact that matches two entities is a card for each, tier 2's; one that
matches none waits for the miner. Tier 1 never creates an entity, and never applies a key the log shows it applied
before, whatever happened to it since, nor one the stoplist holds (DR-0461). A fact the store refuses (a never-store
value, a redaction rule, a file it can't rewrite) is skipped and listed with the round; the rest go in. It doesn't run
on another machine, where two machines changing one entity file would conflict in git, nor while a line of the curate
log doesn't read (DR-0460).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass, field
from itertools import combinations

from docket.curate.propose import Proposal
from docket.errors import DocketError
from docket.index.build import open_index
from docket.index.matching import EMAIL, max_distance, within
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


@dataclass(frozen=True)
class Tier1:
    """What tier 1 did: its commit, the facts the store refused (named by rule and entity, never by value), the facts
    that matched two entities, for the miner's cards, and why it didn't run, when it didn't."""

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
    chunks: set[int] = field(default_factory=set[int])


@dataclass
class _Entities:
    """What the index holds of the entities: each one's names and aliases, folded; the people and orgs by a name or
    plain alias; who lists each id; the single words of names and aliases."""

    names: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    named: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    ids: dict[tuple[str, str], set[str]] = field(default_factory=dict[tuple[str, str], set[str]])
    words: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    known: set[str] = field(default_factory=set[str])

    @classmethod
    def read(cls, conn: sqlite3.Connection) -> _Entities:
        out = cls()
        sql = "SELECT a.entity, e.type, a.text, a.kind FROM aliases a JOIN entities e ON e.id = a.entity"
        for entity, kind, text, how in conn.execute(sql):
            name = folded(text)
            out.names.setdefault(entity, set()).add(name)
            if kind in PEOPLE and how in ("name", "plain"):
                out.named.setdefault(name, set()).add(entity)
            if name and " " not in name:
                out.words.append((entity, name))
        for entity, key, value in conn.execute("SELECT entity, key, value FROM external_ids"):
            out.ids.setdefault((key, value.casefold()), set()).add(entity)
        # What aliases hold already, as written or as a misspelling an asr alias matches, as the miner reads it.
        terms = "SELECT term FROM alias_terms WHERE col = 'folded' AND (how = 'written' OR (word >= 0 AND how = 'asr'))"
        out.known = {r[0] for r in conn.execute(terms)}
        return out


def find(conn: sqlite3.Connection) -> list[_Found]:
    """Every fact the index holds, each with the entities it matched, in a stable order."""
    entities = _Entities.read(conn)
    found: dict[tuple[str, tuple[str, ...], str], _Found] = {}

    def note(fact: _Found, chunks: set[int]) -> None:
        part = fact.id or fact.alias
        key = (fact.rule, fact.entities, part.model_dump_json() if part is not None else "")
        found.setdefault(key, fact).chunks.update(chunks)

    sql = "SELECT id, source, authors FROM chunks WHERE authors != '[]' AND source IS NOT NULL"
    for chunk, source, authors in conn.execute(sql):
        for author in json.loads(authors):
            for fact in _from_author(author, source, entities):
                note(fact, {chunk})
    for fact, chunks in _from_engines(conn, entities):
        note(fact, chunks)
    return sorted(found.values(), key=lambda f: (f.rule, f.entities, f.text))


def _from_author(author: str, source: str, entities: _Entities) -> list[_Found]:
    m = AUTHOR.match(author)
    if m is None or not m.group("name"):
        return []
    name, ident = m.group("name"), m.group("id")
    address = EMAIL.fullmatch(f"<{ident}>") is not None
    key = "email" if address else source
    holders = sorted(entities.ids.get((key, ident.casefold()), set()))
    if holders:
        # A shown name: the name a system shows beside an id an entity lists, when it isn't one of its names yet.
        shown = TAIL.sub("", name).strip()
        new = tuple(h for h in holders if folded(shown) not in entities.names.get(h, set()))
        if not shown or not folded(shown) or not new:
            return []
        return [_Found("shown name", new, shown, "typed", alias=AliasFields(text=shown, kind="plain"))]
    named = tuple(sorted(entities.named.get(folded(name), set())))
    if not named:
        return []
    rule = "address" if address else "chat id"
    value = ident.casefold() if address else ident
    return [_Found(rule, named, name, "typed", id=IdFields(key=key, value=value))]


def _from_engines(conn: sqlite3.Connection, entities: _Entities) -> list[tuple[_Found, set[int]]]:
    """A spelling two engines agree on: capitalised alike in their transcripts' overlapping utterances of a recording,
    near one name or alias of an entity the recording mentions."""
    sql = (
        "SELECT r.grp, r.engine, s.start_ms, s.end_ms, s.text FROM segments s JOIN records r ON r.rid = s.rid "
        "WHERE r.kind = 'transcript' AND r.engine IS NOT NULL ORDER BY r.grp, s.start_ms"
    )
    heard: dict[str, dict[str, list[tuple[int, int, str]]]] = {}
    for grp, engine, start, end, text in conn.execute(sql):
        heard.setdefault(grp, {}).setdefault(engine, []).append((start, end, text))
    out: list[tuple[_Found, set[int]]] = []
    for grp, engines in sorted(heard.items()):
        agreed: set[str] = set()
        for a, b in combinations(sorted(engines), 2):
            for start, end, text in engines[a]:
                for other_start, other_end, other in engines[b]:
                    if other_start < end and start < other_end:
                        agreed |= _capitalised(text) & _capitalised(other)
        if not agreed:
            continue
        mentioned = {r[0] for r in conn.execute(
            "SELECT DISTINCT m.entity FROM mentions m JOIN chunks c ON c.id = m.chunk WHERE c.grp = ?", (grp,)
        )}  # fmt: skip
        chunks = list(conn.execute("SELECT id, text FROM chunks WHERE grp = ?", (grp,)))
        for spelling in sorted(agreed):
            word = fold(spelling).lower()
            if word in entities.known:
                continue
            near = tuple(sorted({
                e for e, name in entities.words
                if e in mentioned and name != word and max_distance(name) and within(name, max_distance(name), [word])
            }))  # fmt: skip
            if near:
                holding = {c for c, text in chunks if spelling in _capitalised(text)}
                fact = _Found("engines", near, spelling, "heard", alias=AliasFields(text=spelling, kind="asr"))
                out.append((fact, holding))
    return out


def _capitalised(text: str) -> set[str]:
    return {w for w in WORD.findall(text) if w[:1].isupper()}


def run(vault: Vault, *, via: Via = "cli") -> Tier1:
    """Find the facts, apply each that matched one entity and isn't refused, in one commit; hand the rest on."""
    primary = vault.config.primary_machine
    if primary != vault.machine:
        return Tier1(note=f"tier 1 runs on the primary machine, {primary}; this is {vault.machine}")
    read = curatelog.read_all(vault.root)
    if read.problems:
        return Tier1(note="tier 1 didn't run: a line of the curate log doesn't read; docket curate log names it")
    log = curatelog.fold(read.lines)
    applied_before = {line.key for line in read.lines if line.by == "tier1"}
    try:
        with closing(open_index(vault)) as conn:
            found = find(conn)
            where = {c: (grp, loc) for c, grp, loc in conn.execute("SELECT id, grp, locator FROM chunks")}
    except (DocketError, sqlite3.Error):
        return Tier1(note="tier 1 didn't run: there is no index to read yet")
    planned: list[Planned] = []
    proposals: list[Proposal] = []
    for fact in found:
        change = Change(id=fact.id) if fact.id is not None else Change(alias=fact.alias)
        if len(fact.entities) > 1:
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
    written, skipped = _apply(vault, planned, via)
    return Tier1(written, tuple(skipped), tuple(proposals))


def _apply(vault: Vault, planned: Sequence[Planned], via: Via) -> tuple[Written | None, list[str]]:
    """The facts in one commit, each one the store refuses left out and listed, by its rule and entity only."""
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
    kept = [p for p in planned if p.n not in refused]
    store = EntityStore(vault)
    while kept:
        try:
            return store.apply(kept, by="tier1", via=via), skipped
        except RefusedError as e:
            gone = skip(e.problems)
            kept = [p for p in kept if p.n not in gone]
    return None, skipped
