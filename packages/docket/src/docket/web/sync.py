"""The Sync page's routes under /api/web (DR-0788): the whole page, and its runs part, which the page asks again every
5 seconds while a run is open.

| Route | What |
|---|---|
| `GET /sync` | the page for the URL's source, run and k: needs, failures, runs and cursors, and the canonical URL |
| `GET /sync/runs` | the runs part alone, from the service's memory: no cursor file, no check |

The page reads the service's memory (the open runs and the run history), the cursor files, and `run_open`'s own
checks, in this process. It writes nothing: both routes answer GET only, and nothing here opens, closes or drops a run
or edits a cursor; the run routes keep the bearer token (DR-0243). A value the URL can't hold, or a `DocketError`,
answers 400 with its words; anything else is logged as its type and frames and answers 500 with no detail.
"""

from __future__ import annotations

import asyncio
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from pydantic import BaseModel
from starlette.responses import Response

from docket.errors import DocketError
from docket.service import history
from docket.service import sync as running
from docket.service.state import Service
from docket.vault import cursors
from docket.web import sync_view
from docket.web.day_api import Refused, safely
from docket.web.sync_view import Facts, SourceFacts, SyncPage, SyncRuns, SyncState


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
        folder = config.folder is not None
        sources.append(
            SourceFacts(
                name,
                enabled=enabled,
                live=live,
                refused=refused,
                owed=owed,
                final=final,
                folder=folder,
                lookback_days=config.lookback_days,
            )
        )
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


def router(service: Service) -> APIRouter:
    api = APIRouter()

    async def read(request: Request) -> SyncPage:
        state = SyncState.read(request.query_params)
        facts = await asyncio.to_thread(gather, service)
        return sync_view.page(sync_view.build(facts, state), state, facts.tz, facts.machine)

    async def read_runs(request: Request) -> SyncRuns:
        state = SyncState.read(request.query_params)
        facts = await asyncio.to_thread(runs_only, service)
        return SyncRuns(canonical=state.url(), runs=sync_view.runs_part(sync_view.build(facts, state), facts.tz))

    @api.get("/sync", response_model=SyncPage, responses={400: {"model": Refused}})
    async def sync_read(request: Request) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: read(request))

    @api.get("/sync/runs", response_model=SyncRuns, responses={400: {"model": Refused}})
    async def sync_runs(request: Request) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: read_runs(request))

    return api
