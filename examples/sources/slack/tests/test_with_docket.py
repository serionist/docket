"""The Slack mapper through Docket: the core's checks, the versions it computes, the never-store list, and a live run
through the service and the capture hook, with this folder copied into a temp project. Collected only where docket can
be imported (see conftest.py)."""

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
from slack_payloads import (
    BEN,
    CHANNEL,
    DM,
    FILE_POST,
    PARENT,
    POST,
    REPLY_1,
    REPLY_2,
    RITA,
    Msg,
    Run,
    live_channel,
)
from starlette.testclient import TestClient

from docket import capture
from docket.service.app import ServiceApp, create_app
from docket.service.state import Settings
from docket.sources.contract import check_output
from docket.vault.config import NeverStore, Owner, SourceConfig, VaultConfig
from docket.vault.gate import Gate
from docket.vault.neverstore import match
from docket.vault.records import RawRecord, version
from docket.vault.vault import Vault

SOURCE_FOLDER = Path(__file__).resolve().parents[1]


def rid(m: Msg, channel: str = CHANNEL) -> str:
    return f"slack:{channel}/{m.ts}"


# One version per shape, pinned: a change to the mapper's output or the core's projection shows up here first. Change
# them only with a migration in mind, since every message the lookback reads again would get a new version.
GOLDEN = {
    rid(POST): "sha256:51684b374f0140b466dd463fd3f6e6ec6ce24cf9a7ff0c99e13c1f9b88466a0d",
    rid(REPLY_1): "sha256:95215b551e1d0e20108fec1441af6520e6139039c6282f61c848142efd6d8424",
    rid(FILE_POST): "sha256:ffba342d58f2aaacc3055f9b923b31844032e628129eacd733046c0075119f65",
}


def checked(mapper: ModuleType, run: Run, holds: list[str] | None = None) -> dict[str, RawRecord]:
    return {r.rid: r for r in check_output("slack", mapper.map_run(run.payload(holds))).records}


def test_the_core_accepts_every_record_and_pins_one_version_per_shape(mapper: ModuleType) -> None:
    records = checked(mapper, live_channel(Run()))
    assert len(records) == 5
    assert {r: version(records[r]) for r in GOLDEN} == GOLDEN
    assert records[rid(REPLY_1)].thread == rid(PARENT)


def test_a_reaction_a_reply_count_the_reading_tool_or_the_order_never_versions_a_message(mapper: ModuleType) -> None:
    first = checked(mapper, live_channel(Run()))
    again = (
        Run()
        .thread(CHANNEL, replace(PARENT, lines=("Reactions: eyes (1)",)),
                replace(REPLY_1, lines=("Reactions: heart (1)",)), REPLY_2)
        .channel(CHANNEL, replace(PARENT, replies=5), replace(POST, lines=()),
                 replace(FILE_POST, lines=("Reactions: +1 (1)", *FILE_POST.lines)))
    )  # fmt: skip
    second = checked(mapper, again)
    assert {r: version(x) for r, x in second.items()} == {r: version(x) for r, x in first.items()}


def test_an_edit_or_a_new_file_is_a_new_version(mapper: ModuleType) -> None:
    base = checked(mapper, live_channel(Run()))
    changed = checked(
        mapper,
        Run().channel(
            CHANNEL,
            replace(FILE_POST, lines=("Files: test-plan-v2.pdf (ID: F0TESTFILE5, application/pdf, 1.3 MB)",)),
            replace(PARENT, text="Draft test plan is up (v2).\nComments welcome by Thursday."),
        ),
    )
    for m in (FILE_POST, PARENT):
        assert version(changed[rid(m)]) != version(base[rid(m)])


def test_a_dm_on_the_never_store_list_is_matched_by_its_d_id_with_or_without_the_prefix(mapper: ModuleType) -> None:
    run = Run().thread(DM, Msg(RITA, POST.ts, "Can we move our 1:1?"), Msg(BEN, REPLY_1.ts, "Sure, Friday."))
    records = checked(mapper, run)
    assert len(records) == 2
    for listed in (DM, f"slack:{DM}"):
        assert {match(r, NeverStore(containers=[listed])) for r in records.values()} == {"containers"}
    assert {match(r, NeverStore(containers=[CHANNEL])) for r in records.values()} == {None}


# -- a live run, end to end ------------------------------------------------------------------------------------------


def project_with_this_source(tmp_path: Path) -> Vault:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project, check=True)
    target = project / ".claude" / "skills" / "sources" / "slack"
    shutil.copytree(SOURCE_FOLDER, target, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    config = VaultConfig(
        owner=Owner(name="Ada Example", emails=["ada@example.invalid"], time_zone="Europe/Budapest"),
        primary_machine="test-box",
        sources={
            "slack": SourceConfig(
                sensitivity="work",
                mapper=".claude/skills/sources/slack/mapper.py",
                tools=["slack_read_channel", "slack_read_thread"],
            )
        },
    )
    return Vault.init(project / "vault", config, "test-box")


def test_a_live_run_goes_through_the_service_and_the_capture_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = project_with_this_source(tmp_path)
    now = datetime(2026, 10, 15, 18, 0, tzinfo=UTC)
    bearer = {"Authorization": "Bearer t0ken-for-tests-only"}
    app = create_app(Settings(port=8750, vault=vault.root, token=lambda: "t0ken-for-tests-only"))
    assert isinstance(app, ServiceApp)
    app.service.runs.clock = lambda: now
    with TestClient(app, base_url="http://127.0.0.1:8750") as http:

        def through(method: str, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            answer = http.request(method, path, json=body, headers=bearer)
            return answer.status_code, answer.json()

        monkeypatch.setattr(capture, "_call", through)
        opened = http.post("/api/runs", json={"source": "slack"}, headers=bearer).json()
        assert opened["tools"] == ["slack_read_channel", "slack_read_thread"]
        assert opened["skill"].endswith("SKILL.md")
        bound = capture.handle(
            {
                "session_id": "s1",
                "hook_event_name": "PostToolUse",
                "tool_name": "mcp__plugin_docket_docket__run_open",
                "tool_input": {"source": "slack"},
                "tool_response": json.dumps(opened),
                "tool_use_id": "toolu_open",
            }
        )
        assert bound is not None
        assert "is this session's" in bound["hookSpecificOutput"]["additionalContext"]
        captures = live_channel(Run()).captures
        for n, c in enumerate(captures, start=1):
            said = capture.handle(
                {
                    "session_id": "s1",
                    "hook_event_name": "PostToolUse",
                    "tool_name": f"mcp__plugin_slack_slack__{c['tool']}",
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
        # Slack is work: every message goes straight to ready, the replies with them, and the mark moves.
        assert (body["ready"], body["pending"], body["set_aside"], body["moved"]) == (5, 0, [], True)
        staged = {e.staged.record.rid: e.staged.record.thread for e in Gate(vault).list(source="slack").entries}
        assert staged[rid(REPLY_2)] == rid(PARENT)
        again = http.post("/api/runs", json={"source": "slack"}, headers=bearer).json()
        assert f"{CHANNEL}/{POST.ts}" in again["kept"]
        assert f"{CHANNEL}/{REPLY_2.ts}" in again["kept"]
