"""What a forget takes (DR-0578), worked out from what the vault holds now, with the dry run's lines and the `--now`
screen's (DR-0577).

| Kind | What it takes |
|---|---|
| a rid | it and every rid under it, staged or in raw, and the Whisper transcripts made from the media it is in |
| its media | the media on it and the rids it sits under, with every key; those rids go on the list's `media` |
| a held rid | its versions in raw, and the media on it and the rids it sits under, with every key; nothing on `media` |
| a folder source's file or part | its versions, staged and in raw, a file's parts with it, and a file's identity |
| `--never-store` | each rid with a version in raw that a never-store list in force matches now, as its kind says |
| `--held` | each rid in raw under a held id not released, as a held rid |

The docs are judged by the docs' rule against raw as the forget leaves it, its line on the list; a legacy note whose
every covered rid is in the family, or is a recording a family transcript is under, is removed, and its old paths'
hashes and `import_hash` go on the line. Raw is read twice, streamed, and only what each version needs is kept. The
ledger's ops are judged by the ledger's rule against the same (`forget.ledger`, DR-0694), and the ops it masks go on
the line by their ids.

The dry run counts by kind and gives line numbers: never a doc's id, a line's text, a title, a note's old path or a
listed value, and with `--never-store` records are counted by source. The screen at the console shows the same lines
with ids, but those a never-store list names, which it counts (DR-0558). Both name the ledger's shells and what else
the ops' masks touch by A- and M- numbers, never a word (DR-0697). Every dry run names what a forget can't reach.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from docket import index
from docket.copied import filled
from docket.documents import records as documents
from docket.errors import DocketError
from docket.forget import ledger as forget_ledger
from docket.forget.apply import Judged, judge, logged_keys, removed_before
from docket.index.build import open_index
from docket.ledger import ops as ledger_ops
from docket.media import layout
from docket.media import staged as media_staged
from docket.media.keystore import KeyStore
from docket.vault import curatelog, decision_form, forgetdocs, forgotten, holds, neverstore, releaselog
from docket.vault.anchors import AnchorError, parse_locator, trailing_anchors
from docket.vault.config import NeverStore
from docket.vault.docstore import DocStore
from docket.vault.forgotten import Forget, Forgotten, Version, identity_of, old_path_hash
from docket.vault.processed import DOC_TYPES, ProcessedDoc
from docket.vault.records import RID, is_native_id, version
from docket.vault.segments import segments, time_range
from docket.vault.staging import Staging, key_of, rid_hash
from docket.vault.vault import Vault

Kind = Literal["rids", "never-store", "held"]
How = Literal["whole", "held", "folder"]
# Each list on a screen stops here, then a count.
SHOWN = 20
WHISPER = "whisper"
LEGACY = "legacy-note"
CANT = (
    "key exports made before (make a new one, then destroy the old); the LFS server's encrypted copies and each "
    "clone's .git/lfs; the source itself; other clones until their next gate call; files outside the vault that "
    "quote it (question and speech files for docket eval, a Claude session that read it, an old ledger's export file "
    "docket ledger import reads); ledger text that names it in plain words, with no anchor or locator"
)
CANT_API = "chunks sent to the API provider vault.yaml allows"
CANT_NEVER = "docs that say a listed value without citing these records"
DOC_NAMES = {
    "recording-note": ("recording note", "recording notes"),
    "digest": ("digest", "digests"),
    "brief": ("brief", "briefs"),
    "decision": ("decision", "decisions"),
    "correction": ("correction", "corrections"),
    LEGACY: ("legacy note", "legacy notes"),
}


@dataclass(frozen=True)
class Taken:
    """One rid a forget takes, and how: whole, by its versions under a hold, or as a folder source's file or part."""

    rid: str
    how: How


@dataclass(frozen=True)
class DocCut:
    """What the forget makes of one doc: the lines it drops for their own anchors, those it drops for what they cite,
    and whether it is removed, or a final decision left with no evidence."""

    id: str
    type: str
    anchored: tuple[int, ...]
    built_on: tuple[int, ...]
    removed: bool
    no_evidence: bool
    # The sha256 of each line it drops, as its file holds it: what the history rewrite blanks, by hash (DR-0594).
    hashes: frozenset[str] = frozenset()


