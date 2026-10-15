"""The capture hook, `docket hook post-tool-use` (DR-0242): Claude Code runs it after every MCP tool call.

On docket's own `run_open` it ties the new run to this session; on a tool an open run of this session lists, it posts
the tool's input and result, unchanged, to the service; on anything else it does nothing. The common case, an MCP call
no run wants, reads one small file (the service's `runs.json`) with the standard library and platformdirs only: no
keychain, no network, none of the CLI's imports. Claude gets one line per capture beside the result, or why it wasn't
kept. It never prints a result, never blocks the call or changes its output, and exits 0 whatever happens.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import UTC, datetime
from typing import Any, BinaryIO, cast

from docket import paths

MARKER = "runs.json"
READ_TRIES = 5
READ_PAUSE = 0.02
RUN = re.compile(r"^run-[0-9]{8}T[0-9]{6}-[0-9a-f]{16}$")
# What Claude Code hands a hook instead of an MCP result over its 50,000-character threshold: a note pointing at the
# file it saved the result to (seen on Claude Code 2.1.300). Such a capture is never the whole result.
SAVED_TO_FILE = re.compile(
    r"^Error: result \([0-9,]+ characters? across [0-9,]+ lines?\) exceeds maximum allowed tokens"
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def own_name(tool_name: object) -> str | None:
    """An MCP tool's own name, the part after `mcp__<server>__`; None for a tool that isn't an MCP server's."""
    if not isinstance(tool_name, str) or not tool_name.startswith("mcp__"):
        return None
    _, sep, own = tool_name[len("mcp__") :].partition("__")
    return own if sep and own else None


def whole(result: object) -> bool:
    """Whether a tool_response is the tool's own result, not Claude Code's note about a file it saved it to."""
    texts = [result] if isinstance(result, str) else _texts(result)
    return not any(SAVED_TO_FILE.match(text) for text in texts)


def _texts(result: object) -> list[str]:
    """The text blocks of a result Claude Code hands over as MCP content: a list of `{type, text}`."""
    if not isinstance(result, list):
        return []
    texts: list[str] = []
    for item in cast(list[Any], result):
        text = cast(dict[str, Any], item).get("text") if isinstance(item, dict) else None
        if isinstance(text, str):
            texts.append(text)
    return texts


def opened_run(result: object) -> str | None:
    """The run id in a `run_open` result, which Claude Code hands over as its structured content in JSON text (seen on
    2.1.300), or as text blocks, or as an object."""
    given: Any = result
    candidates: list[Any] = [given] if isinstance(given, dict) else []
    for text in [given] if isinstance(given, str) else _texts(cast(object, given)):
        try:
            candidates.append(json.loads(text))
        except ValueError:
            continue
    for found in candidates:
        if isinstance(found, dict):
            opened = cast(dict[str, Any], found)
            run = opened.get("run")
            if isinstance(run, str) and RUN.match(run) and "steps" in opened:
                return run
    return None


def marker_runs() -> list[dict[str, Any]]:
    """The open runs the service listed, tried a few times over 100 ms; nothing readable means no run."""
    path = paths.state_dir() / MARKER
    for attempt in range(READ_TRIES):
        try:
            data: Any = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            if attempt + 1 < READ_TRIES:
                time.sleep(READ_PAUSE)
            continue
        runs: Any = cast(dict[str, Any], data).get("runs") if isinstance(data, dict) else None
        items = cast(list[Any], runs) if isinstance(runs, list) else []
        return [cast(dict[str, Any], r) for r in items if isinstance(r, dict)]
    return []


def wanted(runs: list[dict[str, Any]], session: object, agent: object, own: str) -> str | None:
    """The open run of this session and agent that lists this tool."""
    for run in runs:
        tools = run.get("tools")
        if (
            run.get("session") == session
            and run.get("agent") == agent
            and isinstance(tools, list)
            and own in cast(list[Any], tools)
            and isinstance(run.get("run"), str)
        ):
            return str(run["run"])
    return None


def say(line: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": _CONTROL.sub("?", line)},
    }


def handle(data: Any) -> dict[str, Any] | None:
    """What the hook prints for one PostToolUse input, or None to print nothing."""
    if not isinstance(data, dict):
        return None
    event = cast(dict[str, Any], data)
    if event.get("hook_event_name") != "PostToolUse":
        return None
    own = own_name(event.get("tool_name"))
    if own is None:
        return None
    session, agent = event.get("session_id"), event.get("agent_id")
    if not isinstance(session, str) or not (agent is None or isinstance(agent, str)):
        return None
    result = event.get("tool_response")
    if own == "run_open":
        run = opened_run(result)
        if run is not None:
            return bind(run, session, agent)
    run = wanted(marker_runs(), session, agent, own)
    if run is None:
        return None
    if not whole(result):
        return say(f"docket: not kept: {own} (too large)")
    return post(run, own, event, session, agent)


def bind(run: str, session: str, agent: str | None) -> dict[str, Any]:
    answer = _call("POST", f"/api/runs/{run}/session", {"session": session, "agent": agent})
    if answer is not None and answer[0] == 200:
        return say(f"docket: run {run} is this session's")
    why = _why(answer)
    return say(f"docket: run {run} isn't tied to this session ({why}); stop: its captures won't be kept")


def post(run: str, own: str, event: dict[str, Any], session: str, agent: str | None) -> dict[str, Any]:
    tool_input: Any = event.get("tool_input")
    body: dict[str, Any] = {
        "session": session,
        "agent": agent,
        "tool_name": event.get("tool_name"),
        "tool_use_id": str(event.get("tool_use_id") or ""),
        "arguments": tool_input if isinstance(tool_input, dict) else {},
        "result": event.get("tool_response"),
        "captured": datetime.now(UTC).isoformat(),
    }
    answer = _call("POST", f"/api/runs/{run}/captures", body)
    if answer is None or answer[0] != 200:
        return say(f"docket: not kept: {own} ({_why(answer)})")
    kept = answer[1]
    return say(
        f"docket: kept capture {kept.get('capture')} of run {run} ({own}), "
        f"{_kb(kept.get('used'))} of {_kb(kept.get('budget'))}"
    )


def _kb(value: Any) -> str:
    return f"{int(value) // 1024} KB" if isinstance(value, int) else "? KB"


def _why(answer: tuple[int, dict[str, Any]] | None) -> str:
    if answer is None:
        return "the service isn't running"
    status, body = answer
    detail = body.get("error") or body.get("detail") or f"the service answered {status}"
    return str(detail)


def _call(method: str, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
    """The run path's request: the token from its store and the port from the service's record, read only now."""
    from docket.errors import DocketError  # noqa: PLC0415 - kept off the no-run path
    from docket.service import client  # noqa: PLC0415
    from docket.service.token import Tokens  # noqa: PLC0415

    try:
        token = Tokens().read()
    except DocketError as e:
        return 0, {"error": f"the token can't be read: {e}"}
    answer = client.call_json(method, path, body, token=token)
    return None if answer is None else (answer.status, answer.body)


def main(stdin: BinaryIO | None = None, stdout: Any = None) -> int:
    """Read Claude Code's input, print what Claude reads, exit 0."""
    out = stdout or sys.stdout
    try:
        raw = (stdin or sys.stdin.buffer).read()
        reply = handle(json.loads(raw.decode("utf-8")))
    except Exception as e:
        reply = say(f"docket: the capture hook failed ({type(e).__name__})")
    if reply is not None:
        out.write(json.dumps(reply) + "\n")
        out.flush()
    return 0
