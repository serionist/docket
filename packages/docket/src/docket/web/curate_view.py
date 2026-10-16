"""The Curate page's view (DR-0473): its URL, what one read gathers, and what the templates show.

Everything here reads: this machine's round, the curate log, the entity files and, for the open card only, its snippets,
cut from the index's chunks when the card is shown and never kept (DR-0468). Nothing of a card's snippets shows before
it is opened. Names are shown as they are, never redacted; a value a never-store list holds never shows, nor a snippet
that names one.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from starlette.datastructures import QueryParams

from docket.curate import batch, schedule
from docket.curate.batch import HIDDEN, Batch, Measure, Snippet, State, hides, id_hidden, shown_entity, shown_key
from docket.curate.propose import UNSEEN, Card
from docket.errors import DocketError
from docket.index.build import open_index
from docket.index.languages import resolve
from docket.vault import curatelog
from docket.vault.config import NeverStore
from docket.vault.curatelog import Line
from docket.vault.entities import ENTITY_TYPES, entity_rel, load_entities
from docket.vault.neverstore import listed
from docket.vault.vault import Vault

PATH = "/curate"
PANEL = "/curate/panel"
ROUND = "/curate/round"
DECIDE = "/curate/decide"
UNDO = "/curate/undo"
LIFT = "/curate/lift"
CARD = re.compile(r"^[0-9a-f]{6}$")
DONE = re.compile(
    r"^(?:(?P<act>accepted|rejected|edited)-(?P<card>[0-9a-f]{6})|(?P<what>undone|lifted)-(?P<n>\d{1,4})"
    r"|round-(?P<cards>\d{1,3}))$"
)
LOG_LINES = 20
# The forms matching left out, listed for curation; more than this is a list for the CLI.
DROPPED = 50
NO_FILE = "none"
TRUST = {UNSEEN: "other order, not seen"}
ADDS = {"name": "the name", "plain": "added as an alias", "asr": "added as an asr alias", "no": "found already"}
WHY_DROPPED = {"short": "under three letters", "common": "in too many chunks"}


class CurateStateError(DocketError):
    """A parameter of the Curate page's URL, or of a form it posts, that can't be read; the message names it."""


@dataclass(frozen=True)
class CurateState:
    """The Curate page's URL, in one canonical form: the open card, or the card open in its edit form, and what the
    last change did."""

    open: str | None = None
    edit: bool = False
    done: str | None = None

    @staticmethod
    def read(params: QueryParams | Mapping[str, str]) -> CurateState:
        def first(name: str) -> str | None:
            return (params.get(name) or "").strip() or None

        opened, edit = first("open"), first("edit")
        for name, value in (("open", opened), ("edit", edit)):
            if value is not None and not CARD.match(value):
                raise CurateStateError(f"{name} is a card's id, six hex characters, not {value!r}")
        if opened is not None and edit is not None and opened != edit:
            raise CurateStateError("edit names the open card")
        done = first("done")
        if done is not None and not DONE.match(done):
            raise CurateStateError(f"done is what the last change did, such as accepted-1a2b3c, not {done!r}")
        return CurateState(open=edit or opened, edit=edit is not None, done=done)

    def pairs(self, *, done: bool = False) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if self.open is not None:
            out.append(("edit" if self.edit else "open", self.open))
        out += [("done", self.done)] if done and self.done else []
        return out

    def url(self, path: str = PATH, *, done: bool = False) -> str:
        query = urlencode(self.pairs(done=done))
        return f"{path}?{query}" if query else path


def canonical(url: str) -> str | None:
    """A Curate page URL in its canonical form, or None for one that isn't the page's or doesn't read."""
    path, _, query = url.partition("?")
    if not (path.endswith(PATH) or path.endswith(PANEL)):
        return None
    try:
        return CurateState.read(QueryParams(query)).url()
    except DocketError:
        return None


# -- what one read gathers -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Opened:
    """The open card: its state, its snippets, cut now, and the hash of the entity file a decision writes."""

    card: Card
    state: State
    snippets: tuple[Snippet, ...]
    hash: str


