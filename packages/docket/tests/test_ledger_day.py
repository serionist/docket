"""The owner's day (#207, DR-0670 to DR-0672): todo.py's dashboard from one snapshot read, each section in its order
with its cap; an item listed once among overdue, replies, decisions and tasks, invites and waiting, while the queue
and a meeting repeat it; today's meetings not yet ended and the next working day's, cancelled and declined ones out;
snoozes whose day has come; two machines' sync marks; a status file a hand edit holds; the cuts in their order; the
counts line; `docket ledger day` and `GET /api/day`. Invented people only."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from docket_testkit import empty_vault, folder_vault, git, make_config
from ledger_kit import ADD_FIELDS, fresh_tables, meeting, op, point, ulid, write_ops
from starlette.testclient import TestClient

from docket.cli import main
from docket.index import ledger as ledger_index
from docket.index import rebuild
from docket.ledger import day, status, store, views
from docket.ledger.day import Day, DayLine, StatusFile, SyncMark
from docket.ledger.ops import Op
from docket.ledger.views import Ask, Counts, ItemRow, LedgerRead, MeetingRow, PointRow, StreamRow
from docket.service.app import create_app
from docket.service.state import Settings
from docket.vault import cursors
from docket.vault.config import CONFIG_FILE, RenderedFile, SourceConfig, VaultConfig
from docket.vault.vault import Vault

TZ = ZoneInfo("Europe/Budapest")
# Tuesday 20 October, 10:00 in Budapest.
AT = datetime(2026, 10, 20, 8, 0, tzinfo=UTC)
TOKEN = "t0ken-for-tests-only"  # noqa: S105 - an invented token
BEARER = {"Authorization": f"Bearer {TOKEN}"}


def item(n: int, number: int, **fields: Any) -> Op:
    return op("add", n, number=number, fields={**ADD_FIELDS, **fields})


def slot(day_of: str, start: str, end: str) -> dict[str, str]:
    return {"start": f"2026-10-{day_of}T{start}+02:00", "end": f"2026-10-{day_of}T{end}+02:00"}


def seed() -> list[Op]:
    """A Tuesday's ledger: an overdue reply to discuss that is on today's meeting too, a snooze whose day has come and
    one still asleep, a task due today, a wait long and one short, a task due this week and a decision with no due, an
    invite, a reply to chase, a closed one; today's meeting still to come and one ended, a cancelled and a declined
    one, the next working day's, one after it and an old one with an open point; three streams, one closed."""
    return [
        item(1, 1, opened="2026-10-10", due="2026-10-18", meeting=ulid(40)),
        op("triage", 2, 1, verdict="discuss"),
        item(3, 2, kind="waiting", title="Ben's numbers", who="Ben Example", opened="2026-10-10"),
        item(4, 3, kind="task", title="Book the room", who="Cleo Example", stream="ops", due="2026-10-22"),
        op("triage", 5, 4, verdict="claude"),
        item(6, 4, kind="decision", title="Pick the vendor", opened="2026-10-01"),
        item(7, 5, kind="invite", title="Answer the offsite invite", who="Dan Example"),
        item(8, 6, title="Confirm the venue", opened="2026-10-12"),
        op("snooze", 9, 8, until=date(2026, 10, 19)),
        item(10, 7, title="Thank the hosts"),
        op("snooze", 11, 10, until=date(2026, 10, 25)),
        item(12, 8, kind="waiting", title="Eve's sign-off", who="Eve Example", opened="2026-10-19"),
        item(13, 9, kind="task", title="Send the agenda", due="2026-10-20"),
        op("triage", 14, 13, verdict="me"),
        item(15, 10, title="Answer the survey", who="Dan Example"),
        op("done", 16, 15),
        item(17, 11, title="Chase the quote", who="Ben Example", opened="2026-10-15"),
        op("triage", 18, 17, verdict="chase"),
        meeting(40, number=1, subject="Pilot sync", organiser="Ada Example", stream="pilot",
                **slot("20", "14:00", "14:30")),
        point(41, 40, "The pilot's dates", item=ulid(1)),
        point(42, 40, "Ben's numbers", proposed=True),
        meeting(43, number=2, subject="Standup", **slot("20", "09:00", "09:15")),
        meeting(44, number=3, subject="Called off", response="cancelled", **slot("20", "16:00", "17:00")),
        meeting(45, number=4, subject="Vendor call", response="declined", **slot("21", "09:00", "10:00")),
        meeting(46, number=5, subject="Budget review", organiser="Cleo Example", **slot("21", "11:00", "12:00")),
        meeting(47, number=6, subject="Thursday planning", **slot("22", "10:00", "11:00")),
        meeting(48, number=7, subject="Old review", **slot("01", "10:00", "11:00")),
        point(49, 48, "What we left open"),
        op("stream", 50, fields={"name": "pilot", "status": "active", "where": "Waiting on the venue",
                                 "next": "Book the room", "deadline": "2026-10-30"}),
        op("stream", 51, fields={"name": "ops", "status": "waiting", "where": "The quote is out"}),
        op("stream", 52, fields={"name": "retired", "status": "closed"}),
    ]  # fmt: skip


