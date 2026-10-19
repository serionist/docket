"""Importing a ledger with its history (#202, DR-0653 to DR-0656): `docket ledger import <file>`.

The file is JSON lines, each an op line as DR-0611 writes it, with `via: import`, the export's own ids and, by the
export's habit, `day` alone. `machine`, `batch` and `after` are the writer's, so the import sets them: this machine,
the first op id of the op's month, and the op before it of the same object. A line carrying one of them, or another
`via`, refuses the file. Every value passes the text rules a live write passes (`store.TextRules`).

Each add's id is 2000-01-01 plus its number in milliseconds (DR-0658), before any id Docket mints, so every number
of the file holds against a claim Docket made first (DR-0613); a file whose adds aren't is refused.

The ledger may hold the file's ops, each line for line as the import would write it, which are skipped, so a rerun
writes nothing and an import stopped after a month finishes, an op the forgotten list masks masked before the text
rules see it, then compared and written so, and no rerun after a forget brings its text back or stops on it (DR-0696);
and objects of its own that name none of the file's, such as the calendar step's meetings (DR-0647). An op from
elsewhere naming one of the file's objects, or a held line with another text, refuses the import: revert first
(DR-0656). So does a problem `docket vault check` would name in the fold of the result that the ledger didn't have
already, and a number one of the file's objects would lose; one the ledger's own object loses goes through, and the
import's last write renumbers that object (DR-0613).

The months are written oldest first, each one write through `store.py` and one commit. Old meetings come in keyless,
and `docket ledger calendar` joins them afterwards (DR-0655); the dry run shows those joins by running the catch-up's
own dry run over the ledger as the import would leave it. It also counts what the file brings, for the export to check
against the old ledger's own counts (DR-0654).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any, cast

from pydantic import ValidationError

from docket import recall
from docket.index.build import ledger_tables, ledger_tables_of
from docket.ledger import calendar, fold, ops, store
from docket.ledger.fold import Ledger, number_text
from docket.ledger.meetings import Meeting, ObjectFold, meeting_text
from docket.ledger.ops import Line, Op
from docket.vault import forgotten
from docket.vault.vault import Vault

# The keys the writer sets, never the export (DR-0653).
WRITERS = ("machine", "batch", "after")
# A field naming an op or an object by its id: never text, never screened.
IDS = ("item", "meeting", "continues", "from", "to")
# One line of text, as the writer keeps it (`store.one_line`).
ONE_LINE = (*store.ONE_LINE, "note", "label")
# The words a fold's problem uses for a number two objects claim: `_problems` says whose it is.
CLASH = ("held by two items;", "held by two meetings;")
NO_SCHEME = "(no scheme)"
# An add's id is this time plus its number in ms, a stream's this time itself: before any id Docket mints, so an old
# number holds against a claim Docket made before the import (DR-0658).
ADDS_AT = int(datetime(2000, 1, 1, tzinfo=UTC).timestamp() * 1000)


@dataclass(frozen=True)
class Counted:
    """What the dry run counts of the file's objects in the ledger as the import would leave it (DR-0654)."""

    items: dict[str, dict[str, int]] = field(default_factory=dict[str, dict[str, int]])
    archived_items: int = 0
    log_entries: dict[str, int] = field(default_factory=dict[str, int])
    meetings: int = 0
    archived_meetings: int = 0
    points: dict[str, int] = field(default_factory=dict[str, int])
    chains: list[str] = field(default_factory=list[str])
    naming_a_meeting: int = 0
    streams: dict[str, int] = field(default_factory=dict[str, int])
    next_item: str = ""
    next_meeting: str = ""
    not_from_the_file: dict[str, int] = field(default_factory=dict[str, int])
    renumbered: list[str] = field(default_factory=list[str])
    unresolved: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    joins: list[str] = field(default_factory=list[str])
    near_misses: list[str] = field(default_factory=list[str])
    idle: dict[str, int] = field(default_factory=dict[str, int])


@dataclass(frozen=True)
class Plan:
    """An import as planned: the ops to write a month at a time, oldest first, each in id order; how many the ledger
    holds already; every refusal, by place; and with a report, the counts."""

    months: tuple[tuple[Op, ...], ...]
    held: int
    refusals: tuple[str, ...]
    counted: Counted | None = None

    @property
    def ops(self) -> int:
        return sum(len(month) for month in self.months)


