"""The mapper interface, contract 1: what a source's mapper gets and what it gives back.

A mapper is `map_run(run) -> dict` in the consuming project's `.claude/skills/sources/<source>/mapper.py`. Both sides
are plain JSON, so a mapper needs nothing but Python's standard library and never imports Docket.

In: `contract`, `source`, the run's `manifest`, its `captures` (each a tool's result as given, or a file from an import
folder) and the source's `holds`. Out: `contract`, `records` in the core's shape, `set_aside`, `waiting` and `held`.
A later contract changes the number, so a mapper written for another one fails loudly instead of mapping wrong.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from docket.errors import DocketError
from docket.vault.records import Attachment, RawRecord, Segment, is_native_id, source_name_problem
from docket.vault.segments import normalize, render

CONTRACT = 1
RUN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("a time needs its time zone")
    return value.astimezone(UTC) if value is not None else None


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Capture(_Model):
    """One thing a run brought in: a tool's result as the tool gave it, or a file from an import folder."""

    seq: int = Field(ge=0)
    run: str
    captured: datetime
    # The tool's own name, without the host's prefix; None for a file.
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict[str, Any])
    result: Any = None
    # A file's name in its import folder; None for a tool's result.
    file: str | None = None

    _captured = field_validator("captured")(_aware)


class Window(_Model):
    since: datetime | None = None
    until: datetime | None = None

    _times = field_validator("since", "until")(_aware)


class Skipped(_Model):
    id: str | None = None
    reason: str


class Manifest(_Model):
    """What the skill says about its run: the run's id, how many captures it made, and what it skipped."""

    run: str
    source: str
    captures: int = Field(ge=0)
    # Don't wait for anything more: map what is there.
    final: bool = False
    window: Window | None = None
    skipped: list[Skipped] = Field(default_factory=list[Skipped])
    # A folder of payloads fetched earlier, read as more captures of the run.
    folder: str | None = None

    @field_validator("run")
    @classmethod
    def _run(cls, value: str) -> str:
        if not RUN.match(value):
            raise ValueError("a run's id is letters, digits, _, ., : and -, at most 64")
        return value

    @field_validator("source")
    @classmethod
    def _source(cls, value: str) -> str:
        problem = source_name_problem(value)
        if problem is not None:
            raise ValueError(problem)
        return value


def mapper_input(source: str, manifest: Manifest, captures: Iterable[Capture], holds: Iterable[str]) -> dict[str, Any]:
    """The run as the mapper reads it."""
    return {
        "contract": CONTRACT,
        "source": source,
        "manifest": manifest.model_dump(mode="json"),
        "captures": [c.model_dump(mode="json") for c in captures],
        "holds": sorted(holds),
    }


# -- what comes back -------------------------------------------------------------------------------------------------

TOKEN = re.compile(r"^[A-Za-z0-9_.\[\]-]{1,64}$")
OUTPUT_KEYS = frozenset({"contract", "records", "set_aside", "waiting", "held"})
# The names a shape error may show: the record's own fields. Anything else in an error's location came from the
# mapper's output, and could be anything.
_FIELDS = frozenset(RawRecord.model_fields) | frozenset(Segment.model_fields) | frozenset(Attachment.model_fields)


class MapperRefusedError(DocketError):
    """The core refuses what a mapper returned. The whole run fails, so the mark stays and a fixed mapper sees the same
    captures again. The message names a record by its position and field, never by a value from it."""


@dataclass(frozen=True)
class SetAside:
    id: str | None
    reason: str
    tool: str | None = None
    key: str | None = None


@dataclass(frozen=True)
class Waiting:
    id: str
    for_: str


@dataclass(frozen=True)
class Mapped:
    records: list[RawRecord]
    set_aside: list[SetAside]
    waiting: list[Waiting]
    held: list[str]