def tables(root: Path, made: list[Op]) -> sqlite3.Connection:
    write_ops(root, made)
    conn = fresh_tables()
    ledger_index.rebuild(conn, root)
    return conn


def built(conn: sqlite3.Connection, at: datetime = AT, **keys: Any) -> Day:
    today = at.astimezone(TZ).date()
    ask = Ask(view="snapshot", since=today.isoformat(), until=day.next_working_day(today).isoformat())
    return day.build(views.read(conn, ask, TZ, at), at, **keys)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = tables(tmp_path, seed())
    yield c
    c.close()


def texts(found: list[DayLine]) -> list[str]:
    return [line.text for line in found]


SYNCS = [
    SyncMark(source="calls", machine="laptop", mark=AT - timedelta(days=2), due=True),
    SyncMark(source="chat", machine="test-box", mark=AT - timedelta(hours=3), due=False),
    SyncMark(source="mail", machine=None, mark=None, due=True),
]
EDITED = StatusFile(
    path="projects/pilot/STATUS.md",
    what='a hand edit holds its frontmatter: next "Call Ada" in the file, "Book the room" in the ledger; set the '
    "stream to the file's, or put the ledger's back",
    owner=True,
)
STALE = StatusFile(path="projects/ops/STATUS.md", what="stale; docket render", owner=False)
FULL = [
    "day Tue 10-20; sync calls 2d, chat 3h, mail never; due: calls, mail (ask Claude to sync them)",
    f"status: {EDITED.path}: {EDITED.what}",
    "status: projects/ops/STATUS.md: stale; docket render",
    "2 calendar record(s) wait for their meetings; `docket ledger calendar`",
    "queue: 5 untriaged; discuss 1, claude 1, me 1, chase 1",
    "discuss",
    "  A-0001 [reply] Ada Example: Send Ada the dates (10d, due 10-18, 2d late) [pilot] @M-0001 <discuss>",
    "claude",
    "  A-0003 [task] Cleo Example: Book the room (2d, due 10-22) [ops] <claude>",
    "me: A-0009",
    "chase: A-0011",
    "meetings today 10-20",
    "M-0001 10-20 14:00-14:30 Pilot sync, Ada Example [pilot]; 2 open; items A-0001",
    "  * The pilot's dates",
    "  ? Ben's numbers",
    "  A-0001 Send Ada the dates (due 10-18)",
    "meetings Wed 10-21",
    "M-0005 10-21 11:00-12:00 Budget review, Cleo Example",
    "overdue or due today (3)",
    "A-0001 [reply] Ada Example: Send Ada the dates (10d, due 10-18, 2d late) [pilot] @M-0001 <discuss>",
    "A-0006 [reply] Ada Example: Confirm the venue (8d, snooze ended 10-19) [pilot]",
    "A-0009 [task] Ada Example: Send the agenda (2d, due 10-20) [pilot] <me>",
    "replies owed (1)",
    "A-0011 [reply] Ben Example: Chase the quote (5d) [pilot] <chase>",
    "decisions and tasks due within 7 days (1)",
    "A-0003 [task] Cleo Example: Book the room (2d, due 10-22) [ops] <claude>",
    "  1 more not due this week: the ledger tool's list, kind task or decision",
    "invites (1)",
    "A-0005 [invite] Dan Example: Answer the offsite invite (2d) [pilot]",
    "waiting on others over 3 days (1)",
    "A-0002 [waiting] Ben Example: Ben's numbers (10d) [pilot]",
    "streams (2)",
    "ops [waiting]: The quote is out",
    "pilot: Waiting on the venue -> Book the room (10-30)",
    day.LAST,
]
# Each cut's line, in the cut order; the seventh leaves each meeting's line as it was, so it shows by what goes.
CUTS = [
    "streams (2): the ledger tool's snapshot",
    "waiting on others over 3 days (1): the ledger tool's list, kind waiting",
    "invites (1): the ledger tool's list, kind invite",
    "decisions and tasks due within 7 days (1), 1 more not due this week: the ledger tool's list, kind task or "
    "decision",
    "replies owed (1): the ledger tool's list, kind reply",
    "queue: 5 untriaged; discuss 1, claude 1, me 1, chase 1; the ledger tool's queue lists them",
    "  * The pilot's dates",
    "meetings Wed 10-21: 1 meeting; the ledger tool's meetings lists them",
]


