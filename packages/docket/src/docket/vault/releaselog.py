"""The release log (DR-0200): which held ids the owner let in, and with which cuts.

`state/releases/<source>.jsonl` is committed, one JSON line per release: the native id; the cuts, in milliseconds on
the recording's timeline, each widened to a fixed point over its utterances as the owner saw them go; the records
without segments the owner kept, each at the version they read; when, and on which machine. Ids and times only, never
content. Two machines can append at once, so the log merges by union (`*.jsonl merge=union`), and several lines for
one id give the union of their cuts and keep only what every line keeps: a merge can only take more out.

It fails closed like a hold list: a line that isn't a release, a log or folder that is a link, or a file whose name
isn't a source's stops every ingest and every gate call, since skipping it could let a stretch in uncut.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from docket.errors import DocketError
from docket.vault.cuts import Cut, merge
from docket.vault.records import RawRecord, is_native_id, owning_id, source_name_problem
from docket.vault.staging import write_replacing
from docket.vault.vault import Vault

RELEASES = "state/releases"
SUFFIX = ".jsonl"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LoggedCut(_Model):
    start: int = Field(ge=0)
    end: int

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end <= self.start:
            raise ValueError("a cut ends after it starts")
        return self


class Kept(_Model):
    rid: str
    v: str = Field(min_length=1)


class Release(_Model):
    """One release, as one line of the log."""

    schema_version: Literal[1] = 1
    id: str
    cuts: list[LoggedCut] = Field(min_length=1)
    keep: list[Kept] = Field(default_factory=list[Kept])
    at: datetime
    machine: str = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not is_native_id(value):
            raise ValueError("not a native id")
        return value

    @field_validator("at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("a release's time needs its time zone")
        return value.astimezone(UTC)

    def line(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class Granted:
    """An id's releases folded into one: the union of their cuts, and the records every one of them keeps."""

    id: str
    cuts: tuple[Cut, ...]
    keep: frozenset[tuple[str, str]]
    lines: tuple[Release, ...]

    def keeps(self, record: RawRecord) -> bool:
        return (record.rid, record.v) in self.keep

    @property
    def first(self) -> Release:
        return min(self.lines, key=lambda r: r.at)


def fold(lines: list[Release]) -> dict[str, Granted]:
    by_id: dict[str, list[Release]] = {}
    for release in lines:
        by_id.setdefault(release.id, []).append(release)
    granted: dict[str, Granted] = {}
    for native, group in by_id.items():
        cuts = merge([Cut(start=c.start, end=c.end, unit="ms") for r in group for c in r.cuts])
        kept = [frozenset((k.rid, k.v) for k in r.keep) for r in group]
        granted[native] = Granted(native, tuple(cuts), kept[0].intersection(*kept[1:]), tuple(group))
    return granted


@dataclass(frozen=True)
class Releases:
    """Every source's releases, as read on one call."""

    ids: dict[str, dict[str, Granted]] = field(default_factory=dict[str, dict[str, Granted]])

    def of(self, source: str) -> dict[str, Granted]:
        return self.ids.get(source, {})

    def covering(self, record: RawRecord) -> Granted | None:
        """The release a record falls under, by its rid, thread or container, as a hold covers it."""
        granted = self.of(record.source)
        ids = frozenset(granted)
        for ref in (record.rid, record.thread, record.container):
            found = owning_id(ref, record.source, ids)
            if found is not None:
                return granted[found]
        return None


def _linked(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _folder(root: Path) -> Path:
    folder = root / RELEASES
    if _linked(folder):
        raise DocketError(f"{RELEASES} is a link; release logs are read and written only as plain files in the vault")
    return folder


def log_path(root: Path, source: str) -> Path:
    """A source's log, from a checked name, never through a link."""
    problem = source_name_problem(source)
    if problem is not None:
        raise DocketError(problem)
    path = _folder(root) / f"{source}{SUFFIX}"
    if _linked(path):
        raise DocketError(f"{RELEASES}/{source}{SUFFIX} is a link; a release log is a plain file in the vault")
    return path


def parse(text: str, where: str) -> list[Release]:
    """The releases in a log's text. A bad line is named by its number only, like a hold list's."""
    out: list[Release] = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            out.append(Release.model_validate_json(line))
        except ValidationError:
            problem = f"{where}: line {n} isn't a release; fix or delete it (a release log fails closed)"
            raise DocketError(problem) from None
    return out


def _text(path: Path, where: str) -> str:
    try:
        return path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DocketError(f"{where} isn't UTF-8") from None
    except OSError as e:
        raise DocketError(f"{where}: can't read it: {e.strerror or type(e).__name__}") from None


def read(root: Path, source: str) -> dict[str, Granted]:
    path = log_path(root, source)
    if not path.exists():
        return {}
    where = f"{RELEASES}/{source}{SUFFIX}"
    return fold(parse(_text(path, where), where))


def read_all(root: Path) -> Releases:
    """Every source's log. A `.jsonl` there whose name isn't a source's fails closed like a bad line."""
    folder = _folder(root)
    if not folder.is_dir():
        return Releases()
    ids: dict[str, dict[str, Granted]] = {}
    for path in sorted(folder.iterdir()):
        if path.suffix != SUFFIX or path.is_dir():
            continue
        problem = source_name_problem(path.stem)
        if problem is not None:
            raise DocketError(f"{RELEASES}/{path.name}: {problem}; rename or delete it (a release log fails closed)")
        ids[path.stem] = read(root, path.stem)
    return Releases(ids)


def add(vault: Vault, source: str, release: Release) -> None:
    """Append one release and commit the log alone, naming no id. A log that doesn't read is never added to."""
    with vault.writing():
        path = log_path(vault.root, source)
        where = f"{RELEASES}/{source}{SUFFIX}"
        text = _text(path, where) if path.exists() else ""
        parse(text, where)
        if text and not text.endswith("\n"):
            text += "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_replacing(vault.home.tmp_dir, path, f"{text}{release.line()}\n", f"releases-{source}")
        commit_pending(vault, source, f"Releases: {source}, 1 added on {vault.machine}")


def commit_pending(vault: Vault, source: str, message: str | None = None) -> str | None:
    """Commit the log when it differs from HEAD: a release whose commit failed (another git held the index) is
    committed by the next call, or it never reaches another machine. A log an ignore rule hides counts too, so its
    add fails loudly before any hold comes off, instead of the line staying out of git unseen."""
    rel = f"{RELEASES}/{source}{SUFFIX}"
    if not (vault.root / rel).exists():
        return None
    git = vault.git
    if not (git.untracked(rel) or git.untracked(rel, ignored=True) or git.changed_against_head(rel)):
        return None
    return vault.commit([rel], message or f"Releases: {source}, the log committed on {vault.machine}")
