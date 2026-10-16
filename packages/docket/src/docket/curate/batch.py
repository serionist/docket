"""A curate round (DR-0468, DR-0474, DR-0475): its batch of cards, kept per machine in the vault's private folder, what
the log says was decided on each, how long the rounds took, and the user's own questions before and after.

`<private>/curate/batch.json` holds the round's id, when it started, its cards, what tier 3 left out by reason, and the
question set's numbers before and after. It is per machine, never committed and disposable: decisions live in the log,
and a card's state is read from it, by the card's id, which is its key's, so a decision made on another machine closes
it here too. A decision names a card by its id, or by its round and number; one naming a round that isn't this
machine's current one is refused.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from docket import index, questions, userconfig
from docket.curate import tier1
from docket.curate.propose import Card, mine
from docket.curate.schedule import DUE, last_round, minutes, now_utc, round_id
from docket.curate.tier1 import Tier1
from docket.errors import DocketError
from docket.index.build import open_index
from docket.vault import curatelog
from docket.vault.config import NeverStore
from docket.vault.curatelog import AliasFields, Change, EntityFields, IdFields, Line, Via, folded
from docket.vault.entities import load_entities
from docket.vault.entitystore import EntityStore, Planned, Reject, Written
from docket.vault.home import make_private
from docket.vault.neverstore import listed
from docket.vault.staging import write_replacing
from docket.vault.vault import Vault

FILE = "curate/batch.json"
SNIPPETS = 3
ID_LENGTH = 6
WIDTH = 80
HIDDEN = "(on a never-store list)"

State = Literal["open", "accepted", "rejected", "done"]


class Measure(BaseModel):
    """The user's question set at one moment: recall at 10 and MRR, over all and per kind."""

    at: datetime
    all: tuple[float, float]
    kinds: dict[str, tuple[float, float]]


class Batch(BaseModel):
    schema_version: Literal[1] = 1
    round: str
    started: datetime
    cards: list[Card]
    left_out: dict[str, int]
    before: Measure | None = None
    after: Measure | None = None
    # Tier 1's facts the store refused, by rule and entity, and why tier 1 didn't run, when it didn't (DR-0470).
    skipped: list[str] = []
    tier1: str | None = None

    def card(self, ref: str, numbered: bool) -> Card | None:
        if numbered:
            return next((c for c in self.cards if str(c.n) == ref), None)
        return next((c for c in self.cards if c.id == ref), None)


def batch_path(vault: Vault) -> Path:
    return vault.home.private / FILE


def load(vault: Vault) -> Batch | None:
    """This machine's current round, or None: a batch that is gone or doesn't read is none, since it is disposable."""
    try:
        return Batch.model_validate_json(batch_path(vault).read_bytes())
    except (OSError, ValidationError):
        return None


def save(vault: Vault, batch: Batch) -> None:
    path = batch_path(vault)
    make_private(vault.home.git_dir, path.parent)
    write_replacing(vault.home, path, batch.model_dump_json(indent=1), "curate-batch")


# -- a round ------------------------------------------------------------------------------------------------------


def start(vault: Vault, *, via: Via = "cli") -> Batch:
    """Start a round: the index brought up to date, tier 1's facts committed on the primary machine and the index
    updated again, the miner over it, the question set measured, the batch kept (DR-0468, DR-0470)."""
    index.update(vault)
    begun = now_utc()
    ran = tier1.run(vault, via=via)
    if ran.wrote:
        index.update(vault)
    batch = build(vault, ran, started=begun)
    save(vault, batch)
    return batch


def build(vault: Vault, ran: Tier1 | None = None, *, started: datetime | None = None) -> Batch:
    """A new round from the index as it is, with tier 1's facts that matched two entities as cards: the miner over it
    and the question set measured, nothing written. The service brings the index up to date first, runs this beside
    the writer, and only the save on it (DR-0468). A round starts when tier 1 did, so its lines are this round's."""
    log = curatelog.fold(curatelog.read_all(vault.root).lines)
    with closing(open_index(vault)) as conn:
        mined = mine(conn, vault, log, ran.proposals if ran is not None else ())
    now = started or now_utc()
    return Batch(
        round=round_id(now, vault.machine),
        started=now,
        cards=mined.cards,
        left_out=dict(mined.left_out),
        before=measure(vault),
        skipped=list(ran.skipped) if ran is not None else [],
        tier1=ran.note if ran is not None else None,
    )


def measure(vault: Vault) -> Measure | None:
    """The question set `curate.questions` names, through keyword search, so a round's two measures compare; None when
    it names none (DR-0475)."""
    path = userconfig.curate_questions()
    if path is None:
        return None
    asked = questions.load(path, ZoneInfo(vault.config.owner.time_zone))
    report = questions.run(vault, asked)
    kinds = {kind: (score.recall, score.mrr) for kind, score in report.kinds.items()}
    return Measure(at=now_utc(), all=(report.all.recall, report.all.mrr), kinds=kinds)


