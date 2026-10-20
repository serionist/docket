"""The block a sync agent writes to `blocks/<source>.txt` (DR-0722): a line per finding, a label and its fields split
by ` | `. The labels are a one-way door, never renamed or removed, and a line gains fields only at its end, so a line
of an older shape still reads: each label takes its fields from the first count below to the second.

    RUN:      plaud | run-20261020T071500-ab12cd34ef56ab78 | captures 4 | ready 2, pending 0 | skipped 0 | ...
    NOTES:    recording-note:2026-10-19-pilot-weekly | plaud:rec-0101 | 10:01 | 10:58 | M-0001

Any other line, an agent's stray text or a code fence, is left out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Each label with the fewest fields its line takes, the shape before the block went to a file, and the most, agent.md's.
FIELDS: dict[str, tuple[int, int]] = {
    "RUN": (3, 7),
    "OWED": (6, 10),
    "INVITES": (4, 5),
    "WAITING": (4, 7),
    "CLOSES": (4, 4),
    "MEETINGS": (3, 3),
    "STREAMS": (4, 4),
    "NOTES": (4, 5),
    "MINUTES": (5, 5),
    "OUTCOME": (3, 3),
    "LEARNED": (2, 2),
}
SPLIT = " | "
_LINE = re.compile(r"^(?P<label>[A-Z]+):[ \t]*(?P<rest>.*)$")


@dataclass(frozen=True)
class Line:
    """A block line: its label, its fields stripped, its line number in the file, and what is wrong with its shape."""

    label: str
    fields: list[str]
    n: int
    problem: str | None = None


def parse(text: str) -> list[Line]:
    """The block's lines in order. A line with too few or too many fields for its label keeps its fields and says so;
    a field that held ` | ` splits, which shows as too many, and one that ended or began with `|` leaves a field
    starting or ending with it, which is said too: its bar moved a field along."""
    out: list[Line] = []
    for n, raw in enumerate(text.splitlines(), start=1):
        found = _LINE.match(raw.strip())
        if found is None or found["label"] not in FIELDS:
            continue
        label = found["label"]
        fields = [field.strip() for field in found["rest"].split(SPLIT)]
        least, most = FIELDS[label]
        barred = [k for k, field in enumerate(fields, start=1) if field.startswith("|") or field.endswith("|")]
        problem = None
        if not least <= len(fields) <= most:
            took = str(least) if least == most else f"{least} to {most}"
            problem = f"{label} takes {took} fields, not {len(fields)}"
        elif barred:
            problem = f"{label}'s field {barred[0]} starts or ends with |: a field holds no bar"
        out.append(Line(label=label, fields=fields, n=n, problem=problem))
    return out
