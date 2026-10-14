"""`vault.yaml`: who the vault belongs to, its sources, and what must never be stored.

Unknown keys are refused rather than ignored: a mistyped `never_store` would otherwise quietly store what the user
listed never to store.
"""

from __future__ import annotations

import re
from collections.abc import Hashable
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from docket.errors import DocketError
from docket.vault.machine import validate as validate_machine

CONFIG_FILE = "vault.yaml"


class UniqueKeyLoader(yaml.SafeLoader):
    """A safe loader that refuses a key given twice in one map. YAML keeps the last one, so a second `people:` would
    quietly replace the list the user wrote first."""


def _unique_keys(loader: yaml.SafeLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key: Any = loader.construct_object(key_node)  # pyright: ignore[reportUnknownMemberType]
        if not isinstance(key, Hashable):
            continue  # construct_mapping refuses it with its own message
        if key in seen:
            raise yaml.constructor.ConstructorError(None, None, f"{key!r} is given twice", key_node.start_mark)
        seen.add(key)
    return loader.construct_mapping(node)


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_keys)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Owner(_Model):
    name: str
    emails: list[str] = Field(default_factory=list[str])
    languages: list[str] = Field(default_factory=lambda: ["en"])
    time_zone: str

    @field_validator("time_zone")
    @classmethod
    def _iana(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"{value!r} isn't an IANA time zone, such as Europe/Budapest") from e
        return value


class SourceConfig(_Model):
    enabled: bool = True
    # work: everything from it is work; mixed: it can carry private content, so records wait to be classified;
    # private: nothing from it is stored.
    sensitivity: Literal["work", "mixed", "private"] = "work"


class NeverStore(_Model):
    people: list[str] = Field(default_factory=list[str])
    containers: list[str] = Field(default_factory=list[str])
    keywords: list[str] = Field(default_factory=list[str])


class RedactionPattern(_Model):
    name: str
    pattern: str

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as e:
            raise ValueError(f"not a valid regular expression: {e}") from e
        return value


class Redaction(_Model):
    builtin: bool = True
    patterns: list[RedactionPattern] = Field(default_factory=list[RedactionPattern])


class VaultConfig(_Model):
    schema_version: Literal[1] = 1
    owner: Owner
    primary_machine: str
    sources: dict[str, SourceConfig] = Field(default_factory=dict[str, SourceConfig])
    never_store: NeverStore = Field(default_factory=NeverStore)
    redaction: Redaction = Field(default_factory=Redaction)

    @field_validator("primary_machine")
    @classmethod
    def _machine(cls, value: str) -> str:
        try:
            return validate_machine(value)
        except DocketError as e:
            raise ValueError(str(e)) from e

    @staticmethod
    def load(path: Path) -> VaultConfig:
        try:
            data: Any = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)  # noqa: S506 - a SafeLoader
        except yaml.YAMLError as e:
            raise DocketError(f"{path}: not valid YAML: {e}") from e
        try:
            return VaultConfig.model_validate(data or {})
        except ValidationError as e:
            problems = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())
            raise DocketError(f"{path}: {problems}") from e

    def dump(self) -> str:
        header = "# The vault's settings. docket reads this on every run; unknown keys are refused.\n"
        body = yaml.safe_dump(
            self.model_dump(mode="json"), sort_keys=False, allow_unicode=True, default_flow_style=False, width=100
        )
        return header + body
