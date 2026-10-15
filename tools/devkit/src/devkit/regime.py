"""The docs regime: `docs/docs-regime.yaml`, the schema every entry file is checked against and written by."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from devkit.repo import KitError
from devkit.yamlio import Shape, as_map, load_yaml

REGIME_PATH = "docs/docs-regime.yaml"
SOFT_DELETE = ("removed", "removed_why")


def _strs(data: Any, where: str) -> list[str]:
    if data is None:
        return []
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):  # pyright: ignore[reportUnknownVariableType]
        raise KitError(f"{REGIME_PATH}: {where} must be a list of names")
    return list(data)  # pyright: ignore[reportUnknownArgumentType]


@dataclass
class Kind:
    """One kind of entry: the fields it requires and allows, the values some take, and how it is written."""

    name: str
    key: str
    required: list[str]
    optional: list[str]
    one_of: list[str] = field(default_factory=list[str])
    order: list[str] = field(default_factory=list[str])
    id_pattern: re.Pattern[str] | None = None
    flow: bool = False
    flags: list[str] = field(default_factory=list[str])
    list_fields: list[str] = field(default_factory=list[str])
    values: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    dates: list[str] = field(default_factory=list[str])
    defaults: dict[str, str] = field(default_factory=dict[str, str])
    table: str | None = None

    @property
    def fields(self) -> list[str]:
        return [*self.order, *[f for f in SOFT_DELETE if f not in self.order]]

    @property
    def shape(self) -> Shape:
        return Shape(
            order=tuple(self.fields),
            flow=self.flow,
            flags=frozenset(self.flags),
            list_fields=frozenset(self.list_fields),
        )

    @property
    def numbered(self) -> bool:
        return self.id_pattern is not None


@dataclass
class Regime:
    root: Path
    folder_file: str
    folder_kinds: list[str]
    exclude: list[str]
    kinds: dict[str, Kind]
    citation_free: list[str]
    entry_dir: str | None = None
    entry_kinds: list[str] = field(default_factory=list[str])

    @staticmethod
    def load(root: Path) -> Regime:
        path = root / REGIME_PATH
        if not path.is_file():
            raise KitError(f"{REGIME_PATH} not found under {root}")
        raw: Any = load_yaml(path.read_text(encoding="utf-8"))
        data = as_map(raw)
        if not data:
            raise KitError(f"{REGIME_PATH}: not a mapping")
        loc = as_map(data.get("locations"))
        tables = as_map(loc.get("tables"))
        kinds: dict[str, Kind] = {}
        raw_kinds = as_map(data.get("kinds"))
        if not raw_kinds:
            raise KitError(f"{REGIME_PATH}: no kinds")
        for name, spec in raw_kinds.items():
            spec_map = as_map(spec)
            if not spec_map:
                raise KitError(f"{REGIME_PATH}: kind {name!r} must be a mapping")
            kinds[name] = _kind(name, spec_map, tables.get(name))
        folder_kinds = _strs(loc.get("folder_kinds"), "locations.folder_kinds")
        for k in [*folder_kinds, *tables]:
            if k not in kinds:
                raise KitError(f"{REGIME_PATH}: locations name kind {k!r}, which kinds doesn't define")
        entry_dir = loc.get("entry_dir")
        entry_kinds = _strs(loc.get("entry_kinds"), "locations.entry_kinds")
        if entry_dir is not None and (not isinstance(entry_dir, str) or not entry_dir or "/" in entry_dir):
            raise KitError(f"{REGIME_PATH}: locations.entry_dir must be one folder name")
        if bool(entry_dir) != bool(entry_kinds):
            raise KitError(f"{REGIME_PATH}: locations.entry_dir and entry_kinds go together")
        for k in entry_kinds:
            if k not in folder_kinds or not kinds[k].numbered:
                raise KitError(f"{REGIME_PATH}: entry_kinds names {k!r}, which isn't a numbered folder kind")
        return Regime(
            root=root,
            folder_file=str(loc.get("folder_file") or "index.yaml"),
            folder_kinds=folder_kinds,
            exclude=_strs(loc.get("exclude"), "locations.exclude"),
            kinds=kinds,
            citation_free=_strs(data.get("citation_free"), "citation_free"),
            entry_dir=entry_dir if isinstance(entry_dir, str) else None,
            entry_kinds=entry_kinds,
        )

    def kind(self, name: str) -> Kind:
        if name not in self.kinds:
            raise KitError(f"no kind {name!r}; the kinds are {', '.join(self.kinds)}")
        return self.kinds[name]

    @property
    def table_files(self) -> dict[str, str]:
        """Table kinds by the file that holds them."""
        return {k.table: k.name for k in self.kinds.values() if k.table}


def _kind(name: str, spec: dict[str, Any], table: Any) -> Kind:
    where = f"kinds.{name}"
    required = _strs(spec.get("required"), f"{where}.required")
    optional = _strs(spec.get("optional"), f"{where}.optional")
    one_of = _strs(spec.get("one_of"), f"{where}.one_of")
    order = _strs(spec.get("order"), f"{where}.order") or [*required, *one_of, *optional]
    declared = {*required, *optional, *one_of}
    if set(order) != declared:
        raise KitError(f"{REGIME_PATH}: {where}.order must list exactly the kind's fields")
    key = spec.get("key")
    if not isinstance(key, str) or key not in required:
        raise KitError(f"{REGIME_PATH}: {where}.key must name a required field")
    pattern = spec.get("id_pattern")
    values_raw: Any = spec.get("values") or {}
    values = {str(f): _strs(v, f"{where}.values.{f}") for f, v in values_raw.items()}
    defaults_raw: Any = spec.get("defaults") or {}
    return Kind(
        name=name,
        key=key,
        required=required,
        optional=optional,
        one_of=one_of,
        order=order,
        id_pattern=re.compile(pattern) if isinstance(pattern, str) else None,
        flow=spec.get("style") == "flow",
        flags=_strs(spec.get("flags"), f"{where}.flags"),
        list_fields=_strs(spec.get("list_fields"), f"{where}.list_fields"),
        values=values,
        dates=_strs(spec.get("dates"), f"{where}.dates"),
        defaults={str(k): str(v) for k, v in defaults_raw.items()},
        table=table if isinstance(table, str) else None,
    )
