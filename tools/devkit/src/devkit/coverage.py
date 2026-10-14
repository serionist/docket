"""The coverage floors: each package's line coverage may rise, never fall.

The floors are in `tools/devkit/coverage-floors.json`, a whole percentage per top-level module. A change that raises a
package's coverage past its floor raises the floor in the same change; nothing ever lowers one to pass.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FLOORS = "tools/devkit/coverage-floors.json"


@dataclass(frozen=True)
class Measure:
    module: str
    covered: int
    statements: int

    @property
    def percent(self) -> float:
        return 100.0 if self.statements == 0 else 100.0 * self.covered / self.statements


def module_of(path: str) -> str | None:
    """The top-level module of a measured file: `packages/<p>/src/<module>/...` or `tools/<t>/src/<module>/..."""
    parts = Path(path).as_posix().split("/")
    if "src" in parts:
        i = parts.index("src")
        if i + 1 < len(parts) - 1:
            return parts[i + 1]
    return None


def measure(report: dict[str, Any]) -> dict[str, Measure]:
    """Per-module totals from coverage.py's JSON report."""
    totals: dict[str, list[int]] = {}
    files: dict[str, Any] = report.get("files") or {}
    for path, data in files.items():
        module = module_of(path)
        if module is None:
            continue
        summary: dict[str, Any] = data.get("summary") or {}
        t = totals.setdefault(module, [0, 0])
        t[0] += int(summary.get("covered_lines", 0))
        t[1] += int(summary.get("num_statements", 0))
    return {m: Measure(m, c, s) for m, (c, s) in totals.items()}


def judge(measures: dict[str, Measure], floors: dict[str, int]) -> tuple[list[str], list[str]]:
    """(failures, floors that can rise)."""
    failures: list[str] = []
    rise: list[str] = []
    for module, m in sorted(measures.items()):
        floor = floors.get(module)
        if floor is None:
            failures.append(f"{module}: {m.percent:.1f}% and no floor; add it to {FLOORS}")
            continue
        if m.percent + 1e-9 < floor:
            failures.append(f"{module}: {m.percent:.1f}% is below its floor of {floor}%")
        elif int(m.percent) > floor:
            rise.append(f"{module}: {m.percent:.1f}%, floor {floor}%: raise it to {int(m.percent)}")
    for module in sorted(set(floors) - set(measures)):
        failures.append(f"{module}: has a floor but nothing was measured")
    return failures, rise


def load_floors(root: Path) -> dict[str, int]:
    data: Any = json.loads((root / FLOORS).read_text(encoding="utf-8"))
    return {str(k): int(v) for k, v in data.items() if not str(k).startswith("$")}
