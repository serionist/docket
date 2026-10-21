"""The Search page's reads under /api/web (DR-0709): the results for the page's own query string, and the open hit's
panel beside them, each answering the canonical URL for what it read (DR-0174, DR-0704).

| Route | What |
|---|---|
| `GET /search` | how search read the query, the chips, the facets and the groups of hits |
| `GET /search/panel` | the panel `hit` opens, or the one a query that is one locator opens |

A value the URL can't hold answers 400 naming every one that didn't read. An index still being built answers
`waiting` with why, from either read, and the page asks again in 5 s; a locator that doesn't resolve is the panel's
own error. Anything
else is logged as its type and frames and answers 500 with no detail.
"""

from __future__ import annotations

import asyncio
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from pydantic import BaseModel
from starlette.responses import Response

from docket import index
from docket.service import calls
from docket.service.state import Service
from docket.service.writer import IndexBuildingError
from docket.web import panel, views
from docket.web.day_api import Refused, refused, safely
from docket.web.panel import HitPanel
from docket.web.shell import index_line
from docket.web.state import SearchState
from docket.web.views import Results


class SearchRead(BaseModel):
    """The results the Search page lists for its URL, with what the start and the waiting state say."""

    # The URL as the server writes it, which the app puts in the address bar as it is (DR-0704).
    canonical: str
    # None when nothing is asked: the start, or a query that is one locator.
    results: Results | None
    # The query when it is one locator, which the panel opens instead of searching its words (DR-0174).
    locator: str | None
    # Why the index can't answer yet; the page asks again in 5 s.
    waiting: str
    # The index's size and last update, which the start says.
    index_line: str


class HitRead(BaseModel):
    canonical: str
    # None when the URL opens nothing, or while the index is being built.
    panel: HitPanel | None
    # Why the index can't answer yet; the page asks again in 5 s, as for the results.
    waiting: str


def router(service: Service) -> APIRouter:
    api = APIRouter()

    def zone() -> ZoneInfo:
        return ZoneInfo(service.vault().config.owner.time_zone)

    async def results(request: Request) -> Response | SearchRead:
        state, problems = SearchState.read_what_reads(request.query_params)
        if problems:
            return refused("; ".join(problems))
        read = SearchRead(
            canonical=state.url(),
            results=None,
            locator=state.query if state.locator_query else None,
            waiting="",
            index_line=index_line(service),
        )
        if not state.asked:
            return read
        tz = zone()
        try:
            result, _ = await calls.search(
                service,
                state.query if not state.locator_query else "",
                people=state.people,
                entities=state.entities,
                sources=state.sources,
                since=state.since,
                until=state.until,
                k=state.k,
            )
        except IndexBuildingError as e:
            read.waiting = str(e)
            return read
        names = await asyncio.to_thread(index.labels, service.vault(), views.label_ids(result, state))
        read.results = views.build(result, state, names, tz)
        return read

    async def hit(request: Request) -> HitRead:
        state = SearchState.read(request.query_params)
        try:
            built = await panel.build(service, state, zone()) if state.open_hit else None
        except IndexBuildingError as e:
            return HitRead(canonical=state.url(), panel=None, waiting=str(e))
        return HitRead(canonical=state.url(), panel=built, waiting="")

    @api.get("/search", response_model=SearchRead, responses={400: {"model": Refused}})
    async def search(request: Request) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: results(request))

    @api.get("/search/panel", response_model=HitRead, responses={400: {"model": Refused}})
    async def search_panel(request: Request) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: hit(request))

    return api
