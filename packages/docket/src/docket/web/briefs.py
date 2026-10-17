"""The Briefs page: `/briefs` is always a whole page, and its links are plain links (DR-0557).

The page reads the stale report, the brief and the raw its lines cite in this process, through `docket.briefs` and
recall's own reads, and writes nothing: GET only, as the Recording page is. The report reads the index, so the page
waits for an update as a search does. A value the URL can't hold, or a `DocketError`, is the page's own error state;
anything else is logged as its type and frames and answered with the error page, as on the other pages (DR-0170).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from starlette.responses import Response

from docket import briefs, recall
from docket.errors import DocketError
from docket.service.state import Service
from docket.service.writer import IndexBuildingError
from docket.vault import brief_form
from docket.vault.neverstore import slug_listed
from docket.vault.vault import Vault
from docket.web import templating
from docket.web.briefs_state import BriefsState, BriefsStateError
from docket.web.briefs_view import BriefsView, Facts, build
from docket.web.shell import shell


def gather(vault: Vault, state: BriefsState, now: datetime) -> Facts:
    """Everything the page shows, read now: the report, and for an open brief its lines and the open line verified,
    with what a never-store list now names left unread."""
    tz = ZoneInfo(vault.config.owner.time_zone)
    lists = vault.config.never_store
    if state.brief is None:
        return Facts(now, tz, briefs.report(vault, stale_only=False, update=False))
    entity = state.brief.partition(":")[2]
    report = briefs.report(vault, [entity], stale_only=False, update=False)
    found = next((b for b in report.briefs if b.id == state.brief), None)
    if found is None:
        # A brief a list holds answers hidden whether or not it exists, so the page tells nothing of either.
        return Facts(now, tz, report, hidden=bool(report.hidden) or slug_listed(entity, lists))
    doc = recall.read(vault, state.brief).doc
    if doc is None:  # pragma: no cover - a doc id always reads as a doc, or raises
        raise DocketError(f"no doc {state.brief}")
    hidden = frozenset(brief_form.hidden_lines(((line.n, line.text) for line in doc.lines), vault.root, lists))
    if state.line is None or state.line in hidden:
        return Facts(now, tz, report, found, doc=doc, hidden_lines=hidden)
    [line] = recall.verify(vault, state.brief, lines=[state.line]).lines
    cited = brief_form.hidden_lines(enumerate(ref.text for ref in line.refs), vault.root, lists)
    return Facts(now, tz, report, found, doc=doc, hidden_lines=hidden, line=line, hidden_refs=frozenset(cited))


def _page(
    context: dict[str, Any], view: BriefsView | None, state: BriefsState, problem: str = "", *, status: int = 200
) -> Response:
    page = context | {"view": view, "state": state, "problem": problem}
    return templating.page("briefs/page.html", page, status=status)


def router(service: Service) -> APIRouter:
    web = APIRouter()

    @web.get("/briefs", include_in_schema=False)
    async def briefs_page(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            context = shell(service, request, "Briefs", current="briefs")
            try:
                state = BriefsState.read(request.query_params)
            except BriefsStateError as e:
                return _page(context, None, BriefsState(), str(e), status=400)
            try:
                await service.updater.fresh()
                facts = await asyncio.to_thread(gather, service.vault(), state, datetime.now(UTC))
            except IndexBuildingError as e:
                return _page(context, None, state, str(e), status=503)
            except DocketError as e:
                return _page(context, None, state, str(e), status=400)
            view = build(facts, state)
            if view.mode != "list":
                context["title"] = f"{view.title} - Briefs"
            return _page(context, view, state, status=404 if view.mode == "missing" else 200)

        def failed() -> Response:
            context = shell(None, request, "Something failed", current="briefs")
            return templating.page("error.html", context, status=500)

        return await templating.safely(request, "/briefs", answer, failed)

    return web
