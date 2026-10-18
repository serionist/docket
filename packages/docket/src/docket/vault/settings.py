"""vault.yaml's one writer, the changes a page makes, and the lines to paste.

`rewrite` is the general entry: under the vault lock it reads vault.yaml, asks for the change, and commits the file
rewritten whole when its text is what Docket writes for it, or edited in place, its comments and layout kept, when it
isn't (DR-0602). `docket redaction set`, `docket source new` and every page write through it.

It makes any change its caller asks for, the CLI's and a page's alike (DR-0601): the owner says yes before the command,
at their own terminal or in chat to Claude. The one change kept at the owner's console is turning credentials redaction
off: the CLI asks `console.confirm` for it, and a page refuses it.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import ValidationError

from docket import console
from docket.errors import DocketError
from docket.vault import inforce, neverstore, yamledit
from docket.vault.config import CONFIG_FILE, SourceConfig, UniqueKeyLoader, VaultConfig, write_config
from docket.vault.neverstore import ListName
from docket.vault.vault import Vault

Via = Literal["cli", "page"]
# The rank and the lists are the settings in force's; pages read them from here.
RANK = inforce.RANK
LISTS = inforce.LISTS
# The built-in redaction switches, by their keys in vault.yaml's redaction block.
BUILTIN_RULES = ("credentials", "phone")
# A line to paste never wraps: a long keyword stays one line.
_WIDTH = 4096
# A never-store value is a name, a chat or a phrase: this is long enough for any, and short enough for one line.
MAX_VALUE = 200
TAKEN_IN = "Config: vault.yaml as edited"
# A commit's body when the owner confirmed it at their console (DR-0601).
CONFIRMED = "confirmed at the console"
CREDENTIALS_OFF_HERE = (
    "turning credentials redaction off is yours to confirm at your own console: docket redaction set credentials off"
)
UNCOMMITTED_CREDENTIALS_OFF = (
    f"{inforce.NOTICE}, and it turns credentials redaction off; commit it yourself, or run a docket command that "
    "writes vault.yaml at your own console, which asks"
)
UNREADABLE_HEAD = inforce.UNREADABLE_HEAD


class StaleError(DocketError):
    """vault.yaml changed since the page read it, so the change the page asked for is not made."""


class CredentialsOffError(DocketError):
    """A page asked to turn credentials redaction off, by its change or by an uncommitted edit a write would take in:
    only the owner's console does (DR-0601)."""


@dataclass(frozen=True)
class Rewritten:
    """What `rewrite` did: vault.yaml rewritten and committed, nothing to do, or nothing done because the file isn't in
    Docket's form, so the change is the user's to make by hand, or because the owner didn't confirm turning credentials
    redaction off. `taken_in` is the commit of an edit the file held that wasn't committed yet, made on its own
    first."""

    changed: bool = False
    commit: str | None = None
    by_hand: bool = False
    taken_in: str | None = None
    declined: bool = False


@dataclass(frozen=True)
class _Held:
    """What HEAD holds against the file: its vault.yaml's text, its settings when the file differs from it (an edit not
    committed yet, which a write takes in first), and whether that edit turns credentials redaction off."""

    text: str
    committed: VaultConfig | None = None
    credentials_off: bool = False


def file_hash(data: bytes) -> str:
    """vault.yaml as a page read it, which its form carries back, so a change made since is seen (DR-0602)."""
    return hashlib.sha256(data).hexdigest()


def credentials_off(before: VaultConfig, after: VaultConfig) -> bool:
    """Whether a change turns credentials redaction off: the one change asked at the owner's console (DR-0601)."""
    return before.redaction.credentials and not after.redaction.credentials


