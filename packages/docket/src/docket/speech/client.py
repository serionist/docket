"""The core's side of the speech worker (DR-0567, DR-0569): one process per job, its lines, its limits.

The worker starts with an environment holding only what Python needs and `HF_HUB_OFFLINE=1`. It has a minute to load
its model and say hello; then the job has its time limit, after which it is stopped. Its stderr is kept, the last 64 KB,
for the install's error and never logged: a library's message can quote a transcript.
"""

from __future__ import annotations

import contextlib
import json
import queue
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, cast

from docket import childproc
from docket.childproc import GROUPS, child_env, stop
from docket.errors import DocketError

HELLO_SECONDS = 60.0
STDERR_KEEP = 64 * 1024
LINE_CAP = 1024 * 1024
WORKER = Path(__file__).with_name("worker.py")
MISSING = "the speech model isn't on this machine; run docket models install --speech"
_TIMEOUT = object()


@dataclass(frozen=True)
class Said:
    """One segment the worker gave, on the recording's timeline."""

    start_ms: int
    end_ms: int
    text: str
    logprob: float
    no_speech: float


@dataclass
class Result:
    segments: list[Said] = field(default_factory=list[Said])
    duration_ms: int = 0
    language: str | None = None
    stderr: str = ""


def command(python: Path) -> list[str]:
    return [str(python), "-I", "-X", "utf8", str(WORKER)]


def run_job(
    argv: Sequence[str],
    spec: dict[str, Any],
    *,
    time_limit: float,
    progress: Callable[[int], None] | None = None,
    hello_seconds: float = HELLO_SECONDS,
) -> Result:
    """The worker run on one job: its segments, the audio's length and the language it used."""
    env = {**child_env(), "HF_HUB_OFFLINE": "1"}
    proc = subprocess.Popen(  # noqa: S603 - the speech environment's own Python and Docket's worker, or a test's stand-in
        [*argv, json.dumps(spec)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=GROUPS,
        creationflags=childproc.CREATE_NO_WINDOW,
    )
    lines: queue.Queue[dict[str, Any] | None] = queue.Queue()
    kept = bytearray()
    out, err = cast(tuple[IO[bytes], IO[bytes]], (proc.stdout, proc.stderr))
    threads = [
        threading.Thread(target=_read, args=(out, lines), daemon=True),
        threading.Thread(target=_keep, args=(err, kept), daemon=True),
    ]
    for thread in threads:
        thread.start()
    result = Result()
    try:
        hello = _next(lines, hello_seconds)
        said = cast(dict[str, Any], hello) if isinstance(hello, dict) else {}
        if "error" in said:
            raise _refusal(str(said["error"]))
        if "hello" not in said:
            raise DocketError("the speech worker didn't start; docket models install --speech checks it")
        while True:
            line = _next(lines, time_limit)
            if line is _TIMEOUT:
                minutes = round(time_limit / 60)
                raise DocketError(f"transcribing took longer than {minutes} minutes, so it was stopped; nothing staged")
            if not isinstance(line, dict):
                raise DocketError("the speech worker stopped (it crashed or ran out of memory); nothing staged")
            if _take(cast(dict[str, Any], line), result, progress):
                return result
    finally:
        if proc.poll() is None:
            stop(proc)
        proc.wait()
        for thread in threads:
            thread.join(5.0)
        result.stderr = kept.decode("utf-8", "replace").replace("\r\n", "\n")


def _take(line: dict[str, Any], result: Result, progress: Callable[[int], None] | None) -> bool:
    """One line into the result; whether the job is done."""
    try:
        if "segment" in line:
            s = line["segment"]
            result.segments.append(
                Said(int(s["start_ms"]), int(s["end_ms"]), str(s["text"]), float(s["logprob"]), float(s["no_speech"]))
            )
            return False
        if "progress" in line:
            if progress is not None:
                progress(int(line["progress"]))
            return False
        if "done" in line:
            result.duration_ms = int(line["done"]["duration_ms"])
            language = line["done"].get("language")
            result.language = str(language) if language else None
            return True
        if "error" in line:
            raise _refusal(str(line["error"]))
    except (KeyError, TypeError, ValueError):
        pass
    raise DocketError("the speech worker answered out of its protocol; nothing staged")


def _refusal(kind: str) -> DocketError:
    if kind == "ModelMissingError":
        return DocketError(MISSING)
    return DocketError(f"the speech worker stopped ({kind}); nothing staged")


def _next(lines: queue.Queue[dict[str, Any] | None], seconds: float) -> object:
    try:
        return lines.get(timeout=seconds)
    except queue.Empty:
        return _TIMEOUT


def _read(pipe: IO[bytes], lines: queue.Queue[dict[str, Any] | None]) -> None:
    with pipe:
        while line := pipe.readline(LINE_CAP + 1):
            found: Any = None
            if len(line) <= LINE_CAP and line.endswith(b"\n"):
                with contextlib.suppress(ValueError):
                    found = json.loads(line)
            if not isinstance(found, dict):
                break
            lines.put(cast(dict[str, Any], found))
    lines.put(None)


def _keep(pipe: IO[bytes], kept: bytearray) -> None:
    with pipe:
        while chunk := pipe.read(4096):
            kept.extend(chunk)
            del kept[:-STDERR_KEEP]