def states(vault: Vault, batch: Batch, lines: Sequence[Line]) -> dict[str, State]:
    """Each card's state: accepted by a line naming it and not undone, rejected by one while the stoplist holds the
    card's own key (lifting it opens the card again), done when the files hold its change already (a hand edit, another
    machine's), open otherwise."""
    log = curatelog.fold(lines)
    held: dict[str, set[str]] = {}
    ids: dict[str, set[tuple[str, str]]] = {}
    for e in load_entities(vault.root).entities:
        held[e.id] = {folded(t) for t in (*e.names.values(), *(a.text for a in e.aliases))}
        ids[e.id] = {(key, value.casefold()) for key, values in e.ids.items() for value in values}
    every = set[str]().union(*held.values())
    out: dict[str, State] = {}
    for card in batch.cards:
        named = [line for line in lines if line.card == card.id and line.id not in log.undone_by]
        ops = {line.op for line in named if line.op != "reject" or card.key in log.stoplist}
        ops &= {"create", "add", "reject"}
        if ops - {"reject"}:
            out[card.id] = "accepted"
        elif ops:
            out[card.id] = "rejected"
        elif (
            (card.kind == "new" and folded(card.name) in every)
            or (card.kind == "alias" and all(folded(s.text) in held.get(card.entity, set()) for s in card.adds()))
            or (card.fact is not None and (card.fact[0], card.fact[1].casefold()) in ids.get(card.entity, set()))
        ):
            out[card.id] = "done"
        else:
            out[card.id] = "open"
    return out


def cards_for(batch: Batch | None, refs: Sequence[str], round_: str | None) -> tuple[Batch, list[Card]]:
    """The cards a decision names, by id or by the round and number; a round that isn't this machine's current one is
    refused, so a number read off an old list never decides another card."""
    if batch is None:
        raise DocketError("no round on this machine yet; start one with docket curate round")
    if round_ is not None and round_ != batch.round:
        current = batch.round
        raise DocketError(f"round {round_} isn't this machine's current one, {current}; docket curate show lists it")
    cards: list[Card] = []
    for ref in refs:
        # An id is six hex characters, which may all be digits; a card's number is at most two.
        numbered = ref.isdigit() and len(ref) < ID_LENGTH
        if numbered and round_ is None:
            raise DocketError(f"a card's number needs its round: --round {batch.round}")
        card = batch.card(ref, numbered)
        if card is None:
            raise DocketError(f"no card {ref} in round {batch.round}")
        cards.append(card)
    return batch, cards


def _open(vault: Vault, batch: Batch, cards: Sequence[Card]) -> None:
    now = states(vault, batch, curatelog.read_all(vault.root).lines)
    for card in cards:
        if now[card.id] != "open":
            raise DocketError(f"card {card.id} is {now[card.id]} already")


def _aliases(card: Card) -> tuple[AliasFields, ...]:
    return tuple(AliasFields(text=s.text, kind="asr" if s.add == "asr" else "plain") for s in card.adds())


def accept(
    vault: Vault,
    batch: Batch,
    cards: Sequence[Card],
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    via: Via = "cli",
) -> Written:
    """Accept cards as they are: a new entity with the card's name and aliases, or an entity's new aliases; --type and
    --id change one new-entity card's guesses."""
    if (entity_type or entity_id) and (len(cards) != 1 or cards[0].kind != "new"):
        raise DocketError("--type and --id are for one new-entity card")
    _open(vault, batch, cards)
    planned: list[Planned] = []
    for card in cards:
        if card.kind == "alias":
            planned += [Planned(card.n, card.entity, Change(alias=a), batch.round, card.id) for a in _aliases(card)]
            continue
        if card.fact is not None:
            fact = IdFields(key=card.fact[0], value=card.fact[1])
            planned.append(Planned(card.n, card.entity, Change(id=fact), batch.round, card.id))
            continue
        try:
            fields = EntityFields(
                id=entity_id or card.entity,
                type=entity_type or card.type,
                name={card.lang: card.name},
                aliases=_aliases(card),
            )
        except ValidationError as e:
            raise DocketError(f"--type or --id: {e.errors()[0]['msg'].removeprefix('Value error, ')}") from None
        planned.append(Planned(card.n, fields.id, Change(entity=fields), batch.round, card.id))
    return EntityStore(vault).apply(planned, via=via)


