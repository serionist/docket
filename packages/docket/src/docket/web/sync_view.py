"""The Sync page's parts, worked out from what the service knows: what needs a session or the user, what failed and
what to do about it, the runs open and ended, and each machine's cursors (DR-0300 to DR-0305).

`build` takes the facts and a clock and does no I/O, so every rule here is tested on invented facts. The page shows
ids, counts, times and Docket's own messages: nothing here reads a capture, and no session or agent id is in the facts.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from starlette.datastructures import QueryParams

from docket.errors import DocketError
from docket.service import history
from docket.service.history import RunLine
from docket.service.runs import RUN_ID, OpenRun
from docket.vault import cursors
from docket.vault.records import source_name_problem
from docket.web import urls

PATH = "/sync"
RUNS = "/sync/runs"
DEFAULT_K = 20
MORE = 20
MAX_K = history.KEEP
# How long an expiry, or a held id already in raw, stays among the failures after the close that found it.
RECENT = timedelta(days=7)
# The entries a cursor's lists show before saying how many more there are.
SHOWN = 200

# What to do about each failure, by its kind (DR-0305). A test holds this to every kind.
ADVICE: dict[str, str] = {
    history.UNBOUND: "turn the docket plugin on in the session that syncs, with docket on PATH, and sync again",
    history.IDLE: "nothing: the next sync covers the window; a rate limit on the listing ends a run this way too",
    history.TOO_LONG: "nothing: the next sync covers the window",
    history.REPLACED: "nothing: the next sync covers the window; sync one source in one session at a time",
    history.STOPPED: "nothing: the next sync covers the window",
    history.SHORT: (
        "a tool not listed: add it to sources.<source>.tools in vault.yaml, or refresh the skill from the reference; "
        "a cap: fewer items a run"
    ),
    history.COUNT: "nothing; if it repeats, the capture hook misses calls: check the docket plugin in that session",
    history.MAPPER: "run the mapper's tests, .claude/skills/sources/<source>/tests in the project",
    history.REFUSED: "what the message says",
    history.FAILED: "docket service status names the service's log",
    "cursor": "nothing if once: the message says what the next run does; if it repeats, look at the vault's state/",
    "rate-limited": "nothing: later runs fetch them by id",
    "too-large": "refresh the source's SKILL.md and mapper.py from the reference, which pages smaller",
    "gone": "nothing: the source no longer has them",
    "retry": "nothing: later runs fetch them by id",
    "expired": "Docket stopped asking for these; if one matters, fetch it by hand and docket ingest its folder",
    "held-in-raw": "it waits for docket forget",
    "own-cursor": "fix the line it names; a merge conflict: keep one side, the one with the older mark is safe",
    "other-cursor": "fix it on that machine; its marks and sets are left out meanwhile",
}

# The word for each way a run ended, as the page shows it.
ENDED: dict[str, str] = {
    history.CLOSED: "closed",
    history.UNBOUND: "never bound",
    history.IDLE: "idle",
    history.TOO_LONG: "too long",
    history.REPLACED: "replaced",
    history.STOPPED: "stopped",
    history.SHORT: "short",
    history.COUNT: "count",
    history.MAPPER: "mapper",
    history.REFUSED: "refused",
    history.FAILED: "failed",
}


# -- the URL ---------------------------------------------------------------------------------------------------------


class SyncStateError(DocketError):
    """A parameter of the Sync page's URL that can't be read; the message names it."""


@dataclass(frozen=True)
class SyncState:
    """The Sync page's URL (DR-0303): `source`, `run` and `k`, in one canonical form, defaults left out."""

    source: str | None = None
    run: str | None = None
    k: int = DEFAULT_K

    @staticmethod
    def read(params: QueryParams | Mapping[str, str]) -> SyncState:
        source = (params.get("source") or "").strip() or None
        if source is not None and source_name_problem(source) is not None:
            raise SyncStateError(f"source is a source's name, such as plaud, not {source!r}")
        run = (params.get("run") or "").strip() or None
        if run is not None and not RUN_ID.match(run):
            raise SyncStateError(f"run is a run's id, such as run-20261015T091200-3f9a1c2e4b5d6e7f, not {run!r}")
        given = (params.get("k") or "").strip()
        # isdigit alone takes a superscript two, which int() refuses.
        if given and (not (given.isascii() and given.isdigit()) or not 1 <= int(given) <= MAX_K):
            raise SyncStateError(f"k is a whole number from 1 to {MAX_K}, not {given!r}")
        return SyncState(source=source, run=run, k=int(given) if given else DEFAULT_K)

    def query(self) -> str:
        pairs = [(n, v) for n, v in (("source", self.source), ("run", self.run)) if v is not None]
        if self.k != DEFAULT_K:
            pairs.append(("k", str(self.k)))
        return urls.query(pairs)

    def url(self, path: str = PATH) -> str:
        query = self.query()
        return f"{path}?{query}" if query else path

    def with_run(self, run: str) -> SyncState:
        return replace(self, run=run)

    def with_k(self, k: int) -> SyncState:
        return replace(self, k=k)


