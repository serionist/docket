"""`docket sync pull <sync> <source>` (#305): a source's run made by its own `pull.py`, beside its skill, so what the
source returns goes into the run without passing through Claude's context. The agent then classifies from files.

It drives the run through the service's run routes, as the capture hook does (DR-0243), under a session of its own,
`docket-pull-<sync>`, which no Claude Code session has, so the hook never adds to it:

1. Open the run, as `run_open` does; its window, what to skip and what is owed go to the script.
2. Run `pull.py` with Docket's interpreter and the command's own environment, since it starts the source's MCP
   server: one JSON request on stdin, a JSON line per call on stdout (pull contract 1, in the example's `pull.py`).
   Each `capture` line is posted to the run as it comes; a `stop` line, a line that isn't one, an exit that isn't 0
   or the time limit leaves the run unclosed, and it drops with the mark kept.
3. Close it, then the final run when one is due, the same way.
4. The media step, when the run's steps have one: the script's `media` phase gives each recording's audio link,
   never captured, and the links are put four at a time.
5. Each recording that waits in staging to be classified, not held, written to files in the sync folder:
   `recordings/<source>/pending/<key>.notes.md` (the recording and its notes, a paragraph a line) and
   `<key>.transcript.md` (a segment a line with the times a cut takes), each in parts the Read tool takes whole.

What it prints is the run's RUN line and a `classify` line per recording: ids, counts and paths, never a title. The
script's stderr is never read: it can quote a payload.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, cast

from docket.childproc import GROUPS, console_flags, stop
from docket.errors import DocketError
from docket.sync import folder, recordings, slices, start
from docket.vault.anchors import format_time, paragraphs
from docket.vault.gate import Gate
from docket.vault.records import RawRecord
from docket.vault.segments import segments
from docket.vault.vault import Vault

CONTRACT = 1
SESSION = "docket-pull-"
TIME_LIMIT = 1200.0
# A line is one capture: at most what the service keeps of one.
LINE_CAP = 32 * 1024 * 1024
AT_ONCE = 4
PENDING = "pending"
NO_FINAL = "no final run is needed"
# Why a staged record waits that keeps it out of the files: held for its owner, or held back by a problem.
UNSHOWN = ("on-hold", "held-back")
OPEN_SECONDS = 60.0
CAPTURE_SECONDS = 30.0
# A close refused for these stays open and is asked again, after each of these pauses in seconds.
BUSY = ("another run is closing", "call ingest again")
BUSY_WAITS = (10.0, 20.0, 30.0, 30.0, 30.0)
CLOSE_SECONDS = 150.0
MEDIA_SECONDS = 600.0

# A request to the service: its path, its JSON body and how long to wait; the status and the body that came back, or
# None when nothing answered.
Post = Callable[[str, dict[str, Any], float], tuple[int, dict[str, Any]] | None]


class PullStoppedError(DocketError):
    """The pull stopped before the run could close: the run drops, the mark stays."""


@dataclass
class Waiting:
    """A recording that waits to be classified: its records' rids and the files that hold it."""

    rid: str
    records: list[str]
    files: list[Path]


@dataclass
class Pulled:
    source: str
    run: str
    captures: int
    closed: dict[str, Any]
    final: str
    put: int = 0
    refused: list[str] = field(default_factory=list[str])
    waiting: list[Waiting] = field(default_factory=list[Waiting])


def http(token: str) -> Post:
    """Requests to the running service with its token."""
    from docket.service import client  # noqa: PLC0415 - the HTTP client only where a pull runs

    def post(path: str, body: dict[str, Any], timeout: float) -> tuple[int, dict[str, Any]] | None:
        answer = client.call_json("POST", path, body, token=token, timeout=timeout)
        return None if answer is None else (answer.status, answer.body)

    return post


def _asked(post: Post, path: str, body: dict[str, Any], timeout: float, what: str) -> dict[str, Any]:
    return _answered(post(path, body, timeout), what)


