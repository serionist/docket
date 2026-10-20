"""`docket sync start` and `docket sync notes` (DR-0720, DR-0721): the sync folder's prompts and the ledger its agents
read, written from the vault directly, so they work with the service down; nothing else is written.

`context.md`, every agent's one file of the ledger:

    sync sync-20261020T071500-1a2b3c4d  Tue 2026-10-20 09:15 Europe/Berlin  owed since: the run's window
    sources
    plaud | from 2026-10-13 09:15 | agents/plaud.md | blocks/plaud.txt
    recordings with no note (1)
    plaud:rec-0101 | 10-19 10:01 | Pilot weekly
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

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from importlib import resources
from pathlib import Path
from zoneinfo import ZoneInfo

from docket import __version__, recall
from docket.errors import DocketError
from docket.index import build as index_build
from docket.ledger import day as ledger_day
from docket.ledger import views
from docket.sources import runner
from docket.sync import block, folder
from docket.vault import cursors
from docket.vault.processed import id_problem
from docket.vault.vault import Vault

THE_WINDOW = "the run's window"
# While the ledger holds no item, what is owed is looked for this many working days back (DR-0721).
FIRST_DAYS = 40
# The meetings run to this many working days after today: the prep week.
PREP_DAYS = 5
NOTE_TYPE = "recording-note"
PLACEHOLDER = re.compile(r"\{([A-Z_]+)\}")
LAYOUT = "a title, who spoke, the notes with every number, date, name and commitment said, and the actions at the end"
TIME_FINDS = "none named: the one 10 minutes either side of its start, by `ledger` view `meetings`"


@dataclass(frozen=True)
class Started:
    folder: Path
    info: folder.Info
    cleared: int


@dataclass(frozen=True)
class Note:
    doc: str
    prompt: Path


@dataclass(frozen=True)
class Noted:
    """The note prompts written, and what was said instead of one."""

    notes: list[Note]
    said: list[str]


def template(name: str) -> str:
    """A prompt as the package ships it: what is below its first `---` line, whatever line ends a checkout gave it."""
    text = resources.files("docket.sync").joinpath(name).read_text(encoding="utf-8").replace("\r\n", "\n")
    return text.split("\n---\n", 1)[1].lstrip("\n")


def fill(text: str, values: dict[str, str]) -> str:
    """The placeholders filled in one pass, so a value that holds a placeholder's name, as a path can, stays as it
    is."""
    return PLACEHOLDER.sub(lambda found: values.get(found.group(1), found.group(0)), text)


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


def _when(moment: datetime, tz: ZoneInfo, today: date) -> str:
    local = moment.astimezone(tz)
    day = f"{local:%m-%d}" if local.year == today.year else f"{local:%Y-%m-%d}"
    return f"{day} {local:%H:%M}"


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
    until = today
    for _ in range(PREP_DAYS):
        until = ledger_day.next_working_day(until)
    since = min(w.since for w in found.values()).astimezone(tz).date()
    ask = views.Ask(view="snapshot", since=since.isoformat(), until=until.isoformat())
    with index_build.ledger_tables(vault) as conn:
        snapshot = views.read(conn, ask, tz, now)
        empty = conn.execute("SELECT 1 FROM items LIMIT 1").fetchone() is None
    owed = working_days_back(today, FIRST_DAYS).isoformat() if empty else THE_WINDOW
    try:
        recordings: list[index_build.Unnoted] | str = index_build.unnoted(
            vault, {source: (w.since, w.until) for source, w in found.items()}
        )
    except DocketError as e:
        recordings = str(e)
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
    _write(context, "\n".join(_context(info, snapshot, recordings, tz)) + "\n")
    prompt = template("agent.md")
    for source in info.sources:
        values = {"SOURCE": source.source, "VAULT": str(vault.root), "TODAY": today.isoformat(), "ZONE": info.zone,
                  "OWED_SINCE": owed, "CONTEXT": str(context), "BLOCK": source.block}  # fmt: skip
        _write(Path(source.prompt), fill(prompt, values))
    # Last, so a folder with its sync.json is whole.
    _write(path / folder.INFO, info.model_dump_json(indent=2) + "\n")
    return Started(path, info, cleared)


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def _context(
    info: folder.Info, snapshot: views.LedgerRead, recordings: list[index_build.Unnoted] | str, tz: ZoneInfo
) -> list[str]:
    made = info.made.astimezone(tz)
    day = ledger_day.WEEKDAYS[made.weekday()]
    out = [f"sync {info.sync}  {day} {made:%Y-%m-%d %H:%M} {info.zone}  owed since: {info.owed_since}", "sources"]
    out += [
        f"{s.source} | from {s.since.astimezone(tz):%Y-%m-%d %H:%M} | {folder.AGENTS}/{s.source}.md | "
        f"{folder.BLOCKS}/{s.source}.txt"
        for s in info.sources
    ]
    if isinstance(recordings, str):
        out.append(f"recordings with no note: the index can't say ({recordings})")
    else:
        out.append(f"recordings with no note ({len(recordings)})")
        out += [f"{r.rid} | {_when(r.ts, tz, info.today)} | {views.as_field(r.title or '-')}" for r in recordings]
    return out + views.context_lines(snapshot)


def lines(started: Started) -> list[str]:
    """What `docket sync start` prints: the folder, then a line per agent with its prompt's path."""
    out = [f"sync folder: {started.folder}"]
    out += [f"agent {s.source}: {s.prompt}" for s in started.info.sources]
    if started.cleared:
        out.append(f"cleared {started.cleared} sync folder(s) over 7 days old")
    return out


