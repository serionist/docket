"""vault.yaml's one writer, the short list of changes a page may make, and the lines to paste for every other change.

`rewrite` is the general entry: under the vault lock it reads vault.yaml, asks for the change, and rewrites and commits
the file only when its text is what Docket writes for it, so no comment or layout of the user's is lost (DR-0166,
DR-0193, DR-0507). `docket redaction set` and `docket source new` write through it, and so does every page.

A page's write is held to `loosens` (from `inforce`, the settings in force's one rule): pages make only the changes
that keep more out, and every other vault.yaml change is named with its command or its line, never made (DR-0506).
What a page may import from here is `FOR_PAGES`; a test holds the web and the service to it.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import yaml
from pydantic import ValidationError

from docket.errors import DocketError
from docket.vault import inforce, neverstore
from docket.vault.config import CONFIG_FILE, SourceConfig, UniqueKeyLoader, VaultConfig, write_config
from docket.vault.neverstore import ListName
from docket.vault.vault import Vault

Via = Literal["cli", "page"]
# The rank, the lists and the door are the settings in force's one rule; pages read them from here.
RANK = inforce.RANK
LISTS = inforce.LISTS
loosens = inforce.loosens
# The built-in redaction switches, by their keys in vault.yaml's redaction block.
BUILTIN_RULES = ("credentials", "phone")
# The names docket.web and docket.service may import from here: the read-only helpers and the stricter operations,
# with what they raise. Never `rewrite`, which makes any change (DR-0506).
FOR_PAGES = frozenset(
    {
        "RANK",
        "LISTS",
        "loosens",
        "key_line",
        "item_line",
        "source_block",
        "pattern_block",
        "file_hash",
        "checked_value",
        "add_listed",
        "Added",
        "StaleError",
        "edit_to_take_in",
        "rule_on",
        "stricter",
        "turn_off",
        "BUILTIN_RULES",
        "LooseningError",
        "Rewritten",
    }
)
# A line to paste never wraps: a long keyword stays one line.
_WIDTH = 4096
# A never-store value is a name, a chat or a phrase: this is long enough for any, and short enough for one line.
MAX_VALUE = 200
TAKEN_IN = "Config: vault.yaml as edited"


class LooseningError(DocketError):
    """A page asked for a change outside the list pages may make."""


class StaleError(DocketError):
    """vault.yaml changed since the page read it, so the change the page asked for is not made."""


class UncommittedLooseningError(DocketError):
    """vault.yaml holds an edit not committed yet that lets more in than the committed file, which no write of Docket's
    takes in: committing it would carry a loosening to every clone (DR-0506, DR-0507)."""


UNCOMMITTED_LOOSENING = (
    "vault.yaml has an edit that isn't committed and lets more in; commit it yourself or undo it, then try again"
)
UNREADABLE_HEAD = (
    "vault.yaml isn't in the last commit as a file Docket can read, so an edit of it can't be weighed; commit it "
    "yourself, then try again"
)


@dataclass(frozen=True)
class Rewritten:
    """What `rewrite` did: vault.yaml rewritten and committed, nothing to do, or nothing done because the file isn't in
    Docket's form, so the change is the user's to make by hand. `taken_in` is the commit of an edit the file held that
    wasn't committed yet, made on its own first."""

    changed: bool = False
    commit: str | None = None
    by_hand: bool = False
    taken_in: str | None = None


def file_hash(data: bytes) -> str:
    """vault.yaml as a page read it, which its form carries back, so a change made since is seen (DR-0507)."""
    return hashlib.sha256(data).hexdigest()


