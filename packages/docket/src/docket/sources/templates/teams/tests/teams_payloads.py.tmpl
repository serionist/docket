"""Teams payloads, invented and written from scratch in the shape `scripts/teams-chat.js` and `scripts/teams-chats.js`
return. Ben Sample, Rita Sample and Dóra Minta chat on 19 Oct 2026. Nothing here comes from a real chat.

The shapes are the ones a probe found on the page: chat ids in Teams' three layouts (a group's
`19:<32 hex>@thread.v2`, a one-on-one's `19:<guid>_<guid>@unq.gbl.spaces`, a meeting's `19:meeting_<base64>@thread.v2`),
message ids that are the message's first arrival in epoch milliseconds, user ids `8:orgid:<guid>`, file ids as GUIDs
and image ids as `0-<region>-<shard>-<32 hex>`. A result reaches the capture hook the way watched runs of the
calendar example saw it: the array as JSON text, then Chrome's tab context."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

VERSION = "docket:teams-chat/1"
LIST_VERSION = "docket:teams-chats/1"
PIECE = 900

BEN = ("Ben Sample", "8:orgid:0b0b0b0b-1111-4222-8333-444444444444")
RITA = ("Rita Sample", "8:orgid:7a7a7a7a-5555-4666-8777-888888888888")
DORA = ("Dóra Minta", "8:orgid:d0d0d0d0-9999-4aaa-8bbb-cccccccccccc")


def guid(user: tuple[str, str]) -> str:
    return user[1].removeprefix("8:orgid:")


@dataclass(frozen=True)
class Chat:
    id: str
    kind: str
    name: str


GROUP = Chat("19:5a3c1e0f9b2d4c6e8a0b1c2d3e4f5a6b@thread.v2", "group", "Ben, Rita, Dóra")
ONE = Chat(f"19:{guid(BEN)}_{guid(RITA)}@unq.gbl.spaces", "one-on-one", "Rita Sample")
MEETING = Chat(
    "19:meeting_"
    + base64.b64encode(b"e3c1a2b4-5d6f-4a7b-9c8d-0e1f2a3b4c5d").decode("ascii").rstrip("=")
    + "@thread.v2",
    "meeting",
    "Sensor rig review",
)


def esc(text: str) -> str:
    """A field as the script escapes it: ¤ first, then = and the tab."""
    return text.replace("¤", "¤¤").replace("=", "¤e").replace("\t", "¤t")


def one(text: str) -> str:
    return esc(" ".join(text.split()))


def ms(at: str) -> str:
    """A message id: its first arrival in epoch milliseconds."""
    return str(int(datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp() * 1000))


@dataclass(frozen=True)
class Message:
    """One message as the script reads it: its own data, or with `shown` only what the page shows."""

    time: str
    author: tuple[str, str]
    text: str
    edited: str = ""
    flags: tuple[str, ...] = ()
    # (quoted id, quoted author, preview)
    quote: tuple[str, str, str] | None = None
    # (kind, ref, name)
    attachments: tuple[tuple[str, str, str], ...] = ()
    shown: bool = False
    id: str = ""

    @property
    def mid(self) -> str:
        return self.id or ms(self.time)

    def fields(self) -> str:
        quote = self.quote or ("", "", "")
        flags = ("shown", *self.flags) if self.shown else self.flags
        return "\t".join(
            [
                one(self.mid),
                self.time,
                one(self.author[0]),
                "" if self.shown else one(self.author[1]),
                self.edited,
                ",".join(flags),
                *(one(q) for q in quote),
                "¤;".join("¤:".join(one(p) for p in a) for a in self.attachments),
                esc(self.text),
            ]
        )

    def pieces(self) -> list[str]:
        """The message's strings, as the script cuts them."""
        body = self.fields()
        k = max(1, -(-len(body) // PIECE))
        return [("+\t" if i else f"m\t{k}\t") + body[i * PIECE : (i + 1) * PIECE] for i in range(k)]


RIG_FREE = Message("2026-10-19T07:58:12.311Z", RITA, "Morning! Is the rig free at 10?")
RIG_YES = Message(
    "2026-10-19T08:01:40.020Z",
    BEN,
    "Yes, until noon 👍",
    flags=("mine",),
    quote=(ms("2026-10-19T07:58:12.311Z"), "Rita Sample", "Morning! Is the rig free at 10?"),
)
PLAN = Message(
    "2026-10-19T08:15:03.500Z",
    DORA,
    "Plan attached, and the wiring photo",
    attachments=(
        ("file", "f1e2d3c4-b5a6-4978-8a9b-0c1d2e3f4a5b", "Torque test plan v2.xlsx"),
        ("image", "0-weu-d3-9c0ffee0d15ea5e5ca7f00d5ba5eba11", "image"),
    ),
)
# '=', '&', ';', a tab, the escape mark itself and a literal "¤e": all must come back as typed.
SETTINGS = Message(
    "2026-10-19T08:20:44.004Z",
    RITA,
    "Settings for the run:\n- gain=2&offset=0.5; mode=fast\n- tab\there\n- budget ¤ 40, literal ¤e stays",
)
EDITED = Message("2026-10-19T08:31:09.990Z", BEN, "Moved to 10:30, room 4.12", edited="2026-10-19T08:33:00.000Z")
LONG = Message("2026-10-19T09:02:00.000Z", DORA, "Checklist: " + "torque, cabling, firmware, logs; " * 40)
GROUP_MESSAGES = (RIG_FREE, RIG_YES, PLAN, SETTINGS, EDITED, LONG)

# Read without the message's data behind the element: no user id, the name as shown.
SHOWN = Message("2026-10-19T10:05:00.000Z", RITA, "Call me when you're out", shown=True)
MEETING_NOTE = Message("2026-10-19T12:41:30.250Z", DORA, "Notes are in the Loop page, see you Thursday")


def chat(
    c: Chat,
    *messages: Message,
    since: str = "",
    start: int = 0,
    more: int | None = None,
    system: int = 0,
    chat_id: str | None = None,
) -> list[str]:
    """The script's strings for an open chat: the version line, the header, each message's strings, `more` when cut."""
    times = [m.time for m in messages]
    header = [
        "chat",
        one(c.id if chat_id is None else chat_id),
        c.kind,
        one(c.name),
        str(len(messages)),
        min(times) if times else "",
        max(times) if times else "",
        one(since),
        str(len(messages)),
        str(start),
        str(sum(1 for m in messages if not m.shown)),
        str(system),
    ]
    out = [VERSION, "\t".join(header)]
    for m in messages:
        out += m.pieces()
    if more is not None:
        out.append(f"more\t{more}")
    return out


def chat_list(*rows: tuple[Chat, bool, str]) -> list[str]:
    """`teams-chats.js`'s strings: each row's id, kind, unread, last activity, muted and name."""
    out = [LIST_VERSION, "\t".join(["chats", str(len(rows)), "0", str(len(rows)), str(sum(u for _, u, _ in rows))])]
    for c, unread, last in rows:
        out.append("\t".join(["c", one(c.id), c.kind, "1" if unread else "0", last, "0", one(c.name)]))
    return out


def hooked(value: Any) -> list[dict[str, Any]]:
    """A result as Claude Code hands it to the capture hook: the value as JSON text, then the tab context Claude in
    Chrome adds."""
    tabs = '\n\nTab Context:\n- Executed on tabId: 101\n- Available tabs:\n  • tabId 101: "Chat | Microsoft Teams"'
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
                "captured": f"2026-10-19T16:{seq:02d}:00Z",
                "tool": tool,
                "arguments": arguments,
                "result": result,
                "file": file,
            }
        )
        return self

    def script(self, strings: list[str], *, shape: Any = hooked) -> Run:
        """`javascript_tool` running teams-chat.js or teams-chats.js."""
        return self.add(
            "javascript_tool", shape(strings), action="javascript_exec", text="/* teams-chat.js */", tabId=101
        )

    def snippet(self, value: Any = ("opened 19:x@thread.v2", "was unread", "now read", "loaded 12")) -> Run:
        """`javascript_tool` opening a chat, paging it back or restoring unread: a result without a version line."""
        return self.add("javascript_tool", hooked(list(value)), action="javascript_exec", text="/* open */", tabId=101)

    def payload(self, holds: list[str] | None = None) -> dict[str, Any]:
        manifest = {
            "run": "r1",
            "source": "teams",
            "captures": len(self.captures),
            "final": self.final,
            "window": self.window,
        }
        return {"contract": 1, "source": "teams", "manifest": manifest, "captures": self.captures, "holds": holds or []}


def live_run(run: Run) -> Run:
    """A run as the skill reads it: the chat list, then per chat an open-and-page snippet and the chat script."""
    run.script(
        chat_list(
            (GROUP, True, "2026-10-19T09:02:00.000Z"),
            (MEETING, False, "2026-10-19T12:41:30.250Z"),
            (ONE, True, "2026-10-19T10:05:00.000Z"),
        )
    )
    run.snippet().script(chat(GROUP, *GROUP_MESSAGES, since="2026-10-18T16:00:00.000Z", system=2))
    run.snippet().script(chat(ONE, SHOWN, since="2026-10-18T16:00:00.000Z"))
    run.snippet().script(chat(MEETING, MEETING_NOTE, since="2026-10-18T16:00:00.000Z", system=6))
    return run.snippet(("restored", GROUP.id)).snippet(("restored", ONE.id))


ALL = (*GROUP_MESSAGES, SHOWN, MEETING_NOTE)
CHAT_OF = {**dict.fromkeys(GROUP_MESSAGES, GROUP), SHOWN: ONE, MEETING_NOTE: MEETING}


def rid(m: Message) -> str:
    return f"teams:{CHAT_OF[m].id}/{m.mid}"