@dataclass(frozen=True)
class Imported:
    """What `run` did: the plan, and unless it was a dry run or refused, the month commits and the renumber's."""

    plan: Plan
    dry_run: bool
    commits: tuple[str, ...] = ()
    renumbered: str | None = None


class _Lines:
    """The file's lines read: each as the import would write it, by its id, with the place it came from."""

    def __init__(self) -> None:
        self.ops: dict[str, tuple[Op, str, int]] = {}
        self.refusals: list[str] = []


def run(vault: Vault, text: str, name: str, *, dry_run: bool = False) -> Imported:
    """Plan the import of `text`, an export's lines read from `name`, and unless it is a dry run or refused, write it a
    month at a time, renumber what lost a number to it, and bring the index's ledger tables up to date, all under the
    vault lock (DR-0653). A dry run counts what it would bring (DR-0654)."""
    with vault.writing():
        planned = plan(vault, text, name, report=dry_run)
        if dry_run or planned.refusals:
            return Imported(planned, dry_run)
        commits = store.write_import(vault, planned.months)
        renumbered = store.write(vault, [], by="claude", via="import").commit
        with ledger_tables(vault):
            pass
    return Imported(planned, dry_run, commits, renumbered)


def plan(vault: Vault, text: str, name: str, *, report: bool = False) -> Plan:
    """The import of `text` as it would be written, each refusal by its line, and with `report` the dry run's counts.
    Lines that don't read are refused before anything is folded: the fold of a file missing them would only name what
    hangs on them."""
    listed = forgotten.load(vault.root, vault.home).ops
    with fold.paused():
        read = _read(vault, text, name, listed)
    refusals = read.refusals
    if refusals:
        return Plan((), 0, tuple(refusals))
    held = ops.read_all(vault.root, masked=listed)
    texts: dict[str, set[str]] = {}
    for line in held.lines:
        texts.setdefault(line.op.id, set()).add(line.text)
    fresh: list[Line] = []
    skipped = 0
    for op_id, (op, written, n) in sorted(read.ops.items()):
        if op_id in texts:
            if written in texts[op_id]:
                skipped += 1
            else:
                refusals.append(f"{name}:{n}: op {op_id} is held with another text; revert, then import again")
            continue
        fresh.append(Line(op, written, name, n))
    ids = set(read.ops)
    objects = {op.item for op, _, _ in read.ops.values()}
    elsewhere = [line for line in held.lines if line.op.id not in ids]
    touching = [line for line in elsewhere if _names(line.op, ids)]
    if touching:
        refusals.append(
            f"{len(touching):,} op(s) not from the file name its objects ({touching[0].where} first); "
            "revert, then import again"
        )
    if refusals:
        return Plan((), skipped, tuple(refusals))
    with fold.paused():
        ledger = fold.fold([*held.lines, *fresh])
        before = fold.fold(held.lines)
    refusals += _problems(ledger, before, objects)
    months: dict[str, list[Op]] = {}
    for line in fresh:
        months.setdefault(f"{line.op.day:%Y-%m}", []).append(line.op)
    counted = None
    if report and not refusals:
        counted = _count(vault, ledger, objects=objects, ids=ids, elsewhere=elsewhere)
    return Plan(tuple(tuple(months[m]) for m in sorted(months)), skipped, tuple(refusals), counted)


# -- reading the file ---------------------------------------------------------------------------------------------


