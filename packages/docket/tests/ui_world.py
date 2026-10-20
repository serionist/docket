"""The invented worlds `uv run devkit ui` serves for the screenshot loop (DR-0712), built with the test kits' own
records, people and ledger: run as a script, `python ui_world.py <folder> <world>`, in the environment devkit ui gives
it (its own config and state folders, the failing keyring), which is where the file token it makes lands.

| World | What |
|---|---|
| `empty` | a new vault: no items, nothing staged, a live source never synced |
| `week` | a working week: day_kit's ledger and more items beside it, the files its materials name, staging_kit's |
| | three records waiting, a live source due a sync |

Invented people only: Ada Example, Ben Sample and Cleo Example.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from day_kit import add, day, seed
from docket_testkit import make_config
from staging_kit import mail, memo, transcript

from docket.ledger import store
from docket.service.token import Tokens
from docket.vault.config import SourceConfig, VaultConfig
from docket.vault.gate import Gate
from docket.vault.vault import Vault

WORLDS = ("empty", "week")
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


def build(folder: Path, world: str) -> Vault:
    if world not in WORLDS:
        raise ValueError(f"no world {world!r}; the worlds are {', '.join(WORLDS)}")
    vault = Vault.init(folder, config(), MACHINE)
    if world == "week":
        Gate(vault).stage([transcript(), mail(), memo()])
        store.write(vault, seed(), by="claude", via="cli")
        store.write(vault, busier(), by="claude", via="cli")
        notes = vault.home.top / "notes"
        notes.mkdir(exist_ok=True)
        (notes / "plan.md").write_text(PLAN_TEXT, encoding="utf-8", newline="\n")
        (notes / "costs.xlsx").write_bytes(b"PK\x03\x04 invented")
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