@dataclass(frozen=True, order=True)
class Stop:
    """What stops a forget: the screen's words, which name `id` when it has one, and the dry run's, which name no doc
    and stand in on the screen when a never-store list names the id (DR-0558)."""

    screen: str
    dry: str
    id: str = ""


@dataclass(frozen=True)
class Note:
    """A note on what a forget leaves: its words naming `rid`, and the same words without it, for a screen whose
    never-store list names the rid."""

    rid: str
    text: str
    unnamed: str


@dataclass(frozen=True)
class _Version:
    """A version as the plan keeps it: where it is (its run file, or "" when staged), its version, its source, whether
    a never-store list matches it, and a folder file's identity."""

    rel: str
    v: str
    source: str
    matched: bool
    identity: str | None


@dataclass
class Plan:
    """A forget worked out: what it takes, the list's line, and what the screens say."""

    kind: Kind
    asked: tuple[str, ...]
    source: str | None
    repo: Path
    vault_root: Path
    taken: list[Taken] = field(default_factory=list[Taken])
    # Every rid the forget takes records of, nested ones and Whisper transcripts included: in memory only.
    family: set[str] = field(default_factory=set[str])
    nothing: list[str] = field(default_factory=list[str])
    already: list[str] = field(default_factory=list[str])
    rids: set[str] = field(default_factory=set[str])
    versions: set[tuple[str, str]] = field(default_factory=set[tuple[str, str]])
    # The same versions as rid and version, for the history rewrite's expressions: in memory only.
    pairs: set[tuple[str, str]] = field(default_factory=set[tuple[str, str]])
    # The stored media files the forget takes, by their paths in the vault, for the rewrite's pointer blobs.
    stored: set[str] = field(default_factory=set[str])
    files: set[str] = field(default_factory=set[str])
    media: set[str] = field(default_factory=set[str])
    keys: set[str] = field(default_factory=set[str])
    keys_here: int = 0
    old_paths: set[str] = field(default_factory=set[str])
    import_hashes: set[str] = field(default_factory=set[str])
    records: int = 0
    runs: set[str] = field(default_factory=set[str])
    staged: int = 0
    stored_files: int = 0
    staged_files: int = 0
    by_source: Counter[str] = field(default_factory=Counter[str])
    docs: list[DocCut] = field(default_factory=list[DocCut])
    legacy_kept: list[str] = field(default_factory=list[str])
    entities: list[str] = field(default_factory=list[str])
    ops: list[str] = field(default_factory=list[str])
    # The ledger's ops the forget masks and what that leaves, by number (DR-0694).
    ledger: forget_ledger.Found = forget_ledger.NOTHING
    stops: list[Stop] = field(default_factory=list[Stop])
    notes: list[Note] = field(default_factory=list[Note])
    api: bool = False
    lists: NeverStore = field(default_factory=NeverStore)

    def line(self, machine: str, at: datetime) -> Forget:
        """The list's line for this forget: hashes only (DR-0579)."""
        return Forget(
            rids=sorted(rid_hash(r) for r in self.rids),
            versions=[Version(rid=r, key=k) for r, k in sorted(self.versions)],
            files=sorted(self.files),
            media=sorted(rid_hash(r) for r in self.media),
            keys=sorted(self.keys),
            old_paths=sorted(old_path_hash(p) for p in self.old_paths),
            import_hashes=sorted(self.import_hashes),
            ops=sorted(self.ledger.new),
            at=at,
            machine=machine,
        )

    def signature(self) -> tuple[object, ...]:
        """Everything the screen showed and the line holds, to tell whether the vault changed under the lock."""
        return (
            sorted((t.rid, t.how) for t in self.taken),
            sorted(self.family),
            sorted(self.versions),
            sorted(self.files),
            sorted(self.keys),
            sorted(self.old_paths),
            sorted(self.import_hashes),
            (self.records, sorted(self.runs), self.staged, self.stored_files, self.staged_files),
            sorted(self.docs, key=lambda d: d.id),
            sorted(self.legacy_kept),
            sorted(self.stops),
            self.ledger,
        )


