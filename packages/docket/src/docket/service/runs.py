"""Open sync runs, held in the service's memory from `run_open` to `ingest` (DR-0240, DR-0243).

A run is opened for a source, bound once by the capture hook to the Claude Code session (and, in a subagent, the agent)
that opened it, and takes that session's captures of the tools its source listed when it opened. Nothing of a capture
goes to disk or to a log: the registry is memory, and a service restart drops every open run. `runs.json` in the state
folder tells the hook which session wants which tools, ids and names only, so on an MCP call no run wants the hook reads
one small file and nothing else.

A run is dropped, its captures freed and the mark kept, when it comes up short, fails its close, sees no capture for 30
minutes, stays open 6 hours, is never bound, or is replaced by a newer run of its source or of its session. A closing
run is never dropped or replaced. Each finished run's outcome stays an hour, so a repeated `ingest` gets the truth.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from docket.errors import DocketError
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
    captures: list[Capture] = field(default_factory=list[Capture])
    tool_uses: dict[str, int] = field(default_factory=dict[str, int])
    size: int = 0
    short: str | None = None
    closing: bool = False
    last: datetime | None = None


@dataclass(frozen=True)
class Outcome:
    at: datetime
    closed: Any = None
    dropped: str | None = None


@dataclass(frozen=True)
class Kept:
    number: int
    used: int
    budget: int


class Runs:
    def __init__(self, state: Path | None, clock: Callable[[], datetime] = now_utc) -> None:
        self.clock = clock
        self.marker = state / MARKER if state is not None else None
        self._lock = threading.RLock()
        self._runs: dict[str, Run] = {}
        self._outcomes: dict[str, Outcome] = {}
        self._mapping: str | None = None
        # A marker a stopped or crashed service left names runs nobody holds.
        if self.marker is not None:
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
                self._drop(old.id, f"replaced by a newer run, {run.id}")
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
                self._drop(other.id, f"replaced by a newer run of its session, {run_id}")
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
            run.last = self.clock()
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

    def finish(self, run_id: str, closed: Any) -> None:
        with self._lock:
            self._runs.pop(run_id, None)
            self._outcomes[run_id] = Outcome(at=self.clock(), closed=closed)
            if self._mapping == run_id:
                self._mapping = None
            self._write_marker()

    def drop(self, run_id: str, why: str) -> None:
        with self._lock:
            self._drop(run_id, why)
            self._write_marker()

    def close_all(self, why: str = "the service stopped") -> None:
        with self._lock:
            for run_id in list(self._runs):
                self._drop(run_id, why)
            self._write_marker()

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

    def _drop(self, run_id: str, why: str) -> None:
        run = self._runs.pop(run_id, None)
        if run is None:
            return
        self._outcomes[run_id] = Outcome(at=self.clock(), dropped=why)
        if self._mapping == run_id:
            self._mapping = None

    def _sweep(self) -> None:
        now = self.clock()
        changed = False
        for run in list(self._runs.values()):
            if run.closing:
                continue
            why = None
            if run.session is None and now - run.opened > BIND_WITHIN:
                why = "the capture hook never tied it to a session; is the docket plugin on in this session?"
            elif now - run.opened > MAX_OPEN:
                why = "it stayed open 6 hours"
            elif run.last is not None and now - run.last > IDLE:
                why = "30 minutes passed with no capture"
            if why is not None:
                self._drop(run.id, why)
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
