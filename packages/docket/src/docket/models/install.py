"""`docket models install` and `docket models remove` (DR-0332, DR-0333).

The install builds the worker's environment with uv from the hashed requirements in the wheel, fetches the model files
at their pinned revision with their sha256, resuming a broken download, starts the worker once to check it and time a
batch for the budget, and only then records the environment and the user's choice. On CUDA it checks that ONNX Runtime
really runs on the GPU: a driver too old for CUDA 13 falls back to the CPU without a word.
"""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from importlib import resources
from pathlib import Path
from typing import IO, Protocol

from docket.errors import DocketError
from docket.models import config
from docket.models.catalogue import DEFAULT_EMBEDDER, EMBEDDERS, Embedder, ModelFile
from docket.models.client import Worker, WorkerError
from docket.models.config import DEVICES, Choice, Device, Installed
from docket.models.provider import CHAR_CAP, cost
from docket.models.runtime import worker_for
from docket.models.worker import CUDA
from docket.vault.vault import remove_tree

HUB = "https://huggingface.co"
CHUNK = 1 << 20
FETCH_SECONDS = 60.0
# The budget is what the machine embeds in this long (DR-0330).
BUDGET_SECONDS = 0.3
MIN_BUDGET = CHAR_CAP
MAX_BUDGET = 1_000_000
CALIBRATION = ["passage: " + ("The printer sent a new quote for the second print run. " * 30)[:1200]] * 16
PACKAGES = {"cpu": "about 50 MB", "cuda": "about 1.3 GB"}
CUDA_SYSTEMS = ("win32", "linux")

Run = Callable[..., subprocess.CompletedProcess[str]]
Which = Callable[[str], str | None]
Starter = Callable[[Choice, Device], Worker]


class Opener(Protocol):
    def __call__(self, request: urllib.request.Request, *, timeout: float) -> AbstractContextManager[IO[bytes]]: ...


def _open(request: urllib.request.Request, *, timeout: float) -> AbstractContextManager[IO[bytes]]:
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - the hub's https URL, built from the catalogue


def vectors_problem() -> str | None:
    """Why vectors can't be searched on this system: sqlite-vec ships no build for it."""
    if importlib.util.find_spec("sqlite_vec") is None:
        return (
            "vectors need sqlite-vec, which has no build for this system (it ships them for Windows on x86-64, macOS, "
            "and Linux on x86-64 and arm64); keyword search works without it"
        )
    return None