def rejects(card: Card, round_: str) -> list[Reject]:
    """What rejecting a card stoplists (DR-0461): a new card's name and the spellings found only through a join, never a
    label or a written name that stands on its own; an alias card's every alias."""
    if card.kind == "alias":
        return [
            Reject(f"alias:{card.entity}:{folded(s.text)}", s.text, card.entity, round_, card.id) for s in card.adds()
        ]
    if card.fact is not None:
        return [Reject(card.key, card.fact[1], card.entity, round_, card.id)]
    joined = [s for s in card.spellings if s.add != "name" and not s.alone]
    return [
        Reject(card.key, card.name, None, round_, card.id),
        *(Reject(f"new:-:{folded(s.text)}", s.text, None, round_, card.id) for s in joined),
    ]


def reject(vault: Vault, batch: Batch, cards: Sequence[Card], *, via: Via = "cli") -> Written:
    _open(vault, batch, cards)
    return EntityStore(vault).reject([r for card in cards for r in rejects(card, batch.round)], via=via)


def settle(vault: Vault, batch: Batch | None, planned: Sequence[Planned]) -> tuple[list[Planned], list[Reject]]:
    """A changes file's parts that name a card, given the round, and the card's spellings the edit left out, as a name
    or an alias, each rejected as that entity's alias, so it stays a candidate elsewhere (DR-0461)."""
    named = [p for p in planned if p.card is not None]
    if not named:
        return list(planned), []
    batch, cards = cards_for(batch, list(dict.fromkeys(p.card for p in named if p.card is not None)), None)
    for card in cards:
        if card.kind == "id":
            raise DocketError(f"card {card.id} gives an id: accept or reject it as it is")
    _open(vault, batch, cards)
    round_ = batch.round
    out = [Planned(p.n, p.entity, p.change, round_ if p.card else None, p.card) for p in planned]
    unticked: list[Reject] = []
    for card in cards:
        mine_ = [p for p in out if p.card == card.id]
        kept = {folded(text) for p in mine_ for text in _texts(p.change)}
        entity = mine_[0].entity
        unticked += [
            Reject(f"alias:{entity}:{folded(s.text)}", s.text, entity, round_, card.id)
            for s in card.adds()
            if folded(s.text) not in kept
        ]
    return out, unticked


def _texts(change: Change) -> list[str]:
    """The names and aliases a change writes: an edit may move a spelling from the aliases to a name."""
    if change.entity is not None:
        return [*change.entity.name.values(), *(a.text for a in change.entity.aliases)]
    return [part.text for part in (change.alias, change.name) if part is not None]


def wants_after(vault: Vault, batch: Batch) -> bool:
    """Whether a measured round has its last card decided and no measure after yet."""
    if batch.before is None or batch.after is not None:
        return False
    return "open" not in states(vault, batch, curatelog.read_all(vault.root).lines).values()


def after(vault: Vault, batch: Batch) -> Batch:
    """Once a measured round's last card is decided, the question set again, after the index caught up."""
    if not wants_after(vault, batch):
        return batch
    index.update(vault)
    batch = batch.model_copy(update={"after": measure(vault)})
    save(vault, batch)
    return batch


# -- what a round shows -------------------------------------------------------------------------------------------


class Snippet(BaseModel):
    locator: str
    text: str
    # Where the spelling is in the text, when it is there as written.
    mark: tuple[int, int] | None = None


class CardView(BaseModel):
    card: Card
    state: State
    snippets: list[Snippet]


class BatchView(BaseModel):
    round: str
    started: datetime
    cards: list[CardView]
    left_out: dict[str, int]
    # What tier 1 applied since the round started, as logged but for each value a never-store list holds now (DR-0501).
    applied: list[Line]
    skipped: list[str] = []
    tier1: str | None = None
    # What tier 1 took back since the round started: each undo's id, entity, why and key, a never-store value hidden.
    taken_back: list[str] = []
    # The applied lines a stopped write left: HEAD doesn't have them yet, and the next write commits them.
    unfinished: list[str] = []
    before: Measure | None = None
    after: Measure | None = None


def cut(text: str, spelling: str, locator: str) -> Snippet:
    """About 160 characters of a chunk around a spelling, on one line, the spelling marked where it is as written."""
    flat = " ".join(text.split())
    m = re.search(re.escape(spelling), flat, re.IGNORECASE)
    if m is None:
        return Snippet(locator=locator, text=flat[: 2 * WIDTH])
    a, b = max(m.start() - WIDTH, 0), min(m.end() + WIDTH, len(flat))
    return Snippet(locator=locator, text=flat[a:b], mark=(m.start() - a, m.end() - a))


