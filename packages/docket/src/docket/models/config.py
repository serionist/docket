"""The models on this machine: the user's choice in their config's `models:` block (DR-0335), where the worker's
environment and the model files live, and what `docket models install` left there.

A block that doesn't read turns models off and says why; it never stops the service or keyword search. The files live
outside any vault and any repo: the environment under the user's data folder, the model files under their cache
folder, or both under DOCKET_MODELS_DIR, as tests and CI's cache set it.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from platformdirs import user_cache_path, user_data_path

from docket import userconfig
from docket.models.catalogue import DEFAULT_EMBEDDER, EMBEDDERS, Embedder, ModelFile
from docket.models.provider import Off

Device = Literal["cpu", "cuda"]
DEVICES: tuple[Device, ...] = ("cpu", "cuda")
PROVIDERS = ("local", "none")
KEYS = ("provider", "device", "embedder")
MARKER = "docket-models.json"
ENV_VAR = "DOCKET_MODELS_DIR"


@dataclass(frozen=True)
class Choice:
    """Models are on: the local provider, its device and its embedder."""

    device: Device
    embedder: Embedder


def read() -> Choice | Off:
    """The user's choice, or Off: with no reason when there is no block or it says provider none, with one when the
    block doesn't read."""
    try:
        raw: Any = userconfig.load().get("models")
    except (yaml.YAMLError, UnicodeDecodeError, OSError):
        # Every command reads the models, so a config that doesn't read turns them off rather than stopping them.
        return Off(f"{userconfig.config_path()} doesn't read as YAML, so models are off")
    where = f"the models block in {userconfig.config_path()}"
    if raw is None:
        return Off()
    if not isinstance(raw, dict):
        return Off(f"{where} isn't a map of provider, device and embedder, so models are off")
    block = {str(k): v for k, v in cast(dict[object, Any], raw).items()}
    unknown = sorted(set(block) - set(KEYS))
    if unknown:
        return Off(f"{where} has keys this Docket doesn't know ({', '.join(unknown)}), so models are off")
    provider = block.get("provider", "local")
    if provider == "api":
        return Off(f"{where} names the api provider, which isn't there yet, so models are off")
    if provider not in PROVIDERS:
        return Off(f"{where} names provider {provider!r}; it is local or none, so models are off")
    if provider == "none":
        return Off()
    device = block.get("device", "cpu")
    if device not in DEVICES:
        return Off(f"{where} names device {device!r}; it is cpu or cuda, so models are off")
    embedder = EMBEDDERS.get(str(block.get("embedder", DEFAULT_EMBEDDER)))
    if embedder is None:
        return Off(f"{where} names an embedder this Docket doesn't have ({', '.join(EMBEDDERS)}), so models are off")
    return Choice(device, embedder)


def write(device: Device, embedder: Embedder) -> None:
    userconfig.set_section("models", {"provider": "local", "device": device, "embedder": embedder.name})


def forget() -> None:
    userconfig.set_section("models", None)


# -- where things live ------------------------------------------------------------------------------------------


def _root() -> Path | None:
    env = os.environ.get(ENV_VAR)
    return Path(env) if env else None


def env_dir(device: Device) -> Path:
    root = _root()
    base = root if root is not None else user_data_path("docket", appauthor=False) / "models"
    return base / f"env-{device}"


def files_dir() -> Path:
    root = _root()
    return root / "files" if root is not None else user_cache_path("docket", appauthor=False) / "models"


def env_python(env: Path) -> Path:
    return env / "Scripts" / "python.exe" if sys.platform == "win32" else env / "bin" / "python"


def file_path(embedder: Embedder, file: ModelFile) -> Path:
    return files_dir().joinpath(*embedder.repo.split("/"), embedder.revision, *file.path.split("/"))


def requirements(device: Device) -> str:
    """The environment's hashed requirements, as shipped in the wheel."""
    return resources.files("docket.models").joinpath(f"requirements-{device}.txt").read_text(encoding="utf-8")


def requirements_hash(device: Device) -> str:
    return hashlib.sha256(requirements(device).encode("utf-8")).hexdigest()


# -- what the install left --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Installed:
    device: Device
    budget: int
    requirements: str
    python: str


def python_version() -> str:
    return ".".join(str(n) for n in sys.version_info[:2])


def mark(installed: Installed) -> None:
    path = env_dir(installed.device) / MARKER
    path.write_text(json.dumps(installed.__dict__, indent=2) + "\n", encoding="utf-8", newline="\n")


def installed(device: Device) -> Installed | str:
    """What the install left for a device, or why it isn't usable: no environment, one built from other requirements or
    on another Python, or model files missing."""
    path = env_dir(device) / MARKER
    if not path.is_file() or not env_python(env_dir(device)).is_file():
        return f"the models aren't installed for {device} on this machine; run docket models install"
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
        found = Installed(
            cast(Device, str(data["device"])), int(data["budget"]), str(data["requirements"]), str(data["python"])
        )
    except (ValueError, KeyError, TypeError):
        return "the models' environment can't be read; run docket models install"
    if found.requirements != requirements_hash(device) or found.python != python_version():
        return "the models' environment is from another Docket or Python; run docket models install"
    return found


def missing_files(embedder: Embedder) -> list[ModelFile]:
    """The embedder's files that aren't there at their size: a full check of their hashes is the install's."""
    return [f for f in embedder.files if not (p := file_path(embedder, f)).is_file() or p.stat().st_size != f.size]
