"""Writing the ledger (DR-0610, DR-0612, DR-0613, DR-0618): the one writer of ops, a whole write or none of it.

A write runs under the vault lock. It brings the index's ledger tables up to date from the op files, or folds them in
memory when the index can't be had (DR-0616, DR-0619), and reads each item it touches from them, and each meeting and
stream it touches with the rest of its part (DR-0633), folding only those; gives a renumber to each item or meeting a
clash left without a number; checks each change against the ledger as the write leaves it, the way todo.py's verbs
check them; mints the ids and numbers; screens the text; then writes this machine's month file through a temp file and
a rename and commits it, with any op file a stopped write left. A change that can't be made refuses the whole write,
naming the change by its place and, for the text rules, the field, never the value.

    write(vault, [{"op": "add", "fields": {"kind": "reply", "title": "Send Ada the dates", "who": "Ada",
                   "stream": "pilot", "source": "chat:C0X/1"}},
                  {"op": "triage", "item": "A-0302", "verdict": "chase"}], by="claude", via="cli")

Changes name an item by its A- number or its id, a meeting by its M- number or an id of its adds, a stream by its
name. A number more than one item or meeting has held names none: it is named by its id until the clash is behind it.
Undo takes `of`, an op's id, or `batch`, a write's first op id; an undo also undoes what the op it undoes left idle,
done and drop counting as one kind (DR-0614), and for points only rolls and copies (DR-0624). An undo asked by batch
writes `of_batch`, that batch, on each undo op, so a reader can tell how it was asked (DR-0614).

Meetings, points and streams (#199): `meeting` adds one, or with an event the ledger holds sets what changed on the
add carrying that event, or with `continues` joins an unkeyed meeting (DR-0621); `stream` adds or sets one by name;
`point`, `point_set`, `keep` and `point_item` change a meeting's talking points; `rollover` rolls the open points of
the meetings that are due to the next of their series (DR-0624).

A write is a batch (#201): the changes `BATCH` names, each with `by` (DR-0639), `as` on one that makes something and
`$name` standing for its id in a later one (DR-0640); `done` and `drop` with an outcome, `notes` appending, `archive`
with no item archiving what is due (DR-0641), an add refused when it looks like an open item and a keyless meeting
when it doubles one (DR-0642). A point's n is its place when the batch began. A dry run goes on past a refusal and
lists each one, the changes naming a refused change's `$name` marked as hanging on it.

The calendar step (#200) writes through `write_calendar`, by sync and never refusing: each event record's meeting set
to what differs, the unkeyed meeting with its start and subject joined, or a new meeting, and nothing for a key whose
meeting the owner removed; an undo asked by batch or written by sync is as if never (DR-0647, DR-0649). In the
catch-up's own pass, which never sets, a held key's join carries the held meeting's values, an empty one as null. This
module also keeps the step's pending rids, in this clone's private folder.

A forget's apply writes its masks through `write_masks`, the one change a committed line takes (DR-0693); a write
commits an op file a stopped apply left masked, its lines' ids listed, as it commits one a stopped write left (DR-0696).
After a forget (DR-0696): a source, a ref or an anchor naming a rid forgotten whole or a listed legacy note's old path
is refused, naming the field, a hash check that keeps new ops from needing a mask; and on a shell, an item or a meeting
whose making op the list holds (DR-0694), a text field or a log is refused, naming its number, and any op's note and
outcome are written as `[forgotten]`, so a drop, an archive, a close with an outcome and a sync's due archives still
go. The rollover leaves a shell's points where they are, and a shell's point makes no item.
"""

from __future__ import annotations

import heapq
import re
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any, NoReturn, cast
from zoneinfo import ZoneInfo

from docket import console
from docket.errors import DocketError
from docket.index.build import ledger_tables
from docket.index.ledger import Held, Light
from docket.ledger import check, ops, status
from docket.ledger.fold import CLOSED, NUMBER, Folded, Item, family, fold_item, number_text, paused
from docket.ledger.meetings import Meeting, ObjectFold, Objects, Point, Stream, fold_objects, meeting_text
from docket.ledger.ops import (
    DAYS,
    FIELDS,
    KINDS,
    MEETING_FIELDS,
    MEETING_REQUIRED,
    OPS_DIR,
    REQUIRED,
    RESPONSES,
    STATES,
    STATUSES,
    STREAM_FIELDS,
    VERDICTS,
    By,
    Line,
    Op,
    Via,
)  # fmt: skip
from docket.vault import forgotten
from docket.vault.anchors import AnchorError, parse_anchor, parse_locator
from docket.vault.home import make_private
from docket.vault.neverstore import in_container, matches_text, names_person
from docket.vault.processed import DOC_TYPES
from docket.vault.redact import Redactor
from docket.vault.staging import write_replacing
from docket.vault.vault import UnmergedError, Vault

# The changes a caller asks for, and the keys each takes besides `op`. A renumber is the writer's own.
CHANGES: dict[str, tuple[str, ...]] = {
    "add": ("fields", "note"),
    "set": ("item", "fields", "note"),
    "log": ("item", "note"),
    "triage": ("item", "verdict", "note"),
    "snooze": ("item", "until", "note"),
    "done": ("item", "note"),
    "drop": ("item", "note"),
    "reopen": ("item", "note"),
    "material": ("item", "ref", "label", "remove", "note"),
    "archive": ("item", "note"),
    "undo": ("of", "batch", "note"),
    "meeting": ("fields", "note"),
    "stream": ("fields", "note"),
    "point": ("item", "fields", "note"),
    "point_set": ("item", "point", "fields", "note"),
    "keep": ("item", "note"),
    "point_item": ("item", "point", "fields", "note"),
    "rollover": ("note",),
}
# The changes a batch takes, and the keys each takes besides `op` (DR-0639): the writer's own, `notes` (a set of the
# whole text), `outcome` on a close, `force` on an add (DR-0642), `as` on the four that make something (DR-0640), and
# `by` on any. The names and keys are a door: they only gain (tests/fixtures/ledger-batch.json).
BATCH: dict[str, tuple[str, ...]] = {
    "add": ("fields", "note", "as", "force", "by"),
    "set": ("item", "fields", "note", "by"),
    "log": ("item", "note", "by"),
    "triage": ("item", "verdict", "note", "by"),
    "snooze": ("item", "until", "note", "by"),
    "done": ("item", "note", "outcome", "by"),
    "drop": ("item", "note", "outcome", "by"),
    "reopen": ("item", "note", "by"),
    "notes": ("item", "append", "set", "note", "by"),
    "material": ("item", "ref", "label", "remove", "note", "by"),
    "archive": ("item", "note", "by"),
    "undo": ("of", "batch", "note", "by"),
    "meeting": ("fields", "note", "as", "by"),
    "stream": ("fields", "note", "by"),
    "point": ("item", "fields", "note", "as", "by"),
    "point_set": ("item", "point", "fields", "note", "by"),
    "keep": ("item", "note", "by"),
    "point_item": ("item", "point", "fields", "note", "as", "by"),
    "rollover": ("note", "by"),
}
# A name `as` gives, and a `$name` naming it, read only in these keys of a change and of its fields (DR-0640).
NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
NAMED = re.compile(r"^\$([a-z][a-z0-9_]{0,31})$")
REFS = ("item", "point", "of")
FIELD_REFS = ("item", "meeting", "continues")
# todo.py's: an archive is due this long after a close or a meeting's start (DR-0641).
ARCHIVE_AFTER = timedelta(days=14)
# todo.py's look-alike: titles sharing this much of the shorter one's words of this many letters or more (DR-0642).
ALIKE_SHARE = 0.6
ALIKE_LETTERS = 4
WORD_RUN = re.compile(r"\w+")
MEETING_NUMBER = re.compile(r"^M-?(\d{1,9})$", re.IGNORECASE)
POINT_NUMBER = re.compile(r"^#?(\d{1,4})$")
SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
ONE_LINE = ("title", "who", "source", "subject", "organiser", "where", "attendee", "text", "answer", "next")
TEXTS = ("description", "notes", "outcome")
# Text is written redacted. Any other value can't hold a marker: a locator would stop finding its thing, and a kind,
# a stream, a verdict or a day would stop being one, so a redaction rule's match there refuses the write (DR-0618,
# DR-0627).
REDACTED = (
    "title", "who", "note", "notes", "description", "outcome", "label", "subject", "organiser", "where", "attendee",
    "text", "answer", "next",
)  # fmt: skip
LOCATORS = ("source", "ref", "event", "series")
# The locators that point at a record an item or a material rests on: refused into a never-store container (DR-0690).
POINTERS = ("source", "ref")
PEOPLE = ("who", "organiser", "attendee")
TIMES = ("start", "end")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
# Every control character but tab, LF and CR, then the BOM and the two non-characters that end the BMP. SO to US is
# written out a character at a time: as a range, code scanning reads it as a typo for a narrower one.
CONTROL = re.compile(
    "[\x00-\x08\x0b\x0c"
    "\x0e\x0f\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f"
    "\x7f-\x9f\ufeff\ufffe\uffff]"
)
SUMMARY = 60
# todo.py's: a meeting that is over rolls its open points a day after its end, or at once with an outcome (DR-0624).
ROLL_AFTER = timedelta(hours=24)
# An anchor anywhere in a value: one on a rid forgotten whole is refused (DR-0696).
ANCHOR = re.compile(r"\[r:[^\]\s]+\]")
# todo.py's series rule: one subject starting with the other names one series only from this many characters.
SERIES_PREFIX = 15
# A "Name: what" point turned into an item waits on Name when the name is this many words or fewer, as in todo.py.
NAME_WORDS = 3


class LedgerError(DocketError):
    """A write refused: nothing of it was written."""


class RefusedError(LedgerError):
    """One change refused, by its place and its op, and why; `refs` names the open items an add looks like, by id, so
    a page can show them beside Add anyway (DR-0661)."""

    def __init__(self, n: int, kind: str, why: str, refs: tuple[str, ...] = ()) -> None:
        super().__init__(f"change {n} ({kind}): {why}; nothing written")
        self.n, self.kind, self.why, self.refs = n, kind, why, refs


@dataclass(frozen=True)
class Refusal:
    """A change a dry run couldn't make: its place, its op and why; `on`, the refused change whose `$name` it names,
    when it hangs on one and wasn't checked."""

    n: int
    op: str
    why: str
    on: int | None = None


@dataclass(frozen=True)
class Written:
    """What a write wrote, or with a dry run would write: its ops in order, its commit, the number of each item and
    meeting it touched; then per change the ops it wrote (a write's renumbers are in `ops` only); each `as` name and
    the id it names; how each id an op or a name touched reads (A-0302, M-0040, M-0040#3, a stream's name); what the
    writer says of a change beyond its ops, by its place; a dry run's refusals; and what the render of the streams'
    STATUS.md files after it said, each file written, left or refused (DR-0675)."""

    ops: tuple[Op, ...]
    commit: str | None
    numbers: dict[str, int] = field(default_factory=dict[str, int])
    changes: tuple[tuple[Op, ...], ...] = ()
    names: dict[str, str] = field(default_factory=dict[str, str])
    labels: dict[str, str] = field(default_factory=dict[str, str])
    remarks: tuple[tuple[int, str], ...] = ()
    refused: tuple[Refusal, ...] = ()
    rendered: tuple[str, ...] = ()


@dataclass(frozen=True)
class Event:
    """A calendar record as the calendar step gives it to the writer (DR-0646, DR-0648): its rid, the meeting fields it
    carries, event among them, and whether only an add or a join may come of it, as in the catch-up's own pass
    (DR-0647)."""

    rid: str
    fields: dict[str, Any]
    adds_only: bool = False


@dataclass(frozen=True)
class Outcome:
    """What the calendar step made of one record (DR-0647, DR-0649): `added`, `joined`, `set`, `kept` (nothing to
    change), `idle` (its meeting is archived), `removed` (the owner removed its meeting) or `left out`, with why; the
    meeting as it reads, its subject and start as written, and the near misses an add has, as they read."""

    rid: str
    what: str
    meeting: str | None = None
    subject: str | None = None
    start: str | None = None
    why: str | None = None
    near: tuple[str, ...] = ()


@dataclass(frozen=True)
class CalendarWritten:
    """What a calendar step wrote, or with a dry run would write: its ops, its commit, an outcome per record, and the
    highest M- number it gave, 0 for none."""

    ops: tuple[Op, ...]
    commit: str | None
    outcomes: tuple[Outcome, ...]
    numbered: int = 0


LEFT_OUT = "left out"


class TextRefusedError(LedgerError):
    """A value the text rules refuse: why, naming the field and never the value."""


