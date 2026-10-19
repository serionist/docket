"""The invented worlds `uv run devkit ui` serves for the screenshot loop (DR-0712), built with the test kits' own
records, people and ledger: run as a script, `python ui_world.py <folder> <world>`, in the environment devkit ui gives
it (its own config and state folders, the failing keyring), which is where the file token it makes lands.

| World | What |
|---|---|
| `empty` | a new vault: no items, nothing staged, a live source never synced |
| `week` | a working week: day_kit's ledger, staging_kit's three records waiting, a live source due a sync |

Invented people only: Ada Example and Ben Sample.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from day_kit import seed
from docket_testkit import make_config
from staging_kit import mail, memo, transcript

from docket.ledger import store
from docket.service.token import Tokens
from docket.vault.config import SourceConfig, VaultConfig
from docket.vault.gate import Gate
from docket.vault.vault import Vault

WORLDS = ("empty", "week")
MACHINE = "ui-box"


def config() -> VaultConfig:
    made = make_config()
    made.primary_machine = MACHINE
    # A live source: one the capture hook posts for, so the Sync page and the sidebar mark it due until it syncs.
    made.sources["chat"] = SourceConfig(tools=["read_channel"])
    made.sources["memo"] = SourceConfig()
    return made


def build(folder: Path, world: str) -> Vault:
    if world not in WORLDS:
        raise ValueError(f"no world {world!r}; the worlds are {', '.join(WORLDS)}")
    vault = Vault.init(folder, config(), MACHINE)
    if world == "week":
        Gate(vault).stage([transcript(), mail(), memo()])
        store.write(vault, seed(), by="claude", via="cli")
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
