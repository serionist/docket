"""`docket sync start` (DR-0746): the sync folder's prompts and the ledger its agents read, written from the vault
directly, so it works with the service down; nothing else is written. `docket sync recordings` adds each recording's
agent later, once its source's agent is back (`docket.sync.recordings`).

Each agent reads its own slice of the ledger, `context/<source>.md` (`docket.sync.slices`, #305), and a source with a
`pull.py` in its skill folder gets `pull.md`, whose agent runs `docket sync pull` and reads no ledger at all.
`context.md` is the whole ledger as the sync started, for the main thread:

    sync sync-20261020T071500-1a2b3c4d  Tue 2026-10-20 09:15 Europe/Berlin  owed since: the run's window
    sources
    plaud | from 2026-10-13 09:15 | agents/plaud.md | blocks/plaud.txt
    open items (1)
    A-0001 | reply | Ben Sample | Send Ben the pilot dates | slack:C0PILOT/1791100000.000100
    snoozed (0)
    meetings 10-13 to 10-27, or with an open point (1)
    M-0001 | 10-19 10:00-10:30 | Pilot weekly | Ben Sample
      #1 open | Ben: the pilot's dates | 01M4QJ9FZ000X93GNMW1YNY5N8
    streams (1)
    pilot | active | The pilot starts on the 28th | Book the room

Each window is the one `run_open` would open with this machine's clock (DR-0607); the run's own answer stays the one
an agent follows. The meetings run from the earliest window's day to the fifth working day after today, so a
recording from the window's first day and the prep week both find theirs.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from importlib import resources
from pathlib import Path
from zoneinfo import ZoneInfo

from docket import __version__
from docket.errors import DocketError
from docket.index import build as index_build
from docket.ledger import day as ledger_day
from docket.ledger import views
from docket.sources import runner
from docket.sync import folder, reconcile, slices
from docket.vault import cursors
from docket.vault.vault import Vault

THE_WINDOW = "the run's window"
# While the ledger holds no item, what is owed is looked for this many working days back (DR-0772).
FIRST_DAYS = 40
# The meetings run to this many working days after today: the prep week.
PREP_DAYS = 5
PLACEHOLDER = re.compile(r"\{([A-Z_]+)\}")
# A part of a prompt kept only for a source it fits: `[[if NAME]]` or `[[if !NAME]]`, then `[[end]]`, each a line.
PART = re.compile(r"^\[\[if (?P<no>!?)(?P<name>[A-Z_]+)\]\]\n(?P<body>.*?)^\[\[end\]\]\n", re.MULTILINE | re.DOTALL)
PULL = "pull.py"


@dataclass(frozen=True)
class Started:
    folder: Path
    info: folder.Info
    cleared: int


def template(name: str) -> str:
    """A prompt as the package ships it: what is below its first `---` line, whatever line ends a checkout gave it."""
    text = resources.files("docket.sync").joinpath(name).read_text(encoding="utf-8").replace("\r\n", "\n")
    return text.split("\n---\n", 1)[1].lstrip("\n")


def owner(vault: Vault) -> str:
    """The owner as an agent's prompt names them: their name and their addresses, from `vault.yaml`, so an agent with
    none of the session's context knows whose asks to find (#272)."""
    found = vault.config.owner
    return f"{found.name} ({', '.join(found.emails)})" if found.emails else found.name


def fill(text: str, values: dict[str, str], flags: frozenset[str] = frozenset()) -> str:
    """The parts `flags` keep, then the placeholders filled in one pass, so a value that holds a placeholder's name,
    as a path can, stays as it is."""
    kept = PART.sub(lambda found: found["body"] if (found["name"] in flags) != bool(found["no"]) else "", text)
    return PLACEHOLDER.sub(lambda found: values.get(found.group(1), found.group(0)), kept)


def pull_script(vault: Vault, source: str) -> Path | None:
    """The source's `pull.py`, beside its skill, when it has one."""
    try:
        path = runner.skill_folder(runner.consuming_repo(vault.root), vault.root, source) / PULL
    except DocketError:
        return None
    return path if path.is_file() else None


def prep_end(today: date) -> date:
    """The prep week's last day: the fifth working day after today."""
    until = today
    for _ in range(PREP_DAYS):
        until = ledger_day.next_working_day(until)
    return until


def working_days_back(day: date, n: int) -> date:
    while n:
        day -= timedelta(days=1)
        n -= day.weekday() < 5
    return day


def windows(vault: Vault, sources: Sequence[str], now: datetime) -> dict[str, cursors.Window]:
    """Each source's window as `run_open` computes it: the furthest mark any machine reached, less the lookback."""
    own = cursors.read(vault.root, vault.machine)
    everyone, _ = cursors.read_others(vault.root, vault.machine)
    return {
        source: cursors.window(
            own.get(source), {m: c[source] for m, c in everyone.items() if source in c}, now,
            lookback_days=vault.config.sources[source].lookback_days,
        )
        for source in sources
    }  # fmt: skip


