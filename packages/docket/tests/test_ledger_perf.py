"""A ledger of a few years: 5,000 items and 50,000 ops read and folded by `load`, and folded into the index's tables,
each in under a second; one op more, as a write leaves it, read into them in a tenth of one (DR-0616).

The ops are written as the writer writes them, a month file per machine, each item an add and nine changes. Wall
time is asserted only when no tracer runs; the files are built before the clock starts.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from docket_testkit import traced
from ledger_kit import ADD_FIELDS

from docket.index import ledger as ledger_index
from docket.index.schema import LEDGER_TABLES
from docket.ledger import load, ops
from docket.ledger.ops import Op

ITEMS = 5_000
SECONDS = 1.0
CATCH_UP_SECONDS = 0.1
CHANGES = ("log", "triage", "set", "log", "snooze", "triage", "log", "done", "reopen")


def build(root: Path) -> None:
    files: dict[str, list[str]] = {}
    highest: str | None = None
    start = datetime(2026, 1, 5, 8, tzinfo=UTC)
    for n in range(ITEMS):
        when = start + timedelta(minutes=n * 90)
        machine = ("desk", "laptop")[n % 2]
        ids = ops.mint(highest, when, 1 + len(CHANGES))
        highest = ids[-1]
        keys = {
            "log": {"note": "chased in the thread again, no answer yet"},
            "triage": {"verdict": "chase"},
            "set": {"fields": {"due": "2026-12-01", "notes": "ask twice, then call"}},
            "snooze": {"until": when.date() + timedelta(days=3)},
        }
        made = [Op(schema_version=1, id=ids[0], item=ids[0], op="add", day=when.date(), at=when.replace(microsecond=0),
                   machine=machine, by="claude", via="cli", batch=ids[0], number=n + 1,
                   fields={**ADD_FIELDS, "title": f"Send Ada the dates, round {n}"})]  # fmt: skip
        made += [
            Op(schema_version=1, id=ids[k], item=ids[0], op=kind, day=when.date(), machine=machine, by="sync",
               via="tool", batch=ids[0], after=ids[k - 1], **keys.get(kind, {}))
            for k, kind in enumerate(CHANGES, start=1)
        ]  # fmt: skip
        for o in made:
            files.setdefault(ops.rel_of(o), []).append(o.text())
    for rel, texts in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(texts) + "\n", encoding="utf-8", newline="\n")


@pytest.mark.perf
def test_a_few_thousand_items_read_and_fold_in_under_a_second(tmp_path: Path) -> None:
    build(tmp_path)
    began = time.perf_counter()
    ledger = load(tmp_path)
    elapsed = time.perf_counter() - began
    print(f"ledger fold: {elapsed:.2f} s for {ITEMS:,} items")
    assert len(ledger.items) == ITEMS
    assert {item.state for item in ledger.items.values()} == {"open"}
    assert ledger.problems == ()
    if not traced():
        assert elapsed < SECONDS, f"reading and folding {ITEMS:,} items took {elapsed:.2f} s"


@pytest.mark.perf
def test_a_few_thousand_items_fold_into_the_index_in_under_a_second(tmp_path: Path) -> None:
    """The rebuild's ledger part, on a file set up as a rebuild sets its new index up; then a write's catch-up."""
    root = tmp_path / "vault"
    build(root)
    conn = sqlite3.connect(tmp_path / "index.sqlite", isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA synchronous = OFF")
        conn.executescript(LEDGER_TABLES)
        began = time.perf_counter()
        conn.execute("BEGIN")
        read = ledger_index.rebuild(conn, root)
        conn.execute("COMMIT")
        elapsed = time.perf_counter() - began
        counts = [conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("items", "item_log")]  # noqa: S608
        item, text = conn.execute("SELECT item, keys FROM item_log ORDER BY id DESC LIMIT 1").fetchone()
        last = Op.model_validate_json(text)
        (op_id,) = ops.mint(last.id, datetime(2026, 12, 1, tzinfo=UTC), 1)
        more = last.model_copy(update={"id": op_id, "op": "log", "note": "one more", "after": last.id})
        with (root / ops.rel_of(more)).open("a", encoding="utf-8", newline="\n") as f:
            f.write(more.text() + "\n")
        began = time.perf_counter()
        conn.execute("BEGIN IMMEDIATE")
        grown = ledger_index.update(conn, root)
        conn.execute("COMMIT")
        caught = time.perf_counter() - began
        logged = conn.execute("SELECT line FROM item_log WHERE id = ?", (op_id,)).fetchone()
    finally:
        conn.close()
    print(f"ledger into the index: {elapsed:.2f} s for {ITEMS:,} items; one op more: {caught * 1000:.0f} ms")
    assert (read, *counts) == (ITEMS * (1 + len(CHANGES)), ITEMS, ITEMS * (1 + len(CHANGES)))
    assert (grown, logged, item) == (1, ("one more",), last.item)
    if not traced():
        assert elapsed < SECONDS, f"folding {ITEMS:,} items into the index took {elapsed:.2f} s"
        assert caught < CATCH_UP_SECONDS, f"reading one op more into the index took {caught:.2f} s"
