"""`docket sync summary <sync>` (#272): the sync's summary groups, read from the ledger after the write, a line per
item in the short form, for Claude to read once and talk through. It reads and writes nothing else: no shell over a
snapshot, and no write, so the skill pre-approves it.

    summary sync-20261020T071500-1a2b3c4d  Tue 2026-10-20 09:40 Europe/Budapest: 4 open, 1 snoozed, 1 overdue
    batch 01JAB3W8Q4X5M2Z7K9R0T6YVNC: 9 lines; undo it whole with {"op": "undo", "batch": "01JAB3W8..."}
    closed (1)
      A-0001 done: slack:C0PILOT/1791100500.000200
    added (1)
      A-0008 | reply | Dan Example | Send Dan the floor plan | 0d @M-0001
    logged 1 on 1 item(s); 1 point, 1 material, 1 point_set
    outcomes written (1)
      M-0001 outcome: The pilot starts on the 28th. Dan books the room.
    meetings and prep (1)
      M-0002 | 10-26 10:00-10:30 | Pilot weekly | Ben Sample
        #1 open | Dan: is the room booked for the 28th? -> A-0003 (proposed)
        A-0003 | task | Dan Example | Book the room for the pilot | 7d, due 10-23 @M-0002
    today and overdue (1)
      ...
    runs short (1)
      teams: mark moved no: the chat list didn't load
    notes (1 of 2 put)
      plaud:rec-0101 | Pilot weekly | recording-note:2026-10-19-pilot-weekly
      plaud:rec-0102 | A call | no note in the index
    staging: 3 pending, 0 ready

| Group | What |
|---|---|
| batch, closed, added | this sync's batches: each by `sync`, its first op minted after the sync's start (DR-0612) |
| meetings and prep | to the fifth working day: each meeting not cancelled or declined, with an open point or item |
| today and overdue, this week, whenever | what the owner owes: due by today, in the next 7 days, the rest |
| invites, waiting | the open invites; what the owner waits on |
| left over | a meeting before now still holding an open point, which rollover had nowhere to take |
| runs short | a RUN line whose mark didn't move or whose run didn't open; a block with no RUN line; no block |
| notes | each recording given an agent, and the note the index says covers it |

An item is listed once among today, this week, whenever, invites and waiting; a meeting may list it again. The whole
keeps within 30,000 characters, what Claude Code keeps of a Bash result: past it, rows go from whenever first, then
left over, added, waiting, this week, invites, closed, the outcomes, today and the meetings, each group keeping its
count and a line saying how many more `docket ledger list` gives.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from docket.errors import DocketError
from docket.index import build as index_build
from docket.ledger import day as ledger_day
from docket.ledger import ops, views
from docket.sync import block, folder
from docket.vault.staging import Staging
from docket.vault.vault import Vault

# What Claude Code keeps of a Bash result before it saves the rest to a file.
ROOM = 30_000
OWES = ("reply", "decision", "task")
WEEK = timedelta(days=7)
PREP_DAYS = 5
GONE = ("cancelled", "declined")
CLOSES = ("done", "drop")
# The groups rows go from first when the whole is past the room: those listed in another group too first.
CUT = ("whenever", "left over", "added", "waiting", "this week", "invites", "closed", "outcomes written",
       "today and overdue", "meetings and prep")  # fmt: skip
# A sync writes one batch, or a few when a write is fixed and made again: the newest this many are read.
MOST_BATCHES = 3
# Ops minted since the start, by the ids' index: a batch's ops all have ids from its own on (DR-0612).
BATCHES = (
    "SELECT batch FROM item_log WHERE id >= ? AND batch >= ? AND by = 'sync' UNION SELECT batch FROM meeting_log WHERE "
    "id >= ? AND batch >= ? AND by = 'sync' UNION SELECT batch FROM stream_log WHERE id >= ? AND batch >= ? AND by = "
    "'sync' ORDER BY batch"
)


@dataclass
class Group:
    """A group of the summary: its heading, its rows, how many a cut took off its end, and what its heading counts
    when that isn't its rows, as a meeting's points and items are rows under it."""

    name: str
    rows: list[str] = field(default_factory=list[str])
    cut: int = 0
    counted: bool = True
    count: int | None = None

    def lines(self) -> list[str]:
        if not self.rows and not self.cut:
            return []
        n = self.count if self.count is not None else len(self.rows) + self.cut
        head = f"{self.name} ({n})" if self.counted else self.name
        more = [f"  and {self.cut} more: docket ledger list gives them"] if self.cut else []
        return [head, *self.rows, *more]


@dataclass(frozen=True)
class Summary:
    """The summary's head line and its groups, in order; the lines a group with no rows gives are none."""

    head: str
    groups: list[Group]

    def lines(self) -> list[str]:
        return [self.head, *(line for group in self.groups for line in group.lines())]


