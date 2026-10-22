"""What the shell takes from the browser and the service: the theme and keys switches its two cookies hold (DR-0706),
which the document is written with, and the index line at the sidebar's foot (DR-0708).
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request

from docket.service.state import Service

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
