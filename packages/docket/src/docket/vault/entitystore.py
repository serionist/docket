"""Writing entities (DR-0463): the creates and adds of a changes file, and undo, each written as a line of the curate
log first and committed with the entity files it changed.

A write runs under the vault lock, in order:

1. Lines of the log that HEAD doesn't have, left by a write that stopped before its commit, are finished: a create or
   an add applied where the file lacks it, an undo's removal made where the file still holds it; then committed.
2. A file the write touches that differs from HEAD, a hand edit, is committed on its own, as a doc's is taken in.
3. The new lines are appended to the log, the files written through a temp file and a rename (or deleted, for an
   undone create), and both committed in one commit. A failure puts both back and unstages them.

A file is rewritten whole in the canonical form, and only when its own read reports no problem and it holds no comment:
the rewrite would lose what the loader didn't read. What is written must read back as the entity intended. Every value
a change writes is held to the never-store lists and the redaction rules first (DR-0464). A refusal names the change
by its position and the field, never a value (DR-0476). A changes file goes in whole or not at all.

    changes:
      - create: {id: cleo-test, type: person, name: {en: Cleo Test}, aliases: [{text: Cleo}], role: rights agent}
      - entity: northwind
        add: {aliases: [{text: Nortwin, kind: asr}], ids: {email_domain: northwind.example}}
"""

from __future__ import annotations

import contextlib
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Self, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator
from pydantic_core import ErrorDetails

from docket.errors import DocketError
from docket.vault import curatelog, rendered
from docket.vault.curatelog import (
    AliasFields,
    By,
    Change,
    EntityFields,
    Evidence,
    Folded,
    IdFields,
    IdKey,
    Lang,
    Line,
    NameFields,
    RelatedFields,
    Slug,
    Slugs,
    Texts,
    Via,
    Wordy,
    folded,
    key_of,
)
from docket.vault.entities import (
    Alias,
    Entity,
    comment_line,
    entity_files,
    entity_rel,
    load_file,
    load_text,
    render,
)
from docket.vault.neverstore import listed
from docket.vault.redact import Redactor
from docket.vault.staging import write_replacing
from docket.vault.vault import UnmergedError, Vault


class RefusedError(DocketError):
    """Nothing was written. Each problem names a change by its position, or an op by its id, never a value."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = list(problems)


class _UnwritableError(Exception):
    """A change this write can't make, and why, without the value."""


@dataclass(frozen=True)
class Planned:
    """One part of a change to write: its position in what was asked (a changes file's change 3), its entity, the
    part, and the round and card it settles, if any."""

    n: int
    entity: str
    change: Change
    round: str | None = None
    card: str | None = None
    # Tier 1's: the rule that found the fact, and where (DR-0469).
    rule: str | None = None
    evidence: Evidence | None = None


@dataclass(frozen=True)
class Reject:
    """A key a rejection puts on the stoplist, with the text it names (held to the never-store lists: a listed one
    writes no line), and the entity, round and card it belongs to, if any."""

    key: str
    value: str
    entity: str | None = None
    round: str | None = None
    card: str | None = None


@dataclass(frozen=True)
class Written:
    """What a write did: its log lines and commit, the ops of a stopped write it finished first, the hand edits it
    committed first, the parts the entities held already, and the undone ops whose part the file no longer held."""

    lines: tuple[Line, ...] = ()
    commit: str | None = None
    finished: tuple[str, ...] = ()
    taken_in: tuple[str, ...] = ()
    already: int = 0
    only_logged: tuple[str, ...] = ()
    # What the render after the write did with each file vault.yaml names, written, left or refused (DR-0554).
    rendered: tuple[str, ...] = ()


def _rendered(vault: Vault, written: Written) -> Written:
    """A curate write, then, when it committed something, the files vault.yaml names rendered from the entities. It
    runs once the write has let go of the vault's lock, so a remote's check never holds it, and a render that fails is
    said in the result, never raised (DR-0554)."""
    if not (written.commit or written.finished or written.taken_in) or not vault.config.rendered:
        return written
    return replace(written, rendered=tuple(rendered.safely(vault)))


