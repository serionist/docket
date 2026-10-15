"""Live sync runs: `run_open` and `ingest`, which the MCP tools and the REST routes share (DR-0240 to DR-0252).

`run_open` checks the source, works out the run's window from the cursors, what to skip and what is owed, holds the run
in memory and answers with the steps every source follows. The capture hook binds the run and posts its captures.
`ingest` closes it: the count checked, the source's mapper run off the writer thread for at most 90 seconds, the
records through the core's checks, holds and the gate on the writer, then this machine's cursor moved and committed.
What either says is ids, counts and times, never a title.
"""

from __future__ import annotations

import asyncio
import secrets
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from docket import index
from docket.errors import DocketError
from docket.service import history
from docket.service.runs import BUDGET, Run
from docket.service.state import Service
from docket.sources import ingest as ingesting
from docket.sources import local, runner
from docket.sources.contract import TOKEN, Manifest, MapperRefusedError, Skipped, Window
from docket.sources.runner import MapperFailedError
from docket.vault import cursors, holds, releaselog
from docket.vault.gate import Gate
from docket.vault.lock import LockBusyError
from docket.vault.vault import Vault

MARKER_LINE = "<!-- docket: steps from run_open -->"
REFERENCE = "https://github.com/serionist/docket/tree/main/examples/sources/plaud"
BLANK = "https://github.com/serionist/docket/blob/main/packages/docket/src/docket/sources/templates/blank/SKILL.md.tmpl"
# The plugin's MCP entry gives every Docket tool call 120 seconds (DR-0160): the close answers within it.
MAPPER_SECONDS = 90.0
# Room for each list in run_open's answer, so the whole answer stays under about 20,000 characters, far below the
# 50,000 past which Claude Code would hand the hook a note about a saved file instead, and no bind would come. The run
# keeps every id; the answer shows what fits, oldest owed first.
KEPT_CHARS = 8_000
HELD_CHARS = 1_500
WAITING_CHARS = 3_000
RETRY_CHARS = 3_000

STEPS = """\
How this run goes:
1. Wait for "docket: run {run} is this session's" after this answer; without it the capture hook isn't running: stop.
2. Follow the skill above for this source's own calls. Skip held ids, and kept ids unless waiting; fetch retry and
   waiting ids by id, and skip one the source no longer has as gone.
3. List until the window's start. Each successful call of a listed tool gets "docket: kept capture <n> ...". A
   successful call of a listed tool with no docket line: stop without closing.
4. "docket: not kept: <tool> (too large)": put that item in skipped as too-large and go on. Any other "not kept":
   stop without closing.
5. Past the budget, fetch no new item; put each one left in skipped as later.
6. Close: ingest(run, captures=<the highest n you saw>, skipped=[...]). Stopping without closing is safe: the run
   drops in 30 minutes and the next sync covers the window again.
7. After closing this run: run_open(source, final=true), and fetch only the ids it gives, each by id."""

FINAL_STEPS = """\
How this final run goes:
1. Wait for "docket: run {run} is this session's" after this answer; without it the capture hook isn't running: stop.
2. Fetch each waiting and retry id above by id, as the skill says, and nothing else: no listing. Skip one the source
   no longer has as gone; past the budget, put each one left in skipped as later.
3. Each successful call of a listed tool gets "docket: kept capture <n> ...". A successful call of a listed tool with
   no docket line: stop without closing.
4. "docket: not kept: <tool> (too large)": put that item in skipped as too-large and go on. Any other "not kept":
   stop without closing.
5. Close: ingest(run, captures=<the highest n you saw>, final=true, skipped=[...])."""


class Due(BaseModel):
    """A retry or waiting id: why it is owed, and since when."""

    id: str
    why: str
    since: date


class RunOpened(BaseModel):
    run: str
    source: str
    final: bool
    since: datetime | None
    until: datetime | None
    skill: str
    tools: list[str]
    budget: int
    held: list[str]
    kept: list[str]
    kept_whole: bool
    waiting: list[Due]
    retry: list[Due]
    notes: list[str]
    steps: str


class Skip(BaseModel):
    id: str | None = Field(default=None, description="The item's own id, or null when it has none.")
    reason: str = Field(description="A short token: later, too-large, gone, rate-limited.")


class SetAsideEntry(BaseModel):
    id: str | None
    reason: str
    tool: str | None
    key: str | None


