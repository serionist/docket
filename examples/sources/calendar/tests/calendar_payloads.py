"""Calendar payloads, invented and written from scratch in the shape `scripts/calendar-week.js` returns. Ben Sample,
Rita Sample and Dóra Minta meet in the week of 18 Oct 2026 (UTC+2). Nothing here comes from a real calendar.

The shapes are the ones a probe found on the page: ids in Exchange's immutable layout (a single event's or
a series' 51 bytes starting 00 09 00 2e, an occurrence's 62 bytes starting 00 09 01 39 and holding its slot's date
and its series' entry id), and aria-labels in each layout seen. A `javascript_tool` result reaches the capture hook as
watched runs saw it: the array as JSON text, then Chrome's tab context."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any

VERSION = "docket:calendar-week/1"
# OWA writes a week's range with an en dash.
WEEK = "2026, October 18\u201324"
NEXT_WEEK = "2026, October 25\u201331"
ZONE = "Europe/Budapest"
RITA = ("Rita Sample", "rita@example.invalid")
DORA = ("Dóra Minta - Test Lab, Example Org", "dora@example.invalid")
BEN = ("Ben Sample", "ben@example.invalid")
TEAMS = "Microsoft Teams Meeting"
PIECE = 900


def entry(seed: str) -> bytes:
    """46 bytes standing in for an item's entry id, with bytes that encode to `+` and `/` in base64."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return digest[:20] + bytes([0xFB, 0xEF, 0xBE, 0xFF, 0xFF, 0xFF]) + digest[12:32]


def single_id(seed: str) -> str:
    """A single event's id, or a series' (the same layout), as the page shows it."""
    return base64.b64encode(bytes([0, 9, 0, 0x2E, 0]) + entry(seed)).decode("ascii")


def occurrence_id(seed: str, slot: date) -> str:
    """An occurrence's id: its slot's date as .NET ticks at 00:00 UTC, then its series' entry id."""
    ticks = (
        datetime(slot.year, slot.month, slot.day, tzinfo=UTC) - datetime(1, 1, 1, tzinfo=UTC)
    ).days * 864_000_000_000
    raw = bytes([0, 9, 1, 0x39, 0, 8]) + ticks.to_bytes(8, "big") + bytes([0x2E]) + entry(seed) + bytes([0x10])
    return base64.b64encode(raw).decode("ascii")


def rest(shown: str) -> str:
    return shown.replace("+", "-").replace("/", "_")


