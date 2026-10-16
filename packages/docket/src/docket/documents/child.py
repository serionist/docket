"""The extraction child's own process (DR-0485): `python -I -X utf8 -m docket.documents.child --memory <bytes>`.

It sets its memory cap where the system lets a process set its own (Linux; Windows' is the parent's job object), says
`ready`, then takes one JSON line per file on stdin and answers on stdout:

| Line | When |
|---|---|
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
import stat
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO, TextIO, cast

from docket.documents import formats
from docket.documents.limits import LIMITS, Limits, NotReadError, mib

if sys.platform == "linux":  # pragma: no cover - one of the two runs on each system; test_documents_extract runs both
    import resource

    def limit_memory(cap: int) -> None:
        """The child's address space capped: Linux lets a process cap its own."""
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))

else:  # pragma: no cover

    def limit_memory(cap: int) -> None:
        """Nothing here: Windows' cap is the job object the parent puts the child in, and macOS enforces none, so it
        has the time limit and the size caps only."""


def handle(request: dict[str, Any], limits: Limits = LIMITS) -> Iterator[dict[str, Any]]:
    """One file's answer lines."""
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
    limit_memory(args.memory)
    stdout_before = sys.stdout
    sys.stdout = stderr
    try:
        _write(stdout, {"ready": True})
        while line := stdin.readline():
            for answer in handle(json.loads(line)):
                _write(stdout, answer)
    finally:
        sys.stdout = stdout_before
    return 0


if __name__ == "__main__":  # pragma: no cover - the child's process; main() is tested in-process
    sys.exit(main(sys.argv[1:], sys.stdin.buffer, sys.stdout.buffer, sys.stderr))
