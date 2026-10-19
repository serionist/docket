"""Meetings with their talking points, and streams, folded from their ops (DR-0620 to DR-0625).

An object is the adds of one key, a meeting's event or a stream's name, with every op naming any of them; a meeting
with no event is its own until a keyed add continues it, and then its id and number are the object's (DR-0621). Each
object folds from its own lines in id order, its undos first (DR-0614), each later add as a set of its fields.

Points fold across meetings, since a roll copies one onto the next meeting (DR-0623). A copy stands only while its
source stands and the source's winning roll, its first, names it; an op or a `from` naming an idle copy reads as
naming the copy that roll names, so an answer given on a losing copy counts and a copy rolled on from it stands. A
roll whose copy stands nowhere, its meeting removed, is idle, so its point reads as open. A meeting is archived while
its archive stands and no point of it is open, and a set sorting after that archive is idle (DR-0624). Numbers come
last, as items' do (DR-0613): the add a meeting stands on and its renumbers claim, and each other add's number is one
it held, which never wins a number from another meeting; in a join, the key's renumbers are numbers it held too.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, cast

from docket.ledger.fold import Entry, Material, change_material, undos
from docket.ledger.ops import MAKES, MEETING_REQUIRED, REFUSES, Line, Op, fits

# A meeting's fields a set changes: never its key or the meeting it continues, which only an add sets (DR-0621).
MEETING_SETTABLE = (
    "subject", "start", "end", "series", "organiser", "where", "response", "attendees", "stream", "notes", "outcome",
)  # fmt: skip
TIMES = ("start", "end")
STREAM_SETTABLE = ("status", "where", "next", "deadline")


def meeting_text(number: int) -> str:
    return f"M-{number:04d}"


@dataclass
class Point:
    """A talking point. `rolled_from` is the point it was copied from (or, from an import that couldn't pair it, a
    meeting), `rolled_to` the copy its roll made. An idle copy stands nowhere: its ops and the copies rolled on from it
    go to `moved_to`, the copy its source's winning roll names, or nowhere when there is none."""

    id: str
    meeting: str
    text: str
    added_by: str
    day: date
    item: str | None = None
    rolled_from: str | None = None
    rolled_to: str | None = None
    state: str = "open"
    answer: str | None = None
    proposed: bool = False
    stands: bool = True
    moved_to: str | None = None


@dataclass
class Meeting:
    id: str
    # The number it holds; None while a clash waits for its renumber. `claimed` is its latest claim's.
    number: int | None
    claimed: int
    subject: str
    start: datetime
    end: datetime
    added_by: str
    adds: tuple[str, ...]
    event: str | None = None
    series: str | None = None
    organiser: str | None = None
    where: str | None = None
    response: str | None = None
    attendees: tuple[str, ...] = ()
    stream: str | None = None
    notes: str | None = None
    outcome: str | None = None
    materials: list[Material] = field(default_factory=list[Material])
    archived: bool = False
    # Its standing points in order: the nth is `M-0040#n`.
    points: list[Point] = field(default_factory=list[Point])
    last: str = ""
    log: list[Entry] = field(default_factory=list[Entry])

    @property
    def day(self) -> date:
        """The day it starts, as its own offset reads it."""
        return self.start.date()

    def open_points(self) -> list[Point]:
        return [p for p in self.points if p.state == "open"]


@dataclass
class Stream:
    id: str
    name: str
    status: str
    # The day of its latest op that changed a field, so nothing reads the clock (DR-0625).
    updated: date
    added_by: str
    adds: tuple[str, ...]
    where: str | None = None
    next: str | None = None
    deadline: date | None = None
    last: str = ""
    log: list[Entry] = field(default_factory=list[Entry])


