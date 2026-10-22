"""What doc_put checks of a decision, how every read takes one, and what a brief's Decisions section may cite
(DR-0563, DR-0782, DR-0565).

A decision is `decision:<yyyy-mm-dd>-<words>`, on one `subject`: a project, topic or stream entity that `entities` holds
too. Every put writes it proposed, its title `# Proposed: <the decision>`. Only the Briefs page or `docket decision set`
confirms or rejects one, and moves its `replaces` into `supersedes` (DR-0782). `replaces` and `contradicts` name
confirmed, current decisions on the same subject. A confirmed or rejected decision is final: it takes no new rev, and
every read takes it whole from its last commit. A proposed one reads as on disk with its status and links from its last
commit, so an edit no commit holds never makes one read as decided. Never-store is as for briefs (DR-0558). Problems
name the line, never a listed value.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from docket.errors import DocketError
from docket.vault import brief_form
from docket.vault.anchors import format_anchor, normalize_newlines, trailing_anchors
from docket.vault.config import NeverStore
from docket.vault.entities import load_entities
from docket.vault.neverstore import matches_text
from docket.vault.processed import DocProblem, ProcessedDoc, line_roles, parse_doc

FINAL = ("confirmed", "rejected")
SUBJECT_TYPES = ("project", "topic", "stream")
# What a decision's title says before the decision while it isn't confirmed, so no reader takes it as decided.
PREFIX = {"proposed": "Proposed: ", "rejected": "Rejected: "}
EDITED = "edited on disk, not taken in"
# What the decisions tool, the Briefs page and `read` say of a final decision a forget left with no evidence (DR-0580).
EVIDENCE_FORGOTTEN = "its evidence was forgotten"
NOT_CONFIRMED = "a Decisions line cites no confirmed decision"
CITE_ONE = (
    "a Decisions line cites one current decision and nothing else, as - <its title> <its cite>; the decisions tool "
    "gives both"
)
# What stops a hand edit to a decision, on its line: the take-in and the redact pass add their own advice.
LINKS_EDIT = "changes the decision's status or links, which only the Briefs page or docket decision set writes"
TITLE_EDIT = "takes Proposed: from the title of a decision only the Briefs page or docket decision set confirms"
FINAL_EDIT = "changes a decision that is {status}, which is final: a change is a new decision that replaces it"
NAMES_SOMEONE = "an id here names someone a never-store list holds; leave them out"
DECISIONS_EDIT = "is a Decisions line that doesn't read as a current decision's title with its cite"
# What `read` and a search hit say beside a brief's Decisions line that cites no confirmed decision.
NOT_A_DECISION = "not a confirmed decision"
_ID = re.compile(r"^decision:\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*$")
_TITLE = re.compile(r"^ {0,3}# +(\S.*?)[ \t]*$")
_HEADING = re.compile(r"^ {0,3}(#{1,2})(?:[ \t]+(.*?))?[ \t]*$")


@dataclass(frozen=True)
class Standing:
    """Where a decision stands, as a brief, the tool and the page read it: its subject, its status, whether it is
    current (confirmed, and no confirmed decision supersedes it), which one does, its title without the prefix, the
    title's line, and its rev."""

    id: str
    subject: str
    status: str
    current: bool
    superseded_by: str | None
    title: str
    title_line: int | None
    rev: int


def heading(doc: ProcessedDoc) -> tuple[int, str] | None:
    """The doc's `# ` title, outside code and comments: its line in the file and its text."""
    lines = doc.body.split("\n")
    for i, (line, role) in enumerate(zip(lines, line_roles(lines), strict=True)):
        if role != "skip" and (m := _TITLE.match(line)):
            return doc.head_lines + 1 + i, m.group(1)
    return None


def bare(title: str) -> str:
    """A decision's title without what it says before it while it isn't confirmed."""
    return next((title[len(p) :] for p in PREFIX.values() if title.startswith(p)), title)


