"""`vault.yaml`: who the vault belongs to, its sources, and what must never be stored.

Unknown keys are refused rather than ignored: a mistyped `never_store` would otherwise quietly store what the user
listed never to store.
"""

from __future__ import annotations

import os
import re
from collections.abc import Hashable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    ValidationError,
    field_validator,
    model_serializer,
    model_validator,
)

from docket.errors import DocketError
from docket.vault.home import Home, make_private
from docket.vault.machine import validate as validate_machine
from docket.vault.records import source_name_problem
from docket.vault.staging import retry

CONFIG_FILE = "vault.yaml"
# The one redaction switch vault.yaml held before credentials and phone numbers were split.
LEGACY_BUILTIN = "builtin"


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
    # A folder source (DR-0481): the folder docket scan reads, relative to the consuming repo or under ~/, and fnmatch
    # patterns on the paths inside it to leave out. Written only when set, so a vault.yaml without folder sources
    # reads as before on an older Docket.
    folder: str | None = None
    exclude: list[str] = Field(default_factory=list[str])

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

    @field_validator("folder")
    @classmethod
    def _folder(cls, value: str | None) -> str | None:
        if value is None:
            return None
        problem = folder_problem(value)
        if problem is not None:
            raise ValueError(problem)
        return value

    @field_validator("exclude")
    @classmethod
    def _exclude(cls, value: list[str]) -> list[str]:
        if any(not pattern.strip() for pattern in value):
            raise ValueError("an exclude pattern is a path pattern such as archive/* or *.draft.md, never empty")
        return value

    @model_validator(mode="after")
    def _folder_source(self) -> SourceConfig:
        if self.folder is None:
            if self.exclude:
                raise ValueError("exclude goes with folder: it leaves paths of a folder source out")
            return self
        if self.mapper is not None or self.tools or self.media_hosts:
            raise ValueError("a folder source has no mapper, tools or media_hosts: docket scan reads its folder")
        if self.sensitivity == "mixed":
            raise ValueError(
                "a folder source is work: mixed would hold every page on the Staging page one by one; keep work, and "
                "leave out what isn't with exclude"
            )
        if self.sensitivity == "private":
            raise ValueError("a folder source is work: to store nothing from it, set enabled: false instead")
        return self

    @model_serializer(mode="wrap")
    def _dump(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """`folder` and `exclude` only when set: an older Docket refuses keys it doesn't know (DR-0021)."""
        data: dict[str, Any] = handler(self)
        if self.folder is None:
            data.pop("folder", None)
        if not self.exclude:
            data.pop("exclude", None)
        return data


def folder_problem(value: str) -> str | None:
    """Why a folder source's folder can't be written in vault.yaml, or None: it is relative to the consuming repo, or
    under the home folder as ~/..., so it reads the same on every machine (DR-0481)."""
    if "\\" in value:
        return f"{value!r}: a folder is written with /, such as docs or ~/Work"
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).anchor:
        return (
            f"{value!r} is an absolute path, which names one machine's drive: write the folder relative to the repo "
            "(docs) or under your home folder (~/Work), and give a machine whose folder is elsewhere its own path in "
            "Docket's config.yaml, under folders"
        )
    if value.startswith("~") and value != "~" and not value.startswith("~/"):
        return f"{value!r}: ~ is your own home folder, written ~/Work; another user's isn't taken"
    if ".." in PurePosixPath(value).parts or not value.strip():
        return f"{value!r}: a folder stays inside the repo or under ~/, with no .."
    return None


class NeverStore(_Model):
    people: list[str] = Field(default_factory=list[str])
    containers: list[str] = Field(default_factory=list[str])
    keywords: list[str] = Field(default_factory=list[str])


class RedactionPattern(_Model):
    name: str
    pattern: str
    enabled: bool = True

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as e:
            raise ValueError(f"not a valid regular expression: {e}") from e
        return value


class Redaction(_Model):
    """Which redaction rules run. Credentials (passwords, API keys and tokens, private key blocks) are on unless the
    user turns them off; phone numbers are off unless the user turns them on. Names are never redacted: keeping one
    out is the never-store lists' job."""

    credentials: bool = True
    phone: bool = False
    patterns: list[RedactionPattern] = Field(default_factory=list[RedactionPattern])

    @model_validator(mode="before")
    @classmethod
    def _builtin(cls, data: Any) -> Any:
        """`builtin`, the one switch every vault.yaml written before held, reads as `credentials`."""
        if not isinstance(data, dict):
            return data
        fields = dict(cast(dict[str, Any], data))
        if LEGACY_BUILTIN not in fields:
            return fields
        if "credentials" in fields:
            raise ValueError("builtin is the old name of credentials; keep credentials and delete builtin")
        fields["credentials"] = fields.pop(LEGACY_BUILTIN)
        return fields

    def legacy(self) -> dict[str, Any] | None:
        """This block as a vault.yaml written before the switches were split held it, or None when that form can't
        say it: phone on, or a pattern turned off."""
        if self.phone or not all(p.enabled for p in self.patterns):
            return None
        patterns = [{"name": p.name, "pattern": p.pattern} for p in self.patterns]
        return {LEGACY_BUILTIN: self.credentials, "patterns": patterns}


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
        return _dump(self.model_dump(mode="json"))

    def written_by_docket(self, text: str) -> bool:
        """Whether a vault.yaml's text is exactly what Docket writes for these settings, now or before the redaction
        switches were split, so Docket may rewrite it without losing anything the user wrote by hand."""
        if text == self.dump():
            return True
        legacy = self.redaction.legacy()
        return legacy is not None and text == _dump({**self.model_dump(mode="json"), "redaction": legacy})


def _dump(data: dict[str, Any]) -> str:
    header = "# The vault's settings. docket reads this on every run; unknown keys are refused.\n"
    return header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False, width=100)


def write_config(home: Home, path: Path, text: str) -> None:
    """Write vault.yaml through a temp file in Docket's private folder under the git dir and a rename, so a crash
    leaves the old file or the new one."""
    tmp = make_private(home.git_dir, home.tmp_dir) / f"vault-yaml.{os.getpid()}.tmp"
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        retry(lambda: tmp.replace(path))
    finally:
        tmp.unlink(missing_ok=True)
