"""Cutting a recording's audio before it is encrypted (DR-0559, DR-0560): the child that decodes it, its limits, and
the folder its plain files live in.

The audio arrives uncut, so it is plain only here: in `<private tmp>/media-cut-<pid>/`, one folder per process,
inside Docket's private folder. The put deletes its files on every path; a process killed mid-cut leaves its folder,
which the next sweep removes once that pid no longer runs, whatever its age.

| Outcome | Then |
|---|---|
| `Cut` | the WebM Opus file in the folder, its duration and onset, and what the input was |
| `DocketError` | why, in Docket's words: past the time limit, crashed, no audio, damaged, too long; files gone |

The child's stderr is read and dropped, never logged: a library's message can quote the file.
"""

from __future__ import annotations

import collections
import contextlib
import functools
import json
import os
import queue
import secrets
import shutil
import subprocess
import sys
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, cast

from docket import childproc
from docket.childproc import CAPPED, GROUPS, child_env, stop
from docket.errors import DocketError
from docket.vault.home import Home, make_private

if TYPE_CHECKING:  # the ranges module reaches the vault package, which imports the gate
    from docket.media.ranges import Needed

MIB = 1024 * 1024
TIME_LIMIT = 900.0
HELLO_SECONDS = 30.0
MEMORY = 512 * MIB
MAX_MS = 12 * 3600 * 1000
# 16 kHz mono float32, as the speech worker takes it.
PCM_BYTES_PER_SECOND = 16_000 * 4
PAD_MS = 250
# The timeline checks, failing closed (DR-0560): the transcript must end before the audio does, give or take END_SLACK,
# and the audio's first sound must fall within ONSET_SLACK of the transcript's first words.
END_SLACK_MS = 1000
ONSET_SLACK_MS = 1500
FOLDER = "media-cut-"
LINE_CAP = 64 * 1024
CHILD = ("-I", "-X", "utf8", "-m", "docket.media.child")
_TIMEOUT = object()

Range = tuple[int, int]


@dataclass(frozen=True)
class Cut:
    """The cut audio, plain, in this process's folder, and what the child learned decoding it."""

    path: Path
    duration_ms: int
    onset_ms: int | None
    codec: str
    rate: int
    channels: int
    bit_rate: int | None

    @property
    def source(self) -> str:
        """What came in, for the put's line: the first real recording shows what its source sends."""
        rate = f"{self.bit_rate // 1000} kbit/s" if self.bit_rate else "bit rate unknown"
        return f"from {self.codec}, {self.rate} Hz, {self.channels} channel(s), {rate}"


def _seconds(ms: int) -> str:
    return f"{ms / 1000:.1f} s"


def timeline_problem(rid: str, needed: Needed, done: Cut) -> str | None:
    """Why the audio and its transcript may not share a timeline, or None: a cut that lands elsewhere than the
    transcript's would leave the stretch audible."""
    if needed.last_ms is not None and needed.last_ms > done.duration_ms + END_SLACK_MS:
        audio = _seconds(done.duration_ms)
        return (
            f"{rid}'s transcript ends at {_seconds(needed.last_ms)}, after its audio does ({audio}): the two may not "
            "share a timeline, so the audio wasn't stored"
        )
    if needed.first_ms is not None:
        if done.onset_ms is None:
            return f"{rid}'s audio is silent throughout though its transcript isn't, so the audio wasn't stored"
        if abs(done.onset_ms - needed.first_ms) > ONSET_SLACK_MS:
            return (
                f"{rid}'s audio first sounds at {_seconds(done.onset_ms)} and its transcript starts at "
                f"{_seconds(needed.first_ms)}, {_seconds(abs(done.onset_ms - needed.first_ms))} apart: the two may not "
                "share a timeline, so the audio wasn't stored"
            )
    return None


def onset_note(done: Cut) -> str:
    return f", first sound at {_seconds(done.onset_ms)}" if done.onset_ms is not None else ""


def padded(ranges: Sequence[Range], pad: int = PAD_MS) -> list[Range]:
    """Each range widened by `pad` either side, from 0, and the ranges that then touch made one."""
    out: list[Range] = []
    for start, end in sorted((max(0, s - pad), e + pad) for s, e in ranges):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def folder(home: Home) -> Path:
    """This process's folder for plain audio, made private."""
    path = make_private(home.git_dir, home.tmp_dir) / f"{FOLDER}{os.getpid()}"
    path.mkdir(exist_ok=True)
    return path


def plain_file(home: Home, suffix: str) -> Path:
    return folder(home) / f"{secrets.token_hex(6)}{suffix}"


def done_with(*paths: Path | None) -> None:
    """Delete a put's plain files, and its folder once nothing else of this process is in it."""
    for path in paths:
        if path is not None:
            path.unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                path.parent.rmdir()


def sweep(home: Home) -> int:
    """Remove the folders of processes that no longer run: what a process killed mid-cut left. How many went."""
    if not home.tmp_dir.is_dir():
        return 0
    gone = 0
    for path in home.tmp_dir.glob(f"{FOLDER}*"):
        pid = path.name.removeprefix(FOLDER)
        if not path.is_dir() or not pid.isdigit() or int(pid) == os.getpid() or childproc.alive(int(pid)):
            continue
        shutil.rmtree(path, ignore_errors=True)
        gone += not path.exists()
    return gone


