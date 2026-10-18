"""The vocabulary prompt a Hungarian recording gets (DR-0569): the names Whisper should spell as the vault does.

The names are the meeting's entities as `meeting.py` picks them (the family's people resolved to entities, those
people's related projects, and the entities a record of the family names), by their names and `asr` aliases, an entity
a never-store list holds left out whole, as briefs and the people page leave it out (DR-0558). A single form the
redaction rules would change is left out too, since Whisper can repeat a prompt's words, and a never-store value in the
transcript would drop it and withhold the recording's family. Up to 40 names; the prompt goes only to the local worker
and is never logged.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from docket import meeting
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


def vocabulary(
    language: str | None, records: Sequence[RawRecord], entities: Iterable[Entity], config: VaultConfig
) -> str | None:
    """The prompt for a family's records, or None: a language with no lead, or no name left after the screen."""
    lead = LEAD.get(language or "")
    if lead is None:
        return None
    redactor = Redactor.from_config(config.redaction)
    names: list[str] = []
    seen: set[str] = set()
    for entity in meeting.entities(records, entities, config):
        for form in meeting.forms(entity):
            key = neverstore.fold(form)
            if key in seen or not _fits(form, redactor):
                continue
            seen.add(key)
            names.append(form.strip())
    names = names[:MAX_NAMES]
    while names and len(f"{lead} {', '.join(names)}.") > MAX_CHARS:
        names.pop()
    return f"{lead} {', '.join(names)}." if names else None


def _fits(name: str, redactor: Redactor) -> bool:
    """Whether a name of an entity no list holds may go in: redaction leaves it as it is."""
    return redactor.text(name) == name
