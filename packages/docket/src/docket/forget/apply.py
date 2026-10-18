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

A key the apply deletes that no line names, a stored file's this clone made before its pull, goes on a keys-only line
(DR-0595), so the clones that imported its export delete it too.

Each file is written whole through a temp file and a rename, and all of them go in one commit. With no list the apply
costs a stat; otherwise `<private>/forget/applied.json` keeps the list it applied and the size and modification time of
each run file, doc, media file and cursor file it checked, so only what moved since is read again. Nothing it keeps or
reports names a record.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from docket.errors import DocketError
from docket.media import cutlog, layout
from docket.media import staged as media_staged
from docket.media.keystore import KeyStore
from docket.vault import cursors, decision_form, forgetdocs, forgotten
from docket.vault.docstore import DocStore
from docket.vault.forgotten import FORGOTTEN, Forget, Forgotten
from docket.vault.home import Home
from docket.vault.processed import DocRefusedError, ProcessedDoc, doc_path
from docket.vault.records import RawRecord
from docket.vault.segments import segments, time_range
from docket.vault.spans import RawLookup
from docket.vault.staging import Staging, retry, rid_hash, write_replacing
from docket.vault.vault import Vault

FOLDER = "forget"
APPLIED = "applied.json"
SCHEMA_VERSION = 1

Stamp = tuple[int, int]


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
    # Docs the rule changes that stay as they are: a hand edit its take-in refuses, or a final decision edited on disk.
    left: list[str] = field(default_factory=list[str])
    # Keys this clone held that no line named, now on a keys-only line.
    listed_keys: int = 0
    commit: str | None = None


@dataclass
class _State:
    """`applied.json`: the list as it was applied, keys too, the stamps of what it checked, the folder files whose
    parts it takes from here on, the hashes of the rids whose lines it took, and the paths written but not committed."""

    applied: str = ""
    runs: dict[str, Stamp] = field(default_factory=dict[str, Stamp])
    docs: dict[str, Stamp] = field(default_factory=dict[str, Stamp])
    media: dict[str, Stamp] = field(default_factory=dict[str, Stamp])
    cursor: Stamp | None = None
    files: list[str] = field(default_factory=list[str])
    skipped: list[str] = field(default_factory=list[str])
    pending: list[str] = field(default_factory=list[str])

    def dump(self) -> str:
        return json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "list": self.applied,
                "runs": self.runs,
                "docs": self.docs,
                "media": self.media,
                "cursor": self.cursor,
                "files": sorted(self.files),
                "skipped": sorted(self.skipped),
                "pending": sorted(self.pending),
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


