"""The vocabulary prompt a Hungarian recording gets (DR-0569): the names Whisper should spell as the vault does.

The names are the family's people (its author, recipients and speakers) resolved to entities, those people's related
projects, and the entities a record of the family names, by their names and `asr` aliases. Each is screened first: one a
never-store list holds, or one the redaction rules would change, is left out, since Whisper can repeat a prompt's words,
and a never-store value in the transcript would drop it and withhold the recording's family. Up to 40 names; the prompt
goes only to the local worker and is never logged.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from docket.vault import neverstore
from docket.vault.config import VaultConfig
from docket.vault.entities import Entity
from docket.vault.records import RawRecord
from docket.vault.redact import Redactor

# The prompt's lead, by the language it is for: only Hungarian gets one (design §8).
LEAD = {"hu": "Résztvevők és témák:"}
MAX_NAMES = 40
# About 120 tokens: well under the 224 Whisper takes, since a long list confuses it.
MAX_CHARS = 480


def _forms(entity: Entity, *, asr: bool = True) -> list[str]:
    names = [n for n in entity.names.values() if n.strip()]
    return names + [a.text for a in entity.aliases if a.kind == "asr" or not asr]


def _says(folded: str, form: str) -> bool:
    found = neverstore.fold(form).strip()
    return bool(found) and re.search(rf"(?<!\w){re.escape(found)}(?!\w)", folded) is not None


def vocabulary(
    language: str | None, records: Sequence[RawRecord], entities: Iterable[Entity], config: VaultConfig
) -> str | None:
    """The prompt for a family's records, or None: a language with no lead, or no name left after the screen."""
    lead = LEAD.get(language or "")
    if lead is None:
        return None
    known = list(entities)
    people = {neverstore.fold(p) for r in records for p in (r.author, *r.to) if p}
    people |= {neverstore.fold(s.speaker) for r in records for s in r.segments or [] if s.speaker}
    said = neverstore.fold(" ".join(f"{r.title or ''} {r.text}" for r in records))
    chosen: list[Entity] = [e for e in known if any(neverstore.fold(f) in people for f in _forms(e, asr=False))]
    related = {i for e in chosen for i in e.related.get("project", ())}
    chosen += [e for e in known if e.id in related and e.type == "project"]
    chosen += [e for e in known if any(_says(said, f) for f in _forms(e, asr=False))]
    redactor = Redactor.from_config(config.redaction)
    names: list[str] = []
    seen: set[str] = set()
    for entity in chosen:
        for form in _forms(entity):
            key = neverstore.fold(form)
            if key in seen or not _fits(form, config, redactor):
                continue
            seen.add(key)
            names.append(form.strip())
    names = names[:MAX_NAMES]
    while names and len(f"{lead} {', '.join(names)}.") > MAX_CHARS:
        names.pop()
    return f"{lead} {', '.join(names)}." if names else None


def _fits(name: str, config: VaultConfig, redactor: Redactor) -> bool:
    """Whether a name may go in: no never-store list holds it, and redaction leaves it as it is."""
    lists = config.never_store
    if neverstore.listed(name, lists) is not None or neverstore.matches_text(name, lists):
        return False
    return redactor.text(name) == name