class WaitingEntry(BaseModel):
    id: str
    waits_for: str


class RunClosed(BaseModel):
    run: str
    source: str
    final: bool
    pending: int = 0
    ready: int = 0
    skipped: int = 0
    on_hold: int = 0
    dropped: dict[str, int] = Field(default_factory=dict[str, int])
    set_aside: list[SetAsideEntry] = Field(default_factory=list[SetAsideEntry])
    waiting: list[WaitingEntry] = Field(default_factory=list[WaitingEntry])
    held_in_raw: list[str] = Field(default_factory=list[str])
    mark_before: datetime | None = None
    mark_after: datetime | None = None
    moved: bool = False
    cursor_problem: str | None = None
    retry_count: int = 0
    waiting_count: int = 0
    expired: list[str] = Field(default_factory=list[str])
    repeated: bool = False


# -- run_open --------------------------------------------------------------------------------------------------------


def _since(text: str | None, zone: ZoneInfo, now: datetime) -> datetime | None:
    when = index.parse_when(text, "since")
    if when is None:
        return None
    if isinstance(when, datetime):
        moment = when if when.tzinfo is not None else when.replace(tzinfo=zone)
    else:
        moment = datetime(when.year, when.month, when.day, tzinfo=zone)
    if moment > now:
        raise DocketError("since is in the future")
    return moment


def _skill(vault: Vault, source: str) -> Path:
    folder = runner.skill_folder(runner.consuming_repo(vault.root), vault.root, source)
    skill = folder / "SKILL.md"
    if not skill.is_file():
        raise DocketError(f"no SKILL.md in {folder}; docket source new {source} writes one")
    text = skill.read_bytes().decode("utf-8", "replace")
    if MARKER_LINE not in text:
        if source == "plaud":
            fix = (
                f"copy SKILL.md and mapper.py from {REFERENCE} over yours, or move your folder aside and run "
                "docket source new plaud --example"
            )
        else:
            fix = f"copy the marker line and the run sections from Docket's blank template, {BLANK}, into yours"
        raise DocketError(f"{skill} is from before live runs: {fix}")
    return skill


def _checked_source(vault: Vault, source: str) -> list[str]:
    config = vault.config.sources.get(source)
    if config is None:
        runnable = sorted(n for n, c in vault.config.sources.items() if c.enabled and c.tools and c.mapper)
        names = ", ".join(runnable) or "none yet"
        raise DocketError(f"no source {source!r} in vault.yaml; sources that can run: {names}")
    if not config.enabled:
        raise DocketError(f"source {source} is turned off in vault.yaml")
    if config.sensitivity == "private":
        raise DocketError(f"source {source} is private in vault.yaml: nothing from it is stored, so it runs no sync")
    if not config.tools:
        raise DocketError(f"source {source} lists no tools in vault.yaml; set sources.{source}.tools")
    runner.find_mapper(vault, source)
    return sorted(config.tools)


def _staged(vault: Vault, source: str) -> list[tuple[str, datetime]]:
    return [(e.staged.record.rid, e.staged.record.ts) for e in Gate(vault).list(source=source).entries]


def _natives(source: str, rids: Iterable[str]) -> set[str]:
    prefix = f"{source}:"
    return {r[len(prefix) :] for r in rids if r.startswith(prefix)}


def _kept(vault: Vault, source: str, window: cursors.Window, notes: list[str]) -> frozenset[str]:
    try:
        raw = index.kept(vault, source, window.since, window.until)
    except DocketError:
        notes.append("kept is incomplete: the index can't answer, so raw's records aren't listed")
        raw = []
    staged = [rid for rid, ts in _staged(vault, source) if window.since <= ts <= window.until]
    return frozenset(_natives(source, [*raw, *staged]))


def _fit[T](items: Sequence[T], text: Callable[[T], str], room: int) -> tuple[list[T], bool]:
    """The items whose text fits the room, in order, and whether all of them did."""
    shown: list[T] = []
    used = 0
    for item in items:
        used += len(text(item)) + 2
        if used > room:
            return shown, False
        shown.append(item)
    return shown, True


def _owed(entry: cursors.Retry | cursors.Waiting) -> str:
    why = entry.reason if isinstance(entry, cursors.Retry) else entry.for_
    return f"{entry.id} ({why}, since {entry.since})"


