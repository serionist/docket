"""Which doc lines a forget drops (DR-0580): one rule for the index's build (DR-0593), and for the apply and `read`.

An anchor is judged by where it reads, not by the rid it names. A time on a record without segments of its own reads on
the transcript under it that holds the time (DR-0370), so once a forgotten transcript is gone, a time on a rid the
list's media holds that lands on no transcript left reads on the family.

| A doc | Then |
|---|---|
| a content line with a raw anchor on the family | dropped, whatever else it cites |
| a content line with a pinned ref to a dropped line, in the rev the ref names | dropped too, to a fixed point |
| `covers` | the rids nothing is left of go |
| left with no content line | removed, but a confirmed or rejected decision, which keeps its title and links |
| a legacy note whose old path or body hash is listed | removed whole |

A ref to an earlier rev than the cited doc's current one is judged on that rev, read through `older`, as pinning reads
it (DR-0540). The rule finds lines by the numbers their file gives them, as refs do; a doc it changes comes back as
Docket writes it, numbered as the rewritten file will be.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from docket.vault.anchors import Anchor, DocRef, trailing_anchors
from docket.vault.decision_form import FINAL
from docket.vault.forgotten import Forgotten
from docket.vault.processed import ProcessedDoc, parse_doc

LEGACY = "legacy-note"
# What the rule reads beyond the docs it judges, a round at a time: docs by id as they read now, and docs at an earlier
# rev by id and rev.
Current = Callable[[Collection[str]], Mapping[str, ProcessedDoc]]
Older = Callable[[Collection[tuple[str, int]]], Mapping[tuple[str, int], ProcessedDoc]]


class Raw(Protocol):
    """What the rule asks of raw as the forgotten list leaves it."""

    def gone(self, rid: str) -> bool:
        """Whether nothing of a rid is left to read: forgotten whole, or every version of it forgotten."""
        ...

    def timed(self, rid: str) -> bool:
        """Whether the rid's record has segments of its own, so a time on it reads on itself."""
        ...

    def lands(self, rid: str, start: int, last: int) -> bool:
        """Whether a time on a record without segments lands on a transcript under it that is left."""
        ...

    def media(self, rid: str) -> bool:
        """Whether the list's media holds the rid: a rid under it, or it, was forgotten whole."""
        ...


@dataclass(frozen=True)
class Fate:
    """What a forget makes of one doc: the doc as it is left, None when it is removed, and the lines it drops by their
    numbers in the file."""

    doc: ProcessedDoc | None
    dropped: frozenset[int] = frozenset()


def on_family(anchor: Anchor, rid: str, raw: Raw) -> bool:
    """Whether an anchor reads on what a forget took (DR-0580)."""
    if raw.gone(rid):
        return True
    return (
        anchor.kind == "time"
        and not raw.timed(rid)
        and raw.media(rid)
        and not raw.lands(rid, anchor.start, anchor.last)
    )


def _refs(doc: ProcessedDoc) -> list[tuple[int, DocRef]]:
    """Each doc-line ref on a content line, with its line's number in the file; a legacy note has none."""
    if doc.meta.type == LEGACY:
        return []
    return [(n, ref) for n, line, role in doc.roles() if role == "content" for ref in trailing_anchors(line).refs]


def anchored(doc: ProcessedDoc, raw: Raw) -> set[int]:
    """The content lines with a raw anchor on the family. A short anchor reads on the doc's one covered record."""
    if doc.meta.type == LEGACY:
        return set()
    one = doc.meta.covers[0] if len(doc.meta.covers) == 1 else None
    out: set[int] = set()
    for n, line, role in doc.roles():
        if role != "content":
            continue
        for anchor in trailing_anchors(line).anchors:
            rid = anchor.rid or one
            if rid is not None and on_family(anchor, rid, raw):
                out.add(n)
                break
    return out


