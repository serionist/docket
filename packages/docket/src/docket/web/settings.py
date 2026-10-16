"""The Settings page: `/settings` is always a whole page (DR-0505), and `/settings/never-store` adds a never-store
value, the page's one write so far.

`GET` reads vault.yaml, the user's config, the runner's mapper check, this machine's models and the service's own models
state, in this process and never through `/api`, and writes nothing. Pages make only the changes on DR-0506's list,
each through vault.yaml's one writer and its `loosens` check; every other change is named here, as its command or its
line. A value the URL can't hold, or a `DocketError`, is the page's own error state; anything else is logged as its type
and frames and answered with the error page, as on the other pages (DR-0170).

An add is two plain form posts the Guard checks (DR-0152), on the service's writer thread, as Staging's and Curate's
writes are (DR-0381, DR-0473): the first answers the confirm view, naming what the value drops now and what already
stored holds it (DR-0509); the second, carrying the hash of the vault.yaml the view was built from, adds it and answers
a 303 whose `done` names the list and a count. The value travels only in the posts' bodies: never in a URL, the log or
a commit message (DR-0508).
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

from fastapi import APIRouter, Request
from starlette.datastructures import QueryParams
from starlette.responses import RedirectResponse, Response

from docket import userconfig
from docket.errors import DocketError
from docket.index.build import vector_counts
from docket.index.query import Reach, reach
from docket.models import report
from docket.service.state import Service
from docket.sources import runner
from docket.vault import neverstore
from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.vault.gate import Gate
from docket.vault.neverstore import ListName
from docket.vault.settings import LISTS, Added, StaleError, add_listed, checked_value, file_hash
from docket.web import settings_view, templating
from docket.web.settings_view import Facts, SettingsState, SettingsStateError, SourceFacts
from docket.web.shell import shell


def gather(service: Service) -> Facts:
    """Everything the page shows, read now. Nothing here writes: the mapper check resolves paths, the models' report
    reads markers, and the vectors are counted in the index."""
    vault = service.vault()
    data = (vault.root / CONFIG_FILE).read_bytes()
    text = data.decode("utf-8")
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
        shown=file_hash(data),
    )


def _context(view: settings_view.SettingsView | None, state: SettingsState, problem: str = "") -> dict[str, Any]:
    return {"view": view, "state": state, "problem": problem, "shown_url": SettingsState(show="lists").url()}


def _list_name(given: str | None) -> ListName:
    if given not in LISTS:
        raise DocketError(f"a never-store list is people, containers or keywords, not {given!r}")
    return given


def _with(config: VaultConfig, name: ListName, value: str) -> VaultConfig:
    """The settings as they would be with the value on its list: what the confirm view counts against."""
    lists = config.never_store
    return config.model_copy(update={"never_store": lists.model_copy(update={name: [*getattr(lists, name), value]})})


def _staged(service: Service, name: ListName, value: str) -> tuple[int, str, bool]:
    """On the writer thread, so no write runs between the read and the count: what the value would drop from staging,
    and vault.yaml's hash and form as read."""
    vault = service.vault()
    data = (vault.root / CONFIG_FILE).read_bytes()
    config = vault.config
    dropped = Gate(vault).would_drop(_with(config, name, value))
    return dropped["never-store"], file_hash(data), config.written_by_docket(data.decode("utf-8"))


def _stored(service: Service, name: ListName, value: str) -> tuple[Reach | None, str | None]:
    """What already stored the value would have kept out, from the index, beside the writer; or why it can't say."""
    try:
        return reach(service.vault(), name, value), None
    except DocketError as e:
        return None, str(e).split("; run ")[0]
    except sqlite3.Error:
        return None, "the index is busy being brought up to date; look again in a moment"


def _add(service: Service, name: ListName, value: str, shown: str) -> Added:
    """The add, on the writer thread; then the gate's sweep, so what the list now names leaves staging at once."""
    vault = service.vault()
    added = add_listed(vault, name, value, shown=shown)
    if added.written.changed:
        Gate(vault).list()
    return added


def router(service: Service) -> APIRouter:
    web = APIRouter()

    async def whole(request: Request, state: SettingsState, *, status: int = 200, problem: str = "") -> Response:
        """The whole page for a state, or its error state; read beside the writer, since a read writes nothing."""
        context = shell(service, request, "Settings", current="settings")
        if problem:
            page = context | _context(None, state, problem)
            return templating.page("settings/page.html", page, status=status, referrer=templating.POSTS_FORMS)
        try:
            facts = await asyncio.to_thread(gather, service)
        except DocketError as e:
            page = context | _context(None, state, str(e))
            return templating.page("settings/page.html", page, status=400, referrer=templating.POSTS_FORMS)
        page = context | _context(settings_view.build(facts, state), state)
        return templating.page("settings/page.html", page, status=status, referrer=templating.POSTS_FORMS)

    async def confirm(
        request: Request, name: ListName, value: str, *, status: int = 200, stale: bool = False
    ) -> Response:
        staged, shown, docket_form = await service.writer.run(lambda: _staged(service, name, value))
        stored, why = await asyncio.to_thread(_stored, service, name, value)
        view = settings_view.build_confirm(
            name, value, staged=staged, stored=stored, why=why, shown=shown, docket_form=docket_form, stale=stale
        )
        context = shell(service, request, f"Add to {name}", current="settings") | {"confirm": view}
        return templating.page("settings/confirm.html", context, status=status, referrer=templating.POSTS_FORMS)

    def failed_page(request: Request) -> Response:
        return templating.page("error.html", shell(None, request, "Something failed", current="settings"), status=500)

    @web.get("/settings", include_in_schema=False)
    async def settings_page(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            try:
                state = SettingsState.read(request.query_params)
            except SettingsStateError as e:
                return await whole(request, SettingsState(), status=400, problem=str(e))
            return await whole(request, state)

        return await templating.safely(request, "/settings", answer, lambda: failed_page(request))

    @web.post("/settings/never-store", include_in_schema=False)
    async def never_store(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            form = await templating.read_form(request)
            if isinstance(form, Response):
                return form
            try:
                return await posted(form)
            except DocketError as e:
                # A refusal names the rule, never the value; the field is typed again.
                return await whole(request, SettingsState(), status=400, problem=str(e))

        async def posted(form: QueryParams) -> Response:
            name, value = _list_name(form.get("list")), checked_value(form.get("value") or "")
            if form.get("confirmed") != "1":
                if neverstore.holds(service.vault().config.never_store, name, value):
                    return _done(name, 0)
                return await confirm(request, name, value)
            shown = (form.get("shown") or "").strip()
            try:
                added = await service.writer.run(lambda: _add(service, name, value, shown))
            except StaleError:
                return await confirm(request, name, value, status=409, stale=True)
            if added.written.by_hand:
                return await confirm(request, name, value)
            return _done(name, 0 if added.already else 1)

        return await templating.safely(request, "/settings/never-store", answer, lambda: failed_page(request))

    return web


def _done(name: ListName, count: int) -> Response:
    done = SettingsState(done=f"listed-{name}-{count}")
    return RedirectResponse(done.url(done=True), status_code=303)