def _oldest(entry: cursors.Retry | cursors.Waiting) -> tuple[date, str]:
    return entry.since, entry.id


def _stored(vault: Vault, source: str) -> Callable[[Iterable[str]], set[str]]:
    def stored(natives: Iterable[str]) -> set[str]:
        wanted = list(natives)
        try:
            found = index.stored_under(vault, source, wanted)
        except DocketError:
            found = set[str]()
        refs = _natives(source, [rid for rid, _ in _staged(vault, source)])
        return found | {n for n in wanted if cursors.covers(n, refs)}

    return stored


@dataclass(frozen=True)
class Ready:
    """A source that passed `run_open`'s checks before any work, with what those checks read."""

    tools: list[str]
    skill: Path
    own: cursors.Cursor | None
    others: dict[str, cursors.Cursor]
    # Another machine's cursor file left out, for the run's notes.
    notes: list[str]
    held: frozenset[str]


def preflight(vault: Vault, source: str) -> Ready:
    """`run_open`'s checks before any work, which the Sync page runs too (DR-0304): the source in `vault.yaml`, its
    skill with the marker line, this machine's cursor file, no held record waiting in staging."""
    tools = _checked_source(vault, source)
    skill = _skill(vault, source)
    own = cursors.read(vault.root, vault.machine).get(source)
    everyone, notes = cursors.read_others(vault.root, vault.machine)
    others = {m: c[source] for m, c in everyone.items() if source in c}
    held = holds.read(vault.root, source)
    released = frozenset(releaselog.read(vault.root, source))
    ingesting.check_staging(vault, source, held, held & released)
    return Ready(tools=tools, skill=skill, own=own, others=others, notes=notes, held=held)


def owed(vault: Vault, source: str, ready: Ready, now: datetime, *, final: bool) -> cursors.Handover:
    """The retry and waiting ids a run opened now would be handed; with `final`, what a final run would get, so the
    Sync page says one is due exactly when `run_open(final=true)` would open."""
    return cursors.handover(ready.own, ready.others, now, ready.held, _stored(vault, source), final=final)


def new_run_id(now: datetime) -> str:
    return f"run-{now:%Y%m%dT%H%M%S}-{secrets.token_hex(8)}"


async def run_open(
    service: Service,
    source: str,
    *,
    since: str | None = None,
    final: bool = False,
    session: str | None = None,
    agent: str | None = None,
) -> tuple[RunOpened, str]:
    """Open a run of a source: a final one with no window when `final` is set."""
    if since is not None and final:
        raise DocketError("a final run has no window: give since or final, not both")
    building: str | None = None
    if not final:
        try:
            await service.updater.fresh()
        except DocketError:
            building = "the index is still being built, so kept may miss raw's newest records"

    def build() -> RunOpened:
        vault = service.vault()
        ready = preflight(vault, source)
        tools, skill, own, others, held = ready.tools, ready.skill, ready.own, ready.others, ready.held
        notes = list(ready.notes)
        if building is not None:
            notes.append(building)
        # Whole seconds: the mark is written to the second, and the window must start where the mark says.
        now = service.runs.clock().replace(microsecond=0)
        window: cursors.Window | None = None
        kept: frozenset[str] = frozenset()
        if not final:
            window = cursors.window(own, others, now, _since(since, ZoneInfo(vault.config.owner.time_zone), now))
            notes.extend(window.notes)
            kept = _kept(vault, source, window, notes)
        handed = owed(vault, source, ready, now, final=final)
        if final and not handed.waiting and not handed.retry:
            raise DocketError(f"nothing of {source} has waited a week; no final run is needed")
        shown, whole = ([], False) if since is not None else _fit(sorted(kept), str, KEPT_CHARS)
        if since is not None:
            notes.append("a run opened with since gets no kept list: it is for a backfill's listing")
        elif not whole:
            notes.append("kept is incomplete: more ids than the answer holds")
        held_shown, held_whole = _fit(sorted(held), str, HELD_CHARS)
        if not held_whole:
            notes.append(f"held is incomplete: {len(held)} ids, {len(held_shown)} shown; Docket keeps them all out")
        waiting, _ = _fit(sorted(handed.waiting.values(), key=_oldest), _owed, WAITING_CHARS)
        retry, _ = _fit(sorted(handed.retry.values(), key=_oldest), _owed, RETRY_CHARS)
        for name, whole_set, part in (("waiting", handed.waiting, waiting), ("retry", handed.retry, retry)):
            if len(part) < len(whole_set):
                notes.append(
                    f"{name} is incomplete: {len(whole_set)} ids, the {len(part)} oldest shown; the rest come in later "
                    "runs"
                )
        if final:
            # A final close expires what it was handed: only what Claude was shown.
            handed = cursors.Handover(retry={r.id: r for r in retry}, waiting={w.id: w for w in waiting})
        run = Run(
            id=new_run_id(now),
            source=source,
            tools=frozenset(tools),
            opened=now,
            final=final,
            window=window,
            kept=kept,
            handed=handed,
            held=held,
            session=session,
            agent=agent,
            backfill=since is not None,
        )
        service.runs.open(run)
        return RunOpened(
            run=run.id,
            source=source,
            final=final,
            since=window.since if window else None,
            until=window.until if window else None,
            skill=str(skill),
            tools=tools,
            budget=BUDGET,
            held=held_shown,
            kept=shown,
            kept_whole=whole and since is None,
            waiting=[Due(id=w.id, why=w.for_, since=w.since) for w in waiting],
            retry=[Due(id=r.id, why=r.reason, since=r.since) for r in retry],
            notes=notes,
            steps=(FINAL_STEPS if final else STEPS).format(run=run.id),
        )

    opened = await service.writer.run(build)
    return opened, opened_text(opened)


