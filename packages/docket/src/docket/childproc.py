"""What every child process Docket starts shares: an environment holding only what Python needs to start, and a stop
that takes the helpers it started with it where the system has process groups (DR-0123, DR-0485)."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from collections.abc import Callable

# What Python needs to start on each system, and nothing the service holds in its own environment.
KEEP_ENV = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LANG", "LC_ALL")
GROUPS = hasattr(os, "killpg")


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