@dataclass(frozen=True)
class Facts:
    tz: ZoneInfo
    now: datetime
    batch: Batch | None
    states: dict[str, State]
    lines: tuple[Line, ...]
    problems: tuple[str, ...]
    stoplist: frozenset[str]
    undone: dict[str, str]
    lists: NeverStore
    langs: tuple[str, ...]
    opened: Opened | None
    dropped: tuple[tuple[str, str, str], ...]
    # The log's lines a stopped write left: HEAD doesn't have them yet, and the next write commits them.
    unfinished: frozenset[str] = frozenset()


def file_hash(vault: Vault, card: Card) -> str:
    """The hash of the entity file a decision on the card writes: the entity's own for an alias card, the new one's
    place for a new card; `none` while there is no file there."""
    rel = entity_rel(card.type, card.entity)
    if card.kind == "alias":
        rel = next((e.path for e in load_entities(vault.root).entities if e.id == card.entity), rel)
    path = vault.root / rel
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else NO_FILE


def gather(vault: Vault, state: CurateState) -> Facts:
    """The round, the log, the cards' states and the open card; the index only for that card's snippets and the forms
    matching left out."""
    found = batch.load(vault)
    read = curatelog.read_all(vault.root)
    log = curatelog.fold(read.lines)
    states = batch.states(vault, found, read.lines) if found is not None else {}
    lists = vault.config.never_store
    opened: Opened | None = None
    dropped: tuple[tuple[str, str, str], ...] = ()
    try:
        with closing(open_index(vault)) as conn:
            card = found.card(state.open, False) if found is not None and state.open else None
            if card is not None:
                cut = [s for s in batch.snippets(conn, card) if not hides(s.text, card, lists)]
                opened = Opened(card, states[card.id], tuple(cut), file_hash(vault, card))
            dropped = _dropped(conn)
    except (DocketError, sqlite3.Error):
        # No index yet, or one being rebuilt: the cards still show, without snippets.
        card = found.card(state.open, False) if found is not None and state.open else None
        if card is not None:
            opened = Opened(card, states[card.id], (), file_hash(vault, card))
    return Facts(
        tz=ZoneInfo(vault.config.owner.time_zone),
        now=schedule.now_utc(),
        batch=found,
        states=states,
        lines=read.lines,
        problems=read.problems,
        stoplist=log.stoplist,
        undone=log.undone_by,
        lists=lists,
        langs=resolve(vault.config.owner.languages).codes,
        opened=opened,
        dropped=dropped,
        unfinished=frozenset(line.id for line in curatelog.unfinished(vault)),
    )


def _dropped(conn: sqlite3.Connection) -> tuple[tuple[str, str, str], ...]:
    """The forms matching left out as short or common, each entity's once (DR-0350): read only."""
    rows = conn.execute(
        "SELECT DISTINCT entity, term, how FROM alias_terms WHERE word = -1 AND how IN ('short', 'common') "
        "ORDER BY entity, term LIMIT ?",
        (DROPPED,),
    )
    return tuple((entity, term, how) for entity, term, how in rows)


# -- what the page shows ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SpellingView:
    text: str
    trust: str
    add: str
    chunks: int


@dataclass(frozen=True)
class CardRow:
    id: str
    n: int
    state: State
    kind: str
    title: str
    spellings: tuple[SpellingView, ...]
    missed: str
    url: str
    fragment: str
    open: bool


@dataclass(frozen=True)
class SnippetView:
    locator: str
    before: str
    mark: str
    after: str
    search: str


@dataclass(frozen=True)
class EditSpelling:
    i: int
    text: str
    trust: str
    checked: bool
    kind: str


@dataclass(frozen=True)
class EditForm:
    """The edit form, as the card proposes it or as the user typed it into a form the page refused."""

    new: bool
    types: tuple[str, ...]
    type: str
    id: str
    names: tuple[tuple[str, str], ...]
    spellings: tuple[EditSpelling, ...]
    role: str


