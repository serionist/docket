"""The curate log (DR-0460): every change Docket makes to entities, and every rejection, undo and lift, as one JSON line
of `entities/_log/<yyyy-mm>-<machine>.jsonl`, committed with the change it records.

    {"schema_version": 1, "id": "20261016T091402-desk-1", "op": "add", "at": "2026-10-16T09:14:02Z",
     "machine": "desk", "by": "user", "via": "cli", "key": "alias:northwind:nortwin", "entity": "northwind",
     "change": {"alias": {"text": "Nortwin", "kind": "asr", "needs": []}}}

| op | what |
|---|---|
| create | a new entity, its fields as written (`change.entity`) |
| add | one alias, name, id or related link added to an entity |
| reject | one key on the stoplist |
| undo | the inverse of the op `of` names, applied to the entity file as it stood |
| lift | one key taken off the stoplist |

A key is `<kind>:<entity or ->:<text>`, the text folded as the index folds `aliases.folded` and an id's value
casefolded (DR-0461). The stoplist is folded from the lines: a key's latest reject, or undo of an add, puts it on; a
lift, or an undo of its reject, takes it off. An undo from the round its op was decided in fixes a click and stoplists
nothing; an undone create is handed to the miner, which works out its keys from the index (DR-0462).

Names and ids as the entity files hold them, counts and locators; never a snippet. One file per machine and month,
merged by union like the ledger's ops, and read leniently: a line that doesn't read is a problem and is skipped. A line
is its change's intent, written before the entity file and committed with it, so the lines the working tree holds and
HEAD doesn't are what a stopped write left, and the next write finishes them (DR-0463). The format is a one-way door: a
later one writes schema_version 2, which this Docket skips with a problem.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Self, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from docket.errors import DocketError
from docket.index.text import fold as fold_word
from docket.index.text import words
from docket.vault import machine as machines
from docket.vault.entities import ENTITY_TYPES, ID_KEY, LANG, WORDY, Alias, Entity, entity_rel
from docket.vault.processed import DEVICE_NAMES, SLUG, SLUG_MAX
from docket.vault.vault import Vault

LOG_DIR = "entities/_log"
SUFFIX = ".jsonl"
FILE_NAME = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])-(.+)\.jsonl$")
# <yyyymmddThhmmss>-<machine>-<n>: a machine's name never ends in -<digits>, so the three parts never blur.
OP_ID = re.compile(r"^(\d{8}T\d{6})-(.+)-([1-9]\d*)$")
STAMP = "%Y%m%dT%H%M%S"
MAX_LOCATORS = 5

Op = Literal["create", "add", "reject", "undo", "lift"]
By = Literal["user", "tier1"]
Via = Literal["cli", "page"]


def folded(text: str) -> str:
    """Text as the index folds an alias (`aliases.folded`): its words, accents and case folded, one space between."""
    return " ".join(fold_word(w) for w in words(text))


# -- the fields, checked as an entity file's are; a message never repeats the value it refuses --------------------


def _wordy(value: str) -> str:
    if not WORDY.search(value):
        raise ValueError("text with at least one letter or digit")
    return value.strip()


def _slug(value: str) -> str:
    if not SLUG.match(value) or len(value) > SLUG_MAX:
        raise ValueError("an id: lowercase letters, digits and inner hyphens, at most 80 characters")
    return value


def _entity_id(value: str) -> str:
    if value in DEVICE_NAMES:
        raise ValueError("an id that isn't a name Windows keeps for a device")
    return _slug(value)


def _type(value: str) -> str:
    if value not in ENTITY_TYPES:
        raise ValueError(f"one of {', '.join(ENTITY_TYPES)}")
    return value


def _lang(value: str) -> str:
    if not LANG.match(value):
        raise ValueError("a language code: two or three lowercase letters")
    return value


def _id_key(value: str) -> str:
    if not ID_KEY.match(value):
        raise ValueError("a key of lowercase letters, digits, ., _ or -")
    return value


def _some(value: Any) -> Any:
    """Text or a list of it, as an entity file allows for needs and for a map's values."""
    return [value] if isinstance(value, str) else value


