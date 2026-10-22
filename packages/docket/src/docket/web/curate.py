"""The Curate page's routes under /api/web (DR-0709, DR-0786): this machine's round, the open card, and the page's
four writes.

| Route | What |
|---|---|
| `GET /curate` | the round's cards, what tier 1 applied, the measures, the log, the stoplist; an old URL's `done` |
| `GET /curate/panel` | the open card with its snippets, cut now, and its edit form with `edit` |
| `POST /curate/round` | a new round: the index first, tier 1 on the primary machine, the miner, the batch |
| `POST /curate/decide` | accept, reject or edit a card, at the round and the entity file the page showed |
| `POST /curate/undo` | a logged op taken back |
| `POST /curate/lift` | a stoplisted key lifted, named by its hash |

A read never writes, and runs beside the writer. Each write is a JSON write with the browser's session only: the
Guard's same-origin check, the token refused, the body checked before any route reads it (DR-0709); it runs on the
service's writer thread through the batch's and the entity store's own calls, as the CLI does, and logs `via: page`.
A decision carries the round, the card and the hash of the entity file the page showed: a round that isn't this
machine's current one is refused, and under the lock a changed file answers 409, and the page reads the card again. A
round's miner reads the index beside the writer, after the index is brought up to date; only the batch's write goes on
the writer (DR-0468). A value the URL or the body can't hold, or a `DocketError`, answers 400 with its words; anything
else is logged as its type and frames and answers 500 with no detail.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.responses import Response

from docket.curate import batch, schedule, tier1
from docket.curate.batch import Batch
from docket.curate.propose import Card
from docket.errors import DocketError
from docket.service.state import Service
from docket.vault import curatelog
from docket.vault.curatelog import AliasFields, Change, EntityFields
from docket.vault.entitystore import EntityStore, Planned, RefusedError
from docket.vault.vault import Vault
from docket.web import curate_view
from docket.web.curate_view import CardPanel, CurateState, CurateStateError, CurateView, Facts
from docket.web.day_api import Refused, safely

CHANGED = "This entity's file changed since the page showed the card: nothing was written. Here it is as it stands now."
Act = Literal["accept", "reject", "edit"]
ACTS: tuple[Act, ...] = ("accept", "reject", "edit")


def key_id(key: str) -> str:
    """How a lift names a stoplisted key: its hash, so a value the key holds is never in the page."""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


class Lift(BaseModel):
    # The key as the page shows it, a never-store value hidden; its hash, which a lift names; and whether it lifts.
    shown: str
    id: str
    liftable: bool


class CurateRead(BaseModel):
    # The URL as the server writes it, which the app puts in the address bar as it is (DR-0704); never `done`.
    canonical: str
    view: CurateView
    lifts: list[Lift]
    # What a write did, for a URL from before the app that still names it.
    notice: str | None


class CuratePanelRead(BaseModel):
    canonical: str
    panel: CardPanel | None
    # An open card this round doesn't hold.
    missing: str | None


class Round(BaseModel):
    """A new round: nothing to say but that it is asked for."""

    model_config = ConfigDict(extra="forbid")


class Edited(BaseModel):
    """A card as the edit form says: the spellings kept, by their place, each plain or asr; for a new card its type,
    id, a name per language and role. The spellings left out are rejected as its aliases (DR-0461)."""

    model_config = ConfigDict(extra="forbid")

    keep: list[int] = []
    kinds: dict[str, str] = {}
    type: str = ""
    id: str = ""
    names: dict[str, str] = {}
    role: str = ""


class CurateDecide(BaseModel):
    """Accept, reject or edit one card, at the round and the entity file's hash the page showed it with."""

    model_config = ConfigDict(extra="forbid")

    act: str
    round: str
    card: str
    hash: str
    edit: Edited | None = None


class Undo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: str


class LiftKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # A stoplisted key's hash, as the page shows it.
    key: str


class Done(BaseModel):
    # What the write did, in the words the page says it with.
    notice: str


def lifts(facts: Facts) -> list[Lift]:
    # No Lift on a hidden key: its line would commit the value again, and the store refuses it (DR-0464).
    return [
        Lift(
            shown=curate_view.stop_key(k, facts.lists),
            id=key_id(k),
            liftable=not curate_view.hidden(k, facts.lists),
        )
        for k in sorted(facts.stoplist)
    ]


