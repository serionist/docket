"""Meetings from calendar records (DR-0646 to DR-0652): what the core reads from an event record, the step's one write
by sync, joins of unkeyed meetings, undos read by how they were asked, what is kept out, the pending rids and the
catch-up. Invented people and events only."""

from __future__ import annotations

import shutil
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from docket_testkit import empty_vault, make_config, sh, traced
from ledger_kit import meeting as hand_meeting
from ledger_kit import ulid

from docket.cli import main
from docket.ledger import calendar, ops, store
from docket.ledger.calendar import header, meeting_fields, person
from docket.ledger.fold import Ledger, fold
from docket.ledger.meetings import Meeting
from docket.ledger.ops import Op
from docket.ledger.store import Event, LedgerError, write, write_calendar
from docket.media.staged import withhold
from docket.vault.config import NeverStore, SourceConfig, VaultConfig
from docket.vault.records import RawRecord
from docket.vault.vault import Vault

NOW = datetime(2026, 10, 19, 5, 0, tzinfo=UTC)
ZONE = ZoneInfo("Europe/Budapest")
BEN = "Ben Sample <ben@example.invalid>"
SERIES = "cal:S1"
# A catch-up writes its meetings in one write, each add folding again what it read: 300 records in this time.
CATCH_UP_SECONDS = 3.0


def config(**changes: Any) -> VaultConfig:
    base = make_config()
    sources = {**base.sources, "cal": SourceConfig(), **changes.pop("sources", {})}
    return base.model_copy(update={"sources": sources, **changes})


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return empty_vault(tmp_path, config())


def event(
    n: int,
    hour: int = 7,
    minute: int = 0,
    *,
    day: int = 19,
    minutes: int = 30,
    title: str | None = "Weekly pilot sync",
    where: str | None = "Room 1",
    response: str | None = "none",
    cancelled: bool = False,
    **keys: Any,
) -> RawRecord:
    """An event record in the reference calendar's shape: the time as shown, then the header's lines."""
    start = datetime(2026, 10, day, hour, minute, tzinfo=UTC)
    end = start + timedelta(minutes=minutes)
    text = [f"{start:%H:%M} to {end:%H:%M}, as shown"]
    text += [f"Where: {where}"] if where else []
    text += [f"Response: {response}"] if response else []
    text += ["Cancelled"] if cancelled else []
    shown = f"Canceled: {title}" if cancelled and title else title
    base: dict[str, Any] = {
        "rid": f"cal:E{n}", "source": "cal", "kind": "event", "ts": start, "author": BEN, "title": shown,
        "text": "\n".join(text), "native": {"end": end.isoformat(), "all_day": False}, "fetched": NOW, "via": "test",
    }  # fmt: skip
    return RawRecord.model_validate({**base, **keys})


def ledger(vault: Vault) -> Ledger:
    return fold(ops.read_all(vault.root).lines)


def meetings(vault: Vault) -> dict[str, Meeting]:
    """The standing meetings by event, an unkeyed one by its subject."""
    return {m.event or f"({m.subject})": m for m in ledger(vault).meetings.values()}


def step(vault: Vault, *records: RawRecord, minutes: int = 0, **keys: Any) -> calendar.Stepped:
    return calendar.step(vault, list(records), via="cli", now=NOW + timedelta(minutes=minutes), **keys)


def said(stepped: calendar.Stepped) -> list[tuple[str, str | None]]:
    return [(o.what, o.meeting) for o in stepped.outcomes]


def put_ops(vault: Vault, *written: Op) -> None:
    """Ops another clone wrote and the user's git merged in."""
    for o in written:
        path = vault.root / ops.rel_of(o)
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(before + o.text() + "\n", encoding="utf-8", newline="\n")
    vault.commit(sorted({ops.rel_of(o) for o in written}), "Merge another clone's ops")


# -- the record -----------------------------------------------------------------------------------------------------