@dataclass(frozen=True)
class Event:
    """One event as the week view shows it: its label's parts and what the script reads from the event's own data."""

    id: str
    subject: str
    when: str
    start: str
    end: str
    organiser: tuple[str, str] | None
    response: str = "NoResponseReceived"
    place: str = TEAMS
    show_as: str = "Tentative"
    kind: str = "Recurring event"
    # A past day's label leaves the place out.
    past: bool = False
    # The label names no organiser when the owner organises it.
    by: bool = True
    icon: str = ""
    # The event's own data could be read: start, end, response, organiser and place.
    data: bool = True

    def label(self) -> str:
        parts = [p for p in (self.icon, self.subject, self.when) if p]
        if self.place and not self.past:
            parts.append(self.place)
        if self.organiser and self.by:
            parts.append(f"By {self.organiser[0]}")
        parts.append(self.show_as)
        if self.kind:
            parts.append(self.kind)
        return ", ".join(parts)

    def pieces(self) -> list[str]:
        """The event's strings, as the script cuts them."""
        name, address = self.organiser or ("", "")
        fields = [self.id, self.start, self.end]
        if self.data:
            fields += [self.response, name, address, self.place]
        else:
            fields += ["", "", "", ""]
        body = "\t".join([*fields, self.label()])
        k = max(1, -(-len(body) // PIECE))
        return [("+\t" if i else f"ev\t{k}\t") + body[i * PIECE : (i + 1) * PIECE] for i in range(k)]


STANDUP = "standup-series"
REVIEW = "review-series"
SYNC = "sync-series"

STANDUP_MON = Event(
    occurrence_id(STANDUP, date(2026, 10, 19)),
    "Test bench standup",
    "9:00 to 9:30, Monday, October 19, 2026",
    "2026-10-19T07:00:00Z",
    "2026-10-19T07:30:00Z",
    RITA,
)
STANDUP_TUE = replace(
    STANDUP_MON,
    id=occurrence_id(STANDUP, date(2026, 10, 20)),
    when="9:00 to 9:30, Tuesday, October 20, 2026",
    start="2026-10-20T07:00:00Z",
    end="2026-10-20T07:30:00Z",
)
# Its slot is Tuesday; it was moved to Wednesday afternoon.
REVIEW_MOVED = Event(
    occurrence_id(REVIEW, date(2026, 10, 20)),
    "Sensor rig review",
    "14:00 to 15:00, Wednesday, October 21, 2026",
    "2026-10-21T12:00:00Z",
    "2026-10-21T13:00:00Z",
    DORA,
    response="Accept",
    show_as="Busy",
    kind="Exception to recurring event",
)
# Ben's own: no By in the label, a to-do icon before the subject, a room and Teams.
PLANNING = Event(
    single_id("planning"),
    "Q4 planning - draft, v2",
    "10:00 to 12:00, Thursday, October 22, 2026",
    "2026-10-22T08:00:00Z",
    "2026-10-22T10:00:00Z",
    BEN,
    response="Organizer",
    place=f"Room 4.12; {TEAMS}",
    show_as="Busy",
    kind="",
    by=False,
    icon="Todo icon",
)
OFFSITE = Event(
    single_id("offsite"),
    "Lab offsite",
    "all day event, Thursday, October 22, 2026 to Friday, October 23, 2026",
    "2026-10-21T22:00:00Z",
    "2026-10-23T22:00:00Z",
    RITA,
    place="",
    show_as="Free",
    kind="",
)
SYNC_FRI = Event(
    occurrence_id(SYNC, date(2026, 10, 23)),
    "Weekly test sync",
    "13:30 to 14:15, Friday, October 23, 2026",
    "2026-10-23T11:30:00Z",
    "2026-10-23T12:15:00Z",
    BEN,
    response="Accept",
    show_as="Busy",
)
SYNC_FRI_CANCELLED = replace(SYNC_FRI, subject="Canceled: Weekly test sync", show_as="Free")
EVENTS = (STANDUP_MON, STANDUP_TUE, REVIEW_MOVED, PLANNING, OFFSITE, SYNC_FRI)


def week(*events: Event, range_: str = WEEK, start: int = 0, more: int | None = None) -> list[str]:
    """The script's strings for a week: the version line, the header, each event's strings, and `more` when cut."""
    data = sum(1 for e in events if e.data)
    out = [VERSION, "\t".join(["week", range_, str(len(events)), str(start), str(data), ZONE])]
    for e in events:
        out += e.pieces()
    if more is not None:
        out.append(f"more\t{more}")
    return out


def hooked(value: Any) -> list[dict[str, Any]]:
    """A result as Claude Code hands it to the capture hook: the value as JSON text, then the tab context Claude in
    Chrome adds."""
    tabs = '\n\nTab Context:\n- Executed on tabId: 101\n- Available tabs:\n  • tabId 101: "Calendar" ("about:blank")'
    return [{"type": "text", "text": json.dumps(value, indent=2, ensure_ascii=False)}, {"type": "text", "text": tabs}]


class Run:
    """A run's captures as the core hands them to the mapper."""

    def __init__(self, *, final: bool = False, window: Mapping[str, str | None] | None = None) -> None:
        self.captures: list[dict[str, Any]] = []
        self.final = final
        self.window = window

    def add(self, tool: str | None, result: Any, *, file: str | None = None, **arguments: Any) -> Run:
        seq = len(self.captures)
        self.captures.append(
            {
                "seq": seq,
                "run": "r1",
                "captured": f"2026-10-18T16:{seq:02d}:00Z",
                "tool": tool,
                "arguments": arguments,
                "result": result,
                "file": file,
            }
        )
        return self

    def script(self, strings: list[str], *, shape: Any = hooked) -> Run:
        """`javascript_tool` running calendar-week.js."""
        return self.add(
            "javascript_tool", shape(strings), action="javascript_exec", text="/* calendar-week.js */", tabId=101
        )

    def click(self, value: Any = (f"before {WEEK}", f"after {NEXT_WEEK}")) -> Run:
        """`javascript_tool` paging the week: a result without the version line."""
        return self.add(
            "javascript_tool", hooked(list(value)), action="javascript_exec", text="/* next week */", tabId=101
        )

    def payload(self, holds: list[str] | None = None) -> dict[str, Any]:
        manifest = {
            "run": "r1",
            "source": "calendar",
            "captures": len(self.captures),
            "final": self.final,
            "window": self.window,
        }
        return {
            "contract": 1,
            "source": "calendar",
            "manifest": manifest,
            "captures": self.captures,
            "holds": holds or [],
        }


def live_week(run: Run) -> Run:
    """A run as the skill reads it: this week, a click to the next, the next week (empty here)."""
    return run.script(week(*EVENTS)).click().script(week(range_=NEXT_WEEK))
