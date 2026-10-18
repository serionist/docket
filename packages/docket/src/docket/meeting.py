"""A recording's meeting as the entities know it (DR-0569, DR-0583): who and what it is about, for the vocabulary prompt
Whisper gets and for the names a reconcile's spots are matched against.

- The meeting's entities: the family's people (its author, recipients and speakers) resolved to entities, those
  people's related projects, and the entities a record of the family names by a name or an alias.
- The glossary: every active entity that isn't a person, as the rendered glossary holds them.

An entity a never-store list holds by any of its names, aliases or contact ids, or by its id, is left out of both whole,
its unlisted nickname too, as briefs and the people page leave it out (DR-0558).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from docket.vault import neverstore
from docket.vault.config import VaultConfig
from docket.vault.entities import Entity
from docket.vault.records import RawRecord


def forms(entity: Entity, *, asr: bool = True) -> list[str]:
    """An entity's names, then its aliases: only the `asr` ones with `asr`, every one without."""
    names = [n for n in entity.names.values() if n.strip()]
    return names + [a.text for a in entity.aliases if a.kind == "asr" or not asr]


# A word as a form and the records' text are compared: folded, so case and accents never decide.
WORD = re.compile(r"\w+")


def _words(text: str) -> tuple[str, ...]:
    return tuple(WORD.findall(neverstore.fold(text)))


def entities(records: Sequence[RawRecord], known: Iterable[Entity], config: VaultConfig) -> list[Entity]:
    """The meeting's entities, each once: its people first, then their projects, then those its records name. The
    records' text is folded and cut into runs of words once, and each form is looked up among the runs of its length,
    so a vault of a thousand entities costs what a few do."""
    known = list(known)
    people = {neverstore.fold(p) for r in records for p in (r.author, *r.to) if p}
    people |= {neverstore.fold(s.speaker) for r in records for s in r.segments or [] if s.speaker}
    chosen: list[Entity] = [e for e in known if any(neverstore.fold(f) in people for f in forms(e, asr=False))]
    related = {i for e in chosen for i in e.related.get("project", ())}
    chosen += [e for e in known if e.id in related and e.type == "project"]
    named = {e.id: [w for f in forms(e, asr=False) if (w := _words(f))] for e in known}
    said = _runs(" ".join(f"{r.title or ''} {r.text}" for r in records), {len(w) for ws in named.values() for w in ws})
    chosen += [e for e in known if any(w in said for w in named[e.id])]
    return [e for e in {e.id: e for e in chosen}.values() if not listed(e, config)]


def _runs(text: str, lengths: set[int]) -> set[tuple[str, ...]]:
    """Every run of words a text holds, of the lengths asked."""
    words = _words(text)
    return {words[i : i + n] for n in lengths for i in range(len(words) - n + 1)}


def glossary(known: Iterable[Entity], config: VaultConfig) -> list[Entity]:
    """The glossary's terms: every active entity that isn't a person and that no list holds."""
    return [e for e in known if e.type != "person" and e.status == "active" and not listed(e, config)]


def listed(entity: Entity, config: VaultConfig) -> bool:
    """Whether a never-store list holds an entity by any of its names, aliases or contact ids, or by its id: every
    form of it stays out then, the unlisted nickname too."""
    lists = config.never_store
    values = [*entity.names.values(), *(a.text for a in entity.aliases), *(v for vs in entity.ids.values() for v in vs)]
    return (
        neverstore.person_listed(entity, lists)
        or neverstore.slug_listed(entity.id, lists)
        or any(neverstore.listed(v, lists) is not None for v in values)
    )
