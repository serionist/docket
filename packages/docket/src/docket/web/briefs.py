"""The Briefs page: `/briefs` is always a whole page, and its links are plain links (DR-0557); `/briefs/decision` is
the user's click on a decision, which writes its status as `docket decision set` does (DR-0603, DR-0566).

`GET` reads the stale report, the brief, the raw its lines cite and the entity's decisions in this process, through
`docket.briefs`, `docket.decisions` and recall's own reads, and writes nothing. The report reads the index, so the page
waits for an update as a search does.

The click is a plain form post the Guard checks (DR-0152), as the Settings page's are. It runs on the service's writer
thread through `DocStore.decide`, carries the rev the page showed, and answers a 303 back to the page with `done`; a
decision that moved since, or one that replaces or contradicts a decision no longer current, writes nothing and answers
409 with the page as it stands now. A value the URL or the form can't hold, or a `DocketError`, is the page's own error
state; anything else is logged as its type and frames and answered with the error page, as on the other pages (DR-0170).
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from starlette.responses import RedirectResponse, Response

from docket import briefs, decisions, recall
from docket.errors import DocketError
from docket.service.state import Service
from docket.service.writer import IndexBuildingError
from docket.vault import brief_form, decision_form
from docket.vault.docstore import DocStore
from docket.vault.entities import Entity, load_entities
from docket.vault.neverstore import slug_listed
from docket.vault.vault import Vault
from docket.web import templating
from docket.web.briefs_state import BriefsState, BriefsStateError
from docket.web.briefs_view import BriefsView, Facts, build
from docket.web.decisions_view import DONE, DecisionDoc, DecisionsPart, part
from docket.web.shell import shell

# The words a refused click names its field with.
REV = "rev is the rev of the decision the page showed"


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


def _page(
    context: dict[str, Any], view: BriefsView | None, state: BriefsState, problem: str = "", *, status: int = 200
) -> Response:
    page = {"warning": "", "done": ""} | context | {"view": view, "state": state, "problem": problem}
    return templating.page("briefs/page.html", page, status=status, referrer=templating.POSTS_FORMS)


def router(service: Service) -> APIRouter:
    web = APIRouter()

    async def whole(request: Request, state: BriefsState, *, status: int = 200, warning: str = "") -> Response:
        """The page for a state, or its error state, with what the last click did or why it wrote nothing."""
        context = shell(service, request, "Briefs", current="briefs")
        context |= {"warning": warning, "done": DONE.get(state.done or "", "")}
        try:
            await service.updater.fresh()
            facts, decided = await asyncio.to_thread(read_page, service.vault(), state, datetime.now(UTC))
        except IndexBuildingError as e:
            return _page(context, None, state, str(e), status=503)
        except DocketError as e:
            return _page(context, None, state, str(e), status=400)
        view = build(facts, state.but(done=None))
        view.decisions = decided
        if view.mode != "list":
            context["title"] = f"{view.title} - Briefs"
        return _page(context, view, state, status=404 if view.mode == "missing" else status)

    def failed(request: Request) -> Response:
        context = shell(None, request, "Something failed", current="briefs")
        return templating.page("error.html", context, status=500)

    def refused(request: Request, state: BriefsState, problem: str) -> Response:
        return _page(shell(service, request, "Briefs", current="briefs"), None, state, problem, status=400)

    @web.get("/briefs", include_in_schema=False)
    async def briefs_page(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            try:
                state = BriefsState.read(request.query_params)
            except BriefsStateError as e:
                return refused(request, BriefsState(), str(e))
            return await whole(request, state)

        return await templating.safely(request, "/briefs", answer, lambda: failed(request))

    @web.post("/briefs/decision", include_in_schema=False)
    async def decide(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def answer() -> Response:
            form = await templating.read_form(request)
            if isinstance(form, Response):
                return form
            try:
                state = BriefsState.read({"brief": form.get("brief") or "", "type": form.get("type") or ""})
            except BriefsStateError as e:
                return refused(request, BriefsState(), str(e))
            if state.brief is None:
                return refused(request, state, "brief is the page the decision is on")
            rev = (form.get("rev") or "").strip()
            doc_id, pick = (form.get("decision") or "").strip(), (form.get("pick") or "").strip()
            subject = state.brief.partition(":")[2]
            try:
                if not rev.isascii() or not rev.isdigit():
                    raise DocketError(REV)
                shown = int(rev)
                await service.writer.run(lambda: DocStore(service.vault()).decide(doc_id, shown, pick, subject))
            except decision_form.MovedError as e:
                return await whole(request, state, status=409, warning=str(e))
            except DocketError as e:
                return await whole(request, state, status=400, warning=str(e))
            return RedirectResponse(state.but(done=pick).url(), status_code=303)

        return await templating.safely(request, "/briefs/decision", answer, lambda: failed(request))

    return web
