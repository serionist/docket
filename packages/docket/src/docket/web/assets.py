"""What `/static/` serves, and with which content type: the icon in the package's `static/` folder, and the app's build
through `frontend` (DR-0705).

Only the files a folder holds are served, read once at start: nothing from the network, no path from a request ever
reaches the disk. Content types come from the table below, never from `mimetypes`, which on Windows reads the registry
and can call a script `text/plain`, and `nosniff` would then block it. Every answer is `no-cache` with a strong ETag,
so an upgrade never leaves an old stylesheet in use.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".woff2": "font/woff2",
}


@dataclass(frozen=True)
class Asset:
    data: bytes
    type: str
    etag: str


def _walk(folder: Traversable, prefix: str = "") -> dict[str, Traversable]:
    out: dict[str, Traversable] = {}
    for item in folder.iterdir():
        if item.is_dir():
            out |= _walk(item, f"{prefix}{item.name}/")
        else:
            out[f"{prefix}{item.name}"] = item
    return out


def load(folder: Traversable | None = None, *, skip: frozenset[str] = frozenset()) -> dict[str, Asset]:
    """Every file under `static/`, or the folder given, by its path there, but those in `skip`. A file whose type
    isn't in the table stops the service: it would otherwise be served as something a browser guesses at."""
    found = _walk(folder or files("docket.web").joinpath("static"))
    out: dict[str, Asset] = {}
    for path, item in sorted(found.items()):
        if path in skip:
            continue
        suffix = "." + path.rsplit(".", 1)[-1] if "." in path else ""
        if suffix not in TYPES:
            raise ValueError(f"static/{path} has no content type in docket.web.assets.TYPES")
        data = item.read_bytes()
        out[path] = Asset(data, TYPES[suffix], f'"{hashlib.sha256(data).hexdigest()[:32]}"')
    return out


ASSETS = load()


def serve(request: Request, path: str, table: dict[str, Asset] | None = None) -> Response:
    """A static file of the table, `static/`'s by default, or a plain 404: no page, so a miss names nothing of the
    user's."""
    asset = (ASSETS if table is None else table).get(path)
    if asset is None:
        return PlainTextResponse("not found", status_code=404)
    headers = {"ETag": asset.etag, "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == asset.etag:
        return Response(status_code=304, headers=headers)
    body = b"" if request.method == "HEAD" else asset.data
    return Response(body, media_type=asset.type, headers=headers | {"Content-Length": str(len(asset.data))})
