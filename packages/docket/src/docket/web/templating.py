"""The web app's templates: one Jinja environment, its filters, and `page()`, the response every page and fragment
goes out as (DR-0170, DR-0172).

Every value is autoescaped and a missing one fails the render (`StrictUndefined`); nothing from a record or a doc is
marked safe. Each response carries the page CSP, stricter than the Guard's floor, and `no-store`, so the user's words
stay out of the browser's cache.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import datetime
from importlib.resources import files
from zoneinfo import ZoneInfo

from jinja2 import Environment, PackageLoader, StrictUndefined
from markupsafe import Markup, escape
from starlette.datastructures import QueryParams
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, Response

from docket.index.query import SPLIT_FIRST, SPLIT_SECOND
from docket.index.text import HYPHENS, has_letter
from docket.plaintext import WORD, fold
from docket.vault.anchors import format_time
from docket.web import assets, pages

log = logging.getLogger("docket.service.web")

PAGE_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self'; connect-src 'self'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
# htmx's settings: its own indicator CSS off (ours has it), and a 401 not swapped in, since the page reloads into the
# sign-in page instead. Read once by htmx from a meta tag, so no script sets them.
HTMX_CONFIG = json.dumps({"includeIndicatorCSS": False, "noSwap": [204, 304, 401]})
STYLESHEETS = (
    "css/tokens.css",
    "css/base.css",
    "css/components.css",
    "css/search.css",
    "css/sync.css",
    "css/staging.css",
    "css/curate.css",
    "css/settings.css",
    "css/recording.css",
    "css/briefs.css",
)
# A page that posts a form says so: under the service's floor, no-referrer, a browser sends Origin: null on a
# same-origin post (Fetch's rule for serializing a request's origin), and the Guard refuses that as cross-origin.
POSTS_FORMS = "same-origin"
SCRIPTS = (assets.HTMX, "js/docket.js")
# A word of a snippet is marked when it is a query word, or starts with one this long (an inflected form).
MARK_PREFIX = 4
FAILED = (
    "Something failed in Docket. The service log says what, and never holds your words; docket service status names it."
)
# A form's body: a few hundred fields at most. Anything bigger isn't a page's.
MAX_BODY = 64 * 1024
FORM = "application/x-www-form-urlencoded"

ICONS: dict[str, str] = json.loads(files("docket.web").joinpath("icons.json").read_text(encoding="utf-8"))


def icon(name: str) -> Markup:
    """An icon as inline SVG, hidden from screen readers: a word beside it says what it means."""
    return Markup(  # noqa: S704 - the paths are the package's own icons.json, never content
        f'<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">{ICONS[name]}</svg>'
    )


def at(value: datetime | None, tz: ZoneInfo, with_time: bool = True) -> str:
    """A time in the owner's zone, as `7 Oct 2026 10:00`."""
    if value is None:
        return ""
    local = value.astimezone(tz)
    day = f"{local.day} {local:%b %Y}"
    return f"{day} {local:%H:%M}" if with_time else day


def clock(seconds: int | None) -> str:
    """Seconds into a recording, as its anchors write them: `02:30`, `1:02:30`."""
    return "" if seconds is None else format_time(seconds)


def _pair(text: str, a: re.Match[str], b: re.Match[str], wanted: set[str]) -> bool:
    """Two words in a row joining into a query word (DR-0282): a hyphenated pair always; a spaced one only with the
    halves a split has, since the page can't see which splits the index admitted (DR-0281)."""
    if fold(a.group(0)) + fold(b.group(0)) not in wanted:
        return False
    if text[a.end() : b.start()] in HYPHENS:
        return True
    first, second = a.group(0), b.group(0)
    return len(first) >= SPLIT_FIRST and len(second) >= SPLIT_SECOND and has_letter(first) and has_letter(second)


