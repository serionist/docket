"""`docket sync reconcile <sync>` (DR-0760): the sync's batch drafted from its blocks into `draft.jsonl` in the sync
folder by fixed rules, and a report. It writes the draft and nothing else: the batch stays Claude's.

Every locator is resolved first, as `read` resolves it, through one index for the whole run, and a line whose locator
doesn't open is dropped and listed; the ledger is read fresh. The ops come in the sync's order. One the rules can't
settle carries `unsure`, a key the writer refuses, so it can't be written unsettled: above it, `# ?` lines quote its
block line and what settling it needs, so Claude never asks `show` for it (#272): each candidate's `context.md` line
with its due and its latest log line, a point or an outcome the op would change, whole, the meeting's points, the
stream's line; below it, a `#` line per alternative. Claude settles each with `docket sync settle`, keeping the op,
taking an alternative or dropping it, or edits it.

    # docket sync reconcile sync-20261020T071500-1a2b3c4d: 4 ops, 1 unsure
    # closes
    {"op": "done", "item": "A-0001", "note": "slack:C0PILOT/1791100500.000200", "outcome": "Sent Ben the dates"}
    # asks
    # ? blocks/slack.txt:4 OWED: Dan Example | slack:C0PILOT/1791101200.000100 | 2026-10-19 11:05 | ...
    # ? A-0003 | task | Dan Example | Book the room for the pilot | slack:... @M-0002 | last 10-13: Dan: by Friday
    {"op": "add", "fields": {...}, "unsure": "a look-alike of A-0003 by who and title: ..."}
    # {"op": "log", "item": "A-0003", "note": "Dan: Book the room for the pilot's first day: slack:..."}
    {"op": "rollover"}
    # prep: the next five working days: M-0002
    #   M-0002 | 10-26 10:00-10:30 | Pilot weekly | Ben Sample
    #     M-0002#1 open | Dan: is the room booked? -> A-0003 (proposed) | 01M4QJ9FZ000X93GNMW1YNY5N9
    {"op": "archive"}

A thread counts only for the same who and side, and never where a record's thread is its container (a Teams chat, a
recording), which everyone in it shares. Two lines are one ask only with the same who, side and look-alike title:
surely so only by the same continues ref, never by a shared locator alone, and never either side of a close of the item
they continue.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from docket import recall
from docket.index import build as index_build
from docket.index.ledger import name_tokens
from docket.ledger import day as ledger_day
from docket.ledger import views
from docket.ledger.ops import STREAM_FIELDS
from docket.ledger.store import alike, locator_rid, title_words
from docket.sync import block, folder
from docket.vault.records import RawRecord
from docket.vault.vault import Vault

DRAFT = "draft.jsonl"
OWES = ("reply", "decision", "task")
OPEN = ("open", "snoozed")
MINUTES = ("answered", "dropped", "open")
# What a MEETINGS line sets, as agent.md says: a calendar's records set the rest, and the minutes the outcome.
MEETING_SETS = ("response", "attendees", "start", "end")
# An ask links to a meeting it was asked in or up to 120 minutes after, from 10 minutes before its start (DR-0651); a
# recording to one 10 minutes either side of its start.
ASKED_BEFORE = timedelta(minutes=10)
ASKED_AFTER = timedelta(minutes=120)
RECORDED = timedelta(minutes=10)
PREP_DAYS = 5
# Each label's locator, by its field's place.
LOCATOR = {"OWED": 1, "WAITING": 1, "INVITES": 1, "CLOSES": 2, "MEETINGS": 2, "STREAMS": 3, "NOTES": 1, "MINUTES": 4,
           "OUTCOME": 2}  # fmt: skip
GOES_WITH = "goes with the op above: keep it with that op, else delete it"
NONE = "-"
# What a quote of the ledger's text keeps: a log line on a candidate's line, and an answer or an outcome whole up to
# the 2,000 characters at which the Read tool cuts a line.
LAST = 120
QUOTE = 1_000
# A MINUTES line's point given by its number, as `1` or `#1`, instead of its id.
POINT_NUMBER = re.compile(r"^#?(\d{1,3})$")


@dataclass(frozen=True)
class Owner:
    """The owner as a meeting's people name them: their name's words and their addresses."""

    tokens: set[str]
    emails: list[str]

    @classmethod
    def of(cls, vault: Vault) -> Owner:
        owner = vault.config.owner
        return cls(name_tokens(owner.name), [e.lower() for e in owner.emails])

    def is_owner(self, person: str) -> bool:
        text = person.lower()
        return any(email in text for email in self.emails) or (bool(self.tokens) and self.tokens <= name_tokens(person))

    def own(self, m: views.MeetingFacts) -> bool:
        """The owner's own block: no one but the owner in it, and the owner organising it."""
        people = [p for p in [m.row.organiser, *m.attendees] if p]
        if any(not self.is_owner(p) for p in people):
            return False
        return bool(people) or m.row.response == "organiser"


def overlapping(
    meetings: Sequence[views.MeetingFacts], since: datetime, until: datetime, owner: Owner
) -> list[views.MeetingFacts]:
    """The meetings overlapping a window that a link may take: not cancelled, and not the owner's own block."""
    return [
        m for m in meetings
        if m.row.start < until and m.row.end > since and m.row.response != "cancelled" and not owner.own(m)
    ]  # fmt: skip


def recorded_in(meetings: Sequence[views.MeetingFacts], start: datetime, owner: Owner) -> list[views.MeetingFacts]:
    """The meetings a recording is of by time: those 10 minutes either side of its start (DR-0651), as a NOTES line's
    link and a recording agent's prompt both take them."""
    return overlapping(meetings, start - RECORDED, start + RECORDED, owner)