def _answered(answer: tuple[int, dict[str, Any]] | None, what: str) -> dict[str, Any]:
    """An answer's body, or why there is none."""
    if answer is None:
        raise DocketError(f"{what}: the service isn't running; `docket service start`, then pull again")
    status, found = answer
    if status != 200:
        raise DocketError(f"{what}: {found.get('error') or found.get('detail') or f'the service answered {status}'}")
    return found


def _listed(value: Any) -> list[Any]:
    return cast(list[Any], value) if isinstance(value, list) else []


@dataclass(frozen=True)
class _Job:
    """One pull: the service, the script, the source, the session its runs are under, and the server command."""

    post: Post
    script: Path
    source: str
    session: str
    server: list[str] | None
    pause: Callable[[float], None] = time.sleep

    def request(self, opened: dict[str, Any], phase: str) -> dict[str, Any]:
        owed = [str(d["id"]) for d in [*_listed(opened.get("waiting")), *_listed(opened.get("retry"))]]
        window = {"since": opened["since"], "until": opened["until"]} if opened.get("since") else None
        return {"contract": CONTRACT, "phase": phase, "window": window, "kept": opened.get("kept") or [],
                "held": opened.get("held") or [], "ids": owed, "server": self.server}  # fmt: skip

    def drive(self, request: dict[str, Any], run: str) -> tuple[int, set[str], list[dict[str, Any]]]:
        """The script's calls posted to the run as they come: how many, the ids fetched, the ids skipped."""
        count = 0
        fetched: set[str] = set()
        skipped: list[dict[str, Any]] = []
        for line in child(self.script, request):
            if "capture" in line:
                given: Any = line["capture"]
                made = cast(dict[str, Any], given) if isinstance(given, dict) else {}
                if not isinstance(made.get("tool"), str):
                    raise PullStoppedError("the pull wrote a capture line without its tool")
                arguments: Any = made.get("arguments")
                count += 1
                body: dict[str, Any] = {"session": self.session, "agent": None, "tool_name": made["tool"],
                        "tool_use_id": f"{run}-{count}", "arguments": arguments if isinstance(arguments, dict) else {},
                        "result": made.get("result"), "captured": datetime.now(UTC).isoformat()}  # fmt: skip
                _asked(self.post, f"/api/runs/{run}/captures", body, CAPTURE_SECONDS,
                       f"capture {count} of run {run} wasn't kept")  # fmt: skip
            elif isinstance(line.get("skip"), dict):
                skip = cast(dict[str, Any], line["skip"])
                skipped.append({"id": skip.get("id"), "reason": str(skip.get("reason") or "later")})
            elif isinstance(line.get("fetched"), str):
                fetched.add(line["fetched"])
            elif "stop" in line:
                raise PullStoppedError(f"the pull stopped: {line['stop']}; the run isn't closed and the mark stays")
        return count, fetched, skipped

    def run(self, *, final: bool) -> tuple[dict[str, Any], int, set[str], dict[str, Any]] | None:
        """A run opened, driven and closed: what opened it, its captures, the ids it fetched, what its close said.
        None for a final run when none is due."""
        body: dict[str, Any] = {"source": self.source, "session": self.session} | ({"final": True} if final else {})
        answer = self.post("/api/runs", body, OPEN_SECONDS)
        if final and answer is not None and answer[0] != 200 and NO_FINAL in str(answer[1].get("error") or ""):
            return None
        opened = _answered(answer, f"the {'final ' if final else ''}run didn't open")
        run = str(opened["run"])
        count, fetched, skipped = self.drive(self.request(opened, "final" if final else "run"), run)
        close = {"captures": count, "skipped": skipped} | ({"final": True} if final else {})
        return opened, count, fetched, self.close(run, close)

    def close(self, run: str, body: dict[str, Any]) -> dict[str, Any]:
        """The run's close, asked again while another run's close, or another process's hold on the vault, keeps it
        open: the sync's agents close side by side, and both refusals leave the run open (DR-0796)."""
        for wait in (*BUSY_WAITS, None):
            answer = self.post(f"/api/runs/{run}/close", body, CLOSE_SECONDS)
            busy = answer is not None and answer[0] != 200 and any(
                said in str(answer[1].get("error") or "") for said in BUSY)  # fmt: skip
            if not busy or wait is None:
                return _answered(answer, f"run {run} didn't close")
            self.pause(wait)
        raise AssertionError  # pragma: no cover - the loop returns on its last try

    def media(self, ids: list[str], pulled: Pulled) -> None:
        """Each recording's audio, its links from the script's media phase, put four at a time."""
        if not ids:
            return
        request = {"contract": CONTRACT, "phase": "media", "window": None, "kept": [], "held": [], "ids": ids,
                   "server": self.server}  # fmt: skip
        links: list[tuple[str, str]] = []
        for line in child(self.script, request):
            media = line.get("media")
            if isinstance(media, dict):
                media = cast(dict[str, Any], media)
                if isinstance(media.get("id"), str) and isinstance(media.get("url"), str):
                    links.append((media["id"], media["url"]))

        def put(link: tuple[str, str]) -> str | None:
            rid = f"{self.source}:{link[0]}"
            answer = self.post("/api/media", {"rid": rid, "url": link[1]}, MEDIA_SECONDS)
            if answer is not None and answer[0] == 200:
                return None
            return f"{rid}: {'the service is gone' if answer is None else answer[1].get('error') or answer[0]}"

        with ThreadPoolExecutor(max_workers=AT_ONCE) as pool:
            for refused in pool.map(put, links):
                if refused is None:
                    pulled.put += 1
                else:
                    pulled.refused.append(refused)