def canonical(url: str) -> str | None:
    """A Sync page URL in its canonical form, or None for one that isn't the page's or doesn't read."""
    path, _, query = url.partition("?")
    if not path.endswith(PATH):
        return None
    try:
        return SyncState.read(QueryParams(query)).url()
    except SyncStateError:
        return None


# -- the facts -------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceFacts:
    name: str
    # Turned on in vault.yaml, and not private: a private source runs no sync.
    enabled: bool
    # Whether vault.yaml lists tools for it: only those sync live.
    live: bool
    # run_open's refusal before any work, in its own words; None when it would open.
    refused: str | None = None
    # What a run opened now would be handed, and a final run, by run_open's rules (DR-0247): expired entries, held ids
    # and the sets of a machine whose mark is 30 days old are left out.
    owed: cursors.Handover = field(default_factory=cursors.Handover)
    final: cursors.Handover = field(default_factory=cursors.Handover)
    # A folder source: Docket reads its folder itself with docket scan, never in a sync (DR-0480).
    folder: bool = False
    # Its lookback_days in vault.yaml, which the window takes over a cursor's own (DR-0607); None when unset.
    lookback_days: int | None = None


@dataclass(frozen=True)
class Facts:
    now: datetime
    # The owner's zone, for every time the page names.
    tz: ZoneInfo
    machine: str
    sources: list[SourceFacts]
    # This machine's cursor file, or why it doesn't read.
    own: cursors.Cursors
    own_problem: str | None
    others: dict[str, cursors.Cursors]
    # Another machine's cursor file left out, in cursors.read_others' words.
    others_notes: list[str]
    open_runs: list[OpenRun]
    # The run history, newest first.
    lines: list[RunLine]
    history_problem: str | None = None


# -- what the page shows ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Need:
    source: str
    kind: Literal["you", "due", "final"]
    line: str
    paste: str | None = None


@dataclass(frozen=True)
class Failure:
    kind: str
    label: str
    source: str
    what: str
    todo: str
    when: datetime | None = None
    ids: list[str] = field(default_factory=list[str])
    run: str | None = None
    severity: Literal["error", "warn"] = "warn"


@dataclass(frozen=True)
class IdList:
    title: str
    ids: list[str]
    more: int = 0


@dataclass(frozen=True)
class RunCard:
    id: str
    source: str
    state: Literal["open", "closed", "dropped"]
    word: str
    badges: list[str]
    opened: datetime
    ended: datetime | None
    took: str
    facts: list[str]
    why: str | None
    lists: list[IdList]
    link: str
    focused: bool = False


@dataclass(frozen=True)
class Entry:
    id: str
    why: str
    since: date
    age: str
    leaves: date | None
    final_due: bool


@dataclass(frozen=True)
class MachineRow:
    machine: str
    this: bool
    mark: datetime | None
    mark_age: str
    lookback: int
    retry: list[Entry]
    waiting: list[Entry]
    more: int
    handed: str | None
    ahead: bool


@dataclass(frozen=True)
class CursorBlock:
    source: str
    window_since: datetime | None
    notes: list[str]
    rows: list[MachineRow]
    problem: str | None


@dataclass(frozen=True)
class Chip:
    label: str
    url: str
    on: bool


@dataclass(frozen=True)
class SyncView:
    lead: str
    chips: list[Chip]
    needs: list[Need]
    paste_all: str | None
    failures: list[Failure]
    focus: RunCard | None
    open_runs: list[RunCard]
    recent: list[RunCard]
    more: str | None
    cursors: list[CursorBlock]
    notes: list[str]
    as_of: datetime
    live: bool
    refresh: str
    # How many sources vault.yaml lists tools for: none means nothing syncs live yet.
    live_sources: int


# -- building it -----------------------------------------------------------------------------------------------------


