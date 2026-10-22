"""The REST API under /api: the calls the MCP tools make, the service's status, the browser's one-time link, and the
run routes the capture hook posts to (bearer token only, DR-0243).

It is Docket's own: the CLI, the plugin's hooks and the web app's pages call it, shipped from this repo together, so
it is not a door until the first release. The MCP tools are.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.responses import Response

from docket import capture
from docket.errors import DocketError
from docket.index import SearchResult
from docket.index.query import MAX_K
from docket.ledger import day as ledger_day
from docket.recall import ReadResult, VerifyResult
from docket.service import calls, sync
from docket.service.models import ModelsStatus
from docket.service.runs import BUDGET, CAPTURE_CAP
from docket.service.sessions import CODE_SECONDS
from docket.service.speech import SpeechJob, SpeechStatus
from docket.service.state import Service, Status
from docket.vault.docstore import PutResult
from docket.vault.processed import DocRefusedError
from docket.vault.spans import WINDOW

# A doc is a few KB; anything this big isn't one. The MCP SDK caps /mcp at the same.
MAX_BODY = 4 * 1024 * 1024
# The paths whose clients read JSON, never a page: their failures and misses stay JSON.
MACHINE_PATHS = ("/api", "/mcp")


# A run's open, bind or close is a few KB; a capture is up to CAPTURE_CAP.
RUN_BODY = 1024 * 1024


class OpenBody(BaseModel):
    source: str
    since: str | None = None
    final: bool = False
    session: str | None = None
    agent: str | None = None


class BindBody(BaseModel):
    session: str
    agent: str | None = None


class CaptureBody(BaseModel):
    session: str
    agent: str | None = None
    tool_name: str
    tool_use_id: str
    arguments: dict[str, Any] = Field(default_factory=dict[str, Any])
    result: Any = None
    captured: datetime | None = None


class CloseBody(BaseModel):
    captures: int
    final: bool = False
    skipped: list[sync.Skip] = Field(default_factory=list[sync.Skip])
    folder: str | None = None


class MediaBody(BaseModel):
    rid: str
    url: str | None = None
    path: str | None = None


class Bound(BaseModel):
    run: str
    session: str


class KeptCapture(BaseModel):
    capture: int
    used: int
    budget: int


class OpenRuns(BaseModel):
    runs: list[dict[str, Any]]


def bearer_only(request: Request) -> None:
    """The run routes take the token, never the browser's session: no page has a reason to open runs or post
    captures. A wrong token never gets this far; the guard answers it."""
    if request.headers.get("authorization") is None:
        raise HTTPException(status_code=401, detail="the run routes take the bearer token only")


async def capped_body(request: Request, cap: int) -> bytes:
    """The body, read as it streams and refused once past the cap, whether or not it said its length."""
    size = request.headers.get("content-length")
    if size is not None and size.isdigit() and int(size) > cap:
        raise HTTPException(status_code=413, detail=f"a body here is at most {cap} bytes")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > cap:
            raise HTTPException(status_code=413, detail=f"a body here is at most {cap} bytes")
    return bytes(body)


def parsed[M: BaseModel](model: type[M], body: bytes) -> M:
    try:
        return model.model_validate_json(body)
    except ValidationError as e:
        # The location and type only: a value can be anything the caller sent.
        first = e.errors(include_url=False, include_input=False, include_context=False)[0]
        where = ".".join(str(p) for p in first["loc"]) or "the body"
        raise DocketError(f"{where} isn't valid ({first['type']})") from None


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

    @api.get("/models")
    def models() -> ModelsStatus:  # pyright: ignore[reportUnusedFunction]
        # The models alone, for the CLI: status also hashes the vault's files for the index's state.
        return service.models.status()

    @api.get("/speech")
    def speech() -> SpeechStatus:  # pyright: ignore[reportUnusedFunction]
        return service.speech.status()

    @api.post("/transcribe")
    async def transcribe(  # pyright: ignore[reportUnusedFunction]
        request: Request, rid: Annotated[str, Query(max_length=512)]
    ) -> SpeechJob:
        # Only a client with the token queues a job: no page has a reason to (DR-0571).
        bearer_only(request)
        found, _ = await calls.transcribe(service, rid)
        return found

    @api.post("/session")
    def session_link() -> SessionLink:  # pyright: ignore[reportUnusedFunction]
        code = service.sessions.new_code()
        return SessionLink(
            url=f"{origin}/session?code={code}", expires_at=datetime.now(UTC) + timedelta(seconds=CODE_SECONDS)
        )

    @api.get("/day")
    async def day(  # pyright: ignore[reportUnusedFunction]
        room: Annotated[int, Query(ge=0)] = ledger_day.ROOM,
    ) -> ledger_day.DayAnswer:
        # The session start's hook asks it with the room its own lines leave (DR-0670).
        return await calls.day(service, room)

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

    @api.get("/runs")
    def open_runs(request: Request) -> OpenRuns:  # pyright: ignore[reportUnusedFunction]
        bearer_only(request)
        return OpenRuns(runs=service.runs.listing())

    @api.post("/runs")
    async def run_open(request: Request) -> sync.RunOpened:  # pyright: ignore[reportUnusedFunction]
        bearer_only(request)
        body = parsed(OpenBody, await capped_body(request, RUN_BODY))
        opened, _ = await sync.run_open(
            service, body.source, since=body.since, final=body.final, session=body.session, agent=body.agent
        )
        return opened

    @api.post("/runs/{run}/session")
    async def bind(run: str, request: Request) -> Bound:  # pyright: ignore[reportUnusedFunction]
        bearer_only(request)
        body = parsed(BindBody, await capped_body(request, RUN_BODY))
        service.runs.bind(run, body.session, body.agent)
        return Bound(run=run, session=body.session)

    @api.post("/runs/{run}/captures")
    async def post_capture(run: str, request: Request) -> KeptCapture:  # pyright: ignore[reportUnusedFunction]
        bearer_only(request)
        try:
            raw = await capped_body(request, CAPTURE_CAP)
        except HTTPException:
            service.runs.mark_short(run, f"a capture past {CAPTURE_CAP // (1024 * 1024)} MiB")
            raise
        body = parsed(CaptureBody, raw)
        size = len(json.dumps(body.result, ensure_ascii=False).encode("utf-8"))
        kept = service.runs.capture(
            run,
            session=body.session,
            agent=body.agent,
            tool=capture.own_name(body.tool_name) or body.tool_name,
            tool_use_id=body.tool_use_id,
            arguments=body.arguments,
            result=body.result,
            captured=body.captured if body.captured and body.captured.tzinfo else datetime.now(UTC),
            size=size,
        )
        return KeptCapture(capture=kept.number, used=kept.used, budget=BUDGET)

    @api.post("/media")
    async def media(request: Request) -> calls.MediaPut:  # pyright: ignore[reportUnusedFunction]
        # media_put's own call (DR-0547), for `docket sync pull`, which puts a pull's audio side by side (#305).
        bearer_only(request)
        body = parsed(MediaBody, await capped_body(request, RUN_BODY))
        stored, _ = await calls.media_put(service, body.rid, path=body.path, url=body.url)
        return stored

    @api.post("/runs/{run}/close")
    async def close(run: str, request: Request) -> sync.RunClosed:  # pyright: ignore[reportUnusedFunction]
        bearer_only(request)
        body = parsed(CloseBody, await capped_body(request, RUN_BODY))
        closed, _ = await sync.ingest(
            service, run, captures=body.captures, final=body.final, skipped=body.skipped, folder=body.folder
        )
        return closed

    return api


def handle_errors(app: FastAPI, page: Callable[[], Response] | None = None) -> None:
    """Docket's own errors as answers the caller can act on; anything else a plain 500, its details in the log only:
    on a page path, outside /api and /mcp, `page`'s answer, which names no detail either."""

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
    async def internal(request: Request, _error: Exception) -> Response:  # pyright: ignore[reportUnusedFunction]
        path = request.url.path
        if page is not None and not any(path == p or path.startswith(f"{p}/") for p in MACHINE_PATHS):
            return page()
        return JSONResponse({"error": "internal error; see the service log"}, status_code=500)
