"""What doc_put checks of a brief beyond every doc's rules (DR-0555, DR-0558): the entity it is for, its fixed
sections, and that it names no one a never-store list holds.

A brief is `brief:<entity id>`, for an entity in `entities/`, with that entity in its `entities` list. Its body is a
`# <title>`, then `## What changed`, `## Last conversations` and `## Decisions`, in that order, each once; a section
may be empty. A further `##` section after the three is kept with a warning until the maintainer's trial week settles
the form. A brief for an entity a list holds is refused, or for one the people list names by any of its names,
aliases or contact ids; so is one whose `entities` holds such an entity, and one whose text, title or refs name a
listed person in any shape: the name as written or in the other word order, without its address, each address, and
the names, aliases and contact ids the store knows for a listed person. A brief edited by hand is held to the same
never-store checks before its take-in, its form aside. Problems name the line, never the value.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from docket.vault.anchors import trailing_anchors
from docket.vault.config import NeverStore
from docket.vault.entities import Entity, load_entities
from docket.vault.neverstore import listed, matches_text, person_listed, person_phrases, slug_listed
from docket.vault.processed import DocProblem, ProcessedDoc, key_line, line_roles

SECTIONS = ("What changed", "Last conversations", "Decisions")
_TITLE = re.compile(r"^ {0,3}# +(\S.*?)[ \t]*$")
_SECTION = re.compile(r"^ {0,3}## +(\S.*?)[ \t]*$")
NAMES_SOMEONE = "names someone a never-store list holds; leave them out"
# What stops a hand edit to a brief, on its line: the take-in and the redact pass add their own advice.
NAMES_LISTED = "names someone a never-store list holds"
HELD = "the brief is for an entity a never-store list holds; it gets no brief"
IN_ENTITIES = f"entities: an entity here {NAMES_SOMEONE}"


def entity_of(doc_id: str) -> str:
    return doc_id.partition(":")[2]


def check(doc: ProcessedDoc, root: Path, lists: NeverStore) -> tuple[list[DocProblem], list[str]]:
    """What refuses a brief, and what the put's result warns of."""
    entity_id = entity_of(doc.meta.id)
    entities = load_entities(root).entities
    found = next((e for e in entities if e.id == entity_id), None)
    problems: list[DocProblem] = []
    if found is None:
        message = f"{doc.meta.id} is for an entity that doesn't exist; a brief is brief:<entity id>"
        problems.append(DocProblem(None, message))
    elif held(found, lists):
        problems.append(DocProblem(_head_line(doc, "id"), HELD))
        return problems, []
    if entity_id not in doc.meta.entities:
        problems.append(DocProblem(None, f"entities: a brief names its entity, {entity_id}"))
    problems += _sections(doc)
    warned = [f"a section the form doesn't name yet, kept until the form settles: {title}" for title in _extra(doc)]
    problems += _in_entities(doc, entities, lists)
    problems += _screen(doc, entities, lists)
    return problems, warned


def screen(doc: ProcessedDoc, root: Path, lists: NeverStore) -> list[DocProblem]:
    """The never-store checks alone, for a brief edited by hand before its take-in: its entity, its entities list and
    the people screen, each problem on a line of the file. The form is a put's to hold, not the take-in's."""
    entities = load_entities(root).entities
    found = next((e for e in entities if e.id == entity_of(doc.meta.id)), None)
    problems = [DocProblem(_head_line(doc, "id"), HELD)] if found is not None and held(found, lists) else []
    return problems + _in_entities(doc, entities, lists) + _screen(doc, entities, lists)


def held(entity: Entity, lists: NeverStore) -> bool:
    """An entity that gets no brief: a list holds its id or a name, or the people list names it by any of its names,
    aliases or contact ids (`person_listed`, the rule the people page drops a row by)."""
    return (
        slug_listed(entity.id, lists)
        or any(listed(n, lists) is not None for n in entity.names.values())
        or person_listed(entity, lists)
    )


def _aliases(entity: Entity) -> list[str]:
    return [a.text for a in entity.aliases]


def _head_line(doc: ProcessedDoc, key: str) -> int | None:
    return key_line(doc.source.split("\n")[1 : doc.head_lines - 1], key) if doc.source else None


