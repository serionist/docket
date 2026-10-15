"""The gate's rules for derived text: what a doc must not say, and redaction that keeps its lines and anchors.

Every doc Docket commits passes here first (DR-0195): a put, a take-in of a hand edit, the notes import and `docket
doc redact`. A doc is redacted line by line (DR-0197): each content line's trailing anchors are left out and put back
byte for byte, the text parts are redacted joined by line breaks, as a transcript's segments are, and a line a match
runs into becomes its marker whole. The never-store keywords are then held to the text as it would be committed
(DR-0196), the built-in rules' markers left out, and a match refuses the doc, naming lines and the list, never the
keyword. The frontmatter is never redacted: its values are ids, locators and the writer, as a record's ids are.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, replace

from docket.vault.anchors import normalize_newlines, trailing_anchors
from docket.vault.config import NeverStore
from docket.vault.neverstore import matches_text
from docket.vault.processed import DocProblem, DocRefusedError, ProcessedDoc, key_line, line_roles
from docket.vault.redact import BUILTIN_NAMES, MARKER, Redactor

LEGACY = "legacy-note"
SAYS = "says something on the never-store keywords list"
LEAVE_IT_OUT = f"{SAYS}; leave it out"
PATTERN_SAYS = (
    "a redaction marker here names something on the never-store keywords list; give that pattern in vault.yaml "
    "another name"
)
# The frontmatter keys this Docket writes, and of them the ones whose values are screened in any doc.
KNOWN = frozenset({"id", "type", "rev", "covers", "by", "supersedes", "entities", "old_paths", "import_hash"})
SCREENED = frozenset({"id", "entities", "supersedes", "old_paths"})
_KEY = re.compile(r"^([A-Za-z_][\w-]*)[ \t]*:(.*)$")


@dataclass(frozen=True)
class Redacted:
    """A body redacted: the text, the markers added per rule, and the indexes of the lines that gained one."""

    body: str
    counts: Counter[str]
    lines: list[int]


def split(doc_type: str, lines: list[str]) -> list[tuple[str, str]]:
    """Each line as its text and its trailing run of anchors with the spaces before it. Only a content line of a doc
    that has anchors keeps a run, and only when every token in it parses; any other line is all text."""
    roles = line_roles(lines) if doc_type != LEGACY else ["free"] * len(lines)
    out: list[tuple[str, str]] = []
    for line, role in zip(lines, roles, strict=True):
        trailing = trailing_anchors(line) if role == "content" else None
        if trailing is not None and trailing.anchors and not trailing.errors:
            out.append((line[: len(trailing.text)], line[len(trailing.text) :]))
        else:
            out.append((line, ""))
    return out


def redact(doc_type: str, body: str, redactor: Redactor) -> Redacted:
    """The body with every rule the settings turn on applied, its line count and its anchors as they were."""
    parts = split(doc_type, normalize_newlines(body).split("\n"))
    counts = Counter[str]()
    said = redactor.segment_texts([text for text, _ in parts], counts)
    lines: list[str] = []
    changed: list[int] = []
    for i, ((text, run), new) in enumerate(zip(parts, said, strict=True)):
        if new != text:
            changed.append(i)
        # An anchor-only line a match ran into: without the space its anchors would read as text.
        lines.append(f"{new} {run}" if run and not text and new else new + run)
    return Redacted("\n".join(lines), counts, changed)


def _rule(marker: str) -> str:
    return marker[len("[redacted:") : -1]


def _mask(text: str, builtin_only: bool) -> str:
    """The text with redaction markers made spaces: the built-in rules' only, whose words are Docket's own, or all."""
    return MARKER.sub(lambda m: m.group(0) if builtin_only and _rule(m.group(0)) not in BUILTIN_NAMES else " ", text)


def _keywords(lists: NeverStore) -> bool:
    return any(k.strip() for k in lists.keywords)


def body_hits(doc_type: str, body: str, lists: NeverStore) -> list[tuple[int, str]]:
    """The lines of a body (indexes) that say something on the keywords list, each with what to tell the writer. A
    keyword that runs over a line break names both lines."""
    if not _keywords(lists):
        return []

    def says(text: str) -> bool:
        return matches_text(text, lists)

    def told(text: str) -> str:
        # When the words alone don't say it, a user pattern's marker does: its name holds the keyword.
        return LEAVE_IT_OUT if says(_mask(text, False)) else PATTERN_SAYS

    texts = [_mask(text, True) for text, _ in split(doc_type, normalize_newlines(body).split("\n"))]
    hits = {i: told(text) for i, text in enumerate(texts) if text.strip() and says(text)}
    joined = "\n".join(texts)
    if not hits and says(joined):
        for i in range(len(texts) - 1):
            pair = f"{texts[i]}\n{texts[i + 1]}"
            if says(pair):
                hits.setdefault(i, told(pair))
                hits.setdefault(i + 1, told(pair))
        # Over three lines or more: no pair says it, so every line with text is named.
        hits = hits or {i: told(joined) for i, text in enumerate(texts) if text.strip()}
    return sorted(hits.items())


def meta_hits(doc: ProcessedDoc, head: list[str], lists: NeverStore) -> list[DocProblem]:
    """The frontmatter keys whose values say something on the keywords list: the id's slug, entities, the slugs in
    supersedes, and old_paths. `head` is the frontmatter's lines, for each key's line number."""
    if not _keywords(lists):
        return []
    meta = doc.meta
    values = {
        "id": [meta.id.partition(":")[2]],
        "entities": meta.entities,
        "supersedes": [other.partition(":")[2] for other in meta.supersedes],
        "old_paths": meta.old_paths,
    }
    return [
        DocProblem(key_line(head, key), f"{key}: {LEAVE_IT_OUT}")
        for key, found in values.items()
        if any(matches_text(value, lists) for value in found)
    ]


def check(doc: ProcessedDoc, head: list[str], redactor: Redactor, lists: NeverStore) -> tuple[ProcessedDoc, Redacted]:
    """A doc for a put: redacted, then screened as it would be committed. A match raises DocRefusedError with the
    never-store problems alone, so nothing else about the doc, and never the keyword, goes back. `head` is the
    frontmatter's lines in the text as given."""
    redacted = redact(doc.meta.type, doc.body, redactor)
    first = doc.head_lines + 1
    problems = meta_hits(doc, head, lists)
    problems += [DocProblem(first + i, message) for i, message in body_hits(doc.meta.type, redacted.body, lists)]
    if problems:
        raise DocRefusedError(problems)
    return replace(doc, body=redacted.body), redacted