@dataclass(frozen=True)
class Found:
    """A block line and the file it came from, by its path in the sync folder: `blocks/slack.txt`, or a recording's
    `blocks/plaud/rec-0101.txt`."""

    file: str
    line: block.Line

    @property
    def quoted(self) -> str:
        """`blocks/slack.txt:4 OWED: Ben Sample | ...`, as the draft and the report quote it."""
        return f"{self.where} {self.text}"

    @property
    def where(self) -> str:
        return f"{self.file}:{self.line.n}"

    @property
    def text(self) -> str:
        return f"{self.line.label}: {block.SPLIT.join(self.line.fields)}"

    def field(self, k: int) -> str:
        return self.line.fields[k] if k < len(self.line.fields) else ""

    def value(self, k: int) -> str | None:
        """A field that may say nothing: `-`, or missing from an older line's shape, is None."""
        text = self.field(k)
        return None if text in ("", NONE) else text

    @property
    def locator(self) -> str:
        return self.field(LOCATOR[self.line.label])


@dataclass
class Draft:
    """An op of the draft and its line: why it is unsure, the `# ?` lines above it (its block line, then the
    candidates), the alternatives below it, and the ops that go with it, such as a set of the due after a log. A line
    whose shape is wrong makes it unsure from the start, since a field may have moved."""

    op: dict[str, Any]
    found: Found
    why: list[str] = field(default_factory=list[str])
    quoted: list[str] = field(default_factory=list[str])
    alternatives: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    then: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    def __post_init__(self) -> None:
        if self.found.line.problem is not None:
            self.unsure(f"{self.found.line.problem}, so the fields may have moved: fix the op from the block line")

    def unsure(self, why: str, *quoted: str) -> None:
        self.why.append(why)
        self.quote(*quoted)

    def quote(self, *quoted: str) -> None:
        """More `# ?` lines, its block line always the first."""
        self.quoted += [q for q in dict.fromkeys((self.found.quoted, *quoted)) if q not in self.quoted]


@dataclass(frozen=True)
class Merge:
    """Lines drafted as one ask, and whether surely so."""

    lines: list[str]
    sure: bool


@dataclass(frozen=True)
class Reconciled:
    """What `docket sync reconcile` wrote and says: the draft's path, its ops, its unsure lines by number, and what the
    report lists."""

    path: Path
    ops: int
    unsure: list[int]
    runs: list[str]
    merges: list[Merge]
    dropped: list[str]
    skipped: list[str]
    said: list[str]
    learned: list[str]
    prep: list[str]


@dataclass(frozen=True)
class _Ask:
    """An OWED or WAITING line as the reconcile reads it; an older line's shape lacks the fields after its sixth."""

    found: Found
    side: str
    who: str
    title: str
    day: date | None
    when: datetime | None
    kind: str | None
    stream: str | None
    continues: str | None
    due: str | None
    why: str | None
    today: bool

    @property
    def key(self) -> str:
        return _who(self.who)


def _who(text: str) -> str:
    return " ".join(text.split()).lower()


def _json(op: dict[str, Any]) -> str:
    return json.dumps(op, ensure_ascii=False)


def _one_line(text: str) -> str:
    """An outcome's lines as one quoted line, joined by ` / ` as an OUTCOME line writes them, cut."""
    return views.as_field(views.cut(" / ".join(part.strip() for part in text.splitlines() if part.strip()), QUOTE))


def _moment(text: str, tz: ZoneInfo) -> tuple[date | None, datetime | None]:
    """The day an ask was made, `YYYY-MM-DD` with its local `HH:MM` after it or not: the day, and the time when
    given."""
    day_part, _, time_part = text.strip().replace("T", " ", 1).partition(" ")
    try:
        day = date.fromisoformat(day_part)
    except ValueError:
        return None, None
    try:
        hour, minute = time_part.strip().split(":")[:2]
        return day, datetime(day.year, day.month, day.day, int(hour), int(minute), tzinfo=tz)
    except ValueError:
        return day, None