def full_day(conn: sqlite3.Connection) -> Day:
    return built(conn, syncs=SYNCS, files=[EDITED, STALE], notes=[FULL[3]])


# -- the sections -----------------------------------------------------------------------------------------------------


def test_the_day_says_todo_py_s_sections_in_its_order(conn: sqlite3.Connection) -> None:
    """The sync line; the status files and the calendar's backlog; the queue; today's meetings still to come and the
    next working day's, an ended, a cancelled, a declined, a later and an old one left out; overdue with a snooze whose
    day has come; replies; decisions and tasks this week and how many are not; invites; waits over three days; the
    streams not closed; the last line. A snooze still asleep, a short wait and a closed item show nowhere."""
    found = day.lines(full_day(conn))
    assert texts(found) == FULL
    # The owner sees a due sync and a hand-edited status file; Claude every line.
    assert [line.text for line in found if line.owner] == FULL[:2]
    for gone in ("Thank the hosts", "Eve's sign-off", "Answer the survey", "Standup", "Called off", "Vendor call",
                 "Thursday planning", "Old review", "retired"):  # fmt: skip
        assert gone not in "\n".join(texts(found)), gone


def test_an_item_is_listed_once_among_the_sections_but_the_queue_and_a_meeting_repeat_it(
    conn: sqlite3.Connection,
) -> None:
    found = full_day(conn)
    listed = {s.name: [i.ref for i in s.items] for s in found.sections}
    assert listed == {"overdue": ["A-0001", "A-0006", "A-0009"], "replies": ["A-0011"], "decisions": ["A-0003"],
                      "invites": ["A-0005"], "waiting": ["A-0002"]}  # fmt: skip
    assert found.later == 1
    # The overdue reply to discuss is in the queue and on today's meeting too.
    assert [i.ref for i in found.queue if i.triage == "discuss"] == ["A-0001"]
    assert [i.ref for i in found.meetings[0].meetings[0].items] == ["A-0001"]


def test_an_item_past_a_section_s_cap_stays_in_its_count_and_shows_nowhere_further_down(tmp_path: Path) -> None:
    made = [item(n, n, title=f"Reply {n:02d}", due="2026-10-19", opened="2026-10-18") for n in range(1, 13)]
    with tables(tmp_path, made) as c:
        found = built(c)
    said = texts(day.lines(found))
    assert said[said.index("overdue or due today (12)") + 11] == "  +2 more: the ledger tool's snapshot"
    assert [s.name for s in found.sections if s.items] == ["overdue"]
    assert not [line for line in said if line.startswith("replies owed")]


def test_each_section_keeps_its_cap(tmp_path: Path) -> None:
    """Six rows a verdict, six meetings a day with four points and three items each, ten replies, eight decisions and
    tasks, five invites and six waits, each saying how many more and where."""
    made: list[Op] = []
    count = 0

    def add(n: int, **fields: Any) -> list[int]:
        nonlocal count
        first = count + 1
        count += n
        made.extend(item(k, k, opened="2026-10-01", **fields) for k in range(first, count + 1))
        return list(range(first, count + 1))

    made.extend(op("triage", 500 + k, k, verdict="discuss") for k in add(7))
    add(11)
    add(9, kind="task", due="2026-10-24")
    add(6, kind="invite")
    add(7, kind="waiting")
    add(4, meeting=ulid(900))
    made.extend(meeting(900 + 10 * k, number=k + 1, subject=f"Review {k}", **slot("20", f"1{k}:00", f"1{k}:30"))
                for k in range(7))  # fmt: skip
    made.extend(point(980 + k, 900, f"Point {k}") for k in range(5))
    with tables(tmp_path, made) as c:
        said = texts(day.lines(built(c), 1000))
    for line in (
        "  +1 more: the ledger tool's queue",
        "+1 more: the ledger tool's meetings",
        "  +1 more points: the ledger tool's show M-0001",
        "  +1 more items: the ledger tool's show M-0001",
        "  +12 more: the ledger tool's list, kind reply",
        "  +1 more: the ledger tool's list, kind task or decision",
        "  +1 more: the ledger tool's list, kind invite",
        "  +1 more: the ledger tool's list, kind waiting",
    ):
        assert line in said, line
    assert len([line for line in said if line.startswith("M-")]) == 6
    assert len([line for line in said if line.startswith("  * Point")]) == 4