def cut(
    home: Home,
    source: Path,
    ext: str,
    ranges: Sequence[Range],
    *,
    max_bytes: int,
    command: Sequence[str] | None = None,
    time_limit: float = TIME_LIMIT,
    memory: int = MEMORY,
    hello_seconds: float = HELLO_SECONDS,
) -> Cut:
    """`source` decoded in the child, the padded ranges silenced, and written as WebM Opus beside it."""
    output = plain_file(home, ".webm")
    request = {
        "input": str(source),
        "format": ext,
        "output": str(output),
        "cuts": [list(r) for r in padded(ranges)],
        "max_ms": MAX_MS,
        "max_bytes": max_bytes,
    }
    argv = [*(command or (sys.executable, *CHILD)), "--memory", str(memory)]
    try:
        answer = _run(argv, request, output.parent, time_limit=time_limit, memory=memory, hello_seconds=hello_seconds)
        return _cut(answer, output)
    except BaseException:
        done_with(output)
        raise


def decode_pcm(
    home: Home,
    source: Path,
    ext: str,
    *,
    command: Sequence[str] | None = None,
    time_limit: float = TIME_LIMIT,
    memory: int = MEMORY,
    hello_seconds: float = HELLO_SECONDS,
) -> Cut:
    """`source` decoded in the child to 16 kHz mono float32 beside it, for the speech worker (DR-0569): at most
    12 hours, so at most what 12 hours take."""
    output = plain_file(home, ".f32")
    request: dict[str, Any] = {
        "mode": "pcm",
        "input": str(source),
        "format": ext,
        "output": str(output),
        "cuts": [],
        "max_ms": MAX_MS,
        "max_bytes": MAX_MS // 1000 * PCM_BYTES_PER_SECOND,
    }
    argv = [*(command or (sys.executable, *CHILD)), "--memory", str(memory)]
    try:
        answer = _run(argv, request, output.parent, time_limit=time_limit, memory=memory, hello_seconds=hello_seconds)
        return _cut(answer, output, failed="the audio wasn't decoded for speech")
    except BaseException:
        done_with(output)
        raise


def _cut(answer: dict[str, Any], output: Path, *, failed: str = "the audio wasn't stored") -> Cut:
    try:
        if "not_read" in answer:
            raise DocketError(f"{failed}: {answer['not_read']['detail']}")
        facts = answer["done"]
        bit_rate = facts["bit_rate"]
        onset = facts["onset_ms"]
        return Cut(
            path=output,
            duration_ms=int(facts["duration_ms"]),
            onset_ms=int(onset) if onset is not None else None,
            codec=str(facts["codec"]),
            rate=int(facts["rate"]),
            channels=int(facts["channels"]),
            bit_rate=int(bit_rate) if bit_rate is not None else None,
        )
    except (KeyError, TypeError, ValueError):
        raise DocketError(f"the audio cutter answered out of its protocol: {failed}") from None


def _run(
    argv: list[str], request: dict[str, Any], cwd: Path, *, time_limit: float, memory: int, hello_seconds: float
) -> dict[str, Any]:
    proc = subprocess.Popen(  # noqa: S603 - Docket's own interpreter and module, or a test's stand-in
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=child_env(),
        start_new_session=GROUPS,
        creationflags=childproc.CREATE_NO_WINDOW,
    )
    try:
        job = childproc.job(proc.pid, memory, "the audio cutter")
    except DocketError:
        with proc:
            stop(proc)
        raise
    answers: queue.Queue[dict[str, Any] | None] = queue.Queue()
    out, err = cast(tuple[IO[bytes], IO[bytes]], (proc.stdout, proc.stderr))
    threads = [
        threading.Thread(target=_read, args=(out, answers), daemon=True),
        threading.Thread(target=_drop, args=(err,), daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        hello = _next(answers, hello_seconds)
        said = cast(dict[str, Any], hello) if isinstance(hello, dict) else {}
        if said.get("ready") is not True:
            raise DocketError("the audio cutter didn't start, so nothing was stored")
        if CAPPED and said.get("memory") != memory:
            raise DocketError("the audio cutter started without its memory cap, so it was stopped")
        stdin = cast(IO[bytes], proc.stdin)
        try:
            stdin.write(json.dumps(request).encode("utf-8") + b"\n")
            stdin.close()
        except (OSError, ValueError):
            raise DocketError("the audio cutter stopped before it was asked anything, so nothing was stored") from None
        answer = _next(answers, time_limit)
        if answer is _TIMEOUT:
            minutes = round(time_limit / 60)
            raise DocketError(
                f"cutting the audio took longer than {minutes} minutes, so it was stopped and nothing stored"
            )
        if not isinstance(answer, dict):
            raise DocketError("the audio cutter stopped (it crashed or ran out of memory), so nothing was stored")
        return cast(dict[str, Any], answer)
    finally:
        with contextlib.suppress(OSError):
            cast(IO[bytes], proc.stdin).close()
        if proc.poll() is None:
            stop(proc)
        proc.wait()
        childproc.close_job(job)
        for thread in threads:
            thread.join(5.0)


def _next(answers: queue.Queue[dict[str, Any] | None], seconds: float) -> object:
    try:
        return answers.get(timeout=seconds)
    except queue.Empty:
        return _TIMEOUT


def _read(pipe: IO[bytes], answers: queue.Queue[dict[str, Any] | None]) -> None:
    """The child's lines as answers; None once it ends or says something out of protocol."""
    with pipe:
        while line := pipe.readline(LINE_CAP + 1):
            message: Any = None
            if len(line) <= LINE_CAP and line.endswith(b"\n"):
                with contextlib.suppress(ValueError):
                    message = json.loads(line)
            if not isinstance(message, dict):
                break
            answers.put(cast(dict[str, Any], message))
    answers.put(None)


def _drop(pipe: IO[bytes]) -> None:
    """The child's stderr, read to its end and kept nowhere, with no step that runs only when it wrote something."""
    with pipe:
        collections.deque(iter(functools.partial(pipe.read, 4096), b""), maxlen=0)
