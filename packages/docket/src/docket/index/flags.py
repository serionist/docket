"""Which doc lines cite a line that changed (DR-0542), worked out each time and never stored.

A pinned ref, `[d:<doc>@<rev>#<n>=<hash>]`, is flagged when its doc is gone, when that doc is superseded, or when no
citable line of its current text has the hash. Only the lines citing the changed line are flagged; they clear when
their own doc's next rev cites the line afresh or drops the ref. A correction's refs read the other way: a correction
is applied once the line it corrects reads otherwise, and waits until then (DR-0543).

From the index, one join over `derived_from` and `doc_lines` answers for the whole vault (`docket doc stale`, the
session-start count); for the one doc `read` shows, the same rule runs over the vault's files, so it never waits on
the index. A token that starts like an anchor or a ref and holds a redaction marker is damage an older Docket's
`docket doc redact` did (DR-0541): it is named with the newest earlier rev where the line's anchors were whole.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from docket.vault.anchors import DocRef, trailing_anchors
from docket.vault.processed import ProcessedDoc
from docket.vault.redact import MARKER

Why = Literal["changed", "gone", "superseded"]
CORRECTION = "correction"
LEGACY = "legacy-note"


@dataclass(frozen=True)
class Flag:
    """A line's pinned ref and what became of the line it cites: `why` is None when the line still reads the same.
    `correction` says the citing doc is a correction, whose refs mean the opposite."""

    doc: str
    line: int
    ref: DocRef
    why: Why | None
    now: int | None = None
    superseded_by: str | None = None
    correction: bool = False

    @property
    def flagged(self) -> bool:
        return self.why is not None and not self.correction

    @property
    def unapplied(self) -> bool:
        return self.why is None and self.correction

    @property
    def message(self) -> str:
        cited = f"line {self.ref.line} of {self.ref.doc} rev {self.ref.rev}"
        if self.why is None:
            said = f"{'corrects' if self.correction else 'cites'} {cited}, which still reads the same"
            return f"{said}: not applied yet" if self.correction else said
        if self.correction:
            return f"corrects {cited}: applied"
        if self.why == "gone":
            return f"cites {cited}, and {self.ref.doc} is gone"
        if self.why == "superseded":
            return f"cites {cited}, and {self.ref.doc} is superseded by {self.superseded_by}"
        return f"cites {cited}, which reads otherwise in rev {self.now}; check it, then cite it again or drop it"


def pinned_refs(doc: ProcessedDoc) -> list[tuple[int, DocRef]]:
    """Each pinned ref on a content line of an anchored doc, with its line number in the file, in order."""
    if doc.meta.type == LEGACY:
        return []
    return [
        (n, ref)
        for n, line, role in doc.roles()
        if role == "content"
        for ref in trailing_anchors(line).refs
        if ref.rev is not None and ref.hash is not None
    ]


def ref_rows(doc: ProcessedDoc) -> list[tuple[str, int, int, str, int, int, str]]:
    """The doc's `derived_from` rows: (doc, line, ordinal, src, rev, src_line, hash) for each pinned ref."""
    rows: list[tuple[str, int, int, str, int, int, str]] = []
    ordinal: dict[int, int] = {}
    for n, ref in pinned_refs(doc):
        k = ordinal.get(n, 0)
        ordinal[n] = k + 1
        rows.append((doc.meta.id, n, k, ref.doc, ref.rev or 0, ref.line, ref.hash or ""))
    return rows


def _why(exists: bool, superseded_by: str | None, kept: bool) -> Why | None:
    if not exists:
        return "gone"
    if superseded_by is not None:
        return "superseded"
    return None if kept else "changed"


# -- from the index ---------------------------------------------------------------------------------------------------

_FLAGS = """
SELECT d.doc, d.line, d.src, d.rev, d.src_line, d.hash, t.type, s.id IS NOT NULL, s.rev, s.superseded_by,
  EXISTS (SELECT 1 FROM doc_lines l WHERE l.doc = d.src AND l.hash = d.hash)
FROM derived_from d
JOIN docs t ON t.id = d.doc
LEFT JOIN docs s ON s.id = d.src
WHERE t.superseded_by IS NULL
ORDER BY d.doc, d.line, d.ordinal
"""


def from_index(conn: sqlite3.Connection) -> list[Flag]:
    """Every pinned ref of every current doc, with what became of the line it cites."""
    out: list[Flag] = []
    for doc, line, src, rev, src_line, found, doc_type, exists, now, by, kept in conn.execute(_FLAGS):
        ref = DocRef(src, rev, src_line, found)
        why = _why(bool(exists), by, bool(kept))
        out.append(Flag(doc, line, ref, why, now, by, doc_type == CORRECTION))
    return out


def counts(conn: sqlite3.Connection) -> tuple[int, int]:
    """How many lines are flagged, and how many correction lines wait to be applied."""
    found = from_index(conn)
    return len({(f.doc, f.line) for f in found if f.flagged}), len({(f.doc, f.line) for f in found if f.unapplied})


def summary(flagged: int, unapplied: int) -> str:
    """The two counts as the session-start line and `docket index status` say them."""
    said = [f"{flagged} line(s) cite lines that changed"] if flagged else []
    said += [f"{unapplied} correction line(s) not applied"] if unapplied else []
    return ", ".join(said)


# -- from the vault, for one doc ---------------------------------------------------------------------------------------


def for_doc(
    doc: ProcessedDoc, get: Callable[[str], ProcessedDoc | None], superseded_by: Callable[[str], list[str]]
) -> list[Flag]:
    """A doc's pinned refs, each with what became of the line it cites, read from the vault's files: `get` reads a
    doc's file (None when there is none) and `superseded_by` names the docs that supersede one."""
    cited: dict[str, tuple[ProcessedDoc | None, set[str]]] = {}
    out: list[Flag] = []
    for n, ref in pinned_refs(doc):
        if ref.doc not in cited:
            found = get(ref.doc)
            cited[ref.doc] = (found, set(found.citable().values()) if found is not None else set())
        found, hashes = cited[ref.doc]
        by = next(iter(superseded_by(ref.doc)), None)
        why = _why(found is not None, by, ref.hash in hashes)
        out.append(Flag(doc.meta.id, n, ref, why, found.meta.rev if found else None, by, doc.meta.type == CORRECTION))
    return out


# -- damage an older Docket did ----------------------------------------------------------------------------------------


def damaged(doc: ProcessedDoc) -> list[tuple[int, str]]:
    """The content lines whose trailing run holds a token that starts with `[r:` or `[d:` and holds a redaction
    marker, each with the first such token (DR-0541)."""
    if doc.meta.type == LEGACY:
        return []
    out: list[tuple[int, str]] = []
    for n, line, role in doc.roles():
        if role != "content":
            continue
        trailing = trailing_anchors(line)
        bad = [t for t in trailing.failed if t.startswith(("[r:", "[d:")) and MARKER.search(t)]
        if bad:
            out.append((n, bad[0]))
    return out


def restore_from(earlier: Iterable[tuple[int, ProcessedDoc]]) -> int | None:
    """The newest of the earlier revs, given newest first, whose content lines hold no damaged token."""
    return next((rev for rev, doc in earlier if not damaged(doc)), None)
