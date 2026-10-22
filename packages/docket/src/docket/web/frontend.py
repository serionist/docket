"""The React app's build (DR-0703, DR-0705): the files `npm run build` commits under `docket/web/app/`, read once at
start, its scripts, styles and fonts served under `/static/app/` and its document for each route `routes.json` lists.

The document goes out only through those routes, and with 404 for a page path the app has no route for, each time
with a fresh style nonce in its meta element and its CSP, and with the theme the `docket_theme` cookie chose, so the
first paint is right (DR-0706). A browser without a session never gets this far: the Guard answers it with the
sign-in page.
"""

from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from docket.web import assets
from docket.web.shell import prefs

DOCUMENT = "index.html"
ROUTES = "routes.json"
STAMP = "build.json"
# What the build leaves in the document for the service to fill: the nonce's meta element and the theme.
NONCE_MARK = 'content="docket-nonce"'
THEME_MARK = 'data-theme="system"'
NONCE_BYTES = 16


@dataclass(frozen=True)
class Build:
    files: dict[str, assets.Asset]
    document: str
    routes: tuple[str, ...]
    # The hash of the sources it was built from (build.json), which the gate holds to the sources.
    inputs: str


def load(folder: Traversable | None = None) -> Build:
    """The build in the folder; one without its document, the marks in it, or its routes stops the service."""
    folder = folder or files("docket.web").joinpath("app")
    document = folder.joinpath(DOCUMENT).read_text(encoding="utf-8")
    for mark in (NONCE_MARK, THEME_MARK):
        if document.count(mark) != 1:
            raise ValueError(f"the web app's {DOCUMENT} holds {mark} {document.count(mark)} times, not once")
    routes: list[str] = json.loads(folder.joinpath(ROUTES).read_text(encoding="utf-8"))
    stamp: dict[str, str] = json.loads(folder.joinpath(STAMP).read_text(encoding="utf-8"))
    return Build(
        files=assets.load(folder, skip=frozenset({DOCUMENT})),
        document=document,
        routes=tuple(routes),
        inputs=stamp["inputs"],
    )


BUILD = load()


def csp(nonce: str) -> str:
    """The document's policy: scripts from here only, styles from here or carrying the nonce, nothing from
    elsewhere."""
    return (
        f"default-src 'none'; script-src 'self'; style-src 'self' 'nonce-{nonce}'; font-src 'self'; img-src 'self'; "
        "media-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )


def new_nonce() -> str:
    return base64.b64encode(secrets.token_bytes(NONCE_BYTES)).decode("ascii")


def document(request: Request, build: Build = BUILD, *, status: int = 200) -> HTMLResponse:
    """The app's document for a route of its own, or with 404 for a page path it has none for, which the app shows as
    not found (DR-0792): a nonce of its own, the theme, and `no-store`, since the page asked for is the user's own
    words."""
    nonce = new_nonce()
    theme = prefs(request)["theme"] or "system"
    html = build.document.replace(NONCE_MARK, f'content="{nonce}"', 1).replace(THEME_MARK, f'data-theme="{theme}"', 1)
    headers = {"Content-Security-Policy": csp(nonce), "Cache-Control": "no-store"}
    return HTMLResponse(html, status_code=status, headers=headers)


def serve(request: Request, path: str, build: Build = BUILD) -> Response:
    """One of the build's files, as `/static/` serves its own."""
    return assets.serve(request, path, build.files)
