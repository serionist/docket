"""Open sync runs, held in the service's memory from `run_open` to `ingest` (DR-0240, DR-0243).

A run is opened for a source, bound once by the capture hook to the Claude Code session (and, in a subagent, the agent)
that opened it, and takes that session's captures of the tools its source listed when it opened. Nothing of a capture
goes to disk or to a log: the registry is memory, and a service restart drops every open run. `runs.json` in the state
folder tells the hook which session wants which tools, ids and names only, so on an MCP call no run wants the hook reads
one small file and nothing else.

A run is dropped, its captures freed and the mark kept, when it comes up short, fails its close, sees no capture for 30
minutes, stays open 6 hours, is never bound, or is replaced by a newer run of its source or of its session. A closing
run is never dropped or replaced. Each finished run's outcome stays an hour, so a repeated `ingest` gets the truth.

Every end names its kind (DR-0302), and its line, ids and counts only, goes to the run history (DR-0301).
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from docket.errors import DocketError
from docket.service import history
from docket.service.history import Closing, Counts, RunLine
from docket.sources.contract import Capture
from docket.vault import cursors
from docket.vault.staging import retry

MARKER = "runs.json"
BIND_WITHIN = timedelta(seconds=60)
IDLE = timedelta(minutes=30)
MAX_OPEN = timedelta(hours=6)
KEEP_OUTCOME = timedelta(hours=1)
CAPTURE_CAP = 32 * 1024 * 1024
TOTAL_CAP = 256 * 1024 * 1024
# What a run may capture before it fetches no new item: about a tenth of Claude's context, so a sync never fills it.
BUDGET = 400 * 1024
RUN_ID = re.compile(r"^run-(\d{8}T\d{6})-[0-9a-f]{16}$")


class RunError(DocketError):
    """A run can't do what was asked: it is gone, closing, or someone else's."""


class BusyError(RunError):
    """Another run is closing; this one stays open."""


def now_utc() -> datetime:
    return datetime.now(UTC)


@dataclass
class Run:
    id: str
    source: str
    tools: frozenset[str]
    opened: datetime
    final: bool
    window: cursors.Window | None
    # The native ids raw and staging held in the window at open: the close's "told to skip" (DR-0247).
    kept: frozenset[str]
    handed: cursors.Handover
    held: frozenset[str]
    session: str | None = None
    agent: str | None = None
    # Opened with `since`, for a backfill's listing.
    backfill: bool = False
    captures: list[Capture] = field(default_factory=list[Capture])
    tool_uses: dict[str, int] = field(default_factory=dict[str, int])
    size: int = 0
    short: str | None = None
    closing: bool = False
    last: datetime | None = None
    last_capture: datetime | None = None
    # What the close's manifest skipped, by reason, once a close has begun.
    skips: dict[str, int] = field(default_factory=dict[str, int])


@dataclass(frozen=True)
class Outcome:
    at: datetime
    closed: Any = None
    dropped: str | None = None
    kind: str = history.CLOSED


@dataclass(frozen=True)
class OpenRun:
    """An open run as a status page shows it: ids, counts and times, never content, no session or agent."""

    id: str
    source: str
    final: bool
    backfill: bool
    bound: bool
    closing: bool
    captures: int
    bytes: int
    budget: int
    opened: datetime
    last: datetime | None
    # When it drops with nothing more: the bind's minute, 30 minutes idle or 6 hours open; None while it closes.
    drops_at: datetime | None
    short: str | None
    since: datetime | None
    until: datetime | None
    told_to_skip: int
    held: int
    handed: Counts


@dataclass(frozen=True)
class Kept:
    number: int
    used: int
    budget: int


def _opened_by_id(run_id: str) -> datetime | None:
    match = RUN_ID.match(run_id)
    return datetime.strptime(match[1], "%Y%m%dT%H%M%S").replace(tzinfo=UTC) if match else None


def _left(marker: Path) -> list[tuple[str, str]]:
    """The runs a marker names, as (run, source): ids only, and nothing at all from one that doesn't read."""
    try:
        data: Any = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    runs: Any = cast(dict[str, Any], data).get("runs") if isinstance(data, dict) else None
    if not isinstance(runs, list):
        return []
    left: list[tuple[str, str]] = []
    for entry in cast(list[Any], runs):
        if not isinstance(entry, dict):
            continue
        run, source = cast(dict[str, Any], entry).get("run"), cast(dict[str, Any], entry).get("source")
        if isinstance(run, str) and isinstance(source, str):
            left.append((run, source))
    return left


