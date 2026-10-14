"""Cuts: the stretches of a record marked non-work, removed before it reaches the raw log.

A cut is a range of characters in the staged text, or of milliseconds in a recording. Characters are cut here; times
need the source's own cutter, which its connector registers, because only it knows where a recording keeps its
segments. Every cutter leaves `[cut]` in `text` where it cut: that marker is how another machine knows the record was
cut by hand.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from docket.errors import DocketError
from docket.vault.records import RawRecord

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


Cutter = Callable[[RawRecord, list[Cut]], RawRecord]

_CUTTERS: dict[str, Cutter] = {}


def register_cutter(source: str, cutter: Cutter) -> None:
    """A connector whose records carry time (a recording's segments) registers how to cut them."""
    _CUTTERS[source] = cutter


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
    """The default cutter: each character range of `text` becomes `[cut]`, and `native` and the segments are cleared,
    because a copy of the stretch there may be HTML or escaped, or split across utterances, which a plain replace
    would miss."""
    if any(c.unit == "ms" for c in cuts):
        raise DocketError(f"{record.source} has no cutter for time cuts; its connector brings one")
    text = record.text
    for cut in sorted(cuts, key=lambda c: c.start, reverse=True):
        if cut.end > len(text):
            raise DocketError(f"a cut to character {cut.end} is past the end of the text ({len(text)})")
        text = text[: cut.start] + CUT_MARKER + text[cut.end :]
    return record.model_copy(update={"text": text, "native": {}, "segments": None})


def apply(record: RawRecord, cuts: Sequence[Cut]) -> RawRecord:
    """The record with its cuts made, by its source's cutter or the default one."""
    if not cuts:
        return record
    cutter = _CUTTERS.get(record.source, cut_text)
    result = cutter(record, merge(cuts))
    if CUT_MARKER not in result.text:
        raise DocketError(f"{record.source}'s cutter left no {CUT_MARKER} in the text; another machine couldn't tell")
    return result


_TIME = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")


def parse(spec: str) -> Cut:
    """`120-340` is characters; `12:30-14:05` or `1:02:03-1:04:00` is a time range."""
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
    return ((hours * 60 + minutes) * 60 + seconds) * 1000


def _cut(start: int, end: int, unit: Unit, spec: str) -> Cut:
    if end <= start:
        raise DocketError(f"a cut ends after it starts: {spec!r}")
    return Cut(start=start, end=end, unit=unit)
