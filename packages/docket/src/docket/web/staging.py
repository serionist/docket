"""The Staging page: `/staging` is always a whole page and `/staging/panel` always the open record; `/staging/classify`
and `/staging/promote` are the page's two writes (DR-0380 to DR-0384).

Every act is the CLI's own call on the staging gate, run on the service's writer thread (DR-0153): reads too, since
every gate call sweeps staging first, which deletes and rewrites files. The writes are plain form posts the Guard
checks as it checks every write (DR-0152), answered with a 303 to the page so a reload never posts again (DR-0381).
Nothing here releases a hold or reads an import folder (DR-0382).

A value the URL or a form can't hold, or a `DocketError`, is the page's own error state; anything else is logged as its
type and frames and answered with the error page, as on Search and Sync (DR-0170).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from starlette.responses import RedirectResponse, Response

from docket.errors import DocketError
from docket.service.state import Service
from docket.sources import local
from docket.vault import holds, releaselog
from docket.vault.cuts import Cut
from docket.vault.gate import Gate, Label, Listing, Shown
from docket.vault.records import source_name_problem
from docket.vault.redact import Redactor
from docket.web import staging_view, templating
from docket.web.shell import shell
from docket.web.staging_view import PANEL, StagingState, StagingStateError

CHANGED = "This record changed since you opened it: nothing was written. Here it is as it stands now."


@dataclass(frozen=True)
class Facts:
    """What the page shows, read in one writer job."""

    tz: ZoneInfo
    now: datetime
    root: str
    listing: Listing
    redactor: Redactor
    holds: holds.Holds
    releases: releaselog.Releases
    folders: dict[str, list[str]]
    shown: Shown | None


def gather(service: Service, state: StagingState, *, held: bool = True) -> Facts:
    """The list through the gate's own call, the open record through `Gate.show`, and the held ids."""
    vault = service.vault()
    gate = Gate(vault)
    listing = gate.list()
    shown = gate.show(state.open) if state.open and any(e.key == state.open for e in listing.entries) else None
    return Facts(
        tz=ZoneInfo(vault.config.owner.time_zone),
        now=datetime.now(UTC),
        root=str(vault.root),
        listing=listing,
        redactor=Redactor.from_config(vault.config.redaction),
        # Read after the gate's own read, which fails closed on a list that doesn't read.
        holds=holds.read_all(vault.root) if held else holds.Holds(),
        releases=releaselog.read_all(vault.root) if held else releaselog.Releases(),
        folders=local.imported(vault) if held else {},
        shown=shown,
    )


def _panel(facts: Facts, state: StagingState) -> dict[str, Any]:
    if not state.open:
        return {"panel": None, "missing": None}
    if facts.shown is None:
        return {"panel": None, "missing": staging_view.missing(state)}
    return {"panel": staging_view.build_panel(facts.shown, facts.redactor, state), "missing": None}


def _context(facts: Facts, state: StagingState) -> dict[str, Any]:
    on_hold = Counter(
        e.staged.record.source for e in facts.listing.entries if facts.listing.why.get(e.key) == "on-hold"
    )
    held = staging_view.build_held(
        facts.holds, facts.releases, state, folders=facts.folders, root=facts.root, on_hold=on_hold
    )
    view = staging_view.build_list(
        facts.listing,
        facts.redactor,
        state,
        facts.now,
        held_ids=sum(len(facts.holds.of(s)) for s in facts.holds.ids),
        held_sources=[s for s in facts.holds.ids if facts.holds.of(s)],
    )
    return {"view": view, "held": held, "state": state, "tz": facts.tz, "problem": "", "changed": ""} | _panel(
        facts, state
    )


def _empty(state: StagingState, problem: str) -> dict[str, Any]:
    return {
        "view": None,
        "held": [],
        "state": state,
        "tz": ZoneInfo("UTC"),
        "problem": problem,
        "changed": "",
        "panel": None,
        "missing": None,
    }


