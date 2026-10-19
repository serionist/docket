"""The apply (DR-0581): the forgotten list against what this clone's tree, staging and key store hold. A forget runs it
once, under the vault lock after the list's commit, and every gate call on every clone runs it first, so a crash at any
step, a pull, a line a union merge brought back, or a clone's own copy of a record from before its pull is finished at
the next call. Until then every reader skips what the list takes.

| Part | What goes |
|---|---|
| keys | every listed key this clone holds |
| staging | each staged version the list takes, with the media on it and the rids it sits under; a whole rid withheld |
| raw | each listed line, out of the run files in the tree; a file left with no line stays, empty |
| media | each stored file on a rid the list's `media` holds or forgotten whole, or whose key is listed, and its keys |
| docs | the lines the docs' rule drops, each doc as `forgetdocs` leaves it, its hand edit taken in first; empty, gone |
| cursors | this machine's retry and waiting entries on a rid forgotten whole (DR-0245) |
| ledger | each listed op's line in every machine's op file, as its mask; a twin masking made, a union merge's, gone |

A key the apply deletes that no line names, a stored file's this clone made before its pull, goes on a keys-only line
(DR-0595), so the clones that imported its export delete it too. So does an op the ledger's rule masks that no line
names: the rule is matched again over every op line once the list's digest moved, and over the whole ledger when a
line an op file gained looks like one it masks, so a clone's own op from before its pull, or one a pull brought, is
found (DR-0694). A stream whose ops it masked has its STATUS.md rendered again once the lock is let go (DR-0695).

Keys and staging are this clone's own and go at once. The tree waits while git is in the middle of something the owner
runs, a rebase, a merge, a detached HEAD or unmerged paths, since its commit would be refused and the owner's
`git rebase --continue` would then refuse Docket's changes (DR-0597); every reader skips what the list takes meanwhile.

Each file is written whole through a temp file and a rename, and all of them go in one commit. The paths, and the docs'
revs, are recorded before anything in the tree changes, so a crash or a failed commit leaves them for the next call to
commit. With no list the apply costs a stat; otherwise `<private>/forget/applied.json` keeps the list it applied, the
HEAD it last walked the docs and media at, and the size and modification time each run file, doc, media file, cursor
file and op file had when it was read, a media file's key with it, so only what moved since is read again. Nothing it
keeps or reports names a record. Op files are read raw and written through the ledger's one writer (DR-0693, DR-0695).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from docket.errors import DocketError
from docket.forget import ledger as forget_ledger
from docket.ledger import ops as ledger_ops
from docket.ledger import status
from docket.ledger import store as ledger_store
from docket.media import cutlog, layout
from docket.media import staged as media_staged
from docket.media.keystore import KeyStore
from docket.vault import cursors, decision_form, forgetdocs, forgotten
from docket.vault.anchors import DocRef
from docket.vault.docstore import DocStore
from docket.vault.forgotten import FORGOTTEN, Forget, Forgotten
from docket.vault.home import Home
from docket.vault.processed import DocRefusedError, ProcessedDoc, doc_path
from docket.vault.rawlog import RunFile, lines_of, record_of
from docket.vault.segments import segments, time_range
from docket.vault.spans import RawLookup
from docket.vault.staging import Staging, retry, rid_hash, write_replacing
from docket.vault.vault import Vault

log = logging.getLogger(__name__)

FOLDER = "forget"
APPLIED = "applied.json"
SCHEMA_VERSION = 1

Stamp = tuple[int, int]
# A stored media file's size and modification time, and its key id as its header named it then: None for a header that
# doesn't read, an LFS pointer's, and UNREAD while no listed key asked for it.
MediaStamp = tuple[int, int, str | None]
UNREAD = ""


@dataclass
class Applied:
    """What one apply did, by counts: never a rid."""

    keys: int = 0
    staged: int = 0
    staged_media: int = 0
    records: int = 0
    media: int = 0
    docs: int = 0
    removed: int = 0
    # Ledger op lines written as their masks.
    ops: int = 0
    # Ops the ledger's rule masked that no line named, now on an ops-only line.
    listed_ops: int = 0
    # The streams whose ops were masked, by name, and what rendering their STATUS.md said (DR-0675).
    streams: list[str] = field(default_factory=list[str])
    rendered: tuple[str, ...] = ()
    # Docs the rule changes that stay as they are: a hand edit its take-in refuses, or a final decision edited on disk.
    left: list[str] = field(default_factory=list[str])
    # Keys this clone held that no line named, now on a keys-only line.
    listed_keys: int = 0
    commit: str | None = None
    # Why the tree's part waits for the owner's git (DR-0597); keys and staging went all the same.
    waits: str | None = None


@dataclass
class _State:
    """`applied.json`: the list as it was applied, keys too; the HEAD the docs and media were last walked at; the
    stamps of what it read, each taken before the read, op files only once the list masks an op; the docs it left,
    watched on every call; the folder files whose parts it takes from here on; the hashes of the rids whose lines it
    took; and the paths written but not committed, with the revs of the docs among them."""

    applied: str = ""
    # The digest of what raw and the docs are judged by: a keys-only line leaves it as it was (DR-0593).
    digest: str = ""
    head: str | None = None
    runs: dict[str, Stamp] = field(default_factory=dict[str, Stamp])
    docs: dict[str, Stamp] = field(default_factory=dict[str, Stamp])
    media: dict[str, MediaStamp] = field(default_factory=dict[str, MediaStamp])
    cursor: Stamp | None = None
    ledger: dict[str, Stamp] = field(default_factory=dict[str, Stamp])
    left: dict[str, Stamp | None] = field(default_factory=dict[str, Stamp | None])
    files: list[str] = field(default_factory=list[str])
    skipped: list[str] = field(default_factory=list[str])
    pending: list[str] = field(default_factory=list[str])
    revs: dict[str, int] = field(default_factory=dict[str, int])

    def dump(self) -> str:
        return json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "list": self.applied,
                "digest": self.digest,
                "head": self.head,
                "runs": self.runs,
                "docs": self.docs,
                "media": self.media,
                "cursor": self.cursor,
                "ledger": self.ledger,
                "left": self.left,
                "files": sorted(self.files),
                "skipped": sorted(self.skipped),
                "pending": sorted(self.pending),
                "revs": self.revs,
            },
            separators=(",", ":"),
        )


def _reached(step: str) -> None:
    """A test seam: a crash test raises here, after the step."""


# -- the state ---------------------------------------------------------------------------------------------------------


def state_path(home: Home) -> Path:
    return home.private / FOLDER / APPLIED


def _stamps(value: Any) -> dict[str, Stamp]:
    return {str(rel): (int(size), int(mtime)) for rel, (size, mtime) in value.items()}


def _media_of(value: Any) -> dict[str, MediaStamp]:
    out: dict[str, MediaStamp] = {}
    for rel, (size, mtime, key) in value.items():
        out[str(rel)] = (int(size), int(mtime), None if key is None else str(key))
    return out


def _read_state(home: Home) -> _State:
    """What the last apply kept; one that doesn't read, or that a later Docket wrote, is no state, so everything is
    read again."""
    try:
        data: Any = json.loads(state_path(home).read_text(encoding="utf-8"))
        cursor = data["cursor"]
        head = data["head"]
        found = _State(
            applied=str(data["list"]),
            digest=str(data["digest"]),
            head=None if head is None else str(head),
            runs=_stamps(data["runs"]),
            docs=_stamps(data["docs"]),
            media=_media_of(data["media"]),
            cursor=None if cursor is None else (int(cursor[0]), int(cursor[1])),
            # An apply from before the ledger's masks kept none: every op file is read once.
            ledger=_stamps(data.get("ledger", {})),
            left={str(rel): None if s is None else (int(s[0]), int(s[1])) for rel, s in data["left"].items()},
            files=[str(f) for f in data["files"]],
            skipped=[str(s) for s in data["skipped"]],
            pending=[str(p) for p in data["pending"]],
            revs={str(doc_id): int(rev) for doc_id, rev in data["revs"].items()},
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return _State()
    return found if data["schema_version"] == SCHEMA_VERSION else _State()


def skipped(home: Home) -> frozenset[str]:
    """The hashes of the rids whose lines an apply here took, for a read to judge docs as the apply did."""
    return frozenset(_read_state(home).skipped)


def _write_state(home: Home, state: _State) -> None:
    path = state_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_replacing(home, path, state.dump(), "forget-applied")


def key_of(listed: Forgotten) -> str:
    """The list as the apply follows it, keys too, which change no index row but leave every clone: empty with no
    list."""
    if not (listed.digest or listed.keys):
        return ""
    text = f"{listed.digest}\n{','.join(sorted(listed.keys))}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stamp(path: Path) -> Stamp | None:
    """A file's size and modification time, or None when it isn't there."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return st.st_size, st.st_mtime_ns


