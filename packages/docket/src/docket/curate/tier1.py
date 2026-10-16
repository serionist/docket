"""Tier 1 (DR-0469, DR-0470): facts the index holds that need no judgement, applied by themselves when a round starts
on the vault's primary machine, before the miner, in one commit.

- address: an author written `Name <address>` gives `ids.email` to the one person or org whose full name or plain
  alias, two words or more, the name folds to;
- chat id: an author written `Name <id>`, the id not an address, gives `ids.<source>` to that one entity;
- shown name: the name a system shows for an address or id an entity already lists, not yet one of its names, not
  another entity's, and one of its own shortened, never lengthened, becomes its plain alias, a trailing `(...)`
  dropped;
- engines: one spelling, capitalised alike in two engines' transcripts on overlapping utterances of a recording,
  becomes an `asr` alias of the one entity the recording mentions that it is within matching's fuzzy distance of.

An address or id shown with names that can't all be one person's is a shared sender, a service's notification address
or a team's: it gives nothing, and since the index keeps every chunk, it stays shared at every later round. An address
or id tier 1 gave before it looked shared, and no one undid, it takes back itself: an undo line `by: tier1` with the
fact's rule and `why: now shared`, which stoplists it. A fact that matches two entities, a one-word name, and a
spelling the miner's own screens catch (a common word or one only at a sentence's start, a case form of the name it is
near, a word of another entity's name) are cards, never facts.

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
from docket.vault.curatelog import MAX_LOCATORS, AliasFields, Change, Evidence, IdFields, Line, Via, folded, key_of
from docket.vault.entitystore import EntityStore, Planned, RefusedError, Written, screen
from docket.vault.vault import Vault

Rule = str
# An author as a system writes a person beside their address or id.
AUTHOR = re.compile(r"^\s*(?P<name>[^<>]*?)\s*<(?P<id>[^<>\s]+)>\s*$")
# What a system adds after a shown name: `Ada Example (Northwind)`.
TAIL = re.compile(r"\s*\([^()]*\)\s*$")
PEOPLE = ("person", "org")
# A shown name's word shortens an entity's word when it is it, its initial, or a start of it this long or longer.
PREFIX = 3
# Why tier 1 takes back an address or id it gave.
NOW_SHARED = "now shared"
Segment = tuple[int, int, str]


@dataclass(frozen=True)
class Plan:
    """What tier 1 found, worked out beside the writer: the changes to apply, its own lines to take back, the facts
    that are cards, and why it doesn't run, when it doesn't."""

    planned: tuple[Planned, ...] = ()
    proposals: tuple[Proposal, ...] = ()
    note: str | None = None
    take_back: tuple[Line, ...] = ()


@dataclass(frozen=True)
class Tier1:
    """What tier 1 did: its commit, the facts the store refused (named by rule and entity, never by value), the facts
    that are cards, for the miner, why it didn't run, when it didn't, and the commit taking back what it gave before
    the sender looked shared."""

    written: Written | None = None
    skipped: tuple[str, ...] = ()
    proposals: tuple[Proposal, ...] = ()
    note: str | None = None
    taken_back: Written | None = None

    @property
    def wrote(self) -> bool:
        return any(w is not None and bool(w.lines) for w in (self.written, self.taken_back))


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


@dataclass(frozen=True)
class Findings:
    """What the index holds: the facts, each with the entities it matched, and the shared senders, each an id's key
    and its value, casefolded."""

    facts: list[_Found]
    shared: frozenset[tuple[str, str]]


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


def find(conn: sqlite3.Connection, vault: Vault) -> Findings:
    """Every fact the index holds, each with the entities it matched, in a stable order, and the shared senders."""
    entities = _Entities.read(conn)
    shown = _shown(conn)
    shared = frozenset((key, value.casefold()) for (key, value, _), names in shown.items() if _shared(list(names)))
    found: dict[tuple[str, tuple[str, ...], str], _Found] = {}

    def note(fact: _Found, chunks: set[int]) -> None:
        part = fact.id or fact.alias
        key = (fact.rule, fact.entities, part.model_dump_json() if part is not None else "")
        kept = found.setdefault(key, fact)
        kept.chunks.update(chunks)
        kept.card = kept.card or fact.card

    for fact, chunks in _from_authors(shown, shared, entities):
        note(fact, chunks)
    for fact, chunks in _from_engines(conn, vault, entities):
        note(fact, chunks)
    return Findings(sorted(found.values(), key=lambda f: (f.rule, f.entities, f.text)), shared)


# -- authors ----------------------------------------------------------------------------------------------------------

Shown = dict[tuple[str, str, Rule], dict[str, set[int]]]


def _shown(conn: sqlite3.Connection) -> Shown:
    """Each address or id authors are written with, by its id's key, its value and its rule, and every name it is
    shown with, each with the chunks it is in."""
    shown: Shown = {}
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
    return shown


def _from_authors(
    shown: Shown, shared: frozenset[tuple[str, str]], entities: _Entities
) -> Iterator[tuple[_Found, set[int]]]:
    """The facts each address or id gives, from every name it is shown with: none from a shared sender."""
    for (key, value, rule), names in sorted(shown.items()):
        if (key, value.casefold()) in shared:
            continue
        holders = sorted(entities.ids.get((key, value.casefold()), set()))
        for name, chunks in sorted(names.items()):
            fact = _shown_name(name, holders, entities) if holders else _named(name, key, value, rule, entities)
            if fact is not None:
                yield fact, chunks


def _shared(names: Sequence[str]) -> bool:
    """Whether the names an address or id is shown with are more than one person's: two of them not one person's."""
    words = [folded(n).split() for n in names]
    return any(not _one_person(a, b) for a, b in combinations(words, 2))


def _one_person(a: list[str], b: list[str]) -> bool:
    """Whether two names are one person's: every word of the shorter is a word of the other, its initial, or has it as
    its initial, in any order (`Example, Ada`, `A. Example` and `Ada Example`). A word that only starts another
    (`Ann` and `Anna`) may be someone else's: a shared sender then, a miss rather than a wrong fact."""
    short, long_ = sorted((a, b), key=len)
    return all(any(w == v or _initial(w, v) or _initial(v, w) for v in long_) for w in short)