@dataclass(frozen=True)
class Panel:
    id: str
    n: int
    new: bool
    # An id card is accepted or rejected as it is.
    editable: bool
    kind: str
    title: str
    state: State
    type: str
    entity: str
    spellings: tuple[SpellingView, ...]
    missed: str
    newest: str
    snippets: tuple[SnippetView, ...]
    hidden: tuple[tuple[str, str], ...]
    close_url: str
    close_fragment: str
    edit_url: str
    edit_fragment: str
    back_url: str
    back_fragment: str
    form: EditForm | None


@dataclass(frozen=True)
class LogRow:
    id: str
    at: datetime
    op: str
    entity: str
    part: str
    by: str
    via: str
    undo: bool
    undone_by: str | None
    unfinished: bool = False


@dataclass(frozen=True)
class Measures:
    rows: tuple[tuple[str, str, str], ...]


@dataclass
class View:
    lead: str
    due: str
    minutes: str
    round: str | None
    cards: list[CardRow] = field(default_factory=list[CardRow])
    left_out: str = ""
    applied: list[LogRow] = field(default_factory=list[LogRow])
    measures: Measures | None = None
    dropped: list[str] = field(default_factory=list[str])
    log: list[LogRow] = field(default_factory=list[LogRow])
    stoplist: list[str] = field(default_factory=list[str])
    problems: tuple[str, ...] = ()
    skipped: list[str] = field(default_factory=list[str])
    tier1: str | None = None
    done: str | None = None


def _s(n: int) -> str:
    return "" if n == 1 else "s"


def shown(text: str, lists: NeverStore) -> str:
    """A name or a spelling as the page shows it: hidden when a never-store list holds it."""
    return HIDDEN if listed(text, lists) is not None else text


def shown_id(card: Card, lists: NeverStore) -> str:
    """The id a card writes, hidden as `show` hides it: an id is a name's slug."""
    return HIDDEN if id_hidden(card, lists) else card.entity


def kind_of(card: Card, lists: NeverStore) -> str:
    if card.kind == "new":
        return f"New {card.type}"
    return f"{'Id' if card.kind == 'id' else 'Alias'} for {shown(card.name, lists)}"


def title_of(card: Card, lists: NeverStore) -> str:
    if card.kind == "new":
        return shown(card.name, lists)
    if card.fact is not None:
        return f"{card.fact[0]} {shown(card.fact[1], lists)}"
    return ", ".join(shown(s.text, lists) for s in card.adds())


def missed_of(card: Card) -> str:
    if card.missed_chunks:
        return (
            f"search misses it in {card.missed_chunks} chunk{_s(card.missed_chunks)} of {card.missed_containers} "
            f"place{_s(card.missed_containers)}"
        )
    return f"search finds it already, in {card.chunks} chunk{_s(card.chunks)}"


def _spellings(card: Card, lists: NeverStore) -> tuple[SpellingView, ...]:
    return tuple(
        SpellingView(
            text=shown(s.text, lists),
            trust=TRUST.get(s.trust, s.trust),
            add=ADDS[s.add],
            chunks=s.chunks,
        )
        for s in card.spellings
    )


def row(card: Card, state: State, lists: NeverStore, opened: str | None) -> CardRow:
    open_ = CurateState(open=card.id)
    return CardRow(
        id=card.id,
        n=card.n,
        state=state,
        kind=kind_of(card, lists),
        title=title_of(card, lists),
        spellings=_spellings(card, lists),
        missed=missed_of(card),
        url=open_.url(),
        fragment=open_.url(PANEL),
        open=card.id == opened,
    )


def snippet(s: Snippet) -> SnippetView:
    a, b = s.mark if s.mark is not None else (0, 0)
    return SnippetView(
        locator=s.locator,
        before=s.text[:a],
        mark=s.text[a:b],
        after=s.text[b:],
        search="/search?" + urlencode({"hit": s.locator}),
    )


