"""Importing a ledger with its history (#202, DR-0653 to DR-0656): the dry run's counts, a month per commit, a rerun
that adds nothing, the refusals, the ledger's own meetings beside the import, the catch-up's joins, and the revert.

The file comes from a stand-in for the owner's export script (DR-0657, DR-0658): ops as it writes them, via import,
`day` alone, each add's id 2000-01-01 plus its number in ms, every other op's from its day (a meeting's: its start) and
its place among its object's ops. Invented people, streams and events only."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from docket_testkit import best_of, commit_config, empty_vault, git, make_config, make_record, sh, traced
from forget_kit import forget

from docket.cli import main
from docket.index.build import ledger_tables
from docket.ledger import calendar, imports, ops, store, views
from docket.ledger.fold import Ledger, fold
from docket.vault.config import SourceConfig, VaultConfig
from docket.vault.records import RawRecord
from docket.vault.vault import Vault

ZONE = ZoneInfo("Europe/Budapest")
NOW = datetime(2026, 10, 27, 8, 0, tzinfo=UTC)
# The pilot's weekly sync: Monday 09:00 in Budapest, three weeks running, the last one after the DST change.
WEEKLY = datetime(2026, 10, 12, 7, 0, tzinfo=UTC)
KICK_OFF = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
RECORD = make_record(1)
NAME = "export.jsonl"


def config() -> VaultConfig:
    base = make_config()
    return base.model_copy(update={"sources": {**base.sources, "cal": SourceConfig()}})


def made(root: Path) -> Vault:
    """An empty vault holding one chat record, for an item's source to name."""
    found = empty_vault(root, config())
    found.write_run("chat", [RECORD], NOW)
    return found


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return made(tmp_path)


def day(month: int, n: int) -> date:
    return date(2026, month, n)


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


class Export:
    """A stand-in for the owner's export: each add's id is 2000-01-01 plus its number in milliseconds (a stream's,
    with none, 2000-01-01 itself), each other op's its day at 00:00 UTC (a meeting's: its start) plus its place among
    its object's ops, with the first 80 bits of sha256 of the object's number and that place. Not `early`, the adds
    take the other ops' rule, as DR-0658 had it first."""

    def __init__(self, *, early: bool = True) -> None:
        self.lines: list[dict[str, Any]] = []
        self.places: dict[str, int] = {}
        self.ids: dict[str, str] = {}
        self.starts: dict[str, datetime] = {}
        self.early = early

    def _op(self, key: str, base: int, kind: str, on: date, by: str, **keys: Any) -> str:
        place = self.places.get(key, 0)
        self.places[key] = place + 1
        bits = int.from_bytes(hashlib.sha256(f"{key}/{place}".encode()).digest()[:10], "big")
        at = imports.ADDS_AT + keys.get("number", 0) if self.early and kind in ops.MAKES else base + place
        op_id = ops.encode((at << ops.RANDOM_BITS) | bits)
        item = self.ids.setdefault(key, op_id)
        line = {"schema_version": 1, "id": op_id, "item": item, "op": kind, "day": on.isoformat(), "by": by}
        self.lines.append({**line, "via": "import", **keys})
        return op_id

    def item(self, n: int, on: date, by: str = "claude", key: str | None = None, **fields: Any) -> str:
        """An item's add, opened on its day; `key` other than its number's makes a second item claiming that number."""
        return self._op(key or f"A-{n:04d}", _ms(datetime(on.year, on.month, on.day, tzinfo=UTC)), "add", on, by,
                        number=n, fields={**fields, "opened": on.isoformat()})  # fmt: skip

    def on(self, n: int, kind: str, on: date, by: str = "claude", **keys: Any) -> str:
        return self._op(f"A-{n:04d}", _ms(datetime(on.year, on.month, on.day, tzinfo=UTC)), kind, on, by, **keys)

    def logged(self, n: int, on: date, text: str, by: str = "claude", real: tuple[str, dict[str, Any]] | None = None):
        """An old log line: the op its words name first, when they name one, then the line itself."""
        if real is not None:
            self.on(n, real[0], on, by, **real[1])
        self.on(n, "log", on, by, note=text)

    def meeting(self, n: int, start: datetime, subject: str, key: str | None = None, **fields: Any) -> str:
        key = key or f"M-{n:04d}"
        self.starts[key] = start
        local = start.astimezone(ZONE)
        times = {"start": local.isoformat(timespec="minutes"),
                 "end": (local + timedelta(minutes=30)).isoformat(timespec="minutes")}  # fmt: skip
        return self._op(key, _ms(start), "meeting", local.date(), "claude", number=n,
                        fields={"subject": subject, **times, **fields})  # fmt: skip

    def at(self, n: int, kind: str, on: date | None = None, **keys: Any) -> str:
        key = f"M-{n:04d}"
        start = self.starts[key]
        return self._op(key, _ms(start), kind, on or start.astimezone(ZONE).date(), "claude", **keys)

    def stream(self, name: str, on: date, **fields: Any) -> str:
        return self._op(f"stream:{name}", _ms(datetime(on.year, on.month, on.day, tzinfo=UTC)), "stream", on, "claude",
                        fields={"name": name, **fields})  # fmt: skip

    def text(self, change: Callable[[dict[str, Any]], dict[str, Any]] = lambda line: line) -> str:
        return "".join(json.dumps(change(dict(line)), ensure_ascii=False) + "\n" for line in self.lines)


def item_fields(kind: str, title: str, who: str, stream: str, source: str, **more: Any) -> dict[str, Any]:
    return {"kind": kind, "title": title, "who": who, "stream": stream, "source": source, **more}


