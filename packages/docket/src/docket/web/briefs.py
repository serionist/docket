"""The Briefs page's routes under /api/web (DR-0709, DR-0780, DR-0781): the page its URL names, and the owner's
click on a decision, which writes its status as `docket decision set` does (DR-0782).

| Route | What |
|---|---|
| `GET /briefs` | the list, or a brief with its open line's raw, and the entity's decisions; the canonical URL |
| `POST /briefs/decision` | a pick on a waiting decision, at the rev the page showed: what it did, or why not |

The read takes the stale report, the brief, the raw its lines cite and the entity's decisions in this process, through
`docket.briefs`, `docket.decisions` and recall's own reads, and writes nothing. The report reads the index, so the read
waits for an update as a search does, and an index still being built answers `waiting`.

The click is a JSON write with the browser's session only, as every /api/web write: the Guard's same-origin check,
the token refused, the body checked before any route reads it (DR-0709). It runs on the service's writer thread
through `DocStore.decide` with the rev the page showed; a decision that moved since, or one that replaces or
contradicts a decision no longer current, writes nothing and answers 409 with why, and the page reads again. A value
the URL or the body can't hold, or a `DocketError`, answers 400 with its words; anything else is logged as its type
and frames and answers 500 with no detail.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.responses import Response

from docket import briefs, decisions, recall
from docket.errors import DocketError
from docket.service.state import Service
from docket.service.writer import IndexBuildingError
from docket.vault import brief_form, decision_form
from docket.vault.docstore import DocStore
from docket.vault.entities import Entity, load_entities
from docket.vault.neverstore import slug_listed
from docket.vault.vault import Vault
from docket.web.briefs_state import BriefsState
from docket.web.briefs_view import BriefsView, Facts, build
from docket.web.day_api import Refused, safely
from docket.web.decisions_view import DONE, DecisionDoc, DecisionsPart, part


class BriefsRead(BaseModel):
    # The URL as the server writes it, which the app puts in the address bar as it is (DR-0704); never `done`.
    canonical: str
    # None while the index is being built.
    view: BriefsView | None
    # The entity's decisions, for a brief or an entity of a project, topic or stream.
    decisions: DecisionsPart | None
    # What a click did, for a URL from before the app that still names it.
    notice: str | None
    # Why the index can't answer yet; the page asks again in 5 s.
    waiting: str


class Decide(BaseModel):
    """A pick on a waiting decision: the page's brief, the decision, the rev the page showed and the pick."""

    model_config = ConfigDict(extra="forbid")

    brief: str
    decision: str
    rev: int
    pick: str


class Decided(BaseModel):
    decision: str
    pick: str
    # What the pick did, in the words the page says it with.
    notice: str
    # The decision's rev after the pick.
    rev: int


def gather(vault: Vault, state: BriefsState, now: datetime) -> Facts:
    """Everything the page shows but the decisions, read now: the report, and for an open brief its lines and the open
    line verified, with what a never-store list now names left unread."""
    tz = ZoneInfo(vault.config.owner.time_zone)
    lists = vault.config.never_store
    if state.brief is None:
        return Facts(now, tz, briefs.report(vault, stale_only=False, update=False), waiting=_waiting(vault))
    entity = state.brief.partition(":")[2]
    report = briefs.report(vault, [entity], stale_only=False, update=False)
    found = next((b for b in report.briefs if b.id == state.brief), None)
    if found is None:
        # A brief a list holds answers hidden whether or not it exists, so the page tells nothing of either: by its
        # id, or by its entity as doc_put holds it, a contact id or a nickname included.
        held = _entity(vault, entity)
        hidden = (
            bool(report.hidden) or slug_listed(entity, lists) or (held is not None and brief_form.held(held, lists))
        )
        if not hidden and held is not None and held.type in decision_form.SUBJECT_TYPES:
            on = decisions.report(vault, [entity], update=False)
            if on.decisions or on.hidden:
                name = briefs.entity_name(held, vault.config.owner.languages)
                return Facts(now, tz, report, hidden=False, entity_name=name)
        return Facts(now, tz, report, hidden=hidden)
    doc = recall.read(vault, state.brief).doc
    if doc is None:  # pragma: no cover - a doc id always reads as a doc, or raises
        raise DocketError(f"no doc {state.brief}")
    hidden = frozenset(brief_form.hidden_lines(((line.n, line.text) for line in doc.lines), vault.root, lists))
    store = DocStore(vault)
    stored = store.get(state.brief)
    standing = decision_form.standings(store.decisions())
    unconfirmed = frozenset(decision_form.unconfirmed(stored, standing)) if stored is not None else frozenset[int]()
    facts = Facts(now, tz, report, found, doc=doc, hidden_lines=hidden, unconfirmed=unconfirmed)
    if state.line is None or state.line in hidden:
        return facts
    [line] = recall.verify(vault, state.brief, lines=[state.line]).lines
    cited = brief_form.hidden_lines(enumerate(ref.text for ref in line.refs), vault.root, lists)
    return replace(facts, line=line, hidden_refs=frozenset(cited))