def ago(then: datetime, now: datetime) -> str:
    """How long ago, in the largest unit that says it: `just now`, `4 min ago`, `26 h ago`, `3 days ago`."""
    seconds = max(0, int((now - then).total_seconds()))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 48 * 3600:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} days ago"


def took(start: datetime, end: datetime) -> str:
    seconds = max(0, int((end - start).total_seconds()))
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min"


def kb(size: int) -> str:
    return f"{(size + 1023) // 1024} KB"


def _day(moment: datetime, tz: ZoneInfo) -> str:
    local = moment.astimezone(tz)
    return f"{local.day} {local:%b %H:%M}"


def _date(day: date) -> str:
    return f"{day.day} {day:%b}"


def _by_reason(counts: Mapping[str, int]) -> str:
    return ", ".join(f"{reason} {n}" for reason, n in sorted(counts.items()))


def _paste(source: str, final: bool = False) -> str:
    if final:
        return f'Run Docket\'s final run for {source}: run_open("{source}", final=true).'
    return f'Sync {source} with Docket: run_open("{source}") and follow its steps.'


def needs(facts: Facts, wanted: str | None) -> tuple[list[Need], str | None]:
    """What needs the user first, then what needs a Claude session (DR-0304)."""
    running = {r.source for r in facts.open_runs}
    you: list[Need] = []
    sessions: list[Need] = []
    due: list[str] = []
    for source in facts.sources:
        if not source.live or not source.enabled or (wanted is not None and source.name != wanted):
            continue
        if source.refused is not None:
            you.append(Need(source.name, "you", source.refused))
            continue
        if source.name in running:
            continue
        marks = cursors.marks(source.name, facts.own, facts.others, facts.machine, facts.now)
        stale = cursors.due(marks, facts.now)
        owed = [*source.final.waiting.values(), *source.final.retry.values()]
        final = ""
        if owed:
            oldest = min(e.since for e in owed)
            final = f"{len(owed)} id{'s' if len(owed) != 1 else ''} due a final run, the oldest since {_date(oldest)}"
        if stale:
            when = f"last synced {ago(marks[0][1], facts.now)}, on {marks[0][0]}" if marks else "never synced"
            sessions.append(
                Need(source.name, "due", "; ".join(p for p in (f"due: {when}", final) if p), _paste(source.name))
            )
            due.append(source.name)
        elif owed:
            sessions.append(Need(source.name, "final", final, _paste(source.name, final=True)))
    paste_all = None
    if len(due) > 1:
        calls = ", then ".join(f'run_open("{s}")' for s in due)
        paste_all = f"Sync {', '.join(due)} with Docket, one source at a time: {calls}, following each one's steps."
    return you + sessions, paste_all


def _latest(lines: Iterable[RunLine]) -> dict[str, datetime]:
    """When each source's latest of these runs ended."""
    last: dict[str, datetime] = {}
    for line in lines:
        if line.source not in last or line.ended > last[line.source]:
            last[line.source] = line.ended
    return last


def failures(facts: Facts, wanted: str | None) -> list[Failure]:
    """The failures still current, each with what to do (DR-0305)."""
    out: list[Failure] = []
    if facts.own_problem is not None:
        out.append(
            Failure(
                "own-cursor",
                "cursor file",
                "",
                f"this machine's cursor file doesn't read: {facts.own_problem}",
                ADVICE["own-cursor"],
                severity="error",
            )
        )
    out.extend(Failure("other-cursor", "cursor file", "", note, ADVICE["other-cursor"]) for note in facts.others_notes)
    lines = [line for line in facts.lines if wanted is None or line.source == wanted]
    last = _latest(line for line in lines if line.whole)
    # Drops since their source's last whole close, one row per source and kind with the newest run named.
    grouped: dict[tuple[str, str], list[RunLine]] = defaultdict(list)
    for line in lines:
        after = last.get(line.source)
        if after is not None and line.ended <= after:
            continue
        if not line.whole:
            grouped[(line.source, line.kind)].append(line)
    for (source, kind), runs in grouped.items():
        newest = max(runs, key=lambda r: r.ended)
        count = f"{len(runs)} runs; the newest: " if len(runs) > 1 else ""
        out.append(
            Failure(
                kind,
                ENDED.get(kind, kind),
                source,
                f"{count}{newest.why or ENDED.get(kind, kind)}",
                ADVICE.get(kind, ADVICE[history.REFUSED]),
                when=newest.ended,
                run=newest.run,
                severity="warn"
                if kind in {history.IDLE, history.TOO_LONG, history.REPLACED, history.STOPPED, history.COUNT}
                else "error",
            )
        )
    # A close whose cursor didn't move or commit, until a later whole close of its source went through clean.
    clean = _latest(line for line in lines if line.whole and line.cursor_problem is None)
    for line in lines:
        if not line.whole or line.cursor_problem is None:
            continue
        if line.source in clean and line.ended <= clean[line.source]:
            continue
        out.append(
            Failure(
                "cursor", "cursor", line.source, line.cursor_problem, ADVICE["cursor"], when=line.ended, run=line.run
            )
        )
    # Expiries, and held ids already in raw, for a week after the close that found them.
    for line in lines:
        if not line.whole or facts.now - line.ended > RECENT:
            continue
        if line.expired_count:
            out.append(
                Failure(
                    "expired",
                    "expired",
                    line.source,
                    f"{line.expired_count} id{'s' if line.expired_count != 1 else ''} expired at this close",
                    ADVICE["expired"],
                    when=line.ended,
                    ids=line.expired,
                    run=line.run,
                )
            )
        if line.held_in_raw_count:
            out.append(
                Failure(
                    "held-in-raw",
                    "held in raw",
                    line.source,
                    f"{line.held_in_raw_count} held id{'s' if line.held_in_raw_count != 1 else ''} already in raw",
                    ADVICE["held-in-raw"],
                    when=line.ended,
                    ids=line.held_in_raw,
                    run=line.run,
                )
            )
    out.extend(_retry_failures(facts, wanted))
    return out


