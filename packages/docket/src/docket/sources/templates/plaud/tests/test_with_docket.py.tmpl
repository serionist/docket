"""The Plaud mapper through Docket: the core's checks, the versions it computes, and `docket ingest` end to end, with
this folder copied into a temp project the way a user copies it."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from plaud_payloads import (
    BACKFILL_SUMMARY,
    CALL,
    CALL_PAGE_1,
    CALL_PAGE_2,
    HELD,
    PRIVATE_TITLE,
    SHORT,
    UNREADY,
    Run,
    live_call,
    meta,
    segment,
    structured,
)
from starlette.testclient import TestClient

from docket import capture
from docket.cli import main
from docket.service.app import ServiceApp, create_app
from docket.service.state import Settings
from docket.sources import release as releasing
from docket.sources.contract import check_output
from docket.vault.config import Owner, SourceConfig, VaultConfig
from docket.vault.gate import Gate
from docket.vault.records import RawRecord, version
from docket.vault.vault import Vault

EXAMPLE = Path(__file__).resolve().parents[1]

# One version per kind, pinned: a change to the mapper's output or the core's projection shows up here first. Change
# them only with a migration in mind, since every record the lookback reads again would get a new version.
GOLDEN = {
    f"plaud:{CALL}": "sha256:8ba206bf9dddaf0d361aba1fbd69d1511a1dc74bcb6563ff3284636db5f41c14",
    f"plaud:{CALL}/transaction_polish": "sha256:91f5005d45ac5fac0f0ebcddb4ca977e15fd6f33082f5be41768e3e2f9f76183",
    f"plaud:{CALL}/summary": "sha256:96d23dccf11179e623ad46d6acfdbe370238db1366abf88e1634da414cc16901",
}


def checked(mapper: ModuleType, run: Run, holds: list[str] | None = None) -> dict[str, RawRecord]:
    return {r.rid: r for r in check_output("plaud", mapper.map_run(run.payload(holds))).records}


def test_the_core_accepts_every_record_and_pins_one_version_per_kind(mapper: ModuleType) -> None:
    records = checked(mapper, live_call(Run()))
    assert {rid: version(r) for rid, r in records.items()} == GOLDEN
    transcript = records[f"plaud:{CALL}/transaction_polish"]
    assert transcript.text.splitlines()[0] == "Ben Sample: Morning, Cleo."


def test_page_order_a_serial_number_or_a_link_never_versions_a_recording(mapper: ModuleType) -> None:
    first = checked(mapper, live_call(Run()))
    detail = meta(CALL, "Supplier call", serial_number="SN-0001", presigned_url="https://example.invalid/a?t=1")
    again = (
        Run()
        .transcript(CALL, CALL_PAGE_2, cursor="c2")
        .note(CALL)
        .transcript(CALL, [*reversed(CALL_PAGE_1), CALL_PAGE_1[0]], next_cursor="c2")
        .add("get_file", structured(detail), file_id=CALL)
    )
    second = checked(mapper, again)
    assert {rid: version(r) for rid, r in second.items()} == {rid: version(r) for rid, r in first.items()}


def test_a_backfill_file_and_a_capture_of_one_recording_version_alike(mapper: ModuleType) -> None:
    captured = checked(mapper, live_call(Run()))
    backfilled = checked(mapper, Run().listing(meta(CALL, "Supplier call")).backfill(CALL))
    assert {rid: version(r) for rid, r in backfilled.items()} == {rid: version(r) for rid, r in captured.items()}


def test_a_new_name_a_renamed_speaker_or_an_edited_tab_is_a_new_version(mapper: ModuleType) -> None:
    base = checked(mapper, live_call(Run()))
    renamed = [{**s, "speaker": "Ben S."} if s["speaker"] == "Ben Sample" else s for s in CALL_PAGE_1]
    changed = checked(
        mapper,
        Run()
        .transcript(CALL, renamed, next_cursor="c2")
        .transcript(CALL, CALL_PAGE_2, cursor="c2")
        .note(CALL, [{"data_type": "auto_sum_note", "data_title": "Summary", "data_content": "Price NOT agreed."}])
        .listing(meta(CALL, "Supplier call, take two")),
    )
    for rid in base:
        assert version(changed[rid]) != version(base[rid])


# -- docket ingest, end to end ---------------------------------------------------------------------------------------


def project_with_the_example(tmp_path: Path) -> Vault:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project, check=True)
    shutil.copytree(EXAMPLE, project / ".claude" / "skills" / "sources" / "plaud")
    config = VaultConfig(
        owner=Owner(name="Ada Example", emails=["ada@example.invalid"], time_zone="Europe/Budapest"),
        primary_machine="test-box",
        sources={
            "plaud": SourceConfig(
                sensitivity="mixed",
                mapper=".claude/skills/sources/plaud/mapper.py",
                tools=["list_files", "get_file", "get_transcript", "get_note"],
            )
        },
    )
    return Vault.init(project / "vault", config, "test-box")


def write_folder(folder: Path, files: dict[str, Any]) -> None:
    folder.mkdir()
    for name, content in files.items():
        data = content.encode("utf-8") if isinstance(content, str) else json.dumps(content).encode("utf-8")
        (folder / name).write_bytes(data)


def test_docket_ingest_imports_a_backfill_through_the_example(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = project_with_the_example(tmp_path)
    listing = {
        "tool": "list_files",
        "arguments": {"page": 1},
        "result": structured(
            {
                "files": [
                    meta(CALL, "Supplier call"),
                    meta(SHORT, "Quick sync"),
                    meta(HELD, PRIVATE_TITLE),
                ]
            }
        ),
        "captured": "2026-10-14T10:00:00Z",
    }

    def backfill(file_id: str, block: str | None = "transaction_polish") -> dict[str, Any]:
        segments = [*CALL_PAGE_1, *CALL_PAGE_2] if block else []
        return {"file_id": file_id, "block": block, "segments": segments, "summary": BACKFILL_SUMMARY}

    folder = tmp_path / "backfill"
    write_folder(
        folder,
        {
            "0-listing.json": listing,
            f"{CALL}.json": backfill(CALL),
            f"{SHORT}.json": backfill(SHORT, block=None),
            f"{UNREADY}.json": backfill(UNREADY),
            f"{HELD}.json": backfill(HELD),
            "hold.txt": f"{HELD}\n",
        },
    )
    args = ["ingest", "plaud", str(folder), "--vault", str(vault.root)]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "holds      plaud: 1 added" in out
    assert "staged     plaud: 5 pending, 0 ready, 0 already staged or in raw" in out
    assert "on hold    1" in out
    assert f"set aside  {UNREADY}  no-meta  start_at" in out
    assert PRIVATE_TITLE not in out
    assert HELD not in out
    entries = Gate(vault).list(source="plaud").entries
    assert sorted(e.staged.record.rid for e in entries) == sorted(
        [
            f"plaud:{CALL}",
            f"plaud:{CALL}/transaction_polish",
            f"plaud:{CALL}/summary",
            f"plaud:{SHORT}",
            f"plaud:{SHORT}/summary",
        ]
    )
    assert {e.staged.state for e in entries} == {"pending"}
    assert main(args) == 0
    assert "staged     plaud: 0 pending, 0 ready, 5 already staged or in raw" in capsys.readouterr().out


def test_a_held_recording_goes_in_only_with_the_cuts_its_owner_confirms(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = project_with_the_example(tmp_path)
    stretch = [
        segment(0, 10_000, "Ben Sample", "Morning, Ada."),
        segment(10_000, 20_000, "Ada Example", "First, my weekend plans."),
        segment(18_000, 30_000, "Ben Sample", "The cabin by the lake?"),
        segment(30_000, 40_000, "Ada Example", "Yes, from Saturday."),
        segment(40_000, 50_000, "Ben Sample", "Now the offer: ten million."),
    ]
    listing = {
        "tool": "list_files",
        "arguments": {"page": 1},
        "result": structured({"files": [meta(i, n) for i, n in [(CALL, "Supplier call"), (SHORT, "Quick sync"),
                                                                 ("rec-cut-01", "Supplier sync"),
                                                                 ("rec-cut-03", PRIVATE_TITLE)]]}),
        "captured": "2026-10-14T10:00:00Z",
    }  # fmt: skip

    def backfill(file_id: str, segments: list[Any], block: str | None = "transaction_polish") -> dict[str, Any]:
        return {"file_id": file_id, "block": block, "segments": segments, "summary": BACKFILL_SUMMARY}

    folder = tmp_path / "backfill"
    write_folder(
        folder,
        {
            "0-listing.json": listing,
            f"{CALL}.json": backfill(CALL, [*CALL_PAGE_1, *CALL_PAGE_2]),
            f"{SHORT}.json": backfill(SHORT, [], block=None),
            "rec-cut-01.json": backfill("rec-cut-01", stretch),
            "rec-cut-03.json": backfill("rec-cut-03", [], block=None),
            "hold.txt": "rec-cut-01\nrec-cut-03\n",
        },
    )
    root = str(vault.root)
    assert main(["ingest", "plaud", str(folder), "--vault", root]) == 0
    assert "on hold    2" in capsys.readouterr().out
    assert not any("rec-cut" in e.staged.record.rid for e in Gate(vault).list().entries)

    # The owner at the console, typing back the code each question shows.
    monkeypatch.setattr(releasing, "_console_problem", lambda: None)
    monkeypatch.setattr(releasing, "_ask", lambda prompt: re.findall(r"Type ([A-Z]{4}) to", prompt)[0])
    release = ["hold", "release", "plaud", "--from", str(folder), "--vault", root]
    assert main([*release, "rec-cut-01", "--cut", "0:12-0:35", "--keep", "plaud:rec-cut-01"]) == 0
    assert main([*release, "rec-cut-03", "--cut", "0:01-0:02"]) == 0
    out = capsys.readouterr().out
    assert "released   rec-cut-01" in out
    assert "held       rec-cut-03  no transcript to cut by time" in out

    gate = Gate(vault)
    gate.promote()
    stored = {r.rid: r for _, _, r in vault.rawlog.iter_records()}
    transcript = stored["plaud:rec-cut-01/transaction_polish"]
    inside = [s.text for s in transcript.segments or [] if s.start_ms < 40_000 and s.end_ms > 10_000]
    assert inside == ["[cut]"]
    assert not any(word in transcript.model_dump_json() for word in ("weekend", "cabin", "Saturday"))
    assert stored["plaud:rec-cut-01"].title == "Supplier sync"
    assert "plaud:rec-cut-01/summary" not in stored
    assert main(["hold", "list", "plaud", "--vault", root]) == 0
    assert capsys.readouterr().out == "plaud  rec-cut-03\n"


def hooked(result: dict[str, Any]) -> Any:
    """A result as Claude Code hands it to the capture hook (seen on 2.1.300): structured content as its JSON text,
    else the content blocks."""
    if "structuredContent" in result:
        return json.dumps(result["structuredContent"])
    return result["content"]


def test_a_live_run_goes_through_the_service_and_the_capture_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = project_with_the_example(tmp_path)
    now = datetime(2026, 10, 15, 9, 12, tzinfo=UTC)
    bearer = {"Authorization": "Bearer t0ken-for-tests-only"}
    app = create_app(Settings(port=8750, vault=vault.root, token=lambda: "t0ken-for-tests-only"))
    assert isinstance(app, ServiceApp)
    app.service.runs.clock = lambda: now
    with TestClient(app, base_url="http://127.0.0.1:8750") as http:

        def through(method: str, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            answer = http.request(method, path, json=body, headers=bearer)
            return answer.status_code, answer.json()

        monkeypatch.setattr(capture, "_call", through)
        calls = 0

        def run_once(run: Run) -> dict[str, Any]:
            nonlocal calls
            opened = http.post("/api/runs", json={"source": "plaud"}, headers=bearer).json()
            said = capture.handle(
                {
                    "session_id": "s1",
                    "hook_event_name": "PostToolUse",
                    "tool_name": "mcp__plugin_docket_docket__run_open",
                    "tool_input": {"source": "plaud"},
                    "tool_response": json.dumps(opened),
                    "tool_use_id": "toolu_open",
                }
            )
            assert said is not None
            kept = 0
            for c in run.captures:
                calls += 1
                said = capture.handle(
                    {
                        "session_id": "s1",
                        "hook_event_name": "PostToolUse",
                        "tool_name": f"mcp__plaud__{c['tool']}",
                        "tool_input": c["arguments"],
                        "tool_response": hooked(c["result"]),
                        "tool_use_id": f"toolu_{calls}",
                    }
                )
                assert said is not None
                assert "docket: kept capture" in said["hookSpecificOutput"]["additionalContext"]
                kept += 1
            closed = http.post(f"/api/runs/{opened['run']}/close", json={"captures": kept}, headers=bearer)
            assert closed.status_code == 200, closed.text
            return {"opened": opened, "closed": closed.json()}

        first = run_once(live_call(Run()))
        assert (first["closed"]["pending"], first["closed"]["moved"]) == (3, True)
        # Later runs list the call again and skip it: the mapper reports it waiting to be fetched, and the core drops
        # that, since the run was told to skip it.
        second = run_once(Run().listing(meta(CALL, "Supplier call")))
        assert CALL in second["opened"]["kept"]
        third = run_once(Run().listing(meta(CALL, "Supplier call")))
        assert (third["opened"]["waiting"], third["opened"]["retry"]) == ([], [])
        # A run stopped without closing drops after its idle time, and the mark stays where it was.
        mark = third["closed"]["mark_after"]
        stopped = http.post("/api/runs", json={"source": "plaud"}, headers=bearer).json()
        app.service.runs.clock = lambda: now + timedelta(minutes=31)
        dropped = http.post(f"/api/runs/{stopped['run']}/close", json={"captures": 0}, headers=bearer).json()
        assert "the capture hook never tied it to a session" in dropped["error"]
        assert http.post("/api/runs", json={"source": "plaud"}, headers=bearer).json()["since"] < mark
