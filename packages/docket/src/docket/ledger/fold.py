"""Folding the ledger's ops into items (DR-0611 to DR-0615): each item from its own ops, in id order.

The fold never reads the clock: every date comes from an op's day, so a rebuild on any day, on any machine, gives the
same ledger. Lines are taken by id; a line twice is one op, and one id on two texts keeps both, ordered by their text
(DR-0612). The op whose item is its own id makes the object and says what it is (DR-0620): an add's ops fold here into
an item, a meeting's and a stream's in `meetings`; one of a kind this Docket doesn't know is kept and folds into
nothing, and any other op naming an item no add made is left out. Per item:

1. Its add is the first add by id and text; an op with no add, or sorting before it, is left out.
2. Every undo's target is checked first: one naming an op that isn't an earlier one of its item, a renumber or a bad
   undo is left out (DR-0614). Then undos are read from the latest back, each leaving out the op it names unless it
   is undone itself. An undone add hides the item.
3. The rest replay in order. An op that meets a state it can't change (a second done after a merge) is idle: it
   changes nothing and stays in the log. A kind, or a name in `fields`, this Docket doesn't know changes nothing.

Numbers come last, across items (DR-0613): the add an item stands on and its renumbers claim numbers, the claim that
sorts first keeps one, and an item whose latest claim lost holds none until a renumber gives it one. A log line's
words are made when they are read.
"""

from __future__ import annotations

import gc
import re
from collections.abc import Generator, Iterable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, cast

from docket.ledger.ops import DAYS, MAKES, OPS, REQUIRED, FileRead, Line, Op, fits, read_all

if TYPE_CHECKING:
    from docket.ledger.meetings import Meeting, Objects, Stream

CLOSED = ("done", "dropped")
# done and drop are one kind for the undo that also undoes what the undone op left idle (DR-0614).
FAMILY = {"drop": "done"}
SHORT = 160
# What the log shows for an op whose words don't hang on its keys; a kind this Docket doesn't know shows its name.
WORDS = {"add": "added", "log": "", "done": "done", "drop": "dropped", "reopen": "reopened", "archive": "archived",
         "meeting": "added", "stream": "added"}  # fmt: skip
SETTABLE = ("kind", "title", "who", "stream", "source", "meeting", "opened", "due", "description", "notes", "outcome")
KEPT = (*REQUIRED, "opened")
# An item's number as a change or a command names it: A-0302, a-302 or 302.
NUMBER = re.compile(r"^(?:A-?)?(\d{1,9})$", re.IGNORECASE)


def family(kind: str) -> str:
    return FAMILY.get(kind, kind)


def number_text(number: int) -> str:
    return f"A-{number:04d}"


@dataclass(frozen=True)
class Material:
    ref: str
    label: str | None = None


@dataclass(frozen=True, slots=True)
class Entry:
    """One op in an item's log: whether it changed anything, the undo that left it out, and for an undo the op it
    undid. Its line is what the log shows: None for an imported op other than a log line (DR-0611)."""

    op: Op
    applied: bool
    undone_by: str | None = None
    target: Op | None = None

    @property
    def line(self) -> str | None:
        op = self.op
        if op.via == "import" and op.op != "log":
            return None
        words = f"undo: {describe(self.target) or self.target.op}" if self.target is not None else describe(op)
        if op.note:
            words = f"{words}: {op.note}" if words else op.note
        return words if self.applied or self.undone_by is not None else f"{words} (nothing to do)"


@dataclass
class Item:
    id: str
    # The number it holds; None while a clash waits for its renumber. `claimed` is its latest claim's.
    number: int | None
    claimed: int
    kind: str
    title: str
    who: str
    stream: str
    source: str
    opened: date
    added_by: str
    # The meeting it hangs on, by the id of an add of that meeting (DR-0626).
    meeting: str | None = None
    due: date | None = None
    description: str | None = None
    notes: str | None = None
    outcome: str | None = None
    state: str = "open"
    until: date | None = None
    closed: date | None = None
    triage: str | None = None
    archived: bool = False
    materials: list[Material] = field(default_factory=list[Material])
    last: str = ""
    log: list[Entry] = field(default_factory=list[Entry])