# -- the parts ---------------------------------------------------------------------------------------------------------


def drop_keys(listed: Forgotten, keys: KeyStore) -> int:
    """Every listed key this clone holds, deleted: what it opened can't be opened here again (DR-0595)."""
    gone = 0
    for key in sorted(listed.keys):
        if keys.has(key):
            keys.delete(key)
            gone += 1
    return gone


def drop_listed_keys(vault: Vault, keys: KeyStore | None = None) -> int:
    """The listed keys this clone holds, deleted, as the service does at its start: a clone that only searches and plays
    loses them too (DR-0595)."""
    return drop_keys(forgotten.load(vault.root, vault.home), keys or KeyStore(vault.home))


def _staging(vault: Vault, keys: KeyStore, listed: Forgotten) -> tuple[int, int]:
    """Each staged version the list takes, in the order it was staged, so a folder file takes its parts; then each
    staged media file on a rid no media is stored on again, or whose key is listed. How many versions and media files
    went."""
    root = vault.root
    head = vault.git.in_head
    staging = Staging(root, vault.home)
    reading = listed.reading()
    versions = media = 0
    for entry in sorted(staging.scan().entries, key=lambda e: (e.staged.staged_at, e.key)):
        record = entry.staged.record
        if not reading.skips(record):
            continue
        media += media_staged.drop(root, keys, record.rid, head)
        if listed.whole(record.rid):
            media_staged.withhold(root, record.rid)
        staging.delete(entry.path)
        versions += 1
    base = root / "staging" / layout.STAGED
    for sidecar_path in sorted(base.glob("*/*.json")) if base.is_dir() else []:
        sidecar = media_staged.read_sidecar(sidecar_path)
        if sidecar is not None and (listed.media_refused(sidecar.rid) or sidecar.key in listed.keys):
            media += media_staged.delete(root, keys, sidecar.rid, head)
    return versions, media


