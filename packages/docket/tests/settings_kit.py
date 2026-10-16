"""What the Settings page's tests share: a consuming project with a mixed `calls` source whose mapper is in place, a
work `memo` source with none, a folder `documents` source, invented never-store values and a pattern of the user's,
committed as Docket writes vault.yaml. Invented values only: Cleo Test, Dora #2, chat:garden, Project: Phoenix."""

from __future__ import annotations

from pathlib import Path

import pytest
from docket_testkit import consuming_project, copy_project, digest, shared

from docket.vault.config import NeverStore, Redaction, RedactionPattern, SourceConfig, VaultConfig
from docket.vault.vault import Vault

MAPPER = "def map_run(run):\n    return {'contract': 1, 'records': []}\n"
PEOPLE = ["Cleo Test", "Dora #2"]
CONTAINERS = ["chat:garden"]
KEYWORDS = ["Project: Phoenix"]
PATTERN = RedactionPattern(name="ticket-id", pattern=r"TCK-\d{6}")


def _build(folder: Path) -> Path:
    vault = consuming_project(folder, {"calls": MAPPER})
    path = vault.root / "vault.yaml"
    config = VaultConfig.load(path)
    config.sources["calls"] = config.sources["calls"].model_copy(update={"tools": ["list_calls", "get_call"]})
    config.sources["memo"] = SourceConfig(tools=["get_memo"])
    config.sources["documents"] = SourceConfig(folder="docs", exclude=["archive/*"])
    config.never_store = NeverStore(people=PEOPLE, containers=CONTAINERS, keywords=KEYWORDS)
    config.redaction = Redaction(patterns=[PATTERN])
    path.write_text(config.dump(), encoding="utf-8", newline="\n")
    vault.commit(["vault.yaml"], "Config: the Settings page's test vault")
    return vault.root.parent


def settings_template(factory: pytest.TempPathFactory) -> Path:
    key = "settings-project-" + digest(MAPPER, repr(PEOPLE), repr(CONTAINERS), repr(KEYWORDS), PATTERN.pattern)
    return shared(_build, key, factory)


def settings_vault(template: Path, tmp_path: Path) -> Vault:
    return copy_project(template, tmp_path)


def edit_config(vault: Vault, **changes: object) -> VaultConfig:
    """vault.yaml rewritten as Docket writes it, with these top-level keys changed; not committed."""
    path = vault.root / "vault.yaml"
    config = VaultConfig.load(path).model_copy(update=changes)
    path.write_text(config.dump(), encoding="utf-8", newline="\n")
    return config