def rewrite(
    vault: Vault, edit: Callable[[VaultConfig], VaultConfig], message: str, *, via: Via, shown: str | None = None
) -> Rewritten:
    """Change vault.yaml under the vault lock: `edit` gets the settings as they stand and gives the new ones. Nothing to
    change is no write at all, whatever the file did since. Otherwise a page's change is refused when the file isn't
    the one it showed (`shown`, its hash) and held to `loosens`. A change is written only when the file is exactly what
    Docket writes for it, now or in the form before the redaction switches were split; an edit it holds that isn't
    committed yet is committed on its own first, so each commit says what it did, and refused, with nothing written,
    when that edit lets more in (`edit_to_take_in`). The change is read back through the schema, written through a temp
    file and a rename, and committed alone; a failure puts the file back."""
    path = vault.root / CONFIG_FILE
    with vault.writing():
        data = path.read_bytes()
        text = data.decode("utf-8")
        before = VaultConfig.load(path)
        after = edit(before)
        if after == before:
            return Rewritten()
        if shown is not None and file_hash(data) != shown:
            raise StaleError("vault.yaml changed since the page showed it, so nothing was written")
        if via == "page" and loosens(before, after):
            raise LooseningError(
                "a page makes only the changes that keep more out: a never-store value added, a redaction rule turned "
                "on, a source made stricter or turned off; make this one at the console or in vault.yaml by hand"
            )
        if not before.written_by_docket(text):
            return Rewritten(by_hand=True)
        updated = after.dump()
        _reads_back(updated)
        taken_in = vault.commit([CONFIG_FILE], TAKEN_IN) if edit_to_take_in(vault, before, text) else None
        write_config(vault.home, path, updated)
        try:
            commit = vault.commit([CONFIG_FILE], message if via == "cli" else f"{message}\n\nvia page")
        except BaseException:
            write_config(vault.home, path, text)
            raise
        return Rewritten(changed=True, commit=commit, taken_in=taken_in)


def edit_to_take_in(vault: Vault, now: VaultConfig, text: str) -> bool:
    """Whether vault.yaml holds an edit not committed yet, which a write commits on its own first. One that lets more in
    than the committed file, by `loosens`, is refused instead, naming no value: a click must never carry a loosening
    someone typed into history. A committed file that is gone or doesn't read is refused too, as nothing can be weighed
    against it. Run under the vault lock by `rewrite`, and by a page before it asks."""
    found = vault.git.run("show", f"HEAD:./{CONFIG_FILE}", check=False)
    if found.returncode != 0:
        raise UncommittedLooseningError(UNREADABLE_HEAD)
    committed_text = found.stdout.replace("\r\n", "\n")
    if committed_text == text.replace("\r\n", "\n"):
        return False
    try:
        committed = VaultConfig.model_validate(yaml.load(committed_text, Loader=UniqueKeyLoader))  # noqa: S506 - a SafeLoader
    except (yaml.YAMLError, ValidationError):
        raise UncommittedLooseningError(UNREADABLE_HEAD) from None
    if loosens(committed, now):
        raise UncommittedLooseningError(UNCOMMITTED_LOOSENING)
    return True


# -- the stricter operations ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Added:
    """What adding a never-store value did: the write, and whether the list held the value already."""

    written: Rewritten
    already: bool


def checked_value(value: str) -> str:
    """A never-store value as a page may add it, its ends trimmed; refused, naming the rule and never the value, when
    empty, past MAX_VALUE characters, or holding a control character or a line break."""
    value = value.strip()
    if not value:
        raise DocketError("a never-store value is a name, a chat or a phrase, never empty")
    if len(value) > MAX_VALUE:
        raise DocketError(f"a never-store value is at most {MAX_VALUE} characters")
    if any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") for ch in value):
        raise DocketError("a never-store value is one line, with no control characters")
    return value


def add_listed(vault: Vault, name: ListName, value: str, *, shown: str) -> Added:
    """Add a value to a never-store list from a page, the one change this makes: the list only grows (DR-0506). A value
    the list already holds, as the matcher reads it, is no write at all, so a post sent twice adds it once. The commit
    names the list and the count, never the value (DR-0508)."""
    if name not in LISTS:
        raise DocketError(f"a never-store list is people, containers or keywords, not {name!r}")
    value = checked_value(value)
    already = False

    def edit(config: VaultConfig) -> VaultConfig:
        nonlocal already
        lists = config.never_store
        if neverstore.holds(lists, name, value):
            already = True
            return config
        grown = lists.model_copy(update={name: [*getattr(lists, name), value]})
        return config.model_copy(update={"never_store": grown})

    written = rewrite(vault, edit, f"Config: never-store {name}, 1 added", via="page", shown=shown)
    return Added(written, already)