def export(*, early: bool = True) -> Export:
    """Five items through the line forms an export maps, four meetings with rolls paired and not, two streams."""
    e = Export(early=early)
    e.stream("pilot", day(9, 1), status="active", where="Waiting on the venue", next="Book the room")
    e.stream("ops", day(9, 1), status="waiting")
    e.meeting(1, WEEKLY, "Weekly pilot sync", organiser="Ada Example", attendees=["Ada Example", "Ben Sample"])
    e.at(1, "material", ref="notes/calls/2026-10-12-pilot.md", label="recording")
    venue = e.at(1, "point", fields={"text": "The venue"})
    budget = e.at(1, "point", fields={"text": "Budget"})
    e.at(1, "point_set", point=budget, fields={"state": "answered", "answer": "agreed"})
    e.meeting(2, WEEKLY + timedelta(days=7), "Weekly pilot sync")
    copy = e.at(2, "point", fields={"text": "The venue", "from": venue})
    e.at(1, "point_set", point=venue, fields={"state": "rolled", "to": copy})
    # A copy the export couldn't pair names the meeting it came from (DR-0623).
    dates = e.at(2, "point", fields={"text": "Dates", "from": e.ids["M-0001"]})
    e.meeting(3, WEEKLY + timedelta(days=14), "Weekly pilot sync")
    last = e.at(3, "point", fields={"text": "The venue", "from": copy, "proposed": True})
    e.at(2, "point_set", point=copy, fields={"state": "rolled", "to": last})
    e.at(2, "point_set", point=dates, fields={"state": "dropped"})
    # A roll the export couldn't pair names the meeting it went to.
    agenda = e.at(2, "point", fields={"text": "Agenda"})
    e.at(2, "point_set", point=agenda, fields={"state": "rolled", "to": e.ids["M-0003"]})
    e.meeting(4, KICK_OFF, "Kick-off")
    scope = e.at(4, "point", fields={"text": "Scope"})
    e.at(4, "point_set", point=scope, fields={"state": "answered", "answer": "pilot first"})
    e.at(4, "archive", day(9, 15))
    # Chased twice, snoozed, its due moved, closed and reopened.
    e.item(1, day(9, 1), **item_fields("reply", "Send Ada the venue dates", "Ada Example", "pilot",
                                       "mail:Venue dates | 2026-09-01 10:00"))  # fmt: skip
    e.on(1, "material", day(9, 1), ref="https://example.invalid/venues", label="venue list")
    e.logged(1, day(9, 1), "asked in the thread")
    e.logged(1, day(9, 3), "triage: chase", "user", ("triage", {"verdict": "chase"}))
    e.logged(1, day(9, 5), "snoozed to 2026-09-09", "user", ("snooze", {"until": "2026-09-09"}))
    e.logged(1, day(9, 10), "due: - -> 2026-09-20", "claude", ("set", {"fields": {"due": "2026-09-20"}}))
    e.logged(1, day(9, 21), "done: answered in the thread", "sync", ("done", {"note": "answered in the thread"}))
    e.logged(1, day(10, 2), "reopened (was done): one more question", "user", ("reopen", {}))
    e.logged(1, day(10, 3), "triage: chase", "user", ("triage", {"verdict": "chase"}))
    # A v1 note's lines, closed by a settling op since no line said so, then archived.
    e.item(2, day(9, 15), **item_fields("task", "Draft the budget", "Ben Sample", "ops", "app:tasks"))
    e.logged(2, day(9, 15), "2026-09-15 started the draft")
    e.logged(2, day(9, 18), "sent to Ben 18 Sep")
    e.on(2, "done", day(9, 18))
    e.on(2, "archive", day(10, 2))
    # On a meeting, its source a record in raw: the tasks app's undo, then a triage set by hand.
    e.item(3, day(10, 13), "user", **item_fields("waiting", "Cleo: the catering quote", "Cleo Test", "pilot",
                                                 RECORD.rid, meeting=e.ids["M-0002"]))  # fmt: skip
    discuss = e.on(3, "triage", day(10, 14), "user", verdict="discuss")
    e.on(3, "log", day(10, 14), "user", note="triage: discuss")
    e.on(3, "undo", day(10, 14), "user", of=discuss)
    e.on(3, "log", day(10, 14), "user", note="undo: back to open")
    e.logged(3, day(10, 15), "triage: - -> me", "claude", ("triage", {"verdict": "me"}))
    # Snoozed by hand: its until line and its state line, one snooze.
    e.item(4, day(10, 1), **item_fields("decision", "Pick the venue", "Ada Example", "pilot", "todo:open questions"))
    e.on(4, "snooze", day(10, 2), "user", until="2026-11-30")
    e.on(4, "log", day(10, 2), "user", note="until: - -> 2026-11-30")
    e.on(4, "log", day(10, 2), "user", note="state: open -> snoozed")
    e.item(5, day(10, 5), **item_fields("invite", "Supplier fair", "Ben Sample", "ops", "mail:Fair | 2026-10-05"))
    e.logged(5, day(10, 6), "dropped: not this year", "user", ("drop", {"note": "not this year"}))
    return e


def ledger(vault: Vault) -> Ledger:
    return fold(ops.read_all(vault.root).lines)


def summary(vault: Vault) -> dict[str, Any]:
    """The ledger as the owner reads it: each item's state and history, each meeting's points, each stream."""
    found = ledger(vault)
    items = {
        f"A-{i.number:04d}": (
            i.state,
            i.triage,
            i.due,
            i.until,
            i.closed,
            i.archived,
            [x.line for x in i.log if x.line is not None],
        )
        for i in found.items.values()
        if i.number is not None
    }
    meetings = {
        f"M-{m.number:04d}": (m.subject, m.archived, m.event, [(p.text, p.state, p.answer) for p in m.points])
        for m in found.meetings.values()
        if m.number is not None
    }
    streams = {s.name: (s.status, s.where) for s in found.streams.values()}
    return {"items": items, "meetings": meetings, "streams": streams}


def run(vault: Vault, text: str, *, dry_run: bool = False) -> imports.Imported:
    return imports.run(vault, text, NAME, dry_run=dry_run)


def commits(vault: Vault) -> list[str]:
    return sh(vault.root, "git", "log", "--format=%s").splitlines()


def event(n: int, start: datetime, title: str) -> RawRecord:
    end = start + timedelta(minutes=30)
    return RawRecord.model_validate({
        "rid": f"cal:E{n}", "source": "cal", "kind": "event", "ts": start, "author": "Ben Sample <ben@example.invalid>",
        "title": title, "text": "as shown\nWhere: Room 1\nResponse: accepted", "fetched": NOW, "via": "test",
        "native": {"end": end.isoformat(), "all_day": False},
    })  # fmt: skip


