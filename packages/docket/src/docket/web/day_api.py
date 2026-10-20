"""The day pages' JSON routes under /api/web (DR-0709 to DR-0711), the Ledger's first: its list, the open item's panel,
a text material in the viewer and the poll's version check; then the writes, each a JSON body in and a JSON answer
out: a button's write, a field's save and a material opened on the owner's desktop.

| Route | What |
|---|---|
| `GET /ledger` | the list, read with the page's query string as it is, and the canonical URL to write back |
| `GET /ledger/panel` | the item `open` names, or why no panel opens |
| `GET /ledger/text` | the text material `text` names, at most its first 256 KB |
| `GET /ledger/changed` | whether the op files moved since the version `v` |
| `POST /ledger/write` | one batch, by user, via page; a bulk through a dry run; the notice with its Undo |
| `POST /ledger/save` | a field saved on change: the row, the panel and the version after it |
| `POST /ledger/open` | a material opened with its app, or shown in its folder |

A value the URL or the body can't hold, and the writer's refusal, answer 400 with their words as `error`, an add
refused as a look-alike with the items it looks like; anything else is logged as its type and frames and answers 500
with no detail.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.responses import Response

from docket.errors import DocketError
from docket.ledger import ops, views
from docket.ledger.store import RefusedError
from docket.ledger.views import LedgerRead
from docket.service.state import Service
from docket.vault.vault import Vault
from docket.web import day, day_view
from docket.web.day_state import DayState, DayStateError, ref_of
from docket.web.day_view import ItemPanel, LedgerList, Notice, Row
from docket.web.display import FAILED

log = logging.getLogger("docket.service.web")

# A write's body: a few dozen keys at most, or the ticked rows of a long list. Anything bigger isn't a page's.
MAX_BODY = 256 * 1024
JSON = "application/json"


class Refused(BaseModel):
    """Why a read or a write was refused, in the writer's or the reader's own words; an add refused as a look-alike
    names the open items it looks like, so the page can offer Add anyway (DR-0710)."""

    error: str
    alikes: list[Row] = []


class Filters(BaseModel):
    """The list's own filters as the canonical URL holds them, for the page's filter bar."""

    q: str
    stream: str
    kind: str
    who: str
    k: int
    state: str
    group: str
    archived: bool


class Ledger(BaseModel):
    """The Ledger's list (DR-0666), as the page shows it, with what its bars need."""

    # The URL as the server writes it, which the app puts in the address bar as it is (DR-0704).
    canonical: str
    # The op files' version, taken before the read: what the poll compares (DR-0711).
    version: str
    today: str
    list: LedgerList
    filters: Filters
    kinds: list[str]
    # The act bar's verdicts as no row holds them, and its snooze choices.
    verdicts: list[day_view.Button]
    snooze: list[day_view.Snooze]
    tomorrow: str
    # What the write `done` names did, for a URL that still carries it.
    notice: Notice | None


class Panel(BaseModel):
    canonical: str
    panel: ItemPanel | None
    # Why no panel opens, such as an item that doesn't stand or a meeting's number.
    missing: str


class Viewer(BaseModel):
    ref: str
    text: str
    # Whether the text is only its first 256 KB.
    cut: bool


class Changed(BaseModel):
    changed: bool
    version: str


class Wrote(BaseModel):
    """What a write did: its notice, with Undo or Redo by its batch, the rows a bulk skipped, where the selection goes,
    an add's new item, and the version under the write's own lock, so the page's poll doesn't refetch for it."""

    notice: Notice
    done: str | None
    skipped: list[str]
    at: str | None
    added: str | None
    version: str


class Saved(BaseModel):
    """A field saved: the item's row and panel after it, and the version, so nothing else is refetched (DR-0710)."""

    row: Row
    panel: ItemPanel
    version: str


class Opened(BaseModel):
    notice: Notice


class Open(BaseModel):
    page: str = "ledger"
    item: str
    ref: str
    reveal: bool = False


def refused(words: str, alikes: list[Row] | None = None) -> JSONResponse:
    return JSONResponse(Refused(error=words, alikes=alikes or []).model_dump(mode="json"), status_code=400)


async def json_only(request: Request) -> None:
    """A write takes a JSON body only, so no form from any page can post one, and a small one (DR-0709)."""
    kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if kind != JSON:
        raise HTTPException(status_code=415, detail="a write's body is application/json")
    if len(await request.body()) > MAX_BODY:
        raise HTTPException(status_code=413, detail="a write's body is at most 256 KB")


async def safely(request: Request, answer: Callable[[], Awaitable[Response | BaseModel]]) -> Response | BaseModel:
    """A route's answer; a refusal its words with 400, and on an unexpected error 500 with no detail, the log getting
    the type and frames only, since a message can quote an item."""
    try:
        return await answer()
    except DocketError as e:
        return refused(e.why if isinstance(e, RefusedError) else str(e))
    except Exception as e:
        log.error("%s %s failed", request.method, request.url.path, exc_info=e)
        return JSONResponse({"error": FAILED}, status_code=500)


