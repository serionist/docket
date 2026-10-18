"""`docket media confirm-cuts <rid>` (DR-0561): the owner says a recording's old hand decisions were cuts.

A decision written before `_decided.jsonl` kept its kind could have been a private classification, so it refuses the
recording's media. This shows the evidence for each of the recording's rids with such decisions: how many there are,
and the versions in raw each could be. Only a version this clone promoted cut by hand counts, since `decided` is this
clone's alone while raw is shared; a version cut by the release alone (its every `[cut]` span inside the release's
cuts) leaves no decision, and one for each `cut` decision the rid already has is spoken for. When a rid has more
kind-less decisions than versions left, at least one was a private classification, and nothing is confirmed.
Otherwise each of those keys gets a `cut` line. It asks nothing: the owner says yes before the command, at their own
terminal or in chat to Claude, who runs it (DR-0599).
"""

from __future__ import annotations

from dataclasses import dataclass

from docket import console
from docket.errors import DocketError
from docket.media import layout
from docket.media import ranges as cut_ranges
from docket.media.ranges import Range
from docket.vault import releaselog
from docket.vault.rawlog import RUN_FILE
from docket.vault.spans import RawLookup
from docket.vault.staging import Staging, rid_hash
from docket.vault.vault import Vault

Version = tuple[str, tuple[Range, ...]]


@dataclass(frozen=True)
class Evidence:
    """One rid's kind-less decisions; its versions in raw cut by hand on this clone and elsewhere, each by its run with
    what was cut; and how many `cut` decisions it already has, each accounting for one version cut here."""

    rid: str
    keys: tuple[str, ...]
    here: tuple[Version, ...]
    elsewhere: tuple[Version, ...]
    cut_decisions: int

    @property
    def left(self) -> int:
        """The versions cut here that no `cut` decision accounts for: what a kind-less decision could be."""
        return max(0, len(self.here) - self.cut_decisions)

    @property
    def matches(self) -> bool:
        return len(self.keys) <= self.left


def _clock(ms: int) -> str:
    seconds = ms // 1000
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def _machine(run: str) -> str | None:
    found = RUN_FILE.match(f"{run}.jsonl")
    return found.group(3) if found is not None else None


def _spans(spans: tuple[Range, ...]) -> str:
    return ", ".join(f"{_clock(s)}-{_clock(e)}" for s, e in spans)


def evidence(vault: Vault, rid: str) -> list[Evidence]:
    """The evidence for each rid of the recording's family with kind-less decisions."""
    layout.source_of(rid)
    staging = Staging(vault.root, vault.home)
    decided = staging.decided()
    raw = RawLookup(vault.root)
    staged = [e.staged.record for e in staging.scan().entries if layout.in_family(rid, e.staged.record.rid)]
    family = sorted({*raw.nested(rid), *(r.rid for r in staged), rid})
    latest = (raw.versions(rid) or [r for r in staged if r.rid == rid] or [None])[-1]
    release = releaselog.read_all(vault.root).covering(latest) if latest is not None else None
    granted = [(c.start, c.end) for c in release.cuts] if release is not None else []
    out: list[Evidence] = []
    for other in family:
        kinds = decided.kinds.get(rid_hash(other), {})
        keys = tuple(sorted(k for k, kind in kinds.items() if kind is None))
        if not keys:
            continue
        here: list[Version] = []
        elsewhere: list[Version] = []
        for version in raw.versions(other):
            spans = tuple(cut_ranges.by_hand(version, granted))
            if spans:
                (here if _machine(version.run) == vault.machine else elsewhere).append((version.run, spans))
        cut = sum(1 for kind in kinds.values() if kind == "cut")
        out.append(Evidence(other, keys, tuple(here), tuple(elsewhere), cut))
    return out


def lines(found: list[Evidence]) -> list[str]:
    out: list[str] = []
    for item in found:
        out.append(
            f"{item.rid}: {len(item.keys)} decision(s) without a kind; {len(item.here)} version(s) cut by hand on "
            f"this machine, {item.cut_decisions} of them already a cut decision"
        )
        could = " or ".join(run for run, _ in item.here) if item.left else "none of its versions"
        for key in item.keys:
            out.append(f"  decision {key[:8]} could be: {could}")
        for run, spans in item.here:
            out.append(f"  {run}: cut at {_spans(spans)}")
        for run, spans in item.elsewhere:
            out.append(f"  {run}: cut at {_spans(spans)} on another machine, which this one's decisions don't cover")
    return out


def confirm(vault: Vault, rid: str) -> int:
    """Show the evidence, then mark each kind-less decision of the family a cut. How many were marked."""
    with vault.lock:
        found = evidence(vault, rid)
        if not found:
            raise DocketError(f"no record of {rid} has a decision without a kind; there is nothing to confirm")
        for line in lines(found):
            console.say(line)
        short = [item for item in found if not item.matches]
        if short:
            names = ", ".join(item.rid for item in short)
            raise DocketError(
                f"{names}: more decisions without a kind than versions this machine cut by hand that no cut decision "
                "accounts for, so at least one was a private classification; the audio stays out"
            )
        Staging(vault.root, vault.home).append_decided(
            [(rid_hash(item.rid), key) for item in found for key in item.keys], "cut"
        )
    return sum(len(item.keys) for item in found)
