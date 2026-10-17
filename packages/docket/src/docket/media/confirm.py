"""`docket media confirm-cuts <rid>` (DR-0561): the owner says a recording's old hand decisions were cuts.

A decision written before `_decided.jsonl` kept its kind could have been a private classification, so it refuses the
recording's media. This shows the evidence for each of the recording's rids with such decisions: how many there are,
and which of its versions in raw were cut by hand, with their runs and cut times. A version whose every `[cut]` span
lies inside the release's cuts was cut by the release alone, and is left out, since a release leaves no decision.
When a rid has more kind-less decisions than versions cut by hand, at least one was a private classification, and
nothing is confirmed. Otherwise the owner types back a code at their own console, and each of those keys gets a `cut`
line. Claude Code's shell, a pipe or a redirect is refused (DR-0518).
"""

from __future__ import annotations

from dataclasses import dataclass

from docket import console
from docket.errors import DocketError
from docket.media import layout
from docket.media import ranges as cut_ranges
from docket.media.ranges import Range
from docket.vault import releaselog
from docket.vault.spans import RawLookup
from docket.vault.staging import Staging, rid_hash
from docket.vault.vault import Vault

ACT = "a confirmation that a recording's decisions were cuts"


@dataclass(frozen=True)
class Evidence:
    """One rid's kind-less decisions, and its versions in raw cut by hand, each by its run, with what was cut."""

    rid: str
    keys: tuple[str, ...]
    by_hand: tuple[tuple[str, tuple[Range, ...]], ...]

    @property
    def matches(self) -> bool:
        return len(self.keys) <= len(self.by_hand)


def _clock(ms: int) -> str:
    seconds = ms // 1000
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


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
        keys = tuple(sorted(k for k, kind in decided.kinds.get(rid_hash(other), {}).items() if kind is None))
        if not keys:
            continue
        cut: list[tuple[str, tuple[Range, ...]]] = []
        for version in raw.versions(other):
            spans = cut_ranges.by_hand(version, granted)
            if spans:
                cut.append((version.run, tuple(spans)))
        out.append(Evidence(other, keys, tuple(cut)))
    return out


def lines(found: list[Evidence]) -> list[str]:
    out: list[str] = []
    for item in found:
        out.append(
            f"{item.rid}: {len(item.keys)} decision(s) without a kind, {len(item.by_hand)} version(s) cut by hand"
        )
        for run, spans in item.by_hand:
            out.append(f"  {run}: cut at {', '.join(f'{_clock(s)}-{_clock(e)}' for s, e in spans)}")
    return out


def confirm(vault: Vault, rid: str) -> int:
    """Show the evidence; on the code, mark each kind-less decision of the family a cut. How many were marked."""
    problem = console.problem(ACT)
    if problem is not None:
        raise DocketError(problem)
    found = evidence(vault, rid)
    if not found:
        raise DocketError(f"no record of {rid} has a decision without a kind; there is nothing to confirm")
    shown = lines(found)
    short = [item for item in found if not item.matches]
    if short:
        for line in shown:
            console.say(line)
        names = ", ".join(item.rid for item in short)
        raise DocketError(
            f"{names}: more decisions without a kind than versions cut by hand, so at least one was a private "
            "classification; the audio stays out"
        )
    screen = [*shown, "", "Each of these was a cut, and none a private classification?"]
    answer = console.typed_back(screen, "confirm they were cuts", "leave them as they are", "nothing was confirmed")
    if answer != "yes":
        return 0
    with vault.lock:
        again = evidence(vault, rid)
        if again != found:
            raise DocketError(f"{rid}'s decisions changed while you were asked; run it again")
        Staging(vault.root, vault.home).append_decided(
            [(rid_hash(item.rid), key) for item in found for key in item.keys], "cut"
        )
    return sum(len(item.keys) for item in found)
