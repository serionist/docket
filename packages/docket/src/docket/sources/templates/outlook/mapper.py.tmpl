"""Outlook mail's mapper for Docket (contract 1): a run's captures of Claude in Chrome's `javascript_tool`, where
`scripts/mail-read.js` read a conversation or a message Outlook on the web had loaded, turned into one record per
message.

Standard library only: Docket runs this file in its own process and reads the JSON it returns. Docket checks every
rid and computes every version; this file never does either.

Every `javascript_tool` call of a run is captured: the search, opening a conversation, loading more rows. Only a result
whose first string is `docket:mail-read/1` gives records. `docket:mail-rows/1`, the list, is known and gives none: it
only says what to open. Every other result is passed over without a word.

A read gives a header line, then its messages in pieces of at most 900 characters: `m<TAB><n><TAB>...` starts a
message that takes n pieces, `+<TAB>...` goes on with it (the script names the fields). A long conversation pages:
`more<TAB><k>` ends a part, and the next call starts at piece k. The pieces of one read are joined across its parts by
the header's piece count and mark, so a part never joins a read of other content. Inside a field `¤¤` is `¤`, `¤e` is
`=` and `¤t` a tab, since Chrome blocks a string holding `=` with `&` or `;`; in a list field `¤:` parts an entry and
`¤;` ends one.

One record per message, kind `mail`:

- rid `outlook:<id>`: the message's Exchange immutable id in the URL-safe form (`+` as `-`, `/` as `_`), which a move
  between folders keeps. thread `outlook:<conversation id>`, the same form.
- ts the time it was sent, author `Name <address>`, `to` its To and then its Cc, title the subject, text the body as the
  pane shows it: the message's own part and the quoted text Exchange holds apart for it (a forward's original); a
  reply's quote of an earlier message of the same conversation is that message's own record. Attachments by name only,
  inline images left out.
- No id on the page (the pane read as shown): rid `outlook:d-<hash of sender, sent second, subject and body start>`,
  `fidelity: derived`.

A message read twice in a run (the conversation read again, or by another row) is emitted once, from the latest read.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

CONTRACT = 1
SOURCE = "outlook"
VIA = "owa-mail"
TOOL = "javascript_tool"
READ = "docket:mail-read/1"
ROWS = "docket:mail-rows/1"
VERSIONS = frozenset({READ, ROWS})
# A message line's fields after `m` and its piece count: id, conversation, internet message id, sent, received, from,
# from address, sender, to, cc, bcc, subject, folder, folder type, flags, attachments, text. The header's after `conv`:
# mode, id, subject, messages in data, returned, older, drafts, since, from, pieces, mark, from data, more to load.
FIELDS = 17
HEADER = 13
MODES = frozenset({"conversation", "message", "pane", ""})
ID = re.compile(r"^[A-Za-z0-9_-]{8,500}={0,2}$")
MARK = re.compile(r"^[0-9a-f]{8}$")
KINDS = frozenset({"file", "item", "reference"})
ESCAPES = {"¤": "¤", "e": "=", "t": "\t"}
# What Chrome puts in place of a piece, or at the end of one it cut.
BLOCKED = "blocked"
CUT = "cut-short"


@dataclass
class Out:
    set_aside: list[dict[str, Any]] = field(default_factory=list)
    held: set[str] = field(default_factory=set)
    # rid -> the record from the latest read of it.
    latest: dict[str, dict[str, Any]] = field(default_factory=dict)

    def aside(self, native: str | None, reason: str, tool: str | None = TOOL, key: str | None = None) -> None:
        self.set_aside.append({"id": native, "reason": reason, "tool": tool, "key": key})


@dataclass(frozen=True)
class Header:
    mode: str
    id: str
    since: str
    start: int
    pieces: int
    mark: str


@dataclass
class Read:
    """One read of a conversation or a message: its pieces by number, gathered from every part of it."""

    header: Header
    pieces: dict[int, str] = field(default_factory=dict)
    bad: dict[int, str] = field(default_factory=dict)
    last: int = 0
    fetched: str = ""


@dataclass(frozen=True)
class Line:
    id: str
    conversation: str
    message_id: str
    sent: str
    received: str
    name: str
    address: str
    sender: str
    to: str
    cc: str
    bcc: str
    subject: str
    folder: str
    folder_type: str
    flags: str
    attachments: str
    text: str


ReadKey = tuple[str, str, str, int, str]


def map_run(run: dict[str, Any]) -> dict[str, Any]:
    held = set(run.get("holds") or [])
    out = Out()
    reads: dict[ReadKey, Read] = {}
    for capture in sorted(run["captures"], key=lambda c: c["seq"]):
        _capture(capture, reads, out)
    # Reads in the order of their last part, so the latest read of a message wins.
    for read in sorted(reads.values(), key=lambda r: r.last):
        for line in _lines(read, out):
            record = _record(line, read, held, out)
            if record is not None:
                out.latest[record["rid"]] = record
    return {
        "contract": CONTRACT,
        "records": [out.latest[rid] for rid in sorted(out.latest)],
        "set_aside": out.set_aside,
        "waiting": [],
        "held": sorted(out.held),
    }


# -- one capture -----------------------------------------------------------------------------------------------------


def _capture(capture: dict[str, Any], reads: dict[ReadKey, Read], out: Out) -> None:
    if capture.get("file") is not None:
        # Mail has no import folder: every message comes through the script.
        out.aside(None, "not-a-read", None, "file")
        return
    if capture.get("tool") != TOOL:
        out.aside(None, "unknown-tool", None)
        return
    items = strings(capture.get("result"))
    if items is None or items[0] != READ:
        # The list, the search, opening a conversation, loading more rows: nothing to keep.
        return
    header = _header(items[1]) if len(items) > 1 else None
    if header is None:
        out.aside(None, "bad-header", TOOL, "conv")
        return
    key = (header.mode, header.id, header.since, header.pieces, header.mark)
    read = reads.setdefault(key, Read(header))
    read.last = capture["seq"]
    read.fetched = capture["captured"]
    at = header.start
    for item in items[2:]:
        if item.startswith("more\t") or (item.startswith("[TRUNCATED") and item.endswith("]")):
            # The end of this part; what Chrome left out of the array shows as missing pieces.
            break
        if item.startswith("[BLOCKED"):
            read.bad.setdefault(at, BLOCKED)
        elif item.endswith("[TRUNCATED]"):
            read.bad.setdefault(at, CUT)
        else:
            # A piece read whole wins over the same piece blocked or cut in another part.
            read.pieces[at] = item
            read.bad.pop(at, None)
        at += 1


def _header(text: str) -> Header | None:
    parts = text.split("\t")
    if len(parts) != HEADER + 1 or parts[0] != "conv" or parts[1] not in MODES:
        return None
    numbers = [*parts[4:8], parts[9], parts[10], parts[12], parts[13]]
    if not all(p.isdigit() for p in numbers) or not MARK.match(parts[11]):
        return None
    given = unescape(parts[2])
    if given and not ID.match(given):
        return None
    return Header(parts[1], given, parts[8], int(parts[9]), int(parts[10]), parts[11])


# -- a read's pieces, joined -----------------------------------------------------------------------------------------


def _lines(read: Read, out: Out) -> Iterator[Line]:
    """Each message's line, its pieces joined. A message with a piece Chrome blocked, cut or left out is set aside, by
    its id when its first piece came through."""
    pending: list[str] = []
    expected = 0
    broken: str | None = None
    orphan = False
    for n in range(read.header.pieces):
        piece = read.pieces.get(n)
        trouble = read.bad.get(n, CUT if piece is None else None)
        if trouble is not None or piece is None:
            if pending and len(pending) < expected:
                broken = broken or trouble or CUT
                pending.append("")
            else:
                # The first piece of a message is gone: what goes on from it can't be placed.
                yield from _finish(pending, expected, broken, out)
                pending, expected, broken = [], 0, None
                if not orphan:
                    out.aside(None, trouble or CUT, TOOL, "line")
                orphan = True
            continue
        if piece.startswith("m\t"):
            yield from _finish(pending, expected, broken, out)
            pending, expected, broken, orphan = [], 0, None, False
            count, sep, rest = piece[2:].partition("\t")
            if not sep or not count.isdigit() or int(count) < 1:
                out.aside(None, "bad-line", TOOL, "line")
                continue
            pending, expected = [rest], int(count)
        elif piece.startswith("+\t"):
            if pending and len(pending) < expected:
                pending.append(piece[2:])
            elif not orphan:
                out.aside(None, "bad-line", TOOL, "line")
        else:
            out.aside(None, "bad-line", TOOL, "line")
    yield from _finish(pending, expected, broken, out)


def _finish(pending: list[str], expected: int, broken: str | None, out: Out) -> Iterator[Line]:
    if not pending:
        return
    fields = "".join(pending).split("\t")
    if broken is not None or len(pending) != expected:
        out.aside(_first_id(pending), broken or CUT, TOOL, "line")
    elif len(fields) != FIELDS:
        out.aside(_first_id(pending), "bad-line", TOOL, "line")
    else:
        yield Line(*fields)


def _first_id(pending: list[str]) -> str | None:
    first = unescape(pending[0].split("\t", 1)[0]) if pending else ""
    return first if ID.match(first) else None


# -- one message -----------------------------------------------------------------------------------------------------


def _record(line: Line, read: Read, held: set[str], out: Out) -> dict[str, Any] | None:
    given = unescape(line.id)
    if given and not ID.match(given):
        out.aside(None, "bad-id", TOOL, "id")
        return None
    conversation = unescape(line.conversation) or (read.header.id if read.header.mode == "conversation" else "")
    if conversation and not ID.match(conversation):
        conversation = ""
    for ref in (given, conversation):
        if ref and ref in held:
            out.held.add(ref)
            return None
    flags = [f for f in unescape(line.flags).split(",") if f]
    if "body:none" in flags:
        # OWA had the message without its body: it isn't read yet, and the next read gets it.
        out.aside(given or None, "no-body", TOOL, "text")
        return None
    sent = _time(line.sent) or _time(line.received)
    if sent is None:
        out.aside(given or None, "no-time", TOOL, "time")
        return None
    entries = split_items(line.attachments)
    if any(len(e) != 4 or e[0] not in KINDS for e in entries):
        out.aside(given or None, "bad-attachments", TOOL, "attachments")
        return None
    name, address = unescape(line.name).strip(), unescape(line.address).strip()
    subject, text = unescape(line.subject), unescape(line.text)
    native = given or derived_id(address or name, sent, subject, text)
    to = [_person(*_pair(e)) for e in [*split_items(line.to), *split_items(line.cc)]]
    bcc = [_person(*_pair(e)) for e in split_items(line.bcc)]
    sender = split_items(line.sender)
    extra: dict[str, Any] = {
        "id": given or None,
        "conversation": conversation or None,
        "message_id": unescape(line.message_id) or None,
        "sent": line.sent or None,
        "received": line.received or None,
        "sender": _person(*_pair(sender[0])) if sender else None,
        "bcc": [b for b in bcc if b] or None,
        "folder": unescape(line.folder) or None,
        "folder_type": unescape(line.folder_type) or None,
        "flags": flags or None,
        "files": [{"name": e[1], "type": e[2] or None, "size": int(e[3]) if e[3].isdigit() else None} for e in entries]
        or None,
        "read_as": read.header.mode or None,
    }
    return {
        "rid": f"{SOURCE}:{native}",
        "source": SOURCE,
        "kind": "mail",
        "thread": f"{SOURCE}:{conversation}" if conversation else None,
        "ts": _utc(sent),
        "author": _person(name, address),
        "to": [t for t in to if t],
        "title": subject or None,
        "text": text,
        "attachments": [{"name": e[1] or e[0], "kind": e[0], "ref": None} for e in entries],
        "native": {k: v for k, v in extra.items() if v is not None},
        "fetched": read.fetched,
        "via": VIA,
        "fidelity": "verbatim" if given else "derived",
    }


def _pair(entry: list[str]) -> tuple[str, str]:
    """A name¤:address entry; an entry without its address part is the name alone."""
    return (entry[0] if entry else ""), (entry[1] if len(entry) > 1 else "")


def _person(name: str, address: str) -> str | None:
    """`Name <address>`, or whichever of the two the page gave."""
    name, address = name.strip(), address.strip()
    if name and address and name.lower() != address.lower():
        return f"{name} <{address}>"
    return address or name or None


def derived_id(sender: str, sent: datetime, subject: str, text: str) -> str:
    """The id of a message the page shows without one: its sender, the second it was sent, its subject and the start
    of its body."""
    basis = "\n".join([sender.lower(), _utc(sent), subject.strip(), " ".join(text.split())[:200]])
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


# -- the script's escapes --------------------------------------------------------------------------------------------


def unescape(text: str) -> str:
    """A field as the page had it: `¤¤` is `¤`, `¤e` is `=`, `¤t` a tab."""
    return "".join(_tokens(text, items=False)[0][0])


def split_items(text: str) -> list[list[str]]:
    """A list field: entries ended by `¤;`, their parts by `¤:`, each part unescaped."""
    if not text:
        return []
    return [["".join(part) for part in item] for item in _tokens(text, items=True)]


def _tokens(text: str, *, items: bool) -> list[list[list[str]]]:
    """Read left to right, so `¤¤e` is `¤e` and never `¤=`."""
    found: list[list[list[str]]] = [[[]]]
    n = 0
    while n < len(text):
        ch = text[n]
        if ch == "¤" and n + 1 < len(text):
            nxt = text[n + 1]
            if nxt in ESCAPES:
                found[-1][-1].append(ESCAPES[nxt])
                n += 2
                continue
            if items and nxt == ":":
                found[-1].append([])
                n += 2
                continue
            if items and nxt == ";":
                found.append([[]])
                n += 2
                continue
        found[-1][-1].append(ch)
        n += 1
    return found


# -- the tool's result -----------------------------------------------------------------------------------------------


def strings(result: Any) -> list[str] | None:
    """The script's strings: the first array of strings in the result whose first one is a version line this mapper
    knows."""
    for found in _candidates(result, 0):
        if isinstance(found, list) and found and found[0] in VERSIONS and all(isinstance(s, str) for s in found):
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
        if isinstance(value, list) and value and value[0] in VERSIONS:
            yield value
            return