@dataclass
class _Raw:
    """What the list takes from the run files read: each file's text as it is left, the rids whose lines went, how many
    lines, and the folder files whose parts go from here on."""

    left: dict[Path, str] = field(default_factory=dict[Path, str])
    rids: set[str] = field(default_factory=set[str])
    lines: int = 0
    files: frozenset[str] = frozenset()


def _raw(vault: Vault, listed: Forgotten, state: _State, moved: bool, runs: Sequence[tuple[RunFile, Stamp]]) -> _Raw:
    """The listed lines of each run file that moved, or of every one when the list did, in run order. A line that
    doesn't read as a record is kept as it is: only a line the list takes ever leaves a run file. Lines are split at
    LFs alone, as every reader of run files splits them."""
    out = _Raw()
    reading = listed.reading(() if moved else state.files)
    for run, stamp in runs:
        rel = run.path.relative_to(vault.root).as_posix()
        if not moved and state.runs.get(rel) == stamp:
            continue
        text = run.path.read_bytes().decode("utf-8")
        kept: list[str] = []
        taken = 0
        for line in lines_of(text):
            record = record_of(line) if line.strip() else None
            if record is not None and reading.skips(record):
                out.rids.add(record.rid)
                taken += 1
                continue
            kept.append(line)
        if taken:
            out.left[run.path] = "".join(kept)
            out.lines += taken
    out.files = reading.files
    return out


def logged_keys(root: Path, source: str, stem: str) -> set[str]:
    """Every key the media cut log names for a rid's hash: its stored file's, and those of files a recut replaced."""
    return {line.key for line in cutlog.read(root, source) if line.rid == stem and line.key is not None}


class _Logged:
    """The keys the media cut log names, by source and rid hash, each source's log read once."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._by: dict[str, dict[str, set[str]]] = {}

    def of(self, source: str, stem: str) -> set[str]:
        found = self._by.get(source)
        if found is None:
            found = dict[str, set[str]]()
            for line in cutlog.read(self.root, source):
                if line.key is not None:
                    found.setdefault(line.rid, set()).add(line.key)
            self._by[source] = found
        return set(found.get(stem, set()))


@dataclass
class _Media:
    """The stored files the list takes and the keys to delete with them, gathered before anything goes; the keys among
    them no line names; and each file's stamp and key as read now."""

    removed: list[Path] = field(default_factory=list[Path])
    keys: set[str] = field(default_factory=set[str])
    unlisted: set[str] = field(default_factory=set[str])
    seen: dict[str, MediaStamp] = field(default_factory=dict[str, MediaStamp])