def test_the_next_working_day_after_a_friday_is_monday(tmp_path: Path) -> None:
    friday = datetime(2026, 10, 23, 7, 0, tzinfo=UTC)
    made = [
        meeting(1, number=1, subject="Friday retro", **slot("23", "15:00", "16:00")),
        meeting(2, number=2, subject="Saturday hike", **slot("24", "09:00", "12:00")),
        # Summer time is over by Monday.
        meeting(3, number=3, subject="Monday standup", start="2026-10-26T09:00+01:00", end="2026-10-26T09:15+01:00"),
    ]
    with tables(tmp_path, made) as c:
        found = built(c, friday)
    after = [day.next_working_day(date(2026, 10, d)) for d in (23, 24, 25, 26)]
    assert after == [date(2026, 10, 26)] * 3 + [date(2026, 10, 27)]
    assert texts(day.lines(found)) == [
        "day Fri 10-23",
        "meetings today 10-23",
        "M-0001 10-23 15:00-16:00 Friday retro",
        "meetings Mon 10-26",
        "M-0003 10-26 09:00-09:15 Monday standup",
        day.LAST,
    ]
    assert day.counts(found) == "day: 0 overdue, 0 due today, 1 meeting today, 0 untriaged"


def test_a_stream_without_where_or_next_and_a_deadline_in_another_year(tmp_path: Path) -> None:
    made = [
        op("stream", 1, fields={"name": "bare", "status": "active"}),
        op("stream", 2, fields={"name": "far", "status": "active", "next": "Plan it", "deadline": "2027-02-01"}),
    ]
    with tables(tmp_path, made) as c:
        said = texts(day.lines(built(c)))
    assert said[-3:] == ["bare", "far -> Plan it (2027-02-01)", day.LAST]


# -- the cuts ---------------------------------------------------------------------------------------------------------


def test_past_its_room_the_day_is_cut_whole_from_the_streams_up_then_filled_back_to_the_room(
    conn: sqlite3.Connection,
) -> None:
    """Room by room, from the whole day down to none: never past the room unless what is never cut passes it, and
    full to it within a line. One line short, the streams are cut to their count and can't take a row back."""
    found = full_day(conn)
    least = len(day.lines(found, 0))
    for room in range(len(FULL), -1, -1):
        said = texts(day.lines(found, room))
        assert len(said) <= max(room, least), room
        assert len(said) >= min(room, len(FULL)) - 1 or len(said) == least, room
    assert texts(day.lines(found, len(FULL))) == FULL
    assert texts(day.lines(found, len(FULL) - 1)) == [*FULL[:-4], CUTS[0], day.LAST]
    assert texts(day.lines(found, len(FULL) - 3)) == [*FULL[:29], CUTS[1], CUTS[0], day.LAST]
    # In 20 lines every cut but the next day's is made; the replies' row fits back, today's points and the queue's rows
    # don't.
    assert texts(day.lines(found, 20)) == [
        *FULL[:4], CUTS[5], *FULL[11:13], *FULL[16:24], CUTS[3], CUTS[2], CUTS[1], CUTS[0], day.LAST
    ]  # fmt: skip


def test_what_is_never_cut_passes_the_room_only_on_an_extreme_day(conn: sqlite3.Connection) -> None:
    said = texts(day.lines(full_day(conn), 0))
    assert said == [
        *FULL[:4],
        CUTS[5],
        *FULL[11:13],
        CUTS[7],
        *FULL[18:22],
        CUTS[4],
        CUTS[3],
        CUTS[2],
        CUTS[1],
        CUTS[0],
        day.LAST,
    ]