def _read(vault: Vault, text: str, name: str, masked: Collection[str]) -> _Lines:
    """Each line as an op the import would write: the writer's keys set, its values screened, read as DR-0611 reads
    it; one whose id `masked` holds is cleaned and masked before it is screened. Two lines of one id are one op when
    they say the same, and are refused when they don't."""
    out = _Lines()
    given: dict[str, tuple[int, dict[str, Any]]] = {}
    for n, raw in enumerate(text.split("\n"), start=1):
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            out.refusals.append(f"{name}:{n}: isn't JSON")
            continue
        if not isinstance(data, dict):
            out.refusals.append(f"{name}:{n}: isn't a JSON object")
            continue
        found = cast(dict[str, Any], data)
        why = _refused_keys(found)
        if why is not None:
            out.refusals.append(f"{name}:{n}: {why}")
            continue
        op_id = str(found.get("id"))
        twice = given.get(op_id)
        if twice is None:
            given[op_id] = (n, found)
        elif twice[1] != found:
            out.refusals.append(f"{name}:{n}: op {op_id} is on line {twice[0]} with another text")
    rules = _Screen(vault)
    firsts: dict[str, str] = {}
    before: dict[str, str] = {}
    late: list[int] = []
    for op_id, (n, data) in sorted(given.items()):
        item, day = str(data.get("item")), str(data.get("day"))[:7]
        filled = {**data, "machine": vault.machine, "batch": firsts.setdefault(day, op_id)}
        if item in before:
            filled["after"] = before[item]
        try:
            if op_id in masked:
                # Masked before any rule sees it, so what a forget took can't stop the import that brings its op back,
                # and cleaned first, as the writer cleaned it, so its locators digest as the ledger's mask does.
                filled = json.loads(ops.mask(json.dumps(_screened(filled, rules.cleaned))))
            op = ops.read_op(json.dumps(_screened(filled, rules.text), ensure_ascii=False))
        except store.TextRefusedError as e:
            out.refusals.append(f"{name}:{n}: {e}")
            continue
        except ValidationError as e:
            first = e.errors()[0]
            where = ".".join(str(part) for part in first["loc"]) or "the line"
            out.refusals.append(f"{name}:{n}: isn't a ledger op of schema 1 ({where}: {first['msg']})")
            continue
        if op.by not in ops.BY:
            out.refusals.append(f"{name}:{n}: by is one of {', '.join(ops.BY)}")
            continue
        out.ops[op.id] = (op, op.text(), n)
        before[op.item] = op.id
        if op.op in ops.MAKES and ops.decode(op.id) >> ops.RANDOM_BITS != ADDS_AT + (op.number or 0):
            late.append(n)
    if late:
        out.refusals.append(
            f"{len(late):,} add(s) don't sort early ({name}:{min(late)} first): an export gives each add the id "
            "2000-01-01 plus its number in ms, so every old number holds against one Docket gave (DR-0658)"
        )
    return out


def _refused_keys(data: Mapping[str, Any]) -> str | None:
    writers = [key for key in WRITERS if key in data]
    if writers:
        return f"{', '.join(writers)} {'is' if len(writers) == 1 else 'are'} the writer's; leave it to the import"
    if data.get("via") != "import":
        return "via is import on every line"
    return None


def _screened(data: Mapping[str, Any], text: Callable[[str, str], str | None]) -> dict[str, Any]:
    """A line's values as the writer would write them, each text value through `text`: `_Screen.text`, which cleans,
    redacts and screens it, or `_Screen.cleaned`, which only cleans it; ids as they are."""
    out = dict(data)
    for key in ("note", "label", "ref", "verdict", "until"):
        value = out.get(key)
        if isinstance(value, str):
            out[key] = text(key, value)
    fields = out.get("fields")
    if isinstance(fields, dict):
        screened: dict[str, Any] = {}
        for key, value in cast(dict[str, Any], fields).items():
            if key == "attendees" and isinstance(value, list):
                names = [text("attendee", v) if isinstance(v, str) else v for v in cast(list[Any], value)]
                screened[key] = [v for v in names if v is not None]
            elif isinstance(value, str) and key not in IDS:
                screened[key] = text(key, value)
            else:
                screened[key] = value
        out["fields"] = screened
    return out


class _Screen:
    """The text rules over a file's values, each name and value screened once: an export repeats its people, streams,
    kinds and log lines on line after line."""

    def __init__(self, vault: Vault) -> None:
        self.rules = store.TextRules(vault)
        self.seen: dict[tuple[str, str], tuple[str | None, str | None]] = {}

    @staticmethod
    def cleaned(name: str, value: str) -> str | None:
        """A value cleaned as its name is, as the writer cleans it, before any rule: None when nothing is left."""
        if name in ONE_LINE:
            return store.one_line(value) or None
        return store.clean_text(value) if name in store.TEXTS else value or None

    def text(self, name: str, value: str) -> str | None:
        """A value as the writer writes it, cleaned as its name is and screened (`store.TextRules`)."""
        found = self.seen.get((name, value))
        if found is None:
            text = self.cleaned(name, value)
            try:
                found = (self.rules.screen(name, text) if text else None, None)
            except store.TextRefusedError as e:
                found = (None, str(e))
            self.seen[(name, value)] = found
        if found[1] is not None:
            raise store.TextRefusedError(found[1])
        return found[0]


