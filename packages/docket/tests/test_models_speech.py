"""`docket models install --speech`, its status and its remove (DR-0567, DR-0568), with uv, the hub, nvidia-smi and
the worker standing in: the environment's build, the files at their pinned hashes, the timing and the peak VRAM in the
marker, the user config's `speech` section, and what each refuses."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import threading
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from docket import userconfig
from docket.errors import DocketError
from docket.models import config
from docket.models.catalogue import ModelFile
from docket.speech import catalogue, client, setup
from docket.speech.catalogue import SpeechModel

FAKE_WORKER = Path(__file__).with_name("speech_fake_worker.py")
FILES = {"model.bin": b"weights", "config.json": b"{}"}
MODEL = SpeechModel(
    name="fake",
    repo="example/fake-whisper",
    revision="0123456789abcdef0123456789abcdef01234567",
    files=tuple(ModelFile(name, len(data), hashlib.sha256(data).hexdigest()) for name, data in FILES.items()),
    licence="MIT",
)


@pytest.fixture(autouse=True)
def fake_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(catalogue.SPEECH, "fake", MODEL)


def done(code: int = 0, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, out, err)


class Machine:
    """uv and nvidia-smi as the install calls them: a venv makes the environment's Python; the GPU has `total` MB."""

    def __init__(self, total: int | None = None, fail_at: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.total = total
        self.fail_at = fail_at

    def which(self, name: str) -> str | None:
        return None if name == "nvidia-smi" and self.total is None else f"/bin/{name}"

    def __call__(self, args: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        if args[0] == "/bin/nvidia-smi":
            return done(0, "GPU 0: An Example GPU") if args[1] == "-L" else done(0, f"{self.total}\n")
        step = args[1]
        if step == self.fail_at:
            return done(2, "", "error: no matching distribution\nhint: check the index")
        if step == "venv":
            python = config.env_python(Path(args[-1]))
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b"")
        return done()


def hub(request: urllib.request.Request, timeout: float) -> io.BytesIO:
    """The hub, standing in: each file at its pinned revision."""
    assert request.full_url.startswith(f"https://huggingface.co/{MODEL.repo}/resolve/{MODEL.revision}/")
    return io.BytesIO(FILES[request.full_url.rsplit("/", 1)[1]])


def install(machine: Machine, said: list[str], device: str = "cpu", vram: int | None = None) -> setup.Installed:
    return setup.install(
        "fake",
        device,
        say=said.append,
        which=machine.which,
        run=machine,
        opener=hub,
        timer=lambda speech: (1.25, vram),
    )


def test_install_builds_the_environment_fetches_the_files_times_them_and_turns_speech_on() -> None:
    machine, said = Machine(), []
    found = install(machine, said)
    assert machine.calls[0][:5] == ["/bin/uv", "venv", "--clear", "--python", sys.executable]
    assert str(machine.calls[0][-1]).endswith("env-speech-cpu")
    assert machine.calls[1][:3] == ["/bin/uv", "pip", "install"] and "--require-hashes" in machine.calls[1]
    assert machine.calls[1][-1].endswith("requirements-speech-cpu.txt")
    assert said[0] == (
        "speech: whisper fake (MIT) on cpu; downloads 0 MB of model files and about 0.3 GB of packages, once"
    )
    assert said[1].startswith("on a CPU an hour of audio takes about an hour")
    assert "model.bin: downloaded" in said and said[-1] == "ready: whisper fake on cpu, 30 s of audio in 1.2 s"
    assert found == setup.installed("cpu")
    assert userconfig.load()["speech"] == {"model": "fake", "device": "cpu"}
    chosen = setup.chosen()
    assert isinstance(chosen, setup.Speech) and chosen.model == MODEL and chosen.compute == "int8"
    assert chosen.folder == config.files_dir() / "example" / "fake-whisper" / MODEL.revision
    assert setup.status() == "speech     whisper fake on cpu, 30 s of audio in 1.2 s"
    # Again: the files are here, so only the environment and the timing run.
    said.clear()
    install(machine, said)
    assert "model.bin: already here" in said


def test_on_cuda_it_names_nvidia_s_terms_and_the_gpu_s_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    said: list[str] = []
    install(Machine(total=24_000), said, device="auto", vram=3900)
    assert said[1] == (
        "it installs NVIDIA's cuBLAS under NVIDIA's terms, and needs about 4.5 GB of the GPU's memory while it "
        "transcribes"
    )
    assert setup.status() == "speech     whisper fake on cuda, 30 s of audio in 1.2 s, 3,900 MB of VRAM"
    chosen = setup.chosen()
    assert isinstance(chosen, setup.Speech)
    assert setup.spec(chosen, Path("a.f32"), language="hu", prompt=None)["compute"] == "float16"
    said.clear()
    install(Machine(total=7000), said, device="cuda", vram=3900)
    assert "this GPU has 6.8 GB: Whisper runs only while nothing else holds it" in said
    with pytest.raises(DocketError, match=r"this GPU has 3.9 GB of memory.*install it with --device cpu"):
        install(Machine(total=4000), [], device="cuda")


def test_install_refuses_an_unknown_model_and_a_machine_without_uv() -> None:
    with pytest.raises(DocketError, match="no speech model 'huge'; there is "):
        setup.install("huge", "cpu")
    machine = Machine()
    with pytest.raises(DocketError, match="built with uv, which isn't on PATH"):
        setup.install("fake", "cpu", which=lambda name: None, run=machine, opener=hub, timer=lambda s: (1.0, None))
    with pytest.raises(DocketError, match=r"installing the speech packages failed \(uv exit 2\)"):
        install(Machine(fail_at="pip"), [])


def test_the_marker_and_the_choice_say_why_speech_won_t_run() -> None:
    assert setup.chosen() == setup.OFF and setup.status() is None
    userconfig.set_section("speech", {"model": "huge", "device": "cpu"})
    assert "names no model and device this Docket knows" in str(setup.chosen())
    assert str(setup.status()).startswith("speech     off: the speech section in")
    userconfig.set_section("speech", {"model": "fake", "device": "cpu"})
    assert setup.chosen() == "speech isn't installed for cpu on this machine; run docket models install --speech"
    install(Machine(), [])
    marker = setup.env_dir("cpu") / setup.MARKER
    data = json.loads(marker.read_text(encoding="utf-8"))
    marker.write_text(json.dumps({**data, "python": "2.7"}), encoding="utf-8")
    assert "from another Docket or Python" in str(setup.chosen())
    marker.write_text("{", encoding="utf-8")
    assert setup.chosen() == "the speech environment can't be read; run docket models install --speech"
    install(Machine(), [])
    config.file_path(MODEL, MODEL.files[0]).unlink()
    assert setup.chosen() == "the speech model lacks model.bin; run docket models install --speech"
    userconfig.config_path().write_text("speech: [unclosed", encoding="utf-8")
    assert "doesn't read as YAML, so speech is off" in str(setup.chosen())


def test_remove_takes_the_environments_the_files_and_the_choice_and_leaves_search_s() -> None:
    install(Machine(), [])
    search = config.env_dir("cpu")
    search.mkdir(parents=True)
    said: list[str] = []
    setup.remove(said.append)
    assert not setup.env_dir("cpu").exists() and not config.file_path(MODEL, MODEL.files[0]).exists()
    assert "speech" not in userconfig.load() and search.is_dir()
    assert said[-1] == "speech is off on this machine"
    said.clear()
    setup.remove(said.append)
    assert said == ["speech wasn't installed on this machine"]


def test_a_remove_a_running_job_holds_says_to_let_it_end(monkeypatch: pytest.MonkeyPatch) -> None:
    install(Machine(), [])

    def held(path: Path) -> None:
        raise PermissionError(13, "in use")

    monkeypatch.setattr(setup, "remove_tree", held)
    with pytest.raises(DocketError, match=r"a transcription holds these files \(.*env-speech-cpu.*\): let it end"):
        setup.remove(lambda _: None)
    assert setup.chosen() == setup.OFF


def test_the_timing_runs_the_worker_on_generated_audio_without_vad(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    got = tmp_path / "spec.json"
    monkeypatch.setattr(
        client, "command", lambda python: [sys.executable, str(FAKE_WORKER), json.dumps({"spec_out": str(got)})]
    )
    speech = setup.Speech(MODEL, "cpu", Path("python"))
    seconds, vram = setup.time_worker(speech)
    spec = json.loads(got.read_text(encoding="utf-8"))
    assert seconds > 0 and vram is None
    assert spec["vad"] is False and spec["language"] == "en" and spec["prompt"] is None
    assert not Path(spec["pcm"]).exists(), "the clip goes with its folder"


def test_the_gpu_s_peak_is_what_nvidia_smi_saw_above_where_it_started() -> None:
    readings: Iterator[str] = iter(["1000", "1200", "4100", "3000"])
    polled = threading.Event()
    calls = 0

    def smi(args: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        if calls >= 4:
            polled.set()
        return done(0, f"{next(readings, '3000')}\n")

    watch = setup._Vram("/bin/nvidia-smi", smi)  # pyright: ignore[reportPrivateUsage]
    assert polled.wait(10)
    assert watch.stop() == 3100
    assert setup._Vram(None, smi).stop() is None  # pyright: ignore[reportPrivateUsage]
    assert setup.vram_total(lambda _: "/bin/nvidia-smi", lambda *a, **k: done(9, "", "failed")) is None


def test_the_catalogue_pins_each_model_and_names_the_engine_a_transcript_carries() -> None:
    large = catalogue.SPEECH["large-v3"]
    assert large.engine == "whisper-large-v3@edaa852 faster-whisper-1.2.1"
    assert catalogue.DEFAULT_SPEECH == "large-v3" and round(large.download / 1e9, 2) == 3.09
    assert catalogue.SPEECH["tiny"].engine == "whisper-tiny@d90ca5f faster-whisper-1.2.1"
    assert all(len(m.revision) == 40 and all(len(f.sha256) == 64 for f in m.files) for m in catalogue.SPEECH.values())
