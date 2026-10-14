"""The user's own Docket settings, outside any vault: for now, which vault commands use by default."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import yaml
from platformdirs import user_config_path


def config_path() -> Path:
    env = os.environ.get("DOCKET_CONFIG_DIR")
    folder = Path(env) if env else user_config_path("docket", appauthor=False)
    return folder / "config.yaml"


def load() -> dict[str, Any]:
    path = config_path()
    if not path.is_file():
        return {}
    data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in cast(dict[object, Any], data).items()}


def default_vault() -> Path | None:
    value = load().get("default_vault")
    return Path(value) if isinstance(value, str) and value else None


def set_default_vault(path: Path) -> None:
    data = load()
    data["default_vault"] = str(path)
    target = config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(data, sort_keys=True, allow_unicode=True), encoding="utf-8")
