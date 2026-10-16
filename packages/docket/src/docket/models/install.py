"""`docket models install` and `docket models remove` (DR-0332, DR-0333, DR-0455, DR-0456).

The install builds the worker's environment with uv from the hashed requirements in the wheel, fetches the model files
at their pinned revision with their sha256, resuming a broken download, starts the worker once to check it and time a
batch for the budget, and only then records the environment and the user's choice. On CUDA it checks that ONNX Runtime
really runs on the GPU: a driver too old for CUDA 13 falls back to the CPU without a word.

The device's reranker comes too, unless `--no-reranker`: a worker with it in its spec, the same session settings a
search gets, reranks invented pairs of real length, and the median of three after a warm-up goes into the marker with
the reranker's name and revision. Within RERANK_ON_SECONDS it is on; past it, or when it didn't load or ran on the
wrong provider, it is off on this machine and the install says why.
"""

from __future__ import annotations

import hashlib
import http.client
import importlib.util
import platform
import shutil
import sqlite3
import statistics
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
from docket.index.vectors import load_extension
from docket.models import config
from docket.models.catalogue import (
    DEFAULT_EMBEDDER,
    EMBEDDERS,
    RERANKER_FOR,
    RERANKERS,
    Embedder,
    ModelFile,
    Reranker,
)
from docket.models.client import Skip, Worker, WorkerError
from docket.models.config import DEVICES, RERANK_ON_SECONDS, Choice, Device, Installed, RerankTiming
from docket.models.provider import CHAR_CAP, cost
from docket.models.runtime import rerank_spec, worker_for
from docket.models.worker import CUDA
from docket.service import client
from docket.vault.vault import remove_tree

HUB = "https://huggingface.co"
CHUNK = 1 << 20
FETCH_SECONDS = 60.0
# The budget is what the machine embeds in this long (DR-0330).
BUDGET_SECONDS = 0.3
MIN_BUDGET = CHAR_CAP
MAX_BUDGET = 1_000_000
CALIBRATION = ["passage: " + ("The printer sent a new quote for the second print run. " * 30)[:1200]] * 16
# A reranker is timed on pairs at its token limit: an invented question, and a passage of about 340 words.
RERANK_QUERY = "who offered to cover the shipping cost for the second print run"
RERANK_TEXT = "The printer sent a new quote for the second print run and asked about the paper weight. " * 21
RERANK_RUNS = 3
PACKAGES = {"cpu": "about 50 MB", "cuda": "about 1.3 GB"}
# ONNX Runtime 1.30's Mac wheels are for Apple silicon on macOS 14 or later.
MACOS_MIN = 14
CUDA_SYSTEMS = ("win32", "linux")

Run = Callable[..., subprocess.CompletedProcess[str]]
Which = Callable[[str], str | None]
# A worker for a choice on a device, with the reranker a spec names (runtime.worker_for, or a test's double).
Starter = Callable[..., Worker]


class Opener(Protocol):
    def __call__(self, request: urllib.request.Request, *, timeout: float) -> AbstractContextManager[IO[bytes]]: ...


def _open(request: urllib.request.Request, *, timeout: float) -> AbstractContextManager[IO[bytes]]:
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - the hub's https URL, built from the catalogue


def runtime_problem() -> str | None:
    """Why ONNX Runtime has no build for this system: on a Mac it ships for Apple silicon on macOS 14 or later only."""
    if sys.platform != "darwin":
        return None
    release = platform.mac_ver()[0]
    major = int(release.split(".")[0]) if release.split(".")[0].isdigit() else 0
    if platform.machine() != "arm64" or major < MACOS_MIN:
        return (
            f"the models' ONNX Runtime ships for Macs with Apple silicon on macOS {MACOS_MIN} or later only, and this "
            f"is macOS {release or 'unknown'} on {platform.machine()}; keyword search works without it"
        )
    return None


