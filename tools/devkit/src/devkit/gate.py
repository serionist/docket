"""`uv run gate`: the one gate. What passes here is the merge rule.

Lanes:
  quick  the static checks, in seconds: lock file, lint, format, types, docs, commit messages, private names, secrets,
         the harness (AGENTS.md, skills, plugin manifests), the web app's build and API document. The pre-push hook
         runs it.
  full   quick, then the tests with coverage and its floors, the secret scan over the whole history, and the plugin
         validated by Claude Code itself. finish-work runs it before a PR. CI runs it in parts (DR-0754): `--skip
         tests` once, and `--only tests --shard K/N` on each share, which saves its coverage data instead of holding
         the floors.
  os     types and tests, for the other operating systems CI runs: the test files that can differ by system and the
         smoke set (DR-0754), and with `--all` every test file, as the nightly runs them; `--shard K/N` runs share K
         of N of those files, so one system's tests run on several runners.
  coverage  every share's coverage data combined and held to the floors, with the question set's numbers: the
         full lane's floors, for a CI run whose shares ran on runners of their own.

In a lane with tests, the tests start first and the other steps run beside them; each run prints its slowest tests. In a
lane without coverage (os) the timed tests (marked `perf`) run after the rest, alone and one at a time, once the other
steps are done, and the run prints what they printed: their times. The two runs share one templates folder, so the
timed run reuses the vaults the parallel run built, and the timed run has a temp folder of its own. Under coverage the
index's time limits are off, so the full lane keeps them in its one run. A share writes what each of its files cost to
`.local/times/`, and `gate weights <folder>` folds a CI run's into the weights the shares split by. On GitHub Actions a
tests step past 4.5 minutes prints a warning the PR shows.

`gate check-text <file>` checks a text that is about to be posted (an issue or PR body) for private names and tool
attribution.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from devkit import commits, content, coverage, evalfloors, pins, systems
from devkit.regime import Regime
from devkit.repo import KitError, default_base, listed_files, repo_root, utf8_stdio
from devkit.store import Store

AGENTS_MAX_LINES = 150
LANES = ("quick", "full", "os", "models", "coverage")
MODELS_ENV = "DOCKET_TEST_MODELS"
MODELS_TEST = "packages/docket/tests/test_eval_set.py"


@dataclass
class Outcome:
    status: str  # ok, fail, skip
    detail: list[str] = field(default_factory=list[str])

    @staticmethod
    def ok(*detail: str) -> Outcome:
        return Outcome("ok", list(detail))

    @staticmethod
    def fail(*detail: str) -> Outcome:
        return Outcome("fail", list(detail))

    @staticmethod
    def skip(reason: str) -> Outcome:
        return Outcome("skip", [reason])


@dataclass
class Ctx:
    root: Path
    lane: str
    ci: bool
    base: str | None
    pr: commits.PullRequest | None
    # Set once the lane's other steps are done; the timed tests wait for it. Set from the start for a step run alone.
    alone: threading.Event = field(default_factory=lambda: _set(threading.Event()))
    # K/N: the tests step runs share K of N of the test files (DR-0754); None runs them all.
    shard: str | None = None
    # The os lane's tests: every test file, not only those that can differ by system and the smoke set (DR-0754).
    whole: bool = False


def _set(event: threading.Event) -> threading.Event:
    event.set()
    return event


@dataclass
class Step:
    name: str
    lanes: tuple[str, ...]
    about: str
    run: Callable[[Ctx], Outcome]


def _run(ctx: Ctx, cmd: list[str], *, env: dict[str, str] | None = None) -> tuple[int, list[str]]:
    proc = subprocess.run(
        cmd,
        cwd=ctx.root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env={**os.environ, **(env or {})},
    )
    lines = (proc.stdout + proc.stderr).rstrip().split("\n")
    return proc.returncode, [ln for ln in lines if ln.strip()]


def _tool(ctx: Ctx, cmd: list[str]) -> Outcome:
    code, lines = _run(ctx, cmd)
    return Outcome.ok() if code == 0 else Outcome.fail(*lines[-60:])


# -- steps ---------------------------------------------------------------------------------------------------------


def step_lock(ctx: Ctx) -> Outcome:
    uv = shutil.which("uv")
    if uv is None:
        return Outcome.fail("uv isn't on PATH")
    found = pins.problems(ctx.root)
    if found:
        return Outcome.fail(*found)
    return _tool(ctx, [uv, "lock", "--check"])


def step_ruff(ctx: Ctx) -> Outcome:
    return _tool(ctx, [sys.executable, "-m", "ruff", "check", "--no-cache", "."])


def step_format(ctx: Ctx) -> Outcome:
    return _tool(ctx, [sys.executable, "-m", "ruff", "format", "--check", "--no-cache", "."])


def step_types(ctx: Ctx) -> Outcome:
    return _tool(ctx, [sys.executable, "-m", "pyright", "--warnings"])


def step_docs(ctx: Ctx) -> Outcome:
    problems = Store.load(Regime.load(ctx.root)).check()
    return Outcome.fail(*[str(p) for p in problems]) if problems else Outcome.ok()


def step_commits(ctx: Ctx) -> Outcome:
    bad: list[str] = []
    for sha, message in commits.branch_messages(ctx.root, ctx.base):
        for line in commits.attribution_lines(message):
            bad.append(f"commit {sha[:10]}: tool attribution: {line.strip()}")
    if ctx.pr is not None:
        bad.extend(f"PR title {p}" for p in commits.title_problems(ctx.pr.title))
        for text, what in ((ctx.pr.title, "PR title"), (ctx.pr.body, "PR body")):
            bad.extend(f"{what}: tool attribution: {ln.strip()}" for ln in commits.attribution_lines(text))
    if bad:
        return Outcome.fail(*bad)
    checked = len(commits.branch_messages(ctx.root, ctx.base))
    return Outcome.ok(f"{checked} commit(s)" + (", the PR title and body" if ctx.pr else ""))


def step_content(ctx: Ctx) -> Outcome:
    path = content.list_path()
    if path is None:
        return Outcome.skip(f"no deny list (DOCKET_DENYLIST or {content.DEFAULT_PATH}); CI checks with its own")
    if not path.is_file():
        return Outcome.fail(f"DOCKET_DENYLIST names {path}, which doesn't exist")
    names = content.load(path)
    hits = content.scan_files(ctx.root, listed_files(ctx.root), names)
    for sha, message in commits.branch_messages(ctx.root, ctx.base):
        hits.extend(content.scan_text(message, names, f"commit {sha[:10]}"))
    if ctx.pr is not None:
        hits.extend(content.scan_text(ctx.pr.title, names, "PR title"))
        hits.extend(content.scan_text(ctx.pr.body, names, "PR body"))
    if hits:
        return Outcome.fail(*[h.show(masked=ctx.ci) for h in hits])
    return Outcome.ok(f"{len(names)} name(s)")


def step_secrets(ctx: Ctx) -> Outcome:
    gitleaks = shutil.which("gitleaks")
    if gitleaks is None:
        return Outcome.fail("gitleaks isn't on PATH: winget install gitleaks, brew install gitleaks, or its release")
    cmd = [gitleaks, "git", "--no-banner", "--redact", "--config", ".gitleaks.toml"]
    if ctx.lane == "quick":
        cmd.append(f"--log-opts={ctx.base}..HEAD" if ctx.base else "--log-opts=-1")
    cmd.append(".")
    out = _tool(ctx, cmd)
    if out.status == "fail":
        return out
    # What the next commit would carry: the staged and the unstaged changes.
    for extra in (["--staged"], []):
        code, lines = _run(
            ctx, [gitleaks, "git", "--pre-commit", *extra, "--no-banner", "--redact", "--config", ".gitleaks.toml", "."]
        )
        if code != 0:
            return Outcome.fail("in uncommitted changes:", *lines[-40:])
    return Outcome.ok("whole history" if ctx.lane != "quick" else "this branch's commits and uncommitted changes")


def step_systems(ctx: Ctx) -> Outcome:
    """Every test file named in test-systems.json, and no file listed as the same everywhere showing a system's
    signals (DR-0758)."""
    found = systems.problems(ctx.root, listed_files(ctx.root))
    if found:
        return Outcome.fail(*found)
    listed = systems.load(ctx.root)
    return Outcome.ok(f"{len(listed.system)} test files run on every system, {len(listed.same)} on Linux alone per PR")


def _frontmatter(text: str) -> dict[str, str]:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).split("\n"):
        key, sep, value = line.partition(":")
        if sep and not line.startswith(" "):
            out[key.strip()] = value.strip()
    return out


def step_web(ctx: Ctx) -> Outcome:
    """The web app's committed build against its sources, and its API document against its routes, with no Node."""
    from devkit import web  # noqa: PLC0415 - the document's check imports the app in a process of its own

    problems = [p for p in (web.stamp_problem(ctx.root), web.schema_problem(ctx.root)) if p]
    return Outcome.fail(*problems) if problems else Outcome.ok("the build matches its sources, openapi.json its routes")