# -- the dry run ----------------------------------------------------------------------------------------------------


def test_the_dry_run_counts_what_the_file_brings_and_writes_nothing(vault: Vault) -> None:
    before = commits(vault)
    done = run(vault, export().text(), dry_run=True)
    counted = done.plan.counted
    assert counted is not None and done.plan.refusals == ()
    assert counted.items == {
        "decision": {"snoozed": 1}, "invite": {"dropped": 1}, "reply": {"open": 1}, "task": {"done": 1},
        "waiting": {"open": 1},
    }  # fmt: skip
    assert counted.archived_items == 1
    assert counted.log_entries == {"A-0001": 7, "A-0002": 2, "A-0003": 3, "A-0004": 2, "A-0005": 1}
    assert (counted.meetings, counted.archived_meetings) == (4, 1)
    assert counted.points == {"answered": 2, "dropped": 1, "open": 1, "rolled": 3}
    assert counted.chains == ["M-0001#1 > M-0002#1 > M-0003#1", "M-0001 > M-0002#2", "M-0002#3 > M-0003"]
    assert counted.naming_a_meeting == 2
    assert counted.streams == {"active": 1, "waiting": 1}
    assert (counted.next_item, counted.next_meeting) == ("A-0006", "M-0005")
    assert counted.unresolved == {"app:": ["A-0002"], "mail:": ["A-0001", "A-0005"], "todo:": ["A-0004"]}
    assert (counted.not_from_the_file, counted.renumbered, counted.joins, counted.idle) == ({}, [], [], {})
    sept = len([line for line in export().lines if line["day"].startswith("2026-09")])
    assert [len(month) for month in done.plan.months] == [sept, len(export().lines) - sept]
    assert (commits(vault), ops.read_all(vault.root).lines) == (before, ())
    said = imports.lines(done)
    assert said[0] == f"import: {done.plan.ops} op(s) to write in 2 month(s) (2026-09 to 2026-10), 0 held already"
    assert "chains: 3, 2 end(s) naming a meeting" in said
    assert "  M-0001#1 > M-0002#1 > M-0003#1" in said
    assert "sources it can't resolve: 4" in said and "  mail: 2: A-0001, A-0005" in said
    assert said[-1] == "dry run: nothing written"


def test_the_dry_run_names_the_joins_the_catch_up_would_make_after_it(vault: Vault) -> None:
    vault.write_run("cal", [event(2, WEEKLY + timedelta(days=7), "weekly  PILOT sync")], NOW)
    done = run(vault, export().text(), dry_run=True)
    assert done.plan.counted is not None
    assert done.plan.counted.joins == ["M-0002 2026-10-19 09:00 weekly PILOT sync > cal:E2"]
    run(vault, export().text())
    caught = calendar.catch_up(vault, day(9, 1), now=NOW)
    assert [(o.what, o.meeting) for o in caught.outcomes] == [("joined", "M-0002")]
    m = {mm.number: mm for mm in ledger(vault).meetings.values()}[2]
    assert (m.event, m.id) == ("cal:E2", export().ids["M-0002"])


def test_the_json_gives_each_item_s_log_entries_for_the_export_to_compare(vault: Vault) -> None:
    data = json.loads(imports.as_json(run(vault, export().text(), dry_run=True)))
    assert data["counted"]["log_entries"]["A-0001"] == 7
    assert (data["months"], data["held"], data["refusals"], data["dry_run"]) == (["2026-09", "2026-10"], 0, [], True)


def test_a_file_with_no_meetings_asks_the_catch_up_nothing_and_a_line_twice_is_one_op(vault: Vault) -> None:
    e = Export()
    e.stream("pilot", day(9, 1), status="active")
    doubled = e.text() + e.text()
    done = run(vault, doubled, dry_run=True)
    assert done.plan.counted is not None
    assert (done.plan.ops, done.plan.refusals, done.plan.counted.joins, done.plan.counted.chains) == (1, (), [], [])
    assert "meetings: 0, 0 archived; points: none; next M-0001" in imports.lines(done)


# -- the import -----------------------------------------------------------------------------------------------------


def test_the_import_writes_a_month_per_commit_and_the_ledger_reads_as_the_old_one(vault: Vault) -> None:
    done = run(vault, export().text())
    assert done.commits and done.renumbered is None
    sept = len([line for line in export().lines if line["day"].startswith("2026-09")])
    assert commits(vault)[:2] == [
        f"Ledger: import 2026-10, {len(export().lines) - sept} ops on test-box",
        f"Ledger: import 2026-09, {sept} ops on test-box",
    ]
    got = summary(vault)
    assert got["items"]["A-0001"][:6] == ("open", "chase", day(9, 20), None, None, False)
    assert got["items"]["A-0001"][6] == [
        "asked in the thread", "triage: chase", "snoozed to 2026-09-09", "due: - -> 2026-09-20",
        "done: answered in the thread", "reopened (was done): one more question", "triage: chase",
    ]  # fmt: skip
    assert got["items"]["A-0002"][:6] == ("done", None, None, None, day(9, 18), True)
    assert got["items"]["A-0003"][:2] == ("open", "me")
    assert got["items"]["A-0004"][:4] == ("snoozed", None, None, day(11, 30))
    assert got["items"]["A-0005"][0] == "dropped"
    assert got["meetings"]["M-0004"][:3] == ("Kick-off", True, None)
    assert got["meetings"]["M-0003"][3] == [("The venue", "open", None)]
    assert got["streams"] == {"pilot": ("active", "Waiting on the venue"), "ops": ("waiting", None)}
    assert all(line.op.machine == "test-box" and line.op.via == "import" for line in ops.read_all(vault.root).lines)
    assert ledger(vault).problems == ()
    said = imports.lines(done)
    assert said[-2].startswith("written in 2 commit(s), the last ")
    assert said[-1] == "next: docket ledger calendar --since <the calendar's first day> joins the old meetings"


