"""The staging gate: the only way a record reaches the raw log (DR-0015).

Intake drops in memory what a private source or a never-store list names, so a dropped record never touches the disk,
then redacts the rest and stages it: `ready` from a work source, `pending` from a mixed one or when the record was
decided by hand before. Classification (by the user or Claude) makes a pending record ready, with cuts, or drops it.
Promotion cuts, redacts again with the current rules and writes one run per source. Every call first sweeps staging
against the current vault.yaml and reads the hold lists: a record on hold is never staged, classified or promoted.

It reads the release logs with them (DR-0201): a record under a released id goes in only with the release's cuts, at
intake and again at promotion, and one without segments only when the release keeps it; the rest is left out and only
counted. A hold beats a release.

Media waits beside its record (DR-0547): every drop of a version deletes the staged media on its rid and on each rid it
nests under first, and a drop for a never-store value or as private marks those rids withheld. Every sweep and every
promotion runs media's four checks again and deletes what fails; promotion stores what passes once its record is in raw.

Nothing here names a dropped record: not a log line, not an error, not a file. Only counts leave the gate.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Generator, Iterable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from docket.errors import DocketError
from docket.media import layout as media_layout
from docket.media import staged as media_staged
from docket.media.keystore import KeyStore
from docket.media.screen import Screen
from docket.vault import cuts as cutting
from docket.vault import holds, inforce, neverstore, releaselog
from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.vault.cuts import CUT_MARKER, Cut
from docket.vault.host import Lfs, lfs_state
from docket.vault.records import RawRecord, version
from docket.vault.redact import Redactor, markers
from docket.vault.spans import RawLookup
from docket.vault.staging import Classifier, Decided, Entry, Scan, Staged, Staging, State, key_of, rid_hash
from docket.vault.vault import Vault

log = logging.getLogger(__name__)

Label = Literal["work", "private"]
# Why a staged record waits, as the gate knows it (DR-0380): a hold covers it; the last promotion held it back; it is
# pending under a release, after an earlier version was decided by hand, or from a mixed source; or it is ready by its
# source's rule, or classified.
Why = Literal["on-hold", "held-back", "released", "by-hand", "mixed", "work", "classified"]
MIN_KEY_PREFIX = 6
# Each staged record's time is after the one before (DR-0488), even when the clock hasn't moved: Windows' moves every
# 15.6 ms.
TICK = timedelta(microseconds=1)


@dataclass(frozen=True)
class MediaRun:
    """One source's media stored by a promotion: how many files, and the commit."""

    source: str
    stored: int
    commit: str | None


@dataclass
class StageResult:
    ready: int = 0
    pending: int = 0
    skipped: int = 0
    on_hold: int = 0
    dropped: Counter[str] = field(default_factory=Counter[str])
    redactions: Counter[str] = field(default_factory=Counter[str])
    # Media files deleted: with a dropped version of their record, or failing their checks at the sweep.
    media_dropped: int = 0


@dataclass(frozen=True)
class SourceRun:
    source: str
    # The first run file the promotion wrote, or None; `files` holds them all, in run order (DR-0488).
    run: Path | None
    written: int
    skipped: int
    commit: str | None
    redactions: Counter[str]
    files: tuple[Path, ...] = ()


@dataclass(frozen=True)
class Held:
    key: str
    source: str
    problem: str


@dataclass
class PromoteResult:
    runs: list[SourceRun] = field(default_factory=list[SourceRun])
    held: list[Held] = field(default_factory=list[Held])
    dropped: Counter[str] = field(default_factory=Counter[str])
    on_hold: int = 0
    media: list[MediaRun] = field(default_factory=list["MediaRun"])
    media_dropped: int = 0
    media_held: list[str] = field(default_factory=list[str])


@dataclass(frozen=True)
class Listing:
    entries: list[Entry]
    unreadable: list[tuple[Path, str]]
    # Each entry's reason, by its key.
    why: dict[str, Why] = field(default_factory=dict[str, Why])
    # Media files waiting beside their records.
    media: int = 0

    def counts(self) -> Counter[str]:
        return Counter(e.staged.state for e in self.entries)


