"""The Settings page's parts (DR-0505, DR-0601, DR-0510 to DR-0512): what vault.yaml says about the sources, the
never-store lists and redaction, what this machine's models are, and for every change its button, its command or its
line.

`SettingsState` is the URL. `build` and `build_confirm` take the facts the route gathered and do no I/O, so every rule
here is tested on invented settings. The never-store values are in the view only when the URL asks for them
(`show=lists`), so a page read for anything else holds none; a value typed to add travels only in a post's body and
its confirm view, never in a URL (DR-0508). Every line to paste comes from `vault.settings`, quoted as vault.yaml holds
it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from starlette.datastructures import QueryParams

from docket.copied import filled
from docket.errors import DocketError
from docket.index.build import VectorCoverage
from docket.index.query import Reach
from docket.models.report import Report
from docket.vault import inforce, redaction
from docket.vault.config import SourceConfig, VaultConfig
from docket.vault.settings import BUILTIN_RULES, LISTS, RANK, item_line, key_line, pattern_block, source_block

PATH = "/settings"
ADD = "/settings/never-store"
SHOWS = ("lists",)
# What the last post did, by kind and count only: never a value (DR-0512).
DONE = re.compile(
    r"^(?:(?P<listed>listed|unlisted)-(?P<list>people|containers|keywords)-(?P<count>[01])"
    r"|(?P<rule>credentials|phone|pattern)-(?P<state>on|off)"
    r"|(?P<source>[a-z0-9][a-z0-9._-]*)-(?P<act>work|mixed|private|off|on))$"
)
# Past this share of the index's chunks, a keyword's confirm view warns that it takes work too.
BROAD = 0.05

# What each never-store list keeps out (DR-0026), one line each.
MATCHES = {
    "people": "keeps out a record whose author, a recipient, a speaker or its payload names them, an address alone too",
    "containers": "keeps out a record in this chat, channel or thread, named with its source's prefix or without",
    "keywords": "keeps out a record that says it as whole words, in its text, title, names or payload, and any doc",
}
# What each list's add field asks for.
ASKS = {
    "people": "A name, as a mail, a chat or a recording writes it",
    "containers": "A chat, channel or thread, such as chat:family",
    "keywords": "A word or a phrase",
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
    # What the last post did, as DONE has it; never in a link the page makes.
    done: str | None = None

    @staticmethod
    def read(params: QueryParams | Mapping[str, str]) -> SettingsState:
        show = (params.get("show") or "").strip() or None
        if show is not None and show not in SHOWS:
            raise SettingsStateError(f"show is lists, which shows the never-store values, not {show!r}")
        done = (params.get("done") or "").strip() or None
        if done is not None and not DONE.match(done):
            raise SettingsStateError(f"done says what the last change did, such as listed-people-1, not {done!r}")
        return SettingsState(show=show, done=done)

    def url(self, *, done: bool = False) -> str:
        pairs = [("show", self.show), ("done", self.done if done else None)]
        query = "&".join(f"{k}={v}" for k, v in pairs if v)
        return f"{PATH}?{query}" if query else PATH

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
    # vault.yaml holds an edit not committed yet that isn't in force until it is (DR-0521).
    waiting: bool = False
    # The running service's models in its own words, and the end of its worker's stderr.
    service: list[str] = field(default_factory=list[str])
    service_errors: list[str] = field(default_factory=list[str])
    vectors: list[VectorCoverage] = field(default_factory=list[VectorCoverage])
    # vault.yaml's hash as read, which the page's forms carry back (DR-0602).
    shown: str = ""


# -- what the page shows ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    """One change to a source: the line that makes it, where it goes, what it does, and whether it lets more in."""

    what: str
    line: str
    where: str
    effect: str
    looser: bool
    # What a page's button posts for it (DR-0601).
    act: str


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
    ask: str


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
    # A page's button turns it the other way: a built-in rule, or a pattern whose name is its own, but credentials off,
    # which only the owner's console turns (DR-0601).
    button: bool = False


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
    shown_hash: str = ""
    done: str | None = None
    # Said at the top while vault.yaml holds an edit that isn't in force, naming no value.
    waiting: str | None = None


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
            "vault.yaml is as Docket writes it: each change has its button here, committed as made, or its command, "
            "which Claude can run for you; turning credentials redaction off is the one you confirm in your own "
            "terminal. vault.yaml is committed, so a change reaches your other machines with the vault."
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
        waiting=f"{inforce.NOTICE}." if facts.waiting else None,
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
        shown_hash=facts.shown,
        done=done_line(state.done, config),
    )


def done_line(done: str | None, config: VaultConfig) -> str | None:
    """What the last post did, in words: from `done`, which holds a list and a count, a rule turned on, or a source
    and what became of it; never a value or a pattern's name. A rule's and a source's say how things stand, which is
    as true after a post sent twice."""
    found = DONE.match(done or "")
    if found is None:
        return None
    if found.group("rule"):
        what = {"credentials": "Credentials", "phone": "Phone numbers", "pattern": "Your pattern's matches"}
        rule = what[found.group("rule")]
        if found.group("state") == "off":
            return f"{rule} are no longer redacted, from the next sync; markers already stored stay."
        on = redaction.TURNED_ON[:1].upper() + redaction.TURNED_ON[1:]
        return f"{rule} are redacted from now on. {on}."
    if found.group("source"):
        source = found.group("source")
        return {
            "private": f"{source} is private: nothing from it is stored, and what waited from it in staging is gone.",
            "mixed": f"{source} is mixed: each record from it waits on the Staging page for work or private.",
            "work": f"{source} is work: its records are stored without a look.",
            "off": f"{source} is off: its syncs and scans stop, and what waits from it stays.",
            "on": f"{source} is on: its syncs and scans start again.",
        }[found.group("act")]
    name, count = found.group("list"), found.group("count")
    if found.group("listed") == "unlisted":
        if count == "0":
            return f"The {name} list didn't hold that, so nothing changed."
        return f"Took one value off {name}, and committed vault.yaml: from the next sync, what it kept out comes in."
    if count == "0":
        return f"The {name} list held that already, so nothing changed."
    travels = "your own push" if config.storage == "folder" else "docket sync"
    return (
        f"Added one value to {name}, and committed vault.yaml: what waits in staging that it names is dropped, and "
        f"{travels} takes it to your other machines."
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
        if entry.media_folders:
            shown.append(("Media folders", ", ".join(entry.media_folders)))
    changes: list[Change] = []
    if entry.folder is None:
        for level in sorted(RANK, key=lambda v: -RANK[v]):
            if level != entry.sensitivity:
                looser = RANK[level] < RANK[entry.sensitivity]
                effect = SENSITIVITY_EFFECT[(entry.sensitivity, level)]
                changes.append(Change(f"Make it {level}", key_line("sensitivity", level), where, effect, looser, level))
    if entry.enabled:
        changes.append(
            Change(
                "Turn it off",
                key_line("enabled", False),
                where,
                "its syncs and scans stop; what waits stays",
                looser=False,
                act="off",
            )
        )
    else:
        changes.append(
            Change(
                "Turn it on",
                key_line("enabled", True),
                where,
                "lets it in again: its syncs and scans start",
                True,
                "on",
            )
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
    return NeverList(name, len(values), MATCHES[name], item_line(EXAMPLES[name]), listed, ASKS[name])


def _rules(config: VaultConfig, vault: str, docket_form: bool, unfilled: list[tuple[str, str]]) -> list[Rule]:
    settings = config.redaction
    found = [
        (redaction.CREDENTIALS, False, settings.credentials, redaction.BUILTIN[redaction.CREDENTIALS]),
        (redaction.PHONE, False, settings.phone, redaction.BUILTIN[redaction.PHONE]),
    ]
    found += [(p.name, True, p.enabled, p.pattern) for p in settings.patterns]
    names = [p.name for p in settings.patterns]
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
        own = not pattern or (names.count(name) == 1 and name not in BUILTIN_RULES)
        button = own and not (on and name == redaction.CREDENTIALS)
        rules.append(Rule(name, pattern, on, what, command, note, looser=on, line=line, button=button))
    return rules


def _models(facts: Facts) -> ModelsView:
    found = facts.models
    rows: list[tuple[str, str]] = [("Models", found.models)]
    rows += [(f"Environment, {e.device}", f"{e.line} ({e.path})") for e in found.environments]
    if found.on:
        rows += [("Files", found.files or ""), ("Search", found.search or ""), ("Reranker", found.reranker or "")]
        rows += [("Sent to the API", found.sent)] if found.sent is not None else []
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
        Command(
            "Or Voyage AI's API instead: this machine's choice, its key, then the vault's yes",
            "docket models install --api",
        ),
        Command("The key, typed in your own terminal and kept in this machine's keychain", "docket models key voyage"),
        Command("Allow this vault's text to go to Voyage AI", "docket models allow voyage"),
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


# -- the confirm view -------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfirmView:
    """An add's own question (DR-0509): the value, what it drops now and what already stored holds it, what it commits,
    and the form that adds it, or the line to paste when vault.yaml isn't Docket's own."""

    name: str
    value: str
    lines: list[str]
    broad: str | None
    commits: str
    docket_form: bool
    shown: str
    line: str
    stale: bool


def build_confirm(
    name: str,
    value: str,
    *,
    staged: int,
    stored: Reach | None,
    why: str | None,
    shown: str,
    docket_form: bool,
    stale: bool = False,
    takes_in: bool = False,
) -> ConfirmView:
    """The confirm view for a value about to go on a list: the counts in words, the share of the index a keyword takes,
    and that the value is committed as typed."""
    lines = [
        f"It drops {_n(staged, 'record')} waiting in staging now."
        if staged
        else "Nothing waiting in staging names it now."
    ]
    broad = None
    if stored is None:
        lines.append(f"What is already stored can't be counted: {why}.")
    elif stored.chunks or stored.records:
        share = stored.chunks / stored.total if stored.total else 0.0
        lines.append(
            f"Already stored, at least {_n(stored.records, 'record')} and {stored.chunks:,} of {stored.total:,} chunks "
            f"({share:.1%}) hold it. They stay in raw: forgetting them comes later, with docket forget."
        )
        if share >= BROAD:
            broad = (
                f"That is {share:.0%} of everything indexed: a value this common keeps out work as well. Check it is "
                "the one you mean."
            )
    else:
        lines.append(f"Nothing already stored holds it, of {stored.total:,} chunks.")
    if name == "keywords":
        lines.append("Docs that say it stay as they are; a later doc that says it is refused.")
    lines.append("From the next sync, a record it names is dropped before it is stored, with no trace.")
    if takes_in:
        lines.append("Your edit of vault.yaml that isn't committed yet goes in first, as a commit of its own.")
    commits = (
        "It is committed in vault.yaml as you typed it: it is in your repo's history, and goes wherever the vault is "
        "pushed. The commit message names the list, never the value."
    )
    return ConfirmView(
        name=name,
        value=value,
        lines=lines,
        broad=broad,
        commits=commits,
        docket_form=docket_form,
        shown=shown,
        line=item_line(value),
        stale=stale,
    )


@dataclass(frozen=True)
class SourceConfirm:
    """Making a source private, asked once more (DR-0509): what it drops now, what it stops, and the form that makes
    it, or the line to change when vault.yaml isn't Docket's own."""

    source: str
    lines: list[str]
    docket_form: bool
    shown: str
    line: str
    stale: bool


def build_source_confirm(
    source: str, *, staged: int, shown: str, docket_form: bool, stale: bool = False, takes_in: bool = False
) -> SourceConfirm:
    lines = [
        f"It drops {_n(staged, 'record')} of {source} waiting in staging now."
        if staged
        else f"Nothing of {source} waits in staging now."
    ]
    lines.append(f"From the next sync, nothing from {source} is stored; its records already stored stay in raw.")
    lines.append("Making it mixed or work again is a button on the Settings page: it lets more in.")
    if takes_in:
        lines.append("Your edit of vault.yaml that isn't committed yet goes in first, as a commit of its own.")
    return SourceConfirm(
        source=source,
        lines=lines,
        docket_form=docket_form,
        shown=shown,
        line=key_line("sensitivity", "private"),
        stale=stale,
    )
