"""The whole ledger, `docket ledger show --all --json` (DR-0659): every item, meeting and stream that stands, archived
too, each exactly as `docket ledger show` gives it by its ref; the op files' stamp, taken under the vault's lock with
the read under it, and the lock's file, which a script's own `FileLock` takes; the golden, stamped; and the command's
refusals. Invented people only."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from docket_testkit import empty_vault, make_config
from filelock import FileLock, Timeout
from ledger_kit import ADD_FIELDS, fresh_tables, meeting, op, point, point_set, roll, ulid, write_ops

from docket.cli import main
from docket.index import ledger as ledger_index
from docket.index import rebuild
from docket.ledger import ops, views, whole
from docket.ledger.ops import Op
from docket.ledger.views import Ask, LedgerRead
from docket.vault.vault import Vault

TZ = ZoneInfo("Europe/Budapest")
# Tuesday 20 October, 10:00 in Budapest.
AT = datetime(2026, 10, 20, 8, 0, tzinfo=UTC)
LONG = "Send the pilot's dates to everyone on the list before the steering group meets on Thursday afternoon"
GOLDEN = Path(__file__).parent / "fixtures" / "ledger-show-all.json"
# What the golden holds for what differs from one run to the next.
ROOT, LOCK, TIME = "<the vault's root>", "<the vault lock's file>", 0


def item(n: int, number: int, **fields: Any) -> Op:
    return op("add", n, number=number, fields={**ADD_FIELDS, **fields})


def seed() -> list[Op]:
    """Items in each state, triaged, snoozed, moved, reopened, archived, one undone and one imported; meetings with
    points answered, proposed and rolled, one archived and one a calendar record's add continues; a number two items
    and two meetings claim; and two streams."""
    return [
        item(1, 1, opened="2026-10-10", due="2026-10-18", meeting=ulid(20)),
        op("triage", 2, 1, verdict="chase"),
        op("material", 3, 1, ref="https://example.invalid/venues", label="venue list"),
        item(4, 2, kind="waiting", title="Ben's numbers", who="Ben Example", notes="ask twice"),
        op("snooze", 5, 4, until=date(2026, 10, 25)),
        item(6, 3, kind="task", title=LONG, who="Cleo Example", stream="ops", due="2026-10-22", meeting=ulid(20),
             description="the long one"),
        item(8, 4, kind="decision", title="Pick the vendor", opened="2026-10-01", outcome="vendor two"),
        op("done", 9, 8),
        op("archive", 10, 8),
        item(11, 5, title="Answer the survey", who="Dan Example"),
        op("drop", 12, 11),
        item(13, 6, title="Confirm the venue", due="2026-10-21"),
        op("triage", 14, 13, verdict="chase"),
        op("undo", 15, 13, of=ulid(14)),
        op("set", 16, 13, fields={"due": "2026-10-23"}),
        op("reopen", 17, 13),
        # An add undone stands nowhere.
        item(18, 7, title="Added by mistake"),
        op("undo", 19, 18, of=ulid(18)),
        meeting(20, number=1, subject="Weekly pilot sync", start="2026-10-21T09:00+02:00", end="2026-10-21T09:30+02:00",
                organiser="Ada Example", attendees=["Ada Example", "Ben Example"], stream="pilot",
                notes="bring numbers", where="Room 3"),
        point(21, 20, "The pilot's dates", item=ulid(1)),
        point(22, 20, "The budget"),
        point_set(23, 20, 22, state="answered", answer="agreed"),
        point(24, 20, "Ben's numbers", item=ulid(4), proposed=True),
        meeting(30, number=2, subject="Weekly pilot sync", start="2026-10-28T09:00+01:00",
                end="2026-10-28T09:30+01:00"),
        *roll(31, 20, 21, 30),
        meeting(33, number=3, subject="Archived retro", start="2026-10-02T10:00+02:00", end="2026-10-02T11:00+02:00"),
        op("archive", 34, 33),
        # The calendar step's add for an event continues the old meeting, whose id and number hold.
        meeting(35, number=4, subject="Vendor call", start="2026-10-22T14:00+02:00", end="2026-10-22T15:00+02:00"),
        meeting(36, "calendar:E1", number=4, subject="Vendor call", start="2026-10-22T14:00+02:00",
                end="2026-10-22T15:00+02:00", continues=ulid(35), response="tentative", by="sync"),
        # Another clone claimed A-0008 and M-0005 first: both of each are named by their ids.
        item(40, 8, title="Ours"),
        replace(item(41, 8, title="Theirs"), id=ulid(0), item=ulid(0), machine="laptop"),
        meeting(42, number=5, subject="Our sync"),
        replace(meeting(43, number=5, subject="Their sync"), id=ulid(9, ms=1), item=ulid(9, ms=1), machine="laptop"),
        # An imported item: its changes show no line.
        op("add", 44, via="import", by="user", number=9, fields={**ADD_FIELDS, "title": "From the old ledger"}),
        op("set", 45, 44, via="import", by="user", fields={"due": "2026-10-30"}),
        op("log", 46, 44, via="import", by="user", note="asked in the thread"),
        op("stream", 50, fields={"name": "pilot", "status": "active", "next": "Book the room", "deadline": "2026-10-30",
                                 "where": "Waiting on the venue"}),
        op("stream", 51, fields={"name": "ops", "status": "waiting"}),
    ]  # fmt: skip


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    write_ops(tmp_path, seed())
    c = fresh_tables()
    ledger_index.rebuild(c, tmp_path)
    return c


def ref_of(found: LedgerRead) -> str:
    assert found.detail is not None
    rows = {"item": found.items, "meeting": found.meetings, "stream": found.streams}[found.detail.kind]
    return rows[0].ref


def test_every_object_comes_as_show_gives_it_by_its_ref(conn: sqlite3.Connection) -> None:
    items, meetings, streams = views.every(conn, TZ, AT)
    for found in (*items, *meetings, *streams):
        shown = views.read(conn, Ask(view="show", ref=ref_of(found)), TZ, AT)
        assert found.model_dump(mode="json") == shown.model_dump(mode="json"), ref_of(found)
    assert [ref_of(f) for f in items] == [
        "A-0004", "A-0001", "A-0002", "A-0003", "A-0005", "A-0006", ulid(0), ulid(40), "A-0009",
    ]  # fmt: skip
    assert [ref_of(f) for f in meetings] == ["M-0003", ulid(9, ms=1), ulid(42), "M-0001", "M-0004", "M-0002"]
    assert [ref_of(f) for f in streams] == ["ops", "pilot"]
    # Archived ones too; an undone add's item nowhere.
    assert [f.items[0].archived for f in items].count(True) == 1 and meetings[0].meetings[0].archived
    assert "Added by mistake" not in json.dumps([f.model_dump(mode="json") for f in items])


def test_a_number_two_objects_claim_comes_with_no_other_holder(conn: sqlite3.Connection) -> None:
    items, meetings, _ = views.every(conn, TZ, AT)
    clashed = [f for f in (*items, *meetings) if ref_of(f) in (ulid(0), ulid(40), ulid(9, ms=1), ulid(42))]
    assert len(clashed) == 4
    assert all(f.detail is not None and f.detail.also == [] for f in clashed)


def test_a_joined_meeting_s_history_holds_both_adds_and_an_imported_item_s_only_its_log(
    conn: sqlite3.Connection,
) -> None:
    items, meetings, _ = views.every(conn, TZ, AT)
    (joined,) = [f for f in meetings if ref_of(f) == "M-0004"]
    assert joined.detail is not None
    assert (joined.meetings[0].id, joined.detail.event) == (ulid(35), "calendar:E1")
    assert [c.id for c in joined.detail.history] == [ulid(35), ulid(36)]
    (old,) = [f for f in items if ref_of(f) == "A-0009"]
    assert old.detail is not None and old.items[0].due == date(2026, 10, 30)
    assert [c.line for c in old.detail.history] == ["asked in the thread"]


# -- the command and its stamp ------------------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Vault:
    v = empty_vault(tmp_path, make_config())
    monkeypatch.setattr(views, "now", lambda: AT)
    write_ops(v.root, seed())
    return v


def run(capsys: pytest.CaptureFixture[str], vault: Vault, *args: str) -> tuple[int, str, str]:
    code = main(["ledger", "show", *args, "--vault", str(vault.root)])
    out, err = capsys.readouterr()
    return code, out, err.strip()


@pytest.mark.parametrize("route", ["memory", "index"])
def test_the_command_gives_the_golden_stamped_with_the_files_and_the_lock(
    vault: Vault, route: str, capsys: pytest.CaptureFixture[str]
) -> None:
    if route == "index":
        rebuild(vault)
    code, out, _ = run(capsys, vault, "--all", "--json")
    assert code == 0 and out.isascii()
    found = json.loads(out)
    stamp = found["stamp"]
    listed = ops.listing(vault.root)[0]
    assert (stamp["root"], stamp["lock"]) == (vault.root.absolute().as_posix(), str(vault.lock.path))
    assert Path(stamp["root"]).is_absolute() and "\\" not in stamp["root"]
    assert all((Path(stamp["root"]) / f["path"]).is_file() for f in stamp["files"])
    assert stamp["files"] == [
        {"path": rel, "size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns} for rel, path in listed
    ]
    files = [{**f, "mtime_ns": TIME} for f in stamp["files"]]
    golden = {**found, "stamp": {"root": ROOT, "files": files, "lock": LOCK}}
    assert golden == json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_each_object_in_the_command_s_json_is_show_s_json(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, vault, "--all", "--json")
    assert code == 0
    found = json.loads(out)
    for kind in ("items", "meetings", "streams"):
        for one in found[kind]:
            ref = one[kind][0]["ref"]
            code, shown, _ = run(capsys, vault, ref, "--json")
            assert (code, one) == (0, json.loads(shown)), ref


def test_the_stamp_and_the_read_are_taken_under_the_vault_lock(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    held: list[bool] = []
    real_listing, real_every = ops.listing, views.every

    def listing(root: Path) -> Any:
        held.append(locked(vault))
        return real_listing(root)

    def every(*args: Any) -> Any:
        held.append(locked(vault))
        return real_every(*args)

    monkeypatch.setattr(ops, "listing", listing)
    monkeypatch.setattr(views, "every", every)
    whole.read(vault, AT)
    # The stamp's listing first, the fold's in memory, with no index, after it.
    assert len(held) > 2 and all(held)
    assert not locked(vault)


def locked(vault: Vault) -> bool:
    """Whether another process's FileLock on the stamp's lock file would wait: what a script asks."""
    outcome: list[bool] = []

    def other() -> None:
        try:
            with FileLock(str(vault.lock.path), timeout=0):
                outcome.append(False)
        except Timeout:
            outcome.append(True)

    thread = threading.Thread(target=other)
    thread.start()
    thread.join()
    return outcome[0]


def test_a_write_after_the_read_moves_the_stamp(vault: Vault) -> None:
    before = whole.read(vault, AT).stamp
    write_ops(vault.root, [op("log", 60, 1, note="asked again")])
    after = whole.read(vault, AT).stamp
    assert before.lock == after.lock
    assert [f.path for f in before.files] == [f.path for f in after.files]
    assert [f.size for f in after.files] != [f.size for f in before.files]


def test_the_stamp_lists_only_the_op_files_every_reader_reads(vault: Vault) -> None:
    folder = vault.root / ops.OPS_DIR
    (folder / ops.KEEP).write_text("", encoding="utf-8")
    (folder / "notes.txt").write_text("not an op file\n", encoding="utf-8")
    (folder / "2026-13-desk.jsonl").write_text("", encoding="utf-8")
    (folder / "2026-11-desk.jsonl").mkdir()
    stamp = whole.read(vault, AT).stamp
    assert [f.path for f in stamp.files] == ["ledger/ops/2026-10-desk.jsonl", "ledger/ops/2026-10-laptop.jsonl"]


def test_the_command_refuses_all_with_a_ref_or_without_json_and_nothing_at_all(
    vault: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    refused = "docket: --all takes no ref, and gives JSON only: docket ledger show --all --json"
    assert run(capsys, vault, "--all") == (2, "", refused)
    assert run(capsys, vault, "A-1", "--all", "--json") == (2, "", refused)
    assert run(capsys, vault) == (2, "", "docket: give an item's A- number, a meeting's M- number, an id or a "
                                          "stream's name, or --all --json")  # fmt: skip


def test_an_empty_ledger_gives_no_objects_and_no_files(tmp_path: Path) -> None:
    v = empty_vault(tmp_path, make_config())
    found = whole.read(v, AT)
    assert (found.items, found.meetings, found.streams, found.stamp.files) == ([], [], [], [])
    assert found.today == date(2026, 10, 20)
