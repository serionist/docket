"""Which ledger ops a forget masks (DR-0694): worked out by the plan for the list's line, and again by every clone's
apply, which finds a clone's own ops from before its pull.

| An op | Masked |
|---|---|
| with an `[r:` anchor on the family, judged where it reads, or a `[d:` ref to a line the docs' rule drops | it |
| a material whose ref reads on the family: a rid, a doc the forget removes, a listed legacy note's old path | it |
| an item's add whose source, in the add or a set, reads on the family; a meeting's add whose event it takes | it |
| of a shell: an item, or a meeting, whose making op, any add of a meeting, is masked | each, points too |
| a copy along `from`, or an item `point_item` made, of a point masked for its own words | each, to a fixed point |

A point is masked for its own words when it was raised on a shell, when an anchor matched it or a `point_set` setting
its text, or when a `point_set` on a shell set its text there. Any other copy rolled into a shell goes as that shell's
op, and the chain beyond it keeps its words. A shell's adds digest their event alike (DR-0693), so an unlisted add of
that event, a merge's, is the shell's too. Only an op whose line a mask changes is named: one with no text has nothing
to hide, and a listed one is masked already.

What it finds is said by number, never a word: the shells it leaves, the open items among them to drop, each object
that lost a material or a link and kept its text, and each open point on a shell rolled in from a live meeting, to
raise again. Streams are counted, their names being words.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from docket.ledger import fold as folding
from docket.ledger import ops
from docket.ledger.fold import Ledger, number_text
from docket.ledger.meetings import meeting_text
from docket.ledger.ops import Line, Op
from docket.vault.anchors import AnchorError, DocRef, parse_anchor, parse_locator, parse_ref
from docket.vault.forgetdocs import Raw, on_family
from docket.vault.forgotten import Forgotten
from docket.vault.processed import DOC_TYPES, doc_path

# An anchor or a doc-line ref anywhere in a value: an op's text has no anchored lines as a doc has.
TOKEN = re.compile(r"\[[rd]:[^\]\s]+\]")
# A doc's file in the vault, as a material's ref may name it from the vault or the host repo's top.
DOC_FILE = re.compile(r"(?:^|/)(processed/([a-z0-9-]+)/(?:\d{4}/\d{2}/)?([^/]+)\.md)$")
MEETING_SOURCE = "meeting:"
OPEN = ("open", "snoozed")


class Docs(Protocol):
    """What the rule asks of the docs as the forget leaves them."""

    def removed(self, doc_id: str) -> bool:
        """Whether the forget removes the doc."""
        ...

    def cited(self, refs: Collection[DocRef]) -> set[DocRef]:
        """The refs that lead to a line the docs' rule drops, or to a doc it removes."""
        ...


@dataclass(frozen=True)
class Found:
    """What the rule masks that no list line names yet, each op whose line a mask changes; then, by number, the shells
    those leave, the open items among them, each object that lost a material or a link and kept its text, each open
    point on a new shell rolled in from a live meeting, and how many stream ops lost their text."""

    new: frozenset[str] = frozenset()
    shells: tuple[str, ...] = ()
    open: tuple[str, ...] = ()
    kept: tuple[str, ...] = ()
    heads: tuple[str, ...] = ()
    streams: int = 0


NOTHING = Found()


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for one in cast(list[Any], value):
            yield from _strings(one)
    elif isinstance(value, dict):
        for one in cast(dict[str, Any], value).values():
            yield from _strings(one)


def tokens(text: str) -> tuple[list[str], list[DocRef]]:
    """The `[r:` anchors an op line's values hold, as written, and its `[d:` refs read: from every value, as JSON
    reads it, so an escape in the line hides none."""
    anchors: list[str] = []
    refs: list[DocRef] = []
    if "[r:" not in text and "[d:" not in text:
        return anchors, refs
    for value in _strings(json.loads(text)):
        for found in TOKEN.findall(value):
            try:
                if found.startswith("[r:"):
                    anchors.append(found)
                else:
                    refs.append(parse_ref(found))
            except AnchorError:
                continue
    return anchors, refs


def _doc_of(path: str) -> str | None:
    """The doc a path names by its file in the vault, as `doc_path` lays it out."""
    m = DOC_FILE.search(path)
    if m is None:
        return None
    doc_id = f"{m.group(2)}:{m.group(3)}"
    return doc_id if doc_path(doc_id).as_posix() == m.group(1) else None


