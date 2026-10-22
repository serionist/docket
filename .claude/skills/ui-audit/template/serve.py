"""A throwaway Docket service over an invented world, for the ui-audit runner and check-ui's live lenses.

    uv run python serve.py <world> [--record FILE] [--link PAGE] [--stdin] [--minutes N]
    uv run python serve.py link <record> <page>

It runs devkit ui's own service (`devkit.ui.service`): the world built by `packages/docket/tests/ui_world.py`, then
`docket serve` on port 0 with config, state and models folders of its own, a file token and the failing keyring, so it
never reaches port 8741, the owner's vault, folders or keychain. It asks the service's `/api/status` with that token
and writes the answer to the record (default `state/service.json`): port, localhost origin, world, vault, pid,
machine and the token, which lib/docket.mjs checks before any step. It holds the service until stdin closes
(`--stdin`, the runner's way), the file `<record>.stop` appears, or the minutes run out; then it stops the service and
removes its folders. `link` prints a new one-time sign-in link for a running service, good for 60 seconds.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
import urllib.request
from collections.abc import Sequence
from pathlib import Path

from devkit.ui import service, sign_in_link


def repo_root(start: Path) -> Path:
    """The checkout this file sits in, as the nearest folder holding `.git` (a folder, or a file in a worktree)."""
    for folder in (start, *start.parents):
        if (folder / ".git").exists():
            return folder
    raise SystemExit(f"no checkout above {start}")


def status(port: int, token: str) -> dict[str, object]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/status", headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(request, timeout=60) as answer:  # noqa: S310 - the throwaway service on this machine
        return json.loads(answer.read())


def write_record(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def hold(record: Path, *, stdin: bool, minutes: float) -> None:
    """Until stdin closes (when asked to watch it), the stop file appears, or the time runs out."""
    done = threading.Event()
    if stdin:

        def drain() -> None:
            for _ in sys.stdin:
                pass
            done.set()

        threading.Thread(target=drain, daemon=True).start()
    stop = record.with_name(f"{record.name}.stop")
    deadline = time.monotonic() + minutes * 60
    while not done.is_set() and not stop.exists() and time.monotonic() < deadline:
        done.wait(1)


def serve(world: str, record: Path, *, link: str | None, stdin: bool, minutes: float) -> int:
    root = repo_root(Path(__file__).resolve().parent)
    with service(root, world) as (port, token):
        said = status(port, token)
        write_record(
            record,
            {
                "port": port,
                "origin": f"http://localhost:{port}",
                "world": world,
                "vault": said["vault"],
                "pid": said["pid"],
                "machine": said["machine"],
                "started": said["started"],
                "holder": os.getpid(),
                "token": token,
            },
        )
        print(f"serving the {world} world at http://localhost:{port} (record {record})", flush=True)
        if link:
            print(f"sign in once, within 60 s: {sign_in_link(port, token, link)}", flush=True)
        try:
            hold(record, stdin=stdin, minutes=minutes)
        finally:
            record.unlink(missing_ok=True)
            record.with_name(f"{record.name}.stop").unlink(missing_ok=True)
    print("stopped, and its folders removed", flush=True)
    return 0


def main(argv: Sequence[str]) -> int:
    # A stop by signal unwinds like any other, so devkit ui's service stops `docket serve` and removes its folders.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    if argv[:1] == ["link"]:
        if len(argv) != 3:
            raise SystemExit("usage: serve.py link <record> <page>")
        found = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        print(sign_in_link(int(found["port"]), str(found["token"]), argv[2]))
        return 0
    parser = argparse.ArgumentParser(prog="serve.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("world", help="a world of packages/docket/tests/ui_world.py, such as empty, week or staging")
    parser.add_argument("--record", type=Path, default=Path("state") / "service.json")
    parser.add_argument("--link", help="print a one-time sign-in link that lands on this page, such as /queue")
    parser.add_argument("--stdin", action="store_true", help="stop when stdin closes (the runner's way)")
    parser.add_argument("--minutes", type=float, default=30, help="stop after this long (default 30)")
    args = parser.parse_args(argv)
    return serve(args.world, args.record.resolve(), link=args.link, stdin=args.stdin, minutes=args.minutes)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
