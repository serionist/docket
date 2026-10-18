"""The Teams mapper through Docket: the core's checks, the versions it computes, and a live run through the service and
the capture hook, with this folder copied into a temp project. Collected only where docket can be imported (see
conftest.py)."""

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
from starlette.testclient import TestClient
from teams_payloads import (
    ALL,
    BEN,
    EDITED,
    GROUP,
    GROUP_MESSAGES,
    PLAN,
    RIG_FREE,
    RIG_YES,
    SHOWN,
    Run,
    chat,
    guid,
    live_run,
    rid,
)

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
    rid(RIG_FREE): "sha256:ede54eadf271c972e52e65be28a3c27a411dba73ca1f9f811e59eb60c08e2671",
    rid(RIG_YES): "sha256:17b4b2da7af928203ec32ba8accbc461b868fe1249f16945d4e094733daa352b",
    rid(PLAN): "sha256:33a293cf249c1a7a4796726bbfa2652f949a17075ddf73299df133ab7523b20d",
    rid(SHOWN): "sha256:531343222d4fe5619cd2b4c53bff2a0469768212e6846512a8a36adfceefec1d",
}


def checked(mapper: ModuleType, run: Run, holds: list[str] | None = None) -> dict[str, RawRecord]:
    return {r.rid: r for r in check_output("teams", mapper.map_run(run.payload(holds))).records}


def test_the_core_accepts_every_record_and_pins_one_version_per_shape(mapper: ModuleType) -> None:
    records = checked(mapper, live_run(Run()))
    assert len(records) == len(ALL)
    assert {r: version(records[r]) for r in GOLDEN} == GOLDEN
    assert records[rid(RIG_FREE)].ts == datetime(2026, 10, 19, 7, 58, 12, 311000, tzinfo=UTC)


def test_a_derived_container_passes_the_core_s_checks(mapper: ModuleType) -> None:
    records = checked(mapper, Run().script(chat(GROUP, RIG_FREE, chat_id="")))
    (record,) = records.values()
    assert record.fidelity == "derived" and (record.container or "").startswith("teams:d-")


def test_flags_the_chat_s_name_or_a_quote_s_preview_never_version_a_message(mapper: ModuleType) -> None:
    first = checked(mapper, Run().script(chat(GROUP, *GROUP_MESSAGES)))
    again = [replace(m, flags=(*m.flags, "cards:1")) for m in GROUP_MESSAGES]
    again[1] = replace(again[1], quote=(RIG_FREE.mid, "Rita Sample", "Morning!"))
    second = checked(mapper, Run().script(chat(replace(GROUP, name="Rig crew"), *again, since="2026-10-19T00:00:00Z")))
    assert {r: version(x) for r, x in second.items()} == {r: version(x) for r, x in first.items()}


def test_an_edit_or_a_new_attachment_is_a_new_version(mapper: ModuleType) -> None:
    before = replace(EDITED, text="Moved to 10:00, room 4.12", edited="")
    base = checked(mapper, Run().script(chat(GROUP, before, PLAN)))
    more = replace(PLAN, attachments=(*PLAN.attachments, ("file", "aa11bb22-cc33-4d44-8e55-ff6677889900", "v3.xlsx")))
    changed = checked(mapper, Run().script(chat(GROUP, EDITED, more)))
    for m in (EDITED, PLAN):
        assert version(changed[rid(m)]) != version(base[rid(m)])


# -- a live run, end to end ------------------------------------------------------------------------------------------


def project_with_this_source(tmp_path: Path) -> Vault:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project, check=True)
    target = project / ".claude" / "skills" / "sources" / "teams"
    shutil.copytree(SOURCE_FOLDER, target, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    config = VaultConfig(
        owner=Owner(name="Ben Sample", emails=["ben@example.invalid"], time_zone="Europe/Budapest"),
        primary_machine="test-box",
        sources={
            "teams": SourceConfig(
                sensitivity="work",
                mapper=".claude/skills/sources/teams/mapper.py",
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
        opened = http.post("/api/runs", json={"source": "teams"}, headers=bearer).json()
        assert opened["tools"] == ["javascript_tool"]
        assert opened["skill"].endswith("SKILL.md")
        bound = capture.handle(
            {
                "session_id": "s1",
                "hook_event_name": "PostToolUse",
                "tool_name": "mcp__plugin_docket_docket__run_open",
                "tool_input": {"source": "teams"},
                "tool_response": json.dumps(opened),
                "tool_use_id": "toolu_open",
            }
        )
        assert bound is not None
        assert "is this session's" in bound["hookSpecificOutput"]["additionalContext"]
        # The chat list, each chat's open-and-page snippet and read, the unread restores: all are captured, and only
        # the chat reads map to records.
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
        # Teams is work: every message goes straight to ready, and the mark moves.
        assert (body["ready"], body["pending"], body["set_aside"], body["moved"]) == (len(ALL), 0, [], True)
        staged = {e.staged.record.rid: e.staged.record for e in Gate(vault).list(source="teams").entries}
        assert staged[rid(RIG_FREE)].container == f"teams:{GROUP.id}"
        assert staged[rid(RIG_YES)].author == f"Ben Sample <{guid(BEN)}>"
