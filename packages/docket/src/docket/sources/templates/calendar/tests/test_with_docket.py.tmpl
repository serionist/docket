"""The calendar mapper through Docket: the core's checks, the versions it computes, and a live run through the service
and the capture hook, with this folder copied into a temp project. Collected only where docket can be imported (see
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
from calendar_payloads import (
    BLOCKED_IN_V1,
    EQUALS,
    EVENTS,
    NEXT_WEEK,
    OFFSITE,
    PLANNING,
    RD_SYNC,
    ROOM_OCC,
    STANDUP,
    STANDUP_MON,
    SYNC_FRI,
    SYNC_FRI_CANCELLED,
    Event,
    Run,
    live_week,
    rest,
    single_id,
    week,
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


def rid(e: Event) -> str:
    return f"calendar:{rest(e.id)}"


# One version per shape, pinned: a change to the mapper's output or the core's projection shows up here first. Change
# them only with a migration in mind, since every event the lookback reads again would get a new version.
GOLDEN = {
    rid(STANDUP_MON): "sha256:509b33b6e5122d222413b5a5e635999fc27712b24ab2ce7a0d4ad91f8142ce79",
    rid(PLANNING): "sha256:e2d953a457b595682eaf0b85b4257b38b3fb0063bdffb16445d69c23d0244e2a",
    rid(OFFSITE): "sha256:bcccf1b219fedcd68321c94fac29c041bf592853333dfb18d4ab86c5be1e32ee",
}
# The events Chrome blocked under /1 (a booked room beside Teams, an `&`, an `=`), as /1 would have mapped them had they
# come through, and as /2 does.
GOLDEN_BLOCKED_IN_V1 = {
    rid(ROOM_OCC): "sha256:722643d0cfd304fe4421b761e5a35d4186c81b947a330cc9dc585945d6fa7a0c",
    rid(RD_SYNC): "sha256:b2d29eb9141469a4889a8f5ee2153ce59b18875364daccbdaf26a5075461afbc",
    rid(EQUALS): "sha256:58c853fe0dcff586fa6fc31338e4bd1036b3419e08b4101a7b093cc6976eb8b9",
}


def checked(mapper: ModuleType, run: Run, holds: list[str] | None = None) -> dict[str, RawRecord]:
    return {r.rid: r for r in check_output("calendar", mapper.map_run(run.payload(holds))).records}


def test_the_core_accepts_every_record_and_pins_one_version_per_shape(mapper: ModuleType) -> None:
    records = checked(mapper, live_week(Run()))
    assert len(records) == len(EVENTS)
    assert {r: version(records[r]) for r in GOLDEN} == GOLDEN
    assert records[rid(STANDUP_MON)].ts == datetime(2026, 10, 19, 7, 0, tzinfo=UTC)


def test_v1_and_v2_give_every_event_the_same_pinned_version(mapper: ModuleType) -> None:
    for version_ in (1, 2):
        records = checked(mapper, Run().script(week(*EVENTS, *BLOCKED_IN_V1, version=version_)))
        assert {r: version(records[r]) for r in GOLDEN} == GOLDEN
        assert {r: version(records[r]) for r in GOLDEN_BLOCKED_IN_V1} == GOLDEN_BLOCKED_IN_V1


def test_a_derived_rid_passes_the_core_s_checks(mapper: ModuleType) -> None:
    records = checked(mapper, Run().script(week(replace(SYNC_FRI, id=""))))
    (record,) = records.values()
    assert record.fidelity == "derived" and record.rid.startswith("calendar:d-")


def test_show_as_a_past_day_s_label_or_the_week_read_in_never_version_an_event(mapper: ModuleType) -> None:
    first = checked(mapper, Run().script(week(*EVENTS)))
    again = Run().script(week(*(replace(e, show_as="Busy", past=True) for e in EVENTS), range_=NEXT_WEEK))
    second = checked(mapper, again)
    assert {r: version(x) for r, x in second.items()} == {r: version(x) for r, x in first.items()}


def test_a_cancellation_or_a_new_response_is_a_new_version(mapper: ModuleType) -> None:
    base = checked(mapper, Run().script(week(SYNC_FRI, STANDUP_MON)))
    changed = checked(mapper, Run().script(week(SYNC_FRI_CANCELLED, replace(STANDUP_MON, response="Accept"))))
    for e in (SYNC_FRI, STANDUP_MON):
        assert version(changed[rid(e)]) != version(base[rid(e)])


# -- a live run, end to end ------------------------------------------------------------------------------------------


def project_with_this_source(tmp_path: Path) -> Vault:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project, check=True)
    target = project / ".claude" / "skills" / "sources" / "calendar"
    shutil.copytree(SOURCE_FOLDER, target, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    config = VaultConfig(
        owner=Owner(name="Ben Sample", emails=["ben@example.invalid"], time_zone="Europe/Budapest"),
        primary_machine="test-box",
        sources={
            "calendar": SourceConfig(
                sensitivity="work",
                mapper=".claude/skills/sources/calendar/mapper.py",
                tools=["javascript_tool"],
                lookback_days=7,
            )
        },
    )
    return Vault.init(project / "vault", config, "test-box")


def test_a_live_run_goes_through_the_service_and_the_capture_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = project_with_this_source(tmp_path)
    now = datetime(2026, 10, 18, 18, 0, tzinfo=UTC)
    bearer = {"Authorization": "Bearer t0ken-for-tests-only"}
    app = create_app(Settings(port=8750, vault=vault.root, token=lambda: "t0ken-for-tests-only"))
    assert isinstance(app, ServiceApp)
    app.service.runs.clock = lambda: now
    with TestClient(app, base_url="http://127.0.0.1:8750") as http:

        def through(method: str, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            answer = http.request(method, path, json=body, headers=bearer)
            return answer.status_code, answer.json()

        monkeypatch.setattr(capture, "_call", through)
        opened = http.post("/api/runs", json={"source": "calendar"}, headers=bearer).json()
        assert opened["tools"] == ["javascript_tool"]
        assert opened["skill"].endswith("SKILL.md")
        bound = capture.handle(
            {
                "session_id": "s1",
                "hook_event_name": "PostToolUse",
                "tool_name": "mcp__plugin_docket_docket__run_open",
                "tool_input": {"source": "calendar"},
                "tool_response": json.dumps(opened),
                "tool_use_id": "toolu_open",
            }
        )
        assert bound is not None
        assert "is this session's" in bound["hookSpecificOutput"]["additionalContext"]
        # The week, the click to the next one, the next week: all three are captured, and the click maps to nothing.
        captures = live_week(Run()).captures
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
        # The calendar is work: every event goes straight to ready, and the mark moves.
        assert (body["ready"], body["pending"], body["set_aside"], body["moved"]) == (len(EVENTS), 0, [], True)
        staged = {e.staged.record.rid: e.staged.record for e in Gate(vault).list(source="calendar").entries}
        assert staged[rid(STANDUP_MON)].thread == f"calendar:{rest(single_id(STANDUP))}"
        assert staged[rid(SYNC_FRI)].author == "Ben Sample <ben@example.invalid>"
