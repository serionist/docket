"""When curate rounds happen (DR-0468, DR-0474): a round's id and start, when the next is due, and the review minutes
per week, all from the curate log. It loads nothing of the index, so the session-start hook can say a round is due.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from docket.vault.curatelog import Line

DUE = timedelta(days=7)
# A pause in a round longer than this counts as this much: the rest was time away (DR-0474).
GAP = timedelta(minutes=5)
STAMP = "%Y-%m-%dT%H%M%S"


def round_id(now: datetime, machine: str) -> str:
    return f"{now:{STAMP}}-{machine}"


def started(round_: str) -> datetime | None:
    try:
        return datetime.strptime(round_[:17], STAMP).replace(tzinfo=UTC)
    except ValueError:
        return None


def now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def minutes(lines: Sequence[Line]) -> dict[str, float]:
    """Review minutes per ISO week, newest first: per round, the gaps from its start through each of its lines, each
    capped at five minutes (DR-0474)."""
    rounds: dict[str, list[datetime]] = {}
    for line in lines:
        if line.round is not None:
            rounds.setdefault(line.round, []).append(line.at)
    out: dict[str, float] = {}
    for round_, times in rounds.items():
        begun = started(round_)
        if begun is None:
            continue
        spent = sum((min(b - a, GAP) for a, b in pairwise(sorted([begun, *times]))), timedelta())
        year, week, _ = begun.isocalendar()
        key = f"{year}-W{week:02d}"
        out[key] = out.get(key, 0.0) + spent.total_seconds() / 60
    return dict(sorted(out.items(), reverse=True))


def last_round(lines: Sequence[Line], also: datetime | None = None) -> datetime | None:
    """When the latest round on any machine started: the log's rounds, and `also`, this machine's open one."""
    begun = [started(line.round) for line in lines if line.round is not None]
    found = [b for b in begun if b is not None] + ([also] if also is not None else [])
    return max(found, default=None)