def start(vault: Vault, named: Sequence[str], at: datetime) -> Started:
    """A sync folder for the live sources, or those named: refused, writing nothing, when this machine's cursor file
    doesn't read, no source syncs live, or a name isn't a live source's. Old sync folders go first."""
    now = at.astimezone(UTC).replace(microsecond=0)
    tz = ZoneInfo(vault.config.owner.time_zone)
    today = now.astimezone(tz).date()
    syncs, problem = ledger_day.sync_marks(vault, now)
    if problem is not None:
        raise DocketError(f"no sync: {problem}")
    live = [s.source for s in syncs]
    if not live:
        raise DocketError("no source syncs live: a live source has its tools in vault.yaml, is on and isn't private")
    asked = list(dict.fromkeys(named))
    unknown = [name for name in asked if name not in live]
    if unknown:
        raise DocketError(f"not a live source: {', '.join(unknown)}; the live ones: {', '.join(live)}")
    found = windows(vault, asked or live, now)
    until = prep_end(today)
    since = min(w.since for w in found.values()).astimezone(tz).date()
    ask = views.Ask(view="snapshot", since=since.isoformat(), until=until.isoformat())
    me = reconcile.Owner.of(vault)
    with index_build.ledger_tables(vault) as conn:
        snapshot = views.read(conn, ask, tz, now)
        empty = conn.execute("SELECT 1 FROM items LIMIT 1").fetchone() is None
        attendees = _attendees(conn, [m.id for m in snapshot.meetings])
        covers = {
            source: (slices.people(conn, source, w.since, me.is_owner),
                     slices.sees_meetings(conn, source), _has_recordings(conn, source))
            for source, w in found.items()
        }  # fmt: skip
    owed = working_days_back(today, FIRST_DAYS).isoformat() if empty else THE_WINDOW
    cleared = folder.clear(now)
    sync_id = folder.new_id(now)
    path = folder.make(sync_id)
    info = folder.Info(
        sync=sync_id, vault=str(vault.root), made=now, today=today, zone=vault.config.owner.time_zone,
        owed_since=owed, docket=__version__,
        sources=[
            folder.Source(source=source, since=w.since, until=w.until,
                          prompt=str(path / folder.AGENTS / f"{source}.md"),
                          block=str(path / folder.BLOCKS / f"{source}.txt"))
            for source, w in found.items()
        ],
    )  # fmt: skip
    context = path / folder.CONTEXT
    _write(context, "\n".join(_context(info, snapshot, tz)) + "\n")
    prompt, pulled = template("agent.md"), template("pull.md")
    who = owner(vault)
    for source in info.sources:
        people, every_meeting, recorded = covers[source.source]
        pulls = pull_script(vault, source.source) is not None
        files: list[Path] = []
        if not pulls:
            sliced = slices.for_source(snapshot, source.source, people, attendees, until=until,
                                       every_meeting=every_meeting)  # fmt: skip
            head = f"{_head(info, tz)}  the ledger for {source.source}"
            files = slices.write(path / slices.CONTEXTS, source.source, head, views.context_lines(sliced))
        values = {"SOURCE": source.source, "VAULT": str(vault.root), "TODAY": today.isoformat(), "ZONE": info.zone,
                  "OWED_SINCE": owed, "CONTEXT": slices.listed(files) if files else str(context),
                  "BLOCK": source.block, "OWNER": who, "SYNC": sync_id}  # fmt: skip
        flags = frozenset(name for name, on in (
            ("CLASSIFY", vault.config.sources[source.source].sensitivity != "work"), ("RECORDINGS", recorded),
        ) if on)  # fmt: skip
        _write(Path(source.prompt), fill(pulled if pulls else prompt, values, flags))
    # Last, so a folder with its sync.json is whole.
    _write(path / folder.INFO, info.model_dump_json(indent=2) + "\n")
    return Started(path, info, cleared)


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def _head(info: folder.Info, tz: ZoneInfo) -> str:
    made = info.made.astimezone(tz)
    day = ledger_day.WEEKDAYS[made.weekday()]
    return f"sync {info.sync}  {day} {made:%Y-%m-%d %H:%M} {info.zone}  owed since: {info.owed_since}"


def _attendees(conn: sqlite3.Connection, meetings: Sequence[str]) -> dict[str, list[str]]:
    """Each meeting's attendees, by its id, read 500 at a time."""
    out: dict[str, list[str]] = {}
    for k in range(0, len(meetings), 500):
        chunk = list(meetings[k : k + 500])
        query = f"SELECT id, attendees FROM meetings WHERE id IN ({', '.join('?' * len(chunk))})"  # noqa: S608
        out |= {meeting: json.loads(attendees) for meeting, attendees in conn.execute(query, chunk)}
    return out


def _has_recordings(conn: sqlite3.Connection, source: str) -> bool:
    """Whether the source has a recording in raw; with no records to ask, it may."""
    if not slices.has_records(conn):
        return True
    found = conn.execute("SELECT 1 FROM records WHERE source = ? AND kind = 'recording' LIMIT 1", (source,))
    return found.fetchone() is not None


def _context(info: folder.Info, snapshot: views.LedgerRead, tz: ZoneInfo) -> list[str]:
    out = [_head(info, tz), "sources"]
    out += [
        f"{s.source} | from {s.since.astimezone(tz):%Y-%m-%d %H:%M} | {folder.AGENTS}/{s.source}.md | "
        f"{folder.BLOCKS}/{s.source}.txt"
        for s in info.sources
    ]
    return out + views.context_lines(snapshot)


def lines(started: Started) -> list[str]:
    """What `docket sync start` prints: the folder, then a line per agent with its prompt's path."""
    out = [f"sync folder: {started.folder}"]
    out += [f"agent {s.source}: {s.prompt}" for s in started.info.sources]
    if started.cleared:
        out.append(f"cleared {started.cleared} sync folder(s) over 7 days old")
    return out