@dataclass(frozen=True)
class HandEdit:
    """A doc file as a take-in or the pass would commit it: its own frontmatter as written and its body redacted,
    what was redacted, and what stops it, as (line in the file, what)."""

    text: str
    redacted: Redacted
    stops: list[tuple[int, str]]


def _parts(text: str) -> tuple[list[str], list[str]]:
    """A doc file's frontmatter lines, both --- lines included, and its body lines."""
    lines = normalize_newlines(text.removeprefix("﻿")).split("\n")
    close = lines.index("---", 1)
    return lines[: close + 1], lines[close + 1 :]


def _values(head: list[str]) -> list[tuple[int, str, str, str]]:
    """Each frontmatter line after the first --- as (line number, the line, its key, the text to check): a comment line
    whole, a key's value, or a list item or continuation under the key above it."""
    out: list[tuple[int, str, str, str]] = []
    key = ""
    for n, line in enumerate(head[1:-1], start=2):
        stripped = line.strip()
        m = _KEY.match(line)
        if stripped.startswith("#"):
            out.append((n, line, "#", stripped))
        elif m is not None:
            key = m.group(1)
            out.append((n, line, key, m.group(2)))
        elif stripped:
            out.append((n, line, key, stripped))
    return out


def hand_edit(doc_type: str, text: str, last: str | None, redactor: Redactor, lists: NeverStore) -> HandEdit:
    """A hand edit to commit: its body redacted, its frontmatter left as written. What stops it: a body line on the
    keywords list, and in the frontmatter, which is committed as it stands, a comment, a key this Docket doesn't
    know, a screened key, or a line the edit changed from the last rev (every line when there is none) that says a
    keyword, or holds what redaction would hide. No marker is ever written into YAML, so redaction there refuses."""
    head, body = _parts(text)
    before = set(_parts(last)[0]) if last is not None else set[str]()
    redacted = redact(doc_type, "\n".join(body), redactor)
    stops: list[tuple[int, str]] = []
    for n, line, key, value in _values(head):
        if key == "rev":
            continue
        hand = key == "#" or key not in KNOWN or line not in before
        probe = value.partition(":")[2] if key in ("id", "supersedes") else value
        if (hand or key in SCREENED) and _keywords(lists) and matches_text(probe, lists):
            stops.append((n, SAYS))
        elif hand:
            counts = Counter[str]()
            redactor.text(value, counts)
            if counts:
                rules = ", ".join(sorted(counts))
                stops.append((n, f"holds what the {rules} rule{'s' if len(counts) > 1 else ''} would redact"))
    first = len(head) + 1
    stops += [(first + i, SAYS) for i, _ in body_hits(doc_type, redacted.body, lists)]
    return HandEdit("\n".join([*head, redacted.body]), redacted, stops)