def test_the_history_counts_reach_back_through_the_imported_ops(vault: Vault) -> None:
    run(vault, export().text())
    with ledger_tables(vault) as conn:
        shown = views.read(conn, views.Ask(view="show", ref="A-0001"), ZONE, NOW)
    assert shown.detail is not None
    detail = shown.detail
    assert (detail.chased, detail.snoozes, detail.due_moves, detail.reopens) == (2, 1, 1, 1)


def test_the_writer_s_keys_are_set_and_after_chains_an_object_s_ops(vault: Vault) -> None:
    e = export()
    run(vault, e.text())
    held = {line.op.id: line.op for line in ops.read_all(vault.root).lines}
    first = e.lines[0]["id"]
    sept = sorted(x["id"] for x in e.lines if x["day"].startswith("2026-09"))
    assert {held[i].batch for i in sept} == {sept[0]}
    one = sorted(x["id"] for x in e.lines if x["item"] == e.ids["A-0001"])
    assert [held[i].after for i in one] == [None, *one[:-1]]
    assert held[first].batch is not None


def test_a_rerun_writes_nothing_and_a_stopped_import_finishes(vault: Vault) -> None:
    text = export().text()
    planned = imports.plan(vault, text, NAME)
    store.write_import(vault, planned.months[:1])
    again = run(vault, text)
    assert (again.plan.held, len(again.commits)) == (len(planned.months[0]), 1)
    whole = summary(vault)
    before = commits(vault)
    third = run(vault, text)
    assert (third.plan.held, third.plan.ops, third.commits, commits(vault)) == (len(export().lines), 0, (), before)
    assert imports.lines(third)[-1] == "nothing to write: the ledger holds it all"
    assert summary(vault) == whole


def test_a_rerun_after_a_forget_compares_and_writes_its_listed_ops_masked(vault: Vault) -> None:
    """The export derives the same ids again, so a forget's listed op is compared as its mask and, after a revert,
    written as one: no rerun brings its text back (DR-0696)."""
    text = export().text()
    run(vault, text)
    raw = {line.op.id: line.text for line in ops.read_all(vault.root, masked=frozenset()).lines}
    (asked,) = [op_id for op_id, line in raw.items() if "asked in the thread" in line]
    forget(vault, ops=[asked])
    before = commits(vault)
    again = run(vault, text)
    assert (again.plan.refusals, again.plan.ops, again.commits, commits(vault)) == ((), 0, (), before)
    store.revert_import(vault)
    run(vault, text)
    written = {line.op.id: line.text for line in ops.read_all(vault.root, masked=frozenset()).lines}
    assert written[asked] == ops.mask(raw[asked])
    assert {i: t for i, t in written.items() if i != asked} == {i: t for i, t in raw.items() if i != asked}
    assert not any("asked in the thread" in p.read_text(encoding="utf-8") for _, p in ops.listing(vault.root)[0])


def test_a_listed_op_is_masked_before_the_text_rules_see_it(vault: Vault) -> None:
    """A forget of an ask, every op of the shell it leaves listed, then the person it named put on the never-store
    list, a revert and the same export again: the listed ops are masked before their values are screened, so the import
    brings them back as their masks instead of stopping on what the forget took (DR-0696)."""
    text = export().text()
    run(vault, text)
    raw = {line.op.id: line.text for line in ops.read_all(vault.root, masked=frozenset()).lines}
    (cleo,) = [op_id for op_id, line in raw.items() if '"who":"Cleo Test"' in line]
    forget(vault, ops=[op_id for op_id, line in raw.items() if f'"item":"{cleo}"' in line])
    vault = configured(vault, people=["Cleo Test"])
    store.revert_import(vault)
    again = run(vault, text)
    assert again.plan.refusals == () and again.commits
    written = {line.op.id: line.text for line in ops.read_all(vault.root, masked=frozenset()).lines}
    assert written[cleo] == ops.mask(raw[cleo])
    assert not any("Cleo Test" in p.read_text(encoding="utf-8") for _, p in ops.listing(vault.root)[0])
    # The rule is in force: with the op unlisted, the same line stops the import, naming its line and field.
    fresh = configured(empty_vault(vault.root.parent / "two", config()), people=["Cleo Test"])
    (said,) = run(fresh, text).plan.refusals
    assert said.endswith(": who names someone on the never-store people list; leave them out")


def test_a_listed_op_is_masked_before_its_source_meets_the_never_store_containers(vault: Vault) -> None:
    """The containers screen sits where the other rules do, after the clean and the mask: a forgotten op whose source
    sits in a container listed afterwards comes back as its mask, and unlisted, the same line stops the import
    (DR-0690, DR-0696)."""
    text = export().text()
    run(vault, text)
    raw = {line.op.id: line.text for line in ops.read_all(vault.root, masked=frozenset()).lines}
    (budget,) = [op_id for op_id, line in raw.items() if '"source":"app:tasks"' in line]
    forget(vault, ops=[op_id for op_id, line in raw.items() if f'"item":"{budget}"' in line])
    vault = configured(vault, containers=["tasks"])
    store.revert_import(vault)
    again = run(vault, text)
    assert again.plan.refusals == () and again.commits
    written = {line.op.id: line.text for line in ops.read_all(vault.root, masked=frozenset()).lines}
    assert written[budget] == ops.mask(raw[budget])
    fresh = configured(empty_vault(vault.root.parent / "two", config()), containers=["tasks"])
    (said,) = run(fresh, text).plan.refusals
    assert said.endswith(": source points into a never-store container; leave it out")


def test_an_unlisted_line_holding_text_on_a_shell_refuses_the_import_by_its_place(vault: Vault) -> None:
    """A shell's add on the list and its other ops not, as a list written by hand leaves it: after a revert, the same
    export names each line that would bring text back onto the shell, and nothing is written (DR-0696)."""
    text = export().text()
    run(vault, text)
    raw = {line.op.id: line.text for line in ops.read_all(vault.root, masked=frozenset()).lines}
    (cleo,) = [op_id for op_id, line in raw.items() if '"who":"Cleo Test"' in line]
    forget(vault, ops=[cleo])
    store.revert_import(vault)
    again = run(vault, text)
    assert again.plan.refusals and not again.commits
    for said in again.plan.refusals:
        assert said.startswith(f"{NAME}:") and said.endswith(
            ": holds text on A-0003, a shell a forget left; leave it out of the file"
        )


