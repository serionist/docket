"""A Whisper transcript follows its family's cuts (DR-0590, DR-0591): the promotion that brings a cut to a recording's
family writes the family's Whisper transcript's next version, cut by text, in the same run, so search never shows a
stretch the owner cut. It needs no Whisper, audio or key.

| Step | What |
|---|---|
| the scan | the gate's cut scan keeps each rid's `[cut]` spans and each machine transcript's latest marks (`Seen`) |
| the families | those with a cut, in raw or promoted now, and a Whisper transcript, in raw or promoted now |
| the ranges | the family's, as media's screen reads them, in memory; a transcript whose marks don't hold them is due |
| the cut | `cut`: what overlaps a range goes, an end outside its own marks padded as the audio is |

The gate reads raw only for a transcript that is due, and decides the rest: the sources this machine runs, a hold, the
redaction rules and the never-store lists.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from typing import Any

from docket.errors import DocketError
from docket.media import cutter, layout
from docket.media.ranges import Range, covers, marks, merge
from docket.vault import cuts as cutting
from docket.vault.records import TRANSCRIBED_VIA, RawRecord, owning_id
from docket.vault.releaselog import Releases
from docket.vault.segments import render
from docket.vault.staging import Entry

SUFFIX = "/whisper"
# Where a line stands in raw: its run file's place in run order, then its line in the file.
At = tuple[tuple[str, str, str, int, str], int]


def whisper_rid(rid: str) -> str:
    return f"{rid}{SUFFIX}"


def is_machine(record: RawRecord) -> bool:
    """A transcript Docket made by machine (DR-0570)."""
    return record.via.startswith(TRANSCRIBED_VIA)


@dataclass
class Seen:
    """What the gate's cut scan keeps of raw: each rid's `[cut]` spans over its versions, and each machine transcript's
    marks at its latest line by run order, whether that line holds a `[cut]` or not."""

    spans: dict[str, set[Range]] = field(default_factory=dict[str, set[Range]])
    latest: dict[str, tuple[At, tuple[Range, ...]]] = field(default_factory=dict[str, tuple[At, tuple[Range, ...]]])

    def add(self, data: Any, at: At) -> None:
        """One line of raw, parsed."""
        rid = str(data["rid"])
        segs: list[Any] = data.get("segments") or []
        found = tuple((int(s["start_ms"]), int(s["end_ms"])) for s in segs if s.get("text") == cutting.CUT_MARKER)
        if found:
            self.spans.setdefault(rid, set()).update(found)
        if str(data.get("via", "")).startswith(TRANSCRIBED_VIA):
            before = self.latest.get(rid)
            if before is None or before[0] < at:
                self.latest[rid] = (at, found)


@dataclass(frozen=True)
class Due:
    """A Whisper transcript whose family has a range its latest version doesn't hold: its rid, the family's ranges; the
    key of its staged version when the promotion takes that one, to be cut in its place; else where its latest version
    stands in raw, so the gate writes the cut only in a run that sorts after it."""

    rid: str
    ranges: tuple[Range, ...]
    key: str | None = None
    at: At | None = None


def due(
    seen: Seen,
    promoted: Sequence[tuple[Entry, RawRecord]],
    staged: Sequence[Entry],
    releases: Releases,
    sources: Collection[str],
) -> list[Due]:
    """The Whisper transcripts of these sources to cut at this promotion, worked out in memory from the scan, what the
    promotion writes (each staged entry with its final record) and what else waits in staging. A family whose staged
    Whisper transcript the promotion takes gets only that one cut, so a version from raw never hides it."""
    taken = {final.rid: (entry.key, final) for entry, final in promoted if is_machine(final)}
    cut = [*seen.spans, *(final.rid for _, final in promoted if marks(final)), *taken]
    families = {
        owner
        for rid in cut
        for owner in layout.owners(rid)
        if owner.partition(":")[0] in sources and (whisper_rid(owner) in seen.latest or whisper_rid(owner) in taken)
    }
    keys = {entry.key for entry, _ in promoted}
    waiting = [e for e in staged if e.key not in keys]
    out: list[Due] = []
    for family in sorted(families):
        rid = whisper_rid(family)
        ranges = _ranges(family, seen, [final for _, final in promoted], waiting, releases)
        if rid in taken:
            key, held, at = taken[rid][0], marks(taken[rid][1]), None
        else:
            key, held, at = None, list(seen.latest[rid][1]), seen.latest[rid][0]
        if not covers(held, ranges):
            out.append(Due(rid, ranges, key, at))
    return out


def cut(record: RawRecord, ranges: Sequence[Range]) -> RawRecord:
    """A Whisper transcript's next version cut at the ranges: an end of a range outside its own marks is padded as its
    audio is silenced (DR-0560), since it was heard before it was; everything but its segments and text is kept."""
    segs = cutting.place(record.segments or [], ranges, cutter.PAD_MS)
    return record.model_copy(update={"segments": segs, "text": render(segs), "native": {}})


def _ranges(
    family: str, seen: Seen, promoted: Sequence[RawRecord], waiting: Sequence[Entry], releases: Releases
) -> tuple[Range, ...]:
    """A family's ranges, as media's screen reads them (DR-0561): every `[cut]` span in raw and in what the promotion
    writes, staging's with their own time cuts made, and the release's cuts. A cut by characters, or a staged one that
    doesn't fit yet, adds none: the times known are cut."""
    found = [span for rid, spans in seen.spans.items() if layout.in_family(family, rid) for span in spans]
    found += [span for record in promoted if layout.in_family(family, record.rid) for span in marks(record)]
    found += [span for e in waiting if layout.in_family(family, e.staged.record.rid) for span in marks(_made(e))]
    source = family.partition(":")[0]
    granted = releases.of(source)
    released = owning_id(family, source, frozenset(granted))
    if released is not None:
        found += [(c.start, c.end) for c in granted[released].cuts]
    return merge(found)


def _made(entry: Entry) -> RawRecord:
    """A staged record with its own time cuts made, as promotion will make them; as it stands when they can't be."""
    record, cuts = entry.staged.record, list(entry.staged.cuts)
    if not cuts or not record.segments or any(c.unit == "char" for c in cuts):
        return record
    try:
        return cutting.apply(record, cuts)
    except DocketError:
        # A cut promotion would hold: its times aren't settled yet.
        return record