def _initial(w: str, v: str) -> bool:
    return len(w) == 1 and v.startswith(w)


def _shortens(shown: list[str], own: list[str]) -> bool:
    """Whether a shown name is an entity's own, shortened: each of its words is a word of the own name, its initial,
    or a start of it of three letters or more, in any order (`Cleo T.` for `Cleo Test`); never one longer than the
    word it stands for (`Gáborné` for `Gábor`, `Adam` for `Ada`)."""
    return all(any(w == v or (v.startswith(w) and (len(w) == 1 or len(w) >= PREFIX)) for v in own) for w in shown)


def _shown_name(name: str, holders: Sequence[str], entities: _Entities) -> _Found | None:
    """The name a system shows beside an id an entity lists: its alias, when it isn't one of its names yet, isn't
    another entity's, and is one of its own shortened."""
    key = folded(name)
    new = tuple(
        h
        for h in holders
        if key not in entities.names.get(h, set())
        and not entities.other(key, h)
        and any(_shortens(key.split(), own.split()) for own in entities.names.get(h, set()))
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
            findings = find(conn, vault)
            where = {c: (grp, loc) for c, grp, loc in conn.execute("SELECT id, grp, locator FROM chunks")}
    except (DocketError, sqlite3.Error):
        return Plan(note="tier 1 didn't run: there is no index to read yet")
    # What it gave from a sender that looked like one person's then and looks shared now, if no one undid it.
    take_back = tuple(
        line
        for line in read.lines
        if line.by == "tier1"
        and line.op == "add"
        and line.id not in log.undone_by
        and line.change is not None
        and line.change.id is not None
        and (line.change.id.key, line.change.id.value.casefold()) in findings.shared
    )
    planned: list[Planned] = []
    proposals: list[Proposal] = []
    for fact in findings.facts:
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
    return Plan(tuple(planned), tuple(kept), take_back=take_back)


def apply(vault: Vault, found: Plan, *, via: Via = "cli") -> Tier1:
    """On the writer: first what the plan takes back, in one commit, then its facts, in another. A line undone or a
    key the log took since the plan is left out, and each the store refuses is skipped and listed, by its rule and
    entity only."""
    if found.note is not None:
        return Tier1(note=found.note)
    lines = curatelog.read_all(vault.root).lines
    log = curatelog.fold(lines)
    skipped: list[str] = []
    store = EntityStore(vault)
    back = _take_back(store, [ln for ln in found.take_back if ln.id not in log.undone_by], skipped, via)
    taken = {line.key for line in lines if line.by == "tier1"} | log.stoplist
    planned = [p for p in found.planned if key_of(p.entity, p.change) not in taken]
    by_n = {p.n: p for p in planned}

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
    while kept:
        try:
            written = store.apply(kept, by="tier1", via=via)
            return Tier1(written, tuple(skipped), found.proposals, taken_back=back)
        except RefusedError as e:
            gone = skip(e.problems)
            kept = [p for p in kept if p.n not in gone]
    return Tier1(None, tuple(skipped), found.proposals, taken_back=back)


def _take_back(store: EntityStore, lines: list[Line], skipped: list[str], via: Via) -> Written | None:
    """Undo tier 1's own lines in one commit, each `by: tier1` with its rule and why; one the store refuses (a value a
    never-store list now holds, a file it can't rewrite) is skipped and listed, by its rule and entity only."""
    by_id = {line.id: line for line in lines}
    while by_id:
        try:
            return store.undo(list(by_id), via=via, by="tier1", why=NOW_SHARED)
        except RefusedError as e:
            for problem in e.problems:
                # The store names each op it refuses by its id, never by its value.
                op_id, _, why = problem.partition(": ")
                line = by_id.pop(op_id)
                skipped.append(f"taking back {line.rule} for {line.entity}: {why}")
    return None


def run(vault: Vault, *, via: Via = "cli") -> Tier1:
    """Plan and apply, in one call: the CLI's round start."""
    return apply(vault, plan(vault), via=via)