def _retry_failures(facts: Facts, wanted: str | None) -> list[Failure]:
    """Retry entries skipped for a reason other than later, as a run opened now would be handed them, by source and
    reason: an expired entry, a held id, or a retired machine's file asks for nothing, so it is no failure."""
    grouped: dict[tuple[str, str], list[cursors.Retry]] = defaultdict(list)
    for source in facts.sources:
        if wanted is not None and source.name != wanted:
            continue
        for entry in source.owed.retry.values():
            if entry.reason != cursors.LATER:
                grouped[(source.name, entry.reason)].append(entry)
    files = [(facts.machine, facts.own), *sorted(facts.others.items())]
    out: list[Failure] = []
    for (source, reason), entries in sorted(grouped.items()):
        oldest = min(e.since for e in entries)
        ids = {e.id for e in entries}
        machines = sorted(m for m, found in files if source in found and ids & set(found[source].retry))
        leaves = oldest + cursors.EXPIRES
        n = len(entries)
        out.append(
            Failure(
                reason,
                reason,
                source,
                f"{n} id{'s' if n != 1 else ''} in retry since {_date(oldest)}, on {', '.join(machines)}; "
                f"the oldest leaves on {_date(leaves)}",
                ADVICE.get(reason, ADVICE["retry"]),
                ids=sorted(ids)[: history.IDS],
            )
        )
    return out


def _open_card(run: OpenRun, now: datetime, tz: ZoneInfo, state: SyncState) -> RunCard:
    badges = [b for b, on in (("final", run.final), ("backfill", run.backfill), ("closing", run.closing)) if on]
    if not run.bound:
        badges.append("not bound yet")
    facts = [f"{run.captures} capture{'s' if run.captures != 1 else ''}, {kb(run.bytes)} of {kb(run.budget)}"]
    if run.last is not None:
        facts.append(f"last capture {ago(run.last, now)}")
    if run.drops_at is not None and run.bound:
        facts.append(f"drops at {run.drops_at.astimezone(tz):%H:%M} without a capture")
    elif run.drops_at is not None:
        facts.append(f"drops at {run.drops_at.astimezone(tz):%H:%M:%S} unless the capture hook ties it to its session")
    if run.since is not None and run.until is not None:
        facts.append(f"window {_day(run.since, tz)} to {_day(run.until, tz)}")
    facts.append(
        f"told to skip {run.told_to_skip}, held {run.held}, handed {run.handed.retry} to retry and "
        f"{run.handed.waiting} waiting"
    )
    return RunCard(
        id=run.id,
        source=run.source,
        state="open",
        word="open",
        badges=badges,
        opened=run.opened,
        ended=None,
        took=took(run.opened, now),
        facts=facts,
        why=f"short: {run.short}; its close will drop it" if run.short else None,
        lists=[],
        link=state.with_run(run.id).url(),
        focused=state.run == run.id,
    )