def snippets(conn: sqlite3.Connection, card: Card) -> list[Snippet]:
    """Cut from the index's chunks when the card is shown, never kept (DR-0468); a chunk the index no longer holds is
    passed over."""
    out: list[Snippet] = []
    for locator, spelling in card.evidence:
        row = conn.execute("SELECT text FROM chunks WHERE locator = ? LIMIT 1", (locator,)).fetchone()
        if row is not None and len(out) < SNIPPETS:
            out.append(cut(row[0], spelling, locator))
    return out


def view(vault: Vault, batch: Batch) -> BatchView:
    """The round as the CLI and the skill show it: each card with its state and snippets, and what tier 1 and other
    writers applied since the round started without a card."""
    index.update(vault)
    lines = curatelog.read_all(vault.root).lines
    now = states(vault, batch, lines)
    by_tier1 = [ln for ln in lines if ln.at >= batch.started and ln.by == "tier1"]
    lists = vault.config.never_store
    applied = [hidden_line(ln, lists) for ln in by_tier1 if ln.op != "undo"]
    unfinished = {ln.id for ln in curatelog.unfinished(vault)}
    with closing(open_index(vault)) as conn:
        cards = [CardView(card=c, state=now[c.id], snippets=snippets(conn, c)) for c in batch.cards]
    return BatchView(
        round=batch.round,
        started=batch.started,
        cards=cards,
        left_out=batch.left_out,
        applied=applied,
        skipped=batch.skipped,
        tier1=batch.tier1,
        taken_back=[
            f"{ln.id}  {ln.entity or '-'}  {ln.why}, {shown_key(ln.key, lists)}" for ln in by_tier1 if ln.op == "undo"
        ],
        unfinished=[ln.id for ln in by_tier1 if ln.id in unfinished],
        before=batch.before,
        after=batch.after,
    )


def shown_key(key: str, lists: NeverStore) -> str:
    """A key as the CLI and the page show it: the value it names hidden when a never-store list holds it."""
    text = curatelog.key_text(key)
    return key[: len(key) - len(text)] + HIDDEN if text and listed(text, lists) is not None else key


def hidden_line(line: Line, lists: NeverStore) -> Line:
    """A logged line as `show` and its JSON give it: each value a never-store list holds now hidden, in its key and in
    its change, as the Curate page hides them; the rest as logged. A list can gain a value after tier 1 applied it. The
    copy is for showing only, never written, so it skips the checks HIDDEN would fail."""

    def shown(text: str) -> str:
        return HIDDEN if listed(text, lists) is not None else text

    update: dict[str, object] = {"key": shown_key(line.key, lists)}
    change = line.change
    if change is None:
        return line.model_copy(update=update)
    part: dict[str, object] = {}
    if change.alias is not None:
        part["alias"] = change.alias.model_copy(update={"text": shown(change.alias.text)})
    if change.name is not None:
        part["name"] = change.name.model_copy(update={"text": shown(change.name.text)})
    if change.id is not None:
        part["id"] = change.id.model_copy(update={"value": shown(change.id.value)})
    if change.entity is not None:
        e = change.entity
        part["entity"] = e.model_copy(
            update={
                "name": {lang: shown(text) for lang, text in e.name.items()},
                "aliases": tuple(a.model_copy(update={"text": shown(a.text)}) for a in e.aliases),
                "ids": {key: tuple(shown(v) for v in values) for key, values in e.ids.items()},
                "role": shown(e.role) if e.role else e.role,
                "note": shown(e.note) if e.note else e.note,
            }
        )
    update["change"] = change.model_copy(update=part)
    return line.model_copy(update=update)


# -- the measures -------------------------------------------------------------------------------------------------


class Status(BaseModel):
    round: str | None
    cards: int
    open: int
    due: bool
    last: datetime | None
    minutes: dict[str, float]
    stoplist: int
    questions: str | None
    before: Measure | None = None
    after: Measure | None = None


def status(vault: Vault, *, again: bool = False) -> Status:
    """Due or not, this machine's round, the minutes, the stoplist, and the question set; with `again`, the question set
    measured now as the round's after."""
    batch = load(vault)
    lines = curatelog.read_all(vault.root).lines
    if again and batch is not None:
        index.update(vault)
        batch = batch.model_copy(update={"after": measure(vault)})
        save(vault, batch)
    now = states(vault, batch, lines) if batch is not None else {}
    last = last_round(lines, batch.started if batch is not None else None)
    path = userconfig.curate_questions()
    return Status(
        round=batch.round if batch is not None else None,
        cards=len(now),
        open=sum(state == "open" for state in now.values()),
        due=last is None or now_utc() - last >= DUE,
        last=last,
        minutes=minutes(lines),
        stoplist=len(curatelog.fold(lines).stoplist),
        questions=str(path) if path is not None else None,
        before=batch.before if batch is not None else None,
        after=batch.after if batch is not None else None,
    )