def busy(**sizes: int) -> Day:
    """A day built straight from rows, as the code review's probe built it: `sizes` says how many of each."""
    today, at = date(2026, 10, 20), datetime(2026, 10, 20, 7, 0, tzinfo=UTC)
    made: list[ItemRow] = []

    def row(kind: str, *, due: date | None = None, triage: str | None = None, days: int = 5) -> ItemRow:
        n = len(made) + 1
        late = (today - due).days if due is not None and due < today else None
        return ItemRow(ref=f"A-{n:04d}", id=f"I{n:025d}", number=n, kind=kind, title=f"Invented title {n}",
                       who="Ada Example", stream="pilot", source="x", meeting=None, opened=today - timedelta(days=days),
                       due=due, state="open", until=None, closed=None, triage=triage, archived=False, days=days,
                       late=late)  # fmt: skip

    # Each kind of row: its kind, how many, its due, its verdict and how many days it has been open.
    kinds: list[tuple[str, int, date | None, str | None, int]] = [
        ("task", sizes["overdue"], today - timedelta(days=2), None, 5),
        ("task", sizes["untriaged"], None, None, 5),
        *(("task", sizes[verdict], None, verdict, 5) for verdict in ("discuss", "claude", "me", "chase")),
        ("reply", sizes["replies"], None, None, 5),
        ("decision", sizes["decisions"], today + timedelta(days=3), None, 5),
        ("invite", sizes["invites"], None, None, 5),
        ("waiting", sizes["waiting"], None, None, 9),
    ]
    for kind, count, due, triage, days in kinds:
        for _ in range(count):
            made.append(row(kind, due=due, triage=triage, days=days))
    meetings: list[MeetingRow] = []
    points: list[PointRow] = []
    for k, (on, count) in enumerate(((today, sizes["today"]), (today + timedelta(days=1), sizes["tomorrow"]))):
        for j in range(count):
            start = datetime(on.year, on.month, on.day, 9 + j, tzinfo=UTC)
            ref = f"M-{k}{j:03d}"
            linked = [i.ref for i in made[j * sizes["items"] : (j + 1) * sizes["items"]]]
            meetings.append(MeetingRow(ref=ref, id=f"M{k}{j}", number=None, subject=f"Invented meeting {k}{j}",
                                       start=start, end=start + timedelta(minutes=30), organiser="Ada Example",
                                       stream=None, response=None, archived=False, points_open=sizes["points"],
                                       items=linked))  # fmt: skip
            points += [PointRow(ref=f"{ref}#{p}", id=f"P{k}{j}{p}", meeting=ref, n=p, state="open",
                                text=f"Invented point {p}", item=None, proposed=False)
                       for p in range(1, sizes["points"] + 1)]  # fmt: skip
    streams = [StreamRow(ref=f"s{k}", id=f"S{k}", name=f"s{k}", status="active", next="Next step", deadline=None,
                         where="Somewhere", updated=today) for k in range(sizes["streams"])]  # fmt: skip
    found = LedgerRead(view="snapshot", today=today, counts=Counts(), items=made, meetings=meetings, points=points,
                       streams=streams)  # fmt: skip
    return day.build(found, at)


BUSY = {"overdue": 40, "untriaged": 258, "discuss": 5, "claude": 8, "me": 3, "chase": 4, "replies": 15,
        "decisions": 12, "invites": 3, "waiting": 7, "today": 6, "tomorrow": 4, "points": 2, "items": 1,
        "streams": 7}  # fmt: skip


def test_a_busy_day_fills_its_room_and_keeps_today_s_first_meetings_points() -> None:
    """Cut whole, the busy day took 32 of 42 lines, every point and item of today's meetings gone. Given back, the
    last cut first, today's meetings take theirs back meeting by meeting, then the queue its rows."""
    said = texts(day.lines(busy(**BUSY), 42))
    assert len(said) in (41, 42)
    first = said.index("meetings today 10-20")
    assert said[first + 1 : first + 5] == [
        "M-0000 10-20 09:00-09:30 Invented meeting 00, Ada Example; 2 open; items A-0001",
        "  * Invented point 1",
        "  * Invented point 2",
        "  A-0001 Invented title 1 (due 10-18)",
    ]


def test_a_day_one_line_over_gives_back_what_fits() -> None:
    edge = busy(**{**BUSY, "overdue": 4, "untriaged": 10, "discuss": 3, "claude": 3, "me": 1, "chase": 1,
                   "replies": 10, "decisions": 0, "invites": 0, "waiting": 0, "today": 3, "tomorrow": 2,
                   "streams": 0})  # fmt: skip
    whole = texts(day.lines(edge, 1000))
    said = texts(day.lines(edge, len(whole) - 1))
    assert len(said) in (len(whole) - 2, len(whole) - 1)
    assert "replies owed (10)" in said