def step_harness(ctx: Ctx) -> Outcome:
    bad: list[str] = []
    agents = ctx.root / "AGENTS.md"
    if agents.is_file():
        n = len(agents.read_text(encoding="utf-8").splitlines())
        if n > AGENTS_MAX_LINES:
            bad.append(f"AGENTS.md has {n} lines; it is a router, keep it to {AGENTS_MAX_LINES}")
    else:
        bad.append("AGENTS.md is missing")
    # git skips a hook without the executable bit on Linux and macOS, so a hook stored as 100644 is a hook that is off.
    code, lines = _run(ctx, ["git", "ls-files", "-s", "--", ".githooks"])
    for line in lines if code == 0 else []:
        mode, _, rest = line.partition(" ")
        if mode != "100755":
            name = rest.split("	")[-1]
            bad.append(f"{name} is stored without the executable bit: git update-index --chmod=+x {name}")
    for skill in sorted([*ctx.root.glob(".claude/skills/*/SKILL.md"), *ctx.root.glob("plugin/skills/*/SKILL.md")]):
        rel = skill.relative_to(ctx.root).as_posix()
        meta = _frontmatter(skill.read_text(encoding="utf-8"))
        if meta.get("name") != skill.parent.name:
            bad.append(f"{rel}: frontmatter name must be {skill.parent.name!r}")
        if not meta.get("description"):
            bad.append(f"{rel}: frontmatter needs a description")
    bad.extend(_manifest_problems(ctx.root))
    claude = shutil.which("claude")
    validated = 0
    if claude is not None and ctx.lane == "full":
        for target in ("plugin", "."):
            if (ctx.root / target / ".claude-plugin").is_dir():
                code, lines = _run(ctx, [claude, "plugin", "validate", "--strict", target])
                validated += 1
                if code != 0:
                    bad.extend(f"claude plugin validate {target}: {ln}" for ln in lines[-20:])
    elif ctx.lane == "full":
        bad.append("Claude Code (`claude`) isn't on PATH, and the full gate validates the plugin with it")
    if bad:
        return Outcome.fail(*bad)
    return Outcome.ok(f"plugin validated by Claude Code ({validated})" if validated else "")