# -- the changes file ---------------------------------------------------------------------------------------------


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AddFields(_Model):
    aliases: tuple[AliasFields, ...] = ()
    names: dict[Lang, Wordy] = Field(default_factory=dict[str, str])
    ids: dict[IdKey, Texts] = Field(default_factory=dict[str, tuple[str, ...]])
    related: dict[Slug, Slugs] = Field(default_factory=dict[str, tuple[str, ...]])

    @model_validator(mode="after")
    def _some(self) -> Self:
        if not (self.aliases or self.names or self.ids or self.related):
            raise ValueError("add holds aliases, names, ids or related")
        return self


class ChangeItem(_Model):
    create: EntityFields | None = None
    entity: Slug | None = None
    add: AddFields | None = None
    card: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{6}$")] | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if (self.create is None) == (self.entity is None and self.add is None) or (self.entity is None) != (
            self.add is None
        ):
            raise ValueError("a change is create: {...}, or entity: <id> with add: {...}")
        return self


class ChangesFile(_Model):
    changes: tuple[ChangeItem, ...]

    @model_validator(mode="after")
    def _some(self) -> Self:
        # Here, not as the field's minimum: pydantic reports a minimum beside every item that fails.
        if not self.changes:
            raise ValueError("changes lists at least one change")
        return self


FIELDS = frozenset({"changes"}.union(*(m.model_fields for m in (ChangeItem, EntityFields, AddFields, AliasFields))))


def _problem(error: ErrorDetails) -> str:
    """A refusal by position and field; a map's own keys are never printed, since they are the user's text."""
    loc = list(error["loc"])
    where = "the file"
    if loc[:1] == ["changes"] and len(loc) > 1 and isinstance(loc[1], int):
        where = f"change {loc[1] + 1}"
        loc = loc[2:]
    path: list[str] = []
    for part in loc:
        if not isinstance(part, int) and part not in FIELDS:
            break
        path.append(f"item {part + 1}" if isinstance(part, int) else part)
    message = error["msg"].removeprefix("Value error, ")
    return f"{where}: {' '.join(path)}: {message}" if path else f"{where}: {message}"


def load_changes(path: Path) -> list[Planned]:
    """A changes file's parts, in order, each with the position of its change; refused whole, with every problem."""
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise RefusedError(["the file isn't UTF-8"]) from None
    # YAML's base loader makes text, lists and maps only, as for entity files.
    loader = yaml.BaseLoader(text)
    try:
        data: Any = loader.get_single_data()
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        raise RefusedError([f"the file isn't YAML{f' (line {mark.line + 1})' if mark else ''}"]) from None
    finally:
        loader.dispose()
    try:
        parsed = ChangesFile.model_validate(data)
    except ValidationError as e:
        raise RefusedError(list(dict.fromkeys(_problem(error) for error in e.errors()))) from None
    planned: list[Planned] = []
    for n, item in enumerate(parsed.changes, start=1):
        if item.create is not None:
            planned.append(Planned(n, item.create.id, Change(entity=item.create), card=item.card))
            continue
        add, entity = cast(AddFields, item.add), cast(str, item.entity)
        card = item.card
        planned += [Planned(n, entity, Change(alias=a), card=card) for a in add.aliases]
        planned += [
            Planned(n, entity, Change(name=NameFields(lang=k, text=v)), card=card) for k, v in add.names.items()
        ]
        planned += [
            Planned(n, entity, Change(id=IdFields(key=k, value=v)), card=card) for k, vs in add.ids.items() for v in vs
        ]
        planned += [
            Planned(n, entity, Change(related=RelatedFields(type=k, target=t)), card=card)
            for k, ts in add.related.items()
            for t in ts
        ]
    return planned