def _line_card(line: RunLine, tz: ZoneInfo, state: SyncState) -> RunCard:
    badges = [b for b, on in (("final", line.final), ("backfill", line.backfill)) if on]
    facts = [f"{line.captures} capture{'s' if line.captures != 1 else ''}, {kb(line.bytes)}"]
    lists: list[IdList] = []
    if line.whole:
        staged = line.staged or history.Staged()
        facts.append(
            f"staged {staged.pending} pending, {staged.ready} ready; {staged.skipped} already staged or in raw"
        )
        if staged.on_hold:
            facts.append(f"on hold {staged.on_hold}")
        if line.dropped:
            facts.append(f"dropped at intake: {_by_reason(line.dropped)}")
        if line.set_aside_count or line.waiting_count:
            facts.append(f"set aside {line.set_aside_count}, waiting {line.waiting_count}")
        mark = line.mark or history.Mark()
        facts.append(f"mark moved to {_day(mark.after, tz)}" if mark.moved and mark.after else "mark stays")
        owed = line.owed or history.Counts()
        facts.append(f"owes {owed.retry} to retry, {owed.waiting} waiting")
        if line.expired_count:
            facts.append(f"expired {line.expired_count}")
        lists = [
            IdList(title, ids, more)
            for title, ids, more in (
                (
                    "set aside",
                    [f"{e.id or '(no id)'} ({e.reason})" for e in line.set_aside],
                    line.set_aside_count - len(line.set_aside),
                ),
                ("waiting", line.waiting, line.waiting_count - len(line.waiting)),
                ("expired", line.expired, line.expired_count - len(line.expired)),
                ("held but in raw", line.held_in_raw, line.held_in_raw_count - len(line.held_in_raw)),
            )
            if ids
        ]
    if line.skipped:
        facts.append(f"skipped: {_by_reason(line.skipped)}")
    if line.since is not None and line.until is not None:
        facts.append(f"window {_day(line.since, tz)} to {_day(line.until, tz)}")
    why = line.why if not line.whole else line.cursor_problem
    return RunCard(
        id=line.run,
        source=line.source,
        state="closed" if line.whole else "dropped",
        word=ENDED.get(line.kind, line.kind),
        badges=badges,
        opened=line.opened,
        ended=line.ended,
        took=took(line.opened, line.ended),
        facts=facts,
        why=why,
        lists=lists,
        link=state.with_run(line.run).url(),
        focused=state.run == line.run,
    )


def _entry(entry: cursors.Retry | cursors.Waiting, today: date, due: frozenset[str]) -> Entry:
    """One entry of a cursor file; `due` is what a final run would be handed now, so the badge says what run_open
    would do."""
    age = (today - entry.since).days
    if isinstance(entry, cursors.Retry):
        leaves = None if entry.reason == cursors.LATER else entry.since + cursors.EXPIRES
        return Entry(entry.id, entry.reason, entry.since, f"{age} days", leaves, entry.id in due)
    return Entry(entry.id, entry.for_, entry.since, f"{age} days", None, entry.id in due)


def _row(
    machine: str, cursor: cursors.Cursor, facts: Facts, this: bool, due: frozenset[str], *, setting: int | None
) -> MachineRow:
    today = facts.now.date()
    retry = sorted((_entry(e, today, due) for e in cursor.retry.values()), key=lambda e: (e.since, e.id))
    waiting = sorted((_entry(e, today, due) for e in cursor.waiting.values()), key=lambda e: (e.since, e.id))
    more = max(0, len(retry) - SHOWN) + max(0, len(waiting) - SHOWN)
    handed = None
    if not this:
        fresh = cursor.mark is not None and facts.now - cursor.mark < cursors.OTHERS_FOR
        handed = "its sets are handed over" if fresh else "its sets aren't handed over: its mark is 30 days old or none"
    return MachineRow(
        machine=machine,
        this=this,
        mark=cursor.mark,
        mark_age=ago(cursor.mark, facts.now) if cursor.mark is not None else "",
        lookback=cursors.lookback(cursor, setting),
        retry=retry[:SHOWN],
        waiting=waiting[:SHOWN],
        more=more,
        handed=handed,
        ahead=cursor.mark is not None and cursor.mark > facts.now,
    )


