"""A ledger of a few years: 5,000 items and 50,000 ops, and 2,000 meetings with 20,000 point ops, read and folded by
`load`, 500 of its ops read as a forget's masks (DR-0695), in under a second and a half; all of it folded into the
index's tables in under two seconds; one op more, on an item or a meeting, as a write leaves it, read into them in a
tenth of a second; a write touching one meeting planned in a tenth of a second, reading only its part (DR-0616,
DR-0628, DR-0633, DR-0635); a snapshot of it, every item open, read from the tables with its lines in a fifth of a
second (DR-0638); the day's route on a vault holding it in half a second (DR-0672); and all of it as `docket ledger
show --all --json` gives it, stamped, with its JSON, in under two seconds (DR-0659).

The ops are written as the writer writes them, a month file per machine, each item an add and nine changes, each
meeting four points answered and a point rolled from the meeting before it in its weekly series of fifty. Wall time
is asserted only when no tracer runs; the files are built, and read once, before the clock starts, so a virus scanner
reading a file new to it on its first open isn't timed.
"""

from __future__ import annotations

import functools
import json
import os
import sqlite3
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from docket_testkit import Best, best_of, empty_vault, make_config, traced
from ledger_kit import CHANGES, ITEMS, MEETINGS, SERIES, build
from starlette.testclient import TestClient

from docket.index import build as index_build
from docket.index import ledger as ledger_index
from docket.index.ledger import Held
from docket.index.schema import LEDGER_TABLES
from docket.ledger import load, ops, store, views, whole
from docket.ledger.ops import Op
from docket.service.app import create_app
from docket.service.state import Settings

FOLD_SECONDS = 1.5
# A full rebuild runs only on a schema bump or a forget, never on the daily path (DR-0635).
REBUILD_SECONDS = 2.0
CATCH_UP_SECONDS = 0.1
WRITE_SECONDS = 0.1
SNAPSHOT_SECONDS = 0.2
DAY_SECONDS = 0.5
SHOW_ALL_SECONDS = 2.0


def one_more(root: Path, last: Op, kind: str, **keys: object) -> Op:
    """One op more after `last`, as a write leaves it: appended to its month file."""
    (op_id,) = ops.mint(last.id, datetime(2026, 12, 1, tzinfo=UTC), 1)
    more = replace(last, **{"id": op_id, "op": kind, "after": last.id, "point": None, "fields": None, **keys})
    with (root / ops.rel_of(more)).open("a", encoding="utf-8", newline="\n") as f:
        f.write(more.text() + "\n")
    return more