# -- what the vault holds ----------------------------------------------------------------------------------------------


@dataclass
class _Held:
    """Raw and staging as the list in force leaves them, each rid's versions as the plan keeps them."""

    raw: dict[str, list[_Version]] = field(default_factory=dict[str, list[_Version]])
    staged: dict[str, list[_Version]] = field(default_factory=dict[str, list[_Version]])

    def rids(self) -> set[str]:
        return {*self.raw, *self.staged}

    def under(self, rid: str) -> list[str]:
        """The rid and every rid nested under it that raw or staging holds."""
        return sorted(r for r in self.rids() if r == rid or r.startswith(f"{rid}/"))


def _held(vault: Vault, lists: NeverStore | None) -> _Held:
    out = _Held()
    for path, _, record in vault.rawlog.iter_records():
        matched = lists is not None and neverstore.match(record, lists) is not None
        found = _Version(path.relative_to(vault.root).as_posix(), record.v or version(record), record.source, matched,
                         identity_of(record))  # fmt: skip
        out.raw.setdefault(record.rid, []).append(found)
    for entry in Staging(vault.root, vault.home).scan().entries:
        record = entry.staged.record
        out.staged.setdefault(record.rid, []).append(
            _Version("", record.v or version(record), record.source, False, identity_of(record))
        )
    return out


class _After:
    """Raw as the forget leaves it, for the docs' rule, read in run order under the list with the forget's line on it:
    each rid left with its latest version's segments, the rids whose lines go, and each transcript's span by the
    records it is under."""

    def __init__(self, vault: Vault, listed: Forgotten) -> None:
        self.listed = listed
        reading = listed.reading()
        self.left: set[str] = set()
        self.timed_rids: set[str] = set()
        self.taken: set[str] = set()
        self.ranges: dict[str, list[tuple[int, int]]] = {}
        for _, _, record in vault.rawlog.iter_records():
            if reading.skips(record):
                self.taken.add(record.rid)
                continue
            self.left.add(record.rid)
            said = segments(record)
            (self.timed_rids.add if said else self.timed_rids.discard)(record.rid)
            bounds = time_range(said) if record.kind == "transcript" else None
            if bounds is None:
                continue
            for parent in {record.thread, record.container}:
                if parent is not None and parent != record.rid:
                    self.ranges.setdefault(parent, []).append(bounds)

    def gone(self, rid: str) -> bool:
        if self.listed.whole(rid):
            return True
        if rid in self.left:
            return False
        return rid in self.taken or self.listed.versioned(rid)

    def timed(self, rid: str) -> bool:
        return rid in self.timed_rids

    def lands(self, rid: str, start: int, last: int) -> bool:
        return any(lo <= start and last <= hi for lo, hi in self.ranges.get(rid, []))

    def media(self, rid: str) -> bool:
        return rid_hash(rid) in self.listed.media


# -- working it out ----------------------------------------------------------------------------------------------------


def make(vault: Vault, kind: Kind, rids: Sequence[str] = (), source: str | None = None, *, stays: bool = True) -> Plan:
    """The forget of these rids, or of what `--never-store` or `--held` names, worked out from what the vault holds
    now. It writes nothing but the index's own update, which the entities' counts read; without `stays`, as under the
    vault lock, the index isn't touched and what stays isn't counted."""
    if kind == "rids" and (source is not None or not rids):
        raise DocketError("name the rids to forget; --source goes with --never-store or --held")
    if kind != "rids" and rids:
        raise DocketError("one kind a call: rids, or --never-store, or --held")
    listed = forgotten.load(vault.root, vault.home)
    plan = Plan(kind, tuple(rids), source, vault.home.top, vault.root)
    plan.api = vault.config.api_provider is not None
    plan.lists = vault.config.never_store
    held = _held(vault, plan.lists if kind == "never-store" else None)
    for taken in _taken(vault, plan, held, listed):
        plan.taken.append(taken)
        _take(plan, held, taken)
    if plan.taken:
        _media(vault, plan)
        _docs(vault, plan, listed)
        _counts(plan, held)
        if stays:
            _stays(vault, plan)
    return plan


