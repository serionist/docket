"""The Search page: `/search` is always a whole page, `/search/results` and `/search/panel` always fragments.

No route reads `HX-Request` to choose, so a reload, a bookmark and the back button always get a page. A fragment's
answer names the page's canonical URL: `HX-Push-Url` when it differs from the page the request came from, else
`HX-Replace-Url`, so the same search twice adds no history entry; the waiting state's retry always replaces.

A route catches its own errors: a value the URL can't hold, or a `DocketError`, is the page's own error state; any
other exception is logged as its type and frames, never its message, and answered with the error page (DR-0170).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from starlette.responses import Response

from docket import index
from docket.errors import DocketError
from docket.service import calls
from docket.service.state import Service
from docket.service.writer import IndexBuildingError
from docket.web import panel, templating, views
from docket.web.shell import shell
from docket.web.state import SearchState

log = logging.getLogger("docket.service.web")

RETRY = "X-Docket-Retry"
FAILED = (
    "Something failed in Docket. The service log says what, and never holds your words; docket service status names it."
)
WAIT_SECONDS = 5


def _history(request: Request, state: SearchState, *, replace: bool = False) -> dict[str, str]:
    """The header that puts this state's canonical URL in the address bar: a new entry only when it changed."""
    canonical = state.url()
    current = SearchState.from_url(request.headers.get("hx-current-url", ""))
    same = current is not None and current.url() == canonical
    return {"HX-Replace-Url" if replace or same else "HX-Push-Url": canonical}


async def _workspace(service: Service, state: SearchState) -> tuple[dict[str, Any], int]:
    """The context of everything under the query box, and its status."""
    context: dict[str, Any] = {"state": state, "view": None, "problem": "", "waiting": False, "panel": None}
    status = 200
    try:
        tz = _zone(service)
        context["tz"] = tz
        if state.asked:
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
            names = await asyncio.to_thread(index.labels, service.vault(), views.label_ids(result, state))
            context["view"] = views.build(result, state, names, tz)
        if state.open_hit:
            context["panel"] = await panel.build(service, state, tz)
    except IndexBuildingError as e:
        context["waiting"], context["problem"] = True, str(e)
        context["retry"] = state.url(views.RESULTS)
    except DocketError as e:
        context["problem"], status = str(e), 400
    context.setdefault("tz", ZoneInfo("UTC"))
    return context, status


def _zone(service: Service) -> ZoneInfo:
    return ZoneInfo(service.vault().config.owner.time_zone)


async def _safely(
    request: Request, route: str, answer: Callable[[], Awaitable[Response]], failed: Callable[[], Response]
) -> Response:
    """The route's answer; on an unexpected error, the log gets the type and frames only, since a message can quote a
    record, and the answer is the page's or the fragment's own error, so a fragment never swaps in a whole page."""
    try:
        return await answer()
    except Exception as e:
        log.error("%s %s failed", request.method, route, exc_info=e)
        return failed()


def _dates(request: Request) -> dict[str, str]:
    """Since and until as the URL gave them, read or not, so a date that didn't read stays in its field."""
    return {name: (request.query_params.get(name) or "").strip() for name in ("since", "until")}


def _unread(request: Request, message: str) -> dict[str, Any]:
    """A URL with a value that didn't read, or a failure: what did read stays on the page, and nothing is searched."""
    state, _ = SearchState.read_what_reads(request.query_params)
    return {
        "state": state,
        "view": None,
        "problem": message,
        "waiting": False,
        "panel": None,
        "tz": ZoneInfo("UTC"),
        "dates": _dates(request),
    }


def router(service: Service) -> APIRouter:
    web = APIRouter()

    @web.get("/search", include_in_schema=False)
    async def search_page(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            context = shell(service, request, "Search") | {"dates": _dates(request)}
            state, problems = SearchState.read_what_reads(request.query_params)
            if problems:
                return templating.page("search/page.html", context | _unread(request, "; ".join(problems)), status=400)
            found, status = await _workspace(service, state)
            if state.query:
                context["title"] = f"{state.query} - Search"
            return templating.page("search/page.html", context | found, status=status)

        def failed() -> Response:
            return templating.page("error.html", shell(None, request, "Something failed"), status=500)

        return await _safely(request, "/search", answer, failed)

    @web.get("/search/results", include_in_schema=False)
    async def search_results(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            context = shell(service, request, "Search") | {"dates": _dates(request)}
            state, problems = SearchState.read_what_reads(request.query_params)
            if problems:
                context |= _unread(request, "; ".join(problems))
                return templating.page("search/_workspace.html", context, status=400)
            found, status = await _workspace(service, state)
            headers = _history(request, state, replace=request.headers.get(RETRY) == "1")
            return templating.page("search/_workspace.html", context | found, status=status, headers=headers)

        def failed() -> Response:
            context = shell(None, request, "Search") | _unread(request, FAILED)
            return templating.page("search/_workspace.html", context, status=500)

        return await _safely(request, "/search/results", answer, failed)

    @web.get("/search/panel", include_in_schema=False)
    async def search_panel(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            try:
                state = SearchState.read(request.query_params)
                built = await panel.build(service, state, _zone(service)) if state.open_hit else None
            except DocketError as e:
                return _panel_error(str(e), 400)
            status = 400 if built is not None and built.mode == "error" else 200
            headers = _history(request, state)
            return templating.page("search/_panel.html", {"panel": built}, status=status, headers=headers)

        return await _safely(request, "/search/panel", answer, lambda: _panel_error(FAILED, 500))

    return web


def _panel_error(message: str, status: int) -> Response:
    failed = panel.PanelView(mode="error", locator="", error=message)
    return templating.page("search/_panel.html", {"panel": failed}, status=status)