def test_a_changed_line_or_an_op_from_elsewhere_on_its_objects_refuses_a_rerun(vault: Vault) -> None:
    e = export()
    run(vault, e.text())

    def renamed(line: dict[str, Any]) -> dict[str, Any]:
        if line["id"] == e.ids["A-0005"]:
            line["fields"] = {**line["fields"], "title": "Supplier fair, renamed"}
        return line

    refused = run(vault, e.text(renamed))
    assert refused.plan.refusals[0].endswith("is held with another text; revert, then import again")
    store.write(vault, [{"op": "log", "item": "A-0001", "note": "asked again"}], now=NOW)
    refused = run(vault, e.text())
    assert refused.plan.refusals == (
        f"1 op(s) not from the file name its objects ({refused_where(vault)} first); revert, then import again",
    )
    assert imports.lines(refused)[-1] == "nothing written"


def refused_where(vault: Vault) -> str:
    return next(line.where for line in ops.read_all(vault.root).lines if line.op.via == "cli")


def test_the_ledger_s_own_meetings_stand_beside_it_and_lose_a_number_it_claims_first(vault: Vault) -> None:
    vault.write_run("cal", [event(9, datetime(2026, 10, 20, 7, tzinfo=UTC), "Ops review")], NOW)
    calendar.step(vault, ["cal:E9"], via="cli", now=NOW)
    (own,) = ledger(vault).meetings.values()
    dry = run(vault, export().text(), dry_run=True)
    assert dry.plan.counted is not None
    assert dry.plan.counted.not_from_the_file == {"meeting by sync via cli on test-box": 1}
    assert dry.plan.counted.renumbered == [f"M-0001 ({own.id})"]
    done = run(vault, export().text())
    assert done.renumbered is not None
    numbers = {m.id: m.number for m in ledger(vault).meetings.values()}
    assert (numbers[own.id], numbers[export().ids["M-0001"]]) == (5, 1)
    said = imports.lines(dry)
    assert said[said.index("Docket-made, renumbered by its last write: 1") + 1] == f"  M-0001 ({own.id})"
    assert imports.lines(done)[-2].endswith(f", renumbered in {done.renumbered[:7]}")


# The owner's meetings: one a day at 09:00 in Budapest, 1 to 10 October, M-0001 to M-0010 in the old ledger.
DAILY = datetime(2026, 10, 1, 7, 0, tzinfo=UTC)
# The live clone's calendar step ran on 5 October at noon, before half of them started and before the import.
STEP_AT = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def test_every_imported_meeting_keeps_its_number_over_the_step_s_and_the_joins_land_on_it(vault: Vault) -> None:
    vault.write_run("cal", [event(n, DAILY + timedelta(days=n - 1), f"Daily sync {n}") for n in range(1, 11)], STEP_AT)
    calendar.catch_up(vault, day(9, 21), now=STEP_AT)
    step = {m.id: m.number or 0 for m in ledger(vault).meetings.values()}
    assert sorted(step.values()) == list(range(1, 11))
    e = Export()
    for n in range(1, 11):
        e.meeting(n, DAILY + timedelta(days=n - 1), f"Daily sync {n}")
        e.at(n, "point", fields={"text": f"Prep for day {n}"})
    old = {n: e.ids[f"M-{n:04d}"] for n in range(1, 11)}
    dry = run(vault, e.text(), dry_run=True)
    assert dry.plan.refusals == () and dry.plan.counted is not None
    assert len(dry.plan.counted.renumbered) == 10
    run(vault, e.text())
    found = ledger(vault)
    assert {n: found.meetings[i].number for n, i in old.items()} == {n: n for n in old}
    assert sorted(found.meetings[i].number or 0 for i in step) == list(range(11, 21))
    assert found.problems == ()
    caught = calendar.catch_up(vault, day(9, 21), now=NOW)
    assert [(o.what, o.meeting) for o in caught.outcomes] == [("joined", f"M-{n:04d}") for n in old]
    assert [join.split(" ")[0] for join in dry.plan.counted.joins] == [f"M-{n:04d}" for n in old]
    found = ledger(vault)
    assert {m.event: (m.id, m.number) for m in found.meetings.values()} == {f"cal:E{n}": (i, n) for n, i in old.items()}
    assert found.problems == ()


def test_every_imported_item_keeps_its_number_over_one_docket_gave_first(vault: Vault) -> None:
    for title in ("one", "two", "three"):
        fields = item_fields("task", f"Docket's {title}", "Ben Sample", "ops", "app:tasks")
        store.write(vault, [{"op": "add", "fields": fields, "force": True}], now=STEP_AT)
    own = {i.id: i.number or 0 for i in ledger(vault).items.values()}
    assert sorted(own.values()) == [1, 2, 3]
    e = Export()
    for n, d in ((1, 1), (2, 4), (3, 8)):
        e.item(n, day(10, d), **item_fields("task", f"The owner's {n}", "Ada Example", "ops", "app:tasks"))
        e.logged(n, day(10, d), "asked in the thread")
    dry = run(vault, e.text(), dry_run=True)
    assert dry.plan.counted is not None
    assert dry.plan.counted.renumbered == sorted(f"A-{n:04d} ({i})" for i, n in own.items())
    run(vault, e.text())
    found = ledger(vault)
    assert {n: found.items[e.ids[f"A-{n:04d}"]].number for n in (1, 2, 3)} == {1: 1, 2: 2, 3: 3}
    assert sorted(found.items[i].number or 0 for i in own) == [4, 5, 6]
    assert found.problems == ()


