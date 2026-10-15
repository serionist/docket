"""The eval floors: per kind of question, the recall at 10 and MRR the invented question set scores on main (DR-0295).

The floors are in `tools/devkit/eval-floors.json`, rounded down to two decimals. The set's test fails a kind below its
floor on every system. In the full lane the gate hands the test a file to write its numbers to and holds them here: a
number above its floor fails until the change that earned it raises the floor, and a floor lower than the base
branch's fails unless its kind carries a `lowered` note the base's didn't.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from devkit.repo import git

FLOORS = "tools/devkit/eval-floors.json"
REPORT = ".local/eval.json"
REPORT_ENV = "DOCKET_EVAL_REPORT"
MEASURES = ("recall", "mrr")

Floors = dict[str, dict[str, Any]]


def floored(value: float) -> float:
    """A number rounded down to two decimals, as the floors keep it."""
    return math.floor(value * 100 + 1e-9) / 100


def parse(text: str) -> Floors:
    """The floors per kind from the file's text, its `$comment` left out."""
    data: dict[str, Any] = json.loads(text)
    return {str(k): dict(v) for k, v in data.items() if not str(k).startswith("$")}


def load_floors(root: Path) -> Floors:
    return parse((root / FLOORS).read_text(encoding="utf-8"))


def base_floors(root: Path, base: str | None) -> Floors | None:
    """The floors as the base commit has them, or None without a base or before the file existed."""
    if base is None:
        return None
    text = git(root, "show", f"{base}:{FLOORS}", check=False)
    return parse(text) if text.strip() else None


def judge(measured: dict[str, dict[str, float]], floors: Floors, base: Floors | None) -> tuple[list[str], str]:
    """(failures, the line of numbers per kind)."""
    failures: list[str] = []
    for kind in [*measured, *(k for k in floors if k not in measured)]:
        if kind not in floors:
            failures.append(f"eval {kind}: measured but no floor; add it to {FLOORS}")
            continue
        if kind not in measured:
            failures.append(f"eval {kind}: has a floor but nothing was measured")
            continue
        for measure in MEASURES:
            value, floor = floored(measured[kind][measure]), float(floors[kind][measure])
            if value < floor:
                failures.append(f"eval {kind} {measure}: {value:.2f} is below its floor of {floor:.2f}")
            elif value > floor:
                failures.append(f"eval {kind} {measure}: {value:.2f}, floor {floor:.2f}: raise it to {value:.2f}")
    for kind, floor in floors.items():
        old = (base or {}).get(kind)
        if old is None:
            continue
        lowered = [m for m in MEASURES if float(floor[m]) < float(old[m])]
        note = floor.get("lowered")
        if lowered and (not note or note == old.get("lowered")):
            failures.append(
                f"eval {kind}: {' and '.join(lowered)} lowered from the base; say why in a new lowered note on it"
            )
    numbers = ", ".join(f"{k} {floored(v['recall']):.2f}/{floored(v['mrr']):.2f}" for k, v in measured.items())
    return failures, f"eval recall@10/MRR: {numbers}"


def check(root: Path, base: str | None) -> tuple[list[str], str]:
    """The numbers the question set's test wrote, held against the floors and the base's: (failures, a line)."""
    numbers = root / REPORT
    if not numbers.is_file():
        return [f"eval: the question set's test wrote no numbers to {REPORT}"], "eval: not measured"
    measured: dict[str, dict[str, float]] = json.loads(numbers.read_text(encoding="utf-8"))
    return judge(measured, load_floors(root), base_floors(root, base))
