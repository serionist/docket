"""Cuts: the stretches of a record marked non-work, removed before it reaches the raw log.

A cut is a range of characters in the staged text, or of milliseconds in a transcript. The core makes both (DR-0028):
characters in `text`, times in a transcript's `segments`, whose text is rendered from them, so a record with segments
takes time cuts only and one without takes characters only. No source registers a cutter.

Every cut leaves `[cut]` in `text` where it cut: that marker is how another machine knows the record was cut by hand.
A cut record keeps no `native`, which would still hold the stretch, and a verbatim one is marked `fidelity: cut`
(DR-0126).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from docket.errors import DocketError
from docket.vault.records import RawRecord, Segment
from docket.vault.segments import normalize, render

CUT_MARKER = "[cut]"

Unit = Literal["char", "ms"]


class Cut(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: int = Field(ge=0)
    end: int
    unit: Unit = "char"

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end <= self.start:
            raise ValueError(f"a cut ends after it starts, not at {self.end} from {self.start}")
        return self


def merge(cuts: Sequence[Cut]) -> list[Cut]:
    """Cuts in order, with overlapping or touching ones of the same unit made one."""
    out: list[Cut] = []
    for cut in sorted(cuts, key=lambda c: (c.unit, c.start, c.end)):
        last = out[-1] if out else None
        if last is not None and last.unit == cut.unit and cut.start <= last.end:
            out[-1] = Cut(start=last.start, end=max(last.end, cut.end), unit=cut.unit)
        else:
            out.append(cut)
    return out


def cut_text(record: RawRecord, cuts: list[Cut]) -> RawRecord:
    """Each character range of `text` becomes `[cut]`, and `native` is cleared, because a copy of the stretch there
    may be HTML or escaped, which a plain replace would miss."""
    text = record.text
    for cut in sorted(cuts, key=lambda c: c.start, reverse=True):
        if cut.end > len(text):
            raise DocketError(f"a cut to character {cut.end} is past the end of the text ({len(text)})")
        text = text[: cut.start] + CUT_MARKER + text[cut.end :]
    return record.model_copy(update={"text": text, "native": {}})


def _overlaps(segment: Segment, cut: Cut) -> bool:
    return _within(segment, cut.start, cut.end)


def _within(segment: Segment, start: int, end: int) -> bool:
    # A segment with no length still sits at a moment: it goes when that moment is inside the cut.
    return segment.start_ms < end and max(segment.end_ms, segment.start_ms + 1) > start


def place(segs: Sequence[Segment], ranges: Sequence[tuple[int, int]], pad: int = 0) -> list[Segment]:
    """A machine transcript's segments cut at time ranges (DR-0570, DR-0590): every segment overlapping a range goes,
    `[cut]` ones too, and one `[cut]` segment sits at each range, merged with the ones it held. Never widened over what
    it removed, since every transcript's `[cut]` segments widen its audio's cut (DR-0561), and never refused for a range
    after the last segment. An end of a range outside the `[cut]` segments it held is padded by `pad` for what goes:
    the words there were heard from audio not yet silent there."""
    held = [(s.start_ms, s.end_ms) for s in segs if s.text == CUT_MARKER]
    spans: list[tuple[int, int]] = []
    for start, end in sorted([*ranges, *held]):
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    # Each end on its own: one inside a mark it held was silent in the audio it was heard from, so a range that grew
    # on one side is padded on that side only.
    drop = [
        (start if _held(start, held) else max(0, start - pad), end if _held(end, held) else end + pad)
        for start, end in spans
    ]
    kept = [s for s in segs if not any(_within(s, start, end) for start, end in drop)]
    return normalize([*kept, *(Segment(start_ms=start, end_ms=end, text=CUT_MARKER) for start, end in spans)])


def _held(at: int, held: Sequence[tuple[int, int]]) -> bool:
    return any(start <= at <= end for start, end in held)


def cut_segments(record: RawRecord, cuts: list[Cut]) -> RawRecord:
    """Each segment overlapping a cut goes whole, since an utterance has no word times; one `[cut]` segment spans each
    cut widened to what it removed, cuts that touch after widening made one; the text is rendered again."""
    segs = list(record.segments or [])
    # Where the transcript ends as `_overlaps` reads it: an utterance with no length still takes its millisecond.
    last = max(max(s.end_ms, s.start_ms + 1) for s in segs)
    for cut in cuts:
        if cut.start >= last:
            raise DocketError(f"a cut from {_clock(cut.start)} starts after the transcript ends ({_clock(last)})")
    spans: list[tuple[int, int]] = []
    for cut in cuts:
        removed = [s for s in segs if _overlaps(s, cut)]
        start = min([cut.start, *(s.start_ms for s in removed)])
        end = max([cut.end, *(s.end_ms for s in removed)])
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    kept = [s for s in segs if not any(_overlaps(s, cut) for cut in cuts)]
    marks = [Segment(start_ms=start, end_ms=end, text=CUT_MARKER) for start, end in spans]
    ordered = normalize([*kept, *marks])
    return record.model_copy(update={"segments": ordered, "text": render(ordered), "native": {}})


def widen(segs: Sequence[Segment], cut: Cut) -> Cut:
    """A time cut taken out to every utterance it touches, and again, until no further utterance overlaps: the span a
    release shows the owner and logs, so cutting by it again removes exactly the utterances they saw go, even where
    two people talk over each other."""
    if cut.unit != "ms":
        raise DocketError("only a time cut widens over a transcript's utterances")
    span = cut
    while True:
        touched = removes(segs, span)
        start = min([span.start, *(s.start_ms for s in touched)])
        end = max([span.end, *(s.end_ms for s in touched)])
        if (start, end) == (span.start, span.end):
            return span
        span = Cut(start=start, end=end, unit="ms")


def removes(segs: Sequence[Segment], cut: Cut) -> list[Segment]:
    """The utterances a time cut takes out: every one it overlaps."""
    return [s for s in segs if _overlaps(s, cut)]


def apply(record: RawRecord, cuts: Sequence[Cut]) -> RawRecord:
    """The record with its cuts made: by time on a transcript's segments, by characters on any other text."""
    if not cuts:
        return record
    merged = merge(cuts)
    units = {c.unit for c in merged}
    if record.segments:
        if "char" in units:
            # The text is rendered from the segments, so a character cut would leave the stretch in them.
            raise DocketError(
                f"{record.rid} is a transcript, its text rendered from its segments: cut a time, such as "
                "12:30-14:05, not characters"
            )
        result = cut_segments(record, merged)
    else:
        if "ms" in units:
            raise DocketError(f"{record.rid} has no segments to cut by time; cut its transcript, or classify it whole")
        result = cut_text(record, merged)
    # A derived record keeps its mark, which says its id was built from its content; [cut] and no native say the rest.
    return result.model_copy(update={"fidelity": "cut"}) if result.fidelity == "verbatim" else result


