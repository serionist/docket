"""The Settings page's parts (DR-0505, DR-0506, DR-0510 to DR-0512): what vault.yaml says about the sources, the
never-store lists and redaction, what this machine's models are, and for every change its command or its line.

`SettingsState` is the URL. `build` takes the facts the route gathered and does no I/O, so every rule here is tested on
invented settings. The never-store values are in the view only when the URL asks for them (`show=lists`), so a page
read for anything else holds none. Every line to paste comes from `vault.settings`, quoted as vault.yaml holds it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from starlette.datastructures import QueryParams

from docket.errors import DocketError
from docket.index.build import VectorCoverage
from docket.models.report import Report
from docket.vault import redaction
from docket.vault.config import SourceConfig, VaultConfig
from docket.vault.settings import LISTS, RANK, item_line, key_line, pattern_block, source_block
from docket.web.commands import filled

PATH = "/settings"
SHOWS = ("lists",)

# What each never-store list keeps out (DR-0026), one line each.
MATCHES = {
    "people": "keeps out a record whose author, a recipient, a speaker or its payload names them, an address alone too",
    "containers": "keeps out a record in this chat, channel or thread, named with its source's prefix or without",
    "keywords": "keeps out a record that says it as whole words, in its text, title, names or payload, and any doc",
}
# Invented values for the line each list's add shows.
EXAMPLES = {"people": "Dora Example", "containers": "chat:family", "keywords": "Project Phoenix"}
PATTERN_EXAMPLE = ("ticket-id", r"TCK-\d{6}")
# What a change of sensitivity does, from one to the other.
SENSITIVITY_EFFECT = {
    ("work", "mixed"): "every record waits on the Staging page for work or private; ready ones go back to pending",
    ("work", "private"): "nothing from it is stored; what waits from it in staging is dropped",
    ("mixed", "private"): "nothing from it is stored; what waits from it in staging is dropped",
    ("mixed", "work"): "lets more in: its records are stored without a look",
    ("private", "mixed"): "lets it in: its records are kept, each waiting on the Staging page first",
    ("private", "work"): "lets it in: every record is kept without a look",
}
TURNED_OFF = "lets in what it redacts, as written, from the next sync; markers already stored stay"


class SettingsStateError(DocketError):
    """A parameter of the Settings page's URL that can't be read; the message names it."""


@dataclass(frozen=True)
class SettingsState:
    """The Settings page's URL (DR-0512): `show=lists` puts the never-store values in the page."""

    show: str | None = None

    @staticmethod
    def read(params: QueryParams | Mapping[str, str]) -> SettingsState:
        show = (params.get("show") or "").strip() or None
        if show is not None and show not in SHOWS:
            raise SettingsStateError(f"show is lists, which shows the never-store values, not {show!r}")
        return SettingsState(show=show)

    def url(self) -> str:
        return f"{PATH}?show={self.show}" if self.show else PATH

    @property
    def values(self) -> bool:
        return self.show == "lists"


# -- the facts --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceFacts:
    name: str
    entry: SourceConfig
    # Why the runner wouldn't take its mapper, in find_mapper's words; None when it would, or for a folder source.
    mapper_problem: str | None = None
    # This machine's own folder for a folder source, from the user's config (DR-0490).
    here: str | None = None


@dataclass(frozen=True)
class Facts:
    root: Path
    machine: str
    config: VaultConfig
    # vault.yaml's text is what Docket writes for it, so Docket's commands change it rather than print the line.
    docket_form: bool
    sources: list[SourceFacts]
    models: Report
    config_path: Path
    folders_problem: str | None = None
    # The running service's models in its own words, and the end of its worker's stderr.
    service: list[str] = field(default_factory=list[str])
    service_errors: list[str] = field(default_factory=list[str])
    vectors: list[VectorCoverage] = field(default_factory=list[VectorCoverage])


