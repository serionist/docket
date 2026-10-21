"""`docket sync reconcile` (DR-0760): the sync's batch drafted from its blocks by fixed rules, each row of the plan's
table sure and unsure, a thread match held to the same who and side, merges only of look-alike asks, every merge
reported, and the draft, settled, written by `docket ledger batch` (DR-0724's tool is in test_service_mcp.py).
Invented people and data only: the pilot's ledger, its channel, Cleo's mail, a team chat, three recordings and the
calendar."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from docket_testkit import copy_vault, empty_vault, make_config, make_record, template_with
from ledger_kit import meeting, op, point_set, ulid, write_ops
from mcp import Client
from mcp.types import CallToolResult, TextContent
from recording_kit import doc

from docket import recall
from docket.cli import main
from docket.errors import DocketError
from docket.index import build as index_build
from docket.ledger import store, views
from docket.service.state import Service, Settings
from docket.service.tools import create_mcp
from docket.sync import reconcile, start
from docket.vault.config import SourceConfig
from docket.vault.records import RawRecord
from docket.vault.vault import Vault

# Tuesday 20 October 2026, 09:15 in Budapest: the prep week runs to Tuesday the 27th.
NOW = datetime(2026, 10, 20, 7, 15, tzinfo=UTC)
POINT = "01M4QJ9FZ000X93GNMW1YNY5N8"
ANSWERED = ulid(31)
BEN_ASK = "slack:C0PILOT/1791100000.000100"
SENT = "slack:C0PILOT/1791100500.000200"
BEN_AGAIN = "slack:C0PILOT/1791100600.000100"
BEN_LATER = "slack:C0PILOT/1791100650.000100"
DAN_ASK = "slack:C0PILOT/1791101200.000100"
ROOM = "slack:C0PILOT/1791100800.000300"
EVE = "slack:C0PILOT/1791100900.000100"
OLD = "slack:C0PILOT/1791000000.000100"
TWO_ASKS = "slack:C0PILOT/1791101500.000100"
QUOTE = "outlook:AAMkADquote1"
QUOTE_AGAIN = "outlook:AAMkADquote2"
PLAN_MAIL = "outlook:AAMkADplan1"
INVITE_MAIL = "outlook:AAMkADinvite1"
CHAT = "teams:chat-pilot/1791100700000"
CHAT_AGAIN = "teams:chat-pilot/1791101100000"
WEEKLY = "plaud:rec-0101"
BETWEEN = "plaud:rec-0102"
FOCUSED = "plaud:rec-0103"
KICKOFF = "calendar:kickoff-1028"
SERIES = "calendar:sensor-sync-1"
UNTITLED = "calendar:untitled-1"
OFFSITE = "calendar:offsite-1"
SOURCES = ("calendar", "outlook", "plaud", "slack", "teams")
NOTE = "recording-note:2026-10-19-pilot-weekly"


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """A time in October 2026 in UTC: Budapest is two hours ahead until the 25th."""
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def records() -> dict[str, list[RawRecord]]:
    """Each source's records: a Slack thread is its first message's, a mail's its conversation, a chat's the chat, a
    recording's the recording, as the reference mappers write them."""

    def slack(rid: str, when: datetime, thread: str | None = None) -> RawRecord:
        return make_record(rid=rid, source="slack", kind="message", container="slack:C0PILOT", thread=thread or rid,
                           ts=when)  # fmt: skip

    def mail(rid: str, when: datetime, thread: str, title: str) -> RawRecord:
        return make_record(rid=rid, source="outlook", kind="mail", container=None, thread=thread, ts=when, title=title)

    def chat(rid: str, when: datetime) -> RawRecord:
        return make_record(rid=rid, source="teams", kind="message", container="teams:chat-pilot",
                           thread="teams:chat-pilot", ts=when)  # fmt: skip

    def recording(rid: str, when: datetime) -> list[RawRecord]:
        under = {"source": "plaud", "container": rid, "thread": rid, "ts": when}
        return [make_record(rid=rid, kind="recording", title="A call", text="", **under),
                make_record(rid=f"{rid}/transcript", kind="transcript", text="Ben: the dates.", **under)]  # fmt: skip

    def event(rid: str, title: str | None) -> RawRecord:
        return make_record(rid=rid, source="calendar", kind="event", container=None, thread=rid, ts=at(28, 8),
                           title=title, text="Response: none")  # fmt: skip

    return {
        "slack": [
            slack(BEN_ASK, at(12, 7)),
            slack(SENT, at(19, 12), BEN_ASK),
            slack(BEN_AGAIN, at(19, 9), BEN_ASK),
            slack(BEN_LATER, at(19, 14), BEN_ASK),
            slack(DAN_ASK, at(19, 9, 5)),
            slack(ROOM, at(13, 7)),
            slack(EVE, at(13, 8)),
            slack(OLD, at(5, 7)),
            slack(TWO_ASKS, at(19, 10)),
        ],
        "outlook": [
            mail(QUOTE, at(14, 7), "outlook:conv-quote", "The sensors' quote"),
            mail(QUOTE_AGAIN, at(19, 7), "outlook:conv-quote", "Re: The sensors' quote"),
            mail(PLAN_MAIL, at(19, 8), "outlook:conv-plan", "The plan and the floor"),
            mail(INVITE_MAIL, at(19, 9), "outlook:conv-invite", "Invitation: Supplier day"),
        ],
        "teams": [chat(CHAT, at(15, 7)), chat(CHAT_AGAIN, at(19, 7))],
        "plaud": [
            *recording(WEEKLY, at(19, 8, 5)),
            *recording(BETWEEN, at(19, 8, 20)),
            *recording(FOCUSED, at(19, 12, 30)),
        ],
        "calendar": [
            event(KICKOFF, "Kickoff, the record's title"),
            event(SERIES, "Sensor sync"),
            event(UNTITLED, None),
            event(OFFSITE, "Offsite day"),
        ],
    }


def build(root: Path) -> Vault:
    """The pilot's ledger: Ben's, Cleo's, Dan's and Eve's asks, Finn's done, Ben's in the team chat, Ivy's invite; the
    weekly with an open point and an answered one, a design review overlapping it with an outcome, a cancelled call,
    the owner's focus time, next week's weekly and a kickoff on its event."""
    config = make_config()
    config.sources = {name: SourceConfig(tools=["read"]) for name in SOURCES}
    vault = Vault.init(root / "vault", config, "test-box")
    weekly = {"subject": "Pilot weekly", "organiser": "Ben Sample"}
    made = [
        op("stream", 1, fields={"name": "pilot", "status": "active"}),
        op("add", 2, number=1, fields={"kind": "reply", "title": "Send Ben the pilot dates", "who": "Ben Sample",
                                       "stream": "pilot", "source": BEN_ASK}),
        op("add", 3, number=2, fields={"kind": "waiting", "title": "Cleo's quote for the sensors", "who": "Cleo Test",
                                       "stream": "pilot", "source": QUOTE, "due": "2026-10-21"}),
        meeting(4, "calendar:weekly-1019", number=1, start="2026-10-19T10:00+02:00", end="2026-10-19T10:30+02:00",
                attendees=["Ben Sample", "Dan Example", "Gail Example"], **weekly),
        meeting(5, "calendar:weekly-1026", number=2, start="2026-10-26T10:00+01:00", end="2026-10-26T10:30+01:00",
                **weekly),
        op("point", 6, 4, id=POINT, fields={"text": "Ben: the pilot's dates"}),
        op("add", 7, number=3, fields={"kind": "task", "title": "Book the room for the pilot", "who": "Dan Example",
                                       "stream": "pilot", "source": ROOM, "meeting": ulid(5)}),
        op("add", 8, number=4, fields={"kind": "task", "title": "Draft the pilot plan", "who": "Eve Example",
                                       "stream": "pilot", "source": EVE}),
        op("snooze", 9, 8, until=date(2026, 10, 25)),
        op("add", 10, number=5, fields={"kind": "reply", "title": "Send Finn the crane report", "who": "Finn Example",
                                        "stream": "pilot", "source": OLD}),
        op("done", 11, 10, note="sent"),
        meeting(12, None, number=3, start="2026-10-19T10:15+02:00", end="2026-10-19T11:00+02:00",
                subject="Design review", organiser="Gail Example", attendees=["Gail Example"],
                outcome="Gail owns the design."),
        meeting(13, None, number=4, start="2026-10-19T16:00+02:00", end="2026-10-19T17:00+02:00",
                subject="Vendor call", organiser="Ben Sample", response="cancelled"),
        meeting(14, None, number=5, start="2026-10-19T14:00+02:00", end="2026-10-19T16:00+02:00",
                subject="Focus time", organiser="Ada Example"),
        meeting(15, KICKOFF, number=6, start="2026-10-28T09:00+01:00", end="2026-10-28T10:00+01:00",
                subject="Pilot kickoff", organiser="Ivy Example"),
        op("add", 16, number=6, fields={"kind": "reply", "title": "Review the pilot budget", "who": "Ben Sample",
                                        "stream": "pilot", "source": CHAT}),
        op("add", 17, number=7, fields={"kind": "invite", "title": "Offsite day", "who": "Ivy Example",
                                        "stream": "pilot", "source": OFFSITE}),
        op("point", 31, 4, fields={"text": "The budget"}),
        point_set(32, 4, 31, state="answered", answer="40k, agreed"),
    ]  # fmt: skip
    vault.commit(write_ops(vault.root, made), "The pilot's ledger")
    for source, found in records().items():
        vault.write_run(source, found)
    return vault


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return template_with(tmp_path_factory, build)