def marked(text: str, terms: Iterable[str]) -> Markup:
    """A snippet with the query's words marked, built from escaped pieces so a record's markup stays text."""
    wanted = {fold(t) for t in terms if t}
    found = list(WORD.finditer(text))
    marks: set[int] = set()
    for i, m in enumerate(found):
        word = fold(m.group(0))
        if word in wanted or any(len(t) >= MARK_PREFIX and word.startswith(t) for t in wanted):
            marks.add(i)
        if i + 1 < len(found) and _pair(text, m, found[i + 1], wanted):
            marks |= {i, i + 1}
    out: list[str] = []
    last = 0
    for i in sorted(marks):
        m = found[i]
        out += [str(escape(text[last : m.start()])), f"<mark>{escape(m.group(0))}</mark>"]
        last = m.end()
    out.append(str(escape(text[last:])))
    return Markup("".join(out))  # noqa: S704 - every piece is escaped above


def _environment() -> Environment:
    env = Environment(
        loader=PackageLoader("docket.web", "templates"),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    # Jinja types its two registries loosely; these are the names the templates use.
    names: dict[str, object] = {
        "icon": icon,
        "asset": asset,
        "integrity": assets.integrity,
        "vendored": assets.VENDORED,
        "htmx_config": HTMX_CONFIG,
        "stylesheets": STYLESHEETS,
        "scripts": SCRIPTS,
        "nav": pages.NAV,
    }
    filters: dict[str, object] = {"at": at, "clock": clock, "marked": marked}
    for name, value in names.items():
        env.globals[name] = value  # pyright: ignore[reportUnknownMemberType]
    for name, value in filters.items():
        env.filters[name] = value  # pyright: ignore[reportUnknownMemberType]
    return env


def asset(path: str) -> str:
    return f"/static/{path}"


ENV = _environment()


def render(template: str, context: Mapping[str, object]) -> str:
    return ENV.get_template(template).render(context)


def history(
    request: Request, canonical: str, canonical_of: Callable[[str], str | None], *, replace: bool = False
) -> dict[str, str]:
    """The header a fragment answers with, putting its page's canonical URL in the address bar: a new history entry
    only when the URL changed (DR-0170). `canonical_of` reads the page the request came from into the same form."""
    same = canonical_of(request.headers.get("hx-current-url", "")) == canonical
    return {"HX-Replace-Url" if replace or same else "HX-Push-Url": canonical}


async def safely(
    request: Request, route: str, answer: Callable[[], Awaitable[Response]], failed: Callable[[], Response]
) -> Response:
    """A route's answer; on an unexpected error, the log gets the type and frames only, since a message can quote a
    record, and the answer is the page's or the fragment's own error, so a fragment never swaps in a whole page."""
    try:
        return await answer()
    except Exception as e:
        log.error("%s %s failed", request.method, route, exc_info=e)
        return failed()


def page(
    template: str,
    context: Mapping[str, object],
    *,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
    referrer: str | None = None,
) -> HTMLResponse:
    """A page or a fragment, with the page CSP and `no-store`; a page with a form passes `referrer=POSTS_FORMS`."""
    own = {"Referrer-Policy": referrer} if referrer is not None else {}
    return HTMLResponse(
        render(template, context),
        status_code=status,
        headers={"Content-Security-Policy": PAGE_CSP, "Cache-Control": "no-store", **own, **(headers or {})},
    )


async def read_form(request: Request) -> QueryParams | Response:
    """A form post's fields, read here with the standard library's rules: urlencoded, UTF-8, at most MAX_BODY. A form
    with no fields may come with no body and no type."""
    kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY:
            return PlainTextResponse("a form post is at most 64 KB", status_code=413)
    if body and kind != FORM:
        return PlainTextResponse("a form post is application/x-www-form-urlencoded", status_code=415)
    try:
        return QueryParams(body.decode("utf-8"))
    except UnicodeDecodeError:
        return PlainTextResponse("a form post is UTF-8", status_code=400)
