"""A pytest plugin the gate loads for `--shard K/N`: the run keeps one share of the test files, so the shares of one CI
job's tests run on runners of their own (DR-0754).

The files are split by what each cost on this system in a CI run, from `tools/devkit/test-weights.json`: the heaviest
first, each to the share with the least so far. A file the weights don't know counts as their median, and with no
weights for the system every file counts the same, which deals them out in path order, one share after the next. A
file stays whole in one share: its module fixtures, and a timed test's vault that an untimed test of the same file
builds in the parallel run (DR-0492), never cross to another runner. The share is taken before any other filter, so the
parallel run and the timed run of one share keep the same files.

With `--select system` the run keeps only the test files tools/devkit/test-systems.json lists under system, and every
test marked smoke wherever it lives: what Windows and macOS run on every PR (DR-0754). The selection comes before the
share, so the shares split the selected files.

With `--shard-times=<file>` the run writes what each of its files cost, its tests' time over the run's workers, and
the tests that failed; `gate weights` folds a CI run's files into the weights. Each system's files have weights of
their own, under its name; `--shard-key` names another key, as the browser shares' "Linux browser" (DR-0712), whose
files cost what a browser costs, not what they cost skipped.
"""

from __future__ import annotations

import json
import platform
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, cast

import pytest

from devkit import systems
from devkit.repo import KitError

_SPEC = re.compile(r"^([1-9]\d*)/([1-9]\d*)$")
WEIGHTS = "tools/devkit/test-weights.json"
COMMENT = (
    "What each test file cost a CI share's run, per system, in seconds: its tests' time over the run's workers, plus "
    "its timed tests' time alone. The shard plugin splits the files by it (tools/devkit/src/devkit/shard.py); `gate "
    "weights <folder>` writes it from a CI run's times artifacts."
)


def parse(spec: str) -> tuple[int, int]:
    """`K/N` as (K, N): share K of N, 1 <= K <= N."""
    found = _SPEC.match(spec)
    if found is None or not 1 <= int(found.group(1)) <= int(found.group(2)):
        raise ValueError(f"--shard takes K/N with 1 <= K <= N, such as 1/2, not {spec!r}")
    return int(found.group(1)), int(found.group(2))


def share(files: set[str], k: int, n: int, weights: dict[str, float] | None = None) -> set[str]:
    """The files of share K of N: the heaviest first, each to the share with the least weight so far, the first such
    share on a tie. Without weights every file weighs the same, so they go out in path order, one share after the
    next."""
    known = [w for f, w in (weights or {}).items() if f in files]
    median = statistics.median(known) if known else 1.0
    weight = {f: (weights or {}).get(f, median) for f in files}
    loads = [0.0] * n
    mine: set[str] = set()
    for f in sorted(files, key=lambda f: (-weight[f], f)):
        least = loads.index(min(loads))
        loads[least] += weight[f]
        if least == k - 1:
            mine.add(f)
    return mine


def load_weights(path: Path, system: str | None = None) -> dict[str, float]:
    """This system's weights from the file; none when the file has none for it."""
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    found: dict[str, Any] = data.get(system or platform.system()) or {}
    return {str(f): float(w) for f, w in found.items()}


class Times:
    """What each file's tests took in this run, and the tests that failed, written at its end. Under xdist the
    controller gets every worker's reports, so it alone writes, and divides by its workers: what the file cost the
    run's wall time. A red nightly names its set of failing tests from these (DR-0754)."""

    def __init__(self, config: pytest.Config, out: Path) -> None:
        self.config = config
        self.out = out
        self.seconds: dict[str, float] = defaultdict(float)
        self.failed: set[str] = set()

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.seconds[report.nodeid.split("::")[0]] += report.duration
        if report.failed:
            self.failed.add(report.nodeid)

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        # A file that doesn't import fails here, before it has a test to fail; its id is the file's path.
        if report.failed:
            self.failed.add(report.nodeid)

    def pytest_sessionfinish(self) -> None:
        workers = int(self.config.getoption("numprocesses", None) or 1)
        record = {
            "system": key_of(self.config),
            "share": self.config.getoption("--shard"),
            "marks": self.config.option.markexpr,
            "workers": workers,
            "files": {f: round(s / workers, 2) for f, s in sorted(self.seconds.items())},
            "failed": sorted(self.failed),
        }
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self.out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--shard", default=None, metavar="K/N", help="run share K of N of the test files (DR-0754)")
    parser.addoption(
        "--select",
        default=None,
        choices=["system"],
        help="keep only the test files test-systems.json lists under system, and the tests marked smoke (DR-0754)",
    )
    parser.addoption("--shard-weights", default=None, metavar="FILE", help="split the files by these weights")
    parser.addoption("--shard-times", default=None, metavar="FILE", help="write what each file cost this run")
    parser.addoption("--shard-key", default=None, metavar="KEY", help="the weights' key: the system's name by default")