def test_the_header_gives_the_first_where_and_response_and_a_cancelled_line_and_never_a_body() -> None:
    assert header("9:00 to 9:30\r\nWhere: Room 1\r\nWhere: Room 2\nResponse: accepted\nCancelled") == (
        "Room 1", "accepted", True,
    )  # fmt: skip
    assert header("9:00\nResponse: \nResponse: declined\n\nWhere: in the body\nCancelled") == (None, None, False)
    assert header("") == (None, None, False)


def test_a_person_is_the_name_without_its_address_or_the_address_alone() -> None:
    assert [person(v) for v in (BEN, " Ben   Sample ", "<ben@example.invalid>", "ben@example.invalid", " ", None)] == [
        "Ben Sample", "Ben Sample", "ben@example.invalid", "ben@example.invalid", None, None,
    ]  # fmt: skip
    assert person("<  >") is None


def test_a_record_gives_its_fields_in_the_owner_s_zone_either_side_of_the_dst_change() -> None:
    summer = meeting_fields(event(1, thread=SERIES, to=[BEN, "Cleo Test <cleo@example.invalid>", BEN]), ZONE)
    assert summer == {
        "event": "cal:E1", "subject": "Weekly pilot sync", "start": "2026-10-19T09:00+02:00",
        "end": "2026-10-19T09:30+02:00", "series": SERIES, "organiser": "Ben Sample", "where": "Room 1",
        "response": "none", "attendees": ["Ben Sample", "Cleo Test"],
    }  # fmt: skip
    # A week later the clocks went back: the same 09:00 is an hour later in UTC, and carries +01:00.
    winter = meeting_fields(event(2, 8, day=26, where=None, response="organiser", author=None), ZONE)
    assert winter == {
        "event": "cal:E2", "subject": "Weekly pilot sync", "start": "2026-10-26T09:00+01:00",
        "end": "2026-10-26T09:30+01:00", "response": "organiser",
    }  # fmt: skip


def test_a_cancelled_record_drops_its_prefix_and_an_odd_response_or_no_title_is_read_as_such() -> None:
    gone = meeting_fields(event(1, cancelled=True, response="tentative"), ZONE)
    assert isinstance(gone, dict)
    assert (gone["subject"], gone["response"]) == ("Weekly pilot sync", "cancelled")
    odd = meeting_fields(event(2, title=None, response="maybe"), ZONE)
    assert isinstance(odd, dict)
    assert (odd["subject"], "response" in odd) == ("(no subject)", False)


@pytest.mark.parametrize(
    ("keys", "why"),
    [
        ({"fidelity": "derived"}, "derived: its rid is built from its content"),
        ({"native": {"end": "2026-10-20T00:00:00+00:00", "all_day": True}}, "an all-day event"),
        ({"native": {"all_day": False}}, "no end"),
        ({"native": {"end": "2026-10-19T09:30:00"}}, "no end"),
        ({"native": {"end": "soon"}}, "no end"),
        ({"native": {"end": "2026-10-19T06:00:00+00:00"}}, "end is before start"),
    ],
)
def test_a_record_the_core_can_t_take_says_why(keys: dict[str, Any], why: str) -> None:
    assert meeting_fields(event(1, **keys), ZONE) == why


def test_one_version_per_rid_the_last_given_and_only_events() -> None:
    first, moved = event(1), event(1, 6)
    note = event(2).model_copy(update={"kind": "message"})
    assert [r.ts.hour for r in calendar.latest([first, note, moved])] == [6]


# -- the step's write -----------------------------------------------------------------------------------------------