@dataclass(frozen=True)
class ObjectFold:
    """One meeting's or stream's fold: what it is (None when every add of it is undone), its adds, the lines it kept
    in order, the ops undone and by what, the ops that were idle, the claims of its number as (op id, number), and the
    numbers its other adds held, with, in a join, the key's renumbers."""

    kind: str
    id: str
    value: Meeting | Stream | None
    adds: tuple[str, ...]
    lines: tuple[Line, ...]
    undone: dict[str, str]
    idle: frozenset[str]
    claims: tuple[tuple[str, int], ...] = ()
    held: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class Objects:
    """Every meeting and stream that stands, every one's fold (hidden ones too), the object each add belongs to, every
    point by id (idle copies too), each M- number's holders (claims first, in claim order, then the meetings whose other
    adds held it), the meeting holding each event and the stream holding each name, the highest M- number any op holds,
    and what was left out."""

    meetings: dict[str, Meeting]
    streams: dict[str, Stream]
    folds: dict[str, ObjectFold]
    owner: dict[str, str]
    points: dict[str, Point]
    holders: dict[int, tuple[str, ...]]
    events: dict[str, str]
    names: dict[str, str]
    highest_number: int
    problems: tuple[tuple[str, str], ...]

    def clashes(self) -> list[Meeting]:
        """Meetings whose latest claim lost its number to an earlier one: each waits for a renumber."""
        return [m for m in self.meetings.values() if m.number is None]


@dataclass
class _Pre:
    """The adds of one key, or one unkeyed meeting, with their lines checked and their undos read, and the meeting
    they continue, if any."""

    kind: str
    key: str | None
    adds: list[str]
    kept: list[Line]
    undone: dict[str, str]
    continued: str | None = None


def _key(line: Line) -> tuple[str, str]:
    return (line.op.id, line.text)


def _maker(add_id: str, group: Sequence[Line]) -> Line:
    """The line that makes the object: the first by text of its own lines whose kind makes one."""
    return min((ln for ln in group if ln.op.id == add_id and ln.op.op in MAKES), key=lambda ln: ln.text)


def fold_objects(groups: Mapping[str, Sequence[Line]]) -> Objects:
    """The meetings and streams the lines make: `groups` holds each meeting's or stream's add id with the lines that
    name it."""
    problems: list[tuple[str, str]] = []
    makers = {add_id: _maker(add_id, group) for add_id, group in groups.items()}
    by_key: dict[tuple[str, str], list[str]] = {}
    alone: list[str] = []
    for add_id in sorted(makers):
        op = makers[add_id].op
        fields = op.fields or {}
        key = fields.get("name") if op.op == "stream" else fields.get("event")
        if key:
            by_key.setdefault((op.op, key), []).append(add_id)
        else:
            alone.append(add_id)
    pres: dict[str, _Pre] = {}
    pre_of: dict[str, str] = {}
    for (kind, key), adds in by_key.items():
        pres[adds[0]] = _pre(kind, key, adds, groups, problems)
        pre_of.update(dict.fromkeys(adds, adds[0]))
    for add_id in alone:
        pres[add_id] = _pre("meeting", None, [add_id], groups, problems)
        pre_of[add_id] = add_id
    joins = _joins(makers, pres, pre_of, set(alone), problems)
    continued = set(joins.values())
    members: dict[str, _Pre] = {}
    for pre_id, pre in sorted(pres.items()):
        if pre_id in continued:
            continue
        joined = pres.get(joins.get(pre_id, ""))
        if joined is None:
            members[pre_id] = pre
            continue
        # A continued meeting's id is the object's (DR-0621).
        adds = sorted([*pre.adds, *joined.adds])
        kept = sorted([*pre.kept, *joined.kept], key=_key)
        undone = {**pre.undone, **joined.undone}
        members[joined.adds[0]] = _Pre(pre.kind, pre.key, adds, kept, undone, continued=joined.adds[0])
    owner = {add_id: obj_id for obj_id, pre in members.items() for add_id in pre.adds}
    points, applied, left = _points(members, owner, problems)
    standing: dict[str, list[Point]] = {}
    for point_id in sorted(points):
        if points[point_id].stands:
            standing.setdefault(points[point_id].meeting, []).append(points[point_id])
    folds: dict[str, ObjectFold] = {}
    meetings: dict[str, Meeting] = {}
    streams: dict[str, Stream] = {}
    for obj_id, pre in sorted(members.items()):
        lines = [ln for ln in pre.kept if ln.op.id not in left] if left else pre.kept
        if pre.kind == "meeting":
            folded = _fold_meeting(obj_id, pre, lines, makers, points=standing.get(obj_id, []), applied=applied)
            if isinstance(folded.value, Meeting):
                meetings[obj_id] = folded.value
        else:
            folded = _fold_stream(obj_id, pre, lines, makers)
            if isinstance(folded.value, Stream):
                streams[obj_id] = folded.value
        folds[obj_id] = folded
    holders = _numbers(folds, meetings, problems)
    every = [line.op.number or 0 for line in makers.values() if line.op.op == "meeting"]
    every += [
        ln.op.number or 0 for f in folds.values() if f.kind == "meeting" for ln in f.lines if ln.op.op == "renumber"
    ]
    return Objects(
        meetings=meetings,
        streams=streams,
        folds=folds,
        owner=owner,
        points=points,
        holders=holders,
        events={m.event: m.id for m in meetings.values() if m.event},
        names={s.name: s.id for s in streams.values()},
        highest_number=max(every, default=0),
        problems=tuple(problems),
    )