def test_a_file_whose_adds_don_t_sort_early_is_refused(vault: Vault) -> None:
    e = export(early=False)
    adds = sum(1 for line in e.lines if line["op"] in ops.MAKES)
    said = run(vault, e.text(), dry_run=True).plan.refusals
    assert said == (
        f"{adds} add(s) don't sort early ({NAME}:1 first): an export gives each add the id 2000-01-01 plus its number "
        "in ms, so every old number holds against one Docket gave (DR-0658)",
    )


def test_a_number_the_ledger_s_own_object_claims_first_refuses_naming_both_claims(vault: Vault) -> None:
    one, two = Export(), Export()
    one.item(1, day(9, 1), **item_fields("task", "The first file's", "Ada Example", "ops", "app:tasks"))
    two.item(1, day(9, 2), key="another", **item_fields("task", "The second file's", "Ada Example", "ops", "app:x"))
    first, second = sorted([one, two], key=lambda e: e.lines[0]["id"])
    run(vault, first.text())
    (held,) = ops.read_all(vault.root).lines
    said = run(vault, second.text(), dry_run=True).plan.refusals
    assert said == (
        f"A-0001 is claimed first by the ledger's own item {held.op.id} ({held.where}), so the file's "
        f"{second.lines[0]['id']} ({NAME}:1) would lose it; every number of the file holds (DR-0658)",
    )


def test_a_catch_up_s_joins_on_its_meetings_refuse_a_rerun_until_a_revert(vault: Vault) -> None:
    vault.write_run("cal", [event(2, WEEKLY + timedelta(days=7), "Weekly pilot sync")], NOW)
    run(vault, export().text())
    calendar.catch_up(vault, day(9, 1), now=NOW)
    assert run(vault, export().text()).plan.refusals[0].startswith("1 op(s) not from the file name its objects")


# -- refusals -------------------------------------------------------------------------------------------------------


def configured(vault: Vault, **never: list[str]) -> Vault:
    path = vault.root / "vault.yaml"
    config_now = VaultConfig.load(path)
    for name, values in never.items():
        setattr(config_now.never_store, name, values)
    path.write_text(config_now.dump(), encoding="utf-8", newline="\n")
    commit_config(vault.root)
    return Vault.open(vault.root)


def test_text_is_redacted_and_a_rule_s_refusal_names_the_line_and_field_never_the_value(vault: Vault) -> None:
    vault = configured(vault, keywords=["Project Nebula"], people=["Dora Example"])
    e = export()

    def secret(line: dict[str, Any]) -> dict[str, Any]:
        if line["id"] == e.ids["A-0002"]:
            line["fields"] = {**line["fields"], "title": "password: hunter2-Example", "notes": "  a\r\nb  "}
        return line

    run(vault, e.text(secret))
    (budget,) = [i for i in ledger(vault).items.values() if i.number == 2]
    assert (budget.title, budget.notes) == ("password: [redacted:password]", "  a\nb")
    assert "hunter2" not in "".join(p.read_text(encoding="utf-8") for _, p in ops.listing(vault.root)[0])
    fresh = configured(empty_vault(vault.root.parent / "two", config()), keywords=["Project Nebula"],
                       people=["Dora Example"])  # fmt: skip

    def leaks(line: dict[str, Any]) -> dict[str, Any]:
        if line["id"] == e.ids["A-0001"]:
            line["fields"] = {**line["fields"], "who": "Dora Example"}
        if line["op"] == "log" and line.get("note") == "asked in the thread":
            line["note"] = "about project nebula"
        if line["id"] == e.ids["A-0004"]:
            line["fields"] = {**line["fields"], "source": "https://ada:hunter2-Example@example.invalid/x"}
        return line

    said = run(fresh, e.text(leaks)).plan.refusals
    assert len(said) == 3
    assert any(": who names someone on the never-store people list" in why for why in said)
    assert any(": note says something on the never-store keywords list" in why for why in said)
    assert any(": source matches the password redaction rule, and a locator can't hold a marker" in why for why in said)
    assert all(why.startswith(f"{NAME}:") for why in said)
    assert not any("hunter2" in why or "ebula" in why or "Dora" in why for why in said)


def test_lines_that_aren_t_an_export_s_ops_are_refused_each_by_its_place(vault: Vault) -> None:
    e = export()
    good = e.text().splitlines()
    first = json.loads(good[0])
    log = {"schema_version": 1, "id": ops.encode(ops.decode(first["id"]) + 500), "item": first["item"], "op": "log",
           "day": first["day"], "by": "claude", "via": "import", "note": "one more"}  # fmt: skip
    bad = [
        "not json",
        "[1, 2]",
        json.dumps({**first, "machine": "desk", "after": first["id"]}),
        json.dumps({**first, "via": "cli"}),
        json.dumps({**first, "fields": {**first["fields"], "where": "elsewhere"}}),
        json.dumps({**log, "by": "me"}),
        "   ",
        json.dumps({**log, "id": ops.encode(ops.decode(log["id"]) + 1), "schema_version": 2}),
    ]
    said = run(vault, "\n".join([*good, *bad]), dry_run=True).plan.refusals
    n = len(good)
    assert said[:6] == (
        f"{NAME}:{n + 1}: isn't JSON",
        f"{NAME}:{n + 2}: isn't a JSON object",
        f"{NAME}:{n + 3}: machine, after are the writer's; leave it to the import",
        f"{NAME}:{n + 4}: via is import on every line",
        f"{NAME}:{n + 5}: op {first['id']} is on line 1 with another text",
        f"{NAME}:{n + 6}: by is one of user, claude, sync",
    )
    assert said[6].startswith(f"{NAME}:{n + 8}: isn't a ledger op of schema 1 (schema_version: ")
    assert len(said) == 7


