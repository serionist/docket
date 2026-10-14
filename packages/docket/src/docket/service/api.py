"""The REST API under /api: the four calls the MCP tools make, the service's status, and the browser's one-time link.

It is Docket's own: the CLI, the plugin's hooks and the web app's pages call it, shipped from this repo together, so
it is not a door until the first release. The MCP tools are.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from docket.errors import DocketError
from docket.index import SearchResult
from docket.index.query import MAX_K
from docket.recall import ReadResult, VerifyResult
from docket.service import calls
from docket.service.sessions import CODE_SECONDS
from docket.service.state import Service, Status
from docket.vault.docstore import PutResult
from docket.vault.processed import DocRefusedError
from docket.vault.spans import WINDOW

# A doc is a few KB; anything this big isn't one. The MCP SDK caps /mcp at the same.
MAX_BODY = 4 * 1024 * 1024


class Health(BaseModel):
    service: str = "docket"


class SessionLink(BaseModel):
    url: str
    expires_at: datetime


def router(service: Service) -> APIRouter:
    api = APIRouter(prefix="/api")
    origin = f"http://127.0.0.1:{service.settings.port}"

    @api.get("/health")
    def health() -> Health:  # pyright: ignore[reportUnusedFunction]
        return Health()

    @api.get("/status")
    def status() -> Status:  # pyright: ignore[reportUnusedFunction]
        return service.status()

    @api.post("/session")
    def session_link() -> SessionLink:  # pyright: ignore[reportUnusedFunction]
        code = service.sessions.new_code()
        return SessionLink(
            url=f"{origin}/session?code={code}", expires_at=datetime.now(UTC) + timedelta(seconds=CODE_SECONDS)
        )

    @api.get("/search")
    async def search(  # pyright: ignore[reportUnusedFunction]
        query: str,
        *,
        people: Annotated[list[str] | None, Query()] = None,
        entities: Annotated[list[str] | None, Query()] = None,
        sources: Annotated[list[str] | None, Query()] = None,
        since: str | None = None,
        until: str | None = None,
        k: Annotated[int, Query(ge=1, le=MAX_K)] = 20,
    ) -> SearchResult:
        found, _ = await calls.search(
            service,
            query,
            people=people or [],
            entities=entities or [],
            sources=sources or [],
            since=since,
            until=until,
            k=k,
        )
        return found

    @api.get("/read")
    async def read(  # pyright: ignore[reportUnusedFunction]
        locator: str,
        view: Literal["summary", "raw", "both"] = "summary",
        around: Annotated[int, Query(ge=0)] = 0,
        rev: Annotated[int | None, Query(ge=1)] = None,
    ) -> ReadResult:
        found, _ = await calls.read(service, locator, view=view, around=around, rev=rev)
        return found

    @api.get("/verify")
    async def verify(  # pyright: ignore[reportUnusedFunction]
        doc: str,
        lines: Annotated[list[int] | None, Query()] = None,
        window: Annotated[int, Query(ge=1)] = WINDOW,
    ) -> VerifyResult:
        found, _ = await calls.verify(service, doc, lines=lines, window=window)
        return found

    @api.post("/docs")
    async def docs(request: Request) -> PutResult:  # pyright: ignore[reportUnusedFunction]
        size = request.headers.get("content-length")
        if size is not None and size.isdigit() and int(size) > MAX_BODY:
            raise DocketError(f"a doc is at most {MAX_BODY // (1024 * 1024)} MiB")
        body = await request.body()
        if len(body) > MAX_BODY:
            raise DocketError(f"a doc is at most {MAX_BODY // (1024 * 1024)} MiB")
        try:
            text = body.decode("utf-8").removeprefix("﻿")
        except UnicodeDecodeError:
            raise DocketError("a doc is UTF-8 text") from None
        stored, _ = await calls.doc_put(service, text)
        return stored

    return api


def handle_errors(app: FastAPI) -> None:
    """Docket's own errors as answers the caller can act on; anything else a plain 500, its details in the log only."""

    @app.exception_handler(DocRefusedError)
    async def refused(_request: Request, error: Exception) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        problems = error.problems if isinstance(error, DocRefusedError) else []
        return JSONResponse(
            {"problems": [{"line": p.line, "message": p.message, "excerpt": p.excerpt} for p in problems]},
            status_code=422,
        )

    @app.exception_handler(DocketError)
    async def docket_error(_request: Request, error: Exception) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return JSONResponse({"error": str(error)}, status_code=400)

    @app.exception_handler(Exception)
    async def internal(_request: Request, _error: Exception) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return JSONResponse({"error": "internal error; see the service log"}, status_code=500)
