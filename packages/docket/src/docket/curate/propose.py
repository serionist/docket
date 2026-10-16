"""The miner (DR-0465 to DR-0467): from the first pass's spellings to at most 15 cards, ranked by what search misses.

Tier 3 leaves out, never shown and counted by reason: a spelling under three letters or of digits only; a calendar
word, a language's name or a generic label's word; a single word written lower case in over a fifth of its uses away
from a sentence's start (or never capitalised but there); a run every word of which is such a word, or seen under
three times; a spelling an alias already holds as written or as a misspelling; one a never-store list matches; one in a
single container. A run whose first word is common and stands at a sentence's start gives its count to the rest of it.

Every label and the 300 spellings capitalised most often go on: each is found in the index's folded column, so a chat
message written in lower case counts as its, and they group where they meet:

- folded the same, or two words seen in both orders;
- a Hungarian case form joins the word it is a case of, whatever that word's trust, and a heard word within matching's
  fuzzy distance of a written or label one joins it, each only where the two meet in at least two places (a container,
  or a recording a doc covers); a case form never joins when it is a label or the first word of a written run;
- a single word joins the one run whose places hold most of its own.

A group that reaches an entity's name or alias, as a case form or within the fuzzy distance, where the entity is
mentioned, or a label whose first word is one person's, becomes an alias card for it when accepting it would add a
mention; the rest become new-entity cards, named by the run a single word joined or else the most trusted spelling.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal, cast

from pydantic import BaseModel

from docket.curate.spellings import GENERIC_WORDS, HEARD, TRUSTS, Scan, Seen, scan
from docket.index.languages import SNOWBALL, Language, code_of, resolve
from docket.index.matching import SHORT, max_distance, within
from docket.index.query import NameQuery
from docket.index.text import fold, phrase
from docket.vault.curatelog import Change, EntityFields, Folded, folded
from docket.vault.neverstore import listed
from docket.vault.processed import SLUG_MAX
from docket.vault.vault import Vault

MAX_CARDS = 15
# The spellings past tier 3 that go on to be found and grouped, most often capitalised first, besides every label: the
# rest wait for a later round, when the frequent ones are decided.
TOP = 300
MEET = 2
COMMON_SHARE = 0.2
RUN_SEEN = 3
EVIDENCE = 6
# Calendar words English capitalises, which the lower-case rule can't leave out; Hungarian writes them lower case.
CALENDAR = frozenset((
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
    "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "monday",
    "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "mon", "tue", "tues", "wed", "thu", "thur",
    "thurs", "fri", "sat", "sun",
))  # fmt: skip
LANGUAGE_NAMES = frozenset(SNOWBALL.values())
REASONS = ("one-off words", "rare phrases", "common words", "short", "known", "never-store", "rejected")

Add = Literal["name", "plain", "asr", "no"]
Role = Literal["same", "order", "case", "asr", "part"]


class CardSpelling(BaseModel):
    """A spelling on a card: its trust, what accepting does with it (the card's name, a plain or asr alias, or nothing,
    for a case form or a spelling the name holds), whether it stands on its own, and the chunks holding it."""

    text: str
    trust: str
    add: Add
    alone: bool
    chunks: int


class Card(BaseModel):
    id: str
    n: int
    kind: Literal["new", "alias"]
    key: str
    entity: str
    type: str
    name: str
    lang: str
    spellings: list[CardSpelling]
    chunks: int
    containers: int
    missed_chunks: int
    missed_containers: int
    newest: str
    # (locator, the spelling there), in the order to show.
    evidence: list[tuple[str, str]]

    def adds(self) -> list[CardSpelling]:
        return [s for s in self.spellings if s.add in ("plain", "asr")]


@dataclass
class Mined:
    cards: list[Card]
    left_out: Counter[str] = field(default_factory=Counter[str])


def card_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:6]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", folded(text)).strip("-")[:SLUG_MAX].strip("-")


# -- what the index says about chunks and entities --------------------------------------------------------------


@dataclass
class _Index:
    conn: sqlite3.Connection
    grp: dict[int, str] = field(default_factory=dict[int, str])
    ts: dict[int, str] = field(default_factory=dict[int, str])
    locator: dict[int, str] = field(default_factory=dict[int, str])
    places: dict[int, frozenset[str]] = field(default_factory=dict[int, frozenset[str]])
    counts: dict[str, int] | None = None

    @classmethod
    def read(cls, conn: sqlite3.Connection) -> _Index:
        out = cls(conn)
        covers: dict[str, set[str]] = {}
        sql = "SELECT dc.doc, r.grp FROM doc_covers dc JOIN records r ON r.rid = dc.rid"
        for doc, grp in conn.execute(sql):
            covers.setdefault(doc, set()).add(grp)
        for chunk, grp, ts, locator, doc in conn.execute("SELECT id, grp, ts, locator, doc FROM chunks"):
            out.grp[chunk], out.ts[chunk], out.locator[chunk] = grp, ts or "", locator
            out.places[chunk] = frozenset({grp, *covers.get(doc, ())})
        return out

    def holding(self, words: Sequence[str]) -> set[int]:
        rows = self.conn.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", (phrase("folded", words),))
        return {r[0] for r in rows}

    def where(self, chunks: Iterable[int]) -> set[str]:
        return {p for c in chunks for p in self.places[c]}

    def containers(self, chunks: Iterable[int]) -> set[str]:
        return {self.grp[c] for c in chunks}

    def count(self, word: str) -> int:
        """How often the index holds a word, folded, every use counted: read once, since the vocabulary view scans
        for each term it is asked."""
        if self.counts is None:
            rows = self.conn.execute("SELECT term, cnt FROM vocab WHERE col = 'folded'")
            self.counts = {term: int(cnt) for term, cnt in rows}
        return self.counts.get(word, 0)


@dataclass(frozen=True)
class _Alias:
    entity: str
    type: str
    text: str
    first: bool


def _aliases(conn: sqlite3.Connection) -> list[_Alias]:
    sql = "SELECT a.entity, e.type, a.text, a.kind FROM aliases a JOIN entities e ON e.id = a.entity ORDER BY 1, 2"
    return [_Alias(entity, kind, text, how == "name") for entity, kind, text, how in conn.execute(sql)]


def _known(conn: sqlite3.Connection) -> set[str]:
    """What aliases hold already: each one as written, and each misspelling an asr alias matches."""
    rows = conn.execute(
        "SELECT term FROM alias_terms WHERE col = 'folded' AND (how = 'written' OR (word >= 0 AND how = 'asr'))"
    )
    return {r[0] for r in rows}


def _authors(conn: sqlite3.Connection) -> set[str]:
    """The names authors are shown with (`Ada Example <ada@example.com>`), folded."""
    out: set[str] = set()
    for (listed_authors,) in conn.execute("SELECT DISTINCT authors FROM chunks WHERE authors != '[]'"):
        for author in json.loads(listed_authors):
            name = author.split("<", 1)[0].strip()
            if name and "<" in author:
                out.add(folded(name))
    return out


# -- tier 3 -------------------------------------------------------------------------------------------------------


class _Tier3:
    def __init__(self, scan_: Scan, index: _Index, vault: Vault, known: set[str]) -> None:
        self.scan = scan_
        self.index = index
        self.lists = vault.config.never_store
        self.known = known
        self.mid: Counter[str] = Counter()
        for s in scan_.seen.values():
            if len(s.words) == 1:
                self.mid[s.words[0]] += s.mid

    def common(self, word: str) -> bool:
        lower = max(self.index.count(word) - self.scan.cased[word], 0)
        return lower > COMMON_SHARE * (lower + self.mid[word]) or self.mid[word] == 0

    def trim(self) -> None:
        """A run whose first word is common, at a sentence's start, gives its count to the rest of it."""
        for s in [s for s in self.scan.seen.values() if len(s.words) > 1 and s.start and self.common(s.words[0])]:
            del self.scan.seen[s.text]
            rest = s.text.split(" ", 1)[1]
            if len(s.words) > 2:
                tail = self.scan.seen.setdefault(rest, Seen(rest, s.words[1:]))
                tail.mid += s.mid + s.start
                tail.trust = min(tail.trust, s.trust)
                tail.chunks |= s.chunks

    def reason(self, s: Seen) -> str | None:
        letters = "".join(s.words)
        if len(letters) < SHORT or letters.isdigit():
            return "short"
        single = len(s.words) == 1
        if not s.label and (
            any(w in CALENDAR or w in LANGUAGE_NAMES or w in GENERIC_WORDS for w in s.words)
            or (single and self.common(s.words[0]))
            or (not single and all(self.common(w) for w in s.words))
        ):
            return "common words"
        if not s.label and ((not single and s.mid + s.start < RUN_SEEN) or (single and not s.alone)):
            return "rare phrases"
        if " ".join(s.words) in self.known:
            return "known"
        if listed(s.text, self.lists):
            return "never-store"
        if len(self.index.containers(s.chunks)) < 2:
            return "one-off words" if single else "rare phrases"
        return None


