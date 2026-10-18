"""Outlook mail payloads, invented and written from scratch in the shape `scripts/mail-read.js` and
`scripts/mail-rows.js` return. Ben Sample, Rita Sample and Dóra Minta mail each other on 19 Oct 2026, all at
example.com. Nothing here comes from a real mailbox.

The shapes are the ones a probe found in OWA's own data: message ids that are Exchange immutable ids
(68 characters of base64, `AAkA...`, holding `+` and `/`), conversation ids of 80 characters ending in `=`, both handed
out in the URL-safe form; times in UTC to the second; recipients with their addresses; attachments with a content type
and a size. A result reaches the capture hook the way watched runs of the calendar and Teams examples saw it: the
array as JSON text, then Chrome's tab context."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

VERSION = "docket:mail-read/1"
ROWS_VERSION = "docket:mail-rows/1"
PIECE = 900

BEN = ("Ben Sample", "ben.sample@example.com")
RITA = ("Rita Sample", "rita.sample@example.com")
DORA = ("Dóra Minta", "dora.minta@example.com")


def item_id(n: int) -> str:
    """An Exchange immutable id as OWA holds it: 51 bytes, the last six giving a `+` run and a `/` run."""
    raw = bytes([0, 9, 0, 0x2E, 0]) + hashlib.sha512(f"item-{n}".encode()).digest()[:40] + b"\xfb\xef\xbe\xff\xff\xff"
    return base64.b64encode(raw).decode("ascii")


def conversation_id(n: int) -> str:
    """A conversation id as OWA holds it: 59 bytes, so 80 characters ending in one `=`."""
    raw = bytes([0, 4, 0x24]) + hashlib.sha256(f"conv-{n}".encode()).digest() + b"\x00\x10\x00" + bytes(21)
    return base64.b64encode(raw).decode("ascii")


def safe(shown: str) -> str:
    """An id in the URL-safe form the scripts hand out."""
    return shown.replace("+", "-").replace("/", "_")


def esc(text: str) -> str:
    """A field as the script escapes it: ¤ first, then = and the tab."""
    return text.replace("¤", "¤¤").replace("=", "¤e").replace("\t", "¤t")


def one(text: str) -> str:
    return esc(" ".join(text.split()))


def people(*who: tuple[str, str]) -> str:
    return "¤;".join(f"{one(n)}¤:{one(a)}" for n, a in who)


@dataclass(frozen=True)
class Mail:
    """One message as the script reads it from OWA's data."""

    n: int
    conv: int
    sent: str
    sender: tuple[str, str]
    subject: str
    text: str
    to: tuple[tuple[str, str], ...] = ()
    cc: tuple[tuple[str, str], ...] = ()
    bcc: tuple[tuple[str, str], ...] = ()
    on_behalf: tuple[str, str] | None = None
    folder: tuple[str, str] = ("Inbox", "inbox")
    flags: tuple[str, ...] = ("body:own",)
    # (kind, name, content type, size)
    attachments: tuple[tuple[str, str, str, int], ...] = ()
    message_id: str = ""
    shown: bool = False
    # Unread when the rows were listed, before anything was opened.
    unread: bool = False

    @property
    def id(self) -> str:
        return "" if self.shown else safe(item_id(self.n))

    @property
    def thread(self) -> str:
        return safe(conversation_id(self.conv))

    def fields(self) -> str:
        return "\t".join(
            [
                esc(self.id),
                esc(self.thread),
                one(self.message_id or f"<msg{self.n}@example.com>"),
                self.sent,
                "" if self.shown else self.sent,
                one(self.sender[0]),
                "" if self.shown else one(self.sender[1]),
                people(self.on_behalf) if self.on_behalf else "",
                people(*self.to),
                people(*self.cc),
                people(*self.bcc),
                one(self.subject),
                "" if self.shown else one(self.folder[0]),
                "" if self.shown else one(self.folder[1]),
                ",".join(("shown",) if self.shown else self.flags),
                "¤;".join("¤:".join([k, one(name), one(kind), str(size)]) for k, name, kind, size in self.attachments),
                esc(self.text),
            ]
        )

    def pieces(self) -> list[str]:
        """The message's strings, as the script cuts them."""
        body = self.fields()
        k = max(1, -(-len(body) // PIECE))
        return [("+\t" if i else f"m\t{k}\t") + body[i * PIECE : (i + 1) * PIECE] for i in range(k)]


def mark(pieces: list[str]) -> str:
    """The script's mark: FNV-1a over the pieces' UTF-16 code units."""
    h = 2166136261
    for p in pieces:
        data = p.encode("utf-16-le")
        for i in range(0, len(data), 2):
            h ^= data[i] | (data[i + 1] << 8)
            h = (h * 16777619) & 0xFFFFFFFF
    return f"{h:08x}"


def read(
    *mails: Mail,
    mode: str = "conversation",
    of: str | None = None,
    since: str = "",
    start: int = 0,
    stop: int | None = None,
    older: int = 0,
    drafts: int = 0,
) -> list[str]:
    """The script's strings for one read: the version line, the header, the pieces from `start` (to `stop`, then a
    `more` line, as a long read's parts come)."""
    pieces = [p for m in mails for p in m.pieces()]
    given = (
        of
        if of is not None
        else (mails[0].thread if mode == "conversation" and mails else mails[0].id if mails else "")
    )
    header = [
        "conv",
        mode,
        "" if mode == "pane" else esc(given),
        one(mails[-1].subject if mails else ""),
        str(len(mails) + older),
        str(len(mails)),
        str(older),
        str(drafts),
        one(since),
        str(start),
        str(len(pieces)),
        mark(pieces),
        "0" if mode == "pane" else "1",
        "0",
    ]
    end = len(pieces) if stop is None else stop
    out = [VERSION, "\t".join(header), *pieces[start:end]]
    if end < len(pieces):
        out.append(f"more\t{end}")
    return out


def rows(*mails: Mail, folder: str = "", messages: bool = False) -> list[str]:
    """`mail-rows.js`'s strings: the All-folders search, one conversation row per thread; with `folder` a folder, in
    conversation view (Deleted Items) or with `messages` in message view (Junk Email), one row per message; newest
    first. A row's unread count is 1 when the message was unread when listed."""
    latest: dict[str, Mail] = {}
    for m in mails:
        key = m.id if messages else m.thread
        if key not in latest or m.sent > latest[key].sent:
            latest[key] = m
    ordered = sorted(latest.values(), key=lambda m: m.sent, reverse=True)
    lists = "messages" if messages else "conversations"
    view = ["folder", one(folder), lists] if folder else ["search", one("received>=2026-10-19"), lists]
    count = str(len(ordered))
    header = ["rows", *view, count, count, "0", ordered[-1].sent if ordered else "", "", count, "0", count]
    out = [ROWS_VERSION, "\t".join(header)]
    for m in ordered:
        kind, ident = ("i", m.id) if messages else ("c", m.thread)
        unread = sum(1 for x in mails if x.unread and (x.id == m.id if messages else x.thread == m.thread))
        row = [kind, esc(ident), esc(m.thread), m.sent, str(unread), "1", one(m.folder[0]), one(m.folder[1]), ""]
        out.append("\t".join(["r", *row, one(m.sender[0]), one(m.sender[1]), one(m.subject)]))
    return out


def unread_ids(strings: list[str]) -> list[str]:
    """The restore list the skill notes from a rows result: the ids of the rows whose unread count is above 0."""
    return [r.split("\t")[2] for r in strings[2:] if r.startswith("r\t") and int(r.split("\t")[5] or 0) > 0]


ASK = Mail(
    1,
    1,
    "2026-10-19T07:58:12Z",
    RITA,
    "Torque rig: slot on Tuesday?",
    "Hi Ben,\n\nCan we have the rig on Tuesday from 10?\n\nThanks,\nRita",
    to=(BEN,),
    cc=(DORA,),
    unread=True,
)
ANSWER = Mail(
    2,
    1,
    "2026-10-19T08:01:40Z",
    BEN,
    "RE: Torque rig: slot on Tuesday?",
    "Yes, 10 to 12 is yours.\n\nBen",
    to=(RITA,),
    cc=(DORA,),
    folder=("Sent Items", "sentitems"),
    flags=("body:own", "quoted:thread", "copies:1"),
)
# A forward: the original from outside the conversation is the quoted text Exchange keeps for it.
FORWARD = Mail(
    3,
    2,
    "2026-10-19T08:15:03Z",
    DORA,
    "FW: Sensor quote",
    "Ben, see below.\n\nFrom: Vendor Sales <sales@example.com>\nSent: Friday, October 16, 2026 4:02 PM\n"
    "To: Dóra Minta\nSubject: Sensor quote\n\nPlease find the quote attached.",
    to=(BEN,),
    flags=("body:own", "quoted:kept", "inline:2"),
    attachments=(("file", "Quote 2026-114.pdf", "application/pdf", 182044), ("item", "Sensor spec", "", 52311)),
)
# '=', '&', ';', a tab, a link with a query string, the escape mark itself and a literal "¤e": all come back as typed.
SETTINGS = Mail(
    4,
    3,
    "2026-10-19T08:20:44Z",
    RITA,
    "Run settings",
    "Settings for the run:\n- gain=2&offset=0.5; mode=fast\n- tab\there\n- budget ¤ 40, literal ¤e stays\n"
    "- form: https://forms.example.com/r?id=42&lang=en",
    to=(BEN,),
    bcc=(DORA,),
    on_behalf=("Lab Team", "lab@example.com"),
    flags=("body:own", "importance:high"),
)
LONG = Mail(
    5,
    4,
    "2026-10-19T09:02:00Z",
    DORA,
    "Checklist",
    "Checklist: " + "torque, cabling, firmware, logs; " * 90,
    to=(BEN, RITA),
)
# A message in Junk Email, read alone: its whole body.
JUNK = Mail(
    6,
    5,
    "2026-10-19T09:30:00Z",
    ("Weekly Digest", "digest@example.com"),
    "Your weekly digest",
    "Five things this week.\n\nUnsubscribe: https://example.com/u?x=1",
    to=(BEN,),
    folder=("Junk Email", "junkemail"),
    flags=("unread", "body:whole"),
    unread=True,
)
# The pane read as shown, without OWA's data: no id, no addresses, the time to the minute.
SHOWN = Mail(
    7,
    6,
    "2026-10-19T10:05:00Z",
    ("Rita Sample", ""),
    "Call me",
    "Call me when you're out",
    to=(("Ben Sample", ""),),
    shown=True,
)

THREAD = (ASK, ANSWER)
ALL = (ASK, ANSWER, FORWARD, SETTINGS, LONG, JUNK)


def hooked(value: Any) -> list[dict[str, Any]]:
    """A result as Claude Code hands it to the capture hook: the value as JSON text, then the tab context Claude in
    Chrome adds."""
    tabs = '\n\nTab Context:\n- Executed on tabId: 101\n- Available tabs:\n  • tabId 101: "Mail - Ben Sample - Outlook"'
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
        """`javascript_tool` running mail-read.js or mail-rows.js."""
        return self.add(
            "javascript_tool", shape(strings), action="javascript_exec", text="/* mail-read.js */", tabId=101
        )

    def snippet(self, value: Any = ("opened", "AAQk¤e", "was read", "messages 2")) -> Run:
        """`javascript_tool` searching, opening a conversation or loading more rows: a result without a version line."""
        return self.add("javascript_tool", hooked(list(value)), action="javascript_exec", text="/* open */", tabId=101)

    def payload(self, holds: list[str] | None = None) -> dict[str, Any]:
        manifest = {
            "run": "r1",
            "source": "outlook",
            "captures": len(self.captures),
            "final": self.final,
            "window": self.window,
        }
        return {
            "contract": 1,
            "source": "outlook",
            "manifest": manifest,
            "captures": self.captures,
            "holds": holds or [],
        }


# What the folders snippet lists: the folders the search leaves out, by kind and id, and the archive mailbox.
FOLDERS = (
    "folders apart 3",
    "f\tAQMkJunk¤e\tjunkemail\tmsgfolderroot\t40\t3",
    "f\tAQMkDeleted¤e\tdeleteditems\tmsgfolderroot\t25\t2",
    "f\tAQMkArchive¤e\tarchive\tmsgfolderroot\t0\t0",
    "archive mailbox yes",
)


def live_run(run: Run) -> Run:
    """A run as the skill reads it: the search and its rows, then per conversation the open snippet and the read; then
    the folders apart: Junk Email's rows and its message read alone, Deleted Items' rows and a conversation the search
    also gave; then the online archive's search, empty. The unread restore comes after the close, so it isn't here."""
    since = "2026-10-18T16:00:00Z"
    run.snippet(("searched", "scope all folders", "rows 4")).script(rows(*ALL[:5]))
    run.snippet().script(read(LONG, since=since))
    run.snippet().script(read(SETTINGS, since=since))
    run.snippet(("loaded already", FORWARD.thread, "messages 1")).script(read(FORWARD, since=since))
    run.snippet().script(read(*THREAD, since=since))
    run.snippet(FOLDERS).snippet(("opened folder", "AQMkJunk¤e", "junkemail", "rows 25 of 40"))
    run.script(rows(JUNK, folder="Junk Email", messages=True)).snippet()
    run.script(read(JUNK, mode="message", since=since))
    run.snippet(("opened folder", "AQMkDeleted¤e", "deleteditems", "rows 25 of 25"))
    run.script(rows(ANSWER, folder="Deleted Items")).snippet(("loaded already", ANSWER.thread, "messages 2"))
    run.script(read(*THREAD, since=since))
    run.snippet(("archive mailbox selected: search now",)).snippet(("searched", "scope archive mailbox", "rows 0"))
    return run.script(rows())


def rid(m: Mail) -> str:
    return f"outlook:{m.id}"
