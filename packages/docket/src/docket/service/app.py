"""The service's ASGI app: one port for the MCP endpoint at /mcp, the REST API under /api and the pages.

A request is logged and its errors caught by `logs.Requests`, then passes the checks in `security.Guard`; /mcp then
goes to the MCP SDK's app, the rest to FastAPI, where the web app (`docket.web`) adds its pages and `/static/`.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from docket import __version__, web
from docket.service import api
from docket.service.logs import Requests
from docket.service.security import Guard
from docket.service.sessions import COOKIE, Sessions, moving_on, safe_next
from docket.service.state import Service, Settings
from docket.service.tools import create_mcp, security


def create_fastapi(service: Service, lifespan: object = None) -> FastAPI:
    app = FastAPI(
        title="Docket",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,  # pyright: ignore[reportArgumentType]
    )
    api.handle_errors(app)
    app.include_router(api.router(service))

    @app.get("/session", response_class=HTMLResponse)
    def session_page(  # pyright: ignore[reportUnusedFunction]
        code: str = "", after: str = Query(default="/", alias="next")
    ) -> HTMLResponse:
        started = service.sessions.redeem(code) if code else None
        if started is None:
            return HTMLResponse(web.used_link_page(safe_next(after)), status_code=401)
        # A page that moves on, not a redirect: coming from a file, a redirect's navigation stays cross-site and the
        # browser would hold back the Strict cookie on it.
        page = HTMLResponse(moving_on(safe_next(after), icon=web.ICON))
        page.set_cookie(COOKIE, started, httponly=True, samesite="strict", path="/")
        return page

    web.install(app, service)
    return app


class ServiceApp:
    """The ASGI callable uvicorn serves: the request log, the guard, then /mcp to the MCP app and the rest to
    FastAPI."""

    def __init__(self, service: Service, api_app: FastAPI, mcp: ASGIApp) -> None:
        self.service = service
        self.api = api_app
        self.mcp = mcp
        guard = Guard(
            self._dispatch,
            port=service.settings.port,
            token=service.settings.token,
            sessions=service.sessions,
            sign_in=web.sign_in_page,
        )
        self.outer = Requests(guard)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.outer(scope, receive, send)

    async def _dispatch(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] == "/mcp":
            await self.mcp(scope, receive, send)
            return
        await self.api(scope, receive, send)


def create_app(settings: Settings, *, sessions: Sessions | None = None) -> ServiceApp:
    service = Service(settings, sessions)
    server = create_mcp(service)
    # Stateless, so a restarted service needs no session from its clients; JSON, since no tool streams progress.
    mcp = server.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True, transport_security=security(settings.port)
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        async with AsyncExitStack() as stack:
            stack.callback(service.writer.close)
            # Open runs live in memory: a stopping service drops them and takes its marker away.
            stack.callback(service.runs.close_all)
            # The worker starts in the background; until it says hello, searches go without vectors.
            service.models.start()
            stack.callback(service.models.close)
            # Speech reads the machine's choice once; with none installed it stays off and starts no thread.
            service.speech.start()
            stack.callback(service.speech.close)
            await stack.enter_async_context(server.session_manager.run())
            # The index is brought up to date at start, and built when there is none; nobody waits for it here.
            service.updater.poke()
            yield

    return ServiceApp(service, create_fastapi(service, lifespan), mcp)