# -- what the page shows ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    """One change to a source: the line that makes it, where it goes, what it does, and whether it lets more in."""

    what: str
    line: str
    where: str
    effect: str
    looser: bool


@dataclass(frozen=True)
class SourceView:
    name: str
    folder: bool
    sensitivity: str
    enabled: bool
    facts: list[tuple[str, str]]
    problem: str | None
    changes: list[Change]
    entry: str


@dataclass(frozen=True)
class Listed:
    value: str
    line: str


@dataclass(frozen=True)
class NeverList:
    name: str
    count: int
    matches: str
    example: str
    values: list[Listed]


@dataclass(frozen=True)
class Rule:
    """A redaction rule or one of the user's patterns, and the command that turns it the other way."""

    name: str
    pattern: bool
    on: bool
    what: str
    command: str
    note: str
    looser: bool
    # The vault.yaml line the command would print, for a file Docket won't rewrite.
    line: str | None


@dataclass(frozen=True)
class Command:
    what: str
    text: str


@dataclass(frozen=True)
class ModelsView:
    on: bool
    rows: list[tuple[str, str]]
    errors: list[str]
    commands: list[Command]
    config_path: str
    off_line: str


@dataclass(frozen=True)
class SettingsView:
    lead: str
    form: str
    docket_form: bool
    machine: str
    primary: str
    sources: list[SourceView]
    lists: list[NeverList]
    shown: bool
    rules: list[Rule]
    credentials_off: str | None
    names: str
    pattern_example: str
    models: ModelsView
    folders_problem: str | None
    unfilled: list[tuple[str, str]]


def build(facts: Facts, state: SettingsState) -> SettingsView:
    config = facts.config
    unfilled: list[tuple[str, str]] = []
    vault = filled(str(facts.root), "vault", unfilled)
    sources = [_source(s) for s in facts.sources]
    lists = [_list(name, getattr(config.never_store, name), state.values) for name in LISTS]
    rules = _rules(config, vault, facts.docket_form, unfilled)
    models = _models(facts)
    on = sum(1 for s in facts.sources if s.entry.enabled)
    values = sum(n.count for n in lists)
    lead = (
        f"{_n(len(sources), 'source')}, {on} on; {_n(values, 'never-store value')}; credentials "
        f"{'redacted' if config.redaction.credentials else 'not redacted'}, phone numbers "
        f"{'redacted' if config.redaction.phone else 'not'}; models {'on' if models.on else 'off'} on {facts.machine}."
    )
    if facts.docket_form:
        form = (
            "vault.yaml is as Docket writes it: each change below is a command to run, or a line to change by hand. "
            "vault.yaml is committed, so a change reaches your other machines with the vault."
        )
    else:
        form = (
            "vault.yaml has comments or a layout of your own, so it is never rewritten: each change below is the line "
            "to change by hand, and Docket's commands print it too."
        )
    return SettingsView(
        lead=lead,
        form=form,
        docket_form=facts.docket_form,
        machine=facts.machine,
        primary=config.primary_machine,
        sources=sources,
        lists=lists,
        shown=state.values,
        rules=rules,
        credentials_off=None if config.redaction.credentials else redaction.CREDENTIALS_OFF,
        names=redaction.NAMES[:1].upper() + redaction.NAMES[1:],
        pattern_example=pattern_block(*PATTERN_EXAMPLE),
        models=models,
        folders_problem=facts.folders_problem,
        unfilled=sorted(set(unfilled)),
    )


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _source(facts: SourceFacts) -> SourceView:
    entry, name = facts.entry, facts.name
    where = f"sources.{name}"
    shown: list[tuple[str, str]] = []
    if entry.folder is not None:
        shown.append(("Folder", entry.folder))
        if facts.here is not None:
            shown.append(("Here", facts.here))
        shown.append(("Exclude", ", ".join(entry.exclude) or "nothing"))
    else:
        shown.append(("Tools", ", ".join(entry.tools) or "none: nothing syncs live"))
        shown.append(("Mapper", entry.mapper or "none"))
        if entry.media_hosts:
            shown.append(("Media hosts", ", ".join(entry.media_hosts)))
    changes: list[Change] = []
    if entry.folder is None:
        for level in sorted(RANK, key=lambda v: -RANK[v]):
            if level != entry.sensitivity:
                looser = RANK[level] < RANK[entry.sensitivity]
                effect = SENSITIVITY_EFFECT[(entry.sensitivity, level)]
                changes.append(Change(f"Make it {level}", key_line("sensitivity", level), where, effect, looser))
    if entry.enabled:
        changes.append(
            Change(
                "Turn it off", key_line("enabled", False), where, "its syncs and scans stop; what waits stays", False
            )
        )
    else:
        changes.append(
            Change("Turn it on", key_line("enabled", True), where, "lets it in again: its syncs and scans start", True)
        )
    changes.sort(key=lambda c: c.looser)
    return SourceView(
        name=name,
        folder=entry.folder is not None,
        sensitivity=entry.sensitivity,
        enabled=entry.enabled,
        facts=shown,
        problem=facts.mapper_problem,
        changes=changes,
        entry=source_block(name, entry),
    )