def _manifest_problems(root: Path) -> list[str]:
    bad: list[str] = []
    plugin = root / "plugin" / ".claude-plugin" / "plugin.json"
    market = root / ".claude-plugin" / "marketplace.json"
    for path in (plugin, market):
        if not path.is_file():
            bad.append(f"{path.relative_to(root).as_posix()} is missing")
    if bad:
        return bad
    try:
        p: Any = json.loads(plugin.read_text(encoding="utf-8"))
        m: Any = json.loads(market.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [f"a plugin manifest isn't valid JSON: {e}"]
    for k in ("name", "version", "description"):
        if not p.get(k):
            bad.append(f"plugin.json needs {k}")
    entries: list[Any] = m.get("plugins") or []
    if not any(e.get("name") == p.get("name") for e in entries):
        bad.append("marketplace.json doesn't list the plugin by its name")
    for e in entries:
        src = e.get("source")
        if isinstance(src, str) and src.startswith("./") and not (root / src).is_dir():
            bad.append(f"marketplace.json: source {src} doesn't exist")
    return bad


SLOWEST = 15
# pytest's --durations lines: "12.34s call     path::test".
_DURATION = re.compile(r"^\d+\.\d+s (setup|call|teardown) ")


def slowest(lines: list[str]) -> list[str]:
    """The slowest tests' lines from pytest's output, so each run's log, CI's on every system included, names them."""
    return [f"slowest: {ln.strip()}" for ln in lines if _DURATION.match(ln.strip())]


# A CI job spends half a minute or so around its tests step, so a share's job passes 5 minutes about when the step
# passes this (DR-0754). At 4 it warned on green runs: macOS's shares' steps took up to 4.3 minutes.
TESTS_WARN_MINUTES = 4.5


def slow_tests_warning(step: str, took: float) -> str | None:
    """On GitHub Actions, a warning the PR shows when the tests step took long enough to put its job past 5 minutes,
    so the suite can't grow slower unseen; the step's time and its slowest tests are in the log beside it."""
    if step != "tests" or os.environ.get("GITHUB_ACTIONS") != "true" or took <= TESTS_WARN_MINUTES * 60:
        return None
    return (
        f"::warning title=Slow tests::the tests step took {took / 60:.1f} minutes, past {TESTS_WARN_MINUTES}: "
        "its job is past 5 about then; the step's slowest tests are in its log"
    )


# The marker of a test that asserts an elapsed time (DR-0430).
PERF = "perf"
# Where the app's test kit builds its shared templates, named for both runs of a lane without coverage (DR-0492).
TEMPLATES_ENV = "DOCKET_TEST_TEMPLATES"
# pytest's rules: "==== PASSES ====" opens a section, "____ test ____" and "---- Captured stdout call ----" its parts.
_RULE = re.compile(r"^([=_-])\1+ .+ \1+$")
# The last line under -q, which follows the PASSES section with no rule: "2 passed, 9 deselected in 41.20s (0:00:41)".
_STATS = re.compile(r"^(\d+ [a-z]+, )*\d+ [a-z]+ in \d+\.\d+s( \(\d+:\d\d:\d\d\))?$")


def timed(lines: list[str]) -> list[str]:
    """What the passed tests printed, from pytest's PASSES section (-rP), so each run's log shows the timed tests'
    times: pytest shows a passed test's output only there."""
    out: list[str] = []
    inside = False
    for ln in lines:
        rule = _RULE.match(ln.strip())
        if (rule and rule.group(1) == "=") or _STATS.match(ln.strip()):
            inside = ln.strip(" =") == "PASSES"
        elif inside and not rule:
            out.append(f"timed: {ln.rstrip()}")
    return out


def _summary(lines: list[str]) -> str:
    return next((ln for ln in reversed(lines) if " passed" in ln or " failed" in ln), "").strip("= ")


# Where a covered share saves its coverage data, `share-K-of-N`, for the coverage lane to combine (DR-0754).
COVERAGE_DATA = ".local/coverage"
_SHARE_DATA = re.compile(r"^share-([1-9]\d*)-of-([1-9]\d*)$")
# Where a share writes what each of its files cost, for `gate weights`.
TIMES = ".local/times"


def _times(ctx: Ctx, spec: str | None, run: str) -> list[str]:
    """The option that has a share's run write its files' times, a file per system, share and run."""
    if spec is None:
        return []
    out = ctx.root / TIMES / f"{platform.system()}-{spec.replace('/', '-of-')}-{run}.json"
    out.unlink(missing_ok=True)
    return [f"--shard-times={out}"]


def step_tests(ctx: Ctx) -> Outcome:
    report = ctx.root / ".local" / "coverage.json"
    report.parent.mkdir(exist_ok=True)
    report.unlink(missing_ok=True)
    numbers = ctx.root / evalfloors.REPORT
    numbers.unlink(missing_ok=True)
    pytest = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    covered = ctx.lane == "full"
    # The full lane always runs as a share, its one share 1/1 when it isn't split: `gate full` then runs the shard
    # plugin's lines as each of CI's covered shares does, and both read the same coverage (DR-0754). The repo's root
    # conftest.py loads the plugin.
    spec = ctx.shard or ("1/1" if covered else None)
    if spec is not None:
        from devkit.shard import WEIGHTS  # noqa: PLC0415 - the plugin imports pytest, which only a sharded run needs

        # Both runs keep the same share of the test files, so the timed run finds what its share's parallel run built.
        pytest += [f"--shard={spec}", f"--shard-weights={ctx.root / WEIGHTS}"]
    if ctx.lane == "os" and not ctx.whole:
        # Windows and macOS run what can differ by system on every PR, and the rest nightly (DR-0754). The selection
        # comes before the share, so the shares split the selected files.
        pytest.append("--select=system")
    # One worker per core: most of the time goes to git subprocesses that would otherwise wait on each other.
    cmd = [*pytest, "-n", "auto", f"--durations={SLOWEST}", *_times(ctx, spec, "tests")]
    env: dict[str, str] = {}
    if covered:
        # The question set's test writes its numbers there, for the eval floors (DR-0295).
        env[evalfloors.REPORT_ENV] = str(numbers)
        if ctx.shard is not None:
            return _covered_share(ctx, ctx.shard, cmd, env)
        cmd += ["--cov", f"--cov-report=json:{report.as_posix()}", "--cov-report=term:skip-covered"]
    else:
        # Without coverage the timed tests assert their limits, so they leave the parallel run and run alone after it;
        # under coverage the index's limits are off and they stay in it (DR-0430).
        cmd += ["-m", f"not {PERF}"]
        # A folder of the step's own, made empty: one templates folder for both runs, so the timed run finds the vaults
        # the parallel run built and only times, and the timed run's temp folder, so it takes no numbered folder of
        # pytest's and never ends by deleting the parallel run's, as pytest does with a session three back (DR-0492).
        with tempfile.TemporaryDirectory(prefix="docket-tests-", ignore_cleanup_errors=True) as at:
            env[TEMPLATES_ENV] = str(Path(at, "templates"))
            code, lines = _run(ctx, cmd, env=env)
            alone = [*pytest, "-m", PERF, "-raP", f"--basetemp={Path(at, 'timed')}", *_times(ctx, spec, "timed")]
            return _alone_after(ctx, code, lines, alone, env)
    code, lines = _run(ctx, cmd, env=env)
    if code != 0:
        return Outcome.fail(*lines[-80:])
    return _floors(ctx, report, _summary(lines), slowest(lines))


def _floors(ctx: Ctx, report: Path, first: str, extra: list[str]) -> Outcome:
    """The coverage report held to the floors, each file under one named with its lines, and the question set's
    numbers to theirs."""
    data = json.loads(report.read_text(encoding="utf-8"))
    measures = coverage.measure(data)
    floors = coverage.load_floors(ctx.root)
    failures, rise = coverage.judge(measures, floors)
    failures += coverage.missing(data, coverage.below(measures, floors))
    eval_failures, eval_line = evalfloors.check(ctx.root, ctx.base)
    failures += eval_failures
    if failures:
        return Outcome.fail(*failures, *rise)
    per = ", ".join(f"{m.module} {m.percent:.1f}%" for m in measures.values())
    return Outcome.ok(first, per, eval_line, *rise, *extra)


def _covered_share(ctx: Ctx, spec: str, cmd: list[str], env: dict[str, str]) -> Outcome:
    """A share of the full lane's tests under coverage, its data saved for the coverage lane, which holds the floors
    on every share's together: one share's coverage says nothing about a floor."""
    data = ctx.root / COVERAGE_DATA / f"share-{spec.replace('/', '-of-')}"
    data.parent.mkdir(parents=True, exist_ok=True)
    data.unlink(missing_ok=True)
    code, lines = _run(ctx, [*cmd, "--cov", "--cov-report="], env={**env, "COVERAGE_FILE": str(data)})
    if code != 0:
        return Outcome.fail(*lines[-80:])
    if not data.is_file():
        return Outcome.fail(f"the run saved no coverage data to {data.relative_to(ctx.root).as_posix()}")
    saved = f"coverage data in {data.relative_to(ctx.root).as_posix()}; `gate coverage` holds the floors on all shares"
    return Outcome.ok(_summary(lines), saved, *slowest(lines))


def step_floors(ctx: Ctx) -> Outcome:
    """The coverage lane: every share's coverage data combined, then held to the floors as the full lane holds its
    own run's, with the question set's numbers the share that ran it saved (DR-0754)."""
    folder = ctx.root / COVERAGE_DATA
    found: dict[tuple[int, int], Path] = {}
    for path in sorted(folder.iterdir()) if folder.is_dir() else []:
        named = _SHARE_DATA.match(path.name)
        if named:
            found[int(named.group(1)), int(named.group(2))] = path
    totals = sorted({n for _, n in found})
    if len(totals) != 1:
        if not totals:
            return Outcome.fail(f"{COVERAGE_DATA} holds no share's coverage data: `gate full --shard K/N` saves it")
        return Outcome.fail(f"{COVERAGE_DATA} holds shares of runs split {totals} ways: one run's shares, all of them")
    n = totals[0]
    absent = [str(k) for k in range(1, n + 1) if (k, n) not in found]
    if absent:
        return Outcome.fail(f"no coverage data for share {', '.join(absent)} of {n}: each share's run saves it")
    combined = ctx.root / ".local" / "coverage-combined"
    report = ctx.root / ".local" / "coverage.json"
    combined.unlink(missing_ok=True)
    report.unlink(missing_ok=True)
    shares = [str(found[k, n]) for k in range(1, n + 1)]
    python = [sys.executable, "-m", "coverage"]
    for cmd in (
        [*python, "combine", "--keep", f"--data-file={combined}", *shares],
        [*python, "json", f"--data-file={combined}", "-o", str(report)],
    ):
        code, lines = _run(ctx, cmd)
        if code != 0:
            return Outcome.fail(*lines[-40:])
    return _floors(ctx, report, f"{n} shares' coverage combined", [])


# pytest's exit code when it ran no test: a share whose files hold no timed test has nothing to time.
NO_TESTS = 5


def _alone_after(ctx: Ctx, code: int, lines: list[str], cmd: list[str], env: dict[str, str]) -> Outcome:
    """The timed tests after the rest's run, once the lane's other steps are done too, one at a time: no other work
    shares the CPUs with what they time. -raP adds the passed tests' output, which holds their times, to the summary."""
    ctx.alone.wait()
    timed_code, timed_lines = _run(ctx, cmd, env=env)
    none_timed = timed_code == NO_TESTS
    if code != 0 or (timed_code != 0 and not none_timed):
        return Outcome.fail(*(lines[-80:] if code else []), *(timed_lines[-80:] if timed_code else []))
    alone = "timed alone: none in these files" if none_timed else f"timed alone: {_summary(timed_lines)}"
    return Outcome.ok(_summary(lines), alone, *timed(timed_lines), *slowest(lines))


def cpu_name() -> str:
    """The machine's CPU, printed beside the reranked numbers: the reranker's own time depends on it (DR-0458)."""
    info = Path("/proc/cpuinfo")
    if info.is_file():
        for line in info.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("model name") and ":" in line:
                return line.split(":", 1)[1].strip()
    return platform.processor() or platform.machine() or "an unknown CPU"


def step_models(ctx: Ctx) -> Outcome:
    """The question set with the embedder, and with the CPU reranker on its 30 pairs, against floors of their own
    (DR-0339, DR-0458): the models CI job runs it."""
    folder = os.environ.get(MODELS_ENV)
    if not folder:
        return Outcome.fail(
            f"set {MODELS_ENV} to a folder `docket models install --device cpu` filled (it sets DOCKET_MODELS_DIR)"
        )
    numbers = ctx.root / evalfloors.REPORT_MODELS
    reranked = ctx.root / evalfloors.REPORT_RERANK
    numbers.parent.mkdir(exist_ok=True)
    numbers.unlink(missing_ok=True)
    reranked.unlink(missing_ok=True)
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", MODELS_TEST, "-k", "embedder or reranker"]
    env = {evalfloors.REPORT_MODELS_ENV: str(numbers), evalfloors.REPORT_RERANK_ENV: str(reranked)}
    code, lines = _run(ctx, cmd, env=env)
    if code != 0:
        return Outcome.fail(*lines[-60:])
    failures, line = evalfloors.check(ctx.root, ctx.base, models=True)
    more, rerank_line = evalfloors.check(ctx.root, ctx.base, rerank=True)
    reranked_line = f"reranked on {cpu_name()}: {rerank_line}"
    if failures or more:
        return Outcome.fail(*failures, *more, line, reranked_line)
    return Outcome.ok(line, reranked_line)


STEPS = [
    Step(
        "lock",
        ("quick", "full"),
        "uv.lock matches the pyproject files; the models' pins the dev group's, each with a wheel for its systems",
        step_lock,
    ),
    Step("ruff", ("quick", "full"), "lint", step_ruff),
    Step("format", ("quick", "full"), "formatting", step_format),
    Step("types", ("quick", "full", "os"), "pyright (strict on src/)", step_types),
    Step("docs", ("quick", "full"), "entry files and citations against the regime", step_docs),
    Step(
        "systems",
        ("quick", "full"),
        "every test file in test-systems.json, by whether it can differ by system",
        step_systems,
    ),
    Step("commits", ("quick", "full"), "no tool attribution; the PR text with DOCKET_PR_TEXT=1", step_commits),
    Step("content", ("quick", "full"), "no private names (the deny list)", step_content),
    Step("secrets", ("quick", "full"), "gitleaks", step_secrets),
    Step("harness", ("quick", "full"), "AGENTS.md, skills, plugin manifests", step_harness),
    Step("web", ("quick", "full"), "the web app's build matches its sources; openapi.json its routes", step_web),
    Step("tests", ("full", "os"), "pytest; coverage floors in the full lane, the timed tests alone in os", step_tests),
    Step("models", ("models",), "the question set with the embedder, against its floors", step_models),
    Step("floors", ("coverage",), "the shares' coverage data combined, against the floors", step_floors),
]


def run(
    lane: str,
    only: Sequence[str] | None = None,
    shard: str | None = None,
    skip: Sequence[str] | None = None,
    *,
    whole: bool = False,
) -> int:
    if whole and lane != "os":
        raise KitError("--all widens the os lane's tests to every test file; the full lane runs every one already")
    if shard is not None:
        from devkit.shard import parse  # noqa: PLC0415 - the plugin imports pytest, which only a sharded run needs

        if lane not in ("os", "full"):
            raise KitError("--shard splits the tests of the os lane, or of the full lane: `gate coverage` joins those")
        try:
            parse(shard)
        except ValueError as e:
            raise KitError(str(e)) from e
    root = repo_root()
    pr = commits.pull_request()
    base = pr.base_sha if pr and pr.base_sha else default_base(root)
    if base is None and os.environ.get("GITHUB_EVENT_NAME") == "push":
        base = "HEAD~1"
    # The PR's title and body are checked only by the PR text workflow, which runs again on every edit; the gate's
    # own run reads them from the push event, so a fixed title would stay red there until the next push.
    pr_text = pr if os.environ.get("DOCKET_PR_TEXT") == "1" else None
    ctx = Ctx(root=root, lane=lane, ci=os.environ.get("CI") == "true", base=base, pr=pr_text, shard=shard, whole=whole)
    steps = [s for s in STEPS if lane in s.lanes and (not only or s.name in only) and s.name not in (skip or [])]
    unknown = sorted({*(only or []), *(skip or [])} - {s.name for s in STEPS})
    if unknown:
        raise KitError(f"no step {', '.join(unknown)}; `gate list` shows them")
    failed: list[str] = []
    # The tests take most of the time and need none of the other steps, so they start first and run beside them; the
    # lines still print in the steps' order.
    tests = next((s for s in steps if s.name == "tests"), None) if len(steps) > 1 else None
    if tests is not None:
        ctx.alone.clear()
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(_timed, tests, ctx) if tests is not None else None
        try:
            for step in steps:
                if step is tests and running is not None:
                    # The steps before it are done and those after wait for it: the timed tests can run alone.
                    ctx.alone.set()
                    out, took = running.result()
                else:
                    out, took = _timed(step, ctx)
                label = {"ok": "ok  ", "fail": "FAIL", "skip": "skip"}[out.status]
                first = f"  {out.detail[0]}" if out.detail and out.status != "fail" else ""
                print(f"{label} {step.name:<8} {took:5.1f}s{first}", flush=True)
                rest = out.detail if out.status == "fail" else out.detail[1:]
                for line in rest:
                    print(f"       {line}", flush=True)
                warning = slow_tests_warning(step.name, took)
                if warning:
                    print(warning, flush=True)
                if out.status == "fail":
                    failed.append(step.name)
        finally:
            # A step that raised leaves the loop early; the pool's exit waits for the tests, which wait for this.
            ctx.alone.set()
    if failed:
        print(f"gate {lane}: failed ({', '.join(failed)})")
        return 1
    print(f"gate {lane}: passed")
    return 0


def _timed(step: Step, ctx: Ctx) -> tuple[Outcome, float]:
    start = time.monotonic()
    try:
        out = step.run(ctx)
    except KitError as e:
        out = Outcome.fail(str(e))
    return out, time.monotonic() - start


def weights(folder: Path | None) -> int:
    """The shares' weights rewritten from a CI run's times: `gh run download <run> -p "times-*" -D <folder>` first."""
    from devkit.shard import WEIGHTS, merge_times  # noqa: PLC0415 - the plugin imports pytest

    if folder is None or not folder.is_dir():
        raise KitError('weights needs the folder of a CI run\'s times: gh run download <run> -p "times-*" -D <folder>')
    root = repo_root()
    for line in merge_times(folder, root / WEIGHTS, root):
        print(line)
    print(f"wrote {WEIGHTS}")
    return 0


def check_text(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    problems = [f"tool attribution: {ln.strip()}" for ln in commits.attribution_lines(text)]
    deny = content.list_path()
    if deny is not None and deny.is_file():
        names = content.load(deny)
        problems += [h.show(masked=False) for h in content.scan_text(text, names, path.name)]
    for p in problems:
        print(p)
    print("text ok" if not problems else f"{len(problems)} problem(s): fix them before posting")
    return 1 if problems else 0


def main(argv: Sequence[str] | None = None) -> int:
    utf8_stdio()
    p = argparse.ArgumentParser(prog="gate", description="The one gate: what passes here is the merge rule.")
    p.add_argument("lane", nargs="?", default="quick", choices=[*LANES, "list", "check-text", "weights"])
    p.add_argument("file", nargs="?", help="check-text: the text file to check; weights: the folder of a run's times")
    p.add_argument("--only", help="comma-separated steps of the lane to run")
    p.add_argument("--skip", help="comma-separated steps of the lane to leave out")
    p.add_argument("--shard", metavar="K/N", help="os and full lanes: run share K of N of the test files (DR-0754)")
    p.add_argument(
        "--all",
        action="store_true",
        dest="whole",
        help="os lane: every test file, not only those that can differ by system and the smoke set (DR-0754)",
    )
    args = p.parse_args(argv)
    try:
        if args.lane == "list":
            for s in STEPS:
                print(f"{s.name:<8} {','.join(s.lanes):<16} {s.about}")
            return 0
        if args.lane == "check-text":
            if not args.file:
                raise KitError("check-text needs the file to check")
            return check_text(Path(args.file))
        if args.lane == "weights":
            return weights(Path(args.file) if args.file else None)
        split = [(v.split(",") if v else None) for v in (args.only, args.skip)]
        return run(args.lane, split[0], args.shard, split[1], whole=args.whole)
    except KitError as e:
        print(f"gate: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
