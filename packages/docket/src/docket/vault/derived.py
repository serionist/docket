"""The gate's rules for derived text: what a doc must not say, and redaction that keeps its lines and anchors.

Every doc Docket commits passes here first (DR-0195): a put, a take-in of a hand edit, the notes import and `docket
doc redact`. A doc is redacted line by line (DR-0197): each content line's trailing anchors are left out and put back
byte for byte, the text parts are redacted joined by line breaks, as a transcript's segments are, and a line a match
runs into becomes its marker whole. The never-store keywords are held to the text as written, as intake holds a
record, and as it would be committed (DR-0523), the built-in rules' markers left out, and a match refuses the doc,
naming lines and the list, never the keyword. The frontmatter is never redacted: its values are ids, locators and the
writer, as a record's ids are.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, replace
from itertools import pairwise

from docket.vault.anchors import UNREAD, is_unread, normalize_newlines, trailing_anchors
from docket.vault.config import NeverStore
from docket.vault.neverstore import matches_text
from docket.vault.processed import (
    DECISION_KEYS,
    DOC_TYPES,
    DocProblem,
    DocRefusedError,
    ProcessedDoc,
    key_line,
    line_roles,
)
from docket.vault.redact import BUILTIN_NAMES, MARKER, Redactor

LEGACY = "legacy-note"
SAYS = "says something on the never-store keywords list"
LEAVE_IT_OUT = f"{SAYS}; leave it out"
PATTERN_SAYS = (
    "a redaction marker here names something on the never-store keywords list; give that pattern in vault.yaml "
    "another name"
)
# The frontmatter keys this Docket writes, and of them the ones whose values are screened in any doc.
KNOWN = frozenset(
    {"id", "type", "rev", "covers", "by", "supersedes", "entities", "old_paths", "import_hash", *DECISION_KEYS}
)
SCREENED = frozenset({"id", "entities", "supersedes", "old_paths", "subject", "replaces", "contradicts"})
_KEY = re.compile(r"^([A-Za-z_][\w-]*)[ \t]*:(.*)$")
# The rev line a take-in rewrites whole: `rev:` and a number, nothing else.
_REV = re.compile(r"rev:[ \t]*\d*[ \t]*")
# A doc type before a slug, `minutes:`, is Docket's own word, so the keyword screen leaves it out.
_TYPED = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(t) for t in DOC_TYPES) + r"):")


@dataclass(frozen=True)
class Redacted:
    """A body redacted: the text, the markers added per rule, and the indexes of the lines that gained one."""

    body: str
    counts: Counter[str]
    lines: list[int]


def split(doc_type: str, lines: list[str]) -> list[tuple[str, str]]:
    """Each line as its text and its trailing run of anchors and refs with the spaces before it. A content line of a
    doc that has anchors keeps its run whether or not every token parses, so redaction never writes inside one
    (DR-0541); a legacy note's lines and every other line are all text."""
    roles = line_roles(lines) if doc_type != LEGACY else ["free"] * len(lines)
    out: list[tuple[str, str]] = []
    for line, role in zip(lines, roles, strict=True):
        trailing = trailing_anchors(line) if role == "content" else None
        if trailing is not None and (trailing.anchors or trailing.refs or trailing.failed):
            out.append((line[: len(trailing.text)], line[len(trailing.text) :]))
        else:
            out.append((line, ""))
    return out


def run_stops(doc_type: str, lines: list[str]) -> list[tuple[int, str]]:
    """The content lines (indexes) whose trailing run holds a token this Docket doesn't parse, each with what to say:
    a write that would redact the doc leaves it whole instead, so nothing in such a line reaches a commit unredacted
    and no anchor's bytes are rewritten (DR-0541)."""
    if doc_type == LEGACY:
        return []
    out: list[tuple[int, str]] = []
    for i, (line, role) in enumerate(zip(lines, line_roles(lines), strict=True)):
        if role != "content":
            continue
        trailing = trailing_anchors(line)
        if not trailing.failed:
            continue
        token, error = trailing.failed[0], trailing.errors[0]
        if is_unread(token):
            out.append((i, f"holds {token}, {UNREAD}"))
        else:
            out.append((i, f"has an anchor that doesn't parse ({error}); fix the line"))
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


def keyword_lines(doc_type: str, written: str, committed: str, lists: NeverStore) -> list[tuple[int, str]]:
    """The body lines that say something on the keywords list as written or as they would be committed (DR-0523):
    intake screens a record before it redacts it, so a keyword inside what a rule redacts still refuses the doc. A
    line named both ways tells what the text as written says, which the writer can act on."""
    hits = dict(body_hits(doc_type, committed, lists))
    hits.update(body_hits(doc_type, written, lists))
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
    """A doc for a put: redacted, then screened as written and as it would be committed. A match raises DocRefusedError
    with the never-store problems alone, so nothing else about the doc, and never the keyword, goes back. `head` is
    the frontmatter's lines in the text as given."""
    redacted = redact(doc.meta.type, doc.body, redactor)
    first = doc.head_lines + 1
    problems = meta_hits(doc, head, lists)
    # `by` is the frontmatter's one free text: it isn't screened for keywords (it names the writer, and `sync` would
    # refuse every doc), but what redaction would hide in it refuses the doc, since no marker goes into YAML.
    rules = _rules_added(doc.meta.by, redactor.text(doc.meta.by))
    if rules:
        plural = "s" if len(rules) > 1 else ""
        message = f"by: holds what the {', '.join(rules)} rule{plural} would redact; say who wrote it, nothing more"
        problems.append(DocProblem(key_line(head, "by"), message))
    said = keyword_lines(doc.meta.type, doc.body, redacted.body, lists)
    problems += [DocProblem(first + i, message) for i, message in said]
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