def vectors_problem() -> str | None:
    """Why vectors can't be searched with this Docket: sqlite-vec ships no build for the system, or this Python's
    SQLite can't load it."""
    if importlib.util.find_spec("sqlite_vec") is None:
        return (
            "vectors need sqlite-vec, which has no build for this system (it ships them for Windows on x86-64, macOS, "
            "and Linux on x86-64 and arm64); keyword search works without it"
        )
    probe = sqlite3.connect(":memory:")
    try:
        why = load_extension(probe)
    finally:
        probe.close()
    if why is not None:
        return (
            "the Python Docket runs on can't load SQLite extensions, so search can't use vectors; uv's own Pythons "
            "can: uv tool install --python-preference only-managed docket"
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
    try:
        _uv([uv, "venv", "--clear", "--python", sys.executable, str(env)], "making the models' environment", run)
        with resources.as_file(resources.files("docket.models").joinpath(f"requirements-{device}.txt")) as wanted:
            _uv(
                [uv, "pip", "install", "--python", str(config.env_python(env)), "--require-hashes", "-r", str(wanted)],
                "installing the models' packages",
                run,
            )
    except DocketError as e:
        if client.recorded() is None:
            raise
        # On Windows a running service's worker holds the environment's files, as after a remove it stopped.
        raise DocketError(
            f"{e}\nthe service's worker may hold the environment's files: docket service stop, then docket models "
            "install"
        ) from None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(embedder: Embedder | Reranker, file: ModelFile, opener: Opener = _open) -> bool:
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
    try:
        with opener(urllib.request.Request(url, headers=headers), timeout=FETCH_SECONDS) as answer:  # noqa: S310
            # A server that ignores the range sends the whole file again.
            resumed = have > 0 and getattr(answer, "status", 200) == 206
            with part.open("ab" if resumed else "wb") as out:
                shutil.copyfileobj(answer, out, CHUNK)
    except (OSError, http.client.HTTPException) as e:
        raise DocketError(
            f"downloading {file.path} stopped ({e}); run docket models install again, it resumes"
        ) from None
    got = part.stat().st_size
    if got < file.size:
        # A connection closed early ends the body without an error: what came is kept for the next install.
        raise DocketError(
            f"downloading {file.path} stopped at {got:,} of {file.size:,} bytes; run docket models install again, "
            "it resumes"
        )
    if got != file.size or _sha256(part) != file.sha256:
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


def cpu_name(system: str = sys.platform, run: Run = subprocess.run, cpuinfo: Path = Path("/proc/cpuinfo")) -> str:
    """The machine's CPU as its system names it, for the timing's line: the same pairs take far apart on two CPUs."""
    try:
        if system.startswith("linux"):
            for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("model name") and ":" in line:
                    return line.split(":", 1)[1].strip()
        elif system == "darwin":
            found = run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, check=False)
            if found.stdout.strip():
                return found.stdout.strip()
    except OSError:
        pass
    return platform.processor() or platform.machine() or "an unknown CPU"


def time_reranker(worker: Worker, reranker: Reranker, clock: Callable[[], float] = time.monotonic) -> RerankTiming:
    """Start a worker whose spec names the reranker and time its pairs, the median of RERANK_RUNS after a warm-up; or
    say why it is off here: it didn't load, ran on another provider than its device's, or the worker failed."""

    def off(why: str) -> RerankTiming:
        return RerankTiming(reranker.name, reranker.revision, reranker.pairs, None, why)

    texts = [RERANK_TEXT] * reranker.pairs
    times: list[float] = []
    try:
        hello = worker.start()
        if hello.reranker is None:
            return off("the worker didn't load it")
        if hello.reranker.off is not None:
            return off(hello.reranker.off)
        for n in range(RERANK_RUNS + 1):
            began = clock()
            got = worker.rerank(RERANK_QUERY, texts, None)
            if isinstance(got, Skip):
                return off(got.why)
            if n:
                times.append(clock() - began)
    except WorkerError as e:
        return off(str(e))
    finally:
        worker.stop()
    return RerankTiming(reranker.name, reranker.revision, reranker.pairs, statistics.median(times))


def timing_line(timing: RerankTiming, reranker: Reranker, where: str) -> str:
    """What the install says of the reranker's timing, the CPU or the device named."""
    head = f"reranker: {reranker.name} ({reranker.licence}, {reranker.trained_on})"
    if timing.seconds is None:
        return f"{head} is off on this machine: {timing.why}"
    took = f"{timing.pairs} pairs in {timing.seconds:.2f} s on {where}"
    if timing.on:
        return f"{head}: {took}"
    return (
        f"{head} is off on this machine: {took}, past {RERANK_ON_SECONDS:g} s; docket models install --no-reranker "
        f"frees its {reranker.download / 1e6:,.0f} MB"
    )