class TextRules:
    """The text rules every op's values pass (DR-0618, DR-0627), for the writer and the import alike: text redacted, a
    never-store keyword, a person on the people list in who, an organiser or an attendee, a source or a material's ref
    pointing into a never-store container (DR-0690), a locator or an anchor on what a forget took whole (DR-0696), or a
    redaction rule's match in a value that isn't text refused. `index` is the index's connection the containers are
    asked of, when the caller holds one. With `once`, the import's, an index that fails is asked no more and
    `unanswered` says so, for the caller to refuse once; else each value it can't check is refused."""

    def __init__(self, vault: Vault, index: sqlite3.Connection | None = None, *, once: bool = False) -> None:
        self.lists = vault.config.never_store
        self.redactor = Redactor.from_config(vault.config.redaction)
        self.index = index
        self.once = once
        self.unanswered = False
        # Whether `index` has the records table: a writer folding the op files in memory holds the ledger's alone.
        self.records: bool | None = None
        self.listed = forgotten.load(vault.root, vault.home)

    def forgotten(self, name: str, value: str) -> None:
        """Refuse a locator naming a rid forgotten whole or a listed legacy note's old path, and an anchor on such a
        rid in any value, naming the field: hashes on the list, never a value."""
        listed = self.listed
        if not (listed.rids or listed.old_paths):
            return
        if name in LOCATORS and _names_forgotten(value, listed):
            raise TextRefusedError(f"{name} names what a forget took; leave it out")
        for token in ANCHOR.findall(value) if "[r:" in value else ():
            try:
                rid = parse_anchor(token).rid
            except AnchorError:
                continue
            if rid is not None and listed.whole(rid):
                raise TextRefusedError(f"{name} anchors on what a forget took; leave it out")

    def screen(self, name: str, value: str, *, removing: bool = False) -> str:
        """A value as it may be written: text redacted, anything else as given; `TextRefusedError` when it may not. A
        material's removal adds no pointer, so `removing` skips the containers."""
        self.forgotten(name, value)
        lists = self.lists
        counts: Counter[str] = Counter()
        redacted = self.redactor.text(value, counts)
        if matches_text(value, lists) or matches_text(redacted, lists):
            raise TextRefusedError(f"{name} says something on the never-store keywords list; leave it out")
        if name in PEOPLE and names_person(value, lists):
            raise TextRefusedError(f"{name} names someone on the never-store people list; leave them out")
        if name in POINTERS and not removing and self.contained(name, value):
            raise TextRefusedError(f"{name} points into a never-store container; leave it out")
        if name in REDACTED:
            return redacted
        if counts:
            what = (
                "locator" if name in LOCATORS else "day" if name in (*DAYS, "until", "deadline")
                else "time" if name in TIMES else name
            )  # fmt: skip
            raise TextRefusedError(f"{name} matches the {min(counts)} redaction rule, and a {what} can't hold a marker")
        return value

    def contained(self, name: str, value: str) -> bool:
        """Whether a locator's rid sits in a container on the never-store list: by its own text up to each slash, which
        holds a Slack or Teams rid's conversation, then by the container and thread the index holds for it, which a
        mail rid doesn't carry."""
        lists = self.lists
        if not any(c.strip() for c in lists.containers):
            return False
        rid = locator_rid(value)
        parts = rid.split("/")
        if any(in_container("/".join(parts[:i]), lists) for i in range(1, len(parts) + 1)):
            return True
        return any(in_container(where, lists) for where in self.places(name, rid))

    def places(self, name: str, rid: str) -> list[str]:
        """The container and thread the index holds for a rid; none without the index. An index that doesn't answer
        refuses the value, since it can't be checked; with `once`, it is marked and asked no more."""
        if self.index is None or self.unanswered:
            return []
        try:
            if self.records is None:
                probe = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'records'"
                self.records = self.index.execute(probe).fetchone() is not None
            if not self.records:
                return []
            row = self.index.execute("SELECT container, thread FROM records WHERE rid = ?", (rid,)).fetchone()
        except sqlite3.Error:
            if self.once:
                self.unanswered = True
                return []
            raise TextRefusedError(
                f"{name} can't be checked against the never-store containers: the index didn't answer; try again"
            ) from None
        return [str(where) for where in (row or ()) if where]


def locator_rid(value: str) -> str:
    """The rid a locator names, as `read` takes one: brackets and a leading `r:` taken off, then the position the
    anchor grammar reads at its end (DR-0198), so a `@thread.v2` in a Teams chat's id stays. Anything that isn't a rid
    comes back as written."""
    text = value.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    text = text.removeprefix("r:")
    try:
        return parse_anchor(f"[r:{text}]").rid or text
    except AnchorError:
        return text


def _names_forgotten(value: str, listed: forgotten.Forgotten) -> bool:
    """Whether a locator names a rid forgotten whole, or a legacy note's old path a forget removed, by `read`'s
    reading of it (DR-0131)."""
    try:
        found = parse_locator(value, DOC_TYPES)
    except AnchorError:
        return False
    if found.kind == "record":
        return listed.whole(found.value)
    return found.kind == "path" and listed.legacy({found.value, found.value.replace("\\", "/")}, None)


def _texts(keys: Mapping[str, Any]) -> list[str]:
    """The keys and fields of an op a mask would change, but its note: what a shell refuses (DR-0696)."""
    out = [key for key in ("ref", "label") if keys.get(key) is not None]
    fields = cast(dict[str, Any], keys.get("fields") or {})
    masked = ops.masked_fields(fields)
    return out + [name for name, value in fields.items() if masked[name] != value]


def one_line(text: str) -> str:
    return " ".join(CONTROL.sub("", text).split())


def clean_text(text: str) -> str | None:
    """Free text as stored: line breaks as LF, no other control character, no trailing spaces, no blank lines at
    either end; None when nothing is left."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u2028", "\n").replace("\u2029", "\n")
    rows = [CONTROL.sub("", row).rstrip() for row in text.split("\n")]
    out = "\n".join(rows).strip("\n")
    return out if out.strip() else None


def write(
    vault: Vault,
    changes: Sequence[Mapping[str, Any]],
    *,
    by: By = "claude",
    via: Via = "cli",
    dry_run: bool = False,
    now: datetime | None = None,
) -> Written:
    """Make the changes, all of them or none, as one write by `by` through `via`, unless a change gives its own; with
    `dry_run`, check every change and say what would be written and what is refused, writing nothing. A write that
    changed a stream renders its STATUS.md once the lock is let go, and a render's failure never fails it
    (DR-0675)."""
    if by not in ops.BY or via not in ops.VIA:
        raise LedgerError(f"by is one of {', '.join(ops.BY)}, via one of {', '.join(ops.VIA)}")
    moment = now or datetime.now(UTC)
    with vault.writing():
        ops.folder(vault.root)
        # The tables are asked only while the write is planned: its commit runs with the index free.
        with ledger_tables(vault) as conn:
            plan = _Batch(vault, Held(conn), by, via, moment, dry_run=dry_run)
            plan.renumber()
            for n, change in enumerate(changes, start=1):
                plan.step(n, change)
            new = tuple(plan.new)
            written = plan.written(new)
            message = plan.message() if new else ""
            touched = plan.streams_touched()
        if dry_run or not new:
            return written
        written = replace(written, commit=commit_ops(vault, new, message))
        marked = status.mark(vault, touched)
    return replace(written, rendered=status.after_write(vault, touched, marked))


def write_calendar(
    vault: Vault,
    events: Sequence[Event],
    *,
    via: Via,
    dry_run: bool = False,
    now: datetime | None = None,
    taken: int = 0,
    renumber: bool = True,
    tables: sqlite3.Connection | None = None,
) -> CalendarWritten:
    """The calendar step's one write, by sync (DR-0647): each record's meeting added, joined or set, in the order
    given. It never refuses: a record it can't take is an outcome left out, with why, and the rest is written. A
    catch-up's dry run, whose chunks write nothing, gives `taken`, the highest M- number its chunks before gave, so
    this one numbers after them, and renumbers in its first chunk alone. A dry run may plan against `tables`, the
    ledger as a write not yet made would leave it, as the import's dry run asks (DR-0655)."""
    if via not in ops.VIA:
        raise LedgerError(f"via is one of {', '.join(ops.VIA)}")
    if tables is not None and not dry_run:
        raise LedgerError("only a dry run plans against tables of its own")
    moment = now or datetime.now(UTC)
    with vault.writing():
        ops.folder(vault.root)
        with ledger_tables(vault, tables) as conn:
            plan = _Write(vault, Held(conn), "sync", via, moment)
            plan.highest_meeting = max(plan.highest_meeting, taken)
            if renumber:
                plan.renumber()
            outcomes = tuple(plan.calendar(n, event) for n, event in enumerate(events, start=1))
            new = tuple(plan.new)
            message = plan.message() if new else ""
        numbered = max(plan.given_meetings, default=0)
        if dry_run or not new:
            return CalendarWritten(new, None, outcomes, numbered)
        return CalendarWritten(new, commit_ops(vault, new, message), outcomes, numbered)


def _is_roll(op: Op) -> bool:
    return op.op == "point_set" and (op.fields or {}).get("state") == "rolled"


def _is_copy(op: Op) -> bool:
    return op.op == "point" and bool((op.fields or {}).get("from"))


def _same_subject(a: str, b: str) -> bool:
    return " ".join(a.lower().split()) == " ".join(b.lower().split())


def _one_series(a: str, b: str) -> bool:
    """todo.py's: the same subject, or one starting with the other, 15 characters or more."""
    x, y = (" ".join(s.lower().split()) for s in (a, b))
    shorter, longer = sorted((x, y), key=len)
    return x == y or (len(shorter) >= SERIES_PREFIX and longer.startswith(shorter))


