"""The Briefs page's Decisions part (DR-0781): an entity's decisions, newest first, as the report gives them.

| Part | Shows |
|---|---|
| Waiting for you | each proposed decision, those that contradict one first, beside what they contradict or replace |
| Decided | each current decision, what it superseded folded under it, and a rejected candidate that contradicted it |
| Rejected | folded |

A proposed decision is only ever under Waiting, never among the decided (DR-0565). Its picks are the app's JSON write
`POST /api/web/briefs/decision`, one of the two callers of a decision's one writer (DR-0782): Confirm and Reject, or
for a candidate that contradicts a decision, the three picks. A line a never-store list now names is shown as hidden,
and the report already leaves out a decision a list holds (DR-0558). `part` takes what was read and does no I/O, and
its models are what the read answers (DR-0709).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal, cast

from pydantic import BaseModel

from docket.decisions import DecisionsReport, DecisionState
from docket.vault.anchors import trailing_anchors
from docket.web.briefs_view import HIDDEN_LINE, BriefLink, anchor_link, doc_link
from docket.web.panel import Style, anchored_rid, line_parts
from docket.web.state import SearchState

# What each click did, as its answer and a URL from before the app say it.
DONE = {
    "confirm": "Confirmed: it is a decision now.",
    "reject": "Rejected: it stays, marked rejected, and isn't proposed again.",
    "replace": "Confirmed, replacing the decision it contradicted; that one stays, superseded.",
    "stand": "The decision stands; the candidate is rejected and the contradiction kept on record.",
    "unrelated": "Confirmed beside the decision it seemed to contradict; both stand.",
}


Standing = Literal["current", "superseded", "proposed", "rejected"]


@dataclass(frozen=True)
class DecisionDoc:
    """A decision's lines as the page reads them: each line after its title, the records its short anchors point at,
    and the lines a never-store list now names."""

    lines: tuple[tuple[int, str], ...]
    covers: tuple[str, ...] = ()
    hidden: frozenset[int] = frozenset()


class DecisionLine(BaseModel):
    n: int
    body: str
    style: Style
    anchors: list[BriefLink]
    refs: list[BriefLink]
    hidden: bool


class Pick(BaseModel):
    value: str
    label: str
    primary: bool = False


class Card(BaseModel):
    id: str
    title: str
    standing: Standing
    said: str
    # The rev the page shows, which a pick carries back (DR-0782).
    rev: int
    by: str
    verify: str
    lines: list[DecisionLine] = []
    picks: list[Pick] = []
    # What a candidate contradicts or would replace, beside it.
    against: list[Card] = []
    # What it superseded, folded under it.
    folded: list[Card] = []
    # A rejected candidate that contradicted it, folded under it.
    contradicted: list[Card] = []
    note: str = ""


class DecisionsPart(BaseModel):
    waiting: list[Card] = []
    current: list[Card] = []
    rejected: list[Card] = []
    hidden_note: str = ""
    empty: str = ""


PLAIN = [Pick(value="confirm", label="Confirm", primary=True), Pick(value="reject", label="Reject")]
THREE = [
    Pick(value="replace", label="The new one replaces it", primary=True),
    Pick(value="stand", label="The old one stands"),
    Pick(value="unrelated", label="Not a contradiction"),
]


def standing(one: DecisionState) -> Standing:
    """Where a decision stands, as its badge says: current, superseded, proposed or rejected."""
    if one.status == "confirmed":
        return "current" if one.current else "superseded"
    return cast("Standing", one.status)


def _said(doc_id: str) -> str:
    """The day a decision was said, from its id."""
    day = date.fromisoformat(doc_id.partition(":")[2][:10])
    return f"{day.day} {day:%b %Y}"


def _lines(found: DecisionDoc | None) -> list[DecisionLine]:
    if found is None:
        return []
    out: list[DecisionLine] = []
    for n, text in found.lines:
        if n in found.hidden:
            out.append(DecisionLine(n=n, body=HIDDEN_LINE, style="text", anchors=[], refs=[], hidden=True))
            continue
        style, body, anchors = line_parts(text)
        if style == "blank":
            continue
        links = [anchor_link(anchored_rid(a, list(found.covers)), a) for a in anchors]
        refs = [doc_link(r.doc, r.line) for r in trailing_anchors(text).refs]
        out.append(DecisionLine(n=n, body=body, style=style, anchors=links, refs=refs, hidden=False))
    return out


def part(report: DecisionsReport, docs: Mapping[str, DecisionDoc], name: str) -> DecisionsPart:
    """An entity's decisions as the page shows them."""
    by_id = {d.id: d for d in report.decisions}

    def card(one: DecisionState, *, depth: int = 0, children: bool = True) -> Card:
        found = Card(
            id=one.id,
            title=one.title,
            standing=standing(one),
            said=_said(one.id),
            rev=one.rev,
            by=one.by.partition(",")[0],
            verify=SearchState(hit=one.id).url(),
            lines=_lines(docs.get(one.id)),
            note=one.note or "",
        )
        if depth == 0 and one.status == "proposed":
            found.picks = list(THREE if one.contradicts else PLAIN)
            targets = [*one.contradicts, *one.replaces]
            found.against = [card(by_id[t], depth=1, children=False) for t in targets if t in by_id]
        if not children:
            return found
        replaced = sorted((d for d in report.decisions if d.superseded_by == one.id), key=lambda d: d.id, reverse=True)
        found.folded = [card(d, depth=depth + 1) for d in replaced]
        found.contradicted = [
            card(by_id[c], depth=depth + 1)
            for c in one.contradicted_by
            if c in by_id and by_id[c].status == "rejected" and one.status == "confirmed"
        ]
        return found

    proposed = [d for d in report.decisions if d.status == "proposed"]
    # The candidates that contradict a decision first, then those that replace one; the sort keeps newest first.
    proposed.sort(key=lambda d: (not d.contradicts, not d.replaces))
    out = DecisionsPart(
        waiting=[card(d) for d in proposed],
        current=[card(d) for d in report.decisions if d.status == "confirmed" and d.current],
        rejected=[card(d) for d in report.decisions if d.status == "rejected"],
    )
    if report.hidden:
        out.hidden_note = (
            "1 decision hidden: a never-store list holds what it names."
            if report.hidden == 1
            else f"{report.hidden} decisions hidden: a never-store list holds what they name."
        )
    if not report.decisions:
        out.empty = f"No decisions on {name} yet. The decision skill proposes them from the notes when you ask Claude."
    return out