def _pre(
    kind: str, key: str | None, adds: list[str], groups: Mapping[str, Sequence[Line]], problems: list[tuple[str, str]]
) -> _Pre:
    """The lines of a key's adds, each sorting after the add it names, sets checked against the object's rules, and
    every undo read (DR-0614)."""
    kept: list[Line] = []
    refused = REFUSES[kind]
    for line in sorted((ln for add_id in adds for ln in groups[add_id]), key=_key):
        op = line.op
        if op.id < op.item:
            problems.append((line.where, "sorts before its item's add; left out"))
        elif op.op in refused:
            problems.append((line.where, f"a {kind} takes no {op.op}; left out"))
        elif op.op == "set" and not fits(kind, op.fields or {}):
            problems.append((line.where, f"a set whose fields don't hold what a {kind}'s hold; left out"))
        else:
            kept.append(line)
    kept, undone, bad = undos(kept, {line.op.id: line.op for line in kept})
    problems += bad
    return _Pre(kind, key, adds, kept, undone)


def _joins(
    makers: dict[str, Line],
    pres: dict[str, _Pre],
    pre_of: dict[str, str],
    alone: set[str],
    problems: list[tuple[str, str]],
) -> dict[str, str]:
    """Each key's pre-group and the unkeyed meeting it continues: the first standing add by id that continues one wins,
    a later one naming the same pair agrees, and one naming another pair has its continues left out (DR-0621)."""
    joins: dict[str, str] = {}
    taken: set[str] = set()
    for add_id in sorted(makers):
        line = makers[add_id]
        wanted = (line.op.fields or {}).get("continues") if line.op.op == "meeting" else None
        pre_id = pre_of[add_id]
        if not wanted or add_id in pres[pre_id].undone:
            continue
        if pres[pre_id].key is None:
            problems.append((line.where, "continues a meeting but has no event; its continues is left out"))
        elif wanted not in alone:
            problems.append((line.where, "continues no meeting without an event; its continues is left out"))
        elif joins.get(pre_id) == wanted:
            continue
        elif pre_id in joins or wanted in taken:
            problems.append(
                (line.where, "continues another meeting than its key does, or one another key continues; left out")
            )
        else:
            joins[pre_id] = wanted
            taken.add(wanted)
    return joins