def _list(values: Sequence[str]) -> str:
    return f"{len(values)}: {', '.join(values)}" if values else "none"


def opened_text(opened: RunOpened) -> str:
    window = (
        f"{opened.since.astimezone(UTC):%Y-%m-%dT%H:%M:%SZ} to {opened.until.astimezone(UTC):%Y-%m-%dT%H:%M:%SZ}"
        if opened.since and opened.until
        else "none: a final run"
    )
    kept = _list(opened.kept) + ("" if opened.kept_whole or not opened.kept else ", and more")
    lines = [
        f"run      {opened.run}  {opened.source}",
        f"window   {window}",
        f"skill    {opened.skill}",
        f"tools    {', '.join(opened.tools)}",
        f"budget   {opened.budget // 1024} KB of results",
        f"held     {_list(opened.held)}",
        f"kept     {kept}",
        "waiting  " + _list([f"{d.id} ({d.why}, since {d.since})" for d in opened.waiting]),
        "retry    " + _list([f"{d.id} ({d.why}, since {d.since})" for d in opened.retry]),
        "notes    " + ("; ".join(opened.notes) if opened.notes else "none"),
        "",
        opened.steps,
    ]
    return "\n".join(lines)


# -- ingest ----------------------------------------------------------------------------------------------------------


def _skips(skipped: Sequence[Skip]) -> list[Skipped]:
    out: list[Skipped] = []
    for n, skip in enumerate(skipped, start=1):
        if skip.id is not None and not cursors.writable(skip.id):
            raise DocketError(f"skipped {n}: its id isn't one")
        if not TOKEN.match(skip.reason):
            raise DocketError(f"skipped {n}: its reason is a short token, such as later, too-large or gone")
        out.append(Skipped(id=skip.id, reason=skip.reason))
    return out


async def ingest(
    service: Service,
    run_id: str,
    *,
    captures: int,
    final: bool = False,
    skipped: Sequence[Skip] = (),
    folder: str | None = None,
) -> tuple[RunClosed, str]:
    """Close a run: whole, or dropped with the mark kept. A repeated call answers what the first one did."""
    runs = service.runs
    earlier = runs.outcome(run_id)
    if earlier is not None and earlier.closed is not None:
        closed = earlier.closed.model_copy(update={"repeated": True})
        return closed, closed_text(closed)
    skips = _skips(skipped)
    if captures < 0:
        raise DocketError("captures is 0 or more")
    run = runs.begin_close(run_id)
    run.skips = dict(Counter(s.reason for s in skips))
    # From here a closing run never outlives the call: it goes back to open, is dropped, or reaches _finish, which
    # records its outcome whatever happens.
    try:
        folder_path = _check_close(service, run, captures, final, folder)
        manifest = Manifest(
            run=run.id,
            source=run.source,
            captures=captures,
            final=final,
            window=Window(since=run.window.since, until=run.window.until) if run.window else None,
            skipped=skips,
            folder=str(folder_path) if folder_path is not None else None,
        )
    except _ReopenError as e:
        runs.reopen(run_id)
        raise DocketError(str(e)) from None
    except _DropError as e:
        runs.drop(run_id, str(e), e.kind)
        raise
    except DocketError as e:
        runs.drop(run_id, str(e), history.REFUSED)
        raise
    except Exception:
        runs.drop(run_id, "the close failed", history.FAILED)
        raise
    # Shielded: a client that gives up doesn't stop a close halfway, and its outcome is kept for the next ingest.
    closed = await asyncio.shield(asyncio.ensure_future(_finish(service, run, manifest, folder_path)))
    return closed, closed_text(closed)


