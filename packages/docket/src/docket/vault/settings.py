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
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

import yaml
from pydantic import ValidationError

from docket import console
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
# A commit's body when the owner typed the code back for it at their console (DR-0519).
CONFIRMED = "confirmed at the console"


class LooseningError(DocketError):
    """A page asked for a change outside the list pages may make."""


class StaleError(DocketError):
    """vault.yaml changed since the page read it, so the change the page asked for is not made."""


class UncommittedLooseningError(DocketError):
    """vault.yaml holds an edit not committed yet that lets more in than the committed file, which no write of Docket's
    takes in: committing it would carry a loosening to every clone (DR-0506, DR-0507)."""


UNCOMMITTED_LOOSENING = f"{inforce.NOTICE}; commit it yourself or undo it, then try again"
UNREADABLE_HEAD = inforce.UNREADABLE_HEAD


@dataclass(frozen=True)
class Rewritten:
    """What `rewrite` did: vault.yaml rewritten and committed, nothing to do, or nothing done because the file isn't in
    Docket's form, so the change is the user's to make by hand, or because the owner didn't type the code back.
    `taken_in` is the commit of an edit the file held that wasn't committed yet, made on its own first."""

    changed: bool = False
    commit: str | None = None
    by_hand: bool = False
    taken_in: str | None = None
    declined: bool = False


@dataclass(frozen=True)
class _Held:
    """What HEAD holds against the file: its vault.yaml's text, its settings when the file differs from it (an edit not
    committed yet, which a write takes in first), and whether that edit loosens."""

    text: str
    committed: VaultConfig | None = None
    loosens: bool = False


def file_hash(data: bytes) -> str:
    """vault.yaml as a page read it, which its form carries back, so a change made since is seen (DR-0507)."""
    return hashlib.sha256(data).hexdigest()


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
    hash), when it loosens (DR-0506), and when the file holds an edit not committed yet that loosens (DR-0507).

    The CLI's change that loosens, or one made over an uncommitted edit that does, is made only on the owner's yes at
    their own console (DR-0517): refused before anything is written in Claude Code's shell or without a console, then
    shown, path by path and never a never-store value, with the command's `warnings`, and asked for a code typed back,
    outside the vault lock (DR-0519). On the code the file and HEAD's vault.yaml are read again, and anything is written
    only when both are as asked about. Nothing here answers the question for the owner.

    A change is written only when the file is exactly what Docket writes for it, now or in the form before the
    redaction switches were split; an uncommitted edit goes in first, in a commit of its own. The change is read back
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
        change_loosens = loosens(before, after)
        if via == "page" and change_loosens:
            raise LooseningError(
                "a page makes only the changes that keep more out: a never-store value added, a redaction rule turned "
                "on, a source made stricter or turned off; make this one at the console or in vault.yaml by hand"
            )
        updated = after.dump()
        _reads_back(updated)
        if via == "cli" and change_loosens:
            _at_console()
        if not before.written_by_docket(text):
            return Rewritten(by_hand=True)
        held = _weigh(vault, before, text)
        if held.loosens and via == "page":
            raise UncommittedLooseningError(UNCOMMITTED_LOOSENING)
        if held.loosens:
            _at_console(UNCOMMITTED_LOOSENING)
        asked = via == "cli" and (held.loosens or change_loosens)
        if not asked:
            return _write(vault, text, updated, _message(message, via, False), held, edit_confirmed=False)
        # A conflict refuses before the owner is asked, not after they typed the code (DR-0528).
        vault.refuse_unmerged()
    screen = _screen(held, before, after if change_loosens else None, warnings)
    answer = console.confirm(
        screen,
        "make this change",
        "leave vault.yaml as it is",
        "vault.yaml stays as it is",
        act="make a vault.yaml change that isn't only stricter",
    )
    if answer != "yes":
        return Rewritten(declined=True)
    with vault.writing():
        if file_hash(path.read_bytes()) != file_hash(data) or _head_text(vault) != held.text:
            raise StaleError("vault.yaml changed while you were asked, so nothing was written; run the command again")
        return _write(vault, text, updated, _message(message, via, change_loosens), held, edit_confirmed=held.loosens)


def _at_console(edit: str | None = None) -> None:
    """Refused, before anything is written, without the owner at their own console: in Claude Code's shell, from a
    pipe or a redirect. `edit` says what else would unblock it, for an uncommitted edit."""
    problem = console.problem(console.LOOSENING)
    if problem is not None:
        raise DocketError(problem if edit is None else f"{edit}; or confirm it: {problem}")


def _message(message: str, via: Via, confirmed: bool) -> str:
    if via == "page":
        return f"{message}\n\nvia page"
    return f"{message}\n\n{CONFIRMED}" if confirmed else message


def _write(vault: Vault, text: str, updated: str, message: str, held: _Held, *, edit_confirmed: bool) -> Rewritten:
    """The uncommitted edit committed on its own first, then the change written and committed; a failed commit of the
    change puts the file back as it was."""
    path = vault.root / CONFIG_FILE
    taken_in = None
    if held.committed is not None:
        taken_in = vault.commit([CONFIG_FILE], f"{TAKEN_IN}\n\n{CONFIRMED}" if edit_confirmed else TAKEN_IN)
    write_config(vault.home, path, updated)
    try:
        commit = vault.commit([CONFIG_FILE], message)
    except BaseException:
        write_config(vault.home, path, text)
        raise
    return Rewritten(changed=True, commit=commit, taken_in=taken_in)


def _screen(held: _Held, before: VaultConfig, after: VaultConfig | None, warnings: Sequence[str]) -> list[str]:
    """What the owner is asked about: the uncommitted edit, then the command's change, each only when it loosens."""
    lines: list[str] = []
    if held.committed is not None and held.loosens:
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
        raise UncommittedLooseningError(UNREADABLE_HEAD) from None
    if committed_text == text.replace("\r\n", "\n"):
        return _Held(committed_text)
    try:
        committed = VaultConfig.model_validate(yaml.load(committed_text, Loader=UniqueKeyLoader))  # noqa: S506 - a SafeLoader
    except (yaml.YAMLError, ValidationError):
        raise UncommittedLooseningError(UNREADABLE_HEAD) from None
    return _Held(committed_text, committed, loosens(committed, now))


def edit_to_take_in(vault: Vault, now: VaultConfig, text: str) -> bool:
    """Whether vault.yaml holds an edit not committed yet, which a write commits on its own first. One that loosens
    against the committed file is refused instead, naming no value: a click must never carry a loosening someone typed
    into history; the CLI asks at the console in `rewrite` instead. A committed file that is gone or doesn't read is
    refused too. Run by a page before it asks."""
    held = _weigh(vault, now, text)
    if held.loosens:
        raise UncommittedLooseningError(UNCOMMITTED_LOOSENING)
    return held.committed is not None


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


# -- what changes -----------------------------------------------------------------------------------------------------


def changes(before: VaultConfig, after: VaultConfig) -> list[str]:
    """Each difference by its full path, such as `sources.calls.sensitivity: mixed -> work`; a never-store list only
    as how many values were taken off and added, never a value, so a swap reads as one off and one added (DR-0519)."""
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
