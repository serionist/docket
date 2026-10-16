"""vault.yaml's one writer, the short list of changes a page may make, and the lines to paste for every other change.

`rewrite` is the general entry: under the vault lock it reads vault.yaml, asks for the change, and rewrites and commits
the file only when its text is what Docket writes for it, so no comment or layout of the user's is lost (DR-0166,
DR-0193, DR-0507). `docket redaction set` and `docket source new` write through it, and so does every page.

A page's write is held to `loosens`, an allowlist: pages make only the changes that keep more out, and every other
vault.yaml change is named with its command or its line, never made (DR-0506). What a page may import from here is
`FOR_PAGES`; a test holds the web and the service to it.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

import yaml
from pydantic import ValidationError

from docket.errors import DocketError
from docket.vault import neverstore
from docket.vault.config import (
    CONFIG_FILE,
    NeverStore,
    Redaction,
    SourceConfig,
    UniqueKeyLoader,
    VaultConfig,
    write_config,
)
from docket.vault.neverstore import ListName
from docket.vault.vault import Vault

Via = Literal["cli", "page"]
# Stricter to the right: work stores everything, mixed waits for a classification, private stores nothing.
RANK = {"work": 0, "mixed": 1, "private": 2}
LISTS: tuple[ListName, ...] = ("people", "containers", "keywords")
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
    committed yet is committed on its own first, so each commit says what it did. The change is read back through the
    schema, written through a temp file and a rename, and committed alone; a failure puts the file back."""
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
        taken_in = vault.commit([CONFIG_FILE], TAKEN_IN)
        write_config(vault.home, path, updated)
        try:
            commit = vault.commit([CONFIG_FILE], message if via == "cli" else f"{message}\n\nvia page")
        except BaseException:
            write_config(vault.home, path, text)
            raise
        return Rewritten(changed=True, commit=commit, taken_in=taken_in)


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


def _reads_back(text: str) -> None:
    """The new text through the schema, as every later read takes it: a change built around the validators (a folder
    source made mixed by a copy) is refused before it is written."""
    try:
        VaultConfig.model_validate(yaml.load(text, Loader=UniqueKeyLoader))  # noqa: S506 - a SafeLoader
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())
        raise DocketError(f"the change doesn't fit vault.yaml: {problems}") from None


# -- the door ---------------------------------------------------------------------------------------------------------


def loosens(before: VaultConfig, after: VaultConfig) -> bool:
    """Whether `after` is anything but `before` with the changes pages may make (DR-0506): a value added to a
    never-store list, credentials or phone turned on, a pattern turned on with nothing else of it changed, a source made
    stricter, a source turned off. Everything else loosens: a value taken off, a rule turned off, a pattern added or
    changed, a source added, removed, made looser or turned on, its tools, mapper, hosts, folder or exclude, the owner,
    the storage, the primary machine, and any key added later.

    An allowlist at every level: each helper copies `before`'s value back into `after` only for a field a page may
    change and only where the change is one it may make, and the whole models are then compared, so a field this code
    doesn't name, at the top or inside a list, a rule or a pattern, counts as it stands."""
    allowed = after.model_copy(
        update={
            "never_store": _lists_back(before.never_store, after.never_store),
            "redaction": _redaction_back(before.redaction, after.redaction),
            "sources": _sources_back(before.sources, after.sources),
        }
    )
    return allowed != before


def _lists_back(before: NeverStore, after: NeverStore) -> NeverStore:
    """`after` with each list that only grew put back as it was."""
    grown = {
        name: getattr(before, name) for name in LISTS if Counter(getattr(before, name)) <= Counter(getattr(after, name))
    }
    return after.model_copy(update=grown)


def _redaction_back(before: Redaction, after: Redaction) -> Redaction:
    """`after` with credentials and phone put back where they weren't turned off, and each pattern's `enabled` put back
    where it wasn't turned off, the patterns matched by position; their names, expressions and anything else stay as
    `after` has them, so the comparison sees a change to them."""
    update: dict[str, object] = {}
    if after.credentials >= before.credentials:
        update["credentials"] = before.credentials
    if after.phone >= before.phone:
        update["phone"] = before.phone
    if len(after.patterns) == len(before.patterns):
        update["patterns"] = [
            new.model_copy(update={"enabled": old.enabled}) if new.enabled >= old.enabled else new
            for old, new in zip(before.patterns, after.patterns, strict=True)
        ]
    return after.model_copy(update=update)


def _sources_back(before: Mapping[str, SourceConfig], after: Mapping[str, SourceConfig]) -> dict[str, SourceConfig]:
    """`after`'s sources with each one's sensitivity put back where it was made stricter or kept, and `enabled` where
    it was turned off or kept; every other key stays as `after` has it. A source added or removed stays a difference."""
    if list(before) != list(after):
        return dict(after)
    out: dict[str, SourceConfig] = {}
    for name, new in after.items():
        old = before[name]
        update: dict[str, object] = {}
        if RANK[new.sensitivity] >= RANK[old.sensitivity]:
            update["sensitivity"] = old.sensitivity
        if new.enabled <= old.enabled:
            update["enabled"] = old.enabled
        out[name] = new.model_copy(update=update)
    return out


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
