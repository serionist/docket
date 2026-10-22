"""Values as the pages show them, as text: a time in the owner's zone, a time into a recording, and the words for a
failure. The app's JSON reads all use these, so a time reads the same on every page, and the
app formats none of its own (DR-0709).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from docket.vault.anchors import format_time

# What a page says when something failed that it can't name: never the error's own words, which may quote a record.
FAILED = (
    "Something failed in Docket. The service log says what, and never holds your words; docket service status names it."
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