def _list(name: str, values: list[str], shown: bool) -> NeverList:
    listed = [Listed(v, item_line(v)) for v in values] if shown else []
    return NeverList(name, len(values), MATCHES[name], item_line(EXAMPLES[name]), listed)


def _rules(config: VaultConfig, vault: str, docket_form: bool, unfilled: list[tuple[str, str]]) -> list[Rule]:
    settings = config.redaction
    found = [
        (redaction.CREDENTIALS, False, settings.credentials, redaction.BUILTIN[redaction.CREDENTIALS]),
        (redaction.PHONE, False, settings.phone, redaction.BUILTIN[redaction.PHONE]),
    ]
    found += [(p.name, True, p.enabled, p.pattern) for p in settings.patterns]
    rules: list[Rule] = []
    for name, pattern, on, what in found:
        rule = filled(name, "pattern", unfilled) if pattern else name
        turn = "off" if on else "on"
        if on:
            note = redaction.CREDENTIALS_OFF if name == redaction.CREDENTIALS else TURNED_OFF
        else:
            note = redaction.TURNED_ON
        line = None if docket_form else redaction.by_hand(name, not on, pattern)
        named = f'"{rule}"' if pattern else rule
        command = f'docket redaction set {named} {turn} --vault "{vault}"'
        rules.append(Rule(name, pattern, on, what, command, note, looser=on, line=line))
    return rules


def _models(facts: Facts) -> ModelsView:
    found = facts.models
    rows: list[tuple[str, str]] = [("Models", found.models)]
    rows += [(f"Environment, {e.device}", f"{e.line} ({e.path})") for e in found.environments]
    if found.on:
        rows += [("Files", found.files or ""), ("Search", found.search or ""), ("Reranker", found.reranker or "")]
        rows += [("Vectors", f"{v.model}: {v.have:,} of {v.total:,} chunks") for v in facts.vectors]
        if not facts.vectors:
            rows.append(("Vectors", "none yet: the service embeds after its next index update"))
    rows += [("The service", line) for line in facts.service]
    commands = [
        Command("Install them, or again after an upgrade; it picks the GPU when there is one", "docket models install"),
        Command("On the CPU only", "docket models install --device cpu"),
        Command("Without the reranker", "docket models install --no-reranker"),
        Command("Then restart the service, which reads its models when it starts", "docket service install"),
        Command("Remove them; keyword search goes on", "docket models remove"),
        Command("First, where the service's worker holds the files", "docket service stop"),
    ]
    return ModelsView(
        on=found.on,
        rows=rows,
        errors=facts.service_errors,
        commands=commands,
        config_path=str(facts.config_path),
        off_line=key_line("provider", "none"),
    )