# -- grouping -----------------------------------------------------------------------------------------------------


class _Groups:
    """Union-find over the spellings that went on, with how each joined its group."""

    def __init__(self, spellings: list[Seen], index: _Index) -> None:
        self.spellings = spellings
        self.parent = list(range(len(spellings)))
        self.role: dict[int, Role] = {}
        self.chunks = [s.chunks | index.holding(s.words) for s in spellings]
        self.places = [index.where(c) for c in self.chunks]

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return self.parent[i]

    def join(self, i: int, j: int, role: Role) -> None:
        self.role.setdefault(i, role)
        self.parent[self.find(i)] = self.find(j)

    def meet(self, i: int, j: int) -> int:
        return len(self.places[i] & self.places[j])

    def groups(self) -> list[list[int]]:
        out: dict[int, list[int]] = {}
        for i in range(len(self.spellings)):
            out.setdefault(self.find(i), []).append(i)
        return list(out.values())


def _case_langs(vault: Vault) -> list[Language]:
    return [lang for lang in resolve(vault.config.owner.languages).languages if lang.cases is not None]


def _group(g: _Groups, langs: list[Language]) -> None:
    sp = g.spellings
    by_words: dict[tuple[str, ...], int] = {}
    for i, s in enumerate(sp):
        if s.words in by_words:
            g.join(i, by_words[s.words], "same")
        else:
            by_words[s.words] = i
    for i, s in enumerate(sp):
        other = by_words.get(s.words[::-1])
        if len(s.words) == 2 and other is not None and other != i:
            g.join(i, other, "order")
    singles = [i for i, s in enumerate(sp) if len(s.words) == 1]
    written_heads = {sp[i].words[0] for i in range(len(sp)) if len(sp[i].words) > 1 and sp[i].written}
    cases: dict[str, list[int]] = {}
    for b in singles:
        for lang in langs:
            for case in lang.cases(sp[b].text.lower()) if lang.cases is not None else []:
                cases.setdefault(fold(case), []).append(b)
    for i in singles:
        if sp[i].label or sp[i].words[0] in written_heads:
            continue
        for b in cases.get(sp[i].words[0], []):
            if sp[b].words != sp[i].words and g.meet(i, b) >= MEET:
                g.join(i, b, "case")
                break
    heard: dict[str, list[int]] = {}
    for i in singles:
        if not sp[i].written and not sp[i].label:
            heard.setdefault(sp[i].words[0], []).append(i)
    vocab = sorted(heard)
    for w in singles:
        word = sp[w].words[0]
        if not (sp[w].written or sp[w].label) or not max_distance(word):
            continue
        for term, _ in within(word, max_distance(word), vocab):
            for h in heard[term]:
                if g.find(h) != g.find(w) and g.meet(h, w) >= MEET:
                    g.join(h, w, "asr")
    # A word that starts one run's group only joins it: two people may share a first name.
    starts: dict[str, set[int]] = {}
    for r in (i for i, s in enumerate(sp) if len(s.words) > 1):
        starts.setdefault(sp[r].words[0], set()).add(r)
    for i in singles:
        runs = starts.get(sp[i].words[0], set())
        if len({g.find(r) for r in runs}) == 1 and g.find(i) != g.find(next(iter(runs))):
            best = max(runs, key=lambda r: (g.meet(i, r), sp[r].text))
            if g.meet(i, best) >= MEET:
                g.join(i, best, "part")