@dataclass(frozen=True)
class Shown:
    entry: Entry
    cut_before: RawRecord | None
    why: Why = "mixed"
    # The cuts a release adds to it at classification and promotion, whatever the staged file holds (DR-0201).
    release_cuts: tuple[Cut, ...] = ()


class Gate:
    def __init__(self, vault: Vault) -> None:
        self.vault = vault
        self.staging = Staging(vault.root, vault.home)
        self._cut_rids: set[str] = set()
        self._read_runs: set[Path] = set()
        self._holds = holds.Holds()
        self._releases = releaselog.Releases()
        # Whether the last read found an edit of vault.yaml not in force, for the messages that send the user there.
        self._waiting = False
        self.keys = KeyStore(vault.home)
        self._media_dropped = 0

    def stage(self, records: Iterable[RawRecord]) -> StageResult:
        """Take records from a connector: drop, skip or stage each one."""
        batch = list(records)
        with self.vault.lock:
            config = self._config()
            for record in batch:
                source = config.sources.get(record.source)
                if source is None:
                    raise DocketError(
                        f"source {record.source!r} isn't in vault.yaml; add it with its sensitivity (work, mixed or "
                        f"private) so the gate can tell how private it is{inforce.note(self._waiting)}"
                    )
                if not source.enabled:
                    raise DocketError(
                        f"source {record.source!r} is disabled in vault.yaml{inforce.note(self._waiting)}"
                    )
            self._media_dropped = 0
            result = StageResult(dropped=self._sweep(config))
            scan = self.staging.scan()
            staged_keys = {e.key for e in scan.entries}
            decided = self.staging.decided()
            cut_in_staging = _cut_rids(scan)
            redactor = Redactor.from_config(config.redaction)
            # The order promotion keeps (DR-0488): after everything staging holds, then in the batch's order.
            now = max([datetime.now(UTC), *(e.staged.staged_at + TICK for e in scan.entries)])
            for record in batch:
                sensitivity = config.sources[record.source].sensitivity
                if sensitivity == "private":
                    self._drop_media(record.rid, withhold=False)
                    result.dropped["private"] += 1
                    continue
                if neverstore.match(record, config.never_store):
                    self._drop_media(record.rid, withhold=True)
                    result.dropped["never-store"] += 1
                    continue
                if self._holds.covers(record):
                    result.on_hold += 1
                    continue
                release = self._releases.covering(record)
                taken = record
                if release is not None and record.segments:
                    try:
                        taken = cutting.apply(record, release.cuts)
                    except DocketError:
                        # A version the release's times don't fit (it ends before a cut starts): never half cut.
                        self._drop_media(record.rid, withhold=False)
                        result.dropped["left-out"] += 1
                        continue
                redacted, counts = redactor.record(taken)
                redacted = redacted.model_copy(update={"v": version(redacted), "run": ""})
                if release is not None and not redacted.segments and not release.keeps(redacted):
                    self._drop_media(record.rid, withhold=False)
                    result.dropped["left-out"] += 1
                    continue
                key = key_of(redacted.rid, redacted.v)
                if key in staged_keys or key in decided.keys or self.vault.rawlog.contains(redacted):
                    result.skipped += 1
                    continue
                pending = sensitivity == "mixed" or self._by_hand(redacted, decided, cut_in_staging)
                self.staging.write(Staged(state="pending" if pending else "ready", staged_at=now, record=redacted))
                now += TICK
                staged_keys.add(key)
                result.pending += pending
                result.ready += not pending
                result.redactions += counts
            result.media_dropped = self._media_dropped
            log.info(
                "gate: %d ready, %d pending, %d skipped, %d dropped, %d on hold",
                result.ready,
                result.pending,
                result.skipped,
                result.dropped.total(),
                result.on_hold,
            )
            return result

    def show(self, ref: str) -> Shown:
        """One staged record, why it waits, the cuts a release adds to it, and the version of it in raw that was cut,
        when there is one."""
        with self.vault.lock:
            self._sweep(self._config())
            scan = self.staging.scan()
            entry = _resolve(ref, scan.entries)
            record = entry.staged.record
            cut = [r for r in self.vault.rawlog.read(record.rid) if CUT_MARKER in r.text]
            why = self._why(entry.staged, self.staging.decided(), _cut_rids(scan))
            return Shown(entry, cut[-1] if cut else None, why, tuple(self._release_cuts(record)))

    def classify(self, ref: str, label: Label, cuts: Sequence[Cut] = (), by: Classifier = "user") -> Staged | None:
        """Work makes a staged record ready, with its cuts; private drops it. Returns the record, or None if dropped."""
        if label not in ("work", "private"):
            raise DocketError(f"a record is work or private, not {label!r}")
        with self.vault.lock:
            config = self._config()
            self._sweep(config)
            entry = _resolve(ref, self.staging.scan().entries)
            record = entry.staged.record
            if self._holds.covers(record):
                # Private too: dropping it would record a decision that releasing the hold would have to undo.
                raise DocketError(
                    f"{record.rid} is on hold in {holds.HOLDS}/{record.source}.txt: it can't be classified, work or "
                    "private, until the hold is released"
                )
            if label == "private":
                if cuts:
                    raise DocketError("cuts go with work; a private record is dropped whole")
                rid = entry.staged.record.rid
                for path in self._uncommitted_runs_with(rid):
                    # The next commit would take it into git; everything in that file is still staged.
                    raise DocketError(
                        f"{rid} is already in {path}, a run file git hasn't committed yet: delete that file (all of "
                        "it is still staged and goes again at the next promotion), then classify it again"
                    )
                self._drop_media(rid, withhold=True)
                self.staging.remember([(rid, entry.key)])
                self.staging.delete(entry.path)
                log.info("gate: one record classified private and dropped")
                return None
            merged = cutting.merge(cuts)
            # A dry run, in promotion's order, so a cut that can't be made fails now and not at promotion.
            final = cutting.apply(entry.staged.record, [*merged, *self._release_cuts(entry.staged.record)])
            Redactor.from_config(config.redaction).record(final)
            staged = entry.staged.model_copy(
                update={"state": "ready", "classified_by": by, "cuts": merged, "problem": None}
            )
            self.staging.write(staged)
            return staged

    def promote(self, source: str | None = None) -> PromoteResult:
        """Write every ready record to the raw log: cut, redacted with the current rules, one run per source, in the
        order the records were staged, so of two versions of one record the later one is the later line (#107)."""
        with self.vault.lock:
            # Before anything is written: commit what an earlier promotion left behind, so a repo Docket can't commit
            # to (a rebase in progress, a detached HEAD) stops the promotion here, with no new run file on disk.
            self.vault.commit([], f"Raw: an interrupted run from {self.vault.machine}")
            config = self._config()
            self._media_dropped = 0
            result = PromoteResult(dropped=self._sweep(config))
            redactor = Redactor.from_config(config.redaction)
            groups: dict[str, list[tuple[Entry, RawRecord]]] = {}
            # Staging's files sort by key, a hash; raw's order is the order things were staged, then their own time.
            entries = sorted(self.staging.scan().entries, key=lambda e: (e.staged.staged_at, e.staged.record.ts, e.key))
            for entry in entries:
                staged = entry.staged
                name = staged.record.source
                if staged.state != "ready" or (source is not None and name != source):
                    continue
                if self._holds.covers(staged.record):
                    result.on_hold += 1
                    continue
                try:
                    if name not in config.sources:
                        raise DocketError(f"source {name!r} isn't in vault.yaml any more")
                    final, _ = redactor.record(
                        cutting.apply(staged.record, [*staged.cuts, *self._release_cuts(staged.record)])
                    )
                except DocketError as e:
                    result.held.append(self._hold(entry, str(e)))
                    continue
                groups.setdefault(name, []).append((entry, final))
            for name in sorted(groups):
                result.runs.append(self._promote_source(name, groups[name]))
            self._promote_media(config, result)
            log.info(
                "gate: promoted %d record(s) and %d media file(s), held %d, dropped %d, on hold %d",
                sum(r.written for r in result.runs),
                sum(m.stored for m in result.media),
                len(result.held),
                result.dropped.total(),
                result.on_hold,
            )
            return result

    def media_check(self, rid: str) -> str | None:
        """Why media for a rid can't be staged now, or None: the four checks after a sweep, under the lock."""
        with self.vault.lock:
            config = self._config()
            self._sweep(config)
            with self._screen(config) as screen:
                return screen.why(rid)

    # -- inside the gate -------------------------------------------------------------------------------------------

    def _drop_media(self, rid: str, *, withhold: bool) -> None:
        """Before a version of `rid` goes: the staged media on it and on each rid it nests under, then, for a drop
        for a never-store value or as private, the withheld marks (DR-0547)."""
        self._media_dropped += media_staged.drop(self.vault.root, self.keys, rid, self.vault.git.in_head)
        if withhold:
            media_staged.withhold(self.vault.root, rid)

    @contextmanager
    def _screen(self, config: VaultConfig) -> Generator[Screen]:
        """Media's checks over what staging, raw and the logs hold now; the holds and releases the sweep just read.
        Raw is read at its places while the index is current for the source (DR-0446), so a gate call while media
        waits doesn't load the source whole. The index is held only while the checks run, never through a git call or
        a write (DR-0547)."""
        from docket.index import lookup  # noqa: PLC0415 - the index's modules import the vault's, not the other way

        index = lookup.open_read(self.vault)
        try:
            yield Screen(
                root=self.vault.root,
                config=config,
                staged=self.staging.scan().entries,
                raw=RawLookup(self.vault.root, lambda: index),
                decided=self.staging.decided(),
                withheld=media_staged.read_withheld(self.vault.root),
                holds=self._holds,
                releases=self._releases,
                left_out=self._left_out,
            )
        finally:
            if index is not None:
                index.close()

    def _media_sweep(self, config: VaultConfig) -> media_staged.Swept:
        """Clear media's orphans and delete what fails its checks; the media that waits, every check passing."""
        swept = media_staged.sweep(self.vault.root, self.vault.home, self.keys, self.vault.git.in_head)
        self._media_dropped += swept.orphans
        if not swept.waiting:
            return swept
        with self._screen(config) as screen:
            failing = {w.sidecar.rid for w in swept.waiting if screen.why(w.sidecar.rid) is not None}
        for rid in sorted(failing):
            self._media_dropped += media_staged.delete(self.vault.root, self.keys, rid, self.vault.git.in_head)
        kept = [w for w in swept.waiting if w.sidecar.rid not in failing]
        return media_staged.Swept(kept, swept.orphans, swept.leftover)

    def _promote_media(self, config: VaultConfig, result: PromoteResult) -> None:
        """Store the media whose record is in raw now and whose checks pass, one commit per source after the runs'.
        Files a crash left in media/ uncommitted go first, checked through their sidecars. A commit that fails moves
        back to staging the files HEAD doesn't hold."""
        swept = self._media_sweep(config)
        result.media_dropped = self._media_dropped
        if not swept.waiting and not swept.leftover:
            # Nothing waits: no git call, so a promotion of records alone costs what it did.
            return
        # The screen's index is closed before the commits, which nothing reading it may hold up.
        with self._screen(config) as screen:
            groups = self._media_groups(swept, screen, result)
        for source in sorted(groups):
            moved = groups[source]
            try:
                commit = self.vault.commit(
                    media_staged.rel(self.vault.root, (target for target, _, _ in moved)),
                    f"Media: {source}, {len(moved)} file(s) from {self.vault.machine}",
                )
            except BaseException:
                self._move_back(moved)
                raise
            for _, _, sidecar in moved:
                sidecar.unlink(missing_ok=True)
            result.media.append(MediaRun(source, len(moved), commit))
        result.media_dropped = self._media_dropped

    def _move_back(self, moved: list[tuple[Path, Path | None, Path]]) -> None:
        """After a media commit that raised: back to staging each file moved for it that HEAD doesn't hold. One that
        landed and then raised (Ctrl+C after git finished) leaves its files where HEAD has them, and their sidecars
        go at the next promotion. When git can't say, every file stays, and the next promotion commits it from
        media/ as a crash's leftover."""
        root = self.vault.root
        try:
            loose = set(self.vault.git.not_in_head(media_layout.STORED))
        except DocketError:
            return
        for target, back, _ in moved:
            if back is not None and target.relative_to(root).as_posix() in loose:
                media_staged.move(target, back)

    def _media_groups(
        self, swept: media_staged.Swept, screen: Screen, result: PromoteResult
    ) -> dict[str, list[tuple[Path, Path | None, Path]]]:
        """The files to commit, per source: each with where it goes back to on a failed commit, and its sidecar."""
        root = self.vault.root
        # A file a crash left in media/ uncommitted always has its sidecar in staging still: git is asked only then.
        untracked = self.vault.git.not_in_head(media_layout.STORED) if swept.leftover else []
        lfs: Lfs | None = None
        # A sidecar left beside a file a commit already took, by a crash before its deletion: only the sidecar goes.
        for sidecar, rid in swept.leftover:
            if not set(media_staged.rel(root, media_layout.stored_files(root, rid))) & set(untracked):
                sidecar.unlink(missing_ok=True)
        # Per source: each file to commit, where it goes back to on a failed commit, and its sidecar.
        groups: dict[str, list[tuple[Path, Path | None, Path]]] = {}
        for name in untracked:
            path = root / name
            parts = Path(name).parts
            source = parts[1] if len(parts) > 2 else ""
            sidecar = media_layout.staged_dir(root, source) / f"{path.name.split('.', 1)[0]}.json"
            rid = media_staged.rid_of(sidecar) if source else None
            if rid is None:
                result.media_held.append(f"{name} has no sidecar in staging, so it isn't committed")
                continue
            if screen.why(rid, committing=True) is not None:
                media_staged.forget_loose(root, self.keys, path, rid, self.vault.git.in_head)
                media_staged.delete(root, self.keys, rid, self.vault.git.in_head)
                self._media_dropped += 1
                continue
            groups.setdefault(source, []).append((path, None, sidecar))
        for item in swept.waiting:
            versions = screen.raw.versions(item.sidecar.rid)
            if not versions:
                continue
            lfs = lfs or lfs_state(self.vault.home.top)
            problem = None if lfs.installed and lfs.filter_set else f"git-lfs {lfs.summary()}"
            if problem is not None:
                if item.sidecar.problem != problem:
                    media_staged.write_sidecar(
                        self.vault.home, item.sidecar_path, item.sidecar.model_copy(update={"problem": problem})
                    )
                result.media_held.append(problem)
                continue
            target = media_layout.stored_path(root, item.sidecar.rid, versions[-1].ts, item.sidecar.ext)
            media_staged.move(item.path, target)
            groups.setdefault(media_layout.source_of(item.sidecar.rid), []).append(
                (target, item.path, item.sidecar_path)
            )
        return groups

    def _promote_source(self, name: str, items: list[tuple[Entry, RawRecord]]) -> SourceRun:
        run = self.vault.rawlog.write_run(name, [final for _, final in items])
        files = f" in {len(run.paths)} run files" if len(run.paths) > 1 else ""
        what = f"{run.written} record(s){files}" if run.written else "an interrupted run"
        # Committed even when nothing new was written: a crash between an earlier write and its commit left the run
        # file behind, and its staged files must not go before it is committed.
        commit = self.vault.commit([], f"Raw: {name}, {what} from {self.vault.machine}")
        self.staging.remember([(e.staged.record.rid, e.key) for e, _ in items if e.staged.cuts])
        for entry, _ in items:
            self.staging.delete(entry.path)
        redactions = sum((markers(final) for _, final in items), Counter[str]())
        return SourceRun(name, run.path, run.written, run.skipped, commit, redactions, run.paths)

    def _uncommitted_runs_with(self, rid: str) -> list[str]:
        """The run files git hasn't committed yet that hold a record, vault-relative."""
        found: list[str] = []
        for rel in self.vault.uncommitted_runs():
            with (self.vault.root / rel).open(encoding="utf-8", newline="\n") as f:
                for line in f:
                    data: Any = json.loads(line) if line.strip() else {}
                    if data.get("rid") == rid:
                        found.append(rel)
                        break
        return found

    def _hold(self, entry: Entry, problem: str) -> Held:
        if entry.staged.problem != problem:
            self.staging.write(entry.staged.model_copy(update={"problem": problem}))
        return Held(entry.key, entry.staged.record.source, problem)

    def _config(self) -> VaultConfig:
        """The settings in force now (DR-0521): what an edit of vault.yaml only tightens applies at once, the rest once
        committed, and a file that doesn't load stops the gate rather than leave it on older settings."""
        path = self.vault.root / CONFIG_FILE
        try:
            settings = inforce.load(self.vault.root, self.vault.home)
        except OSError as e:
            raise DocketError(f"{path}: can't read it: {e}") from e
        self._waiting = settings.waiting
        return settings.in_force

    def would_drop(self, config: VaultConfig) -> Counter[str]:
        """What the sweep would drop from staging under these settings, by why, writing nothing: a page names it before
        a change it asks once more about (DR-0509). The same two rules the sweep applies first, in its order."""
        dropped = Counter[str]()
        with self.vault.lock:
            for entry in self.staging.scan().entries:
                record = entry.staged.record
                source = config.sources.get(record.source)
                if source is not None and source.sensitivity == "private":
                    dropped["private"] += 1
                elif neverstore.match(record, config.never_store):
                    dropped["never-store"] += 1
        return dropped

    def _sweep(self, config: VaultConfig) -> Counter[str]:
        """Apply the current vault.yaml to what waits: drop what it now forbids, delete what was decided by hand, and
        send back to pending what nobody classified and can no longer go through on its source's word. The hold lists
        are read here too, on every call: a list that doesn't read stops the gate rather than let a held record by."""
        self._holds = holds.read_all(self.vault.root)
        self._releases = releaselog.read_all(self.vault.root)
        self.staging.clear_temp()
        self._refresh_cuts()
        scan = self.staging.scan()
        decided = self.staging.decided()
        cut_in_staging = _cut_rids(scan)
        dropped = Counter[str]()
        for entry in scan.entries:
            staged = entry.staged
            source = config.sources.get(staged.record.source)
            rid = staged.record.rid
            if source is not None and source.sensitivity == "private":
                self._drop_media(rid, withhold=False)
                self.staging.delete(entry.path)
                dropped["private"] += 1
            elif neverstore.match(staged.record, config.never_store):
                self._drop_media(rid, withhold=True)
                self.staging.delete(entry.path)
                dropped["never-store"] += 1
            elif entry.key in decided.keys:
                # Classified private, or promoted with cuts, before a crash let its file be deleted.
                self._drop_media(rid, withhold=False)
                self.staging.delete(entry.path)
            elif not self._holds.covers(staged.record) and self._left_out(staged.record):
                # Staged before its release arrived (on another clone, under its hold), and the release leaves it out.
                self._drop_media(rid, withhold=False)
                self.staging.delete(entry.path)
                dropped["left-out"] += 1
            elif (
                staged.state == "ready"
                and staged.classified_by is None
                and (
                    (source is not None and source.sensitivity == "mixed")
                    or self._by_hand(staged.record, decided, cut_in_staging)
                )
            ):
                self.staging.write(staged.model_copy(update={"state": "pending"}))
        self._media_sweep(config)
        if dropped:
            log.info("gate: swept %d record(s) out of staging", dropped.total())
        return dropped

    def _by_hand(self, record: RawRecord, decided: Decided, cut_in_staging: set[str]) -> bool:
        """Whether a record was decided by hand: here (classified private, or cut), on any machine (a cut version in
        raw, or a release of it), or in another staged version holding cuts. A new version of it then waits for the
        user."""
        rid = record.rid
        return (
            rid_hash(rid) in decided.rids
            or rid in self._cut_rids
            or rid in cut_in_staging
            or self._releases.covering(record) is not None
        )

    def _release_cuts(self, record: RawRecord) -> list[Cut]:
        """The cuts a release adds to a record with segments, every time it is cut; none for any other record."""
        release = self._releases.covering(record)
        return list(release.cuts) if release is not None and record.segments else []

    def _left_out(self, record: RawRecord) -> bool:
        """Whether a release leaves a staged record out: one without segments it doesn't keep at this version, or one
        with segments its times can't be made on."""
        release = self._releases.covering(record)
        if release is None:
            return False
        if not record.segments:
            return not release.keeps(record)
        try:
            cutting.apply(record, release.cuts)
        except DocketError:
            return True
        return False

    def intake_view(self, record: RawRecord) -> RawRecord | str:
        """What intake would make of one record, without staging it: why it would drop it ("private source",
        "never-store"), or the record redacted and versioned as it would be staged. `docket hold show` and a release's
        screen read held records through this, so what the owner reads and keeps is what the gate would write. It
        refuses what `stage` refuses, a source missing from vault.yaml or disabled there, so a release stops before
        its question, not after its commits."""
        config = self._config()
        source = config.sources.get(record.source)
        if source is None:
            raise DocketError(f"source {record.source!r} isn't in vault.yaml")
        if not source.enabled:
            raise DocketError(f"source {record.source!r} is disabled in vault.yaml")
        if source.sensitivity == "private":
            return "private source"
        if neverstore.match(record, config.never_store):
            return "never-store"
        # The rules vault.yaml turns on, as intake applies them (DR-0190), so a held record reads as it would be staged.
        redacted, _ = Redactor.from_config(config.redaction).record(record)
        return redacted.model_copy(update={"v": version(redacted), "run": ""})

    def _refresh_cuts(self) -> None:
        """The rids whose raw text holds a cut marker, from the run files this gate hasn't read yet."""
        for run in self.vault.rawlog.run_files():
            if run.path in self._read_runs:
                continue
            with run.path.open(encoding="utf-8", newline="\n") as f:
                for line in f:
                    if CUT_MARKER not in line:
                        continue
                    data: Any = json.loads(line)
                    if CUT_MARKER in str(data.get("text", "")):
                        self._cut_rids.add(str(data["rid"]))
            self._read_runs.add(run.path)

    # Last, so the name doesn't shadow the builtin in the annotations above it.
    def list(self, source: str | None = None, state: State | None = None) -> Listing:
        """What waits in staging, oldest first, and any file that isn't a staged record."""
        with self.vault.lock:
            self._sweep(self._config())
            scan = self.staging.scan()
            decided, cut_in_staging = self.staging.decided(), _cut_rids(scan)
            why: dict[str, Why] = {e.key: self._why(e.staged, decided, cut_in_staging) for e in scan.entries}
        entries = sorted(
            (
                e
                for e in scan.entries
                if (source is None or e.staged.record.source == source) and (state is None or e.staged.state == state)
            ),
            key=lambda e: (e.staged.record.ts, e.key),
        )
        media = media_staged.count_waiting(self.vault.root)
        return Listing(entries, scan.unreadable, {e.key: why[e.key] for e in entries}, media)

    def _why(self, staged: Staged, decided: Decided, cut_in_staging: set[str]) -> Why:
        """Why a staged record waits, from the facts the sweep just read."""
        record = staged.record
        if self._holds.covers(record):
            return "on-hold"
        if staged.problem is not None:
            return "held-back"
        if staged.state == "ready":
            return "work" if staged.classified_by is None else "classified"
        if self._releases.covering(record) is not None:
            return "released"
        return "by-hand" if self._by_hand(record, decided, cut_in_staging) else "mixed"


def _cut_rids(scan: Scan) -> set[str]:
    return {e.staged.record.rid for e in scan.entries if e.staged.cuts}


def _resolve(ref: str, entries: list[Entry]) -> Entry:
    """A staged record by its rid, or by a prefix of its key."""
    ref = ref.strip()
    if ":" in ref:
        found = [e for e in entries if e.staged.record.rid == ref]
    else:
        if len(ref) < MIN_KEY_PREFIX:
            raise DocketError(f"name a staged record by its rid, or by at least {MIN_KEY_PREFIX} characters of its key")
        found = [e for e in entries if e.key.startswith(ref.lower())]
    if not found:
        raise DocketError(f"nothing staged as {ref}")
    if len(found) > 1:
        keys = ", ".join(e.key[:12] for e in found)
        raise DocketError(f"{ref} names {len(found)} staged versions ({keys}); use a longer key")
    return found[0]
