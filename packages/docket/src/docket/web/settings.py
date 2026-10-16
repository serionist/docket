"""The Settings page: `/settings` is always a whole page (DR-0505).

It reads vault.yaml, the user's config, the runner's mapper check, this machine's models and the service's own models
state, in this process and never through `/api`, and writes nothing. Pages make only the changes on DR-0506's list,
each through vault.yaml's one writer and its `loosens` check; every other change is named here, as its command or its
line. A value the URL can't hold, or a `DocketError`, is the page's own error state; anything else is logged as its type
and frames and answered with the error page, as on the other pages (DR-0170).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from starlette.responses import Response

from docket import userconfig
from docket.errors import DocketError
from docket.index.build import vector_counts
from docket.models import report
from docket.service.state import Service
from docket.sources import runner
from docket.vault.config import CONFIG_FILE
from docket.web import settings_view, templating
from docket.web.settings_view import Facts, SettingsState, SettingsStateError, SourceFacts
from docket.web.shell import shell


def gather(service: Service) -> Facts:
    """Everything the page shows, read now. Nothing here writes: the mapper check resolves paths, the models' report
    reads markers, and the vectors are counted in the index."""
    vault = service.vault()
    text = (vault.root / CONFIG_FILE).read_bytes().decode("utf-8")
    config = vault.config
    folders_problem: str | None = None
    try:
        here = userconfig.folders().get(vault.root.resolve(), {})
    except DocketError as e:
        folders_problem, here = str(e), {}
    sources: list[SourceFacts] = []
    for name, entry in sorted(config.sources.items()):
        problem: str | None = None
        if entry.folder is None:
            try:
                runner.find_mapper(vault, name)
            except DocketError as e:
                problem = str(e)
        folder = here.get(name)
        sources.append(SourceFacts(name, entry, problem, str(folder) if folder is not None else None))
    said, errors = report.service_lines(service.models.status().model_dump())
    return Facts(
        root=vault.root,
        machine=vault.machine,
        config=config,
        docket_form=config.written_by_docket(text),
        sources=sources,
        models=report.read(),
        config_path=userconfig.config_path(),
        folders_problem=folders_problem,
        service=said,
        service_errors=errors,
        vectors=vector_counts(vault),
    )


def _context(view: settings_view.SettingsView | None, state: SettingsState, problem: str = "") -> dict[str, Any]:
    return {"view": view, "state": state, "problem": problem, "shown_url": SettingsState(show="lists").url()}


def router(service: Service) -> APIRouter:
    web = APIRouter()

    @web.get("/settings", include_in_schema=False)
    async def settings_page(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            context = shell(service, request, "Settings", current="settings")
            try:
                state = SettingsState.read(request.query_params)
            except SettingsStateError as e:
                return templating.page(
                    "settings/page.html", context | _context(None, SettingsState(), str(e)), status=400
                )
            try:
                facts = await asyncio.to_thread(gather, service)
            except DocketError as e:
                return templating.page("settings/page.html", context | _context(None, state, str(e)), status=400)
            view = settings_view.build(facts, state)
            return templating.page("settings/page.html", context | _context(view, state))

        def failed() -> Response:
            return templating.page(
                "error.html", shell(None, request, "Something failed", current="settings"), status=500
            )

        return await templating.safely(request, "/settings", answer, failed)

    return web
