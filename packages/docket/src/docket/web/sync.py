"""The Sync page: `/sync` is always a whole page, `/sync/runs` always its runs part, which asks again every 5 seconds
while a run is open (DR-0300, DR-0306).

The page reads the service's memory (the open runs and the run history), the cursor files, and `run_open`'s own checks,
in this process and never through `/api`. It writes nothing: both routes answer GET only, as Search's do. A value the
URL can't hold, or a `DocketError`, is the page's own error state; anything else is logged as its type and frames and
answered with the error page, as on Search (DR-0170).
"""

from __future__ import annotations

import asyncio
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from starlette.responses import Response

from docket.errors import DocketError
from docket.service import history
from docket.service import sync as running
from docket.service.state import Service
from docket.vault import cursors
from docket.web import sync_view, templating
from docket.web.shell import shell
from docket.web.sync_view import Facts, SourceFacts, SyncState, SyncStateError

FAILED = (
    "Something failed in Docket. The service log says what, and never holds your words; docket service status names it."
)


def gather(service: Service) -> Facts:
    """Everything the page shows, read now: the vault's sources and their checks, the cursor files, the runs."""
    vault = service.vault()
    now = service.runs.clock()
    own: cursors.Cursors = {}
    own_problem: str | None = None
    try:
        own = cursors.read(vault.root, vault.machine)
    except DocketError as e:
        own_problem = str(e)
    others, notes = cursors.read_others(vault.root, vault.machine)
    sources: list[SourceFacts] = []
    for name, config in sorted(vault.config.sources.items()):
        live = bool(config.tools)
        # A private source stores nothing and runs no sync: muted, like one turned off.
        enabled = config.enabled and config.sensitivity != "private"
        refused: str | None = None
        owed = final = cursors.Handover()
        if live and enabled:
            try:
                ready = running.preflight(vault, name)
                owed = running.owed(vault, name, ready, now, final=False)
                final = running.owed(vault, name, ready, now, final=True)
            except DocketError as e:
                refused = str(e)
        sources.append(SourceFacts(name, enabled=enabled, live=live, refused=refused, owed=owed, final=final))
    return Facts(
        now=now,
        tz=ZoneInfo(vault.config.owner.time_zone),
        machine=vault.machine,
        sources=sources,
        own=own,
        own_problem=own_problem,
        others=others,
        others_notes=notes,
        open_runs=service.runs.snapshot(),
        lines=service.history.recent(k=history.KEEP),
        history_problem=service.history.problem,
    )


def runs_only(service: Service) -> Facts:
    """What the runs part needs, from the service's memory and the owner's zone: no cursor file, no check."""
    vault = service.vault()
    return Facts(
        now=service.runs.clock(),
        tz=ZoneInfo(vault.config.owner.time_zone),
        machine=vault.machine,
        sources=[],
        own={},
        own_problem=None,
        others={},
        others_notes=[],
        open_runs=service.runs.snapshot(),
        lines=service.history.recent(k=history.KEEP),
        history_problem=service.history.problem,
    )


def _context(view: sync_view.SyncView | None, state: SyncState, tz: ZoneInfo, problem: str = "") -> dict[str, Any]:
    def run_url(run: str) -> str:
        return state.with_run(run).url()

    return {"view": view, "state": state, "tz": tz, "problem": problem, "fragment": False, "run_url": run_url}


def router(service: Service) -> APIRouter:
    web = APIRouter()

    @web.get("/sync", include_in_schema=False)
    async def sync_page(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            context = shell(service, request, "Sync", current="sync")
            try:
                state = SyncState.read(request.query_params)
            except SyncStateError as e:
                page = context | _context(None, SyncState(), ZoneInfo("UTC"), str(e))
                return templating.page("sync/page.html", page, status=400)
            try:
                facts = await asyncio.to_thread(gather, service)
            except DocketError as e:
                page = context | _context(None, state, ZoneInfo("UTC"), str(e))
                return templating.page("sync/page.html", page, status=400)
            view = sync_view.build(facts, state)
            return templating.page("sync/page.html", context | _context(view, state, facts.tz))

        def failed() -> Response:
            return templating.page("error.html", shell(None, request, "Something failed", current="sync"), status=500)

        return await templating.safely(request, "/sync", answer, failed)

    @web.get("/sync/runs", include_in_schema=False)
    async def sync_runs(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            try:
                state = SyncState.read(request.query_params)
                facts = await asyncio.to_thread(runs_only, service)
            except DocketError as e:
                return _runs_error(str(e), 400)
            context = _context(sync_view.build(facts, state), state, facts.tz) | {"fragment": True}
            # The refresh never adds a history entry: the page's URL stays as it was.
            headers = templating.history(request, state.url(), sync_view.canonical, replace=True)
            return templating.page("sync/_runs.html", context, headers=headers)

        return await templating.safely(request, "/sync/runs", answer, lambda: _runs_error(FAILED, 500))

    return web


def _runs_error(message: str, status: int) -> Response:
    context = _context(None, SyncState(), ZoneInfo("UTC"), message) | {"fragment": True}
    return templating.page("sync/_runs.html", context, status=status)