def rule_on(vault: Vault, rule: str, *, shown: str) -> Rewritten:
    """Turn a redaction rule on from a page: `credentials`, `phone`, or one of the user's patterns by its name. Only
    ever on (DR-0506); a rule on already is no write. A name that is no rule, or that a built-in rule and a pattern or
    two patterns share, is refused, as `docket redaction set` refuses it."""

    def edit(config: VaultConfig) -> VaultConfig:
        found = config.redaction
        named = [p for p in found.patterns if p.name == rule]
        if rule in BUILTIN_RULES and not named:
            return config.model_copy(update={"redaction": found.model_copy(update={rule: True})})
        if len(named) != 1 or rule in BUILTIN_RULES:
            raise DocketError(
                "no redaction rule by that name, or more than one: turn it on with docket redaction set, or in "
                "vault.yaml by hand"
            )
        patterns = [p.model_copy(update={"enabled": True}) if p.name == rule else p for p in found.patterns]
        return config.model_copy(update={"redaction": found.model_copy(update={"patterns": patterns})})

    return rewrite(vault, edit, f"Config: redaction {rule} on", via="page", shown=shown)


def stricter(vault: Vault, source: str, sensitivity: str, *, shown: str) -> Rewritten:
    """Make a source stricter from a page: work to mixed, either to private (DR-0506). The same sensitivity is no
    write, and a looser one is refused. A folder source is work only, so the schema refuses it before it is written."""
    if sensitivity not in RANK:
        raise DocketError(f"a source's sensitivity is work, mixed or private, not {sensitivity!r}")

    def edit(config: VaultConfig) -> VaultConfig:
        entry = _source(config, source)
        if RANK[sensitivity] < RANK[entry.sensitivity]:
            raise LooseningError(
                f"{source} is {entry.sensitivity}; a page makes a source only stricter, so {sensitivity} is a line to "
                "change in vault.yaml by hand"
            )
        return _with_source(config, source, entry.model_copy(update={"sensitivity": sensitivity}))

    return rewrite(vault, edit, f"Config: source {source} {sensitivity}", via="page", shown=shown)


def turn_off(vault: Vault, source: str, *, shown: str) -> Rewritten:
    """Turn a source off from a page (DR-0506): its syncs and scans stop, and what waits from it stays. Off already is
    no write; turning one on is a line to change by hand."""

    def edit(config: VaultConfig) -> VaultConfig:
        entry = _source(config, source)
        return _with_source(config, source, entry.model_copy(update={"enabled": False}))

    return rewrite(vault, edit, f"Config: source {source} off", via="page", shown=shown)


def _source(config: VaultConfig, name: str) -> SourceConfig:
    entry = config.sources.get(name)
    if entry is None:
        raise DocketError(f"no source {name} in vault.yaml")
    return entry


def _with_source(config: VaultConfig, name: str, entry: SourceConfig) -> VaultConfig:
    return config.model_copy(update={"sources": {**config.sources, name: entry}})


def _reads_back(text: str) -> None:
    """The new text through the schema, as every later read takes it: a change built around the validators (a folder
    source made mixed by a copy) is refused before it is written."""
    try:
        VaultConfig.model_validate(yaml.load(text, Loader=UniqueKeyLoader))  # noqa: S506 - a SafeLoader
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())
        raise DocketError(f"the change doesn't fit vault.yaml: {problems}") from None


# -- the lines to paste -----------------------------------------------------------------------------------------------


def _dump(data: Any) -> str:
    """YAML as vault.yaml holds it, quoted where it must be: `Project: Phoenix` and `Dora #2` stay one value each."""
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False, width=_WIDTH)
    return text.rstrip("\n")


def key_line(key: str, value: object) -> str:
    """One key and its value, such as `sensitivity: private`."""
    return _dump({key: value})


def item_line(value: str) -> str:
    """One item of a list, such as `- 'Dora #2'`."""
    return _dump([value])


def source_block(name: str, entry: SourceConfig) -> str:
    """A source's entry as the lines to paste under `sources:`."""
    body = _dump({name: entry.model_dump(mode="json")})
    return "".join(f"  {line}\n" for line in body.splitlines())


def pattern_block(name: str, pattern: str) -> str:
    """One of the user's redaction patterns as the lines to paste under `redaction:`'s `patterns:`."""
    body = _dump([{"name": name, "pattern": pattern}])
    return "".join(f"  {line}\n" for line in body.splitlines())
