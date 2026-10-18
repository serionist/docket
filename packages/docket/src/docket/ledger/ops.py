"""The ledger's op line (DR-0610 to DR-0615): one JSON line per change, in `ledger/ops/<yyyy-mm>-<machine>.jsonl`.

    {"schema_version":1,"id":"01JAB3W8Q4X5M2Z7K9R0T6YVNC","item":"01JAB3W8Q4X5M2Z7K9R0T6YVNC","op":"add",
     "day":"2026-10-18","at":"2026-10-18T09:14:02Z","machine":"desk","by":"claude","via":"cli",
     "batch":"01JAB3W8Q4X5M2Z7K9R0T6YVNC","number":302,"fields":{"kind":"reply","title":"Send Ada the dates",
     "who":"Ada","stream":"pilot","source":"chat:C0X/1"}}

| op | its own keys | what it does |
|---|---|---|
| add | number, fields (kind, title, who, stream, source; opened, due, description, notes, outcome) | a new item |
| set | fields, null clearing an optional one | the plain fields |
| log | note | a log line |
| triage | verdict, or null | an open or snoozed item open with it, or cleared |
| snooze | until | snoozed |
| done, drop | note | closed on the op's day |
| reopen, archive | - | open again; a closed item out of the day's lists |
| material | ref, label, remove | a path, URL or locator added, relabelled or removed |
| undo | of | the op it names left out of the fold (DR-0614) |
| renumber | number | a new number after a clash (DR-0613) |

Every op names one item, its add's id. An op's id is a ULID minted as a hybrid logical clock (DR-0612), and the
ledger's order is the ids' order. A line that doesn't read is a problem and is skipped; a key, a name in `fields` or
an op kind this Docket doesn't know is read and changes nothing, so a later Docket can add one in schema 1; a name in
`fields` this Docket knows holds text or null, one it doesn't any value. Kinds, verdicts, streams, `by` and `via` are
read as text and checked when written. The format is a one-way door: a later one writes schema_version 2.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from docket.errors import DocketError
from docket.vault import machine as machines

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
OPS = ("add", "set", "log", "triage", "snooze", "done", "drop", "reopen", "material", "archive", "undo", "renumber")
FIELDS = ("kind", "title", "who", "stream", "source", "opened", "due", "description", "notes", "outcome")
REQUIRED = ("kind", "title", "who", "stream", "source")
DAYS = ("opened", "due")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UTC_OFFSET = timedelta(0)
# A line's keys in the order the writer puts them, so one op has one text.
KEY_ORDER = (
    "schema_version", "id", "item", "op", "day", "at", "machine", "by", "via", "batch", "after", "number", "fields",
    "verdict", "until", "ref", "label", "remove", "of", "note",
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
KNOWN_FIELDS = frozenset(FIELDS)


def _add(op: Op) -> bool:
    fields = op.fields or {}
    return op.number is not None and op.item == op.id and all(fields.get(name) for name in REQUIRED)


# What each kind this Docket knows must carry for the fold to apply it; a kind it doesn't know needs nothing.
SHAPES: dict[str, Callable[[Op], bool]] = {
    "add": _add,
    "set": lambda op: bool(op.fields),
    "log": lambda op: bool(op.note),
    "snooze": lambda op: op.until is not None,
    "material": lambda op: bool(op.ref),
    "undo": lambda op: op.of is not None,
    "renumber": lambda op: op.number is not None,
}


class Op(BaseModel):
    """One op, as one line of an op file. Strict, so a number written as text or a day as a number doesn't read."""

    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

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
    # A name this Docket knows holds text or null; a later Docket's name may hold any JSON value, kept unread.
    fields: dict[str, Any] | None = None
    verdict: str | None = None
    until: date | None = None
    ref: str | None = None
    label: str | None = None
    remove: bool = False
    of: Ulid | None = None
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
        if fields:
            for name, value in fields.items():
                if value is not None and name in KNOWN_FIELDS and not isinstance(value, str):
                    raise ValueError(f"{name} is text or null")
            for name in DAYS:
                value = fields.get(name)
                if value is not None and not (DAY.fullmatch(value) and is_day(value)):
                    raise ValueError(f"{name} is a day, YYYY-MM-DD")
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
                out[key] = {name: value[name] for name in sorted(value, key=_field_order)}
            else:
                out[key] = value
        text = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
        return text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def is_day(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _field_order(name: str) -> tuple[int, str]:
    return (FIELDS.index(name) if name in FIELDS else len(FIELDS), name)


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


# -- reading ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    """An op as it was read: its text, which orders two ops of one id, and where it was."""

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


def parse(text: str, rel: str) -> Read:
    """The lines of an op file that read and sit in the right file, and a problem for each other one, by number.
    Lines are split on LF alone: an op's text never holds one."""
    lines: list[Line] = []
    problems: list[tuple[str, str]] = []
    ids: list[str] = []
    # The file's month and machine, compared field by field: formatting each op's file name costs as much as reading it.
    m = FILE_NAME.fullmatch(rel.rsplit("/", 1)[-1])
    year, month, machine = (int(m.group(1)), int(m.group(2)), m.group(3)) if m else (0, 0, "")
    for n, raw in enumerate(text.split("\n"), start=1):
        if not raw.strip():
            continue
        try:
            op = Op.model_validate_json(raw)
        except ValidationError:
            problems.append((f"{rel}:{n}", "isn't a ledger op of schema 1; skipped"))
            unread = id_of(raw)
            if unread is not None:
                ids.append(unread)
            continue
        ids.append(op.id)
        if op.day.month != month or op.day.year != year or op.machine != machine:
            problems.append((f"{rel}:{n}", f"belongs in {rel_of(op)}, by its day and machine; skipped"))
            continue
        lines.append(Line(op, raw, rel, n))
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


def read_all(root: Path) -> Read:
    """Every machine's op files. A file there not named `<yyyy-mm>-<machine>.jsonl`, a link, or one that isn't UTF-8
    is a problem and is skipped; a folder that is a link is read as empty, and said."""
    path = root / OPS_DIR
    if linked(path):
        return Read((), ((OPS_DIR, "is a link; nothing in it is read"),))
    lines: list[Line] = []
    problems: list[tuple[str, str]] = []
    ids: list[str] = []
    for child in sorted(path.iterdir()) if path.is_dir() else []:
        rel = f"{OPS_DIR}/{child.name}"
        if child.name == KEEP:
            continue
        if not _op_file(child):
            problems.append((rel, "isn't an op file (<yyyy-mm>-<machine>.jsonl); skipped"))
            continue
        try:
            text = child.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            problems.append((rel, "isn't UTF-8; skipped"))
            continue
        found = parse(text, rel)
        lines += found.lines
        problems += found.problems
        if found.highest is not None:
            ids.append(found.highest)
    return Read(tuple(lines), tuple(problems), max(ids, default=None))
