"""The coverage floors: each package's line coverage may rise, and only the owner lowers a floor (DR-0755).

The floors are in `tools/devkit/coverage-floors.json`, a whole percentage per top-level module. A change that raises a
package's coverage past its floor raises the floor in the same change. A floor below the merge base's fails unless the
file's `$lowered` names, for its module, a decided decision that sets the new number: the citation sits in the diff
beside the number, so the gate reads it the same way on a laptop, in a PR's run and on main after the squash.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from devkit.regime import Regime
from devkit.repo import KitError, git
from devkit.store import Store

FLOORS = "tools/devkit/coverage-floors.json"
# The decision that lowered each module's floor below the merge base's, by its id (DR-0755).
LOWERED = "$lowered"


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


def below(measures: dict[str, Measure], floors: dict[str, int]) -> set[str]:
    """The modules under their floors."""
    return {module for module, m in measures.items() if module in floors and m.percent + 1e-9 < floors[module]}


def missing(report: dict[str, Any], modules: set[str]) -> list[str]:
    """Each file of these modules with lines no test ran, and the lines: what a floor's failure points at, on a system
    whose run can't be repeated here."""
    files: dict[str, Any] = report.get("files") or {}
    out: list[str] = []
    for path, data in sorted(files.items()):
        numbers: list[Any] = data.get("missing_lines") or []
        lines = [str(n) for n in numbers]
        if lines and module_of(path) in modules:
            out.append(f"  {Path(path).as_posix()}: line {', '.join(lines)}")
    return out


def parse(text: str) -> tuple[dict[str, int], dict[str, str]]:
    """(the floors per module, the decision `$lowered` names per module) from the file's text."""
    data: Any = json.loads(text)
    floors = {str(k): int(v) for k, v in data.items() if not str(k).startswith("$")}
    named: Any = data.get(LOWERED) or {}
    return floors, {str(k): str(v) for k, v in named.items()}


def load_floors(root: Path) -> dict[str, int]:
    return parse((root / FLOORS).read_text(encoding="utf-8"))[0]


def base_floors(root: Path, base: str) -> dict[str, int] | None:
    """The floors as the merge base has them, or None before the file existed. A base this clone can't read fails:
    a shallow checkout would otherwise pass every lowered floor."""
    if not git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}", check=False).strip():
        raise KitError(f"can't read the merge base {base}, whose floors these are held against: fetch its history")
    if not git(root, "ls-tree", "--name-only", base, "--", FLOORS, check=False).strip():
        return None
    return parse(git(root, "show", f"{base}:{FLOORS}"))[0]


def sets(entry: Mapping[str, Any], module: str, floor: int) -> bool:
    """Whether a decision's text names the module and the floor as a percentage, as `devkit at 85%`."""
    binds: list[Any] = list(entry.get("binds") or [])
    text = " ".join([str(entry.get("title") or ""), str(entry.get("description") or ""), *map(str, binds)])
    named = re.search(rf"\b{re.escape(module)}\b", text) is not None
    return named and re.search(rf"(?<![\d.]){floor}%", text) is not None


def guard(
    floors: dict[str, int],
    base: dict[str, int] | None,
    named: dict[str, str],
    decisions: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    """A failure for each floor below the base's whose module `$lowered` doesn't name a decided decision that sets
    the new number: only the owner decides one, so only the owner lowers a floor."""
    out: list[str] = []
    for module, floor in sorted(floors.items()):
        old = (base or {}).get(module)
        if old is None or floor >= old:
            continue
        lowered = f"{module}: floor lowered from {old}% to {floor}%"
        ident = named.get(module)
        entry = decisions.get(ident) if ident else None
        if ident is None:
            out.append(f"{lowered}; name the decided decision that sets it under {LOWERED} in {FLOORS} (DR-0755)")
        elif entry is None:
            out.append(f"{lowered}, and {ident}, which {LOWERED} names, isn't a decision")
        elif entry.get("status") != "decided":
            out.append(f"{lowered}, and {ident} is {entry.get('status')}, not decided: only the owner lowers a floor")
        elif not sets(entry, module, floor):
            out.append(f"{lowered}, and {ident} doesn't say {module} at {floor}%")
    return out


def check_lowered(root: Path, base: str | None) -> list[str]:
    """The floors held against the merge base's (DR-0755): none to hold without a base, as on main itself."""
    if base is None:
        return []
    floors, named = parse((root / FLOORS).read_text(encoding="utf-8"))
    old = base_floors(root, base)
    if old is None or all(floors.get(m, f) >= f for m, f in old.items()):
        return []
    decisions = {loc.key: loc.entry for loc in Store.load(Regime.load(root)).entries("decisions")}
    return guard(floors, old, named, decisions)
