"""Writing the ledger (DR-0610, DR-0612, DR-0613, DR-0618): the one writer of ops, a whole write or none of it.

A write runs under the vault lock. It brings the index's ledger tables up to date from the op files, or folds them in
memory when the index can't be had (DR-0616, DR-0619), and reads each item it touches from them, and every meeting and
stream; gives a renumber to each item or meeting a clash left without a number; checks each change against the ledger
as the write leaves it, the way todo.py's verbs check them; mints the ids and numbers; screens the text; then writes
this machine's month file through a temp file and a rename and commits it, with any op file a stopped write left. A
change that can't be made refuses the whole write, naming the change by its place and, for the text rules, the field,
never the value.

    write(vault, [{"op": "add", "fields": {"kind": "reply", "title": "Send Ada the dates", "who": "Ada",
                   "stream": "pilot", "source": "chat:C0X/1"}},
                  {"op": "triage", "item": "A-0302", "verdict": "chase"}], by="claude", via="cli")

Changes name an item by its A- number or its id, a meeting by its M- number or an id of its adds, a stream by its
name. A number more than one item or meeting has held names none: it is named by its id until the clash is behind it.
Undo takes `of`, an op's id, or `batch`, a write's first op id; an undo also undoes what the op it undoes left idle,
done and drop counting as one kind (DR-0614), and for points only rolls and copies (DR-0624).

Meetings, points and streams (#199): `meeting` adds one, or with an event the ledger holds sets what changed on the
add carrying that event, or with `continues` joins an unkeyed meeting (DR-0621); `stream` adds or sets one by name;
`point`, `point_set`, `keep` and `point_item` change a meeting's talking points; `rollover` rolls the open points of
the meetings that are due to the next of their series (DR-0624).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn, cast
from zoneinfo import ZoneInfo

from docket import console
from docket.errors import DocketError
from docket.index.build import ledger_tables
from docket.index.ledger import Held
from docket.ledger import check, ops
from docket.ledger.fold import CLOSED, NUMBER, Folded, Item, family, fold_item, load, number_text
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
from docket.vault.neverstore import matches_text, names_person
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
PEOPLE = ("who", "organiser", "attendee")
TIMES = ("start", "end")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ufeff\ufffe\uffff]")
SUMMARY = 60
# todo.py's: a meeting that is over rolls its open points a day after its end, or at once with an outcome (DR-0624).
ROLL_AFTER = timedelta(hours=24)
# todo.py's series rule: one subject starting with the other names one series only from this many characters.
SERIES_PREFIX = 15
# A "Name: what" point turned into an item waits on Name when the name is this many words or fewer, as in todo.py.
NAME_WORDS = 3


class LedgerError(DocketError):
    """A write refused: nothing of it was written."""


@dataclass(frozen=True)
class Written:
    """What a write wrote, or with a dry run would write: its ops in order, its commit, and the number of each item
    and meeting it touched."""

    ops: tuple[Op, ...]
    commit: str | None
    numbers: dict[str, int] = field(default_factory=dict[str, int])


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
    """Make the changes, all of them or none, as one write by `by` through `via`; with `dry_run`, check them and say
    what would be written, writing nothing."""
    if by not in ops.BY or via not in ops.VIA:
        raise LedgerError(f"by is one of {', '.join(ops.BY)}, via one of {', '.join(ops.VIA)}")
    moment = now or datetime.now(UTC)
    with vault.writing():
        ops.folder(vault.root)
        # The tables are asked only while the write is planned: its commit runs with the index free.
        with ledger_tables(vault) as conn:
            plan = _Write(vault, Held(conn), by, via, moment)
            plan.renumber()
            for n, change in enumerate(changes, start=1):
                plan.change(n, change)
            new = tuple(plan.new)
            numbers = plan.numbers()
            message = plan.message() if new else ""
        if dry_run or not new:
            return Written(new, None, numbers)
        return Written(new, commit_ops(vault, new, message), numbers)


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
    """One write as it is planned: the ledger's tables it reads, the lines of each item it touched and of every meeting
    and stream, with its own ops added, and the numbers it gave."""

    def __init__(self, vault: Vault, held: Held, by: By, via: Via, now: datetime) -> None:
        self.vault = vault
        self.held = held
        self.highest_id = held.highest_id()
        self.highest_number = held.highest_number()
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
        self.redactor = Redactor.from_config(vault.config.redaction)
        self.kind = ""
        # Every meeting's and stream's lines, folded from the op files, and for each add the adds of its object, so an
        # op's `after` sees them all.
        ledger = load(vault.root)
        self.lines.update((add_id, list(ledger.lines[add_id])) for add_id in ledger.objects.owner)
        self.object_adds = set(ledger.objects.owner)
        self.same: dict[str, set[str]] = {}
        for add_id, obj_id in ledger.objects.owner.items():
            self.same.setdefault(obj_id, set()).add(add_id)
            self.same[add_id] = self.same[obj_id]
        self._objects: Objects | None = ledger.objects

    # -- what stands ------------------------------------------------------------------------------------------------

    def lines_of(self, item_id: str) -> list[Line]:
        """An item's lines: as the tables hold them, read once, with this write's ops added."""
        if item_id not in self.lines:
            self.lines[item_id] = self.held.lines(item_id)
        return self.lines[item_id]

    def fold(self, item_id: str) -> Folded:
        return fold_item(item_id, self.lines_of(item_id))

    def objects(self) -> Objects:
        """The meetings and streams as the write leaves them, folded again after each op written to one."""
        if self._objects is None:
            self._objects = fold_objects({add_id: self.lines[add_id] for add_id in self.object_adds})
        return self._objects

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

    def refuse(self, n: int, why: str) -> NoReturn:
        raise LedgerError(f"change {n} ({self.kind}): {why}; nothing written")

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
        objects = self.objects()
        if ops.ULID.fullmatch(text) and text in objects.owner:
            obj_id = objects.owner[text]
            if obj_id in objects.meetings:
                return "meeting", obj_id
            if obj_id in objects.streams:
                return "stream", obj_id
            self.refuse(n, f"{text} was undone")
        if MEETING_NUMBER.fullmatch(text):
            return "meeting", self.resolve_meeting(n, text).id
        if text in objects.names:
            return "stream", objects.names[text]
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
        objects = self.objects()
        if ops.ULID.fullmatch(text):
            meeting = objects.meetings.get(objects.owner.get(text, ""))
            if meeting is None:
                self.refuse(n, f"no meeting {text}")
            return meeting
        m = MEETING_NUMBER.fullmatch(text)
        if m is None:
            self.refuse(n, "a meeting is named by its M- number or its id")
        number = int(m.group(1))
        # The meetings are folded again after each op this write writes, so the holders are current, and only a
        # meeting that stands holds a number.
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
            joined = (op.fields or {}).get("continues")
            group = self.same.get(joined, set()) if isinstance(joined, str) else set[str]()
            group.add(op.id)
            for add_id in group:
                self.same[add_id] = group
            self.object_adds.add(op.id)
        if item in self.object_adds:
            self._objects = None
        return op

    def next_number(self) -> int:
        return max(self.highest_number, *self.given, 0) + 1

    def next_meeting_number(self) -> int:
        return max(self.objects().highest_number, *self.given_meetings, 0) + 1

    def renumber(self) -> None:
        """A new number for each item or meeting whose latest claim lost one to an earlier claim (DR-0613)."""
        self.kind = "renumber"
        for item_id in self.held.clashing():
            number = self.next_number()
            self.given[number] = item_id
            self.emit("renumber", item_id, number=number)
        for meeting in self.objects().clashes():
            number = self.next_meeting_number()
            self.given_meetings[number] = meeting.id
            self.emit("renumber", meeting.id, number=number)

    # -- the text rules (DR-0618, DR-0627) --------------------------------------------------------------------------

    def screen(self, n: int, name: str, value: str) -> str:
        """A value as it may be written: text redacted, anything else as given. A never-store keyword, a person on the
        people list in who, an organiser or an attendee, or a redaction rule's match in a value that isn't text
        refuses the write."""
        lists = self.vault.config.never_store
        counts: Counter[str] = Counter()
        redacted = self.redactor.text(value, counts)
        if matches_text(value, lists) or matches_text(redacted, lists):
            self.refuse(n, f"{name} says something on the never-store keywords list; leave it out")
        if name in PEOPLE and names_person(value, lists):
            self.refuse(n, f"{name} names someone on the never-store people list; leave them out")
        if name in REDACTED:
            return redacted
        if counts:
            what = (
                "locator" if name in LOCATORS else "day" if name in (*DAYS, "until", "deadline")
                else "time" if name in TIMES else name
            )  # fmt: skip
            self.refuse(n, f"{name} matches the {min(counts)} redaction rule, and a {what} can't hold a marker")
        return value

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
        names = self.objects().names
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
        kind = change.get("op")
        self.kind = str(kind)
        allowed = CHANGES.get(kind) if isinstance(kind, str) else None
        if allowed is None:
            self.refuse(n, f"op is one of {', '.join(CHANGES)}")
        extra = sorted(str(key) for key in change if key not in ("op", *allowed))
        if extra:
            self.refuse(n, f"{kind} takes only {', '.join(allowed)}, not {', '.join(extra)}")
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
        ref = self.screen(n, "ref", material_ref(self.vault, raw))
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
        fields = self.meeting_fields(n, change.get("fields"))
        event, continues = fields.get("event"), fields.get("continues")
        objects = self.objects()
        if continues is not None:
            if event is None:
                self.refuse(n, "continues needs an event: only a meeting with one continues a meeting")
            joined = objects.meetings[continues]
            if joined.event is not None:
                self.refuse(n, f"{self.meeting_label(joined)} has an event; only a meeting without one is continued")
            held = objects.meetings.get(objects.events.get(event, ""))
            if held is not None and not self.keyed(held.id):
                self.refuse(n, f"that event continues {self.meeting_label(held)} already")
            self.need_meeting_fields(n, fields)
            # The continued meeting's number goes on the add, so the object claims no new one (DR-0621).
            self.emit("meeting", None, number=joined.number or joined.claimed, fields=_present(fields), note=note)
            return
        held = objects.meetings.get(objects.events.get(event, "")) if event is not None else None
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
        """A set of what changed. A set on an archived meeting is idle, so the writer writes nothing for it and refuses
        nothing: the sync's write never fails on an old meeting (DR-0624)."""
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
        if changed and not meeting.archived:
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
        held = self.objects().streams.get(self.objects().names.get(name, ""))
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
        are taken by start, each from the ledger as the rolls before it left it."""
        for meeting_id in [m.id for m in sorted(self.objects().meetings.values(), key=lambda m: (m.start, m.id))]:
            meeting = self.objects().meetings[meeting_id]
            still = meeting.open_points()
            if not still or meeting.archived:
                continue
            over = meeting.end <= self.now and (bool(meeting.outcome) or self.now - meeting.end >= ROLL_AFTER)
            if meeting.response != "cancelled" and not over:
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

    def next_in_series(self, meeting: Meeting) -> Meeting | None:
        """The next meeting by start, not cancelled or declined: the same series when both have one, else todo.py's
        rule, the same subject, then one starting with the other with the same organiser (DR-0624)."""
        later = sorted(
            (m for m in self.objects().meetings.values()
             if m.id != meeting.id and m.start > meeting.start and m.response not in ("cancelled", "declined")
             and not m.archived),
            key=lambda m: (m.start, m.id),
        )  # fmt: skip
        if meeting.series is not None:
            same = next((m for m in later if m.series == meeting.series), None)
            if same is not None:
                return same
            later = [m for m in later if m.series is None]
        exact = next((m for m in later if _same_subject(m.subject, meeting.subject)), None)
        if exact is not None:
            return exact
        return next(
            (m for m in later if _one_series(m.subject, meeting.subject)
             and (not (m.organiser and meeting.organiser) or m.organiser == meeting.organiser)),
            None,
        )  # fmt: skip

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
        targets = sorted(
            (line.op for item_id in sorted(self.lines) for line in self.lines[item_id] if line.op.batch == batch),
            key=lambda op: op.id, reverse=True,
        )  # fmt: skip
        done = 0
        for op in targets:
            if op.op != "renumber" and op.id not in self.state_of(op.item)[1]:
                self.undo_asked(n, op, note)
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
        """An op's line, from the items whose lines hold its id, by item id and then as each item's lines run."""
        for item_id in self.held.items_of(op_id):
            self.lines_of(item_id)
        return next(
            (line for item_id in sorted(self.lines) for line in self.lines[item_id] if line.op.id == op_id), None
        )

    def undo_asked(self, n: int, target: Op, note: str | None) -> None:
        """The undo a caller asks for. An item's op undoes as DR-0614 has it. A meeting's or a stream's add undoes
        every add of it, unless it continues a meeting: that one undoes alone and splits the pair (DR-0621). A roll
        and its copy undo together, refused while the copy has rolled on (DR-0624)."""
        if target.item not in self.object_adds:
            self.undo_one(target, note)
            return
        targets = [target]
        if target.op in ("meeting", "stream") and not (target.fields or {}).get("continues"):
            folded = self.fold_of(target.item)
            targets += [self.adds_line(a) for a in folded.adds if a != target.id and a not in folded.undone]
        elif _is_roll(target) or _is_copy(target):
            copy_id = str((target.fields or {}).get("to")) if _is_roll(target) else target.id
            copy = self.objects().points.get(copy_id)
            if copy is not None and copy.state == "rolled":
                self.refuse(n, "its copy has rolled on since; undo that roll first")
        for k, op in enumerate(targets):
            if op.id not in self.all_undone():
                self.undo_object_op(op, note if k == 0 else None)

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

    def undo_one(self, target: Op, note: str | None) -> None:
        """Undo an item's op, then each op of its kind on its item that it had left idle and that would now apply,
        one at a time, so another machine's close doesn't take over once this one is gone (DR-0614)."""
        kinds = {line.op.id: line.op.op for line in self.lines_of(target.item)}
        prior = self.fold(target.item)
        of = target.id
        while True:
            self.emit("undo", target.item, of=of, note=note)
            later = self.fold(target.item)
            woke = sorted(
                op_id for op_id in prior.idle - later.idle
                if op_id not in later.undone and family(kinds[op_id]) == family(target.op)
            )  # fmt: skip
            if not woke:
                return
            prior, of, note = later, woke[0], None

    def undo_object_op(self, target: Op, note: str | None) -> None:
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
                    self.emit("undo", op.item, of=op.id, note=note)
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


# -- committing -------------------------------------------------------------------------------------------------


def pending(vault: Vault) -> list[str]:
    """Op files a write that stopped before its commit left: new ones, and tracked ones that only grew. One that lost
    or changed a committed line refuses the write, since its commit would make the change for good."""
    changed = vault.git.changed_against_head(OPS_DIR)
    for rel in changed:
        if not check.committed_kept(vault, rel):
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