def _names(op: Op, ids: set[str]) -> bool:
    """Whether an op names one of the file's ops or objects."""
    if op.item in ids or op.point in ids or op.of in ids:
        return True
    fields = op.fields or {}
    return any(isinstance(fields.get(key), str) and fields[key] in ids for key in IDS)


def _problems(ledger: Ledger, before: Ledger, objects: set[str]) -> list[str]:
    """What the fold of the result names that the ledger alone didn't, but a number two objects claim; and each number
    one of the file's objects would lose, naming both claims. A number the ledger's own object loses goes through: the
    import's last write renumbers that object (DR-0613)."""
    had = set(before.problems)
    out = [
        f"{where}: {what}" for where, what in ledger.problems if (where, what) not in had and not what.startswith(CLASH)
    ]
    found = ledger.objects
    for item in ledger.clashes():
        if item.id in objects:
            winner = ledger.holders[item.claimed][0]
            first, lost = (_side(i, ledger.folds[i], item.claimed) for i in (winner, item.id))
            out.append(_lost(number_text(item.claimed), "item", winner in objects, first, lost))
    for meeting in found.clashes():
        if meeting.id in objects:
            winner = found.holders[meeting.claimed][0]
            first, lost = (_side(i, found.folds[i], meeting.claimed) for i in (winner, meeting.id))
            out.append(_lost(meeting_text(meeting.claimed), "meeting", winner in objects, first, lost))
    return out


def _side(obj_id: str, folded: fold.Folded | ObjectFold, number: int) -> str:
    """An object by its id and the place of its first claim of a number: the file's line, or the op file's."""
    op_id = next(op_id for op_id, claimed in folded.claims if claimed == number)
    return f"{obj_id} ({next((line.where for line in folded.lines if line.op.id == op_id), op_id)})"


def _lost(number: str, kind: str, theirs: bool, first: str, lost: str) -> str:
    """A number one of the file's objects would lose, naming the claim that keeps it and the one that loses it."""
    whose = "the file's" if theirs else "the ledger's own"
    return (
        f"{number} is claimed first by {whose} {kind} {first}, so the file's {lost} would lose it; every number "
        "of the file holds (DR-0658)"
    )


# -- what the dry run counts (DR-0654) ----------------------------------------------------------------------------


def _count(vault: Vault, ledger: Ledger, *, objects: set[str], ids: set[str], elsewhere: Sequence[Line]) -> Counted:
    found = ledger.objects
    items = [ledger.items[i] for i in sorted(objects) if i in ledger.items]
    kinds: dict[str, Counter[str]] = {}
    for item in items:
        kinds.setdefault(item.kind, Counter())[item.state] += 1
    logs = {
        number_text(item.claimed): sum(1 for line in ledger.lines[item.id] if line.op.op == "log") for item in items
    }
    meetings = [found.meetings[i] for i in sorted(objects) if i in found.meetings]
    streams = [found.streams[i] for i in sorted(objects) if i in found.streams]
    chains, naming = _chains(ledger, meetings)
    not_ours = Counter(f"{line.op.op} by {line.op.by} via {line.op.via} on {line.op.machine}" for line in elsewhere)
    # A number one of the file's objects loses refuses the import (`_problems`): every loser left is Docket-made.
    renumbered = [f"{number_text(i.claimed)} ({i.id})" for i in ledger.clashes()]
    renumbered += [f"{meeting_text(m.claimed)} ({m.id})" for m in found.clashes()]
    idle: Counter[str] = Counter()
    for fold_of in (*ledger.folds.values(), *found.folds.values()):
        idle.update(line.op.op for line in fold_of.lines if line.op.id in fold_of.idle and line.op.id in ids)
    joins, near = _joins(vault, ledger, meetings)
    return Counted(
        items={kind: dict(sorted(states.items())) for kind, states in sorted(kinds.items())},
        archived_items=sum(1 for item in items if item.archived),
        log_entries=logs,
        meetings=len(meetings),
        archived_meetings=sum(1 for m in meetings if m.archived),
        points=dict(sorted(Counter(p.state for m in meetings for p in m.points).items())),
        chains=chains,
        naming_a_meeting=naming,
        streams=dict(sorted(Counter(s.status for s in streams).items())),
        next_item=number_text(ledger.highest_number + 1),
        next_meeting=meeting_text(found.highest_number + 1),
        not_from_the_file=dict(sorted(not_ours.items())),
        renumbered=sorted(renumbered),
        unresolved=_unresolved(vault, items),
        joins=joins,
        near_misses=near,
        idle=dict(sorted(idle.items())),
    )