@dataclass(frozen=True)
class Folded:
    """One item's fold: the item (None when its add is undone), the lines it kept in order, the ops undone and by
    what, the ops that were idle, its number claims as (op id, number), and what was left out."""

    item: Item | None
    lines: tuple[Line, ...]
    undone: dict[str, str]
    idle: frozenset[str]
    claims: tuple[tuple[str, int], ...]
    problems: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Ledger:
    """What the ops add up to: every item whose add stands, every item's fold (hidden ones too), the lines read by
    the add they name, each number's claimants in claim order, the problems, the highest op id and number any op
    holds, and the meetings and streams (DR-0620)."""

    items: dict[str, Item]
    folds: dict[str, Folded]
    lines: dict[str, tuple[Line, ...]]
    holders: dict[int, tuple[str, ...]]
    problems: tuple[tuple[str, str], ...]
    highest_id: str | None
    highest_number: int
    objects: Objects

    @property
    def meetings(self) -> dict[str, Meeting]:
        return self.objects.meetings

    @property
    def streams(self) -> dict[str, Stream]:
        return self.objects.streams

    def clashes(self) -> list[Item]:
        """Items whose latest claim lost its number to an earlier one: each waits for a renumber."""
        return [item for item in self.items.values() if item.number is None]


def _key(line: Line) -> tuple[str, str]:
    return (line.op.id, line.text)


def describe(op: Op) -> str:
    """An op in a few words, as its log line shows it. Not a door: the words can change."""
    kind = op.op
    if kind == "set":
        parts: list[str] = []
        for name, value in (op.fields or {}).items():
            if name in ("description", "notes"):
                parts.append(f"{name} {'cleared' if value is None else 'edited'}")
            elif name == "outcome" and value is not None:
                parts.append(f"outcome: {_short(value)}")
            elif isinstance(value, list):
                parts.append(f"{name}: {', '.join(str(v) for v in cast(list[object], value))}")
            else:
                parts.append(f"{name}: {'-' if value is None else value}")
        return "; ".join(parts)
    if kind in ("point", "point_set"):
        # The meetings' module imports this one, so its words are imported when they are read.
        from docket.ledger.meetings import describe_point  # noqa: PLC0415

        return describe_point(op)
    if kind == "triage":
        return f"triage: {op.verdict}" if op.verdict else "triage cleared"
    if kind == "snooze":
        return f"snoozed to {op.until}"
    if kind == "material":
        return f"material {'removed' if op.remove else 'added'}: {op.label or op.ref}"
    if kind == "renumber":
        return f"renumbered to {number_text(op.number or 0)}"
    return WORDS.get(kind, kind)


def _short(text: str) -> str:
    one = " ".join(text.split())
    return one if len(one) <= SHORT else one[: SHORT - 1] + "…"


