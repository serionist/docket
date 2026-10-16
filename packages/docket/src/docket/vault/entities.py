"""Entities: the keyword layer, one YAML file per person, customer, project, site or term.

`entities/<type>/<id>.yaml`:

    id: northwind                                   the file's name; a slug, unique across all types
    type: customer                                  the folder's name; one of ENTITY_TYPES
    name: {en: Northwind, hu: Northwind}            a name per language code, at least one
    aliases:                                        other ways it is written or said
      - {text: Northwind Traders}
      - {text: Nortwin, kind: asr}                  how speech recognition mangles it
      - {text: Norton, kind: asr, needs: [ben-sample]}   counts only with one of these in the same chunk
    related: {person: [ben-sample]}                 a type to entity ids
    ids: {email_domain: northwind.example}          a key to a string or a list of strings
    status: active                                  or archived
    role: ...                                       free text; so is note
    schema: 1                                       absent or 1; any other value isn't read by these rules

Read with YAML's base loader, so every value is text (`no` and `2026` stay names), and leniently: an unknown key or type
is a problem and the rest of the file loads; a file that can't be read as an entity is a problem and is skipped. Users'
vaults hold hundreds of these files, so the format is a one-way door (DR-0113).

Docket writes a file whole in one canonical form (`render`), and only a file that reads without a problem or a
comment, since the rewrite would lose what the loader didn't read (DR-0463).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

import yaml

from docket.vault.anchors import normalize_newlines
from docket.vault.processed import DEVICE_NAMES, SLUG, SLUG_MAX

ENTITY_TYPES = (
    "person", "org", "customer", "project", "site", "device", "product", "topic", "stream", "meeting-series",
    "document", "repo", "place", "term",
)  # fmt: skip
KEYS = frozenset({"id", "type", "name", "aliases", "related", "ids", "status", "role", "note", "schema"})
ALIAS_KEYS = frozenset({"text", "kind", "needs"})
LANG = re.compile(r"^[a-z]{2,3}$")
# A source's name can hold a dot (slack.work), and an ids key names a source.
ID_KEY = re.compile(r"^[a-z0-9_.-]+$")
WORDY = re.compile(r"[^\W_]")
RELATED_RULE = "lowercase letters, digits and inner hyphens"
IDS_RULE = "lowercase letters, digits, ., _ or -"

AliasKind = Literal["plain", "asr"]


@dataclass(frozen=True)
class Alias:
    text: str
    kind: AliasKind = "plain"
    needs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Entity:
    id: str
    type: str
    path: str
    names: dict[str, str]
    aliases: tuple[Alias, ...] = ()
    related: dict[str, tuple[str, ...]] = field(default_factory=dict[str, tuple[str, ...]])
    ids: dict[str, tuple[str, ...]] = field(default_factory=dict[str, tuple[str, ...]])
    status: Literal["active", "archived"] = "active"
    role: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class EntityProblem:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


@dataclass
class Entities:
    entities: list[Entity]
    problems: list[EntityProblem]
    files: list[str]

    def by_id(self) -> dict[str, Entity]:
        return {e.id: e for e in self.entities}


class _SkipError(Exception):
    """A file that can't be read as an entity."""


class _Loader(yaml.BaseLoader):
    """YAML's base loader, noting each key a mapping repeats: YAML keeps the last silently, and a second `aliases:`
    added at the end of a file would replace the first."""

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self.repeated: list[tuple[int, str]] = []

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[str] = set()
        for key_node, _ in node.value:
            # A key that isn't text (a list) is refused by the loader itself, as YAML that isn't an entity.
            if isinstance(key_node, yaml.ScalarNode):
                key = str(key_node.value)
                if key in seen:
                    self.repeated.append((key_node.start_mark.line + 1, key))
                seen.add(key)
        return super().construct_mapping(node, deep=deep)


def entity_files(root: Path) -> list[Path]:
    """The files the loader reads: `.yaml` under entities/ and one folder down, never a name starting with `_` or
    `.`, in path order."""
    folder = root / "entities"
    found: list[Path] = []
    for child in sorted(folder.iterdir()) if folder.is_dir() else []:
        if child.name.startswith(("_", ".")):
            continue
        if child.is_dir():
            found += [p for p in sorted(child.iterdir()) if p.is_file() and not p.name.startswith(("_", "."))]
        elif child.suffix == ".yaml":
            found.append(child)
    return found


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} is text")
    return value.strip()


def _texts(value: Any, what: str) -> tuple[str, ...]:
    """A string or a list of strings."""
    items = cast(list[Any], value) if isinstance(value, list) else [value]
    return tuple(_text(item, what) for item in items)