class _Write:
    """One write as it is planned: the ledger's tables it reads, the lines of each item it touched and of each meeting
    and stream it touched with the rest of its part, with its own ops added, and the numbers it gave."""

    def __init__(self, vault: Vault, held: Held, by: By, via: Via, now: datetime) -> None:
        self.vault = vault
        self.held = held
        self.highest_id = held.highest_id()
        self.highest_number = held.highest_number()
        self.highest_meeting = held.highest_meeting_number()
        self.by: By = by
        self.via: Via = via
        self.now = now
        self.zone = ZoneInfo(vault.config.owner.time_zone)
        self.day = now.astimezone(self.zone).date()
        self.at = now.astimezone(UTC).replace(microsecond=0)
        self.lines: dict[str, list[Line]] = {}
        self.new: list[Op] = []
        self.given: dict[int, str] = {}
        self.given_meetings: dict[int, str] = {}
        self.rules = TextRules(vault, held.conn)
        self.listed = self.rules.listed.ops
        # Whether each add an op named is a shell's (DR-0694).
        self.shells: dict[str, bool] = {}
        self.kind = ""
        # The change being made, which a refusal from deep in a write names.
        self.n = 0
        # The meetings' and streams' adds read so far, whole parts (DR-0633), and their fold; the streams' names as the
        # tables hold them, for the streams no part read holds.
        self.object_adds: set[str] = set()
        self._objects: Objects | None = None
        self.same: dict[str, set[str]] = {}
        self.names = held.streams()

    # -- what stands ------------------------------------------------------------------------------------------------

    def lines_of(self, item_id: str) -> list[Line]:
        """An item's lines: as the tables hold them, read once, with this write's ops added."""
        if item_id not in self.lines:
            self.lines[item_id] = self.held.lines(item_id)
        return self.lines[item_id]

    def fold(self, item_id: str) -> Folded:
        return fold_item(item_id, self.lines_of(item_id))

    def load(self, adds: Iterable[str]) -> None:
        """Read from the tables, once, the meetings and streams these adds belong to with the rest of their parts:
        what folding them needs, and nothing more (DR-0633)."""
        wanted = {add_id for add_id in adds if add_id not in self.object_adds}
        if not wanted:
            return
        with paused():
            found = self.held.part(wanted, known=self.object_adds)
            read = self.held.object_lines(found)
        for add_id in found:
            self.lines[add_id] = read.get(add_id, [])
        self.object_adds |= found
        # Folded at once, so each add read knows its object's adds before an op is written to it.
        self._objects = None
        self.objects()

    def objects(self) -> Objects:
        """The meetings and streams the write has read, as it leaves them, folded again after each op written to one,
        with the collector paused as `load` pauses it; and for each add the adds of its object, so an op's `after`
        sees them all."""
        if self._objects is None:
            with paused():
                self._objects = fold_objects({add_id: self.lines[add_id] for add_id in self.object_adds})
            self.same = {}
            for add_id, obj_id in self._objects.owner.items():
                self.same.setdefault(obj_id, set()).add(add_id)
                self.same[add_id] = self.same[obj_id]
        return self._objects

    def owner(self, add_id: str) -> str | None:
        """The meeting or stream an add belongs to, read from the tables when the write hasn't yet; None for an id no
        meeting or stream has as an add."""
        if add_id not in self.object_adds:
            if not self.held.is_object(add_id):
                return None
            self.load([add_id])
        return self.objects().owner.get(add_id)

    def stream_names(self) -> dict[str, str]:
        """Each stream's name with its id: the fold's for the streams the write has read, the tables' for the rest."""
        out = {name: stream_id for name, stream_id in self.names.items() if stream_id not in self.object_adds}
        if self.object_adds:
            out.update(self.objects().names)
        return out

    def by_event(self, event: str) -> Meeting | None:
        """The meeting holding an event, as the write leaves it."""
        self.load(self.held.keyed("meeting", event))
        objects = self.objects()
        return objects.meetings.get(objects.events.get(event, ""))

    def by_name(self, name: str) -> Stream | None:
        """The stream holding a name, as the write leaves it."""
        self.load(self.held.keyed("stream", name))
        objects = self.objects()
        return objects.streams.get(objects.names.get(name, ""))

    def number_of(self, item_id: str) -> int:
        mine = [number for number, named in self.given.items() if named == item_id]
        return max(mine) if mine else self.fold(item_id).claims[-1][1]

    def meeting_number(self, add_id: str) -> int:
        """The number of the meeting an add belongs to: its latest claim's, or for one every add of which is undone,
        the number of the add its id is."""
        folded = self.fold_of(add_id)
        return folded.claims[-1][1] if folded.claims else self.adds_line(folded.id).number or 0

    def standing(self, item_id: str) -> Item:
        """An item `resolve` named, which stands."""
        return cast(Item, self.fold(item_id).item)

    def fold_of(self, add_id: str) -> ObjectFold:
        """The meeting's or stream's fold an add belongs to."""
        objects = self.objects()
        return objects.folds[objects.owner[add_id]]

    def label(self, add_id: str) -> str:
        """How the commit message names what an op changed: an item's or a meeting's number, a stream's name."""
        if add_id not in self.object_adds:
            return number_text(self.number_of(add_id))
        folded = self.fold_of(add_id)
        if folded.kind == "stream":
            return str((self.adds_line(folded.id).fields or {}).get("name"))
        return meeting_text(self.meeting_number(add_id))

    def numbers(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for op in self.new:
            if op.item not in self.object_adds:
                out[op.item] = self.number_of(op.item)
            elif self.fold_of(op.item).kind == "meeting":
                out[op.item] = self.meeting_number(op.item)
        return out

    def streams_touched(self) -> dict[str, Stream | None]:
        """The streams this write's ops changed, by name, as the write leaves them: None for one that no longer
        stands, every add of it undone."""
        out: dict[str, Stream | None] = {}
        for op in self.new:
            if op.item not in self.object_adds:
                continue
            folded = self.fold_of(op.item)
            if folded.kind == "stream":
                out[str((self.adds_line(folded.id).fields or {}).get("name"))] = cast(Stream | None, folded.value)
        return out

    def refuse(self, n: int, why: str, refs: tuple[str, ...] = ()) -> NoReturn:
        raise RefusedError(n, self.kind, why, refs)

    def shell(self, item_id: str) -> str | None:
        """The number of the shell an op naming `item_id` lands on (DR-0694): an item whose add the forgotten list
        holds, or a meeting one of whose adds it holds; None for anything else, a stream among them."""
        listed = self.listed
        if not listed:
            return None
        meeting = item_id in self.object_adds
        found = self.shells.get(item_id)
        if found is None:
            # No add this write makes is listed and none joins a shell, so what an add is holds for the write.
            if meeting:
                folded = self.fold_of(item_id)
                found = folded.kind == "meeting" and not listed.isdisjoint(folded.adds)
            else:
                found = item_id in listed
            self.shells[item_id] = found
        if not found:
            return None
        return meeting_text(self.meeting_number(item_id)) if meeting else number_text(self.number_of(item_id))

    def on_shell(self, label: str, kind: str, keys: dict[str, Any]) -> dict[str, Any]:
        """An op's keys on a shell: a log or any other text refused, naming the shell; a note and an outcome written as
        the marker, so a drop, an archive, a close with an outcome and a sync's due archives still go, and a sync's
        batch closing a shell among other items never fails on it (DR-0696)."""
        if kind == "log":
            self.refuse(self.n, f"{label} is a shell a forget left; it takes no log")
        fields = cast(dict[str, Any], keys.get("fields") or {})
        if fields.get("outcome") is not None:
            keys = {**keys, "fields": {**fields, "outcome": ops.MARK}}
        texts = _texts(keys)
        if texts:
            self.refuse(self.n, f"{label} is a shell a forget left; it takes no {texts[0]}")
        return {**keys, "note": ops.MARK} if keys.get("note") is not None else keys

    def resolve(self, n: int, ref: object) -> str:
        """The item a change names, by its id or its A- number."""
        if not isinstance(ref, str) or not ref.strip():
            self.refuse(n, "it needs item, an A- number or an item's id")
        text = ref.strip()
        if ops.ULID.fullmatch(text):
            if self.fold(text).item is None:
                self.refuse(n, f"no item {text}")
            return text
        m = NUMBER.fullmatch(text)
        if m is None:
            self.refuse(n, "item is an A- number or an item's id")
        number = int(m.group(1))
        named = self.held.holders(number)
        if number in self.given and self.given[number] not in named:
            named.append(self.given[number])
        if not named:
            self.refuse(n, f"no item {number_text(number)}")
        if len(named) > 1:
            self.refuse(n, f"{number_text(number)} has been held by more than one item; name the item by its id")
        if self.fold(named[0]).item is None:
            self.refuse(n, f"{number_text(number)} was undone")
        return named[0]

    def resolve_any(self, n: int, ref: object) -> tuple[str, str]:
        """What a change names, as (kind, id): an item by its A- number or id, a meeting by its M- number or an id of
        any of its adds, a stream by its name."""
        if not isinstance(ref, str) or not ref.strip():
            self.refuse(n, "it needs item, an A- number or an item's id, an M- number or a stream's name")
        text = ref.strip()
        obj_id = self.owner(text) if ops.ULID.fullmatch(text) else None
        if obj_id is not None:
            objects = self.objects()
            if obj_id in objects.meetings:
                return "meeting", obj_id
            if obj_id in objects.streams:
                return "stream", obj_id
            self.refuse(n, f"{text} was undone")
        if MEETING_NUMBER.fullmatch(text):
            return "meeting", self.resolve_meeting(n, text).id
        if text in self.stream_names():
            return "stream", cast(Stream, self.by_name(text)).id
        if not (ops.ULID.fullmatch(text) or NUMBER.fullmatch(text)):
            if SLUG.fullmatch(text):
                self.refuse(n, f"no stream {text}")
            self.refuse(n, "item is an A- number or an item's id, an M- number or a meeting's id, or a stream's name")
        return "item", self.resolve(n, text)

    def resolve_meeting(self, n: int, ref: object) -> Meeting:
        """A standing meeting a change names, by its M- number or an id of its adds (DR-0613, DR-0621)."""
        if not isinstance(ref, str) or not ref.strip():
            self.refuse(n, "it needs item, a meeting's M- number or id")
        text = ref.strip()
        if ops.ULID.fullmatch(text):
            obj_id = self.owner(text)
            meeting = self.objects().meetings.get(obj_id or "")
            if meeting is None:
                self.refuse(n, f"no meeting {text}")
            return meeting
        m = MEETING_NUMBER.fullmatch(text)
        if m is None:
            self.refuse(n, "a meeting is named by its M- number or its id")
        number = int(m.group(1))
        # Every meeting the tables say holds it read, and folded again after each op this write writes, so the
        # holders are current, and only a meeting that stands holds a number.
        self.load(self.held.meeting_holders(number))
        objects = self.objects()
        named = objects.holders.get(number, ())
        label = meeting_text(number)
        if len(named) != 1:
            many = f"{label} has been held by more than one meeting; name it by its id"
            self.refuse(n, many if named else f"no meeting {label}")
        return objects.meetings[named[0]]

    # -- making ops -------------------------------------------------------------------------------------------------

    def emit(self, kind: str, item_id: str | None, **keys: Any) -> Op:
        """The next op of this write, added to its item's lines so the next change sees it."""
        # The first op is minted on the clock; each next one is the one before plus one (DR-0612).
        op_id = ops.encode(ops.decode(self.new[-1].id) + 1) if self.new else ops.mint(self.highest_id, self.now, 1)[0]
        item = item_id or op_id
        shell = self.shell(item) if item_id is not None else None
        if shell is not None:
            keys = self.on_shell(shell, kind, keys)
        mine = self.lines_of(item)
        seen = [line.op.id for add_id in self.same.get(item, {item}) for line in self.lines_of(add_id)]
        op = Op(
            schema_version=1, id=op_id, item=item, op=kind, day=self.day, at=self.at, machine=self.vault.machine,
            by=self.by, via=self.via, batch=self.new[0].id if self.new else op_id,
            after=max(seen) if seen else None, **keys,
        )  # fmt: skip
        mine.append(Line(op, op.text(), ops.rel_of(op), 0))
        self.new.append(op)
        if kind in ("meeting", "stream"):
            fields = op.fields or {}
            joined = fields.get("continues")
            group = self.same.get(joined, set()) if isinstance(joined, str) else set[str]()
            group.add(op.id)
            for add_id in group:
                self.same[add_id] = group
            self.object_adds.add(op.id)
            # A key's other adds, undone ones too, are one object with this one: their part is read with it.
            key = fields.get("event" if kind == "meeting" else "name")
            if isinstance(key, str):
                self.load(self.held.keyed(kind, key))
        if item in self.object_adds:
            self._objects = None
        return op

    def next_number(self) -> int:
        return max(self.highest_number, *self.given, 0) + 1

    def next_meeting_number(self) -> int:
        read = self.objects().highest_number if self.object_adds else 0
        return max(self.highest_meeting, read, *self.given_meetings, 0) + 1

    def renumber(self) -> None:
        """A new number for each item or meeting whose latest claim lost one to an earlier claim (DR-0613): the
        tables say which, having folded every meeting's claims."""
        self.kind = "renumber"
        for item_id in self.held.clashing():
            number = self.next_number()
            self.given[number] = item_id
            self.emit("renumber", item_id, number=number)
        for meeting_id in self.held.meeting_clashes():
            self.load([meeting_id])
            number = self.next_meeting_number()
            self.given_meetings[number] = meeting_id
            self.emit("renumber", meeting_id, number=number)

    # -- the text rules (DR-0618, DR-0627) --------------------------------------------------------------------------

    def screen(self, n: int, name: str, value: str, *, removing: bool = False) -> str:
        """A value as it may be written (`TextRules.screen`), or the write refused, naming the change."""
        try:
            return self.rules.screen(name, value, removing=removing)
        except TextRefusedError as e:
            self.refuse(n, str(e))

    def note(self, n: int, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            self.refuse(n, "note is text")
        text = one_line(value)
        return self.screen(n, "note", text) if text else None

    def text_value(self, n: int, name: str, value: str) -> str | None:
        text = one_line(value) if name in ONE_LINE else clean_text(value) if name in TEXTS else value
        return self.screen(n, name, text) if text else None

    def slug(self, n: int, name: str, value: str) -> str:
        if not SLUG.fullmatch(value):
            self.refuse(n, f"{name} is a slug: lowercase letters, digits and inner hyphens")
        return value

    def held_stream(self, n: int, value: str) -> str:
        """A stream an item or a meeting names: once the ledger holds a stream, one it holds (DR-0625)."""
        names = self.stream_names()
        if names and value not in names:
            self.refuse(n, f"no stream {value}; add it first, or name one of {', '.join(sorted(names))}")
        return value

    def value(self, n: int, name: str, value: object) -> str | None:
        """An item field's value as written, or None to clear it."""
        if value is None or value in ("", "-"):
            return None
        if not isinstance(value, str):
            self.refuse(n, f"{name} is text")
        if name == "kind" and value not in KINDS:
            self.refuse(n, f"kind is one of {', '.join(KINDS)}")
        if name == "stream":
            self.held_stream(n, self.slug(n, "stream", value))
        if name in DAYS and not _day(value):
            self.refuse(n, f"{name} is a day, YYYY-MM-DD")
        if name == "meeting":
            return self.resolve_meeting(n, value).id
        return self.text_value(n, name, value)

    def fields(self, n: int, given: object, *, adding: bool) -> dict[str, str | None]:
        if not isinstance(given, Mapping) or not given:
            self.refuse(n, "it needs fields")
        given = cast(Mapping[object, object], given)
        out: dict[str, str | None] = {}
        for name, raw in given.items():
            if name not in FIELDS:
                self.refuse(n, f"there is no field {name}; the fields are {', '.join(FIELDS)}")
            out[str(name)] = self.value(n, str(name), raw)
        if adding:
            missing = [name for name in REQUIRED if out.get(name) is None]
            if missing:
                self.refuse(n, f"an add needs {missing[0]}")
            return {name: value for name, value in out.items() if value is not None}
        cleared = [name for name in (*REQUIRED, "opened") if name in out and out[name] is None]
        if cleared:
            self.refuse(n, f"{cleared[0]} can't be cleared")
        return out

    def time(self, n: int, name: str, value: str) -> str:
        """A meeting's start or end as written: the local time with its offset (DR-0622). A time without one is the
        owner's local time, in vault.yaml's zone."""
        try:
            moment = datetime.fromisoformat(value.strip().replace(" ", "T", 1))
        except ValueError:
            self.refuse(n, f"{name} is a time, YYYY-MM-DDTHH:MM with or without its offset")
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=self.zone)
        offset = cast(timedelta, moment.utcoffset())
        sign = "-" if offset < timedelta(0) else "+"
        minutes = abs(int(offset.total_seconds())) // 60
        return self.screen(n, name, f"{moment:%Y-%m-%dT%H:%M}{sign}{minutes // 60:02d}:{minutes % 60:02d}")

    def meeting_value(self, n: int, name: str, value: object) -> Any:
        """A meeting field's value as written, or None to clear it."""
        if value is None or value in ("", "-"):
            return None
        if name == "attendees":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in cast(list[object], value)):
                self.refuse(n, "attendees is a list of names")
            names = [self.text_value(n, "attendee", v) for v in cast(list[str], value)]
            return [name for name in names if name] or None
        if not isinstance(value, str):
            self.refuse(n, f"{name} is text")
        if name in TIMES:
            return self.time(n, name, value)
        if name == "response":
            if value not in RESPONSES:
                self.refuse(n, f"response is one of {', '.join(RESPONSES)}")
            return self.screen(n, name, value)
        if name == "stream":
            return self.screen(n, name, self.held_stream(n, self.slug(n, "stream", value)))
        if name == "continues":
            return self.resolve_meeting(n, value).id
        if name in LOCATORS:
            return self.screen(n, name, one_line(value)) if one_line(value) else None
        return self.text_value(n, name, value)

    def meeting_fields(self, n: int, given: object) -> dict[str, Any]:
        if not isinstance(given, Mapping) or not given:
            self.refuse(n, "it needs fields")
        out: dict[str, Any] = {}
        for name, raw in cast(Mapping[object, object], given).items():
            if name not in MEETING_FIELDS:
                self.refuse(n, f"there is no meeting field {name}; the fields are {', '.join(MEETING_FIELDS)}")
            out[str(name)] = self.meeting_value(n, str(name), raw)
        return out

    def stream_fields(self, n: int, given: object) -> dict[str, str | None]:
        if not isinstance(given, Mapping) or not given:
            self.refuse(n, "it needs fields")
        out: dict[str, str | None] = {}
        for name, raw in cast(Mapping[object, object], given).items():
            if name not in STREAM_FIELDS:
                self.refuse(n, f"there is no stream field {name}; the fields are {', '.join(STREAM_FIELDS)}")
            key = str(name)
            if raw is None or raw in ("", "-"):
                out[key] = None
                continue
            if not isinstance(raw, str):
                self.refuse(n, f"{key} is text")
            if key == "name":
                out[key] = self.screen(n, key, self.slug(n, "name", raw))
            elif key == "status":
                if raw not in STATUSES:
                    self.refuse(n, f"status is one of {', '.join(STATUSES)}")
                out[key] = self.screen(n, key, raw)
            elif key == "deadline":
                if not _day(raw):
                    self.refuse(n, "deadline is a day, YYYY-MM-DD")
                out[key] = self.screen(n, key, raw)
            else:
                out[key] = self.text_value(n, key, raw)
        return out

    # -- the changes ------------------------------------------------------------------------------------------------

    def change(self, n: int, change: Mapping[str, Any]) -> None:
        """One of the writer's changes, its op and keys checked already against `BATCH` (`_Batch.batch_change`)."""
        kind = change.get("op")
        self.kind, self.n = str(kind), n
        note = self.note(n, change.get("note"))
        if kind == "add":
            self.add_item(n, self.fields(n, change.get("fields"), adding=True), note)
            return
        if kind == "undo":
            self.undo(n, change, note)
            return
        if kind in ("meeting", "stream", "rollover", "point", "point_set", "keep", "point_item"):
            getattr(self, kind)(n, change, note)
            return
        if kind in ("set", "log", "material", "archive"):
            what, obj_id = self.resolve_any(n, change.get("item"))
            if what != "item":
                self.object_change(n, cast(str, kind), what, obj_id, change=change, note=note)
                return
            item_id = obj_id
        else:
            item_id = self.resolve(n, change.get("item"))
        item = self.standing(item_id)
        label = number_text(self.number_of(item_id))
        closed = item.state in CLOSED
        if kind == "set":
            asked = self.fields(n, change.get("fields"), adding=False)
            changed = {name: value for name, value in asked.items() if _text(getattr(item, name)) != value}
            if changed:
                self.emit("set", item_id, fields=changed, note=note)
        elif kind == "log":
            if note is None:
                self.refuse(n, "a log needs a note")
            self.emit("log", item_id, note=note)
        elif kind == "triage":
            verdict = change.get("verdict")
            verdict = None if verdict in (None, "", "-") else verdict
            if verdict is not None and verdict not in VERDICTS:
                self.refuse(n, f"verdict is one of {', '.join(VERDICTS)}, or - to clear it")
            if verdict is not None:
                self.screen(n, "verdict", verdict)
            if closed:
                self.refuse(n, f"{label} is {item.state}; reopen it first")
            if item.state != "open" or item.triage != verdict:
                self.emit("triage", item_id, verdict=verdict, note=note)
        elif kind == "snooze":
            until = change.get("until")
            if not isinstance(until, str) or not _day(until) or date.fromisoformat(until) <= self.day:
                self.refuse(n, "snooze needs until, a day after today (YYYY-MM-DD)")
            self.screen(n, "until", until)
            if closed:
                self.refuse(n, f"{label} is {item.state}; reopen it first")
            # A snooze to the day it is snoozed to already changes nothing, as a triage to its verdict doesn't.
            if item.state != "snoozed" or item.until != date.fromisoformat(until):
                self.emit("snooze", item_id, until=date.fromisoformat(until), note=note)
        elif kind in ("done", "drop"):
            if closed:
                self.refuse(n, f"{label} is already {item.state}")
            if kind == "drop" and note is None:
                self.refuse(n, "a drop needs a note: why")
            self.emit(kind, item_id, note=note)
        elif kind == "reopen":
            if item.state == "open":
                self.refuse(n, f"{label} is already open")
            self.emit("reopen", item_id, note=note)
        elif kind == "material":
            self.material(n, item_id, item.materials, change, note)
        elif not closed or item.archived:
            now = "archived" if item.archived else item.state
            self.refuse(n, f"an archive takes a closed item that isn't archived, and {label} is {now}")
        else:
            self.emit("archive", item_id, note=note)

    def add_item(self, n: int, fields: dict[str, str | None], note: str | None) -> Op:
        number = self.next_number()
        op = self.emit("add", None, number=number, fields=fields, note=note)
        self.given[number] = op.id
        return op

    def material(
        self, n: int, item_id: str, materials: Sequence[Any], change: Mapping[str, Any], note: str | None
    ) -> None:
        raw, label, remove = change.get("ref"), change.get("label"), change.get("remove", False)
        if not isinstance(raw, str) or not raw.strip():
            self.refuse(n, "a material needs ref: a path, a URL or a locator")
        if not isinstance(remove, bool) or (label is not None and not isinstance(label, str)):
            self.refuse(n, "remove is true or false, and label is text")
        ref = self.screen(n, "ref", material_ref(self.vault, raw), removing=remove)
        text = self.screen(n, "label", one_line(label)) if label and one_line(label) else None
        held = next((m for m in materials if m.ref == ref), None)
        if remove and held is None:
            self.refuse(n, "it has no material at that ref")
        if not remove and held is not None and held.label == text:
            self.refuse(n, "it has that material already")
        self.emit("material", item_id, ref=ref, label=None if remove else text, remove=remove, note=note)

    # -- meetings, streams and points (#199) ------------------------------------------------------------------------

    def meeting(self, n: int, change: Mapping[str, Any], note: str | None) -> None:
        """A new meeting; with an event the ledger holds, a set of what changed on the add carrying it; with
        `continues`, an add joining that unkeyed meeting to the event's (DR-0621)."""
        if self.archived_event(change.get("fields")):
            return
        fields = self.meeting_fields(n, change.get("fields"))
        event, continues = fields.get("event"), fields.get("continues")
        if continues is not None:
            if event is None:
                self.refuse(n, "continues needs an event: only a meeting with one continues a meeting")
            held = self.by_event(event)
            joined = self.objects().meetings[continues]
            shell = self.shell(continues)
            if shell is not None:
                self.refuse(n, f"{shell} is a shell a forget left; no meeting continues it")
            if joined.event is not None:
                self.refuse(n, f"{self.meeting_label(joined)} has an event; only a meeting without one is continued")
            if held is not None and not self.keyed(held.id):
                self.refuse(n, f"that event continues {self.meeting_label(held)} already")
            self.need_meeting_fields(n, fields)
            # The continued meeting's number goes on the add, so the object claims no new one (DR-0621).
            self.emit("meeting", None, number=joined.number or joined.claimed, fields=_present(fields), note=note)
            return
        held = self.by_event(event) if event is not None else None
        if held is None:
            self.need_meeting_fields(n, fields)
            if _when(fields["end"]) < _when(fields["start"]):
                self.refuse(n, "end is before start")
            number = self.next_meeting_number()
            op = self.emit("meeting", None, number=number, fields=_present(fields), note=note)
            self.given_meetings[number] = op.id
            return
        fields.pop("event")
        fields.pop("continues", None)
        self.set_meeting(n, held, fields, note, key_add=self.key_add(held))

    def archived_event(self, given: object) -> bool:
        """Whether a meeting change is an event's update of an archived meeting: its event names one and it continues
        none. That set is idle, so it is found before any field is checked: the writer writes nothing for it and
        refuses nothing, and the sync's write never fails on an old meeting (DR-0624)."""
        if not isinstance(given, Mapping):
            return False
        given = cast(Mapping[object, object], given)
        event, continues = given.get("event"), given.get("continues")
        if not isinstance(event, str) or continues not in (None, "", "-"):
            return False
        held = self.by_event(one_line(event))
        return held is not None and held.archived

    def need_meeting_fields(self, n: int, fields: dict[str, Any]) -> None:
        missing = [name for name in MEETING_REQUIRED if fields.get(name) is None]
        if missing:
            self.refuse(n, f"a meeting needs {missing[0]}")

    def keyed(self, add_id: str) -> bool:
        """Whether an add carries an event."""
        return any(
            bool((line.op.fields or {}).get("event")) for line in self.lines.get(add_id, []) if line.op.id == add_id
        )

    def key_add(self, meeting: Meeting) -> str:
        """The add a calendar record's ops name: the first standing one carrying the meeting's event (DR-0621)."""
        undone = self.objects().folds[meeting.id].undone
        return next(a for a in meeting.adds if a not in undone and self.keyed(a))

    def meeting_label(self, meeting: Meeting) -> str:
        return meeting_text(meeting.number or meeting.claimed)

    def set_meeting(self, n: int, meeting: Meeting, fields: dict[str, Any], note: str | None, *, key_add: str) -> None:
        """A set of what changed, on a meeting that isn't archived: a set on an archived one is idle, and its callers
        write nothing for it before they check a field (DR-0624)."""
        if "event" in fields or "continues" in fields:
            self.refuse(n, "event and continues are set only by a meeting's add")
        cleared = [name for name in MEETING_REQUIRED if name in fields and fields[name] is None]
        if cleared:
            self.refuse(n, f"{cleared[0]} can't be cleared")
        start = _when(fields["start"]) if fields.get("start") else meeting.start
        end = _when(fields["end"]) if fields.get("end") else meeting.end
        if end < start:
            self.refuse(n, "end is before start")
        changed = {name: value for name, value in fields.items() if _meeting_differs(meeting, name, value)}
        if changed:
            self.emit("set", key_add, fields=changed, note=note)

    def object_change(
        self, n: int, kind: str, what: str, obj_id: str, *, change: Mapping[str, Any], note: str | None
    ) -> None:
        """A set, log, material or archive on a meeting or a stream."""
        objects = self.objects()
        if what == "stream":
            stream = objects.streams[obj_id]
            if kind == "set":
                self.set_stream(n, stream, self.stream_fields(n, change.get("fields")), note)
            elif kind == "log":
                if note is None:
                    self.refuse(n, "a log needs a note")
                self.emit("log", obj_id, note=note)
            else:
                self.refuse(n, f"{kind} takes an item or a meeting, and {stream.name} is a stream")
            return
        meeting = objects.meetings[obj_id]
        label = self.meeting_label(meeting)
        if kind == "set":
            # Idle on an archived meeting, so nothing is checked and nothing refused (DR-0624).
            if not meeting.archived:
                self.set_meeting(n, meeting, self.meeting_fields(n, change.get("fields")), note, key_add=obj_id)
            return
        if kind == "log":
            if note is None:
                self.refuse(n, "a log needs a note")
            self.emit("log", obj_id, note=note)
            return
        if meeting.archived:
            self.refuse(n, f"{label} is archived; undo its archive first")
        if kind == "material":
            self.material(n, obj_id, meeting.materials, change, note)
            return
        still = len(meeting.open_points())
        if still:
            self.refuse(n, f"an archive takes a meeting with no open point, and {label} has {still}")
        self.emit("archive", obj_id, note=note)

    def stream(self, n: int, change: Mapping[str, Any], note: str | None) -> None:
        """A new stream, or a set of what changed on the one holding the name (DR-0625)."""
        fields = self.stream_fields(n, change.get("fields"))
        name = fields.get("name")
        if name is None:
            self.refuse(n, "a stream needs name")
        held = self.by_name(name)
        if held is not None:
            fields.pop("name")
            self.set_stream(n, held, fields, note)
            return
        if fields.get("status") is None:
            self.refuse(n, "a stream needs status")
        self.emit("stream", None, fields={k: v for k, v in fields.items() if v is not None}, note=note)

    def set_stream(self, n: int, stream: Stream, fields: dict[str, str | None], note: str | None) -> None:
        if "name" in fields:
            self.refuse(n, "a stream's name is its key and never changes")
        if "status" in fields and fields["status"] is None:
            self.refuse(n, "status can't be cleared")
        changed = {name: value for name, value in fields.items() if _text(getattr(stream, name)) != value}
        if changed:
            self.emit("set", stream.id, fields=changed, note=note)

    def open_meeting(self, n: int, ref: object) -> Meeting:
        meeting = self.resolve_meeting(n, ref)
        if meeting.archived:
            self.refuse(n, f"{self.meeting_label(meeting)} is archived; undo its archive first")
        return meeting

    def point_of(self, n: int, meeting: Meeting, ref: object) -> Point:
        """A standing point of the meeting, by its id or its n."""
        text = str(ref).strip() if isinstance(ref, (str, int)) and not isinstance(ref, bool) else ""
        label = self.meeting_label(meeting)
        m = POINT_NUMBER.fullmatch(text)
        if m is not None:
            k = int(m.group(1))
            if not 1 <= k <= len(meeting.points):
                self.refuse(n, f"{label} has {len(meeting.points)} point(s); no point {k}")
            return meeting.points[k - 1]
        found = next((p for p in meeting.points if p.id == text), None)
        if found is None:
            self.refuse(n, f"{label} has no point {text or '-'}; name one by its id or its number")
        return found

    def point(self, n: int, change: Mapping[str, Any], note: str | None) -> None:
        """A talking point on a meeting: its text, the item it comes from, and whether the sync proposes it."""
        meeting = self.open_meeting(n, change.get("item"))
        given = change.get("fields")
        if not isinstance(given, Mapping):
            self.refuse(n, "it needs fields")
        given = cast(Mapping[str, Any], given)
        extra = sorted(str(k) for k in given if k not in ("text", "item", "proposed"))
        if extra:
            self.refuse(n, f"a point's add takes text, item and proposed, not {', '.join(extra)}")
        raw = given.get("text")
        text = self.text_value(n, "text", raw) if isinstance(raw, str) else None
        if text is None:
            self.refuse(n, "a point needs text")
        if any(p.text == text for p in meeting.open_points()):
            self.refuse(n, f"{self.meeting_label(meeting)} has that point open already")
        fields: dict[str, Any] = {"text": text}
        if given.get("item") not in (None, "", "-"):
            fields["item"] = self.resolve(n, given.get("item"))
        proposed = given.get("proposed", False)
        if not isinstance(proposed, bool):
            self.refuse(n, "proposed is true or false")
        if proposed:
            fields["proposed"] = True
        self.emit("point", meeting.id, fields=fields, note=note)

    def point_set(self, n: int, change: Mapping[str, Any], note: str | None) -> None:
        """One change to a point: text, state, answer, item or proposed, with what todo.py infers written out
        (DR-0623)."""
        meeting = self.resolve_meeting(n, change.get("item"))
        point = self.point_of(n, meeting, change.get("point"))
        if point.state == "rolled":
            self.refuse(n, "that point rolled on; change it on the meeting it rolled to")
        if meeting.archived:
            self.refuse(n, f"{self.meeting_label(meeting)} is archived; undo its archive first")
        given = change.get("fields")
        if not isinstance(given, Mapping) or not given:
            self.refuse(n, "it needs fields")
        given = cast(Mapping[str, Any], given)
        out: dict[str, Any] = {}
        for name, raw in given.items():
            if name not in ("text", "state", "answer", "item", "proposed"):
                self.refuse(n, f"a point's fields are text, state, answer, item and proposed, not {name}")
            empty = raw is None or raw in ("", "-")
            if name == "proposed":
                if not isinstance(raw, bool) and raw is not None:
                    self.refuse(n, "proposed is true or false")
                out[name] = True if raw else None
            elif name == "item":
                out[name] = None if empty else self.resolve(n, raw)
            elif empty:
                if name in ("text", "state"):
                    self.refuse(n, f"{name} can't be cleared")
                out[name] = None
            elif not isinstance(raw, str):
                self.refuse(n, f"{name} is text")
            elif name == "state":
                if raw not in STATES or raw == "rolled":
                    self.refuse(n, "a point's state is open, answered or dropped; the rollover rolls one")
                out[name] = self.screen(n, name, raw)
            else:
                out[name] = self.text_value(n, name, raw)
        if out.get("answer") is not None and "state" not in out and point.state == "open":
            out["state"] = "answered"
        if (out.get("state") == "answered" or out.get("answer") is not None) and point.proposed:
            out.setdefault("proposed", None)
        changed = {name: value for name, value in out.items() if _point_differs(point, name, value)}
        if changed:
            self.emit("point_set", meeting.id, point=point.id, fields=changed, note=note)

    def keep(self, n: int, change: Mapping[str, Any], note: str | None) -> None:
        """Every proposed point of a meeting kept: a point_set each."""
        meeting = self.open_meeting(n, change.get("item"))
        for point in meeting.points:
            if point.proposed and point.state != "rolled":
                self.emit("point_set", meeting.id, point=point.id, fields={"proposed": None}, note=note)

    def point_item(self, n: int, change: Mapping[str, Any], note: str | None) -> None:
        """A point turned into an item hanging on its meeting, as todo.py's `point item`: "Name: what" waits on Name,
        anything else is a task; the point keeps a link to it."""
        meeting = self.open_meeting(n, change.get("item"))
        point = self.point_of(n, meeting, change.get("point"))
        shell = self.shell(meeting.id)
        if shell is not None:
            self.refuse(n, f"{shell} is a shell a forget left; its points make no item")
        if point.state == "rolled":
            self.refuse(n, "that point rolled on; change it on the meeting it rolled to")
        if point.item is not None:
            self.refuse(n, "that point is on an item already")
        raw: object = change.get("fields") or {}
        if not isinstance(raw, Mapping):
            self.refuse(n, "fields is kind, who or stream")
        given = cast(Mapping[str, Any], raw)
        extra = sorted(str(k) for k in given if k not in ("kind", "who", "stream"))
        if extra:
            self.refuse(n, f"a point's item takes kind, who and stream, not {', '.join(extra)}")
        who, sep, rest = point.text.partition(":")
        named = bool(sep) and 0 < len(who.split()) <= NAME_WORDS and bool(rest.strip())
        stream = given.get("stream") or meeting.stream
        if not stream:
            self.refuse(n, f"{self.meeting_label(meeting)} has no stream; give the item one")
        wanted = {
            "kind": given.get("kind") or ("waiting" if named else "task"), "title": point.text,
            "who": given.get("who") or (who.strip() if named else meeting.organiser or "me"), "stream": stream,
            "source": f"meeting:{meeting.id}", "meeting": meeting.id,
        }  # fmt: skip
        why = f"from a talking point of {self.meeting_label(meeting)}"
        item = self.add_item(n, self.fields(n, wanted, adding=True), note or why)
        fields: dict[str, Any] = {"item": item.id}
        if point.proposed:
            fields["proposed"] = None
        self.emit("point_set", meeting.id, point=point.id, fields=fields)

    def rollover(self, n: int, change: Mapping[str, Any], note: str | None) -> None:
        """The open points of each meeting that is due rolled to the next meeting of its series, as todo.py rolls them:
        a cancelled meeting at once, one that is over with an outcome, any other a day after its end. The meetings
        are taken by start, each from the ledger as the rolls before it left it: those the tables say are due with an
        open point, those the write has read with one, and each meeting a roll went to, which starts later. Only those
        are read."""
        heap = [
            (row.start, row.id) for row in self.held.with_open_points()
            if row.id not in self.object_adds and _due(self.now, row.end, row.outcome, row.response)
        ]  # fmt: skip
        if self.object_adds:
            heap += [(m.start, m.id) for m in self.objects().meetings.values() if m.open_points() and not m.archived]
        heapq.heapify(heap)
        done: set[str] = set()
        while heap:
            _, meeting_id = heapq.heappop(heap)
            obj_id = self.owner(meeting_id)
            meeting = self.objects().meetings.get(obj_id or "")
            if meeting is None or meeting.id in done:
                continue
            done.add(meeting.id)
            still = meeting.open_points()
            if not still or meeting.archived or not _due(self.now, meeting.end, meeting.outcome, meeting.response):
                continue
            # A shell's points are forgotten: the screen named each one to raise again (DR-0694).
            if self.shell(meeting.id) is not None:
                continue
            target = self.next_in_series(meeting)
            if target is None:
                continue
            for point in still:
                fields: dict[str, Any] = {"text": point.text, "from": point.id}
                if point.item is not None:
                    fields["item"] = point.item
                if point.proposed:
                    fields["proposed"] = True
                copy = self.emit("point", target.id, fields=fields, note=note)
                self.emit("point_set", meeting.id, point=point.id, fields={"state": "rolled", "to": copy.id}, note=note)
            heapq.heappush(heap, (target.start, target.id))

    def next_in_series(self, meeting: Meeting) -> Meeting | None:
        """The next meeting by start, not cancelled or declined: the same series when both have one, else todo.py's
        rule, the same subject, then one starting with the other with the same organiser (DR-0624). The meetings the
        write hasn't read are asked from the tables, and only the one chosen is read."""
        read = self.objects().meetings.values()
        later = sorted(
            [*(Light(m.id, m.start, m.series, m.subject, m.organiser) for m in read
               if m.id != meeting.id and m.start > meeting.start and m.response not in ("cancelled", "declined")
               and not m.archived),
             *(m for m in self.held.later_meetings(meeting.start) if m.id not in self.object_adds)],
            key=lambda m: (m.start, m.id),
        )  # fmt: skip
        chosen: Light | None = None
        if meeting.series is not None:
            chosen = next((m for m in later if m.series == meeting.series), None)
            if chosen is None:
                later = [m for m in later if m.series is None]
        if chosen is None:
            chosen = next((m for m in later if _same_subject(m.subject, meeting.subject)), None)
        if chosen is None:
            chosen = next(
                (m for m in later if _one_series(m.subject, meeting.subject)
                 and (not (m.organiser and meeting.organiser) or m.organiser == meeting.organiser)),
                None,
            )  # fmt: skip
        if chosen is None:
            return None
        obj_id = cast(str, self.owner(chosen.id))
        return self.objects().meetings[obj_id]

    # -- calendar records (#200) ------------------------------------------------------------------------------------

    def calendar(self, n: int, event: Event) -> Outcome:
        """One record's meeting (DR-0647 to DR-0649): its key's meeting set to what differs, an unkeyed meeting with
        its start and subject joined, or a new meeting; nothing for a key whose meeting the owner removed. What the
        text rules refuse is left out, with why, before anything is written."""
        self.kind, self.n = "calendar", n
        try:
            fields = self.meeting_fields(n, event.fields)
            self.need_meeting_fields(n, fields)
            if not fields.get("event"):
                self.refuse(n, "a calendar record's meeting needs its event")
            if _when(str(fields["end"])) < _when(str(fields["start"])):
                self.refuse(n, "end is before start")
        except RefusedError as e:
            return Outcome(event.rid, LEFT_OUT, why=e.why)
        key, subject, start = str(fields["event"]), str(fields["subject"]), str(fields["start"])
        # What the record may touch is read in one go, its key's adds, the meetings at its start and its day's unkeyed
        # ones, so the write folds what it has read once per record, not again for each read.
        when = _when(start)
        self.load([*self.held.keyed("meeting", key), *self.held.meetings_at(when), *self.held.unkeyed_on(when.date())])

        def said(what: str, meeting: str | None = None, near: tuple[str, ...] = (), why: str | None = None) -> Outcome:
            return Outcome(event.rid, what, meeting, subject, start, why, near)

        held = self.by_event(key)
        removed, split = self.undone_of(key)
        if held is not None:
            joined = self.join(key, held, fields, split, keep=event.adds_only)
            if joined is not None:
                return said("joined", joined)
            label = self.meeting_label(held)
            if event.adds_only:
                return said("kept", label)
            if held.archived:
                return said("idle", label)
            before = len(self.new)
            changes = {name: value for name, value in fields.items() if name != "event"}
            self.set_meeting(n, held, changes, None, key_add=self.key_add(held))
            return said("set" if len(self.new) > before else "kept", label)
        if removed:
            return said("removed")
        joined = self.join(key, None, fields, split)
        if joined is not None:
            return said("joined", joined)
        if fields.get("response") == "cancelled":
            return said(LEFT_OUT, why="cancelled, and no meeting holds it")
        number = self.next_meeting_number()
        op = self.emit("meeting", None, number=number, fields=_present(fields))
        self.given_meetings[number] = op.id
        return said("added", meeting_text(number), self.near_misses(fields))

    def undone_of(self, key: str) -> tuple[bool, set[str]]:
        """What the owner's undos of a key's adds meant, read by how each was asked (DR-0649): whether one asked by of
        removed its meeting, and the meetings a split took off it, never joined from it again. An undo asked by batch,
        its op carrying of_batch, or written by sync, is as if never."""
        adds = [*self.held.keyed("meeting", key), *(op.id for op in self.new if self.keyed(op.id))]
        removed = False
        split: set[str] = set()
        objects = self.objects()
        for add_id in dict.fromkeys(adds):
            add = self.adds_line(add_id)
            if (add.fields or {}).get("event") != key:
                continue
            folded = objects.folds[objects.owner[add_id]]
            undo_id = folded.undone.get(add_id)
            if undo_id is None:
                continue
            # Read from the meeting's lines: an undo may name any add of the key as its item (DR-0621).
            undo = next(line.op for line in folded.lines if line.op.id == undo_id)
            if undo.of_batch is not None or undo.by == "sync":
                continue
            joined = (add.fields or {}).get("continues")
            stands = objects.meetings.get(objects.owner.get(str(joined), "")) if joined else None
            if stands is not None:
                split.add(stands.id)
            else:
                removed = True
        return removed, split

    def join(
        self, key: str, held: Meeting | None, fields: dict[str, Any], split: set[str], *, keep: bool = False
    ) -> str | None:
        """A join of the unkeyed meeting with the record's start and subject, the earliest by id, unless the key
        continues one already or a split took that meeting off it (DR-0649): a meeting add with continues, the
        record's fields on it as on any add. In the catch-up's own pass (`keep`) a held key's join carries instead the
        held meeting's values of the fields the record gives, an empty one as null, so it sets nothing on it, and a
        field cleared by hand stays cleared over an imported add sorting later (DR-0647). The joined meeting, as it
        reads; None when there is none to join."""
        if held is not None and not self.keyed(held.id):
            return None
        start = _when(str(fields["start"]))
        self.load(self.held.meetings_at(start))
        found = sorted(
            m.id for m in self.objects().meetings.values()
            if m.event is None and m.start == start and _same_subject(m.subject, str(fields["subject"]))
            and m.id not in split
        )  # fmt: skip
        if not found:
            return None
        joined = self.objects().meetings[found[0]]
        # The continued meeting's number goes on the add, so the object claims no new one (DR-0621).
        number = joined.number or joined.claimed
        carried = _present(fields)
        if keep and held is not None:
            carried |= {name: _as_written(held, name) for name in fields if name != "event"}
        self.emit("meeting", None, number=number, fields={**carried, "continues": joined.id})
        return self.meeting_label(joined)

    def near_misses(self, fields: dict[str, Any]) -> tuple[str, ...]:
        """The unkeyed meetings a new meeting nearly joined, for joining by hand (DR-0649): at its start under another
        subject, or on its day under its subject at another start."""
        start = _when(str(fields["start"]))
        self.load([*self.held.meetings_at(start), *self.held.unkeyed_on(start.date())])
        subject = str(fields["subject"])
        near = [
            m for m in self.objects().meetings.values()
            if m.event is None and (
                (m.start == start and not _same_subject(m.subject, subject))
                or (m.day == start.date() and m.start != start and _same_subject(m.subject, subject))
            )
        ]  # fmt: skip
        return tuple(self.meeting_label(m) for m in sorted(near, key=lambda m: (m.start, m.id)))

    # -- undo -------------------------------------------------------------------------------------------------------

    def undo(self, n: int, change: Mapping[str, Any], note: str | None) -> None:
        of, batch = change.get("of"), change.get("batch")
        if (of is None) == (batch is None) or not isinstance(of or batch, str):
            self.refuse(n, "an undo needs of, an op's id, or batch, a write's first op id")
        if isinstance(of, str):
            found = self.op_line(of)
            if found is None:
                self.refuse(n, f"no op {of}")
            if found.op.op == "renumber":
                self.refuse(n, "a renumber can't be undone")
            kept, undone, _ = self.state_of(found.op.item)
            if found not in kept:
                self.refuse(n, f"op {of} is left out of the fold, so there is nothing to undo")
            if of in undone:
                self.refuse(n, f"op {of} is undone already")
            self.undo_asked(n, found.op, note)
            return
        for item_id in self.held.in_batch(cast(str, batch)):
            self.lines_of(item_id)
        self.load(self.held.objects_in_batch(cast(str, batch)))
        targets = sorted(
            (line.op for item_id in sorted(self.lines) for line in self.lines[item_id] if line.op.batch == batch),
            key=lambda op: op.id, reverse=True,
        )  # fmt: skip
        done = 0
        for op in targets:
            if op.op != "renumber" and op.id not in self.state_of(op.item)[1]:
                # Each undo op says the batch it undid, so a reader can tell it from one asked by of (DR-0614).
                self.undo_asked(n, op, note, of_batch=cast(str, batch))
                done += 1
        if not done:
            self.refuse(n, f"no op of batch {batch} to undo")

    def state_of(self, add_id: str) -> tuple[list[Line], dict[str, str], frozenset[str]]:
        """The lines kept by the object an add belongs to, its undone ops and its idle ones."""
        if add_id not in self.object_adds:
            folded = self.fold(add_id)
            return list(folded.lines), folded.undone, folded.idle
        objects = self.objects()
        folded = objects.folds[objects.owner[add_id]]
        return list(folded.lines), folded.undone, folded.idle

    def op_line(self, op_id: str) -> Line | None:
        """An op's line, from the items, meetings and streams whose lines hold its id, by add id and then as each one's
        lines run."""
        for item_id in self.held.items_of(op_id):
            self.lines_of(item_id)
        self.load(self.held.objects_of(op_id))
        return next(
            (line for item_id in sorted(self.lines) for line in self.lines[item_id] if line.op.id == op_id), None
        )

    def undo_asked(self, n: int, target: Op, note: str | None, *, of_batch: str | None = None) -> None:
        """The undo a caller asks for. An item's op undoes as DR-0614 has it. A meeting's or a stream's add undoes
        every add of it, unless it continues a meeting: that one undoes alone and splits the pair (DR-0621). A roll
        and its copy undo together, refused while the copy has rolled on (DR-0624)."""
        if target.item not in self.object_adds:
            self.undo_one(target, note, of_batch)
            return
        targets = [target]
        if target.op in ("meeting", "stream") and not (target.fields or {}).get("continues"):
            folded = self.fold_of(target.item)
            targets += [self.adds_line(a) for a in folded.adds if a != target.id and a not in folded.undone]
        elif _is_roll(target) or _is_copy(target):
            copy_id = str((target.fields or {}).get("to")) if _is_roll(target) else target.id
            if self.rolled_on(copy_id):
                self.refuse(n, "its copy has rolled on since; undo that roll first")
        for k, op in enumerate(targets):
            if op.id not in self.all_undone():
                self.undo_object_op(op, note if k == 0 else None, of_batch)

    def rolled_on(self, copy_id: str) -> bool:
        """Whether a copy has rolled on: it folds as rolled, or, a losing copy that never folds, a roll that isn't
        undone names it (DR-0624)."""
        copy = self.objects().points.get(copy_id)
        if copy is not None and copy.state == "rolled":
            return True
        undone = self.all_undone()
        return any(
            _is_roll(line.op) and line.op.point == copy_id and line.op.id not in undone
            for add_id in self.object_adds for line in self.lines[add_id]
        )  # fmt: skip

    def adds_line(self, add_id: str) -> Op:
        return next(line.op for line in self.lines[add_id] if line.op.id == add_id)

    def with_pair(self, op: Op) -> list[Op]:
        """An op and, for a roll or the copy it made, the other half: they are undone together (DR-0624)."""
        if _is_roll(op):
            copy = self.op_line(str((op.fields or {}).get("to")))
            return [op] if copy is None else [op, copy.op]
        if _is_copy(op):
            roll = next(
                (line.op for add_id in sorted(self.object_adds) for line in self.lines[add_id]
                 if _is_roll(line.op) and (line.op.fields or {}).get("to") == op.id),
                None,
            )  # fmt: skip
            return [op] if roll is None else [op, roll]
        return [op]

    def undo_one(self, target: Op, note: str | None, of_batch: str | None = None) -> None:
        """Undo an item's op, then each op of its kind on its item that it had left idle and that would now apply,
        one at a time, so another machine's close doesn't take over once this one is gone (DR-0614)."""
        kinds = {line.op.id: line.op.op for line in self.lines_of(target.item)}
        prior = self.fold(target.item)
        of = target.id
        while True:
            self.emit("undo", target.item, of=of, of_batch=of_batch, note=note)
            later = self.fold(target.item)
            woke = sorted(
                op_id for op_id in prior.idle - later.idle
                if op_id not in later.undone and family(kinds[op_id]) == family(target.op)
            )  # fmt: skip
            if not woke:
                return
            prior, of, note = later, woke[0], None

    def undo_object_op(self, target: Op, note: str | None, of_batch: str | None = None) -> None:
        """Undo a meeting's or a stream's op, then, as DR-0614 does, each op of its kind it had left idle that would now
        apply. For points the kind is a roll or a copy alone, so undoing a roll never undoes an answer another clone
        gave meanwhile; these undos the rule adds are never refused (DR-0624)."""
        kind = _undo_family(target)
        prior = self.all_idle()
        batch = self.with_pair(target)
        while True:
            undone = self.all_undone()
            for op in batch:
                if op.id not in undone:
                    self.emit("undo", op.item, of=op.id, of_batch=of_batch, note=note)
                    note = None
            if kind is None:
                return
            later = self.all_idle()
            undone = self.all_undone()
            woke = sorted(
                op_id for op_id in prior - later
                if op_id not in undone and _undo_family(cast(Line, self.op_line(op_id)).op) == kind
            )  # fmt: skip
            if not woke:
                return
            prior, batch = later, self.with_pair(cast(Line, self.op_line(woke[0])).op)

    def all_idle(self) -> set[str]:
        return {op_id for folded in self.objects().folds.values() for op_id in folded.idle}

    def all_undone(self) -> set[str]:
        return {op_id for folded in self.objects().folds.values() for op_id in folded.undone}

    def message(self) -> str:
        said = list(dict.fromkeys(f"{op.op} {self.label(op.item)}" for op in self.new))
        summary = ", ".join(said)
        if len(summary) > SUMMARY:
            summary = f"{said[0]} and {len(self.new) - 1} more"
        return f"Ledger: {summary} on {self.vault.machine}"