def _chains(ledger: Ledger, meetings: Sequence[Meeting]) -> tuple[list[str], int]:
    """Each roll chain of the file's meetings by number, `M-0031#2 > M-0035#1`, from its first point; and how many of
    its ends name a meeting, where the export couldn't pair a point (DR-0623)."""
    found = ledger.objects
    place: dict[str, str] = {}
    for meeting in found.meetings.values():
        for n, point in enumerate(meeting.points, start=1):
            place[point.id] = f"{_label(meeting)}#{n}"

    def meeting_of(add_id: str) -> str:
        held = found.meetings.get(found.owner.get(add_id, add_id))
        return _label(held) if held is not None else add_id

    chains: list[str] = []
    naming = 0
    for meeting in meetings:
        for point in meeting.points:
            source = point.rolled_from
            if source in found.points or (source is None and point.rolled_to is None):
                continue
            parts = [place[point.id]]
            if source is not None:
                parts.insert(0, meeting_of(source))
                naming += 1
            to = point.rolled_to
            while to is not None:
                if to not in place:
                    parts.append(meeting_of(to))
                    naming += 1
                    break
                parts.append(place[to])
                to = found.points[to].rolled_to
            chains.append(" > ".join(parts))
    return chains, naming


def _label(meeting: Meeting) -> str:
    return meeting_text(meeting.number if meeting.number is not None else meeting.claimed)


def _unresolved(vault: Vault, items: Iterable[fold.Item]) -> dict[str, list[str]]:
    """The items' sources `read` finds nothing at, by scheme, each with the items naming it (DR-0131)."""
    by_source: dict[str, list[str]] = {}
    for item in items:
        by_source.setdefault(item.source, []).append(number_text(item.claimed))
    found = recall.resolves(vault, by_source)
    out: dict[str, list[str]] = {}
    for source, numbers in sorted(by_source.items()):
        if source in found:
            continue
        scheme = store.SCHEME.match(source)
        out.setdefault(scheme.group(0) if scheme else NO_SCHEME, []).extend(numbers)
    return {scheme: sorted(numbers) for scheme, numbers in sorted(out.items())}


def _joins(vault: Vault, ledger: Ledger, meetings: Sequence[Meeting]) -> tuple[list[str], list[str]]:
    """The joins and near misses the catch-up's own dry run makes over the ledger as the import would leave it, from
    the first day a meeting of the file starts (DR-0652, DR-0655)."""
    if not meetings:
        return [], []
    first: date = min(m.day for m in meetings)
    with ledger_tables_of(ledger) as conn:
        stepped = calendar.catch_up(vault, first, dry_run=True, tables=conn)
    joins: list[str] = []
    near: list[str] = []
    for outcome in stepped.outcomes:
        when = (outcome.start or "").replace("T", " ")[:16]
        if outcome.what == "joined":
            joins.append(f"{outcome.meeting} {when} {outcome.subject} > {outcome.rid}")
        near += [f"{outcome.meeting} has {other} close by" for other in outcome.near]
    return joins, near


# -- what it says ---------------------------------------------------------------------------------------------------