def test_the_step_adds_meetings_in_start_order_in_one_write_by_sync(vault: Vault) -> None:
    stepped = step(vault, event(2, 8), event(1, 7, thread=SERIES))
    assert said(stepped) == [("added", "M-0001"), ("added", "M-0002")]
    assert [(o.op, o.by, o.via) for o in stepped.ops] == [("meeting", "sync", "cli")] * 2
    held = meetings(vault)
    assert (held["cal:E1"].number, held["cal:E1"].series, held["cal:E2"].number) == (1, SERIES, 2)
    message = sh(vault.root, "git", "log", "-1", "--format=%s").strip()
    assert message == "Ledger: meeting M-0001, meeting M-0002 on test-box"
    assert calendar.lines(stepped)[-1] == f"2 op(s) written, commit {stepped.commit[:7]}"  # type: ignore[index]


def test_a_new_version_sets_what_differs_and_one_that_changes_nothing_writes_nothing(vault: Vault) -> None:
    step(vault, event(1))
    moved = step(vault, event(1, 8, minutes=60, title="Weekly pilot sync, moved", where=None), minutes=1)
    assert said(moved) == [("set", "M-0001")]
    (put,) = moved.ops
    assert (put.op, put.fields) == ("set", {
        "subject": "Weekly pilot sync, moved", "start": "2026-10-19T10:00+02:00", "end": "2026-10-19T11:00+02:00",
    })  # fmt: skip
    # A version without a Where: line clears nothing; the place stays.
    m = meetings(vault)["cal:E1"]
    assert (m.where, m.subject, m.start.hour) == ("Room 1", "Weekly pilot sync, moved", 10)
    same = step(vault, event(1, 8, minutes=60, title="Weekly pilot sync, moved", native={
        "end": "2026-10-19T09:00:00+00:00", "all_day": False, "show_as": "Busy"}), minutes=2)  # fmt: skip
    assert (said(same), same.ops, same.commit) == ([("kept", "M-0001")], (), None)


def test_two_versions_of_a_moved_event_in_one_step_land_at_its_last_time(vault: Vault) -> None:
    stepped = step(vault, event(1, 9), event(1, 6))
    assert said(stepped) == [("added", "M-0001")]
    assert meetings(vault)["cal:E1"].start.astimezone(UTC).hour == 6


def test_a_set_on_an_archived_meeting_is_idle_and_a_cancellation_sets_the_response(vault: Vault) -> None:
    step(vault, event(1), event(2, 8))
    write(vault, [{"op": "archive", "item": "M-0001"}], now=NOW + timedelta(minutes=1))
    stepped = step(vault, event(1, title="Renamed"), event(2, 8, cancelled=True), minutes=2)
    assert said(stepped) == [("idle", "M-0001"), ("set", "M-0002")]
    m = meetings(vault)["cal:E2"]
    assert (m.subject, m.response) == ("Weekly pilot sync", "cancelled")
    assert meetings(vault)["cal:E1"].subject == "Weekly pilot sync"


def test_a_cancelled_record_no_meeting_holds_makes_none(vault: Vault) -> None:
    stepped = step(vault, event(1, cancelled=True))
    assert said(stepped) == [(store.LEFT_OUT, None)]
    assert (stepped.outcomes[0].why, stepped.ops) == ("cancelled, and no meeting holds it", ())


def test_the_writer_leaves_out_what_its_rules_refuse_and_writes_the_rest(vault: Vault) -> None:
    good = Event("cal:E1", meeting_fields(event(1), ZONE))  # type: ignore[arg-type]
    fields: dict[str, Any] = dict(good.fields)
    written = write_calendar(vault, [
        Event("cal:E9", {**fields, "event": "cal:sk-abcdefghijklmnopqrstuvwxyz0123"}),
        Event("cal:E8", {**fields, "end": "2026-10-19T08:00+02:00"}),
        Event("cal:E7", {name: v for name, v in fields.items() if name != "event"}),
        Event("cal:E6", {name: v for name, v in fields.items() if name != "subject"}),
        good,
    ], via="cli", now=NOW)  # fmt: skip
    assert [(o.rid, o.what) for o in written.outcomes] == [
        ("cal:E9", store.LEFT_OUT), ("cal:E8", store.LEFT_OUT), ("cal:E7", store.LEFT_OUT),
        ("cal:E6", store.LEFT_OUT), ("cal:E1", "added"),
    ]  # fmt: skip
    whys = [o.why for o in written.outcomes[:4]]
    assert whys[1:] == ["end is before start", "a calendar record's meeting needs its event", "a meeting needs subject"]
    assert "redaction rule" in str(whys[0])
    assert written.commit is not None
    assert write_calendar(vault, [], via="cli", now=NOW).ops == ()
    with pytest.raises(LedgerError, match="via is one of"):
        write_calendar(vault, [good], via="mail", now=NOW)  # type: ignore[arg-type]