def pull(
    vault: Vault,
    sync: str,
    source: str,
    *,
    post: Post,
    server: Sequence[str] | None = None,
    pause: Callable[[float], None] = time.sleep,
) -> Pulled:
    """The source's run, its final run when one is due, its audio and its files to classify, by its `pull.py`."""
    path, info = folder.find(sync, vault.root)
    if source not in {s.source for s in info.sources}:
        named = ", ".join(s.source for s in info.sources)
        raise DocketError(f"{source} isn't a source of {info.sync}; its sources: {named}")
    script = start.pull_script(vault, source)
    if script is None:
        raise DocketError(f"{source} has no pull.py beside its skill: its agent runs it through run_open")
    job = _Job(post, script, source, SESSION + info.sync, list(server) if server else None, pause)
    first = job.run(final=False)
    if first is None:  # pragma: no cover - only a final run is ever none
        raise DocketError("the run didn't open")
    opened, count, fetched, closed = first
    pulled = Pulled(source, str(opened["run"]), count, closed, final="none due")
    final = job.run(final=True)
    if final is not None:
        pulled.final = f"{final[0]['run']}, {final[1]} captures"
        fetched |= final[2]
    if "media_put" in str(opened.get("steps", "")):
        job.media(sorted(fetched | {str(i) for i in _listed(opened.get("media"))}), pulled)
    pulled.waiting = _waiting(vault, path, source)
    return pulled


