"""Plaud's pull, Docket's reference source (pull contract 1): the run's calls made here, against the user's own Plaud
MCP server, so what Plaud returns goes straight into Docket's run and never through Claude's context (#305).

`docket sync pull <sync> plaud` runs this file with Docket's interpreter and hands it one JSON request on stdin; it
writes JSON lines on stdout, one per call it made, and Docket posts each to the run as the capture hook would. Standard
library only, like the mapper beside it. It starts the Plaud MCP server itself, as Claude Code does, by the command
the request names, else `npx -y @plaud-ai/mcp@latest`: the server signs in with its own tokens, which this file never
reads.

The request: `phase` (run, final or media), `window` (`since` and `until`, or null), `kept` and `held` (native ids to
skip), `ids` (to fetch by id) and `server` (the command, or null). The lines:

| Line | What |
|---|---|
| `{"capture": {"tool", "arguments", "result"}}` | a call that succeeded, its result as the server gave it |
| `{"skip": {"id", "reason"}}` | an id it skipped: gone, rate-limited |
| `{"fetched": id}` | a recording it fetched by id or from the listing, whose audio the media phase may put |
| `{"media": {"id", "url"}}` | the media phase: a recording's https link to its audio, never captured |
| `{"stop": why}` | stopped before the end: Docket doesn't close the run |

The route is the one `SKILL.md` gives Claude: list newest first until a page's oldest recording starts before the
window, each recording's transcript pages by cursor (polished first, else raw) and its notes, then the listing again,
since Plaud renames a recording when it writes its notes; by id, `get_file` first.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from typing import IO, Any

CONTRACT = 1
SERVER = ["npx", "-y", "@plaud-ai/mcp@latest"]
PROTOCOL = "2025-06-18"
PAGE_SIZE = 50
# A page of 100 utterances stays under the 50,000 characters Claude Code keeps whole; the pull keeps the same pages,
# so a run pulled and a run Claude made map alike.
LIMIT = 100
BLOCKS = ("transaction_polish", "transaction")
PAUSES = (2, 4, 8, 16)
# Pages a listing goes through at most: a year of recordings at the page size.
MAX_PAGES = 40


class StopError(Exception):
    """The pull can't go on: Docket keeps the run open, and it drops."""


class GoneError(Exception):
    """The server has no such recording."""


class LimitedError(Exception):
    """Still rate-limited after the pauses."""


class Server:
    """The Plaud MCP server over stdio: newline-delimited JSON-RPC 2.0, one request at a time."""

    def __init__(self, argv: list[str]) -> None:
        found = shutil.which(argv[0])
        try:
            self.proc = subprocess.Popen(  # noqa: S603 - the user's own server command
                [found or argv[0], *argv[1:]],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as e:
            raise StopError(f"the Plaud MCP server didn't start ({type(e).__name__}): is Node installed?") from None
        self.n = 0
        self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                    "clientInfo": {"name": "docket-pull", "version": str(CONTRACT)}})  # fmt: skip
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, message: dict[str, Any]) -> None:
        stdin: IO[bytes] = self.proc.stdin  # type: ignore[assignment]
        stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        stdin.flush()

    def request(self, method: str, params: dict[str, Any]) -> Any:
        self.n += 1
        self._send({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params})
        stdout: IO[bytes] = self.proc.stdout  # type: ignore[assignment]
        while True:
            line = stdout.readline()
            if not line:
                raise StopError("the Plaud MCP server ended")
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            if "method" in message and "id" in message:
                # A request of the server's own, such as ping: answered empty, or refused.
                known = message["method"] == "ping"
                reply = {"result": {}} if known else {"error": {"code": -32601, "message": "not supported"}}
                self._send({"jsonrpc": "2.0", "id": message["id"], **reply})
                continue
            if message.get("id") != self.n:
                continue
            if "error" in message:
                raise StopError(f"the Plaud MCP server refused {method}: {_short(message['error'])}")
            return message.get("result")

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """A tool's result: retried with growing pauses while rate-limited; a sign-in it needs stops the pull."""
        for pause in (*PAUSES, None):
            result = self.request("tools/call", {"name": tool, "arguments": arguments})
            result = result if isinstance(result, dict) else {}
            if not result.get("isError"):
                return result
            text = _text(result).lower()
            if "not authenticated" in text or "401" in text or "unauthorized" in text:
                raise StopError("plaud isn't signed in: call its login tool, then pull again")
            if "429" in text or "too many requests" in text or "rate limit" in text:
                if pause is None:
                    raise LimitedError
                time.sleep(pause)
                continue
            if "404" in text or "not found" in text or "500" in text:
                raise GoneError
            raise StopError(f"{tool} failed: {_short(text)}")
        raise LimitedError

    def close(self) -> None:
        for pipe in (self.proc.stdin, self.proc.stdout):
            if pipe is not None:
                pipe.close()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def _text(result: dict[str, Any]) -> str:
    content = result.get("content")
    items = content if isinstance(content, list) else []
    return "\n".join(i.get("text", "") for i in items if isinstance(i, dict) and isinstance(i.get("text"), str))


def _short(value: Any) -> str:
    text = value.get("message", "") if isinstance(value, dict) else str(value)
    return " ".join(str(text).split())[:120]


