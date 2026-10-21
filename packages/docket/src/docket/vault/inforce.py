"""The settings in force (DR-0521): what the gate and every job that decides what gets in obey.

An edit of vault.yaml not committed yet keeps more out at once and lets more in only once committed. `effective` is
HEAD's settings with every part of the working file that only keeps more out put in, field by field, each checked
against what the gate and the jobs do with it; everything else stays HEAD's, so a key a later schema adds waits too.
Every write of Docket's that changes vault.yaml commits such an edit first (DR-0601), so it is in force from there.
"""

from __future__ import annotations

import contextlib
import json
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from docket.errors import DocketError
from docket.vault.config import CONFIG_FILE, NeverStore, Redaction, RedactionPattern, SourceConfig, VaultConfig
from docket.vault.git import Git
from docket.vault.home import Home
from docket.vault.neverstore import ListName
from docket.vault.staging import write_replacing

# Stricter to the right: work stores everything, mixed waits for a classification, private stores nothing.
RANK = {"work": 0, "mixed": 1, "private": 2}
LISTS: tuple[ListName, ...] = ("people", "containers", "keywords")
# What every place that reads the settings in force says while the file holds more: never a value, and never that the
# edit lets more in, since what waits can be neutral, such as a time zone.
NOTICE = "vault.yaml has an edit that isn't in force until you commit it"
UNREADABLE_HEAD = (
    "vault.yaml isn't in the last commit as a file Docket can read, so an edit of it can't be weighed; commit it "
    "yourself, then try again"
)
# HEAD's vault.yaml by the commit that holds it, so no git call is made while HEAD stays. Disposable: one that doesn't
# read is a miss.
CACHE = "committed-vault-yaml.json"


class UnreadableHeadError(DocketError):
    """HEAD holds no vault.yaml Docket can read, so nothing can be weighed against it."""


@dataclass(frozen=True)
class Settings:
    """vault.yaml as written, and the settings in force under it."""

    file: VaultConfig
    in_force: VaultConfig

    @property
    def waiting(self) -> bool:
        """Whether the file holds an edit that isn't in force until it is committed."""
        return self.in_force != self.file


def note(waiting: bool) -> str:
    """What a message that sends the owner to vault.yaml adds while an edit there isn't in force."""
    return f"; {NOTICE}" if waiting else ""


def load(root: Path, home: Home) -> Settings:
    """The settings in force for the vault at `root`: HEAD's vault.yaml with what the file only tightens. A file that
    is HEAD's is in force whole, with nothing else read. A change of `storage` not committed stops here, since the git
    dir HEAD is read from depends on it."""
    path = root / CONFIG_FILE
    text = path.read_text(encoding="utf-8")
    file = VaultConfig.parse(text, path)
    held = committed_text(root, home)
    if held == text.replace("\r\n", "\n"):
        return Settings(file, file)
    try:
        committed = VaultConfig.parse(held, Path(f"HEAD:{CONFIG_FILE}"))
    except DocketError:
        raise UnreadableHeadError(UNREADABLE_HEAD) from None
    if committed.storage != file.storage:
        raise DocketError(f"{NOTICE}: its storage, which Docket reads HEAD by")
    return Settings(file, effective(committed, file))


def committed_text(root: Path, home: Home) -> str:
    """HEAD's vault.yaml, from the cache when HEAD is the commit it was read at, else by `git show <id>:` with the id
    read first, so a commit landing between the two reads never files one commit's text under another's id. HEAD's id
    comes from the git dir's files (DR-0765), or from git where they can't say, such as a linked worktree."""
    git = Git(root, home=home)
    commit = git.head_id()
    if commit is None:
        raise UnreadableHeadError(UNREADABLE_HEAD)
    cached = _cached(home)
    if cached is not None and cached[0] == commit:
        return cached[1]
    found = git.run("show", f"{commit}:./{CONFIG_FILE}", check=False)
    if found.returncode != 0:
        raise UnreadableHeadError(UNREADABLE_HEAD)
    text = found.stdout.replace("\r\n", "\n")
    remember(home, commit, text)
    return text


def carry(home: Home, commit: str, parents: Sequence[str], paths: Sequence[str]) -> None:
    """After a commit of Docket's own that didn't name vault.yaml, made on the cached commit: the cache moves to it,
    since `--only` never takes a path it wasn't given. One that named vault.yaml leaves a miss for the next read, which
    asks git: the file read now could already hold an edit made after the commit, and must never pass for HEAD's."""
    if CONFIG_FILE in paths:
        return
    cached = _cached(home)
    if cached is not None and list(parents) == [cached[0]]:
        remember(home, commit, cached[1])