# -- joins ----------------------------------------------------------------------------------------------------------


def test_a_record_joins_the_unkeyed_meeting_with_its_start_and_subject_and_its_number_holds(vault: Vault) -> None:
    put_ops(vault, hand_meeting(1, subject="weekly  PILOT sync", number=5), hand_meeting(2, number=6))
    stepped = step(vault, event(1, thread=SERIES))
    # Two match: the earliest by id is joined, and the other stays as it was.
    assert said(stepped) == [("joined", "M-0005")]
    (join,) = stepped.ops
    assert ((join.fields or {})["continues"], join.number) == (ulid(1), 5)
    held = meetings(vault)
    assert (held["cal:E1"].id, held["cal:E1"].number, held["cal:E1"].subject) == (ulid(1), 5, "Weekly pilot sync")
    assert held["(Weekly pilot sync)"].number == 6
    # The key continues one meeting: a new version sets, and never joins the other.
    again = step(vault, event(1, thread=SERIES, where="Room 2"), minutes=1)
    assert said(again) == [("set", "M-0005")]


def test_a_minute_off_is_no_join_but_both_near_misses_are_named(vault: Vault) -> None:
    put_ops(
        vault,
        hand_meeting(1, start="2026-10-19T09:01+02:00", end="2026-10-19T09:30+02:00"),
        hand_meeting(2, subject="Supplier call", number=2),
        hand_meeting(3, "cal:OTHER", start="2026-10-19T11:00+02:00", end="2026-10-19T11:30+02:00", number=3),
    )
    stepped = step(vault, event(1))
    (added,) = stepped.outcomes
    assert (added.what, added.meeting, added.near) == ("added", "M-0004", ("M-0002", "M-0001"))
    assert calendar.lines(stepped)[:3] == [
        "added    M-0004  2026-10-19 09:00  Weekly pilot sync",
        "near     M-0004 has M-0002 close by: join it by hand if it is the same",
        "near     M-0004 has M-0001 close by: join it by hand if it is the same",
    ]


def test_an_archived_old_meeting_is_joined_and_only_gains_its_key(vault: Vault) -> None:
    put_ops(vault, hand_meeting(1, where="Old room", number=3))
    write(vault, [{"op": "archive", "item": "M-0003"}], now=NOW)
    stepped = step(vault, event(1, where="Room 9"), minutes=1)
    assert said(stepped) == [("joined", "M-0003")]
    m = meetings(vault)["cal:E1"]
    assert (m.archived, m.where, m.number) == (True, "Old room", 3)


def test_a_meeting_the_step_made_first_still_joins_the_imported_one_whose_number_wins(vault: Vault) -> None:
    step(vault, event(1))
    put_ops(vault, hand_meeting(1, number=40))
    stepped = step(vault, event(1), minutes=1)
    assert said(stepped) == [("joined", "M-0040")]
    m = meetings(vault)["cal:E1"]
    assert (m.id, m.number, len(m.adds)) == (ulid(1), 40, 3)


# -- undos, read by how they were asked ---------------------------------------------------------------------------


def adds_of(vault: Vault, key: str) -> list[Op]:
    return [line.op for line in ops.read_all(vault.root).lines if (line.op.fields or {}).get("event") == key
            and line.op.op == "meeting"]  # fmt: skip


