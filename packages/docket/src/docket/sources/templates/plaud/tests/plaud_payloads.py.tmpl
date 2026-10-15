"""Plaud's payloads, invented and written from scratch in the shape its MCP tools return: a supplier call between Ben
Sample and Cleo Test with a renamed speaker, an overlap and a page break; a short one whose summary arrives later
under a new name; one whose transcript isn't ready; one held. Nothing here comes from a real recording."""

from __future__ import annotations

import json
from typing import Any

CALL = "of_00c0ffee00c0ffee00c0ffee00c0ffee"
SHORT = "of_00beef0000beef0000beef0000beef00"
UNREADY = "of_00cafe0000cafe0000cafe0000cafe00"
HELD = "of_00dead0000dead0000dead0000dead00"
PRIVATE_TITLE = "Dentist with Cleo"


def meta(file_id: str, name: str, start_at: Any = "2026-10-14T09:30:00", **extra: Any) -> dict[str, Any]:
    return {
        "id": file_id,
        "name": name,
        "created_at": "2026-10-14T09:31:02",
        "serial_number": None,
        "start_at": start_at,
        "duration": 2_400_000,
        **extra,
    }


def segment(start: int, end: int, speaker: str, content: str, **extra: Any) -> dict[str, Any]:
    return {
        "start_time": start,
        "end_time": end,
        "speaker": speaker,
        "original_speaker": "Speaker 1",
        "content": content,
        "segment_uid": f"u{start}",
        **extra,
    }


CALL_PAGE_1 = [
    segment(0, 4000, "Ben Sample", "Morning, Cleo."),
    # Cleo starts before Ben has finished: an overlap, kept. The storage path inside must go.
    segment(3000, 9000, "Cleo Test", "Morning. Shall we start with the price?", data_link="s3://bucket/internal"),
]
CALL_PAGE_2 = [segment(728_000, 740_500, "Ben Sample", "So ten million, for the whole quarter.")]
TABS = [
    {
        "data_type": "auto_sum_note",
        "data_title": "Summary",
        "data_tab_name": "summary",
        "data_content": "Price agreed.",
    },
    {"data_type": "template", "data_title": None, "data_tab_name": "Action items", "data_content": "- Cleo sends it"},
    {"data_type": "high_light", "data_title": None, "data_tab_name": "Highlights", "data_content": "  "},
]
BACKFILL_SUMMARY = [
    {"title": "Summary", "type": "auto_sum_note", "content": "Price agreed."},
    {"title": "Action items", "type": "template", "content": "- Cleo sends it"},
]


def structured(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "(see structuredContent)"}], "structuredContent": payload}


def as_text(payload: Any) -> dict[str, Any]:
    text = f"Here is what Plaud returned:\n{json.dumps(payload)}\nAsk if you need more."
    return {"content": [{"type": "text", "text": text}]}


class Run:
    """A run's captures as the core hands them to the mapper."""

    def __init__(self, *, final: bool = False) -> None:
        self.captures: list[dict[str, Any]] = []
        self.final = final

    def add(self, tool: str | None, result: Any, *, file: str | None = None, **arguments: Any) -> Run:
        seq = len(self.captures)
        self.captures.append(
            {
                "seq": seq,
                "run": "r1",
                "captured": f"2026-10-14T10:{seq:02d}:00Z",
                "tool": tool,
                "arguments": arguments,
                "result": result,
                "file": file,
            }
        )
        return self

    def listing(self, *items: Any) -> Run:
        return self.add("list_files", structured({"files": list(items), "total": len(items)}), page=1)

    def transcript(
        self,
        file_id: str,
        segments: list[Any],
        *,
        block: str = "transaction_polish",
        cursor: str | None = None,
        next_cursor: str | None = None,
        result: Any = None,
    ) -> Run:
        page = {"file_id": file_id, "block": block, "segments": segments, "next_cursor": next_cursor}
        arguments: dict[str, Any] = {"file_id": file_id, "block": block, "limit": 500}
        if cursor is not None:
            arguments["cursor"] = cursor
        return self.add("get_transcript", structured(page) if result is None else result, **arguments)

    def note(self, file_id: str, tabs: list[Any] | None = None) -> Run:
        return self.add("get_note", as_text({"notes": TABS if tabs is None else tabs}), file_id=file_id)

    def backfill(
        self, file_id: Any, block: str | None = "transaction_polish", segments: Any = None, summary: Any = None
    ) -> Run:
        content = {
            "file_id": file_id,
            "block": block,
            "segments": [*CALL_PAGE_1, *CALL_PAGE_2] if segments is None else segments,
            "summary": BACKFILL_SUMMARY if summary is None else summary,
        }
        return self.add(None, content, file=f"{file_id}.json")

    def payload(self, holds: list[str] | None = None) -> dict[str, Any]:
        manifest = {"run": "r1", "source": "plaud", "captures": len(self.captures), "final": self.final}
        return {"contract": 1, "source": "plaud", "manifest": manifest, "captures": self.captures, "holds": holds or []}


def live_call(run: Run) -> Run:
    """The supplier call as a live run fetches it: listed, two transcript pages, its notes, listed again."""
    return (
        run.listing(meta(CALL, "Recording 2026-10-14"))
        .transcript(CALL, CALL_PAGE_1, next_cursor="c2")
        .transcript(CALL, CALL_PAGE_2, cursor="c2")
        .note(CALL)
        .listing(meta(CALL, "Supplier call"))
    )
