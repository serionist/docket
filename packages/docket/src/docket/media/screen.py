"""The four checks a media file passes at the put, before the fetch and again after it, at every sweep and at
promotion, under the settings in force then (DR-0547). Each reads only what is durable: vault.yaml in force, the raw
log, staging's files, its `decided` keys and its withheld marks, the hold lists and the release logs.

A media file's records are its rid's record and every record nested under `<rid>/`: Plaud's audio sits on the
recording, and what was said, and cut, is in the transcript under it. Thread and container stay out.

| Check | Fails when |
|---|---|
| its record | no version of the rid is staged or in raw |
| its source and text | the source isn't in force, is off or private; a record of it matches a never-store value |
| the owner's decisions | the rid is withheld; a record of it was decided by hand, staged with cuts or is in raw cut |
| holds and releases | a hold covers its record; a release with cuts does; a release leaves a record of it out |
| one file per rid | media for the rid is already in `media/` |

The reasons go to the owner at a put; the gate only counts them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from docket.media import layout
from docket.vault import neverstore
from docket.vault.config import VaultConfig
from docket.vault.cuts import CUT_MARKER
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

    def why(self, rid: str, *, committing: bool = False) -> str | None:
        """Why media for a rid can't be staged or stored now, or None when every check passes. `committing` is for a
        file a crash left in media/ uncommitted: it is the stored file the last check would find."""
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
        rids = {rid, *(record.rid for record in records)}
        cut = any(e.staged.cuts for e in staged) or any(_is_cut(r) for r in raw)
        if cut or any(rid_hash(other) in self.decided.rids for other in rids):
            return (
                f"{rid} or a record under it was cut or decided private by hand; its audio waits until Docket can "
                "cut it the same way"
            )
        latest = (own or raw_own)[-1]
        if self.holds.covers(latest):
            return f"{rid} is on hold: its media waits until the hold is released"
        release = self.releases.covering(latest)
        if release is not None and release.cuts:
            return f"{rid} was released with cuts: its audio waits until Docket can cut it the same way"
        if any(self.left_out(record) for record in records):
            return f"a release leaves a record of {rid} out"
        if not committing and layout.stored_files(self.root, rid):
            return f"media for {rid} is stored already"
        return None

    def _family_in_raw(self, rid: str) -> list[str]:
        if rid not in self._nested:
            self._nested[rid] = self.raw.nested(rid)
        return self._nested[rid]


def _is_cut(record: RawRecord) -> bool:
    return record.fidelity == "cut" or CUT_MARKER in record.text