class _Judge:
    """One forget's judgement of a value: raw as it leaves it, the list, and the docs."""

    def __init__(self, raw: Raw, listed: Forgotten, docs: Docs) -> None:
        self.raw = raw
        self.listed = listed
        self.docs = docs

    def anchor(self, token: str) -> bool:
        try:
            found = parse_anchor(token)
        except AnchorError:
            return False
        return found.rid is not None and on_family(found, found.rid, self.raw)

    def locator(self, value: object) -> bool:
        """Whether a source, a material's ref or an event reads on the family by `read`'s rules (DR-0131): a record
        and its position, a doc the forget removes, or a path to one or to a legacy note the list holds."""
        if not isinstance(value, str) or ops.MARKED.fullmatch(value):
            return False
        try:
            found = parse_locator(value, DOC_TYPES)
        except AnchorError:
            return False
        if found.kind == "record":
            return found.anchor is not None and on_family(found.anchor, found.value, self.raw)
        if found.kind == "doc":
            return self.docs.removed(found.value)
        path = found.value.replace("\\", "/").removeprefix("./")
        doc_id = _doc_of(path)
        return (doc_id is not None and self.docs.removed(doc_id)) or self.listed.legacy({found.value, path}, None)


def _changes(line: Line) -> bool:
    return ops.mask(line.text) != line.text