# -- cards --------------------------------------------------------------------------------------------------------


class _Miner:
    def __init__(self, conn: sqlite3.Connection, vault: Vault, log: Folded) -> None:
        self.conn = conn
        self.vault = vault
        self.log = log
        self.index = _Index.read(conn)
        self.scan = scan(conn)
        self.aliases = _aliases(conn)
        self.taken = {r[0] for r in conn.execute("SELECT id FROM entities")}
        self.names = NameQuery(conn, vault)
        self.lang = next((c for c in (code_of(name) for name in vault.config.owner.languages) if c is not None), "en")
        self.mentions: dict[str, set[int]] = {}
        self.hungarian = "hu" in resolve(vault.config.owner.languages).codes

    def mentioned(self, entity: str) -> set[int]:
        if entity not in self.mentions:
            rows = self.conn.execute("SELECT chunk FROM mentions WHERE entity = ?", (entity,))
            self.mentions[entity] = {r[0] for r in rows}
        return self.mentions[entity]

    def new_stoplist(self) -> set[str]:
        """The new: keys rejected or undone, and those of each undone create, worked out from the index now: its name,
        and each alias no label and no written spelling holds on its own, unless lifted since (DR-0462)."""
        keys = {k for k in self.log.stoplist if k.startswith("new:-:")}
        alone = {" ".join(s.words) for s in self.scan.seen.values() if s.label or s.written}
        for create in self.log.undone_creates:
            # A create's line holds the entity it made: the log's reader checks that.
            fields = cast(EntityFields, cast(Change, create.change).entity)
            undone = self.log.by_id[self.log.undone_by[create.id]].at
            first = sorted(fields.name.items())[0][1]
            texts = [first, *(a.text for a in fields.aliases if folded(a.text) not in alone)]
            for key in (f"new:-:{folded(t)}" for t in texts):
                if not self.log.lifted_since(key, undone):
                    keys.add(key)
        return keys

    def run(self) -> Mined:
        tier3 = _Tier3(self.scan, self.index, self.vault, _known(self.conn))
        tier3.trim()
        left: Counter[str] = Counter()
        passed: list[Seen] = []
        for s in self.scan.seen.values():
            why = tier3.reason(s)
            if why is None:
                passed.append(s)
            else:
                left[why] += 1
        passed.sort(key=lambda s: (not s.label, -(s.mid + s.start), s.text))
        labels = sum(s.label for s in passed)
        g = _Groups(passed[: labels + TOP], self.index)
        _group(g, _case_langs(self.vault))
        stop = self.new_stoplist()
        authors = _authors(self.conn)
        cards: list[Card] = []
        for members in g.groups():
            entity = self.reached(g, members)
            card = (
                self.alias_card(g, members, entity)
                if entity is not None
                else self.new_card(g, members, stop, authors, left)
            )
            if card is not None:
                cards.append(card)
        cards.sort(key=_rank, reverse=True)
        cards = [c.model_copy(update={"n": n}) for n, c in enumerate(cards[:MAX_CARDS], start=1)]
        return Mined(cards, left)

    # -- an alias card --------------------------------------------------------------------------------------------

    def reached(self, g: _Groups, members: list[int]) -> str | None:
        """The entity a group reaches, as a case form or within the fuzzy distance of its name or alias where it is
        mentioned, or by a label's first word being one person's; the one it meets most."""
        best: tuple[int, str] | None = None
        heads = {s.words[0] for s in g.spellings if len(s.words) > 1 and s.written}
        for i in members:
            s = g.spellings[i]
            if len(s.words) != 1:
                continue
            word = s.words[0]
            # As in the joins, a label or a written run's first word is a name of its own, never a case form.
            cased = not (s.label or word in heads)
            for a in self.aliases:
                text = folded(a.text)
                near = " " not in text and (
                    (cased and word in {fold(c) for lang in _case_langs(self.vault) for c in _cases(lang, a.text)})
                    or bool(max_distance(text) and within(text, max_distance(text), [word]))
                )
                meet = len(g.places[i] & self.index.where(self.mentioned(a.entity))) if near else 0
                if meet >= MEET and (best is None or (meet, a.entity) > best):
                    best = (meet, a.entity)
        if best is not None:
            return best[1]
        for i in members:
            s = g.spellings[i]
            # A word that starts one entity's name of several words, where that entity is mentioned.
            firsts = {a.entity for a in self.aliases if _first_of_several(a.text) == s.words[0]}
            if len(s.words) == 1 and len(firsts) == 1:
                entity = next(iter(firsts))
                if len(g.places[i] & self.index.where(self.mentioned(entity))) >= MEET:
                    return entity
            if s.label:
                people = {
                    a.entity for a in self.aliases if a.type == "person" and folded(a.text).split()[0] == s.words[0]
                }
                if len(people) == 1:
                    return people.pop()
        return None

    def alias_card(self, g: _Groups, members: list[int], entity: str) -> Card | None:
        """A group that reaches an entity: a card when its aliases would add a mention, else none, and never a new
        entity."""
        spellings: list[CardSpelling] = []
        holding: set[int] = set()
        for i in members:
            s = g.spellings[i]
            add: Add = "no" if g.role.get(i) == "case" else "asr" if s.trust >= HEARD else "plain"
            if add != "no" and f"alias:{entity}:{folded(s.text)}" in self.log.stoplist:
                continue
            spellings.append(_spelling(s, add, g.chunks[i]))
            holding |= g.chunks[i] if add != "no" else set()
        if not any(s.add != "no" for s in spellings):
            return None
        missed = holding - self.mentioned(entity)
        if not missed:
            return None
        entity_type = next(a.type for a in self.aliases if a.entity == entity)
        name = next((a.text for a in self.aliases if a.entity == entity and a.first), entity)
        first = next(s.text for s in spellings if s.add != "no")
        key = f"alias:{entity}:{folded(first)}"
        return self._card(
            kind="alias",
            key=key,
            entity=entity,
            entity_type=entity_type,
            name=name,
            spellings=spellings,
            holding=holding,
            missed=missed,
            evidence=self.evidence(g, members, self.mentioned(entity), None),
        )

    # -- a new card -----------------------------------------------------------------------------------------------

    def order_of(self, head: Seen) -> Seen | None:
        """A two-word name written the other way round anywhere, as the first pass saw it, the most trusted first."""
        if len(head.words) != 2:
            return None
        found = [s for s in self.scan.seen.values() if s.words == head.words[::-1]]
        return min(found, key=lambda s: (s.trust, s.text), default=None)

    def new_card(
        self, g: _Groups, members: list[int], stop: set[str], authors: set[str], left: Counter[str]
    ) -> Card | None:
        kept = [i for i in members if f"new:-:{' '.join(g.spellings[i].words)}" not in stop]
        if not kept:
            left["rejected"] += 1
            return None
        sp = g.spellings
        runs = [i for i in kept if len(sp[i].words) > 1]
        joined_runs = [r for r in runs if any(g.role.get(i) == "part" and g.find(i) == g.find(r) for i in kept)]
        pool = joined_runs or [i for i in kept if g.role.get(i) not in ("case", "asr")] or kept
        head = min(pool, key=lambda i: (sp[i].trust, -len(self.index.containers(g.chunks[i])), sp[i].text))
        name = sp[head].text
        two = len(sp[head].words) == 2
        orders = {i for i in kept if two and sp[i].words == sp[head].words[::-1] and i != head}
        spellings: list[CardSpelling] = []
        holding: set[int] = set()
        for i in [head, *(i for i in kept if i != head)]:
            s = sp[i]
            add: Add = (
                "name"
                if i == head
                else "no"
                if s.words == sp[head].words or g.role.get(i) == "case"
                else "plain"
                if i in orders or s.trust < HEARD
                else "asr"
            )
            spellings.append(_spelling(s, add, g.chunks[i]))
            holding |= g.chunks[i]
        # A two-word name's other order counts however rarely it is written: tier 3's count is for noise, and the
        # order Hungarian writes a name in is the name.
        other = self.order_of(sp[head]) if not orders else None
        if other is not None:
            spellings.append(_spelling(other, "plain", other.chunks))
        found = {r[0] for r in self.conn.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", (self.names.expression(name),)
        )}  # fmt: skip
        person = any(sp[i].label for i in kept) or bool(orders) or other is not None or folded(name) in authors
        # A person's two-word name in a vault whose owner writes Hungarian, which puts the family name first: the other
        # order too, even unwritten, so a question in that order names them (no chunk holds it yet: 0).
        words = name.split()
        if person and not orders and other is None and len(words) == 2 and self.hungarian:
            reversed_ = f"{words[1]} {words[0]}"
            trust = TRUSTS[sp[head].trust]
            spellings.append(CardSpelling(text=reversed_, trust=trust, add="plain", alone=True, chunks=0))
        entity = slug(name) or "entity"
        n = 2
        while entity in self.taken:
            entity = f"{slug(name)}-{n}"
            n += 1
        self.taken.add(entity)
        key = f"new:-:{folded(name)}"
        kind = "person" if person else "term"
        return self._card(
            kind="new",
            key=key,
            entity=entity,
            entity_type=kind,
            name=name,
            spellings=spellings,
            holding=holding,
            missed=holding - found,
            evidence=self.evidence(g, kept, g.chunks[head], head),
        )

    def _card(
        self,
        *,
        kind: Literal["new", "alias"],
        key: str,
        entity: str,
        entity_type: str,
        name: str,
        spellings: list[CardSpelling],
        holding: set[int],
        missed: set[int],
        evidence: list[tuple[str, str]],
    ) -> Card:
        index = self.index
        return Card(
            id=card_id(key),
            n=0,
            kind=kind,
            key=key,
            entity=entity,
            type=entity_type,
            name=name,
            lang=self.lang,
            spellings=spellings,
            chunks=len(holding),
            containers=len(index.containers(holding)),
            missed_chunks=len(missed),
            missed_containers=len(index.containers(missed)),
            newest=max((index.ts[c] for c in holding), default=""),
            evidence=evidence,
        )

    def evidence(self, g: _Groups, members: list[int], named: set[int], head: int | None) -> list[tuple[str, str]]:
        """Up to six chunks to cut snippets from, one per container, newest first: a spelling where the name is also
        written, then a spelling alone, then the name (DR-0468)."""
        pool: list[tuple[int, str, int, str]] = []
        for i in members:
            text = g.spellings[i].text
            for c in g.chunks[i]:
                rank = 2 if i == head else 0 if c in named else 1
                pool.append((rank, self.index.ts[c], c, text))
        # Newest first within each rank: two stable sorts, the second by rank.
        pool.sort(key=lambda p: (p[1], -p[2]), reverse=True)
        pool.sort(key=lambda p: p[0])
        out: list[tuple[str, str]] = []
        used: set[str] = set()
        for _, _, c, text in pool:
            if self.index.grp[c] not in used and len(out) < EVIDENCE:
                used.add(self.index.grp[c])
                out.append((self.index.locator[c], text))
        return out


def _cases(lang: Language, text: str) -> list[str]:
    return lang.cases(text.lower()) if lang.cases is not None else []


def _spelling(s: Seen, add: Add, chunks: set[int]) -> CardSpelling:
    return CardSpelling(text=s.text, trust=TRUSTS[s.trust], add=add, alone=s.label or s.written, chunks=len(chunks))


def _rank(card: Card) -> tuple[int, int, int, str]:
    """By the containers search misses, then the chunks, then the newest; a card with no gap after every card with one,
    by its containers (DR-0467)."""
    if card.missed_containers:
        return (1, card.missed_containers, card.missed_chunks, card.newest)
    return (0, card.containers, 0, card.newest)


def mine(conn: sqlite3.Connection, vault: Vault, log: Folded) -> Mined:
    return _Miner(conn, vault, log).run()


def _first_of_several(text: str) -> str | None:
    """A name's first word, folded, when it has several."""
    words = folded(text).split()
    return words[0] if len(words) > 1 else None