@dataclass(frozen=True)
class _Line:
    """A frontmatter line after the first ---: its number in the file, the line, the key it sits under (`#` for a
    comment line), and what the keyword screen reads of it."""

    n: int
    line: str
    key: str
    probe: str


def _lines(head: list[str]) -> list[_Line]:
    """Each frontmatter line with its key: a comment line, a key's own line, or a list item or continuation under the
    key above it. The keyword screen reads a comment or a line under a key this Docket doesn't know whole, the key's
    name included (`dora_example: yes`), and a known key's value, its `<type>:` prefixes left out, since a doc type is
    Docket's own word."""
    out: list[_Line] = []
    key = ""
    for n, line in enumerate(head[1:-1], start=2):
        stripped = line.strip()
        m = _KEY.match(line)
        if stripped.startswith("#"):
            out.append(_Line(n, line, "#", line))
            continue
        if m is not None:
            key = m.group(1)
            value = m.group(2)
        elif stripped:
            value = stripped
        else:
            continue
        out.append(_Line(n, line, key, line if key not in KNOWN else _TYPED.sub(" ", value)))
    return out


def _runs(lines: list[_Line]) -> list[list[_Line]]:
    """The lines in runs of neighbours: a label and its value, or a keyword's words, can sit on two lines in a row."""
    runs: list[list[_Line]] = []
    for line in lines:
        if runs and runs[-1][-1].n + 1 == line.n:
            runs[-1].append(line)
        else:
            runs.append([line])
    return runs


def _rules_added(before: str, after: str) -> list[str]:
    added = Counter(_rule(m.group(0)) for m in MARKER.finditer(after))
    added.subtract(_rule(m.group(0)) for m in MARKER.finditer(before))
    return sorted(rule for rule, n in added.items() if n > 0)


def hand_edit(doc_type: str, text: str, last: str | None, redactor: Redactor, lists: NeverStore) -> HandEdit:
    """A hand edit to commit: its body redacted, its frontmatter left as written. What stops it: a body line on the
    keywords list, as written or redacted, a content line whose anchors hold a token this Docket doesn't parse
    (DR-0541), and in the frontmatter, which is committed as it stands, a comment, a key
    this Docket doesn't know, a screened key, or a line the edit changed from the last rev (every line when there is
    none) that says a keyword, or holds what redaction would hide. Lines are read whole and in runs of neighbours, as a
    body is, so a label is its key and a value on the next line still counts. No marker is ever written into YAML, so
    redaction there refuses. The one rev line the take-in rewrites is left out; any other rev line is read like a
    changed one."""
    head, body = _parts(text)
    before = set(_parts(last)[0]) if last is not None else set[str]()
    redacted = redact(doc_type, "\n".join(body), redactor)
    lines = _lines(head)
    # The rev line the take-in replaces: the first that starts `rev:`, when it holds a number and nothing else.
    first_rev = next((line for line in lines if line.line.startswith("rev:")), None)
    lines = [line for line in lines if not (line is first_rev and _REV.fullmatch(line.line))]
    hand = [line for line in lines if line.key in ("#", "rev") or line.key not in KNOWN or line.line not in before]
    screened = [line for line in lines if line in hand or line.key in SCREENED]
    stops: dict[int, str] = {}
    if _keywords(lists):
        stops.update((line.n, SAYS) for line in screened if matches_text(line.probe, lists))
        for run in _runs(hand):
            for one, two in pairwise(run):
                if matches_text(f"{one.probe}\n{two.probe}", lists):
                    stops.setdefault(one.n, SAYS)
                    stops.setdefault(two.n, SAYS)
    for run in _runs(hand):
        said = redactor.segment_texts([line.line for line in run], Counter[str]())
        for line, new in zip(run, said, strict=True):
            rules = _rules_added(line.line, new)
            if rules:
                plural = "s" if len(rules) > 1 else ""
                stops.setdefault(line.n, f"holds what the {', '.join(rules)} rule{plural} would redact")
    first = len(head) + 1
    written = "\n".join(body)
    stops.update((first + i, SAYS) for i, _ in keyword_lines(doc_type, written, redacted.body, lists))
    for i, what in run_stops(doc_type, body):
        stops.setdefault(first + i, what)
    return HandEdit("\n".join([*head, redacted.body]), redacted, sorted(stops.items()))
