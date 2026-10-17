"""`docket models install --speech`, its status and its remove (DR-0567, DR-0568).

Speech gets an environment of its own, `models/env-speech-<device>`, built with uv from the hashed requirements in the
wheel as the embedder's is (DR-0332): CTranslate2's wheels are built against CUDA 12, so the CUDA one takes NVIDIA's
cuBLAS 12 under NVIDIA's terms, and faster-whisper's own ONNX Runtime for its VAD would clash with the embedder's GPU
build. The model files come from Hugging Face at their pinned revision with their sha256 (DR-0333). The install times
the worker on 30 s of generated audio once, with the peak VRAM nvidia-smi saw on CUDA, and keeps both in the
environment's marker. The choice is the user config's `speech` section: the model and the device.

Off by default: a plain `docket models install` never fetches it. On a CPU it transcribes only when asked.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from array import array
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, cast

import yaml

from docket import userconfig
from docket.errors import DocketError
from docket.models import config
from docket.models.config import Device
from docket.models.install import Opener, Run, Which, fetch, pick_device, uv_step
from docket.models.install import _open as default_opener  # pyright: ignore[reportPrivateUsage]
from docket.speech import client
from docket.speech.catalogue import DEFAULT_SPEECH, SPEECH, SpeechModel
from docket.speech.worker import RATE
from docket.vault.vault import remove_tree

SECTION = "speech"
OFF = "speech isn't installed on this machine; docket models install --speech adds it"
MARKER = "docket-speech.json"
COMPUTE: dict[Device, str] = {"cpu": "int8", "cuda": "float16"}
PACKAGES: dict[Device, str] = {"cpu": "about 0.3 GB", "cuda": "about 1.2 GB"}
TIMING_SECONDS = 30
TIMING_LIMIT = 600.0
# The CPU's speed: large-v3 ran about as fast as the audio on the maintainer's 16 threads, and faster on 8.
THREADS = 8
# What large-v3 in float16 with beam 5 needs on a GPU: faster-whisper's own figure for large-v2, the same size.
VRAM_NEED_GB = 4.5
# With search's models on the GPU too (2 to 3 GB): a card under 6 GB can't hold both, one under 8 GB only by turns.
SHARED_GB = 6
ALONGSIDE_GB = 8


@dataclass(frozen=True)
class Speech:
    """Speech is on: the model, the device, and the environment's Python that runs the worker."""

    model: SpeechModel
    device: Device
    python: Path

    @property
    def compute(self) -> str:
        return COMPUTE[self.device]

    @property
    def folder(self) -> Path:
        return config.file_path(self.model, self.model.files[0]).parent


@dataclass(frozen=True)
class Installed:
    model: str
    device: Device
    requirements: str
    python: str
    seconds: float
    vram_mb: int | None = None


def env_dir(device: Device) -> Path:
    return config.models_dir() / f"env-speech-{device}"


def _requirements(device: Device) -> str:
    return f"speech-{device}"


def installed(device: Device) -> Installed | str:
    """What the install left for a device, or why it isn't usable."""
    path = env_dir(device) / MARKER
    if not path.is_file() or not config.env_python(env_dir(device)).is_file():
        return f"speech isn't installed for {device} on this machine; run docket models install --speech"
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
        vram = data.get("vram_mb")
        found = Installed(
            str(data["model"]),
            cast(Device, str(data["device"])),
            str(data["requirements"]),
            str(data["python"]),
            float(data["seconds"]),
            int(vram) if vram is not None else None,
        )
    except (ValueError, KeyError, TypeError):
        return "the speech environment can't be read; run docket models install --speech"
    if found.requirements != config.requirements_hash(_requirements(device)) or found.python != config.python_version():
        return "the speech environment is from another Docket or Python; run docket models install --speech"
    return found


def mark(found: Installed) -> None:
    path = env_dir(found.device) / MARKER
    path.write_text(json.dumps(found.__dict__, indent=2) + "\n", encoding="utf-8", newline="\n")


