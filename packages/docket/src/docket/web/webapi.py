"""The browser's own JSON routes, under /api/web (DR-0709). They take the session cookie only, never the bearer token,
and answer values ready to show, built by the same Python the pages use; their Pydantic models are the contract,
whose OpenAPI document `uv run devkit web-schema` writes to packages/docket/web/openapi.json for the app's types.

PR 1 of #252 has the shell's one read; each page adds its own as it moves.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from docket.errors import DocketError
from docket.ledger import day as ledger_day
from docket.ledger import views
from docket.service.state import Service
from docket.vault.staging import Staging
from docket.web import shell as shell_parts

PREFIX = "/api/web"
TITLE = "Docket's web app API"


class Shell(BaseModel):
    """What the sidebar shows beside its pages (DR-0708): the counts and marks, and the vault, machine and index line
    at its foot. A count is None when its part of the vault doesn't read; `problem` says why the vault doesn't open."""

    vault: str | None
    machine: str | None
    # The owner's time zone, as vault.yaml names it: the zone every time the app shows is in.
    zone: str | None
    index_line: str
    # Open items with no call yet, as the Queue lists them.
    untriaged: int | None
    # Records waiting at the gate: pending, ready and on hold, the staged files as they stand. The Staging page's
    # sweep, when it opens, may drop some first.
    staging: int | None
    # The live sources whose sync is due, by name; none while a run of theirs is open.
    sync_due: list[str]
    problem: str | None


def session_only(request: Request) -> None:
    """A browser's call: the token is for Claude Code and the CLI, so a call carrying it is refused here, and the
    Guard has already refused one carrying neither it nor a session."""
    if request.headers.get("authorization") is not None:
        raise HTTPException(status_code=403, detail="the web app's routes take the browser's session only")


async def read_shell(service: Service) -> Shell:
    index_line = shell_parts.index_line(service)
    try:
        vault = service.vault()
    except (DocketError, OSError) as e:
        problem = str(e) if isinstance(e, DocketError) else f"can't read the vault: {e.strerror or type(e).__name__}"
        return Shell(
            vault=None,
            machine=None,
            zone=None,
            index_line=index_line,
            untriaged=None,
            staging=None,
            sync_due=[],
            problem=problem,
        )
    running = {run.source for run in service.runs.snapshot()}

    def ledger() -> tuple[int | None, list[str]]:
        try:
            day = ledger_day.read(vault, views.now(), running)
        except (DocketError, OSError):
            return None, []
        return sum(1 for i in day.queue if i.triage is None), [s.source for s in day.syncs if s.due]

    def waiting() -> int | None:
        # The staged files as they stand, read beside the writer, never on it: the shell's read writes nothing and
        # takes no lock. The gate's sweep, which may drop a record, runs when the Staging page opens.
        try:
            return len(Staging(vault.root, vault.home).scan().entries)
        except (DocketError, OSError):
            return None

    (untriaged, due), staging = await asyncio.gather(asyncio.to_thread(ledger), asyncio.to_thread(waiting))
    return Shell(
        vault=vault.root.name,
        machine=vault.machine,
        zone=vault.config.owner.time_zone,
        index_line=index_line,
        untriaged=untriaged,
        staging=staging,
        sync_due=due,
        problem=None,
    )


def router(service: Service) -> APIRouter:
    api = APIRouter(prefix=PREFIX, dependencies=[Depends(session_only)])

    @api.get("/shell")
    async def shell() -> Shell:  # pyright: ignore[reportUnusedFunction]
        return await read_shell(service)

    return api


def openapi() -> dict[str, Any]:
    """The routes' OpenAPI document. Building it reads only the routes' models, so no service is needed."""
    app = FastAPI(title=TITLE, version="1", docs_url=None, redoc_url=None)
    app.include_router(router(cast("Service", None)))
    return app.openapi()


def openapi_text() -> str:
    """The document as openapi.json holds it: sorted, indented, one newline at the end."""
    return json.dumps(openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
