"""An invented Plaud MCP server for the pull's tests: newline-delimited JSON-RPC 2.0 over stdio, answering from a
scenario file and logging each tool call to a file. Nothing here comes from Plaud's real data.

    python fake_plaud_server.py <scenario.json> <calls.jsonl>

The scenario: `pages` (the listing, a list of pages), `transcripts` (`<id>|<block>|<cursor>` to a page), `notes`
(`<id>` to its notes), `files` (`<id>` to its details), `signed_out` (every call refused as not signed in),
`limited` (`<tool>|<id>` to how many times it answers 429 first) and `gone` (ids it has no recording for).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def ok(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "(see structuredContent)"}], "structuredContent": payload}


def error(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def main() -> None:
    scenario = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    log = Path(sys.argv[2])
    limited: dict[str, int] = dict(scenario.get("limited") or {})
    for raw in sys.stdin:
        message = json.loads(raw)
        if "id" not in message:
            continue
        method, params = message.get("method"), message.get("params") or {}
        if method == "initialize":
            result: Any = {"protocolVersion": params.get("protocolVersion"), "capabilities": {"tools": {}},
                           "serverInfo": {"name": "fake-plaud", "version": "0"}}  # fmt: skip
        elif method == "tools/call":
            name, arguments = params["name"], params.get("arguments") or {}
            with log.open("a", encoding="utf-8") as calls:
                calls.write(json.dumps({"tool": name, "arguments": arguments}) + "\n")
            result = answer(scenario, limited, name, arguments)
        else:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message["id"],
                                         "error": {"code": -32601, "message": "no such method"}}) + "\n")  # fmt: skip
            sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}) + "\n")
        sys.stdout.flush()


def answer(scenario: dict[str, Any], limited: dict[str, int], name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if scenario.get("signed_out"):
        return error("Not authenticated. Call the login tool.")
    file_id = arguments.get("file_id", "")
    key = f"{name}|{file_id}"
    if limited.get(key, 0) > 0:
        limited[key] -= 1
        return error("429 Too Many Requests")
    if file_id in (scenario.get("gone") or []):
        return error("404 Not Found")
    if name == "list_files":
        pages = scenario.get("pages") or []
        page = int(arguments.get("page", 1))
        return ok({"files": pages[page - 1] if page <= len(pages) else [], "total": sum(len(p) for p in pages)})
    if name == "get_transcript":
        key = f"{file_id}|{arguments.get('block')}|{arguments.get('cursor') or ''}"
        found = (scenario.get("transcripts") or {}).get(key)
        return ok(found if found is not None else {"file_id": file_id, "segments": [], "next_cursor": None})
    if name == "get_note":
        return ok((scenario.get("notes") or {}).get(file_id, {"notes": []}))
    if name == "get_file":
        return ok((scenario.get("files") or {}).get(file_id, {"id": file_id}))
    return error(f"no tool {name}")


if __name__ == "__main__":
    main()