@pytest.fixture
def vault(template: Path, tmp_path: Path) -> Vault:
    v = copy_vault(template, tmp_path)
    index_build.rebuild(v)
    return v


def owed(who: str, locator: str, asked: str, ask: str, *, kind: str = "reply", stream: str = "pilot",
         continues: str = "-", due: str = "-", when: str = "this week") -> str:  # fmt: skip
    return f"OWED:     {who} | {locator} | {asked} | {ask} | not yet | {when} | {kind} | {stream} | {continues} | {due}"


def waiting(who: str, locator: str, since: str, what: str, *, stream: str = "pilot", continues: str = "-",
            due: str = "-") -> str:  # fmt: skip
    return f"WAITING:  {who} | {locator} | {since} | {what} | {stream} | {continues} | {due}"


RUN = "RUN:      {} | run-1 | captures 2 | ready 2, pending 0 | skipped 0 | mark moved yes | -"


def run(vault: Vault, **blocks: list[str]) -> tuple[reconcile.Reconciled, list[str]]:
    """A sync started, its blocks written, each after its RUN line, and its reconcile: what it says and the draft's
    lines."""
    started = start.start(vault, [], NOW)
    for source, lines in blocks.items():
        text = "\n".join([RUN.format(source), *lines]) + "\n"
        (started.folder / "blocks" / f"{source}.txt").write_text(text, encoding="utf-8")
    done = reconcile.reconcile(vault, started.info.sync, NOW)
    assert done.path == started.folder / "draft.jsonl"
    return done, done.path.read_text(encoding="utf-8").splitlines()


def ops(lines: list[str]) -> list[dict[str, Any]]:
    """The block lines' own ops: all but the rollover and the archive, the last."""
    found = [json.loads(line) for line in lines if not line.startswith("#")]
    assert found[-1] == {"op": "archive"} and found.count({"op": "rollover"}) == 1
    return [op for op in found[:-1] if op != {"op": "rollover"}]