# -- the counts line and the sync line --------------------------------------------------------------------------------


def test_the_counts_line_says_counts_and_source_names_only(conn: sqlite3.Connection) -> None:
    assert day.counts(full_day(conn)) == (
        "day: 1 overdue, 1 due today, 1 meeting today, 5 untriaged; sync due: calls, mail"
    )
    assert day.counts(built(conn)) == "day: 1 overdue, 1 due today, 1 meeting today, 5 untriaged"
    why = "state/cursors-test-box.yaml: line 1 isn't valid YAML"
    broken = built(conn, sync_problem=why)
    assert day.counts(broken) == f"day: 1 overdue, 1 due today, 1 meeting today, 5 untriaged; sync: {why}"
    assert day.lines(broken)[0] == DayLine(text=f"day Tue 10-20; sync: {why}", owner=True)


def test_a_sync_line_with_nothing_due_is_claude_s_alone(conn: sqlite3.Connection) -> None:
    fresh = [SyncMark(source="chat", machine="test-box", mark=AT - timedelta(minutes=5), due=False)]
    assert day.lines(built(conn, syncs=fresh))[0] == DayLine(text="day Tue 10-20; sync chat 5m")


def test_how_long_ago_a_sync_reached() -> None:
    said = [day.ago(AT - d, AT) for d in (timedelta(minutes=59), timedelta(hours=47), timedelta(hours=49))]
    assert said == ["59m", "47h", "2d"]
    assert (day.ago(None, AT), day.ago(AT + timedelta(minutes=5), AT)) == ("never", "0m")


# -- from the vault ---------------------------------------------------------------------------------------------------


def configured(vault: Vault, **update: Any) -> Vault:
    """vault.yaml with these keys, committed: what it widens counts only once committed (DR-0521)."""
    path = vault.root / CONFIG_FILE
    config = VaultConfig.load(path).model_copy(update=update)
    path.write_text(config.dump(), encoding="utf-8", newline="\n")
    git(vault.home.top, "commit", "-qm", "Configure", "--", f"{vault.home.prefix}{CONFIG_FILE}")
    return Vault.open(vault.root)


def write_cursor(vault: Vault, machine: str, found: cursors.Cursors) -> None:
    path = vault.root / cursors.STATE / f"cursors-{machine}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cursors.dump(found), encoding="utf-8")


LIVE = {
    "calls": SourceConfig(tools=["list_calls"], sensitivity="mixed"),
    "chat": SourceConfig(tools=["list_messages"]),
    "mail": SourceConfig(tools=["list_mail"]),
    "memo": SourceConfig(tools=["list_memos"]),
    "diary": SourceConfig(tools=["list_entries"], sensitivity="private"),
    "paused": SourceConfig(tools=["list_items"], enabled=False),
    "files": SourceConfig(),
}


def test_the_syncs_are_each_live_source_s_newest_mark_of_any_machine(tmp_path: Path) -> None:
    """This machine's mark and another's; a mark ahead of the clock left out; a source with a run open never due;
    a private, a paused and a source with no tools left out; another machine's file that doesn't read too."""
    vault = configured(empty_vault(tmp_path, make_config()), sources=LIVE)
    write_cursor(vault, "test-box", {"chat": cursors.Cursor(mark=AT - timedelta(hours=3)),
                                     "calls": cursors.Cursor(mark=AT - timedelta(days=3))})  # fmt: skip
    write_cursor(vault, "laptop", {"calls": cursors.Cursor(mark=AT - timedelta(days=2)),
                                   "mail": cursors.Cursor(mark=AT + timedelta(hours=1))})  # fmt: skip
    (vault.root / cursors.STATE / "cursors-broken-box.yaml").write_text("nonsense: [\n", encoding="utf-8")
    syncs, problem = day.sync_marks(vault, AT, running=["memo"])
    assert problem is None
    assert syncs == [
        SyncMark(source="calls", machine="laptop", mark=AT - timedelta(days=2), due=True),
        SyncMark(source="chat", machine="test-box", mark=AT - timedelta(hours=3), due=False),
        SyncMark(source="mail", machine=None, mark=None, due=True),
        SyncMark(source="memo", machine=None, mark=None, due=False),
    ]
    (vault.root / cursors.STATE / "cursors-test-box.yaml").write_text("sources: [\n", encoding="utf-8")
    none, why = day.sync_marks(vault, AT)
    assert none == []
    assert why is not None and why.startswith("state/cursors-test-box.yaml: line ")
    assert day.sync_marks(configured(vault, sources={"files": SourceConfig()}), AT) == ([], None)


