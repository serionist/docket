"""The invented worlds `uv run devkit ui` serves for the screenshot loop (DR-0712), built with the test kits' own
records, people and ledger: run as a script, `python ui_world.py <folder> <world>`, in the environment devkit ui gives
it (its own config and state folders, the failing keyring), which is where the file token it makes lands.

| World | What |
|---|---|
| `empty` | a new vault: no items, nothing staged, a live source never synced |
| `week` | a working week: day_kit's ledger and more items beside it, one whose snooze ended yesterday, the files |
| | its materials name, staging_kit's three records waiting, a live source due a sync; meetings around today, |
| | one under way, a recording, a point rolled on, and Ben's entity and brief |
| `meetings` | the week with its index built, so a meeting's people link to their briefs from the first read |

Invented people only: Ada Example, Ben Sample and Cleo Example.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from briefs_kit import brief
from day_kit import ZONE, add, day, seed
from docket_testkit import fixture_records, make_config, make_record
from ledger_kit import write_ops
from staging_kit import mail, memo, transcript

from docket import recall
from docket.index import build as index_build
from docket.ledger import ops, store
from docket.service.token import Tokens
from docket.vault.config import SourceConfig, VaultConfig
from docket.vault.gate import Gate
from docket.vault.vault import Vault

WORLDS = ("empty", "week", "meetings")
MACHINE = "ui-box"
PLAN_TEXT = """# The plan

Book the room first, then the caterer.

- Room: the long hall, 40 seats, the 12th or the 19th
- Caterer: two menus, the second one vegetarian
- Badges: printed the day before, collected at the door
"""


def config() -> VaultConfig:
    made = make_config()
    made.primary_machine = MACHINE
    # A live source: one the capture hook posts for, so the Sync page and the sidebar mark it due until it syncs.
    made.sources["chat"] = SourceConfig(tools=["read_channel"])
    made.sources["memo"] = SourceConfig()
    return made


def busier() -> list[dict[str, Any]]:
    """More of the week beside day_kit's seven, so a page shows a list as full as a working day's: calls of every
    kind, dues in every group, one snoozed and one closed."""
    ops: list[dict[str, Any]] = [
        add("brief", "reply", "Answer Ben about the pilot budget", "Ben Sample", "pilot", due=day(-4)),
        {"op": "triage", "item": "$brief", "verdict": "me"},
        add("deck", "task", "Send the deck to the venue", "Ada Example", "ops", due=day(-1)),
        {"op": "triage", "item": "$deck", "verdict": "claude"},
        add("badge", "task", "Order badges for the visit", "Cleo Example", "ops", due=day(0)),
        {"op": "triage", "item": "$badge", "verdict": "claude"},
        add("caterer", "decision", "Choose the caterer's second menu", "Ben Sample", "ops", due=day(1)),
        {"op": "triage", "item": "$caterer", "verdict": "discuss"},
        add("slides", "task", "Draft the pilot's review slides", "Ada Example", "pilot", due=day(2)),
        add("parking", "waiting", "Parking passes from the venue", "Cleo Example", "ops", due=day(2)),
        {"op": "triage", "item": "$parking", "verdict": "chase"},
        add("visit", "invite", "The supplier's site visit, Thursday", "Ben Sample", "pilot", due=day(3)),
        add("minutes", "task", "Write up the steering minutes", "Ada Example", "pilot", due=day(4)),
        {"op": "triage", "item": "$minutes", "verdict": "claude"},
        add("budget", "decision", "Sign off the spring budget", "Cleo Example", "ops", due=day(12)),
        {"op": "triage", "item": "$budget", "verdict": "me"},
        add("survey", "waiting", "Survey results from the pilot group", "Ben Sample", "pilot", due=day(20)),
        add("contract", "reply", "Reply on the contract's notice period", "Ada Example", "ops"),
        {"op": "triage", "item": "$contract", "verdict": "discuss"},
        add("photos", "task", "Collect the event photos", "Cleo Example", "ops"),
        add("printer", "task", "Fix the printer settings", "Ben Sample", "ops"),
        {"op": "snooze", "item": "$printer", "until": day(9)},
        add("invoice", "task", "Pay the venue's deposit invoice", "Ada Example", "ops"),
        {"op": "done", "item": "$invoice", "note": "paid by card"},
    ]
    return ops


def woke(vault: Vault) -> None:
    """An item snoozed to yesterday, so it is open again with its snooze ended: the writer snoozes only to a day after
    today, so its snooze op is written as the writer would have written it yesterday."""
    written = store.write(
        vault, [add("permit", "task", "Renew the parking permit", "Cleo Example", "ops")], by="claude", via="cli"
    )
    added = written.ops[-1]
    today = datetime.now(UTC).astimezone(ZONE).date()
    (minted,) = ops.mint(added.id, datetime.now(UTC), 1)
    snooze = ops.Op(
        schema_version=1,
        id=minted,
        item=added.item,
        op="snooze",
        day=today,
        machine=MACHINE,
        by="claude",
        via="cli",
        until=today - timedelta(days=1),
    )
    vault.commit(write_ops(vault.root, [snooze]), "A snooze that ended")


BEN = """id: ben-sample
type: person
name: {en: Ben Sample}
aliases:
  - {text: Ben}