Wordy = Annotated[str, AfterValidator(_wordy)]
Slug = Annotated[str, AfterValidator(_slug)]
Lang = Annotated[str, AfterValidator(_lang)]
IdKey = Annotated[str, AfterValidator(_id_key)]
Free = Annotated[str, AfterValidator(str.strip)]
Slugs = Annotated[tuple[Slug, ...], BeforeValidator(_some)]
Texts = Annotated[tuple[Wordy, ...], BeforeValidator(_some), Field(min_length=1)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AliasFields(_Model):
    text: Wordy
    kind: Literal["plain", "asr"] = "plain"
    needs: Slugs = ()


class EntityFields(_Model):
    """An entity's fields, as a create writes them and the entity file holds them."""

    id: Annotated[str, AfterValidator(_entity_id)]
    type: Annotated[str, AfterValidator(_type)]
    name: dict[Lang, Wordy] = Field(min_length=1)
    aliases: tuple[AliasFields, ...] = ()
    related: dict[Slug, Slugs] = Field(default_factory=dict[str, tuple[str, ...]])
    ids: dict[IdKey, Texts] = Field(default_factory=dict[str, tuple[str, ...]])
    status: Literal["active", "archived"] = "active"
    role: Free | None = None
    note: Free | None = None

    @classmethod
    def of(cls, entity: Entity) -> EntityFields:
        return cls(
            id=entity.id,
            type=entity.type,
            name=dict(entity.names),
            aliases=tuple(AliasFields(text=a.text, kind=a.kind, needs=a.needs) for a in entity.aliases),
            related=dict(entity.related),
            ids=dict(entity.ids),
            status=entity.status,
            role=entity.role,
            note=entity.note,
        )

    def entity(self) -> Entity:
        return Entity(
            id=self.id,
            type=self.type,
            path=entity_rel(self.type, self.id),
            names=dict(self.name),
            aliases=tuple(Alias(a.text, a.kind, a.needs) for a in self.aliases),
            related=dict(self.related),
            ids=dict(self.ids),
            status=self.status,
            role=self.role,
            note=self.note,
        )


class NameFields(_Model):
    lang: Lang
    text: Wordy


class IdFields(_Model):
    key: IdKey
    value: Wordy


class RelatedFields(_Model):
    type: Slug
    target: Slug


class Change(_Model):
    """What a create or an add writes: exactly one of a whole entity, an alias, a name, an id or a related link."""

    entity: EntityFields | None = None
    alias: AliasFields | None = None
    name: NameFields | None = None
    id: IdFields | None = None
    related: RelatedFields | None = None

    @model_validator(mode="after")
    def _one(self) -> Self:
        if sum(part is not None for part in (self.entity, self.alias, self.name, self.id, self.related)) != 1:
            raise ValueError("a change is one of entity, alias, name, id or related")
        return self


class Evidence(_Model):
    chunks: int = Field(ge=0)
    containers: int = Field(ge=0)
    locators: tuple[str, ...] = Field(default=(), max_length=MAX_LOCATORS)


class Line(_Model):
    """One op, as one line of the log."""

    schema_version: Literal[1]
    id: str
    op: Op
    at: datetime
    machine: str
    by: By
    via: Via
    rule: str | None = None
    round: str | None = None
    card: str | None = None
    key: str = Field(min_length=1)
    entity: str | None = None
    change: Change | None = None
    of: str | None = None
    evidence: Evidence | None = None

    @field_validator("id", "of")
    @classmethod
    def _op_id(cls, value: str | None) -> str | None:
        if value is not None and not OP_ID.match(value):
            raise ValueError("an op's id: <yyyymmddThhmmss>-<machine>-<n>")
        return value

    @field_validator("at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("a time with its offset")
        return value.astimezone(UTC)

    @field_validator("machine")
    @classmethod
    def _machine(cls, value: str) -> str:
        try:
            return machines.validate(value)
        except DocketError:
            raise ValueError("a machine's name") from None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        change = self.change
        wants = {
            "create": change is not None and change.entity is not None and change.entity.id == self.entity,
            "add": change is not None and change.entity is None and self.entity is not None,
            "undo": change is None and self.of is not None,
            "reject": change is None and self.of is None,
            "lift": change is None,
        }
        if not wants[self.op]:
            raise ValueError(
                "create carries the entity it makes, add one part and its entity, undo the op it undoes in of, and "
                "reject and lift no change"
            )
        return self

    def text(self) -> str:
        return self.model_dump_json(exclude_none=True)

    @property
    def rel(self) -> str:
        """The log file this line goes in: its machine's, for the month it happened in."""
        return f"{LOG_DIR}/{self.at:%Y-%m}-{self.machine}{SUFFIX}"


# -- keys -------------------------------------------------------------------------------------------------------


def key_of(entity: str, change: Change) -> str:
    """The key a create or an add is logged under (DR-0461): a create's names its first name, by language code."""
    if change.entity is not None:
        return f"new:-:{folded(sorted(change.entity.name.items())[0][1])}"
    if change.alias is not None:
        return f"alias:{entity}:{folded(change.alias.text)}"
    if change.name is not None:
        return f"name:{entity}:{change.name.lang}={folded(change.name.text)}"
    if change.id is not None:
        return f"id:{entity}:{change.id.key}={change.id.value.casefold()}"
    related = cast(RelatedFields, change.related)
    return f"related:{entity}:{related.type}={related.target}"


# -- reading ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LogRead:
    """Every line that reads, in the order they happened, and what didn't read."""

    lines: tuple[Line, ...]
    problems: tuple[str, ...]


def _linked(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def folder(root: Path) -> Path:
    """The log's folder, never through a link: an entity write follows what the log says."""
    path = root / LOG_DIR
    if _linked(path):
        raise DocketError(f"{LOG_DIR} is a link; the curate log is read and written only as plain files in the vault")
    return path


def parse(text: str, where: str) -> tuple[list[Line], list[str]]:
    """The lines of a log's text that read, and a problem for each one that doesn't, named by its number."""
    lines: list[Line] = []
    problems: list[str] = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            lines.append(Line.model_validate_json(line))
        except ValidationError:
            problems.append(f"{where}: line {n} isn't a curate log line of schema 1; skipped")
    return lines, problems


def _ordered(lines: Iterable[Line]) -> tuple[Line, ...]:
    return tuple(sorted(lines, key=lambda line: (line.at, line.id)))


def read_all(root: Path) -> LogRead:
    """Every machine's log files. A file there whose name isn't `<yyyy-mm>-<machine>.jsonl`, or that isn't UTF-8, is
    a problem and is skipped."""
    path = folder(root)
    lines: list[Line] = []
    problems: list[str] = []
    for child in sorted(path.iterdir()) if path.is_dir() else []:
        where = f"{LOG_DIR}/{child.name}"
        m = FILE_NAME.match(child.name)
        if not child.is_file() or _linked(child) or m is None or machines.NAME.match(m.group(3)) is None:
            problems.append(f"{where}: isn't a log file (<yyyy-mm>-<machine>.jsonl); skipped")
            continue
        try:
            text = child.read_bytes().decode("utf-8-sig")
        except UnicodeDecodeError:
            problems.append(f"{where}: isn't UTF-8; skipped")
            continue
        found, bad = parse(text, where)
        lines += found
        problems += bad
    return LogRead(_ordered(lines), tuple(problems))


def unfinished(vault: Vault) -> list[Line]:
    """The lines the working tree's log files hold and HEAD doesn't: what a write that stopped before its commit
    left, in the order they happened."""
    git = vault.git
    rels = {*git.changed_against_head(LOG_DIR), *git.untracked(LOG_DIR, ignored_too=True)}
    out: list[Line] = []
    for rel in sorted(r for r in rels if (vault.root / r).is_file()):
        committed = set(git.run("show", f"HEAD:./{rel}", check=False).stdout.splitlines())
        text = (vault.root / rel).read_bytes().decode("utf-8-sig")
        out += parse("\n".join(line for line in text.splitlines() if line not in committed), rel)[0]
    return list(_ordered(out))


def new_ids(lines: Sequence[Line], machine: str, at: datetime, count: int) -> list[str]:
    """Ids for `count` new ops at one second on one machine, after any the log has for that second."""
    stamp = f"{at:{STAMP}}"
    taken = [int(m.group(3)) for line in lines if (m := OP_ID.match(line.id)) and m.group(1, 2) == (stamp, machine)]
    first = max(taken, default=0) + 1
    return [f"{stamp}-{machine}-{n}" for n in range(first, first + count)]


# -- folding ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Folded:
    """What the log adds up to: each op by id, which ops were undone and by what, the stoplist, the creates an undo
    hands to the miner, and when each key was last lifted."""

    by_id: dict[str, Line]
    undone_by: dict[str, str]
    stoplist: frozenset[str]
    undone_creates: tuple[Line, ...]
    lifted: dict[str, datetime]

    def lifted_since(self, key: str, when: datetime) -> bool:
        """Whether a key was lifted after a time: an undone create's keys stay off the stoplist once lifted."""
        return key in self.lifted and self.lifted[key] > when


def fold(lines: Iterable[Line]) -> Folded:
    ordered = _ordered(lines)
    by_id = {line.id: line for line in ordered}
    undone_by: dict[str, str] = {}
    on: dict[str, bool] = {}
    lifted: dict[str, datetime] = {}
    creates: list[Line] = []
    for line in ordered:
        target = by_id.get(line.of or "")
        if line.op == "reject":
            on[line.key] = True
        elif line.op == "lift" or (line.op == "undo" and target is not None and target.op == "reject"):
            on[line.key] = False
            lifted[line.key] = line.at
        if line.op != "undo" or target is None:
            continue
        undone_by[target.id] = line.id
        # An undo in the round its op was decided in fixes a click: the card opens again, and nothing is stoplisted.
        if line.round is not None and line.round == target.round:
            continue
        if target.op == "add":
            on[target.key] = True
        elif target.op == "create":
            creates.append(target)
    return Folded(by_id, undone_by, frozenset(k for k, v in on.items() if v), tuple(creates), lifted)