class _Reader:
    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.problems: list[EntityProblem] = []

    def problem(self, message: str) -> None:
        self.problems.append(EntityProblem(self.rel, message))

    def names(self, value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            raise _SkipError("name is a map of a language code to the name, such as {en: Northwind}")
        out: dict[str, str] = {}
        for lang, name in cast(dict[Any, Any], value).items():
            if not isinstance(lang, str) or not LANG.match(lang) or not isinstance(name, str) or not WORDY.search(name):
                self.problem(f"name: {lang!s}: a language code (two or three lowercase letters) and the name")
                continue
            out[lang] = name.strip()
        if not out:
            raise _SkipError("name gives the name in at least one language")
        return out

    def alias(self, n: int, value: Any) -> Alias | None:
        if not isinstance(value, dict):
            self.problem(f"aliases item {n}: an alias is {{text: ..., kind: asr, needs: [...]}}")
            return None
        item = {str(k): v for k, v in cast(dict[Any, Any], value).items()}
        for key in sorted(set(item) - ALIAS_KEYS):
            self.problem(f"aliases item {n}: unknown key {key!r} (text, kind, needs)")
        text = item.get("text")
        if not isinstance(text, str) or not WORDY.search(text):
            self.problem(f"aliases item {n}: text is the alias, with at least one letter or digit")
            return None
        kind: AliasKind = "plain"
        if "kind" in item:
            if item["kind"] == "asr":
                kind = "asr"
            else:
                self.problem(f"aliases item {n}: kind is asr or absent, not {item['kind']!r}; read as plain")
        try:
            needs = _texts(item["needs"], "needs") if "needs" in item else ()
        except ValueError:
            # Without its condition the alias would match everywhere: a `needs` alias is one that misfires alone.
            self.problem(f"aliases item {n}: needs is a list of entity ids; the alias is left out")
            return None
        return Alias(text.strip(), kind, needs)

    def aliases(self, value: Any) -> tuple[Alias, ...]:
        if not isinstance(value, list):
            self.problem("aliases is a list of {text: ...}")
            return ()
        found = (self.alias(n, item) for n, item in enumerate(cast(list[Any], value), start=1))
        return tuple(a for a in found if a is not None)

    def mapping(self, key: str, value: Any, keys: re.Pattern[str], rule: str) -> dict[str, tuple[str, ...]]:
        if not isinstance(value, dict):
            self.problem(f"{key} is a map")
            return {}
        out: dict[str, tuple[str, ...]] = {}
        for k, v in cast(dict[Any, Any], value).items():
            try:
                if not isinstance(k, str) or not keys.match(k):
                    raise ValueError
                out[k] = _texts(v, key)
            except ValueError:
                self.problem(f"{key}: {k!s}: a key of {rule}, then text or a list of it")
        return out

    def free(self, key: str, value: Any) -> str | None:
        if isinstance(value, str):
            return value.strip()
        self.problem(f"{key} is text")
        return None

    def read(self, data: dict[str, Any], stem: str, folder: str | None) -> Entity:
        if data.get("schema", "1") != "1":
            raise _SkipError(f"schema {data['schema']!r} is a format this Docket doesn't read; update Docket")
        for key in sorted(set(data) - KEYS):
            self.problem(f"unknown key {key!r}")
        entity_id, entity_type = data.get("id"), data.get("type")
        if not isinstance(entity_id, str) or not SLUG.match(entity_id) or len(entity_id) > SLUG_MAX:
            raise _SkipError("id is a slug: lowercase letters, digits and inner hyphens, at most 80 characters")
        if entity_id in DEVICE_NAMES:
            raise _SkipError(f"id {entity_id!r} is a name Windows reserves for a device")
        if entity_id != stem:
            raise _SkipError(f"id {entity_id!r} isn't the file's name; the file is entities/<type>/<id>.yaml")
        if not isinstance(entity_type, str) or entity_type != folder:
            raise _SkipError(f"type {entity_type!r} isn't the folder's name; the file is entities/<type>/<id>.yaml")
        if entity_type not in ENTITY_TYPES:
            self.problem(f"type {entity_type!r} isn't one this Docket knows ({', '.join(ENTITY_TYPES)}); loaded")
        if "name" not in data:
            raise _SkipError("name is required: {en: ...}")
        status = data.get("status", "active")
        if status not in ("active", "archived"):
            self.problem(f"status is active or archived, not {status!r}; read as active")
            status = "active"
        return Entity(
            id=entity_id,
            type=entity_type,
            path=self.rel,
            names=self.names(data["name"]),
            aliases=self.aliases(data["aliases"]) if "aliases" in data else (),
            related=self.mapping("related", data["related"], SLUG, RELATED_RULE) if "related" in data else {},
            ids=self.mapping("ids", data["ids"], ID_KEY, IDS_RULE) if "ids" in data else {},
            status=status,
            role=self.free("role", data["role"]) if "role" in data else None,
            note=self.free("note", data["note"]) if "note" in data else None,
        )


def load_file(root: Path, path: Path) -> tuple[Entity | None, list[EntityProblem]]:
    def text() -> str:
        try:
            return path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            raise _SkipError("isn't UTF-8") from None

    return _load(path.relative_to(root).as_posix(), text)


def load_text(rel: str, text: str) -> tuple[Entity | None, list[EntityProblem]]:
    """An entity file's text, read as the file at `rel` (entities/<type>/<id>.yaml) would be."""
    return _load(rel, lambda: text)


def _load(rel: str, read: Callable[[], str]) -> tuple[Entity | None, list[EntityProblem]]:
    reader = _Reader(rel)
    path = PurePosixPath(rel)
    parts = path.parts[1:]
    try:
        if path.suffix != ".yaml":
            raise _SkipError("an entity file ends in .yaml")
        if len(parts) != 2:
            raise _SkipError("an entity file goes in entities/<type>/<id>.yaml")
        text = read()
        loader = _Loader(normalize_newlines(text.removeprefix("﻿")))
        try:
            data: Any = loader.get_single_data()
        except yaml.YAMLError as e:
            raise _SkipError(f"isn't YAML: {getattr(e, 'problem', None) or e}") from None
        finally:
            loader.dispose()
        for line, key in loader.repeated:
            reader.problem(f"line {line}: {key!r} is given twice; the last one is read")
        if not isinstance(data, dict):
            raise _SkipError("an entity is keys and values: id, type, name, ...")
        fields = {str(k): v for k, v in cast(dict[Any, Any], data).items()}
        return reader.read(fields, path.stem, parts[0]), reader.problems
    except _SkipError as e:
        return None, [*reader.problems, EntityProblem(rel, f"{e}; skipped")]


def load_entities(root: Path) -> Entities:
    """Every entity under root/entities, with what is wrong in each file; ids that name no entity are problems too."""
    entities: list[Entity] = []
    problems: list[EntityProblem] = []
    seen: dict[str, str] = {}
    files = entity_files(root)
    for path in files:
        entity, found = load_file(root, path)
        problems += found
        if entity is None:
            continue
        if entity.id in seen:
            problems.append(EntityProblem(entity.path, f"{seen[entity.id]} has the id {entity.id!r} too; skipped"))
            continue
        seen[entity.id] = entity.path
        entities.append(entity)
    for entity in entities:
        named = {(f"needs of {a.text!r}", n) for a in entity.aliases for n in a.needs}
        named |= {(f"related {t}", n) for t, ids in entity.related.items() for n in ids}
        for what, other in sorted(named):
            if other not in seen:
                problems.append(EntityProblem(entity.path, f"{what}: no entity has the id {other!r}"))
    entities.sort(key=lambda e: e.id)
    return Entities(entities, problems, [p.relative_to(root).as_posix() for p in files])


# Wide enough that no name, alias or note is ever folded onto a second line.
WIDTH = 1_000_000


def entity_rel(entity_type: str, entity_id: str) -> str:
    return f"entities/{entity_type}/{entity_id}.yaml"


class _Flow(dict[str, Any]):
    """A mapping written on one line."""


class _Dumper(yaml.SafeDumper):
    """YAML's safe dumper: a value YAML would read as something other than text (`no`, `2026`) is quoted, and a list's
    items are indented under their key, as the design's examples write them."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        super().increase_indent(flow, False)


def _flow(dumper: yaml.SafeDumper, data: _Flow) -> yaml.Node:
    return dumper.represent_mapping("tag:yaml.org,2002:map", list(data.items()), flow_style=True)


_Dumper.add_representer(_Flow, _flow)


def _alias(alias: Alias) -> _Flow:
    item = _Flow(text=alias.text)
    if alias.kind == "asr":
        item["kind"] = "asr"
    if alias.needs:
        item["needs"] = list(alias.needs)
    return item


def render(entity: Entity) -> str:
    """An entity in the one form Docket writes (DR-0463): DR-0113's keys in order, name, related and ids as maps on one
    line, each alias a map on its own line; an id with one value as text, related ids always as a list; status only
    when archived, and no schema line (absent is 1)."""
    data: dict[str, Any] = {"id": entity.id, "type": entity.type, "name": _Flow(sorted(entity.names.items()))}
    if entity.aliases:
        data["aliases"] = [_alias(a) for a in entity.aliases]
    if entity.related:
        data["related"] = _Flow({k: list(v) for k, v in entity.related.items()})
    if entity.ids:
        data["ids"] = _Flow({k: v[0] if len(v) == 1 else list(v) for k, v in entity.ids.items()})
    if entity.status != "active":
        data["status"] = entity.status
    if entity.role is not None:
        data["role"] = entity.role
    if entity.note is not None:
        data["note"] = entity.note
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, default_flow_style=False, width=WIDTH)


def comment_line(text: str) -> int | None:
    """The first line holding a comment in an entity file that reads, or None. A `#` is a comment where it starts a
    line or follows a space or tab outside every value, as YAML reads it."""
    text = normalize_newlines(text.removeprefix("﻿"))
    scanner = yaml.BaseLoader(text)
    values: list[tuple[int, int]] = []
    try:
        while scanner.check_token():
            # The stubs leave get_token untyped; the scanner returns a token.
            token = cast(yaml.Token, scanner.get_token())  # pyright: ignore[reportUnknownMemberType]
            if isinstance(token, yaml.ScalarToken):
                values.append((token.start_mark.index, token.end_mark.index))
    finally:
        scanner.dispose()
    for m in re.finditer("#", text):
        i = m.start()
        if (i == 0 or text[i - 1] in " \t\n") and not any(start <= i < end for start, end in values):
            return text.count("\n", 0, i) + 1
    return None
