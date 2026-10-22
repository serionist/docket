"""A pytest plugin the gate loads for `--shard K/N`: the run keeps one share of the test files, so the shares of one CI
job's tests run on runners of their own (DR-0754).

The run's files are those tools/devkit/test-systems.json names under its arguments, split by what each cost on this
system in a CI run, from `tools/devkit/test-weights.json`: the heaviest first, each to the share with the least so far.
A file the weights don't know counts as their median, and with no weights for the system every file counts the same,
which deals them out in path order, one share after the next. A file stays whole in one share: its module fixtures, and
a timed test's vault that an untimed test of the same file builds in the parallel run (DR-0492), never cross to another
runner. The split is worked out from the list before anything is collected, so the parallel run and the timed run of
one share keep the same files, and a share imports only its own: the others are never collected. A test file the run
collects that the list names nowhere fails its collection, in every share, since no share would hold it (DR-0783).

With `--leave-out nightly` the tests marked nightly leave the run, and a file whose every test is, by its module's own
`pytestmark = pytest.mark.nightly`, leaves it before the split, so the shares balance on what a PR runs (DR-0757).

With `--select system` the run keeps only the test files tools/devkit/test-systems.json lists under system, and every
test marked smoke or perf wherever it lives: what Windows and macOS run on every PR (DR-0754), the small timed tests
among them. A file under same is collected for those only when its text shows `pytest.mark.smoke` or
`pytest.mark.perf`, so the others are never imported; a file under same holding a test marked either way that its text
doesn't show fails its collection in every run that collects it, and Linux collects every file on every PR. The
selection comes before the share, so the shares split the selected files.

With `--shard-timed=<file>` the run writes the tests of its files that its timed run takes, those marked perf, so the
gate skips a timed run with none. With `--shard-times=<file>` the run writes what each of its files cost, its tests'
time over the run's workers, 0 for a file of the share that ran nothing, and the tests that failed; `gate weights` folds
a CI run's files into the weights. Each system's files have weights of their own, under its name; `--shard-key` names
another key, as the browser shares' "Linux browser" (DR-0712), whose files cost what a browser costs, not what they
cost skipped.
"""

from __future__ import annotations

import ast
import json
import os
import platform
import re
import statistics
from collections import defaultdict
from collections.abc import Generator
from dataclasses import dataclass, field
from fnmatch import fnmatch
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
PERF = "perf"
# What --leave-out takes: the marker of the timed tests on big vaults and of the tests that build them (DR-0757).
LEAVE_OUT = ("nightly",)
# How a file's text shows a test the selection keeps wherever it lives: pytest.mark.smoke, pytest.mark.perf.
_EVERY_SYSTEM_MARK = re.compile(rf"\bmark\.({'|'.join(systems.EVERY_SYSTEM)})\b")


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


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def marks_every_test(text: str, mark: str) -> bool:
    """Whether a module marks its every test with pytest.mark.<mark>: its one top-level `pytestmark = ...` names it,
    alone or in a list, and nothing else in the module names pytestmark, so pytest gives the mark to every test the
    module holds."""
    if f"mark.{mark}" not in text or "pytestmark" not in text:
        return False
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    if sum(isinstance(n, ast.Name) and n.id == "pytestmark" for n in ast.walk(tree)) != 1:
        return False
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and [ast.unparse(t) for t in stmt.targets] == ["pytestmark"]:
            value = stmt.value
            found = value.elts if isinstance(value, ast.List | ast.Tuple) else [value]
            return any(ast.unparse(e) == f"pytest.mark.{mark}" for e in found)
    return False


@dataclass(frozen=True)
class Plan:
    """A run's test files, worked out before anything is collected."""

    listed: systems.Systems
    # Every test file test-systems.json names.
    named: frozenset[str]
    # Those under the run's arguments that it runs, less those --leave-out or --select leave out whole.
    run: frozenset[str]
    # Those of its share: all of run when it isn't split.
    mine: frozenset[str]
    # Whether a file's text shows pytest.mark.smoke or pytest.mark.perf, as the run reads them.
    shown: dict[str, bool] = field(default_factory=dict[str, bool])