def pick_device(wanted: str, *, which: Which = shutil.which, run: Run = subprocess.run) -> Device:
    """The device asked for, or for auto CUDA when nvidia-smi lists a GPU, else the CPU."""
    if wanted == "auto":
        smi = which("nvidia-smi")
        if smi is not None and sys.platform in CUDA_SYSTEMS:
            found = run([smi, "-L"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
            if found.returncode == 0 and found.stdout.strip():
                return "cuda"
        return "cpu"
    for device in DEVICES:
        if wanted == device:
            if device == "cuda" and sys.platform not in CUDA_SYSTEMS:
                raise DocketError("CUDA runs on Windows and Linux; on this system take --device cpu")
            return device
    raise DocketError(f"--device is auto, cpu or cuda, not {wanted!r}")


def _uv(args: Sequence[str], what: str, run: Run) -> None:
    done = run(list(args), capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if done.returncode != 0:
        tail = "\n".join((done.stderr or done.stdout).strip().splitlines()[-5:])
        raise DocketError(f"{what} failed (uv exit {done.returncode}):\n{tail}")


def build_env(device: Device, uv: str, run: Run) -> None:
    """A fresh virtualenv on Docket's own Python, with the device's hashed requirements and nothing else."""
    env = config.env_dir(device)
    env.parent.mkdir(parents=True, exist_ok=True)
    _uv([uv, "venv", "--clear", "--python", sys.executable, str(env)], "making the models' environment", run)
    with resources.as_file(resources.files("docket.models").joinpath(f"requirements-{device}.txt")) as wanted:
        _uv(
            [uv, "pip", "install", "--python", str(config.env_python(env)), "--require-hashes", "-r", str(wanted)],
            "installing the models' packages",
            run,
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(embedder: Embedder, file: ModelFile, opener: Opener = _open) -> bool:
    """Download one file at the pinned revision, resuming a `.part` left before, and keep it only when its sha256 is
    the catalogue's. Returns whether it downloaded anything."""
    target = config.file_path(embedder, file)
    if target.is_file() and target.stat().st_size == file.size and _sha256(target) == file.sha256:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    have = part.stat().st_size if part.is_file() else 0
    if have >= file.size:
        part.unlink()
        have = 0
    url = f"{HUB}/{embedder.repo}/resolve/{embedder.revision}/{file.path}"
    headers = {"Range": f"bytes={have}-"} if have else {}
    with opener(urllib.request.Request(url, headers=headers), timeout=FETCH_SECONDS) as answer:  # noqa: S310
        # A server that ignores the range sends the whole file again.
        resumed = have > 0 and getattr(answer, "status", 200) == 206
        with part.open("ab" if resumed else "wb") as out:
            shutil.copyfileobj(answer, out, CHUNK)
    if part.stat().st_size != file.size or _sha256(part) != file.sha256:
        part.unlink()
        raise DocketError(
            f"{file.path} didn't match its pinned sha256 and was removed; run docket models install again"
        )
    part.replace(target)
    return True


def check_and_time(
    worker: Worker, embedder: Embedder, device: Device, clock: Callable[[], float] = time.monotonic
) -> int:
    """Start the worker, check what it says, and time a batch: the budget is what it embeds in BUDGET_SECONDS."""
    try:
        hello = worker.start()
        if hello.dims != embedder.dims:
            raise DocketError(f"the worker's model gives {hello.dims} dims, not {embedder.dims}; run the install again")
        if device == "cuda" and hello.provider != CUDA:
            raise DocketError(
                "ONNX Runtime fell back to the CPU: CUDA 13 needs an NVIDIA driver of R580 or later. "
                "Update the driver, "
                "or run docket models install --device cpu"
            )
        started = clock()
        worker.batch(CALIBRATION)
        seconds = max(clock() - started, 1e-3)
    except WorkerError as e:
        tail = "\n".join(worker.stderr().strip().splitlines()[-5:])
        raise DocketError(f"{e}{f'\n{tail}' if tail else ''}") from None
    finally:
        worker.stop()
    per_second = sum(cost(t) for t in CALIBRATION) / seconds
    return max(MIN_BUDGET, min(MAX_BUDGET, int(per_second * BUDGET_SECONDS)))


def install(
    wanted: str = "auto",
    *,
    embedder_name: str = DEFAULT_EMBEDDER,
    say: Callable[[str], None] = print,
    which: Which = shutil.which,
    run: Run = subprocess.run,
    opener: Opener = _open,
    start: Starter = worker_for,
) -> Installed:
    problem = vectors_problem()
    if problem is not None:
        raise DocketError(problem)
    embedder = EMBEDDERS.get(embedder_name)
    if embedder is None:
        raise DocketError(f"no embedder {embedder_name!r}; there is {', '.join(EMBEDDERS)}")
    device = pick_device(wanted, which=which, run=run)
    uv = which("uv")
    if uv is None:
        raise DocketError("the models' environment is built with uv, which isn't on PATH: https://docs.astral.sh/uv/")
    say(
        f"models: {embedder.name} ({embedder.licence}) on {device}; downloads {embedder.download / 1e6:,.0f} MB of "
        f"model files and {PACKAGES[device]} of packages, once"
    )
    if isinstance(config.installed(device), str):
        say(f"environment: {config.env_dir(device)}")
        build_env(device, uv, run)
    for file in embedder.files:
        say(f"{file.path}: {'downloaded' if fetch(embedder, file, opener) else 'already here'}")
    choice = Choice(device, embedder)
    budget = check_and_time(start(choice, device), embedder, device)
    found = Installed(device, budget, config.requirements_hash(device), config.python_version())
    config.mark(found)
    config.write(device, embedder)
    say(f"ready: {embedder.name} on {device}; the service picks it up when it starts again")
    return found


def remove(say: Callable[[str], None] = print) -> None:
    """The environments, the model files and the config block, gone; the index keeps working without them."""
    for device in DEVICES:
        env = config.env_dir(device)
        if env.exists():
            remove_tree(env)
            say(f"removed {env}")
    files = config.files_dir()
    if files.exists():
        remove_tree(files)
        say(f"removed {files}")
    config.forget()
    say("models are off on this machine; keyword search goes on")
