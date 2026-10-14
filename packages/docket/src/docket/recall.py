"""The recall API: write a summary with its anchors, read it, and check every line against the raw it came from.

doc_put, read and verify are the names of the MCP tools the service (#8) exposes; it maps them one to one. Summary
first, raw for accuracy: `read` gives a doc or the doc lines that point at a record, and `verify` puts each line of a
doc next to the raw its anchors point at. Text from docs and records comes back as data in the result models; nothing
here acts on what it says.

`read` also takes the path other files cite a legacy note by (its old path), with an optional time: the note, and the
raw at that time in the recording it covered. A rid in the raw log stays a rid, and `r:` is always raw.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from docket.errors import DocketError
from docket.vault import legacy
from docket.vault.anchors import Anchor, AnchorError, format_anchor, parse_locator, split_path_locator, trailing_anchors
from docket.vault.docstore import DocStore, PutResult
from docket.vault.processed import DOC_TYPES, ProcessedDoc, parse_doc
from docket.vault.records import RID, RawRecord
from docket.vault.segments import segments
from docket.vault.spans import WINDOW, RawLookup, Span, span
from docket.vault.vault import Vault

View = Literal["summary", "raw", "both"]
VIEWS: tuple[View, ...] = ("summary", "raw", "both")


class DocLine(BaseModel):
    n: int
    text: str
    anchors: list[str] = Field(default_factory=list[str])


class DocView(BaseModel):
    """A doc as stored: its frontmatter, its text, its lines numbered as an editor shows them, and the docs that
    supersede it."""

    id: str
    type: str
    rev: int
    path: str
    by: str
    covers: list[str]
    supersedes: list[str]
    entities: list[str]
    superseded_by: list[str]
    old_paths: list[str] = Field(default_factory=list[str])
    text: str
    lines: list[DocLine]


class CitingLine(BaseModel):
    """A line of a current doc that points at the record asked for."""

    doc: str
    rev: int
    n: int
    text: str
    anchors: list[str]


class LegacyRef(BaseModel):
    """A legacy note that covers the record asked for: it has no anchors, so it is named whole."""

    id: str
    old_paths: list[str]


class ReadResult(BaseModel):
    """What `read` found. `resolved` is the canonical locator a path led to (a doc id, or `r:<rid>@<time>` for an old
    path with a time), so an answer cites that; `problems` says what couldn't be placed."""

    locator: str
    view: View
    doc: DocView | None = None
    lines: list[CitingLine] = Field(default_factory=list[CitingLine])
    spans: list[Span] = Field(default_factory=list[Span])
    legacy: list[LegacyRef] = Field(default_factory=list[LegacyRef])
    resolved: str | None = None
    problems: list[str] = Field(default_factory=list[str])


class VerifiedLine(BaseModel):
    n: int
    text: str
    anchors: list[str] = Field(default_factory=list[str])
    spans: list[Span] = Field(default_factory=list[Span])
    problems: list[str] = Field(default_factory=list[str])


class VerifyResult(BaseModel):
    doc: str
    rev: int
    lines: list[VerifiedLine]


def doc_put(vault: Vault, text: str) -> PutResult:
    """Store a processed doc; `DocRefusedError` carries every problem when it can't be stored."""
    return DocStore(vault).put(text)


def read(vault: Vault, locator: str, view: str = "summary", around: int = 0, rev: int | None = None) -> ReadResult:
    """A doc or a record by locator. `summary` is the doc, or the doc lines that point at the record; `raw` is the
    covered records, or the raw the locator points at; `both` is both. `around` widens a record's raw: minutes either
    side of a time, paragraphs either side of a paragraph, messages either side in a thread."""
    if view not in VIEWS:
        raise DocketError(f"view is one of {', '.join(VIEWS)}, not {view!r}")
    if around < 0:
        raise DocketError("around counts minutes, paragraphs or messages either side; it can't be negative")
    the_view: View = "summary" if view == "summary" else "raw" if view == "raw" else "both"
    store = DocStore(vault)
    value = locator.strip()
    try:
        path = None if value.startswith(("[", "r:")) else split_path_locator(value)
        if path is not None and not (RID.match(value) and RawLookup(vault.root).versions(value)):
            return _read_path(store, locator, found=path, view=the_view, around=around, rev=rev)
        loc = parse_locator(locator, DOC_TYPES)
    except AnchorError as e:
        raise DocketError(str(e)) from None
    if loc.kind != "record":
        return _read_doc(store, _load(store, loc.kind, loc.value, rev), locator, the_view)
    if rev is not None:
        raise DocketError("rev is a doc's revision; for a record, the raw view says how many versions it has")
    lookup = RawLookup(vault.root)
    versions = lookup.versions(loc.value)
    if not versions:
        hint = " (for a position, write r:<rid>@mm:ss)" if "@" in loc.value or "#" in loc.value else ""
        raise DocketError(f"no record {loc.value} in the raw log{hint}")
    record = versions[-1]
    anchor = loc.anchor or Anchor(record.rid, "record")
    result = ReadResult(locator=locator, view=the_view)
    if the_view != "summary":
        result.spans = _raw_spans(lookup, record, anchor, len(versions), around)
    if the_view != "raw":
        docs = store.all()
        result.lines = _citing(docs, record.rid, anchor, around)
        result.legacy = [
            LegacyRef(id=d.meta.id, old_paths=d.meta.old_paths)
            for d in docs
            if d.meta.type == "legacy-note" and record.rid in d.meta.covers
        ]
    return result


