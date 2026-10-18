"""Slack's mapper for Docket (contract 1): a run's captures of the Slack plugin's `slack_read_channel` and
`slack_read_thread`, turned into one record per message.

Standard library only: Docket runs this file in its own process and reads the JSON it returns. Docket checks every
rid and computes every version; this file never does either.

Both tools answer JSON whose `messages` is one rendered text, in two layouts:

- a channel read: a `Channel: #<name> (<id>)` line, then per message `=== Message from <name> <<address>> (<user
  id>) at <local time> ===`, a `Message TS: <ts>` line and the text;
- a thread read: `=== THREAD PARENT MESSAGE ===`, then `=== THREAD REPLIES (<n> total) ===` and a `--- Reply <i> of
  <n> ---` per reply, each message with `From:`, `Time:` and `Message TS:` lines before its text.

Under a message's text come the lines that describe it: `Thread: <n> replies (latest: <local time>)`, `Reactions:
<name> (<n>), ...` and `Files: <name> (ID: <file id>, <type>, <size>), ...`. They go in `native`, never in the text,
so a new reaction or reply makes no new version. The wording is pinned from a watched run.

One record per message: rid `slack:<channel id>/<ts>`, container `slack:<channel id>`, thread `slack:<channel
id>/<parent ts>`, where a top-level message is its own parent. A message read twice in a run (by its channel and by
its thread, or on a page the cursor repeated) is emitted once, from the latest capture.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

CONTRACT = 1
SOURCE = "slack"
VIA = "slack-mcp"
TOOLS = ("slack_read_channel", "slack_read_thread")
# A conversation: a channel (C), a private group (G) or a DM (D). A user id (U, W) opens a DM too, but under an id
# that isn't the DM's, so its messages would get a second rid: such a read is set aside.
CHANNEL = re.compile(r"^[CDG][A-Z0-9]{2,31}$")
USER = re.compile(r"^[UW][A-Z0-9]{2,31}$")
TS = re.compile(r"^\d{9,11}\.\d{6}$")
CHANNEL_LINE = re.compile(r"^Channel: .*\((?P<id>[A-Z0-9]{2,32})\)\s*$")
# A message's first line, in a channel read and in a thread read; and the line a thread read puts before its replies.
MESSAGE_HEADER = re.compile(
    r"^=== Message from (?P<who>.+) at (?P<at>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?: \S+)?) ===\s*$"
)
PARENT_HEADER = re.compile(r"^=== THREAD PARENT MESSAGE ===\s*$")
REPLY_HEADER = re.compile(r"^--- Reply \d+ of \d+ ---\s*$")
REPLIES_LINE = re.compile(r"^=== THREAD REPLIES \(\d+ total\) ===\s*$")
# A line that starts like one of those and isn't: the layout changed, so the message is set aside, not misread.
LOOKS_LIKE_HEADER = ("=== Message", "=== THREAD", "--- Reply")
FIELD = re.compile(r"^(?P<key>From|Time|Message TS): (?P<value>.*?)\s*$")
WHO = re.compile(r"^(?P<name>.*?)(?: <(?P<email>[^<>\s]+)>)? \((?P<user>[UWB][A-Z0-9]{2,31})\)$")
# The lines under a message's text, in Slack's words.
THREAD_LINE = re.compile(r"^Thread: (?P<n>\d+) replies \(latest: (?P<latest>[^()]+)\)\s*$")
REACTIONS_LINE = re.compile(r"^Reactions: \S+ \(\d+\)(?:, \S+ \(\d+\))*\s*$")
FILES_LINE = re.compile(r"^Files: (?P<value>.+ \(ID: F[A-Z0-9]+[^()]*\))\s*$")
# One file of a Files line: its name may hold brackets and commas; the ID is what ends it.
FILE = re.compile(r"(?P<name>.+?) \(ID: (?P<id>F[A-Z0-9]+)(?:, [^()]*)?\)(?:, |$)")


@dataclass
class Out:
    set_aside: list[dict[str, Any]] = field(default_factory=list)
    held: set[str] = field(default_factory=set)
    # rid -> the record from the latest capture that read it.
    latest: dict[str, dict[str, Any]] = field(default_factory=dict)

    def aside(self, native: str | None, reason: str, tool: str | None = None, key: str | None = None) -> None:
        self.set_aside.append({"id": native, "reason": reason, "tool": tool, "key": key})


@dataclass
class Block:
    """One message's lines as a result lays them out: `message` in a channel read, `parent` or `reply` in a thread
    read. A channel read's header gives the author and time; a thread read's From and Time lines do."""

    kind: str
    who: str | None = None
    at: str | None = None
    lines: list[str] = field(default_factory=list)


@dataclass
class Parsed:
    kind: str
    ts: str
    who: str
    at: str | None
    meta: list[str]
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
    tool = capture.get("tool")
    if capture.get("file") is not None:
        # Slack has no import folder: every message comes through a read.
        out.aside(None, "not-a-read", None, "file")
        return
    if tool not in TOOLS:
        out.aside(None, "unknown-tool")
        return
    arguments = capture.get("arguments") or {}
    asked = arguments.get("channel_id")
    asked = asked if isinstance(asked, str) else None
    # A held conversation is never parsed: its id in the arguments is enough.
    if asked is not None and asked in held:
        out.held.add(asked)
        return
    parent: str | None = None
    if tool == "slack_read_thread":
        given = arguments.get("message_ts")
        if not isinstance(given, str) or not TS.match(given):
            out.aside(_conversation(asked), "bad-thread-ts", tool, "message_ts")
            return
        parent = given
    payload = unwrap(capture.get("result"))
    if payload is None:
        out.aside(_conversation(asked), "no-json", tool)
        return
    text = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(text, str):
        out.aside(_conversation(asked), "no-messages", tool, "messages")
        return
    lines = text.splitlines()
    # A thread read has no Channel line: its conversation is the one asked for.
    channel = _shown_channel(lines) or asked
    if channel is None or not CHANNEL.match(channel):
        reason = "user-id-channel" if channel is not None and USER.match(channel) else "bad-channel"
        out.aside(None, reason, tool, "channel_id")
        return
    for native in (channel, f"{channel}/{parent}" if parent else None):
        if native is not None and native in held:
            out.held.add(native)
            return
    messages = [p for b in _blocks(lines, channel, tool, out) if (p := _parse(b, channel, tool, out)) is not None]
    # A thread's replies hang from the parent the result shows, else from the one asked for (a later page).
    root = next((p.ts for p in messages if p.kind == "parent"), parent)
    for message in messages:
        thread_ts = message.ts if tool == "slack_read_channel" or message.kind == "parent" else root or message.ts
        record = _record(message, channel, thread_ts, capture, tool=tool, held=held, out=out)
        if record is not None:
            # Captures come in order, so the latest one that read a message wins.
            out.latest[record["rid"]] = record


def _conversation(value: str | None) -> str | None:
    """A conversation's id fit to name in a set-aside entry, or None."""
    return value if value is not None and CHANNEL.match(value) else None


