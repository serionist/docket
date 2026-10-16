"""Running the extraction child (DR-0485): one file at a time, each with a time limit, in a child with a memory cap,
started fresh every 100 files, and what came of each file.

| Outcome | What happened | The folder source then |
|---|---|---|
| `Done` | read whole: its parts, text and people, and the bytes' sha256 and size | stages it |
| `NotRead` | past a cap, damaged or protected, said in Docket's words | stores a record saying why, with no text |
| `Failed("stopped")` | past its time limit; the child is killed | keeps the file's last version, tries once more |
| `Failed("crashed")` | the child ended, ran out of memory or answered out of protocol | the same |
| `Failed("unreadable")` | the system wouldn't let the child read it now | the same, every scan |

A file's parts are kept only once the child says it is done. The child's stderr is kept in memory, never logged: a
traceback can quote a document.
"""

from __future__ import annotations

import contextlib
import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Literal, cast

from pydantic import TypeAdapter, ValidationError

from docket.childproc import GROUPS, child_env, stop
from docket.documents.formats import Extracted, Part, format_of
from docket.documents.limits import MIB
from docket.errors import DocketError

TIME_LIMIT = 120.0
HELLO_SECONDS = 30.0
STOP_SECONDS = 5.0
FILES_PER_CHILD = 100
MEMORY = 2 * 1024 * MIB
# A line holds at most a part's 1 MiB of text, escaped; a file's lines at most its 4 MiB, a few times over.
LINE_CAP = 8 * MIB
FILE_LINES_CAP = 24 * MIB
STDERR_KEEP = 64 * 1024
CHILD = ("-I", "-X", "utf8", "-m", "docket.documents.child")
# The service runs under pythonw on Windows: a console child would flash a window for every start.
NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_PART = TypeAdapter(Part)
_EXTRACTED = TypeAdapter(Extracted)


@dataclass(frozen=True)
class Done:
    sha256: str
    size: int
    extracted: Extracted


@dataclass(frozen=True)
class NotRead:
    reason: str
    detail: str
    sha256: str | None
    size: int | None

    @property
    def line(self) -> str:
        """What the file's record says in place of its text."""
        return f"Not read: {self.detail}."


@dataclass(frozen=True)
class Failed:
    why: Literal["stopped", "crashed", "unreadable"]


Outcome = Done | NotRead | Failed


class _JunkError(Exception):
    """A line out of the protocol: the child is stopped, and the file counts as a crash."""


@dataclass
class _Child:
    proc: subprocess.Popen[bytes]
    answers: queue.Queue[dict[str, Any] | None]
    job: int | None = None
    files: int = 0
    threads: list[threading.Thread] = field(default_factory=list[threading.Thread])


