"""The Plaud pull (#305) against an invented Plaud MCP server: the route `SKILL.md` gives Claude, made in code, each
call a line Docket posts to the run. Invented recordings only, from plaud_payloads."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from plaud_payloads import CALL, CALL_PAGE_1, CALL_PAGE_2, HELD, SHORT, TABS, UNREADY, meta, segment

HERE = Path(__file__).resolve().parent
FAKE = HERE / "fake_plaud_server.py"
SINCE = "2026-10-13T00:00:00Z"
OLD = "of_00f00d0000f00d0000f00d0000f00d00"
KEPT = "of_00face0000face0000face0000face00"
BY_ID = "of_00bead0000bead0000bead0000bead00"


def scenario(**extra: Any) -> dict[str, Any]:
    """The supplier call over two pages, a short call, one whose polished transcript isn't ready, one held, one kept,
    and one from before the window on the second page."""
    return {
        "pages": [
            [meta(CALL, "Supplier call", "2026-10-14T09:30:00"), meta(SHORT, "Short", "2026-10-14T08:00:00")],
            [meta(UNREADY, "Unready", "2026-10-13T10:00:00"), meta(HELD, "Held", "2026-10-13T09:00:00")],
            [meta(KEPT, "Kept", "2026-10-13T08:00:00"), meta(OLD, "Old", "2026-10-01T08:00:00")],
            [meta("of_never_listed", "Never", "2026-09-01T08:00:00")],
        ],
        "transcripts": {
            f"{CALL}|transaction_polish|": {"segments": CALL_PAGE_1, "next_cursor": "c2"},
            f"{CALL}|transaction_polish|c2": {"segments": CALL_PAGE_2, "next_cursor": None},
            f"{SHORT}|transaction_polish|": {"segments": [segment(0, 2000, "Ben Sample", "Quick one.")]},
            f"{UNREADY}|transaction|": {"segments": [segment(0, 3000, "Cleo Test", "Raw words.")]},
            f"{BY_ID}|transaction_polish|": {"segments": [segment(0, 1000, "Ben Sample", "By id.")]},
        },
        "notes": {CALL: {"notes": TABS}},
        "files": {
            CALL: meta(CALL, "Supplier call", presigned_url="https://example.invalid/a.mp3?sig=1"),
            SHORT: meta(SHORT, "Short", presigned_url="http://example.invalid/b.mp3"),
        },
        **extra,
    }


def run(pull: ModuleType, tmp_path: Path, phase: str, given: dict[str, Any], **request: Any) -> list[dict[str, Any]]:
    """One phase of the pull, in this process, against the fake server: the lines it wrote."""
    (tmp_path / "scenario.json").write_text(json.dumps(given), encoding="utf-8")
    server = pull.Server([sys.executable, str(FAKE), str(tmp_path / "scenario.json"), str(tmp_path / "calls.jsonl")])
    out = io.StringIO()
    try:
        getattr(pull.Pull(server, out), phase)({"contract": 1, "phase": phase, **request})
    except pull.StopError as e:
        out.write(json.dumps({"stop": str(e)}) + "\n")
    finally:
        server.close()
    return [json.loads(line) for line in out.getvalue().splitlines()]


def calls(lines: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    return [(line["capture"]["tool"], line["capture"]["arguments"]) for line in lines if "capture" in line]


@pytest.fixture
def small_pages(pull: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pull, "PAGE_SIZE", 2)


@pytest.mark.usefixtures("small_pages")
def test_a_run_lists_to_the_window_and_fetches_each_recording_in_it_once(pull: ModuleType, tmp_path: Path) -> None:
    lines = run(pull, tmp_path, "run", scenario(), window={"since": SINCE}, held=[HELD], kept=[KEPT], ids=[BY_ID])
    made = calls(lines)
    # The listing stops at the page whose oldest recording is before the window, and is listed again at the end.
    pages = [args["page"] for tool, args in made if tool == "list_files"]
    assert pages == [1, 2, 3, 1, 2, 3]
    fetched = [(tool, args.get("file_id"), args.get("block"), args.get("cursor")) for tool, args in made
               if tool != "list_files"]  # fmt: skip
    assert fetched == [
        ("get_transcript", CALL, "transaction_polish", None),
        ("get_transcript", CALL, "transaction_polish", "c2"),
        ("get_note", CALL, None, None),
        ("get_transcript", SHORT, "transaction_polish", None),
        ("get_note", SHORT, None, None),
        # Its polished transcript isn't ready, so the raw one.
        ("get_transcript", UNREADY, "transaction_polish", None),
        ("get_transcript", UNREADY, "transaction", None),
        ("get_note", UNREADY, None, None),
        # An id owed but not listed is fetched by id, its file first; the held, the kept and the old never are.
        ("get_file", BY_ID, None, None),
        ("get_transcript", BY_ID, "transaction_polish", None),
        ("get_note", BY_ID, None, None),
    ]
    assert [line["fetched"] for line in lines if "fetched" in line] == [CALL, SHORT, UNREADY, BY_ID]
    # A capture carries the server's result as it came, structured content and all.
    first_page = next(line["capture"]["result"] for line in lines if "capture" in line)
    assert first_page["structuredContent"]["files"][0]["id"] == CALL


def test_a_kept_recording_owed_again_is_fetched_and_one_gone_is_skipped(pull: ModuleType, tmp_path: Path) -> None:
    lines = run(pull, tmp_path, "run", scenario(gone=["of_gone"]), window={"since": SINCE}, kept=[KEPT],
                ids=[KEPT, "of_gone"])  # fmt: skip
    assert ("get_note", {"file_id": KEPT}) in calls(lines)
    assert {"skip": {"id": "of_gone", "reason": "gone"}} in lines
    assert "of_gone" not in [line.get("fetched") for line in lines]


def test_a_signed_out_server_stops_the_pull_before_any_capture(pull: ModuleType, tmp_path: Path) -> None:
    lines = run(pull, tmp_path, "run", scenario(signed_out=True), window={"since": SINCE})
    assert lines == [{"stop": "plaud isn't signed in: call its login tool, then pull again"}]


def test_a_rate_limit_is_waited_out_and_one_that_lasts_skips_that_recording(
    pull: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    waited: list[float] = []
    monkeypatch.setattr(pull, "PAUSES", (1, 2))
    # The module's own name only: time.sleep itself is every thread's.
    monkeypatch.setattr(pull, "sleep", waited.append)
    given = scenario(limited={f"get_note|{CALL}": 1, f"get_transcript|{SHORT}": 9})
    lines = run(pull, tmp_path, "run", given, window={"since": SINCE})
    assert waited == [1, 1, 2]
    assert {"skip": {"id": SHORT, "reason": "rate-limited"}} in lines
    assert ("get_note", {"file_id": CALL}) in calls(lines)
    # A listing that stays limited stops the pull: the run can't close without it.
    stopped = run(pull, tmp_path, "run", scenario(limited={"list_files|": 9}), window={"since": SINCE})
    assert stopped == [{"stop": "plaud's listing stayed rate-limited"}]


def test_the_media_phase_gives_https_links_only_and_captures_nothing(pull: ModuleType, tmp_path: Path) -> None:
    lines = run(pull, tmp_path, "media", scenario(gone=["of_gone"]), ids=[CALL, SHORT, "of_gone"])
    assert lines == [{"media": {"id": CALL, "url": "https://example.invalid/a.mp3?sig=1"}}]


def test_the_script_answers_one_request_on_stdin_with_lines_and_exits_0(pull: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "scenario.json").write_text(json.dumps(scenario()), encoding="utf-8")
    server = [sys.executable, str(FAKE), str(tmp_path / "scenario.json"), str(tmp_path / "calls.jsonl")]
    request = {"contract": 1, "phase": "final", "ids": [BY_ID], "server": server}
    done = subprocess.run([sys.executable, "-I", str(HERE.parent / "pull.py")], input=json.dumps(request).encode(),
                          capture_output=True, check=False, timeout=60)  # fmt: skip
    assert done.returncode == 0
    lines = [json.loads(line) for line in done.stdout.decode().splitlines()]
    tools = [line["capture"]["tool"] for line in lines if "capture" in line]
    assert tools == ["get_file", "get_transcript", "get_note"]
    other = subprocess.run([sys.executable, "-I", str(HERE.parent / "pull.py")], input=b'{"contract": 2}',
                           capture_output=True, check=False, timeout=60)  # fmt: skip
    assert json.loads(other.stdout) == {"stop": "this pull.py speaks contract 1"}


@pytest.mark.usefixtures("small_pages")
def test_what_the_pull_captures_maps_as_a_run_claude_made(pull: ModuleType, mapper: ModuleType, tmp_path: Path) -> None:
    lines = run(pull, tmp_path, "run", scenario(), window={"since": SINCE}, held=[HELD], kept=[KEPT])
    captures = [
        {"seq": n, "run": "r1", "captured": f"2026-10-14T10:{n:02d}:00Z", "tool": line["capture"]["tool"],
         "arguments": line["capture"]["arguments"], "result": line["capture"]["result"], "file": None}
        for n, line in enumerate(line for line in lines if "capture" in line)
    ]  # fmt: skip
    manifest = {"run": "r1", "source": "plaud", "captures": len(captures), "final": False,
                "window": {"since": SINCE, "until": "2026-10-15T00:00:00Z"}}  # fmt: skip
    mapped = mapper.map_run({"contract": 1, "source": "plaud", "manifest": manifest, "captures": captures,
                             "holds": [HELD]})  # fmt: skip
    records = {r["rid"]: r for r in mapped["records"]}
    transcript = records[f"plaud:{CALL}/transaction_polish"]
    assert [s["text"] for s in transcript["segments"]] == [s["content"] for s in [*CALL_PAGE_1, *CALL_PAGE_2]]
    assert f"plaud:{CALL}/summary" in records and f"plaud:{UNREADY}/transaction" in records
    assert not any(HELD in rid or KEPT in rid for rid in records)