def _shown_channel(lines: list[str]) -> str | None:
    """The id a channel read's own `Channel:` line gives, before the first message."""
    for line in lines:
        if line.startswith(LOOKS_LIKE_HEADER):
            return None
        found = CHANNEL_LINE.match(line)
        if found:
            return found.group("id")
    return None


def _blocks(lines: list[str], channel: str, tool: str, out: Out) -> list[Block]:
    """Each message's lines. A header that doesn't read is set aside, its lines with it."""
    blocks: list[Block] = []
    current: Block | None = None
    for line in lines:
        header = MESSAGE_HEADER.match(line)
        if header is not None:
            current = Block("message", header.group("who").strip(), header.group("at"))
        elif PARENT_HEADER.match(line):
            current = Block("parent")
        elif REPLY_HEADER.match(line):
            current = Block("reply")
        elif REPLIES_LINE.match(line):
            current = None
            continue
        elif line.startswith(LOOKS_LIKE_HEADER):
            out.aside(channel, "bad-header", tool)
            current = None
            continue
        else:
            if current is not None:
                current.lines.append(line)
            continue
        blocks.append(current)
    return blocks


def _parse(block: Block, channel: str, tool: str, out: Out) -> Parsed | None:
    """A block's fields (From and Time in a thread read, then Message TS), its text and the lines under it."""
    fields: dict[str, str] = {}
    n = 0
    while n < len(block.lines):
        line = block.lines[n]
        found = FIELD.match(line)
        if not line.strip():
            n += 1
            continue
        if found is None or found.group("key") in fields:
            break
        fields[found.group("key")] = found.group("value")
        n += 1
        if found.group("key") == "Message TS":
            break
    ts = fields.get("Message TS")
    if ts is None or not TS.match(ts):
        out.aside(channel, "no-ts", tool, "Message TS")
        return None
    who = block.who or fields.get("From")
    if not who:
        out.aside(f"{channel}/{ts}", "no-author", tool, "From")
        return None
    meta, text_lines = _split(block.lines[n:])
    text = "\n".join(line.rstrip() for line in text_lines).strip("\n")
    return Parsed(block.kind, ts, who.strip(), block.at or fields.get("Time"), meta, text)