def edit_form(card: Card, langs: tuple[str, ...], typed: QueryParams | None, lists: NeverStore) -> EditForm:
    """The form as the card proposes it, or with what the user typed; a never-store value hidden either way. The form
    posts its spellings by their place, so a hidden one is still ticked or not."""
    adds = [s for s in card.spellings if s.add in ("plain", "asr")]

    def given(name: str, proposed: str) -> str:
        return shown((typed.get(name) or "") if typed is not None else proposed, lists)

    kept = set(typed.getlist("keep")) if typed is not None else {str(i) for i in range(len(adds))}
    spellings = tuple(
        EditSpelling(
            i=i,
            text=shown(s.text, lists),
            trust=TRUST.get(s.trust, s.trust),
            checked=str(i) in kept,
            kind=given(f"kind.{i}", s.add),
        )
        for i, s in enumerate(adds)
    )
    names = tuple((code, given(f"name.{code}", card.name if code == card.lang else "")) for code in langs)
    return EditForm(
        new=card.kind == "new",
        types=ENTITY_TYPES,
        type=given("type", card.type),
        id=given("id", shown_id(card, lists)),
        names=names,
        spellings=spellings,
        role=given("role", ""),
    )


def panel(facts: Facts, state: CurateState, typed: QueryParams | None = None) -> Panel | None:
    opened = facts.opened
    if opened is None or facts.batch is None:
        return None
    card = opened.card
    closed, editing, shown = CurateState(), CurateState(open=card.id, edit=True), CurateState(open=card.id)
    newest = ""
    if card.newest:
        newest = f"{datetime.fromisoformat(card.newest.replace('Z', '+00:00')).astimezone(facts.tz):%d %b %Y}"
    return Panel(
        id=card.id,
        n=card.n,
        new=card.kind == "new",
        editable=card.kind != "id",
        kind=kind_of(card, facts.lists),
        title=title_of(card, facts.lists),
        state=opened.state,
        type=card.type,
        entity=shown_id(card, facts.lists),
        spellings=_spellings(card, facts.lists),
        missed=missed_of(card),
        newest=newest,
        snippets=tuple(snippet(s) for s in opened.snippets),
        hidden=(("round", facts.batch.round), ("card", card.id), ("hash", opened.hash)),
        close_url=closed.url(),
        close_fragment=closed.url(PANEL),
        edit_url=editing.url(),
        edit_fragment=editing.url(PANEL),
        back_url=shown.url(),
        back_fragment=shown.url(PANEL),
        form=edit_form(card, facts.langs, typed, facts.lists)
        if state.edit and opened.state == "open" and card.kind != "id"
        else None,
    )


def describe(line: Line, lists: NeverStore) -> str:
    """What one op changed, in a few words, a never-store value hidden."""

    change = line.change
    if change is None:
        if line.op == "undo":
            return f"taken back: {line.why}, {stop_key(line.key, lists)}" if line.why else f"undoes {line.of}"
        return stop_key(line.key, lists)
    if change.entity is not None:
        return f"{change.entity.type}, {'; '.join(shown(n, lists) for n in sorted(change.entity.name.values()))}"
    if change.alias is not None:
        return f"alias {shown(change.alias.text, lists)}{' (asr)' if change.alias.kind == 'asr' else ''}"
    if change.name is not None:
        return f"name {change.name.lang} {shown(change.name.text, lists)}"
    if change.id is not None:
        return f"id {change.id.key} {shown(change.id.value, lists)}"
    related = change.related
    return f"related {related.type} {shown_entity(related.target, lists)}" if related is not None else ""


def log_row(line: Line, facts: Facts) -> LogRow:
    return LogRow(
        id=line.id,
        at=line.at,
        op=line.op,
        entity=shown_entity(line.entity, facts.lists) or "-",
        part=describe(line, facts.lists),
        by=line.by,
        via=line.via,
        # No Undo on a hidden key: its line would commit the value again, and the store refuses it (DR-0464).
        undo=line.op in ("create", "add", "reject")
        and line.id not in facts.undone
        and not hidden(line.key, facts.lists),
        undone_by=facts.undone.get(line.id),
        unfinished=line.id in facts.unfinished,
    )


def hidden(key: str, lists: NeverStore) -> bool:
    return listed(curatelog.key_text(key), lists) is not None


