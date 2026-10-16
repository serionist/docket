"""vault.yaml's one writer, the short list of changes a page may make, and the lines to paste for every other change.

`rewrite` is the general entry: under the vault lock it reads vault.yaml, asks for the change, and rewrites and commits
the file only when its text is what Docket writes for it, so no comment or layout of the user's is lost (DR-0166,
DR-0193, DR-0507). `docket redaction set` and `docket source new` write through it, and so does every page.

A page's write is held to `loosens`, an allowlist: pages make only the changes that keep more out, and every other
vault.yaml change is named with its command or its line, never made (DR-0506). What a page may import from here is
`FOR_PAGES`; a test holds the web and the service to it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

import yaml
from pydantic import ValidationError

from docket.errors import DocketError
from docket.vault.config import (
    CONFIG_FILE,
    NeverStore,
    Redaction,
    SourceConfig,
    UniqueKeyLoader,
    VaultConfig,
    write_config,
)
from docket.vault.vault import Vault

Via = Literal["cli", "page"]
# Stricter to the right: work stores everything, mixed waits for a classification, private stores nothing.
RANK = {"work": 0, "mixed": 1, "private": 2}
LISTS = ("people", "containers", "keywords")
# The names docket.web and docket.service may import from here: the read-only helpers, and the stricter operations as
# they come. Never `rewrite`, which makes any change (DR-0506).
FOR_PAGES = frozenset({"RANK", "LISTS", "loosens", "key_line", "item_line", "source_block", "pattern_block"})
# A line to paste never wraps: a long keyword stays one line.
_WIDTH = 4096


class LooseningError(DocketError):
    """A page asked for a change outside the list pages may make."""


@dataclass(frozen=True)
class Rewritten:
    """What `rewrite` did: vault.yaml rewritten and committed, nothing to do, or nothing done because the file isn't in
    Docket's form, so the change is the user's to make by hand."""

    changed: bool = False
    commit: str | None = None
    by_hand: bool = False


def rewrite(vault: Vault, edit: Callable[[VaultConfig], VaultConfig], message: str, *, via: Via) -> Rewritten:
    """Change vault.yaml under the vault lock: `edit` gets the settings as they stand and gives the new ones. A change
    is written only when the file is exactly what Docket writes for it, now or in the form before the redaction
    switches were split; it is read back through the schema, written through a temp file and a rename, and committed
    alone. A failure puts the file back. A page's change is held to `loosens` first."""
    path = vault.root / CONFIG_FILE
    with vault.writing():
        text = path.read_bytes().decode("utf-8")
        before = VaultConfig.load(path)
        after = edit(before)
        if after == before:
            return Rewritten()
        if via == "page" and loosens(before, after):
            raise LooseningError(
                "a page makes only the changes that keep more out: a never-store value added, a redaction rule turned "
                "on, a source made stricter or turned off; make this one at the console or in vault.yaml by hand"
            )
        if not before.written_by_docket(text):
            return Rewritten(by_hand=True)
        updated = after.dump()
        _reads_back(updated)
        write_config(vault.home, path, updated)
        try:
            commit = vault.commit([CONFIG_FILE], message if via == "cli" else f"{message}\n\nvia page")
        except BaseException:
            write_config(vault.home, path, text)
            raise
        return Rewritten(changed=True, commit=commit)


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
    the storage, the primary machine, and any key added later."""
    allowed = after.model_copy(
        update={
            "never_store": _lists_back(before.never_store, after.never_store),
            "redaction": _redaction_back(before.redaction, after.redaction),
            "sources": _sources_back(before.sources, after.sources),
        }
    )
    return allowed != before


def _lists_back(before: NeverStore, after: NeverStore) -> NeverStore:
    """`before` when `after` only adds values to the lists, so the comparison sees no change; `after` otherwise."""
    grown = all(Counter(getattr(before, name)) <= Counter(getattr(after, name)) for name in LISTS)
    return before if grown else after


def _redaction_back(before: Redaction, after: Redaction) -> Redaction:
    """`before` when `after` only turns rules or patterns on; `after` otherwise."""
    if after.credentials < before.credentials or after.phone < before.phone:
        return after
    if len(after.patterns) != len(before.patterns):
        return after
    for old, new in zip(before.patterns, after.patterns, strict=True):
        if (new.name, new.pattern) != (old.name, old.pattern) or new.enabled < old.enabled:
            return after
    return before


def _sources_back(before: Mapping[str, SourceConfig], after: Mapping[str, SourceConfig]) -> dict[str, SourceConfig]:
    """`before` when `after` has the same sources and only makes some stricter or turns them off; `after` otherwise."""
    if list(before) != list(after):
        return dict(after)
    for name, old in before.items():
        new = after[name]
        stricter = RANK[new.sensitivity] >= RANK[old.sensitivity]
        off = new.enabled <= old.enabled
        back = new.model_copy(
            update={
                "sensitivity": old.sensitivity if stricter else new.sensitivity,
                "enabled": old.enabled if off else new.enabled,
            }
        )
        if back != old:
            return dict(after)
    return dict(before)


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