def check_output(source: str, data: Any) -> Mapped:
    """What a mapper returned, checked against the contract, its transcripts put in the core's order and text."""
    if not isinstance(data, dict):
        raise MapperRefusedError("the mapper's output isn't an object")
    out = cast(dict[str, Any], data)
    if set(out) - OUTPUT_KEYS:
        raise MapperRefusedError(f"the mapper's output has a key contract {CONTRACT} doesn't know")
    if out.get("contract") != CONTRACT:
        raise MapperRefusedError(f"the mapper answers another contract than {CONTRACT}")
    items = out.get("records")
    if not isinstance(items, list):
        raise MapperRefusedError("the mapper's output has no records list")
    records: list[RawRecord] = []
    seen: set[str] = set()
    for n, item in enumerate(cast(list[Any], items), start=1):
        record = _record(source, n, item)
        if record.rid in seen:
            raise MapperRefusedError(f"record {n}: its rid came earlier in the run's output; a rid comes once")
        seen.add(record.rid)
        records.append(record)
    return Mapped(
        records=records,
        set_aside=[_set_aside(n, e) for n, e in enumerate(_listed(out, "set_aside"), start=1)],
        waiting=[_waiting(n, e) for n, e in enumerate(_listed(out, "waiting"), start=1)],
        held=[_held(n, e) for n, e in enumerate(_listed(out, "held"), start=1)],
    )


def _ref(value: Any, source: str) -> bool:
    prefix = f"{source}:"
    return isinstance(value, str) and value.startswith(prefix) and is_native_id(value[len(prefix) :])


def _record(source: str, n: int, item: Any) -> RawRecord:
    where = f"record {n}"
    if not isinstance(item, dict):
        raise MapperRefusedError(f"{where} isn't an object")
    data = cast(dict[str, Any], item)
    if data.get("v") or data.get("run"):
        raise MapperRefusedError(f"{where}: the mapper set v or run; the core computes the version and names the run")
    if data.get("fidelity") == "cut":
        raise MapperRefusedError(f"{where}: the mapper set fidelity cut; only the core's cutter marks a record cut")
    if data.get("source") != source:
        raise MapperRefusedError(f"{where}: its source isn't the run's source, {source}")
    if not _ref(data.get("rid"), source):
        raise MapperRefusedError(f"{where}: its rid isn't {source}:<native id>")
    for name in ("container", "thread"):
        if data.get(name) is not None and not _ref(data.get(name), source):
            raise MapperRefusedError(f"{where}: its {name} isn't a rid of {source}")
    if data.get("segments") and data.get("text"):
        raise MapperRefusedError(f"{where}: a record with segments leaves text empty; the core renders it")
    try:
        record = RawRecord.model_validate(data)
    except ValidationError as e:
        # Pydantic's message and input quote the value, which can be a title: only the location and the type go out.
        first = e.errors(include_url=False, include_input=False, include_context=False)[0]
        field = ".".join(str(p) if isinstance(p, int) or p in _FIELDS else "?" for p in first["loc"]) or "it"
        raise MapperRefusedError(f"{where}: {field} isn't valid ({first['type']})") from None
    if record.segments:
        ordered = normalize(record.segments)
        record = record.model_copy(update={"segments": ordered, "text": render(ordered)})
    return record


def _listed(out: dict[str, Any], key: str) -> list[Any]:
    value = out.get(key, [])
    if not isinstance(value, list):
        raise MapperRefusedError(f"the mapper's {key} isn't a list")
    return cast(list[Any], value)


def _token(value: Any, *, optional: bool) -> bool:
    return (optional and value is None) or (isinstance(value, str) and bool(TOKEN.match(value)))


def _native(value: Any, *, optional: bool) -> bool:
    return (optional and value is None) or (isinstance(value, str) and is_native_id(value))


def _set_aside(n: int, entry: Any) -> SetAside:
    e = cast(dict[str, Any], entry) if isinstance(entry, dict) else {}
    if (
        not e
        or set(e) - {"id", "reason", "tool", "key"}
        or not _native(e.get("id"), optional=True)
        or not _token(e.get("reason"), optional=False)
        or not _token(e.get("tool"), optional=True)
        or not _token(e.get("key"), optional=True)
    ):
        raise MapperRefusedError(f"set_aside {n} isn't {{id, reason, tool, key}} as an id and short tokens")
    return SetAside(e.get("id"), e["reason"], e.get("tool"), e.get("key"))


def _waiting(n: int, entry: Any) -> Waiting:
    e = cast(dict[str, Any], entry) if isinstance(entry, dict) else {}
    if set(e) != {"id", "for"} or not _native(e["id"], optional=False) or not _token(e["for"], optional=False):
        raise MapperRefusedError(f"waiting {n} isn't {{id, for}} as an id and a short token")
    return Waiting(e["id"], e["for"])


def _held(n: int, entry: Any) -> str:
    if not _native(entry, optional=False):
        raise MapperRefusedError(f"held {n} isn't an id")
    return cast(str, entry)