def payload(result: dict[str, Any]) -> Any:
    """A result's structuredContent, else the first JSON value in its text, as the mapper reads it."""
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    text = _text(result)
    decoder = json.JSONDecoder()
    for start, ch in enumerate(text):
        if ch in "{[":
            try:
                return decoder.raw_decode(text, start)[0]
            except ValueError:
                continue
    return None


def items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("files", "items", "data", "list"):
            if isinstance(value.get(key), list):
                return value[key]
    return []


def started(value: Any) -> datetime | None:
    """Plaud's time as UTC, as the mapper reads it: a time without a zone is UTC, a number milliseconds."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value / 1000, UTC)
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return None


class Pull:
    def __init__(self, server: Server, out: IO[str]) -> None:
        self.server = server
        self.out = out

    def emit(self, line: dict[str, Any]) -> None:
        self.out.write(json.dumps(line, ensure_ascii=False) + "\n")
        self.out.flush()

    def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        result = self.server.call(tool, arguments)
        self.emit({"capture": {"tool": tool, "arguments": arguments, "result": result}})
        return payload(result)

    def listing(self, since: datetime | None, pages: int | None = None) -> tuple[list[dict[str, Any]], int]:
        """The listing, newest first, until a page's oldest recording starts before `since`, or `pages` pages."""
        found: list[dict[str, Any]] = []
        page = 0
        while page < (pages or MAX_PAGES):
            page += 1
            try:
                answer = self.call("list_files", {"page": page, "page_size": PAGE_SIZE})
            except LimitedError:
                raise StopError("plaud's listing stayed rate-limited") from None
            listed = [i for i in items(answer) if isinstance(i, dict)]
            found += listed
            starts = [t for t in (started(i.get("start_at")) for i in listed) if t is not None]
            if len(listed) < PAGE_SIZE or (since is not None and starts and min(starts) < since):
                break
        return found, page

    def recording(self, file_id: str, *, by_id: bool) -> None:
        """One recording: its file first when fetched by id, then its transcript's pages, then its notes."""
        try:
            if by_id:
                self.call("get_file", {"file_id": file_id})
            for block in BLOCKS:
                if self.transcript(file_id, block):
                    break
            self.call("get_note", {"file_id": file_id})
        except GoneError:
            self.emit({"skip": {"id": file_id, "reason": "gone"}})
            return
        except LimitedError:
            self.emit({"skip": {"id": file_id, "reason": "rate-limited"}})
            return
        self.emit({"fetched": file_id})

    def transcript(self, file_id: str, block: str) -> bool:
        """Every page of a block by cursor; whether it held segments."""
        cursor: str | None = None
        held = False
        for _ in range(1000):
            arguments: dict[str, Any] = {"file_id": file_id, "block": block, "limit": LIMIT}
            if cursor:
                arguments["cursor"] = cursor
            page = self.call("get_transcript", arguments)
            if not isinstance(page, dict):
                return held
            held = held or bool(page.get("segments"))
            cursor = page.get("next_cursor")
            if not cursor:
                return held
        return held

    def run(self, request: dict[str, Any]) -> None:
        window = request.get("window") or {}
        since = started(window.get("since"))
        held, kept, ids = (set(request.get(k) or []) for k in ("held", "kept", "ids"))
        listed, pages = self.listing(since)
        todo: list[str] = []
        for meta in listed:
            file_id, start = meta.get("id"), started(meta.get("start_at"))
            if not isinstance(file_id, str) or file_id in held or (file_id in kept and file_id not in ids):
                continue
            if start is not None and since is not None and start < since:
                continue
            todo.append(file_id)
        for file_id in dict.fromkeys(todo):
            self.recording(file_id, by_id=False)
        for file_id in sorted(ids - set(todo) - held):
            self.recording(file_id, by_id=True)
        if todo:
            self.listing(since, pages)

    def final(self, request: dict[str, Any]) -> None:
        held = set(request.get("held") or [])
        for file_id in sorted(set(request.get("ids") or []) - held):
            self.recording(file_id, by_id=True)

    def media(self, request: dict[str, Any]) -> None:
        """Each recording's audio link, from get_file, never captured: a signed link is a credential for a day."""
        for file_id in sorted(set(request.get("ids") or [])):
            try:
                detail = payload(self.server.call("get_file", {"file_id": file_id}))
            except (GoneError, LimitedError):
                continue
            url = detail.get("presigned_url") if isinstance(detail, dict) else None
            if isinstance(url, str) and url.startswith("https://"):
                self.emit({"media": {"id": file_id, "url": url}})


def main() -> int:
    request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    out = sys.stdout
    if request.get("contract") != CONTRACT:
        out.write(json.dumps({"stop": f"this pull.py speaks contract {CONTRACT}"}) + "\n")
        return 0
    server: Server | None = None
    try:
        server = Server(list(request.get("server") or SERVER))
        pull = Pull(server, out)
        phase = request.get("phase")
        if phase == "run":
            pull.run(request)
        elif phase == "final":
            pull.final(request)
        elif phase == "media":
            pull.media(request)
        else:
            raise StopError(f"no phase {phase!r}")
    except StopError as e:
        out.write(json.dumps({"stop": str(e)}) + "\n")
    finally:
        if server is not None:
            server.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