def _media(
    vault: Vault, listed: Forgotten, cached: Mapping[str, MediaStamp], now: Mapping[str, Stamp], taken: Iterable[str]
) -> _Media:
    """Each stored file the list takes: on a rid its `media` holds, or one forgotten whole whose lines went now, with
    every key the cut log names for that rid; a file whose own key is listed; or an LFS pointer, whose key can't be
    read, when every key the cut log names for its rid is listed, as a version forget lists them. A file's key comes
    from its stamp's cache while the file hasn't moved, else from its header, read only once a key is listed or its rid
    is. Nothing is deleted here."""
    root = vault.root
    out = _Media()
    hashes = set(listed.media) | {rid_hash(rid) for rid in taken if listed.whole(rid)}
    logged = _Logged(root)
    for rel, stamp in sorted(now.items()):
        path = root / rel
        held = cached.get(rel)
        key = held[2] if held is not None and held[:2] == stamp else UNREAD
        stem, source = path.name.split(".", 1)[0], path.parts[-4]
        if key == UNREAD and (stem in hashes or listed.keys):
            key = media_staged.key_of(path)
        out.seen[rel] = (*stamp, key)
        if stem in hashes:
            found = ({key} if key else set[str]()) | logged.of(source, stem)
        elif key == UNREAD:
            # No key is listed, so no file is taken by its own.
            continue
        elif key is not None:
            if key not in listed.keys:
                continue
            found = {key}
        else:
            found = logged.of(source, stem)
            if not found or not found <= listed.keys:
                continue
        out.removed.append(path)
        out.keys |= found
        out.unlisted |= found - listed.keys
    return out


class Left:
    """Raw as the list leaves it, for the docs' rule (DR-0580): read through a lookup that skips listed lines, with the
    hashes of the rids whose lines a forget took."""

    def __init__(self, lookup: RawLookup, listed: Forgotten, skipped: Iterable[str]) -> None:
        self.lookup = lookup
        self.listed = listed
        self.skipped = frozenset(skipped)

    def gone(self, rid: str) -> bool:
        if self.listed.whole(rid):
            return True
        # The list's hashes before raw: a rid neither taken nor versioned is never gone, and the ledger asks of every
        # locator it holds (DR-0694).
        if rid_hash(rid) not in self.skipped and not self.listed.versioned(rid):
            return False
        return not self.lookup.versions(rid)

    def timed(self, rid: str) -> bool:
        latest = self.lookup.latest(rid)
        return latest is not None and bool(segments(latest))

    def lands(self, rid: str, start: int, last: int) -> bool:
        for transcript in self.lookup.transcripts(rid):
            bounds = time_range(segments(transcript))
            if bounds is not None and bounds[0] <= start and last <= bounds[1]:
                return True
        return False

    def media(self, rid: str) -> bool:
        return rid_hash(rid) in self.listed.media


def readers(store: DocStore) -> tuple[forgetdocs.Current, forgetdocs.Older]:
    """How the docs' rule reads what refs lead to, through the store: each cited doc as it reads now, and each
    earlier rev from git."""

    def current(wanted: Iterable[str]) -> dict[str, ProcessedDoc]:
        out: dict[str, ProcessedDoc] = {}
        for doc_id in wanted:
            try:
                found = store.get(doc_id)
            except DocketError:
                continue
            if found is not None:
                out[doc_id] = found
        return out

    def older(wanted: Iterable[tuple[str, int]]) -> dict[tuple[str, int], ProcessedDoc]:
        out: dict[tuple[str, int], ProcessedDoc] = {}
        for doc_id, rev in wanted:
            try:
                out[(doc_id, rev)] = store.at_rev(doc_id, rev)
            except DocketError:
                continue
        return out

    return current, older


def judge(
    store: DocStore, docs: Sequence[ProcessedDoc], raw: forgetdocs.Raw, listed: Forgotten
) -> dict[str, forgetdocs.Fate]:
    """What the docs' rule makes of these docs, reading what their refs lead to through the store."""
    current, older = readers(store)
    return forgetdocs.fates(docs, raw, listed, current=current, older=older)