class _Rule:
    """The rule over one reading of the ledger: every line by its id and by the object it names, folded once."""

    def __init__(self, lines: Sequence[Line], held: frozenset[str]) -> None:
        self.held = held
        self.texts: dict[str, list[Line]] = {}
        self.by_item: dict[str, list[Line]] = {}
        self.copies: dict[str, list[str]] = {}
        self.sets_of: dict[str, list[Line]] = {}
        for line in lines:
            op = line.op
            self.texts.setdefault(op.id, []).append(line)
            self.by_item.setdefault(op.item, []).append(line)
            fields = op.fields or {}
            if op.op == "point" and isinstance(fields.get("from"), str):
                self.copies.setdefault(fields["from"], []).append(op.id)
            elif op.op == "point_set" and op.point is not None:
                self.sets_of.setdefault(op.point, []).append(line)
        with folding.paused():
            self.ledger: Ledger = folding.fold(lines)
        objects = self.ledger.objects
        self.meetings = {obj_id: f.adds for obj_id, f in objects.folds.items() if f.kind == "meeting"}
        self.events: dict[str, str] = {}
        for adds in self.meetings.values():
            for add_id in adds:
                event = (self.op(add_id).fields or {}).get("event")
                if isinstance(event, str):
                    self.events[add_id] = ops.masked_locator(event)
        self.points = {op_id for op_id, found in self.texts.items() if found[0].op.op == "point"}

    def op(self, op_id: str) -> Op:
        return self.texts[op_id][0].op

    # -- what an op says itself ------------------------------------------------------------------------------------

    def direct(self, judge: _Judge) -> set[str]:
        """The ops the rule masks for what they say themselves, and the adds of the items a source set on the family;
        the refs judged in one go."""
        out: set[str] = set()
        cited: dict[DocRef, set[str]] = {}
        for op_id, found in self.texts.items():
            if op_id in self.held:
                continue
            for line in found:
                op = line.op
                anchors, refs = tokens(line.text)
                for ref in refs:
                    cited.setdefault(ref, set()).add(op_id)
                fields = op.fields or {}
                if (
                    any(judge.anchor(token) for token in anchors)
                    or (op.op == "material" and judge.locator(op.ref))
                    or (op.op == "meeting" and judge.locator(fields.get("event")))
                ):
                    out.add(op_id)
                elif op.op in ("add", "set") and judge.locator(fields.get("source")):
                    out |= {op_id, op.item}
        for ref in judge.docs.cited(list(cited)) if cited else ():
            out |= cited[ref]
        return out

    # -- shells and chains -----------------------------------------------------------------------------------------

    def shells(self, masked: Collection[str]) -> dict[str, str]:
        """Each add of a shell with the shell's id: an item whose add is masked, a meeting with a masked add, and a
        meeting whose add's event digests as a shell's does."""
        out = {item_id: item_id for item_id in self.ledger.folds if item_id in masked}
        events: set[str] = set()
        for obj_id, adds in self.meetings.items():
            if any(add_id in masked for add_id in adds):
                out.update(dict.fromkeys(adds, obj_id))
                events.update(self.events[a] for a in adds if a in self.events)
        for obj_id, adds in self.meetings.items():
            if obj_id not in out.values() and any(self.events.get(a) in events for a in adds):
                out.update(dict.fromkeys(adds, obj_id))
        return out

    def own_words(self, shell: dict[str, str], direct: Collection[str]) -> set[str]:
        """The points masked for their own words: raised on a shell, matched by an anchor, or whose text a point_set
        an anchor matched, or one on a shell, set."""
        out: set[str] = set()
        for op_id in direct:
            op = self.op(op_id)
            if op.op == "point":
                out.add(op.id)
            elif op.op == "point_set" and op.point is not None and "text" in (op.fields or {}):
                out.add(op.point)
        meetings = {add_id for add_id, obj_id in shell.items() if obj_id in self.meetings}
        for add_id in meetings:
            for line in self.by_item.get(add_id, ()):
                op = line.op
                source = (op.fields or {}).get("from")
                if op.op == "point" and source not in self.points:
                    out.add(op.id)
                elif op.op == "point_set" and op.point is not None and "text" in (op.fields or {}):
                    out.add(op.point)
        return out

    def follow(self, own: set[str], masked: set[str]) -> None:
        """Each point masked for its own words, its copies along `from` and their point_sets, and the items
        `point_item` made from any of them, into `masked`, to a fixed point."""
        frontier = list(own)
        seen = set(own)
        while frontier:
            point_id = frontier.pop()
            masked.add(point_id)
            for line in self.sets_of.get(point_id, ()):
                masked.add(line.op.id)
                made = (line.op.fields or {}).get("item")
                if isinstance(made, str) and made in self.texts and self._made_by(made, line.op):
                    masked.add(made)
            for copy in self.copies.get(point_id, ()):
                if copy not in seen:
                    seen.add(copy)
                    frontier.append(copy)

    def _made_by(self, item_id: str, linked: Op) -> bool:
        """Whether an item is one `point_item` made: its add in the batch of the point_set linking it."""
        add = self.op(item_id)
        return add.op == "add" and add.id == add.item and add.batch is not None and add.batch == linked.batch

    def masked(self, judge: _Judge) -> tuple[set[str], dict[str, str]]:
        """Every op the rule masks, listed ones too, and each add of a shell with its shell."""
        direct = self.direct(judge)
        masked = set(self.held) | direct
        while True:
            before = len(masked)
            shell = self.shells(masked)
            for add_id in shell:
                masked.update(line.op.id for line in self.by_item.get(add_id, ()))
            self.follow(self.own_words(shell, direct), masked)
            if len(masked) == before:
                return masked, shell

    # -- what the screens say --------------------------------------------------------------------------------------

    def label(self, obj_id: str) -> str:
        """An item's or a meeting's number as its object holds it; a hidden one's, its first claim's."""
        ledger = self.ledger
        if obj_id in ledger.folds:
            item = ledger.items.get(obj_id)
            if item is not None:
                return number_text(item.number or item.claimed)
            claims = ledger.folds[obj_id].claims
            return number_text(claims[-1][1] if claims else 0)
        meeting = ledger.objects.meetings.get(obj_id)
        if meeting is not None:
            return meeting_text(meeting.number or meeting.claimed)
        return meeting_text(self.op(self.meetings[obj_id][0]).number or 0)

    def owner(self, item_id: str) -> tuple[str, str] | None:
        """The object an op names, as (kind, id): an item, a meeting or a stream."""
        if item_id in self.ledger.folds:
            return "item", item_id
        obj_id = self.ledger.objects.owner.get(item_id)
        if obj_id is None:
            return None
        return self.ledger.objects.folds[obj_id].kind, obj_id

    def report(self, new: Collection[str], shell: dict[str, str]) -> Found:
        shells = set(shell.values())
        made = sorted({shell[a] for a in shell if a in new}, key=self.label)
        items = self.ledger.items
        open_ = [self.label(i) for i in made if i in items and items[i].state in OPEN]
        lost: dict[str, Counter[str]] = {}
        streams = 0
        for op_id in new:
            found = self.owner(self.op(op_id).item)
            if found is None or found[1] in shells:
                continue
            if found[0] == "stream":
                streams += 1
                continue
            what = "material link" if self.op(op_id).op == "material" else "op"
            lost.setdefault(found[1], Counter())[what] += 1
        said: dict[str, list[str]] = {
            obj_id: [_counted(n, what) for what, n in sorted(counts.items())] for obj_id, counts in lost.items()
        }
        for item_id, item in items.items():
            linked = item.source.removeprefix(MEETING_SOURCE) if item.source.startswith(MEETING_SOURCE) else None
            near = next((shell[a] for a in (linked, item.meeting) if a in shell), None)
            if near is not None and near in made and item_id not in shells:
                said.setdefault(item_id, []).append(f"from {self.label(near)}, a shell")
        kept = sorted((self.label(i), parts) for i, parts in said.items())
        return Found(
            new=frozenset(new),
            shells=tuple(self.label(i) for i in made),
            open=tuple(open_),
            kept=tuple(f"{label}: {', '.join(parts)}; the rest kept" for label, parts in kept),
            heads=tuple(self.heads([i for i in made if i in self.meetings], shells)),
            streams=streams,
        )

    def heads(self, made: Sequence[str], shells: Collection[str]) -> list[str]:
        """Each open point standing on a new shell that was rolled in from a live meeting: the one, walking back along
        `from`, that isn't a shell."""
        objects = self.ledger.objects
        out: list[str] = []
        for obj_id in made:
            meeting = objects.meetings.get(obj_id)
            for n, point in enumerate(meeting.points if meeting is not None else (), start=1):
                came = point.rolled_from
                seen: set[str] = set()
                while came in objects.points and objects.points[came].meeting in shells and came not in seen:
                    seen.add(came)
                    came = objects.points[came].rolled_from
                live = objects.points[came].meeting if came in objects.points else None
                if point.state == "open" and live is not None and live not in shells:
                    out.append(f"{self.label(obj_id)}#{n}, rolled in from {self.label(live)}, open")
        return out


