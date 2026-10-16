"""The recall API: write a summary with its anchors, read it, and check every line against the raw it came from.

doc_put, read and verify are the names of the MCP tools the service (#8) exposes; it maps them one to one. Summary
first, raw for accuracy: `read` gives a doc or the doc lines that point at a record, and `verify` puts each line of a
doc next to the raw its anchors point at. Text from docs and records comes back as data in the result models; nothing
here acts on what it says.

`read` also takes the path other files cite a legacy note by (its old path), with an optional time: the note, and the
raw at that time in the recording it covered. A rid in the raw log stays a rid, and `r:` is always raw. A time on a
record without segments of its own, such as a recording whose source keeps its transcript as a record of its own, is
placed on the one transcript under it (DR-0370), and a transcript's read lists the lines and the legacy notes on the
recording it is under too (DR-0391), as a recording read whole does those on its transcripts (DR-0440).

What a read asks about docs other than the one it shows (an old path's note, the docs superseding it, the docs covering
a record) comes from the index when it is current for processed/, else from one parse of every doc for the whole call;
either way the same code decides (DR-0321, DR-0322). A read loads each source's raw once.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from docket.errors import DocketError
from docket.index import lookup
from docket.index.lookup import DocIndex
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
    """What `read` found. `resolved` is the canonical locator a path or a recording's time led to (a doc id, or
    `r:<rid>@<time>` on the transcript the time was placed on), so an answer cites that; `problems` says what couldn't
    be placed."""

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
    call = _Call(vault)
    try:
        return _read(call, locator, the_view, around, rev)
    finally:
        call.close()


class _Call:
    """What one read shares: the store, one `RawLookup`, so each source's raw loads once, and the answers about docs
    other than the one it shows. Those come from the index when it is current for processed/, opened at the first
    question (DR-0321), else from one parse of every doc; the index only narrows, and the same code decides
    (DR-0322)."""

    def __init__(self, vault: Vault) -> None:
        self.store = DocStore(vault)
        self.raw = RawLookup(vault.root)
        self._opened = False
        self._index: DocIndex | None = None
        self._every: list[ProcessedDoc] | None = None
        self._supersedes: list[tuple[str, list[str]]] | None = None

    def _ask(self) -> DocIndex | None:
        if not self._opened:
            self._opened = True
            self._index = lookup.open_current(self.store.vault)
        return self._index

    def _all(self) -> list[ProcessedDoc]:
        if self._every is None:
            self._every = self.store.all()
        return self._every

    def old_path(self, path: str) -> str:
        """The legacy note an old path names; nothing or several is a `DocketError` naming the candidates."""
        index = self._ask()
        return legacy.find(index.old_paths(path) if index is not None else legacy.old_paths(self._all()), path)

    def supersedes(self) -> list[tuple[str, list[str]]]:
        """Each doc that supersedes others, with the ids it names, in the order `DocStore.all` reads them."""
        if self._supersedes is None:
            index = self._ask()
            self._supersedes = (
                index.supersedes()
                if index is not None
                else [(d.meta.id, d.meta.supersedes) for d in self._all() if d.meta.supersedes]
            )
        return self._supersedes

    def superseded_by(self, doc_id: str) -> list[str]:
        return [doc for doc, listed in self.supersedes() if doc_id in listed]

    def superseded(self) -> set[str]:
        return {target for _, listed in self.supersedes() for target in listed}

    def covering(self, rid: str) -> list[ProcessedDoc]:
        """The docs that cover a record, in the order `DocStore.all` reads them."""
        index = self._ask()
        if index is not None:
            return self.store.at_paths(index.covering(rid))
        return [d for d in self._all() if rid in d.meta.covers]

    def close(self) -> None:
        if self._index is not None:
            self._index.close()


def _read(call: _Call, locator: str, view: View, around: int, rev: int | None) -> ReadResult:
    value = locator.strip()
    try:
        path = None if value.startswith(("[", "r:")) else split_path_locator(value)
        if path is not None and not (RID.match(value) and call.raw.versions(value)):
            return _read_path(call, locator, found=path, view=view, around=around, rev=rev)
        loc = parse_locator(locator, DOC_TYPES)
    except AnchorError as e:
        raise DocketError(str(e)) from None
    if loc.kind != "record":
        return _read_doc(call, _load(call.store, loc.kind, loc.value, rev), locator, view)
    if rev is not None:
        raise DocketError("rev is a doc's revision; for a record, the raw view says how many versions it has")
    versions = call.raw.versions(loc.value)
    if not versions:
        hint = " (for a position, write r:<rid>@mm:ss)" if "@" in loc.value or "#" in loc.value else ""
        raise DocketError(f"no record {loc.value} in the raw log{hint}")
    asked = versions[-1].rid
    anchor = loc.anchor or Anchor(asked, "record")
    result = ReadResult(locator=locator, view=view)
    if anchor.kind == "time":
        timed = call.raw.timed([asked])
        if len(timed) > 1:
            raise DocketError(f"{asked} has {len(timed)} transcripts; read one of {_options(timed, anchor)}")
        if timed and timed[0] != asked:
            anchor = _placed(timed[0], anchor)
            versions = call.raw.versions(timed[0])
            result.resolved = anchor.token[1:-1]
    record = versions[-1]
    if view != "summary":
        result.spans = _raw_spans(call.raw, record, anchor, len(versions), around)
    if view != "raw":
        # A transcript is read as one with its recording: the recording a time was placed from (DR-0370), or the one
        # it is under (DR-0391). A recording read whole is read with the transcripts under it (DR-0440); a paragraph
        # of it isn't, since a transcript's paragraphs are its own.
        rids = [record.rid, asked, *call.raw.recordings(record)]
        if anchor.kind == "record":
            rids += [t.rid for t in call.raw.transcripts(record.rid)]
        result.lines, result.legacy = _on(call, rids, anchor, around)
    return result


def _read_path(
    call: _Call, locator: str, *, found: tuple[str, Anchor | None], view: View, around: int, rev: int | None
) -> ReadResult:
    """A doc by a path: one in the vault's processed/ folder, else a legacy note by its old path; with a time, the
    raw at that time in the one transcript it covers, or that is under a recording it covers (DR-0370), and the lines
    on that transcript and its recording (DR-0440)."""
    path, at = found
    store = call.store
    processed = (store.root / "processed").resolve()
    given = Path(os.path.normpath(store.root / path.replace("\\", "/")))
    if given.is_relative_to(processed) and given.is_file():
        doc = _load(store, "path", str(given), rev)
    else:
        doc = _load(store, "doc", call.old_path(path), rev)
    result = _read_doc(call, doc, locator, view)
    result.resolved = doc.meta.id
    if at is None:
        return result
    lookup = call.raw
    timed = lookup.timed(doc.meta.covers)
    if len(timed) != 1:
        if timed:
            result.problems.append(f"{doc.meta.id} covers {len(timed)} transcripts; read one of {_options(timed, at)}")
        else:
            result.problems.append(
                f"{doc.meta.id} covers no recording with a transcript in the raw log, so {at.token} can't be placed; "
                "import the recording, then run docket import notes again"
            )
        return result
    rid = timed[0]
    anchor = _placed(rid, at)
    result.resolved = anchor.token[1:-1]
    versions = lookup.versions(rid)
    if view != "raw":
        # As r:<transcript>@<time> reads it: the transcript's lines, then those on the recording it is under (DR-0440).
        result.lines, _ = _on(call, [rid, *lookup.recordings(versions[-1])], anchor, around)
    found_span = span(anchor, versions[-1], len(versions), around=around)
    if found_span.problem is not None:
        result.problems.append(found_span.problem)
    if view != "summary":
        result.spans = [] if found_span.problem is not None else [found_span]
    return result


def _placed(rid: str, at: Anchor) -> Anchor:
    return Anchor(rid, at.kind, at.start, at.end, format_anchor(rid, at.kind, at.start, at.end))


def _options(rids: list[str], at: Anchor) -> str:
    return ", ".join(_placed(rid, at).token[1:-1] for rid in rids)


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
    return span(anchor, versions[-1], len(versions), window=window, lookup=lookup)


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


def _read_doc(call: _Call, doc: ProcessedDoc, locator: str, view: View) -> ReadResult:
    meta = doc.meta
    store = call.store
    result = ReadResult(locator=locator, view=view)
    if view != "raw":
        superseded_by = call.superseded_by(meta.id)
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
        for rid in meta.covers:
            versions = call.raw.versions(rid)
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


def _on(call: _Call, rids: list[str], at: Anchor, around: int) -> tuple[list[CitingLine], list[LegacyRef]]:
    """The lines of current docs on these records near the position, each once, in the order of the rids; and the
    legacy notes covering any of them."""
    lines: list[CitingLine] = []
    seen: set[tuple[str, int]] = set()
    notes: dict[str, ProcessedDoc] = {}
    for rid in dict.fromkeys(rids):
        docs = call.covering(rid)
        for cited in _citing(docs, call.superseded(), rid, at, around):
            if (cited.doc, cited.n) not in seen:
                seen.add((cited.doc, cited.n))
                lines.append(cited)
        notes.update((d.meta.id, d) for d in docs if d.meta.type == "legacy-note")
    return lines, [LegacyRef(id=d.meta.id, old_paths=d.meta.old_paths) for d in notes.values()]


def _citing(docs: list[ProcessedDoc], superseded: set[str], rid: str, at: Anchor, around: int) -> list[CitingLine]:
    """The lines of current docs (none supersedes them) whose anchors point at the record, near the position."""
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