def rewrite(
    vault: Vault,
    edit: Callable[[VaultConfig], VaultConfig],
    message: str,
    *,
    via: Via,
    shown: str | None = None,
    warnings: Sequence[str] = (),
) -> Rewritten:
    """Change vault.yaml: `edit` gets the settings as written and gives the new ones. Nothing to change is no write at
    all, whatever the file did since. A page's change is refused when the file isn't the one it showed (`shown`, its
    hash). Every change is made, the commit saying who (DR-0601), but one that turns credentials redaction off.

    That one, or one made over an uncommitted edit that does, is made for the CLI only on the owner's yes at their own
    console: refused before anything is written in Claude Code's shell or without a console, then shown with the
    command's `warnings` and asked through `console.confirm`, outside the vault lock. On a yes the file and HEAD's
    vault.yaml are read again, and anything is written only when both are as asked about. A page's is refused.

    A file in Docket's form, now or before the redaction switches were split, is rewritten whole; any other is edited in
    place, and only when that reads back as exactly the settings asked for, else nothing is written and the caller
    gives the line (DR-0602). An uncommitted edit goes in first, in a commit of its own. The change is read back
    through the schema, written through a temp file and a rename, and committed alone; a failure puts the file back."""
    path = vault.root / CONFIG_FILE
    with vault.writing():
        data = path.read_bytes()
        text = data.decode("utf-8")
        before = VaultConfig.parse(text, path)
        after = edit(before)
        if after == before:
            return Rewritten()
        if shown is not None and file_hash(data) != shown:
            raise StaleError("vault.yaml changed since the page showed it, so nothing was written")
        change_off = credentials_off(before, after)
        if via == "page" and change_off:
            raise CredentialsOffError(CREDENTIALS_OFF_HERE)
        _reads_back(after.dump())
        if via == "cli" and change_off:
            _at_console()
        updated = after.dump() if before.written_by_docket(text) else _in_place(text, before, after, path)
        if updated is None:
            return Rewritten(by_hand=True)
        held = _weigh(vault, before, text)
        if held.credentials_off and via == "page":
            raise CredentialsOffError(UNCOMMITTED_CREDENTIALS_OFF)
        if held.credentials_off:
            _at_console(UNCOMMITTED_CREDENTIALS_OFF)
        if not (held.credentials_off or change_off):
            return _write(vault, text, updated, _message(message, via, False), held, via=via, edit_confirmed=False)
        # A conflict refuses before the owner is asked, not after they confirmed (DR-0528).
        vault.refuse_unmerged()
    screen = _screen(held, before, after if change_off else None, warnings)
    answer = console.confirm(
        screen,
        "make this change",
        "leave vault.yaml as it is",
        "vault.yaml stays as it is",
        act="turn credentials redaction off",
    )
    if answer != "yes":
        return Rewritten(declined=True)
    with vault.writing():
        if file_hash(path.read_bytes()) != file_hash(data) or _head_text(vault) != held.text:
            raise StaleError("vault.yaml changed while you were asked, so nothing was written; run the command again")
        message = _message(message, via, change_off)
        return _write(vault, text, updated, message, held, via=via, edit_confirmed=held.credentials_off)


def _in_place(text: str, before: VaultConfig, after: VaultConfig, path: Path) -> str | None:
    """The file edited in place, its comments and layout kept (DR-0602), when that reads back as exactly `after`."""
    patched = yamledit.patch(text, before, after)
    if patched is None:
        return None
    try:
        return patched if VaultConfig.parse(patched, path) == after else None
    except DocketError:
        return None


def _at_console(edit: str | None = None) -> None:
    """Refused, before anything is written, without the owner at their own console: in Claude Code's shell, from a
    pipe or a redirect. `edit` says what else would unblock it, for an uncommitted edit."""
    problem = console.problem(console.LOOSENING)
    if problem is not None:
        raise DocketError(problem if edit is None else f"{edit}; or confirm it: {problem}")


def _message(message: str, via: Via, confirmed: bool) -> str:
    if via == "page":
        return f"{message}\n\nvia page"
    return f"{message}\n\n{CONFIRMED}" if confirmed else console.signed(message)


def _write(
    vault: Vault, text: str, updated: str, message: str, held: _Held, *, via: Via, edit_confirmed: bool
) -> Rewritten:
    """The uncommitted edit committed on its own first, then the change written and committed; a failed commit of the
    change puts the file back as it was."""
    path = vault.root / CONFIG_FILE
    taken_in = None
    if held.committed is not None:
        taken = f"{TAKEN_IN}\n\n{CONFIRMED}" if edit_confirmed else _message(TAKEN_IN, via, False)
        taken_in = vault.commit([CONFIG_FILE], taken)
    write_config(vault.home, path, updated)
    try:
        commit = vault.commit([CONFIG_FILE], message)
    except BaseException:
        write_config(vault.home, path, text)
        raise
    return Rewritten(changed=True, commit=commit, taken_in=taken_in)