def standings(decisions: Iterable[ProcessedDoc]) -> dict[str, Standing]:
    """Each decision's standing, by id. A decision is superseded only by a confirmed one, the first by id."""
    docs = [d for d in decisions if d.meta.type == "decision"]
    by: dict[str, str] = {}
    for d in sorted(docs, key=lambda d: d.meta.id):
        if d.meta.status == "confirmed":
            for target in d.meta.supersedes:
                by.setdefault(target, d.meta.id)
    out: dict[str, Standing] = {}
    for d in docs:
        found = heading(d)
        status = d.meta.status or "proposed"
        out[d.meta.id] = Standing(
            id=d.meta.id,
            subject=d.meta.subject or "",
            status=status,
            current=status == "confirmed" and d.meta.id not in by,
            superseded_by=by.get(d.meta.id),
            title=bare(found[1]) if found else d.meta.id,
            title_line=found[0] if found else None,
            rev=d.meta.rev,
        )
    return out


# -- every read -------------------------------------------------------------------------------------------------------


def committed_doc(text: str | None) -> ProcessedDoc | None:
    """A doc as its last commit holds it, or None when no commit does or it doesn't read."""
    if text is None:
        return None
    try:
        return parse_doc(text, lenient=True)
    except DocketError:
        return None


def as_committed(on_disk: ProcessedDoc, committed: ProcessedDoc | None) -> ProcessedDoc:
    """A decision as every read takes it (DR-0782): a confirmed or rejected one whole from its last commit; a proposed
    one as on disk, its status and links from that commit; one no commit holds, proposed with nothing it supersedes,
    replaces or contradicts. Its title says Proposed: whatever the file says. A file that says otherwise is read as
    Docket would write it, so no read, its text included, shows a proposed decision as decided. Any other doc as it
    is."""
    if on_disk.meta.type != "decision":
        return on_disk
    last = committed.meta if committed is not None and committed.meta.type == "decision" else None
    if committed is not None and last is not None and last.status in FINAL:
        return committed
    links = {
        "status": last.status if last is not None else "proposed",
        "supersedes": list(last.supersedes) if last is not None else [],
        "replaces": list(last.replaces) if last is not None else [],
        "contradicts": list(last.contradicts) if last is not None else [],
    }
    meta = on_disk.meta
    titled = _titled(on_disk, "proposed")
    if all(getattr(meta, key) == value for key, value in links.items()) and titled is on_disk:
        return on_disk
    return parse_doc(replace(titled, meta=meta.model_copy(update=links)).render(), lenient=True)


def _titled(doc: ProcessedDoc, status: str) -> ProcessedDoc:
    """The doc with its title saying what its status says before the decision: Proposed:, Rejected:, or nothing."""
    found = heading(doc)
    if found is None:
        return doc
    wanted = PREFIX.get(status, "") + bare(found[1])
    if found[1] == wanted:
        return doc
    lines = doc.body.split("\n")
    at = found[0] - doc.head_lines - 1
    lines[at] = lines[at].replace(found[1], wanted, 1)
    return replace(doc, body="\n".join(lines))


# -- the user's click ----------------------------------------------------------------------------------------------

# What the Briefs page buttons ask for: a candidate is confirmed or rejected; one that contradicts a decision takes one
# of three picks instead, the new one replacing it, the old one standing, or not a contradiction (DR-0781).
PICKS = ("confirm", "reject", "replace", "stand", "unrelated")
CONFIRMING = ("confirm", "replace", "unrelated")
THREE = ("replace", "stand", "unrelated")


class MovedError(DocketError):
    """The decision, or a decision it names, moved since the page showed it: nothing is written (409)."""


def pick_problem(doc: ProcessedDoc, pick: str) -> str | None:
    """What makes a pick wrong for this candidate: a plain confirm of one that contradicts a decision, or one of the
    three picks for one that contradicts none."""
    if pick not in PICKS:
        return f"pick is {', '.join(PICKS)}, not {pick!r}"
    if pick == "confirm" and doc.meta.contradicts:
        return "a candidate that contradicts a decision takes one of three picks: replace, stand or unrelated"
    if pick in THREE and not doc.meta.contradicts:
        return f"{pick} is a pick for a candidate that contradicts a decision; this one contradicts none"
    return None


def status_rev(doc: ProcessedDoc, pick: str, by: str) -> str:
    """The candidate's next rev as the user's click writes it (DR-0782): confirmed, its `replaces` moved into
    `supersedes`, and with replace its `contradicts` too; or rejected, its links kept as the record of what it said.
    Its title says Rejected: or nothing. Only `DocStore.decide` writes it."""
    meta = doc.meta
    status = "confirmed" if pick in CONFIRMING else "rejected"
    supersedes = [*meta.replaces, *(meta.contradicts if pick == "replace" else [])] if status == "confirmed" else []
    links = {
        "status": status,
        "supersedes": supersedes,
        "replaces": [] if status == "confirmed" else list(meta.replaces),
        "contradicts": [] if status == "confirmed" else list(meta.contradicts),
        "by": by,
        "rev": meta.rev + 1,
    }
    return replace(_titled(doc, status), meta=meta.model_copy(update=links)).render()


