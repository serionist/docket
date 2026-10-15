"""Open runs in the service's memory (DR-0240, DR-0243), on an injected clock: bind, captures, close, drop, expiry,
the caps, the outcomes kept, and the marker the capture hook reads."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from docket.service import history
from docket.service import runs as runs_module
from docket.service.history import Closing, Counts, Mark, RunLine, SetAside, Staged
from docket.service.runs import BusyError, Run, RunError, Runs
from docket.vault.cursors import Handover, Retry, Waiting, Window

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
def ended() -> list[RunLine]:
    return []


@pytest.fixture
def runs(state: Path, clock: Clock, ended: list[RunLine]) -> Runs:
    return Runs(state, clock, ended=ended.append)


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
    runs.drop("run-1", "the count didn't match", "count")
    runs.drop("run-1", "again", "count")
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
    runs.drop("run-1", "the mapper failed", "mapper")
    assert runs.begin_close("run-2").id == "run-2"


# -- how runs end, for the history (DR-0301, DR-0302) ------------------------------------------------------------------


def test_each_way_a_run_ends_names_its_kind_and_when(runs: Runs, clock: Clock, ended: list[RunLine]) -> None:
    runs.open(a_run("run-a"))
    runs.open(a_run("run-b"))
    clock.wait(seconds=90)
    runs.listing()
    runs.open(a_run("run-c", "chat", opened=clock()))
    runs.bind("run-c", "s1", None)
    clock.wait(minutes=45)
    runs.open(a_run("run-d", "notes", opened=clock()))
    runs.bind("run-d", "s2", None)
    for n in range(12):
        clock.wait(minutes=29)
        keep(runs, "run-d", session="s2", tool_use_id=f"t{n}")
    clock.wait(minutes=13)
    runs.listing()
    runs.open(a_run("run-e", "mail", opened=clock()))
    runs.bind("run-e", "s3", None)
    runs.close_all()
    assert [(line.run, line.kind, line.outcome) for line in ended] == [
        ("run-a", history.REPLACED, "dropped"),
        ("run-b", history.UNBOUND, "dropped"),
        ("run-c", history.IDLE, "dropped"),
        ("run-d", history.TOO_LONG, "dropped"),
        ("run-e", history.STOPPED, "dropped"),
    ]
    # A limit's drop ends when the limit passed, not when a later call saw it.
    by_run = {line.run: line for line in ended}
    assert by_run["run-b"].ended == NOW + runs_module.BIND_WITHIN
    assert by_run["run-c"].ended == by_run["run-c"].opened + runs_module.IDLE
    assert by_run["run-d"].ended == by_run["run-d"].opened + runs_module.MAX_OPEN
    assert by_run["run-d"].captures == 12
    assert by_run["run-a"].why == "replaced by a newer run, run-b"
    outcome = runs.outcome("run-e")
    assert outcome is not None
    assert (outcome.kind, outcome.dropped) == (history.STOPPED, "the service stopped")


def test_a_whole_close_adds_its_counts_and_caps_its_id_lists(runs: Runs, ended: list[RunLine]) -> None:
    window = Window(since=NOW - timedelta(days=7), until=NOW, default_since=NOW - timedelta(days=7), notes=[])
    handed = Handover(
        retry={"r1": Retry("r1", "later", NOW.date())}, waiting={"w1": Waiting("w1", "summary", NOW.date())}
    )
    runs.open(a_run(window=window, kept=frozenset({"k1", "k2"}), handed=handed, held=frozenset({"h1"})))
    runs.bind("run-1", "s1", None)
    keep(runs)
    run = runs.begin_close("run-1")
    run.skips = {"later": 2}
    expired = [f"x{n:02}" for n in range(25)]
    closing = Closing(
        staged=Staged(pending=3, skipped=1),
        dropped={"never-store": 1},
        set_aside=[SetAside(id=f"s{n:02}", reason="no-text") for n in range(22)],
        waiting=["w1"],
        mark=Mark(before=None, after=NOW, moved=True),
        cursor_problem=None,
        owed=Counts(retry=1, waiting=1),
        expired=expired,
        held_in_raw=["h0"],
    )
    runs.finish("run-1", "the answer", closing)
    [line] = ended
    assert (line.kind, line.outcome, line.whole, line.why) == (history.CLOSED, "closed", True, None)
    assert (line.since, line.until, line.told_to_skip, line.held, line.handed) == (
        window.since,
        NOW,
        2,
        1,
        Counts(retry=1, waiting=1),
    )
    assert (line.captures, line.bytes, line.skipped, line.dropped) == (1, 100, {"later": 2}, {"never-store": 1})
    assert line.staged == Staged(pending=3, skipped=1)
    assert (len(line.set_aside), line.set_aside_count) == (20, 22)
    assert (line.expired, line.expired_count) == (expired[:20], 25)
    assert (line.held_in_raw, line.held_in_raw_count, line.waiting, line.waiting_count) == (["h0"], 1, ["w1"], 1)
    assert line.mark == Mark(after=NOW, moved=True)


def test_a_line_holds_no_session_agent_or_capture(runs: Runs, ended: list[RunLine]) -> None:
    runs.open(a_run())
    runs.bind("run-1", "session-7b3a", "agent-41c2")
    keep(runs, session="session-7b3a", agent="agent-41c2")
    runs.drop("run-1", "x" * 700, history.COUNT)
    [line] = ended
    text = line.model_dump_json()
    assert "session-7b3a" not in text
    assert "agent-41c2" not in text
    assert TITLE not in text
    assert line.why is not None
    assert len(line.why) == history.WHY_CHARS


def test_runs_a_stopped_service_left_open_are_recorded_at_the_next_start(state: Path, clock: Clock) -> None:
    state.mkdir()
    left = {"runs": [{"run": "run-20261015T080000-0123456789abcdef", "source": "memo", "session": "s1"}, "junk"]}
    left["runs"].append({"run": "run-made-up", "source": "chat"})
    left["runs"].append({"run": 7, "source": "chat"})
    (state / "runs.json").write_text(json.dumps(left), encoding="utf-8")
    lines: list[RunLine] = []
    Runs(state, clock, ended=lines.append)
    assert [(line.run, line.source, line.kind, line.opened, line.ended) for line in lines] == [
        ("run-20261015T080000-0123456789abcdef", "memo", history.STOPPED, NOW.replace(hour=8, minute=0), NOW),
        ("run-made-up", "chat", history.STOPPED, NOW, NOW),
    ]
    assert not (state / "runs.json").exists()
    # A marker that doesn't read names nothing.
    for broken in ("not json", '{"runs": 3}', "[]"):
        (state / "runs.json").write_text(broken, encoding="utf-8")
        nothing: list[RunLine] = []
        Runs(state, clock, ended=nothing.append)
        assert nothing == []


def test_the_snapshot_says_when_each_open_run_drops(runs: Runs, clock: Clock) -> None:
    runs.open(a_run(final=True))
    runs.open(a_run("run-2", "chat", backfill=True))
    [unbound, backfill] = runs.snapshot()
    assert (unbound.final, unbound.bound, unbound.last) == (True, False, None)
    assert unbound.drops_at == NOW + timedelta(minutes=1)
    assert backfill.backfill is True
    runs.bind("run-1", "s1", None)
    clock.wait(minutes=5)
    keep(runs)
    bound = runs.snapshot()[0]
    assert (bound.bound, bound.captures, bound.bytes, bound.budget) == (True, 1, 100, runs_module.BUDGET)
    assert (bound.last, bound.drops_at) == (NOW + timedelta(minutes=5), NOW + timedelta(minutes=35))
    runs.mark_short("run-1", "a body past the cap")
    runs.begin_close("run-1")
    closing = runs.snapshot()[0]
    assert (closing.closing, closing.drops_at, closing.short) == (True, None, "a body past the cap")
