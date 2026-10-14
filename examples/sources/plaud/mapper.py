"""Plaud's mapper, Docket's reference source (contract 1): a run's captures of Plaud's MCP tools, and backfill files
fetched before Docket existed, turned into records in the core's shape.

Copy this folder to `.claude/skills/sources/plaud/` in your project and set `sources.plaud.mapper` in vault.yaml
(see SKILL.md). Standard library only: Docket runs it in its own process and reads its JSON. The payload shapes it
reads are the ones its tests pin; when Plaud changes them, change this file and its golden tests together.

Three records per recording, all with the recording's rid as thread and container: the recording (`plaud:<id>`), its
transcript (`plaud:<id>/<block>`) and Plaud's notes (`plaud:<id>/summary`). The recording waits for its notes,
because Plaud renames a recording when it writes them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

CONTRACT = 1
SOURCE = "plaud"
FILE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
# The meta keys both the listing and get_file give; get_file's other keys repeat the transcript and carry links.
META_KEYS = ("id", "name", "created_at", "serial_number", "start_at", "duration")
# Signed links and storage paths: a session token or the account's internals, never content.
DROPPED = frozenset({"presigned_url", "data_link", "data_path"})
# The polished transcript when there is one, else the raw one.
BLOCKS = ("transaction_polish", "transaction")
LISTS = ("files", "items", "data", "list", "notes", "note_list", "tabs")


@dataclass
class Seen:
    """Everything a run brought about one recording."""

    metas: list[tuple[str, int, dict[str, Any]]] = field(default_factory=list)
    pages: dict[str, dict[str | None, tuple[str, int, Any]]] = field(default_factory=dict)
    notes: list[tuple[str, int, list[tuple[str, str, str]], Any]] = field(default_factory=list)
    backfill: tuple[str, int, dict[str, Any]] | None = None


@dataclass
class Out:
    records: list[dict[str, Any]] = field(default_factory=list)
    set_aside: list[dict[str, Any]] = field(default_factory=list)
    waiting: list[dict[str, Any]] = field(default_factory=list)
    held: set[str] = field(default_factory=set)

    def aside(self, file_id: str | None, reason: str, tool: str | None = None, key: str | None = None) -> None:
        self.set_aside.append({"id": file_id, "reason": reason, "tool": tool, "key": key})


def map_run(run: dict[str, Any]) -> dict[str, Any]:
    held = set(run.get("holds") or [])
    final = bool(run["manifest"].get("final"))
    seen: dict[str, Seen] = {}
    out = Out()
    for capture in sorted(run["captures"], key=lambda c: c["seq"]):
        if capture.get("file") is not None:
            _backfill(capture, held, seen, out)
        else:
            _tool(capture, held, seen, out)
    for file_id in sorted(seen):
        _assemble(file_id, seen[file_id], final, out)
    return {
        "contract": CONTRACT,
        "records": out.records,
        "set_aside": out.set_aside,
        "waiting": out.waiting,
        "held": sorted(out.held),
    }


# -- reading captures ------------------------------------------------------------------------------------------------


def _usable(file_id: Any, held: set[str], out: Out, tool: str | None, key: str) -> str | None:
    """A capture's file id, read before anything else of it: a held recording is never parsed, and an id that
    isn't one is never echoed."""
    if not isinstance(file_id, str) or not FILE_ID.match(file_id):
        out.aside(None, "bad-id", tool, key)
        return None
    if file_id in held:
        out.held.add(file_id)
        return None
    return file_id


def _tool(capture: dict[str, Any], held: set[str], seen: dict[str, Seen], out: Out) -> None:
    tool = capture.get("tool")
    when, seq = capture["captured"], capture["seq"]
    arguments = capture.get("arguments") or {}
    if tool == "list_files":
        items = _items(unwrap(capture.get("result")))
        if items is None:
            out.aside(None, "no-listing", tool)
            return
        for item in items:
            file_id = _usable(item.get("id") if isinstance(item, dict) else None, held, out, tool, "id")
            if file_id is not None:
                seen.setdefault(file_id, Seen()).metas.append((when, seq, item))
        return
    if tool not in ("get_file", "get_transcript", "get_note"):
        out.aside(None, "unknown-tool")
        return
    file_id = _usable(arguments.get("file_id"), held, out, tool, "file_id")
    if file_id is None:
        return
    payload = unwrap(capture.get("result"))
    if payload is None:
        out.aside(file_id, "no-json", tool)
        return
    recording = seen.setdefault(file_id, Seen())
    if tool == "get_file":
        if isinstance(payload, dict):
            recording.metas.append((when, seq, payload))
        else:
            out.aside(file_id, "not-an-object", tool)
    elif tool == "get_transcript":
        block = arguments.get("block") or "transaction"
        recording.pages.setdefault(block, {})[arguments.get("cursor")] = (when, seq, payload)
    else:
        tabs = _items(payload)
        if tabs is None:
            out.aside(file_id, "no-tabs", tool)
            return
        recording.notes.append((when, seq, _note_tabs(tabs), tabs))