def summary(vault: Vault, sync: str, at: datetime) -> Summary:
    """The sync's summary from the ledger as it stands at `at`; a sync of another vault, or none on this machine, is
    refused before anything is read."""
    path, info = folder.find(sync, vault.root)
    tz = ZoneInfo(info.zone)
    today = at.astimezone(tz).date()
    end = today
    for _ in range(PREP_DAYS):
        end = ledger_day.next_working_day(end)
    ask = views.Ask(view="snapshot", since=today.isoformat(), until=end.isoformat())
    with index_build.ledger_tables(vault) as conn:
        snapshot = views.read(conn, ask, tz, at)
        batches = _batches(conn, info.made)
        written = {batch: views.batch_lines(conn, batch) for batch in batches[-MOST_BATCHES:]}
    local = at.astimezone(tz)
    c = snapshot.counts
    head = (
        f"summary {info.sync}  {ledger_day.WEEKDAYS[local.weekday()]} {local:%Y-%m-%d %H:%M} {info.zone}: "
        f"{c.open} open, {c.snoozed} snoozed, {c.overdue} overdue"
    )
    groups = _written(written, snapshot, today)
    if len(batches) > MOST_BATCHES:
        older = len(batches) - MOST_BATCHES
        groups.insert(0, _Said(f"{older} older batch(es) by sync since the sync started, not listed"))
    groups += _owed(snapshot, at, end, today)
    groups += [_runs(path, info), _notes(vault, path, info), _staging(vault)]
    return _fitted(Summary(head, groups))


def _batches(conn: sqlite3.Connection, made: datetime) -> list[str]:
    """The batches written by `sync` since the sync started, oldest first: each first op's id minted after its start
    (DR-0612). A sync writes one; a ledger that holds more names only the newest few."""
    floor = ops.encode(int(made.astimezone(UTC).timestamp() * 1000) << ops.RANDOM_BITS)
    return [str(batch) for (batch,) in conn.execute(BATCHES, (floor,) * 6)]


def _row(item: views.ItemRow, today: date) -> str:
    """An item in the summary's short form: `A-0012 | reply | Ada Example | Send the dates | 12d, due 10-21, 2d late
    @M-0003 <chase>`, the source left out, as the day page leaves it."""
    facts = [f"{item.days}d"]
    facts += [f"due {views.short_day(item.due, today)}"] if item.due is not None else []
    facts += [f"{item.late}d late"] if item.late is not None else []
    facts += [f"snooze ended {views.short_day(item.until, today)}"] if item.woke and item.until is not None else []
    text = f"{item.ref} | {item.kind} | {views.as_field(item.who)} | {views.as_field(item.title)} | {', '.join(facts)}"
    text += f" @{item.meeting}" if item.meeting else ""
    return text + (f" <{item.triage}>" if item.triage else "")


def _point(point: views.PointRow) -> str:
    text = f"#{point.n} {point.state} | {views.as_field(point.text)}"
    text += f" -> {point.item}" if point.item else ""
    return text + (" (proposed)" if point.proposed else "")


class _Said(Group):
    """A line of its own, with no rows: always said."""

    def __init__(self, text: str) -> None:
        super().__init__(text, counted=False)

    def lines(self) -> list[str]:
        return [self.name]


def _written(written: dict[str, list[views.BatchLine]], snapshot: views.LedgerRead, today: date) -> list[Group]:
    """Each batch with its undo, then what the batches closed and added, the logs and the other kinds counted, and
    the outcomes they wrote."""
    out: list[Group] = [
        _Said(f'batch {batch}: {len(lines)} lines; undo it whole with {{"op": "undo", "batch": "{batch}"}}')
        for batch, lines in written.items()
    ]
    if not written:
        out.append(_Said("batch: none by sync since the sync started, so nothing is written yet"))
    every = [line for lines in written.values() for line in lines]
    items = {item.ref: item for item in snapshot.items}
    out.append(Group("closed", [f"  {line.ref} {line.line}" for line in every if line.kind in CLOSES]))
    out.append(Group("added", [
        f"  {_row(items[line.ref], today)}" if line.ref in items else f"  {line.ref} {line.line}"
        for line in every if line.kind == "add" and line.ref.startswith("A-")
    ]))  # fmt: skip
    logs = [line for line in every if line.kind == "log"]
    others = Counter(line.kind for line in every if line.kind not in (*CLOSES, "add", "log"))
    said = [f"logged {len(logs)} on {len({line.ref for line in logs})} item(s)"] if logs else []
    said += [", ".join(f"{n} {kind}" for kind, n in others.items())] if others else []
    out += [_Said("; ".join(said))] if said else []
    out.append(Group("outcomes written", [
        f"  {line.ref} {line.line}" for line in every if line.kind == "set" and line.line.startswith("outcome:")
    ]))  # fmt: skip
    return out


