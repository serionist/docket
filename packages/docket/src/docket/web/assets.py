"""The web app's files: what `/static/` serves, with which content type, and the vendored files' pins (DR-0171).

Only the files in the package's `static/` folder are served, read once at start: nothing from the network, no path
from a request ever reaches the disk. Content types come from the table below, never from `mimetypes`, which on
Windows reads the registry and can call a script `text/plain`, and `nosniff` would then block it. Every answer is
`no-cache` with a strong ETag, so an upgrade never leaves an old stylesheet in use.
"""

from __future__ import annotations

import base64
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


@dataclass(frozen=True)
class Pin:
    """A file taken from another project as it was published."""

    version: str
    source: str
    sha256: str
    licence: str
    licence_file: str


HTMX = "vendor/htmx-4.0.0.min.js"
FONT_SANS = "fonts/InterVariable-4.1.woff2"
FONT_MONO = "fonts/JetBrainsMono-Regular-2.304.woff2"
FONT_MONO_BOLD = "fonts/JetBrainsMono-SemiBold-2.304.woff2"

VENDORED = {
    HTMX: Pin(
        "4.0.0",
        "https://registry.npmjs.org/htmx.org/-/htmx.org-4.0.0.tgz, package/dist/htmx.min.js",
        "e484d9171a9db30a39c8f16e3d709d4137f3211c659f8e6125816635033d593f",
        "0BSD",
        "vendor/htmx-LICENSE.txt",
    ),
    FONT_SANS: Pin(
        "4.1",
        "https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip, web/InterVariable.woff2",
        "693b77d4f32ee9b8bfc995589b5fad5e99adf2832738661f5402f9978429a8e3",
        "OFL-1.1",
        "fonts/Inter-LICENSE.txt",
    ),
    FONT_MONO: Pin(
        "2.304",
        "https://github.com/JetBrains/JetBrainsMono/releases/download/v2.304/JetBrainsMono-2.304.zip, "
        "fonts/webfonts/JetBrainsMono-Regular.woff2",
        "a9cb1cd82332b23a47e3a1239d25d13c86d16c4220695e34b243effa999f45f2",
        "OFL-1.1",
        "fonts/JetBrainsMono-LICENSE.txt",
    ),
    FONT_MONO_BOLD: Pin(
        "2.304",
        "https://github.com/JetBrains/JetBrainsMono/releases/download/v2.304/JetBrainsMono-2.304.zip, "
        "fonts/webfonts/JetBrainsMono-SemiBold.woff2",
        "918edad542a1da608fd2ba8daebaff9ac802309103fe760eed465b8b4e47faf1",
        "OFL-1.1",
        "fonts/JetBrainsMono-LICENSE.txt",
    ),
}
# The icons in icons.json are Lucide's (lucide-static 1.52.0, ISC), their licence beside the other vendored ones.
LUCIDE_LICENCE = "vendor/lucide-LICENSE.txt"


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


def integrity(path: str) -> str:
    """The `integrity` attribute of a vendored script, from its pin."""
    return "sha256-" + base64.b64encode(bytes.fromhex(VENDORED[path].sha256)).decode("ascii")


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