def around(lines: list[str], op: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    """An op's `# ?` lines above it and its alternatives below it."""
    k = lines.index(json.dumps(op, ensure_ascii=False))
    above: list[str] = []
    for line in reversed(lines[:k]):
        if not line.startswith("# ? "):
            break
        above.insert(0, line.removeprefix("# ? "))
    below: list[dict[str, Any]] = []
    for line in lines[k + 1 :]:
        if not line.startswith("# {"):
            break
        below.append(json.loads(line[2:]))
    return above, below


def quoted(source: str, n: int, text: str) -> str:
    """A block line as the draft and the report quote it: its file and line, the RUN line being 1, one space after
    its label."""
    label, _, rest = text.partition(":")
    return f"blocks/{source}.txt:{n} {label}: {rest.strip()}"


def unsure(op: dict[str, Any]) -> list[str]:
    return str(op.get("unsure", "")).split("; ") if "unsure" in op else []


def bare(op: dict[str, Any]) -> dict[str, Any]:
    """An op as the reconcile made it, before the draft marked it unsure: what its alternatives are built from."""
    return {k: v for k, v in op.items() if k != "unsure"}


A1 = f"A-0001 | reply | Ben Sample | Send Ben the pilot dates | {BEN_ASK}"
# A candidate's line is its context.md line, then its due and its latest log line (#272).
A2_ROW = f"A-0002 | waiting | Cleo Test | Cleo's quote for the sensors | {QUOTE}"
A2 = f"{A2_ROW} | due 10-21"
A3 = f"A-0003 | task | Dan Example | Book the room for the pilot | {ROOM} @M-0002"
M1 = "M-0001 | 10-19 10:00-10:30 | Pilot weekly | Ben Sample"
M3 = "M-0003 | 10-19 10:15-11:00 | Design review | Gail Example"
LOOK_ALIKE = "by who and title: log it there if it continues that work, else keep the add"


# -- closes ------------------------------------------------------------------------------------------------------------


def test_a_close_on_an_open_or_snoozed_item_is_sure_and_a_moot_one_a_drop(vault: Vault) -> None:
    done, lines = run(vault, slack=[
        f"CLOSES:   A-0001 | done | {SENT} | Sent Ben the dates in the channel",
        f"CLOSES:   A-0004 | moot | {SENT} | the plan went to Eve's lead",
    ])  # fmt: skip
    assert ops(lines) == [
        {"op": "done", "item": "A-0001", "note": SENT, "outcome": "Sent Ben the dates in the channel"},
        {"op": "drop", "item": "A-0004", "note": f"the plan went to Eve's lead: {SENT}"},
    ]
    sync = done.path.parent.name
    assert lines[:2] == [f"# docket sync reconcile {sync}: 4 ops, 0 unsure, rev 1", "# closes"]
    assert done.unsure == []


def test_a_close_naming_nothing_is_unsure_and_one_closed_already_is_skipped_and_said(vault: Vault) -> None:
    given = [
        f"CLOSES:   A-0099 | done | {SENT} | sent",
        f"CLOSES:   A-0005 | done | {SENT} | sent again",
        f"CLOSES:   A-0001 | done | {SENT} | sent",
        f"CLOSES:   A-0001 | done | {SENT} | sent, seen twice",
        f"CLOSES:   A-0002 | answered | {QUOTE_AGAIN} | the quote came",
    ]
    done, lines = run(vault, slack=given)
    nothing, sure, how = ops(lines)
    assert unsure(nothing) == ["A-0099 names no item: fix the ref, or delete the line"]
    assert "unsure" not in sure
    assert unsure(how) == ["answered isn't done or moot: say which"]
    assert around(lines, nothing) == ([quoted("slack", 2, given[0])], [])
    assert done.skipped == [
        f"{quoted('slack', 3, given[1])}: A-0005 is done already",
        f"{quoted('slack', 5, given[3])}: A-0001 closes on blocks/slack.txt:4 already",
    ]
    assert done.unsure == [4, 7]


def test_a_sync_whose_blocks_hold_no_close_says_no_source_reported_one(vault: Vault) -> None:
    """Every agent goes through the open items its people are in, so a sync that closes nothing is worth a look: the
    fourth rehearsal's blocks held no CLOSES line where the old sync closed five items (#286). A close whose locator
    doesn't open is dropped, but was reported."""
    asks = [owed("Dan Example", DAN_ASK, "2026-10-19 09:05", "Book the room for the pilot's first day", kind="task")]
    done, _ = run(vault, slack=asks)
    assert reconcile.NO_CLOSES == "no source reported a close: no block holds a CLOSES line"
    assert reconcile.NO_CLOSES in done.said and reconcile.NO_CLOSES in reconcile.lines(done)
    close = "CLOSES:   A-0001 | done | slack:C0PILOT/1 | sent"
    reported, lines = run(vault, slack=[*asks, close])
    assert reconcile.NO_CLOSES not in reported.said and "# closes" not in lines
    assert reported.dropped == [f"{quoted('slack', 3, close)}: slack:C0PILOT/1 doesn't open"]


# -- asks: a log, a set of the due, an add -----------------------------------------------------------------------------


def test_an_ask_continuing_an_open_item_is_a_log_and_a_due_it_moves_a_set(vault: Vault) -> None:
    done, lines = run(vault, outlook=[
        waiting("Cleo Test", QUOTE_AGAIN, "2026-10-19 09:00", "the quote by Thursday", continues="A-0002",
                due="2026-10-22"),
        waiting("Cleo Test", PLAN_MAIL, "2026-10-19 10:00", "Cleo's quote for the sensors, with the floor plan",
                continues="A-0002", due="2026-10-21"),
    ])  # fmt: skip
    assert ops(lines) == [
        {"op": "log", "item": "A-0002", "note": f"Cleo: the quote by Thursday: {QUOTE_AGAIN}"},
        {"op": "set", "item": "A-0002", "fields": {"due": "2026-10-22"}},
        {
            "op": "log",
            "item": "A-0002",
            "note": f"Cleo: Cleo's quote for the sensors, with the floor plan: {PLAN_MAIL}",
        },
    ]
    assert done.unsure == [] and done.merges == []


def test_a_continues_ref_unlike_its_item_with_nothing_of_its_thread_in_its_trail_is_unsure_its_add_beside_it(
    vault: Vault,
) -> None:
    """The rehearsal's miss: an ask to the same people for other work, logged as sure on their running item. In the
    item's own thread a reworded ask stays sure; a chat, which everyone in it shares, never backs one, and the log
    before a close's evidence is held to the same rule."""
    mail = [
        waiting("Cleo Test", PLAN_MAIL, "2026-10-19 10:00", "the floor plan she promised", continues="A-0002"),
        waiting("Cleo Test", QUOTE_AGAIN, "2026-10-19 09:00", "the signed order form", continues="A-0002"),
    ]
    chat = [owed("Ben Sample", CHAT_AGAIN, "2026-10-19 09:00", "Put the pilot calls in the team's calendars",
                 continues="A-0006")]  # fmt: skip
    slack = [
        f"CLOSES:   A-0001 | done | {SENT} | Sent Ben the dates",
        owed("Ben Sample", TWO_ASKS, "2026-10-18", "Share the venue address", continues="A-0001"),
    ]
    done, lines = run(vault, outlook=mail, slack=slack, teams=chat)
    _, plan, order, venue, calendars = ops(lines)
    why = "but its title doesn't look like that item's and nothing of its thread is in that item's trail: keep the log"
    assert unsure(plan) == [f"it continues A-0002, {why} if it continues that work, else take the add"]
    assert plan == {"op": "log", "item": "A-0002", "note": f"Cleo: the floor plan she promised: {PLAN_MAIL}",
                    "unsure": unsure(plan)[0]}  # fmt: skip
    assert around(lines, plan) == (
        [quoted("outlook", 2, mail[0]), A2],
        [{"op": "add", "fields": {"kind": "waiting", "title": "the floor plan she promised", "who": "Cleo Test",
                                  "stream": "pilot", "source": PLAN_MAIL, "opened": "2026-10-19"}}],
    )  # fmt: skip
    assert order == {"op": "log", "item": "A-0002", "note": f"Cleo: the signed order form: {QUOTE_AGAIN}"}
    assert unsure(venue) == [f"it continues A-0001, {why} if it continues that work, else take the add"]
    assert unsure(calendars) == [f"it continues A-0006, {why} if it continues that work, else take the add"]
    assert around(lines, calendars)[1] == [
        {"op": "add", "fields": {"kind": "reply", "title": "Put the pilot calls in the team's calendars",
                                 "who": "Ben Sample", "stream": "pilot", "source": CHAT_AGAIN, "opened": "2026-10-19"},
         "note": "not yet"},
    ]  # fmt: skip
    assert len(done.unsure) == 3


def test_a_new_ask_is_a_sure_add_with_its_fields_and_the_meeting_it_was_asked_in(vault: Vault) -> None:
    done, lines = run(vault, slack=[
        owed("Dan Example", DAN_ASK, "2026-10-19 10:40", "Send Dan the parking map", kind="task", when="today"),
        owed("Dan Example", DAN_ASK, "2026-10-19", "Approve the catering", kind="decision", due="2026-10-23"),
        waiting("Hal Example", TWO_ASKS, "2026-10-19 12:00", "the badge list"),
    ])  # fmt: skip
    assert ops(lines) == [
        {"op": "add", "fields": {"kind": "task", "title": "Send Dan the parking map", "who": "Dan Example",
                                 "stream": "pilot", "source": DAN_ASK, "opened": "2026-10-19", "due": "2026-10-20",
                                 "meeting": "M-0001"}, "note": "not yet"},
        {"op": "add", "fields": {"kind": "decision", "title": "Approve the catering", "who": "Dan Example",
                                 "stream": "pilot", "source": DAN_ASK, "opened": "2026-10-19", "due": "2026-10-23"},
         "note": "not yet"},
        {"op": "add", "fields": {"kind": "waiting", "title": "the badge list", "who": "Hal Example", "stream": "pilot",
                                 "source": TWO_ASKS, "opened": "2026-10-19"}},
    ]  # fmt: skip
    assert done.unsure == []


def test_an_ask_in_two_meetings_time_is_unsure_and_a_cancelled_one_or_the_owners_block_links_none(
    vault: Vault,
) -> None:
    given = [
        owed("Gail Example", DAN_ASK, "2026-10-19 10:20", "Share the sketches"),
        owed("Ben Sample", TWO_ASKS, "2026-10-19 16:30", "Call the vendor back"),
    ]
    done, lines = run(vault, slack=given)
    gail, ben = ops(lines)
    assert gail["fields"]["meeting"] == "M-0001" and "meeting" not in ben["fields"] and "unsure" not in ben
    assert unsure(gail) == ["asked in the time of M-0001 and M-0003: keep the meeting it was asked in, or take it out"]
    other = {"op": "add", "fields": {**gail["fields"], "meeting": "M-0003"}, "note": "not yet"}
    assert around(lines, gail) == ([quoted("slack", 2, given[0]), M1, M3], [other])
    assert done.unsure == [6]


def test_a_kind_or_a_stream_missing_or_not_held_and_a_day_that_doesn_t_read_are_unsure(vault: Vault) -> None:
    done, lines = run(vault, slack=[
        "OWED:     Hal Example | " + DAN_ASK + " | 2026-10-19 | the badge list | not yet | this week",
        owed("Hal Example", TWO_ASKS, "Monday", "the parking map", stream="ops", kind="call", due="Friday"),
    ])  # fmt: skip
    old, odd = ops(lines)
    assert old["fields"] == {"title": "the badge list", "who": "Hal Example", "source": DAN_ASK, "opened": "2026-10-19"}
    assert unsure(old) == [
        "its kind is missing: reply, decision or task",
        "it names no stream: add one the ledger holds",
    ]
    assert unsure(odd) == [
        "its kind is call: reply, decision or task",
        "the stream ops isn't one the ledger holds: name one it holds",
        "the day asked, 'Monday', isn't YYYY-MM-DD: add opened",
        "the due, 'Friday', isn't YYYY-MM-DD: fix it",
    ]
    # Each quotes the streams the ledger holds, so a stream is named with no read of the ledger.
    held = "streams the ledger holds: pilot"
    assert around(lines, old)[0][1:] == around(lines, odd)[0][1:] == [held]
    assert done.unsure == [5, 8]


# -- asks: the thread, the look-alike, the source an item has --------------------------------------------------------


def test_a_thread_match_with_no_continues_ref_is_unsure_the_add_first_the_log_its_alternative(vault: Vault) -> None:
    given = [owed("Ben Sample", BEN_AGAIN, "2026-10-19 11:00", "Share the venue address")]
    _, lines = run(vault, slack=given)
    (add,) = ops(lines)
    assert add["op"] == "add" and unsure(add) == [
        "in the thread of A-0001 of Ben Sample's, with no continues ref: log it there if it continues that work, else "
        "keep the add"
    ]
    log = {"op": "log", "item": "A-0001", "note": f"Ben: Share the venue address: {BEN_AGAIN}"}
    assert around(lines, add) == ([quoted("slack", 2, given[0]), A1], [log])


def test_a_thread_counts_only_for_the_same_who_and_side_and_never_in_a_chat_or_a_recording(vault: Vault) -> None:
    """Another person's ask in Ben's thread, Ben's own wait there and Ben's next message in the team chat his reply
    came from: each a sure add. His ask in the weekly's recording beside an item from it is no thread match either,
    only unsure as the recording reported again."""
    store.write(vault, [{"op": "add", "fields": {"kind": "reply", "title": "Send Ben the minutes", "who": "Ben Sample",
                                                 "stream": "pilot", "source": WEEKLY}}], by="claude")  # fmt: skip
    done, lines = run(vault, slack=[
        owed("Hal Example", BEN_AGAIN, "2026-10-19 11:00", "Share the venue address"),
        waiting("Ben Sample", BEN_LATER, "2026-10-19 16:00", "his badge list"),
    ], teams=[
        owed("Ben Sample", CHAT_AGAIN, "2026-10-19 09:00", "Share the venue address"),
    ], plaud=[
        owed("Ben Sample", f"r:{WEEKLY}/transcript@05:00", "2026-10-19 10:10", "Book the shuttle bus"),
    ])  # fmt: skip
    recorded, *found = ops(lines)
    assert [(o["fields"]["who"], o["fields"]["title"], "unsure" in o) for o in found] == [
        ("Hal Example", "Share the venue address", False), ("Ben Sample", "his badge list", False),
        ("Ben Sample", "Share the venue address", False),
    ]  # fmt: skip
    assert [why.split(",")[0] for why in unsure(recorded)] == ["its locator is already the source of A-0008"]
    assert done.merges == []


def test_a_continues_ref_a_thread_disagrees_with_and_two_items_in_one_thread_are_unsure(vault: Vault) -> None:
    store.write(vault, [{"op": "add", "fields": {"kind": "reply", "title": "Confirm the sensor vendor",
                                                 "who": "Ben Sample", "stream": "pilot", "source": BEN_LATER}}],
                by="claude")  # fmt: skip
    given = [
        owed("Ben Sample", BEN_AGAIN, "2026-10-19 11:00", "Send Ben the pilot dates, again", continues="A-0001"),
        owed("Ben Sample", SENT, "2026-10-19 14:00", "Share the venue address"),
    ]
    _, lines = run(vault, slack=given)
    log, add = ops(lines)
    assert log == {
        "op": "log", "item": "A-0001", "note": f"Ben: Send Ben the pilot dates, again: {BEN_AGAIN}",
        "unsure": "it continues A-0001, but its thread holds A-0008 of Ben Sample's too: keep the log on A-0001, or "
                  "move it",
    }  # fmt: skip
    a8 = f"A-0008 | reply | Ben Sample | Confirm the sensor vendor | {BEN_LATER}"
    assert around(lines, log) == (
        [quoted("slack", 2, given[0]), A1, a8],
        [{"op": "log", "item": "A-0008", "note": f"Ben: Send Ben the pilot dates, again: {BEN_AGAIN}"}],
    )
    assert unsure(add)[0].startswith("in the thread of A-0001, A-0008 of Ben Sample's, with no continues ref")
    assert [alt["item"] for alt in around(lines, add)[1]] == ["A-0001", "A-0008"]
    assert around(lines, add)[0] == [quoted("slack", 3, given[1]), A1, a8]


def test_a_look_alike_is_unsure_and_names_its_candidate(vault: Vault) -> None:
    given = [owed("Dan Example", DAN_ASK, "2026-10-19", "Book the room for the pilot's first day", kind="task",
                  due="2026-10-23")]  # fmt: skip
    _, lines = run(vault, slack=given)
    (add,) = ops(lines)
    assert add["fields"]["due"] == "2026-10-23" and "meeting" not in add["fields"]
    assert unsure(add) == [f"a look-alike of A-0003 {LOOK_ALIKE}"]
    log = {"op": "log", "item": "A-0003", "note": f"Dan: Book the room for the pilot's first day: {DAN_ASK}"}
    assert around(lines, add) == ([quoted("slack", 2, given[0]), A3], [log])


def test_an_ask_whose_rid_is_an_items_source_closed_or_open_is_unsure_the_item_quoted_with_its_state(
    vault: Vault,
) -> None:
    given = [
        owed("Finn Example", f"r:{OLD}#1", "2026-10-05", "Send Finn the boiler report"),
        waiting("Cleo Test", QUOTE, "2026-10-14", "the sensors' price list"),
    ]
    _, lines = run(vault, slack=given)
    finn, cleo = ops(lines)
    assert unsure(finn) == [
        "its locator is already the source of A-0005, as a recording or a message reported again brings: keep the add "
        "for a new ask, else delete the line"
    ]
    a5 = f"A-0005 | reply | Finn Example | Send Finn the crane report | {OLD} | done 2026-10-18"
    assert around(lines, finn) == ([quoted("slack", 2, given[0]), a5], [])
    # Cleo's is also in the thread of her open wait, and its rid is that wait's source.
    assert around(lines, cleo)[0] == [quoted("slack", 3, given[1]), A2, f"{A2_ROW} | open | due 10-21"]
    assert len(unsure(cleo)) == 2


def test_a_continues_ref_that_isn_t_open_or_names_nothing_is_unsure_the_add_first(vault: Vault) -> None:
    given = [
        owed("Finn Example", TWO_ASKS, "2026-10-19", "Send Finn the boiler report", continues="A-0005"),
        owed("Finn Example", DAN_ASK, "2026-10-19", "Book a taxi for him", continues="A-0042"),
    ]
    _, lines = run(vault, slack=given)
    closed, nothing = ops(lines)
    assert closed["op"] == "add" and unsure(closed) == [
        "it continues A-0005, which is done: keep the add for new work, or log it there"
    ]
    a5 = f"A-0005 | reply | Finn Example | Send Finn the crane report | {OLD} | done 2026-10-18"
    log = {"op": "log", "item": "A-0005", "note": f"Finn: Send Finn the boiler report: {TWO_ASKS}"}
    assert around(lines, closed) == ([quoted("slack", 2, given[0]), a5], [log])
    assert unsure(nothing) == ["it continues A-0042, which names no item: fix the ref, or keep the add"]
    assert around(lines, nothing) == ([quoted("slack", 3, given[1])], [])


# -- asks: the same ask twice ------------------------------------------------------------------------------------------


def test_the_same_ask_twice_by_one_continues_ref_is_one_log_and_the_report_lists_the_merge(vault: Vault) -> None:
    mail = waiting("Cleo Test", QUOTE_AGAIN, "2026-10-19 09:00", "Cleo's quote for the sensors", continues="A-0002")
    chat = waiting("Cleo Test", SENT, "2026-10-19 13:00", "the quote for the sensors", continues="A-0002",
                   due="2026-10-23")  # fmt: skip
    done, lines = run(vault, outlook=[mail], slack=[chat])
    assert ops(lines) == [
        {"op": "log", "item": "A-0002", "note": f"Cleo: Cleo's quote for the sensors: {QUOTE_AGAIN}, {SENT}"},
        {"op": "set", "item": "A-0002", "fields": {"due": "2026-10-23"}},
    ]
    assert done.merges == [reconcile.Merge([quoted("outlook", 2, mail), quoted("slack", 2, chat)], sure=True)]
    assert reconcile.lines(done)[3:5] == [f"merged: {quoted('outlook', 2, mail)}", f"  with {quoted('slack', 2, chat)}"]


def test_one_message_asking_for_the_dates_and_the_budget_is_an_unsure_merge_two_adds_its_alternative(
    vault: Vault,
) -> None:
    """DR-0642's title test is coarse: the dates and the budget share most long words. A shared locator never makes
    a merge sure; an agent writes two lines on one message mostly because it holds two asks."""
    given = [
        owed("Hal Example", TWO_ASKS, "2026-10-19", "Send Hal the pilot dates"),
        owed("Hal Example", TWO_ASKS, "2026-10-19", "Send Hal the pilot budget"),
    ]
    done, lines = run(vault, slack=given)
    (merged,) = ops(lines)
    assert merged["fields"]["title"] == "Send Hal the pilot dates" and "note" in merged
    assert merged["note"] == "not yet"
    assert unsure(merged) == [
        "2 lines with the same who and a look-alike title, drafted as one ask: keep it if they are one, else add the "
        "other too"
    ]
    budget = {"op": "add", "fields": {**merged["fields"], "title": "Send Hal the pilot budget"}, "note": "not yet",
              "force": True}  # fmt: skip
    assert around(lines, merged) == ([quoted("slack", 2, given[0]), quoted("slack", 3, given[1])], [budget])
    assert done.merges == [reconcile.Merge([quoted("slack", 2, given[0]), quoted("slack", 3, given[1])], sure=False)]
    assert reconcile.lines(done)[2].startswith("merged, unsure: ")


def test_asks_that_differ_in_who_side_or_title_stay_apart_in_one_mail_and_at_one_moment(vault: Vault) -> None:
    moment = f"r:{WEEKLY}/transcript@10:00"
    done, lines = run(vault, outlook=[
        owed("Cleo Test", PLAN_MAIL, "2026-10-19 10:00", "Send Cleo the floor plan"),
        owed("Cleo Test", PLAN_MAIL, "2026-10-19 10:00", "Approve the sensor mounts", kind="decision"),
        waiting("Cleo Test", PLAN_MAIL, "2026-10-19 10:00", "Send Cleo the floor plan"),
    ], plaud=[
        owed("Ben Sample", moment, "2026-10-19 10:10", "Send the floor plan around"),
        owed("Dan Example", moment, "2026-10-19 10:10", "Send the floor plan around", kind="task"),
    ])  # fmt: skip
    found = ops(lines)
    assert [(o["fields"]["who"], o["fields"]["kind"], o["fields"]["title"]) for o in found] == [
        ("Cleo Test", "reply", "Send Cleo the floor plan"), ("Cleo Test", "decision", "Approve the sensor mounts"),
        ("Cleo Test", "waiting", "Send Cleo the floor plan"), ("Ben Sample", "reply", "Send the floor plan around"),
        ("Dan Example", "task", "Send the floor plan around"),
    ]  # fmt: skip
    assert done.merges == []
    # Cleo's wait and her ask share a title on two sides: the writer refuses the second add unless it is forced.
    wait = found[2]
    assert unsure(wait) == [
        "it looks like the add for blocks/outlook.txt:2, which the writer refuses: force it if it is another ask, else "
        "delete the line"
    ]
    assert around(lines, wait)[1] == [{k: v for k, v in wait.items() if k != "unsure"} | {"force": True}]
    assert done.unsure == [lines.index(json.dumps(wait)) + 1]


# -- asks on an item a CLOSES line closes ------------------------------------------------------------------------------


def test_pings_either_side_of_a_close_are_a_log_before_and_an_add_after_and_never_merged(vault: Vault) -> None:
    """Ben's dates went out at 14:00 his time: his ping at 11:00 is answered by it, his ping at 16:00 is new work. By
    day, an ask the day before is answered, one two days after is new. The one the day before is a log on another
    topic, outside the item's thread, so it is unsure too, the add beside it."""
    given = [
        f"CLOSES:   A-0001 | done | {SENT} | Sent Ben the dates",
        owed("Ben Sample", BEN_AGAIN, "2026-10-19 11:00", "Send Ben the pilot dates", continues="A-0001"),
        owed("Ben Sample", BEN_LATER, "2026-10-19 16:00", "Send Ben the pilot dates", continues="A-0001"),
        owed("Ben Sample", TWO_ASKS, "2026-10-18", "Share the venue address", continues="A-0001"),
        owed("Ben Sample", DAN_ASK, "2026-10-21", "Book the shuttle bus", continues="A-0001"),
    ]
    done, lines = run(vault, slack=given)
    close, before, after, day_before, day_after = ops(lines)
    assert close["op"] == "done" and done.merges == []
    assert before == {"op": "log", "item": "A-0001", "note": f"Ben: Send Ben the pilot dates: {BEN_AGAIN}"}
    assert {k: v for k, v in day_before.items() if k != "unsure"} == {
        "op": "log", "item": "A-0001", "note": f"Ben: Share the venue address: {TWO_ASKS}"
    }  # fmt: skip
    assert unsure(day_before)[0].startswith("it continues A-0001, but its title doesn't look like that item's")
    assert (after["op"], after["fields"]["source"], day_after["op"]) == ("add", BEN_LATER, "add")
    assert len(done.unsure) == 1


def test_two_pings_after_a_close_are_one_add_unsure_that_they_are_one_ask(vault: Vault) -> None:
    given = [
        f"CLOSES:   A-0001 | done | {SENT} | Sent Ben the dates",
        owed("Ben Sample", BEN_LATER, "2026-10-19 16:00", "Send Ben the pilot dates", continues="A-0001"),
        owed("Ben Sample", DAN_ASK, "2026-10-21", "Send Ben the pilot dates for his team", continues="A-0001"),
    ]
    done, lines = run(vault, slack=given)
    _, add = ops(lines)
    assert add["note"] == f"not yet; also {DAN_ASK}" and unsure(add)[0].startswith("2 lines with the same who")
    assert done.merges == [reconcile.Merge([quoted("slack", 3, given[1]), quoted("slack", 4, given[2])], sure=False)]


def test_an_ask_the_same_day_as_the_close_with_no_time_is_unsure_the_add_first(vault: Vault) -> None:
    given = [
        f"CLOSES:   A-0001 | done | {SENT} | Sent Ben the dates",
        owed("Ben Sample", BEN_LATER, "2026-10-19", "Send Ben the pilot dates for his team", continues="A-0001"),
        f"CLOSES:   A-0003 | done | {NOTE} | booked",
        owed("Dan Example", DAN_ASK, "2026-10-19 11:00", "Book the room again", continues="A-0003", kind="task"),
    ]
    recall.doc_put(vault, doc(NOTE, [f"{WEEKLY}/transcript"], [f"- Dan books the room. [r:{WEEKLY}/transcript]"]))
    _, lines = run(vault, slack=given)
    _, _, ben, dan = ops(lines)
    assert ben["op"] == "add" and unsure(ben) == [
        "A-0001 closes on blocks/slack.txt:2 the same day and the ask says no time: keep the add if it came after, "
        "else log it there"
    ]
    log = {"op": "log", "item": "A-0001", "note": f"Ben: Send Ben the pilot dates for his team: {BEN_LATER}"}
    assert around(lines, ben) == ([quoted("slack", 3, given[1]), A1], [log])
    # Evidence with no time of its own, a note, can't tell either, whatever the ask's time.
    assert unsure(dan)[0].startswith("A-0003 closes on blocks/slack.txt:4 the same day")


# -- invites -----------------------------------------------------------------------------------------------------------


def test_an_invite_is_titled_from_its_meeting_or_its_record_and_logged_on_an_open_invite(vault: Vault) -> None:
    given = [
        f"INVITES:  Ivy Example | {KICKOFF} | 2026-10-28 09:00 | new series | pilot",
        f"INVITES:  Ivy Example | {INVITE_MAIL} | 2026-10-30 09:00 | customer | pilot",
        f"INVITES:  Ivy Example | {OFFSITE} | 2026-11-02 09:00 | clashes with M-0002 | pilot",
        f"INVITES:  Ivy Example | {UNTITLED} | 2026-11-03 09:00 | new series | pilot",
        f"INVITES:  Ivy Example | {SERIES} | 2026-11-04 09:00 | new series | ops",
        f"INVITES:  Ivy Example | {SERIES} | 2026-11-04 09:00 | new series",
    ]
    _, lines = run(vault, calendar=given)
    kickoff, mail, offsite, untitled, unheld, old = ops(lines)
    invite = {"kind": "invite", "who": "Ivy Example", "stream": "pilot"}
    assert kickoff == {"op": "add", "fields": {**invite, "title": "Pilot kickoff", "source": KICKOFF},
                       "note": "new series"}  # fmt: skip
    assert mail == {"op": "add", "fields": {**invite, "title": "Invitation: Supplier day", "source": INVITE_MAIL},
                    "note": "customer"}  # fmt: skip
    assert offsite == {"op": "log", "item": "A-0007", "note": f"clashes with M-0002: {OFFSITE}"}
    assert unsure(untitled) == ["no title: neither a meeting nor the record gives one, so write one"]
    assert "title" not in untitled["fields"]
    assert unsure(unheld) == ["the stream ops isn't one the ledger holds: name one it holds"]
    # The same series twice: the writer would refuse the second add as a look-alike of the first.
    assert unsure(old)[0] == "it names no stream: add one the ledger holds"
    assert unsure(old)[1].startswith("it looks like the add for blocks/calendar.txt:6")
    assert around(lines, untitled)[0] == [quoted("calendar", 5, given[3])]


# -- meetings, links, minutes, streams ---------------------------------------------------------------------------------


def test_meetings_and_streams_get_their_fields_set_and_what_they_don_t_take_is_unsure(vault: Vault) -> None:
    _, lines = run(vault, outlook=[
        f"MEETINGS: M-0001 | response=accepted; attendees=Ben Sample, Dan Example | {QUOTE_AGAIN}",
        f"MEETINGS: M-0099 | response=declined | {QUOTE_AGAIN}",
        f"MEETINGS: M-0002 | colour=blue; where | {QUOTE_AGAIN}",
        f"STREAMS:  pilot | where=The pilot starts on the 28th; next=Book the room | the date is set | {QUOTE_AGAIN}",
        f"STREAMS:  ops | status=waiting | the room waits | {QUOTE_AGAIN}",
        f"STREAMS:  pilot | risk=the room is late | the room may slip | {QUOTE_AGAIN}",
    ])  # fmt: skip
    accepted, nothing, odd, pilot, other, risk = ops(lines)
    # An unsure stream line quotes the stream as it stands, whole, or the streams the ledger holds (#272).
    assert around(lines, other)[0][1:] == ["streams the ledger holds: pilot"]
    assert unsure(risk) == [
        "'risk=the room is late' isn't field=value with a field of name, status, where, next, deadline"
    ]
    assert around(lines, risk)[0][1:] == ["pilot | active | - | -"]
    assert accepted == {"op": "set", "item": "M-0001", "note": QUOTE_AGAIN,
                        "fields": {"response": "accepted", "attendees": ["Ben Sample", "Dan Example"]}}  # fmt: skip
    assert unsure(nothing) == ["M-0099 names no meeting: fix the ref, or delete the line"]
    assert odd["fields"] == {} and [w.split(" isn't ")[0] for w in unsure(odd)] == ["'colour=blue'", "'where'"]
    assert pilot == {"op": "set", "item": "pilot", "note": f"the date is set: {QUOTE_AGAIN}",
                     "fields": {"where": "The pilot starts on the 28th", "next": "Book the room"}}  # fmt: skip
    assert unsure(other) == ["ops isn't a stream the ledger holds: fix the name, or delete the line"]
    assert lines.index("# streams") > lines.index('{"op": "rollover"}')


def test_a_recording_is_a_material_on_the_meeting_at_its_start_and_its_ref_settles_two(vault: Vault) -> None:
    store.write(vault, [{"op": "material", "item": "M-0003", "ref": FOCUSED, "label": "recording"}], by="claude")
    given = [
        f"NOTES:    recording-note:2026-10-19-pilot-weekly | {WEEKLY} | 10:05 | 10:30",
        f"NOTES:    recording-note:2026-10-19-between | {BETWEEN} | 10:20 | 10:50",
        f"NOTES:    recording-note:2026-10-19-review | {BETWEEN} | 10:20 | 10:50 | M-0003",
        f"NOTES:    recording-note:2026-10-19-next | {WEEKLY} | 10:05 | 10:30 | M-0002",
        f"NOTES:    recording-note:2026-10-19-gone | {WEEKLY} | 10:05 | 10:30 | M-0099",
        f"NOTES:    recording-note:2026-10-19-focus | {FOCUSED} | 14:30 | 15:00",
        f"NOTES:    recording-note:2026-10-19-focus | {FOCUSED} | 14:30 | 15:00 | M-0003",
    ]
    done, lines = run(vault, plaud=given)
    weekly, between, review, named, gone = ops(lines)
    link = {"op": "material", "label": "recording"}
    assert weekly == {**link, "item": "M-0001", "ref": WEEKLY}
    assert {k: v for k, v in between.items() if k != "unsure"} == {**link, "item": "M-0001", "ref": BETWEEN}
    assert unsure(between) == ["recorded in the time of M-0001 and M-0003: keep the one its content names"]
    other = {**link, "item": "M-0003", "ref": BETWEEN}
    assert around(lines, between) == ([quoted("plaud", 3, given[1]), M1, M3], [other])
    assert review == {**link, "item": "M-0003", "ref": BETWEEN}
    assert unsure(named) == [
        "M-0002 isn't at the recording's start: keep it if the content names it, else delete the line"
    ]
    assert around(lines, named)[0][1:] == ["M-0002 | 10-26 10:00-10:30 | Pilot weekly | Ben Sample", M1]
    assert gone["item"] == "M-0001"
    assert unsure(gone) == ["M-0099 names no meeting: keep the one at its start, or fix the ref"]
    assert done.said[-2:] == [
        f"{quoted('plaud', 7, given[5])}: no meeting 10 minutes either side of its start, so no link",
        f"{quoted('plaud', 8, given[6])}: M-0003 has it already",
    ]


def test_minutes_are_sure_only_on_an_open_point_and_an_outcome_only_on_a_meeting_with_none(vault: Vault) -> None:
    """Each unsure line quotes what the ledger holds that it would change, whole: the point, the meeting's points, the
    outcome there or the earlier line's, the two outcomes joined as the alternative, and the op on the point a number
    names (#272)."""
    moment = f"r:{WEEKLY}/transcript@12:08"
    given = [
        f"MINUTES:  M-0001 | {POINT} | answered | the 28th, with Ben's team on site | {moment}",
        f"MINUTES:  M-0001 | {POINT} | open | still open | {moment}",
        f"MINUTES:  M-0001 | {ANSWERED} | answered | 45k | {moment}",
        f"MINUTES:  M-0002 | {POINT} | answered | - | {moment}",
        f"MINUTES:  M-0099 | {POINT} | dropped | - | {moment}",
        f"MINUTES:  M-0001 | {ulid(99)} | closed | - | {moment}",
        f"MINUTES:  M-0001 | #2 | answered | the 28th | {moment}",
        f"OUTCOME:  M-0001 | The pilot starts on the 28th / Dan books the room | {moment}",
        f"OUTCOME:  M-0001 | Dan books the room | {moment}",
        f"OUTCOME:  M-0003 | Gail draws it | {moment}",
        f"OUTCOME:  M-0099 | Nobody | {moment}",
    ]
    _, lines = run(vault, plaud=given)
    first, again, answered, elsewhere, nowhere, closed, numbered, outcome, twice, had, gone = ops(lines)
    assert first == {"op": "point_set", "item": "M-0001", "point": POINT, "note": moment,
                     "fields": {"state": "answered", "answer": "the 28th, with Ben's team on site"}}  # fmt: skip
    budget = f"M-0001#1 answered | The budget | answer: 40k, agreed | {ANSWERED}"
    dates = f"M-0001#2 open | Ben: the pilot's dates | {POINT}"
    assert unsure(again) == ["M-0001#2 is answered by an earlier line: keep one, or join them"]
    assert around(lines, again) == ([quoted("plaud", 3, given[1]), dates], [])
    assert unsure(answered) == [
        "M-0001#1 is answered already: keep the change only if this answer is newer, else delete it"
    ]
    assert around(lines, answered) == ([quoted("plaud", 4, given[2]), budget], [])
    assert elsewhere["fields"] == {"state": "answered"}
    assert unsure(elsewhere) == [f"{POINT} isn't a point standing on M-0002: fix it, or delete the line"]
    assert around(lines, elsewhere) == ([quoted("plaud", 5, given[3])], [])
    assert unsure(nowhere) == ["M-0099 names no meeting: fix the ref, or delete the line"]
    assert unsure(closed) == [
        "closed isn't answered, dropped or open",
        f"{ulid(99)} isn't a point standing on M-0001: fix it, or delete the line",
    ]
    assert around(lines, closed) == ([quoted("plaud", 7, given[5]), budget, dates], [])
    # A point by its number: the op on that point, by its id, is the alternative.
    assert unsure(numbered) == [
        "#2 isn't a point standing on M-0001: take the alternative, M-0001#2 by its id, or fix it, or delete the line"
    ]
    by_id = bare(numbered) | {"point": POINT}
    assert around(lines, numbered) == ([quoted("plaud", 8, given[6]), budget, dates], [by_id])
    assert outcome == {"op": "set", "item": "M-0001", "note": moment,
                       "fields": {"outcome": "The pilot starts on the 28th\nDan books the room"}}  # fmt: skip
    assert unsure(twice) == [
        "M-0001 has an outcome from blocks/plaud.txt:9: keep one, or take the alternative, the two joined"
    ]
    joined = "The pilot starts on the 28th\nDan books the room\nDan books the room"
    assert around(lines, twice) == (
        [quoted("plaud", 10, given[8]),
         "M-0001 outcome from blocks/plaud.txt:9: The pilot starts on the 28th / Dan books the room"],
        [bare(twice) | {"fields": {"outcome": joined}}],
    )  # fmt: skip
    assert unsure(had) == ["M-0003 has an outcome already: keep one, or take the alternative, the two joined"]
    assert around(lines, had) == (
        [quoted("plaud", 11, given[9]), "M-0003 outcome: Gail owns the design."],
        [bare(had) | {"fields": {"outcome": "Gail owns the design.\nGail draws it"}}],
    )  # fmt: skip
    assert unsure(gone) == ["M-0099 names no meeting: fix the ref, or delete the line"]


# -- the draft and the report ------------------------------------------------------------------------------------------


def test_the_draft_keeps_its_order_and_the_report_says_each_part(vault: Vault) -> None:
    """Closes, asks, then meetings, links and minutes; the rollover, the prep line, the streams and the archive last,
    whatever order the blocks hold them in; RUN and LEARNED lines only printed."""
    plaud = [
        f"STREAMS:  pilot | next=Book the room | the date is set | {SENT}",
        f"OUTCOME:  M-0001 | The pilot starts on the 28th | r:{WEEKLY}/transcript@14:00",
        f"MINUTES:  M-0001 | {POINT} | answered | the 28th | r:{WEEKLY}/transcript@12:08",
        f"NOTES:    recording-note:2026-10-19-pilot-weekly | {WEEKLY} | 10:05 | 10:30",
        f"MEETINGS: M-0001 | response=accepted | {SENT}",
        "LEARNED:  SKILL.md | the recorder lists newest first",
    ]
    slack = [
        owed("Dan Example", DAN_ASK, "2026-10-19 09:05", "Book the room for the pilot's first day", kind="task"),
        f"CLOSES:   A-0001 | done | {SENT} | Sent Ben the dates",
        owed("Hal Example", "slack:C0PILOT/1", "2026-10-19", "Send Hal the badge list"),
    ]
    done, lines = run(vault, plaud=plaud, slack=slack)
    sync = done.path.parent.name
    starts = [
        f"# docket sync reconcile {sync}: 9 ops, 1 unsure",
        "# closes",
        f'{{"op": "done", "item": "A-0001", "note": "{SENT}"',
        "# asks",
        f"# ? {quoted('slack', 2, slack[0])}",
        f"# ? {A3}",
        '{"op": "add", "fields": {"kind": "task", "title": "Book the room for the pilot\'s first day"',
        '# {"op": "log", "item": "A-0003"',
        "# meetings, links, minutes",
        '{"op": "set", "item": "M-0001", "fields": {"response": "accepted"}',
        '{"op": "material", "item": "M-0001", "ref": "plaud:rec-0101"',
        f'{{"op": "point_set", "item": "M-0001", "point": "{POINT}"',
        '{"op": "set", "item": "M-0001", "fields": {"outcome"',
        '{"op": "rollover"}',
        "# prep: the next five working days: M-0002",
        "#   M-0002 | 10-26 10:00-10:30 | Pilot weekly | Ben Sample",
        "# streams",
        '{"op": "set", "item": "pilot", "fields": {"next": "Book the room"}',
        '{"op": "archive"}',
    ]  # fmt: skip
    assert len(lines) == len(starts), lines
    assert [line for line, begun in zip(lines, starts, strict=True) if not line.startswith(begun)] == []
    assert reconcile.lines(done) == [
        RUN.format("plaud").replace("RUN:      ", "RUN: "),
        RUN.format("slack").replace("RUN:      ", "RUN: "),
        f"draft: {done.path} rev 1: 9 ops, 1 unsure, lines 7",
        f"dropped {quoted('slack', 4, slack[2])}: slack:C0PILOT/1 doesn't open",
        "blocks/calendar.txt isn't there: no block from calendar",
        "blocks/outlook.txt isn't there: no block from outlook",
        "blocks/teams.txt isn't there: no block from teams",
        f"learned {quoted('plaud', 7, plaud[5])}",
        "prep, the next five working days (1)",
        "  M-0002 | 10-26 10:00-10:30 | Pilot weekly | Ben Sample",
    ]


def test_the_prep_line_lists_each_prep_meeting_with_its_open_points(vault: Vault) -> None:
    """The prep is chosen and worded from the draft: under `# prep`, each meeting of the prep week and its open points,
    a proposed one marked; a point answered or dropped isn't listed (#272)."""
    store.write(vault, [
        {"op": "point", "item": "M-0002", "fields": {"text": "Dan: is the room booked?", "item": "A-0003",
                                                     "proposed": True}},
        {"op": "point", "item": "M-0002", "fields": {"text": "The agenda"}, "as": "agenda"},
        {"op": "point", "item": "M-0002", "fields": {"text": "The badges"}},
        {"op": "point_set", "item": "M-0002", "point": "$agenda", "fields": {"state": "dropped"}},
    ], by="claude", now=NOW)  # fmt: skip
    _, lines = run(vault)
    at = lines.index("# prep: the next five working days: M-0002")
    with index_build.ledger_tables(vault) as conn:
        found = views.read(conn, views.Ask(view="show", ref="M-0002"), ZoneInfo("Europe/Budapest"), NOW)
    room, _, badges = (p.id for p in found.points)
    assert lines[at + 1 : at + 5] == [
        "#   M-0002 | 10-26 10:00-10:30 | Pilot weekly | Ben Sample",
        f"#     M-0002#1 open | Dan: is the room booked? -> A-0003 (proposed) | {room}",
        f"#     M-0002#3 open | The badges | {badges}",
        '{"op": "archive"}',
    ]


def test_a_candidate_s_line_says_its_due_and_what_was_last_said_on_it(vault: Vault) -> None:
    """A log on an item whose title doesn't look like the ask's is settled from its candidate's line, with no `show`:
    its due, and its latest log line with its day, cut (#272)."""
    long = "Cleo: the quote comes on Thursday, " + "with the floor plan and the sensors' list " * 4
    store.write(vault, [{"op": "log", "item": "A-0002", "note": "Cleo: next week"},
                        {"op": "log", "item": "A-0002", "note": long}], by="sync", now=NOW)  # fmt: skip
    given = [waiting("Cleo Test", PLAN_MAIL, "2026-10-19 10:00", "the floor plan she promised", continues="A-0002")]
    _, lines = run(vault, outlook=given)
    (log,) = ops(lines)
    last = views.cut(long, reconcile.LAST)
    assert len(last) == reconcile.LAST and last.endswith("…")
    assert around(lines, log)[0] == [quoted("outlook", 2, given[0]), f"{A2} | last 10-20: {last}"]


def test_a_line_whose_shape_is_wrong_is_drafted_unsure_and_never_dropped_unseen(vault: Vault) -> None:
    """A field that held ` | ` splits into one field too many, or moves its bar into the next; the line's locator may
    then be another field, so it stays, unsure, even where that field opens nothing."""
    given = [
        owed("Ben Sample", TWO_ASKS, "2026-10-19", "Pick the date | or the week"),
        "OWED:     Ben Sample | " + TWO_ASKS + " | 2026-10-19 | Pick the venue | | not yet | today",
        "CLOSES:   A-0002 | done",
        "WAITING:  Cleo Test | the quote | 2026-10-19 | the quote | pilot | - | - | more | and more",
    ]
    done, lines = run(vault, slack=given)
    short, too_many, moved, unread = ops(lines)
    assert unsure(too_many)[0] == (
        "OWED takes 6 to 10 fields, not 11, so the fields may have moved: fix the op from the block line"
    )
    assert unsure(moved)[0] == (
        "OWED's field 5 starts or ends with |: a field holds no bar, so the fields may have moved: fix the op from the "
        "block line"
    )
    assert moved["fields"]["due"] == "2026-10-20"
    assert short == {"op": "done", "item": "A-0002", "note": "", "outcome": "", "unsure": (
        "CLOSES takes 4 fields, not 2, so the fields may have moved: fix the op from the block line"
    )}  # fmt: skip
    assert unread["op"] == "add" and unsure(unread)[0].startswith("WAITING takes 4 to 7 fields, not 9")
    assert done.dropped == [] and done.merges == []


def test_a_sync_of_another_vault_or_none_is_refused_and_the_command_prints_the_report(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The command reads the clock: held at the fixtures' Tuesday, so the prep week stays theirs.
    monkeypatch.setattr(views, "now", lambda: NOW)
    started = start.start(vault, [], NOW)
    other = empty_vault(tmp_path / "other", make_config())
    with pytest.raises(DocketError, match=re.escape(f"is a sync of {vault.root}, not of {other.root}")):
        reconcile.reconcile(other, started.info.sync, NOW)
    assert not (started.folder / "draft.jsonl").exists()
    (started.folder / "draft.jsonl").write_text("an older draft\n", encoding="utf-8")
    assert main(["sync", "--vault", str(vault.root), "reconcile", str(started.folder)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"draft: {started.folder / 'draft.jsonl'} rev 1: 2 ops, 0 unsure"
    assert (started.folder / "draft.jsonl").read_text(encoding="utf-8").splitlines()[-4:] == [
        '{"op": "rollover"}',
        "# prep: the next five working days: M-0002",
        "#   M-0002 | 10-26 10:00-10:30 | Pilot weekly | Ben Sample",
        '{"op": "archive"}',
    ]
    assert main(["sync", "reconcile", "sync-1", "--vault", str(vault.root)]) == 2
    assert "isn't a sync" in capsys.readouterr().err


def test_with_no_index_the_raw_and_the_ledger_are_read_whole_and_draft_the_same(vault: Vault) -> None:
    given = [
        owed("Ben Sample", BEN_AGAIN, "2026-10-19 11:00", "Share the venue address"),
        waiting("Cleo Test", QUOTE_AGAIN, "2026-10-19 09:00", "the quote", continues="A-0002"),
        f"INVITES:  Ivy Example | {INVITE_MAIL} | 2026-10-30 09:00 | customer | pilot",
    ]
    _, indexed = run(vault, slack=given)
    index_build.db_path(vault).unlink()
    _, unindexed = run(vault, slack=given)
    assert unindexed[1:] == indexed[1:]
    assert unsure(ops(unindexed)[0])[0].startswith("in the thread of A-0001 of Ben Sample's")


# -- written by file ---------------------------------------------------------------------------------------------------


def by_file(vault: Vault, draft: Path, *, dry_run: bool) -> tuple[CallToolResult, str]:
    """The `ledger_batch` tool by `file`, as the sync calls it, on a service over the vault that serves no port."""
    service = Service(Settings(port=8750, vault=vault.root, token=lambda: "t0ken-for-tests-only"))

    async def call() -> CallToolResult:
        async with Client(create_mcp(service)) as client:
            arguments = {"ops": [], "file": str(draft), "by": "sync", "dry_run": dry_run}
            return await client.call_tool("ledger_batch", arguments)

    try:
        answer = asyncio.run(call())
    finally:
        service.writer.close()
    first = answer.content[0]
    assert isinstance(first, TextContent)
    return answer, first.text


def test_the_draft_settled_dry_runs_clean_and_unsettled_is_refused_naming_unsure(
    vault: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    done, _ = run(vault, slack=[
        f"CLOSES:   A-0001 | done | {SENT} | Sent Ben the dates",
        owed("Dan Example", DAN_ASK, "2026-10-19 10:40", "Book the room for the pilot's first day", kind="task"),
        owed("Hal Example", TWO_ASKS, "2026-10-19", "Send Hal the pilot dates"),
        owed("Hal Example", TWO_ASKS, "2026-10-19", "Send Hal the pilot budget"),
    ], outlook=[
        waiting("Cleo Test", QUOTE_AGAIN, "2026-10-19 09:00", "the quote by Thursday", continues="A-0002",
                due="2026-10-22"),
        f"INVITES:  Ivy Example | {UNTITLED} | 2026-11-03 09:00 | new series | pilot",
    ], plaud=[
        f"NOTES:    recording-note:2026-10-19-pilot-weekly | {WEEKLY} | 10:05 | 10:30",
        f"MINUTES:  M-0001 | {POINT} | answered | the 28th | r:{WEEKLY}/transcript@12:08",
        f"OUTCOME:  M-0001 | The pilot starts on the 28th / Dan books the room | r:{WEEKLY}/transcript@14:00",
    ])  # fmt: skip
    draft = done.path
    vault_arg = ["--vault", str(vault.root)]
    capsys.readouterr()
    assert main(["ledger", "batch", str(draft), "--by", "sync", "--dry-run", *vault_arg]) == 1
    refused = [line for line in capsys.readouterr().out.splitlines() if ": refused, " in line]
    assert len(refused) == len(done.unsure) and all("not unsure" in line for line in refused)
    answer, text = by_file(vault, draft, dry_run=True)
    assert answer.structured_content is not None
    assert [c["refused"] for c in answer.structured_content["changes"] if c["refused"]] == [
        line.split(": refused, ", 1)[1] for line in refused
    ]
    assert text.splitlines()[-1] == f"dry run: {len(refused)} change(s) refused; nothing written"
    # Settled as the skill settles it: one docket sync settle for every unsure line (#272).
    untitled, dan, hal = done.unsure
    sync = [
        "sync",
        "settle",
        draft.parent.name,
        f"{untitled}=drop",
        f"{dan}=alt",
        f"{hal}=keep",
        "--rev",
        "1",
        *vault_arg,
    ]
    assert main(sync) == 0
    assert capsys.readouterr().out.splitlines()[0] == "settled 3 line(s): 1 kept, 1 alternative(s) taken, 1 dropped"
    assert not any('"unsure"' in line for line in draft.read_text(encoding="utf-8").splitlines())
    answer, text = by_file(vault, draft, dry_run=True)
    assert not answer.is_error and text.splitlines()[-1] == "dry run: 9 op(s) would be written; nothing written"
    assert main(["ledger", "batch", str(draft), "--by", "sync", "--dry-run", *vault_arg]) == 0
    out = capsys.readouterr().out.splitlines()
    # The done writes its outcome too; nothing is due to roll over or to archive.
    assert out[-1] == "dry run: 9 op(s) would be written; nothing written", out
    assert main(["ledger", "batch", str(draft), "--by", "sync", *vault_arg]) == 0
    assert capsys.readouterr().out.splitlines()[-1].startswith("9 op(s) written, batch ")


def test_a_thread_is_matched_through_an_items_notes_and_a_snoozed_candidate_says_its_day(vault: Vault) -> None:
    """Cleo's wait was logged with her plan mail, so her next mail in that conversation is in its thread; Eve's
    snoozed task is quoted with the day it wakes, as context.md writes it."""
    store.write(vault, [{"op": "log", "item": "A-0002", "note": f"Cleo sent the plan: {PLAN_MAIL}."}], by="claude")
    given = [
        waiting("Cleo Test", f"r:{PLAN_MAIL}#2", "2026-10-19 10:00", "the signed floor plan"),
        owed("Eve Example", EVE, "2026-10-19", "Draft the pilot plan's appendix", kind="task"),
    ]
    _, lines = run(vault, outlook=given)
    cleo, eve = ops(lines)
    assert unsure(cleo)[0].startswith("in the thread of A-0002 of Cleo Test's, with no continues ref")
    assert around(lines, eve)[0][1:] == [
        f"A-0004 | task | Eve Example | Draft the pilot plan | {EVE} | until 10-25",
        f"A-0004 | task | Eve Example | Draft the pilot plan | {EVE} | snoozed",
    ]
    assert len(unsure(eve)) == 2


def test_a_notes_locators_are_read_as_read_takes_them() -> None:
    assert views.note_locators(f"Cleo sent it: {PLAN_MAIL}. Then [r:{WEEKLY}/transcript@12:08] and {NOTE}, 10:30") == [
        PLAN_MAIL, f"r:{WEEKLY}/transcript@12:08", NOTE,
    ]  # fmt: skip


# -- the first review's cases -----------------------------------------------------------------------------------------


def dry_run(vault: Vault, draft: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, list[str]]:
    capsys.readouterr()
    code = main(["ledger", "batch", str(draft), "--by", "sync", "--dry-run", "--vault", str(vault.root)])
    return code, capsys.readouterr().out.splitlines()


def every_choice_taken(lines: list[str]) -> str:
    """A draft settled by keeping each unsure op and taking every alternative too: two adds where a merge was."""
    out: list[str] = []
    for line in lines:
        if line.startswith("{") and '"unsure"' in line:
            op = json.loads(line)
            del op["unsure"]
            out.append(json.dumps(op))
        else:
            out.append(line[2:] if line.startswith("# {") else line)
    return "\n".join(out) + "\n"


def test_an_invite_dropped_but_reported_again_is_unsure_its_item_quoted_with_its_state(vault: Vault) -> None:
    """Ivy's offsite was dropped, not answered, so the calendar reports it again each sync: the writer's net holds
    only open items, so the add would go in sure."""
    store.write(vault, [{"op": "drop", "item": "A-0007", "note": "not going"}], by="user", now=NOW)
    given = [f"INVITES:  Ivy Example | {OFFSITE} | 2026-11-02 09:00 | clashes with M-0002 | pilot"]
    _, lines = run(vault, calendar=given)
    (add,) = ops(lines)
    assert add["op"] == "add" and unsure(add) == [
        "its rid is already the source of A-0007, as an invite left unanswered is reported again: keep the add for a "
        "new invite, else delete the line"
    ]
    a7 = f"A-0007 | invite | Ivy Example | Offsite day | {OFFSITE} | dropped 2026-10-20"
    assert around(lines, add) == ([quoted("calendar", 2, given[0]), a7], [])


@pytest.mark.parametrize("source", [WEEKLY, f"r:{WEEKLY}/summary#1"])
def test_a_recording_reported_again_by_another_of_its_records_is_unsure(vault: Vault, source: str) -> None:
    """Ben's ask came from the weekly, cited by the recording's rid or its summary's paragraph, and is done; the note
    never landed, so this sync reports the recording again from its transcript's moment."""
    summary = make_record(rid=f"{WEEKLY}/summary", source="plaud", kind="summary", container=WEEKLY, thread=WEEKLY,
                          ts=at(19, 8, 5), text="Ben wants the shuttle times.")  # fmt: skip
    vault.write_run("plaud", [summary])
    index_build.rebuild(vault)
    shuttle = {"kind": "reply", "title": "Send Ben the shuttle times", "who": "Ben Sample", "stream": "pilot",
               "source": source}  # fmt: skip
    store.write(vault, [{"op": "add", "as": "ben", "fields": shuttle}, {"op": "done", "item": "$ben", "note": SENT}],
                by="sync", now=NOW)  # fmt: skip
    _, lines = run(vault, plaud=[owed("Ben Sample", f"r:{WEEKLY}/transcript@05:00", "2026-10-19 10:10",
                                      "Send Ben the bus times for the shuttle")])  # fmt: skip
    (add,) = ops(lines)
    assert [why.split(",")[0] for why in unsure(add)] == ["its locator is already the source of A-0008"]
    a8 = f"A-0008 | reply | Ben Sample | Send Ben the shuttle times | {source} | done 2026-10-20"
    assert around(lines, add)[0][1:] == [a8]


def test_a_due_that_isn_t_a_day_makes_a_log_unsure_as_it_does_an_add(vault: Vault) -> None:
    _, lines = run(vault, outlook=[
        waiting("Cleo Test", QUOTE_AGAIN, "2026-10-19 09:00", "the quote, now by Thursday", continues="A-0002",
                due="Thursday"),
        waiting("Cleo Test", PLAN_MAIL, "2026-10-19 10:00", "the floor plan", due="2026-02-30"),
    ])  # fmt: skip
    log, add = ops(lines)
    assert log["op"] == "log" and unsure(log) == ["the due, 'Thursday', isn't YYYY-MM-DD: fix it"]
    assert add["op"] == "add" and unsure(add)[0] == "the due, '2026-02-30', isn't YYYY-MM-DD: fix it"


def test_a_merge_s_alternative_adds_are_forced_so_both_adds_dry_run_clean(
    vault: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two lines merge only on a look-alike title, so the writer would refuse the second add: taken as the alternative,
    it is forced. One message asking for the dates and the budget, and two pings after a close."""
    for blocks in (
        {"slack": [owed("Hal Example", TWO_ASKS, "2026-10-19", "Send Hal the pilot dates"),
                   owed("Hal Example", TWO_ASKS, "2026-10-19", "Send Hal the pilot budget")]},
        {"slack": [f"CLOSES:   A-0001 | done | {SENT} | Sent Ben the dates",
                   owed("Ben Sample", BEN_LATER, "2026-10-19 16:00", "Send Ben the pilot dates", continues="A-0001"),
                   owed("Ben Sample", DAN_ASK, "2026-10-21", "Send Ben the pilot dates for his team",
                        continues="A-0001")]},
    ):  # fmt: skip
        done, lines = run(vault, **blocks)
        done.path.write_text(every_choice_taken(lines), encoding="utf-8")
        code, out = dry_run(vault, done.path, capsys)
        assert code == 0 and not [line for line in out if ": refused, " in line], out


def test_a_meetings_line_sets_only_what_agent_md_lets_it(vault: Vault) -> None:
    """A MEETINGS line's fields are the response, the attendees, the start and the end: an outcome there, which would
    overwrite the minutes' as a sure op, is unsure."""
    _, lines = run(vault, outlook=[
        f"MEETINGS: M-0003 | outcome=Gail draws it | {QUOTE_AGAIN}",
        f"MEETINGS: M-0001 | start=2026-10-19T10:15; end=2026-10-19T10:45 | {QUOTE_AGAIN}",
    ])  # fmt: skip
    outcome, moved = ops(lines)
    assert outcome["fields"] == {} and unsure(outcome) == [
        "'outcome=Gail draws it' isn't field=value with a field of response, attendees, start, end"
    ]
    assert moved == {"op": "set", "item": "M-0001", "note": QUOTE_AGAIN,
                     "fields": {"start": "2026-10-19T10:15", "end": "2026-10-19T10:45"}}  # fmt: skip
