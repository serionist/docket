"""The four checks a media file passes at the put, before the fetch and again after it, at every sweep and at
promotion, under the settings in force then (DR-0547), and the ranges it must be cut by (DR-0561). Each reads only
what is durable: vault.yaml in force, the raw log, staging's files, its `decided` keys and their kinds, its withheld
marks, the hold lists and the release logs.

A media file's records are its rid's record and every record nested under `<rid>/`: Plaud's audio sits on the
recording, and what was said, and cut, is in the transcript under it. Thread and container stay out.

| Check | Fails when |
|---|---|
| its record | no version of the rid is staged or in raw |
| its source and text | the source isn't in force, is off or private; a record of it matches a never-store value |
| the owner's decisions | withheld; classified private, or decided before kinds were kept; cut by characters |
| holds and releases | a hold covers its record; a release leaves a record of it out |
| one file per rid | media for the rid is already in `media/`, cut by every range it needs |

A release's cuts and a transcript cut by time no longer refuse: they are the ranges the audio is cut by. Staged media
must have been cut by every range its family needs now, else it goes, and is put again. A stored file whose line in
the cut log doesn't hold every range its family needs now is put again beside it (DR-0562). The reasons go to the
owner at a put; the gate only counts them.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from docket.media import cutlog, layout
from docket.media import ranges as cut_ranges
from docket.media.ranges import Needed, Range
from docket.vault import neverstore
from docket.vault.config import VaultConfig
from docket.vault.holds import Holds
from docket.vault.records import RawRecord
from docket.vault.releaselog import Releases
from docket.vault.spans import RawLookup
from docket.vault.staging import Decided, Entry, rid_hash


@dataclass
class Screen:
    """What the checks read, gathered once for a sweep, a promotion or a put."""

    root: Path
    config: VaultConfig
    staged: list[Entry]
    raw: RawLookup
    decided: Decided
    withheld: frozenset[str]
    holds: Holds
    releases: Releases
    left_out: Callable[[RawRecord], bool]
    _nested: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    _logged: dict[str, dict[tuple[str, str], tuple[Range, ...]]] = field(
        default_factory=dict[str, dict[tuple[str, str], tuple[Range, ...]]]
    )

    def why(self, rid: str, *, committing: bool = False, applied: Sequence[Range] | None = None) -> str | None:
        """Why media for a rid can't be staged or stored now, or None when every check passes. `committing` is for a
        file a crash left in media/ uncommitted: it is the stored file the last check would find. `applied` is what
        a staged file was cut by, which must hold every range its family needs now."""
        found = self.plan(rid, committing=committing)
        if isinstance(found, str):
            return found
        if applied is not None and not cut_ranges.covers(applied, found.ranges):
            return f"{rid} has cuts its media wasn't cut by; it is put again, cut, at the next run"
        return None

    def plan(self, rid: str, *, committing: bool = False) -> Needed | str:
        """The ranges a rid's media must be cut by, or why it can't be staged or stored now."""
        own = [e.staged.record for e in self.staged if e.staged.record.rid == rid]
        raw_own = self.raw.versions(rid)
        if not own and not raw_own:
            return f"no record {rid} is staged or in raw; media follows its record, so stage the record first"
        source = layout.source_of(rid)
        entry = self.config.sources.get(source)
        if entry is None:
            return f"source {source} isn't in vault.yaml in force"
        if not entry.enabled:
            return f"source {source} is off in vault.yaml"
        if entry.sensitivity == "private":
            return f"source {source} is private: nothing from it is stored"
        staged = [e for e in self.staged if layout.in_family(rid, e.staged.record.rid)]
        raw = [r for other in self._family_in_raw(rid) for r in self.raw.versions(other)]
        records = [*(e.staged.record for e in staged), *raw]
        if any(neverstore.match(record, self.config.never_store) for record in records):
            return f"a record of {rid} matches a never-store value"
        if rid_hash(rid) in self.withheld:
            return f"a version of {rid} or a record under it was dropped for a never-store value or as private"
        if any(self.decided.private(rid_hash(other)) for other in {rid, *(record.rid for record in records)}):
            return (
                f"{rid} or a record under it was classified private by hand, or decided before Docket kept which "
                f"each decision was; if each was a cut, `docket media confirm-cuts {rid}` in your own terminal"
            )
        latest = (own or raw_own)[-1]
        if self.holds.covers(latest):
            return f"{rid} is on hold: its media waits until the hold is released"
        if any(self.left_out(record) for record in records):
            return f"a release leaves a record of {rid} out"
        made = cut_ranges.made(staged, raw)
        if made == "characters":
            return f"{rid} or a record under it was cut by characters, so there is no time to cut its audio at"
        if made == "unfit":
            return f"{rid}'s staged cuts don't fit its transcript, so there is no time to cut its audio at yet"
        release = self.releases.covering(latest)
        needed = cut_ranges.needed(made, [(c.start, c.end) for c in release.cuts] if release is not None else [])
        stored = layout.stored_files(self.root, rid)
        if not committing and stored and cut_ranges.covers(self.cut_by(rid, stored[0]), needed.ranges):
            return f"media for {rid} is stored already"
        return needed

    def cut_by(self, rid: str, path: Path) -> tuple[Range, ...]:
        """What the rid's stored file was cut by: the cut log's line for its key (DR-0562); none for a file stored as it
        was put, one whose key no line names, or one that isn't here to read."""
        source = layout.source_of(rid)
        if source not in self._logged:
            self._logged[source] = cutlog.logged(self.root, source)
        return cutlog.cut_by(self._logged[source], rid_hash(rid), path)

    def _family_in_raw(self, rid: str) -> list[str]:
        if rid not in self._nested:
            self._nested[rid] = self.raw.nested(rid)
        return self._nested[rid]