class _Rule:
    """One pass of the rule over some docs and everything their refs lead to, read in rounds: a line drops only for
    what it cites, so what it cites, and what that cites, is all the rule reads."""

    def __init__(
        self, docs: Iterable[ProcessedDoc], raw: Raw, listed: Forgotten, current: Current, older: Older
    ) -> None:
        self.docs = {doc.meta.id: doc for doc in docs}
        self.raw = raw
        self.listed = listed
        # Each earlier rev a ref names, with its lines anchored on the family; None when it can't be read.
        self._earlier: dict[tuple[str, int], tuple[ProcessedDoc, set[int]] | None] = {}
        self._close(current, older)
        self.removed = {
            doc_id
            for doc_id, doc in self.docs.items()
            if doc.meta.type == LEGACY and listed.legacy(doc.meta.old_paths, doc.meta.import_hash)
        }
        self.dropped = {doc_id: anchored(doc, raw) for doc_id, doc in self.docs.items() if doc_id not in self.removed}

    def _close(self, current: Current, older: Older) -> None:
        """Read every doc a ref leads to, as it reads now, and every earlier rev a ref names, a round of each at a
        time, so a caller reads each round in one go."""
        frontier = list(self.docs.values())
        missing: set[str] = set()
        while frontier:
            refs = [ref for doc in frontier for _, ref in _refs(doc)]
            wanted = {ref.doc for ref in refs} - set(self.docs) - missing
            found = dict(current(wanted)) if wanted else {}
            missing |= wanted - set(found)
            self.docs.update(found)
            revs = {(ref.doc, ref.rev) for ref in refs if ref.rev is not None and self._earlier_rev(ref.doc, ref.rev)}
            revs -= set(self._earlier)
            read = dict(older(revs)) if revs else {}
            for key in revs:
                doc = read.get(key)
                self._earlier[key] = None if doc is None else (doc, anchored(doc, self.raw))
            frontier = [*found.values(), *read.values()]

    def _earlier_rev(self, doc_id: str, rev: int) -> bool:
        cited = self.docs.get(doc_id)
        return cited is None or cited.meta.rev != rev

    def settle(self) -> None:
        """Drop every line citing a dropped line until no more go: a brief line built on a note line goes with it."""
        changed = True
        while changed:
            changed = False
            for doc_id, lines in self.dropped.items():
                for n, ref in _refs(self.docs[doc_id]):
                    if n not in lines and self.cites_dropped(ref, set()):
                        lines.add(n)
                        changed = True

    def cites_dropped(self, ref: DocRef, seen: set[tuple[str, int, int]]) -> bool:
        """Whether a ref cites a line the rule drops, in the rev it names: the current doc's when it is that rev (or
        names none), else the earlier rev, judged on its own anchors and refs."""
        if ref.doc in self.removed:
            return True
        current = self.docs.get(ref.doc)
        if current is not None and ref.rev in (None, current.meta.rev):
            return ref.line in self.dropped.get(ref.doc, set())
        if ref.rev is None or (ref.doc, ref.rev, ref.line) in seen:
            return False
        seen.add((ref.doc, ref.rev, ref.line))
        earlier = self._earlier.get((ref.doc, ref.rev))
        if earlier is None:
            return False
        if earlier[0].meta.type == LEGACY:
            return self.listed.legacy(earlier[0].meta.old_paths, earlier[0].meta.import_hash)
        if ref.line in earlier[1]:
            return True
        return any(n == ref.line and self.cites_dropped(cited, seen) for n, cited in _refs(earlier[0]))

    def fate(self, doc: ProcessedDoc) -> Fate | None:
        doc_id = doc.meta.id
        if doc_id in self.removed:
            return Fate(None)
        lines = self.dropped.get(doc_id, set())
        covers = [rid for rid in doc.meta.covers if not self.raw.gone(rid)]
        if not lines and covers == doc.meta.covers:
            return None
        left = without(doc, lines, covers)
        final = doc.meta.type == "decision" and doc.meta.status in FINAL
        if doc.meta.type != LEGACY and not final and not any(role == "content" for _, _, role in left.roles()):
            return Fate(None, frozenset(lines))
        return Fate(left, frozenset(lines))


def without(doc: ProcessedDoc, lines: set[int], covers: list[str]) -> ProcessedDoc:
    """A doc with these lines out, by their numbers in the file, and these covers: the doc as the forget leaves it,
    read back from the text Docket writes for it, so its lines are numbered as the rewritten file numbers them."""
    first = doc.head_lines + 1
    body = [line for i, line in enumerate(doc.body.split("\n")) if first + i not in lines]
    left = replace(doc, meta=doc.meta.model_copy(update={"covers": covers}), body="\n".join(body))
    return parse_doc(left.render(), lenient=True)


def _nothing(_wanted: Collection[Any]) -> Mapping[Any, ProcessedDoc]:
    return {}


def fates(
    docs: Iterable[ProcessedDoc],
    raw: Raw,
    listed: Forgotten,
    *,
    current: Current = _nothing,
    older: Older = _nothing,
) -> dict[str, Fate]:
    """Each of `docs` the list changes, by its id; a doc it leaves as it is has no entry. The rule reads what their
    refs lead to through `current`, the docs by id as they read now, and `older`, docs at an earlier rev; each answers
    a whole round at once, leaving out what it can't read."""
    targets = list(docs)
    rule = _Rule(targets, raw, listed, current, older)
    rule.settle()
    out: dict[str, Fate] = {}
    for doc in targets:
        found = rule.fate(rule.docs[doc.meta.id])
        if found is not None:
            out[doc.meta.id] = found
    return out