def _read_path(
    store: DocStore, locator: str, *, found: tuple[str, Anchor | None], view: View, around: int, rev: int | None
) -> ReadResult:
    """A doc by a path: one in the vault's processed/ folder, else a legacy note by its old path; with a time, the
    raw at that time in the one recording it covers."""
    path, at = found
    processed = (store.root / "processed").resolve()
    given = Path(os.path.normpath(store.root / path.replace("\\", "/")))
    if given.is_relative_to(processed) and given.is_file():
        doc = _load(store, "path", str(given), rev)
    else:
        doc_id = legacy.find(legacy.old_paths(store.all()), path)
        doc = _load(store, "doc", doc_id, rev)
    result = _read_doc(store, doc, locator, view)
    result.resolved = doc.meta.id
    if at is None:
        return result
    lookup = RawLookup(store.root)
    timed = [rid for rid in doc.meta.covers if any(segments(r) for r in lookup.versions(rid)[-1:])]
    if len(timed) != 1:
        if timed:
            options = ", ".join(format_anchor(rid, at.kind, at.start, at.end)[1:-1] for rid in timed)
            result.problems.append(f"{doc.meta.id} covers {len(timed)} recordings; read one of {options}")
        else:
            result.problems.append(
                f"{doc.meta.id} covers no recording in the raw log, so {at.token} can't be placed; "
                "import the recording, then run docket import notes again"
            )
        return result
    rid = timed[0]
    anchor = Anchor(rid, at.kind, at.start, at.end, format_anchor(rid, at.kind, at.start, at.end))
    result.resolved = anchor.token[1:-1]
    versions = lookup.versions(rid)
    if view != "raw":
        result.lines = _citing(store.all(), rid, anchor, around)
    found_span = span(anchor, versions[-1], len(versions), around=around)
    if found_span.problem is not None:
        result.problems.append(found_span.problem)
    if view != "summary":
        result.spans = [] if found_span.problem is not None else [found_span]
    return result


def verify(vault: Vault, doc: str, lines: list[int] | None = None, window: int = WINDOW) -> VerifyResult:
    """Each anchored line of a doc, or the lines asked for by their number in the file, next to the raw spans its
    anchors point at. An anchor that doesn't resolve comes back with its problem."""
    store = DocStore(vault)
    try:
        loc = parse_locator(doc, DOC_TYPES)
    except AnchorError as e:
        raise DocketError(str(e)) from None
    if loc.kind == "record":
        raise DocketError(f"{doc} is a record; verify takes a doc: its id or its path")
    found = _load(store, loc.kind, loc.value, None)
    lookup = RawLookup(vault.root)
    covers = found.meta.covers
    wanted = set(lines) if lines else None
    out: list[VerifiedLine] = []
    numbered = found.roles()
    for n, text, role in numbered:
        if wanted is not None and n not in wanted:
            continue
        if found.meta.type == "legacy-note" or role != "content":
            if wanted is not None:
                out.append(VerifiedLine(n=n, text=text))
            continue
        trailing = trailing_anchors(text)
        line = VerifiedLine(n=n, text=text, anchors=[a.token for a in trailing.anchors], problems=list(trailing.errors))
        if not trailing.anchors and not trailing.errors:
            line.problems.append("no anchors at the end of the line")
        for anchor in trailing.anchors:
            line.spans.append(_verify_one(lookup, anchor, covers, window))
            if anchor.rid is not None and anchor.rid not in covers:
                line.problems.append(f"{anchor.rid} isn't in covers")
        out.append(line)
    body = {n for n, _, _ in numbered}
    out.extend(
        VerifiedLine(n=n, text="", problems=[f"line {n} isn't in the doc's body"])
        for n in sorted(wanted - body if wanted else set[int]())
    )
    return VerifyResult(doc=found.meta.id, rev=found.meta.rev, lines=out)