async def _finish(service: Service, run: Run, manifest: Manifest, folder: Path | None) -> RunClosed:
    """The close past its checks, its outcome recorded whatever happens: closed, back to open, or dropped."""
    runs = service.runs
    try:
        closed = await _close(service, run, manifest, folder)
    except LockBusyError as e:
        runs.reopen(run.id)
        raise DocketError(f"{e}; the run stays open: call ingest again in a moment") from None
    except (MapperFailedError, MapperRefusedError) as e:
        runs.drop(run.id, f"the mapper failed: {e}", history.MAPPER)
        raise DocketError(
            f"{e}; the run is dropped and nothing is staged; the mark stays. The mapper's own errors aren't shown, "
            "since they can quote a payload: run its tests"
        ) from None
    except DocketError as e:
        runs.drop(run.id, str(e), history.REFUSED)
        raise DocketError(f"{e}; the run is dropped and the mark stays") from None
    except Exception:
        runs.drop(run.id, "the close failed", history.FAILED)
        raise
    runs.finish(run.id, closed, _closing(closed))
    service.updater.poke()
    return closed


def _closing(closed: RunClosed) -> history.Closing:
    """What a whole close adds to its run's line in the history: ids and counts."""
    return history.Closing(
        staged=history.Staged(
            pending=closed.pending, ready=closed.ready, skipped=closed.skipped, on_hold=closed.on_hold
        ),
        dropped=dict(closed.dropped),
        set_aside=[history.SetAside(id=e.id, reason=e.reason, tool=e.tool, key=e.key) for e in closed.set_aside],
        waiting=[w.id for w in closed.waiting],
        mark=history.Mark(before=closed.mark_before, after=closed.mark_after, moved=closed.moved),
        cursor_problem=closed.cursor_problem,
        owed=history.Counts(retry=closed.retry_count, waiting=closed.waiting_count),
        expired=list(closed.expired),
        held_in_raw=list(closed.held_in_raw),
    )


class _ReopenError(DocketError):
    """A close refused before anything was written, for a reason the caller can fix: the run stays open."""


