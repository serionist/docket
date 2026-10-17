"""The stretches of a recording's audio that are cut (DR-0561), in milliseconds on the recording's timeline.

| From | Ranges |
|---|---|
| the release covering the rid (DR-0200) | its cuts, already widened over the transcript's utterances |
| a transcript's version in raw | its `[cut]` segments, which span what each cut removed (DR-0028) |
| a transcript's staged version | its `[cut]` segments once its own cuts are made, as promotion will make them |

A record without segments cut by characters gives no time to cut the audio at, so its media is refused. Ranges are
kept as the transcript has them; the cutter pads them before it silences the audio.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from docket.errors import DocketError
from docket.vault import cuts as cutting
from docket.vault.cuts import CUT_MARKER
from docket.vault.records import RawRecord

if TYPE_CHECKING:  # the staging module reaches the vault package, which imports the gate
    from docket.vault.staging import Entry

# Why a family has no time to cut its audio at: a record cut by characters, or staged cuts that don't fit yet.
NoTime = Literal["characters", "unfit"]

Range = tuple[int, int]


@dataclass(frozen=True)
class Needed:
    """What a rid's media must be cut by, and where its transcripts start and end, for the timeline checks."""

    ranges: tuple[Range, ...] = ()
    first_ms: int | None = None
    last_ms: int | None = None


def merge(ranges: Iterable[Range]) -> tuple[Range, ...]:
    """Ranges in order, with overlapping or touching ones made one."""
    out: list[Range] = []
    for start, end in sorted(ranges):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return tuple(out)


def covers(applied: Sequence[Range], needed: Sequence[Range]) -> bool:
    """Whether every needed range lies inside the ranges applied."""
    done = merge(applied)
    return all(any(a <= start and end <= b for a, b in done) for start, end in merge(needed))


def marks(record: RawRecord) -> list[Range]:
    """A transcript's `[cut]` segments, as ranges."""
    return [(s.start_ms, s.end_ms) for s in record.segments or [] if s.text == CUT_MARKER]


def by_characters(record: RawRecord) -> bool:
    """A record without segments that was cut: by characters, so with no time to cut its audio at."""
    return not record.segments and (record.fidelity == "cut" or CUT_MARKER in record.text)


def needed(records: Iterable[RawRecord], release: Iterable[Range]) -> Needed:
    """The ranges of a family's transcripts, made cut, and of its release; with where the transcripts start and end."""
    found = list(records)
    timed = [s for record in found for s in record.segments or []]
    return Needed(
        ranges=merge([*release, *(r for record in found for r in marks(record))]),
        first_ms=min((s.start_ms for s in timed), default=None),
        last_ms=max((s.end_ms for s in timed), default=None),
    )


def made(staged: Sequence[Entry], raw: Sequence[RawRecord]) -> list[RawRecord] | NoTime:
    """A family's records with their staged cuts made, as promotion will make them, and its versions in raw; or why
    there is no time to cut its audio at."""
    out: list[RawRecord] = []
    for entry in staged:
        record = entry.staged.record
        if by_characters(record) or any(c.unit == "char" for c in entry.staged.cuts):
            return "characters"
        if entry.staged.cuts:
            try:
                record = cutting.apply(record, list(entry.staged.cuts))
            except DocketError:
                # A cut promotion would hold, so its times aren't settled yet.
                return "unfit"
        out.append(record)
    if any(by_characters(record) for record in raw):
        return "characters"
    return [*out, *raw]


def by_hand(record: RawRecord, release: Sequence[Range]) -> list[Range]:
    """A version's `[cut]` spans that its release's cuts don't hold: what was cut by hand. A version whose every
    span lies inside the release's cuts was cut by the release alone."""
    return [r for r in marks(record) if not covers(release, [r])]