def _points(
    members: dict[str, _Pre], owner: dict[str, str], problems: list[tuple[str, str]]
) -> tuple[dict[str, Point], set[str], set[str]]:
    """Every point of every standing meeting, its copies settled along their `from` chains (DR-0623): the points by
    id, the point ops that applied, and the point_sets left out because they name no point of their meeting or, for
    a roll, no point and no meeting."""
    points: dict[str, Point] = {}
    # Every point op's meeting, undone ones and a removed meeting's included: a from naming one names a point.
    every: dict[str, str] = {}
    adds: dict[str, Line] = {}
    meetings: set[str] = set()
    for obj_id, pre in members.items():
        if pre.kind != "meeting":
            continue
        stands = any(a not in pre.undone for a in pre.adds)
        if stands:
            meetings.add(obj_id)
        for line in pre.kept:
            op = line.op
            if op.op != "point":
                continue
            every[op.id] = obj_id
            if stands and op.id not in pre.undone:
                f = op.fields or {}
                points[op.id] = Point(
                    id=op.id,
                    meeting=obj_id,
                    text=f["text"],
                    added_by=op.by,
                    day=op.day,
                    item=f.get("item"),
                    rolled_from=f.get("from"),
                    proposed=f.get("proposed") is True,
                )
                adds[op.id] = line

    def a_meeting(ref: str) -> bool:
        return ref in owner and members[owner[ref]].kind == "meeting"

    sets_of: dict[str, list[Line]] = {}
    left: set[str] = set()
    for obj_id, pre in members.items():
        for line in pre.kept:
            op = line.op
            if op.op != "point_set" or op.id in pre.undone:
                continue
            target = op.point or ""
            to = str((op.fields or {}).get("to"))
            if _rolls(op) and to not in every and not a_meeting(to):
                problems.append((line.where, "a roll whose to names no point and no meeting; left out"))
                left.add(op.id)
            elif target in points and points[target].meeting == obj_id:
                sets_of.setdefault(target, []).append(line)
            elif every.get(target) != obj_id:
                problems.append((line.where, "names no point of its meeting; left out"))
                left.add(op.id)
    kids: dict[str, list[str]] = {}
    roots: list[str] = []
    dead: list[str] = []
    for point_id in sorted(points):
        source = points[point_id].rolled_from
        if source is None or a_meeting(source):
            roots.append(point_id)
        elif source in points and source < point_id:
            kids.setdefault(source, []).append(point_id)
        elif source in every and source < point_id:
            dead.append(point_id)
        else:
            problems.append((adds[point_id].where, "its from names no earlier point and no meeting; read as none"))
            points[point_id].rolled_from = None
            roots.append(point_id)
    applied: set[str] = set()
    # A roll lands on a copy of its point that is there or, from an import that couldn't pair it, on a meeting that
    # stands, by any of its adds; any other roll's copy stands nowhere.
    landing = {add_id for add_id, obj_id in owner.items() if obj_id in meetings}
    queue: deque[tuple[str, list[Line], list[str]]] = deque((root, [], []) for root in roots)
    while queue:
        point_id, moved_sets, moved_kids = queue.popleft()
        children = sorted({*kids.get(point_id, []), *moved_kids})
        lines = sorted([*sets_of.get(point_id, []), *moved_sets], key=_key)
        winner = _fold_point(points[point_id], lines, applied, children, landing)
        if winner not in children:
            dead += children
            continue
        to_sets: list[Line] = []
        to_kids: list[str] = []
        for child in children:
            if child == winner:
                continue
            points[child].stands, points[child].moved_to = False, winner
            why = f"a copy its source's winning roll doesn't name; its ops go to {winner}"
            problems.append((adds[child].where, why))
            to_sets += sets_of.get(child, [])
            to_kids += kids.get(child, [])
        queue.append((winner, to_sets, to_kids))
    while dead:
        point_id = dead.pop()
        points[point_id].stands = False
        dead += kids.get(point_id, [])
    applied.update(point_id for point_id, point in points.items() if point.stands)
    return points, applied, left


def _rolls(op: Op) -> bool:
    return (op.fields or {}).get("state") == "rolled"


def _fold_point(
    point: Point, lines: list[Line], applied: set[str], copies: Sequence[str], landing: set[str]
) -> str | None:
    """A point from its own point_sets and those moved to it, in id order; the copy its winning roll names, if it
    rolled. A rolled point changes only by an undo, so every op after its roll is idle. A roll whose copy stands
    nowhere, its meeting removed on this clone or another, is idle too: the point reads as open, so it is never lost,
    and a later roll can win (DR-0623)."""
    for line in lines:
        if point.state == "rolled":
            continue
        fields = line.op.fields or {}
        if _rolls(line.op) and (to := str(fields.get("to"))) not in copies and to not in landing:
            continue
        changed = False
        for name, value in fields.items():
            if name == "text" and value and value != point.text:
                point.text, changed = value, True
            elif name == "state" and value and value != point.state:
                point.state, changed = value, True
            elif name == "answer" and value != point.answer:
                point.answer, changed = value, True
            elif name == "item" and value != point.item:
                point.item, changed = value, True
            elif name == "to" and value != point.rolled_to:
                point.rolled_to, changed = value, True
            elif name == "proposed" and (value is True) != point.proposed:
                point.proposed, changed = value is True, True
        if changed:
            applied.add(line.op.id)
    return point.rolled_to if point.state == "rolled" else None


