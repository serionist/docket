"""The web app: pages rendered on the server with Jinja2, parts of them swapped by htmx, every state a URL that
renders without JavaScript (DR-0170). Nothing loads from the network: htmx, the fonts and the icons are files in this
package, served under `/static/` (DR-0171), and every page carries a CSP stricter than the service's floor (DR-0172).

`install` mounts it on the service's FastAPI app; `sign_in_page` and `used_link_page` are the pages the service shows
a browser without a session, in the bare layout that names neither the vault nor the machine.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException
from starlette.responses import RedirectResponse, Response

from docket.service.sessions import open_command
from docket.service.state import Service
from docket.web import assets, briefs, curate, recording, search, settings, staging, sync, templating

# Paths whose 404 stays JSON: the clients of these read JSON, never a page.
MACHINE_PATHS = ("/api", "/mcp")
# The icon every page the service answers names, served without a session like the other static files.
ICON = "/static/icon.svg"


def sign_in_page(wanted: str) -> str:
    """The page a browser without a session gets, naming the command that opens the page it asked for."""
    return templating.render("signin.html", {"command": open_command(wanted), "title": "Sign in"})


def used_link_page(after: str) -> str:
    """The page a used or stale one-time link gets: run `docket open` again, for the same page."""
    return templating.render("used_link.html", {"command": open_command(after), "title": "Link used"})


def install(app: FastAPI, service: Service) -> None:
    app.include_router(search.router(service))
    app.include_router(sync.router(service))
    app.include_router(staging.router(service))
    app.include_router(curate.router(service))
    app.include_router(settings.router(service))
    app.include_router(recording.router(service))
    app.include_router(briefs.router(service))

    @app.api_route("/static/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def static(request: Request, path: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        return assets.serve(request, path)

    @app.get("/", include_in_schema=False)
    def start() -> Response:  # pyright: ignore[reportUnusedFunction]
        # Search is the first page; Today takes this in phase 3.
        return RedirectResponse("/search", status_code=303)

    async def not_found(request: Request, error: Exception) -> Response:
        path = request.url.path
        if not isinstance(error, HTTPException) or error.status_code != 404 or path.startswith(MACHINE_PATHS):
            return await http_exception_handler(request, error)  # pyright: ignore[reportArgumentType]
        return templating.page("not_found.html", {"title": "Not found"}, status=404)

    app.add_exception_handler(HTTPException, not_found)