def test_a_line_that_isn_t_an_op_is_refused_before_the_fold_and_one_the_fold_can_t_take_after(vault: Vault) -> None:
    e = export()
    drop = e.lines[-2]  # A-0005's drop
    snooze = {**drop, "id": ops.encode(ops.decode(drop["id"]) + 1000), "op": "snooze", "until": None}
    stray = {**drop, "id": ops.encode(ops.decode(drop["id"]) + 1001), "item": ops.encode(1), "op": "log",
             "note": "on nothing"}  # fmt: skip

    def text(*more: dict[str, Any]) -> str:
        return "".join(json.dumps(line) + "\n" for line in [*e.lines, *more])

    n = len(e.lines)
    said = run(vault, text(snooze, stray), dry_run=True).plan.refusals
    assert len(said) == 1 and said[0].startswith(f"{NAME}:{n + 1}: isn't a ledger op of schema 1 (the line: ")
    said = run(vault, text(stray), dry_run=True).plan.refusals
    assert said == (f"{NAME}:{n + 1}: names an item no add made; left out",)


def test_two_of_the_file_s_items_or_meetings_on_one_number_are_refused_naming_both(vault: Vault) -> None:
    e = export()
    e.item(1, day(10, 20), key="again", **item_fields("task", "Another", "Ben Sample", "ops", "app:x"))
    e.meeting(1, WEEKLY + timedelta(days=21), "Weekly pilot sync", key="again too")
    place = {line["id"]: n for n, line in enumerate(e.lines, start=1)}
    said = run(vault, e.text(), dry_run=True).plan.refusals
    for number, kind, keys in (("A-0001", "item", ("A-0001", "again")), ("M-0001", "meeting", ("M-0001", "again too"))):
        first, lost = sorted(e.ids[key] for key in keys)
        assert (
            f"{number} is claimed first by the file's {kind} {first} ({NAME}:{place[first]}), so the file's {lost} "
            f"({NAME}:{place[lost]}) would lose it; every number of the file holds (DR-0658)"
        ) in said
    assert len(said) == 2


# -- reverting ------------------------------------------------------------------------------------------------------


def test_a_revert_deletes_every_op_file_in_one_commit_and_a_rerun_gives_the_same_ledger(vault: Vault) -> None:
    text = export().text()
    run(vault, text)
    whole = summary(vault)
    files = [rel for rel, _ in ops.listing(vault.root)[0]]
    dry = store.revert_import(vault, dry_run=True)
    assert (dry.files, dry.vias, dry.commit) == (tuple(files), {"import": len(export().lines)}, None)
    assert imports.revert_lines(dry, dry_run=True) == [
        f"would take out 2 op file(s): import {len(export().lines)}", "dry run: nothing written",
    ]  # fmt: skip
    gone = store.revert_import(vault)
    assert gone.commit is not None and ops.listing(vault.root)[0] == []
    names = sh(vault.root, "git", "show", "--name-status", "--format=%s", "HEAD").splitlines()
    assert names[0] == f"Ledger: import reverted, {len(export().lines)} ops out on test-box"
    assert sorted(names[2:]) == sorted(f"D\t{rel}" for rel in files)
    assert summary(vault) == {"items": {}, "meetings": {}, "streams": {}}
    with ledger_tables(vault) as conn:
        assert conn.execute("SELECT count(*) FROM items").fetchone() == (0,)
    assert imports.revert_lines(gone, dry_run=False)[1] == (
        f"commit {gone.commit[:7]}; every clone pulls it before it writes again"
    )
    run(vault, text)
    assert summary(vault) == whole


def test_a_revert_of_nothing_says_so(vault: Vault) -> None:
    assert imports.revert_lines(store.revert_import(vault), dry_run=False) == ["no op files: nothing to revert"]


LATER = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


def stopped_write(vault: Vault, rel: str) -> None:
    """A write that stopped after its `git add` and before its commit: its new op file left staged."""
    (vault.root / rel).write_text('{"id":"left by a stopped write"}\n', encoding="utf-8", newline="\n")
    git(vault.root, "add", "--", rel)


def ledger_in_head(vault: Vault) -> list[str]:
    """The op files HEAD holds."""
    return [rel for rel in sh(vault.root, "git", "ls-tree", "-r", "--name-only", "HEAD", "--", "ledger").split()
            if rel.endswith(ops.SUFFIX)]  # fmt: skip


def test_a_revert_unstages_a_file_a_stopped_write_left_and_the_next_write_goes_through(vault: Vault) -> None:
    run(vault, export().text())
    stopped_write(vault, "ledger/ops/2026-12-test-box.jsonl")
    reverted = store.revert_import(vault)
    assert "ledger/ops/2026-12-test-box.jsonl" in reverted.files
    assert sh(vault.root, "git", "status", "--porcelain", "--", "ledger") == ""
    fields = item_fields("task", "After the revert", "Ben Sample", "ops", "app:tasks")
    assert store.write(vault, [{"op": "add", "fields": fields}], now=LATER).commit is not None
    assert ledger_in_head(vault) == ["ledger/ops/2026-11-test-box.jsonl"]


def test_the_owner_s_own_commit_after_a_revert_brings_no_op_file_back(vault: Vault) -> None:
    run(vault, export().text())
    stopped_write(vault, "ledger/ops/2026-11-test-box.jsonl")
    store.revert_import(vault)
    (vault.root / "mine.txt").write_text("the owner's own change\n", encoding="utf-8")
    git(vault.root, "add", "mine.txt")
    git(vault.root, "commit", "-qm", "The owner's own commit")
    assert ledger_in_head(vault) == []
    assert sh(vault.root, "git", "status", "--porcelain", "--", "ledger") == ""


def test_a_clone_that_wrote_before_pulling_a_revert_meets_a_conflict_not_a_union(vault: Vault, tmp_path: Path) -> None:
    run(vault, export().text())
    clone = tmp_path / "clone"
    sh(tmp_path, "git", "clone", "-q", str(vault.root), str(clone))
    rel = sorted(rel for rel, _ in ops.listing(vault.root)[0])[-1]
    with (clone / rel).open("a", encoding="utf-8", newline="\n") as f:
        f.write('{"id":"written on the clone"}\n')
    git(clone, "commit", "-qam", "Ledger: a write on the clone")
    store.revert_import(vault)
    pulled = subprocess.run(
        ["git", "-c", "user.name=Ada Example", "-c", "user.email=ada@example.invalid", "pull", "--no-rebase", "-q",
         str(vault.root), "HEAD"], cwd=clone, capture_output=True, text=True, encoding="utf-8", check=False,
    )  # fmt: skip
    assert pulled.returncode != 0
    assert "CONFLICT (modify/delete)" in pulled.stdout + pulled.stderr