def _fold_meeting(
    obj_id: str, pre: _Pre, lines: list[Line], makers: dict[str, Line], *, points: list[Point], applied: set[str]
) -> ObjectFold:
    adds, undone = pre.adds, pre.undone
    standing = [a for a in adds if a not in undone]
    if not standing:
        return ObjectFold("meeting", obj_id, None, tuple(adds), tuple(lines), undone, frozenset())
    # The add it stands on claims its number: the continued meeting's, else its first standing add's. In a join only
    # the continued meeting's own renumbers claim with it; the key's renumbers from before are numbers it held, so a
    # renumber there never takes the joined meeting's number (DR-0621).
    claimer = obj_id if obj_id in standing else standing[0]
    joined = claimer == pre.continued
    renumbers = [ln.op for ln in lines if ln.op.op == "renumber" and ln.op.id not in undone]
    claims = tuple((o.id, o.number or 0) for o in renumbers if not joined or o.item == claimer)
    kept = tuple((o.id, o.number or 0) for o in renumbers if joined and o.item != claimer)
    first = makers[standing[0]].op
    f = first.fields or {}
    meeting = Meeting(
        id=obj_id, number=None, claimed=0, subject=f["subject"], start=datetime.fromisoformat(f["start"]),
        end=datetime.fromisoformat(f["end"]), added_by=first.by, adds=tuple(adds), event=pre.key,
    )  # fmt: skip
    _set_meeting(meeting, {name: f.get(name) for name in MEETING_SETTABLE if name in f})
    mine = points
    archive = next((ln for ln in lines if ln.op.op == "archive" and ln.op.id not in undone), None)
    meeting.archived = archive is not None and not any(p.state == "open" for p in mine)
    shut = archive.op.id if meeting.archived and archive is not None else None
    by_id = {line.op.id: line.op for line in lines}
    idle: set[str] = set()
    for line in lines:
        op = line.op
        target = by_id.get(op.of) if op.of is not None and op.op == "undo" else None
        if op.id in undone:
            meeting.log.append(Entry(op, False, undone[op.id], target))
            continue
        kind = op.op
        if kind == "meeting":
            maker = makers.get(op.id)
            if line is not maker:
                ok = False
            elif op.id == first.id:
                ok = True
            else:
                ok = (shut is None or op.id < shut) and _set_meeting(meeting, _settable(op.fields or {}))
        elif kind == "set":
            ok = (shut is None or op.id < shut) and _set_meeting(meeting, _settable(op.fields or {}))
        elif kind == "material":
            ok = change_material(meeting.materials, op)
        elif kind == "archive":
            ok = line is archive and meeting.archived
        elif kind in ("point", "point_set"):
            ok = op.id in applied
        else:
            # log, undo and renumber apply; a kind this Docket doesn't know changes nothing.
            ok = kind in ("log", "undo", "renumber")
        if not ok:
            idle.add(op.id)
        meeting.log.append(Entry(op, ok, None, target))
        meeting.last = op.id
    meeting.points = mine
    claimed = (claimer, makers[claimer].op.number or 0)
    held = (*((a, makers[a].op.number or 0) for a in standing if a != claimer), *kept)
    return ObjectFold(
        "meeting", obj_id, meeting, tuple(adds), tuple(lines), undone, frozenset(idle), (claimed, *claims), held
    )


def _settable(fields: dict[str, Any]) -> dict[str, Any]:
    return {name: value for name, value in fields.items() if name in MEETING_SETTABLE}


def _set_meeting(meeting: Meeting, fields: dict[str, Any]) -> bool:
    """The plain fields a set or a later add sets; null clears an optional one, and a required one can't be cleared.
    A time is compared as written, its offset included."""
    changed = False
    for name in MEETING_SETTABLE:
        if name not in fields:
            continue
        value = fields[name]
        if value is None and name in MEETING_REQUIRED:
            continue
        if name in TIMES:
            new: Any = datetime.fromisoformat(cast(str, value))
            if getattr(meeting, name).isoformat() != new.isoformat():
                setattr(meeting, name, new)
                changed = True
            continue
        new = tuple(value) if name == "attendees" and value is not None else () if name == "attendees" else value
        if getattr(meeting, name) != new:
            setattr(meeting, name, new)
            changed = True
    return changed