def _screen(held: _Held, before: VaultConfig, after: VaultConfig | None, warnings: Sequence[str]) -> list[str]:
    """What the owner is asked about: the uncommitted edit, then the command's change, each only when it turns
    credentials redaction off."""
    lines: list[str] = []
    if held.committed is not None and held.credentials_off:
        lines += [f"{inforce.NOTICE}; it goes in first:", *(f"  {c}" for c in changes(held.committed, before))]
    if after is not None:
        lines += ["this command changes vault.yaml:", *(f"  {c}" for c in changes(before, after))]
    return [*lines, *(f"note     {w}" for w in warnings)]


def _head_text(vault: Vault) -> str | None:
    try:
        return inforce.committed_text(vault.root, vault.home)
    except inforce.UnreadableHeadError:
        return None


def _weigh(vault: Vault, now: VaultConfig, text: str) -> _Held:
    """HEAD's vault.yaml against the file as written. A committed file that is gone or doesn't read is refused, for
    every caller, as nothing can be weighed against it."""
    try:
        committed_text = inforce.committed_text(vault.root, vault.home)
    except inforce.UnreadableHeadError:
        raise DocketError(UNREADABLE_HEAD) from None
    if committed_text == text.replace("\r\n", "\n"):
        return _Held(committed_text)
    try:
        committed = VaultConfig.model_validate(yaml.load(committed_text, Loader=UniqueKeyLoader))  # noqa: S506 - a SafeLoader
    except (yaml.YAMLError, ValidationError):
        raise DocketError(UNREADABLE_HEAD) from None
    return _Held(committed_text, committed, credentials_off(committed, now))


def edit_to_take_in(vault: Vault, now: VaultConfig, text: str) -> bool:
    """Whether vault.yaml holds an edit not committed yet, which a write commits on its own first. One that turns
    credentials redaction off is refused for a page, naming no value; a committed file that is gone or doesn't read is
    refused too. Run by a page before it asks."""
    held = _weigh(vault, now, text)
    if held.credentials_off:
        raise CredentialsOffError(UNCOMMITTED_CREDENTIALS_OFF)
    return held.committed is not None


# -- the page's changes -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Added:
    """What adding or taking off a never-store value did: the write, and whether the list was so already."""

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
    """Add a value to a never-store list from a page. A value the list already holds, as the matcher reads it, is no
    write at all, so a post sent twice adds it once. The commit names the list and the count, never the value
    (DR-0508)."""
    _list_name(name)
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


def take_off_listed(vault: Vault, name: ListName, value: str, *, shown: str) -> Added:
    """Take a value off a never-store list from a page (DR-0601), as written there: a value the list doesn't hold is no
    write, so a post sent twice takes it off once. The commit names the list and the count, never the value."""
    _list_name(name)
    value = value.strip()
    already = False

    def edit(config: VaultConfig) -> VaultConfig:
        nonlocal already
        lists = config.never_store
        values: list[str] = getattr(lists, name)
        if value not in values:
            already = True
            return config
        kept = lists.model_copy(update={name: [v for v in values if v != value]})
        return config.model_copy(update={"never_store": kept})

    written = rewrite(vault, edit, f"Config: never-store {name}, 1 taken off", via="page", shown=shown)
    return Added(written, already)


def _list_name(name: str) -> None:
    if name not in LISTS:
        raise DocketError(f"a never-store list is people, containers or keywords, not {name!r}")


def set_rule(vault: Vault, rule: str, on: bool, *, shown: str) -> Rewritten:
    """Turn a redaction rule on or off from a page: `credentials`, `phone`, or one of the user's patterns by its name. A
    rule that is so already is no write; credentials off is refused (DR-0601). A name that is no rule, or that a
    built-in rule and a pattern or two patterns share, is refused, as `docket redaction set` refuses it."""

    def edit(config: VaultConfig) -> VaultConfig:
        found = config.redaction
        named = [p for p in found.patterns if p.name == rule]
        if rule in BUILTIN_RULES and not named:
            return config.model_copy(update={"redaction": found.model_copy(update={rule: on})})
        if len(named) != 1 or rule in BUILTIN_RULES:
            raise DocketError(
                "no redaction rule by that name, or more than one: change it with docket redaction set, or in "
                "vault.yaml by hand"
            )
        patterns = [p.model_copy(update={"enabled": on}) if p.name == rule else p for p in found.patterns]
        return config.model_copy(update={"redaction": found.model_copy(update={"patterns": patterns})})

    return rewrite(vault, edit, f"Config: redaction {rule} {'on' if on else 'off'}", via="page", shown=shown)


