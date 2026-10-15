"""Open runs in the service's memory (DR-0240, DR-0243), on an injected clock: bind, captures, close, drop, expiry,
the caps, the outcomes kept, and the marker the capture hook reads."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from docket.service import runs as runs_module
from docket.service.runs import BusyError, Run, RunError, Runs
from docket.vault.cursors import Handover

NOW = datetime(2026, 10, 15, 9, 12, tzinfo=UTC)
TITLE = "Dentist with Cleo"


class Clock:
    def __init__(self) -> None:
        self.at = NOW

    def __call__(self) -> datetime:
        return self.at

    def wait(self, **kw: float) -> None:
        self.at += timedelta(**kw)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def state(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture
def runs(state: Path, clock: Clock) -> Runs:
    return Runs(state, clock)


def a_run(run_id: str = "run-1", source: str = "memo", **fields: Any) -> Run:
    base: dict[str, Any] = {
        "id": run_id,
        "source": source,
        "tools": frozenset({"get_memo", "list_memos"}),
        "opened": NOW,
        "final": False,
        "window": None,
        "kept": frozenset[str](),
        "handed": Handover(),
        "held": frozenset[str](),
    }
    base.update(fields)
    return Run(**base)


def keep(runs: Runs, run_id: str = "run-1", **fields: Any) -> runs_module.Kept:
    base: dict[str, Any] = {
        "session": "s1",
        "agent": None,
        "tool": "get_memo",
        "tool_use_id": "toolu_1",
        "arguments": {"id": "m1"},
        "result": {"id": "m1", "title": TITLE},
        "captured": NOW,
        "size": 100,
    }
    base.update(fields)
    return runs.capture(run_id, **base)


def marker(state: Path) -> dict[str, Any]:
    return json.loads((state / "runs.json").read_text(encoding="utf-8"))


def test_a_run_opens_binds_and_keeps_captures_numbered_as_they_come(runs: Runs, state: Path, clock: Clock) -> None:
    runs.open(a_run())
    assert marker(state) == {
        "runs": [
            {"run": "run-1", "source": "memo", "session": None, "agent": None, "tools": ["get_memo", "list_memos"]}
        ]
    }
    runs.bind("run-1", "s1", None)
    assert marker(state)["runs"][0]["session"] == "s1"
    clock.wait(seconds=5)
    first = keep(runs)
    second = keep(runs, tool_use_id="toolu_2", tool="list_memos", size=300)
    assert (first.number, second.number, second.used, second.budget) == (1, 2, 400, runs_module.BUDGET)
    # A retried hook posts the same call again: kept once, the same number.
    assert keep(runs, tool_use_id="toolu_1").number == 1
    run = runs.begin_close("run-1")
    assert [c.seq for c in run.captures] == [0, 1]
    assert run.captures[0].result == {"id": "m1", "title": TITLE}
    runs.finish("run-1", "the answer")
    assert not (state / "runs.json").exists()
    outcome = runs.outcome("run-1")
    assert outcome is not None
    assert outcome.closed == "the answer"
    # Nothing of a capture went to disk.
    assert not any(TITLE in p.read_text(encoding="utf-8") for p in state.parent.rglob("*") if p.is_file())


def test_a_bind_is_once_within_a_minute_and_only_for_a_run_the_service_opened(runs: Runs, clock: Clock) -> None:
    with pytest.raises(RunError, match="no open run nope"):
        runs.bind("nope", "s1", None)
    runs.open(a_run())
    runs.bind("run-1", "s1", None)
    with pytest.raises(RunError, match="already tied"):
        runs.bind("run-1", "s2", None)
    runs.open(a_run("run-2", "chat"))
    clock.wait(seconds=61)
    with pytest.raises(RunError, match="never tied it to a session"):
        runs.bind("run-2", "s3", None)


def test_a_run_never_bound_is_dropped_after_a_minute(runs: Runs, clock: Clock) -> None:
    runs.open(a_run())
    clock.wait(seconds=61)
    with pytest.raises(RunError, match="never tied it to a session"):
        runs.begin_close("run-1")


def test_captures_come_only_from_the_bound_session_and_agent(runs: Runs) -> None:
    runs.open(a_run())
    runs.bind("run-1", "s1", "agent-1")
    with pytest.raises(RunError, match="another session"):
        keep(runs, session="s2", agent="agent-1")
    with pytest.raises(RunError, match="another session"):
        keep(runs, agent=None)
    keep(runs, agent="agent-1")
    assert runs.begin_close("run-1").short is None


@pytest.mark.parametrize(
    ("fields", "why"),
    [
        ({"tool": "delete_memo"}, "isn't one of memo's tools"),
        ({"size": runs_module.CAPTURE_CAP + 1}, "a capture is at most 32 MiB"),
        ({"size": 1, "tool_use_id": "over"}, "hold 256 MiB already"),
    ],
)
def test_a_capture_refused_from_the_bound_session_marks_the_run_short(
    runs: Runs, fields: dict[str, Any], why: str
) -> None:
    runs.open(a_run())
    runs.bind("run-1", "s1", None)
    if fields.get("tool_use_id") == "over":
        for n in range(runs_module.TOTAL_CAP // runs_module.CAPTURE_CAP):
            keep(runs, tool_use_id=f"full-{n}", size=runs_module.CAPTURE_CAP)
    with pytest.raises(RunError, match=why):
        keep(runs, **fields)
    # A second refusal keeps the first reason.
    with pytest.raises(RunError, match=why):
        keep(runs, **{**fields, "tool_use_id": "again"})
    assert why.rsplit(" ", maxsplit=1)[-1] in str(runs.begin_close("run-1").short)


def test_mark_short_keeps_the_first_reason_and_ignores_a_run_it_doesnt_hold(runs: Runs) -> None:
    runs.mark_short("nope", "a body past the cap")
    runs.open(a_run())
    runs.mark_short("run-1", "first")
    runs.mark_short("run-1", "second")
    runs.bind("run-1", "s1", None)
    assert runs.begin_close("run-1").short == "first"


def test_a_newer_run_of_a_source_or_of_a_session_replaces_the_open_one(runs: Runs) -> None:
    runs.open(a_run())
    runs.open(a_run("run-2"))
    with pytest.raises(RunError, match="dropped: replaced by a newer run, run-2"):
        runs.begin_close("run-1")
    runs.bind("run-2", "s1", None)
    runs.open(a_run("run-3", "chat"))
    runs.bind("run-3", "s1", None)
    with pytest.raises(RunError, match="replaced by a newer run of its session, run-3"):
        keep(runs, "run-2")
    # Opened bound, as the REST route does for a caller that knows its session.
    runs.open(a_run("run-4", "notes", session="s1"))
    with pytest.raises(RunError, match="replaced by a newer run, run-4"):
        runs.begin_close("run-3")


def test_a_closing_run_is_never_replaced_or_expired(runs: Runs, clock: Clock) -> None:
    runs.open(a_run())
    runs.bind("run-1", "s1", None)
    runs.begin_close("run-1")
    with pytest.raises(RunError, match="is closing; open the new one"):
        runs.open(a_run("run-2"))
    runs.open(a_run("run-3", "chat"))
    with pytest.raises(RunError, match="still closing"):
        runs.bind("run-3", "s1", None)
    with pytest.raises(RunError, match="closing already"):
        runs.begin_close("run-1")
    with pytest.raises(RunError, match="is closing"):
        keep(runs)
    clock.wait(hours=7)
    assert [r["run"] for r in runs.listing()] == ["run-1"]


def test_one_close_maps_at_a_time_and_a_second_leaves_its_run_open(runs: Runs, clock: Clock) -> None:
    runs.open(a_run())
    runs.bind("run-1", "s1", None)
    runs.open(a_run("run-2", "chat"))
    runs.bind("run-2", "s2", None)
    runs.begin_close("run-1")
    clock.wait(minutes=29)
    with pytest.raises(BusyError, match="another run is closing"):
        runs.begin_close("run-2")
    # Its idle clock restarted: 29 more minutes and it is still open.
    clock.wait(minutes=29)
    runs.reopen("run-1")
    assert runs.begin_close("run-2").id == "run-2"
    runs.reopen("run-2")
    runs.reopen("gone")
    assert {r["run"]: r["closing"] for r in runs.listing()} == {"run-1": False, "run-2": False}


@pytest.mark.parametrize(("wait", "why"), [({"minutes": 31}, "30 minutes passed"), ({"hours": 7}, "6 hours")])
def test_an_idle_or_old_run_is_dropped(runs: Runs, clock: Clock, state: Path, wait: dict[str, float], why: str) -> None:
    runs.open(a_run())
    runs.bind("run-1", "s1", None)
    if "hours" in wait:
        for n in range(12):
            clock.wait(minutes=29)
            keep(runs, tool_use_id=f"t{n}")
        clock.wait(minutes=13)
    else:
        clock.wait(**wait)
    with pytest.raises(RunError, match=why):
        runs.begin_close("run-1")
    assert not (state / "runs.json").exists()


def test_outcomes_are_kept_an_hour(runs: Runs, clock: Clock) -> None:
    runs.open(a_run())
    runs.drop("run-1", "the count didn't match")
    runs.drop("run-1", "again")
    outcome = runs.outcome("run-1")
    assert outcome is not None
    assert outcome.dropped == "the count didn't match"
    clock.wait(minutes=61)
    assert runs.outcome("run-1") is None


def test_close_all_drops_every_run_and_takes_the_marker_away(runs: Runs, state: Path) -> None:
    runs.open(a_run())
    runs.open(a_run("run-2", "chat"))
    runs.close_all()
    assert runs.listing() == []
    assert not (state / "runs.json").exists()


def test_a_new_registry_deletes_a_marker_a_stopped_service_left(state: Path, clock: Clock) -> None:
    state.mkdir()
    (state / "runs.json").write_text('{"runs": []}', encoding="utf-8")
    Runs(state, clock)
    assert not (state / "runs.json").exists()


def test_without_a_state_folder_the_registry_writes_no_marker() -> None:
    runs = Runs(None)
    runs.open(a_run(opened=runs.clock()))
    runs.bind("run-1", "s1", None)
    assert keep(runs).number == 1


def test_the_listing_holds_ids_counts_and_times_never_content(runs: Runs) -> None:
    runs.open(a_run())
    runs.bind("run-1", "s1", None)
    keep(runs)
    assert runs.listing() == [
        {
            "run": "run-1",
            "source": "memo",
            "bound": True,
            "final": False,
            "closing": False,
            "captures": 1,
            "bytes": 100,
            "opened": NOW.isoformat(),
        }
    ]


def test_a_close_that_drops_its_run_frees_the_next_close(runs: Runs) -> None:
    runs.open(a_run())
    runs.bind("run-1", "s1", None)
    runs.open(a_run("run-2", "chat"))
    runs.bind("run-2", "s2", None)
    runs.begin_close("run-1")
    runs.drop("run-1", "the mapper failed")
    assert runs.begin_close("run-2").id == "run-2"
