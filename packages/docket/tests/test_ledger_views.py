"""The ledger's reads (DR-0638, DR-0643, DR-0644): each view on a seeded ledger, as rows and as lines; notes, outcomes,
attendees, answers and history only in show; k; counts from the ops, against counts made by hand; the refs a batch
takes; the windows; and the commands, as text and as JSON, on an index and with none. Invented people only."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from docket_testkit import empty_vault, make_config, make_record
from ledger_kit import ADD_FIELDS, fresh_tables, meeting, op, point, point_set, ulid, write_ops

from docket.cli import main
from docket.errors import DocketError
from docket.index import ledger as ledger_index
from docket.index import rebuild
from docket.ledger import ops, views
from docket.ledger.ops import Op
from docket.ledger.views import Ask, LedgerRead
from docket.vault.records import version
from docket.vault.vault import Vault

TZ = ZoneInfo("Europe/Budapest")
# Tuesday 20 October, 10:00 in Budapest.
AT = datetime(2026, 10, 20, 8, 0, tzinfo=UTC)
# Text only show says: notes, a description, an outcome, an attendee, an answer.
SHOWN = "SHOW-ONLY-TEXT"
LONG = "Send the pilot's dates to everyone on the list before the steering group meets on Thursday afternoon"


def item(n: int, number: int, **fields: Any) -> Op:
    return op("add", n, number=number, fields={**ADD_FIELDS, **fields})


def seed() -> list[Op]:
    """Seven items, five meetings and two streams, with the ops a sync and the owner would write."""
    return [
        item(1, 1, opened="2026-10-10", due="2026-10-18", meeting=ulid(20)),
        op("triage", 2, 1, verdict="chase"),
        # A chase repeated on an item already marked chase changes nothing, and is a chase.
        op("triage", 3, 1, verdict="chase"),
        item(4, 2, kind="waiting", title="Ben's numbers", who="Ben Example", notes=f"{SHOWN} notes"),
        op("snooze", 5, 4, until=date(2026, 10, 25)),
        item(6, 3, kind="task", title=LONG, who="Cleo Example", stream="ops", due="2026-10-22",
             description=f"{SHOWN} description"),
        op("triage", 7, 6, verdict="discuss"),
        item(8, 4, kind="decision", title="Pick the vendor", opened="2026-10-01", outcome=f"{SHOWN} outcome"),
        op("done", 9, 8),
        op("archive", 18, 8),
        item(10, 5, title="Answer the survey", who="Dan Example"),
        op("drop", 11, 10),
        item(12, 6, title="Confirm the venue", due="2026-10-21"),
        op("triage", 13, 12, verdict="chase"),
        op("undo", 14, 12, of=ulid(13)),
        op("set", 15, 12, fields={"due": "2026-10-23"}),
        op("snooze", 16, 12, until=date(2026, 10, 19)),
        op("reopen", 17, 12),
        meeting(20, number=1, subject="Weekly pilot sync", start="2026-10-21T09:00+02:00", end="2026-10-21T09:30+02:00",
                organiser="Ada Example", attendees=["Ada Example", "Ben Example", f"{SHOWN} Person"], stream="pilot",
                notes=f"{SHOWN} meeting notes", where="Room 3"),
        point(21, 20, "The pilot's dates", item=ulid(1)),
        point(22, 20, "The budget"),
        point_set(23, 20, 22, state="answered", answer=f"{SHOWN} answer"),
        point(24, 20, "Ben's numbers", item=ulid(4), proposed=True),
        meeting(30, number=2, subject="Old review", start="2026-10-01T10:00+02:00", end="2026-10-01T11:00+02:00"),
        point(31, 30, "What we left open"),
        meeting(32, number=3, subject="Archived retro", start="2026-10-02T10:00+02:00", end="2026-10-02T11:00+02:00"),
        op("archive", 33, 32),
        meeting(34, number=4, subject="Far planning", start="2026-11-20T10:00+01:00", end="2026-11-20T11:00+01:00"),
        meeting(35, number=5, subject="Vendor call", start="2026-10-22T14:00+02:00", end="2026-10-22T15:00+02:00",
                response="declined"),
        op("stream", 40, fields={"name": "pilot", "status": "active", "next": "Book the room", "deadline": "2026-10-30",
                                 "where": "projects/pilot/"}),
        op("stream", 41, fields={"name": "ops", "status": "waiting"}),
    ]  # fmt: skip


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    write_ops(tmp_path, seed())
    c = fresh_tables()
    ledger_index.rebuild(c, tmp_path)
    yield c
    c.close()


def ask(conn: sqlite3.Connection, view: str = "snapshot", **keys: Any) -> LedgerRead:
    return views.read(conn, Ask(view=view, **keys), TZ, AT)


def said(conn: sqlite3.Connection, view: str = "snapshot", **keys: Any) -> list[str]:
    return views.lines(ask(conn, view, **keys), TZ)


SOURCE = ADD_FIELDS["source"]


def tables_of(root: Path, made: list[Op]) -> sqlite3.Connection:
    write_ops(root, made)
    c = fresh_tables()
    ledger_index.rebuild(c, root)
    return c


# -- the views ----------------------------------------------------------------------------------------------------


def test_a_snapshot_gives_the_open_items_with_sources_the_window_s_meetings_with_points_and_the_streams(
    conn: sqlite3.Connection,
) -> None:
    """Items open or snoozed; meetings from yesterday to six days on, or not archived with an open point, each with
    its standing points and their ids; every stream. Closed items, an archived meeting and one past the window stay
    out."""
    found = ask(conn)
    assert (found.since, found.until) == (datetime(2026, 10, 19, tzinfo=TZ), datetime(2026, 10, 27, tzinfo=TZ))
    assert views.lines(found, TZ) == [
        "3 open, 1 snoozed, 1 overdue; 3 meetings, 3 points open; 2 streams",
        "items",
        "A-0001 [reply] Ada Example: Send Ada the dates (10d, due 10-18, 2d late) [pilot] @M-0001 <chase> src "
        + SOURCE,
        f"A-0002 [waiting] Ben Example: Ben's numbers (2d, snoozed to 10-25) [pilot] src {SOURCE}",
        f"A-0003 [task] Cleo Example: {LONG[:71]}… (2d, due 10-22) [ops] <discuss> src {SOURCE}",
        f"A-0006 [reply] Ada Example: Confirm the venue (2d, due 10-23) [pilot] src {SOURCE}",
        "meetings 10-19 to 10-26, or with an open point",
        "M-0002 10-01 10:00-11:00 Old review; 1 open",
        f"  #1 open What we left open {ulid(31)}",
        "M-0001 10-21 09:00-09:30 Weekly pilot sync, Ada Example [pilot]; 2 open; items A-0001",
        f"  #1 open The pilot's dates -> A-0001 {ulid(21)}",
        f"  #2 answered The budget {ulid(22)}",
        f"  #3 open Ben's numbers -> A-0002 (proposed) {ulid(24)}",
        "M-0005 10-22 14:00-15:00 Vendor call (declined)",
        "streams",
        "ops waiting",
        "pilot active; next: Book the room; deadline 10-30; where projects/pilot/",
    ]  # fmt: skip
    first = found.items[0]
    assert (first.ref, first.id, first.meeting, first.days, first.late, first.triage) == (
        "A-0001", ulid(1), "M-0001", 10, 2, "chase",
    )  # fmt: skip
    assert [p.ref for p in found.points] == ["M-0002#1", "M-0001#1", "M-0001#2", "M-0001#3"]
    assert found.meetings[1].start == datetime(2026, 10, 21, 9, tzinfo=TZ)
    assert found.counts.model_dump(exclude_defaults=True) == {
        "items": 4, "open": 3, "snoozed": 1, "overdue": 1, "meetings": 3, "points_open": 3, "streams": 2,
    }  # fmt: skip


def test_the_queue_puts_the_untriaged_first_then_each_verdict_in_todo_s_order_each_by_due(tmp_path: Path) -> None:
    made = [
        item(1, 1, title="Chase, no due"), op("triage", 2, 1, verdict="chase"),
        item(3, 2, title="Chase, due late", due="2026-10-25"), op("triage", 4, 3, verdict="chase"),
        item(5, 3, title="Chase, due soon", due="2026-10-21"), op("triage", 6, 5, verdict="chase"),
        item(7, 4, title="Mine", kind="task"), op("triage", 8, 7, verdict="me"),
        item(9, 5, title="Untriaged"),
        item(10, 6, title="Snoozed"), op("snooze", 11, 10, until=date(2026, 10, 30)),
        item(12, 7, title="For Claude"), op("triage", 13, 12, verdict="claude"),
    ]  # fmt: skip
    found = views.read(tables_of(tmp_path, made), Ask(view="queue"), TZ, AT)
    assert [i.title for i in found.items] == [
        "Untriaged", "For Claude", "Mine", "Chase, due soon", "Chase, due late", "Chase, no due",
    ]  # fmt: skip
    lines = views.lines(found, TZ)
    assert lines[0] == "6 open: 1 untriaged, 1 claude, 1 me, 3 chase"
    assert [line for line in lines if not line.startswith("  ")] == [lines[0], "untriaged", "claude", "me", "chase"]
    assert found.counts.triage == {"claude": 1, "me": 1, "chase": 3}


def test_list_counts_every_match_then_gives_the_first_k(conn: sqlite3.Connection) -> None:
    every = ask(conn, "list", state="all", k=2)
    assert views.lines(every, TZ) == [
        "6 items: 3 open, 1 snoozed, 1 done, 1 dropped; 1 overdue; chased 1 item, 2 times; median 17 days to done",
        "the first 2",
        "A-0004 [decision] Ada Example: Pick the vendor (17d, done 10-18, archived) [pilot]",
        "A-0001 [reply] Ada Example: Send Ada the dates (10d, due 10-18, 2d late) [pilot] @M-0001 <chase>",
    ]
    assert len(ask(conn, "list", state="all", k=None).items) == every.counts.items == 6
    assert views.lines(ask(conn, "list", state="dropped"), TZ) == [
        "1 item: 0 open, 0 snoozed, 0 done, 1 dropped; 0 overdue; chased 0 items, 0 times",
        "A-0005 [reply] Dan Example: Answer the survey (0d, dropped 10-18) [pilot]",
    ]


@pytest.mark.parametrize(
    ("keys", "numbers"),
    [
        ({}, [1, 2, 3, 6]),
        ({"state": "snoozed"}, [2]),
        ({"state": "done"}, [4]),
        ({"who": "ADA", "state": "all"}, [4, 1, 6]),
        ({"who": " ben "}, [2]),
        ({"stream": "ops"}, [3]),
        ({"kind": "waiting"}, [2]),
        ({"state": "all", "since": "2026-10-05"}, [1, 2, 3, 5, 6]),
        ({"state": "all", "until": "2026-10-10"}, [4, 1]),
        # A time is the owner's day it falls on: 23:30 UTC on the 10th is the 11th in Budapest.
        ({"state": "all", "since": "2026-10-10T23:30+00:00", "until": "2026-10-18T12:00"}, [2, 3, 5, 6]),
    ],
)
def test_list_takes_who_as_a_part_stream_kind_state_and_the_day_each_opened(
    conn: sqlite3.Connection, keys: dict[str, Any], numbers: list[int]
) -> None:
    assert [i.number for i in ask(conn, "list", **keys).items] == numbers


def test_list_says_the_window_it_read_opened_days_in(conn: sqlite3.Connection) -> None:
    found = ask(conn, "list", since="2026-10-05", until="2026-10-10T12:00")
    assert (found.since, found.until) == (datetime(2026, 10, 5, tzinfo=TZ), datetime(2026, 10, 10, 12, tzinfo=TZ))
    assert (ask(conn, "list").since, ask(conn, "list").until) == (None, None)


def test_counts_come_from_the_ops_that_stand_never_from_the_log_s_words(tmp_path: Path) -> None:
    """A chase counts once per triage to chase that stands: an idle one counts, an undone one doesn't, one spaced
    otherwise than the writer writes counts, and a note quoting a chase doesn't. A due moves on a set of the due,
    a null one too, and on nothing else; snoozes and reopens count their ops; the median is of days to done."""
    spaced = item(9, 3, title="Spaced by hand")
    made = [
        item(1, 1), op("triage", 2, 1, verdict="chase"), op("triage", 3, 1, verdict="chase"),
        op("triage", 4, 1, verdict="me", note='he said "verdict":"chase" in the thread'),
        op("log", 5, 1, note="chased again, triage: chase"),
        op("set", 6, 1, fields={"notes": 'ask about "due": soon'}), op("set", 7, 1, fields={"due": "2026-10-30"}),
        op("set", 8, 1, fields={"due": None}),
        item(10, 2, title="Undone chase", opened="2026-10-12"), op("triage", 11, 10, verdict="chase"),
        op("undo", 12, 10, of=ulid(11)), op("snooze", 13, 10, until=date(2026, 10, 19)), op("reopen", 14, 10),
        op("done", 15, 10),
        spaced, op("done", 17, 9),
    ]  # fmt: skip
    write_ops(tmp_path, made)
    by_hand = [
        {"schema_version": 1, "id": ulid(16), "item": ulid(9), "op": "triage", "day": "2026-10-18", "machine": "desk",
         "by": "user", "via": "cli", "verdict": "chase"},
        {"schema_version": 1, "id": ulid(18), "item": ulid(9), "op": "set", "day": "2026-10-18", "machine": "desk",
         "by": "user", "via": "cli", "fields": {"due": "2026-11-02"}},
        {"schema_version": 1, "id": ulid(19), "item": ulid(9), "op": "triage", "day": "2026-10-18", "machine": "desk",
         "by": "user", "via": "cli", "verdict": "me", "note": "not a chase"},
    ]  # fmt: skip
    with (tmp_path / ops.rel_of(spaced)).open("a", encoding="utf-8", newline="\n") as f:
        # Spaced after a colon, and before one, as JSON allows and the writer never writes.
        spacing = (": ", " : ", ": ")
        f.writelines(json.dumps(line, separators=(", ", c)) + "\n" for line, c in zip(by_hand, spacing, strict=True))
    conn = fresh_tables()
    ledger_index.rebuild(conn, tmp_path)
    shown = [ask(conn, "show", ref=f"A-{n}").detail for n in (1, 2, 3)]
    assert [(d.chased, d.snoozes, d.due_moves, d.reopens) for d in shown if d] == [(2, 0, 2, 0), (0, 1, 0, 1),
                                                                                   (1, 0, 1, 0)]  # fmt: skip
    listed = ask(conn, "list", state="all").counts
    assert (listed.chased_items, listed.chased, listed.done, listed.median_days_to_done) == (2, 3, 2, 3.0)
    assert said(conn, "show", ref="A-2")[-7] == (
        "counts       done after 6 days; chased 0 times, snoozed 1 time, due moved 0 times, reopened 1 time"
    )
    assert "median 3 days to done" in said(conn, "list", state="all")[0]


def test_meetings_overlap_their_window_today_to_six_days_on_by_default(conn: sqlite3.Connection) -> None:
    assert said(conn, "meetings") == [
        "2 meetings, 2 points open; 10-20 to 10-26",
        "M-0001 10-21 09:00-09:30 Weekly pilot sync, Ada Example [pilot]; 2 open; items A-0001",
        "M-0005 10-22 14:00-15:00 Vendor call (declined)",
    ]
    assert [m.ref for m in ask(conn, "meetings", since="2026-10-01", until="2026-11-30").meetings] == [
        "M-0002", "M-0003", "M-0001", "M-0005", "M-0004",
    ]  # fmt: skip
    # Since alone reads the seven days on from it.
    assert [m.ref for m in ask(conn, "meetings", since="2026-11-15").meetings] == ["M-0004"]
    assert [m.ref for m in ask(conn, "meetings", until="2026-10-21").meetings] == ["M-0001"]


@pytest.mark.parametrize(
    ("since", "until", "found"),
    [
        # A window of minutes around a time is todo.py's meeting match.
        ("2026-10-21T08:45+02:00", "2026-10-21T09:15+02:00", ["M-0001"]),
        ("2026-10-21T09:29:59+02:00", "2026-10-21T09:40+02:00", ["M-0001"]),
        ("2026-10-21T09:30+02:00", "2026-10-21T09:40+02:00", []),
        ("2026-10-21T08:00+02:00", "2026-10-21T09:00+02:00", []),
        ("2026-10-21T08:00+02:00", "2026-10-21T09:00:30+02:00", ["M-0001"]),
        # Without an offset, a time is the owner's.
        ("2026-10-21T08:45", "2026-10-21T09:15", ["M-0001"]),
    ],
)
def test_a_window_of_times_finds_the_meetings_it_overlaps_to_the_second(
    conn: sqlite3.Connection, since: str, until: str, found: list[str]
) -> None:
    assert [m.ref for m in ask(conn, "meetings", since=since, until=until).meetings] == found


def test_a_window_says_its_times_and_a_meeting_of_no_length_at_its_start_is_in_it(tmp_path: Path) -> None:
    made = [
        meeting(1, subject="A moment", start="2026-10-21T09:00+02:00", end="2026-10-21T09:00+02:00"),
        meeting(2, subject="Overnight", start="2026-10-25T23:00+01:00", end="2026-10-26T01:00+01:00"),
        meeting(3, subject="Next year", start="2027-01-05T10:00+01:00", end="2027-01-05T11:00+01:00"),
    ]
    conn = tables_of(tmp_path, made)
    at_start = views.read(conn, Ask(view="meetings", since="2026-10-21T09:00", until="2026-10-21T10:00"), TZ, AT)
    assert views.lines(at_start, TZ) == [
        "1 meeting, 0 points open; 2026-10-21 09:00 to 2026-10-21 10:00",
        "M-0001 10-21 09:00-09:00 A moment",
    ]
    # A window given in another zone is read, and said, in the owner's.
    in_utc = views.read(conn, Ask(view="meetings", since="2026-10-21T07:00+00:00", until="2026-10-21T08:00Z"), TZ, AT)
    assert (views.lines(in_utc, TZ), in_utc.since) == (views.lines(at_start, TZ), at_start.since)
    assert in_utc.since is not None and in_utc.since.tzinfo == TZ
    later = views.read(conn, Ask(view="meetings", since="2026-10-25", until="2027-01-31"), TZ, AT)
    assert views.lines(later, TZ)[1:] == [
        "M-0002 10-25 23:00-10-26 01:00 Overnight",
        "M-0003 2027-01-05 10:00-11:00 Next year",
    ]
    assert views.lines(views.read(conn, Ask(view="show", ref="M-2"), TZ, AT), TZ)[2] == (
        "when         2026-10-25 23:00-2026-10-26 01:00"
    )


@pytest.mark.parametrize(
    ("keys", "why"),
    [
        ({"until": "2026-10-19"}, "until is before since: the window is empty"),
        ({"since": "2026-10-21T10:00", "until": "2026-10-21T10:00"}, "until is before since"),
        ({"since": "next week"}, "since is a date or a time"),
    ],
)
def test_a_window_that_holds_nothing_or_doesn_t_read_is_refused(
    conn: sqlite3.Connection, keys: dict[str, str], why: str
) -> None:
    with pytest.raises(DocketError, match=why):
        ask(conn, "meetings", **keys)


def test_suggest_gives_the_meeting_and_what_to_raise_in_it(conn: sqlite3.Connection) -> None:
    """An item already on one of its points is left out (DR-0634)."""
    assert said(conn, "suggest", ref="M-0001") == [
        "M-0001 10-21 09:00-09:30 Weekly pilot sync, Ada Example [pilot]; 2 open; items A-0001",
        "1 to raise",
        "A-0006 [reply] Ada Example: Confirm the venue (2d, due 10-23) [pilot]; on Ada Example, in the meeting",
    ]
    assert ask(conn, "suggest", ref=ulid(20)).items[0].why == "on Ada Example, in the meeting"
    assert said(conn, "suggest", ref="M-4")[1:] == ["0 to raise"]


# -- show -------------------------------------------------------------------------------------------------------


def test_show_gives_an_item_whole_with_its_counts_and_history(conn: sqlite3.Connection) -> None:
    found = ask(conn, "show", ref="A-3")
    (row,) = found.items
    assert row.title == LONG
    assert found.detail is not None
    assert (found.detail.kind, found.detail.description, found.detail.added_by) == (
        "item", f"{SHOWN} description", "claude",
    )  # fmt: skip
    assert views.lines(found, TZ)[:2] == [f"A-0003  {LONG}", f"id           {ulid(6)}"]
    assert f"description  {SHOWN} description" in views.lines(found, TZ)


def test_show_gives_a_meeting_whole_with_its_points_ids_answers_items_and_history(conn: sqlite3.Connection) -> None:
    by_number = ask(conn, "show", ref="m-1")
    assert ask(conn, "show", ref=ulid(20)) == by_number
    detail = by_number.detail
    assert detail is not None
    assert (detail.kind, detail.where, detail.attendees, detail.notes) == (
        "meeting", "Room 3", ["Ada Example", "Ben Example", f"{SHOWN} Person"], f"{SHOWN} meeting notes",
    )  # fmt: skip
    assert [(p.ref, p.id, p.answer) for p in by_number.points] == [
        ("M-0001#1", ulid(21), None), ("M-0001#2", ulid(22), f"{SHOWN} answer"), ("M-0001#3", ulid(24), None),
    ]  # fmt: skip
    assert views.lines(by_number, TZ) == [
        "M-0001  Weekly pilot sync",
        f"id           {ulid(20)}",
        "when         2026-10-21 09:00-09:30",
        "organiser    Ada Example",
        f"attendees    Ada Example, Ben Example, {SHOWN} Person",
        "where        Room 3",
        "stream       pilot",
        f"notes        {SHOWN} meeting notes",
        f"point        #1 open The pilot's dates -> A-0001 {ulid(21)}",
        f"point        #2 answered The budget; answer: {SHOWN} answer {ulid(22)}",
        f"point        #3 open Ben's numbers -> A-0002 (proposed) {ulid(24)}",
        "item         A-0001 [reply] Ada Example: Send Ada the dates (10d, due 10-18, 2d late) [pilot] @M-0001 <chase>",
        "history      2026-10-18        claude via cli  added",
        "             2026-10-18        claude via cli  point added: The pilot's dates",
        "             2026-10-18        claude via cli  point added: The budget",
        f"             2026-10-18        claude via cli  point answered, answer: {SHOWN} answer",
        "             2026-10-18        claude via cli  point proposed: Ben's numbers",
    ]
    archived = views.lines(ask(conn, "show", ref="M-3"), TZ)
    assert "state        archived" in archived


def test_show_gives_a_stream_with_its_open_items_and_their_counts(conn: sqlite3.Connection) -> None:
    by_name = ask(conn, "show", ref="pilot")
    assert ask(conn, "show", ref=ulid(40)) == by_name
    assert views.lines(by_name, TZ) == [
        "pilot  active",
        f"id           {ulid(40)}",
        "next         Book the room",
        "where        projects/pilot/",
        "deadline     2026-10-30",
        "updated      2026-10-18, added by claude",
        "items        2 open, 1 snoozed, 1 done, 1 dropped",
        "item         A-0001 [reply] Ada Example: Send Ada the dates (10d, due 10-18, 2d late) [pilot] @M-0001 <chase>",
        "item         A-0002 [waiting] Ben Example: Ben's numbers (2d, snoozed to 10-25) [pilot]",
        "item         A-0006 [reply] Ada Example: Confirm the venue (2d, due 10-23) [pilot]",
        "history      2026-10-18        claude via cli  added",
    ]
    assert views.lines(ask(conn, "show", ref="ops"), TZ)[:3] == [
        "ops  waiting", f"id           {ulid(41)}", "updated      2026-10-18, added by claude",
    ]  # fmt: skip


def test_only_show_says_notes_descriptions_outcomes_attendees_answers_and_history(conn: sqlite3.Connection) -> None:
    for view, keys in (("snapshot", {}), ("queue", {}), ("list", {"state": "all", "k": None}), ("meetings", {}),
                       ("suggest", {"ref": "M-1"})):  # fmt: skip
        found = ask(conn, view, **keys)
        assert SHOWN not in "\n".join(views.lines(found, TZ)), view
        assert SHOWN not in found.model_dump_json(), view
    for ref in ("A-2", "A-3", "A-4", "M-1"):
        assert SHOWN in "\n".join(said(conn, "show", ref=ref)), ref


# -- refs ---------------------------------------------------------------------------------------------------------


def test_a_number_that_names_nothing_alone_shows_the_id_as_a_batch_needs_it(tmp_path: Path) -> None:
    """Another clone's add claims A-0001 and M-0001 too and sorts first: both of each are named by their ids from then
    on, since a batch refuses the number (DR-0613); show by the number finds its holder and names the other."""
    made = [
        item(1, 1, title="Ours", meeting=ulid(3)), meeting(3, number=1, subject="Our sync"),
        replace(item(2, 1, title="Theirs"), id=ulid(0), item=ulid(0), machine="laptop"),
        replace(meeting(4, number=1, subject="Their sync"), id=ulid(9, ms=1), item=ulid(9, ms=1), machine="laptop"),
        point(5, 3, "Ours to raise", item=ulid(1)),
    ]  # fmt: skip
    conn = tables_of(tmp_path, made)
    found = views.read(conn, Ask(), TZ, AT)
    assert [(i.title, i.ref, i.number) for i in found.items] == [("Theirs", ulid(0), 1), ("Ours", ulid(1), None)]
    assert [(m.subject, m.ref) for m in found.meetings] == [("Their sync", ulid(9, ms=1)), ("Our sync", ulid(3))]
    assert found.items[1].meeting == ulid(3)
    assert found.points[0].ref == f"{ulid(3)}#1" and found.points[0].item == ulid(1)
    ours = views.lines(views.read(conn, Ask(view="show", ref=ulid(1)), TZ, AT), TZ)
    assert ours[:5] == ["no number  Ours", f"id           {ulid(1)}", "kind         reply", "state        open",
                        "number       A-0001 went to an earlier claim; the next write gives a new one"]  # fmt: skip
    theirs = views.lines(views.read(conn, Ask(view="show", ref="A-1"), TZ, AT), TZ)
    assert theirs[0] == "A-0001  Theirs"
    assert f"also held    A-0001 by {ulid(1)}, waiting for a new number" in theirs
    meetings = views.lines(views.read(conn, Ask(view="meetings", since="2026-10-19"), TZ, AT), TZ)
    assert meetings[1:] == [
        f"{ulid(9, ms=1)} 10-19 09:00-09:30 Their sync",
        f"{ulid(3)} 10-19 09:00-09:30 Our sync; 1 open; items {ulid(1)}",
    ]
    shown = views.lines(views.read(conn, Ask(view="show", ref="M-1"), TZ, AT), TZ)
    assert shown[:2] == ["M-0001  Their sync", f"id           {ulid(9, ms=1)}"]
    assert views.lines(views.read(conn, Ask(view="show", ref=ulid(3)), TZ, AT), TZ)[0] == "no number  Our sync"
    assert f"also held    M-0001 by {ulid(3)}, waiting for a new number" in shown


def test_a_meeting_number_none_holds_now_names_who_held_it(tmp_path: Path) -> None:
    made = [meeting(1, number=5), op("renumber", 2, 1, number=9), meeting(3, number=5)]
    conn = tables_of(tmp_path, made)
    with pytest.raises(DocketError) as refused:
        views.read(conn, Ask(view="show", ref="M-5"), TZ, AT)
    assert str(refused.value) == (
        f"M-0005 isn't held now; it was held by {ulid(1)} (now M-0009), {ulid(3)} (waiting for a new number); name "
        "the meeting by its id"
    )
    assert views.read(conn, Ask(view="show", ref="M-9"), TZ, AT).meetings[0].id == ulid(1)


@pytest.mark.parametrize(
    ("view", "ref", "why"),
    [
        ("show", "M-0099", "no meeting M-0099"),
        ("show", "M-0007", "M-0007 was undone"),
        ("show", ulid(50), f"{ulid(50)} was undone"),
        ("show", ulid(52), f"{ulid(52)} was undone"),
        ("show", "gone", "'gone' isn't an item, a meeting or a stream: name one by its A- or M- number, its id, or a "
                         "stream's name"),
        ("show", "A-0099", "no item A-0099"),
        ("show", ulid(60), f"no item {ulid(60)}"),
        ("suggest", "A-1", "suggest takes a meeting: its M- number or its id, not 'A-1'"),
        ("suggest", ulid(1), f"no meeting {ulid(1)}"),
        ("suggest", ulid(50), f"{ulid(50)} was undone"),
    ],
)  # fmt: skip
def test_show_and_suggest_refuse_what_names_nothing(tmp_path: Path, view: str, ref: str, why: str) -> None:
    made = [
        item(1, 1), meeting(50, number=7), op("undo", 51, 50, of=ulid(50)),
        op("stream", 52, fields={"name": "gone", "status": "active"}), op("undo", 53, 52, of=ulid(52)),
    ]  # fmt: skip
    with pytest.raises(DocketError) as refused:
        views.read(tables_of(tmp_path, made), Ask(view=view, ref=ref), TZ, AT)
    assert str(refused.value) == why


@pytest.mark.parametrize(
    ("keys", "why"),
    [
        ({"view": "queue", "who": "Ada"}, "queue takes nothing more, not who"),
        ({"view": "snapshot", "ref": "A-1"}, "snapshot takes since, until, not ref"),
        ({"view": "show", "ref": "A-1", "state": "all"}, "show takes ref, not state"),
        ({"view": "meetings", "k": 10}, "meetings takes since, until, not k"),
        ({"view": "everything"}, "view is one of snapshot, show, queue, list, meetings, suggest, not 'everything'"),
        ({"view": "list", "state": "late"}, "state is one of open, snoozed, done, dropped, all, not 'late'"),
        ({"view": "list", "kind": "ask"}, "kind is one of reply, decision, task, invite, waiting, not 'ask'"),
        ({"view": "list", "k": 0}, "k is 1 to 500, not 0"),
        ({"view": "list", "k": 501}, "k is 1 to 500, not 501"),
        ({"view": "show"}, "show needs ref, an A- or M- number, an id or a stream"),
        ({"view": "suggest", "ref": "  "}, "suggest needs ref, a meeting's M- number or id"),
    ],
)
def test_a_view_refuses_a_parameter_it_doesn_t_take_and_a_value_it_doesn_t_know(
    conn: sqlite3.Connection, keys: dict[str, Any], why: str
) -> None:
    with pytest.raises(DocketError) as refused:
        views.read(conn, Ask(**keys), TZ, AT)
    assert str(refused.value) == why


def test_the_tool_s_kinds_are_the_ledger_s() -> None:
    assert views.KINDS == ops.KINDS


def test_a_day_in_another_year_is_said_whole(tmp_path: Path) -> None:
    made = [
        item(1, 1, due="2027-01-04"),
        op("stream", 2, fields={"name": "pilot", "status": "active", "deadline": "2027-02-01"}),
    ]
    found = views.read(tables_of(tmp_path, made), Ask(), TZ, AT)
    lines = views.lines(found, TZ)
    assert lines[2] == f"A-0001 [reply] Ada Example: Send Ada the dates (2d, due 2027-01-04) [pilot] src {SOURCE}"
    assert lines[-1] == "pilot active; deadline 2027-02-01"


# -- the commands -------------------------------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Vault:
    v = empty_vault(tmp_path, make_config())
    monkeypatch.setenv("DOCKET_VAULT", str(v.root))
    monkeypatch.setattr(views, "now", lambda: AT)
    return v


@pytest.fixture(params=["memory", "index"])
def where(request: pytest.FixtureRequest, vault: Vault) -> str:
    if request.param == "index":
        rebuild(vault)
    return str(request.param)


COMMANDS = (
    ["snapshot"], ["queue"], ["list", "--state", "all"], ["meetings", "--since", "2026-10-01", "--until", "2026-11-30"],
    ["suggest", "M-1"], ["show", "A-1"], ["show", "M-1"], ["show", "pilot"],
)  # fmt: skip


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = main(["ledger", *args])
    out, err = capsys.readouterr()
    return code, out, err.strip()


def test_each_command_prints_its_view_s_lines_or_with_json_its_result(
    vault: Vault, where: str, capsys: pytest.CaptureFixture[str]
) -> None:
    write_ops(vault.root, seed())
    conn = fresh_tables()
    ledger_index.rebuild(conn, vault.root)
    for args in COMMANDS:
        view = args[0]
        options = dict(zip(args[1::2], args[2::2], strict=False))
        keys: dict[str, Any] = {"ref": args[1]} if view in ("show", "suggest") else {
            k.removeprefix("--"): v for k, v in options.items()
        }  # fmt: skip
        code, out, _ = run(capsys, *args)
        assert (code, out.splitlines()) == (0, said(conn, view, **keys)), args
        code, out, _ = run(capsys, *args, "--json")
        assert code == 0
        assert json.loads(out) == ask(conn, view, **keys, **({"k": None} if view == "list" else {})).model_dump(
            mode="json"
        ), args


def test_list_s_text_takes_k_and_its_json_gives_every_row(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    write_ops(vault.root, seed())
    code, out, _ = run(capsys, "list", "--state", "all", "-k", "1")
    assert code == 0 and out.splitlines()[1:] == [
        "the first 1",
        "A-0004 [decision] Ada Example: Pick the vendor (17d, done 10-18, archived) [pilot]",
    ]
    code, out, _ = run(capsys, "list", "--state", "all", "-k", "1", "--json")
    assert code == 0 and len(json.loads(out)["items"]) == json.loads(out)["counts"]["items"] == 6
    code, out, _ = run(capsys, "list", "--who", "ada", "--kind", "decision", "--stream", "pilot", "--state", "done",
                       "--since", "2026-09-01", "--until", "2026-10-31")  # fmt: skip
    assert (code, out.splitlines()[1:]) == (0, ["A-0004 [decision] Ada Example: Pick the vendor (17d, done 10-18, "
                                                "archived) [pilot]"])  # fmt: skip


def test_a_command_s_refusal_is_one_line_and_exit_2(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(capsys, "list", "--state", "late") == (2, "", "docket: state is one of open, snoozed, done, dropped, "
                                                            "all, not 'late'")  # fmt: skip
    assert run(capsys, "show", "M-0001") == (2, "", "docket: no meeting M-0001")


def test_the_commands_read_the_index_and_fold_no_op_file_whole(
    vault: Vault, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    rebuild(vault)
    write_ops(vault.root, seed())

    def whole(*_: object) -> None:
        raise AssertionError("a read folded every op file")

    monkeypatch.setattr(ledger_index, "load", whole)
    for args in COMMANDS:
        assert run(capsys, *args)[0] == 0, args


def test_no_read_quotes_a_record(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    """A record's text never shows in a read, whatever points at it: an item's source is its locator."""
    marker = "QUOKKA-MARKER-7741"
    record = make_record(7, text=f"The dates are fixed, {marker}, says the thread.")
    run_name = "2026-10-14T1000-test-box"
    path = vault.root / "raw" / "chat" / "2026" / "10" / f"{run_name}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(record.model_copy(update={"v": version(record), "run": run_name}).to_json() + "\n",
                    encoding="utf-8", newline="\n")  # fmt: skip
    vault.commit([], "Raw: a record")
    write_ops(vault.root, [
        item(1, 1, source=record.rid, meeting=ulid(2)), meeting(2, number=1, stream="pilot", organiser="Ada Example"),
        point(3, 2, "The dates", item=ulid(1)), op("stream", 4, fields={"name": "pilot", "status": "active"}),
        item(5, 2, title="Ask Ada about the thread", source=record.rid),
    ])  # fmt: skip
    rebuild(vault)
    assert marker in path.read_text(encoding="utf-8")
    outputs: list[str] = []
    for args in (*COMMANDS, ["meetings"], ["show", "A-2"]):
        for extra in ((), ("--json",)):
            code, out, _ = run(capsys, *args, *extra)
            assert code == 0, args
            outputs.append(out)
    assert not [out for out in outputs if marker in out]
    assert record.rid in outputs[0]