class Runs:
    def __init__(
        self,
        state: Path | None,
        clock: Callable[[], datetime] = now_utc,
        ended: Callable[[RunLine], None] | None = None,
    ) -> None:
        self.clock = clock
        self.marker = state / MARKER if state is not None else None
        self._ended = ended
        self._lock = threading.RLock()
        self._runs: dict[str, Run] = {}
        self._outcomes: dict[str, Outcome] = {}
        self._mapping: str | None = None
        # A marker a stopped or crashed service left names runs nobody holds: each is recorded as stopped, then it goes.
        if self.marker is not None:
            now = self.clock()
            for run_id, source in _left(self.marker):
                self._record(
                    RunLine(
                        run=run_id,
                        source=source,
                        opened=_opened_by_id(run_id) or now,
                        ended=now,
                        outcome="dropped",
                        kind=history.STOPPED,
                        why="the service stopped before the run closed; found at the next start",
                    )
                )
            retry(lambda: self.marker.unlink(missing_ok=True) if self.marker else None)

    # -- opening and binding -----------------------------------------------------------------------------------------

    def open(self, run: Run) -> None:
        """Hold a new run. An open run of the same source is replaced, and so is one of the same session and agent
        when the run comes bound; a closing one refuses the new run instead."""
        with self._lock:
            self._sweep()
            now = self.clock()
            run.last = now
            same = [r for r in self._runs.values() if r.source == run.source]
            if run.session is not None:
                same += [r for r in self._runs.values() if (r.session, r.agent) == (run.session, run.agent)]
            if any(r.closing for r in same):
                raise RunError(f"a run of {run.source} is closing; open the new one in a minute")
            for old in same:
                self._drop(old.id, f"replaced by a newer run, {run.id}", history.REPLACED)
            self._runs[run.id] = run
            self._write_marker()

    def bind(self, run_id: str, session: str, agent: str | None) -> Run:
        """Tie a run to the session that opened it: once, within a minute of its opening."""
        with self._lock:
            self._sweep()
            run = self._get(run_id)
            # A run past its minute unbound is gone already: the sweep above dropped it.
            if run.session is not None:
                raise RunError(f"run {run_id} is already tied to a session")
            for other in [r for r in self._runs.values() if (r.session, r.agent) == (session, agent)]:
                if other.closing:
                    raise RunError("this session's last run is still closing; open the new one in a minute")
                self._drop(other.id, f"replaced by a newer run of its session, {run_id}", history.REPLACED)
            run.session, run.agent = session, agent
            self._write_marker()
            return run

    # -- captures --------------------------------------------------------------------------------------------------

    def capture(
        self,
        run_id: str,
        *,
        session: str,
        agent: str | None,
        tool: str,
        tool_use_id: str,
        arguments: dict[str, Any],
        result: Any,
        captured: datetime,
        size: int,
    ) -> Kept:
        """Keep one capture of a run's session, numbered in the order it came. A repeated `tool_use_id` is the same
        capture, kept once. One the run's own session sends that can't be kept marks the run short."""
        with self._lock:
            self._sweep()
            run = self._get(run_id)
            if run.closing:
                raise RunError(f"run {run_id} is closing")
            if (run.session, run.agent) != (session, agent):
                raise RunError(f"run {run_id} belongs to another session")
            if tool_use_id in run.tool_uses:
                return Kept(run.tool_uses[tool_use_id], run.size, BUDGET)
            if tool not in run.tools:
                raise self._short(run, f"{tool} isn't one of {run.source}'s tools")
            if size > CAPTURE_CAP:
                raise self._short(run, f"a capture is at most {CAPTURE_CAP // (1024 * 1024)} MiB")
            if self._held_bytes() + size > TOTAL_CAP:
                raise self._short(run, f"the open runs hold {TOTAL_CAP // (1024 * 1024)} MiB already")
            run.captures.append(
                Capture(
                    seq=len(run.captures), run=run.id, captured=captured, tool=tool, arguments=arguments, result=result
                )
            )
            run.tool_uses[tool_use_id] = len(run.captures)
            run.size += size
            run.last = run.last_capture = self.clock()
            return Kept(len(run.captures), run.size, BUDGET)

    def mark_short(self, run_id: str, why: str) -> None:
        """A capture that couldn't even be read, such as a body past the cap: the run comes up short."""
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None and run.short is None:
                run.short = why

    def _short(self, run: Run, why: str) -> RunError:
        if run.short is None:
            run.short = why
        return RunError(f"{why}; the run is short now and its close will drop it")

    def _held_bytes(self) -> int:
        return sum(r.size for r in self._runs.values())

    # -- closing ---------------------------------------------------------------------------------------------------

    def outcome(self, run_id: str) -> Outcome | None:
        with self._lock:
            self._sweep()
            return self._outcomes.get(run_id)

    def begin_close(self, run_id: str) -> Run:
        """Take a run for its close: one close maps at a time, and a second is refused with its run left open."""
        with self._lock:
            self._sweep()
            run = self._get(run_id)
            if run.closing:
                raise RunError(f"run {run_id} is closing already")
            if self._mapping is not None:
                run.last = self.clock()
                raise BusyError("another run is closing; its run stays open: call ingest again in a minute")
            run.closing = True
            self._mapping = run_id
            return run

    def reopen(self, run_id: str) -> None:
        """Back to open, for a close that wrote nothing: a lock another Docket held, a bad parameter."""
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None:
                run.closing = False
                run.last = self.clock()
            if self._mapping == run_id:
                self._mapping = None

    def finish(self, run_id: str, closed: Any, closing: Closing | None = None) -> None:
        with self._lock:
            run = self._runs.pop(run_id, None)
            now = self.clock()
            self._outcomes[run_id] = Outcome(at=now, closed=closed)
            if self._mapping == run_id:
                self._mapping = None
            if run is not None:
                self._record(self._line(run, now, history.CLOSED, None, closing))
            self._write_marker()

    def drop(self, run_id: str, why: str, kind: str) -> None:
        with self._lock:
            self._drop(run_id, why, kind)
            self._write_marker()

    def close_all(self, why: str = "the service stopped") -> None:
        with self._lock:
            for run_id in list(self._runs):
                self._drop(run_id, why, history.STOPPED)
            self._write_marker()

    def snapshot(self) -> list[OpenRun]:
        """Open runs for the Sync page, oldest first."""
        with self._lock:
            self._sweep()
            return [self._open_run(r) for r in sorted(self._runs.values(), key=lambda r: (r.opened, r.id))]

    @staticmethod
    def _open_run(run: Run) -> OpenRun:
        if run.closing:
            drops_at = None
        elif run.session is None:
            drops_at = run.opened + BIND_WITHIN
        else:
            drops_at = min(run.opened + MAX_OPEN, (run.last or run.opened) + IDLE)
        return OpenRun(
            id=run.id,
            source=run.source,
            final=run.final,
            backfill=run.backfill,
            bound=run.session is not None,
            closing=run.closing,
            captures=len(run.captures),
            bytes=run.size,
            budget=BUDGET,
            opened=run.opened,
            last=run.last_capture,
            drops_at=drops_at,
            short=run.short,
            since=run.window.since if run.window else None,
            until=run.window.until if run.window else None,
            told_to_skip=len(run.kept),
            held=len(run.held),
            handed=Counts(retry=len(run.handed.retry), waiting=len(run.handed.waiting)),
        )

    def listing(self) -> list[dict[str, Any]]:
        """Open runs for a status page: ids, counts and times, never content."""
        with self._lock:
            self._sweep()
            return [
                {
                    "run": r.id,
                    "source": r.source,
                    "bound": r.session is not None,
                    "final": r.final,
                    "closing": r.closing,
                    "captures": len(r.captures),
                    "bytes": r.size,
                    "opened": r.opened.isoformat(),
                }
                for r in self._runs.values()
            ]

    # -- inside ----------------------------------------------------------------------------------------------------

    def _get(self, run_id: str) -> Run:
        run = self._runs.get(run_id)
        if run is not None:
            return run
        gone = self._outcomes.get(run_id)
        if gone is not None and gone.dropped is not None:
            raise RunError(f"run {run_id} was dropped: {gone.dropped}; nothing staged, the mark stays")
        raise RunError(
            f"no open run {run_id}: the service restarted or the run was dropped; nothing staged, the mark stays"
        )

    def _drop(self, run_id: str, why: str, kind: str, ended: datetime | None = None) -> None:
        run = self._runs.pop(run_id, None)
        if run is None:
            return
        now = self.clock()
        self._outcomes[run_id] = Outcome(at=now, dropped=why, kind=kind)
        if self._mapping == run_id:
            self._mapping = None
        self._record(self._line(run, ended or now, kind, why, None))

    @staticmethod
    def _line(run: Run, ended: datetime, kind: str, why: str | None, closing: Closing | None) -> RunLine:
        """A run's line for the history: ids, counts and times, never a capture, a session or an agent."""
        fields: dict[str, Any] = {
            "run": run.id,
            "source": run.source,
            "final": run.final,
            "backfill": run.backfill,
            "opened": run.opened,
            "ended": ended,
            "since": run.window.since if run.window else None,
            "until": run.window.until if run.window else None,
            "outcome": "closed" if kind == history.CLOSED else "dropped",
            "kind": kind,
            "why": history.capped_why(why),
            "captures": len(run.captures),
            "bytes": run.size,
            "told_to_skip": len(run.kept),
            "held": len(run.held),
            "handed": Counts(retry=len(run.handed.retry), waiting=len(run.handed.waiting)),
            "skipped": dict(run.skips),
        }
        if closing is not None:
            fields |= {
                "staged": closing.staged,
                "dropped": dict(closing.dropped),
                "set_aside": closing.set_aside[: history.IDS],
                "set_aside_count": len(closing.set_aside),
                "waiting": history.first_ids(closing.waiting),
                "waiting_count": len(closing.waiting),
                "mark": closing.mark,
                "cursor_problem": history.capped_why(closing.cursor_problem),
                "owed": closing.owed,
                "expired": history.first_ids(closing.expired),
                "expired_count": len(closing.expired),
                "held_in_raw": history.first_ids(closing.held_in_raw),
                "held_in_raw_count": len(closing.held_in_raw),
            }
        return RunLine(**fields)

    def _record(self, line: RunLine) -> None:
        if self._ended is not None:
            self._ended(line)

    def _sweep(self) -> None:
        now = self.clock()
        changed = False
        for run in list(self._runs.values()):
            if run.closing:
                continue
            # A run dropped by a limit ended when the limit passed, not when this sweep saw it.
            if run.session is None and now - run.opened > BIND_WITHIN:
                why = "the capture hook never tied it to a session; is the docket plugin on in this session?"
                kind, ended = history.UNBOUND, run.opened + BIND_WITHIN
            elif now - run.opened > MAX_OPEN:
                why, kind, ended = "it stayed open 6 hours", history.TOO_LONG, run.opened + MAX_OPEN
            elif run.last is not None and now - run.last > IDLE:
                why, kind, ended = "30 minutes passed with no capture", history.IDLE, run.last + IDLE
            else:
                continue
            self._drop(run.id, why, kind, ended)
            changed = True
        for run_id in [i for i, o in self._outcomes.items() if now - o.at > KEEP_OUTCOME]:
            del self._outcomes[run_id]
        if changed:
            self._write_marker()

    def _write_marker(self) -> None:
        marker = self.marker
        if marker is None:
            return
        runs = [
            {"run": r.id, "source": r.source, "session": r.session, "agent": r.agent, "tools": sorted(r.tools)}
            for r in self._runs.values()
        ]
        if not runs:
            retry(lambda: marker.unlink(missing_ok=True))
            return
        marker.parent.mkdir(parents=True, exist_ok=True)
        tmp = marker.with_name(f".{marker.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"runs": runs}), encoding="utf-8")
        retry(lambda: tmp.replace(marker))