def key_of(config: pytest.Config) -> str:
    """The key this run reads its weights under and writes its times under."""
    return str(config.getoption("--shard-key") or platform.system())


def pytest_configure(config: pytest.Config) -> None:
    spec = config.getoption("--shard")
    if spec is not None:
        try:
            parse(spec)
        except ValueError as e:
            raise pytest.UsageError(str(e)) from e
    out = config.getoption("--shard-times")
    if out is not None and not hasattr(config, "workerinput"):
        config.pluginmanager.register(Times(config, Path(out)), "devkit-shard-times")


def selected(items: list[pytest.Item], root: Path, listed: systems.Systems) -> list[pytest.Item]:
    """The tests in a file listed under system, and every test marked smoke."""
    return [
        item
        for item in items
        if item.path.relative_to(root).as_posix() in listed.system or item.get_closest_marker(systems.SMOKE)
    ]


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    spec = config.getoption("--shard")
    select = config.getoption("--select")
    if spec is None and select is None:
        return
    kept = items
    if select is not None:
        try:
            listed = systems.load(config.rootpath)
        except KitError as e:
            raise pytest.UsageError(str(e)) from e
        kept = selected(items, config.rootpath, listed)
    if spec is not None:
        k, n = parse(spec)
        named = config.getoption("--shard-weights")
        files = {item.path.relative_to(config.rootpath).as_posix() for item in kept}
        mine = share(files, k, n, load_weights(Path(named), key_of(config)) if named else None)
        kept = [item for item in kept if item.path.relative_to(config.rootpath).as_posix() in mine]
    held = {id(item) for item in kept}
    # Always, even with nothing to leave out: `gate full`'s one share runs the same lines as each of CI's shares, so
    # both read the same coverage of this file.
    config.hook.pytest_deselected(items=[item for item in items if id(item) not in held])
    items[:] = kept


def merge_times(folder: Path, weights_path: Path, root: Path) -> list[str]:
    """Each system's weights updated from one CI run's times files under the folder, the runs of each share summed per
    file. A file the run has no time for keeps its weight, so a share that failed or wasn't downloaded leaves its files
    as they were; a file no longer under the root is dropped. A line per system written."""
    found: dict[str, dict[str, float]] = {}
    seen: set[tuple[str, str, str]] = set()
    for path in sorted(folder.rglob("*.json")):
        parsed: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            continue
        record = cast(dict[str, Any], parsed)
        if "files" not in record or "system" not in record:
            continue
        key = (str(record["system"]), str(record.get("share")), str(record.get("marks")))
        if key in seen:
            raise KitError(f"the times of share {key[1]} on {key[0]} are there twice: give the times of one CI run")
        seen.add(key)
        per = found.setdefault(key[0], defaultdict(float))
        files: dict[str, Any] = record["files"]
        for f, s in files.items():
            per[f] += float(s)
    if not found:
        raise KitError(f"no times files under {folder}: download a CI run's times artifacts there")
    data: dict[str, Any] = (
        json.loads(weights_path.read_text(encoding="utf-8")) if weights_path.is_file() else {"$comment": COMMENT}
    )
    lines: list[str] = []
    for system, per in sorted(found.items()):
        before: dict[str, Any] = data.get(system) or {}
        kept = {f: float(w) for f, w in before.items() if f not in per and (root / f).is_file()}
        data[system] = {f: round(s, 1) for f, s in sorted({**kept, **per}.items())}
        lines.append(f"{system}: {len(per)} files timed, {sum(per.values()):.0f} s; {len(kept)} kept as they were")
    ordered = {k: data[k] for k in sorted(data, key=lambda k: (not k.startswith("$"), k))}
    weights_path.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8", newline="\n")
    return lines
