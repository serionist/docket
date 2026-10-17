"""What doc_put checks of a brief beyond every doc's rules (DR-0555, DR-0558): the entity it is for, its fixed
sections, and that it names no one a never-store list holds.

A brief is `brief:<entity id>`, for an entity in `entities/`, with that entity in its `entities` list. Its body is a
`# <title>`, then `## What changed`, `## Last conversations` and `## Decisions`, in that order, each once; a section
may be empty. A further `##` section after the three is kept with a warning until the maintainer's trial week settles
the form. A brief for an entity a list holds is refused, and so is one whose text, title or refs name a listed person
in any shape: the name as written or in the other word order, without its address, each address, and the names and
aliases the store knows for a listed entity. Problems name the line, never the value.
"""

from __future__ import annotations

import re
from pathlib import Path

from docket.vault.anchors import trailing_anchors
from docket.vault.config import NeverStore
from docket.vault.entities import Entity, load_entities
from docket.vault.neverstore import listed, matches_text, person_phrases, slug_listed
from docket.vault.processed import DocProblem, ProcessedDoc, line_roles

SECTIONS = ("What changed", "Last conversations", "Decisions")
_TITLE = re.compile(r"^ {0,3}# +(\S.*?)[ \t]*$")
_SECTION = re.compile(r"^ {0,3}## +(\S.*?)[ \t]*$")
NAMES_SOMEONE = "names someone a never-store list holds; leave them out"


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
    elif _held(found, lists):
        problems.append(DocProblem(None, "the brief is for an entity a never-store list holds; it gets no brief"))
        return problems, []
    if entity_id not in doc.meta.entities:
        problems.append(DocProblem(None, f"entities: a brief names its entity, {entity_id}"))
    problems += _sections(doc)
    warned = [f"a section the form doesn't name yet, kept until the form settles: {title}" for title in _extra(doc)]
    problems += _screen(doc, entities, lists)
    return problems, warned


def _held(entity: Entity, lists: NeverStore) -> bool:
    return slug_listed(entity.id, lists) or any(listed(n, lists) is not None for n in entity.names.values())


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


def _screen(doc: ProcessedDoc, entities: list[Entity], lists: NeverStore) -> list[DocProblem]:
    """Every line, the title among them, and every ref's doc id, held to the people list in all its shapes."""
    known = [
        value
        for e in entities
        if any(listed(v, lists) is not None for v in (*e.names.values(), *(a.text for a in e.aliases)))
        for value in (*e.names.values(), *(a.text for a in e.aliases))
    ]
    phrases = NeverStore(keywords=person_phrases(lists, known))
    if not phrases.keywords:
        return []
    problems: list[DocProblem] = []
    for n, line in doc.lines():
        trailing = trailing_anchors(line)
        if matches_text(trailing.text, phrases):
            problems.append(DocProblem(n, NAMES_SOMEONE, ""))
        elif any(matches_text(r.doc, phrases) or slug_listed(entity_of(r.doc), lists) for r in trailing.refs):
            problems.append(DocProblem(n, f"a ref here cites a doc whose id {NAMES_SOMEONE}", ""))
    return problems
