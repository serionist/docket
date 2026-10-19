"""What every page of the shell shows around its own part: the theme and keys switches the browser's two cookies
hold, and the footer's vault, machine and index line.

The footer names the vault's folder and the machine, so only pages behind the session use the shell; the sign-in,
used-link and not-found pages use the bare layout (DR-0177).
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request

from docket.errors import DocketError
from docket.service.state import Service
from docket.web import pages

THEME_COOKIE = "docket_theme"
KEYS_COOKIE = "docket_keys"
THEMES = ("light", "dark")


def prefs(request: Request) -> dict[str, Any]:
    """The theme and the keys switch, from their cookies; anything unknown is the default."""
    theme = request.cookies.get(THEME_COOKIE)
    return {"theme": theme if theme in THEMES else None, "keys_off": request.cookies.get(KEYS_COOKIE) == "off"}


def index_line(service: Service) -> str:
    last = service.last_update
    if last is None:
        return "the index is being brought up to date"
    counts = last.counts
    chunks, records, docs = (counts.get(name, 0) for name in ("chunks", "records", "docs"))
    return f"{chunks:,} chunks from {records:,} records and {docs:,} docs, updated {last.at.astimezone():%H:%M}"


def shell(service: Service | None, request: Request, title: str, current: str = "search") -> dict[str, Any]:
    context: dict[str, Any] = {"title": title, "current": current, "vault_name": "", "machine": "", "index_line": ""}
    context |= {"page_keys": pages.PAGE_KEYS[current], "hints": pages.HINTS[current]}
    context |= prefs(request)
    if service is None:
        return context
    try:
        vault = service.vault()
        context["vault_name"], context["machine"] = vault.root.name, vault.machine
    except (DocketError, OSError):
        context["vault_name"] = "no vault"
    context["index_line"] = index_line(service)
    return context