def evidence_gone(doc: ProcessedDoc) -> bool:
    """Whether a confirmed or rejected decision was left by a forget with nothing but its title and links: no covers
    and no content line (DR-0580)."""
    return (
        doc.meta.type == "decision"
        and doc.meta.status in FINAL
        and not doc.meta.covers
        and not any(role == "content" for _, _, role in doc.roles())
    )


def edited(on_disk: str, committed: str | None) -> bool:
    """Whether a final decision's file says other than its last commit: the index and the tool note it (DR-0782)."""
    return committed is not None and normalize_newlines(on_disk) != normalize_newlines(committed)


# -- a put ------------------------------------------------------------------------------------------------------------


def check(
    doc: ProcessedDoc, stored: ProcessedDoc | None, decisions: list[ProcessedDoc], root: Path, lists: NeverStore
) -> list[DocProblem]:
    """What refuses a decision on put. `stored` is the decision as its last commit holds it, `decisions` every
    decision as every read takes it."""
    meta = doc.meta
    if stored is not None and stored.meta.status in FINAL:
        message = (
            f"{meta.id} is {stored.meta.status}: a decision confirmed or rejected takes no new rev; a change is a new "
            "decision that replaces it"
        )
        return [DocProblem(None, message)]
    entities = load_entities(root).entities
    subject = next((e for e in entities if e.id == meta.subject), None)
    if subject is not None and brief_form.held(subject, lists):
        return [
            DocProblem(
                brief_form.head_line(doc, "subject"),
                "subject: a never-store list holds this entity; it gets no decision",
            )
        ]
    problems: list[DocProblem] = []
    if not _ID.match(meta.id):
        problems.append(
            DocProblem(
                brief_form.head_line(doc, "id"), "a decision's id is decision:<yyyy-mm-dd>-<words>, the day it was said"
            )
        )
    if meta.status != "proposed":
        message = (
            "status: a put writes a decision as proposed; only the Briefs page or docket decision set confirms or "
            "rejects one"
        )
        problems.append(DocProblem(brief_form.head_line(doc, "status"), message))
    if meta.supersedes:
        message = "supersedes: a decision names what it would supersede in replaces; the Briefs page's confirm moves it"
        problems.append(DocProblem(brief_form.head_line(doc, "supersedes"), message))
    if subject is None:
        message = f"subject: {meta.subject} isn't an entity; a decision is on a project, topic or stream"
        problems.append(DocProblem(brief_form.head_line(doc, "subject"), message))
    elif subject.type not in SUBJECT_TYPES:
        message = f"subject: {subject.id} is a {subject.type}; a decision is on a project, topic or stream"
        problems.append(DocProblem(brief_form.head_line(doc, "subject"), message))
    if meta.subject is not None and meta.subject not in meta.entities:
        problems.append(
            DocProblem(brief_form.head_line(doc, "entities"), f"entities: a decision names its subject, {meta.subject}")
        )
    found = heading(doc)
    if found is None or not found[1].startswith(PREFIX["proposed"]) or not bare(found[1]).strip():
        message = "a decision starts with # Proposed: <the decision, one sentence>"
        problems.append(DocProblem(found[0] if found else None, message))
    problems += _targets(doc, standings(decisions))
    again = _again(doc, decisions)
    if again is not None:
        message = (
            f"it cites only what {again}, rejected, cites: a statement the user said isn't a decision isn't proposed "
            "again"
        )
        problems.append(DocProblem(None, message))
    problems += brief_form.in_entities(doc, entities, lists) + brief_form.people_screen(doc, entities, lists)
    if _names_someone(doc, brief_form.phrases(entities, lists)):
        problems.append(DocProblem(brief_form.head_line(doc, "id"), NAMES_SOMEONE))
    return problems


