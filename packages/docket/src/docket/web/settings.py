"""The Settings page: `/settings` is always a whole page (DR-0505); `/settings/never-store` adds a never-store value,
`/settings/redaction` turns a redaction rule on, and `/settings/source` makes a source stricter or turns it off: the
changes on DR-0506's list, and no other.

`GET` reads vault.yaml, the user's config, the runner's mapper check, this machine's models and the service's own models
state, in this process and never through `/api`, and writes nothing. Pages make only the changes on DR-0506's list,
each through vault.yaml's one writer and its `loosens` check; every other change is named here, as its command or its
line. A value the URL can't hold, or a `DocketError`, is the page's own error state; anything else is logged as its type
and frames and answered with the error page, as on the other pages (DR-0170).

An add is two plain form posts the Guard checks (DR-0152), on the service's writer thread, as Staging's and Curate's
writes are (DR-0381, DR-0473): the first answers the confirm view, naming what the value drops now and what already
stored holds it (DR-0509); the second, carrying the hash of the vault.yaml the view was built from, adds it and answers
a 303 whose `done` names the list and a count. The value travels only in the posts' bodies: never in a URL, the log or
a commit message (DR-0508). A rule turned on, a source made mixed or turned off is one post; a source made private
asks once more, naming what waits from it. Each carries the hash of the vault.yaml the page read, so a page older than
the file writes nothing (409), and a post sent twice is no write.
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
from docket.vault import neverstore, redaction
from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.vault.gate import Gate
from docket.vault.neverstore import ListName
from docket.vault.records import source_name_problem
from docket.vault.settings import (
    BUILTIN_RULES,
    LISTS,
    Added,
    StaleError,
    add_listed,
    checked_value,
    edit_to_take_in,
    file_hash,
    key_line,
    rule_on,
    stricter,
    turn_off,
)
from docket.vault.settings import Rewritten as Written
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


BY_HAND = "vault.yaml has comments or a layout of your own, so it is left as it is; change it there"
STALE = "vault.yaml changed since the page showed it, so nothing was written. Here it is as it stands now."
# What a source's button may ask for: only these, each stricter or off (DR-0506).
ACTS = ("mixed", "private", "off")


def _context(
    view: settings_view.SettingsView | None, state: SettingsState, problem: str = "", warning: str = ""
) -> dict[str, Any]:
    shown_url = SettingsState(show="lists").url()
    return {"view": view, "state": state, "problem": problem, "warning": warning, "shown_url": shown_url}


def _list_name(given: str | None) -> ListName:
    if given not in LISTS:
        raise DocketError(f"a never-store list is people, containers or keywords, not {given!r}")
    return given


def _with(config: VaultConfig, name: ListName, value: str) -> VaultConfig:
    """The settings as they would be with the value on its list: what the confirm view counts against."""
    lists = config.never_store
    return config.model_copy(update={"never_store": lists.model_copy(update={name: [*getattr(lists, name), value]})})


def _staged(service: Service, name: ListName, value: str) -> tuple[int, str, bool, bool]:
    """On the writer thread, so no write runs between the read and the count: what the value would drop from staging,
    vault.yaml's hash and form as read, and whether an edit not committed yet goes in first. An edit that lets more in
    is refused here already, as the add would refuse it (DR-0507)."""
    vault = service.vault()
    data = (vault.root / CONFIG_FILE).read_bytes()
    text = data.decode("utf-8")
    config = vault.config
    docket_form = config.written_by_docket(text)
    takes_in = edit_to_take_in(vault, config, text) if docket_form else False
    dropped = Gate(vault).would_drop(_with(config, name, value))
    return dropped["never-store"], file_hash(data), docket_form, takes_in


def _stored(service: Service, name: ListName, value: str) -> tuple[Reach | None, str | None]:
    """What already stored the value would have kept out, from the index, beside the writer; or why it can't say."""
    try:
        return reach(service.vault(), name, value), None
    except DocketError as e:
        return None, str(e).split("; run ")[0]
    except sqlite3.Error:
        return None, "the index is busy being brought up to date; look again in a moment"


def _private_staged(service: Service, source: str) -> tuple[int, str, bool, bool]:
    """On the writer thread: what making a source private would drop from staging, and vault.yaml's hash, form and
    whether an edit not committed yet goes in first, refused here already when it lets more in."""
    vault = service.vault()
    data = (vault.root / CONFIG_FILE).read_bytes()
    text = data.decode("utf-8")
    config = vault.config
    entry = config.sources.get(source)
    if entry is None:
        raise DocketError(f"no source {source} in vault.yaml")
    docket_form = config.written_by_docket(text)
    takes_in = edit_to_take_in(vault, config, text) if docket_form else False
    private = config.model_copy(
        update={"sources": {**config.sources, source: entry.model_copy(update={"sensitivity": "private"})}}
    )
    before, after = Gate(vault).would_drop(config), Gate(vault).would_drop(private)
    return after["private"] - before["private"], file_hash(data), docket_form, takes_in