def _in_entities(doc: ProcessedDoc, entities: list[Entity], lists: NeverStore) -> list[DocProblem]:
    """An id in the brief's `entities` that names a listed person, or an entity a list holds, by its id alone."""
    by_id = {e.id: e for e in entities}
    named = [
        entity_id
        for entity_id in doc.meta.entities
        if slug_listed(entity_id, lists) or (entity_id in by_id and held(by_id[entity_id], lists))
    ]
    return [DocProblem(_head_line(doc, "entities"), IN_ENTITIES)] if named else []


def _headings(doc: ProcessedDoc) -> list[tuple[int, str, str]]:
    """The body's headings outside code and comments: (line in the file, level mark, title)."""
    lines = doc.body.split("\n")
    out: list[tuple[int, str, str]] = []
    for i, (line, role) in enumerate(zip(lines, line_roles(lines), strict=True)):
        if role == "skip":
            continue
        if m := _TITLE.match(line):
            out.append((doc.head_lines + 1 + i, "#", m.group(1)))
        elif m := _SECTION.match(line):
            out.append((doc.head_lines + 1 + i, "##", m.group(1)))
    return out


def _sections(doc: ProcessedDoc) -> list[DocProblem]:
    headings = _headings(doc)
    if not headings or headings[0][1] != "#":
        return [DocProblem(None, "a brief starts with a # <title> heading, then its three ## sections")]
    titles = [title for _, mark, title in headings if mark == "##"]
    if titles[: len(SECTIONS)] != list(SECTIONS):
        wanted = ", ".join(f"## {s}" for s in SECTIONS)
        has = ", ".join(titles) or "none"
        return [DocProblem(None, f"a brief's sections are {wanted}, in that order, each once; it has: {has}")]
    if any(title in SECTIONS for title in titles[len(SECTIONS) :]):
        return [DocProblem(None, "each of a brief's three sections stands once")]
    return []


def _extra(doc: ProcessedDoc) -> list[str]:
    titles = [title for _, mark, title in _headings(doc) if mark == "##"]
    return titles[len(SECTIONS) :] if titles[: len(SECTIONS)] == list(SECTIONS) else []


def hidden_lines(lines: Iterable[tuple[int, str]], root: Path, lists: NeverStore) -> set[int]:
    """The lines a page shows as hidden now (DR-0558): one that says a keyword, names a listed person by the screen
    doc_put holds a brief to, or cites a doc whose id names one. A list that gained a value after the brief was put
    hides its lines from then on."""
    phrases = _phrases(load_entities(root).entities, lists)
    return {n for n, line in lines if matches_text(line, lists) or _names(line, phrases, lists) is not None}


def _phrases(entities: list[Entity], lists: NeverStore) -> NeverStore:
    """The people list in all its shapes, with the names, aliases and contact ids of each entity the people list names
    by any of them, or a list holds by a name or an alias, read as keywords."""
    known = [
        value
        for e in entities
        if person_listed(e, lists) or any(listed(v, lists) is not None for v in (*e.names.values(), *_aliases(e)))
        for value in (*e.names.values(), *_aliases(e), *(v for vs in e.ids.values() for v in vs))
    ]
    return NeverStore(keywords=person_phrases(lists, known))


def _names(line: str, phrases: NeverStore, lists: NeverStore) -> str | None:
    """What a line names, as a problem's message: a listed person in its text, or a ref to a doc whose id names one."""
    if not phrases.keywords:
        return None
    trailing = trailing_anchors(line)
    if matches_text(trailing.text, phrases):
        return NAMES_SOMEONE
    if any(matches_text(r.doc, phrases) or slug_listed(entity_of(r.doc), lists) for r in trailing.refs):
        return f"a ref here cites a doc whose id {NAMES_SOMEONE}"
    return None


def _screen(doc: ProcessedDoc, entities: list[Entity], lists: NeverStore) -> list[DocProblem]:
    """Every line, the title among them, and every ref's doc id, held to the people list in all its shapes."""
    phrases = _phrases(entities, lists)
    found = ((n, _names(line, phrases, lists)) for n, line in doc.lines())
    return [DocProblem(n, message, "") for n, message in found if message is not None]