def _cached(home: Home) -> tuple[str, str] | None:
    try:
        data = json.loads((home.private / CACHE).read_text(encoding="utf-8"))
        commit, text = data["commit"], data["text"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return (commit, text) if isinstance(commit, str) and isinstance(text, str) else None


def remember(home: Home, commit: str, text: str) -> None:
    """File HEAD's vault.yaml under its commit. A write that fails leaves a miss for the next read, never an error: the
    service's threads may race here, and each writes through a temp file of its own."""
    with contextlib.suppress(OSError, DocketError):
        body = json.dumps({"commit": commit, "text": text})
        write_replacing(home, home.private / CACHE, body, f"vault-cache.{secrets.token_hex(4)}")


def effective(committed: VaultConfig, file: VaultConfig) -> VaultConfig:
    """`committed` with every part of `file` that only keeps more out: values added to a never-store list, a rule
    turned on, a new pattern, a source made stricter or turned off, an exclude entry added, a tool removed, a source
    removed. Never looser than `committed`, whatever `file` holds."""
    return committed.model_copy(
        update={
            "never_store": _lists(committed.never_store, file.never_store),
            "redaction": _redaction(committed.redaction, file.redaction),
            "sources": _sources(committed.sources, file.sources),
        }
    )


def _grown(before: list[str], after: list[str]) -> list[str]:
    """`after`'s values, then those of `before` that `after` dropped: what was added counts, and what was taken off
    stays."""
    return [*after, *(value for value in before if value not in after)]


def _lists(before: NeverStore, after: NeverStore) -> NeverStore:
    """Each list: the values added, and the values taken off kept. A value only drops records."""
    return before.model_copy(update={name: _grown(getattr(before, name), getattr(after, name)) for name in LISTS})


def _redaction(before: Redaction, after: Redaction) -> Redaction:
    """Credentials and phone on if either has them on; each of `before`'s patterns turned on where `after` turned it on
    and changed nothing else of it; `after`'s new patterns. Every rule reads the text as given and their spans merge
    (DR-0525), so a rule turned on or added only ever redacts more, wherever it stands."""
    return before.model_copy(
        update={
            "credentials": before.credentials or after.credentials,
            "phone": before.phone or after.phone,
            "patterns": _patterns(before.patterns, after.patterns),
        }
    )


def _patterns(before: list[RedactionPattern], after: list[RedactionPattern]) -> list[RedactionPattern]:
    """`after`'s patterns in its order, each of `before`'s matched by its name and which of that name it is: taken as
    `after` has it where it is only turned on or kept, else as `before` has it; a new one taken when it sets nothing
    beyond its name, pattern and enabled, so one carrying a key a later schema adds waits whole. Then `before`'s that
    `after` dropped or renamed, which stay until the commit."""
    old = _by_name(before)
    out: list[RedactionPattern] = []
    for key, new in _by_name(after).items():
        was = old.pop(key, None)
        if was is None:
            if new == type(new)(name=new.name, pattern=new.pattern, enabled=new.enabled):
                out.append(new)
        elif new.enabled >= was.enabled and new.model_copy(update={"enabled": was.enabled}) == was:
            out.append(new)
        else:
            out.append(was)
    return [*out, *old.values()]


def _by_name(patterns: list[RedactionPattern]) -> dict[tuple[str, int], RedactionPattern]:
    """Each pattern by its name and which of that name it is, in order: two patterns may share a name."""
    seen: dict[str, int] = {}
    out: dict[tuple[str, int], RedactionPattern] = {}
    for pattern in patterns:
        n = seen.get(pattern.name, 0)
        seen[pattern.name] = n + 1
        out[(pattern.name, n)] = pattern
    return out


def _sources(before: Mapping[str, SourceConfig], after: Mapping[str, SourceConfig]) -> dict[str, SourceConfig]:
    """`before`'s sources that `after` still has, each made stricter where `after` is; a source `after` added waits.
    A source removed is refused at intake, held at promote and refused by ingest, scan and sync."""
    return {name: _source(old, after[name]) for name, old in before.items() if name in after}


def _source(before: SourceConfig, after: SourceConfig) -> SourceConfig:
    """The stricter sensitivity, off if either is off, the tools and media folders `after` still lists, and for a
    folder source the exclude entries added. A run takes its tools when it opens and a capture from any other marks it
    short; a path's media comes only from a folder both list (DR-0604); exclude is fnmatch with no negation, so an
    entry only leaves paths out. Every other key stays `before`'s."""
    update: dict[str, object] = {
        "sensitivity": max(before.sensitivity, after.sensitivity, key=RANK.__getitem__),
        "enabled": before.enabled and after.enabled,
        "tools": [tool for tool in after.tools if tool in before.tools],
        "media_folders": [folder for folder in after.media_folders if folder in before.media_folders],
    }
    if before.folder is not None:
        update["exclude"] = _grown(before.exclude, after.exclude)
    return before.model_copy(update=update)