def router(service: Service) -> APIRouter:
    api = APIRouter()

    async def read(request: Request) -> CurateRead:
        asked = CurateState.read(request.query_params)
        listing = curate_view.closed(asked)
        facts = await asyncio.to_thread(lambda: curate_view.gather(service.vault(), listing))
        view = curate_view.build(facts)
        notice = curate_view.done_line(asked.done)
        return CurateRead(canonical=listing.url(), view=view, lifts=lifts(facts), notice=notice)

    async def read_panel(request: Request) -> CuratePanelRead:
        state = CurateState.read(request.query_params)
        facts = await asyncio.to_thread(lambda: curate_view.gather(service.vault(), state))
        found = curate_view.panel(facts, state)
        missing = "This round has no such card: the link is from an older round."
        gone = state.open is not None and found is None
        return CuratePanelRead(canonical=state.url(), panel=found, missing=missing if gone else None)

    async def new_round(_: Round) -> Done:
        # The index first; tier 1's facts found beside the writer and committed on it, then the index again; the
        # miner beside the writer; only the batch's write goes on it (DR-0468, DR-0470).
        await service.updater.fresh()
        begun = schedule.now_utc()
        found = await asyncio.to_thread(lambda: tier1.plan(service.vault()))
        ran = await service.writer.run(lambda: tier1.apply(service.vault(), found, via="page"))
        if ran.wrote:
            await service.updater.fresh()
        made = await asyncio.to_thread(lambda: batch.build(service.vault(), ran, started=begun))
        await service.writer.run(lambda: batch.save(service.vault(), made))
        return Done(notice=curate_view.done_line(f"round-{len(made.cards)}") or "")

    async def decide(body: CurateDecide) -> Response | Done:
        act, round_, card_id, shown = _decision(body)
        try:
            changed = await service.writer.run(
                lambda: _decide(service, act, round_=round_, card_id=card_id, shown=shown, edit=body.edit)
            )
        except DocketError as e:
            raise DocketError(_said(e)) from None
        if changed:
            return JSONResponse(Refused(error=CHANGED).model_dump(mode="json"), status_code=409)
        service.updater.poke()
        await _after(service, round_)
        word = {"accept": "accepted", "reject": "rejected", "edit": "edited"}[act]
        return Done(notice=curate_view.done_line(f"{word}-{card_id}") or "")

    async def undo(body: Undo) -> Done:
        op = body.op.strip()
        if not curatelog.OP_ID.match(op):
            raise CurateStateError(f"op is a logged op's id, as the log shows it, not {op!r}")
        written = await service.writer.run(lambda: _undo(service.vault(), op))
        service.updater.poke()
        return Done(notice=curate_view.done_line(f"undone-{len(written)}") or "")

    async def lift(body: LiftKey) -> Done:
        wanted = body.key.strip()
        await service.writer.run(lambda: _lift(service.vault(), wanted))
        return Done(notice=curate_view.done_line("lifted-1") or "")

    refusals: dict[int | str, dict[str, object]] = {400: {"model": Refused}}

    @api.get("/curate", response_model=CurateRead, responses=refusals)
    async def curate_read(request: Request) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: read(request))

    @api.get("/curate/panel", response_model=CuratePanelRead, responses=refusals)
    async def curate_panel(request: Request) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: read_panel(request))

    @api.post("/curate/round", response_model=Done, responses=refusals)
    async def curate_round(request: Request, body: Round) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: new_round(body))

    @api.post("/curate/decide", response_model=Done, responses={**refusals, 409: {"model": Refused}})
    async def curate_decide(request: Request, body: CurateDecide) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: decide(body))

    @api.post("/curate/undo", response_model=Done, responses=refusals)
    async def curate_undo(request: Request, body: Undo) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: undo(body))

    @api.post("/curate/lift", response_model=Done, responses=refusals)
    async def curate_lift(request: Request, body: LiftKey) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: lift(body))

    return api


def _decision(body: CurateDecide) -> tuple[Act, str, str, str]:
    found: list[Act] = [a for a in ACTS if a == body.act]
    if not found:
        raise CurateStateError(f"act is accept, reject or edit, not {body.act!r}")
    act: Act = found[0]
    round_ = body.round.strip()
    if not round_:
        raise CurateStateError("the decision names no round")
    card_id = body.card.strip()
    if not curate_view.CARD.match(card_id):
        raise CurateStateError(f"card is a card's id, six hex characters, not {card_id!r}")
    shown = body.hash.strip()
    if not shown:
        raise CurateStateError("the decision names no entity file's hash")
    if act == "edit" and body.edit is None:
        raise CurateStateError("an edit carries the card as edited")
    return act, round_, card_id, shown


