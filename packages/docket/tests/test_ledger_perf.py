"""A ledger of a few years: 5,000 items and 50,000 ops read and folded by `load` in under a second (DR-0616).

The ops are written as the writer writes them, a month file per machine, each item an add and nine changes. Wall
time is asserted only when no tracer runs; the files are built before the clock starts.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from docket_testkit import traced
from ledger_kit import ADD_FIELDS

from docket.ledger import load, ops
from docket.ledger.ops import Op

ITEMS = 5_000
SECONDS = 1.0
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
