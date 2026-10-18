"""The speech worker and its client (DR-0567, DR-0569): the worker's protocol with a stand-in model, its pieces and
their offsets, the error it names by type only; the client's limits against a stand-in worker that hangs, crashes or
answers junk. No model runs here and nothing reaches the network; the audio is generated."""

from __future__ import annotations

import io
import json
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from docket.errors import DocketError
from docket.speech import client, worker

FAKE = Path(__file__).with_name("speech_fake_worker.py")


def fake(**plan: Any) -> list[str]:
    return [sys.executable, str(FAKE), json.dumps(plan)]


def pcm(path: Path, seconds: float, *, quiet: tuple[float, float] | None = None) -> Path:
    """Generated audio as the decoder writes it: 16 kHz mono float32, a low hum, silent in `quiet`."""
    n = int(seconds * worker.RATE)
    samples = np.full(n, 0.1, dtype="<f4")
    if quiet is not None:
        samples[int(quiet[0] * worker.RATE) : int(quiet[1] * worker.RATE)] = 0.0
    path.write_bytes(samples.tobytes())
    return path


class Model:
    """faster-whisper's model, standing in: one segment per piece, half a second in."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, dict[str, Any]]] = []

    def transcribe(self, audio: Any, **settings: Any) -> tuple[Iterator[Any], Any]:
        self.calls.append((len(audio), settings))
        said = types.SimpleNamespace(start=0.5, end=1.25, text=" hello there", avg_logprob=-0.3, no_speech_prob=0.02)
        return iter([said]), types.SimpleNamespace(language="en")


def spec(tmp_path: Path, audio: Path, *, model: bool = True, **over: Any) -> dict[str, Any]:
    folder = tmp_path / "model"
    folder.mkdir(exist_ok=True)
    if model:
        (folder / "model.bin").write_bytes(b"weights")
    return {
        "model": str(folder),
        "device": "cpu",
        "compute": "int8",
        "threads": 2,
        "pcm": str(audio),
        "language": None,
        "prompt": "Résztvevők és témák: Ada Example.",
        "vad": True,
        "piece_s": 2,
        **over,
    }


def lines(out: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_pieces_end_on_the_quietest_second_before_their_longest_length() -> None:
    samples = np.full(300 * worker.RATE, 0.1, dtype="<f4")
    samples[99 * worker.RATE : 101 * worker.RATE] = 0.0
    samples[190 * worker.RATE : 192 * worker.RATE] = 0.0
    found = worker.pieces(samples, 120, np)
    assert [(a / worker.RATE, b / worker.RATE) for a, b in found] == [(0, 99.5), (99.5, 191.0), (191.0, 300)]
    assert worker.pieces(samples[: 10 * worker.RATE], 120, np) == [(0, 10 * worker.RATE)]


def test_the_worker_answers_hello_segments_on_the_recording_s_timeline_progress_and_done(tmp_path: Path) -> None:
    audio = pcm(tmp_path / "a.f32", 5.0)
    model = Model()
    out = io.StringIO()
    assert worker.run(spec(tmp_path, audio), out, loader=lambda _: model) == 0
    said = lines(out)
    assert said[0] == {"hello": {"device": "cpu", "compute": "int8"}}
    segments = [line["segment"] for line in said if "segment" in line]
    # Pieces of 2 s at most, each ending on a quiet second: the offsets carry each piece to the recording's time.
    starts = [s["start_ms"] for s in segments]
    assert starts[0] == 500 and starts == sorted(starts) and len(segments) == len(model.calls) >= 3
    assert segments[0] == {"start_ms": 500, "end_ms": 1250, "text": "hello there", "logprob": -0.3, "no_speech": 0.02}
    assert [line["progress"] for line in said if "progress" in line][-1] == 5000
    assert said[-1] == {"done": {"duration_ms": 5000, "language": "en"}}
    first, later = model.calls[0][1], model.calls[1][1]
    assert first["language"] is None and later["language"] == "en", "the first piece's language holds for the rest"
    assert first["initial_prompt"] == "Résztvevők és témák: Ada Example."
    assert first["vad_filter"] is True and first["vad_parameters"] == worker.VAD
    assert first["condition_on_previous_text"] is False and first["beam_size"] == 5 and first["temperature"] == 0.0


def test_the_worker_names_an_error_by_its_type_only(tmp_path: Path) -> None:
    audio = pcm(tmp_path / "a.f32", 1.0)
    out = io.StringIO()
    assert worker.run(spec(tmp_path, audio, model=False), out) == 1
    assert lines(out) == [{"error": "ModelMissingError"}]

    def broken(_: dict[str, Any]) -> Any:
        raise RuntimeError("a message that could quote a transcript: Ada Example said hello")

    out = io.StringIO()
    assert worker.run(spec(tmp_path, audio), out, loader=broken) == 1
    assert lines(out) == [{"error": "RuntimeError"}]


def test_the_worker_s_main_loads_faster_whisper_offline_and_sends_its_chatter_to_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded: dict[str, Any] = {}

    def whisper_model(folder: str, **settings: Any) -> Model:
        print("loading the model")
        loaded.update(folder=folder, **settings)
        return Model()

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=whisper_model))
    audio = pcm(tmp_path / "a.f32", 1.0)
    out, err = io.StringIO(), io.StringIO()
    assert worker.main([json.dumps(spec(tmp_path, audio, piece_s=1800))], out, err) == 0
    assert loaded == {
        "folder": str(tmp_path / "model"),
        "device": "cpu",
        "compute_type": "int8",
        "cpu_threads": 2,
        "local_files_only": True,
    }
    assert err.getvalue() == "loading the model\n"
    assert [next(iter(line)) for line in lines(out)] == ["hello", "segment", "progress", "done"]


def test_the_client_takes_the_worker_s_segments_language_and_progress(tmp_path: Path) -> None:
    audio = pcm(tmp_path / "a.f32", 3.0)
    seen: list[int] = []
    got = client.run_job(
        fake(segments=[[0, 900, "one"], [1000, 2000, "two"]], language="hu"),
        spec(tmp_path, audio),
        time_limit=30,
        progress=seen.append,
    )
    assert [(s.start_ms, s.end_ms, s.text) for s in got.segments] == [(0, 900, "one"), (1000, 2000, "two")]
    assert got.duration_ms == 3000 and got.language == "hu" and seen == [3000]
    assert got.stderr == "a library's chatter, kept and never logged\n"
    assert client.command(Path("py")) == ["py", "-I", "-X", "utf8", str(client.WORKER)]


@pytest.mark.parametrize(
    ("behave", "said"),
    [
        ("missing", "the speech model isn't on this machine; run docket models install --speech"),
        ("silent", "the speech worker didn't start"),
        ("hang", "transcribing took longer than"),
        ("crash", "the speech worker stopped (it crashed or ran out of memory); nothing staged"),
        ("junk", "the speech worker stopped (it crashed or ran out of memory); nothing staged"),
        ("bad", "the speech worker answered out of its protocol; nothing staged"),
        ("error", "the speech worker stopped (RuntimeError); nothing staged"),
    ],
)
def test_a_worker_that_misbehaves_is_stopped_and_named(tmp_path: Path, behave: str, said: str) -> None:
    audio = pcm(tmp_path / "a.f32", 1.0)
    with pytest.raises(DocketError) as caught:
        client.run_job(fake(behave=behave), spec(tmp_path, audio), time_limit=2.0, hello_seconds=3.0)
    assert str(caught.value).startswith(said)


def test_a_line_past_the_cap_ends_the_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client, "LINE_CAP", 64)
    audio = pcm(tmp_path / "a.f32", 1.0)
    with pytest.raises(DocketError, match="crashed or ran out of memory"):
        client.run_job(fake(segments=[[0, 900, "x" * 100]]), spec(tmp_path, audio), time_limit=30)