def _taken(vault: Vault, plan: Plan, held: _Held, listed: Forgotten) -> list[Taken]:
    """The rids the forget takes, each with how."""
    lists = holds.read_all(vault.root)
    released = releaselog.read_all(vault.root)
    folders = {name for name, s in vault.config.sources.items() if s.folder is not None}

    def holding(rid: str) -> bool:
        src = rid.partition(":")[0]
        return holds.covered(rid, src, lists.of(src) - frozenset(released.of(src)))

    def how(rid: str) -> How:
        if holding(rid):
            return "held"
        return "folder" if documents.is_folder_rid(rid, folders) else "whole"

    if plan.kind == "rids":
        out: list[Taken] = []
        for rid in dict.fromkeys(plan.asked):
            if RID.match(rid) is None or not is_native_id(rid.partition(":")[2]):
                raise DocketError(f"{rid!r} isn't a rid: <source>:<id>")
            if listed.whole(rid):
                plan.already.append(rid)
            elif not held.under(rid):
                plan.nothing.append(rid)
            else:
                out.append(Taken(rid, how(rid)))
        return out
    wanted = [r for r in sorted(held.raw) if plan.source is None or r.partition(":")[0] == plan.source]
    if plan.kind == "held":
        return [Taken(rid, "held") for rid in wanted if holding(rid)]
    matched = [rid for rid in wanted if any(v.matched for v in held.raw[rid])]
    # A rid under another one matched goes with it.
    return [Taken(r, how(r)) for r in matched if not any(r.startswith(f"{other}/") for other in matched)]


def _take(plan: Plan, held: _Held, taken: Taken) -> None:
    rid = taken.rid
    if taken.how == "whole":
        made = [f"{owner}/{WHISPER}" for owner in layout.owners(rid)]
        whisper = [w for w in made if w != rid and not w.startswith(f"{rid}/") and held.under(w)]
        for whole in (rid, *whisper):
            plan.rids.add(whole)
            plan.family.update(held.under(whole))
            plan.media.update(layout.owners(whole))
        top = layout.owners(rid)[-1]
        if top != rid:
            plan.notes.append(
                Note(
                    top,
                    f"{top} stays, its audio gone with this; docket forget {top} takes it whole",
                    "the record it sits under stays, its audio gone with this; forgetting that record takes it whole",
                )
            )
        return
    if taken.how == "held":
        # Its versions in raw and those of the rids under it: a hold keeps everything staged out already.
        for one in (r for r in held.under(rid) if r in held.raw):
            plan.family.add(one)
            plan.versions.update((rid_hash(one), key_of(one, v.v)) for v in held.raw[one])
            plan.pairs.update((one, v.v) for v in held.raw[one])
        return
    # A folder source's file takes its parts; a part goes alone.
    for one in held.under(rid) if documents.file_of(rid) == rid else [rid]:
        found = [*held.raw.get(one, []), *held.staged.get(one, [])]
        plan.family.add(one)
        plan.versions.update((rid_hash(one), key_of(one, v.v)) for v in found)
        plan.pairs.update((one, v.v) for v in found)
        plan.files.update(v.identity for v in found if v.identity is not None)
    if documents.file_of(rid) == rid:
        plan.notes.append(
            Note(
                rid,
                f"{rid} stays in its folder: move the file out, since a forget never writes there; a changed file, or "
                "a new one at its path, comes in",
                "a folder file stays in its folder: move it out, since a forget never writes there; a changed file, or "
                "a new one at its path, comes in",
            )
        )


