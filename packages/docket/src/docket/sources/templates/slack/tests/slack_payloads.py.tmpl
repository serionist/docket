"""Slack's payloads, invented and written from scratch in the shape the Slack plugin's read tools return: JSON whose
`messages` is one rendered text, laid out one way by a channel read and another by a thread read. Ben Sample and Rita
Sample talk in #team-test (C0TEST) and in their DM (D0TEST). Nothing here comes from a real workspace.

Both layouts and the wording of the Thread, Reactions and Files lines are as a watched run returned them. Only a
thread's later page, without its parent block, is assumed."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

CHANNEL = "C0TEST"
DM = "D0TEST"
HELD_CHANNEL = "C0HELD"
BEN = ("Ben Sample", "ben@example.invalid", "U0BEN")
RITA = ("Rita Sample", "rita@example.invalid", "U0RITA")
CEST = timezone(timedelta(hours=2))


def local(ts: str) -> str:
    """A ts as the results show it: a local time with its zone's letters."""
    moment = datetime.fromtimestamp(int(ts.split(".", maxsplit=1)[0]), UTC).astimezone(CEST)
    return f"{moment:%Y-%m-%d %H:%M:%S} CEST"


@dataclass(frozen=True)
class Msg:
    """One message: its Reactions and Files lines as both layouts show them; a channel read adds its Thread line."""

    who: tuple[str, str, str]
    ts: str
    text: str
    lines: tuple[str, ...] = ()
    replies: int = 0
    latest: str | None = None

    def thread_line(self) -> str:
        return f"Thread: {self.replies} replies (latest: {local(self.latest or self.ts)})"


# 15 Oct 2026, from 10:00 UTC: a post, a thread's parent with two replies, a file post.
REPLY_1 = Msg(BEN, "1792062300.000300", "Megnézem holnap, és visszaírok.")
REPLY_2 = Msg(RITA, "1792062900.000400", "Thanks. Section 3 is the one to check.", ("Reactions: +1 (1)",))
PARENT = Msg(
    RITA, "1792062000.000200", "Draft test plan is up.\nComments welcome by Thursday.", replies=2, latest=REPLY_2.ts
)
POST = Msg(BEN, "1792058400.000100", "Rita, can you send the test plan by Friday?", ("Reactions: +1 (2), eyes (1)",))
FILES_LINE = (
    "Files: test-plan.pdf (ID: F0TESTFILE1, application/pdf, 1.2 MB), "
    "board photo.png (ID: F0TESTFILE2, image/png, 640.5 KB)"
)
FILE_POST = Msg(BEN, "1792065600.000500", "", (FILES_LINE,))


def channel_block(m: Msg) -> str:
    """A message in a channel read: its header (with the trailing space Slack leaves), ts, text, then its lines."""
    name, email, user = m.who
    head = f"=== Message from {name} <{email}> ({user}) at {local(m.ts)} === \nMessage TS: {m.ts}\n{m.text}"
    return "\n".join([head, *([m.thread_line()] if m.replies else []), *m.lines])


def page(channel: str, *blocks: str, name: str = "team-test", cursor: str | None = None) -> dict[str, Any]:
    """A channel read's answer: the rendered messages, newest first, and the paging note."""
    text = f"Channel: #{name} ({channel})\n\n" + "\n\n".join(blocks)
    more = f"There are more messages available. To view the next page, use cursor: `{cursor}`\n"
    return {"messages": text, "pagination_info": more if cursor else "There are no more messages.\n"}


def thread_block(m: Msg) -> str:
    """A message in a thread read: From, Time and Message TS lines, the text, then a blank line and its lines."""
    name, email, user = m.who
    block = f"From: {name} <{email}> ({user})\nTime: {local(m.ts)}\nMessage TS: {m.ts}\n{m.text}"
    return block + ("\n\n" + "\n".join(m.lines) if m.lines else "")


def thread_page(parent: Msg | None, replies: list[Msg], *, first: int = 1, total: int | None = None) -> dict[str, Any]:
    """A thread read's answer: the parent's block, then each reply's. A later page (assumed) has no parent block."""
    total = total if total is not None else len(replies)
    parts = [f"=== THREAD PARENT MESSAGE ===\n{thread_block(parent)}"] if parent is not None else []
    parts.append(f"=== THREAD REPLIES ({total} total) ===")
    parts += [f"--- Reply {n} of {total} ---\n{thread_block(r)}" for n, r in enumerate(replies, start=first)]
    return {"messages": "\n\n".join(parts) + "\n", "pagination_info": "There are no more messages in this thread.\n"}


def hooked(payload: Any) -> list[dict[str, Any]]:
    """A result as Claude Code hands it to the capture hook: the tool's JSON as text content, non-ASCII escaped."""
    return [{"type": "text", "text": json.dumps(payload)}]


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
                "captured": f"2026-10-15T16:{seq:02d}:00Z",
                "tool": tool,
                "arguments": arguments,
                "result": result,
                "file": file,
            }
        )
        return self

    def channel(self, channel: str, *messages: Msg | str, shown: str | None = None) -> Run:
        """`slack_read_channel` as the skill calls it; `shown` is the id the result's Channel line gives."""
        blocks = [m if isinstance(m, str) else channel_block(m) for m in messages]
        arguments: dict[str, Any] = {"channel_id": channel, "limit": 50, "response_format": "detailed"}
        return self.add("slack_read_channel", hooked(page(shown or channel, *blocks)), **arguments)

    def thread(
        self,
        channel: str,
        parent: Msg | None,
        *replies: Msg,
        ts: str | None = None,
        first: int = 1,
        total: int | None = None,
    ) -> Run:
        """`slack_read_thread` on a parent's ts; `ts` names it when the page carries no parent block."""
        message_ts = ts or (parent.ts if parent is not None else "")
        arguments = {"channel_id": channel, "message_ts": message_ts, "limit": 50, "response_format": "detailed"}
        result = hooked(thread_page(parent, list(replies), first=first, total=total))
        return self.add("slack_read_thread", result, **arguments)

    def payload(self, holds: list[str] | None = None) -> dict[str, Any]:
        manifest = {
            "run": "r1",
            "source": "slack",
            "captures": len(self.captures),
            "final": self.final,
            "window": self.window,
        }
        return {"contract": 1, "source": "slack", "manifest": manifest, "captures": self.captures, "holds": holds or []}


def live_channel(run: Run) -> Run:
    """#team-test as a run reads it: the channel page, newest first, then the thread its Thread line names."""
    return run.channel(CHANNEL, FILE_POST, PARENT, POST).thread(CHANNEL, PARENT, REPLY_1, REPLY_2)
