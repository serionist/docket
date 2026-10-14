"""Running a source's mapper (DR-0123): found from vault.yaml, its path checked, run in its own process with limits.

The mapper is the consuming project's code. Its real path must sit under that repo's
`.claude/skills/sources/<source>/`, so a `.py` that arrived as data (a download, a vault file) never runs. Its process
gets Docket's own interpreter in isolated mode, an environment holding only what Python needs to start, a time limit
and an output cap. Its stderr is kept for the user who asks, never logged: a traceback can quote a payload.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any, cast

from docket.errors import DocketError
from docket.vault.git import toplevel
from docket.vault.records import source_name_problem
from docket.vault.vault import Vault

SKILLS = (".claude", "skills", "sources")
TIME_LIMIT = 300.0
OUTPUT_CAP = 256 * 1024 * 1024
STDERR_KEEP = 64 * 1024
# What Python needs to start on each system, and nothing the service holds in its own environment.
KEEP_ENV = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LANG", "LC_ALL")
_CHUNK = 64 * 1024
_GROUPS = hasattr(os, "killpg")


def _no_groups(pid: int, sig: int) -> None:
    """Windows has no process groups: a helper a mapper starts there outlives the run, though the run still fails."""


_KILLPG: Callable[[int, int], None] = getattr(os, "killpg", _no_groups)
_SIGKILL: int = getattr(signal, "SIGKILL", signal.SIGTERM)


class MapperFailedError(DocketError):
    """The mapper didn't give a usable answer. Its stderr rides along for `--mapper-errors`, never in the message."""

    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


def consuming_repo(vault_root: Path) -> Path:
    """The git repo that holds the vault's folder: the project whose skills and mappers the vault runs."""
    repo = toplevel(vault_root.parent)
    if repo is None:
        raise DocketError(
            f"{vault_root} isn't inside a git repo, so it has no consuming project and runs no mapper; keep the vault "
            "in the repo whose .claude/skills/sources/ holds the mappers"
        )
    return repo


def find_mapper(vault: Vault, source: str) -> Path:
    """A source's mapper, its real path checked against the folder built from the checked name."""
    problem = source_name_problem(source)
    if problem is not None:
        raise DocketError(problem)
    config = vault.config.sources.get(source)
    if config is None:
        raise DocketError(f"source {source!r} isn't in vault.yaml; add it with its sensitivity and its mapper")
    if config.mapper is None:
        raise DocketError(
            f"source {source!r} has no mapper in vault.yaml; set sources.{source}.mapper to "
            f".claude/skills/sources/{source}/mapper.py"
        )
    repo = consuming_repo(vault.root)
    inside = vault.root.resolve()
    folder = repo.joinpath(*SKILLS, source).resolve()
    path = (repo / config.mapper).resolve()
    if (
        not folder.is_relative_to(repo)
        or folder.is_relative_to(inside)
        or not path.is_relative_to(folder)
        or path == folder
        or path.is_relative_to(inside)
    ):
        raise DocketError(
            f"{source}'s mapper must be a file under .claude/skills/sources/{source}/ in {repo}, outside the vault, "
            f"after links are followed; {config.mapper} isn't"
        )
    if not path.is_file():
        raise DocketError(f"no mapper at {config.mapper} in {repo}")
    return path


def run(path: Path, payload: dict[str, Any], *, time_limit: float = TIME_LIMIT, output_cap: int = OUTPUT_CAP) -> Any:
    """Start the mapper, hand it the run, and return the JSON it writes. Every way it can fail is a
    MapperFailedError, and then nothing of the run is staged."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    env = {name: os.environ[name] for name in KEEP_ENV if name in os.environ}
    command = [sys.executable, "-I", "-X", "utf8", "-m", "docket.sources.child", str(path)]
    out, err = bytearray(), bytearray()
    over = threading.Event()
    deadline = time.monotonic() + time_limit
    # A process group of its own where the system has them, so a helper the mapper starts is stopped with it.
    proc = subprocess.Popen(  # noqa: S603 - Docket's own interpreter and module; find_mapper checked the path
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=path.parent,
        env=env,
        start_new_session=_GROUPS,
    )

    # Each thread closes its own pipe when it is done with it: a pipe a stray helper still holds is never closed under
    # a thread blocked reading it.
    def feed(pipe: IO[bytes]) -> None:
        # A mapper that stops early closes its end; what it didn't read doesn't matter then.
        with contextlib.suppress(OSError), pipe:
            pipe.write(data)

    def drain(pipe: IO[bytes]) -> None:
        with pipe:
            while chunk := pipe.read(_CHUNK):
                out.extend(chunk)
                if len(out) > output_cap:
                    over.set()
                    _stop(proc)
                    return

    def keep_tail(pipe: IO[bytes]) -> None:
        with pipe:
            while chunk := pipe.read(_CHUNK):
                err.extend(chunk)
                del err[:-STDERR_KEEP]

    # All three are pipes, since PIPE was asked for each.
    pipes = cast(tuple[IO[bytes], IO[bytes], IO[bytes]], (proc.stdin, proc.stdout, proc.stderr))
    threads = [
        threading.Thread(target=target, args=(pipe,), daemon=True)
        for target, pipe in zip((feed, drain, keep_tail), pipes, strict=True)
    ]
    for thread in threads:
        thread.start()
    try:
        code = proc.wait(timeout=time_limit)
    except subprocess.TimeoutExpired:
        _stop(proc)
        proc.wait()
        code = None
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    # The mapper is gone, yet its output is still open: something it started holds it.
    held_open = any(thread.is_alive() for thread in threads)
    if held_open:
        _stop(proc)
    stderr = err.decode("utf-8", "replace")
    if code is None:
        raise MapperFailedError(f"the mapper ran past its time limit of {time_limit:g} s and was stopped", stderr)
    if held_open:
        raise MapperFailedError("the mapper left a process holding its output, so the run was stopped", stderr)
    if over.is_set():
        raise MapperFailedError(f"the mapper's output passed the cap of {output_cap} bytes and it was stopped", stderr)
    if code != 0:
        raise MapperFailedError(f"the mapper failed (exit {code})", stderr)
    try:
        return json.loads(out.decode("utf-8"))
    except ValueError:
        raise MapperFailedError("the mapper's output isn't JSON", stderr) from None


def _stop(proc: subprocess.Popen[bytes]) -> None:
    """Kill the mapper, and on systems with process groups every process it started. One that has already ended, or
    whose group is gone, is left alone."""
    with contextlib.suppress(OSError):
        _KILLPG(proc.pid, _SIGKILL)
        proc.kill()