def _fold_stream(obj_id: str, pre: _Pre, lines: list[Line], makers: dict[str, Line]) -> ObjectFold:
    adds, undone = pre.adds, pre.undone
    standing = [a for a in adds if a not in undone]
    if not standing:
        return ObjectFold("stream", obj_id, None, tuple(adds), tuple(lines), undone, frozenset())
    first = makers[standing[0]].op
    f = first.fields or {}
    stream = Stream(id=obj_id, name=f["name"], status=f["status"], updated=first.day, added_by=first.by,
                    adds=tuple(adds))  # fmt: skip
    _set_stream(stream, f)
    by_id = {line.op.id: line.op for line in lines}
    idle: set[str] = set()
    for line in lines:
        op = line.op
        target = by_id.get(op.of) if op.of is not None and op.op == "undo" else None
        if op.id in undone:
            stream.log.append(Entry(op, False, undone[op.id], target))
            continue
        if op.op == "stream":
            ok = line is makers.get(op.id) and (op.id == first.id or _set_stream(stream, op.fields or {}))
        elif op.op == "set":
            ok = _set_stream(stream, op.fields or {})
        else:
            ok = op.op in ("log", "undo")
        if ok and op.op in ("stream", "set") and op.id != first.id:
            stream.updated = op.day
        if not ok:
            idle.add(op.id)
        stream.log.append(Entry(op, ok, None, target))
        stream.last = op.id
    return ObjectFold("stream", obj_id, stream, tuple(adds), tuple(lines), undone, frozenset(idle))


def _set_stream(stream: Stream, fields: dict[str, Any]) -> bool:
    changed = False
    for name in STREAM_SETTABLE:
        if name not in fields:
            continue
        value = fields[name]
        if value is None and name == "status":
            continue
        new = date.fromisoformat(value) if name == "deadline" and value is not None else value
        if getattr(stream, name) != new:
            setattr(stream, name, new)
            changed = True
    return changed


def _numbers(
    folds: dict[str, ObjectFold], meetings: dict[str, Meeting], problems: list[tuple[str, str]]
) -> dict[int, tuple[str, ...]]:
    """Each M- number's holders (DR-0613): the claims in claim order, the first keeping it, then each meeting whose
    other adds held it, so a number shown before a merge names every meeting it named (DR-0621)."""
    claims = sorted(
        (op_id, number, f.id) for f in folds.values() if f.value is not None for op_id, number in f.claims
    )  # fmt: skip
    holders: dict[int, list[str]] = {}
    for _, number, obj_id in claims:
        named = holders.setdefault(number, [])
        if obj_id not in named:
            named.append(obj_id)
    for _, number, obj_id in sorted((a, n, f.id) for f in folds.values() if f.value is not None for a, n in f.held):
        named = holders.setdefault(number, [])
        if obj_id not in named:
            named.append(obj_id)
    for obj_id, meeting in meetings.items():
        latest = folds[obj_id].claims[-1][1]
        holds = holders[latest][0] == obj_id
        meeting.claimed, meeting.number = latest, latest if holds else None
        if not holds:
            problems.append((meeting_text(latest), f"held by two meetings; the next write gives {obj_id} a new number"))
    return {number: tuple(named) for number, named in holders.items()}


def describe_point(op: Op) -> str:
    """A point op in a few words, for its meeting's log. Not a door: the words can change."""
    fields = op.fields or {}
    if op.op == "point":
        how = "rolled in" if fields.get("from") else "proposed" if fields.get("proposed") else "added"
        return f"point {how}: {fields.get('text', '')}"
    parts: list[str] = []
    for name, value in fields.items():
        if name == "state":
            parts.append(str(value))
        elif name == "proposed":
            parts.append("kept" if value is None else "proposed")
        elif name in ("answer", "text"):
            parts.append(f"{name}: {'-' if value is None else value}")
        elif name == "item":
            parts.append("item cleared" if value is None else "item linked")
    return "point " + ", ".join(parts)
