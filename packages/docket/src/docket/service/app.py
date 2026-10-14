"""The service's ASGI app: one port for the MCP endpoint at /mcp, the REST API under /api and the pages.

A request passes the checks in `security.Guard` before anything else; /mcp then goes to the MCP SDK's app, the rest
to FastAPI. The web app's pages (#9) add a router; this keeps `/` as a placeholder until then.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from docket import __version__
from docket.errors import DocketError
from docket.service.security import Guard
from docket.service.sessions import CODE_SECONDS, COOKIE, Sessions, moving_on, safe_next
from docket.vault.vault import Vault

# How long a write of the service waits for another Docket process to let go of the vault: less than a client's
# timeout, so the client gets Docket's message rather than its own.
LOCK_SECONDS = 15.0


@dataclass
class Settings:
    port: int
    vault: Path | None
    token: Callable[[], str]
    # Where the token lives, for status: the system keychain or the file's path.
    store: str = "nowhere yet"
    started: datetime = field(default_factory=lambda: datetime.now(UTC))


class Health(BaseModel):
    service: str = "docket"


class Status(BaseModel):
    service: str = "docket"
    version: str
    pid: int
    port: int
    started: datetime
    vault: str | None
    machine: str | None
    vault_problem: str | None
    # Where the service token lives.
    store: str


class SessionLink(BaseModel):
    url: str
    expires_at: datetime


class Service:
    """What the routes share: the settings, the browser sessions, the vault."""

    def __init__(self, settings: Settings, sessions: Sessions | None = None) -> None:
        self.settings = settings
        self.sessions = sessions or Sessions()

    def vault(self) -> Vault:
        """The vault, opened for this call, so an edit to vault.yaml applies at once."""
        if self.settings.vault is None:
            raise DocketError("the service has no vault; run `docket service install --vault <folder>`")
        return Vault.open(self.settings.vault, lock_timeout=LOCK_SECONDS)

    def status(self) -> Status:
        vault: Vault | None = None
        problem: str | None = None
        try:
            vault = self.vault()
        except DocketError as e:
            problem = str(e)
        except OSError as e:
            # A vault the service may not read, such as one under a macOS folder its Python has no permission for.
            problem = f"can't read the vault: {e.strerror or type(e).__name__}"
        return Status(
            version=__version__,
            pid=os.getpid(),
            port=self.settings.port,
            started=self.settings.started,
            vault=str(vault.root if vault else self.settings.vault) if (vault or self.settings.vault) else None,
            machine=vault.machine if vault else None,
            vault_problem=problem,
            store=self.settings.store,
        )


def create_fastapi(service: Service, lifespan: Callable[[FastAPI], Any] | None = None) -> FastAPI:
    app = FastAPI(
        title="Docket", version=__version__, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    origin = f"http://127.0.0.1:{service.settings.port}"

    @app.get("/api/health")
    def health() -> Health:  # pyright: ignore[reportUnusedFunction]
        return Health()

    @app.get("/api/status")
    def status() -> Status:  # pyright: ignore[reportUnusedFunction]
        return service.status()

    @app.post("/api/session")
    def session_link() -> SessionLink:  # pyright: ignore[reportUnusedFunction]
        code = service.sessions.new_code()
        expires = datetime.now(UTC) + timedelta(seconds=CODE_SECONDS)
        return SessionLink(url=f"{origin}/session?code={code}", expires_at=expires)

    @app.get("/session", response_class=HTMLResponse)
    def session_page(  # pyright: ignore[reportUnusedFunction]
        code: str = "", after: str = Query(default="/", alias="next")
    ) -> HTMLResponse:
        started = service.sessions.redeem(code) if code else None
        if started is None:
            return HTMLResponse(
                "<!doctype html><title>Docket</title><p>This link was used or is over a minute old. Run "
                "<code>docket open</code> again.</p>\n",
                status_code=401,
            )
        # A page that moves on, not a redirect: coming from a file, a redirect's navigation stays cross-site and the
        # browser would hold back the Strict cookie on it.
        page = HTMLResponse(moving_on(safe_next(after)))
        page.set_cookie(COOKIE, started, httponly=True, samesite="strict", path="/")
        return page

    @app.get("/", response_class=HTMLResponse)
    def start() -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        return HTMLResponse(
            "<!doctype html><title>Docket</title><p>Docket is running. The web app's pages arrive with its next "
            "version.</p>\n"
        )

    return app


class ServiceApp:
    """The ASGI callable uvicorn serves: the guard, then /mcp to the MCP app and the rest to FastAPI."""

    def __init__(self, service: Service, api: FastAPI, mcp: ASGIApp | None = None) -> None:
        self.service = service
        self.api = api
        self.mcp = mcp
        self.guard = Guard(
            self._dispatch, port=service.settings.port, token=service.settings.token, sessions=service.sessions
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.guard(scope, receive, send)

    async def _dispatch(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] == "/mcp":
            if self.mcp is None:
                await PlainTextResponse("no MCP endpoint", status_code=404)(scope, receive, send)
                return
            await self.mcp(scope, receive, send)
            return
        await self.api(scope, receive, send)


def create_app(settings: Settings, *, sessions: Sessions | None = None) -> ServiceApp:
    service = Service(settings, sessions)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        async with AsyncExitStack():
            yield

    return ServiceApp(service, create_fastapi(service, lifespan))
