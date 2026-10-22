"""The web app: one React app (DR-0703), its build served under `/static/app/` and its document for each route its
`routes.json` lists (`frontend`), beside its JSON routes under `/api/web` (`webapi`, with the pages' own: `day_api`,
`search`, `recording`, `briefs`, `staging`, `curate`, `sync` and `settings`). `/` answers 303 to Today, the start page
(DR-0669); any other page path with a session answers the same document and 404, which the app shows as not found
(DR-0792).

The pages a browser reaches without a session, the sign-in and used-link pages, and the page a failure on a page path
answers, are plain HTML the service writes, with no script (`bare`, DR-0793); they name neither the vault nor the
machine. `install` mounts it all on the service's FastAPI app.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, RedirectResponse, Response

from docket.service.state import Service
from docket.web import assets, bare, frontend, webapi

# Paths whose 404 stays JSON: the clients of these read JSON, never a page.
MACHINE_PATHS = ("/api", "/mcp")
# The icon every page the service answers names, served without a session like the other static files.
ICON = bare.ICON
# Where the React app's build is served, under /static/.
APP_FILES = "app/"
READS = ("GET", "HEAD")


def sign_in_page(wanted: str) -> Response:
    """What the Guard answers a browser without a session: the plain sign-in page, 401."""
    return bare.sign_in(wanted)


def used_link(after: str) -> Response:
    """What a used or stale one-time link gets: the plain used-link page, 401."""
    return bare.used_link(after)


def failed() -> Response:
    """What an unexpected failure on a page path answers: the plain page that says so, 500, naming no detail."""
    return bare.failed()


def _machine(path: str) -> bool:
    return any(path == p or path.startswith(f"{p}/") for p in MACHINE_PATHS)


def install(app: FastAPI, service: Service) -> None:
    app.include_router(webapi.router(service))
    app.add_middleware(webapi.WriteBodies)

    @app.api_route("/static/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def static(request: Request, path: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        if path.startswith(APP_FILES):
            return frontend.serve(request, path.removeprefix(APP_FILES))
        return assets.serve(request, path)

    def app_page(request: Request) -> Response:
        return frontend.document(request)

    for route in frontend.BUILD.routes:
        app.add_api_route(route, app_page, methods=["GET"], include_in_schema=False)

    @app.get("/", include_in_schema=False)
    def start() -> Response:  # pyright: ignore[reportUnusedFunction]
        # Today is the start page (DR-0669); a link to / or to `docket open`'s landing goes there.
        return RedirectResponse("/today", status_code=303)

    async def not_found(request: Request, error: Exception) -> Response:
        path = request.url.path
        missing = isinstance(error, HTTPException) and error.status_code == 404
        if not missing or _machine(path) or request.method not in READS:
            return await http_exception_handler(request, error)  # pyright: ignore[reportArgumentType]
        # The app's own document, which shows its not-found page in the shell; the path itself is never echoed.
        return frontend.document(request, status=404)

    async def unreadable(request: Request, error: Exception) -> Response:
        # A body the app's routes can't read: where and why, never what it held, which can be a never-store value.
        if not request.url.path.startswith(f"{webapi.PREFIX}/") or not isinstance(error, RequestValidationError):
            return await request_validation_exception_handler(request, error)  # pyright: ignore[reportArgumentType]
        said = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")} for e in error.errors()
        ]
        return JSONResponse({"detail": said}, status_code=422)

    app.add_exception_handler(HTTPException, not_found)
    app.add_exception_handler(RequestValidationError, unreadable)
