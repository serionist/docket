"""The Staging page's routes under /api/web (DR-0709, DR-0784, DR-0785): what waits at the gate, the open record, and
the page's two writes.

| Route | What |
|---|---|
| `GET /staging` | the list for the URL's source, state and k, the held recordings, and an old URL's `done` |
| `GET /staging/panel` | the open record as promotion would write it, with what is checked on it, or that it is gone |
| `POST /staging/classify` | work, with the cuts checked or typed, or private: `Gate.classify`, as the CLI calls it |
| `POST /staging/promote` | one source's ready records, or every source's: through `Gate.promote`, via page |

Every act is the CLI's own call on the staging gate, run on the service's writer thread (DR-0153): reads too, since
every gate call sweeps staging first, which deletes and rewrites files. The writes are JSON writes with the browser's
session only: the Guard's same-origin check, the token refused, the body checked before any route reads it (DR-0709).
A classification carries what the panel showed; one from a page the record changed under writes nothing and answers
409, and the page reads the record again. Nothing here releases a hold or reads an import folder (DR-0382). A value the
URL or the body can't hold, or a `DocketError`, answers 400 with its words; anything else is logged as its type and
frames and answers 500 with no detail.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.datastructures import QueryParams
from starlette.responses import Response

from docket.service.state import Service
from docket.sources import local
from docket.vault import holds, releaselog
from docket.vault.cuts import Cut
from docket.vault.gate import Gate, Label, Listing, Shown, held_refusal
from docket.vault.records import source_name_problem
from docket.vault.redact import Redactor
from docket.web import staging_view
from docket.web.day_api import Refused, safely
from docket.web.staging_view import (
    HeldSource,
    Missing,
    Seen,
    StagingList,
    StagingPanel,
    StagingState,
    StagingStateError,
)

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


class StagingRead(BaseModel):
    # The URL as the server writes it, which the app puts in the address bar as it is (DR-0704); never `done`.
    canonical: str
    view: StagingList
    held: list[HeldSource]
    # What a write did, for a URL from before the app that still names it.
    notice: str | None


class StagingPanelRead(BaseModel):
    canonical: str
    panel: StagingPanel | None
    # An open key nothing is staged under any more.
    missing: Missing | None


class Classify(BaseModel):
    """Work or private on a staged record: its key, what is checked on it and typed, as the URL holds them (`u` and
    `line` each a number from 1 or a range, `cut` as --cut takes it), and what the panel showed of it."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    u: list[str] = []
    line: list[str] = []
    cut: list[str] = []
    seen: Seen


class Classified(BaseModel):
    key: str
    label: str
    # What it did, in the words the page says it with.
    notice: str


class Promote(BaseModel):
    """One source's ready records, or every source's when `source` is null."""

    model_config = ConfigDict(extra="forbid")

    source: str | None = None


class Promoted(BaseModel):
    written: int
    held: int
    notice: str


def listed(facts: Facts, state: StagingState) -> tuple[StagingList, list[HeldSource]]:
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
        facts.tz,
        held_ids=sum(len(facts.holds.of(s)) for s in facts.holds.ids),
        held_sources=[s for s in facts.holds.ids if facts.holds.of(s)],
    )
    return view, held


def router(service: Service) -> APIRouter:
    api = APIRouter()

    async def read(request: Request) -> StagingRead:
        asked = StagingState.read(request.query_params)
        state, done = asked.listing(), asked.done
        facts = await service.writer.run(lambda: gather(service, state))
        view, held = listed(facts, state)
        return StagingRead(canonical=state.url(), view=view, held=held, notice=staging_view.done_line(done))

    async def read_panel(request: Request) -> StagingPanelRead:
        state = StagingState.read(request.query_params)
        facts = await service.writer.run(lambda: gather(service, state, held=False))
        if not state.open:
            return StagingPanelRead(canonical=state.url(), panel=None, missing=None)
        if facts.shown is None:
            return StagingPanelRead(canonical=state.url(), panel=None, missing=staging_view.missing(state))
        panel = staging_view.build_panel(facts.shown, facts.redactor, state, facts.tz)
        return StagingPanelRead(canonical=state.url(), panel=panel, missing=None)

    async def classify(body: Classify) -> Response | Classified:
        if body.label not in ("work", "private"):
            raise StagingStateError(f"label is work or private, not {body.label!r}")
        label: Label = "work" if body.label == "work" else "private"
        pairs = [("open", body.key), *(("u", v) for v in body.u), *(("line", v) for v in body.line)]
        state = StagingState.read(QueryParams([*pairs, *(("cut", v) for v in body.cut)]))
        key = state.open
        if key is None:
            raise StagingStateError("the write names no record: key is missing")
        if label == "private" and state.selected():
            raise StagingStateError("cuts go with work; a private record is dropped whole")
        changed = await service.writer.run(lambda: _classify(service, key, label, state, body.seen))
        if changed:
            return JSONResponse(Refused(error=CHANGED).model_dump(mode="json"), status_code=409)
        notice = staging_view.done_line(f"{label}-{key[:12]}") or ""
        return Classified(key=key, label=label, notice=notice)

    async def promote(body: Promote) -> Promoted:
        source = (body.source or "").strip() or None
        if source is not None and source_name_problem(source) is not None:
            raise StagingStateError(f"source names a source, not {source!r}")
        result = await service.writer.run(lambda: Gate(service.vault()).promote(source, via="page"))
        # The calendar step's meetings are a write too, even with no record written: a stopped one replayed.
        if any(run.written for run in result.runs) or (result.meetings is not None and result.meetings.commits):
            service.updater.poke()
        written = sum(run.written for run in result.runs)
        # A calendar step that stopped is said too: its records wait for the next promotion (DR-0647).
        stopped = "-stopped" if result.meetings_stopped is not None else ""
        notice = staging_view.done_line(f"promoted-{written}-{len(result.held)}{stopped}") or ""
        return Promoted(written=written, held=len(result.held), notice=notice)

    @api.get("/staging", response_model=StagingRead, responses={400: {"model": Refused}})
    async def staging_read(request: Request) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: read(request))

    @api.get("/staging/panel", response_model=StagingPanelRead, responses={400: {"model": Refused}})
    async def staging_panel(request: Request) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: read_panel(request))

    @api.post(
        "/staging/classify",
        response_model=Classified,
        responses={400: {"model": Refused}, 409: {"model": Refused}},
    )
    async def staging_classify(request: Request, body: Classify) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: classify(body))

    @api.post("/staging/promote", response_model=Promoted, responses={400: {"model": Refused}})
    async def staging_promote(request: Request, body: Promote) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: promote(body))

    return api


def _classify(service: Service, key: str, label: Label, state: StagingState, shown: Seen) -> bool:
    """Classify a staged record as the CLI does, if it is still as the panel showed it; True when it changed since.
    One vault and its one lock around the check and the call, so nothing writes between them: the gate's own calls
    take the same lock again, which it allows."""
    vault = service.vault()
    gate = Gate(vault)
    with vault.lock:
        now = gate.show(key)
        staged = now.entry.staged
        if now.why == "on-hold":
            # Refused before its body is read: a selection's cuts would say how long it is (DR-0382).
            raise held_refusal(staged.record)
        if staging_view.seen(staged) != shown:
            return True
        cuts: list[Cut] = []
        if label == "work":
            redactor = Redactor.from_config(vault.config.redaction)
            cuts = staging_view.chosen_cuts(staging_view.read_record(staged.record, redactor), state)
        gate.classify(key, label, cuts, by="user")
    return False