def _read_state(home: Home) -> _State:
    """What the last apply kept; one that doesn't read, or that a later Docket wrote, is no state, so everything is
    read again."""
    try:
        data: Any = json.loads(state_path(home).read_text(encoding="utf-8"))
        cursor = data["cursor"]
        found = _State(
            applied=str(data["list"]),
            runs=_stamps(data["runs"]),
            docs=_stamps(data["docs"]),
            media=_stamps(data["media"]),
            cursor=None if cursor is None else (int(cursor[0]), int(cursor[1])),
            files=[str(f) for f in data["files"]],
            skipped=[str(s) for s in data["skipped"]],
            pending=[str(p) for p in data["pending"]],
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
    if not path.exists():
        return None
    st = path.stat()
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


def _record(line: str) -> RawRecord | None:
    try:
        return RawRecord.model_validate(json.loads(line))
    except ValueError:
        return None


@dataclass
class _Raw:
    """What the list takes from the run files read: each file's text as it is left, the rids whose lines went, how many
    lines, and the folder files whose parts go from here on."""

    left: dict[Path, str] = field(default_factory=dict[Path, str])
    rids: set[str] = field(default_factory=set[str])
    lines: int = 0
    files: frozenset[str] = frozenset()


def _raw(vault: Vault, listed: Forgotten, state: _State, moved: bool, runs: Sequence[tuple[Any, Stamp]]) -> _Raw:
    """The listed lines of each run file that moved, or of every one when the list did, in run order. A line that
    doesn't read as a record is kept as it is: only a line the list takes ever leaves a run file."""
    out = _Raw()
    reading = listed.reading(() if moved else state.files)
    for run, stamp in runs:
        rel = run.path.relative_to(vault.root).as_posix()
        if not moved and state.runs.get(rel) == stamp:
            continue
        text = run.path.read_bytes().decode("utf-8")
        kept: list[str] = []
        taken = 0
        for line in text.splitlines(keepends=True):
            record = _record(line) if line.strip() else None
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


@dataclass
class _Media:
    removed: list[Path] = field(default_factory=list[Path])
    # Keys deleted here that no line names.
    unlisted: set[str] = field(default_factory=set[str])


def _media(
    vault: Vault, keys: KeyStore, listed: Forgotten, state: _State, *, moved: bool, taken: Iterable[str]
) -> _Media:
    """Each stored file the list takes: on a rid its `media` holds, or one forgotten whole whose lines went now, with
    every key the cut log names for that rid; or a file whose own key is listed. An LFS pointer is taken by its rid
    alone: its key can't be read. Each key goes from this clone's store."""
    root = vault.root
    out = _Media()
    hashes = set(listed.media) | {rid_hash(rid) for rid in taken if listed.whole(rid)}
    folder = root / layout.STORED
    for path in sorted(folder.glob(f"*/*/*/*{layout.SUFFIX}")) if folder.is_dir() else []:
        stem = path.name.split(".", 1)[0]
        if not moved and state.media.get(path.relative_to(root).as_posix()) == _stamp(path) and stem not in hashes:
            continue
        key = media_staged.key_of(path)
        if stem in hashes:
            found = {key} if key is not None else set[str]()
            found |= logged_keys(root, path.parts[-4], stem)
        elif key is not None and key in listed.keys:
            found = {key}
        else:
            continue
        retry(lambda path=path: path.unlink(missing_ok=True))
        out.removed.append(path)
        for one in sorted(found):
            keys.delete(one)
            if one not in listed.keys:
                out.unlisted.add(one)
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
        if self.lookup.versions(rid):
            return False
        return rid_hash(rid) in self.skipped or self.listed.versioned(rid)

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


def judge(
    store: DocStore, docs: Sequence[ProcessedDoc], raw: forgetdocs.Raw, listed: Forgotten
) -> dict[str, forgetdocs.Fate]:
    """What the docs' rule makes of these docs, reading what their refs lead to through the store: each cited doc as it
    reads now, and each earlier rev from git."""

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

    return forgetdocs.fates(docs, raw, listed, current=current, older=older)


@dataclass
class _Docs:
    writes: list[tuple[str, str | None]] = field(default_factory=list[tuple[str, str | None]])
    revs: list[tuple[str, int]] = field(default_factory=list[tuple[str, int]])
    left: list[str] = field(default_factory=list[str])
    stamps: dict[str, Stamp] = field(default_factory=dict[str, Stamp])


def _doc_files(root: Path) -> dict[str, Path]:
    folder = root / "processed"
    return {p.relative_to(root).as_posix(): p for p in sorted(folder.rglob("*.md"))} if folder.is_dir() else {}


def _docs(vault: Vault, listed: Forgotten, state: _State, moved: bool, skipped: Iterable[str]) -> _Docs:
    """Each doc that moved, or every one when the list did, judged by the docs' rule; a hand edit of a doc it changes
    taken in first and the doc judged again, so the rev the forget writes holds the edit. A final decision edited on
    disk, and a hand edit the take-in refuses, stay as they are, and are named."""
    root = vault.root
    out = _Docs()
    files = _doc_files(root)
    due: list[Path] = []
    for rel, path in files.items():
        stamp = _stamp(path)
        if moved or state.docs.get(rel) != stamp:
            due.append(path)
    if not due or not listed.digest:
        return out
    store = DocStore(vault)
    raw = Left(RawLookup(root), listed, skipped)
    fates = judge(store, store.at_paths(due), raw, listed)
    taken = False
    for doc_id, fate in sorted(fates.items()):
        if _final(fate):
            # Read whole from its last commit, so an edit on disk is never taken in (DR-0564): left for its owner.
            if decision_form.edited(store.read_file(store.file(doc_id)), store.committed_text(doc_id)):
                out.left.append(doc_id)
            continue
        try:
            taken = store.take_in(doc_id) is not None or taken
        except DocRefusedError:
            out.left.append(doc_id)
    if taken:
        fates = judge(store, store.at_paths(due), raw, listed)
    histories = store.histories(sorted(set(fates) - set(out.left)))
    for doc_id in sorted(set(fates) - set(out.left)):
        fate = fates[doc_id]
        rel = doc_path(doc_id).as_posix()
        if fate.doc is None:
            out.writes.append((rel, None))
            continue
        rev = max([fate.doc.meta.rev, *(n for n, _ in histories.get(doc_id, []))]) + 1
        out.writes.append((rel, fate.doc.render(rev=rev)))
        out.revs.append((doc_id, rev))
    return out


def _final(fate: forgetdocs.Fate) -> bool:
    doc = fate.doc
    return doc is not None and doc.meta.type == "decision" and doc.meta.status in decision_form.FINAL


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
    second run finds nothing. `first` is the forget's own run, whose commit counts what it took; any other run's says
    it was applied here. A commit that fails leaves the files on disk, and the next run commits them."""
    listed = forgotten.load(vault.root, vault.home)
    key = key_of(listed)
    if not key:
        return Applied()
    home = vault.home
    with vault.lock:
        if listed.pending and listed.problem is None:
            forgotten.commit_pending(vault)
        state = _read_state(home)
        moved = state.applied != key
        done = Applied()
        done.keys = drop_keys(listed, keys)
        _reached("keys")
        if moved:
            done.staged, done.staged_media = _staging(vault, keys, listed)
        _reached("staging")
        runs = vault.rawlog.stamped()
        stamps = {run.path.relative_to(vault.root).as_posix(): stamp for run, stamp in runs}
        cursor_path = cursors.file_path(vault.root, vault.machine)
        cursor_stamp = _stamp(cursor_path)
        nothing_moved = (
            not moved
            and not state.pending
            and stamps == state.runs
            and cursor_stamp == state.cursor
            and _unmoved(vault.root, state)
        )
        if nothing_moved:
            return done
        raw = _raw(vault, listed, state, moved, runs) if listed.acts else _Raw(files=frozenset(state.files))
        done.records = raw.lines
        media = _media(vault, keys, listed, state, moved=moved, taken=raw.rids)
        done.media = len(media.removed)
        # The list only grows, so a rid whose lines an earlier run took stays taken.
        skipped = {*state.skipped, *(rid_hash(rid) for rid in raw.rids)}
        docs = _docs(vault, listed, state, moved, skipped)
        done.left = docs.left
        cursor = _cursor(vault, listed) if moved or cursor_stamp != state.cursor else None
        writes: list[tuple[str, str | None]] = [
            *((p.relative_to(vault.root).as_posix(), text) for p, text in raw.left.items()),
            *docs.writes,
        ]
        if cursor is not None:
            writes.append((cursor_path.relative_to(vault.root).as_posix(), cursor))
        removed = [p.relative_to(vault.root).as_posix() for p in media.removed]
        unlisted = sorted(media.unlisted) if listed.problem is None else []
        named: set[str] = {*state.pending, *(rel for rel, _ in writes), *removed, *([FORGOTTEN] if unlisted else [])}
        paths = sorted(named)
        # The paths go on record before any is written, so a crash before the commit leaves them for the next run.
        _write_state(home, dataclasses.replace(state, pending=paths))
        _reached("recorded")
        for rel, text in writes:
            path = vault.root / rel
            if text is None:
                retry(lambda path=path: path.unlink(missing_ok=True))
            else:
                write_replacing(home, path, text, "forget")
        done.docs = len(docs.revs)
        done.removed = sum(1 for _, text in docs.writes if text is None)
        _reached("written")
        if unlisted:
            forgotten.write_line(vault, Forget(keys=unlisted, at=datetime.now(UTC), machine=vault.machine))
            done.listed_keys = len(unlisted)
        paths = _committable(vault, paths)
        if paths:
            what = (
                f"Forget: {done.records} record(s), {done.media} media file(s), {done.docs + done.removed} doc(s) "
                f"from {vault.machine}"
                if first
                else f"Forget: applied on {vault.machine}"
            )
            lines = "".join(f"\nDoc: {doc_id} rev {rev}" for doc_id, rev in docs.revs)
            done.commit = vault.commit(paths, f"{what}\n{lines}" if lines else what)
        _reached("committed")
        after = forgotten.load(vault.root, vault.home)
        _write_state(
            home,
            _State(
                applied=key_of(after),
                runs={run.path.relative_to(vault.root).as_posix(): stamp for run, stamp in vault.rawlog.stamped()},
                docs={rel: s for rel, path in _doc_files(vault.root).items() if (s := _stamp(path)) is not None},
                media=_media_stamps(vault.root),
                cursor=_stamp(cursor_path),
                files=sorted(raw.files),
                skipped=sorted(skipped),
            ),
        )
        return done


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


def _unmoved(root: Path, state: _State) -> bool:
    """Whether every doc and stored media file is as the last apply left it: their stamps, never their bytes."""
    docs = {rel: s for rel, path in _doc_files(root).items() if (s := _stamp(path)) is not None}
    return docs == state.docs and _media_stamps(root) == state.media
