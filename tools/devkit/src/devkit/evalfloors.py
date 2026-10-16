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
# The same set with the embedder (DR-0339), measured by the models lane against floors of its own. A number above its
# floor fails there only once its file says `"$above": "fail"`, which it says when three CI runs gave the same numbers;
# until then the line says which floor to raise.
FLOORS_MODELS = "tools/devkit/eval-floors-models.json"
REPORT_MODELS = ".local/eval-models.json"
REPORT_MODELS_ENV = "DOCKET_EVAL_MODELS_REPORT"
# And with the CPU reranker on its 30 pairs, with no time budget (DR-0458), under the same rules.
FLOORS_RERANK = "tools/devkit/eval-floors-rerank.json"
REPORT_RERANK = ".local/eval-rerank.json"
REPORT_RERANK_ENV = "DOCKET_EVAL_RERANK_REPORT"
ABOVE = "$above"
MEASURES = ("recall", "mrr")

Floors = dict[str, dict[str, Any]]


def floored(value: float) -> float:
    """A number rounded down to two decimals, as the floors keep it."""
    return math.floor(value * 100 + 1e-9) / 100


def parse(text: str) -> Floors:
    """The floors per kind from the file's text, its `$comment` left out."""
    data: dict[str, Any] = json.loads(text)
    return {str(k): dict(v) for k, v in data.items() if not str(k).startswith("$")}


def load_floors(root: Path, file: str = FLOORS) -> Floors:
    return parse((root / file).read_text(encoding="utf-8"))


def above_fails(root: Path, file: str) -> bool:
    """Whether a number above its floor fails: always for the keyword floors, and for others once they say so."""
    data: dict[str, Any] = json.loads((root / file).read_text(encoding="utf-8"))
    return data.get(ABOVE, "fail") == "fail"


def base_floors(root: Path, base: str | None, file: str = FLOORS) -> Floors | None:
    """The floors as the base commit has them, or None without a base or before the file existed."""
    if base is None:
        return None
    text = git(root, "show", f"{base}:{file}", check=False)
    return parse(text) if text.strip() else None


def judge(
    measured: dict[str, dict[str, float]],
    floors: Floors,
    base: Floors | None,
    *,
    file: str = FLOORS,
    fail_above: bool = True,
) -> tuple[list[str], str]:
    """(failures, the line of numbers per kind)."""
    failures: list[str] = []
    raise_to: list[str] = []
    for kind in [*measured, *(k for k in floors if k not in measured)]:
        if kind not in floors:
            failures.append(f"eval {kind}: measured but no floor; add it to {file}")
            continue
        if kind not in measured:
            failures.append(f"eval {kind}: has a floor but nothing was measured")
            continue
        for measure in MEASURES:
            value, floor = floored(measured[kind][measure]), float(floors[kind][measure])
            if value < floor:
                failures.append(f"eval {kind} {measure}: {value:.2f} is below its floor of {floor:.2f}")
            elif value > floor:
                above = f"eval {kind} {measure}: {value:.2f}, floor {floor:.2f}: raise it to {value:.2f}"
                (failures if fail_above else raise_to).append(above)
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
    line = f"eval recall@10/MRR: {numbers}"
    if raise_to:
        line += f"; above the floor, to raise once three runs agree: {'; '.join(raise_to)}"
    return failures, line


def check(root: Path, base: str | None, *, models: bool = False, rerank: bool = False) -> tuple[list[str], str]:
    """The numbers the question set's test wrote, held against the floors and the base's: (failures, a line). With
    models, the numbers and floors of the set run with the embedder; with rerank, with the CPU reranker too."""
    file, report = (FLOORS_MODELS, REPORT_MODELS) if models else (FLOORS, REPORT)
    if rerank:
        file, report = FLOORS_RERANK, REPORT_RERANK
    numbers = root / report
    if not numbers.is_file():
        return [f"eval: the question set's test wrote no numbers to {report}"], "eval: not measured"
    measured: dict[str, dict[str, float]] = json.loads(numbers.read_text(encoding="utf-8"))
    return judge(
        measured,
        load_floors(root, file),
        base_floors(root, base, file),
        file=file,
        fail_above=above_fails(root, file),
    )