def _split(lines: list[str]) -> tuple[list[str], list[str]]:
    """A message's lines under its fields as (the lines that describe it, its text): those lines come off the end."""
    end = len(lines)
    meta: list[str] = []
    while end > 0:
        line = lines[end - 1]
        if line.strip() and not _is_meta(line):
            break
        if line.strip():
            meta.insert(0, line.strip())
        end -= 1
    return meta, lines[:end]


def _is_meta(line: str) -> bool:
    return any(p.match(line) for p in (THREAD_LINE, REACTIONS_LINE, FILES_LINE))


# -- one message -----------------------------------------------------------------------------------------------------


def _record(
    message: Parsed,
    channel: str,
    thread_ts: str,
    capture: dict[str, Any],
    *,
    tool: str,
    held: set[str],
    out: Out,
) -> dict[str, Any] | None:
    for ref in (f"{channel}/{message.ts}", f"{channel}/{thread_ts}"):
        if ref in held:
            out.held.add(ref)
            return None
    author, user, email = _author(message.who)
    native: dict[str, Any] = {"channel": channel, "ts": message.ts, "posted": message.at, "tool": tool}
    if user is not None:
        native["user"] = user
    if email:
        native["email"] = email
    if message.meta:
        native["meta"] = message.meta
    thread = next((m for m in map(THREAD_LINE.match, message.meta) if m), None)
    if thread is not None:
        native["reply_count"] = int(thread.group("n"))
        native["latest_reply"] = thread.group("latest").strip()
    return {
        "rid": f"{SOURCE}:{channel}/{message.ts}",
        "source": SOURCE,
        "kind": "message",
        "container": f"{SOURCE}:{channel}",
        "thread": f"{SOURCE}:{channel}/{thread_ts}",
        "ts": slack_time(message.ts),
        "author": author,
        "text": message.text,
        "attachments": _files(message.meta),
        "native": native,
        "fetched": capture["captured"],
        "via": VIA,
        "fidelity": "verbatim",
    }


def _author(who: str) -> tuple[str | None, str | None, str | None]:
    """(`Name <user id>`, the user id, the address) from `<name> <<address>> (<user id>)`; the name alone when it
    carries no id."""
    person = WHO.match(who)
    if person is None:
        return who or None, None, None
    name, user = person.group("name").strip(), person.group("user")
    return (f"{name} <{user}>" if name else user), user, person.group("email")


def _files(meta: list[str]) -> list[dict[str, Any]]:
    """Each file of the message's Files line, by its name and Slack's file id."""
    files: list[dict[str, Any]] = []
    for line in meta:
        found = FILES_LINE.match(line)
        if found is None:
            continue
        for item in FILE.finditer(found.group("value")):
            files.append({"name": item.group("name").strip(), "kind": "file", "ref": item.group("id")})
    return files


def slack_time(ts: str) -> str:
    """A Slack ts as its UTC time to the second: the part after the dot orders messages within a second."""
    return datetime.fromtimestamp(int(ts.split(".", 1)[0]), UTC).isoformat().replace("+00:00", "Z")


# -- the tools' results ----------------------------------------------------------------------------------------------


def unwrap(result: Any) -> Any:
    """A tool's payload: its structuredContent, else the first JSON value in its text, past any preamble or note."""
    if isinstance(result, dict):
        if result.get("structuredContent") is not None:
            return result["structuredContent"]
        content = result.get("content")
        if isinstance(content, list):
            return first_json(_texts(content))
        return result
    if isinstance(result, list) and result and all(isinstance(i, dict) and i.get("type") == "text" for i in result):
        return first_json(_texts(result))
    if isinstance(result, str):
        return first_json(result)
    return result


def _texts(items: list[Any]) -> str:
    return "\n".join(i.get("text", "") for i in items if isinstance(i, dict) and isinstance(i.get("text"), str))


def first_json(text: str) -> Any:
    decoder = json.JSONDecoder()
    for start, ch in enumerate(text):
        if ch in "{[":
            try:
                return decoder.raw_decode(text, start)[0]
            except ValueError:
                continue
    return None
