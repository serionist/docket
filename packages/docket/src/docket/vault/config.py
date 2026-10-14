"""`vault.yaml`: who the vault belongs to, its sources, and what must never be stored.

Unknown keys are refused rather than ignored: a mistyped `never_store` would otherwise quietly store what the user
listed never to store.
"""

from __future__ import annotations

import re
from collections.abc import Hashable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from docket.errors import DocketError
from docket.vault.machine import validate as validate_machine
from docket.vault.records import source_name_problem

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


TOOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
HOST = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")


class SourceConfig(_Model):
    enabled: bool = True
    # work: everything from it is work; mixed: it can carry private content, so records wait to be classified;
    # private: nothing from it is stored.
    sensitivity: Literal["work", "mixed", "private"] = "work"
    # The source's mapper, relative to the consuming repo: .claude/skills/sources/<source>/mapper.py.
    mapper: str | None = None
    # The tools the capture hook posts for this source, by their own names, without the host's prefix.
    tools: list[str] = Field(default_factory=list[str])
    # The hosts media_put may fetch this source's media from, over https.
    media_hosts: list[str] = Field(default_factory=list[str])

    @field_validator("mapper")
    @classmethod
    def _mapper(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "\\" in value or PurePosixPath(value).is_absolute() or PureWindowsPath(value).anchor:
            raise ValueError("a mapper's path is relative to the consuming repo, written with /")
        if ".." in PurePosixPath(value).parts or not value.endswith(".py"):
            raise ValueError("a mapper's path stays inside the repo, with no .., and names a .py file")
        return value

    @field_validator("tools")
    @classmethod
    def _tools(cls, value: list[str]) -> list[str]:
        for tool in value:
            if not TOOL.match(tool) or tool.startswith("mcp__"):
                raise ValueError(
                    f"{tool!r} isn't a tool's own name: letters, digits, _, . and -, without the host's mcp__ prefix"
                )
        return value

    @field_validator("media_hosts")
    @classmethod
    def _hosts(cls, value: list[str]) -> list[str]:
        for host in value:
            if not HOST.match(host):
                raise ValueError(f"{host!r} isn't a host name: lowercase, with no scheme, port or path")
        return value


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
    # Where git keeps the vault: the top of a repo of its own, or a folder of the repo around it. No key means a repo
    # of its own, the only kind there was before folders.
    storage: Literal["repo", "folder"] = "repo"
    owner: Owner
    primary_machine: str
    sources: dict[str, SourceConfig] = Field(default_factory=dict[str, SourceConfig])
    never_store: NeverStore = Field(default_factory=NeverStore)
    redaction: Redaction = Field(default_factory=Redaction)

    @field_validator("sources")
    @classmethod
    def _source_names(cls, value: dict[str, SourceConfig]) -> dict[str, SourceConfig]:
        # A source's name builds paths (its skill's folder, its hold list), so `..` or `a/b` must never get that far.
        for name in value:
            problem = source_name_problem(name)
            if problem is not None:
                raise ValueError(problem)
        return value

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