def _verify_one(lookup: RawLookup, anchor: Anchor, covers: list[str], window: int) -> Span:
    rid = anchor.rid or (covers[0] if len(covers) == 1 else None)
    if rid is None:
        return Span(rid="", anchor=anchor.token, problem="a short anchor needs a doc covering exactly one record")
    versions = lookup.versions(rid)
    if not versions:
        return Span(rid=rid, anchor=anchor.token, problem=f"{rid} isn't in the raw log")
    return span(anchor, versions[-1], len(versions), window=window)


def _load(store: DocStore, kind: str, value: str, rev: int | None) -> ProcessedDoc:
    """A doc by id, or by its path inside the vault's processed/ folder; at a rev when one is asked for."""
    doc_id = value
    if kind == "path":
        given = Path(value)
        path = (given if given.is_absolute() else store.root / given).resolve()
        processed = (store.root / "processed").resolve()
        if not path.is_relative_to(processed):
            raise DocketError(f"{value} isn't a doc in this vault's processed/ folder")
        if not path.is_file():
            raise DocketError(f"no doc at {value}")
        on_disk = parse_doc(store.read_file(path), lenient=True)
        if rev is None:
            return on_disk
        doc_id = on_disk.meta.id
    if rev is not None:
        return store.at_rev(doc_id, rev)
    found = store.get(doc_id)
    if found is None:
        if store.exists(doc_id):
            raise DocketError(f"{doc_id} was deleted; its revs are in history: read it with a rev")
        raise DocketError(f"no doc {doc_id}")
    return found


def _read_doc(store: DocStore, doc: ProcessedDoc, locator: str, view: View) -> ReadResult:
    meta = doc.meta
    result = ReadResult(locator=locator, view=view)
    if view != "raw":
        superseded_by = [d.meta.id for d in store.all() if meta.id in d.meta.supersedes]
        result.doc = DocView(
            id=meta.id,
            type=meta.type,
            rev=meta.rev,
            path=store.file(meta.id).relative_to(store.root).as_posix(),
            by=meta.by,
            covers=meta.covers,
            supersedes=meta.supersedes,
            entities=meta.entities,
            superseded_by=superseded_by,
            old_paths=meta.old_paths,
            text=doc.source,
            lines=[
                DocLine(n=n, text=text, anchors=[a.token for a in trailing_anchors(text).anchors])
                for n, text in enumerate(doc.source.removesuffix("\n").split("\n"), start=1)
            ],
        )
    if view != "summary":
        lookup = RawLookup(store.root)
        for rid in meta.covers:
            versions = lookup.versions(rid)
            if versions:
                result.spans.append(span(Anchor(rid, "record"), versions[-1], len(versions)))
            else:
                result.spans.append(Span(rid=rid, anchor=f"[r:{rid}]", problem=f"{rid} isn't in the raw log"))
    return result


def _raw_spans(lookup: RawLookup, record: RawRecord, anchor: Anchor, versions: int, around: int) -> list[Span]:
    if anchor.kind == "record" and around > 0 and not segments(record):
        return [
            span(Anchor(r.rid, "record"), r, len(lookup.versions(r.rid))) for r in lookup.neighbours(record, around)
        ]
    found = span(anchor, record, versions, around=around)
    if found.problem is not None:
        raise DocketError(found.problem)
    return [found]


def _citing(docs: list[ProcessedDoc], rid: str, at: Anchor, around: int) -> list[CitingLine]:
    """The lines of current docs (none supersedes them) whose anchors point at the record, near the position."""
    superseded = {s for d in docs for s in d.meta.supersedes}
    out: list[CitingLine] = []
    for doc in docs:
        meta = doc.meta
        if meta.id in superseded or rid not in meta.covers or meta.type == "legacy-note":
            continue
        for n, text, role in doc.roles():
            if role != "content":
                continue
            anchors = trailing_anchors(text).anchors
            mine = [a for a in anchors if (a.rid or (meta.covers[0] if len(meta.covers) == 1 else None)) == rid]
            if any(_near(a, at, around) for a in mine):
                out.append(CitingLine(doc=meta.id, rev=meta.rev, n=n, text=text, anchors=[a.token for a in anchors]))
    return out


def _near(anchor: Anchor, at: Anchor, around: int) -> bool:
    """Whether a doc's anchor falls within what a locator points at: anything when either is the whole record."""
    if at.kind == "record" or anchor.kind == "record":
        return True
    if anchor.kind != at.kind:
        return False
    if at.kind == "time":
        lo = at.start - around * 60
        hi = (at.last if at.end is not None else at.start + WINDOW) + around * 60
    else:
        lo, hi = at.start - around, at.last + around
    return anchor.start <= hi and anchor.last >= lo