def measures(before: Measure | None, after: Measure | None) -> Measures | None:
    if before is None:
        return None

    def pair(m: Measure | None, kind: str | None) -> str:
        if m is None:
            return ""
        got = m.all if kind is None else m.kinds.get(kind)
        return "" if got is None else f"{got[0]:.2f} / {got[1]:.2f}"

    kinds = sorted(set(before.kinds) | (set(after.kinds) if after is not None else set[str]()))
    rows = [("all", pair(before, None), pair(after, None)), *((k, pair(before, k), pair(after, k)) for k in kinds)]
    return Measures(tuple(rows))


def minutes_line(lines: tuple[Line, ...]) -> str:
    weeks = list(schedule.minutes(lines).values())[:3]
    if not weeks:
        return "no review time logged yet"
    spent = ", ".join(f"{m:.0f}" if m >= 1 else "<1" for m in weeks)
    return f"review {spent} min in the last {len(weeks)} week{_s(len(weeks))} with a round"


def due_line(facts: Facts) -> str:
    last = schedule.last_round(facts.lines, facts.batch.started if facts.batch is not None else None)
    if last is None:
        return "No round yet: start one when you have a few minutes."
    due = last + schedule.DUE
    if facts.now >= due:
        return f"A round is due: the last started {(facts.now - last).days} days ago."
    return f"The next round is due on {due.astimezone(facts.tz):%d %b}."


def build(facts: Facts, state: CurateState) -> View:
    b = facts.batch
    view = View(
        lead="No round on this machine yet.",
        due=due_line(facts),
        minutes=minutes_line(facts.lines),
        round=b.round if b is not None else None,
        problems=facts.problems,
        done=done_line(state.done),
    )
    if b is not None:
        decided = sum(s != "open" for s in facts.states.values())
        day = f"{b.started.astimezone(facts.tz):%d %b}"
        view.lead = f"Round of {day}: {len(b.cards)} card{_s(len(b.cards))}, {decided} decided."
        tier1 = [ln for ln in facts.lines if ln.by == "tier1" and ln.at >= b.started]
        if tier1:
            since = f"{b.started.astimezone(facts.tz):%d %b}"
            view.lead += f" {len(tier1)} change{_s(len(tier1))} applied by themselves since {since}."
        view.applied = [log_row(ln, facts) for ln in reversed(tier1)]
        view.cards = [row(c, facts.states[c.id], facts.lists, state.open) for c in b.cards]
        view.left_out = ", ".join(f"{n} {why}" for why, n in b.left_out.items() if n)
        view.measures = measures(b.before, b.after)
        view.skipped = b.skipped
        view.tier1 = b.tier1
    view.dropped = [f"{entity}: {term} is {WHY_DROPPED[how]}" for entity, term, how in facts.dropped]
    view.log = [log_row(ln, facts) for ln in reversed(facts.lines[-LOG_LINES:])]
    view.stoplist = sorted(facts.stoplist)
    return view


def stop_key(key: str, lists: NeverStore) -> str:
    """A key as the page shows it: the value it names hidden when a never-store list holds it."""
    return shown_key(key, lists)


def done_line(done: str | None) -> str | None:
    m = DONE.match(done or "")
    if m is None:
        return None
    if m.group("act") == "accepted":
        return f"Card {m.group('card')} accepted and logged; the log below undoes it."
    if m.group("act") == "rejected":
        return f"Card {m.group('card')} rejected: its name, and what only joined it, are on the stoplist."
    if m.group("act") == "edited":
        return f"Card {m.group('card')} accepted as you edited it; what you left out is rejected as its alias."
    if m.group("what") == "undone":
        n = int(m.group("n"))
        return f"Undone: {n} change{_s(n)} taken back and logged."
    if m.group("what") == "lifted":
        return "Lifted: curation may offer that spelling again."
    n = int(m.group("cards"))
    return f"A new round: {n} card{_s(n)}." if n else "A new round, with nothing to propose: every name is known."


def closed(state: CurateState) -> CurateState:
    return replace(state, open=None, edit=False)
