"""The models on this machine: the user's choice in their config's `models:` block (DR-0536), where the worker's
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
from docket.models.catalogue import (
    API_EMBEDDERS,
    API_RERANKERS,
    DEFAULT_API_EMBEDDER,
    DEFAULT_API_RERANKER,
    DEFAULT_EMBEDDER,
    EMBEDDERS,
    RERANK_BATCH,
    RERANKERS,
    ApiEmbedder,
    ApiReranker,
    Embedder,
    ModelFile,
    Reranker,
)
from docket.models.provider import Off

Device = Literal["cpu", "cuda"]
DEVICES: tuple[Device, ...] = ("cpu", "cuda")
PROVIDERS = ("local", "api", "none")
KEYS = ("provider", "device", "embedder", "reranker", "api_monthly_tokens")
# The API provider's tokens a month on this machine, past which it sends nothing (DR-0539): about $3 at voyage-4's
# list price, and a stop for a loop that embeds again and again.
DEFAULT_MONTHLY_TOKENS = 50_000_000
# The reranker is on only where the install timed its pairs within this (DR-0455), leaving its 0.75 s budget room.
RERANK_ON_SECONDS = 0.6
MARKER = "docket-models.json"
ENV_VAR = "DOCKET_MODELS_DIR"


@dataclass(frozen=True)
class Choice:
    """Models are on: the local provider, its device and its embedder, and its reranker or why there is none (no why
    when the user chose none)."""

    device: Device
    embedder: Embedder
    reranker: Reranker | None = None
    reranker_why: str | None = None


@dataclass(frozen=True)
class ApiChoice:
    """The API provider is this machine's choice (#89): its embedder, its reranker or none, and the monthly tokens it
    may send. Whether it sends is also the vault's to say (DR-0537), and the key's (DR-0539)."""

    embedder: ApiEmbedder
    reranker: ApiReranker | None
    monthly_tokens: int
    reranker_why: str | None = None


def read() -> Choice | ApiChoice | Off:
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
    if provider not in PROVIDERS:
        return Off(f"{where} names provider {provider!r}; it is local, api or none, so models are off")
    if provider == "none":
        return Off()
    if provider == "api":
        return _api(block, where)
    device = block.get("device", "cpu")
    if device not in DEVICES:
        return Off(f"{where} names device {device!r}; it is cpu or cuda, so models are off")
    embedder = EMBEDDERS.get(str(block.get("embedder", DEFAULT_EMBEDDER)))
    if embedder is None:
        return Off(f"{where} names an embedder this Docket doesn't have ({', '.join(EMBEDDERS)}), so models are off")
    named = block.get("reranker")
    if named is None:
        return Choice(device, embedder, None, "no reranker is installed; docket models install adds it")
    if named == "none":
        return Choice(device, embedder)
    reranker = RERANKERS.get(str(named))
    if reranker is None:
        # The embedder doesn't depend on the reranker, so only the reranker is off (DR-0536, DR-0451).
        why = f"{where} names a reranker this Docket doesn't have ({', '.join(RERANKERS)}), so the reranker is off"
        return Choice(device, embedder, None, why)
    return Choice(device, embedder, reranker)


def _api(block: dict[str, Any], where: str) -> ApiChoice | Off:
    embedder = API_EMBEDDERS.get(str(block.get("embedder", DEFAULT_API_EMBEDDER)))
    if embedder is None:
        names = ", ".join(API_EMBEDDERS)
        return Off(f"{where} names an embedder the api provider doesn't have ({names}), so models are off")
    monthly = block.get("api_monthly_tokens", DEFAULT_MONTHLY_TOKENS)
    if isinstance(monthly, bool) or not isinstance(monthly, int) or monthly < 1:
        why = f"{where} gives api_monthly_tokens as {monthly!r}; it is a whole number of tokens"
        return Off(f"{why}, so models are off")
    named = block.get("reranker", DEFAULT_API_RERANKER)
    if named == "none":
        return ApiChoice(embedder, None, monthly)
    reranker = API_RERANKERS.get(str(named))
    if reranker is None:
        # As the local provider has it: the embedder doesn't depend on the reranker (DR-0536, DR-0451).
        names = ", ".join(API_RERANKERS)
        why = f"{where} names a reranker the api provider doesn't have ({names}), so the reranker is off"
        return ApiChoice(embedder, None, monthly, why)
    return ApiChoice(embedder, reranker, monthly)


def write_api(embedder: ApiEmbedder, reranker: ApiReranker | None, monthly: int = DEFAULT_MONTHLY_TOKENS) -> None:
    userconfig.set_section(
        "models",
        {
            "provider": "api",
            "embedder": embedder.name,
            "reranker": reranker.name if reranker is not None else "none",
            "api_monthly_tokens": monthly,
        },
    )


def write(device: Device, embedder: Embedder, reranker: Reranker | None = None) -> None:
    userconfig.set_section(
        "models",
        {
            "provider": "local",
            "device": device,
            "embedder": embedder.name,
            "reranker": reranker.name if reranker is not None else "none",
        },
    )


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


def file_path(model: Embedder | Reranker, file: ModelFile) -> Path:
    return files_dir().joinpath(*model.repo.split("/"), model.revision, *file.path.split("/"))


def requirements(device: Device) -> str:
    """The environment's hashed requirements, as shipped in the wheel."""
    return resources.files("docket.models").joinpath(f"requirements-{device}.txt").read_text(encoding="utf-8")