def test_the_owner_s_undo_by_of_removes_the_meeting_for_good_even_several_in_one_write(vault: Vault) -> None:
    step(vault, event(1), event(2, 8))
    (one,), (two,) = adds_of(vault, "cal:E1"), adds_of(vault, "cal:E2")
    write(vault, [{"op": "undo", "of": one.id}, {"op": "undo", "of": two.id}], by="user", now=NOW + timedelta(1))
    assert all(line.op.of_batch is None for line in ops.read_all(vault.root).lines if line.op.op == "undo")
    stepped = step(vault, event(1, title="Moved on"), event(2, 8), minutes=3000)
    assert (said(stepped), stepped.ops) == ([("removed", None), ("removed", None)], ())
    gone = "removed  2026-10-19 09:00  Moved on: the owner removed its meeting, so it stays out"
    assert calendar.lines(stepped)[0] == gone


def test_an_undo_by_batch_or_by_sync_is_as_if_never_and_the_step_adds_again(vault: Vault) -> None:
    first = step(vault, event(1), event(2, 8))
    write(vault, [{"op": "undo", "batch": first.ops[0].id}], by="user", now=NOW + timedelta(minutes=1))
    undos = [line.op for line in ops.read_all(vault.root).lines if line.op.op == "undo"]
    assert {u.of_batch for u in undos} == {first.ops[0].id}
    assert ops.read_op(undos[0].text()).of_batch == first.ops[0].id
    again = step(vault, event(1), event(2, 8), minutes=2)
    assert said(again) == [("added", "M-0003"), ("added", "M-0004")]
    (e1,) = [o for o in adds_of(vault, "cal:E1") if o.number == 3]
    write(vault, [{"op": "undo", "of": e1.id}], by="sync", now=NOW + timedelta(minutes=3))
    assert said(step(vault, event(1), minutes=4)) == [("added", "M-0005")]


def test_a_split_is_never_rejoined_and_its_key_gets_a_meeting_of_its_own(vault: Vault) -> None:
    put_ops(vault, hand_meeting(1, number=7))
    joined = step(vault, event(1))
    (join,) = joined.ops
    write(vault, [{"op": "undo", "of": join.id}], by="claude", now=NOW + timedelta(minutes=1))
    own = step(vault, event(1), minutes=2)
    assert said(own) == [("added", "M-0008")]
    held = meetings(vault)
    assert (held["cal:E1"].number, held["(Weekly pilot sync)"].number) == (8, 7)
    assert said(step(vault, event(1), minutes=3)) == [("kept", "M-0008")]


def test_removing_a_joined_meeting_removes_it_for_good(vault: Vault) -> None:
    put_ops(vault, hand_meeting(1, number=7))
    step(vault, event(1))
    write(vault, [{"op": "undo", "of": ulid(1)}], by="user", now=NOW + timedelta(minutes=1))
    assert said(step(vault, event(1), minutes=2)) == [("removed", None)]


# -- kept out -------------------------------------------------------------------------------------------------------


def test_what_media_s_screen_or_a_hold_keeps_out_makes_nothing_and_an_off_source_still_counts(tmp_path: Path) -> None:
    v = empty_vault(tmp_path, config(
        never_store=NeverStore(keywords=["Project Nightjar"]), sources={"off": SourceConfig(enabled=False)},
    ))  # fmt: skip
    (v.root / "state" / "holds").mkdir(parents=True, exist_ok=True)
    (v.root / "state" / "holds" / "cal.txt").write_text("E3\n", encoding="utf-8")
    withhold(v.root, "cal:E4")
    other = event(5).model_copy(update={"rid": "other:E5", "source": "other"})
    off = event(6).model_copy(update={"rid": "off:E6", "source": "off"})
    stepped = step(v, event(1), event(2, title="Project Nightjar review"), event(3), event(4), other, off)
    assert [(o.rid, o.what, o.why) for o in stepped.outcomes] == [
        ("cal:E1", "added", None), ("off:E6", "added", None),
        ("cal:E2", store.LEFT_OUT, "kept out: never-store"), ("cal:E3", store.LEFT_OUT, "kept out: held"),
        ("cal:E4", store.LEFT_OUT, "kept out: never-store"), ("other:E5", store.LEFT_OUT, "kept out: private"),
    ]  # fmt: skip
    assert calendar.lines(stepped)[2] == "left out cal:E2: kept out: never-store"