def _owed(snapshot: views.LedgerRead, at: datetime, end: date, today: date) -> list[Group]:
    """The meetings and prep, then what the owner owes by its due, the invites, the waits and the meetings left
    over, each item once among the item groups."""
    items = {item.ref: item for item in snapshot.items}
    awake = [item for item in snapshot.items if item.state == "open" or item.woke]
    points: dict[str, list[views.PointRow]] = {}
    for point in snapshot.points:
        if point.state == "open":
            points.setdefault(point.meeting, []).append(point)
    prep = Group("meetings and prep")
    left = Group("left over")
    for m in snapshot.meetings:
        if m.response in GONE:
            continue
        open_items = [items[ref] for ref in m.items if ref in items]
        if m.end <= at:
            if points.get(m.ref):
                left.rows.append(f"  {views.short_meeting(m, today)} | {len(points[m.ref])} open")
            continue
        if m.start.date() > end or not (points.get(m.ref) or open_items):
            continue
        prep.count = (prep.count or 0) + 1
        prep.rows.append(f"  {views.short_meeting(m, today)}")
        prep.rows += [f"    {_point(p)}" for p in points.get(m.ref, [])]
        prep.rows += [f"    {_row(item, today)}" for item in open_items]
    week = today + WEEK
    owes = [item for item in awake if item.kind in OWES]
    return [
        prep,
        Group("today and overdue", [f"  {_row(i, today)}" for i in owes if i.due is not None and i.due <= today]),
        Group("this week", [f"  {_row(i, today)}" for i in owes if i.due is not None and today < i.due <= week]),
        Group("whenever", [f"  {_row(i, today)}" for i in owes if i.due is None or i.due > week]),
        Group("invites", [f"  {_row(i, today)}" for i in awake if i.kind == "invite"]),
        Group("waiting", [f"  {_row(i, today)}" for i in awake if i.kind == "waiting"]),
        left,
    ]


def _runs(path: Path, info: folder.Info) -> Group:
    """Each source whose run stopped short: its RUN line's mark didn't move or its run didn't open, its block has no
    RUN line, or it has no block."""
    out = Group("runs short")
    for source in info.sources:
        file = path / folder.BLOCKS / f"{source.source}.txt"
        if not file.is_file():
            out.rows.append(f"  {source.source}: no block, so its agent's answer says why")
            continue
        runs = [line for line in block.parse(file.read_bytes().decode("utf-8", "replace")) if line.label == "RUN"]
        if not runs:
            out.rows.append(f"  {source.source}: its block has no RUN line")
            continue
        fields = runs[0].fields
        said = fields[-1] if len(fields) > 2 else "-"
        if len(fields) > 1 and fields[1] == "none":
            out.rows.append(f"  {source.source}: no run: {fields[2] if len(fields) > 2 else '-'}")
        elif "mark moved no" in fields:
            out.rows.append(f"  {source.source}: mark moved no: {said}")
    return out


def _notes(vault: Vault, path: Path, info: folder.Info) -> Group:
    """Each recording given an agent in this sync, and the note the index says covers it now."""
    given = [r for source in info.sources for r in folder.recordings(path, source.source)]
    if not given:
        return Group("notes")
    try:
        found = index_build.noted(vault, [r.rid for r in given])
    except DocketError as e:
        return _Said(f"notes: the index can't say which of {len(given)} recording(s) have one: {e}")
    group = Group(f"notes ({len(found)} of {len(given)} put)", counted=False)
    for r in given:
        note = found.get(r.rid, "no note in the index")
        group.rows.append(f"  {r.rid} | {views.as_field(r.title or 'no title')} | {note}")
    return group


def _staging(vault: Vault) -> Group:
    counts = Counter(entry.staged.state for entry in Staging(vault.root, vault.home).scan().entries)
    return _Said(f"staging: {counts['pending']} pending, {counts['ready']} ready")


def _fitted(found: Summary) -> Summary:
    """The summary within the room: past it, rows go from the end of each group in `CUT`'s order, the line saying how
    many more counted at its longest, so the whole stays within."""
    size = sum(len(line) + 1 for line in found.lines())
    by_name = {group.name: group for group in found.groups}
    for name in CUT:
        group = by_name.get(name)
        if size <= ROOM or group is None or not group.rows:
            continue
        more = len(Group(name, cut=len(group.rows) + group.cut).lines()[-1]) + 1
        while size > ROOM and group.rows:
            size -= len(group.rows.pop()) + 1
            group.cut += 1
            size += more if group.cut == 1 else 0
    return found


def lines(found: Summary) -> list[str]:
    """What `docket sync summary` prints."""
    return found.lines()