def requirements_hash(device: Device) -> str:
    return hashlib.sha256(requirements(device).encode("utf-8")).hexdigest()


# -- what the install left --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RerankTiming:
    """The install's timing of a reranker on this machine: its pairs and their seconds, or why it couldn't be timed."""

    name: str
    revision: str
    pairs: int
    seconds: float | None
    why: str | None = None

    @property
    def on(self) -> bool:
        return self.seconds is not None and self.seconds <= RERANK_ON_SECONDS

    @property
    def batch_seconds(self) -> float:
        """A batch's share of the time, which the worker judges its first batch by (DR-0451)."""
        return (self.seconds or 0.0) / max(1, -(-self.pairs // RERANK_BATCH))

    def fits(self, reranker: Reranker) -> bool:
        return self.name == reranker.name and self.revision == reranker.revision


@dataclass(frozen=True)
class Installed:
    device: Device
    budget: int
    requirements: str
    python: str
    rerank: RerankTiming | None = None


def python_version() -> str:
    return ".".join(str(n) for n in sys.version_info[:2])


def mark(installed: Installed) -> None:
    path = env_dir(installed.device) / MARKER
    data: dict[str, object] = {
        "device": installed.device,
        "budget": installed.budget,
        "requirements": installed.requirements,
        "python": installed.python,
    }
    if installed.rerank is not None:
        data["reranker"] = installed.rerank.__dict__
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")


def _timing(raw: object) -> RerankTiming | None:
    """A marker's reranker timing. A marker #67 wrote has none, and one that doesn't read leaves the reranker untimed,
    never the marker unreadable, so the embedder stays on."""
    if not isinstance(raw, dict):
        return None
    data = cast(dict[str, Any], raw)
    try:
        seconds, why = data.get("seconds"), data.get("why")
        return RerankTiming(
            str(data["name"]),
            str(data["revision"]),
            int(data["pairs"]),
            None if seconds is None else float(seconds),
            None if why is None else str(why),
        )
    except (KeyError, TypeError, ValueError):
        return None


def installed(device: Device) -> Installed | str:
    """What the install left for a device, or why it isn't usable: no environment, one built from other requirements or
    on another Python, or model files missing."""
    path = env_dir(device) / MARKER
    if not path.is_file() or not env_python(env_dir(device)).is_file():
        return f"the models aren't installed for {device} on this machine; run docket models install"
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
        found = Installed(
            cast(Device, str(data["device"])),
            int(data["budget"]),
            str(data["requirements"]),
            str(data["python"]),
            _timing(data.get("reranker")),
        )
    except (ValueError, KeyError, TypeError):
        return "the models' environment can't be read; run docket models install"
    if found.requirements != requirements_hash(device) or found.python != python_version():
        return "the models' environment is from another Docket or Python; run docket models install"
    return found


def missing_files(model: Embedder | Reranker) -> list[ModelFile]:
    """A model's files that aren't there at their size: a full check of their hashes is the install's."""
    return [f for f in model.files if not (p := file_path(model, f)).is_file() or p.stat().st_size != f.size]
