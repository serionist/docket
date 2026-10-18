"""The Outlook mail mapper through Docket: the core's checks, the versions it computes, and a live run through the
service and the capture hook, with this folder copied into a temp project. Collected only where docket can be imported
(see conftest.py)."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from outlook_payloads import (
    ALL,
    ANSWER,
    ASK,
    FORWARD,
    JUNK,
    SETTINGS,
    SHOWN,
    THREAD,
    Run,
    live_run,
    read,
    rid,
)
from starlette.testclient import TestClient

from docket import capture
from docket.service.app import ServiceApp, create_app
from docket.service.state import Settings
from docket.sources.contract import check_output
from docket.vault.config import Owner, SourceConfig, VaultConfig
from docket.vault.gate import Gate
from docket.vault.records import RawRecord, version
from docket.vault.vault import Vault

SOURCE_FOLDER = Path(__file__).resolve().parents[1]

# One version per shape, pinned: a change to the mapper's output or the core's projection shows up here first. Change
# them only with a migration in mind, since every message the lookback reads again would get a new version.
GOLDEN = {
    rid(ASK): "sha256:c8ac4d7f58de6714767e7f34bc0593ecfb65d409d1b046ac50603a7b59c23136",
    rid(FORWARD): "sha256:d23a66e77cd2ee0cca9de88e15f90fb58a95c84c23d13d04da7d0b87c9db2bc2",
    rid(SETTINGS): "sha256:90962d439ecd2f0599be301a8ca6a9048e2215d97e33a1e12b415e422a66e87c",
    rid(JUNK): "sha256:23009a31e8f378a49b7142e152b9c0bc89fb6adb3fde8c2e75864e8c4cefca13",
}


def checked(mapper: ModuleType, run: Run, holds: list[str] | None = None) -> dict[str, RawRecord]:
    return {r.rid: r for r in check_output("outlook", mapper.map_run(run.payload(holds))).records}


def test_the_core_accepts_every_record_and_pins_one_version_per_shape(mapper: ModuleType) -> None:
    records = checked(mapper, live_run(Run()))
    assert len(records) == len(ALL)
    assert {r: version(records[r]) for r in GOLDEN} == GOLDEN
    assert records[rid(ASK)].ts == datetime(2026, 10, 19, 7, 58, 12, tzinfo=UTC)
    assert records[rid(ASK)].thread == records[rid(ANSWER)].thread


def test_a_derived_rid_passes_the_core_s_checks(mapper: ModuleType) -> None:
    records = checked(mapper, Run().script(read(SHOWN, mode="pane")))
    (record,) = records.values()
    assert record.fidelity == "derived" and record.rid.startswith("outlook:d-")


def test_read_state_folder_flags_and_copies_never_version_a_message(mapper: ModuleType) -> None:
    first = checked(mapper, Run().script(read(*THREAD)))
    moved = [replace(m, folder=("Archive", "archive"), flags=(*m.flags, "unread", "copies:2")) for m in THREAD]
    second = checked(mapper, Run().script(read(*moved, since="2026-10-19T00:00:00Z")))
    assert {r: version(x) for r, x in second.items()} == {r: version(x) for r, x in first.items()}


def test_an_edited_body_a_new_attachment_or_a_new_recipient_is_a_new_version(mapper: ModuleType) -> None:
    base = checked(mapper, Run().script(read(FORWARD)).script(read(*THREAD)))
    more = replace(FORWARD, attachments=(*FORWARD.attachments, ("file", "Quote v2.pdf", "application/pdf", 1200)))
    edited = replace(ASK, text=ASK.text + "\n\nPS: bring the logger")
    wider = replace(ANSWER, cc=(*ANSWER.cc, ("Gus Guest", "gus@example.com")))
    changed = checked(mapper, Run().script(read(more)).script(read(edited, wider)))
    for m in (FORWARD, ASK, ANSWER):
        assert version(changed[rid(m)]) != version(base[rid(m)])


# -- a live run, end to end ------------------------------------------------------------------------------------------


def project_with_this_source(tmp_path: Path) -> Vault:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project, check=True)
    target = project / ".claude" / "skills" / "sources" / "outlook"
    shutil.copytree(SOURCE_FOLDER, target, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    config = VaultConfig(
        owner=Owner(name="Ben Sample", emails=["ben.sample@example.com"], time_zone="Europe/Budapest"),
        primary_machine="test-box",
        sources={
            "outlook": SourceConfig(
                sensitivity="work",
                mapper=".claude/skills/sources/outlook/mapper.py",
                tools=["javascript_tool"],
                lookback_days=1,
            )
        },
    )
    return Vault.init(project / "vault", config, "test-box")


def test_a_live_run_goes_through_the_service_and_the_capture_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = project_with_this_source(tmp_path)
    now = datetime(2026, 10, 19, 18, 0, tzinfo=UTC)
    bearer = {"Authorization": "Bearer t0ken-for-tests-only"}
    app = create_app(Settings(port=8750, vault=vault.root, token=lambda: "t0ken-for-tests-only"))
    assert isinstance(app, ServiceApp)
    app.service.runs.clock = lambda: now
    with TestClient(app, base_url="http://127.0.0.1:8750") as http:

        def through(method: str, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            answer = http.request(method, path, json=body, headers=bearer)
            return answer.status_code, answer.json()

        monkeypatch.setattr(capture, "_call", through)
        opened = http.post("/api/runs", json={"source": "outlook"}, headers=bearer).json()
        assert opened["tools"] == ["javascript_tool"]
        assert opened["skill"].endswith("SKILL.md")
        bound = capture.handle(
            {
                "session_id": "s1",
                "hook_event_name": "PostToolUse",
                "tool_name": "mcp__plugin_docket_docket__run_open",
                "tool_input": {"source": "outlook"},
                "tool_response": json.dumps(opened),
                "tool_use_id": "toolu_open",
            }
        )
        assert bound is not None
        assert "is this session's" in bound["hookSpecificOutput"]["additionalContext"]
        # The search, the rows, each open snippet and read, Junk Email's rows: all are captured, and only the reads
        # map to records.
        captures = live_run(Run()).captures
        for n, c in enumerate(captures, start=1):
            said = capture.handle(
                {
                    "session_id": "s1",
                    "hook_event_name": "PostToolUse",
                    "tool_name": f"mcp__claude-in-chrome__{c['tool']}",
                    "tool_input": c["arguments"],
                    "tool_response": c["result"],
                    "tool_use_id": f"toolu_{n}",
                }
            )
            assert said is not None
            assert f"docket: kept capture {n} " in said["hookSpecificOutput"]["additionalContext"]
        closed = http.post(f"/api/runs/{opened['run']}/close", json={"captures": len(captures)}, headers=bearer)
        assert closed.status_code == 200, closed.text
        body = closed.json()
        # Mail is work: every message goes straight to ready, and the mark moves.
        assert (body["ready"], body["pending"], body["set_aside"], body["moved"]) == (len(ALL), 0, [], True)
        staged = {e.staged.record.rid: e.staged.record for e in Gate(vault).list(source="outlook").entries}
        assert staged[rid(ASK)].author == "Rita Sample <rita.sample@example.com>"
        assert staged[rid(FORWARD)].attachments[0].name == "Quote 2026-114.pdf"