def set_sensitivity(vault: Vault, source: str, sensitivity: str, *, shown: str) -> Rewritten:
    """Set a source's sensitivity from a page, either way (DR-0601). The same sensitivity is no write. A folder source
    is work only, so the schema refuses another before it is written."""
    if sensitivity not in RANK:
        raise DocketError(f"a source's sensitivity is work, mixed or private, not {sensitivity!r}")

    def edit(config: VaultConfig) -> VaultConfig:
        entry = _source(config, source)
        return _with_source(config, source, entry.model_copy(update={"sensitivity": sensitivity}))

    return rewrite(vault, edit, f"Config: source {source} {sensitivity}", via="page", shown=shown)


def set_enabled(vault: Vault, source: str, on: bool, *, shown: str) -> Rewritten:
    """Turn a source on or off from a page (DR-0601): off, its syncs and scans stop, and what waits from it stays. The
    same state is no write."""

    def edit(config: VaultConfig) -> VaultConfig:
        entry = _source(config, source)
        return _with_source(config, source, entry.model_copy(update={"enabled": on}))

    return rewrite(vault, edit, f"Config: source {source} {'on' if on else 'off'}", via="page", shown=shown)


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


# -- what changes -----------------------------------------------------------------------------------------------------


def changes(before: VaultConfig, after: VaultConfig) -> list[str]:
    """Each difference by its full path, such as `sources.calls.sensitivity: mixed -> work`; a never-store list only
    as how many values were taken off and added, never a value, so a swap reads as one off and one added."""
    out: list[str] = []
    _walk("", before.model_dump(mode="json"), after.model_dump(mode="json"), out)
    return out


def _walk(path: str, old: Any, new: Any, out: list[str]) -> None:
    if path.startswith("never_store.") and isinstance(old, list) and isinstance(new, list):
        olds, news = Counter(cast(list[str], old)), Counter(cast(list[str], new))
        off, on = sum((olds - news).values()), sum((news - olds).values())
        if off or on:
            out.append(f"{path}: {off} taken off, {on} added")
        return
    if isinstance(old, dict) and isinstance(new, dict):
        olds, news = cast(dict[str, Any], old), cast(dict[str, Any], new)
        for key in [*olds, *(k for k in news if k not in olds)]:
            sub = f"{path}.{key}" if path else key
            if key not in news:
                out.append(f"{sub}: removed")
            elif key not in olds:
                out.append(f"{sub}: added")
                _leaves(sub, news[key], out)
            else:
                _walk(sub, olds[key], news[key], out)
        return
    if _maps(old) and _maps(new):
        olds_k, news_k = _by_name(cast(list[dict[str, Any]], old)), _by_name(cast(list[dict[str, Any]], new))
        # Each entry by its name, as the settings in force match patterns (DR-0521): a reorder alone says nothing.
        for key, two in news_k.items():
            one = olds_k.pop(key, None)
            sub = f"{path}[{_label(key)}]"
            if one is None:
                out.append(f"{sub}: added")
                _leaves(sub, two, out)
            else:
                _walk(sub, one, two, out)
        out += [f"{path}[{_label(key)}]: removed" for key in olds_k]
        return
    if old != new:
        out.append(f"{path}: {_shown(old)} -> {_shown(new)}")


def _maps(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, dict) for item in cast(list[Any], value))


def _by_name(entries: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
    """Each entry by its name and which of that name it is, in order; one without a name by its place."""
    seen: dict[str, int] = {}
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for n, entry in enumerate(entries):
        name = str(entry.get("name", f"#{n}"))
        k = seen.get(name, 0)
        seen[name] = k + 1
        out[(name, k)] = entry
    return out


def _label(key: tuple[str, int]) -> str:
    name, k = key
    return name if k == 0 else f"{name} {k + 1}"


def _leaves(path: str, value: Any, out: list[str]) -> None:
    """What an added entry holds, a line a value, empty ones left out; under never_store a count, never a value."""
    if isinstance(value, dict):
        for key, item in cast(dict[str, Any], value).items():
            _leaves(f"{path}.{key}", item, out)
        return
    if value is None or value == []:
        return
    if path.startswith("never_store.") and isinstance(value, list):
        out.append(f"  {path}: {len(cast(list[Any], value))} added")
        return
    out.append(f"  {path}: {_shown(value)}")


def _shown(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "none"
    if isinstance(value, list):
        return "[" + ", ".join(_shown(item) for item in cast(list[Any], value)) + "]"
    return str(value)


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
