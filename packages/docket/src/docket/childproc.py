"""What every child process Docket starts shares: an environment holding only what Python needs to start, a stop
that takes the helpers it started with it where the system has process groups (DR-0123, DR-0485), and the flags a
console program such as git starts with (DR-0535). For the children that read untrusted files, the documents' reader
and the audio cutter (DR-0485, DR-0559): the memory cap a child sets on itself, the job its parent kills it through on
Windows, and whether a process still runs."""

from __future__ import annotations

import contextlib
import functools
import os
import signal
import subprocess
import sys
from collections.abc import Callable

# On every system, though only Windows uses it: its own body is Windows' alone.
from docket import winjob

# What Python needs to start on each system, and nothing the service holds in its own environment.
KEEP_ENV = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LANG", "LC_ALL")
GROUPS = hasattr(os, "killpg")
# Windows gives a console program a console of its own when the process starting it has none, as the service under
# pythonw has: about 140 ms on each git call (#132). The flag skips that; with a console it costs about 12 ms a call.
CREATE_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _no_groups(pid: int, sig: int) -> None:
    """Windows has no process groups: a helper a child starts there outlives it, though the child is stopped."""


_KILLPG: Callable[[int, int], None] = getattr(os, "killpg", _no_groups)
_SIGKILL: int = getattr(signal, "SIGKILL", signal.SIGTERM)


def child_env() -> dict[str, str]:
    return {name: os.environ[name] for name in KEEP_ENV if name in os.environ}


def stop(proc: subprocess.Popen[bytes]) -> None:
    """Kill a child, and on systems with process groups every process it started. One that has already ended, or whose
    group is gone, is left alone."""
    with contextlib.suppress(OSError):
        _KILLPG(proc.pid, _SIGKILL)
        proc.kill()


if sys.platform == "win32":  # pragma: no cover - one of the two runs on each system; test_childproc runs both
    import ctypes

    def _has_console() -> bool:
        # Attached to any console, a hidden one or a pseudoconsole included (Claude Code's shell has one with no
        # window); GetConsoleWindow is 0 there too, and the flag would make each call slower, not faster.
        return bool(ctypes.windll.kernel32.GetConsoleCP())

else:  # pragma: no cover

    def _has_console() -> bool:
        return True


@functools.cache
def console_flags() -> int:
    """The creation flags for a console program Docket starts: CREATE_NO_WINDOW when this process has no console
    (the service under pythonw), none when it has one (a command, a test). Asked once per process (DR-0535)."""
    return 0 if _has_console() else CREATE_NO_WINDOW


# -- the children that read untrusted files ------------------------------------------------------------------------

# Where the system enforces a memory cap the child sets on itself; macOS enforces none.
CAPPED = sys.platform in ("linux", "win32")

if sys.platform == "linux":  # pragma: no cover - one of the three runs on each system; the children's tests run them
    import resource

    def limit_memory(cap: int) -> int | None:
        """The child's address space capped, as Linux lets a process cap its own; the cap it got."""
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
        return resource.getrlimit(resource.RLIMIT_AS)[0]

elif sys.platform == "win32":  # pragma: no cover

    def limit_memory(cap: int) -> int | None:
        """The interpreter in a job of its own that caps its committed memory, and the cap its innermost job holds.
        The job's handle stays open for the process's life."""
        job = winjob.create(winjob.PROCESS_MEMORY, cap)
        winjob.assign(job)
        return winjob.own_memory_limit()

else:  # pragma: no cover

    def limit_memory(cap: int) -> int | None:
        """No cap macOS enforces: the child has the time limit and the size caps only."""
        return None


if sys.platform == "win32":  # pragma: no cover - Windows' own API; the children's tests run it there

    def job(pid: int, memory: int, who: str) -> int | None:
        """A job that kills the child when its handle closes, with the child in it, so a stop takes the interpreter a
        venv's launcher started too; its memory cap holds only where it caught the interpreter, which caps itself."""
        from docket.errors import DocketError  # noqa: PLC0415 - kept off every other child's start

        try:
            handle = winjob.create(winjob.PROCESS_MEMORY | winjob.KILL_ON_CLOSE, memory)
        except OSError as e:
            raise DocketError(f"Windows refused {who} a job object ({e.errno})") from None
        try:
            winjob.assign(handle, pid)
        except OSError as e:
            winjob.close(handle)
            raise DocketError(f"Windows refused {who} its job ({e.errno})") from None
        return handle

    def close_job(handle: int | None) -> None:
        if handle:
            winjob.close(handle)

    def alive(pid: int) -> bool:
        """Whether a process runs with this pid. Not `os.kill(pid, 0)`: on Windows that ends the process."""
        return winjob.running(pid)

else:  # pragma: no cover - one of the two runs on each system

    def job(pid: int, memory: int, who: str) -> int | None:
        """No job objects here: the child caps its own memory on Linux, and macOS has none it enforces."""
        return None

    def close_job(handle: int | None) -> None:
        """Nothing to close where there are no job objects."""

    def alive(pid: int) -> bool:
        """Whether a process runs with this pid: one we may not signal runs, and so is kept."""
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
