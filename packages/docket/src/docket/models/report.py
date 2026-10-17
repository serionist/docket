"""What this machine's models are, as data: `docket models status` prints it and the Settings page shows it (DR-0510).

Read from the user's config and the models' folders only: nothing here starts a worker, times a reranker or reads the
index, so the page can show it on every load.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from docket.models import config, install, runtime
from docket.models.config import ApiChoice, Choice, Device, RerankTiming
from docket.models.provider import Off
from docket.models.voyage import Usage


@dataclass(frozen=True)
class Environment:
    """A device's environment that exists here, and what its marker says: ready with its batch budget, or why not."""

    device: Device
    line: str
    path: Path


@dataclass(frozen=True)
class Report:
    """The choice, and each line `docket models status` prints about it, without the labels."""

    choice: Choice | ApiChoice | Off
    models: str
    environments: list[Environment] = field(default_factory=list[Environment])
    # None while models are off: there are no files, search or reranker to speak of.
    files: str | None = None
    search: str | None = None
    reranker: str | None = None
    # The api provider's tokens this month on this machine, of its cap (DR-0539).
    sent: str | None = None

    @property
    def on(self) -> bool:
        return not isinstance(self.choice, Off)


def read() -> Report:
    choice = config.read()
    if isinstance(choice, Off):
        models = f"off{f': {choice.why}' if choice.why else ' (docket models install turns them on)'}"
    elif isinstance(choice, ApiChoice):
        models = f"{choice.embedder.name} through Voyage AI's API, {choice.embedder.dims} dimensions"
    else:
        models = f"{choice.embedder.name} on {choice.device}"
    environments: list[Environment] = []
    for device in config.DEVICES:
        if config.env_dir(device).exists():
            found = config.installed(device)
            line = found if isinstance(found, str) else f"ready, batches of {found.budget:,} characters"
            environments.append(Environment(device, line, config.env_dir(device)))
    if isinstance(choice, Off):
        return Report(choice, models, environments)
    problem = install.vectors_problem()
    if isinstance(choice, ApiChoice):
        reranker = choice.reranker
        return Report(
            choice,
            models,
            environments,
            files="none here: the api provider runs at Voyage AI",
            search="! " + problem if problem else "uses the vectors",
            reranker=f"{reranker.name} through Voyage AI's API, {reranker.pairs} pairs"
            if reranker is not None
            else choice.reranker_why or "none",
            sent=f"{Usage().spent():,} of {choice.monthly_tokens:,} tokens this month",
        )
    missing = config.missing_files(choice.embedder)
    return Report(
        choice,
        models,
        environments,
        files="missing: " + ", ".join(f.path for f in missing) if missing else "all here",
        search="! " + problem if problem else "uses the vectors",
        reranker=reranker_line(choice),
    )


def lines(report: Report) -> list[str]:
    """The report as `docket models status` prints it."""
    out = [f"models     {report.models}"]
    out += [f"env {e.device:<6} {e.line} ({e.path})" for e in report.environments]
    if report.on:
        out += [f"files      {report.files}", f"search     {report.search}", f"reranker   {report.reranker}"]
    if report.sent is not None:
        out.append(f"sent       {report.sent}")
    return out


def reranker_line(choice: Choice) -> str:
    """The reranker as the install left it: its timing, or why it is off here (DR-0455)."""
    reranker = choice.reranker
    if reranker is None:
        return choice.reranker_why or "none (docket models install adds one)"
    found = config.installed(choice.device)
    if isinstance(found, str):
        return found
    timing = runtime.timed(choice, found)
    if isinstance(timing, RerankTiming):
        took = f"{timing.pairs} pairs in {timing.seconds or 0:.2f} s"
        return f"{reranker.name} ({reranker.licence}) on {reranker.device}: {took}"
    return str(timing)


def service_lines(service: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """The running service's models, from its status in its own words: a line for the embedder and one for the
    reranker, and the end of what its worker wrote to stderr."""
    why = service.get("why")
    state = f"{service.get('model')}, {service.get('state')}" if service.get("model") else "off"
    said = [f"{state}{f': {why}' if why else ''}"]
    if service.get("reranker"):
        reranks = f"{service.get('reranked', 0)} reranked, {service.get('past_budget', 0)} past the budget"
        off = service.get("rerank_why")
        said.append(f"{service.get('reranker')}: {f'off: {off}' if off else reranks}")
    errors = service.get("errors")
    return said, [str(line) for line in cast(list[object], errors)] if isinstance(errors, list) else []