def _entity(vault: Vault, entity_id: str) -> Entity | None:
    return next((e for e in load_entities(vault.root).entities if e.id == entity_id), None)


def _waiting(vault: Vault) -> tuple[tuple[str, str, int], ...]:
    """Each entity with decisions waiting for the user, by name: (its id, its name, how many)."""
    waiting = Counter(d.subject for d in decisions.report(vault, status="proposed", update=False).decisions)
    by_id = {e.id: e for e in load_entities(vault.root).entities}
    languages = vault.config.owner.languages
    named = [(e, briefs.entity_name(by_id[e], languages), n) for e, n in waiting.items() if e in by_id]
    return tuple(sorted(named, key=lambda row: (row[1].casefold(), row[0])))


def decisions_of(vault: Vault, entity_id: str) -> DecisionsPart | None:
    """The Decisions part for a project, topic or stream entity, read now; None for any other entity."""
    found = _entity(vault, entity_id)
    if found is None or found.type not in decision_form.SUBJECT_TYPES:
        return None
    report = decisions.report(vault, [entity_id], update=False)
    store = DocStore(vault)
    docs: dict[str, DecisionDoc] = {}
    for one in report.decisions:
        doc = store.get(one.id)
        if doc is None:  # pragma: no cover - the report read it a moment ago
            continue
        title = decision_form.heading(doc)
        lines = tuple((n, text) for n, text in doc.lines() if title is None or n != title[0])
        hidden = brief_form.hidden_lines(lines, vault.root, vault.config.never_store)
        docs[one.id] = DecisionDoc(lines, tuple(doc.meta.covers), frozenset(hidden))
    return part(report, docs, briefs.entity_name(found, vault.config.owner.languages))


def read_page(vault: Vault, state: BriefsState, now: datetime) -> tuple[Facts, DecisionsPart | None]:
    """The page's facts, and the entity's decisions when it shows a brief or an entity's decisions."""
    facts = gather(vault, state, now)
    if state.brief is None or facts.hidden or (facts.found is None and facts.entity_name is None):
        return facts, None
    return facts, decisions_of(vault, state.brief.partition(":")[2])


def router(service: Service) -> APIRouter:
    api = APIRouter()

    async def read(request: Request) -> BriefsRead:
        state = BriefsState.read(request.query_params)
        notice = DONE[state.done] if state.done is not None else None
        try:
            await service.updater.fresh()
            facts, decided = await asyncio.to_thread(read_page, service.vault(), state, datetime.now(UTC))
        except IndexBuildingError as e:
            return BriefsRead(canonical=state.url(), view=None, decisions=None, notice=notice, waiting=str(e))
        view = build(facts, state)
        return BriefsRead(canonical=state.url(), view=view, decisions=decided, notice=notice, waiting="")

    async def decide(body: Decide) -> Response | Decided:
        state = BriefsState.read({"brief": body.brief})
        if state.brief is None:
            raise DocketError("brief is the page the decision is on")
        doc_id, pick, subject = body.decision.strip(), body.pick.strip(), state.brief.partition(":")[2]
        try:
            put = await service.writer.run(lambda: DocStore(service.vault()).decide(doc_id, body.rev, pick, subject))
        except decision_form.MovedError as e:
            return JSONResponse(Refused(error=str(e)).model_dump(mode="json"), status_code=409)
        return Decided(decision=doc_id, pick=pick, notice=DONE[pick], rev=put.rev)

    @api.get("/briefs", response_model=BriefsRead, responses={400: {"model": Refused}})
    async def briefs_read(request: Request) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: read(request))

    @api.post(
        "/briefs/decision",
        response_model=Decided,
        responses={400: {"model": Refused}, 409: {"model": Refused}},
    )
    async def briefs_decision(request: Request, body: Decide) -> Response | BaseModel:  # pyright: ignore[reportUnusedFunction]
        return await safely(request, lambda: decide(body))

    return api
