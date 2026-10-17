"""What every child process Docket starts shares: an environment holding only what Python needs to start, a stop
that takes the helpers it started with it where the system has process groups (DR-0123, DR-0485), and the flags a
console program such as git starts with (DR-0535)."""

from __future__ import annotations

import contextlib
import functools
import os
import signal
import subprocess
import sys
from collections.abc import Callable

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
        return bool(ctypes.windll.kernel32.GetConsoleWindow())

else:  # pragma: no cover

    def _has_console() -> bool:
        return True


@functools.cache
def console_flags() -> int:
    """The creation flags for a console program Docket starts: CREATE_NO_WINDOW when this process has no console
    (the service under pythonw), none when it has one (a command, a test). Asked once per process (DR-0535)."""
    return 0 if _has_console() else CREATE_NO_WINDOW