def child(script: Path, request: dict[str, Any], time_limit: float = TIME_LIMIT) -> Iterator[dict[str, Any]]:
    """The script's JSON lines as they come. It runs with Docket's interpreter in isolated mode from its own folder,
    with the command's environment, since it starts the source's own server; its stderr is dropped. Refused: a line
    that isn't a JSON object or is past the cap, an exit that isn't 0, and the time limit."""
    proc = subprocess.Popen(  # noqa: S603 - Docket's own interpreter; pull_script found the file in the skill folder
        [sys.executable, "-I", "-X", "utf8", str(script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=script.parent,
        env=dict(os.environ),
        start_new_session=GROUPS,
        creationflags=console_flags(),
    )
    late = threading.Event()

    def overtime() -> None:
        late.set()
        stop(proc)

    timer = threading.Timer(time_limit, overtime)
    timer.start()
    stdin, stdout = cast(IO[bytes], proc.stdin), cast(IO[bytes], proc.stdout)
    try:
        # A script that ends before it reads, as one that doesn't import, says so by its exit, below.
        with contextlib.suppress(BrokenPipeError), stdin:
            stdin.write(json.dumps(request).encode("utf-8"))
        while raw := stdout.readline(LINE_CAP + 1):
            if len(raw) > LINE_CAP:
                raise PullStoppedError(f"a line of the pull passed {LINE_CAP // (1024 * 1024)} MiB")
            try:
                found: Any = json.loads(raw)
            except ValueError:
                raise PullStoppedError("the pull wrote a line that isn't JSON") from None
            if not isinstance(found, dict):
                raise PullStoppedError("the pull wrote a line that isn't a JSON object")
            yield cast(dict[str, Any], found)
    finally:
        timer.cancel()
        if proc.poll() is None:
            stop(proc)
        stdout.close()
        code = proc.wait()
    if late.is_set():
        raise PullStoppedError(f"the pull ran past {time_limit:g} s and was stopped")
    if code != 0:
        raise PullStoppedError(f"the pull failed (exit {code})")


# -- the files to classify ---------------------------------------------------------------------------------------------


def _waiting(vault: Vault, path: Path, source: str) -> list[Waiting]:
    """Each recording of the source that waits to be classified, not held, as files: its notes, then its transcript."""
    listing = Gate(vault).list(source, "pending")
    groups: dict[str, list[RawRecord]] = {}
    for entry in listing.entries:
        if listing.why.get(entry.key) in UNSHOWN:
            continue
        record = entry.staged.record
        groups.setdefault(record.container or record.rid, []).append(record)
    where = path / folder.RECORDINGS / source / PENDING
    return [waiting(where, rid, records) for rid, records in sorted(groups.items())]


def waiting(where: Path, rid: str, records: Sequence[RawRecord]) -> Waiting:
    """A recording's files to classify from: its notes, a paragraph a line, then its transcript, a segment a line."""
    k = recordings.key(rid)
    said = [r for r in records if not segments(r)]
    spoken = [r for r in records if segments(r)]
    files: list[Path] = []
    if said:
        lines = [line for r in said for line in _paragraphs(r)]
        files += slices.write(where, f"{k}.notes", f"waits to be classified: {rid}, its notes", lines)
    if spoken:
        lines = [line for r in spoken for line in _segments(r)]
        files += slices.write(where, f"{k}.transcript", f"waits to be classified: {rid}, its transcript", lines)
    return Waiting(rid, sorted(r.rid for r in records), files)


def _paragraphs(record: RawRecord) -> list[str]:
    head = f"== {record.rid} | {record.kind}" + (f" | {' '.join(record.title.split())}" if record.title else "")
    body = [f"#{n} {' '.join(p.split())}" for n, p in enumerate(paragraphs(record.text), start=1)]
    return [head, *(part for line in body for part in recordings.wrapped(line))]


def _segments(record: RawRecord) -> list[str]:
    head = f"== {record.rid} | {record.kind} | a segment a line; a cut is the times at its start, mm:ss-mm:ss"
    body = [
        f"@{format_time(s.start_ms // 1000)}-{format_time(-(-s.end_ms // 1000))} "
        f"{f'{s.speaker}: ' if s.speaker else ''}{' '.join(s.text.split())}"
        for s in segments(record)
    ]
    return [head, *(part for line in body for part in recordings.wrapped(line))]


def lines(pulled: Pulled) -> list[str]:
    """What `docket sync pull` prints: the RUN line the agent's block takes, then a `classify` line per recording."""
    closed = pulled.closed
    moved = "yes" if closed.get("moved") else "no"
    note = f"pulled; final run {pulled.final}; audio {pulled.put} put, {len(pulled.refused)} refused"
    out = [
        f"RUN:      {pulled.source} | {pulled.run} | captures {pulled.captures} | ready {closed.get('ready', 0)}, "
        f"pending {closed.get('pending', 0)} | skipped {closed.get('skipped', 0)} | mark moved {moved} | {note}"
    ]
    out += [f"refused  {why}" for why in pulled.refused]
    for w in pulled.waiting:
        out.append(f"classify {w.rid}: {', '.join(w.records)} | " + ", ".join(str(f) for f in w.files))
    if not pulled.waiting:
        out.append("nothing waits to be classified")
    return out