_PLAN = pytest.StashKey[Plan | None]()


def _arguments(config: pytest.Config) -> frozenset[Path]:
    """The paths the run collects from, resolved as pytest resolves them: against the folder it was started in."""
    base = config.invocation_params.dir
    # abspath as pytest's own, not resolve(), which follows links where pytest's paths don't (macOS's /var).
    return frozenset(Path(os.path.abspath(base / arg.split("::")[0])) for arg in config.args)  # noqa: PTH100


def _under(path: Path, at: frozenset[Path]) -> bool:
    return path in at or any(p in at for p in path.parents)


def plan(config: pytest.Config) -> Plan | None:
    """The run's files, once per process; None when the run takes none of --shard, --select and --leave-out."""
    if _PLAN not in config.stash:
        config.stash[_PLAN] = _plan(config)
    return config.stash[_PLAN]


def _plan(config: pytest.Config) -> Plan | None:
    spec = config.getoption("--shard")
    select = config.getoption("--select")
    leave = config.getoption("--leave-out")
    if spec is None and select is None and leave is None:
        return None
    root = config.rootpath
    try:
        listed = systems.load(root)
    except KitError as e:
        raise pytest.UsageError(str(e)) from e
    named = frozenset(listed.system) | frozenset(listed.same)
    at = _arguments(config)
    files = {f for f in named if _under(root / f, at)}
    if leave is not None:
        files = {f for f in files if not marks_every_test(_text(root / f), leave)}
    if select is not None:
        files = {f for f in files if f in listed.system or _EVERY_SYSTEM_MARK.search(_text(root / f))}
    mine = files
    if spec is not None:
        k, n = parse(spec)
        weights = config.getoption("--shard-weights")
        mine = share(files, k, n, load_weights(Path(weights), key_of(config)) if weights else None)
    return Plan(listed, named, frozenset(files), frozenset(mine))


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
        # Every file of the share, 0 where none of its tests ran, so `gate weights` keeps no weight a run that ran
        # more of the file left, as one whose every test the nightly takes.
        found = plan(self.config)
        files = dict.fromkeys(found.mine if found else (), 0.0)
        files.update({f: s / workers for f, s in self.seconds.items()})
        record = {
            "system": key_of(self.config),
            "share": self.config.getoption("--shard"),
            "marks": self.config.option.markexpr,
            "workers": workers,
            "files": {f: round(s, 2) for f, s in sorted(files.items())},
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
        help="keep only the test files test-systems.json lists under system, and the tests marked smoke or perf",
    )
    parser.addoption(
        "--leave-out",
        default=None,
        choices=list(LEAVE_OUT),
        help="leave out the tests marked nightly, and before the split the files whose every test is (DR-0757)",
    )
    parser.addoption("--shard-weights", default=None, metavar="FILE", help="split the files by these weights")
    parser.addoption("--shard-times", default=None, metavar="FILE", help="write what each file cost this run")
    parser.addoption("--shard-timed", default=None, metavar="FILE", help="write the run's tests marked perf")
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


def _relative(path: Path, root: Path) -> str:
    """A path as test-systems.json names it; one outside the root as it is, which the list can't name."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _unnamed(rel: str) -> str:
    return f"{rel}: {systems.SYSTEMS} doesn't name it, so no share holds it: name it there"


# Last, so pytest's own ignores (--ignore, collect_ignore, a virtualenv) come first.
@pytest.hookimpl(trylast=True)
def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    """Another share's test files, and those the run leaves out whole, are never collected, so a share imports only
    its own. pytest asks this of every file it finds in a folder it collects, never of a file it was given by name. A
    file the list doesn't name is collected, for its collection to fail (pytest_make_collect_report)."""
    found = plan(config)
    if found is None or not _test_file(collection_path, config):
        return None
    rel = _relative(collection_path, config.rootpath)
    return True if rel in found.named and rel not in found.mine else None