# -- docket sync notes -------------------------------------------------------------------------------------------------


def notes(vault: Vault, sync: str) -> Noted:
    """A prompt per note the blocks' NOTES lines name, two lines with one doc id making one note over both recordings.
    A line that doesn't read, names no recording note, or whose recording doesn't open gets none, and is said."""
    path, info = folder.find(sync, vault.root)
    wanted: dict[str, list[tuple[str, block.Line]]] = {}
    said: list[str] = []
    for source in info.sources:
        rel = f"{folder.BLOCKS}/{source.source}.txt"
        file = path / rel
        if not file.is_file():
            said.append(f"{rel} isn't there: no block from {source.source}")
            continue
        for line in block.parse(file.read_bytes().decode("utf-8", "replace")):
            if line.label != "NOTES":
                continue
            where = f"{rel}:{line.n}"
            doc = line.fields[0]
            problem = line.problem or id_problem(doc)
            if problem is None and not doc.startswith(f"{NOTE_TYPE}:"):
                problem = f"{doc} isn't a {NOTE_TYPE}"
            if problem is not None:
                said.append(f"{where}: {problem}")
                continue
            wanted.setdefault(doc, []).append((where, line))
    rids = [line.fields[1] for lines in wanted.values() for _, line in lines]
    opens = recall.resolves(vault, rids) if rids else set[str]()
    tz = ZoneInfo(info.zone)
    prompt = template("note.md")
    made: list[Note] = []
    for doc, found in wanted.items():
        kept: list[block.Line] = []
        for where, line in found:
            if line.fields[1] in opens:
                kept.append(line)
            else:
                said.append(f"{where}: {line.fields[1]} doesn't open, so it gets no note")
        if not kept:
            continue
        refs = list(dict.fromkeys(line.fields[4] for line in kept if len(line.fields) > 4 and line.fields[4] != "-"))
        values = {
            "VAULT": str(vault.root), "DOC": doc, "TODAY": info.today.isoformat(), "ZONE": info.zone,
            "SYNC_AT": f"{info.made.astimezone(tz):%Y-%m-%dT%H:%M}",
            "RECORDINGS": "; ".join(dict.fromkeys(f"{ln.fields[1]}, {ln.fields[2]} to {ln.fields[3]}" for ln in kept)),
            "MEETING": f"{', '.join(refs)}, which its content names" if refs else TIME_FINDS,
            "LAYOUT": _layout(vault, kept[0].fields[1].removeprefix("r:").partition(":")[0]),
        }  # fmt: skip
        file = path / folder.NOTES / f"{doc.partition(':')[2]}.md"
        _write(file, fill(prompt, values))
        made.append(Note(doc, file))
    return Noted(made, said)


def _layout(vault: Vault, source: str) -> str:
    """The note's layout: the one the source's `sync.md` gives, when it has one (DR-0688), else Docket's."""
    try:
        sync_md = runner.skill_folder(runner.consuming_repo(vault.root), vault.root, source) / "sync.md"
    except DocketError:
        return LAYOUT
    return f"the one {sync_md} gives for a recording note, else {LAYOUT}" if sync_md.is_file() else LAYOUT


def note_lines(noted: Noted) -> list[str]:
    """What `docket sync notes` prints: a line per note with its prompt's path, then what was said instead."""
    out = [f"note {n.doc}: {n.prompt}" for n in noted.notes]
    out += noted.said
    if not out:
        out.append("no note to write: the blocks name none")
    return out
