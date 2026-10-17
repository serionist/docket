"""The speech worker (DR-0567, DR-0569): one job per process, `<speech env python> -I -X utf8 worker.py '<spec>'`.

It runs in the environment `docket models install --speech` built, which holds faster-whisper, CTranslate2, ONNX
Runtime, numpy and nothing of Docket's, so this file imports only the standard library at the top and its packages when
the job starts. The core starts it with an environment holding only what Python needs and `HF_HUB_OFFLINE=1`, and the
model is loaded with `local_files_only=True`: nothing reaches the network.

The spec is JSON: the model folder, the device and compute type, the threads, the PCM file (16 kHz mono float32,
little-endian, from Docket's decoder child), the language or null, the prompt or null, whether VAD runs, and the
longest piece in seconds. It answers JSON lines on stdout:

| Line | When |
|---|---|
| `{"hello": {"device": ..., "compute": ...}}` | the model loaded |
| `{"segment": {"start_ms", "end_ms", "text", "logprob", "no_speech"}}` | each segment, on the recording's timeline |
| `{"progress": <ms done>}` | after each piece |
| `{"done": {"duration_ms", "language"}}` | the whole file transcribed |
| `{"error": "<type>"}` | it stopped: a type only, never a message, which could quote a transcript |

Anything a library prints goes to stderr, which the core keeps and never logs.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

RATE = 16_000
# Settings the plan names (DR-0569): Silero's threshold, faster-whisper's own silence and padding, one pass at
# temperature 0, so the same audio and engine give the same segments.
VAD = {"threshold": 0.5, "min_silence_duration_ms": 2000, "speech_pad_ms": 400}
BEAM = 5
# Where a piece may end: the quietest second in the last minute before its longest length.
LOOK_BACK = 60


class ModelMissingError(Exception):
    """The model folder lacks its files: the install fetches them."""


Loader = Callable[[dict[str, Any]], Any]


def _dll_dirs() -> None:  # pragma: no cover - Windows' CUDA wheels only; the speech CI job runs the CPU
    """Windows finds NVIDIA's cuBLAS wheel only once its folder is on the DLL path."""
    if sys.platform != "win32":
        return
    for name in ("nvidia.cublas", "nvidia.cuda_nvrtc"):
        try:
            spec = importlib.util.find_spec(name)
        except ModuleNotFoundError:
            # The CPU environment has no NVIDIA package at all.
            continue
        locations = list(spec.submodule_search_locations or []) if spec is not None else []
        for location in locations:
            folder = Path(location) / "bin"
            if folder.is_dir():
                os.add_dll_directory(str(folder))
                os.environ["PATH"] = f"{folder}{os.pathsep}{os.environ.get('PATH', '')}"


def load(spec: dict[str, Any]) -> Any:  # pragma: no cover - the speech environment's packages; CI runs it on tiny
    _dll_dirs()
    whisper = importlib.import_module("faster_whisper")
    return whisper.WhisperModel(
        str(spec["model"]),
        device=spec["device"],
        compute_type=spec["compute"],
        cpu_threads=int(spec["threads"]),
        local_files_only=True,
    )


def pieces(samples: Any, piece_s: int, np: Any) -> list[tuple[int, int]]:
    """Where to cut a long file: at most `piece_s` seconds a piece, each ending on the quietest second in the minute
    before its longest end, so a cut rarely splits a word."""
    total = len(samples)
    longest = piece_s * RATE
    out: list[tuple[int, int]] = []
    start = 0
    while total - start > longest:
        end = start + longest
        window = max(start + RATE, end - LOOK_BACK * RATE)
        seconds = (end - window) // RATE
        stretch = np.asarray(samples[window : window + seconds * RATE], dtype=np.float32).reshape(seconds, RATE)
        quietest = int(np.argmin((stretch * stretch).sum(axis=1)))
        cut = window + quietest * RATE + RATE // 2
        out.append((start, cut))
        start = cut
    out.append((start, total))
    return out


def _say(out: TextIO, line: dict[str, Any]) -> None:
    out.write(json.dumps(line, ensure_ascii=False) + "\n")
    out.flush()


def transcribe(spec: dict[str, Any], out: TextIO, model: Any, np: Any) -> None:
    samples = np.memmap(spec["pcm"], dtype="<f4", mode="r")
    language: str | None = spec.get("language")
    for start, end in pieces(samples, int(spec["piece_s"]), np):
        found, info = model.transcribe(
            np.asarray(samples[start:end], dtype=np.float32),
            # The first piece's language, detected when none was given, holds for the rest: one file, one language.
            language=language,
            initial_prompt=spec.get("prompt"),
            vad_filter=bool(spec["vad"]),
            vad_parameters=dict(VAD),
            condition_on_previous_text=False,
            beam_size=BEAM,
            temperature=0.0,
        )
        language = language or getattr(info, "language", None)
        offset = start * 1000 // RATE
        for segment in found:
            _say(
                out,
                {
                    "segment": {
                        "start_ms": offset + round(float(segment.start) * 1000),
                        "end_ms": offset + round(float(segment.end) * 1000),
                        "text": str(segment.text).strip(),
                        "logprob": float(segment.avg_logprob),
                        "no_speech": float(segment.no_speech_prob),
                    }
                },
            )
        _say(out, {"progress": end * 1000 // RATE})
    _say(out, {"done": {"duration_ms": len(samples) * 1000 // RATE, "language": language}})


def run(spec: dict[str, Any], out: TextIO, loader: Loader = load) -> int:
    try:
        if not (Path(str(spec["model"])) / "model.bin").is_file():
            raise ModelMissingError
        np = importlib.import_module("numpy")
        model = loader(spec)
        _say(out, {"hello": {"device": spec["device"], "compute": spec["compute"]}})
        transcribe(spec, out, model, np)
    except Exception as e:
        _say(out, {"error": type(e).__name__})
        return 1
    return 0


def main(argv: list[str], out: TextIO, err: TextIO) -> int:
    stdout = sys.stdout
    sys.stdout = err
    try:
        return run(json.loads(argv[0]), out)
    finally:
        sys.stdout = stdout


if __name__ == "__main__":  # pragma: no cover - the worker's process; main() is tested in-process
    sys.exit(main(sys.argv[1:], sys.stdout, sys.stderr))