def router(service: Service) -> APIRouter:
    api = APIRouter()

    def vault() -> Vault:
        return service.vault()

    async def gather(state: DayState, *, whole: bool = True, alikes: tuple[str, ...] = ()) -> day.Facts:
        return await asyncio.to_thread(lambda: day.gather(vault(), state, whole=whole, alikes=alikes))

    @api.get("/ledger", response_model=Ledger, responses={400: {"model": Refused}})
    async def ledger(request: Request) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Ledger:
            state = DayState.read(request.query_params)
            found = await gather(state)
            # A whole read always lists.
            listing = cast(LedgerRead, found.listing)
            today = listing.today
            return Ledger(
                canonical=state.url(),
                version=found.version,
                today=today.isoformat(),
                list=day_view.build_list(listing, state, found.matched),
                filters=Filters(
                    q=state.q or "",
                    stream=state.stream or "",
                    kind=state.kind or "",
                    who=state.who or "",
                    k=state.k,
                    state=state.state,
                    group=state.group,
                    archived=state.archived,
                ),
                kinds=list(ops.KINDS),
                verdicts=day_view.verdict_buttons([]),
                snooze=day_view.snooze_choices(today),
                tomorrow=(today + timedelta(days=1)).isoformat(),
                notice=day_view.build_notice(state, found.lines),
            )

        return await safely(request, answer)

    @api.get("/ledger/panel", response_model=Panel, responses={400: {"model": Refused}})
    async def ledger_panel(request: Request) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Panel:
            state = DayState.read(request.query_params)
            found = await gather(state, whole=False)
            panel = None
            if found.shown is not None:
                panel = day_view.build_panel(found.shown, found.meeting, found.sources, found.tz)
            return Panel(canonical=state.url(), panel=panel, missing=found.missing)

        return await safely(request, answer)

    @api.get("/ledger/text", response_model=Viewer, responses={400: {"model": Refused}})
    async def ledger_text(request: Request) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response | Viewer:
            state = DayState.read(request.query_params)
            if state.text is None:
                raise DayStateError("text names the material to show, with open naming its item")
            found = await gather(state, whole=False)
            viewer = found.viewer or day.Viewer(state.text, problem=found.missing)
            if viewer.problem:
                return refused(viewer.problem)
            return Viewer(ref=viewer.ref, text=viewer.text, cut=viewer.cut)

        return await safely(request, answer)

    @api.get("/ledger/changed", response_model=Changed)
    async def ledger_changed(request: Request, v: str = "") -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        given = v.strip()

        async def answer() -> Changed:
            root = service.settings.vault
            now = await asyncio.to_thread(lambda: day.version(root)) if root is not None else given
            return Changed(changed=now != given, version=now)

        try:
            return await answer()
        except Exception as e:
            # A poll that fails says nothing changed: the next one asks again.
            log.error("%s %s failed", request.method, request.url.path, exc_info=e)
            return Changed(changed=False, version=given)

    @api.post(
        "/ledger/write",
        response_model=Wrote,
        responses={400: {"model": Refused}},
        dependencies=[Depends(json_only)],
    )
    async def ledger_write(request: Request, body: day.Body) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response | Wrote:
            post = day.posted(body, day.local_now(vault()))
            try:
                wrote = await service.writer.run(lambda: day.write(vault(), post.changes, bulk=post.bulk))
            except RefusedError as e:
                if not e.refs:
                    raise
                found = await gather(DayState(), whole=False, alikes=e.refs)
                today = day.local_now(vault()).date()
                return refused(e.why, [day_view.row_of(r, today) for r in found.alikes])
            written = wrote.written
            batch = written.ops[0].batch if written.ops else None
            lines = (await gather(DayState(done=batch), whole=False)).lines if batch else []
            notice = day_view.notice_of(batch or "nothing", post.said, wrote.skipped, lines)
            added = None
            if post.op == "add" and written.ops:
                new = written.ops[-1].item
                added = written.labels.get(new, new)
            return Wrote(
                notice=notice,
                done=batch,
                skipped=list(wrote.skipped),
                at=post.at if post.op in day.CALLS else None,
                added=added,
                version=wrote.version,
            )

        return await safely(request, answer)

    @api.post(
        "/ledger/save",
        response_model=Saved,
        responses={400: {"model": Refused}},
        dependencies=[Depends(json_only)],
    )
    async def ledger_save(request: Request, body: day.Body) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response | Saved:
            post = day.posted(body, day.local_now(vault()))
            if post.op not in ("set", "notes"):
                raise DayStateError("a save sets a field of an item")
            wrote = await service.writer.run(lambda: day.write(vault(), post.changes, bulk=False))
            item = str(post.changes[0]["item"])
            found = await gather(DayState(open=item), whole=False)
            if found.shown is None:
                return refused(found.missing)
            row = found.shown.items[0]
            # The list cuts a title as the views cut it; the panel shows it whole.
            shown = row.model_copy(update={"title": views.cut(row.title)})
            return Saved(
                row=day_view.row_of(shown, found.shown.today),
                panel=day_view.build_panel(found.shown, found.meeting, found.sources, found.tz),
                version=wrote.version,
            )

        return await safely(request, answer)

    @api.post(
        "/ledger/open",
        response_model=Opened,
        responses={400: {"model": Refused}},
        dependencies=[Depends(json_only)],
    )
    async def ledger_open(request: Request, body: Open) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Opened:
            day.page_of(body.page)
            item = ref_of(body.item, "item")
            ref = body.ref.strip()
            await asyncio.to_thread(lambda: day.open_material(vault(), item, ref, body.reveal))
            return Opened(notice=day_view.notice_of("shown" if body.reveal else "opened", None, (), []))

        return await safely(request, answer)

    return api