# -- what a change holds ------------------------------------------------------------------------------------------


def _values(change: Change) -> list[str]:
    """Every value a change writes, for the never-store lists and the redaction rules."""
    e = change.entity
    if e is not None:
        return [
            e.id,
            *e.name.values(),
            *(a.text for a in e.aliases),
            *(n for a in e.aliases for n in a.needs),
            *(t for ts in e.related.values() for t in ts),
            *(v for vs in e.ids.values() for v in vs),
            *(x for x in (e.role, e.note) if x is not None),
        ]
    if change.alias is not None:
        return [change.alias.text, *change.alias.needs]
    if change.name is not None:
        return [change.name.text]
    if change.id is not None:
        return [change.id.value]
    return [cast(RelatedFields, change.related).target]


def screen(vault: Vault, planned: Sequence[Planned]) -> list[str]:
    """What the never-store lists or the redaction rules refuse, by change. A value is read by its words, so an id's
    hyphens read as spaces and ben-sample meets a list's Ben Sample."""
    lists = vault.config.never_store
    redactor = Redactor.from_config(vault.config.redaction)
    problems: list[str] = []
    for item in planned:
        for value in _values(item.change):
            on = listed(value, lists)
            counts: Counter[str] = Counter()
            redactor.text(value, counts)
            if on is not None:
                problems.append(f"change {item.n}: a value is on the never-store {on} list; leave it out")
            elif counts:
                problems.append(f"change {item.n}: the {min(counts)} redaction rule would change a value; leave it out")
    return list(dict.fromkeys(problems))


def with_part(entity: Entity, change: Change) -> Entity | None:
    """The entity with an add's part, or None when it holds it already."""
    if change.alias is not None:
        a = change.alias
        held = {folded(t) for t in (*entity.names.values(), *(x.text for x in entity.aliases))}
        if folded(a.text) in held:
            return None
        return replace(entity, aliases=(*entity.aliases, Alias(a.text, a.kind, a.needs)))
    if change.name is not None:
        lang, text = change.name.lang, change.name.text
        if lang not in entity.names:
            return replace(entity, names={**entity.names, lang: text})
        if folded(entity.names[lang]) == folded(text):
            return None
        raise _UnwritableError("the entity has another name in that language; change it in the file")
    if change.id is not None:
        key, value = change.id.key, change.id.value
        values = entity.ids.get(key, ())
        if value.casefold() in {v.casefold() for v in values}:
            return None
        return replace(entity, ids={**entity.ids, key: (*values, value)})
    related = cast(RelatedFields, change.related)
    targets = entity.related.get(related.type, ())
    if related.target in targets:
        return None
    return replace(entity, related={**entity.related, related.type: (*targets, related.target)})


def without_part(entity: Entity, change: Change) -> Entity | None:
    """The entity without an add's part, or None when it doesn't hold it."""
    if change.alias is not None:
        text = folded(change.alias.text)
        kept = tuple(a for a in entity.aliases if folded(a.text) != text)
        return None if len(kept) == len(entity.aliases) else replace(entity, aliases=kept)
    if change.name is not None:
        lang = change.name.lang
        if folded(entity.names.get(lang, "")) != folded(change.name.text):
            return None
        if len(entity.names) == 1:
            raise _UnwritableError("the name is the entity's only one; undo its create, or change the file")
        return replace(entity, names={k: v for k, v in entity.names.items() if k != lang})
    if change.id is not None:
        key, value = change.id.key, change.id.value.casefold()
        values = entity.ids.get(key, ())
        left = tuple(v for v in values if v.casefold() != value)
        if left == values:
            return None
        ids = {k: v for k, v in entity.ids.items() if k != key} | ({key: left} if left else {})
        return replace(entity, ids=ids)
    related = cast(RelatedFields, change.related)
    targets = entity.related.get(related.type, ())
    if related.target not in targets:
        return None
    left_targets = tuple(t for t in targets if t != related.target)
    others = {k: v for k, v in entity.related.items() if k != related.type}
    return replace(entity, related=others | ({related.type: left_targets} if left_targets else {}))