def _test_file(path: Path, config: pytest.Config) -> bool:
    return any(fnmatch(path.name, p) for p in config.getini("python_files"))


def collect_problems(path: Path, config: pytest.Config, nodes: list[Any], found: Plan) -> list[str]:
    """Why the collection of a test file, or of a class in one, fails: the list doesn't name the file, so no share
    would hold it; or the file is under same and holds a test marked smoke or perf that its text doesn't show, so
    --select would never collect it on Windows and macOS. Linux's shares between them collect every file under same
    that isn't the nightly's whole, each share its own, so a PR's run there finds every such test."""
    rel = _relative(path, config.rootpath)
    if _test_file(path, config) and rel not in found.named:
        return [_unnamed(rel)]
    if rel not in found.listed.same:
        return []
    out: list[str] = []
    for node in nodes:
        marked = [m for m in systems.EVERY_SYSTEM if node.get_closest_marker(m)]
        if not marked:
            continue
        if rel not in found.shown:
            found.shown[rel] = _EVERY_SYSTEM_MARK.search(_text(path)) is not None
        if not found.shown[rel]:
            out.append(
                f"{node.nodeid} is marked {marked[0]}, but its file's text shows no pytest.mark.{marked[0]}, which is "
                "how Windows and macOS find the files under same that hold one: mark it in the file itself"
            )
    return out


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(
    collector: pytest.Collector,
) -> Generator[None, pytest.CollectReport, pytest.CollectReport]:
    """A file's collection fails on collect_problems: as a collection error a run on several workers shows it, where
    an error raised once collection is done shows only as a worker's crash."""
    report = yield
    found = plan(collector.config)
    if found is not None and report.passed:
        problems = collect_problems(collector.path, collector.config, list(report.result), found)
        if problems:
            report.outcome = "failed"
            report.longrepr = "\n".join(problems)
            report.result = []
    return report


def selected(items: list[pytest.Item], root: Path, listed: systems.Systems) -> list[pytest.Item]:
    """The tests in a file listed under system, and every test marked smoke or perf: a timed test runs on every
    system whatever file holds it, as a system's speed is the system's own (DR-0757)."""
    return [
        item
        for item in items
        if item.path.relative_to(root).as_posix() in listed.system
        or any(item.get_closest_marker(mark) for mark in systems.EVERY_SYSTEM)
    ]


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    found = plan(config)
    if found is not None:
        root = config.rootpath
        kept = items
        leave = config.getoption("--leave-out")
        if leave is not None:
            kept = [item for item in kept if item.get_closest_marker(leave) is None]
        if config.getoption("--select") is not None:
            kept = selected(kept, root, found.listed)
        # Its share's tests, of a file given by name too, which pytest collects without asking.
        kept = [item for item in kept if _relative(item.path, root) in found.mine]
        held = {id(item) for item in kept}
        # Always, even with nothing to leave out: `gate full`'s one share runs the same lines as each of CI's shares,
        # so both read the same coverage of this file.
        config.hook.pytest_deselected(items=[item for item in items if id(item) not in held])
        items[:] = kept
    timed = config.getoption("--shard-timed")
    # Every worker collects the same tests, so the first writes them, or the run itself when it has no workers.
    workerinput: dict[str, Any] = getattr(config, "workerinput", {"workerid": "gw0"})
    if timed is not None and workerinput.get("workerid") == "gw0":
        out = Path(timed)
        out.parent.mkdir(parents=True, exist_ok=True)
        listed = [item.nodeid for item in items if item.get_closest_marker(PERF)]
        out.write_text(json.dumps(listed, indent=2) + "\n", encoding="utf-8", newline="\n")


def timed_tests(path: Path) -> list[str] | None:
    """The tests marked perf that a share's parallel run found in its files, from `--shard-timed`; None when it wrote
    no list."""
    try:
        parsed: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return [str(t) for t in cast(list[Any], parsed)] if isinstance(parsed, list) else None


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