def drop_rerankers(say: Callable[[str], None]) -> None:
    """Remove every reranker's files an earlier install fetched, for --no-reranker."""
    for reranker in RERANKERS.values():
        folder = config.files_dir().joinpath(*reranker.repo.split("/"), reranker.revision)
        if folder.exists():
            remove_tree(folder)
            say(f"removed {folder}")


def install(
    wanted: str = "auto",
    *,
    embedder_name: str = DEFAULT_EMBEDDER,
    rerank: bool = True,
    say: Callable[[str], None] = print,
    which: Which = shutil.which,
    run: Run = subprocess.run,
    opener: Opener = _open,
    start: Starter = worker_for,
    clock: Callable[[], float] = time.monotonic,
) -> Installed:
    problem = runtime_problem() or vectors_problem()
    if problem is not None:
        raise DocketError(problem)
    embedder = EMBEDDERS.get(embedder_name)
    if embedder is None:
        raise DocketError(f"no embedder {embedder_name!r}; there is {', '.join(EMBEDDERS)}")
    device = pick_device(wanted, which=which, run=run)
    uv = which("uv")
    if uv is None:
        raise DocketError("the models' environment is built with uv, which isn't on PATH: https://docs.astral.sh/uv/")
    named = RERANKER_FOR.get(device) if rerank else None
    reranker = RERANKERS[named] if named is not None else None
    size = embedder.download + (reranker.download if reranker is not None else 0)
    also = f" and {reranker.name} ({reranker.licence})" if reranker is not None else ""
    say(
        f"models: {embedder.name} ({embedder.licence}){also} on {device}; downloads {size / 1e6:,.0f} MB of model "
        f"files and {PACKAGES[device]} of packages, once"
    )
    built = isinstance(config.installed(device), str)
    if built:
        say(f"environment: {config.env_dir(device)}")
        build_env(device, uv, run)
    for file in embedder.files:
        say(f"{file.path}: {'downloaded' if fetch(embedder, file, opener) else 'already here'}")
    if reranker is not None:
        for file in reranker.files:
            say(f"{reranker.name} {file.path}: {'downloaded' if fetch(reranker, file, opener) else 'already here'}")
    elif not rerank:
        drop_rerankers(say)
    choice = Choice(device, embedder)
    try:
        budget = check_and_time(start(choice, device), embedder, device)
    except DocketError:
        if built:
            raise
        # An environment that fails its check is built again rather than trusted for its marker: a remove that a
        # running worker stopped half way could leave both the marker and the Python behind.
        say(f"environment: {config.env_dir(device)} didn't pass its check, so it is built again")
        build_env(device, uv, run)
        budget = check_and_time(start(choice, device), embedder, device)
    timing = None
    if reranker is not None:
        timing = time_reranker(start(choice, device, rerank_spec(reranker, 0.0)), reranker, clock)
        say(timing_line(timing, reranker, cpu_name() if device == "cpu" else device))
    found = Installed(device, budget, config.requirements_hash(device), config.python_version(), timing)
    config.mark(found)
    config.write(device, embedder, reranker)
    say(f"ready: {embedder.name} on {device}; the service picks it up when it starts again")
    return found


def remove(say: Callable[[str], None] = print) -> None:
    """The environments, the model files and the config block, gone; the index keeps working without them.

    The choice and each environment's marker go first, so whatever happens next models are off and no half-deleted
    environment passes for an installed one: on Windows a running worker holds its environment's files."""
    config.forget()
    for device in DEVICES:
        (config.env_dir(device) / config.MARKER).unlink(missing_ok=True)
    held: list[str] = []
    for folder in [*(config.env_dir(device) for device in DEVICES), config.files_dir()]:
        if not folder.exists():
            continue
        try:
            remove_tree(folder)
        except OSError:
            held.append(str(folder))
            continue
        say(f"removed {folder}")
    if held:
        raise DocketError(
            f"the service's worker holds these files ({', '.join(held)}): docket service stop, then docket models "
            "remove"
        )
    say("models are off on this machine; keyword search goes on")
