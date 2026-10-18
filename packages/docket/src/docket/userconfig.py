"""The user's own Docket settings, outside any vault: which vault commands use by default, and the service's vault,
port and token store. Also the folders Docket keeps per-user state and logs in."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from platformdirs import user_config_path, user_log_path

from docket import paths
from docket.errors import DocketError

TokenStore = Literal["keyring", "file"]
TOKEN_STORES: tuple[TokenStore, ...] = ("keyring", "file")
MAX_PORT = 65535


def config_path() -> Path:
    env = os.environ.get("DOCKET_CONFIG_DIR")
    folder = Path(env) if env else user_config_path("docket", appauthor=False)
    return folder / "config.yaml"


def state_dir() -> Path:
    """Where the service keeps its pid file, its lock, its open runs' marker and `docket open`'s redirect page."""
    return paths.state_dir()


def log_dir() -> Path:
    # A state folder moved by DOCKET_STATE_DIR (tests, or a user who moved it) takes the log with it.
    env = os.environ.get("DOCKET_STATE_DIR")
    return Path(env) / "log" if env else user_log_path("docket", appauthor=False)


def load() -> dict[str, Any]:
    path = config_path()
    if not path.is_file():
        return {}
    data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in cast(dict[object, Any], data).items()}


def _save(data: dict[str, Any]) -> None:
    target = config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(data, sort_keys=True, allow_unicode=True), encoding="utf-8")


def set_section(name: str, value: dict[str, Any] | None) -> None:
    """Write one top-level section, or take it out with None, and keep the rest of the file as it is."""
    data = load()
    if value is None:
        data.pop(name, None)
    else:
        data[name] = value
    _save(data)


def default_vault() -> Path | None:
    value = load().get("default_vault")
    return Path(value) if isinstance(value, str) and value else None


def set_default_vault(path: Path) -> None:
    data = load()
    data["default_vault"] = str(path)
    _save(data)


@dataclass(frozen=True)
class ServiceSettings:
    vault: Path | None = None
    port: int | None = None
    # `service.token_store` in the file.
    store: TokenStore | None = None


def service() -> ServiceSettings:
    """The `service` keys. A value of the wrong kind is refused: the service would otherwise start on a vault, a port
    or a token store nobody chose."""
    raw: Any = load().get("service")
    if raw is None:
        return ServiceSettings()
    if not isinstance(raw, dict):
        raise DocketError(f"{config_path()}: service must be a map of vault, port and token_store")
    data = cast(dict[str, Any], raw)
    vault: Any = data.get("vault")
    port: Any = data.get("port")
    store: Any = data.get("token_store")
    if vault is not None and not (isinstance(vault, str) and vault):
        raise DocketError(f"{config_path()}: service.vault must be a folder's path")
    if port is not None and (not isinstance(port, int) or isinstance(port, bool) or not 0 < port <= MAX_PORT):
        raise DocketError(f"{config_path()}: service.port must be a port number from 1 to {MAX_PORT}")
    if store is not None and store not in TOKEN_STORES:
        raise DocketError(f"{config_path()}: service.token_store is keyring or file, not {store!r}")
    return ServiceSettings(
        vault=Path(vault) if vault else None,
        port=port,
        store=store,
    )


def folders() -> dict[Path, dict[str, Path]]:
    """The `folders` block (DR-0490): each vault's resolved root, by the path the user wrote, with its folder sources'
    folders on this machine, each absolute. A value of the wrong kind is refused, naming its key: a scan of a folder
    nobody named must never start."""
    raw: Any = load().get("folders")
    if raw is None:
        return {}
    where = f"{config_path()}: folders"
    if not isinstance(raw, dict):
        raise DocketError(f"{where} must be a map from a vault's folder to its sources' folders")
    out: dict[Path, dict[str, Path]] = {}
    for vault, sources in cast(dict[object, Any], raw).items():
        if not isinstance(vault, str) or not vault or not isinstance(sources, dict):
            raise DocketError(f"{where}: {vault!s} must be a vault's folder, with a map of its sources' folders")
        mine: dict[str, Path] = {}
        for source, folder in cast(dict[object, Any], sources).items():
            if not isinstance(folder, str) or not Path(folder).is_absolute():
                raise DocketError(f"{where}: {vault}: {source!s} must be an absolute folder, such as E:/Work")
            mine[str(source)] = Path(folder)
        out[Path(vault).expanduser().resolve()] = mine
    return out


def folder_for(vault_root: Path, source: str) -> Path | None:
    """This machine's folder for a vault's folder source, when the user's config names one."""
    return folders().get(vault_root.resolve(), {}).get(source)


def set_service(
    *, vault: Path | None = None, port: int | None = None, store: TokenStore | None = None
) -> ServiceSettings:
    """Write the given `service` keys and keep the rest of the file as it is."""
    data = load()
    raw: Any = data.get("service")
    section: dict[str, Any] = dict(cast(dict[str, Any], raw)) if isinstance(raw, dict) else {}
    if vault is not None:
        section["vault"] = str(vault)
    if port is not None:
        section["port"] = port
    if store is not None:
        section["token_store"] = store
    data["service"] = section
    _save(data)
    return service()


def media_max_bytes(default: int) -> int:
    """`media.max_bytes`: the largest media file `media_put` takes on this machine (DR-0604), `default` when unset. A
    value of another kind is refused, as the service's keys are."""
    raw: Any = load().get("media")
    value: Any = cast(dict[str, Any], raw).get("max_bytes") if isinstance(raw, dict) else None
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DocketError(f"{config_path()}: media.max_bytes must be a whole number of bytes")
    return value


def curate_questions() -> Path | None:
    """`curate.questions`: the question file a curate round measures before and after (DR-0475), or None when unset.
    A value of another kind is refused, as the service's keys are."""
    raw: Any = load().get("curate")
    if raw is None:
        return None
    value: Any = cast(dict[str, Any], raw).get("questions") if isinstance(raw, dict) else None
    if not isinstance(value, str) or not value:
        raise DocketError(f"{config_path()}: curate.questions must be a question file's path")
    return Path(value).expanduser()
