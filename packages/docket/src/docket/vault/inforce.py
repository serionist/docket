"""The settings in force (DR-0521): what the gate and every job that decides what gets in obey.

An edit of vault.yaml not committed yet keeps more out at once and lets more in only once committed. `effective` is
HEAD's settings with every part of the working file that only keeps more out put in, field by field, each checked
against what the gate and the jobs do with it; everything else stays HEAD's, so a key a later schema adds waits too.
`loosens` is the same rule seen from the other side: a change loosens when the settings in force under it aren't the
change itself (DR-0506). One rule, so the page's door, the take-in, the console's question and the gate never drift.
"""

from __future__ import annotations

from collections.abc import Mapping

from docket.vault.config import NeverStore, Redaction, RedactionPattern, SourceConfig, VaultConfig
from docket.vault.neverstore import ListName

# Stricter to the right: work stores everything, mixed waits for a classification, private stores nothing.
RANK = {"work": 0, "mixed": 1, "private": 2}
LISTS: tuple[ListName, ...] = ("people", "containers", "keywords")


def effective(committed: VaultConfig, file: VaultConfig) -> VaultConfig:
    """`committed` with every part of `file` that only keeps more out: values added to a never-store list, a rule
    turned on, a new pattern run after the others, a source made stricter or turned off, an exclude entry added, a tool
    removed, a source removed. Never looser than `committed`, whatever `file` holds."""
    return committed.model_copy(
        update={
            "never_store": _lists(committed.never_store, file.never_store),
            "redaction": _redaction(committed.redaction, file.redaction),
            "sources": _sources(committed.sources, file.sources),
        }
    )


def loosens(before: VaultConfig, after: VaultConfig) -> bool:
    """Whether `after` is anything but `before` with changes that only keep more out: a page makes none such (DR-0506),
    the CLI makes one only at the owner's console (DR-0517), and one made by hand applies only once committed."""
    return effective(before, after) != after


def _grown(before: list[str], after: list[str]) -> list[str]:
    """`after`'s values, then those of `before` that `after` dropped: what was added counts, and what was taken off
    stays."""
    return [*after, *(value for value in before if value not in after)]


def _lists(before: NeverStore, after: NeverStore) -> NeverStore:
    """Each list: the values added, and the values taken off kept. A value only drops records."""
    return before.model_copy(update={name: _grown(getattr(before, name), getattr(after, name)) for name in LISTS})


def _redaction(before: Redaction, after: Redaction) -> Redaction:
    """Credentials and phone on if either has them on; each of `before`'s patterns turned on where `after` turned it on
    and changed nothing else of it; then `after`'s new patterns. A rule only puts markers in."""
    return before.model_copy(
        update={
            "credentials": before.credentials or after.credentials,
            "phone": before.phone or after.phone,
            "patterns": _patterns(before.patterns, after.patterns),
        }
    )


def _patterns(before: list[RedactionPattern], after: list[RedactionPattern]) -> list[RedactionPattern]:
    """`before`'s patterns in their places, matched by position to `after`'s patterns under names `before` has, each
    taken where it is only turned on; then each of `after`'s patterns under a name `before` doesn't have, when it sets
    nothing beyond its name, pattern and enabled. Each rule reads only the text between markers, so one that runs last
    only adds markers; run before an existing pattern it could split that pattern's match, so where `after` puts it
    waits for the commit, as does a new pattern carrying any other key."""
    names = {p.name for p in before}
    kept = [p for p in after if p.name in names]
    out: list[RedactionPattern] = []
    for n, old in enumerate(before):
        new = kept[n] if n < len(kept) else None
        turned_on = new is not None and new.enabled and new.model_copy(update={"enabled": old.enabled}) == old
        out.append(new if turned_on and new is not None else old)
    out += [p for p in after if p.name not in names and p == type(p)(name=p.name, pattern=p.pattern, enabled=p.enabled)]
    return out


def _sources(before: Mapping[str, SourceConfig], after: Mapping[str, SourceConfig]) -> dict[str, SourceConfig]:
    """`before`'s sources that `after` still has, each made stricter where `after` is; a source `after` added waits.
    A source removed is refused at intake, held at promote and refused by ingest, scan and sync."""
    return {name: _source(old, after[name]) for name, old in before.items() if name in after}


def _source(before: SourceConfig, after: SourceConfig) -> SourceConfig:
    """The stricter sensitivity, off if either is off, the tools `after` still lists, and for a folder source the
    exclude entries added. A run takes its tools when it opens and a capture from any other marks it short; exclude is
    fnmatch with no negation, so an entry only leaves paths out. Every other key stays `before`'s."""
    update: dict[str, object] = {
        "sensitivity": max(before.sensitivity, after.sensitivity, key=RANK.__getitem__),
        "enabled": before.enabled and after.enabled,
        "tools": [tool for tool in after.tools if tool in before.tools],
    }
    if before.folder is not None:
        update["exclude"] = _grown(before.exclude, after.exclude)
    return before.model_copy(update=update)