def _counted(n: int, what: str) -> str:
    return f"{n} {what}{'' if n == 1 else 's'} {'removed' if what == 'material link' else 'masked'}"


def match(lines: Sequence[Line], raw: Raw, listed: Forgotten, docs: Docs) -> Found:
    """The ops the rule masks in these lines, read with the list's ops masked (DR-0695), against raw and the docs as
    the forget leaves them and the list in force with the forget's line on it."""
    rule = _Rule(lines, frozenset(listed.ops))
    masked, shell = rule.masked(_Judge(raw, listed, docs))
    changed = {op_id for op_id in masked - rule.held if any(_changes(line) for line in rule.texts.get(op_id, ()))}
    return rule.report(changed, shell)


# -- the apply's look at what moved ------------------------------------------------------------------------------------


# The ids a line names an object, a point or a copy's source by, and the locators it may hold, as its text writes them:
# what is looked for before a line is read as JSON.
NAMED = re.compile(r'"(?:item|point|from)":"([0-7][0-9A-HJKMNP-TV-Z]{25})"')
LOCATED = re.compile(r'"(?:source|ref|event)":"')


def suspect(raw: str, listed: Forgotten) -> bool:
    """Whether a line an op file gained since the last apply may be one the rule masks, from the list alone and read
    as JSON only when its text says it may: an op on an object, a point or a copy whose op the list holds, or an anchor
    or a locator on a rid the list holds whole, by a version or by its media, or on a listed old path. Only then is the
    whole ledger read and judged again (DR-0694). A `[d:` ref isn't looked at: the clone that wrote it before its pull
    judges it there, its list having moved, and the ops-only line it writes reaches every clone."""
    held = listed.ops
    rids = bool(listed.rids or listed.versions or listed.media or listed.old_paths)
    named = any(found in held for found in NAMED.findall(raw)) if held else False
    if not named and not (rids and ("[r:" in raw or LOCATED.search(raw))):
        return False
    try:
        data: object = json.loads(raw)
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    line = cast(dict[str, Any], data)
    if _held(line.get("id"), held):
        return False
    fields = cast(dict[str, Any], line["fields"]) if isinstance(line.get("fields"), dict) else {}
    if any(_held(value, held) for value in (line.get("item"), line.get("point"), fields.get("from"))):
        return True
    if not rids:
        return False
    if any(
        isinstance(v, str) and _listed(v, listed) for v in (line.get("ref"), fields.get("source"), fields.get("event"))
    ):
        return True
    return any(_listed(token, listed) for value in _strings(line) for token in TOKEN.findall(value))


def _held(value: object, held: Collection[str]) -> bool:
    return isinstance(value, str) and value in held


def _listed(value: str, listed: Forgotten) -> bool:
    """Whether a locator or an anchor names a rid the list takes, by a hash on it, or a listed old path."""
    try:
        found = parse_locator(value, DOC_TYPES)
    except AnchorError:
        return False
    if found.kind == "record":
        return listed.versioned(found.value) or listed.media_refused(found.value)
    return found.kind == "path" and listed.legacy({found.value, found.value.replace("\\", "/")}, None)


def streams(lines: Sequence[Line], ids: Collection[str]) -> list[str]:
    """The names of the streams these ops change: a stream's adds keep its name through a mask."""
    names = {
        line.op.id: str((line.op.fields or {}).get("name"))
        for line in lines if line.op.op == "stream" and line.op.id == line.op.item
    }  # fmt: skip
    return sorted({names[line.op.item] for line in lines if line.op.id in ids and line.op.item in names})