def lines(imported: Imported) -> list[str]:
    """The import as `docket ledger import` prints it: its refusals, or what it brings and what it wrote."""
    planned = imported.plan
    if planned.refusals:
        return [f"refused: {len(planned.refusals):,}", *(f"  {why}" for why in planned.refusals), "nothing written"]
    months = [f"{month[0].day:%Y-%m}" for month in planned.months]
    span = f" ({months[0]} to {months[-1]})" if len(months) > 1 else f" ({months[0]})" if months else ""
    out = [f"import: {planned.ops:,} op(s) to write in {len(months)} month(s){span}, {planned.held:,} held already"]
    counted = planned.counted
    if counted is not None:
        out += _counted_lines(counted)
    if imported.dry_run:
        out.append("dry run: nothing written")
    elif imported.commits or imported.renumbered:
        renumbered = f", renumbered in {imported.renumbered[:7]}" if imported.renumbered else ""
        last = imported.commits[-1][:7] if imported.commits else "none"
        out.append(f"written in {len(imported.commits)} commit(s), the last {last}{renumbered}")
        out.append("next: docket ledger calendar --since <the calendar's first day> joins the old meetings")
    else:
        out.append("nothing to write: the ledger holds it all")
    return out


def _counted_lines(counted: Counted) -> list[str]:
    total = sum(sum(states.values()) for states in counted.items.values())
    kinds = "; ".join(
        f"{kind} {sum(states.values())} ({', '.join(f'{s} {n}' for s, n in states.items())})"
        for kind, states in counted.items.items()
    )
    out = [f"items: {total:,}" + (f": {kinds}" if kinds else "") + f"; {counted.archived_items:,} archived; next "
           f"{counted.next_item}"]  # fmt: skip
    out.append(f"log entries: {sum(counted.log_entries.values()):,}, each item's in --json")
    points = ", ".join(f"{state} {n}" for state, n in counted.points.items()) or "none"
    out.append(f"meetings: {counted.meetings:,}, {counted.archived_meetings:,} archived; points: {points}; next "
               f"{counted.next_meeting}")  # fmt: skip
    out.append(f"chains: {len(counted.chains):,}, {counted.naming_a_meeting:,} end(s) naming a meeting")
    out += [f"  {chain}" for chain in counted.chains]
    streams = ", ".join(f"{status} {n}" for status, n in counted.streams.items()) or "none"
    out.append(f"streams: {sum(counted.streams.values()):,} ({streams})")
    elsewhere = sum(counted.not_from_the_file.values())
    out.append(f"not from the file: {elsewhere:,}")
    out += [f"  {n:,} {what}" for what, n in counted.not_from_the_file.items()]
    out.append(f"Docket-made, renumbered by its last write: {len(counted.renumbered):,}")
    out += [f"  {what}" for what in counted.renumbered]
    unresolved = sum(len(numbers) for numbers in counted.unresolved.values())
    out.append(f"sources it can't resolve: {unresolved:,}")
    out += [f"  {scheme} {len(numbers):,}: {', '.join(numbers)}" for scheme, numbers in counted.unresolved.items()]
    out.append(f"the catch-up's joins: {len(counted.joins):,}, near misses {len(counted.near_misses):,}")
    out += [f"  joined {join}" for join in counted.joins] + [f"  near {near}" for near in counted.near_misses]
    idle = ", ".join(f"{kind} {n}" for kind, n in counted.idle.items())
    out.append(f"ops that change nothing: {idle or 'none'}")
    return out


def as_json(imported: Imported) -> str:
    """The import as JSON, for an export's comparison: the dry run's counts with each item's log entries, the
    refusals, and what was written."""
    planned = imported.plan
    data: dict[str, Any] = {
        "ops": planned.ops,
        "months": [f"{month[0].day:%Y-%m}" for month in planned.months],
        "held": planned.held,
        "refusals": list(planned.refusals),
        "dry_run": imported.dry_run,
        "commits": list(imported.commits),
        "renumbered": imported.renumbered,
        "counted": None if planned.counted is None else asdict(planned.counted),
    }
    return json.dumps(data, ensure_ascii=False, indent=1)


def revert_lines(reverted: store.Reverted, *, dry_run: bool) -> list[str]:
    """The revert as `docket ledger import --revert` prints it (DR-0656)."""
    if not reverted.files:
        return ["no op files: nothing to revert"]
    vias = ", ".join(f"{via} {n:,}" for via, n in reverted.vias.items()) or "no ops"
    out = [f"{'would take' if dry_run else 'took'} out {len(reverted.files)} op file(s): {vias}"]
    if dry_run:
        out.append("dry run: nothing written")
    elif reverted.commit is not None:
        out.append(f"commit {reverted.commit[:7]}; every clone pulls it before it writes again")
    return out