def _backfill(capture: dict[str, Any], held: set[str], seen: dict[str, Seen], out: Out) -> None:
    """A file a backfill wrote: `file_id`, `block` (null when the recording had no transcript), `segments`, and
    `summary` as [{title, type, content}]."""
    content = capture.get("result")
    if not isinstance(content, dict) or "file_id" not in content:
        out.aside(None, "not-a-backfill-file", None, "file_id")
        return
    file_id = _usable(content["file_id"], held, out, None, "file_id")
    if file_id is not None:
        seen.setdefault(file_id, Seen()).backfill = (capture["captured"], capture["seq"], content)


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


def _items(payload: Any) -> list[Any] | None:
    """The list of objects a result carries: the result itself, or the first list under a known key."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in LISTS:
            if isinstance(payload.get(key), list):
                return payload[key]
    return None


def _note_tabs(tabs: list[Any]) -> list[tuple[str, str, str]]:
    """Plaud's tabs as (type, title, content): a tab counts when its content isn't empty; its title is data_title,
    else data_tab_name, else Summary. The backfill script used the same rule, so both routes give the same text."""
    out: list[tuple[str, str, str]] = []
    for tab in tabs:
        if not isinstance(tab, dict):
            continue
        content = tab.get("data_content")
        if isinstance(content, str) and content.strip():
            title = tab.get("data_title") or tab.get("data_tab_name") or "Summary"
            out.append((str(tab.get("data_type") or ""), str(title), content))
    return out


def _backfill_tabs(summary: Any) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for tab in summary if isinstance(summary, list) else []:
        if isinstance(tab, dict) and isinstance(tab.get("content"), str) and tab["content"].strip():
            out.append((str(tab.get("type") or ""), str(tab.get("title") or "Summary"), tab["content"]))
    return out


# -- assembling a recording ------------------------------------------------------------------------------------------


def _assemble(file_id: str, seen: Seen, final: bool, out: Out) -> None:
    rid = f"{SOURCE}:{file_id}"
    # The newest meta wins, so a listing captured after the run's notes gives the renamed recording.
    meta_at = max(seen.metas, key=lambda m: (m[0], m[1]), default=None)
    meta = {k: meta_at[2].get(k) for k in META_KEYS} if meta_at is not None else {}
    ts = parse_time(meta.get("start_at"))
    if meta_at is None or ts is None:
        out.aside(file_id, "no-meta", None, "start_at")
        return
    base = {"source": SOURCE, "container": rid, "thread": rid, "ts": ts, "fidelity": "verbatim"}
    final = final or seen.backfill is not None

    transcript = _transcript(file_id, seen, out)
    if transcript is not None:
        out.records.append({**base, **transcript})
    elif _asked_for_transcript(seen) and not final:
        out.waiting.append({"id": file_id, "for": "transcript"})

    tabs, raw_tabs, via, fetched = _tabs(seen)
    if tabs:
        text = "\n\n".join(f"## {title}\n\n{content}" for _, title, content in sorted(tabs))
        native = {"file_id": file_id, "tabs": scrub(raw_tabs)}
        summary = {"rid": f"{rid}/summary", "kind": "vendor-summary", "engine": "plaud", "text": text}
        out.records.append({**base, **summary, "native": native, "fetched": fetched, "via": via})
    if tabs or final:
        # Staged with its notes, under the newest name: Plaud renames a recording when it writes them.
        name = meta.get("name") if isinstance(meta.get("name"), str) else None
        recording = {"rid": rid, "kind": "recording", "title": name, "native": scrub(meta)}
        out.records.append({**base, **recording, "fetched": meta_at[0], "via": "plaud-mcp"})
    else:
        out.waiting.append({"id": file_id, "for": "summary"})


def _transcript(file_id: str, seen: Seen, out: Out) -> dict[str, Any] | None:
    """The transcript record, from the backfill or a complete page chain of the polished block, else the raw one."""
    if seen.backfill is not None:
        when, _, content = seen.backfill
        block, raw = content.get("block"), content.get("segments")
        if not block or not raw:
            return None
        return _transcript_record(file_id, block, raw, out, fetched=when, via="plaud-file")
    for block in BLOCKS:
        pages = seen.pages.get(block)
        if not pages:
            continue
        chain = _chain(pages)
        if chain is None:
            out.aside(file_id, "broken-chain", "get_transcript", block)
            return None
        raw = [s for _, _, page in chain for s in _page_segments(page)]
        if raw:
            return _transcript_record(file_id, block, raw, out, fetched=chain[-1][0], via="plaud-mcp")
    return None


def _asked_for_transcript(seen: Seen) -> bool:
    """Whether the run asked for a transcript that didn't come usable: not ready yet, or its pages broken."""
    return bool(seen.pages)