def _day(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _new(op: Op) -> Item:
    f = op.fields or {}
    return Item(
        id=op.id,
        number=None,
        claimed=op.number or 0,
        kind=f.get("kind") or "",
        title=f.get("title") or "",
        who=f.get("who") or "",
        stream=f.get("stream") or "",
        source=f.get("source") or "",
        opened=_day(f.get("opened")) or op.day,
        added_by=op.by,
        meeting=f.get("meeting"),
        due=_day(f.get("due")),
        description=f.get("description"),
        notes=f.get("notes"),
        outcome=f.get("outcome"),
    )


def _set(item: Item, fields: dict[str, str | None]) -> bool:
    """The plain fields an op sets; null clears an optional one, and a required one or opened can't be cleared."""
    changed = False
    for name in SETTABLE:
        if name not in fields:
            continue
        value = fields[name]
        if value is None and name in KEPT:
            continue
        new = _day(value) if name in DAYS else value
        if getattr(item, name) != new:
            setattr(item, name, new)
            changed = True
    return changed


def change_material(materials: list[Material], op: Op) -> bool:
    """A material op on an item's or a meeting's materials: added, relabelled or removed; False when it changes
    nothing."""
    ref = op.ref or ""
    current = next((m for m in materials if m.ref == ref), None)
    if op.remove:
        if current is None:
            return False
        materials.remove(current)
        return True
    if current is None:
        materials.append(Material(ref, op.label))
        return True
    if current.label == op.label:
        return False
    materials[materials.index(current)] = Material(ref, op.label)
    return True


def _apply(item: Item, op: Op) -> bool:
    """Apply one op to the item as it stands; False when it meets a state it can't change."""
    kind = op.op
    closed = item.state in CLOSED
    if kind == "set":
        return _set(item, op.fields or {})
    if kind in ("log", "renumber"):
        return True
    if kind == "triage":
        if closed or (item.state == "open" and item.triage == op.verdict):
            return False
        item.state, item.until, item.triage = "open", None, op.verdict
        return True
    if kind == "snooze":
        if closed or (item.state == "snoozed" and item.until == op.until):
            return False
        item.state, item.until, item.triage = "snoozed", op.until, None
        return True
    if kind in ("done", "drop"):
        if closed:
            return False
        item.state, item.closed, item.until, item.triage = ("done" if kind == "done" else "dropped"), op.day, None, None
        return True
    if kind == "reopen":
        if item.state == "open":
            return False
        item.state, item.closed, item.until, item.archived = "open", None, None, False
        return True
    if kind == "material":
        return change_material(item.materials, op)
    if kind == "archive" and closed and not item.archived:
        item.archived = True
        return True
    # An archive that meets an open or archived item, a second add, a kind this Docket doesn't know.
    return False


def undos(kept: list[Line], by_id: dict[str, Op]) -> tuple[list[Line], dict[str, str], list[tuple[str, str]]]:
    """An object's undos (DR-0614): every undo's target is checked first, in order, so a bad undo is named even when a
    later undo undid it, and an undo of a bad undo is bad too; then the undos are read from the latest back, each
    leaving out the op it names unless it is undone itself. The lines kept, each undone op with its undo, and what was
    left out."""
    problems: list[tuple[str, str]] = []
    bad: set[str] = set()
    for line in kept:
        op = line.op
        if op.op != "undo":
            continue
        target = by_id.get(op.of or "")
        if target is None or target.op == "renumber" or target.id >= op.id or target.id in bad:
            problems.append(
                (line.where, "an undo of an op that isn't an earlier one of its item, or of a renumber; left out")
            )
            bad.add(op.id)
    if bad:
        kept = [line for line in kept if line.op.id not in bad]
    undone: dict[str, str] = {}
    for line in reversed(kept):
        op = line.op
        if op.op == "undo" and op.id not in undone:
            undone[op.of or ""] = op.id
    return kept, undone, problems


def maker(item_id: str, group: Sequence[Line]) -> str | None:
    """The kind of the op that makes an object: the first by text of its own lines (whose id is the item's) that is
    an add, a meeting or a stream, else one of a kind this Docket doesn't know; None when no such line makes it."""
    own = sorted((line for line in group if line.op.id == item_id), key=lambda line: line.text)
    known = next((line.op.op for line in own if line.op.op in MAKES), None)
    return known or next((line.op.op for line in own if line.op.op not in OPS), None)


def fold_item(item_id: str, lines: Sequence[Line]) -> Folded:
    """One item from its own lines, in any order."""
    ordered = sorted(lines, key=_key)
    add = next((line for line in ordered if line.op.op == "add"), None)
    if add is None:
        left = tuple((line.where, "names an item no add made; left out") for line in ordered)
        return Folded(None, (), {}, frozenset(), (), left)
    problems: list[tuple[str, str]] = []
    kept = [add]
    for line in ordered:
        if line is add:
            continue
        if line.op.id < add.op.id:
            problems.append((line.where, "sorts before its item's add; left out"))
            continue
        if line.op.op == "set" and not fits("item", line.op.fields or {}):
            problems.append((line.where, "a set whose fields don't hold what an item's hold; left out"))
            continue
        kept.append(line)
    by_id = {line.op.id: line.op for line in kept}
    kept, undone, bad = undos(kept, by_id)
    problems += bad
    # The add the item stands on claims its number, and each renumber after it; a second add with its id claims none.
    claims = (
        (add.op.id, add.op.number or 0),
        *((ln.op.id, ln.op.number or 0) for ln in kept if ln.op.op == "renumber"),
    )
    if add.op.id in undone:
        return Folded(None, tuple(kept), undone, frozenset(), claims, tuple(problems))
    item = _new(add.op)
    idle: set[str] = set()
    log = item.log
    for line in kept:
        op = line.op
        target = by_id.get(op.of) if op.of is not None and op.op == "undo" else None
        if op.id in undone:
            log.append(Entry(op, False, undone[op.id], target))
            continue
        applied = line is add or op.op == "undo" or _apply(item, op)
        if not applied:
            idle.add(op.id)
        log.append(Entry(op, applied, None, target))
        item.last = op.id
    return Folded(item, tuple(kept), undone, frozenset(idle), claims, tuple(problems))


def fold(lines: Iterable[Line]) -> Ledger:
    """Every item from every line read."""
    first: dict[str, Line] = {}
    others: dict[str, dict[str, Line]] = {}
    for line in lines:
        seen = first.setdefault(line.op.id, line)
        if seen is not line and seen.text != line.text:
            others.setdefault(line.op.id, {seen.text: seen})[line.text] = line
    problems: list[tuple[str, str]] = []
    by_item: dict[str, list[Line]] = {}
    for op_id, line in first.items():
        versions = others.get(op_id)
        if versions is None:
            by_item.setdefault(line.op.item, []).append(line)
            continue
        places = ", ".join(sorted(v.where for v in versions.values()))
        problems.append((places, f"op {op_id} is on {len(versions)} texts; each is kept, ordered by its text"))
        for version in versions.values():
            by_item.setdefault(version.op.item, []).append(version)
    item_groups: dict[str, list[Line]] = {}
    object_groups: dict[str, list[Line]] = {}
    for item_id, group in sorted(by_item.items()):
        kind = maker(item_id, group)
        if kind in ("meeting", "stream"):
            object_groups[item_id] = group
        elif kind is None or kind == "add":
            item_groups[item_id] = group
        # Else a later Docket's object: its lines are kept and fold into nothing (DR-0620).
    folds = {item_id: fold_item(item_id, group) for item_id, group in item_groups.items()}
    claims: list[tuple[str, int, str]] = []
    for item_id, folded in folds.items():
        problems += folded.problems
        if folded.item is not None:
            claims += [(op_id, number, item_id) for op_id, number in folded.claims]
    claims.sort()
    holders: dict[int, list[str]] = {}
    for _, number, item_id in claims:
        named = holders.setdefault(number, [])
        if item_id not in named:
            named.append(item_id)
    items: dict[str, Item] = {}
    for item_id, folded in folds.items():
        item = folded.item
        if item is None:
            continue
        latest = folded.claims[-1][1]
        holds = holders[latest][0] == item_id
        item.claimed, item.number = latest, latest if holds else None
        items[item_id] = item
        if not holds:
            problems.append((number_text(latest), f"held by two items; the next write gives {item_id} a new number"))
    every = [number for folded in folds.values() for _, number in folded.claims]
    # The meetings' module imports this one, so it is imported when a fold runs.
    from docket.ledger.meetings import fold_objects  # noqa: PLC0415

    objects = fold_objects(object_groups)
    return Ledger(
        items=items,
        folds=folds,
        lines={item_id: tuple(group) for item_id, group in by_item.items()},
        holders={number: tuple(named) for number, named in holders.items()},
        problems=(*problems, *objects.problems),
        highest_id=max(first) if first else None,
        highest_number=max(every, default=0),
        objects=objects,
    )


@contextmanager
def paused() -> Generator[None]:
    """The cyclic collector paused: a fold makes tens of thousands of objects, none of them in a cycle, and each pass
    of it would walk them all again. Nested, the outermost turns it back on."""
    was = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was:
            gc.enable()


def load(root: Path, files: list[FileRead] | None = None) -> Ledger:
    """The vault's ledger: every op file read and folded, the reading's problems first, and the highest id any line
    holds, one this Docket can't read included (DR-0612), with the collector paused. With `files`, each file read
    is added to it, as the index keeps them (DR-0616)."""
    with paused():
        read = read_all(root, files)
        ledger = fold(read.lines)
    highest = max((i for i in (ledger.highest_id, read.highest) if i is not None), default=None)
    return replace(ledger, problems=read.problems + ledger.problems, highest_id=highest)