def chosen() -> Speech | str:
    """The machine's speech choice, ready to run, or why there is none."""
    try:
        raw: Any = userconfig.load().get(SECTION)
    except (yaml.YAMLError, UnicodeDecodeError, OSError):
        return f"{userconfig.config_path()} doesn't read as YAML, so speech is off"
    if raw is None:
        return OFF
    block = cast(dict[str, Any], raw) if isinstance(raw, dict) else {}
    model = SPEECH.get(str(block.get("model")))
    device = block.get("device")
    if model is None or device not in config.DEVICES:
        return f"the speech section in {userconfig.config_path()} names no model and device this Docket knows"
    found = installed(device)
    if isinstance(found, str):
        return found
    speech = Speech(model, device, config.env_python(env_dir(device)))
    missing = [f.path for f in speech.model.files if not config.file_path(speech.model, f).is_file()]
    if missing:
        return f"the speech model lacks {', '.join(missing)}; run docket models install --speech"
    return speech


def spec(speech: Speech, pcm: Path, *, language: str | None, prompt: str | None, vad: bool = True) -> dict[str, Any]:
    """What a job tells the worker (worker.py)."""
    return {
        "model": str(speech.folder),
        "device": speech.device,
        "compute": speech.compute,
        "threads": THREADS,
        "pcm": str(pcm),
        "language": language,
        "prompt": prompt,
        "vad": vad,
        "piece_s": 1800,
    }


def _clip(path: Path, seconds: int = TIMING_SECONDS) -> None:
    """Generated audio to time the worker on: a tone at a fifth of full scale, as 16 kHz float32."""
    samples = array("f", (0.2 * math.sin(2 * math.pi * 220 * i / RATE) for i in range(seconds * RATE)))
    path.write_bytes(samples.tobytes())


Timer = Callable[[Speech], tuple[float, int | None]]


def time_worker(speech: Speech, *, run: Run = subprocess.run, which: Which = shutil.which) -> tuple[float, int | None]:
    """The worker's seconds on 30 s of generated audio, VAD off, and on CUDA the peak VRAM it took, from nvidia-smi."""
    with tempfile.TemporaryDirectory(prefix="docket-speech-") as tmp:
        pcm = Path(tmp) / "clip.f32"
        _clip(pcm)
        watch = _Vram(which("nvidia-smi"), run) if speech.device == "cuda" else None
        started = time.monotonic()
        try:
            client.run_job(
                client.command(speech.python),
                spec(speech, pcm, language="en", prompt=None, vad=False),
                time_limit=TIMING_LIMIT,
            )
        finally:
            seconds = time.monotonic() - started
            peak = watch.stop() if watch is not None else None
    return seconds, peak


class _Vram:
    """nvidia-smi's memory in use, every half second while the timing runs: its peak above where it started."""

    def __init__(self, smi: str | None, run: Run) -> None:
        self.smi, self.run = smi, run
        self.base = self._used()
        self.peak = self.base
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def _used(self) -> int | None:
        return _smi(self.smi, self.run, "memory.used")

    def _watch(self) -> None:
        while not self._done.wait(0.5):
            used = self._used()
            if used is not None and (self.peak is None or used > self.peak):
                self.peak = used

    def stop(self) -> int | None:
        self._done.set()
        self._thread.join(5.0)
        return self.peak - self.base if self.peak is not None and self.base is not None else None


