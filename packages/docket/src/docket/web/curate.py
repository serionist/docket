"""The Curate page: `/curate` is always a whole page and `/curate/panel` always the open card; starting a round,
deciding a card, undo and lift are its writes (DR-0473).

`GET` never writes. Each write is a plain form post the Guard checks (DR-0152), run on the service's writer thread and
answered with a 303 to the page, as the Staging page's are (DR-0381). A decision carries the round, the card and the
hash of the entity file the page showed: a round that isn't this machine's current one is refused, and under the lock a
changed file refuses it too, the card shown as it stands. A round's miner reads the index beside the writer, after the
index is brought up to date; only the batch's write goes on the writer (DR-0468). Every log line says `via: page`.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import ValidationError
from starlette.datastructures import QueryParams
from starlette.responses import RedirectResponse, Response

from docket.curate import batch, schedule, tier1
from docket.curate.batch import Batch
from docket.curate.propose import Card
from docket.errors import DocketError
from docket.service.state import Service
from docket.vault import curatelog
from docket.vault.curatelog import AliasFields, Change, EntityFields
from docket.vault.entitystore import EntityStore, Planned, RefusedError
from docket.vault.vault import Vault
from docket.web import curate_view, display, templating
from docket.web.curate_view import PANEL, CurateState, CurateStateError, Facts
from docket.web.shell import shell

CHANGED = "This entity's file changed since the page showed the card: nothing was written. Here it is as it stands now."
Act = Literal["accept", "reject", "edit"]


def key_id(key: str) -> str:
    """How a lift names a stoplisted key: its hash, so a value the key holds is never in the page."""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _context(facts: Facts, state: CurateState, typed: QueryParams | None = None) -> dict[str, Any]:
    view = curate_view.build(facts, state)
    # No Lift on a hidden key: its line would commit the value again, and the store refuses it (DR-0464).
    lifts = [
        (curate_view.stop_key(k, facts.lists), key_id(k), not curate_view.hidden(k, facts.lists))
        for k in sorted(facts.stoplist)
    ]
    panel = curate_view.panel(facts, state, typed)
    missing = state.open is not None and panel is None
    return {"view": view, "lifts": lifts, "panel": panel, "missing": missing, "state": state, "tz": facts.tz} | {
        "problem": "",
        "changed": "",
        "close_url": curate_view.closed(state).url(),
        "close_fragment": curate_view.closed(state).url(PANEL),
    }


def _empty(state: CurateState, problem: str) -> dict[str, Any]:
    return {
        "view": None,
        "lifts": [],
        "panel": None,
        "missing": False,
        "state": state,
        "tz": None,
        "problem": problem,
        "changed": "",
        "close_url": curate_view.PATH,
        "close_fragment": PANEL,
    }


def router(service: Service) -> APIRouter:
    web = APIRouter()

    async def whole(
        request: Request, state: CurateState, *, status: int = 200, typed: QueryParams | None = None, **extra: str
    ) -> Response:
        """The whole page for a state, read beside the writer: a read writes nothing. A `DocketError` is the page's
        own error."""
        context = shell(service, request, "Curate", current="curate")
        try:
            facts = await asyncio.to_thread(lambda: curate_view.gather(service.vault(), state))
        except DocketError as e:
            page = context | _empty(state, str(e))
            return templating.page("curate/page.html", page, status=400, referrer=templating.POSTS_FORMS)
        page = context | _context(facts, state, typed) | extra
        return templating.page("curate/page.html", page, status=status, referrer=templating.POSTS_FORMS)

    def failed_page(request: Request) -> Response:
        return templating.page("error.html", shell(None, request, "Something failed", current="curate"), status=500)

    @web.get("/curate", include_in_schema=False)
    async def curate_page(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            try:
                state = CurateState.read(request.query_params)
            except DocketError as e:
                context = shell(service, request, "Curate", current="curate") | _empty(CurateState(), str(e))
                return templating.page("curate/page.html", context, status=400, referrer=templating.POSTS_FORMS)
            return await whole(request, state)

        return await templating.safely(request, "/curate", answer, lambda: failed_page(request))

    @web.get("/curate/panel", include_in_schema=False)
    async def curate_panel(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            try:
                state = CurateState.read(request.query_params)
                facts = await asyncio.to_thread(lambda: curate_view.gather(service.vault(), state))
            except DocketError as e:
                return _panel_error(str(e), 400)
            context = _context(facts, state)
            headers = templating.history(request, state.url(), curate_view.canonical)
            return templating.page("curate/_panel.html", context, headers=headers, referrer=templating.POSTS_FORMS)

        return await templating.safely(request, PANEL, answer, lambda: _panel_error(display.FAILED, 500))

    @web.post("/curate/round", include_in_schema=False)
    async def curate_round(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            form = await templating.read_form(request)
            if isinstance(form, Response):
                return form
            try:
                # The index first; tier 1's facts found beside the writer and committed on it, then the index
                # again; the miner beside the writer; only the batch's write goes on it (DR-0468, DR-0470).
                await service.updater.fresh()
                begun = schedule.now_utc()
                found = await asyncio.to_thread(lambda: tier1.plan(service.vault()))
                ran = await service.writer.run(lambda: tier1.apply(service.vault(), found, via="page"))
                if ran.wrote:
                    await service.updater.fresh()
                made = await asyncio.to_thread(lambda: batch.build(service.vault(), ran, started=begun))
                await service.writer.run(lambda: batch.save(service.vault(), made))
            except DocketError as e:
                return await whole(request, CurateState(), status=400, problem=str(e))
            done = CurateState(done=f"round-{len(made.cards)}")
            return RedirectResponse(done.url(done=True), status_code=303)

        return await templating.safely(request, "/curate/round", answer, lambda: failed_page(request))

    @web.post("/curate/decide", include_in_schema=False)
    async def curate_decide(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            form = await templating.read_form(request)
            if isinstance(form, Response):
                return form
            try:
                act, round_, card_id, shown = _decision(form)
            except DocketError as e:
                return await whole(request, CurateState(), status=400, problem=str(e))
            opened = CurateState(open=card_id, edit=act == "edit")
            try:
                changed = await service.writer.run(
                    lambda: _decide(service, act, round_=round_, card_id=card_id, shown=shown, form=form)
                )
            except DocketError as e:
                # A refused edit: the form again, as typed, naming why in the page's words.
                if act == "edit":
                    return await whole(request, opened, status=400, typed=form, problem=_said(e))
                return await whole(request, CurateState(open=card_id), status=400, problem=_said(e))
            if changed:
                return await whole(request, CurateState(open=card_id), status=409, changed=CHANGED)
            service.updater.poke()
            await _after(service, round_)
            word = {"accept": "accepted", "reject": "rejected", "edit": "edited"}[act]
            done = CurateState(done=f"{word}-{card_id}")
            return RedirectResponse(done.url(done=True), status_code=303)

        return await templating.safely(request, "/curate/decide", answer, lambda: failed_page(request))

    @web.post("/curate/undo", include_in_schema=False)
    async def curate_undo(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            form = await templating.read_form(request)
            if isinstance(form, Response):
                return form
            op = (form.get("op") or "").strip()
            try:
                if not curatelog.OP_ID.match(op):
                    raise CurateStateError(f"op is a logged op's id, as the log shows it, not {op!r}")
                written = await service.writer.run(lambda: _undo(service.vault(), op))
            except DocketError as e:
                return await whole(request, CurateState(), status=400, problem=str(e))
            service.updater.poke()
            done = CurateState(done=f"undone-{len(written)}")
            return RedirectResponse(done.url(done=True), status_code=303)

        return await templating.safely(request, "/curate/undo", answer, lambda: failed_page(request))

    @web.post("/curate/lift", include_in_schema=False)
    async def curate_lift(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            form = await templating.read_form(request)
            if isinstance(form, Response):
                return form
            wanted = (form.get("key") or "").strip()
            try:
                await service.writer.run(lambda: _lift(service.vault(), wanted))
            except DocketError as e:
                return await whole(request, CurateState(), status=400, problem=str(e))
            done = CurateState(done="lifted-1")
            return RedirectResponse(done.url(done=True), status_code=303)

        return await templating.safely(request, "/curate/lift", answer, lambda: failed_page(request))

    return web


def _decision(form: QueryParams) -> tuple[Act, str, str, str]:
    act = form.get("act") or ""
    if act not in ("accept", "reject", "edit"):
        raise CurateStateError(f"act is accept, reject or edit, not {act!r}")
    round_ = (form.get("round") or "").strip()
    if not round_:
        raise CurateStateError("the form names no round")
    card_id = (form.get("card") or "").strip()
    if not curate_view.CARD.match(card_id):
        raise CurateStateError(f"card is a card's id, six hex characters, not {card_id!r}")
    shown = (form.get("hash") or "").strip()
    if not shown:
        raise CurateStateError("the form names no entity file's hash")
    return act, round_, card_id, shown


def _decide(service: Service, act: Act, *, round_: str, card_id: str, shown: str, form: QueryParams) -> bool:
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
            _edit(vault, found, card, form)
    return False


def _edit(vault: Vault, found: Batch, card: Card, form: QueryParams) -> None:
    """The card as the edit form says: the spellings ticked, each plain or asr; for a new card its type, id, a name per
    language and role. The spellings left out are rejected as its aliases (DR-0461)."""
    adds = [s for s in card.spellings if s.add in ("plain", "asr")]
    ticked = sorted({int(v) for v in form.getlist("keep") if v.isascii() and v.isdigit() and int(v) < len(adds)})
    aliases: list[AliasFields] = []
    for i in ticked:
        kind = form.get(f"kind.{i}") or "plain"
        if kind not in ("plain", "asr"):
            raise CurateStateError(f"a spelling is plain or asr, not {kind!r}")
        aliases.append(AliasFields(text=adds[i].text, kind=kind))
    if card.kind == "alias":
        if not aliases:
            batch.reject(vault, found, [card], via="page")
            return
        planned = [Planned(card.n, card.entity, Change(alias=a), card=card.id) for a in aliases]
    else:
        typed = {code: (form.get(f"name.{code}") or "").strip() for code in _langs(form)}
        names = {code: text for code, text in typed.items() if text}
        try:
            fields = EntityFields(
                id=(form.get("id") or "").strip(),
                type=(form.get("type") or "").strip(),
                name=names,
                aliases=tuple(aliases),
                role=(form.get("role") or "").strip() or None,
            )
        except ValidationError as e:
            error = e.errors()[0]
            where = ".".join(str(p) for p in error["loc"]) or "the entity"
            raise CurateStateError(f"{where}: {error['msg'].removeprefix('Value error, ')}") from None
        planned = [Planned(card.n, fields.id, Change(entity=fields), card=card.id)]
    settled, unticked = batch.settle(vault, found, planned)
    EntityStore(vault).apply(settled, rejects=unticked, via="page")


def _langs(form: QueryParams) -> list[str]:
    return sorted({key.removeprefix("name.") for key in form if key.startswith("name.")})


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


def _panel_error(message: str, status: int) -> Response:
    context = _empty(CurateState(), message) | {"error": message}
    return templating.page("curate/_panel.html", context, status=status, referrer=templating.POSTS_FORMS)
