"""Teams' mapper for Docket (contract 1): a run's captures of Claude in Chrome's `javascript_tool`, where
`scripts/teams-chat.js` read a chat open in Teams on the web, turned into one record per message.

Standard library only: Docket runs this file in its own process and reads the JSON it returns. Docket checks every
rid and computes every version; this file never does either.

Every `javascript_tool` call of a run is captured: opening a chat, paging it back, restoring unread, the chat list.
Only a result whose first string is `docket:teams-chat/1` gives records. `docket:teams-chats/1`, the chat list, is
known and gives none: it only says which chats to open. Every other result is passed over without a word.

The script gives a header line for the open chat, then one line per message people wrote, a long line going on in
`+<TAB>` strings (the script names the fields). Inside a field `¤¤` is `¤`, `¤e` is `=` and `¤t` a tab, since Chrome
blocks a string holding `=` with `&` or `;`; in the attachments field `¤:` parts an attachment and `¤;` ends one.

One record per message, kind `message`:

- rid `teams:<chat id>/<message id>`: the chat's thread id (`19:...@thread.v2`, `19:..._...@unq.gbl.spaces`,
  `19:meeting_...@thread.v2`) and the message's own id; container and thread `teams:<chat id>`, since a chat has no
  threads (a reply quotes the message it answers, which goes in `native`).
- ts the message's time, author `Name <user id>` (the Entra object id for an `8:orgid:` user), text what the page
  shows, emoji as their character. Files and images are attachments by name, with Teams' file id or image id as ref.
- No chat id on the page: the container is `teams:d-<hash of the chat's name>`, `fidelity: derived`; a renamed group
  chat then splits in two.

A message read twice in a run (the chat read again, or a later part) is emitted once, from the latest read.
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
SOURCE = "teams"
VIA = "teams-web"
TOOL = "javascript_tool"
CHAT = "docket:teams-chat/1"
CHATS = "docket:teams-chats/1"
VERSIONS = frozenset({CHAT, CHATS})
# A message line's fields after `m` and its string count: id, time, author, user id, edited, flags, quoted id, quoted
# author, quoted preview, attachments, text. The header's after `chat`: chat id, kind, name, messages in the pane,
# oldest, newest, since, kept, from, read from data, system lines.
FIELDS = 11
HEADER = 11
CHAT_ID = re.compile(r"^19:[^\s/]{1,400}$")
MESSAGE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
ORGID = re.compile(r"^8:orgid:(?P<id>[0-9a-fA-F-]{36})$")
ESCAPES = {"¤": "¤", "e": "=", "t": "\t"}


@dataclass
class Out:
    set_aside: list[dict[str, Any]] = field(default_factory=list)
    held: set[str] = field(default_factory=set)
    # rid -> the record from the latest read of it.
    latest: dict[str, dict[str, Any]] = field(default_factory=dict)

    def aside(self, native: str | None, reason: str, tool: str | None = TOOL, key: str | None = None) -> None:
        self.set_aside.append({"id": native, "reason": reason, "tool": tool, "key": key})


@dataclass(frozen=True)
class Chat:
    """The open chat as the header names it: its id, or the one derived from its name; its kind and name."""

    native: str
    derived: bool
    kind: str
    name: str


@dataclass(frozen=True)
class Line:
    id: str
    time: str
    author: str
    user: str
    edited: str
    flags: str
    quote_id: str
    quote_author: str
    quote_text: str
    attachments: str
    text: str


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
        # Teams has no import folder: every message comes through the script.
        out.aside(None, "not-a-read", None, "file")
        return
    if capture.get("tool") != TOOL:
        out.aside(None, "unknown-tool", None)
        return
    items = strings(capture.get("result"))
    if items is None or items[0] != CHAT:
        # The chat list, opening a chat, paging back, an unread restore: nothing to keep.
        return
    chat = _chat(items[1]) if len(items) > 1 else None
    if chat is None:
        out.aside(None, "bad-header", TOOL, "chat")
        return
    if chat.native in held:
        out.held.add(chat.native)
        return
    for line in _lines(items[2:], chat, out):
        record = _record(line, chat, capture, held, out)
        if record is not None:
            # Captures come in order, so the latest read of a message wins.
            out.latest[record["rid"]] = record


def _chat(text: str) -> Chat | None:
    parts = text.split("\t")
    if len(parts) != HEADER + 1 or parts[0] != "chat" or not all(p.isdigit() for p in [parts[4], *parts[8:12]]):
        return None
    given, kind, name = unescape(parts[1]), parts[2], unescape(parts[3])
    if given:
        return Chat(given, False, kind, name) if CHAT_ID.match(given) else None
    if not name.strip():
        return None
    return Chat(derived_chat_id(name), True, kind, name)


def _lines(items: list[str], chat: Chat, out: Out) -> Iterator[Line]:
    """Each message's line, its `+` strings joined to it. A message Chrome blocked or cut is set aside, by its id when
    its first string came through."""
    pending: list[str] = []
    expected = got = 0
    for item in items:
        if item.endswith("[TRUNCATED]") and not item.startswith("["):
            # Chrome cut this string short: the message it belongs to can't be read whole.
            if item.startswith("+\t") and pending:
                out.aside(_first_id(pending, chat), "cut-short", TOOL, "line")
            elif item.startswith("m\t"):
                yield from _finish(pending, expected, got, chat, out)
                out.aside(_first_id([item[2:].partition("\t")[2]], chat), "cut-short", TOOL, "line")
            else:
                out.aside(None, "cut-short", TOOL, "line")
            pending, expected, got = [], 0, 0
        elif item.startswith("m\t"):
            yield from _finish(pending, expected, got, chat, out)
            pending, expected, got = [], 0, 0
            count, sep, rest = item[2:].partition("\t")
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
            # Chrome put this in place of a string: the next piece of the open message, or a whole message.
            if pending and got < expected:
                out.aside(_first_id(pending, chat), "blocked", TOOL, "line")
                pending, expected, got = [], 0, 0
            else:
                out.aside(None, "blocked", TOOL, "line")
        elif item.startswith("[TRUNCATED"):
            # Chrome kept 100 items of the array: the rest of the chat didn't come back.
            out.aside(None, "cut-short", TOOL, "array")
        else:
            out.aside(None, "bad-line", TOOL, "line")
    yield from _finish(pending, expected, got, chat, out)


def _finish(pending: list[str], expected: int, got: int, chat: Chat, out: Out) -> Iterator[Line]:
    if not pending:
        return
    fields = "".join(pending).split("\t")
    if got != expected:
        out.aside(_first_id(pending, chat), "cut-short", TOOL, "line")
    elif len(fields) != FIELDS:
        out.aside(_first_id(pending, chat), "bad-line", TOOL, "line")
    else:
        yield Line(*fields)


def _first_id(pending: list[str], chat: Chat) -> str | None:
    first = unescape("".join(pending).split("\t", 1)[0])
    return f"{chat.native}/{first}" if MESSAGE_ID.match(first) else None


# -- one message -----------------------------------------------------------------------------------------------------


def _record(line: Line, chat: Chat, capture: dict[str, Any], held: set[str], out: Out) -> dict[str, Any] | None:
    mid = unescape(line.id)
    if not MESSAGE_ID.match(mid):
        out.aside(None, "bad-id", TOOL, "id")
        return None
    native = f"{chat.native}/{mid}"
    if native in held:
        out.held.add(native)
        return None
    posted = _time(line.time)
    if posted is None:
        out.aside(native, "no-time", TOOL, "time")
        return None
    attachments = _attachments(line.attachments)
    if attachments is None:
        out.aside(native, "bad-attachments", TOOL, "attachments")
        return None
    name, user = unescape(line.author).strip(), unescape(line.user).strip()
    flags = [f for f in line.flags.split(",") if f]
    extra: dict[str, Any] = {
        "chat": chat.native,
        "chat_kind": chat.kind or None,
        "chat_name": chat.name or None,
        "id": mid,
        "posted": line.time,
        "user": user or None,
        "edited": line.edited or None,
        "flags": flags or None,
        "reply_to": _reply(line),
    }
    return {
        "rid": f"{SOURCE}:{native}",
        "source": SOURCE,
        "kind": "message",
        "container": f"{SOURCE}:{chat.native}",
        "thread": f"{SOURCE}:{chat.native}",
        "ts": _utc(posted),
        "author": _author(name, user),
        "text": unescape(line.text),
        "attachments": attachments,
        "native": {k: v for k, v in extra.items() if v is not None},
        "fetched": capture["captured"],
        "via": VIA,
        "fidelity": "derived" if chat.derived else "verbatim",
    }


def _reply(line: Line) -> dict[str, str] | None:
    """The message this one quotes: its id, its author and the preview Teams shows of it."""
    quoted = unescape(line.quote_id)
    if not quoted:
        return None
    reply = {"id": quoted, "author": unescape(line.quote_author), "preview": unescape(line.quote_text)}
    return {k: v for k, v in reply.items() if v}


def _attachments(text: str) -> list[dict[str, Any]] | None:
    """Each attachment as {name, kind, ref}: a file's name and Teams' file id, or an image's alt text and its id."""
    found: list[dict[str, Any]] = []
    for item in split_items(text):
        if len(item) != 3 or item[0] not in ("file", "image"):
            return None
        kind, ref, name = item
        found.append({"name": name or kind, "kind": kind, "ref": ref or None})
    return found


def _author(name: str, user: str) -> str | None:
    """`Name <user id>`: the Entra object id for an `8:orgid:` user, the id as Teams gives it for anyone else; the name
    alone when the page gave no id."""
    person = ORGID.match(user)
    ident = person.group("id").lower() if person else user
    if name and ident:
        return f"{name} <{ident}>"
    return name or ident or None


def derived_chat_id(name: str) -> str:
    """The container of a chat the page shows without its id: a hash of its name."""
    return "d-" + hashlib.sha256(name.strip().encode("utf-8")).hexdigest()[:24]


def _time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(UTC) if moment.tzinfo is not None else None


def _utc(moment: datetime) -> str:
    """UTC to the millisecond, as the page gives it: messages a second apart keep their order."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# -- the script's escapes --------------------------------------------------------------------------------------------


def unescape(text: str) -> str:
    """A field as the page had it: `¤¤` is `¤`, `¤e` is `=`, `¤t` a tab."""
    return "".join(_tokens(text, items=False)[0][0])


def split_items(text: str) -> list[list[str]]:
    """The attachments field: items ended by `¤;`, their parts by `¤:`, each part unescaped."""
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