class Judged:
    """The docs as a forget leaves them, for the ledger's rule (DR-0694): the docs it removes, and each ref an op's text
    holds judged by the docs' rule against raw as the forget leaves it, in the rev the ref names."""

    def __init__(self, store: DocStore, raw: forgetdocs.Raw, listed: Forgotten, removed: Iterable[str]) -> None:
        self.store = store
        self.raw = raw
        self.listed = listed
        self.gone = frozenset(removed)

    def removed(self, doc_id: str) -> bool:
        return doc_id in self.gone

    def cited(self, refs: Collection[DocRef]) -> set[DocRef]:
        current, older = readers(self.store)
        return forgetdocs.cited(refs, self.raw, self.listed, current=current, older=older)


@dataclass
class _Docs:
    writes: list[tuple[str, str | None]] = field(default_factory=list[tuple[str, str | None]])
    revs: list[tuple[str, int]] = field(default_factory=list[tuple[str, int]])
    left: list[str] = field(default_factory=list[str])
    # The docs the rule removes, by id: what a ledger op naming one reads on (DR-0694).
    removed: list[str] = field(default_factory=list[str])


def _doc_files(root: Path) -> dict[str, Path]:
    folder = root / "processed"
    return {p.relative_to(root).as_posix(): p for p in sorted(folder.rglob("*.md"))} if folder.is_dir() else {}


def _doc_stamps(root: Path) -> dict[str, Stamp]:
    return {rel: s for rel, path in _doc_files(root).items() if (s := _stamp(path)) is not None}


def _docs(
    vault: Vault, listed: Forgotten, state: _State, *, moved: bool, skipped: Iterable[str], stamps: Mapping[str, Stamp]
) -> _Docs:
    """Each doc that moved, or every one when the list did, judged by the docs' rule; a hand edit of a doc it changes
    taken in first and that doc judged again, with the docs citing it, so the rev the forget writes holds the edit. A
    final decision edited on disk, and a hand edit the take-in refuses, stay as they are, and are named."""
    root = vault.root
    out = _Docs()
    due = [root / rel for rel, stamp in stamps.items() if moved or state.docs.get(rel) != stamp or rel in state.left]
    if not due or not listed.digest:
        return out
    store = DocStore(vault)
    raw = Left(RawLookup(root), listed, skipped)
    parsed = store.at_paths(due)
    fates = judge(store, parsed, raw, listed)
    taken: list[str] = []
    for doc_id, fate in sorted(fates.items()):
        if _final(fate):
            # Read whole from its last commit, so an edit on disk is never taken in (DR-0603): left for its owner.
            if decision_form.edited(store.read_file(store.file(doc_id)), store.committed_text(doc_id)):
                out.left.append(doc_id)
            continue
        try:
            if store.take_in(doc_id) is not None:
                taken.append(doc_id)
        except DocRefusedError:
            out.left.append(doc_id)
    if taken:
        # Only the docs taken in read again; with those citing them, the only fates their new rev can change.
        again = {*taken, *forgetdocs.citing(parsed, taken)}
        fresh = {d.meta.id: d for d in store.at_paths([store.file(doc_id) for doc_id in taken])}
        by_id = {d.meta.id: d for d in parsed} | fresh
        for doc_id in again:
            fates.pop(doc_id, None)
        fates |= judge(store, [by_id[i] for i in sorted(again) if i in by_id], raw, listed)
    histories = store.histories(sorted(set(fates) - set(out.left)))
    for doc_id in sorted(set(fates) - set(out.left)):
        fate = fates[doc_id]
        rel = doc_path(doc_id).as_posix()
        if fate.doc is None:
            out.writes.append((rel, None))
            out.removed.append(doc_id)
            continue
        rev = max([fate.doc.meta.rev, *(n for n, _ in histories.get(doc_id, []))]) + 1
        out.writes.append((rel, fate.doc.render(rev=rev)))
        out.revs.append((doc_id, rev))
    return out


def _final(fate: forgetdocs.Fate) -> bool:
    doc = fate.doc
    return doc is not None and doc.meta.type == "decision" and doc.meta.status in decision_form.FINAL