def _targets(doc: ProcessedDoc, standing: dict[str, Standing]) -> list[DocProblem]:
    """`replaces` and `contradicts` name confirmed, current decisions on the same subject."""
    problems: list[DocProblem] = []
    for key, targets in (("replaces", doc.meta.replaces), ("contradicts", doc.meta.contradicts)):
        for target in targets:
            found = standing.get(target)
            why = (
                "doesn't exist"
                if found is None
                else "isn't confirmed"
                if found.status != "confirmed"
                else "is superseded"
                if not found.current
                else "is on another subject"
                if found.subject != doc.meta.subject
                else None
            )
            if why is not None:
                message = f"{key}: {target} {why}; it names only confirmed, current decisions on the same subject"
                problems.append(DocProblem(brief_form.head_line(doc, key), message))
    return problems


def citations(doc: ProcessedDoc) -> frozenset[str]:
    """What a doc's content lines cite: each raw anchor with its record, and each ref by its doc and line."""
    covers = doc.meta.covers
    out: set[str] = set()
    for _, line, role in doc.roles():
        if role != "content":
            continue
        trailing = trailing_anchors(line)
        for a in trailing.anchors:
            rid = a.rid or (covers[0] if len(covers) == 1 else None)
            out.add(format_anchor(rid, a.kind, a.start, a.end) if rid is not None else a.token)
        out.update(f"d:{r.doc}#{r.line}" for r in trailing.refs)
    return frozenset(out)


def _again(doc: ProcessedDoc, decisions: list[ProcessedDoc]) -> str | None:
    """A rejected decision on the same subject that already cites everything this one cites."""
    mine = citations(doc)
    for other in sorted(decisions, key=lambda d: d.meta.id):
        meta = other.meta
        rejected = meta.status == "rejected" and meta.subject == doc.meta.subject and meta.id != doc.meta.id
        if rejected and mine and mine <= citations(other):
            return meta.id
    return None


def says_someone(text: str, shapes: NeverStore, lists: NeverStore) -> bool:
    """Whether a text says a never-store keyword, or names a listed person by the people screen briefs use."""
    return matches_text(text, lists) or brief_form.names(text, shapes, lists) is not None


def id_says_someone(doc_id: str, shapes: NeverStore, lists: NeverStore) -> bool:
    """Whether a doc's id, its words read as a phrase, says a keyword or names a listed person."""
    return says_someone(doc_id.partition(":")[2].replace("-", " "), shapes, lists)


def _names_someone(doc: ProcessedDoc, shapes: NeverStore) -> bool:
    """Whether the decision's own id, or one it replaces or contradicts, names a listed person: its words, read as a
    phrase."""
    ids = (doc.meta.id, *doc.meta.replaces, *doc.meta.contradicts)
    return bool(shapes.keywords) and any(matches_text(i.partition(":")[2].replace("-", " "), shapes) for i in ids)


# -- a hand edit ------------------------------------------------------------------------------------------------------


def hand_stops(
    edited_doc: ProcessedDoc, text: str, last: str | None, root: Path, lists: NeverStore
) -> list[tuple[int, str]]:
    """What stops the take-in of a hand edit to a decision (DR-0782): any change to a confirmed or rejected one; a
    status or a link a proposed one's last commit doesn't hold, or one no commit holds yet that isn't proposed with no
    links; and what never-store refuses on a put. Each on a line of the file."""
    committed = committed_doc(last)
    base = committed.meta if committed is not None and committed.meta.type == "decision" else None
    if base is not None and base.status in FINAL:
        if last is not None and edited(text, last):
            return [(_first_change(text, last), FINAL_EDIT.format(status=base.status))]
        return []
    wanted = {
        "status": base.status if base is not None else "proposed",
        "supersedes": list(base.supersedes) if base is not None else [],
        "replaces": list(base.replaces) if base is not None else [],
        "contradicts": list(base.contradicts) if base is not None else [],
    }
    meta = edited_doc.meta
    has = {
        "status": meta.status,
        "supersedes": meta.supersedes,
        "replaces": meta.replaces,
        "contradicts": meta.contradicts,
    }
    stops = [(brief_form.head_line(edited_doc, key) or 2, LINKS_EDIT) for key in wanted if has[key] != wanted[key]]
    found = heading(edited_doc)
    if found is not None and not found[1].startswith(PREFIX["proposed"]):
        stops.append((found[0], TITLE_EDIT))
    stops += [(p.line or 2, brief_form.NAMES_LISTED) for p in screen(edited_doc, root, lists)]
    return stops