def status_vault(tmp_path: Path) -> Vault:
    """A folder vault whose vault.yaml renders the pilot's and the ops stream's STATUS.md, each folder there."""
    made = folder_vault(tmp_path)
    for name in ("pilot", "ops"):
        (made.home.top / "projects" / name).mkdir(parents=True)
    entries = [RenderedFile(kind="status", stream=name, path=f"projects/{name}/STATUS.md") for name in ("pilot", "ops")]
    return configured(made, rendered=entries)


def test_read_takes_the_day_from_the_vault_with_its_status_files_and_calendar_backlog(tmp_path: Path) -> None:
    """A hand edit holds the pilot's file: the owner's line. The ops file is Docket's but lags: Claude's alone. Read
    from the op files folded in memory and from the index alike."""
    vault = status_vault(tmp_path)
    write_ops(vault.root, seed())
    top = vault.home.top
    edited = "---\nstream: pilot\nstatus: active\nwhere: Waiting on the venue\nnext: Call Ada\ndeadline: 2026-10-30\n"
    (top / "projects" / "pilot" / "STATUS.md").write_text(edited + "---\n\nNotes.\n", encoding="utf-8", newline="\n")
    old = status.Fields("ops", "active", None, None, None, date(2026, 10, 1))
    (top / "projects" / "ops" / "STATUS.md").write_text(status.frontmatter(old), encoding="utf-8", newline="\n")
    store.note_calendar_pending(vault, ["calendar:evt-1", "calendar:evt-2"])
    for where in ("memory", "index"):
        if where == "index":
            rebuild(vault)
        found = day.read(vault, AT)
        assert found.status == [EDITED, STALE]
        assert texts(day.lines(found)) == ["day Tue 10-20", *FULL[1:]]
    assert day.repo_of(vault) == top.resolve()


# -- the command and the route ----------------------------------------------------------------------------------------


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Vault:
    vault = empty_vault(tmp_path, make_config())
    write_ops(vault.root, seed())
    monkeypatch.setattr(views, "now", lambda: AT)
    return vault


def test_docket_ledger_day_prints_the_day_or_its_json(seeded: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["ledger", "day", "--vault", str(seeded.root)]) == 0
    assert capsys.readouterr().out.splitlines() == ["day Tue 10-20", *FULL[4:]]
    assert main(["ledger", "day", "--json", "--vault", str(seeded.root)]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown == json.loads(day.read(seeded, AT).model_dump_json())
    assert [s["name"] for s in shown["streams"]] == ["ops", "pilot"]


def client_for(vault: Path | None) -> TestClient:
    app = create_app(Settings(port=8741, vault=vault, token=lambda: TOKEN, store="the system keychain"))
    return TestClient(app, base_url="http://127.0.0.1:8741")


def test_the_route_answers_the_day_its_counts_and_the_vault_s_repo(seeded: Vault) -> None:
    """A vault in no repo is its own repo (DR-0671)."""
    with client_for(seeded.root) as client:
        found = client.get("/api/day", params={"room": 20}, headers=BEARER)
        whole = client.get("/api/day", headers=BEARER).json()
        negative = client.get("/api/day", params={"room": -1}, headers=BEARER).status_code
    assert found.status_code == 200, found.text
    body = found.json()
    assert body["repo"] == str(seeded.root.resolve())
    assert body["counts"] == "day: 1 overdue, 1 due today, 1 meeting today, 5 untriaged"
    assert [line["text"] for line in body["lines"]] == texts(day.lines(day.read(seeded, AT), 20))
    assert len(body["lines"]) <= 20
    assert [line["text"] for line in whole["lines"]] == ["day Tue 10-20", *FULL[4:]]
    assert negative == 422


def test_the_route_says_why_when_it_can_t_read_the_vault() -> None:
    with client_for(None) as client:
        refused = client.get("/api/day", headers=BEARER)
    assert refused.status_code == 400
    assert refused.json() == {"error": "the service has no vault; run `docket service install --vault <folder>`"}