def _op_stamps(root: Path, listed: Forgotten) -> dict[str, Stamp]:
    """Every op file's stamp, once the list takes anything the ledger's rule judges: none read before."""
    if not listed.digest:
        return {}
    files, _ = ledger_ops.listing(root)
    return {rel: s for rel, path in files if (s := _stamp(path)) is not None}


@dataclass
class _Ops:
    """What the apply does to the op files: each file's text as its masks leave it, how many lines it masks and their
    op ids, and the ops the rule masks that no list line names."""

    texts: dict[str, str] = field(default_factory=dict[str, str])
    lines: int = 0
    ids: set[str] = field(default_factory=set[str])
    new: list[str] = field(default_factory=list[str])


def _ledger(
    root: Path,
    listed: Forgotten,
    state: _State,
    *,
    moved: bool,
    stamps: Mapping[str, Stamp],
    matched: Callable[[], forget_ledger.Found],
) -> _Ops:
    """Each op file that moved, or every one when the list did, read raw, as `ledger_ops.masked_file` leaves it when it
    holds a masked op's line that isn't its mask, or a twin masking made. The rule is matched again, `matched`, when the
    list moved or a line read looks like one it masks; what it finds that no line names is masked in every file, with
    the listed ops. A file that isn't UTF-8 is left: no reader reads it, and `docket vault check` names it."""
    texts: dict[str, str] = {}
    for rel, stamp in sorted(stamps.items()):
        if moved or state.ledger.get(rel) != stamp:
            text = ledger_ops.utf8(root / rel)
            if text is not None:
                texts[rel] = text
    looks = moved or any(forget_ledger.suspect(raw, listed) for text in texts.values() for raw in text.split("\n"))
    new = matched().new if looks and listed.problem is None else frozenset[str]()
    if new:
        # The rule's ops may sit in any file: every one is read.
        for rel in sorted(set(stamps) - set(texts)):
            text = ledger_ops.utf8(root / rel)
            if text is not None:
                texts[rel] = text
    ids = listed.ops | new
    out = _Ops(new=sorted(new))
    for rel, text in texts.items():
        masked = ledger_ops.masked_file(text, ids)
        if masked is not None:
            out.texts[rel] = masked
            changed = [ledger_ops.id_of(raw) for _, raw, mask in ledger_ops.listed_lines(text, ids) if raw != mask]
            out.lines += len(changed)
            out.ids.update(op_id for op_id in changed if op_id is not None)
    return out


def _matched(vault: Vault, listed: Forgotten, raw: forgetdocs.Raw, removed: Iterable[str]) -> forget_ledger.Found:
    """The ledger's rule over every op line, against raw and the docs as the list leaves them (DR-0694)."""
    lines = ledger_ops.read_all(vault.root, masked=listed.ops).lines
    return forget_ledger.match(lines, raw, listed, Judged(DocStore(vault), raw, listed, removed))


def _cursor(vault: Vault, listed: Forgotten) -> str | None:
    """This machine's cursor file without the retry and waiting entries on a rid forgotten whole: its text, or None
    when it holds none."""
    path = cursors.file_path(vault.root, vault.machine)
    if not path.exists() or not listed.rids:
        return None
    found = cursors.read(vault.root, vault.machine)
    changed = False
    for source, cursor in found.items():
        for entries in (cursor.retry, cursor.waiting):
            for native in [n for n in entries if listed.whole(f"{source}:{n}")]:
                del entries[native]
                changed = True
    return cursors.dump(found) if changed else None


# -- the apply ---------------------------------------------------------------------------------------------------------


def apply(vault: Vault, keys: KeyStore, *, first: bool = False) -> Applied:
    """The list in force against this clone, idempotent: each part takes what the list takes and nothing else, so a
    second run finds nothing. `first` is the forget's own run, whose commit counts what it took and which refuses when
    the tree waits; any other run's says it was applied here. A commit that fails leaves the files on disk, and the
    next run commits them."""
    listed = forgotten.load(vault.root, vault.home)
    key = key_of(listed)
    if not key:
        return Applied()
    done = _apply(vault, keys, listed, key, first=first)
    if done.streams:
        # Once the lock is let go, as a write renders (DR-0675): an empty mark reads the ledger as it stands.
        done.rendered = status.after_write(vault, dict.fromkeys(done.streams), ())
    return done