@pytest.mark.perf
def test_a_few_thousand_items_and_meetings_read_and_fold_in_a_second_and_a_half(tmp_path: Path) -> None:
    build(tmp_path)
    logs = [line.op.id for line in ops.read_all(tmp_path, masked=frozenset()).lines if line.op.op == "log"]
    # A forget's 500 masked ops, read as their masks (DR-0695).
    listed = frozenset(logs[::30][:500])
    fold = best_of(3, lambda: load(tmp_path, masked=listed))
    ledger, elapsed = fold.result, fold.seconds
    print(f"ledger fold: {elapsed:.2f} s ({fold.runs()}) for {ITEMS:,} items and {MEETINGS:,} meetings")
    masked = [line.op.note for one in ledger.folds.values() for line in one.lines if line.op.id in listed]
    assert len(listed) == 500 and masked == [ops.MARK] * 500
    assert (len(ledger.items), len(ledger.meetings)) == (ITEMS, MEETINGS)
    assert {item.state for item in ledger.items.values()} == {"open"}
    open_points = [p.text for m in ledger.meetings.values() for p in m.open_points()]
    assert open_points == ["The pilot's dates"] * (MEETINGS // SERIES)
    assert ledger.problems == ()
    if not traced():
        assert elapsed < FOLD_SECONDS, f"reading and folding the ledger took {elapsed:.2f} s, over {FOLD_SECONDS} s"


def caught_up(conn: sqlite3.Connection, root: Path) -> int:
    conn.execute("BEGIN IMMEDIATE")
    grown = ledger_index.update(conn, root)
    conn.execute("COMMIT")
    return grown


@pytest.mark.perf
def test_a_few_thousand_items_and_meetings_fold_into_the_index_in_under_two_seconds(tmp_path: Path) -> None:
    """The rebuild's ledger part, on a file set up as a rebuild sets its new index up; then a write's catch-up, of one
    op on an item and one on a meeting in the middle of its series, which folds that series again."""
    root = tmp_path / "vault"
    build(root)
    opened: list[sqlite3.Connection] = []

    def new_index() -> sqlite3.Connection:
        """Each rebuild into an index file of its own, set up as a rebuild sets its new index up."""
        conn = sqlite3.connect(tmp_path / f"index-{len(opened)}.sqlite", isolation_level=None)
        opened.append(conn)
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA synchronous = OFF")
        conn.executescript(LEDGER_TABLES)
        return conn

    def rebuilt(conn: sqlite3.Connection) -> int:
        conn.execute("BEGIN")
        read = ledger_index.rebuild(conn, root)
        conn.execute("COMMIT")
        return read

    try:
        rebuild = best_of(3, rebuilt, new_index)
        conn, read, elapsed = opened[-1], rebuild.result, rebuild.seconds
        counts = [
            conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: S608 - the tables' own names
            for t in ("items", "item_log", "meetings", "points", "meeting_log")
        ]
        (text,) = conn.execute("SELECT keys FROM item_log ORDER BY id DESC LIMIT 1").fetchone()
        # Each catch-up reads one op more of its own, appended before it as a write leaves it.
        written = [ops.read_op(text)]

        def logged() -> None:
            written.append(one_more(root, written[-1], "log", note="one more"))

        item = best_of(3, lambda _: caught_up(conn, root), logged)
        item_grown, item_caught = item.result, item.seconds
        item_line = conn.execute("SELECT line FROM item_log WHERE id = ?", (written[-1].id,)).fetchone()
        (meeting_id,) = conn.execute("SELECT id FROM meetings WHERE number = ?", (MEETINGS - SERIES // 2,)).fetchone()
        (point_id,) = conn.execute("SELECT id FROM points WHERE meeting = ? AND n = 2", (meeting_id,)).fetchone()
        (text,) = conn.execute("SELECT keys FROM meeting_log ORDER BY id DESC LIMIT 1").fetchone()
        written = [replace(ops.read_op(text), item=meeting_id)]
        # A new answer each run: the same answer again would be idle.
        days = iter(("the 21st", "the 22nd", "the 23rd"))
        given: list[str] = []

        def answered() -> None:
            given.append(next(days))
            written.append(one_more(root, written[-1], "point_set", point=point_id, fields={"answer": given[-1]}))

        meeting = best_of(3, lambda _: caught_up(conn, root), answered)
        meeting_grown, meeting_caught = meeting.result, meeting.seconds
        answer = conn.execute("SELECT answer FROM points WHERE id = ?", (point_id,)).fetchone()
        fate = conn.execute("SELECT fate FROM meeting_log WHERE id = ?", (written[-1].id,)).fetchone()
    finally:
        for conn in opened:
            conn.close()
    print(
        f"ledger into the index: {elapsed:.2f} s ({rebuild.runs()}) for {ITEMS:,} items and {MEETINGS:,} meetings; "
        f"one op more: {item_caught * 1000:.0f} ms ({item.runs('ms')}) on an item, {meeting_caught * 1000:.0f} ms "
        f"({meeting.runs('ms')}) on a meeting"
    )
    assert read == ITEMS * (1 + len(CHANGES)) + MEETINGS * 11 - MEETINGS // SERIES
    assert counts == [ITEMS, ITEMS * (1 + len(CHANGES)), MEETINGS, MEETINGS * 5, MEETINGS * 11 - MEETINGS // SERIES]
    assert (item_grown, item_line) == (1, ("one more",))
    assert (meeting_grown, answer, fate) == (1, (given[-1],), ("applied",))
    if not traced():
        assert elapsed < REBUILD_SECONDS, (
            f"folding the ledger into the index took {elapsed:.2f} s, over {REBUILD_SECONDS} s"
        )
        assert item_caught < CATCH_UP_SECONDS, (
            f"reading one op more on an item took {item_caught:.2f} s, over {CATCH_UP_SECONDS} s"
        )
        assert meeting_caught < CATCH_UP_SECONDS, (
            f"reading one op more on a meeting took {meeting_caught:.2f} s, over {CATCH_UP_SECONDS} s"
        )


@pytest.mark.perf
def test_a_write_touching_one_meeting_reads_only_its_series(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A point added to a meeting of a ledger that size, planned against the index as a write is: it reads the
    fifty meetings of that meeting's series and nothing else, in a tenth of a second. Dry, so git's commit isn't
    timed; the files' times set an hour back, as files a write didn't just write are."""
    vault = empty_vault(tmp_path, make_config())
    build(vault.root)
    hour_ago = time.time_ns() - 3600 * 10**9
    for path in (vault.root / ops.OPS_DIR).iterdir():
        os.utime(path, ns=(hour_ago, hour_ago))
    index_build.rebuild(vault)
    read: list[int] = []
    real = Held.object_lines
    monkeypatch.setattr(Held, "object_lines", lambda self, adds: read.append(len(adds)) or real(self, adds))
    number = MEETINGS - SERIES // 2
    change = {"op": "point", "item": f"M-{number:04d}", "fields": {"text": "Bring the dates"}}
    # Dry, each run plans the same write; it counts only its own reads.
    planned = best_of(
        3, lambda _: store.write(vault, [change], dry_run=True, now=datetime(2026, 12, 1, tzinfo=UTC)), read.clear
    )
    written, elapsed = planned.result, planned.seconds
    print(
        f"a write on one meeting: {elapsed * 1000:.0f} ms ({planned.runs('ms')}), reading {sum(read)} of "
        f"{MEETINGS:,} meetings"
    )
    assert ([o.op for o in written.ops], list(written.numbers.values()), read) == (["point"], [number], [SERIES])
    if not traced():
        assert elapsed < WRITE_SECONDS, f"planning a write on one meeting took {elapsed:.2f} s, over {WRITE_SECONDS} s"


@pytest.mark.perf
def test_a_snapshot_of_a_ledger_that_size_reads_from_the_index_in_a_fifth_of_a_second(tmp_path: Path) -> None:
    """Every item is open, so the snapshot lists all 5,000, and the 40 meetings left with an open point beside those
    of its window; read once before the clock starts, so SQLite's cache is warm as a running service's is. The other
    views are timed for the print only."""
    root = tmp_path / "vault"
    build(root)
    conn = sqlite3.connect(tmp_path / "index.sqlite", isolation_level=None)
    tz = ZoneInfo("Europe/Budapest")
    at = datetime(2026, 6, 1, 8, tzinfo=UTC)
    timed: dict[str, Best[tuple[views.LedgerRead, list[str]]]] = {}

    def read_and_lines(ask: views.Ask) -> tuple[views.LedgerRead, list[str]]:
        found = views.read(conn, ask, tz, at)
        return found, views.lines(found, tz)

    try:
        conn.executescript(LEDGER_TABLES)
        conn.execute("BEGIN")
        ledger_index.rebuild(conn, root)
        conn.execute("COMMIT")
        asks = (views.Ask(), views.Ask(view="queue"), views.Ask(view="list", state="all"), views.Ask(view="meetings"))
        for ask in asks:
            views.lines(views.read(conn, ask, tz, at), tz)
            timed[ask.view] = best_of(3, functools.partial(read_and_lines, ask))
    finally:
        conn.close()
    print(
        "ledger reads: "
        + ", ".join(f"{view} {run.seconds * 1000:.0f} ms ({run.runs('ms')})" for view, run in timed.items())
    )
    snapshot, text = timed["snapshot"].result
    assert (snapshot.counts.open, snapshot.counts.meetings, len(text)) == (ITEMS, 87, 4 + ITEMS + 87 + 435)
    assert len([m for m in snapshot.meetings if m.points_open]) == MEETINGS // SERIES
    if not traced():
        elapsed = timed["snapshot"].seconds
        assert elapsed < SNAPSHOT_SECONDS, f"a snapshot took {elapsed:.2f} s, over {SNAPSHOT_SECONDS} s"


@pytest.mark.perf
def test_the_day_s_route_on_a_vault_holding_a_ledger_that_size_answers_in_half_a_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/day as the session start's hook asks it, on a vault with that ledger and its index built: the vault
    opened, the snapshot of every open item and the day's meetings, the cursor files, the status files and the
    calendar's backlog, the day cut to its room. Asked once before the clock starts, as a running service has been."""
    vault = empty_vault(tmp_path, make_config())
    build(vault.root)
    index_build.rebuild(vault)
    monkeypatch.setattr(views, "now", lambda: datetime(2026, 6, 1, 8, tzinfo=UTC))
    token = "t0ken-for-tests-only"  # noqa: S105 - an invented token
    app = create_app(Settings(port=8741, vault=vault.root, token=lambda: token, store="the system keychain"))
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app, base_url="http://127.0.0.1:8741") as client:
        client.get("/api/day", params={"room": 43}, headers=headers)
        asked = best_of(3, lambda: client.get("/api/day", params={"room": 43}, headers=headers))
    print(f"the day's route: {asked.seconds * 1000:.0f} ms ({asked.runs('ms')}) for {ITEMS:,} open items and "
          f"{MEETINGS:,} meetings")  # fmt: skip
    answer = asked.result
    assert answer.status_code == 200, answer.text
    assert len(answer.json()["lines"]) <= 43
    if not traced():
        assert asked.seconds < DAY_SECONDS, f"the day's route took {asked.seconds:.2f} s, over {DAY_SECONDS} s"


@pytest.mark.perf
def test_the_whole_ledger_shows_with_its_stamp_as_json_in_under_two_seconds(tmp_path: Path) -> None:
    """Every item and meeting as show gives it, from the index's tables brought up to date, stamped under the vault's
    lock, and the JSON the command prints: what an owner's mirror waits for. Read once before the clock starts."""
    vault = empty_vault(tmp_path, make_config())
    build(vault.root)
    index_build.rebuild(vault)
    at = datetime(2026, 6, 1, 8, tzinfo=UTC)

    def shown() -> str:
        return whole.as_json(whole.read(vault, at))

    shown()
    run = best_of(3, shown)
    found = json.loads(run.result)
    print(f"ledger show --all --json: {run.seconds:.2f} s ({run.runs()}), {len(run.result) / 1e6:.1f} MB")
    assert (len(found["items"]), len(found["meetings"]), found["streams"]) == (ITEMS, MEETINGS, [])
    assert sum(len(m["points"]) for m in found["meetings"]) == MEETINGS * 5
    assert len(found["stamp"]["files"]) == len(ops.listing(vault.root)[0])
    if not traced():
        assert run.seconds < SHOW_ALL_SECONDS, f"show --all took {run.seconds:.2f} s, over {SHOW_ALL_SECONDS} s"
