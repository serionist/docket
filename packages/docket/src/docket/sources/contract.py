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
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from docket.vault.records import source_name_problem

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