def _smi(smi: str | None, run: Run, field: str) -> int | None:
    """One of the first GPU's memory figures in MB, as nvidia-smi gives it, or None."""
    if smi is None:
        return None
    found = run(
        [smi, f"--query-gpu={field}", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )  # fmt: skip
    first = found.stdout.strip().splitlines()[:1]
    return int(first[0]) if found.returncode == 0 and first and first[0].strip().isdigit() else None


def vram_total(which: Which, run: Run) -> int | None:
    return _smi(which("nvidia-smi"), run, "memory.total")


def build_env(device: Device, uv: str, run: Run) -> None:
    env = env_dir(device)
    env.parent.mkdir(parents=True, exist_ok=True)
    uv_step([uv, "venv", "--clear", "--python", sys.executable, str(env)], "making the speech environment", run)
    source = resources.files("docket.models").joinpath(f"requirements-{_requirements(device)}.txt")
    with resources.as_file(source) as wanted:
        uv_step(
            [uv, "pip", "install", "--python", str(config.env_python(env)), "--require-hashes", "-r", str(wanted)],
            "installing the speech packages",
            run,
        )


def install(
    name: str = DEFAULT_SPEECH,
    wanted: str = "auto",
    *,
    say: Callable[[str], None] = print,
    which: Which = shutil.which,
    run: Run = subprocess.run,
    opener: Opener = default_opener,
    timer: Timer | None = None,
) -> Installed:
    model = SPEECH.get(name)
    if model is None:
        raise DocketError(f"no speech model {name!r}; there is {', '.join(SPEECH)}")
    device = pick_device(wanted, which=which, run=run)
    uv = which("uv")
    if uv is None:
        raise DocketError("the speech environment is built with uv, which isn't on PATH: https://docs.astral.sh/uv/")
    say(
        f"speech: whisper {model.name} ({model.licence}) on {device}; downloads {model.download / 1e6:,.0f} MB of "
        f"model files and {PACKAGES[device]} of packages, once"
    )
    if device == "cuda":
        total = vram_total(which, run)
        if total is not None and total < SHARED_GB * 1024:
            raise DocketError(
                f"this GPU has {total / 1024:.1f} GB of memory, and Whisper needs about {VRAM_NEED_GB} GB besides "
                "search's models: install it with --device cpu"
            )
        say(
            f"it installs NVIDIA's cuBLAS under NVIDIA's terms, and needs about {VRAM_NEED_GB} GB of the GPU's memory "
            "while it transcribes"
        )
        if total is not None and total < ALONGSIDE_GB * 1024:
            say(f"this GPU has {total / 1024:.1f} GB: Whisper runs only while nothing else holds it")
    else:
        say("on a CPU an hour of audio takes about an hour, so Docket transcribes only when you ask: docket transcribe")
    say(f"environment: {env_dir(device)}")
    build_env(device, uv, run)
    for file in model.files:
        say(f"{file.path}: {'downloaded' if fetch(model, file, opener) else 'already here'}")
    speech = Speech(model, device, config.env_python(env_dir(device)))
    seconds, vram = (timer or time_worker)(speech)
    found = Installed(
        model.name, device, config.requirements_hash(_requirements(device)), config.python_version(), seconds, vram
    )
    mark(found)
    userconfig.set_section(SECTION, {"model": model.name, "device": device})
    used = f", {vram:,} MB of VRAM" if vram is not None else ""
    say(f"ready: whisper {model.name} on {device}, {TIMING_SECONDS} s of audio in {seconds:.1f} s{used}")
    return found


def remove(say: Callable[[str], None] = print) -> None:
    """The speech environments, the speech models' files and the choice, gone; the embedder stays."""
    had = chosen() != OFF
    userconfig.set_section(SECTION, None)
    held: list[str] = []
    folders = [env_dir(d) for d in config.DEVICES]
    folders += [config.file_path(m, m.files[0]).parent for m in SPEECH.values()]
    for folder in folders:
        if not folder.exists():
            continue
        try:
            remove_tree(folder)
        except OSError:
            held.append(str(folder))
            continue
        say(f"removed {folder}")
        had = True
    if held:
        raise DocketError(f"a transcription holds these files ({', '.join(held)}): let it end, then remove again")
    say("speech is off on this machine" if had else "speech wasn't installed on this machine")


def status() -> str | None:
    """One line for `docket models status`: the speech model and device, its timing and VRAM, or why it's off; none
    when speech was never installed here."""
    found = chosen()
    if found == OFF:
        return None
    if isinstance(found, str):
        return f"speech     off: {found}"
    marker = installed(found.device)
    timed = marker if isinstance(marker, Installed) else None
    took = f", {TIMING_SECONDS} s of audio in {timed.seconds:.1f} s" if timed is not None else ""
    used = f", {timed.vram_mb:,} MB of VRAM" if timed is not None and timed.vram_mb is not None else ""
    return f"speech     whisper {found.model.name} on {found.device}{took}{used}"