# -- the files a write leaves -------------------------------------------------------------------------------------


class _Files:
    """The entity files as a write leaves them: each touched entity's new state, or None for one deleted."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.paths: dict[str, list[Path]] = {}
        for path in entity_files(root):
            self.paths.setdefault(path.stem, []).append(path)
        self.now: dict[str, Entity | None] = {}
        self.blame: dict[str, int] = {}

    def file(self, entity_id: str) -> Path | None:
        """The file that holds an entity, or None. Two files with its id are refused, naming both: the loader reads
        the first and skips the other, so a write to either would be lost or unseen."""
        paths = self.paths.get(entity_id, [])
        if len(paths) > 1:
            names = " and ".join(p.relative_to(self.root).as_posix() for p in paths)
            raise _UnwritableError(f"two files have that id, {names}; keep one")
        return paths[0] if paths else None

    def current(self, entity_id: str) -> Entity | None:
        """The entity as this write has it so far, or None when there is none; a file that can't be rewritten whole
        is refused."""
        if entity_id in self.now:
            return self.now[entity_id]
        path = self.file(entity_id)
        if path is None:
            return None
        entity, problems = load_file(self.root, path)
        if entity is None or problems:
            raise _UnwritableError(
                f"the entity's file reads with a problem a rewrite would lose ({problems[-1].message}); fix it first"
            )
        line = comment_line(path.read_bytes().decode("utf-8"))
        if line is not None:
            raise _UnwritableError(
                f"the entity's file holds a comment on line {line}, which a rewrite would lose; take it out, or make "
                "the change by hand"
            )
        return entity

    def set(self, entity_id: str, entity: Entity | None, n: int = 0) -> None:
        self.now[entity_id] = entity
        self.blame[entity_id] = n

    def rel(self, entity_id: str) -> str:
        entity = self.now[entity_id]
        return entity.path if entity is not None else self.paths[entity_id][0].relative_to(self.root).as_posix()

    def outputs(self) -> dict[str, str | None]:
        """Each touched file's new text, or None to delete it."""
        return {self.rel(i): None if e is None else render(e) for i, e in self.now.items()}

    def unreadable(self) -> list[str]:
        """The changes whose entity wouldn't read back as written."""
        out: list[str] = []
        for entity_id, entity in self.now.items():
            if entity is not None and load_text(entity.path, render(entity)) != (entity, []):
                out.append(f"change {self.blame[entity_id]}: the entity wouldn't read back as written")
        return out


def _plan(files: _Files, item: Planned) -> bool:
    """Make one part in the working files; False when the entity holds it already."""
    if item.change.entity is not None:
        if item.entity in files.paths or item.entity in files.now:
            raise _UnwritableError("an entity has that id already")
        files.set(item.entity, item.change.entity.entity(), item.n)
        return True
    current = files.current(item.entity)
    if current is None:
        raise _UnwritableError("no entity has that id")
    new = with_part(current, item.change)
    if new is None:
        return False
    files.set(item.entity, new, item.n)
    return True


def _unplan(files: _Files, line: Line, log: Folded, undoing: set[str]) -> bool:
    """Undo one op in the working files; False when the file no longer held what it added."""
    if line.op == "reject":
        return True
    entity_id, change = cast(str, line.entity), cast(Change, line.change)
    current = files.current(entity_id)
    if current is None:
        return False
    if line.op == "add":
        new = without_part(current, change)
        if new is None:
            return False
        files.set(entity_id, new)
        return True
    if current != cast(EntityFields, change.entity).entity():
        later = [
            op.id
            for op in log.by_id.values()
            if op.entity == entity_id
            and op.op == "add"
            and op.at >= line.at
            and op.id not in log.undone_by
            and op.id not in undoing
        ]
        if later:
            raise _UnwritableError(f"the entity changed since it was created; undo {', '.join(later)} first")
        raise _UnwritableError("the entity was changed by hand since it was created; change or delete its file")
    files.set(entity_id, None)
    return True