# -- the command ----------------------------------------------------------------------------------------------------


def test_the_command_runs_a_dry_run_an_import_and_a_revert(
    vault: Vault, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / NAME
    path.write_bytes(b"\xef\xbb\xbf" + export().text().encode("utf-8"))
    where = ["--vault", str(vault.root)]
    assert main(["ledger", "import", str(path), "--dry-run", *where]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "dry run: nothing written"
    assert main(["ledger", "import", str(path), "--dry-run", "--json", *where]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    monkeypatch.setattr("sys.stdin", _Stdin(export().text().encode("utf-8")))
    assert main(["ledger", "import", "-", *where]) == 0
    assert "written in 2 commit(s)" in capsys.readouterr().out
    assert main(["ledger", "import", "--revert", "--dry-run", *where]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "dry run: nothing written"
    assert main(["ledger", "import", "--revert", *where]) == 0
    assert "every clone pulls it" in capsys.readouterr().out
    assert main(["ledger", "import", str(path), "--revert", *where]) != 0
    assert "--revert takes no file" in capsys.readouterr().err
    assert main(["ledger", "import", *where]) != 0
    assert "give the export's file" in capsys.readouterr().err
    assert main(["ledger", "import", str(tmp_path / "missing.jsonl"), *where]) != 0
    assert "no file" in capsys.readouterr().err
    (tmp_path / "latin.jsonl").write_bytes(b"\xff\xfe")
    assert main(["ledger", "import", str(tmp_path / "latin.jsonl"), *where]) != 0
    assert "isn't UTF-8" in capsys.readouterr().err
    (tmp_path / "bad.jsonl").write_text("not json\n", encoding="utf-8")
    assert main(["ledger", "import", str(tmp_path / "bad.jsonl"), *where]) == 1
    assert capsys.readouterr().out.splitlines() == ["refused: 1", "  bad.jsonl:1: isn't JSON", "nothing written"]


class _Stdin:
    def __init__(self, data: bytes) -> None:
        self.buffer = _Buffer(data)


class _Buffer:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def read(self) -> bytes:
        return self.data


def test_a_dry_run_of_tables_of_its_own_writes_nothing(vault: Vault) -> None:
    with ledger_tables(vault) as conn, pytest.raises(store.LedgerError, match="only a dry run"):
        store.write_calendar(vault, [], via="cli", tables=conn)


# -- a ledger of a few years ----------------------------------------------------------------------------------------

ITEMS = 5_000
MEETINGS = 2_000
SERIES = 50
# The dry run of a ledger the perf ledger's size, and its import, a month per commit, in this time on CI's slowest
# shard, each the best of its runs: about 1.8 s and 6.5 s on a desk (DR-0654). An owner's ledger is a twentieth of it.
DRY_RUN_SECONDS = 6.0
IMPORT_SECONDS = 30.0


def big_export() -> Export:
    """test_ledger_perf's ledger as an export writes it: 5,000 items of an add and nine changes, 2,000 meetings of
    five points, each series' dates point rolled on from the meeting before."""
    e = Export()
    e.stream("pilot", date(2026, 1, 1), status="active")
    start = datetime(2026, 1, 5, 8, tzinfo=UTC)
    for n in range(1, ITEMS + 1):
        on = (start + timedelta(minutes=n * 90)).date()
        e.item(n, on, **item_fields("reply", f"Send Ada the dates, round {n}", "Ada Example", "pilot",
                                    f"mail:round {n}"))  # fmt: skip
        e.logged(n, on, "asked in the thread")
        e.logged(n, on, "triage: chase", "user", ("triage", {"verdict": "chase"}))
        e.logged(n, on, "due: - -> 2026-12-01", "claude", ("set", {"fields": {"due": "2026-12-01"}}))
        e.logged(n, on, "snoozed to 2026-12-24", "user", ("snooze", {"until": "2026-12-24"}))
    before: str | None = None
    for n in range(1, MEETINGS + 1):
        e.meeting(n, start + timedelta(hours=n * 4), f"Weekly pilot sync {n // SERIES}")
        dates = e.at(n, "point", fields={"text": "The pilot's dates", **({"from": before} if before else {})})
        if before is not None:
            e.at(n - 1, "point_set", point=before, fields={"state": "rolled", "to": dates})
        for k in range(4):
            point = e.at(n, "point", fields={"text": f"Point {k}"})
            e.at(n, "point_set", point=point, fields={"state": "answered", "answer": "agreed in the call"})
        before = None if n % SERIES == 0 else dates
    return e


@pytest.mark.nightly
@pytest.mark.perf
def test_a_ledger_the_perf_ledger_s_size_dry_runs_and_imports_in_time(vault: Vault) -> None:
    text = big_export().text()
    lines = text.count("\n")
    dry = best_of(3, lambda: run(vault, text, dry_run=True))
    # Best of two, as one import takes over 3 s on CI; each run imports into a vault of its own.
    vaults = iter((vault, made(vault.root.parent / "again")))
    done = best_of(2, lambda into: (run(into, text), into), lambda: next(vaults))
    (imported, into), planned = done.result, dry.result.plan
    print(f"ledger import of {lines:,} ops: dry run {dry.seconds:.2f} s ({dry.runs()}), import {done.seconds:.2f} s "
          f"({done.runs()}), {len(imported.commits)} writes")  # fmt: skip
    assert planned.refusals == () and planned.counted is not None
    assert (sum(planned.counted.log_entries.values()), planned.counted.meetings) == (ITEMS * 4, MEETINGS)
    assert (imported.plan.ops, len(ledger(into).items)) == (lines, ITEMS)
    if not traced():
        assert dry.seconds < DRY_RUN_SECONDS, f"the dry run took {dry.seconds:.2f} s, over {DRY_RUN_SECONDS} s"
        assert done.seconds < IMPORT_SECONDS, f"the import took {done.seconds:.2f} s, over {IMPORT_SECONDS} s"