class _DropError(DocketError):
    """A close refused for a reason that drops the run, named by its kind (DR-0302)."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _check_close(service: Service, run: Run, captures: int, final: bool, folder: str | None) -> Path | None:
    if final != run.final:
        what = "a final run closes with final=true" if run.final else "only a final run closes with final=true"
        raise _ReopenError(what)
    try:
        folder_path = local.allowed(service.vault(), run.source, folder) if folder is not None else None
    except DocketError as e:
        raise _ReopenError(str(e)) from None
    if run.session is None:
        raise _DropError(
            history.UNBOUND, "the capture hook never tied the run to a session; is the docket plugin on here?"
        )
    if run.short is not None:
        raise _DropError(
            history.SHORT, f"the run came up short: {run.short}; nothing is staged, the next run fetches it again"
        )
    if captures != len(run.captures):
        raise _DropError(
            history.COUNT,
            f"the manifest counts {captures} capture(s) and {len(run.captures)} arrived; nothing is staged, the next "
            "run fetches them again",
        )
    return folder_path


async def _close(service: Service, run: Run, manifest: Manifest, folder: Path | None) -> RunClosed:
    prepared = await service.writer.run(lambda: ingesting.prepare(service.vault(), run.source, manifest, folder=folder))
    mapped = await asyncio.to_thread(
        ingesting.map_prepared, prepared, manifest, run.captures, folder=folder, time_limit=MAPPER_SECONDS
    )

    def stage() -> RunClosed:
        vault = service.vault()
        report = ingesting.stage(vault, prepared, mapped)
        closed = RunClosed(
            run=run.id,
            source=run.source,
            final=run.final,
            pending=report.staged.pending,
            ready=report.staged.ready,
            skipped=report.staged.skipped,
            on_hold=report.on_hold,
            dropped=dict(report.staged.dropped),
            set_aside=[SetAsideEntry(id=e.id, reason=e.reason, tool=e.tool, key=e.key) for e in report.set_aside],
            waiting=[WaitingEntry(id=w.id, waits_for=w.for_) for w in report.waiting],
            held_in_raw=report.held_in_raw,
        )
        try:
            _move_cursor(vault, run, manifest, report, closed)
        except (DocketError, OSError, ValueError) as e:
            # The records are staged: whatever the cursor did, the run closed, and the next run sees the window again.
            _unmoved(closed, _why(e))
        return closed

    return await service.writer.run(stage)


def _move_cursor(vault: Vault, run: Run, manifest: Manifest, report: ingesting.IngestReport, closed: RunClosed) -> None:
    held = holds.read(vault.root, run.source)
    old = cursors.read(vault.root, vault.machine).get(run.source)
    facts = cursors.Close(
        opened=run.opened,
        window_since=run.window.since if run.window else None,
        default_since=run.window.default_since if run.window else None,
        final=run.final,
        captures=manifest.captures,
        kept=run.kept,
        handed=run.handed,
        skipped=[(s.id, s.reason) for s in manifest.skipped],
        records=report.refs,
        waiting=[(w.id, w.for_) for w in report.waiting],
        set_aside=frozenset(e.id for e in report.set_aside if e.id is not None),
        held=held | run.held,
    )
    after = cursors.close(old, facts, run.opened.date())
    closed.mark_before = old.mark if old else None
    closed.mark_after = after.cursor.mark
    closed.moved = after.moved
    closed.expired = after.expired
    closed.retry_count = len(after.cursor.retry)
    closed.waiting_count = len(after.cursor.waiting)
    try:
        cursors.save(vault, run.source, after.cursor)
    except (DocketError, OSError, ValueError) as e:
        if _on_disk(vault, run.source) == after.cursor:
            closed.cursor_problem = f"{_why(e)}; the mark moved but isn't committed; the next close commits it"
        else:
            _unmoved(closed, _why(e))


def _why(error: Exception) -> str:
    # Docket's own message, else the type only: an OS error's message can name a path or quote what it held.
    return str(error) if isinstance(error, DocketError) else type(error).__name__


def _unmoved(closed: RunClosed, why: str) -> None:
    closed.moved = False
    closed.mark_after = closed.mark_before
    closed.cursor_problem = f"{why}; the mark didn't move, and the next run fetches the window again"


def _on_disk(vault: Vault, source: str) -> cursors.Cursor | None:
    try:
        return cursors.read(vault.root, vault.machine).get(source)
    except DocketError:
        return None


def closed_text(closed: RunClosed) -> str:
    lines = [
        f"run      {closed.run}  {closed.source}"
        + ("  (repeated: this is the first close's answer)" if closed.repeated else ""),
        f"staged   {closed.pending} pending, {closed.ready} ready, {closed.skipped} already staged or in raw",
    ]
    if closed.on_hold:
        lines.append(f"on hold  {closed.on_hold}")
    if closed.dropped:
        lines.append("dropped  " + ", ".join(f"{reason} {n}" for reason, n in sorted(closed.dropped.items())))
    for entry in closed.set_aside:
        where = " ".join(part for part in (entry.tool, entry.key) if part)
        lines.append(f"set aside  {entry.id or '(no id)'}  {entry.reason}" + (f"  {where}" if where else ""))
    lines.extend(f"waiting  {w.id}  for {w.waits_for}" for w in closed.waiting)
    lines.extend(
        f"in raw   {rid} is on hold but already in raw; it waits for docket forget" for rid in closed.held_in_raw
    )
    if closed.cursor_problem:
        lines.append(f"cursor   {closed.cursor_problem}")
    elif closed.moved:
        lines.append(f"mark     moved to {closed.mark_after:%Y-%m-%dT%H:%M:%SZ}")
    else:
        lines.append("mark     stays" + (" (a final run)" if closed.final else ""))
    lines.append(f"owed     {closed.retry_count} to retry, {closed.waiting_count} waiting")
    if closed.expired:
        lines.append(f"expired  {', '.join(closed.expired)}")
    return "\n".join(lines)