def _named(files: _Files, line: Line, log: Folded) -> set[str]:
    """The entity file a log line changes: a create's new file, an add's file, and for an undo its op's."""
    target = log.by_id.get(line.of or "") if line.op == "undo" else line
    if target is None or target.change is None:
        return set()
    if target.change.entity is not None:
        return {entity_rel(target.change.entity.type, target.change.entity.id)}
    path = files.file(cast(str, target.entity))
    return {path.relative_to(files.root).as_posix()} if path is not None else set()


def _message(lines: Sequence[Line]) -> str:
    first = lines[0]
    subject = f"Entity: {first.entity or '-'} {first.op}" if len(lines) == 1 else f"Entities: {len(lines)} changes"
    return subject + "\n\n" + "\n".join(f"Curate: {line.id} {line.op} {line.entity or '-'}" for line in lines)


class EntityStore:
    def __init__(self, vault: Vault) -> None:
        self.vault = vault
        self.root = vault.root

    def apply(
        self, planned: Sequence[Planned], *, rejects: Sequence[Reject] = (), by: By = "user", via: Via = "cli"
    ) -> Written:
        """`_apply`, then the files vault.yaml names rendered from the entities (DR-0554)."""
        return _rendered(self.vault, self._apply(planned, rejects=rejects, by=by, via=via))

    def _apply(
        self, planned: Sequence[Planned], *, rejects: Sequence[Reject] = (), by: By = "user", via: Via = "cli"
    ) -> Written:
        """Write a changes file's parts: all, each with its log line, in one commit, or none; and the keys an edit
        unticked, as rejections in the same commit."""
        problems = screen(self.vault, planned)
        if problems:
            raise RefusedError(problems)
        rejected = [r for r in rejects if listed(r.value, self.vault.config.never_store) is None]
        with self.vault.writing():
            edited = self._edited()
            finished = self._finish(edited)
            if finished:
                # Its commit moved HEAD: what differs from it is asked again.
                edited = self._edited()
            files = _Files(self.root)
            kept: list[Planned] = []
            for item in planned:
                try:
                    if _plan(files, item):
                        kept.append(item)
                except _UnwritableError as e:
                    problems.append(f"change {item.n}: {e}")
            problems += files.unreadable()
            if problems:
                raise RefusedError(list(dict.fromkeys(problems)))
            already = len(planned) - len(kept)
            if not kept and not rejected:
                return Written(finished=finished, already=already)
            taken = self._take_in(files, edited)
            parts: list[dict[str, Any]] = [
                {
                    "op": "create" if p.change.entity is not None else "add",
                    "key": key_of(p.entity, p.change),
                    "entity": p.entity,
                    "change": p.change,
                    "round": p.round,
                    "card": p.card,
                    "rule": p.rule,
                    "evidence": p.evidence,
                }
                for p in kept
            ]
            parts += [
                {"op": "reject", "key": r.key, "entity": r.entity, "round": r.round, "card": r.card} for r in rejected
            ]
            lines = self._lines(parts, by, via)
            commit = self._write(lines, files.outputs(), _message(lines))
            return Written(lines, commit, finished, taken, already)

    def reject(self, rejects: Sequence[Reject], *, via: Via = "cli") -> Written:
        """Put keys on the stoplist, a line each, in one commit; a key whose text a never-store list names writes no
        line, since the line would commit the name (DR-0464)."""
        return self.apply([], rejects=rejects, via=via)

    def lift(self, keys: Sequence[str], *, via: Via = "cli") -> Written:
        """`_lift`, then the files vault.yaml names rendered from the entities (DR-0554)."""
        return _rendered(self.vault, self._lift(keys, via=via))

    def _lift(self, keys: Sequence[str], *, via: Via = "cli") -> Written:
        """Take keys off the stoplist, a line each, in one commit; refused whole when a key's text is on a never-store
        list, since the line would commit it again (DR-0464)."""
        lists = self.vault.config.never_store
        problems = [
            f"key {n}: its text is on the never-store {on} list; it stays on the stoplist"
            for n, key in enumerate(dict.fromkeys(keys), start=1)
            if (on := listed(curatelog.key_text(key), lists)) is not None
        ]
        if problems:
            raise RefusedError(problems)
        with self.vault.writing():
            finished = self._finish(self._edited())
            lines = self._lines([{"op": "lift", "key": key} for key in dict.fromkeys(keys)], "user", via)
            return Written(lines, self._write(lines, {}, _message(lines)), finished)

    def undo(
        self,
        op_ids: Sequence[str],
        *,
        via: Via = "cli",
        round_id: str | None = None,
        by: By = "user",
        why: str | None = None,
    ) -> Written:
        """`_undo`, then the files vault.yaml names rendered from the entities (DR-0554)."""
        return _rendered(self.vault, self._undo(op_ids, via=via, round_id=round_id, by=by, why=why))

    def _undo(
        self,
        op_ids: Sequence[str],
        *,
        via: Via = "cli",
        round_id: str | None = None,
        by: By = "user",
        why: str | None = None,
    ) -> Written:
        """Undo logged ops by their ids, the latest first, in one commit; refused whole when one can't be undone. The
        round open on this machine goes on each line: an undo of a decision of that round reopens its card and
        stoplists nothing (DR-0462). Tier 1's undo of its own carries the op's rule and why (DR-0469)."""
        with self.vault.writing():
            edited = self._edited()
            finished = self._finish(edited)
            if finished:
                # Its commit moved HEAD: what differs from it is asked again.
                edited = self._edited()
            log = curatelog.fold(curatelog.read_all(self.root).lines)
            problems: list[str] = []
            targets: list[Line] = []
            for op_id in dict.fromkeys(op_ids):
                line = log.by_id.get(op_id)
                if line is None:
                    problems.append(f"{op_id}: no op in the log has that id")
                elif line.op in ("undo", "lift"):
                    problems.append(f"{op_id}: an undo or a lift can't be undone")
                elif op_id in log.undone_by:
                    problems.append(f"{op_id}: already undone by {log.undone_by[op_id]}")
                elif (on := listed(curatelog.key_text(line.key), self.vault.config.never_store)) is not None:
                    # An undo's line would commit the value again (DR-0464): the file is the user's to edit by hand.
                    problems.append(f"{op_id}: its text is on the never-store {on} list; edit the entity file by hand")
                else:
                    targets.append(line)
            files = _Files(self.root)
            only: list[str] = []
            for line in sorted(targets, key=curatelog.order, reverse=True):
                try:
                    if not _unplan(files, line, log, {t.id for t in targets}):
                        only.append(line.id)
                except _UnwritableError as e:
                    problems.append(f"{line.id}: {e}")
            if problems:
                raise RefusedError(problems)
            if not targets:
                return Written(finished=finished)
            taken = self._take_in(files, edited)
            parts: list[dict[str, Any]] = [
                {"op": "undo", "key": t.key, "entity": t.entity, "of": t.id, "round": round_id}
                | ({"rule": t.rule, "why": why} if by == "tier1" else {})
                for t in targets
            ]
            lines = self._lines(parts, by, via)
            commit = self._write(lines, files.outputs(), _message(lines))
            return Written(lines, commit, finished, taken, only_logged=tuple(only))

    # -- the steps ---------------------------------------------------------------------------------------------

    def _lines(self, parts: list[dict[str, Any]], by: By, via: Via) -> tuple[Line, ...]:
        """The log lines for a write's parts, each given as its op, key, entity and change or the op it undoes."""
        now = datetime.now(UTC).replace(microsecond=0)
        ids = curatelog.new_ids(curatelog.read_all(self.root).lines, self.vault.machine, now, len(parts))
        return tuple(
            Line(schema_version=1, id=op_id, at=now, machine=self.vault.machine, by=by, via=via, **part)
            for op_id, part in zip(ids, parts, strict=True)
        )

    def _edited(self) -> set[str]:
        """What differs from HEAD under entities/, untracked files too: asked once for a stopped write's lines and
        the take-in both (DR-0529)."""
        git = self.vault.git
        return {*git.changed_against_head("entities"), *git.untracked("entities", ignored_too=True)}

    def _take_in(self, files: _Files, edited: set[str]) -> tuple[str, ...]:
        """Commit, each on its own, the hand edits of the existing files this write touches."""
        taken: list[str] = []
        for entity_id in files.now:
            paths = files.paths.get(entity_id)
            rel = paths[0].relative_to(self.root).as_posix() if paths else None
            if rel in edited:
                self.vault.commit([rel], f"Entity: {entity_id} hand edit")
                taken.append(rel)
        return tuple(taken)

    def _write(
        self, lines: Sequence[Line], outputs: dict[str, str | None], message: str, logs: Sequence[str] = ()
    ) -> str | None:
        """Append the lines to the log, then write or delete the files, then commit them all; a failure puts every
        file back and unstages it."""
        home = self.vault.home
        curatelog.folder(self.root)
        originals: dict[str, str | None] = {}

        def keep(rel: str) -> Path:
            path = self.root / rel
            originals.setdefault(rel, path.read_bytes().decode("utf-8") if path.exists() else None)
            path.parent.mkdir(parents=True, exist_ok=True)
            return path

        try:
            for line in lines:
                # A log file that isn't UTF-8 is refused before anything of it is kept or written.
                path = self.root / line.rel
                text = curatelog.text_of(path, line.rel) if path.exists() else ""
                keep(line.rel)
                sep = "\n" if text and not text.endswith("\n") else ""
                write_replacing(home, path, f"{text}{sep}{line.text()}\n", "curate-log")
            for rel, text in outputs.items():
                path = keep(rel)
                if text is None:
                    path.unlink()
                else:
                    write_replacing(home, path, text, "entity")
            return self.vault.commit(list(dict.fromkeys([*logs, *originals])), message)
        except BaseException as e:
            for rel, before in originals.items():
                if not isinstance(e, UnmergedError):
                    self.vault.git.run("reset", "-q", "--", rel, check=False)
                if before is None:
                    (self.root / rel).unlink(missing_ok=True)
                else:
                    write_replacing(home, self.root / rel, before, "restore")
            raise

    def _finish(self, edited: set[str]) -> tuple[str, ...]:
        """Finish the lines a write that stopped before its commit left, and commit them with what they change.
        `edited` is what differs from HEAD under entities/, asked as the write began."""
        pending = curatelog.unfinished(self.vault, strict=True, edited=edited)
        if not pending:
            return ()
        log = curatelog.fold(curatelog.read_all(self.root).lines)
        files = _Files(self.root)
        named: set[str] = set()
        for line in pending:
            with contextlib.suppress(_UnwritableError):
                named |= _named(files, line, log)
                if line.change is not None:
                    _plan(files, Planned(0, cast(str, line.entity), line.change))
                elif line.op == "undo" and line.of in log.by_id:
                    _unplan(files, log.by_id[line.of], log, set())
        # A write killed after its file's rename left the change in the tree: it goes in with its line.
        logs = sorted({line.rel for line in pending} | (named & edited))
        body = "\n".join(f"Curate: {line.id} {line.op} {line.entity}" for line in pending)
        self._write([], files.outputs(), f"Entities: finish a write that stopped\n\n{body}", logs)
        return tuple(line.id for line in pending)