def _media(vault: Vault, plan: Plan) -> None:
    """The media files the forget takes and every key of theirs: on each rid it takes, the rids they sit under and the
    rids under them. A stored file's key from its header, else from the cut log; a file whose key nothing names stops
    the forget, since its key could never leave the other clones (DR-0578)."""
    root = vault.root
    on: set[str] = set(plan.media) | set(plan.family)
    for taken in plan.taken:
        on.update(layout.owners(taken.rid))
    for rid in sorted(on):
        logged = logged_keys(root, layout.source_of(rid), rid_hash(rid))
        if rid in plan.media:
            # Every key the cut log names for a rid forgotten whole: files a recut replaced included.
            plan.keys.update(logged)
        for path in layout.stored_files(root, rid):
            plan.stored_files += 1
            plan.stored.add(path.relative_to(root).as_posix())
            key = media_staged.key_of(path)
            if key is not None:
                plan.keys.add(key)
            elif logged:
                plan.keys.update(logged)
            else:
                rel = path.relative_to(root).as_posix()
                plan.stops.append(
                    Stop(
                        f"{rel} is an LFS pointer and no cut log line names its key: git lfs pull it, then forget",
                        "a stored media file is an LFS pointer whose key no cut log line names: git lfs pull, then "
                        "forget",
                    )
                )
        plan.staged_files += len(layout.staged_files(root, rid))
        sidecar = media_staged.read_sidecar(layout.sidecar_path(root, rid))
        if sidecar is not None:
            plan.keys.add(sidecar.key)
    keys = KeyStore(vault.home)
    plan.keys_here = sum(1 for key in plan.keys if keys.has(key))


def with_line(vault: Vault, plan: Plan, listed: Forgotten) -> Forgotten:
    """The list in force with this forget's line on it, HEAD's lines the disk lacks included."""
    line = plan.line(vault.machine, datetime.now(UTC))
    return Forgotten(
        rids=listed.rids | set(line.rids),
        versions=listed.versions | {(v.rid, v.key) for v in line.versions},
        files=listed.files | set(line.files),
        media=listed.media | set(line.media),
        keys=listed.keys | set(line.keys),
        old_paths=listed.old_paths | set(line.old_paths),
        import_hashes=listed.import_hashes | set(line.import_hashes),
        ops=listed.ops | set(line.ops),
        root=vault.root,
    )


def _docs(vault: Vault, plan: Plan, listed: Forgotten) -> None:
    store = DocStore(vault)
    docs = store.all()
    after = _After(vault, with_line(vault, plan, listed))
    parents = {owner for rid in plan.family for owner in layout.owners(rid)[1:]}
    for doc in docs:
        if doc.meta.type == LEGACY and _legacy_in_family(doc, after, parents):
            plan.old_paths.update(doc.meta.old_paths)
            if doc.meta.import_hash is not None:
                plan.import_hashes.add(doc.meta.import_hash)
    # The legacy notes' hashes reach only the rule, never raw's reading.
    after.listed = with_line(vault, plan, listed)
    fates = judge(store, docs, after, after.listed)
    by_id = {d.meta.id: d for d in docs}
    for doc_id, fate in sorted(fates.items()):
        doc = by_id[doc_id]
        if doc.meta.type == LEGACY:
            if fate.doc is None:
                plan.docs.append(DocCut(doc_id, LEGACY, (), (), removed=True, no_evidence=False))
            else:
                plan.legacy_kept.append(doc_id)
            continue
        anchored = forgetdocs.anchored(doc, after) & set(fate.dropped)
        no_evidence = fate.doc is not None and decision_form.evidence_gone(fate.doc)
        built_on = tuple(sorted(set(fate.dropped) - anchored))
        source = doc.source.split("\n")
        hashes = frozenset(_hash(source[n - 1]) for n in fate.dropped if 0 < n <= len(source))
        cut = DocCut(doc_id, doc.meta.type, tuple(sorted(anchored)), built_on, fate.doc is None, no_evidence, hashes)
        plan.docs.append(cut)
        if doc.meta.type == "decision" and doc.meta.status in decision_form.FINAL:
            path = store.file(doc_id)
            if path.is_file() and decision_form.edited(store.read_file(path), store.committed_text(doc_id)):
                plan.stops.append(
                    Stop(
                        f"{doc_id} was edited on disk: check it out again, then forget",
                        "a confirmed or rejected decision was edited on disk: check it out again, then forget",
                        doc_id,
                    )
                )
    _unreadable(plan, docs)
    # The docs this forget removes, and those an earlier one's commit did, as the apply's match again reads them.
    removed = {*(c.id for c in plan.docs if c.removed), *removed_before(vault)}
    lines = ledger_ops.read_all(vault.root, masked=listed.ops).lines
    plan.ledger = forget_ledger.match(lines, after, after.listed, Judged(store, after, after.listed, removed))