def _decide(service: Service, act: Act, *, round_: str, card_id: str, shown: str, edit: Edited | None) -> bool:
    """Decide a card as the CLI does, if its entity file is still as the page showed it; True when it changed since.
    One vault and its one lock around the check and the write: the store's own calls take the same lock again."""
    vault = service.vault()
    with vault.lock:
        found = batch.load(vault)
        if found is None:
            raise DocketError("no round on this machine; start one")
        if found.round != round_:
            raise DocketError(f"round {round_} isn't this machine's current one, {found.round}: the page was older")
        card = found.card(card_id, False)
        if card is None:
            raise DocketError(f"no card {card_id} in round {found.round}")
        if curate_view.file_hash(vault, card) != shown:
            return True
        if act == "accept":
            batch.accept(vault, found, [card], via="page")
        elif act == "reject":
            batch.reject(vault, found, [card], via="page")
        else:
            _edit(vault, found, card, edit or Edited())
    return False


def _edit(vault: Vault, found: Batch, card: Card, edit: Edited) -> None:
    """The card as the edit form says: the spellings kept, each plain or asr; for a new card its type, id, a name per
    language and role. The spellings left out are rejected as its aliases (DR-0461)."""
    adds = [s for s in card.spellings if s.add in ("plain", "asr")]
    kept = sorted({i for i in edit.keep if 0 <= i < len(adds)})
    aliases: list[AliasFields] = []
    for i in kept:
        kind = edit.kinds.get(str(i), "plain")
        if kind not in ("plain", "asr"):
            raise CurateStateError(f"a spelling is plain or asr, not {kind!r}")
        aliases.append(AliasFields(text=adds[i].text, kind=kind))
    if card.kind == "alias":
        if not aliases:
            batch.reject(vault, found, [card], via="page")
            return
        planned = [Planned(card.n, card.entity, Change(alias=a), card=card.id) for a in aliases]
    else:
        names = {code.strip(): text.strip() for code, text in edit.names.items() if text.strip()}
        try:
            fields = EntityFields(
                id=edit.id.strip(),
                type=edit.type.strip(),
                name=names,
                aliases=tuple(aliases),
                role=edit.role.strip() or None,
            )
        except ValidationError as e:
            error = e.errors()[0]
            where = ".".join(str(p) for p in error["loc"]) or "the entity"
            raise CurateStateError(f"{where}: {error['msg'].removeprefix('Value error, ')}") from None
        planned = [Planned(card.n, fields.id, Change(entity=fields), card=card.id)]
    settled, unticked = batch.settle(vault, found, planned)
    EntityStore(vault).apply(settled, rejects=unticked, via="page")


def _undo(vault: Vault, op: str) -> tuple[curatelog.Line, ...]:
    found = batch.load(vault)
    return EntityStore(vault).undo([op], via="page", round_id=found.round if found is not None else None).lines


def _lift(vault: Vault, wanted: str) -> None:
    keys = [k for k in curatelog.fold(curatelog.read_all(vault.root).lines).stoplist if key_id(k) == wanted]
    if not keys:
        raise CurateStateError("that key isn't on the stoplist any more")
    EntityStore(vault).lift(keys, via="page")


async def _after(service: Service, round_: str) -> None:
    """Once a measured round's last card is decided, the question set again, after the index caught up; skipped while
    the index is still being built, for `docket curate status --eval` later."""
    vault = service.vault()
    found = batch.load(vault)
    if found is None or found.round != round_ or not await asyncio.to_thread(batch.wants_after, vault, found):
        return
    try:
        await service.updater.fresh()
    except DocketError:
        return
    try:
        measured = await asyncio.to_thread(batch.measure, vault)
    except DocketError:
        # The decision is written: a question file that doesn't read waits for `docket curate status --eval`.
        return

    def save() -> None:
        now = batch.load(vault)
        if now is not None and now.round == round_ and now.after is None:
            batch.save(vault, now.model_copy(update={"after": measured}))

    await service.writer.run(save)


def _said(error: DocketError) -> str:
    """A refusal in the page's words: the store names a change of a changes file by its place, which the page's one
    change doesn't need."""
    if isinstance(error, RefusedError):
        return "; ".join(re.sub(r"^change \d+: ", "", problem) for problem in error.problems)
    return str(error)