def router(service: Service) -> APIRouter:
    web = APIRouter()

    async def whole(request: Request, state: StagingState, *, status: int = 200, **extra: str) -> Response:
        """The whole page for a state, read on the writer; a `DocketError` is the page's own error."""
        context = shell(service, request, "Staging", current="staging")
        try:
            facts = await service.writer.run(lambda: gather(service, state))
        except DocketError as e:
            page = context | _empty(state, str(e))
            return templating.page("staging/page.html", page, status=400, referrer=templating.POSTS_FORMS)
        page = context | _context(facts, state) | extra
        return templating.page("staging/page.html", page, status=status, referrer=templating.POSTS_FORMS)

    def failed_page(request: Request) -> Response:
        return templating.page("error.html", shell(None, request, "Something failed", current="staging"), status=500)

    @web.get("/staging", include_in_schema=False)
    async def staging_page(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            try:
                state = StagingState.read(request.query_params)
            except DocketError as e:
                context = shell(service, request, "Staging", current="staging") | _empty(StagingState(), str(e))
                return templating.page("staging/page.html", context, status=400, referrer=templating.POSTS_FORMS)
            return await whole(request, state)

        return await templating.safely(request, "/staging", answer, lambda: failed_page(request))

    @web.get("/staging/panel", include_in_schema=False)
    async def staging_panel(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            try:
                state = StagingState.read(request.query_params)
                facts = await service.writer.run(lambda: gather(service, state, held=False))
            except DocketError as e:
                return _panel_error(str(e), 400)
            context = {"state": state, "tz": facts.tz} | _panel(facts, state)
            headers = templating.history(request, state.url(), staging_view.canonical)
            return templating.page("staging/_panel.html", context, headers=headers, referrer=templating.POSTS_FORMS)

        return await templating.safely(request, PANEL, answer, lambda: _panel_error(templating.FAILED, 500))

    @web.post("/staging/classify", include_in_schema=False)
    async def staging_classify(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            form = await templating.read_form(request)
            if isinstance(form, Response):
                return form
            try:
                back = StagingState.read(form).listing()
                state = StagingState.read(form)
                key = state.open
                if key is None:
                    raise StagingStateError("the form names no record: open is missing")
                if form.get("act") == "preview":
                    # Nothing is written: the preview is the page's own state, at its own URL.
                    return RedirectResponse(state.chosen().url(), status_code=303)
                label = form.get("label")
                if label not in ("work", "private"):
                    raise StagingStateError(f"label is work or private, not {label!r}")
                if label == "private" and state.selected():
                    raise StagingStateError("cuts go with work; a private record is dropped whole")
                shown = {name: form.get(name) or "" for name in ("shown_state", "shown_by", "shown_cuts")}
            except DocketError as e:
                return await whole(request, StagingState(), status=400, problem=str(e))
            try:
                changed = await service.writer.run(lambda: _classify(service, key, label, state, shown))
            except StagingStateError:
                # What was checked or typed doesn't fit the record: the panel names it, beside every check kept.
                return await whole(request, state.chosen(), status=400)
            except DocketError as e:
                return await whole(request, back.opened(key), status=400, problem=str(e))
            if changed:
                return await whole(request, back.opened(key), status=409, changed=CHANGED)
            done = StagingState(source=back.source, state=back.state, k=back.k, done=f"{label}-{key[:12]}")
            return RedirectResponse(done.url(done=True), status_code=303)

        return await templating.safely(request, "/staging/classify", answer, lambda: failed_page(request))

    @web.post("/staging/promote", include_in_schema=False)
    async def staging_promote(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            form = await templating.read_form(request)
            if isinstance(form, Response):
                return form
            try:
                back = StagingState.read(form).listing()
                source = (form.get("promote") or "").strip() or None
                if source is not None and source_name_problem(source) is not None:
                    raise StagingStateError(f"promote names a source, not {source!r}")
            except DocketError as e:
                return await whole(request, StagingState(), status=400, problem=str(e))
            try:
                result = await service.writer.run(lambda: Gate(service.vault()).promote(source, via="page"))
            except DocketError as e:
                return await whole(request, back, status=400, problem=str(e))
            # The calendar step's meetings are a write too, even with no record written: a stopped one replayed.
            if any(run.written for run in result.runs) or (result.meetings is not None and result.meetings.commits):
                service.updater.poke()
            written = sum(run.written for run in result.runs)
            done = StagingState(
                source=back.source, state=back.state, k=back.k, done=f"promoted-{written}-{len(result.held)}"
            )
            return RedirectResponse(done.url(done=True), status_code=303)

        return await templating.safely(request, "/staging/promote", answer, lambda: failed_page(request))

    return web


def _classify(service: Service, key: str, label: Label, state: StagingState, shown: dict[str, str]) -> bool:
    """Classify a staged record as the CLI does, if it is still as the panel showed it; True when it changed since.
    One vault and its one lock around the check and the call, so nothing writes between them: the gate's own calls
    take the same lock again, which it allows."""
    vault = service.vault()
    gate = Gate(vault)
    with vault.lock:
        now = gate.show(key)
        staged = now.entry.staged
        if staging_view.seen(staged) != shown:
            return True
        cuts: list[Cut] = []
        if label == "work":
            redactor = Redactor.from_config(vault.config.redaction)
            cuts = staging_view.chosen_cuts(staging_view.read_record(staged.record, redactor), state)
        gate.classify(key, label, cuts, by="user")
    return False


def _panel_error(message: str, status: int) -> Response:
    context = {"panel": None, "missing": None, "error": message, "state": StagingState(), "tz": ZoneInfo("UTC")}
    return templating.page("staging/_panel.html", context, status=status, referrer=templating.POSTS_FORMS)