def _apply(vault: Vault, keys: KeyStore, listed: Forgotten, key: str, *, first: bool) -> Applied:
    """The apply itself, under the vault lock."""
    home = vault.home
    root = vault.root
    with vault.lock:
        state = _read_state(home)
        moved = state.applied != key
        # What raw, the docs and the cursor entries are judged by: a keys-only line moves the list but not this.
        judged = moved and state.digest != listed.digest
        done = Applied()
        done.keys = drop_keys(listed, keys)
        _reached("keys")
        if moved:
            done.staged, done.staged_media = _staging(vault, keys, listed)
        _reached("staging")
        # Each stamp is taken before its file is read, HEAD's too, so what a pull changes meanwhile is read again on the
        # next call.
        head = vault.git.head_id()
        runs = vault.rawlog.stamped()
        run_stamps = {run.path.relative_to(root).as_posix(): stamp for run, stamp in runs}
        cursor_path = cursors.file_path(root, vault.machine)
        cursor_stamp = _stamp(cursor_path)
        op_stamps = _op_stamps(root, listed)
        # Docs and stored media arrive by a commit or a pull, which moves HEAD: walked only then, or when the list
        # moved, a write waits to be committed, or a doc left for its owner changed.
        left_moved = _left_moved(root, state)
        walk = moved or bool(state.pending) or head != state.head or left_moved
        was = {rel: (size, mtime) for rel, (size, mtime, _) in state.media.items()}
        doc_stamps = _doc_stamps(root) if walk else dict(state.docs)
        media_now = _media_stamps(root) if walk else was
        if (
            not moved
            and not state.pending
            and not listed.pending
            and run_stamps == state.runs
            and cursor_stamp == state.cursor
            and op_stamps == state.ledger
            and doc_stamps == state.docs
            and media_now == was
            and not left_moved
        ):
            if head != state.head:
                _write_state(home, dataclasses.replace(state, head=head))
            return done
        # Before any tracked file changes: the tree is the owner's while git is in the middle of something (DR-0597).
        done.waits = vault.blocked()
        if done.waits is not None:
            if first:
                raise DocketError(done.waits)
            log.info("the forgotten list's apply waits for git: %s", done.waits)
            return done
        if listed.pending and listed.problem is None:
            forgotten.commit_pending(vault)
        raw = _raw(vault, listed, state, judged, runs) if listed.acts else _Raw(files=frozenset(state.files))
        done.records = raw.lines
        if raw.rids and not walk:
            # A whole rid's line came back without HEAD moving: its media and docs are looked at again.
            walk = True
            doc_stamps, media_now = _doc_stamps(root), _media_stamps(root)
        media = _media(vault, listed, state.media, media_now, raw.rids) if walk else _Media()
        done.media = len(media.removed)
        _reached("media")
        # The list only grows, so a rid whose lines an earlier run took stays taken.
        skipped = {*state.skipped, *(rid_hash(rid) for rid in raw.rids)}
        docs = _docs(vault, listed, state, moved=judged, skipped=skipped, stamps=doc_stamps) if walk else _Docs()
        done.left = docs.left
        cursor = _cursor(vault, listed) if judged or cursor_stamp != state.cursor else None
        left = Left(RawLookup(root), listed, skipped)
        ledger = _ledger(
            root, listed, state, moved=judged, stamps=op_stamps,
            matched=lambda: _matched(vault, listed, left, docs.removed),
        )  # fmt: skip
        masks, done.ops = ledger.texts, ledger.lines
        writes: list[tuple[str, str | None]] = [
            *((p.relative_to(root).as_posix(), text) for p, text in raw.left.items()),
            *docs.writes,
        ]
        if cursor is not None:
            writes.append((cursor_path.relative_to(root).as_posix(), cursor))
        removed = [p.relative_to(root).as_posix() for p in media.removed]
        unlisted = sorted(media.unlisted) if listed.problem is None else []
        named: set[str] = {
            *state.pending, *(rel for rel, _ in writes), *masks, *removed,
            *([FORGOTTEN] if unlisted or ledger.new else []),
        }  # fmt: skip
        paths = sorted(named)
        revs = {**state.revs, **dict(docs.revs)}
        # The paths and the docs' revs go on record before anything in the tree changes, so a crash or a failure from
        # here on leaves them for the next run to commit.
        _write_state(home, dataclasses.replace(state, pending=paths, revs=revs))
        _reached("recorded")
        if unlisted or ledger.new:
            # Listed before the keys go, so a key deleted here is named for the clones that imported this one's export,
            # and before the masks are written, so a masked line is never one the list doesn't explain (DR-0696).
            found = Forget(keys=unlisted, ops=ledger.new, at=datetime.now(UTC), machine=vault.machine)
            forgotten.write_line(vault, found)
            done.listed_keys, done.listed_ops = len(unlisted), len(ledger.new)
        for rel, text in writes:
            path = root / rel
            if text is None:
                retry(lambda path=path: path.unlink(missing_ok=True))
            else:
                write_replacing(home, path, text, "forget")
        if masks:
            ledger_store.write_masks(vault, masks)
        for path in media.removed:
            retry(lambda path=path: path.unlink(missing_ok=True))
        for one in sorted(media.keys):
            keys.delete(one)
        done.docs = len(docs.revs)
        done.removed = sum(1 for _, text in docs.writes if text is None)
        _reached("written")
        paths = _committable(vault, paths)
        if paths:
            what = (
                f"Forget: {done.records} record(s), {done.media} media file(s), {done.docs + done.removed} doc(s), "
                f"{done.ops} op(s) from {vault.machine}"
                if first
                else f"Forget: applied on {vault.machine}"
            )
            # The docs' revs a failed commit left too, so every rev the apply wrote is in a history (DR-0112).
            committed = set(paths)
            lines = "".join(
                f"\nDoc: {doc_id} rev {rev}"
                for doc_id, rev in sorted(revs.items())
                if (rel := doc_path(doc_id).as_posix()) in committed and (root / rel).is_file()
            )
            done.commit = vault.commit(paths, f"{what}\n{lines}" if lines else what)
        _reached("committed")
        written = {*(rel for rel, _ in writes), *masks}
        after = forgotten.load(root, home)
        if ledger.ids:
            done.streams = forget_ledger.streams(ledger_ops.read_all(root, masked=after.ops).lines, ledger.ids)
        _write_state(
            home,
            _State(
                applied=key_of(after),
                digest=after.digest,
                head=head,
                runs={rel: (_stamp(root / rel) or s) if rel in written else s for rel, s in run_stamps.items()},
                docs=_restamped(root, doc_stamps, written) if walk else state.docs,
                media={rel: s for rel, s in media.seen.items() if rel not in removed} if walk else state.media,
                cursor=_stamp(cursor_path) if cursor is not None else cursor_stamp,
                ledger={rel: (_stamp(root / rel) or s) if rel in masks else s for rel, s in op_stamps.items()},
                left={rel: doc_stamps.get(rel) for rel in (doc_path(i).as_posix() for i in docs.left)},
                files=sorted(raw.files),
                skipped=sorted(skipped),
            ),
        )
        return done