def _clock(ms: int) -> str:
    seconds = ms // 1000
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


_TIME = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?$")


def parse(spec: str) -> Cut:
    """`120-340` is characters; `12:30-14:05` or `1:02:03-1:04:00` is a time range, and a time may carry milliseconds,
    `0:30.400`, so a cut made on an utterance's own times can be written back exactly (`spec`)."""
    parts = spec.split("-")
    if len(parts) != 2 or not all(p.strip() for p in parts):
        raise DocketError(f"a cut is <start>-<end>, such as 120-340 or 12:30-14:05, not {spec!r}")
    start, end = (p.strip() for p in parts)
    if start.isdigit() and end.isdigit():
        return _cut(int(start), int(end), "char", spec)
    return _cut(_ms(start, spec), _ms(end, spec), "ms", spec)


def _ms(value: str, spec: str) -> int:
    m = _TIME.match(value)
    if not m:
        raise DocketError(f"a cut's times are mm:ss or hh:mm:ss, and both ends the same kind: {spec!r}")
    hours, minutes, seconds = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + int((m.group(4) or "").ljust(3, "0"))


def spec(cut: Cut) -> str:
    """A cut as `parse` reads it back to the same cut: characters as `120-340`, a time as `12:30-14:05`, with
    milliseconds where it has them."""
    if cut.unit == "char":
        return f"{cut.start}-{cut.end}"
    return f"{exact(cut.start)}-{exact(cut.end)}"


def exact(ms: int) -> str:
    """A time as `parse` reads it back to the same millisecond: `12:40`, or `12:40.500` when it isn't a whole
    second."""
    seconds, fraction = divmod(ms, 1000)
    return _clock(seconds * 1000) + (f".{fraction:03d}" if fraction else "")


def _cut(start: int, end: int, unit: Unit, spec: str) -> Cut:
    if end <= start:
        raise DocketError(f"a cut ends after it starts: {spec!r}")
    return Cut(start=start, end=end, unit=unit)
