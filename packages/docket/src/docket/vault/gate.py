"""The staging gate: the only way a record reaches the raw log (DR-0015).

Intake drops in memory what a private source or a never-store list names, so a dropped record never touches the disk,
then redacts the rest and stages it: `ready` from a work source, `pending` from a mixed one or when the record was
decided by hand before. Classification (by the user or Claude) makes a pending record ready, with cuts, or drops it.
Promotion cuts, redacts again with the current rules and writes one run per source. Every call first sweeps staging
against the current vault.yaml.

Nothing here names a dropped record: not a log line, not an error, not a file. Only counts leave the gate.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from docket.errors import DocketError
from docket.vault import cuts as cutting
from docket.vault import neverstore
from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.vault.cuts import CUT_MARKER, Cut
from docket.vault.rawlog import RUN_FILE
from docket.vault.records import RawRecord, version
from docket.vault.redact import Redactor, markers
from docket.vault.staging import Classifier, Decided, Entry, Scan, Staged, Staging, State, key_of, rid_hash
from docket.vault.vault import Vault

log = logging.getLogger(__name__)

Label = Literal["work", "private"]
MIN_KEY_PREFIX = 6


@dataclass
class StageResult:
    ready: int = 0
    pending: int = 0
    skipped: int = 0
    dropped: Counter[str] = field(default_factory=Counter[str])
    redactions: Counter[str] = field(default_factory=Counter[str])


@dataclass(frozen=True)
class SourceRun:
    source: str
    run: Path | None
    written: int
    skipped: int
    commit: str | None
    redactions: Counter[str]


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


@dataclass(frozen=True)
class Listing:
    entries: list[Entry]
    unreadable: list[tuple[Path, str]]

    def counts(self) -> Counter[str]:
        return Counter(e.staged.state for e in self.entries)


@dataclass(frozen=True)
class Shown:
    entry: Entry
    cut_before: RawRecord | None


class Gate:
    def __init__(self, vault: Vault) -> None:
        self.vault = vault
        self.staging = Staging(vault.root)
        self._cut_rids: set[str] = set()
        self._read_runs: set[Path] = set()

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
                        "private) so the gate can tell how private it is"
                    )
                if not source.enabled:
                    raise DocketError(f"source {record.source!r} is disabled in vault.yaml")
            result = StageResult(dropped=self._sweep(config))
            scan = self.staging.scan()
            staged_keys = {e.key for e in scan.entries}
            decided = self.staging.decided()
            cut_in_staging = _cut_rids(scan)
            redactor = Redactor.from_config(config.redaction)
            now = datetime.now(UTC)
            for record in batch:
                sensitivity = config.sources[record.source].sensitivity
                if sensitivity == "private":
                    result.dropped["private"] += 1
                    continue
                if neverstore.match(record, config.never_store):
                    result.dropped["never-store"] += 1
                    continue
                redacted, counts = redactor.record(record)
                redacted = redacted.model_copy(update={"v": version(redacted), "run": ""})
                key = key_of(redacted.rid, redacted.v)
                if key in staged_keys or key in decided.keys or self.vault.rawlog.contains(redacted):
                    result.skipped += 1
                    continue
                pending = sensitivity == "mixed" or self._by_hand(redacted.rid, decided, cut_in_staging)
                self.staging.write(Staged(state="pending" if pending else "ready", staged_at=now, record=redacted))
                staged_keys.add(key)
                result.pending += pending
                result.ready += not pending
                result.redactions += counts
            log.info(
                "gate: %d ready, %d pending, %d skipped, %d dropped",
                result.ready,
                result.pending,
                result.skipped,
                result.dropped.total(),
            )
            return result

    def show(self, ref: str) -> Shown:
        """One staged record, and the version of it in raw that was cut, when there is one."""
        with self.vault.lock:
            self._sweep(self._config())
            entry = _resolve(ref, self.staging.scan().entries)
            rid = entry.staged.record.rid
            cut = [r for r in self.vault.rawlog.read(rid) if CUT_MARKER in r.text]
            return Shown(entry, cut[-1] if cut else None)

    def classify(self, ref: str, label: Label, cuts: Sequence[Cut] = (), by: Classifier = "user") -> Staged | None:
        """Work makes a staged record ready, with its cuts; private drops it. Returns the record, or None if dropped."""
        if label not in ("work", "private"):
            raise DocketError(f"a record is work or private, not {label!r}")
        with self.vault.lock:
            config = self._config()
            self._sweep(config)
            entry = _resolve(ref, self.staging.scan().entries)
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
                self.staging.remember([(rid, entry.key)])
                self.staging.delete(entry.path)
                log.info("gate: one record classified private and dropped")
                return None
            merged = cutting.merge(cuts)
            # A dry run, in promotion's order, so a cut that can't be made fails now and not at promotion.
            Redactor.from_config(config.redaction).record(cutting.apply(entry.staged.record, merged))
            staged = entry.staged.model_copy(
                update={"state": "ready", "classified_by": by, "cuts": merged, "problem": None}
            )
            self.staging.write(staged)
            return staged

    def promote(self, source: str | None = None) -> PromoteResult:
        """Write every ready record to the raw log: cut, redacted with the current rules, one run per source."""
        with self.vault.lock:
            # Before anything is written: commit what an earlier promotion left behind, so a repo Docket can't commit
            # to (a rebase in progress, a detached HEAD) stops the promotion here, with no new run file on disk.
            self.vault.commit([], f"Raw: an interrupted run from {self.vault.machine}")
            config = self._config()
            result = PromoteResult(dropped=self._sweep(config))
            redactor = Redactor.from_config(config.redaction)
            groups: dict[str, list[tuple[Entry, RawRecord]]] = {}
            for entry in self.staging.scan().entries:
                staged = entry.staged
                name = staged.record.source
                if staged.state != "ready" or (source is not None and name != source):
                    continue
                try:
                    if name not in config.sources:
                        raise DocketError(f"source {name!r} isn't in vault.yaml any more")
                    final, _ = redactor.record(cutting.apply(staged.record, staged.cuts))
                except DocketError as e:
                    result.held.append(self._hold(entry, str(e)))
                    continue
                groups.setdefault(name, []).append((entry, final))
            for name in sorted(groups):
                result.runs.append(self._promote_source(name, groups[name]))
            log.info(
                "gate: promoted %d record(s), held %d, dropped %d",
                sum(r.written for r in result.runs),
                len(result.held),
                result.dropped.total(),
            )
            return result

    # -- inside the gate -------------------------------------------------------------------------------------------

    def _promote_source(self, name: str, items: list[tuple[Entry, RawRecord]]) -> SourceRun:
        run = self.vault.rawlog.write_run(name, [final for _, final in items])
        what = f"{run.written} record(s)" if run.written else "an interrupted run"
        # Committed even when nothing new was written: a crash between an earlier write and its commit left the run
        # file behind, and its staged files must not go before it is committed.
        commit = self.vault.commit([], f"Raw: {name}, {what} from {self.vault.machine}")
        self.staging.remember([(e.staged.record.rid, e.key) for e, _ in items if e.staged.cuts])
        for entry, _ in items:
            self.staging.delete(entry.path)
        redactions = sum((markers(final) for _, final in items), Counter[str]())
        return SourceRun(name, run.path, run.written, run.skipped, commit, redactions)

    def _uncommitted_runs_with(self, rid: str) -> list[str]:
        """The run files git hasn't committed yet that hold a record, vault-relative."""
        found: list[str] = []
        for rel in self.vault.git.untracked("raw"):
            if not RUN_FILE.match(Path(rel).name):
                continue
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
        """vault.yaml as it is now: a list the user just edited applies at once, and one that doesn't load stops
        the gate rather than leave it on an older list."""
        path = self.vault.root / CONFIG_FILE
        try:
            return VaultConfig.load(path)
        except OSError as e:
            raise DocketError(f"{path}: can't read it: {e}") from e

    def _sweep(self, config: VaultConfig) -> Counter[str]:
        """Apply the current vault.yaml to what waits: drop what it now forbids, delete what was decided by hand, and
        send back to pending what nobody classified and can no longer go through on its source's word."""
        self.staging.clear_temp()
        self._refresh_cuts()
        scan = self.staging.scan()
        decided = self.staging.decided()
        cut_in_staging = _cut_rids(scan)
        dropped = Counter[str]()
        for entry in scan.entries:
            staged = entry.staged
            source = config.sources.get(staged.record.source)
            if source is not None and source.sensitivity == "private":
                self.staging.delete(entry.path)
                dropped["private"] += 1
            elif neverstore.match(staged.record, config.never_store):
                self.staging.delete(entry.path)
                dropped["never-store"] += 1
            elif entry.key in decided.keys:
                # Classified private, or promoted with cuts, before a crash let its file be deleted.
                self.staging.delete(entry.path)
            elif (
                staged.state == "ready"
                and staged.classified_by is None
                and (
                    (source is not None and source.sensitivity == "mixed")
                    or self._by_hand(staged.record.rid, decided, cut_in_staging)
                )
            ):
                self.staging.write(staged.model_copy(update={"state": "pending"}))
        if dropped:
            log.info("gate: swept %d record(s) out of staging", dropped.total())
        return dropped

    def _by_hand(self, rid: str, decided: Decided, cut_in_staging: set[str]) -> bool:
        """Whether a record was decided by hand: here (classified private, or cut), on any machine (a cut version in
        raw), or in another staged version holding cuts. A new version of it then waits for the user."""
        return rid_hash(rid) in decided.rids or rid in self._cut_rids or rid in cut_in_staging

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
        entries = sorted(
            (
                e
                for e in scan.entries
                if (source is None or e.staged.record.source == source) and (state is None or e.staged.state == state)
            ),
            key=lambda e: (e.staged.record.ts, e.key),
        )
        return Listing(entries, scan.unreadable)


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