ids: {email: ben@example.invalid}
"""


def meeting(name: str, subject: str, start: datetime, minutes: int, **fields: Any) -> dict[str, Any]:
    span = {"subject": subject, "start": start.isoformat(), "end": (start + timedelta(minutes=minutes)).isoformat()}
    return {"op": "meeting", "as": name, "fields": span | fields}


def point(on: str, text: str, **fields: Any) -> dict[str, Any]:
    return {"op": "point", "item": f"${on}", "fields": {"text": text, **fields}}


def meetings(vault: Vault) -> None:
    """The weeks around today on Meetings, beside day_kit's Pilot sync tomorrow: a stand-up done this morning with
    nothing on it, a budget review under way now, yesterday's supplier call with its recording, its outcome and its
    open point rolled on to next week's, a cancelled and a declined one ahead, and a retro twelve days back whose
    point is still open. Ben has an entity and a brief, so his name links to it."""
    now = datetime.now(UTC).astimezone(ZONE).replace(minute=0, second=0, microsecond=0)
    today = now.replace(hour=0)

    def on(days: int, hour: int) -> datetime:
        return today + timedelta(days=days, hours=hour)

    people = {"attendees": ["Ben Sample", "Ada Example", "Cleo Example"], "organiser": "Ben Sample"}
    changes: list[dict[str, Any]] = [
        meeting("standup", "Daily stand-up", now - timedelta(hours=3), 15, event="cal:standup"),
        meeting(
            "budget",
            "Budget review with Ben",
            now - timedelta(hours=1),
            120,
            event="cal:budget",
            stream="ops",
            where="Room 2, second floor",
            response="tentative",
            **people,
        ),
        point("budget", "Ben: the spring numbers"),
        point("budget", "Who signs off the deposit"),
        meeting(
            "supplier",
            "Supplier call",
            on(-1, 15),
            60,
            event="cal:supplier-1",
            stream="pilot",
            outcome="Cleo sends the offer in writing by Friday.",
            attendees=["Cleo Example", "Ada Example"],
        ),
        point("supplier", "The offer in writing"),
        {"op": "material", "item": "$supplier", "ref": "calls:rec-0100", "label": "recording"},
        {"op": "material", "item": "$supplier", "ref": "notes/plan.md", "label": "The plan"},
        meeting("next", "Supplier call", on(6, 15), 60, event="cal:supplier-2", stream="pilot"),
        meeting("demo", "Vendor demo", on(2, 11), 45, event="cal:demo", response="cancelled"),
        meeting("walk", "Venue walk", on(3, 14), 60, event="cal:walk", response="declined", where="The long hall"),
        meeting("retro", "Design retro", on(-12, 10), 60, event="cal:retro"),
        point("retro", "What the pilot taught us"),
    ]
    store.write(vault, changes, by="claude", via="cli")
    proposed = [
        {"op": "point", "item": "M-0003", "fields": {"text": "Ask about the printer budget", "proposed": True}},
        {"op": "point_set", "item": "M-0003", "point": "2", "fields": {"answer": "Cleo, once the quote is in"}},
        {"op": "rollover"},
    ]
    store.write(vault, proposed, by="sync", via="tool")
    vault.write_run("calls", [r for r in fixture_records("calls") if r.rid == "calls:rec-0100"])
    said = make_record(7, text="Ben Sample sends the spring numbers on Friday.")
    vault.write_run("chat", [said])
    entity = vault.root / "entities" / "person" / "ben-sample.yaml"
    entity.parent.mkdir(parents=True, exist_ok=True)
    entity.write_text(BEN, encoding="utf-8", newline="\n")
    vault.commit(["entities/person/ben-sample.yaml"], "Ben's entity")
    lines = [[f"- Ben sends the spring numbers on Friday. [r:{said.rid}]"], [], []]
    recall.doc_put(vault, brief("ben-sample", "Ben Sample", [said.rid], lines))


def build(folder: Path, world: str) -> Vault:
    if world not in WORLDS:
        raise ValueError(f"no world {world!r}; the worlds are {', '.join(WORLDS)}")
    vault = Vault.init(folder, config(), MACHINE)
    if world in ("week", "meetings"):
        Gate(vault).stage([transcript(), mail(), memo()])
        store.write(vault, seed(), by="claude", via="cli")
        store.write(vault, busier(), by="claude", via="cli")
        woke(vault)
        notes = vault.home.top / "notes"
        notes.mkdir(exist_ok=True)
        (notes / "plan.md").write_text(PLAN_TEXT, encoding="utf-8", newline="\n")
        (notes / "costs.xlsx").write_bytes(b"PK\x03\x04 invented")
        meetings(vault)
    if world == "meetings":
        # The op files an hour old, as files no write just wrote are, so the index takes them as settled and a read
        # finds nothing to catch up.
        hour_ago = time.time_ns() - 3600 * 10**9
        for path in (vault.root / ops.OPS_DIR).iterdir():
            os.utime(path, ns=(hour_ago, hour_ago))
        index_build.rebuild(vault)
    return vault


def main(argv: Sequence[str]) -> int:
    if len(argv) != 2:
        print("usage: ui_world.py <folder> <world>", file=sys.stderr)
        return 2
    build(Path(argv[0]), argv[1])
    # The service reads its token from the store install recorded; with the failing keyring that is a file.
    Tokens().ensure()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