def _hash(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def _legacy_in_family(doc: ProcessedDoc, after: _After, parents: Collection[str]) -> bool:
    """Whether a legacy note covers only what the forget takes: each rid it covers in the family, or a recording with
    no segments of its own that a family rid sits under, as an imported transcript note covers its recording."""
    if not doc.meta.covers:
        return False
    return all(after.gone(rid) or (not after.timed(rid) and rid in parents) for rid in doc.meta.covers)


def _unreadable(plan: Plan, docs: Iterable[ProcessedDoc]) -> None:
    """A doc whose anchors this Docket can't read and whose text holds a rid the forget takes stops it (DR-0541)."""
    for doc in docs:
        if doc.meta.type == LEGACY:
            continue
        for n, line, role in doc.roles():
            if role == "content" and trailing_anchors(line).errors and any(rid in line for rid in plan.family):
                plan.stops.append(
                    Stop(
                        f"{doc.meta.id} line {n}: an anchor this Docket can't read names what the forget takes; "
                        "update Docket, or fix the line",
                        f"a {_one(doc.meta.type)} line {n}: an anchor this Docket can't read names what the forget "
                        "takes; update Docket, or fix the line",
                        doc.meta.id,
                    )
                )


def _counts(plan: Plan, held: _Held) -> None:
    for rid in sorted(plan.family):
        whole = any(rid == w or rid.startswith(f"{w}/") for w in plan.rids)
        for found in held.raw.get(rid, []):
            if whole or (rid_hash(rid), key_of(rid, found.v)) in plan.versions:
                plan.records += 1
                plan.runs.add(found.rel)
                plan.by_source[found.source] += 1
        for found in held.staged.get(rid, []):
            if whole or (rid_hash(rid), key_of(rid, found.v)) in plan.versions:
                plan.staged += 1


def _stays(vault: Vault, plan: Plan) -> None:
    """The entities only the family mentions, from the index brought up to date as a search does, and the curate ops
    whose locators all lie in the family: what stays, each named on the screen with its undo."""
    conn: sqlite3.Connection | None = None
    with contextlib.suppress(DocketError, sqlite3.Error):
        index.update(vault)
        conn = open_index(vault)
    if conn is not None:
        try:
            plan.entities = _only_here(conn, plan.family)
        finally:
            conn.close()
    for line in curatelog.read_all(vault.root).lines:
        locators = line.evidence.locators if line.evidence is not None else ()
        if locators and all(_in(locator, plan.family) for locator in locators):
            plan.ops.append(line.id)


def _only_here(conn: sqlite3.Connection, family: Collection[str]) -> list[str]:
    """The entities every record chunk that mentions them lies in the family: no other record mentions them. A doc's
    mention doesn't count, since the forget drops a doc's lines on the family."""
    seen: dict[str, bool] = {}
    query = "SELECT m.entity, c.rids FROM mentions m JOIN chunks c ON c.id = m.chunk WHERE c.doc IS NULL"
    for entity, rids in conn.execute(query):
        found: list[str] = json.loads(rids)
        seen[entity] = seen.get(entity, True) and all(r in family for r in found)
    return sorted(e for e, only in seen.items() if only)


def _in(locator: str, family: Collection[str]) -> bool:
    try:
        found = parse_locator(locator, DOC_TYPES)
    except AnchorError:
        return False
    return found.kind == "record" and found.value in family


# -- the screens -------------------------------------------------------------------------------------------------------


def _first(items: Sequence[str], sep: str = ", ") -> str:
    shown = sep.join(items[:SHOWN])
    return shown + (f"{sep}and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _one(doc_type: str) -> str:
    return DOC_NAMES.get(doc_type, (doc_type, doc_type))[0]


def _docs_of(doc_type: str, n: int) -> str:
    one, many = DOC_NAMES.get(doc_type, (doc_type, f"{doc_type}s"))
    return f"{n} {one if n == 1 else many}"


class _Ids:
    """The ids a screen may show: those a never-store list names are counted instead (DR-0558)."""

    def __init__(self, lists: NeverStore) -> None:
        self.lists = lists
        self.hidden: set[str] = set()

    def shown(self, ids: Iterable[str]) -> list[str]:
        out: list[str] = []
        for one in ids:
            if neverstore.matches_text(one, self.lists):
                self.hidden.add(one)
            else:
                out.append(one)
        return out


def _doc_groups(cuts: Sequence[DocCut], lines: Callable[[DocCut], tuple[int, ...]], ids: _Ids | None) -> list[str]:
    """Each doc type's lines: by type with line numbers in the dry run, by id on the screen."""
    out: list[str] = []
    by_type: dict[str, list[DocCut]] = {}
    for cut in cuts:
        if lines(cut):
            by_type.setdefault(cut.type, []).append(cut)
    for doc_type, group in sorted(by_type.items()):
        total = sum(len(lines(c)) for c in group)
        numbers = {c.id: _first([str(n) for n in lines(c)]) for c in group}
        each = [f"{i} {numbers[i]}" for i in ids.shown(numbers)] if ids is not None else list(numbers.values())
        # A group whose every id a never-store list names shows its counts alone.
        shown = f" ({_first(each, '; ')})" if each else ""
        out.append(f"{_plural(total, 'line')} in {_docs_of(doc_type, len(group))}{shown}")
    return out


def lines(plan: Plan, *, screen: bool) -> list[str]:
    """What the dry run prints, counts and line numbers only, or with `screen` what the `--now` screen does, with
    ids."""
    out = [f"already   {rid} was forgotten" for rid in plan.already]
    out += [f"nothing   nothing stored as {rid}" for rid in plan.nothing]
    if not plan.taken:
        return out
    ids = _Ids(plan.lists) if screen else None

    def named(found: Iterable[str]) -> str:
        """The ids, on the screen only, in brackets after what counts them."""
        shown = ids.shown(found) if ids is not None else []
        return f" ({_first(shown)})" if shown else ""

    runs = _plural(len(plan.runs), "run file")
    staged = f", {plan.staged} staged" if plan.staged else ""
    taken = {t.rid for t in plan.taken}
    counted = ", ".join(f"{n} in {s}" for s, n in sorted(plan.by_source.items()))
    if plan.kind == "rids" or ids is not None:
        how = {"whole": "", "held": " (held: its versions in raw)", "folder": " (a folder file: its versions)"}
        kinds = {t.rid: how[t.how] for t in plan.taken}
        heads = [f"{rid}{kinds[rid]}" for rid in (ids.shown(kinds) if ids is not None else kinds)]
        under = len(plan.family - taken)
        more = f" and {_plural(under, 'record')} with it" if under and ids is None else ""
        head = f"{_first(heads) or _plural(len(kinds), 'record')}{more}"
    elif plan.kind == "never-store":
        head = f"records a never-store list matches ({counted})"
    else:
        head = f"records in raw under a hold not released ({counted})"
    out.append(f"forget    {head}: {_plural(plan.records, 'version')} in {runs}{staged}")
    rest = ids.shown(sorted(plan.family - taken)) if ids is not None else []
    if rest:
        out.append(f"records   {_first(rest)}")
    if plan.stored_files or plan.staged_files or plan.keys:
        out.append(
            f"media     {_plural(plan.stored_files, 'stored file')} and {plan.staged_files} staged; "
            f"{_plural(len(plan.keys), 'key')}, {plan.keys_here} of them here"
        )
    anchored = _doc_groups(plan.docs, lambda c: c.anchored, ids)
    removed = [c.id for c in plan.docs if c.removed and c.type == LEGACY]
    if anchored or removed:
        legacy = f"; {_docs_of(LEGACY, len(removed))} removed{named(removed)}" if removed else ""
        out.append(f"docs      {'; '.join(anchored) or 'no line'}{legacy}")
    built = _doc_groups(plan.docs, lambda c: c.built_on, ids)
    if built:
        out.append(f"built on  {'; '.join(built)}: dropped too")
    gone = [c.id for c in plan.docs if c.removed and c.type != LEGACY]
    if gone:
        out.append(f"removed   {_plural(len(gone), 'doc')} left with no line{named(gone)}")
    final = [c.id for c in plan.docs if c.no_evidence]
    if final:
        out.append(f"decision  {_plural(len(final), 'final decision')} keeps its title, no evidence left{named(final)}")
    if plan.legacy_kept:
        where = named(plan.legacy_kept) if ids is not None else ", named at the console"
        out.append(f"by hand   {_docs_of(LEGACY, len(plan.legacy_kept))} covering more keep their text{where}")
    out += _ledger_lines(plan.ledger)
    if plan.entities or plan.ops:
        what = f"{_plural(len(plan.entities), 'entity', 'entities')} nothing else mentions and " + _plural(
            len(plan.ops), "curate op"
        )
        if ids is not None:
            out.append(f"stays     {what} whose locators all lie here")
            out += [f"          entity {e}" for e in ids.shown(plan.entities)[:SHOWN]]
            out += [f"          docket curate undo {op}" for op in plan.ops[:SHOWN]]
        else:
            out.append(f"stays     {what} whose locators all lie here: named at the console")
    if plan.kind == "rids" or screen:
        out += [f"note      {_noted(note, ids)}" for note in plan.notes]
    out += [f"stops     {_stopped(stop, ids)}" for stop in plan.stops]
    if ids is not None and ids.hidden:
        out.append(f"unnamed   {_plural(len(ids.hidden), 'id')} a never-store list names")
    out.append(f"history   the text stays in the history of {plan.repo}; the forget writes how to take it out")
    cant = [CANT, *([CANT_API] if plan.api else []), *([CANT_NEVER] if plan.kind == "never-store" else [])]
    out.append(f"can't     {'; '.join(cant)}")
    return out


def _ledger_lines(found: forget_ledger.Found) -> list[str]:
    """What the ops' masks leave, by A- and M- numbers on both screens: never a word, a stream's name included."""
    out: list[str] = []

    def shells(numbers: Sequence[str]) -> str:
        return f"{_first(numbers)} {'is a shell' if len(numbers) == 1 else 'are shells'}"

    if found.new:
        left = f"; {shells(found.shells)} now" if found.shells else ""
        out.append(f"ledger    {_plural(len(found.new), 'op')} masked{left}")
    if found.open:
        # Shells, and points masked for their own words on a kept meeting, which never roll on.
        out.append(f"drop      {_first(found.open)} left open, to drop")
    out += [f"kept      {one}" for one in found.kept[:SHOWN]]
    if len(found.kept) > SHOWN:
        out.append(f"kept      and {len(found.kept) - SHOWN} more")
    if found.streams:
        out.append(f"streams   {_plural(found.streams, 'stream op')} masked, the streams kept")
    out += [f"raise     {head}: raise it again" for head in found.heads[:SHOWN]]
    if len(found.heads) > SHOWN:
        out.append(f"raise     and {len(found.heads) - SHOWN} more")
    return out


def _noted(note: Note, ids: _Ids | None) -> str:
    return note.text if ids is None or ids.shown([note.rid]) else note.unnamed


def _stopped(stop: Stop, ids: _Ids | None) -> str:
    if ids is None:
        return stop.dry
    return stop.screen if not stop.id or ids.shown([stop.id]) else stop.dry


def command(plan: Plan, unfilled: list[tuple[str, str]] | None = None) -> str:
    """The `--now` line to run at the owner's own console, each rid in double quotes as the vault path is. A rid
    holding what a shell reads as more than itself stays its placeholder, `<rid 1>`, and goes on `unfilled` with its
    value, to put in by hand (DR-0382)."""
    left = unfilled if unfilled is not None else []
    if plan.kind == "rids":
        what = " ".join(f'"{filled(t.rid, f"rid {n}", left)}"' for n, t in enumerate(plan.taken, start=1))
    else:
        what = f"--{plan.kind}" + (f' --source "{plan.source}"' if plan.source else "")
    return f'docket forget {what} --vault "{plan.vault_root}" --now'