def _is_day(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return len(text) == len("YYYY-MM-DD")


def read_blocks(path: Path, info: folder.Info) -> tuple[list[Found], list[str]]:
    """Every block's lines, each source's and then its recordings', in the sources' order, and each block file that
    isn't there, said."""
    out: list[Found] = []
    said: list[str] = []
    for source in info.sources:
        files = [(f"{folder.BLOCKS}/{source.source}.txt", source.source)]
        files += [
            (f"{folder.BLOCKS}/{source.source}/{r.key}.txt", f"{r.rid}'s agent")
            for r in folder.recordings(path, source.source)
        ]
        for rel, whose in files:
            file = path / rel
            if not file.is_file():
                said.append(f"{rel} isn't there: no block from {whose}")
                continue
            out += [Found(rel, line) for line in block.parse(file.read_bytes().decode("utf-8", "replace"))]
    return out, said


def reconcile(vault: Vault, sync: str, at: datetime) -> Reconciled:
    """Draft the sync's batch into `draft.jsonl`, replacing one there, and say what the report lists. A sync of another
    vault, or none on this machine, is refused before anything is read. The ledger's tables are held while the lines'
    locators are asked of the index, as any ledger read holds them."""
    path, info = folder.find(sync, vault.root)
    found, said = read_blocks(path, info)
    drafting = _Drafting(vault, info, said, at)
    with index_build.ledger_tables(vault) as conn, recall.Lookup(vault) as lookup:
        drafting.resolve(found, lookup)
        drafting.read(conn, lookup)
    drafting.draft()
    return drafting.write(path / DRAFT)


class _Drafting:
    """One reconcile: the lines, the records their locators name, the ledger as read, and the draft as it grows."""

    def __init__(self, vault: Vault, info: folder.Info, said: list[str], at: datetime) -> None:
        self.info = info
        self.tz = ZoneInfo(info.zone)
        self.at = at
        self.today = at.astimezone(self.tz).date()
        until = self.today
        for _ in range(PREP_DAYS):
            until = ledger_day.next_working_day(until)
        self.prep_until = datetime(until.year, until.month, until.day, tzinfo=self.tz) + timedelta(days=1)
        self.owner = Owner.of(vault)
        self.said = said
        self.lines: list[Found] = []
        self.records: dict[str, RawRecord] = {}
        self.dropped: list[str] = []
        self.skipped: list[str] = []
        self.merges: list[Merge] = []
        self.ledger = views.SyncRead(items=[], trails={}, meetings=[], named={}, points=[], streams=[])
        self.by_ref: dict[str, views.ItemRow] = {}
        self.by_who: dict[str, list[tuple[views.ItemRow, set[str]]]] = {}
        self.by_source: dict[str, list[views.ItemRow]] = {}
        # The items whose source is a record of a recording, by the recording's rid, and the recordings' rids: a
        # recording's records share it as their thread and container, and it is a record itself.
        self.by_recording: dict[str, list[views.ItemRow]] = {}
        self.recordings: set[str] = set()
        self.by_thread: dict[str, list[str]] = {}
        self.meetings: dict[str, views.MeetingFacts] = {}
        self.points: dict[str, views.PointRow] = {}
        self.streams: set[str] = set()
        self.closing: dict[str, tuple[Found, datetime | None]] = {}
        # Each meeting's outcome as the draft's lines set it so far, and the line that set it last.
        self.outcomes: dict[str, tuple[str, str]] = {}
        self.answered: set[str] = set()
        self.sections: dict[str, list[Draft]] = {"closes": [], "asks": [], "meetings": [], "streams": []}
        self.prep: list[views.MeetingFacts] = []
        # The adds drafted so far, by who, with their titles' long words and their block line.
        self.added: dict[str, list[tuple[set[str], str]]] = {}

    # -- what the lines name -------------------------------------------------------------------------------------------

    def resolve(self, found: list[Found], lookup: recall.Lookup) -> None:
        """Each locator through the one index, and the record it names: a line whose locator doesn't open is dropped,
        but one whose shape is wrong stays, to be drafted unsure, since its locator may be another field."""
        for f in found:
            if f.line.label in LOCATOR:
                if lookup.opens(f.locator):
                    record = lookup.record(locator_rid(f.locator))
                    if record is not None:
                        self.records[f.locator] = record
                elif f.line.problem is None:
                    self.dropped.append(f"{f.quoted}: {f.locator or 'its locator'} doesn't open")
                    continue
            self.lines.append(f)

    def read(self, conn: sqlite3.Connection, lookup: recall.Lookup) -> None:
        """The ledger fresh: every item, the open ones' trails and their threads, the meetings from the earliest ask to
        the prep week and those the lines name, the points named, the streams."""
        times = [self.at]
        for f in self.lines:
            if f.line.label in ("OWED", "WAITING"):
                times += [t for t in [_moment(f.field(2), self.tz)[1]] if t is not None]
            elif f.line.label == "NOTES" and f.locator in self.records:
                times.append(self.records[f.locator].ts)
        names = [f.field(0) for f in self.lines if f.line.label in ("MEETINGS", "MINUTES", "OUTCOME")]
        names += [ref for f in self.lines if f.line.label == "NOTES" and (ref := f.value(4)) is not None]
        self.ledger = views.sync_read(
            conn, self.tz, self.at, (min(times) - ASKED_AFTER, self.prep_until), meetings=names,
            events=[locator_rid(f.locator) for f in self.lines if f.line.label == "INVITES"],
            points=[f.field(1) for f in self.lines if f.line.label == "MINUTES"],
        )  # fmt: skip
        for row in self.ledger.items:
            self.by_ref[row.id] = row
            self.by_ref.setdefault(row.ref, row)
            self.by_source.setdefault(locator_rid(row.source), []).append(row)
            if row.state in OPEN:
                self.by_who.setdefault(_who(row.who), []).append((row, title_words(row.title)))
        trails = {item: [locator_rid(loc) for loc in trail] for item, trail in self.ledger.trails.items()}
        threads = lookup.threads([*(rid for rids in trails.values() for rid in rids), *self.by_source])
        for item, rids in trails.items():
            for thread in dict.fromkeys((threads.get(rid, (None, None))[0] or rid) for rid in rids):
                self.by_thread.setdefault(thread, []).append(item)
        shared = {container for thread, container in threads.values() if container is not None and thread == container}
        shared |= {r.container for r in self.records.values() if r.container is not None and r.thread == r.container}
        # A chat's records share it too, but a chat isn't a record: only a recording is.
        self.recordings = set(lookup.threads(shared))
        for rid, rows in self.by_source.items():
            recording = self.recording(*threads.get(rid, (None, None)))
            if recording is not None:
                self.by_recording.setdefault(recording, []).extend(rows)
        self.meetings = {m.row.id: m for m in self.ledger.meetings}
        self.points = {p.id: p for p in self.ledger.points}
        self.streams = {s.name for s in self.ledger.streams}

    # -- the draft -----------------------------------------------------------------------------------------------------

    def draft(self) -> None:
        """Every line's op, in the sync's order: closes, asks, meetings, links, the points' minutes and then the
        outcomes, then streams; rollover, prep and archive are the file's."""
        for f in self.lines:
            if f.line.label == "CLOSES":
                self.close(f)
        groups = self.group([self.ask(f) for f in self.lines if f.line.label in ("OWED", "WAITING")])
        heads = {id(group[0].found): group for group in groups}
        links: list[Draft] = []
        minutes: list[Draft] = []
        outcomes: list[Draft] = []
        for f in self.lines:
            label = f.line.label
            if label == "INVITES":
                self.sections["asks"].append(self.invite(f))
            elif id(f) in heads:
                self.sections["asks"].append(self.asked(heads[id(f)]))
            elif label == "MEETINGS":
                self.sections["meetings"].append(self.meeting_set(f))
            elif label == "NOTES":
                links += [d for d in [self.note(f)] if d is not None]
            elif label == "MINUTES":
                minutes.append(self.minutes(f))
            elif label == "OUTCOME":
                outcomes.append(self.outcome(f))
            elif label == "STREAMS":
                self.sections["streams"].append(self.stream_set(f))
        self.sections["meetings"] += links + minutes + outcomes
        self.prep = [
            m for m in self.ledger.meetings
            if self.at <= m.row.start < self.prep_until and m.row.response != "cancelled" and not m.row.archived
        ]  # fmt: skip

    def recording(self, thread: str | None, container: str | None) -> str | None:
        """The recording a record is of, by its thread and container: none for a record of no recording."""
        return container if container is not None and thread == container and container in self.recordings else None

    def sourced(self, locator: str) -> list[views.ItemRow]:
        """The items, in any state, whose source is the locator's rid, anchor aside, or another record of the
        recording it is of: a recording reported again cites its summary or its transcript as it likes."""
        found = {row.id: row for row in self.by_source.get(locator_rid(locator), [])}
        record = self.records.get(locator)
        recording = None if record is None else self.recording(record.thread, record.container)
        found |= {row.id: row for row in self.by_recording.get(recording or "", [])}
        return list(found.values())

    def item(self, ref: str | None) -> views.ItemRow | None:
        return None if ref is None else self.by_ref.get(ref.strip())

    def candidate(self, row: views.ItemRow, *, state: bool = False) -> str:
        """An item's `context.md` line, its title cut as there; with its state, as the ledger holds it now; then its
        due and its latest log line with its day, cut, so a line can be settled without a `show`."""
        text = views.short_item(row.model_copy(update={"title": views.cut(row.title)}))
        if state:
            text += f" | {row.state}" + (f" {row.closed}" if row.closed is not None else "")
        elif row.state == "snoozed" and row.until is not None:
            text += f" | until {views.short_day(row.until, self.today)}"
        if row.due is not None:
            text += f" | due {views.short_day(row.due, self.today)}"
        last = self.ledger.logs.get(row.id)
        if last is not None:
            text += f" | last {views.short_day(last[0], self.today)}: {views.as_field(views.cut(last[1], LAST))}"
        return text

    def point_line(self, point: views.PointRow) -> str:
        """A point whole, as a quote gives it: `M-0001#2 answered | The budget -> A-0006 | answer: 40k | <id>`."""
        text = f"{point.ref} {point.state} | {views.as_field(point.text)}"
        text += f" -> {point.item}" if point.item else ""
        text += " (proposed)" if point.proposed else ""
        text += f" | answer: {views.as_field(views.cut(point.answer, QUOTE))}" if point.answer else ""
        return f"{text} | {point.id}"

    def stream_line(self, name: str) -> str:
        """A stream's line whole, as it stands; one the ledger doesn't hold, the names of those it does."""
        stream = next((s for s in self.ledger.streams if s.name == name), None)
        if stream is None:
            return f"streams the ledger holds: {', '.join(sorted(self.streams)) or 'none'}"
        return views.short_stream(stream, self.today)

    def meeting_line(self, m: views.MeetingFacts) -> str:
        return views.short_meeting(m.row, self.today)

    # -- closes --------------------------------------------------------------------------------------------------------

    def close(self, f: Found) -> None:
        """A done, or a drop when moot, sure on an item open or snoozed now; one already closed is skipped and said."""
        ref, how, said = f.field(0), f.field(1), f.field(3)
        if how == "moot":
            d = Draft({"op": "drop", "item": ref, "note": f"{said}: {f.locator}" if said else f.locator}, f)
        else:
            d = Draft({"op": "done", "item": ref, "note": f.locator, "outcome": said}, f)
            if how != "done":
                d.unsure(f"{how or 'its second field'} isn't done or moot: say which")
        row = self.item(ref)
        if row is None:
            d.unsure(f"{ref or 'its ref'} names no item: fix the ref, or delete the line")
        elif row.state not in OPEN:
            self.skipped.append(f"{f.quoted}: {row.ref} is {row.state} already")
            return
        elif row.id in self.closing:
            self.skipped.append(f"{f.quoted}: {row.ref} closes on {self.closing[row.id][0].where} already")
            return
        else:
            record = self.records.get(f.locator)
            self.closing[row.id] = (f, None if record is None else record.ts)
        self.sections["closes"].append(d)

    # -- asks ----------------------------------------------------------------------------------------------------------

    def ask(self, f: Found) -> _Ask:
        """OWED: who, locator, asked, the ask, why, when, kind, stream, continues, due. WAITING: who, locator, since,
        what, stream, continues, due."""
        day, when = _moment(f.field(2), self.tz)
        if f.line.label == "OWED":
            kind, stream, continues, due = f.value(6), f.value(7), f.value(8), f.value(9)
            why, today = f.value(4), f.field(5) == "today"
        else:
            kind, stream, continues, due = "waiting", f.value(4), f.value(5), f.value(6)
            why, today = None, False
        return _Ask(
            found=f, side=f.line.label, who=f.field(0), title=f.field(3), day=day, when=when, kind=kind, stream=stream,
            continues=continues, due=due, why=why, today=today,
        )  # fmt: skip

    def relation(self, ask: _Ask, item: str) -> str | None:
        """Whether an ask came before or after the evidence a CLOSES line closes its item on; None when that can't be
        told: the same day with no time, or evidence with no time."""
        evidence = self.closing[item][1]
        if evidence is None:
            return None
        if ask.when is not None:
            return "before" if ask.when <= evidence else "after"
        on = evidence.astimezone(self.tz).date()
        if ask.day is None or ask.day == on:
            return None
        return "before" if ask.day < on else "after"

    def group(self, asks: list[_Ask]) -> list[list[_Ask]]:
        """The asks, the same ask twice as one group. A line whose shape is wrong stays alone."""
        groups: list[list[_Ask]] = []
        for ask in asks:
            home = None if ask.found.line.problem else next((g for g in groups if self.same(g[0], ask)), None)
            if home is None:
                groups.append([ask])
            else:
                home.append(ask)
        return groups

    def same(self, a: _Ask, b: _Ask) -> bool:
        """The same ask: the same who, side and look-alike title, and the same continues ref or none, on one side of a
        close of the item they continue."""
        if a.found.line.problem or (a.side, a.key, a.continues) != (b.side, b.key, b.continues):
            return False
        if not alike(title_words(a.title), title_words(b.title)):
            return False
        row = self.item(a.continues)
        if row is None or row.id not in self.closing:
            return True
        sides = (self.relation(a, row.id), self.relation(b, row.id))
        return sides[0] is not None and sides[0] == sides[1]

    def asked(self, group: list[_Ask]) -> Draft:
        """A group's op, its merge reported: sure when it is a log on the item its lines continue, else an add unsure
        that it is one ask."""
        d = self.decided(group)
        if len(group) > 1:
            self.merges.append(Merge([a.found.quoted for a in group], sure=d.op["op"] == "log"))
        return self.doubled(d, group[0].who, group[0].title)

    def doubled(self, d: Draft, who: str, title: str) -> Draft:
        """An add that looks like one drafted above it, by DR-0642's rule on any side: unsure, since the writer refuses
        it unless forced, its forced copy the alternative."""
        if d.op["op"] != "add":
            return d
        words = title_words(title)
        above = [where for theirs, where in self.added.get(_who(who), []) if alike(words, theirs)]
        if above:
            d.unsure(
                f"it looks like the add for {', '.join(above)}, which the writer refuses: force it if it is another "
                "ask, else delete the line"
            )
            d.alternatives.append(d.op | {"force": True})
        self.added.setdefault(_who(who), []).append((words, d.found.where))
        return d

    def decided(self, group: list[_Ask]) -> Draft:
        """A log on the open item the group continues, or an add, with what makes it unsure."""
        head = group[0]
        row = self.item(head.continues)
        if row is not None and row.state in OPEN:
            return self.after_close(group, row) if row.id in self.closing else self.continued(group, row)
        d = self.add(group)
        if head.continues is not None and row is None:
            d.unsure(f"it continues {head.continues}, which names no item: fix the ref, or keep the add")
        elif row is not None:
            d.unsure(
                f"it continues {row.ref}, which is {row.state}: keep the add for new work, or log it there",
                self.candidate(row, state=True),
            )
            d.alternatives.append(self.log(group, row))
        return d

    def after_close(self, group: list[_Ask], row: views.ItemRow) -> Draft:
        """An ask on an item a CLOSES line closes: a log when asked before the evidence, an add after."""
        head = group[0]
        side = self.relation(head, row.id)
        if side == "before":
            return self.unlike(Draft(self.log(group, row), head.found), group, row)
        d = self.add(group, beside=row.id)
        if side is None:
            d.unsure(
                f"{row.ref} closes on {self.closing[row.id][0].where} the same day and the ask says no time: keep the "
                "add if it came after, else log it there",
                self.candidate(row),
            )
            d.alternatives.append(self.log(group, row))
        return d

    def continued(self, group: list[_Ask], row: views.ItemRow) -> Draft:
        """A log on the open item the line continues, and a set of the due it moves; sure unless the thread holds
        another of the person's items."""
        head = group[0]
        d = self.unlike(Draft(self.log(group, row), head.found), group, row)
        due = self.due(group)
        if due is not None and not _is_day(due):
            d.unsure(f"the due, {due!r}, isn't YYYY-MM-DD: fix it")
        elif due is not None and due != (row.due.isoformat() if row.due else None):
            d.then.append({"op": "set", "item": row.ref, "fields": {"due": due}})
        others = [other for other in self.threaded(head) if other.id != row.id]
        if others:
            d.unsure(
                f"it continues {row.ref}, but its thread holds {', '.join(o.ref for o in others)} of {head.who}'s too: "
                f"keep the log on {row.ref}, or move it",
                self.candidate(row), *(self.candidate(o) for o in others),
            )  # fmt: skip
            d.alternatives += [self.log(group, o) for o in others]
        return d

    def unlike(self, d: Draft, group: list[_Ask], row: views.ItemRow) -> Draft:
        """A log on the item an ask's continues ref names is unsure when the ask's title isn't a look-alike of the
        item's by DR-0642's rule and nothing of its thread is in the item's trail: the agent may have named an item of
        the same people for a different ask. The add is its alternative."""
        head = group[0]
        if alike(title_words(head.title), title_words(row.title)) or row.id in self.backed(head):
            return d
        d.unsure(
            f"it continues {row.ref}, but its title doesn't look like that item's and nothing of its thread is in that "
            "item's trail: keep the log if it continues that work, else take the add",
            self.candidate(row),
        )
        d.alternatives.append(self.add(group, beside=row.id).op)
        return d

    def backed(self, ask: _Ask) -> set[str]:
        """The open items whose trail holds the ask's thread, or its locator's rid where the record has no thread;
        none where the record's thread is its container, which everyone in it shares."""
        thread = self.thread(ask)
        return set() if thread is None else set(self.by_thread.get(thread, []))

    def thread(self, ask: _Ask) -> str | None:
        """The thread an ask's locator is in, as the trails are read: the record's thread, else its rid; None where
        the record's thread is its container (a chat, a recording)."""
        record = self.records.get(ask.found.locator)
        if record is None:
            return locator_rid(ask.found.locator)
        if record.container is not None and record.thread == record.container:
            return None
        return record.thread or locator_rid(ask.found.locator)

    def threaded(self, ask: _Ask) -> list[views.ItemRow]:
        """The open items of the ask's who and on its side whose trail holds its thread; none where the record's
        thread is its container, which everyone in it shares."""
        thread = self.thread(ask)
        if thread is None:
            return []
        kinds = OWES if ask.side == "OWED" else ("waiting",)
        rows = (self.by_ref[item] for item in self.by_thread.get(thread, []))
        return [row for row in rows if row.kind in kinds and _who(row.who) == ask.key]

    def add(self, group: list[_Ask], beside: str | None = None) -> Draft:
        """An add for the group's ask: unsure for a field it lacks, a thread match, a look-alike, a locator an item
        already has as its source, and a merge without a continues ref; `beside`, an item a CLOSES line closes, is no
        match."""
        head = group[0]
        fields: dict[str, Any] = {"kind": head.kind, "title": head.title, "who": head.who, "stream": head.stream,
                                  "source": head.found.locator}  # fmt: skip
        if head.day is not None:
            fields["opened"] = head.day.isoformat()
        due = self.due(group)
        if due is not None or head.today:
            fields["due"] = due or self.info.today.isoformat()
        meetings = self.slots_of(head)
        if meetings:
            fields["meeting"] = meetings[0].row.ref
        others = [loc for loc in dict.fromkeys(a.found.locator for a in group) if loc != head.found.locator]
        note = "; ".join([*([head.why] if head.why else []), *([f"also {', '.join(others)}"] if others else [])])
        op = {"op": "add", "fields": {k: v for k, v in fields.items() if v is not None}}
        d = Draft(op | ({"note": note} if note else {}), head.found)
        if head.kind not in (OWES if head.side == "OWED" else ("waiting",)):
            d.unsure(f"its kind is {head.kind or 'missing'}: reply, decision or task")
        if head.stream is None:
            d.unsure("it names no stream: add one the ledger holds", self.stream_line(""))
        elif head.stream not in self.streams:
            d.unsure(f"the stream {head.stream} isn't one the ledger holds: name one it holds",
                     self.stream_line(head.stream))  # fmt: skip
        if head.day is None:
            d.unsure(f"the day asked, {head.found.field(2)!r}, isn't YYYY-MM-DD: add opened")
        if due is not None and not _is_day(due):
            d.unsure(f"the due, {due!r}, isn't YYYY-MM-DD: fix it")
        if len(meetings) > 1:
            d.unsure(
                f"asked in the time of {' and '.join(m.row.ref for m in meetings)}: keep the meeting it was asked in, "
                "or take it out",
                *(self.meeting_line(m) for m in meetings),
            )  # fmt: skip
            d.alternatives += [d.op | {"fields": d.op["fields"] | {"meeting": m.row.ref}} for m in meetings[1:]]
        self.matches(group, d, beside)
        return d

    def matches(self, group: list[_Ask], d: Draft, beside: str | None) -> None:
        """What makes an add unsure by the ledger: a thread match, a look-alike, a locator an item has as its source,
        and the same ask twice without a continues ref."""
        head = group[0]
        threaded = [row for row in self.threaded(head) if row.id != beside]
        if threaded:
            d.unsure(
                f"in the thread of {', '.join(row.ref for row in threaded)} of {head.who}'s, with no continues ref: "
                "log it there if it continues that work, else keep the add",
                *(self.candidate(row) for row in threaded),
            )  # fmt: skip
            d.alternatives += [self.log(group, row) for row in threaded]
        seen = {row.id for row in threaded} | {beside}
        alikes = [row for row in self.alikes(head) if row.id not in seen]
        if alikes:
            d.unsure(
                f"a look-alike of {', '.join(row.ref for row in alikes)} by who and title: log it there if it "
                "continues that work, else keep the add",
                *(self.candidate(row) for row in alikes),
            )  # fmt: skip
            d.alternatives += [self.log(group, row) for row in alikes]
        sourced = list({row.id: row for a in group for row in self.sourced(a.found.locator)}.values())
        if sourced:
            d.unsure(
                f"its locator is already the source of {', '.join(row.ref for row in sourced)}, as a recording or a "
                "message reported again brings: keep the add for a new ask, else delete the line",
                *(self.candidate(row, state=True) for row in sourced),
            )  # fmt: skip
        if len(group) > 1:
            d.unsure(
                f"{len(group)} lines with the same who and a look-alike title, drafted as one ask: keep it if they are "
                "one, else add the other too",
                *(a.found.quoted for a in group[1:]),
            )  # fmt: skip
            # Forced, since the writer refuses an add that looks like one before it in the batch.
            d.alternatives += [self.add([a], beside).op | {"force": True} for a in group[1:]]

    def alikes(self, ask: _Ask) -> list[views.ItemRow]:
        """DR-0642's look-alikes of the ask: the open items of its who whose title shares 60% of the shorter one's
        long words, on any side, as the writer refuses an add for."""
        words = title_words(ask.title)
        return [row for row, theirs in self.by_who.get(ask.key, []) if alike(words, theirs)]

    def due(self, group: list[_Ask]) -> str | None:
        """The due the group's latest ask names, if any names one."""
        named = [a for a in group if a.due is not None]
        latest = max(named, key=lambda a: (a.day or date.min, a.when or self.at), default=None)
        return None if latest is None else latest.due

    def log(self, group: list[_Ask], row: views.ItemRow) -> dict[str, Any]:
        head = group[0]
        first = (head.who.split() or [head.who])[0]
        locators = ", ".join(dict.fromkeys(a.found.locator for a in group))
        return {"op": "log", "item": row.ref, "note": f"{first}: {head.title}: {locators}"}

    def slots_of(self, ask: _Ask) -> list[views.MeetingFacts]:
        """The meetings an ask was made in by its time: someone in it, the organiser or an attendee whose name holds
        every word of the ask's who, asked during it or up to 120 minutes after (DR-0651)."""
        words = name_tokens(ask.who)
        if ask.when is None or not words:
            return []
        return [
            m for m in self.slots(ask.when - ASKED_AFTER, ask.when + ASKED_BEFORE)
            if any(words <= name_tokens(person) for person in [m.row.organiser or "", *m.attendees])
        ]  # fmt: skip

    def slots(self, since: datetime, until: datetime) -> list[views.MeetingFacts]:
        return overlapping(self.ledger.meetings, since, until, self.owner)

    # -- invites -------------------------------------------------------------------------------------------------------

    def invite(self, f: Found) -> Draft:
        """A log on the open invite with that rid; else an add of kind invite, titled from its meeting's subject for an
        event (DR-0646), else the record's title."""
        rid = locator_rid(f.locator)
        why, stream = f.field(3), f.value(4)
        for row in self.by_source.get(rid, []):
            if row.kind == "invite" and row.state in OPEN:
                return Draft({"op": "log", "item": row.ref, "note": f"{why}: {f.locator}"}, f)
        meeting = self.meetings.get(self.ledger.named.get(rid, ""))
        record = self.records.get(f.locator)
        title = meeting.row.subject if meeting is not None else (record.title if record is not None else None)
        fields = {"kind": "invite", "title": title, "who": f.field(0), "stream": stream, "source": f.locator}
        d = Draft({"op": "add", "fields": {k: v for k, v in fields.items() if v}, "note": why}, f)
        if not title:
            d.unsure("no title: neither a meeting nor the record gives one, so write one")
        if stream is None:
            d.unsure("it names no stream: add one the ledger holds", self.stream_line(""))
        elif stream not in self.streams:
            d.unsure(f"the stream {stream} isn't one the ledger holds: name one it holds", self.stream_line(stream))
        sourced = self.sourced(f.locator)
        if sourced:
            d.unsure(
                f"its rid is already the source of {', '.join(row.ref for row in sourced)}, as an invite left "
                "unanswered is reported again: keep the add for a new invite, else delete the line",
                *(self.candidate(row, state=True) for row in sourced),
            )  # fmt: skip
        return self.doubled(d, f.field(0), title or "")

    # -- meetings, links, minutes, streams -----------------------------------------------------------------------------

    def meeting(self, ref: str | None) -> views.MeetingFacts | None:
        return None if ref is None else self.meetings.get(self.ledger.named.get(ref.strip(), ""))

    @staticmethod
    def pairs(text: str, known: Sequence[str], d: Draft) -> dict[str, Any]:
        """`field=value; ...` as fields, `attendees`' names split by `, `; a pair that isn't one, or a field the object
        doesn't take, makes the op unsure."""
        out: dict[str, Any] = {}
        for pair in filter(None, (p.strip() for p in text.split(";"))):
            name, sep, value = (part.strip() for part in pair.partition("="))
            if not sep or name not in known:
                d.unsure(f"{pair!r} isn't field=value with a field of {', '.join(known)}")
                continue
            out[name] = [v.strip() for v in value.split(",") if v.strip()] if name == "attendees" else value
        return out

    def meeting_set(self, f: Found) -> Draft:
        """A set of the line's fields on its meeting, sure when the meeting stands and every field is one a MEETINGS
        line sets."""
        ref = f.field(0)
        d = Draft({"op": "set", "item": ref}, f)
        d.op |= {"fields": self.pairs(f.field(1), MEETING_SETS, d), "note": f.locator}
        if self.meeting(ref) is None:
            d.unsure(f"{ref or 'its ref'} names no meeting: fix the ref, or delete the line")
        return d

    def stream_set(self, f: Found) -> Draft:
        """A set of the line's fields on its stream, by name, sure when the stream stands; unsure, the stream's line
        quoted whole, or the streams the ledger holds."""
        name = f.field(0)
        d = Draft({"op": "set", "item": name}, f)
        d.op |= {"fields": self.pairs(f.field(1), STREAM_FIELDS, d), "note": f"{f.field(2)}: {f.locator}"}
        if name not in self.streams:
            d.unsure(f"{name or 'its stream'} isn't a stream the ledger holds: fix the name, or delete the line")
        if d.why:
            d.quote(self.stream_line(name))
        return d

    def note(self, f: Found) -> Draft | None:
        """The recording as a material on its meeting (DR-0651): the one 10 minutes either side of its start, or the
        line's M- ref of two. None is said, with no op, and so is a meeting that has the recording already."""
        rid = locator_rid(f.locator)
        record = self.records.get(f.locator)
        ref = f.value(4)
        named = self.meeting(ref)
        found = [] if record is None else recorded_in(self.ledger.meetings, record.ts, self.owner)
        if named is not None and any(m.row.id == named.row.id for m in found):
            found = [named]
        if named is None and not found:
            self.said.append(f"{f.quoted}: no meeting 10 minutes either side of its start, so no link")
            return None
        meeting = named or found[0]
        if rid in meeting.materials:
            self.said.append(f"{f.quoted}: {meeting.row.ref} has it already")
            return None
        d = Draft({"op": "material", "item": meeting.row.ref, "ref": rid, "label": "recording"}, f)
        if ref is not None and named is None:
            d.unsure(f"{ref} names no meeting: keep the one at its start, or fix the ref", self.meeting_line(meeting))
        elif named is not None and found != [named]:
            d.unsure(
                f"{named.row.ref} isn't at the recording's start: keep it if the content names it, else delete "
                "the line",
                *(self.meeting_line(m) for m in [named, *found]),
            )  # fmt: skip
        elif len(found) > 1:
            d.unsure(
                f"recorded in the time of {' and '.join(m.row.ref for m in found)}: keep the one its content names",
                *(self.meeting_line(m) for m in found),
            )  # fmt: skip
            d.alternatives += [d.op | {"item": m.row.ref} for m in found[1:]]
        return d

    def minutes(self, f: Found) -> Draft:
        """A point_set by point id, sure only on a point open on that meeting; else unsure, the point quoted whole, or
        the meeting's standing points where the line's point isn't one, and the op on the point its number names as
        the alternative when the line gave a number."""
        ref, point_id, state, answer = f.field(0), f.field(1), f.field(2), f.value(3)
        fields = {"state": state} | ({"answer": answer} if answer is not None else {})
        d = Draft({"op": "point_set", "item": ref, "point": point_id, "fields": fields, "note": f.locator}, f)
        if state not in MINUTES:
            d.unsure(f"{state or 'its state'} isn't answered, dropped or open")
        meeting = self.meeting(ref)
        point = self.points.get(point_id)
        if meeting is None:
            d.unsure(f"{ref or 'its ref'} names no meeting: fix the ref, or delete the line")
        elif point is None or point.meeting != meeting.row.ref:
            standing = [p for p in self.points.values() if p.meeting == meeting.row.ref]
            number = POINT_NUMBER.match(point_id)
            numbered = next((p for p in standing if number and p.n == int(number.group(1))), None)
            fix = f"take the alternative, {numbered.ref} by its id, or " if numbered is not None else ""
            d.unsure(
                f"{point_id or 'its point'} isn't a point standing on {meeting.row.ref}: {fix}fix it, or delete the "
                "line",
                *(self.point_line(p) for p in standing),
            )  # fmt: skip
            if numbered is not None:
                d.alternatives.append(d.op | {"point": numbered.id})
                point = numbered
        elif point.id in self.answered:
            d.unsure(f"{point.ref} is answered by an earlier line: keep one, or join them", self.point_line(point))
        elif point.state != "open":
            d.unsure(
                f"{point.ref} is {point.state} already: keep the change only if this answer is newer, else delete it",
                self.point_line(point),
            )  # fmt: skip
        if point is not None:
            self.answered.add(point.id)
        return d

    def outcome(self, f: Found) -> Draft:
        """A set of the meeting's outcome, its ` / ` made line breaks, sure only on a meeting with none yet; else
        unsure, the outcome there quoted whole, or the earlier line's, and the two joined as the alternative."""
        ref = f.field(0)
        text = "\n".join(part.strip() for part in f.field(1).split(" / ") if part.strip())
        d = Draft({"op": "set", "item": ref, "fields": {"outcome": text}, "note": f.locator}, f)
        meeting = self.meeting(ref)
        had: str | None = None
        if meeting is None:
            d.unsure(f"{ref or 'its ref'} names no meeting: fix the ref, or delete the line")
        elif meeting.row.id in self.outcomes:
            had, where = self.outcomes[meeting.row.id]
            d.unsure(
                f"{meeting.row.ref} has an outcome from {where}: keep one, or take the alternative, the two joined",
                f"{meeting.row.ref} outcome from {where}: {_one_line(had)}",
            )  # fmt: skip
        elif meeting.outcome:
            had = meeting.outcome
            d.unsure(
                f"{meeting.row.ref} has an outcome already: keep one, or take the alternative, the two joined",
                f"{meeting.row.ref} outcome: {_one_line(had)}",
            )  # fmt: skip
        if had is not None:
            d.alternatives.append(d.op | {"fields": {"outcome": f"{had}\n{text}"}})
        if meeting is not None:
            self.outcomes[meeting.row.id] = (had + "\n" + text if had else text, f.where)
        return d

    # -- the file and the report ---------------------------------------------------------------------------------------

    def prep_lines(self) -> list[str]:
        """Under the `# prep` line, each prep meeting's line and its open points, so the prep is chosen and worded
        without a `show`."""
        out: list[str] = []
        for m in self.prep:
            out.append(f"#   {self.meeting_line(m)}")
            out += [f"#     {self.point_line(p)}" for p in self.points.values()
                    if p.meeting == m.row.ref and p.state == "open"]  # fmt: skip
        return out

    def write(self, path: Path) -> Reconciled:
        """The draft, replacing one there, and what the report says."""
        drafts = [d for section in self.sections.values() for d in section]
        count = sum(1 + len(d.then) for d in drafts) + 2
        unsure_count = sum(1 + len(d.then) for d in drafts if d.why)
        out = [f"# docket sync reconcile {self.info.sync}: {count} ops, {unsure_count} unsure"]
        unsure = _section(out, "closes", self.sections["closes"])
        unsure += _section(out, "asks", self.sections["asks"])
        unsure += _section(out, "meetings, links, minutes", self.sections["meetings"])
        out.append(_json({"op": "rollover"}))
        out.append(f"# prep: the next five working days: {', '.join(m.row.ref for m in self.prep) or 'none'}")
        out += self.prep_lines()
        unsure += _section(out, "streams", self.sections["streams"])
        out.append(_json({"op": "archive"}))
        path.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
        return Reconciled(
            path=path, ops=count, unsure=unsure,
            runs=[f.text for f in self.lines if f.line.label == "RUN"], merges=self.merges, dropped=self.dropped,
            skipped=self.skipped, said=self.said, learned=[f.quoted for f in self.lines if f.line.label == "LEARNED"],
            prep=[self.meeting_line(m) for m in self.prep],
        )  # fmt: skip


def _section(out: list[str], title: str, drafts: list[Draft]) -> list[int]:
    """A section's lines onto the draft, and the numbers of its unsure lines."""
    if not drafts:
        return []
    out.append(f"# {title}")
    unsure: list[int] = []
    for d in drafts:
        out += [f"# ? {q}" for q in d.quoted]
        out.append(_json(d.op | ({"unsure": "; ".join(d.why)} if d.why else {})))
        unsure += [len(out)] if d.why else []
        out += [f"# {_json(alt)}" for alt in d.alternatives]
        for then in d.then:
            out.append(_json(then | ({"unsure": GOES_WITH} if d.why else {})))
            unsure += [len(out)] if d.why else []
    return unsure


def lines(done: Reconciled) -> list[str]:
    """What `docket sync reconcile` prints: the RUN lines, the draft and its unsure lines, every merge, the lines
    dropped and the closes skipped, what else was said, the LEARNED lines whole, and the meetings of the prep week."""
    out = list(done.runs)
    at = f", lines {', '.join(map(str, done.unsure))}" if done.unsure else ""
    out.append(f"draft: {done.path}: {done.ops} ops, {len(done.unsure)} unsure{at}")
    for merge in done.merges:
        out.append(f"merged{'' if merge.sure else ', unsure'}: {merge.lines[0]}")
        out += [f"  with {line}" for line in merge.lines[1:]]
    out += [f"dropped {line}" for line in done.dropped]
    out += [f"skipped {line}" for line in done.skipped]
    out += done.said
    out += [f"learned {line}" for line in done.learned]
    out.append(f"prep, the next five working days ({len(done.prep)})")
    out += [f"  {line}" for line in done.prep]
    return out
