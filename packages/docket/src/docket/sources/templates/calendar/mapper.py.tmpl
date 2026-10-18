"""Calendar's mapper for Docket (contract 1): a run's captures of Claude in Chrome's `javascript_tool`, where
`scripts/calendar-week.js` read Outlook on the web's week view, turned into one record per event occurrence.

Standard library only: Docket runs this file in its own process and reads the JSON it returns. Docket checks every
rid and computes every version; this file never does either.

Every `javascript_tool` call of a run is captured, the week paging's clicks too. Only a result whose first string is
`docket:calendar-week/1` is the script's; every other one is passed over without a word. The script gives a header
line and one line per event (the script names the fields), a long line going on in `+<TAB>` strings.

One record per occurrence, kind `event`:

- rid `calendar:<id>`, the event's own id in Microsoft Graph's REST form (`+` as `-`, `/` as `_`). The page's ids are
  Exchange immutable ids: a single event's or a series' starts `AAkA`, an occurrence's `AAkB` and holds its series'
  id and the date of its slot in the series, so an occurrence keeps its rid when it moves.
- thread `calendar:<series id>` for an occurrence, rebuilt from its id; none for a single event.
- ts the start, title the subject as shown, author the organiser as `Name <address>`, text the time as shown, the
  place and the response. Show-as, the label and the end go in `native`, so only a real change makes a new version.
- No id on the page: rid `calendar:d-<hash of organiser, subject and start>`, `fidelity: derived`. A cancellation keeps
  that rid; a moved meeting gets another.

An event read twice in a run (two weeks show it, or the same week read again) is emitted once, from the latest read.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

CONTRACT = 1
SOURCE = "calendar"
VIA = "owa-week-view"
TOOL = "javascript_tool"
VERSION = "docket:calendar-week/1"
# An event line's fields after `ev` and its string count: id, start, end, response, organiser, address, place, label.
FIELDS = 8

WEEKDAY = "(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
MONTH = "(?:January|February|March|April|May|June|July|August|September|October|November|December)"
DATE = rf"{WEEKDAY}, {MONTH} \d{{1,2}}, \d{{4}}"
CLOCK = r"\d{1,2}:\d{2}(?: ?[AP]M)?"
# The label's time and date: `8:30 to 9:00, Monday, October 19, 2026`, or `all day event, <date>[ to <date>]`.
WHEN = re.compile(rf", (?P<when>(?:{CLOCK} to {CLOCK}|all day event), {DATE}(?: to {DATE})?)(?=, |$)")
# What OWA puts before the subject for an icon on the event: `Todo icon, `.
ICONS = re.compile(r"^(?:[A-Z][A-Za-z]*(?: [A-Za-z]+)? icon, )+")
KINDS = ((", Exception to recurring event", "exception"), (", Recurring event", "occurrence"))
SHOW_AS = frozenset({"Free", "Tentative", "Busy", "Away", "Out of office", "Working elsewhere", "Elsewhere"})
CANCELLED = re.compile(r"^(?:Canceled|Cancelled): ?", re.IGNORECASE)
RESPONSES = {
    "Organizer": "organiser",
    "Accept": "accepted",
    "Tentative": "tentative",
    "Decline": "declined",
    "NoResponseReceived": "none",
    "Unknown": "none",
}
LINK = re.compile(r"https?://\S+", re.IGNORECASE)
REST_ID = re.compile(r"^[A-Za-z0-9_-]{8,500}={0,2}$")
# An occurrence's id: 00 09 01 39 00, then 08 and its slot as 8 bytes of .NET ticks, then 2e and its series' 46-byte
# entry id, then one byte. Its series' id is 00 09 00 2e 00 and the same 46 bytes: rebuilt this way, it matched the
# series id OWA holds for every occurrence of a week a probe checked.
OCCURRENCE = bytes([0, 9, 1, 0x39, 0, 8])
SERIES = bytes([0, 9, 0, 0x2E, 0])
TICKS_EPOCH = datetime(1, 1, 1, tzinfo=UTC)


@dataclass
class Out:
    set_aside: list[dict[str, Any]] = field(default_factory=list)
    held: set[str] = field(default_factory=set)
    # rid -> the record from the latest read of it.
    latest: dict[str, dict[str, Any]] = field(default_factory=dict)

    def aside(self, native: str | None, reason: str, tool: str | None = TOOL, key: str | None = None) -> None:
        self.set_aside.append({"id": native, "reason": reason, "tool": tool, "key": key})


@dataclass(frozen=True)
class Week:
    range: str
    zone: str


@dataclass(frozen=True)
class Line:
    id: str
    start: str
    end: str
    response: str
    organiser: str
    address: str
    place: str
    label: str


@dataclass(frozen=True)
class Label:
    subject: str
    when: str
    place: str | None
    organiser: str | None
    show_as: str | None
    kind: str

    @property
    def cancelled(self) -> bool:
        return bool(CANCELLED.match(self.subject))

    @property
    def all_day(self) -> bool:
        return self.when.startswith("all day event")


def map_run(run: dict[str, Any]) -> dict[str, Any]:
    held = set(run.get("holds") or [])
    out = Out()
    for capture in sorted(run["captures"], key=lambda c: c["seq"]):
        _capture(capture, held, out)
    return {
        "contract": CONTRACT,
        "records": [out.latest[rid] for rid in sorted(out.latest)],
        "set_aside": out.set_aside,
        "waiting": [],
        "held": sorted(out.held),
    }


# -- one capture -----------------------------------------------------------------------------------------------------


def _capture(capture: dict[str, Any], held: set[str], out: Out) -> None:
    if capture.get("file") is not None:
        # The calendar has no import folder: every event comes through the script.
        out.aside(None, "not-a-read", None, "file")
        return
    if capture.get("tool") != TOOL:
        out.aside(None, "unknown-tool", None)
        return
    items = strings(capture.get("result"))
    if items is None:
        # A click, a scroll or another script of the run: nothing of the calendar's.
        return
    week = _week(items[1]) if len(items) > 1 else None
    if week is None:
        out.aside(None, "bad-header", TOOL, "week")
        return
    for line in _lines(items[2:], out):
        record = _record(line, week, capture, held, out)
        if record is not None:
            # Captures come in order, so the latest read of an event wins.
            out.latest[record["rid"]] = record


def _week(text: str) -> Week | None:
    parts = text.split("\t")
    if len(parts) != 6 or parts[0] != "week" or not all(p.isdigit() for p in parts[2:5]):
        return None
    return Week(parts[1], parts[5])


def _lines(items: list[str], out: Out) -> Iterator[Line]:
    """Each event's line, its `+` strings joined to it. An event Chrome blocked or cut is set aside, by its id when its
    first string came through."""
    pending: list[str] = []
    expected = got = 0
    for item in items:
        if item.endswith("[TRUNCATED]") and not item.startswith("["):
            # Chrome cut this string short: the event it belongs to can't be read whole.
            if item.startswith("+\t") and pending:
                out.aside(_first_id(pending), "cut-short", TOOL, "line")
            elif item.startswith("ev\t"):
                yield from _finish(pending, expected, got, out)
                out.aside(_first_id([item[3:].partition("\t")[2]]), "cut-short", TOOL, "line")
            else:
                out.aside(None, "cut-short", TOOL, "line")
            pending, expected, got = [], 0, 0
        elif item.startswith("ev\t"):
            yield from _finish(pending, expected, got, out)
            pending, expected, got = [], 0, 0
            count, sep, rest = item[3:].partition("\t")
            if not sep or not count.isdigit() or int(count) < 1:
                out.aside(None, "bad-line", TOOL, "line")
                continue
            pending, expected, got = [rest], int(count), 1
        elif item.startswith("+\t"):
            if pending and got < expected:
                pending.append(item[2:])
                got += 1
            else:
                out.aside(None, "bad-line", TOOL, "line")
        elif item.startswith("more\t"):
            continue
        elif item.startswith("[BLOCKED"):
            # Chrome put this in place of a string: the next piece of the open event, or a whole event.
            if pending and got < expected:
                out.aside(_first_id(pending), "blocked", TOOL, "line")
                pending, expected, got = [], 0, 0
            else:
                out.aside(None, "blocked", TOOL, "line")
        elif item.startswith("[TRUNCATED"):
            # Chrome kept 100 items of the array: the rest of the week didn't come back.
            out.aside(None, "cut-short", TOOL, "array")
        else:
            out.aside(None, "bad-line", TOOL, "line")
    yield from _finish(pending, expected, got, out)


def _finish(pending: list[str], expected: int, got: int, out: Out) -> Iterator[Line]:
    if not pending:
        return
    fields = "".join(pending).split("\t")
    if got != expected:
        out.aside(_first_id(pending), "cut-short", TOOL, "line")
    elif len(fields) != FIELDS:
        out.aside(_first_id(pending), "bad-line", TOOL, "line")
    else:
        yield Line(*fields)


def _first_id(pending: list[str]) -> str | None:
    first = "".join(pending).split("\t", 1)[0]
    return rest_id(first) if first else None


# -- one event -------------------------------------------------------------------------------------------------------


def _record(line: Line, week: Week, capture: dict[str, Any], held: set[str], out: Out) -> dict[str, Any] | None:
    native = rest_id(line.id) if line.id else None
    if line.id and native is None:
        out.aside(None, "bad-id", TOOL, "id")
        return None
    label = parse_label(line.label)
    if label is None:
        out.aside(native, "bad-label", TOOL, "label")
        return None
    start, end = _time(line.start), _time(line.end)
    if start is None:
        out.aside(native, "no-start", TOOL, "start")
        return None
    series, slot = occurrence(line.id) if line.id else (None, None)
    organiser = label.organiser or line.organiser or None
    fidelity = "verbatim"
    if native is None:
        native = derived_id(organiser, label.subject, start)
        fidelity = "derived"
    for ref in (native, series):
        if ref is not None and ref in held:
            out.held.add(ref)
            return None
    text = [label.when]
    place = _unlinked(line.place or label.place or "")
    if place:
        text.append(f"Where: {place}")
    response = RESPONSES.get(line.response, line.response.lower()) if line.response else None
    if response:
        text.append(f"Response: {response}")
    if label.cancelled:
        text.append("Cancelled")
    extra: dict[str, Any] = {
        "id": line.id or None,
        "item": label.kind,
        "slot": slot,
        "show_as": label.show_as,
        "all_day": label.all_day,
        "end": _utc(end) if end is not None else None,
        "response_type": line.response or None,
        "label": line.label,
        "week": week.range or None,
        "zone": week.zone or None,
    }
    return {
        "rid": f"{SOURCE}:{native}",
        "source": SOURCE,
        "kind": "event",
        "thread": f"{SOURCE}:{series}" if series else None,
        "ts": _utc(start),
        "author": _author(organiser, line.address),
        "title": label.subject or None,
        "text": "\n".join(text),
        "native": {k: v for k, v in extra.items() if v is not None},
        "fetched": capture["captured"],
        "via": VIA,
        "fidelity": fidelity,
    }


def parse_label(text: str) -> Label | None:
    """The aria-label's parts, read from its time and date outwards: `[<icon> icon, ]<subject>, <when>[, <place>][, By
    <organiser>], <show as>[, Recurring event | , Exception to recurring event]`. The subject and the organiser can
    hold commas; the time and date, the `, By ` and the words at the end can't be mistaken. None for another shape."""
    found = WHEN.search(text)
    if found is None:
        return None
    subject = ICONS.sub("", text[: found.start()]).strip()
    rest = text[found.end() :]
    kind = "single"
    for suffix, name in KINDS:
        if rest.endswith(suffix):
            rest, kind = rest[: -len(suffix)], name
            break
    show_as = None
    head, sep, last = rest.rpartition(", ")
    if sep and last in SHOW_AS:
        rest, show_as = head, last
    place = organiser = None
    if rest.startswith(", By "):
        organiser = rest[len(", By ") :]
    elif rest.startswith(", ") and ", By " in rest:
        at = rest.index(", By ")
        place, organiser = rest[2:at], rest[at + len(", By ") :]
    elif rest.startswith(", "):
        place = rest[2:]
    elif rest:
        return None
    return Label(subject, found.group("when"), place or None, (organiser or "").strip() or None, show_as, kind)


def rest_id(shown: str) -> str | None:
    """An id as the page gives it (Exchange's form, standard base64) in Microsoft Graph's REST form, which is also a
    form a rid can hold: no `/`, which would split it."""
    value = shown.replace("+", "-").replace("/", "_")
    return value if REST_ID.match(value) else None


def occurrence(shown: str) -> tuple[str | None, str | None]:
    """An occurrence's series id (REST form) and the date of its slot, both read from its own id; (None, None) for a
    single event or an id of another layout."""
    try:
        raw = base64.b64decode(shown, validate=True)
    except (binascii.Error, ValueError):
        return None, None
    if len(raw) != 62 or not raw.startswith(OCCURRENCE) or raw[14] != 0x2E:
        return None, None
    series = base64.b64encode(SERIES + raw[15:61]).decode("ascii")
    ticks = int.from_bytes(raw[6:14], "big")
    try:
        slot = (TICKS_EPOCH + timedelta(microseconds=ticks // 10)).date().isoformat()
    except OverflowError:
        slot = None
    return rest_id(series), slot


def derived_id(organiser: str | None, subject: str, start: datetime) -> str:
    """The id of an event the page shows without one: its organiser, its subject without a cancellation's prefix, and
    its start to the second."""
    basis = "\n".join([organiser or "", CANCELLED.sub("", subject), _utc(start)])
    return "d-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def _time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(UTC) if moment.tzinfo is not None else None


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _author(name: str | None, address: str) -> str | None:
    if name and address:
        return f"{name} <{address}>"
    return name or address or None


def _unlinked(text: str) -> str:
    """Never a join link: the script takes links out, and this makes sure."""
    return re.sub(r"\s+", " ", LINK.sub(" ", text)).strip()


# -- the tool's result -----------------------------------------------------------------------------------------------


def strings(result: Any) -> list[str] | None:
    """The script's strings: the first array of strings in the result whose first one is the version line."""
    for found in _candidates(result, 0):
        if isinstance(found, list) and found and found[0] == VERSION and all(isinstance(s, str) for s in found):
            return found
    return None


def _candidates(result: Any, depth: int) -> Iterator[Any]:
    """The JSON values a result can carry the script's array in: itself, its structuredContent, the arrays in its
    text blocks or its text, and a text that is a JSON string holding them."""
    if depth > 3:
        return
    if isinstance(result, dict):
        if result.get("structuredContent") is not None:
            yield from _candidates(result["structuredContent"], depth + 1)
        content = result.get("content")
        if isinstance(content, list):
            yield from _candidates(content, depth + 1)
        return
    if isinstance(result, list):
        if all(isinstance(i, str) for i in result):
            yield result
            return
        texts = [i["text"] for i in result if isinstance(i, dict) and isinstance(i.get("text"), str)]
        yield from _in_text("\n".join(texts), depth)
        return
    if isinstance(result, str):
        yield from _in_text(result, depth)


def _in_text(text: str, depth: int) -> Iterator[Any]:
    decoder = json.JSONDecoder()
    stripped = text.strip()
    if stripped.startswith('"'):
        try:
            inner = decoder.raw_decode(stripped)[0]
        except ValueError:
            inner = None
        if isinstance(inner, str):
            yield from _candidates(inner, depth + 1)
    for start, ch in enumerate(text):
        if ch != "[":
            continue
        try:
            value = decoder.raw_decode(text, start)[0]
        except ValueError:
            continue
        if isinstance(value, list) and value and value[0] == VERSION:
            yield value
            return
