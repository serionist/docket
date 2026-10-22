"""The checks every request passes before it reaches a route, and the headers every response carries.

| Check | On |
|---|---|
| Host is 127.0.0.1:<port> or localhost:<port> | every request: a site rebinding its name to 127.0.0.1 sends its own |
| Origin, when sent, is this origin; Sec-Fetch-Site, when sent, is same-origin | every write |
| a write made with the session cookie carries Origin or Sec-Fetch-Site | every write |
| the bearer token, compared in constant time | /mcp always, /api when there is no session |
| the session cookie | the pages, and /api when there is no bearer token |

GET and HEAD under /static/ need no session: they are the web app's own files, the same for everyone, and the sign-in
page needs its stylesheet (DR-0793). The Host check still applies to them.

No CORS header is ever sent, so no page of another origin reads an answer or adds a header a preflight would allow.
"""

from __future__ import annotations

import hmac
import html
from collections.abc import Callable
from typing import Literal

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import HTTPConnection
from starlette.responses import HTMLResponse, PlainTextResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from docket.service.sessions import COOKIE, Sessions, open_command

Route = Literal["mcp", "health", "session-api", "api", "session-page", "static", "page"]
SAFE_METHODS = frozenset({"GET", "HEAD"})

HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    # A floor the pages build on: nothing from elsewhere, no framing, forms post here only.
    "Content-Security-Policy": "default-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
}
NO_STORE = ("/api", "/mcp", "/session")


def plain_sign_in(wanted: str) -> Response:
    """The sign-in page without the web app: the command that opens the page asked for."""
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Docket</title></head><body>'
        f"<p>Open Docket from a terminal on this machine: <code>{html.escape(open_command(wanted))}</code></p>"
        "</body></html>\n"
    )
    return HTMLResponse(page, status_code=401, headers={"Cache-Control": "no-store"})


def route_of(path: str, method: str) -> Route:
    if path == "/mcp" or path.startswith("/mcp/"):
        return "mcp"
    if path == "/api/health":
        return "health"
    if path == "/api/session" and method == "POST":
        return "session-api"
    if path == "/api" or path.startswith("/api/"):
        return "api"
    if path == "/session":
        return "session-page"
    if path.startswith("/static/") and method in SAFE_METHODS:
        return "static"
    return "page"


class Guard:
    """The ASGI layer in front of the app: refuses what fails a check, and adds the headers on the way out."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        port: int,
        token: Callable[[], str],
        sessions: Sessions,
        sign_in: Callable[[str], Response] = plain_sign_in,
    ) -> None:
        self.app = app
        self.sign_in = sign_in
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.origins = {f"http://{host}" for host in self.hosts}
        self.token = token
        self.sessions = sessions

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        send = self._with_headers(scope, send)
        refusal = self.refusal(scope)
        if refusal is not None:
            await refusal(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def refusal(self, scope: Scope) -> Response | None:
        """The answer to a request that fails a check, or None when it passes them all."""
        headers = Headers(scope=scope)
        method: str = scope["method"]
        if headers.get("host") not in self.hosts:
            return PlainTextResponse("unknown host", status_code=421)
        if method == "OPTIONS":
            return PlainTextResponse("no cross-origin requests", status_code=405)
        origin, site = headers.get("origin"), headers.get("sec-fetch-site")
        write = method not in SAFE_METHODS
        if write and ((origin is not None and origin not in self.origins) or (site not in (None, "same-origin"))):
            return PlainTextResponse("cross-origin writes are refused", status_code=403)
        route = route_of(scope["path"], method)
        if route in ("health", "session-page", "static"):
            return None
        bearer = self._bearer(headers)
        if bearer is False:
            return _unauthorized()
        if route in ("mcp", "session-api"):
            return None if bearer else _unauthorized()
        session = self.sessions.valid(HTTPConnection(scope).cookies.get(COOKIE))
        if not bearer and not session:
            if route == "api":
                return _unauthorized()
            # 401 and no-store: the page asked for is the user's own words (a query string), so it isn't stored either.
            return self.sign_in(_wanted(scope))
        if write and not bearer and origin is None and site is None:
            # A browser sends one of the two on every write; a write that carries a cookie and neither isn't ours.
            return PlainTextResponse("a write with the session cookie needs its Origin", status_code=403)
        return None

    def _bearer(self, headers: Headers) -> bool | None:
        """True for the right token, False for a wrong one, None when none was sent."""
        value = headers.get("authorization")
        if value is None:
            return None
        scheme, _, given = value.partition(" ")
        if scheme.lower() != "bearer" or not given:
            return False
        return hmac.compare_digest(given.strip().encode("utf-8"), self.token().encode("utf-8"))

    @staticmethod
    def _with_headers(scope: Scope, send: Send) -> Send:
        path: str = scope["path"]
        no_store = any(path == p or path.startswith(f"{p}/") for p in NO_STORE)

        async def sending(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in HEADERS.items():
                    if name not in headers:
                        headers[name] = value
                if no_store:
                    headers["Cache-Control"] = "no-store"
            await send(message)

        return sending


def _wanted(scope: Scope) -> str:
    """The path and query a request asked for, as the browser sent them."""
    raw: bytes = scope.get("raw_path") or scope["path"].encode("utf-8")
    query: bytes = scope.get("query_string", b"")
    return raw.decode("latin-1") + (f"?{query.decode('latin-1')}" if query else "")


def _unauthorized() -> Response:
    return PlainTextResponse("a bearer token is needed", status_code=401, headers={"WWW-Authenticate": "Bearer"})