def _first_change(text: str, last: str) -> int:
    mine, theirs = normalize_newlines(text).split("\n"), normalize_newlines(last).split("\n")
    shorter = min(len(mine), len(theirs))
    pairs = zip(mine[:shorter], theirs[:shorter], strict=True)
    return next((i for i, (a, b) in enumerate(pairs, start=1) if a != b), shorter + 1)


def screen(doc: ProcessedDoc, root: Path, lists: NeverStore) -> list[DocProblem]:
    """The never-store checks alone, for a decision edited by hand before its take-in: its subject, its entities list,
    the people screen and its ids."""
    entities = load_entities(root).entities
    subject = next((e for e in entities if e.id == doc.meta.subject), None)
    problems: list[DocProblem] = []
    if subject is not None and brief_form.held(subject, lists):
        problems.append(DocProblem(brief_form.head_line(doc, "subject"), brief_form.NAMES_LISTED))
    problems += brief_form.in_entities(doc, entities, lists) + brief_form.people_screen(doc, entities, lists)
    if _names_someone(doc, brief_form.phrases(entities, lists)):
        problems.append(DocProblem(brief_form.head_line(doc, "id"), NAMES_SOMEONE))
    return problems


# -- a brief's Decisions section --------------------------------------------------------------------------------------


def decisions_lines(doc: ProcessedDoc) -> list[tuple[int, str]]:
    """A brief's content lines under `## Decisions`, up to the next heading of its level or above."""
    lines = doc.body.split("\n")
    inside = False
    out: list[tuple[int, str]] = []
    for i, (line, role) in enumerate(zip(lines, line_roles(lines), strict=True)):
        if role != "skip" and (m := _HEADING.match(line)):
            inside = m.group(1) == "##" and (m.group(2) or "") == "Decisions"
            continue
        if inside and role == "content":
            out.append((doc.head_lines + 1 + i, line))
    return out


def brief_problems(doc: ProcessedDoc, standing: dict[str, Standing]) -> list[DocProblem]:
    """What refuses a brief's Decisions line (DR-0565): each reads exactly `- <title> <cite>` for a current decision,
    citing its title line, and nothing else."""
    problems: list[DocProblem] = []
    for n, line in decisions_lines(doc):
        trailing = trailing_anchors(line)
        if trailing.anchors or len(trailing.refs) != 1 or not trailing.refs[0].doc.startswith("decision:"):
            problems.append(DocProblem(n, CITE_ONE, ""))
            continue
        ref = trailing.refs[0]
        found = standing.get(ref.doc)
        if found is None:
            problems.append(DocProblem(n, f"a Decisions line cites {ref.doc}, which isn't a decision here", ""))
        elif not found.current:
            state = "superseded" if found.status == "confirmed" else found.status
            message = f"a Decisions line cites {ref.doc}, which is {state}: a brief's Decisions are current decisions"
            problems.append(DocProblem(n, message, ""))
        elif ref.line != found.title_line:
            message = f"a Decisions line cites the title line of {ref.doc}, the cite the decisions tool gives"
            problems.append(DocProblem(n, message, ""))
        elif trailing.text.strip() != f"- {found.title}":
            message = "a Decisions line reads exactly as its decision's title, the words the decisions tool gives"
            problems.append(DocProblem(n, message, ""))
    return problems


def brief_stops(edited_doc: ProcessedDoc, last: str | None, standing: dict[str, Standing]) -> list[int]:
    """The lines a hand edit to a brief adds or changes under `## Decisions` that a put would refuse (DR-0565): the
    take-in and the redact pass leave the edit on disk. A line its last commit already held stays, so redacting a
    brief stored before #162 is never held up; the page marks such a line instead."""
    committed = committed_doc(last)
    held = {line for _, line in decisions_lines(committed)} if committed is not None else set[str]()
    lines = dict(edited_doc.lines())
    return [p.line for p in brief_problems(edited_doc, standing) if p.line is not None and lines[p.line] not in held]


def unconfirmed(doc: ProcessedDoc, standing: dict[str, Standing]) -> set[int]:
    """A brief's Decisions lines that cite no confirmed decision: a brief stored before #162 can hold them, and the
    page marks them and the stale report names them until its next rev."""
    out: set[int] = set()
    for n, line in decisions_lines(doc):
        refs = trailing_anchors(line).refs
        if not any(r.doc in standing and standing[r.doc].status == "confirmed" for r in refs):
            out.add(n)
    return out