def _due(now: datetime, end: datetime, outcome: str | None, response: str | None) -> bool:
    """Whether a meeting's open points roll now, by todo.py's rule (DR-0624): a cancelled meeting at once, one that is
    over with an outcome, any other a day after its end."""
    return response == "cancelled" or (end <= now and (bool(outcome) or now - end >= ROLL_AFTER))


class _Batch(_Write):
    """A write as a batch (#201): each change's own `by`, `as` and `$name`, the changes the writer makes of a batch's
    own (an outcome, notes appended, the archives due), the look-alike and double-meeting refusals, a point's n as the
    batch began, what a rollover couldn't roll, and in a dry run every refusal, the refused change's ops taken back.
    Every change still goes through `_Write.change`."""

    def __init__(self, vault: Vault, held: Held, by: By, via: Via, now: datetime, *, dry_run: bool) -> None:
        super().__init__(vault, held, by, via, now)
        self.write_by: By = by
        self.dry_run = dry_run
        self.as_ids: dict[str, str] = {}
        # A refused change's name, and the change, so a later `$name` naming it hangs on it (a dry run's only).
        self.lost: dict[str, int] = {}
        self.per_change: list[tuple[Op, ...]] = []
        self.remarks: list[tuple[int, str]] = []
        self.refusals: list[Refusal] = []
        self._held_open: dict[str, tuple[str, str, str]] | None = None

    def step(self, n: int, change: Mapping[str, Any]) -> None:
        """One change of the batch, all of it or none: refused, it refuses the write, or in a dry run is listed with
        its ops taken back."""
        kind = str(change.get("op"))
        start, given, given_meetings = len(self.new), dict(self.given), dict(self.given_meetings)
        name = change.get("as")
        hangs = self.hangs_on(change)
        if hangs is not None:
            self.refusals.append(
                Refusal(n, kind, f"it names ${hangs[0]}, which change {hangs[1]} would give", hangs[1])
            )
        else:
            try:
                self.batch_change(n, change)
            except RefusedError as e:
                if not self.dry_run:
                    raise RefusedError(n, kind, e.why, e.refs) from None
                self.take_back(start, given, given_meetings)
                self.refusals.append(Refusal(n, kind, e.why))
            else:
                self.per_change.append(tuple(self.new[start:]))
                return
        if isinstance(name, str):
            self.lost[name] = n
        self.per_change.append(())

    def hangs_on(self, change: Mapping[str, Any]) -> tuple[str, int] | None:
        """The first `$name` a change gives that a refused change would have named, and that change."""
        fields = change.get("fields")
        values = [change.get(key) for key in REFS]
        if isinstance(fields, Mapping):
            values += [cast(Mapping[str, Any], fields).get(key) for key in FIELD_REFS]
        for value in values:
            m = NAMED.fullmatch(value) if isinstance(value, str) else None
            if m is not None and m.group(1) in self.lost:
                return m.group(1), self.lost[m.group(1)]
        return None

    def take_back(self, start: int, given: dict[int, str], given_meetings: dict[int, str]) -> None:
        """A refused change's ops out of the write again, as if it was never asked: a dry run goes on without it."""
        dropped = self.new[start:]
        del self.new[start:]
        self.given, self.given_meetings = given, given_meetings
        # Each op's line went last onto its object's lines, and a meeting's or a stream's add joined its group.
        for op in reversed(dropped):
            self.lines[op.item].pop()
            self.same.pop(op.id, set[str]()).discard(op.id)
        self.object_adds.difference_update(op.id for op in dropped)
        self._objects = None

    def batch_change(self, n: int, change: Mapping[str, Any]) -> None:
        kind = change.get("op")
        self.kind, self.n = str(kind), n
        if not isinstance(kind, str) or kind not in BATCH:
            self.refuse(n, f"op is one of {', '.join(BATCH)}")
        extra = sorted(str(key) for key in change if key not in ("op", *BATCH[kind]))
        if extra:
            self.refuse(n, f"{kind} takes only {', '.join(BATCH[kind])}, not {', '.join(extra)}")
        by = change.get("by", self.write_by)
        if by not in ops.BY:
            self.refuse(n, f"by is one of {', '.join(ops.BY)}")
        self.by = by
        name = change.get("as")
        if name is not None and (not isinstance(name, str) or not NAME.fullmatch(name)):
            self.refuse(n, "as is a name: a lowercase letter, then lowercase letters, digits or underscores")
        if name in self.as_ids or name in self.lost:
            self.refuse(n, f"as {name} is given twice in the batch")
        inner = self.substituted(n, {key: value for key, value in change.items() if key not in ("as", "by")})
        start = len(self.new)
        if kind == "add":
            self.add_change(n, inner)
        elif kind in ("done", "drop"):
            outcome = inner.pop("outcome", None)
            if outcome not in (None, ""):
                self.change(n, {"op": "set", "item": inner.get("item"), "fields": {"outcome": outcome}})
            self.change(n, inner)
        elif kind == "notes":
            self.notes_change(n, inner)
        elif kind == "archive" and inner.get("item") is None:
            self.archive_due(n, inner.get("note"))
        elif kind == "meeting":
            self.meeting_change(n, inner)
        elif kind in ("point_set", "point_item"):
            self.change(n, self.point_as_began(n, inner))
        else:
            self.change(n, inner)
            if kind == "rollover":
                self.stranded(n)
        if name is not None:
            self.as_ids[name] = self.made(kind, self.new[start:], inner)

    def substituted(self, n: int, change: dict[str, Any]) -> dict[str, Any]:
        """The change with each `$name` in its references given as the id it names (DR-0640)."""
        for key in REFS:
            if key in change:
                change[key] = self.named(n, change[key])
        fields = change.get("fields")
        if isinstance(fields, Mapping):
            given = cast(Mapping[str, Any], fields)
            change["fields"] = {key: self.named(n, v) if key in FIELD_REFS else v for key, v in given.items()}
        return change

    def named(self, n: int, value: object) -> object:
        m = NAMED.fullmatch(value) if isinstance(value, str) else None
        if m is None:
            return value
        if m.group(1) not in self.as_ids:
            self.refuse(n, f"{value} isn't given by an earlier change's as")
        return self.as_ids[m.group(1)]

    def made(self, kind: str, written: Sequence[Op], change: Mapping[str, Any]) -> str:
        """The id a change's `as` names: the item an add or a point's item made, the point a point added, the meeting
        a meeting change added, set or joined."""
        if kind in ("add", "point_item"):
            return next(op.id for op in written if op.op == "add")
        if kind == "point":
            return next(op.id for op in written if op.op == "point")
        objects = self.objects()
        made = next((op for op in written if op.op in ("meeting", "set")), None)
        if made is not None:
            return objects.owner[made.id if made.op == "meeting" else made.item]
        # Nothing changed, or an archived meeting's update: the meeting its event names.
        event = cast(Mapping[str, Any], change.get("fields"))["event"]
        return objects.owner[objects.events[one_line(event)]]

    def add_change(self, n: int, change: dict[str, Any]) -> None:
        """An add, refused when it looks like an open item, as todo.py's was, unless forced; the open items with its
        source and another title are named (DR-0642)."""
        force = change.pop("force", False)
        if not isinstance(force, bool):
            self.refuse(n, "force is true or false")
        # Its own checks first, as todo.py checked an add before it looked for one like it.
        likely, same = self.look_alikes(self.fields(n, change.get("fields"), adding=True))
        if likely and not force and self.via != "import":
            named = "; ".join(f"{self.label(item_id)} {_cut(title, SUMMARY)}" for item_id, title in likely)
            why = f"looks like an open item already: {named}; log the news there, or add with force true"
            self.refuse(n, why, tuple(item_id for item_id, _ in likely))
        self.change(n, change)
        self.remarks += [(n, f"{self.label(i)} comes from the same source: {_cut(t, SUMMARY)}") for i, t in same]

    def look_alikes(self, fields: Mapping[str, str | None]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """todo.py's look-alikes of an add's fields as written, as (id, title): the open items with (its source or its
        who) and a title sharing 60% of the shorter one's long words; then the other open items with its source."""
        words, who, source = _title_words(str(fields["title"])), str(fields["who"]).lower(), fields["source"]
        likely: list[tuple[str, str]] = []
        same: list[tuple[str, str]] = []
        for item_id, (other, their_who, their_source) in sorted(self.open_rows().items()):
            if their_source != source and their_who.lower() != who:
                continue
            if _alike(words, _title_words(other)):
                likely.append((item_id, other))
            elif their_source == source:
                same.append((item_id, other))
        return likely, same

    def open_rows(self) -> dict[str, tuple[str, str, str]]:
        """The open and snoozed items' titles, whos and sources: the tables', with the items this write has read as
        it leaves them."""
        if self._held_open is None:
            self._held_open = {i: (t, w, s) for i, t, w, s in self.held.open_items()}
        rows = dict(self._held_open)
        for item_id in self.lines:
            if item_id in self.object_adds:
                continue
            item = self.fold(item_id).item
            if item is not None and item.state in ("open", "snoozed"):
                rows[item_id] = (item.title, item.who, item.source)
            else:
                rows.pop(item_id, None)
        return rows

    def notes_change(self, n: int, change: dict[str, Any]) -> None:
        """todo.py's notes: `append` a line, or `set` the whole text, null clearing it; written as a set of the whole
        text (DR-0611)."""
        if ("append" in change) == ("set" in change):
            self.refuse(n, "notes takes append, a line to add, or set, the whole text")
        item_id = self.resolve(n, change.get("item"))
        text = change.get("set")
        if "append" in change:
            line = change["append"]
            if not isinstance(line, str) or clean_text(line) is None:
                self.refuse(n, "append is text")
            now = self.standing(item_id).notes
            text = f"{now}\n{line}" if now else line
        keys = {"note": change["note"]} if "note" in change else {}
        self.change(n, {"op": "set", "item": item_id, "fields": {"notes": text}, **keys})

    def archive_due(self, n: int, note: object) -> None:
        """Every archive that is due (DR-0641): each item closed 14 days back or more, then each meeting started 14
        days back or more with no open point, skipping one whose archive was undone."""
        cutoff = self.day - ARCHIVE_AFTER
        keys = {"note": note} if note is not None else {}
        for item_id in self.held.closed_by(cutoff):
            folded = self.fold(item_id)
            item = folded.item
            closed = item is not None and item.state in CLOSED and item.closed is not None and item.closed <= cutoff
            if item is None or not closed or item.archived or _archive_undone(folded):
                continue
            self.change(n, {"op": "archive", "item": item_id, **keys})
        # The tables' candidates, by a day either side of the owner's, and the meetings the write has read already.
        self.load(self.held.archivable(cutoff + timedelta(days=1)))
        if not self.object_adds:
            return
        objects = self.objects()
        due = [
            m.id for m in sorted(objects.meetings.values(), key=lambda m: (m.start, m.id))
            if not m.archived and not m.open_points() and m.start.astimezone(self.zone).date() <= cutoff
            and not _archive_undone(objects.folds[m.id])
        ]  # fmt: skip
        for meeting_id in due:
            self.change(n, {"op": "archive", "item": meeting_id, **keys})

    def meeting_change(self, n: int, change: dict[str, Any]) -> None:
        """A meeting change; a keyless one refused when a meeting that stands has its start and subject (DR-0642)."""
        given = cast(Mapping[str, Any], change.get("fields")) if isinstance(change.get("fields"), Mapping) else None
        if given is not None and all(given.get(key) in (None, "", "-") for key in ("event", "continues")):
            # The checks a new meeting meets first, so a double is named only of a meeting that could be added.
            fields = self.meeting_fields(n, given)
            self.need_meeting_fields(n, fields)
            start, end = _when(fields["start"]), _when(fields["end"])
            if end < start:
                self.refuse(n, "end is before start")
            # The meetings the tables hold at that start, with those the write has read or added.
            self.load(self.held.meetings_at(start))
            meetings = self.objects().meetings.values() if self.object_adds else ()
            twin = next((m for m in meetings if m.start == start and _same_subject(m.subject, fields["subject"])), None)
            if twin is not None:
                label = self.meeting_label(twin)
                self.refuse(n, f"{label} has that start and subject already; change it there, or give it an event")
        self.change(n, change)

    def point_as_began(self, n: int, change: dict[str, Any]) -> dict[str, Any]:
        """A point named by its n, as the point that held the place when the batch began (DR-0640)."""
        ref = change.get("point")
        plain = isinstance(ref, (str, int)) and not isinstance(ref, bool)
        m = POINT_NUMBER.fullmatch(str(ref).strip()) if plain else None
        if m is None:
            return change
        meeting = self.resolve_meeting(n, change.get("item"))
        began = self.began(meeting.id)
        if began is None:
            return change
        k, label = int(m.group(1)), self.meeting_label(meeting)
        if not 1 <= k <= len(began):
            self.refuse(n, f"{label} had {len(began)} point(s) when the batch began; no point {k}")
        if began[k - 1] not in {p.id for p in meeting.points}:
            self.refuse(n, f"{label}'s point {k} was taken off earlier in the batch")
        return {**change, "point": began[k - 1]}

    def began(self, meeting_id: str) -> list[str] | None:
        """A meeting's standing points as the batch began, by id: the meetings read, folded without this batch's ops;
        None for a meeting the batch added."""
        mine = {op.id for op in self.new}
        with paused():
            before = fold_objects(
                {a: [ln for ln in self.lines[a] if ln.op.id not in mine] for a in self.object_adds if a not in mine}
            )
        meeting = before.meetings.get(before.owner.get(meeting_id, ""))
        return None if meeting is None else [p.id for p in meeting.points]

    def stranded(self, n: int) -> None:
        """The meetings a rollover couldn't roll: due, not archived, with open points and no next meeting, as todo.py's
        meeting rollover listed them (DR-0641). The rollover read each due meeting, so nothing more is read."""
        if not self.object_adds:
            return
        for meeting in sorted(self.objects().meetings.values(), key=lambda m: (m.start, m.id)):
            still = meeting.open_points()
            due = _due(self.now, meeting.end, meeting.outcome, meeting.response)
            if (
                still
                and not meeting.archived
                and due
                and self.shell(meeting.id) is None
                and (self.next_in_series(meeting) is None)
            ):
                what = f"{self.meeting_label(meeting)} {_cut(meeting.subject, 50)}"
                self.remarks.append((n, f"{what}: {len(still)} open point(s), no next meeting"))

    def written(self, new: tuple[Op, ...]) -> Written:
        """What the batch wrote, or would write, with how each id it touched or named reads."""
        labels: dict[str, str] = {}
        for op in new:
            if op.item not in labels:
                labels[op.item] = self.label(op.item)
            if op.op in ("point", "point_set"):
                point = op.id if op.op == "point" else str(op.point)
                labels[point] = self.point_label(op.item, point)
            if op.op == "renumber":
                labels[op.id] = self.lost_label(op)
        for named in self.as_ids.values():
            if named not in labels:
                labels[named] = self.label(named)
        return Written(
            new, None, self.numbers(), tuple(self.per_change), dict(self.as_ids), labels, tuple(self.remarks),
            tuple(self.refusals),
        )  # fmt: skip

    def lost_label(self, renumber: Op) -> str:
        """The number a renumber's item or meeting lost to an earlier claim: the claim before the renumber's."""
        meeting = renumber.item in self.object_adds
        claims = (self.fold_of(renumber.item) if meeting else self.fold(renumber.item)).claims
        lost = next(number for (_, number), (claim, _) in pairwise(claims) if claim == renumber.id)
        return meeting_text(lost) if meeting else number_text(lost)

    def point_label(self, add_id: str, point_id: str) -> str:
        """A point as `M-0040#3`, its place among its meeting's standing points; one that stands nowhere now, as its
        meeting's."""
        objects = self.objects()
        meeting = objects.meetings.get(objects.owner.get(add_id, ""))
        if meeting is None:
            return self.label(add_id)
        place = next((k for k, p in enumerate(meeting.points, start=1) if p.id == point_id), None)
        return f"{self.meeting_label(meeting)}#{place}" if place is not None else self.meeting_label(meeting)


def _cut(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _title_words(text: str) -> set[str]:
    return {w for w in WORD_RUN.findall(text.lower()) if len(w) >= ALIKE_LETTERS}


def _alike(words: set[str], other: set[str]) -> bool:
    """todo.py's: both have words, and they share at least 60% of the shorter one's."""
    return bool(words and other) and len(words & other) / min(len(words), len(other)) >= ALIKE_SHARE


def _archive_undone(folded: Folded | ObjectFold) -> bool:
    """Whether an archive of it was undone: the sync then leaves it be (DR-0615, DR-0624)."""
    return any(line.op.op == "archive" and line.op.id in folded.undone for line in folded.lines)


def _undo_family(op: Op) -> str | None:
    """The kind an undo of a meeting's or a stream's op also undoes when it woke: a roll and a copy are one kind, any
    other point op none, every other op its own kind."""
    if _is_roll(op) or _is_copy(op):
        return "roll"
    if op.op in ("point", "point_set"):
        return None
    return op.op


def _present(fields: dict[str, Any]) -> dict[str, Any]:
    return {name: value for name, value in fields.items() if value is not None}


def _when(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _as_written(meeting: Meeting, name: str) -> Any:
    """A meeting field's value as an op carries it: a time to the minute with its offset, attendees as a list."""
    value = getattr(meeting, name)
    if name in TIMES:
        return cast(datetime, value).isoformat(timespec="minutes")
    if name == "attendees":
        return list(cast(tuple[str, ...], value)) or None
    return value


def _meeting_differs(meeting: Meeting, name: str, value: Any) -> bool:
    current = getattr(meeting, name)
    if name in TIMES:
        return current.isoformat(timespec="minutes") != value
    if name == "attendees":
        return current != tuple(value or ())
    return current != value


def _point_differs(point: Point, name: str, value: Any) -> bool:
    current = {"text": point.text, "state": point.state, "answer": point.answer, "item": point.item,
               "proposed": True if point.proposed else None}[name]  # fmt: skip
    return current != value


def _day(value: str) -> bool:
    return bool(ops.DAY.fullmatch(value)) and ops.is_day(value)


def _text(value: object) -> str | None:
    return value.isoformat() if isinstance(value, date) else cast(str | None, value)


def material_ref(vault: Vault, ref: str) -> str:
    """A material's ref as stored: a URL or a locator as given; a path with forward slashes, relative to the host
    repo's top in a folder vault and to the vault in a repo of its own, absolute only outside it."""
    text = ref.strip().strip('"').strip()
    if SCHEME.match(text) and not DRIVE.match(text):
        return text
    path = Path(text)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(vault.home.top.resolve()).as_posix()
        except ValueError:
            return path.as_posix()
    return text.replace("\\", "/").removeprefix("./")


# -- the calendar step's pending rids (DR-0647) -------------------------------------------------------------------

PENDING_DIR = "ledger"
PENDING_FILE = "calendar-pending.txt"


def calendar_pending_path(vault: Vault) -> Path:
    """This clone's pending rids: in the vault's private folder, never committed."""
    return vault.home.private / PENDING_DIR / PENDING_FILE


def calendar_pending(vault: Vault) -> list[str]:
    """The rids a promotion noted that no calendar step has cleared yet, in the order noted; none when the file is
    gone."""
    try:
        text = calendar_pending_path(vault).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return list(dict.fromkeys(line.strip() for line in text.split("\n") if line.strip()))


def note_calendar_pending(vault: Vault, rids: Sequence[str]) -> None:
    """Note rids as pending, before the run file holding their versions is written (DR-0647): a crash after it loses
    no set, and a rid whose version never reached raw reads raw's latest and changes nothing."""
    _save_calendar_pending(vault, [*calendar_pending(vault), *rids])


def clear_calendar_pending(vault: Vault, rids: Sequence[str]) -> None:
    """Take rids off the pending list once a calendar step's write committed or had nothing to write."""
    done = set(rids)
    _save_calendar_pending(vault, [rid for rid in calendar_pending(vault) if rid not in done])


def _save_calendar_pending(vault: Vault, rids: Sequence[str]) -> None:
    path = calendar_pending_path(vault)
    kept = list(dict.fromkeys(rids))
    if not kept:
        path.unlink(missing_ok=True)
        return
    make_private(vault.home.git_dir, path.parent)
    write_replacing(vault.home, path, "".join(f"{rid}\n" for rid in kept), "calendar-pending")


# -- the import (#202) -------------------------------------------------------------------------------------------


def write_import(vault: Vault, months: Sequence[Sequence[Op]]) -> tuple[str, ...]:
    """An import's months (DR-0653), oldest first, each the ops of one month, at least one, as `ledger.imports` made
    them from an export's lines: one write of this machine's month file, through a temp file and a rename, and one
    commit. The commits, in order."""
    commits: list[str] = []
    with vault.writing():
        ops.folder(vault.root)
        for made in months:
            said = f"{len(made):,} op{'' if len(made) == 1 else 's'}"
            commit = commit_ops(vault, made, f"Ledger: import {made[0].day:%Y-%m}, {said} on {vault.machine}")
            if commit is not None:
                commits.append(commit)
    return tuple(commits)


@dataclass(frozen=True)
class Reverted:
    """What a revert took out, or with a dry run would: the op files, how many ops of each via, and its commit."""

    files: tuple[str, ...]
    vias: dict[str, int]
    commit: str | None = None


def revert_import(vault: Vault, *, dry_run: bool = False) -> Reverted:
    """`docket ledger import --revert` (DR-0656): every op file deleted, the import's ops and any written since, in one
    commit, as `git rm` leaves them, so a clone that wrote to one meanwhile meets a modify/delete conflict at its
    merge instead of a union merge taking every line back; then the index's ledger tables catch up. For the trial
    imports and a rollback, until the old ledger retires; git keeps the lines."""
    with vault.writing():
        ops.folder(vault.root)
        listed, _ = ops.listing(vault.root)
        vias = Counter(line.op.via for line in ops.read_all(vault.root).lines)
        files = tuple(rel for rel, _ in listed)
        if dry_run or not files:
            return Reverted(files, dict(sorted(vias.items())))
        tracked = set(vault.git.in_head(OPS_DIR) or ())
        # A write that stopped between its add and its commit left its new file staged: kept so, the next commit,
        # the user's own too, would bring it back. Unstaged first, so a failure leaves every file where it was.
        staged = vault.git.staged_new(OPS_DIR)
        if staged:
            vault.git.run("reset", "-q", "--", *staged)
        for _, path in listed:
            path.unlink()
        out = sum(vias.values())
        message = f"Ledger: import reverted, {out:,} op{'' if out == 1 else 's'} out on {vault.machine}"
        gone = sorted(rel for rel in files if rel in tracked)
        commit = vault.commit(gone, console.signed(message)) if gone else None
    with ledger_tables(vault):
        pass
    return Reverted(files, dict(sorted(vias.items())), commit)


# -- a forget's masks (#212) ------------------------------------------------------------------------------------


def write_masks(vault: Vault, texts: Mapping[str, str]) -> None:
    """A forget's masks in place (DR-0693, DR-0695): each op file's text as `ops.masked_file` left it, its listed ops'
    lines masked and a twin masking made dropped, written whole through a temp file and a rename. The one other write
    of a committed line: the apply, under the vault lock, recorded the paths first and commits them in its one
    commit."""
    ops.folder(vault.root)
    for rel, text in sorted(texts.items()):
        write_replacing(vault.home, vault.root / rel, text, "ledger-mask")


# -- committing -------------------------------------------------------------------------------------------------


def pending(vault: Vault) -> list[str]:
    """Op files a write or a forget's apply that stopped before its commit left: new ones, tracked ones that only
    grew, and lines changed to their masks, their op ids listed (DR-0696). One that lost or changed a committed line
    otherwise refuses the write, since its commit would make the change for good."""
    changed = vault.git.changed_against_head(OPS_DIR)
    listed = forgotten.load(vault.root, vault.home).ops if changed else frozenset[str]()
    for rel in changed:
        if not check.committed_kept(vault, rel, listed):
            raise LedgerError(
                f"{rel} lost or changed a committed op line; ops are written once, so put it back from git "
                "(`docket vault check` names it); nothing written"
            )
    new = [rel for rel in vault.git.not_in_head(OPS_DIR) if ops.FILE_NAME.fullmatch(Path(rel).name)]
    return sorted({*changed, *new})


def commit_ops(vault: Vault, new: Sequence[Op], message: str) -> str | None:
    """Append ops to their file and commit it. A write's ops fall in one month on one machine, so it replaces one
    file through a temp file and a rename and lands whole or not at all (DR-0610); a commit that fails before it lands
    puts the file back."""
    rels = sorted({ops.rel_of(op) for op in new})
    if len(rels) != 1:
        raise LedgerError(f"a write's ops fall in one month, and these span {len(rels)} files; nothing written")
    rel = rels[0]
    ops.folder(vault.root).mkdir(parents=True, exist_ok=True)
    path = vault.root / rel
    if ops.linked(path):
        raise LedgerError(f"{rel} is a link; ops are written only as plain files in the vault; nothing written")
    before = ops.text_of(path, rel) if path.exists() else None
    also = pending(vault)
    text = before or ""
    sep = "\n" if text and not text.endswith("\n") else ""
    write_replacing(vault.home, path, text + sep + "".join(f"{op.text()}\n" for op in new), "ledger-ops")
    head = vault.git.head_id()
    try:
        return vault.commit(sorted({rel, *also}), console.signed(message))
    except BaseException as e:
        # A failure after the commit landed leaves its ops where HEAD holds them: putting the file back would make
        # every committed line of the write look gone.
        if vault.git.head_id() != head:
            raise
        if not isinstance(e, UnmergedError):
            vault.git.run("reset", "-q", "--", rel, check=False)
        if before is None:
            path.unlink(missing_ok=True)
        else:
            write_replacing(vault.home, path, before, "ledger-restore")
        raise