def cursor_blocks(facts: Facts, names: list[str]) -> list[CursorBlock]:
    blocks: list[CursorBlock] = []
    finals = {s.name: frozenset({*s.final.retry, *s.final.waiting}) for s in facts.sources}
    settings = {s.name: s.lookback_days for s in facts.sources}
    for source in names:
        due = finals.get(source, frozenset[str]())
        setting = settings.get(source)
        rows: list[MachineRow] = []
        own = facts.own.get(source)
        if own is not None:
            rows.append(_row(facts.machine, own, facts, this=True, due=due, setting=setting))
        others = {m: c[source] for m, c in sorted(facts.others.items()) if source in c}
        rows.extend(_row(m, c, facts, this=False, due=due, setting=setting) for m, c in others.items())
        window: cursors.Window | None = None
        if facts.own_problem is None:
            window = cursors.window(own, others, facts.now, lookback_days=setting)
        blocks.append(
            CursorBlock(
                source=source,
                window_since=window.since if window else None,
                notes=window.notes if window else [],
                rows=rows,
                problem=facts.own_problem,
            )
        )
    return blocks


def _names(facts: Facts) -> list[str]:
    """The page's sources: those that sync live, and any a cursor file or the history names."""
    names = {s.name for s in facts.sources if s.live}
    names |= set(facts.own)
    for found in facts.others.values():
        names |= set(found)
    names |= {line.source for line in facts.lines}
    names |= {r.source for r in facts.open_runs}
    return sorted(names)


def _lead(found: list[Need], open_runs: int, last_whole: datetime | None, now: datetime) -> str:
    you = sum(1 for n in found if n.kind == "you")
    due = sum(1 for n in found if n.kind == "due")
    final = sum(1 for n in found if n.kind == "final")
    parts: list[str] = []
    if due:
        parts.append(f"{due} source{'s' if due != 1 else ''} due a sync")
    if final:
        parts.append(f"{final} due a final run")
    if you:
        parts.append(f"{you} need{'s' if you == 1 else ''} you first")
    if not parts:
        parts.append("nothing needs a session")
    parts.append(f"{open_runs} run{'s' if open_runs != 1 else ''} open")
    parts.append(f"last whole run {ago(last_whole, now)}" if last_whole else "no whole run here yet")
    return "; ".join(parts)


def build(facts: Facts, state: SyncState) -> SyncView:
    names = _names(facts)
    wanted = state.source
    shown = [wanted] if wanted is not None else names
    notes: list[str] = []
    scanned = sorted(s.name for s in facts.sources if s.folder and s.enabled)
    if wanted is not None and wanted in scanned:
        notes.append(f"{wanted} is a folder source: docket scan {wanted} reads its folder, so it never syncs here.")
    elif wanted is not None and wanted not in names:
        notes.append(f"No source {wanted} on this page: vault.yaml lists no tools for it, and nothing synced it here.")
    elif scanned:
        notes.append(f"Folder sources, read by docket scan and never synced here: {', '.join(scanned)}.")
    if facts.history_problem is not None:
        notes.append(f"Recent runs aren't kept across a restart: {facts.history_problem}")
    found, paste_all = needs(facts, wanted)
    open_runs = [r for r in facts.open_runs if wanted is None or r.source == wanted]
    lines = [line for line in facts.lines if wanted is None or line.source == wanted]
    focus: RunCard | None = None
    if state.run is not None:
        match_open = next((r for r in facts.open_runs if r.id == state.run), None)
        match_line = next((line for line in facts.lines if line.run == state.run), None)
        if match_open is not None:
            focus = _open_card(match_open, facts.now, facts.tz, state)
        elif match_line is not None:
            focus = _line_card(match_line, facts.tz, state)
        else:
            notes.append(f"Run {state.run} isn't open, nor among the {history.KEEP} the history keeps.")
    recent = [_line_card(line, facts.tz, state) for line in lines[: state.k] if line.run != state.run]
    more = state.with_k(min(MAX_K, state.k + MORE)).url() if len(lines) > state.k else None
    whole = [line.ended for line in facts.lines if line.whole]
    return SyncView(
        lead=_lead(found, len(open_runs), max(whole) if whole else None, facts.now),
        chips=[Chip("All", SyncState(k=state.k).url(), wanted is None)]
        + [Chip(name, SyncState(source=name, k=state.k).url(), wanted == name) for name in names],
        needs=found,
        paste_all=paste_all,
        failures=failures(facts, wanted),
        focus=focus,
        open_runs=[_open_card(r, facts.now, facts.tz, state) for r in open_runs if r.id != state.run],
        recent=recent,
        more=more,
        cursors=cursor_blocks(facts, [n for n in shown if n in names]),
        notes=notes,
        as_of=facts.now,
        live=bool(facts.open_runs),
        refresh=state.url(RUNS),
        live_sources=sum(1 for s in facts.sources if s.live),
    )