def _left_moved(root: Path, state: _State) -> bool:
    """Whether a doc the last apply left for its owner changed since, so the next call writes the forget's rev."""
    return any(_stamp(root / rel) != stamp for rel, stamp in state.left.items())


def _restamped(root: Path, stamps: Mapping[str, Stamp], written: set[str]) -> dict[str, Stamp]:
    """The docs' stamps as they were read, those the apply wrote taken again and those it removed left out."""
    out: dict[str, Stamp] = {}
    for rel, stamp in stamps.items():
        now = _stamp(root / rel) if rel in written else stamp
        if now is not None:
            out[rel] = now
    return out


def _committable(vault: Vault, paths: Sequence[str]) -> list[str]:
    """The paths a commit can take: each one on disk, and each one gone that git tracks, whose removal it records. A
    file gone that git never held, a doc never committed or a file a crash left, has nothing to commit, and naming it
    would stop git's add."""
    absent = {rel for rel in paths if not (vault.root / rel).exists()}
    if not absent:
        return list(paths)
    tracked = set(vault.git.out("ls-files", "-z", "--", *sorted(absent)).split("\0"))
    return [rel for rel in paths if rel not in absent or rel in tracked]


def _media_stamps(root: Path) -> dict[str, Stamp]:
    folder = root / layout.STORED
    out: dict[str, Stamp] = {}
    for path in sorted(folder.glob(f"*/*/*/*{layout.SUFFIX}")) if folder.is_dir() else []:
        stamp = _stamp(path)
        if stamp is not None:
            out[path.relative_to(root).as_posix()] = stamp
    return out