class Extractor:
    """The child, started on the first file and again after a stop, a crash, or every `files_per_child` files."""

    def __init__(
        self,
        command: Sequence[str] | None = None,
        *,
        time_limit: float = TIME_LIMIT,
        files_per_child: int = FILES_PER_CHILD,
        memory: int = MEMORY,
        hello_seconds: float = HELLO_SECONDS,
    ) -> None:
        self.command = [*(command or (sys.executable, *CHILD)), "--memory", str(memory)]
        self.time_limit = time_limit
        self.files_per_child = files_per_child
        self.memory = memory
        self.hello_seconds = hello_seconds
        self.started = 0
        self._child: _Child | None = None
        self._stderr = bytearray()
        self._lock = threading.Lock()
        self._folder = tempfile.TemporaryDirectory(prefix="docket-documents-")

    def __enter__(self) -> Extractor:
        return self

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None, tb: TracebackType | None) -> None:
        self.close()

    def stderr(self) -> str:
        """The children's last 64 KB of stderr, for a user who asks; never logged."""
        with self._lock:
            return self._stderr.decode("utf-8", "replace")

    def close(self) -> None:
        self._end()
        self._folder.cleanup()

    # -- one file -------------------------------------------------------------------------------------------------

    def extract(self, path: Path) -> Outcome:
        fmt = format_of(path.name)
        if fmt is None:
            raise DocketError(f"{path.suffix or 'a file without an extension'} isn't a document format Docket reads")
        child = self._ready()
        stdin = cast(IO[bytes], child.proc.stdin)
        try:
            stdin.write(json.dumps({"path": str(path), "format": fmt}).encode("utf-8") + b"\n")
            stdin.flush()
        except OSError:
            self._end(kill=True)
            return Failed("crashed")
        try:
            outcome = self._answer(child)
        except _JunkError:
            self._end(kill=True)
            return Failed("crashed")
        if outcome is None:
            self._end(kill=True)
            return Failed("stopped")
        child.files += 1
        if child.files >= self.files_per_child:
            self._end()
        return outcome

    def _answer(self, child: _Child) -> Outcome | None:
        """The child's answer to one file, or None when the time limit passed first."""
        deadline = time.monotonic() + self.time_limit
        parts: list[Part] = []
        while True:
            try:
                message = child.answers.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                return None
            if message is None or len(message) != 1:
                raise _JunkError
            kind, body = next(iter(message.items()))
            try:
                if kind == "part":
                    parts.append(_PART.validate_python(body))
                elif kind == "done":
                    facts = cast(dict[str, Any], body)
                    extracted = _EXTRACTED.validate_python({**_without(facts, "sha256", "size"), "parts": parts})
                    return Done(str(facts["sha256"]), int(facts["size"]), extracted)
                elif kind == "not_read":
                    facts = cast(dict[str, Any], body)
                    size = facts.get("size")
                    sha = facts.get("sha256")
                    return NotRead(
                        str(facts["reason"]),
                        str(facts["detail"]),
                        str(sha) if sha is not None else None,
                        int(size) if size is not None else None,
                    )
                elif kind == "unreadable":
                    return Failed("unreadable")
                else:
                    raise _JunkError
            except (ValidationError, KeyError, TypeError, ValueError):
                raise _JunkError from None

    # -- the child ------------------------------------------------------------------------------------------------

    def _ready(self) -> _Child:
        if self._child is not None and self._child.proc.poll() is None:
            return self._child
        self._end()
        proc = subprocess.Popen(  # noqa: S603 - Docket's own interpreter and module, or a test's stand-in
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self._folder.name,
            env=child_env(),
            start_new_session=GROUPS,
            creationflags=NO_WINDOW,
        )
        # Before the child is sent anything: on Windows the job object holds its memory cap.
        try:
            job = _job(proc.pid, self.memory)
        except DocketError:
            # Leaving the block closes its pipes and waits for it.
            with proc:
                stop(proc)
            raise
        child = _Child(proc, queue.Queue(), job=job)
        self._child = child
        self.started += 1
        out, err = cast(tuple[IO[bytes], IO[bytes]], (proc.stdout, proc.stderr))
        child.threads = [
            threading.Thread(target=_read, args=(out, child.answers), daemon=True),
            threading.Thread(target=self._keep_stderr, args=(err,), daemon=True),
        ]
        for thread in child.threads:
            thread.start()
        try:
            hello = child.answers.get(timeout=self.hello_seconds)
        except queue.Empty:
            hello = None
        if hello != {"ready": True}:
            self._end(kill=True)
            raise DocketError(
                "the documents' reader didn't start; its errors are kept, never logged, since they can quote a file"
            )
        return child

    def _end(self, *, kill: bool = False) -> None:
        """Let the child end, closing its input, or with `kill` stop it at once: one past its limit or out of
        protocol isn't waited for."""
        child, self._child = self._child, None
        if child is None:
            return
        stdin = cast(IO[bytes], child.proc.stdin)
        with contextlib.suppress(OSError):
            stdin.close()
        try:
            if kill:
                raise subprocess.TimeoutExpired(self.command, 0)
            child.proc.wait(timeout=STOP_SECONDS)
        except subprocess.TimeoutExpired:
            stop(child.proc)
            child.proc.wait()
        _close_job(child.job)
        for thread in child.threads:
            thread.join(5.0)

    def _keep_stderr(self, pipe: IO[bytes]) -> None:
        with pipe:
            while chunk := pipe.read(4096):
                with self._lock:
                    self._stderr.extend(chunk)
                    del self._stderr[:-STDERR_KEEP]


def _without(facts: dict[str, Any], *names: str) -> dict[str, Any]:
    return {k: v for k, v in facts.items() if k not in names}


def _read(pipe: IO[bytes], answers: queue.Queue[dict[str, Any] | None]) -> None:
    """The child's lines as answers; None once it ends or says something out of protocol, after which nothing more is
    read from it."""
    total = 0
    with pipe:
        while line := pipe.readline(LINE_CAP + 1):
            total += len(line)
            message: Any = None
            if len(line) <= LINE_CAP and line.endswith(b"\n") and total <= FILE_LINES_CAP:
                with contextlib.suppress(ValueError):
                    message = json.loads(line)
            if not isinstance(message, dict):
                break
            answer = cast(dict[str, Any], message)
            if "done" in answer or "not_read" in answer or "unreadable" in answer:
                total = 0
            answers.put(answer)
    answers.put(None)


# -- the memory cap on Windows: a job object --------------------------------------------------------------------------

if sys.platform == "win32":  # pragma: no cover - Windows' own API; test_documents_extract runs it there
    import ctypes
    from ctypes import wintypes

    JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x100
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_TERMINATE = 0x0001

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    def _job(pid: int, memory: int) -> int | None:
        """A job object that caps the child's committed memory and kills it when the job's handle closes, with the
        child in it. A system that refuses one fails the start: the cap is not optional."""
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            raise DocketError(f"Windows refused a job object for the documents' reader ({ctypes.get_last_error()})")
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        info.ProcessMemoryLimit = memory
        process = _kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        try:
            ok = (
                _kernel32.SetInformationJobObject(
                    job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS, ctypes.byref(info), ctypes.sizeof(info)
                )
                and process
                and _kernel32.AssignProcessToJobObject(job, process)
            )
        finally:
            if process:
                _kernel32.CloseHandle(process)
        if not ok:
            error = ctypes.get_last_error()
            _kernel32.CloseHandle(job)
            raise DocketError(f"Windows refused the documents' reader its memory cap ({error})")
        return int(job)

    def _close_job(job: int | None) -> None:
        if job:
            _kernel32.CloseHandle(job)

else:  # pragma: no cover - one of the two runs on each system

    def _job(pid: int, memory: int) -> int | None:
        """No job objects here: Linux's cap is the child's own, and macOS has none it enforces."""
        return None

    def _close_job(job: int | None) -> None:
        """Nothing to close where there are no job objects."""
