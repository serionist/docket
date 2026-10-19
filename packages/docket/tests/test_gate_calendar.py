"""Meetings at each promotion (DR-0647): the event rids a promotion writes are pending before their run file, the
calendar step makes their meetings once the gate lets go of the lock, and a step that stops never fails the promotion.
Invented people and events only."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import empty_vault, make_record, sh, traced
from test_ledger_calendar import NOW, config, event, meetings, said

from docket.cli import main
from docket.errors import DocketError
from docket.ledger import calendar, ops
from docket.ledger.store import write
from docket.vault.gate import Gate
from docket.vault.rawlog import RawLog
from docket.vault.vault import Vault

# A weekly sync's calendar, a hundred events, promoted with its meetings in this time on CI.
PROMOTE_SECONDS = 2.0


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return empty_vault(tmp_path, config())


def promote(vault: Vault, capsys: pytest.CaptureFixture[str]) -> list[str]:
    """`docket staging promote`, and what it said."""
    assert main(["staging", "promote", "--vault", str(vault.root)]) == 0
    return capsys.readouterr().out.splitlines()


def test_a_promotion_makes_the_meetings_of_the_event_records_it_writes(
    vault: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    Gate(vault).stage([event(1), event(2, 8), make_record(1)])
    out = promote(vault, capsys)
    commit = sh(vault.root, "git", "log", "-1", "--format=%h %s").strip()
    assert out[-1] == f"meetings  2 added; commit {commit[:7]}"
    assert commit.endswith("Ledger: meeting M-0001, meeting M-0002 on test-box")
    held = meetings(vault)
    assert (held["cal:E1"].number, held["cal:E2"].number, calendar.pending(vault)) == (1, 2, [])
    assert {(line.op.by, line.op.via) for line in ops.read_all(vault.root).lines} == {("sync", "cli")}


def test_a_moved_event_s_two_versions_in_one_promotion_land_at_its_last_time(vault: Vault) -> None:
    gate = Gate(vault)
    gate.stage([event(1, 9), event(1, 6)])
    result = gate.promote(via="page")
    assert (result.runs[0].written, result.runs[0].events) == (2, ("cal:E1",))
    assert result.meetings is not None
    assert said(result.meetings) == [("added", "M-0001")]
    assert meetings(vault)["cal:E1"].start.hour == 8
    # The Staging page's promotion writes through its own way in; a later version sets what changed.
    gate.stage([event(1, 6, cancelled=True)])
    again = gate.promote(via="page")
    assert again.meetings is not None
    assert said(again.meetings) == [("set", "M-0001")]
    assert meetings(vault)["cal:E1"].response == "cancelled"
    assert {line.op.via for line in ops.read_all(vault.root).lines} == {"page"}


def test_a_promotion_with_no_event_record_and_none_pending_makes_no_step(vault: Vault) -> None:
    gate = Gate(vault)
    gate.stage([make_record(1)])
    result = gate.promote()
    assert (result.runs[0].events, result.meetings, result.meetings_stopped) == ((), None, None)
    assert ops.read_all(vault.root).lines == ()


def test_a_step_that_stops_never_fails_the_promotion_and_the_next_replays_it(
    vault: Vault, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def busy(*_: Any, **__: Any) -> calendar.Stepped:
        raise DocketError("another docket process holds the vault; try again")

    monkeypatch.setattr(calendar, "step", busy)
    Gate(vault).stage([event(1)])
    out = promote(vault, capsys)
    assert out[0].startswith("cal: 1 written")
    assert out[-1] == (
        "meetings  not made: another docket process holds the vault; try again; their records stay pending for the "
        "next promotion or docket ledger calendar"
    )
    assert (len(vault.rawlog.read("cal:E1")), calendar.pending(vault), meetings(vault)) == (1, ["cal:E1"], {})
    # The vault check says what waits, and the vault is still ok.
    assert main(["vault", "check", "--vault", str(vault.root)]) == 0
    waiting = "1 calendar record(s) wait for their meetings; `docket ledger calendar`"
    assert capsys.readouterr().out.splitlines() == [waiting, "vault ok"]
    monkeypatch.undo()
    # The next promotion has nothing ready, and replays what is pending.
    out = promote(vault, capsys)
    assert out[0].startswith("meetings  1 added, 1 pending replayed; commit ")
    assert out[1] == "nothing ready"
    assert (meetings(vault)["cal:E1"].number, calendar.pending(vault)) == (1, [])
    assert main(["vault", "check", "--vault", str(vault.root)]) == 0
    assert capsys.readouterr().out.splitlines() == ["vault ok"]


def test_a_step_that_fails_any_other_way_never_fails_the_promotion_and_says_the_error_s_type(
    vault: Vault, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def bug(*_: Any, **__: Any) -> calendar.Stepped:
        return {}["x"]  # type: ignore[no-any-return]

    monkeypatch.setattr(calendar, "step", bug)
    Gate(vault).stage([event(1)])
    out = promote(vault, capsys)
    assert out[-1].startswith("meetings  not made: KeyError: 'x'; their records stay pending")
    assert calendar.pending(vault) == ["cal:E1"]

    def bare(*_: Any, **__: Any) -> calendar.Stepped:
        raise RuntimeError

    monkeypatch.setattr(calendar, "step", bare)
    gate = Gate(vault)
    gate.stage([event(2, 8)])
    result = gate.promote()
    assert (result.runs[0].written, result.meetings, result.meetings_stopped) == (1, None, "RuntimeError")
    assert calendar.pending(vault) == ["cal:E1", "cal:E2"]


def test_a_version_raw_holds_already_drives_no_set_over_a_hand_edit(vault: Vault) -> None:
    gate = Gate(vault)
    gate.stage([event(1, response="accepted")])
    # Another clone harvested and promoted the same version, and its run and ops were pulled.
    vault.write_run("cal", [event(1, response="accepted")], NOW)
    calendar.step(vault, ["cal:E1"], via="cli", now=NOW)
    # The sync declines it by hand.
    write(vault, [{"op": "set", "item": "M-0001", "fields": {"response": "declined"}}], by="sync", now=NOW)
    result = gate.promote()
    # The run skips the version raw holds, and nothing is noted or set for it.
    run = result.runs[0]
    assert (run.written, run.skipped, run.events, result.meetings) == (0, 1, (), None)
    assert (meetings(vault)["cal:E1"].response, calendar.pending(vault)) == ("declined", [])


def test_the_rids_are_pending_before_the_run_file_is_written(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    def crash(*_: Any, **__: Any) -> None:
        raise OSError("the disk went away")

    monkeypatch.setattr(RawLog, "write_run", crash)
    gate = Gate(vault)
    gate.stage([event(1), event(2, 8)])
    with pytest.raises(OSError, match="the disk went away"):
        gate.promote()
    assert calendar.pending(vault) == ["cal:E1", "cal:E2"]
    monkeypatch.undo()
    # The records are still staged: the next promotion writes them and makes their meetings.
    result = Gate(vault).promote()
    assert result.meetings is not None
    assert (said(result.meetings), calendar.pending(vault)) == ([("added", "M-0001"), ("added", "M-0002")], [])


@pytest.mark.perf
def test_a_promotion_of_a_week_s_hundred_events_with_their_meetings_stays_quick(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    week = [event(k, 6 + k % 10, k // 10 % 4 * 15, day=19 + k // 40, title=f"Pilot sync {k}") for k in range(100)]
    gate = Gate(vault)
    gate.stage(week)
    real = calendar.step
    stepping: list[float] = []

    def timed(*args: Any, **keys: Any) -> calendar.Stepped:
        started = time.perf_counter()
        try:
            return real(*args, **keys)
        finally:
            stepping.append(time.perf_counter() - started)

    monkeypatch.setattr(calendar, "step", timed)
    started = time.perf_counter()
    result = gate.promote()
    seconds = time.perf_counter() - started
    print(f"promotion of 100 events: {seconds:.2f} s, of which the calendar step {stepping[0]:.2f} s")
    assert result.meetings is not None
    assert (result.runs[0].written, result.meetings.counts) == (100, {"added": 100})
    # Under a tracer such as coverage the time says nothing about the code (DR-0430).
    if not traced():
        assert seconds < PROMOTE_SECONDS, f"100 events took {seconds:.2f} s, over {PROMOTE_SECONDS} s"