def _chain(pages: dict[str | None, tuple[str, int, Any]]) -> list[tuple[str, int, Any]] | None:
    """The pages from the first (no cursor) through each next_cursor to one without: complete, or None. A partial
    transcript is never staged, since it would stay in raw for good."""
    chain: list[tuple[str, int, Any]] = []
    cursor: str | None = None
    while True:
        page = pages.get(cursor)
        if page is None or len(chain) > len(pages):
            return None
        chain.append(page)
        cursor = page[2].get("next_cursor") if isinstance(page[2], dict) else None
        if not cursor:
            return chain


def _page_segments(page: Any) -> list[Any]:
    # A transcript that isn't ready comes back as a list, or with no segments.
    if isinstance(page, dict) and isinstance(page.get("segments"), list):
        return page["segments"]
    return []


def _transcript_record(
    file_id: str, block: str, raw: list[Any], out: Out, *, fetched: str, via: str
) -> dict[str, Any] | None:
    segments: list[dict[str, Any]] = []
    for s in raw:
        item: dict[str, Any] = s if isinstance(s, dict) else {}
        start, end, text = _ms(item.get("start_time")), _ms(item.get("end_time")), item.get("content")
        if start is None or end is None or end < start or not isinstance(text, str):
            out.aside(file_id, "bad-segment", None, "segments")
            return None
        # The speaker as named in Plaud's app, so a renamed speaker is stored under the new name.
        speaker = item["speaker"] if isinstance(item.get("speaker"), str) and item["speaker"] else None
        segments.append({"start_ms": start, "end_ms": end, "speaker": speaker, "text": text})
    return {
        "rid": f"{SOURCE}:{file_id}/{block}",
        "kind": "transcript",
        "engine": f"plaud/{block}",
        "segments": segments,
        "native": {"file_id": file_id, "block": block, "segments": scrub(raw)},
        "fetched": fetched,
        "via": via,
    }


def _ms(value: Any) -> int | None:
    """Plaud's milliseconds as a whole number, or None when it isn't a time."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return round(value)
    return None


def _tabs(seen: Seen) -> tuple[list[tuple[str, str, str]], Any, str, str]:
    """The newest notes: (tabs, as given, via, fetched)."""
    if seen.notes:
        when, _, tabs, raw = max(seen.notes, key=lambda n: (n[0], n[1]))
        return tabs, raw, "plaud-mcp", when
    if seen.backfill is not None:
        when, _, content = seen.backfill
        return _backfill_tabs(content.get("summary")), content.get("summary"), "plaud-file", when
    return [], None, "plaud-mcp", ""


def parse_time(value: Any) -> str | None:
    """Plaud's time as UTC: a time without a zone is UTC (checked against local times written down by hand for a few
    recordings); a time with a zone is taken as given; a number is milliseconds since 1970."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        moment = datetime.fromtimestamp(value / 1000, UTC)
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
    else:
        return None
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def scrub(value: Any) -> Any:
    """A payload without its signed links and storage paths, at any depth."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if k not in DROPPED}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value