def _tighten(service: Service, source: str, act: str, shown: str) -> Written:
    """A source made stricter or turned off, on the writer thread; then the sweep, so what the source's new setting
    forbids leaves staging at once and what it now asks about waits again."""
    vault = service.vault()
    written = turn_off(vault, source, shown=shown) if act == "off" else stricter(vault, source, act, shown=shown)
    if written.changed:
        Gate(vault).list()
    return written


def _add(service: Service, name: ListName, value: str, shown: str) -> Added:
    """The add, on the writer thread; then the gate's sweep, so what the list now names leaves staging at once."""
    vault = service.vault()
    added = add_listed(vault, name, value, shown=shown)
    if added.written.changed:
        Gate(vault).list()
    return added


def router(service: Service) -> APIRouter:
    web = APIRouter()

    async def whole(
        request: Request, state: SettingsState, *, status: int = 200, problem: str = "", warning: str = ""
    ) -> Response:
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
        page = context | _context(settings_view.build(facts, state), state, warning=warning)
        return templating.page("settings/page.html", page, status=status, referrer=templating.POSTS_FORMS)

    async def private(request: Request, source: str, *, status: int = 200, stale: bool = False) -> Response:
        staged, shown, docket_form, takes_in = await service.writer.run(lambda: _private_staged(service, source))
        view = settings_view.build_source_confirm(
            source, staged=staged, shown=shown, docket_form=docket_form, stale=stale, takes_in=takes_in
        )
        context = shell(service, request, f"Make {source} private", current="settings") | {"confirm": view}
        return templating.page("settings/private.html", context, status=status, referrer=templating.POSTS_FORMS)

    async def confirm(
        request: Request, name: ListName, value: str, *, status: int = 200, stale: bool = False
    ) -> Response:
        staged, shown, docket_form, takes_in = await service.writer.run(lambda: _staged(service, name, value))
        stored, why = await asyncio.to_thread(_stored, service, name, value)
        view = settings_view.build_confirm(
            name,
            value,
            staged=staged,
            stored=stored,
            why=why,
            shown=shown,
            docket_form=docket_form,
            stale=stale,
            takes_in=takes_in,
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

    @web.post("/settings/redaction", include_in_schema=False)
    async def redaction_on(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            form = await templating.read_form(request)
            if isinstance(form, Response):
                return form
            rule, shown = form.get("rule") or "", (form.get("shown") or "").strip()
            try:
                written = await service.writer.run(lambda: rule_on(service.vault(), rule, shown=shown))
            except StaleError:
                return await whole(request, SettingsState(), status=409, warning=STALE)
            except DocketError as e:
                return await whole(request, SettingsState(), status=400, problem=str(e))
            if written.by_hand:
                line = redaction.by_hand(rule, True, rule not in BUILTIN_RULES)
                return await whole(request, SettingsState(), warning=f"{BY_HAND}: {line}.")
            kind = rule if rule in BUILTIN_RULES else "pattern"
            return RedirectResponse(SettingsState(done=f"{kind}-on").url(done=True), status_code=303)

        return await templating.safely(request, "/settings/redaction", answer, lambda: failed_page(request))

    @web.post("/settings/source", include_in_schema=False)
    async def source_change(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            form = await templating.read_form(request)
            if isinstance(form, Response):
                return form
            try:
                return await changed(form)
            except DocketError as e:
                return await whole(request, SettingsState(), status=400, problem=str(e))

        async def changed(form: QueryParams) -> Response:
            source, act = (form.get("source") or "").strip(), form.get("act") or ""
            if source_name_problem(source) is not None:
                raise DocketError("source is a source's name from vault.yaml")
            if act not in ACTS:
                raise DocketError("a page makes a source mixed or private, or turns it off; the rest is by hand")
            if act == "private" and form.get("confirmed") != "1":
                return await private(request, source)
            shown = (form.get("shown") or "").strip()
            try:
                written = await service.writer.run(lambda: _tighten(service, source, act, shown))
            except StaleError:
                if act == "private":
                    return await private(request, source, status=409, stale=True)
                return await whole(request, SettingsState(), status=409, warning=STALE)
            if written.by_hand:
                line = key_line("enabled", False) if act == "off" else key_line("sensitivity", act)
                return await whole(request, SettingsState(), warning=f"{BY_HAND}: {line}, under sources.{source}.")
            return RedirectResponse(SettingsState(done=f"{source}-{act}").url(done=True), status_code=303)

        return await templating.safely(request, "/settings/source", answer, lambda: failed_page(request))

    return web


def _done(name: ListName, count: int) -> Response:
    done = SettingsState(done=f"listed-{name}-{count}")
    return RedirectResponse(done.url(done=True), status_code=303)
