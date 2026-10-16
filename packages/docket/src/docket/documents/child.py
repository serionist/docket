"""The extraction child's own process (DR-0485): `python -I -X utf8 -m docket.documents.child --memory <bytes>`.

It caps its own memory first, where the system enforces a cap (Linux's `RLIMIT_AS`, a job object of its own on
Windows), and says `ready` with its own pid and the cap it got, so the parent can refuse a child that runs without one.
A cap the system refuses ends it before `ready`. Then it takes one JSON line per file on stdin and answers on stdout:

| Line | When |
|---|---|
| `{"ready": true, "pid": <n>, "memory": <bytes or null>}` | once, at the start |
| `{"part": {...}}` | one per page, slide or sheet, in order |
| `{"done": {...}}` | the file read whole: its bytes' sha256 and size, format, engine, text, people, note |
| `{"not_read": {...}}` | past a cap, damaged or protected: a reason, Docket's own words, the sha256 and size |
| `{"unreadable": "<the error's type>"}` | the system won't let it read the file now |

It reads a file once, and hashes and extracts from those same bytes. Anything a library prints goes to stderr, so it
can't break the lines; a memory error ends the process, which the parent counts as a crash.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO, TextIO, cast

from docket.documents.limits import LIMITS, Limits, NotReadError, mib

if sys.platform == "linux":  # pragma: no cover - one of the three runs on each system; test_documents_extract runs them
    import resource

    def limit_memory(cap: int) -> int | None:
        """The child's address space capped, as Linux lets a process cap its own; the cap it got."""
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
        return resource.getrlimit(resource.RLIMIT_AS)[0]

elif sys.platform == "win32":  # pragma: no cover
    from docket.documents import winjob

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


def keep_out(modules: dict[str, Any] | None = None) -> None:
    """Make `import numpy` and `import lxml` fail in this process, before openpyxl is imported. openpyxl imports numpy
    when it is installed, and numpy's BLAS reserves memory for every core as it loads, past the cap on a machine with
    many; with lxml it would parse through lxml, past the defusedxml parse `formats.harden_openpyxl` gives it. Only the
    child's own process calls this: in another, their users would break."""
    target = cast(dict[str, Any], sys.modules) if modules is None else modules
    target["numpy"] = None
    target["lxml"] = None


def handle(request: dict[str, Any], limits: Limits = LIMITS) -> Iterator[dict[str, Any]]:
    """One file's answer lines."""
    # Only now, once main has set the cap and kept numpy and lxml out: the readers bring openpyxl, which looks for both.
    from docket.documents import formats  # noqa: PLC0415

    path = Path(str(request["path"]))
    fmt = cast(formats.Format, request["format"])
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            yield {"unreadable": "not a regular file"}
            return
        if info.st_size > limits.file_bytes:
            detail = f"{mib(info.st_size)}, over the cap of {mib(limits.file_bytes)}"
            yield {"not_read": {"reason": "size", "detail": detail, "sha256": None, "size": info.st_size}}
            return
        with path.open("rb") as f:
            data = f.read(limits.file_bytes + 1)
    except OSError as e:
        yield {"unreadable": type(e).__name__}
        return
    sha = hashlib.sha256(data).hexdigest()
    try:
        extracted = formats.extract(data, fmt, limits)
    except NotReadError as e:
        yield {"not_read": {"reason": e.reason, "detail": e.detail, "sha256": sha, "size": len(data)}}
        return
    for part in extracted.parts:
        yield {"part": dataclasses.asdict(part)}
    facts = {k: v for k, v in dataclasses.asdict(extracted).items() if k != "parts"}
    yield {"done": {**facts, "sha256": sha, "size": len(data)}}


def _write(out: BinaryIO, line: dict[str, Any]) -> None:
    out.write(json.dumps(line, ensure_ascii=False).encode("utf-8") + b"\n")
    out.flush()


def main(argv: list[str], stdin: BinaryIO, stdout: BinaryIO, stderr: TextIO) -> int:
    parser = argparse.ArgumentParser(prog="docket.documents.child")
    parser.add_argument("--memory", type=int, required=True)
    args = parser.parse_args(argv)
    # Before anything is read; a cap the system refuses raises here, and the child never says ready.
    memory = limit_memory(args.memory)
    stdout_before = sys.stdout
    sys.stdout = stderr
    try:
        _write(stdout, {"ready": True, "pid": os.getpid(), "memory": memory})
        while line := stdin.readline():
            for answer in handle(json.loads(line)):
                _write(stdout, answer)
    finally:
        sys.stdout = stdout_before
    return 0


if __name__ == "__main__":  # pragma: no cover - the child's process; main() is tested in-process
    keep_out()
    sys.exit(main(sys.argv[1:], sys.stdin.buffer, sys.stdout.buffer, sys.stderr))
