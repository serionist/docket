"""The ledger's op line (DR-0610 to DR-0615): one JSON line per change, in `ledger/ops/<yyyy-mm>-<machine>.jsonl`.

    {"schema_version":1,"id":"01JAB3W8Q4X5M2Z7K9R0T6YVNC","item":"01JAB3W8Q4X5M2Z7K9R0T6YVNC","op":"add",
     "day":"2026-10-18","at":"2026-10-18T09:14:02Z","machine":"desk","by":"claude","via":"cli",
     "batch":"01JAB3W8Q4X5M2Z7K9R0T6YVNC","number":302,"fields":{"kind":"reply","title":"Send Ada the dates",
     "who":"Ada","stream":"pilot","source":"chat:C0X/1"}}

| op | its own keys | what it does |
|---|---|---|
| add | number, fields: kind, title, who, stream, source; meeting, opened, due, description, notes, outcome | an item |
| meeting | number, fields: subject, start, end; event, continues, series, organiser, where, | a meeting |
| | response, attendees, stream, notes, outcome (DR-0622) | |
| stream | fields: name, status; where, next, deadline (DR-0625) | a stream |
| set | fields, null clearing an optional one | the plain fields of an item, a meeting or a stream |
| log | note | a log line |
| triage | verdict, or null | an open or snoozed item open with it, or cleared |
| snooze | until | snoozed |
| done, drop | note | closed on the op's day |
| reopen, archive | - | open again; a closed item, or a meeting with no open point, out of the day's lists |
| material | ref, label, remove | a path, URL or locator added, relabelled or removed |
| point | fields (text; item, from, proposed) | a talking point on a meeting, its id the op's (DR-0623) |
| point_set | point, fields (text, state, answer, item, to, proposed) | one change to a point |
| undo | of; of_batch, the batch it undid, when asked by batch | the op it names left out of the fold (DR-0614) |
| renumber | number | a new number after a clash (DR-0613) |

An add, a meeting or a stream makes an object: its item is its own id, and every other op names its object by item
(DR-0620). An op's id is a ULID minted as a hybrid logical clock (DR-0612), and the ledger's order is the ids' order.
A line that doesn't read is a problem and is skipped; a key, a name in `fields` or an op kind this Docket doesn't know
is read and changes nothing, so a later Docket can add one in schema 1. A name in `fields` this Docket knows holds what
its object's rules say (text, a day, a time with its offset, a list of names, an id, true) or null, checked on the
object's own ops as they are read and on a set when it is folded; a name it doesn't know holds any value. Kinds,
verdicts, states, streams, `by` and `via` are read as text and checked when written. The format is a one-way door: a
later one writes schema_version 2.

A committed line changes only to its mask, its id on the forgotten list (DR-0693, DR-0695): the same keys, its text
`[forgotten]`, its locators `[forgotten:<32 hex>]`, its attendees none. `parse`, given the listed ids, reads each one's
line as its mask, so every reader of the ledger reads one ledger, and a line a merge brought back beside its mask
reads as the same op; the forget's apply, `docket vault check` and the history rewrite read raw lines. Which ops a
forget masks is `docket.forget.ledger`'s rule (DR-0694): an item or a meeting whose making op is masked is a shell.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Collection, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, NamedTuple, Self, cast

from pydantic import ConfigDict, Field, StringConstraints, ValidationError, model_validator
from pydantic.dataclasses import dataclass as pydantic_dataclass

from docket.errors import DocketError
from docket.vault import forgotten
from docket.vault import machine as machines
from docket.vault.config import CONFIG_FILE
from docket.vault.staging import digest

OPS_DIR = "ledger/ops"
SUFFIX = ".jsonl"
FILE_NAME = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])-(.+)\.jsonl$")
# Git keeps an empty folder only with a file in it; init writes this one.
KEEP = ".gitkeep"

# Crockford's base32, as ULIDs write it: 26 characters for 128 bits, 48 of milliseconds and 80 random.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_VALUE = {c: i for i, c in enumerate(ALPHABET)}
ULID = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")
RANDOM_BITS = 80
RANDOM_BYTES = 10

KINDS = ("reply", "decision", "task", "invite", "waiting")
VERDICTS = ("discuss", "claude", "me", "chase")
BY = ("user", "claude", "sync")
VIA = ("cli", "page", "tool", "import")
OPS = (
    "add", "set", "log", "triage", "snooze", "done", "drop", "reopen", "material", "archive", "undo", "renumber",
    "meeting", "stream", "point", "point_set",
)  # fmt: skip
# The kinds that make an object, each an op whose item is its own id (DR-0620).
MAKES = ("add", "meeting", "stream")
# The kinds each object takes. Another kind this Docket knows naming one, a point on an item or a triage on a meeting,
# is a problem and is left out; a kind it doesn't know changes nothing (DR-0620).
TAKES = {
    "item": (
        "add", "set", "log", "triage", "snooze", "done", "drop", "reopen", "material", "archive", "undo", "renumber",
    ),
    "meeting": ("meeting", "set", "log", "material", "archive", "point", "point_set", "undo", "renumber"),
    "stream": ("stream", "set", "log", "undo"),
}  # fmt: skip
REFUSES = {kind: frozenset(OPS) - frozenset(takes) for kind, takes in TAKES.items()}
FIELDS = ("kind", "title", "who", "stream", "source", "meeting", "opened", "due", "description", "notes", "outcome")
REQUIRED = ("kind", "title", "who", "stream", "source")
DAYS = ("opened", "due")
MEETING_FIELDS = (
    "subject", "start", "end", "event", "continues", "series", "organiser", "where", "response", "attendees", "stream",
    "notes", "outcome",
)  # fmt: skip
MEETING_REQUIRED = ("subject", "start", "end")
STREAM_FIELDS = ("name", "status", "where", "next", "deadline")
STREAM_REQUIRED = ("name", "status")
POINT_FIELDS = ("text", "state", "answer", "item", "from", "to", "proposed")
RESPONSES = ("accepted", "tentative", "declined", "none", "organiser", "cancelled")
STATUSES = ("active", "waiting", "closed")
STATES = ("open", "answered", "dropped", "rolled")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# A meeting's start and end: the local time with its offset, so a DST change or a move of zone stays exact (DR-0622).
TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}[+-]\d{2}:\d{2}$")
UTC_OFFSET = timedelta(0)
# A line's keys in the order the writer puts them, so one op has one text.
KEY_ORDER = (
    "schema_version", "id", "item", "op", "day", "at", "machine", "by", "via", "batch", "after", "number", "point",
    "fields", "verdict", "until", "ref", "label", "remove", "of", "of_batch", "note",
)  # fmt: skip

# The writers this Docket writes and the ways in; a line is read with any word in them, as a later Docket may add one.
By = Literal["user", "claude", "sync"]
Via = Literal["cli", "page", "tool", "import"]
Ulid = Annotated[str, StringConstraints(pattern=ULID.pattern)]
# The file name holds the machine to the whole rule (machines.validate); a line only to its letters, read fast.
MachineName = Annotated[str, StringConstraints(pattern=machines.NAME.pattern)]
# An op's kind, and its by and via: a lowercase word of at most 32 letters and underscores (DR-0611).
WORD = r"^[a-z][a-z_]{0,31}$"
Word = Annotated[str, StringConstraints(pattern=WORD)]


def is_day(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def is_time(value: str) -> bool:
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _text(value: object) -> bool:
    return isinstance(value, str)


def _a_day(value: object) -> bool:
    return isinstance(value, str) and bool(DAY.fullmatch(value)) and is_day(value)


def _a_time(value: object) -> bool:
    return isinstance(value, str) and bool(TIME.fullmatch(value)) and is_time(value)


def _names(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(name, str) for name in cast(list[object], value))


def _an_id(value: object) -> bool:
    return isinstance(value, str) and bool(ULID.fullmatch(value))


def _true(value: object) -> bool:
    return value is True


# What each name an object's fields know holds, besides null; a name it doesn't know holds anything (DR-0620).
RULES: dict[str, dict[str, Callable[[object], bool]]] = {
    "item": {**dict.fromkeys(FIELDS, _text), "meeting": _an_id, "opened": _a_day, "due": _a_day},
    "meeting": {**dict.fromkeys(MEETING_FIELDS, _text), "start": _a_time, "end": _a_time, "attendees": _names,
                "continues": _an_id},
    "stream": {**dict.fromkeys(STREAM_FIELDS, _text), "deadline": _a_day},
    "point": {**dict.fromkeys(POINT_FIELDS, _text), "item": _an_id, "from": _an_id, "to": _an_id, "proposed": _true},
}  # fmt: skip
# The object whose rules an op's own fields follow; a set's are its object's, known only when it is folded.
OBJECT_OF = {"add": "item", "meeting": "meeting", "stream": "stream", "point": "point", "point_set": "point"}
# Each object's names whose rule is plain text: fields holding only these, each as text, fit without a rule run, as an
# item's add, a point and most answers do (#218).
TEXT_ONLY = {kind: frozenset(name for name, rule in rules.items() if rule is _text) for kind, rules in RULES.items()}


def fits(kind: str, fields: dict[str, Any]) -> bool:
    """Whether every name the object knows holds what its rules say, or null."""
    if fields.keys() <= TEXT_ONLY[kind] and all(type(value) is str for value in fields.values()):
        return True
    rules = RULES[kind]
    for name, value in fields.items():
        rule = rules.get(name)
        if rule is not None and value is not None and not rule(value):
            return False
    return True


def _add(op: Op) -> bool:
    fields = op.fields or {}
    return op.number is not None and op.item == op.id and all(fields.get(name) for name in REQUIRED)


def _meeting(op: Op) -> bool:
    fields = op.fields or {}
    return op.number is not None and op.item == op.id and all(fields.get(name) for name in MEETING_REQUIRED)


def _stream(op: Op) -> bool:
    fields = op.fields or {}
    return op.item == op.id and all(fields.get(name) for name in STREAM_REQUIRED)


def _point_set(op: Op) -> bool:
    fields = op.fields or {}
    return op.point is not None and bool(fields) and (fields.get("state") != "rolled" or bool(fields.get("to")))


# What each kind this Docket knows must carry for the fold to apply it; a kind it doesn't know needs nothing.
SHAPES: dict[str, Callable[[Op], bool]] = {
    "add": _add,
    "meeting": _meeting,
    "stream": _stream,
    "set": lambda op: bool(op.fields),
    "log": lambda op: bool(op.note),
    "snooze": lambda op: op.until is not None,
    "material": lambda op: bool(op.ref),
    "point": lambda op: op.item != op.id and bool((op.fields or {}).get("text")),
    "point_set": _point_set,
    "undo": lambda op: op.of is not None,
    "renumber": lambda op: op.number is not None,
}
# The names each kind's fields are written in, so one op has one text; a name no list holds goes last.
_ORDERS = {
    kind: {name: n for n, name in enumerate(names)}
    for kind, names in {
        "add": FIELDS, "meeting": MEETING_FIELDS, "stream": STREAM_FIELDS, "point": POINT_FIELDS,
        "point_set": POINT_FIELDS, "set": tuple(dict.fromkeys((*FIELDS, *MEETING_FIELDS, *STREAM_FIELDS))),
    }.items()
}  # fmt: skip


@pydantic_dataclass(config=ConfigDict(extra="ignore", strict=True), frozen=True, slots=True, kw_only=True)
class Op:
    """One op, as one line of an op file. Strict, so a number written as text or a day as a number doesn't read. A
    dataclass with slots, not a model: a fold holds tens of thousands, and a model's dict and set of the fields given
    took three times the memory and a tenth more time to read (#218)."""

    schema_version: Literal[1]
    id: Ulid
    item: Ulid
    op: Word
    day: date
    at: datetime | None = None
    machine: MachineName
    by: Word
    via: Word
    batch: Ulid | None = None
    after: Ulid | None = None
    number: Annotated[int, Field(ge=1)] | None = None
    point: Ulid | None = None
    # A name this Docket knows holds what its object's rules say; a later Docket's name any JSON value, kept unread.
    fields: dict[str, Any] | None = None
    verdict: str | None = None
    until: date | None = None
    ref: str | None = None
    label: str | None = None
    remove: bool = False
    of: Ulid | None = None
    # The batch an undo asked by batch undid, on each undo op it wrote; none on one asked by of (DR-0614).
    of_batch: Ulid | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        # Every op is read through here, tens of thousands at a fold: each test is as cheap as it can be.
        at = self.at
        if at is not None and at.utcoffset() != UTC_OFFSET:
            raise ValueError("at is a UTC time")
        shape = SHAPES.get(self.op)
        if shape is not None and not shape(self):
            raise ValueError(f"{self.op} doesn't carry what it needs")
        fields = self.fields
        kind = OBJECT_OF.get(self.op)
        if fields and kind is not None and not fits(kind, fields):
            raise ValueError(f"a {self.op}'s fields don't hold what a {kind}'s hold")
        return self

    def text(self) -> str:
        """The op as its line: keys in the writer's order, empty ones left out (a triage's verdict kept, since null
        clears), compact JSON in UTF-8, the two line separators escaped so no reader splits a line on them."""
        out: dict[str, Any] = {}
        for key in KEY_ORDER:
            value = getattr(self, key)
            if key == "verdict" and self.op == "triage":
                out[key] = value
            elif value is None or (key == "remove" and value is False):
                continue
            elif isinstance(value, datetime):
                out[key] = f"{value:%Y-%m-%dT%H:%M:%SZ}"
            elif isinstance(value, date):
                out[key] = value.isoformat()
            elif key == "fields":
                order = _ORDERS.get(self.op, _ORDERS["set"])
                last = len(order)
                out[key] = {name: value[name] for name in sorted(value, key=lambda name: (order.get(name, last), name))}
            else:
                out[key] = value
        return _dumped(out)


def _dumped(data: dict[str, Any]) -> str:
    """A line's text: compact JSON in UTF-8, the two line separators escaped so no reader splits a line on them."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


_VALIDATE = cast(Any, Op).__pydantic_validator__.validate_json


def read_op(text: str) -> Op:
    """An op from its line, held to the rules above; a ValidationError when it isn't one."""
    return cast(Op, _VALIDATE(text))


def rel_of(op: Op) -> str:
    """The file an op goes in: its machine's, for the month of its day."""
    return f"{OPS_DIR}/{op.day:%Y-%m}-{op.machine}{SUFFIX}"


# -- ids --------------------------------------------------------------------------------------------------------


def encode(n: int) -> str:
    chars: list[str] = []
    for _ in range(26):
        chars.append(ALPHABET[n & 31])
        n >>= 5
    return "".join(reversed(chars))


def decode(text: str) -> int:
    n = 0
    for c in text:
        n = n * 32 + _VALUE[c]
    return n


def mint(highest: str | None, now: datetime, count: int, rand: Callable[[int], bytes] = os.urandom) -> list[str]:
    """Ids for `count` new ops of one write (DR-0612): a fresh ULID, or, when that isn't above the highest op id the
    ledger holds, one at the millisecond after that id's with fresh random bits; each next op the one before plus one.
    Fresh random bits, never the highest plus one, so two clones that pulled the same op from a clock running ahead
    still mint different ids."""
    first = (int(now.timestamp() * 1000) << RANDOM_BITS) | int.from_bytes(rand(RANDOM_BYTES), "big")
    if highest is not None and first <= decode(highest):
        first = (((decode(highest) >> RANDOM_BITS) + 1) << RANDOM_BITS) | int.from_bytes(rand(RANDOM_BYTES), "big")
    return [encode(first + k) for k in range(count)]


# -- a forget's mask (DR-0693) ----------------------------------------------------------------------------------

MARK = "[forgotten]"
# What a mask writes: the marker, or a locator's, staging's digest of its value, so the adds of one event still make one
# meeting and a material's remove still finds its add, while no rid reads as it.
MARKED = re.compile(r"^\[forgotten(?::[0-9a-f]{32})?\]$")
# The keys and the names in fields whose text a mask takes, and the locators it digests. A name this Docket doesn't
# know is taken too, at any depth, and the keys nested under it digested: a later Docket's text.
TEXT_KEYS = ("note", "label")
TEXT_FIELDS = (
    "title", "who", "description", "notes", "outcome", "subject", "organiser", "where", "text", "answer", "next",
)  # fmt: skip
LOCATOR_KEYS = ("ref",)
LOCATOR_FIELDS = ("source", "event", "series")
ATTENDEES = "attendees"
# What a mask keeps as it is: ids, numbers, days, times, kinds, verdicts, states, responses, streams, a stream's name
# and status, by, via, machine and booleans.
KEPT_KEYS = frozenset(KEY_ORDER) - {*TEXT_KEYS, *LOCATOR_KEYS, "fields"}
KEPT_FIELDS = frozenset((*FIELDS, *MEETING_FIELDS, *STREAM_FIELDS, *POINT_FIELDS)) - {
    *TEXT_FIELDS, *LOCATOR_FIELDS, ATTENDEES,
}  # fmt: skip


def _digested(text: str) -> str:
    """A locator, or a key nested in a value, as staging's digest of it, so two of them stay apart; a marker kept."""
    return text if MARKED.fullmatch(text) else f"[forgotten:{digest(text)}]"


def _marked(value: Any) -> Any:
    """Every string in a value as the marker and every key in it digested, since a later Docket's key may hold words
    too; a marker kept, and the value's shape and every other value as they were."""
    if isinstance(value, str):
        return value if MARKED.fullmatch(value) else MARK
    if isinstance(value, list):
        return [_marked(one) for one in cast(list[Any], value)]
    if isinstance(value, dict):
        return {_digested(name): _marked(one) for name, one in cast(dict[str, Any], value).items()}
    return value


def _located(value: Any) -> Any:
    return _digested(value) if isinstance(value, str) else _marked(value)


def masked_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """An op's fields as its mask leaves them: what the writer asks of a change to a shell (DR-0696)."""
    out: dict[str, Any] = {}
    for name, value in fields.items():
        if name in KEPT_FIELDS:
            out[name] = value
        elif name in LOCATOR_FIELDS:
            out[name] = _located(value)
        elif name == ATTENDEES and isinstance(value, list):
            out[name] = []
        else:
            out[name] = _marked(value)
    return out


def mask(text: str) -> str:
    """An op's line as a forget leaves it (DR-0693), from the line of a JSON object: its keys in their order, its text
    and any string under a name this Docket doesn't know `[forgotten]`, its locators and any key nested under such a
    name `[forgotten:<32 hex>]`, its attendees none, every other value as it was. Idempotent: a value or a key that
    is a marker already stays, so a mask's mask is the mask, and a line a merge brought back beside its mask masks to
    that same text."""
    out: dict[str, Any] = {}
    for key, value in cast(dict[str, Any], json.loads(text)).items():
        if key in KEPT_KEYS:
            out[key] = value
        elif key in LOCATOR_KEYS:
            out[key] = _located(value)
        elif key == "fields" and isinstance(value, dict):
            out[key] = masked_fields(cast(dict[str, Any], value))
        else:
            out[key] = _marked(value)
    return _dumped(out)


# The writer's lines begin with their id, so one whose id isn't listed is passed over without reading its JSON.
_FIRST = re.compile(r'\{"schema_version":1,"id":"([0-7][0-9A-HJKMNP-TV-Z]{25})"')


def _listed_id(raw: str, listed: Collection[str]) -> str | None:
    """The op id a line names, when the list holds it. A line the writer wrote, its id first and once, is read that
    far; any other is read as JSON, so a line that isn't a JSON object names no op."""
    first = _FIRST.match(raw)
    if first is not None and first.group(1) not in listed and raw.count('"id":') == 1:
        return None
    found = id_of(raw)
    return found if found is not None and found in listed else None


def listed_lines(text: str, listed: Collection[str]) -> Iterator[tuple[int, str, str]]:
    """Each line of an op file's raw text whose op id is listed, by its number, with its mask."""
    if not listed:
        return
    for n, raw in enumerate(text.split("\n"), start=1):
        if _listed_id(raw, listed) is not None:
            yield n, raw, mask(raw)


def masked_file(text: str, listed: Collection[str]) -> str | None:
    """An op file's text as a forget leaves it: each listed op's line as its mask, and one that masks to a line before
    it dropped, as a union merge leaves an op's text beside its mask (DR-0695). None when that changes nothing."""
    masks = {n: masked for n, _, masked in listed_lines(text, listed)}
    if not masks:
        return None
    out: list[str] = []
    seen: set[str] = set()
    for n, raw in enumerate(text.split("\n"), start=1):
        masked = masks.get(n)
        if masked is None:
            out.append(raw)
        elif masked not in seen:
            seen.add(masked)
            out.append(masked)
    left = "\n".join(out)
    return None if left == text else left


def masked_ids(root: Path) -> frozenset[str]:
    """The op ids the forgotten list in force names, each read as its mask (DR-0695): a vault's, HEAD's lines and the
    disk's; a folder that isn't a vault's, its disk's alone. A list that doesn't read stops the reader, as it stops
    every reader of raw."""
    if (root / ".git").exists() or (root / CONFIG_FILE).exists():
        return forgotten.at(root).ops
    return forgotten.load(root, None).ops


# -- reading ----------------------------------------------------------------------------------------------------


class Line(NamedTuple):
    """An op as it was read: its text, which orders two ops of one id, and where it was. A tuple, which a fold of
    70,000 lines makes at half a frozen dataclass's cost."""

    op: Op
    text: str
    rel: str
    n: int

    @property
    def where(self) -> str:
        return f"{self.rel}:{self.n}"


@dataclass(frozen=True)
class Read:
    """Every line that reads, a problem (where, what) for each one or file that doesn't, and the highest op id any
    line holds, one this Docket can't read or that sits in the wrong file included: the clock never mints below a
    newer Docket's ops (DR-0612)."""

    lines: tuple[Line, ...]
    problems: tuple[tuple[str, str], ...]
    highest: str | None = None


def linked(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def folder(root: Path) -> Path:
    """The op folder, never through a link: what is written there is what every clone folds."""
    path = root / OPS_DIR
    if linked(path):
        raise DocketError(f"{OPS_DIR} is a link; the ledger is read and written only as plain files in the vault")
    return path


def utf8(path: Path) -> str | None:
    """An op file's raw text, None when it isn't UTF-8: every reader skips such a file, and `docket vault check` names
    it."""
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return None


def text_of(path: Path, rel: str) -> str:
    """An op file's text. One that isn't UTF-8 is refused, naming it: a write appends to it."""
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise DocketError(f"{rel} isn't UTF-8; fix or delete it, then write again") from None


def id_of(raw: str) -> str | None:
    """The op id a line that doesn't read as an op still names, if it names one."""
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    found = cast(dict[str, object], data).get("id") if isinstance(data, dict) else None
    return found if isinstance(found, str) and ULID.fullmatch(found) else None


def parse(text: str, rel: str, masked: Collection[str] = frozenset()) -> Read:
    """The lines of an op file that read and sit in the right file, each one whose op id `masked` holds read as its
    mask (DR-0695), and a problem for each other one, by number. Lines are split on LF alone: an op's text never holds
    one."""
    lines: list[Line] = []
    problems: list[tuple[str, str]] = []
    ids: list[str] = []
    # The file's month and machine, compared field by field: formatting each op's file name costs as much as reading it.
    m = FILE_NAME.fullmatch(rel.rsplit("/", 1)[-1])
    year, month, machine = (int(m.group(1)), int(m.group(2)), m.group(3)) if m else (0, 0, "")
    validate = _VALIDATE
    append = lines.append
    for n, read in enumerate(text.split("\n"), start=1):
        if not read or read.isspace():
            continue
        raw = read
        try:
            op: Op = validate(raw)
            if op.id in masked:
                raw = mask(raw)
                op = validate(raw)
        except ValidationError:
            problems.append((f"{rel}:{n}", "isn't a ledger op of schema 1; skipped"))
            unread = id_of(raw)
            if unread is not None:
                ids.append(unread)
            continue
        ids.append(op.id)
        day = op.day
        if day.month != month or day.year != year or op.machine != machine:
            problems.append((f"{rel}:{n}", f"belongs in {rel_of(op)}, by its day and machine; skipped"))
            continue
        append(Line(op, raw, rel, n))
    return Read(tuple(lines), tuple(problems), max(ids, default=None))


def _op_file(path: Path) -> bool:
    m = FILE_NAME.fullmatch(path.name)
    if m is None or not path.is_file() or linked(path):
        return False
    try:
        machines.validate(m.group(3))
    except DocketError:
        return False
    return True


@dataclass(frozen=True)
class FileRead:
    """One op file as `read_all` read it, for the index (DR-0616): its path from the vault's root, its modification
    time taken before the read, its size and sha256, the highest op id a line of it names, and how many lines read."""

    rel: str
    mtime_ns: int
    size: int
    sha256: str
    highest: str | None
    lines: int


def listing(root: Path) -> tuple[list[tuple[str, Path]], list[tuple[str, str]]]:
    """The op files every reader reads, by their path from the vault's root in name order, and a problem for each
    other file in the folder. A folder that is a link holds none, and is said."""
    path = root / OPS_DIR
    if linked(path):
        return [], [(OPS_DIR, "is a link; nothing in it is read")]
    files: list[tuple[str, Path]] = []
    problems: list[tuple[str, str]] = []
    for child in sorted(path.iterdir()) if path.is_dir() else []:
        rel = f"{OPS_DIR}/{child.name}"
        if child.name == KEEP:
            continue
        if not _op_file(child):
            problems.append((rel, "isn't an op file (<yyyy-mm>-<machine>.jsonl); skipped"))
            continue
        files.append((rel, child))
    return files, problems


def read_all(root: Path, files: list[FileRead] | None = None, masked: Collection[str] | None = None) -> Read:
    """Every machine's op files, each op `masked` holds as its mask, the forgotten list's when none is given
    (DR-0695). A file there not named `<yyyy-mm>-<machine>.jsonl`, a link, or one that isn't UTF-8 is a problem and is
    skipped; a folder that is a link is read as empty, and said. With `files`, each file read is added to it as it was
    read."""
    hidden = masked_ids(root) if masked is None else masked
    listed, problems = listing(root)
    lines: list[Line] = []
    ids: list[str] = []
    for rel, child in listed:
        # Taken before the read, so a file written while it is read looks changed to the next update.
        mtime = child.stat().st_mtime_ns if files is not None else 0
        data = child.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            problems.append((rel, "isn't UTF-8; skipped"))
            continue
        found = parse(text, rel, hidden)
        if files is not None:
            sha = hashlib.sha256(data).hexdigest()
            files.append(FileRead(rel, mtime, len(data), sha, found.highest, len(found.lines)))
        lines += found.lines
        problems += found.problems
        if found.highest is not None:
            ids.append(found.highest)
    return Read(tuple(lines), tuple(problems), max(ids, default=None))