# -- two clones -----------------------------------------------------------------------------------------------------


def clone(vault: Vault, tmp_path: Path, name: str, monkeypatch: pytest.MonkeyPatch) -> Vault:
    """A copy of the vault on another machine, whose name is read when it opens."""
    copy = tmp_path / name
    shutil.copytree(vault.root, copy)
    monkeypatch.setenv("DOCKET_MACHINE", name)
    opened = Vault.open(copy)
    monkeypatch.setenv("DOCKET_MACHINE", vault.machine)
    return opened


@pytest.mark.parametrize("laptop_first", [False, True])
def test_two_clones_stepping_the_same_records_make_one_meeting_per_event(
    vault: Vault, tmp_path: Path, laptop_first: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    put_ops(vault, hand_meeting(1, number=3))
    laptop = clone(vault, tmp_path, "laptop", monkeypatch)
    week = [event(1), event(2, 8), event(3, 9)]
    later = {"minutes": 5}
    step(laptop if laptop_first else vault, *week)
    step(vault if laptop_first else laptop, *week, **later)
    theirs = next((laptop.root / ops.OPS_DIR).glob("*-laptop.jsonl"))
    shutil.copy(theirs, vault.root / ops.OPS_DIR / theirs.name)
    vault.commit([f"{ops.OPS_DIR}/{theirs.name}"], "Merge the laptop's ops")
    folded = ledger(vault)
    by_event = {m.event: (m.id, m.number) for m in folded.meetings.values()}
    assert by_event == {"cal:E1": (ulid(1), 3), "cal:E2": by_event["cal:E2"], "cal:E3": by_event["cal:E3"]}
    assert (by_event["cal:E2"][1], by_event["cal:E3"][1], folded.objects.clashes()) == (4, 5, [])


# -- pending rids ---------------------------------------------------------------------------------------------------


def test_pending_rids_are_noted_cleared_and_replayed_at_raw_s_latest_version_with_their_sets(vault: Vault) -> None:
    assert calendar.pending(vault) == []
    calendar.note_pending(vault, ["cal:E1", "cal:E2"])
    calendar.note_pending(vault, ["cal:E1", "cal:E9"])
    assert calendar.pending(vault) == ["cal:E1", "cal:E2", "cal:E9"]
    calendar.clear_pending(vault, ["cal:E2"])
    assert calendar.pending(vault) == ["cal:E1", "cal:E9"]
    calendar.clear_pending(vault, ["cal:E1", "cal:E9"])
    assert not calendar.pending_path(vault).exists()
    step(vault, event(1))
    # A promotion noted E1's next version, wrote its run file, and stopped before its step: the next step replays it.
    calendar.note_pending(vault, ["cal:E1", "cal:E9"])
    vault.write_run("cal", [event(1, where="Room 2")], NOW + timedelta(minutes=1))
    dry = step(vault, minutes=2, dry_run=True)
    assert (said(dry), dry.commit, calendar.pending(vault)) == ([("set", "M-0001")], None, ["cal:E1", "cal:E9"])
    assert calendar.lines(dry)[-2:] == [
        "replayed 2 pending record(s)", "dry run: 1 op(s) would be written; nothing written",
    ]  # fmt: skip
    replayed = step(vault, minutes=3)
    assert (said(replayed), replayed.replayed) == ([("set", "M-0001")], ("cal:E1", "cal:E9"))
    assert calendar.pending(vault) == []
    assert meetings(vault)["cal:E1"].where == "Room 2"


def test_a_pending_rid_after_a_full_revert_applies_to_the_ledger_it_meets(vault: Vault) -> None:
    vault.write_run("cal", [event(1, where="Room 2")], NOW)
    calendar.note_pending(vault, ["cal:E1"])
    # The ledger is empty, as a full revert leaves it: the rid is read at raw's latest version and adds its meeting.
    stepped = step(vault)
    assert (said(stepped), meetings(vault)["cal:E1"].where) == ([("added", "M-0001")], "Room 2")
    nothing = step(vault, minutes=1)
    assert (nothing.outcomes, calendar.lines(nothing)) == ((), ["no event records"])


# -- the catch-up -------------------------------------------------------------------------------------------------


def test_the_catch_up_adds_and_joins_from_raw_but_never_sets(vault: Vault) -> None:
    put_ops(vault, hand_meeting(1, number=9))
    vault.write_run("cal", [event(1), event(2, 8), event(3, 7, day=1)], NOW)
    dry = calendar.catch_up(vault, date(2026, 10, 18), dry_run=True, now=NOW)
    assert (said(dry), dry.ops[0].via, dry.commit) == ([("joined", "M-0009"), ("added", "M-0010")], "cli", None)
    caught = calendar.catch_up(vault, date(2026, 10, 18), now=NOW + timedelta(minutes=1))
    assert said(caught) == [("joined", "M-0009"), ("added", "M-0010")]
    vault.write_run("cal", [event(2, 8, where="Room 5")], NOW + timedelta(minutes=2))
    again = calendar.catch_up(vault, now=NOW + timedelta(minutes=3))
    assert (said(again), again.ops) == ([("kept", "M-0009"), ("kept", "M-0010")], ())
    assert (meetings(vault)["cal:E2"].where, calendar.lines(again)) == ("Room 1", ["2 kept"])
    assert calendar.since_default(vault, NOW) == date(2026, 10, 5)


def test_the_command_catches_up_and_says_what_it_did(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    vault.write_run("cal", [event(1), event(2, native={"all_day": True, "end": "2026-10-20T00:00:00+00:00"})], NOW)
    code = main(["ledger", "calendar", "--since", "2026-10-01", "--dry-run", "--vault", str(vault.root)])
    out = capsys.readouterr().out.splitlines()
    assert (code, out) == (0, [
        "added    M-0001  2026-10-19 09:00  Weekly pilot sync", "left out cal:E2: an all-day event",
        "1 added, 1 left out", "dry run: 1 op(s) would be written; nothing written",
    ])  # fmt: skip
    assert main(["ledger", "calendar", "--since", "2026-10-01", "--vault", str(vault.root)]) == 0
    assert said_line(capsys)[-1].startswith("1 op(s) written, commit ")
    assert main(["ledger", "calendar", "--since", "Monday", "--vault", str(vault.root)]) != 0
    assert "--since is a day" in capsys.readouterr().err
    # Without --since it reaches 14 days back from today, whatever today is: nothing to write either way.
    assert main(["ledger", "calendar", "--vault", str(vault.root)]) == 0
    assert "op(s) written" not in capsys.readouterr().out


def said_line(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return capsys.readouterr().out.splitlines()


@pytest.mark.perf
def test_a_catch_up_of_300_records_takes_under_three_seconds(vault: Vault) -> None:
    records = [event(k, 6 + k % 12, k % 4 * 15, day=1 + k // 12) for k in range(300)]
    found = calendar.latest(records)
    started = time.perf_counter()
    stepped = calendar.step(vault, via="cli", catch_up=found, now=NOW)
    seconds = time.perf_counter() - started
    print(f"calendar catch-up: {seconds:.2f} s for 300 records")
    assert stepped.counts == {"added": 300}
    # Under a tracer such as coverage the time says nothing about the code (DR-0430).
    if not traced():
        assert seconds < CATCH_UP_SECONDS, f"a catch-up of 300 records took {seconds:.2f} s, over {CATCH_UP_SECONDS} s"
