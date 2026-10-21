from __future__ import annotations

import json
import platform
import re
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from devkit import commits, content, coverage, evalfloors, gate, systems
from devkit.cli import cmd_commit_msg
from devkit.repo import KitError


def test_attribution_is_found_and_stripped() -> None:
    message = (
        "Add the thing\n\nBecause.\n\nCo-Authored-By: Claude Opus <noreply@anthropic.com>\n"
        "\N{ROBOT FACE} Generated with [Claude Code](https://claude.com/claude-code)\n"
    )
    assert len(commits.attribution_lines(message)) == 2
    assert commits.strip_attribution(message) == "Add the thing\n\nBecause.\n"
    assert commits.attribution_lines("Co-Authored-By: Ada Example <ada@example.invalid>") == []


def test_the_commit_msg_hook_rewrites_the_file(tmp_path: Path) -> None:
    path = tmp_path / "MSG"
    path.write_text("Fix it\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n", encoding="utf-8")

    class Args:
        file = str(path)

    assert cmd_commit_msg(Args()) == 0  # type: ignore[arg-type]
    assert path.read_text(encoding="utf-8") == "Fix it\n"


@pytest.mark.parametrize(
    ("title", "ok"),
    [
        ("Add the vault's raw log", True),
        ("add the vault", False),
        ("Add the vault.", False),
        ("WIP vault", False),
        ("A" * 73, False),
    ],
)
def test_pr_titles(title: str, ok: bool) -> None:
    assert (commits.title_problems(title) == []) is ok


def test_names_match_whole_words_ignoring_case() -> None:
    names = content.parse("# private\nAcme Corp # a customer\nZed\n")
    assert [n.text for n in names] == ["Acme Corp", "Zed"]
    hits = content.scan_text("we met acme corp today\nZedd is fine\nzed.", names, "f.md")
    assert [h.where for h in hits] == ["f.md:1", "f.md:3"]
    assert hits[0].show(masked=True) == "f.md:1: deny-list entry 2"
    assert hits[0].show(masked=False) == "f.md:1: 'Acme Corp'"


def test_the_content_step_finds_names_in_files_paths_and_commits(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git: Callable[..., str]
) -> None:
    deny = tmp_path / "deny.txt"
    deny.write_text("Globex\n", encoding="utf-8")
    monkeypatch.setenv("DOCKET_DENYLIST", str(deny))
    git(repo, "switch", "-q", "-c", "work")
    (repo / "globex-notes.md").write_text("nothing\n", encoding="utf-8")
    (repo / "a.md").write_text("a GLOBEX call\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Talk to Globex")
    base = git(repo, "rev-parse", "HEAD~1").strip()
    ctx = gate.Ctx(root=repo, lane="quick", ci=True, base=base, pr=None)
    out = gate.step_content(ctx)
    assert out.status == "fail"
    joined = "\n".join(out.detail)
    assert "globex-notes.md (its path)" in joined
    assert "a.md:1" in joined
    assert "commit " in joined
    assert "Globex" not in joined.replace("globex-notes.md", "")


def test_the_content_step_skips_without_a_list(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(content, "DEFAULT_PATH", repo / "nope.txt")
    ctx = gate.Ctx(root=repo, lane="quick", ci=False, base=None, pr=None)
    assert gate.step_content(ctx).status == "skip"


def test_the_commits_step_reads_the_pr(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"pull_request": {"title": "fix things.", "body": "Generated with Claude Code", "base": {}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    pr = commits.pull_request()
    assert pr is not None
    out = gate.step_commits(gate.Ctx(root=repo, lane="quick", ci=True, base=None, pr=pr))
    assert out.status == "fail"
    assert any("full stop" in d for d in out.detail)
    assert any("PR body: tool attribution" in d for d in out.detail)


def test_coverage_floors() -> None:
    report = {
        "files": {
            "packages/app/src/app/a.py": {"summary": {"covered_lines": 9, "num_statements": 10}, "missing_lines": [3]},
            "tools/kit/src/kit/b.py": {"summary": {"covered_lines": 5, "num_statements": 10}, "missing_lines": [4, 7]},
            "tests/test_x.py": {"summary": {"covered_lines": 1, "num_statements": 1}},
        }
    }
    measures = coverage.measure(report)
    assert set(measures) == {"app", "kit"}
    failures, rise = coverage.judge(measures, {"app": 80, "kit": 60, "gone": 10})
    assert failures == ["kit: 50.0% is below its floor of 60%", "gone: has a floor but nothing was measured"]
    assert rise == ["app: 90.0%, floor 80%: raise it to 90"]
    # A module under its floor names the lines no test ran.
    floors = {"app": 80, "kit": 60}
    assert coverage.missing(report, coverage.below(measures, floors)) == ["  tools/kit/src/kit/b.py: line 4, 7"]


# A decision's id built, so the docs check doesn't read it as a citation.
LOWERS = "DR-" + "0042"


def _decision(status: str, text: str) -> dict[str, Any]:
    return {"id": LOWERS, "title": "A floor comes down", "status": status, "description": text}


def test_a_floor_below_the_base_s_needs_a_decided_decision_that_sets_it() -> None:
    base = {"app": 100, "kit": 90}
    # A raised floor, an unchanged one and a module new since the base pass with nothing named.
    assert coverage.guard({"app": 100, "kit": 92, "new": 50}, base, {}, {}) == []
    lowered = "kit: floor lowered from 90% to 85%"
    assert coverage.guard({"app": 100, "kit": 85}, base, {}, {}) == [
        f"{lowered}; name the decided decision that sets it under $lowered in {coverage.FLOORS} (DR-0755)"
    ]
    named = {"kit": LOWERS}
    assert coverage.guard({"kit": 85}, base, named, {}) == [
        f"{lowered}, and {LOWERS}, which $lowered names, isn't a decision"
    ]
    sets = "kit at 85%: the cut drops the tests of the old reader"
    assert coverage.guard({"kit": 85}, base, named, {LOWERS: _decision("proposed", sets)}) == [
        f"{lowered}, and {LOWERS} is proposed, not decided: only the owner lowers a floor"
    ]
    # A decided decision passes only when it says the module's new floor: the module and a number apart set none.
    apart = "fewer kit processes in the tests, and the floor comes down only for a cut of 85% or more"
    for other in ("app at 85%", "kit at 88%", "kit at 185%", "kit at 0.85%", apart):
        assert coverage.guard({"kit": 85}, base, named, {LOWERS: _decision("decided", other)}) == [
            f"{lowered}, and {LOWERS} doesn't say kit at 85%"
        ]
    assert coverage.guard({"kit": 85}, base, named, {LOWERS: _decision("decided", sets)}) == []
    binds = {**_decision("decided", "the reader goes"), "binds": ["The kit's floor to 85%."]}
    assert coverage.guard({"kit": 85}, base, named, {LOWERS: binds}) == []
    # The base's own $lowered covers its own lowering, not another: a gain earned since can't go back unseen.
    assert coverage.guard({"kit": 85}, base, named, {LOWERS: _decision("decided", sets)}, named) == [
        f"{lowered}, and {LOWERS} lowered it before: name the decided decision that sets it now"
    ]
    # Without a base, as on main itself, nothing is held.
    assert coverage.guard({"kit": 85}, None, {}, {}) == []


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("{", "isn't valid JSON"),
        ("[90]", "holds an object"),
        ('{"kit": "90"}', "kit's floor is a whole percentage, not '90'"),
        ('{"kit": true}', "kit's floor is a whole percentage, not True"),
        ('{"kit": 90, "$lowered": "DR-nnnn"}', "$lowered maps a module to the decision"),
        ('{"kit": 90, "$lowered": {"kit": 7}}', "$lowered maps a module to the decision"),
    ],
)
def test_a_floors_file_in_another_shape_fails_as_a_gate_line(text: str, said: str) -> None:
    with pytest.raises(KitError, match=re.escape(said)):
        coverage.parse(text)


def test_the_floors_are_held_to_the_merge_base_s_and_the_decision_read_from_the_docs(
    repo: Path, git: Callable[..., str]
) -> None:
    floors = repo / coverage.FLOORS
    floors.parent.mkdir(parents=True)
    start = git(repo, "rev-parse", "HEAD").strip()
    floors.write_text(json.dumps({"$comment": "x", "app": 100, "kit": 90}), encoding="utf-8")
    # A base from before the file existed has no floors to hold.
    assert coverage.base_floors(repo, start) is None and coverage.check_lowered(repo, start) == []
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Floors")
    base = git(repo, "rev-parse", "HEAD").strip()
    assert coverage.base_floors(repo, base) == ({"app": 100, "kit": 90}, {})
    floors.write_text(json.dumps({"app": 100, "kit": 95}), encoding="utf-8")
    assert coverage.check_lowered(repo, base) == []
    floors.write_text(json.dumps({"app": 100, "kit": 85}), encoding="utf-8")
    assert coverage.check_lowered(repo, base) == [
        "kit: floor lowered from 90% to 85%; name the decided decision that sets it under $lowered in "
        f"{coverage.FLOORS} (DR-0755)"
    ]
    assert coverage.check_lowered(repo, None) == []
    entry = repo / "tools" / "devkit" / "index.d" / f"{LOWERS}.yaml"
    entry.parent.mkdir()
    text = (
        f"decisions:\n  - id: {LOWERS}\n    title: The kit's floor to 85%\n    status: {{status}}\n"
        '    at: "2026-10-21"\n    by: Ada Example\n    cost: cheap\n    description: The old reader goes.\n'
    )
    entry.write_text(text.format(status="proposed"), encoding="utf-8")
    floors.write_text(json.dumps({"app": 100, "kit": 85, "$lowered": {"kit": LOWERS}}), encoding="utf-8")
    assert coverage.check_lowered(repo, base) == [
        f"kit: floor lowered from 90% to 85%, and {LOWERS} is proposed, not decided: only the owner lowers a floor"
    ]
    entry.write_text(text.format(status="decided"), encoding="utf-8")
    assert coverage.check_lowered(repo, base) == []
    # A base this clone doesn't hold fails, rather than passing every lowered floor.
    with pytest.raises(KitError, match="can't read the merge base 0123456"):
        coverage.check_lowered(repo, "0123456")


def test_check_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    deny = tmp_path / "deny.txt"
    deny.write_text("Initech\n", encoding="utf-8")
    monkeypatch.setenv("DOCKET_DENYLIST", str(deny))
    body = tmp_path / "body.md"
    body.write_text("Fixes the thing for Initech.\n", encoding="utf-8")
    assert gate.check_text(body) == 1
    body.write_text("Fixes the thing.\n", encoding="utf-8")
    assert gate.check_text(body) == 0
    assert "text ok" in capsys.readouterr().out


def test_the_harness_step_checks_skills_and_manifests(repo: Path) -> None:
    (repo / "AGENTS.md").write_text("# a\n", encoding="utf-8")
    skill = repo / ".claude" / "skills" / "do-thing"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: other\ndescription: x\n---\n", encoding="utf-8")
    out = gate.step_harness(gate.Ctx(root=repo, lane="quick", ci=False, base=None, pr=None))
    assert out.status == "fail"
    assert any("frontmatter name must be 'do-thing'" in d for d in out.detail)
    assert any("plugin.json is missing" in d for d in out.detail)


def test_a_name_split_by_a_line_break_is_still_found() -> None:
    names = content.parse("Acme Corp\n")
    hits = content.scan_text("we met Acme\nCorp on Monday", names, "f.md")
    assert [h.where for h in hits] == ["f.md:1"]


def test_a_deny_list_with_a_byte_order_mark_matches_its_first_name(tmp_path: Path) -> None:
    deny = tmp_path / "deny.txt"
    deny.write_bytes("﻿Globex\nInitech\n".encode())
    names = content.load(deny)
    assert [n.text for n in names] == ["Globex", "Initech"]
    assert content.scan_text("globex", names, "x")


def test_utf16_text_files_are_scanned(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("a call with Globex", encoding="utf-16")
    hits = content.scan_files(tmp_path, ["notes.txt"], content.parse("Globex\n"))
    assert [h.where for h in hits] == ["notes.txt:1"]


@pytest.mark.parametrize(
    "line",
    [
        "Keep the summary generated by Claude when a sync reruns",
        "Generated by Claude, the notes cite their raw spans.",
        "The plugin talks to Claude Code through MCP.",
    ],
)
def test_sentences_about_claude_are_not_attribution(line: str) -> None:
    assert commits.attribution_lines(line) == []


def test_the_harness_step_refuses_hooks_without_the_executable_bit(repo: Path, git: Callable[..., str]) -> None:
    hooks = repo / ".githooks"
    hooks.mkdir()
    (hooks / "pre-push").write_text("#!/bin/sh\n", encoding="utf-8")
    git(repo, "add", ".githooks/pre-push")
    git(repo, "update-index", "--chmod=-x", ".githooks/pre-push")
    out = gate.step_harness(gate.Ctx(root=repo, lane="quick", ci=False, base=None, pr=None))
    assert any("pre-push is stored without the executable bit" in d for d in out.detail)
    git(repo, "update-index", "--chmod=+x", ".githooks/pre-push")
    out = gate.step_harness(gate.Ctx(root=repo, lane="quick", ci=False, base=None, pr=None))
    assert not any("executable bit" in d for d in out.detail)


def test_the_board_helper_retries_github_s_transient_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from devkit import cli

    answers = [
        subprocess.CompletedProcess([], 1, "", "GraphQL: Something went wrong while executing your query on ..."),
        subprocess.CompletedProcess([], 0, "ok\n", ""),
    ]
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return answers.pop(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("time.sleep", lambda _: None)
    assert cli._gh("project", "view", "3") == "ok\n"
    assert len(calls) == 2


def test_the_board_helper_gives_up_on_other_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from devkit import cli
    from devkit.repo import KitError

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", "HTTP 404: Not Found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("time.sleep", lambda _: pytest.fail("slept before giving up on a lasting error"))
    with pytest.raises(KitError, match="404"):
        cli._gh("issue", "view", "999")
    assert len(calls) == 1


def test_the_board_helper_stops_retrying_after_its_last_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from devkit import cli
    from devkit.repo import KitError

    sleeps: list[float] = []

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, "", "GraphQL: Something went wrong while executing your query")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("time.sleep", sleeps.append)
    with pytest.raises(KitError, match="Something went wrong"):
        cli._gh("project", "view", "3")
    assert sleeps == [2.0, 5.0, 10.0]


def test_only_the_pr_text_run_checks_the_title(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"title": "a title that is far too long " * 4, "body": ""}}), "utf-8")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.delenv("DOCKET_PR_TEXT", raising=False)
    assert gate.run("quick", ["commits"]) == 0
    monkeypatch.setenv("DOCKET_PR_TEXT", "1")
    assert gate.run("quick", ["commits"]) == 1
    assert "PR title is" in capsys.readouterr().out


# Names and texts where case folding is tricky: dotless i, a capital dotted I in a name's first word and in a text, long
# s, the Kelvin sign, sharp s, a name split across lines, a name in a path.
# Spelled by code point: the linter rightly flags look-alike letters written as themselves.
DOTLESS_I, DOTTED_CAPITAL_I, LONG_S, KELVIN, SHARP_S = chr(0x131), chr(0x130), chr(0x17F), chr(0x212A), chr(0xDF)
CASE_NAMES = f"Kis Ilona\n{DOTTED_CAPITAL_I}stanbul Partners\nIzmir Trade\nStrauss\nKelvin Labs\nSigma Works\n"
CASE_TEXTS = {
    "a.md": f"met kis {DOTLESS_I}lona today",
    "b.md": f"K{DOTLESS_I}s Ilona called",
    "c.md": "istanbul partners and ISTANBUL PARTNERS",
    "d.md": f"{DOTTED_CAPITAL_I}zmir trade fair",
    "e.md": f"Strau{LONG_S}{LONG_S} was there",
    "f.md": f"{KELVIN}elvin labs report",
    "g.md": f"Strau{SHARP_S} alone",
    "h.md": "the sigma\nworks team",
    "strauss.md": "nothing here",
    "i.md": "nothing to see",
}


def test_the_folded_scan_finds_exactly_what_each_pattern_finds(tmp_path: Path) -> None:
    names = content.parse(CASE_NAMES)
    for rel, text in CASE_TEXTS.items():
        (tmp_path / rel).write_text(text, encoding="utf-8")
    files = sorted(CASE_TEXTS)
    every: list[content.Hit] = []
    for rel in files:
        every += [content.Hit(f"{rel} (its path)", n) for n in names if n.pattern.search(rel)]
        every += content.scan_text(CASE_TEXTS[rel], names, rel)
    # A binary file and one that went missing are read for their paths only, as before.
    (tmp_path / "strauss.bin").write_bytes(b"Strauss\0")
    found = content.scan_files(tmp_path, [*files, "strauss.bin", "gone-strauss.md"], names)
    every += [content.Hit(f"{rel} (its path)", names[3]) for rel in ("strauss.bin", "gone-strauss.md")]
    assert [h.show(masked=False) for h in found] == [h.show(masked=False) for h in every]
    assert {h.where.split(":")[0].split(" ")[0] for h in found} == {
        "a.md", "b.md", "c.md", "d.md", "e.md", "f.md", "h.md", "strauss.md", "strauss.bin", "gone-strauss.md"
    }  # fmt: skip


def test_without_re_s_case_table_every_pattern_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert content._case_table("no.such.module") is None
    monkeypatch.setattr(content, "_case_table", lambda: None)
    (tmp_path / "a.md").write_text(f"K{DOTLESS_I}s Ilona called", encoding="utf-8")
    hits = content.scan_files(tmp_path, ["a.md"], content.parse(CASE_NAMES))
    assert [h.where for h in hits] == ["a.md:1"]


def test_slowest_tests_are_read_from_pytest_s_output() -> None:
    lines = [
        "============ slowest 15 durations ============",
        "12.34s call     packages/x/test_a.py::test_one",
        "  0.50s setup    t.py::test_two",
        "1 passed in 3.00s",
    ]
    assert gate.slowest(lines) == [
        "slowest: 12.34s call     packages/x/test_a.py::test_one",
        "slowest: 0.50s setup    t.py::test_two",
    ]


def test_a_tests_step_past_four_and_a_half_minutes_warns_on_github_actions_only(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    took = [0.0]
    monkeypatch.setattr(gate, "STEPS", [gate.Step("tests", ("os",), "", lambda _ctx: gate.Outcome.ok("1 passed"))])
    monkeypatch.setattr(gate, "_timed", lambda step, ctx: (step.run(ctx), took[0]))
    for actions, seconds, warned in (("true", 271.0, True), ("true", 270.0, False), ("", 900.0, False)):
        monkeypatch.setenv("GITHUB_ACTIONS", actions)
        took[0] = seconds
        assert gate.run("os") == 0
        warnings = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("::warning")]
        assert warnings == (
            [
                "::warning title=Slow tests::the tests step took 4.5 minutes, past 4.5: its job is "
                "past 5 about then; the step's slowest tests are in its log"
            ]
            if warned
            else []
        )
    assert gate.slow_tests_warning("types", 900.0) is None


def test_a_lane_runs_its_tests_beside_its_other_steps(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    started = threading.Event()

    def tests(_ctx: gate.Ctx) -> gate.Outcome:
        started.set()
        return gate.Outcome.fail("1 failed")

    def types(_ctx: gate.Ctx) -> gate.Outcome:
        # Run one after the other, the tests would start only after this step returned.
        return gate.Outcome.fail("types broke" if started.wait(10) else "the tests never started")

    steps = [gate.Step("types", ("os",), "", types), gate.Step("tests", ("os",), "", tests)]
    monkeypatch.setattr(gate, "STEPS", steps)
    assert gate.run("os") == 1
    out = capsys.readouterr().out
    assert "types broke" in out
    assert out.index("FAIL types") < out.index("FAIL tests")
    assert "gate os: failed (types, tests)" in out


def test_the_timed_tests_wait_for_the_lane_s_other_steps(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    started, done = threading.Event(), list[str]()

    def tests(ctx: gate.Ctx) -> gate.Outcome:
        started.set()
        return gate.Outcome.ok(f"alone after {done}" if ctx.alone.wait(10) else "never alone")

    def types(_ctx: gate.Ctx) -> gate.Outcome:
        started.wait(10)
        done.append("types")
        return gate.Outcome.ok()

    steps = [gate.Step("types", ("os",), "", types), gate.Step("tests", ("os",), "", tests)]
    monkeypatch.setattr(gate, "STEPS", steps)
    assert gate.run("os") == 0
    assert "alone after ['types']" in capsys.readouterr().out


def test_a_step_that_raises_doesn_t_leave_the_timed_tests_waiting(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def tests(ctx: gate.Ctx) -> gate.Outcome:
        return gate.Outcome.ok() if ctx.alone.wait(10) else gate.Outcome.fail("never alone")

    def types(_ctx: gate.Ctx) -> gate.Outcome:
        raise RuntimeError("types fell over")

    steps = [gate.Step("types", ("os",), "", types), gate.Step("tests", ("os",), "", tests)]
    monkeypatch.setattr(gate, "STEPS", steps)
    with pytest.raises(RuntimeError, match="fell over"):
        gate.run("os")


PASSED_OUTPUT = [
    "..                                                                       [100%]",
    "=================================== PASSES ====================================",
    "__________________________________ test_queries ___________________________________",
    "---------------------------- Captured stdout setup ----------------------------",
    "built 100000 chunks in 40.1 s",
    "---------------------------- Captured stdout call -----------------------------",
    "one word            12.1 ms  20 hits  'kaber'",
    "__________________________________ test_mention ___________________________________",
    "---------------------------- Captured stdout call -----------------------------",
    "mention pass: 300 aliases, 100000 chunks, 512 ms, 2210 mentions",
    "2 passed, 9 deselected in 41.20s",
]


def test_timed_lines_are_read_from_pytest_s_passes_section() -> None:
    assert gate.timed(PASSED_OUTPUT) == [
        "timed: built 100000 chunks in 40.1 s",
        "timed: one word            12.1 ms  20 hits  'kaber'",
        "timed: mention pass: 300 aliases, 100000 chunks, 512 ms, 2210 mentions",
    ]
    # Another section after it ends it, as the stats line does.
    assert gate.timed([*PASSED_OUTPUT[:5], "=========== tests coverage ===========", "src/a.py 90%"]) == [
        "timed: built 100000 chunks in 40.1 s"
    ]
    assert gate.timed(["1 passed in 0.50s"]) == []


def _fake_pytest(
    monkeypatch: pytest.MonkeyPatch, codes: tuple[int, int] = (0, 0), envs: list[dict[str, str]] | None = None
) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(_ctx: gate.Ctx, cmd: list[str], *, env: dict[str, str] | None = None) -> tuple[int, list[str]]:
        calls.append(cmd)
        if envs is not None:
            envs.append(dict(env or {}))
        if "-n" in cmd:
            lines = ["....", "12.34s call     t.py::test_slow", "9 passed, 2 deselected in 3.00s", "a test broke"]
            return codes[0], lines
        return codes[1], [*PASSED_OUTPUT, "a timed test broke"]

    monkeypatch.setattr(gate, "_run", fake_run)
    return calls


def test_the_os_lane_runs_what_can_differ_by_system_and_all_of_it_with_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_pytest(monkeypatch)
    assert gate.step_tests(gate.Ctx(root=tmp_path, lane="os", ci=False, base=None, pr=None, shard="1/4")).status == "ok"
    # Both runs of a share select the same files, before the share is taken.
    assert len(calls) == 2 and all("--select=system" in run for run in calls)
    calls.clear()
    whole = gate.Ctx(root=tmp_path, lane="os", ci=False, base=None, pr=None, shard="1/8", whole=True)
    assert gate.step_tests(whole).status == "ok"
    assert len(calls) == 2 and not any("--select=system" in run for run in calls)
    calls.clear()
    # The full lane runs every file, as it always did.
    _fake_pytest(monkeypatch, codes=(1, 0))
    gate.step_tests(gate.Ctx(root=tmp_path, lane="full", ci=False, base=None, pr=None))
    assert not any("--select=system" in run for run in calls)
    with pytest.raises(KitError, match="--all widens the os lane's tests"):
        gate.run("full", whole=True)
    assert gate.main(["quick", "--all"]) == 2


def systems_root(root: Path, files: dict[str, str], listed: dict[str, Any]) -> list[str]:
    """A root with these files and this test-systems.json; the files' paths, as the gate lists them."""
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    (root / systems.SYSTEMS).parent.mkdir(parents=True, exist_ok=True)
    (root / systems.SYSTEMS).write_text(json.dumps(listed), encoding="utf-8")
    return sorted(files)


WHY = {"kind": "git, files, paths", "why": "an invented reason"}
PLAIN = "def test_it():\n    assert 1 + 1 == 2\n"


def test_every_test_file_is_named_once_and_every_name_is_a_test_file(tmp_path: Path) -> None:
    files = {
        "packages/app/tests/test_git.py": PLAIN,
        "packages/app/tests/test_pure.py": PLAIN,
        "packages/app/tests/test_new.py": PLAIN,
        "packages/app/tests/test_twice.py": PLAIN,
        "tools/devkit/tests/test_kit.py": PLAIN,
        "examples/sources/x/tests/test_mapper.py": PLAIN,
        # Not a test file pytest collects: a kit, and a name outside the test folders.
        "packages/app/tests/app_kit.py": "import os\nWINDOWS = os.name == 'nt'\n",
        "docs/test_notes.py": PLAIN,
    }
    listed = {
        "system": {
            "packages/app/tests/test_git.py": WHY,
            "packages/app/tests/test_twice.py": WHY,
            "packages/app/tests/test_renamed.py": WHY,
        },
        "same": [
            "packages/app/tests/test_pure.py",
            "packages/app/tests/test_twice.py",
            "tools/devkit/tests/test_kit.py",
            "examples/sources/x/tests/test_mapper.py",
            "examples/sources/x/tests/test_mapper.py",
        ],
    }
    found = systems.problems(tmp_path, systems_root(tmp_path, files, listed))
    named = systems.SYSTEMS
    assert found == [
        "examples/sources/x/tests/test_mapper.py: listed under same twice",
        f"packages/app/tests/test_new.py: not in {named}; put it under system with its kind and why if it can differ "
        "by system, else under same",
        f"packages/app/tests/test_renamed.py: in {named}, but no such test file",
        "packages/app/tests/test_twice.py: under system and under same; it is one or the other",
    ]


@pytest.mark.parametrize(
    ("code", "said"),
    [
        ("import sys\n\nWINDOWS = sys.platform == 'win32'\n", "line 3: names the system (sys.platform)"),
        ("import os\nnt = os.name == 'nt'\n", "line 2: names the system (os.name)"),
        ("import platform\nplatform.system()\n", "line 2: names the system (platform.system)"),
        ("import pytest\nfcntl = pytest.importorskip('fcntl')\n", "line 2: skips by system (fcntl)"),
        ("def test_a(tmp_path):\n    (tmp_path / 'a').symlink_to(tmp_path)\n", "line 2: makes links (symlink_to)"),
        ("import os\nos.link('a', 'b')\n", "line 2: makes links (os.link)"),
        ("def test_a(tmp_path):\n    tmp_path.chmod(0o700)\n", "line 2: makes file modes (chmod)"),
        ("import stat\nMODE = stat.S_IWRITE\n", "line 2: makes file modes (S_IWRITE)"),
        ("import os\nos.utime('a', ns=(1, 1))\n", "line 2: makes file times (utime)"),
    ],
)
def test_a_file_listed_as_the_same_everywhere_fails_on_a_system_s_signal(tmp_path: Path, code: str, said: str) -> None:
    files = {"packages/app/tests/test_a.py": code}
    found = systems.problems(tmp_path, systems_root(tmp_path, files, {"same": list(files)}))
    assert found == [
        f"packages/app/tests/test_a.py: {said}: put it under system with why it can differ, or say under signals why "
        "it runs the same everywhere"
    ]
    # Under system it may; under same with a reason, too.
    assert systems.problems(tmp_path, systems_root(tmp_path, files, {"system": dict.fromkeys(files, WHY)})) == []
    why = {"same": list(files), "signals": dict.fromkeys(files, "sets it back as it was, the same everywhere")}
    assert systems.problems(tmp_path, systems_root(tmp_path, files, why)) == []


def test_reading_a_file_s_time_or_mode_is_no_signal_and_a_reason_without_one_goes(tmp_path: Path) -> None:
    reads = "def test_a(tmp_path):\n    stat = tmp_path.stat()\n    assert stat.st_mtime_ns and stat.st_mode\n"
    files = {"packages/app/tests/test_a.py": reads, "packages/app/tests/test_b.py": PLAIN}
    listed = {
        "system": {"packages/app/tests/test_b.py": WHY},
        "same": ["packages/app/tests/test_a.py"],
        "signals": {"packages/app/tests/test_a.py": "a reason", "packages/app/tests/test_b.py": "a reason"},
    }
    assert systems.problems(tmp_path, systems_root(tmp_path, files, listed)) == [
        "packages/app/tests/test_a.py: under signals, but its code shows no signal now: take it out of signals",
        "packages/app/tests/test_b.py: under signals, which are for files under same",
    ]


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("{", "isn't valid JSON"),
        ('{"same": ["a.py"], "same": []}', "names same twice"),
        ("[]", "holds an object with system, same and signals"),
        ('{"same": "a.py"}', "same a list of files"),
        ('{"system": {"a.py": {"kind": "git"}}}', "a.py under system needs its kind and why it can differ"),
        ('{"same": [1]}', "same lists files by their paths"),
        ('{"signals": {"a.py": " "}}', "each file under signals says why it runs the same everywhere"),
    ],
)
def test_the_list_s_shape_is_held(tmp_path: Path, text: str, said: str) -> None:
    (tmp_path / systems.SYSTEMS).parent.mkdir(parents=True)
    (tmp_path / systems.SYSTEMS).write_text(text, encoding="utf-8")
    [found] = systems.problems(tmp_path, [])
    assert said in found


def test_the_systems_step_reads_the_repo_s_files(repo: Path) -> None:
    ctx = gate.Ctx(root=repo, lane="quick", ci=False, base=None, pr=None)
    [missing] = gate.step_systems(ctx).detail
    assert missing.endswith("is missing: it names each test file, by whether it can differ by system")
    files = {"packages/app/tests/test_git.py": PLAIN, "packages/app/tests/test_pure.py": PLAIN}
    listed = {"system": {"packages/app/tests/test_git.py": WHY}, "same": ["packages/app/tests/test_pure.py"]}
    systems_root(repo, files, listed)
    out = gate.step_systems(ctx)
    assert (out.status, out.detail) == ("ok", ["1 test files run on every system, 1 on Linux alone per PR"])
    (repo / "packages" / "app" / "tests" / "test_new.py").write_text(PLAIN, encoding="utf-8")
    assert gate.step_systems(ctx).status == "fail"


def test_the_tests_step_runs_the_timed_tests_alone_after_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_pytest(monkeypatch)
    out = gate.step_tests(gate.Ctx(root=tmp_path, lane="os", ci=False, base=None, pr=None))
    rest, alone = calls
    assert rest[rest.index("not perf") - 1] == "-m" and rest[rest.index("-n") + 1] == "auto"
    assert alone[alone.index("perf") - 1] == "-m" and "-n" not in alone and "-raP" in alone
    assert not any(a.startswith("--cov") for a in [*rest, *alone])
    assert out.status == "ok"
    assert out.detail == [
        "9 passed, 2 deselected in 3.00s",
        "timed alone: 2 passed, 9 deselected in 41.20s",
        "timed: built 100000 chunks in 40.1 s",
        "timed: one word            12.1 ms  20 hits  'kaber'",
        "timed: mention pass: 300 aliases, 100000 chunks, 512 ms, 2210 mentions",
        "slowest: 12.34s call     t.py::test_slow",
    ]


def test_a_share_s_two_runs_keep_the_same_share_of_the_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_pytest(monkeypatch)
    ctx = gate.Ctx(root=tmp_path, lane="os", ci=False, base=None, pr=None, shard="2/2")
    assert gate.step_tests(ctx).status == "ok"
    weights = f"--shard-weights={tmp_path / 'tools' / 'devkit' / 'test-weights.json'}"
    times: list[str] = []
    for run in calls:
        # The root conftest.py loads the plugin: a `-p` would import it before coverage starts.
        assert "--shard=2/2" in run and weights in run and "devkit.shard" not in run
        times += [a.removeprefix("--shard-times=") for a in run if a.startswith("--shard-times=")]
    # Each run writes its files' times to a file of its own, for `gate weights`.
    system = platform.system()
    assert [Path(t).relative_to(tmp_path).as_posix() for t in times] == [
        f".local/times/{system}-2-of-2-tests.json",
        f".local/times/{system}-2-of-2-timed.json",
    ]


def test_the_os_and_full_lanes_take_a_share_and_only_k_of_n(repo: Path) -> None:
    with pytest.raises(KitError, match="os lane, or of the full lane"):
        gate.run("models", shard="1/2")
    with pytest.raises(KitError, match="K/N"):
        gate.run("os", shard="3/2")
    with pytest.raises(KitError, match="K/N"):
        gate.run("full", shard="0/2", only=["tests"])
    assert gate.main(["os", "--shard", "0/2"]) == 2


def test_a_covered_share_saves_its_coverage_data_and_holds_no_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(_ctx: gate.Ctx, cmd: list[str], *, env: dict[str, str] | None = None) -> tuple[int, list[str]]:
        seen.append((cmd, dict(env or {})))
        Path((env or {})["COVERAGE_FILE"]).write_bytes(b"data")
        return 0, ["12.34s call     t.py::test_slow", "9 passed in 3.00s"]

    monkeypatch.setattr(gate, "_run", fake_run)
    ctx = gate.Ctx(root=tmp_path, lane="full", ci=False, base=None, pr=None, shard="2/4")
    out = gate.step_tests(ctx)
    [(cmd, env)] = seen
    assert "--cov" in cmd and "--cov-report=" in cmd and not any(a.startswith("--cov-report=json") for a in cmd)
    # Under coverage the timed tests stay in the one run, as in the whole lane.
    assert "-m" not in cmd[3:] and "--shard=2/4" in cmd
    assert Path(env["COVERAGE_FILE"]) == tmp_path / ".local" / "coverage" / "share-2-of-4"
    assert Path(env[evalfloors.REPORT_ENV]) == tmp_path / evalfloors.REPORT
    assert out.status == "ok"
    assert out.detail == [
        "9 passed in 3.00s",
        "coverage data in .local/coverage/share-2-of-4; `gate coverage` holds the floors on all shares",
        "slowest: 12.34s call     t.py::test_slow",
    ]


def test_a_covered_share_that_fails_or_saves_nothing_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for code, saved in ((1, True), (0, False)):

        def fake_run(
            _ctx: gate.Ctx, cmd: list[str], *, env: dict[str, str] | None = None, code: int = code, saved: bool = saved
        ) -> tuple[int, list[str]]:
            if saved:
                Path((env or {})["COVERAGE_FILE"]).write_bytes(b"data")
            return code, ["a test broke"]

        monkeypatch.setattr(gate, "_run", fake_run)
        out = gate.step_tests(gate.Ctx(root=tmp_path, lane="full", ci=False, base=None, pr=None, shard="1/2"))
        nothing = "the run saved no coverage data to .local/coverage/share-1-of-2"
        assert out.status == "fail"
        assert out.detail == (["a test broke"] if code else [nothing])


def floors_repo(root: Path) -> None:
    """A root with coverage and eval floors, and the numbers the question set's share saved."""
    kit = root / "tools" / "devkit"
    kit.mkdir(parents=True)
    (kit / "coverage-floors.json").write_text(json.dumps({"app": 100}), encoding="utf-8")
    (kit / "eval-floors.json").write_text(json.dumps({"names": {"recall": 0.5, "mrr": 0.4}}), encoding="utf-8")
    (root / ".local").mkdir()
    (root / evalfloors.REPORT).write_text(json.dumps({"names": {"recall": 0.5, "mrr": 0.4}}), encoding="utf-8")


def _fake_coverage(monkeypatch: pytest.MonkeyPatch, covered: int) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(_ctx: gate.Ctx, cmd: list[str], *, env: dict[str, str] | None = None) -> tuple[int, list[str]]:
        calls.append(cmd)
        if cmd[3] == "json":
            lines = {"covered_lines": covered, "num_statements": 10}
            report = {"files": {"packages/app/src/app/a.py": {"summary": lines, "missing_lines": [4][: 10 - covered]}}}
            Path(cmd[cmd.index("-o") + 1]).write_text(json.dumps(report), encoding="utf-8")
        return 0, []

    monkeypatch.setattr(gate, "_run", fake_run)
    return calls


def test_the_coverage_lane_combines_every_share_and_holds_the_floors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    floors_repo(tmp_path)
    data = tmp_path / ".local" / "coverage"
    data.mkdir()
    for k in (2, 1):
        (data / f"share-{k}-of-2").write_bytes(b"data")
    # What a share's run leaves beside its data is no share's.
    (data / ".coverage.host.1234").write_bytes(b"x")
    calls = _fake_coverage(monkeypatch, covered=10)
    ctx = gate.Ctx(root=tmp_path, lane="coverage", ci=False, base=None, pr=None)
    out = gate.step_floors(ctx)
    combine, report = calls
    assert combine[3:5] == ["combine", "--keep"]
    assert combine[-2:] == [str(data / "share-1-of-2"), str(data / "share-2-of-2")]
    assert report[3] == "json" and report[4] == combine[5]
    assert out.status == "ok"
    assert out.detail[:3] == ["2 shares' coverage combined", "app 100.0%", "eval recall@10/MRR: names 0.50/0.40"]
    # Under its floor, the combined coverage fails and names the lines no share ran.
    _fake_coverage(monkeypatch, covered=9)
    out = gate.step_floors(ctx)
    assert out.status == "fail"
    assert out.detail == ["app: 90.0% is below its floor of 100%", "  packages/app/src/app/a.py: line 4"]


@pytest.mark.parametrize(
    ("names", "problem"),
    [
        ([], "holds no share's coverage data"),
        (["share-1-of-3", "share-3-of-3"], "no coverage data for share 2 of 3"),
        (["share-1-of-2", "share-1-of-3"], "runs split [2, 3] ways"),
    ],
)
def test_the_coverage_lane_wants_every_share_of_one_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, names: list[str], problem: str
) -> None:
    floors_repo(tmp_path)
    for name in names:
        (tmp_path / ".local" / "coverage").mkdir(exist_ok=True)
        (tmp_path / ".local" / "coverage" / name).write_bytes(b"data")
    calls = _fake_coverage(monkeypatch, covered=10)
    out = gate.step_floors(gate.Ctx(root=tmp_path, lane="coverage", ci=False, base=None, pr=None))
    assert out.status == "fail" and problem in out.detail[0]
    assert calls == []


def test_the_coverage_lane_fails_a_floor_below_the_merge_base_s(
    repo: Path, git: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    floors_repo(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Floors")
    ctx = gate.Ctx(root=repo, lane="coverage", ci=False, base=git(repo, "rev-parse", "HEAD").strip(), pr=None)
    (repo / coverage.FLOORS).write_text(json.dumps({"app": 90}), encoding="utf-8")
    (repo / ".local" / "coverage").mkdir()
    (repo / ".local" / "coverage" / "share-1-of-1").write_bytes(b"data")
    _fake_coverage(monkeypatch, covered=9)
    # The coverage holds the lowered floor; the floor itself doesn't hold against the base's.
    out = gate.step_floors(ctx)
    assert out.status == "fail"
    assert out.detail == [
        "app: floor lowered from 100% to 90%; name the decided decision that sets it under $lowered in "
        f"{coverage.FLOORS} (DR-0755)"
    ]
    (repo / coverage.FLOORS).write_text(json.dumps({"app": 100}), encoding="utf-8")
    _fake_coverage(monkeypatch, covered=10)
    assert gate.step_floors(ctx).status == "ok"


def test_a_failed_combine_fails_the_lane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    floors_repo(tmp_path)
    (tmp_path / ".local" / "coverage").mkdir()
    (tmp_path / ".local" / "coverage" / "share-1-of-1").write_bytes(b"data")
    monkeypatch.setattr(gate, "_run", lambda _ctx, _cmd, **_: (1, ["Couldn't combine"]))
    out = gate.step_floors(gate.Ctx(root=tmp_path, lane="coverage", ci=False, base=None, pr=None))
    assert out.status == "fail" and out.detail == ["Couldn't combine"]


def test_skip_leaves_a_step_out_of_the_lane(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran: list[str] = []

    def step(name: str) -> gate.Step:
        return gate.Step(name, ("full",), "", lambda _ctx: ran.append(name) or gate.Outcome.ok())

    monkeypatch.setattr(gate, "STEPS", [step("types"), step("tests")])
    assert gate.main(["full", "--skip", "tests"]) == 0
    assert ran == ["types"]
    assert "gate full: passed" in capsys.readouterr().out
    with pytest.raises(KitError, match="no step nope"):
        gate.run("full", skip=["nope"])


def test_weights_folds_a_run_s_times_into_the_weights_file(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "tools" / "devkit").mkdir(parents=True)
    times = tmp_path / "times" / "times-linux-1"
    times.mkdir(parents=True)
    record = {"system": "Linux", "share": "1/1", "marks": "", "workers": 4, "files": {"a.py": 2.0}}
    (times / "Linux-1-of-1-tests.json").write_text(json.dumps(record), encoding="utf-8")
    assert gate.main(["weights", str(tmp_path / "times")]) == 0
    written = json.loads((repo / "tools" / "devkit" / "test-weights.json").read_text(encoding="utf-8"))
    assert written["Linux"] == {"a.py": 2.0}
    assert "Linux: 1 files timed, 2 s; 0 kept as they were" in capsys.readouterr().out
    assert gate.main(["weights"]) == 2
    assert gate.main(["weights", str(tmp_path / "nowhere")]) == 2


def test_a_failure_in_either_run_shows_its_tail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for codes, broke in (((1, 1), ["a test broke", "a timed test broke"]), ((0, 1), ["a timed test broke"])):
        _fake_pytest(monkeypatch, codes=codes)
        out = gate.step_tests(gate.Ctx(root=tmp_path, lane="os", ci=False, base=None, pr=None))
        assert out.status == "fail"
        assert [ln for ln in out.detail if "broke" in ln] == broke


def test_a_share_with_no_timed_test_passes_but_one_with_no_test_at_all_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_pytest(monkeypatch, codes=(0, gate.NO_TESTS))
    out = gate.step_tests(gate.Ctx(root=tmp_path, lane="os", ci=False, base=None, pr=None, shard="9/12"))
    assert out.status == "ok"
    assert out.detail[:2] == ["9 passed, 2 deselected in 3.00s", "timed alone: none in these files"]
    _fake_pytest(monkeypatch, codes=(gate.NO_TESTS, 0))
    out = gate.step_tests(gate.Ctx(root=tmp_path, lane="os", ci=False, base=None, pr=None, shard="9/12"))
    assert out.status == "fail"


def test_each_step_gives_both_runs_one_templates_folder_and_the_timed_run_a_temp_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[Path, list[Path], list[str]]] = []

    def fake_run(_ctx: gate.Ctx, cmd: list[str], *, env: dict[str, str] | None = None) -> tuple[int, list[str]]:
        templates = Path((env or {})[gate.TEMPLATES_ENV])
        basetemps = [Path(a.removeprefix("--basetemp=")) for a in cmd if a.startswith("--basetemp=")]
        built = sorted(p.name for p in templates.iterdir()) if templates.exists() else []
        seen.append((templates, basetemps, built))
        # What a run leaves: a template's git objects, which git makes read-only, and its temp folder.
        templates.mkdir(exist_ok=True)
        (templates / f"object-{len(seen)}").write_bytes(b"x")
        (templates / f"object-{len(seen)}").chmod(0o444)
        for basetemp in basetemps:
            basetemp.mkdir()
        return 0, ["1 passed in 1.00s"]

    monkeypatch.setattr(gate, "_run", fake_run)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    for _ in range(2):
        assert gate.step_tests(gate.Ctx(root=tmp_path, lane="os", ci=True, base=None, pr=None)).status == "ok"
    parallel, timed, again, _ = seen
    # One templates folder for both runs: the timed run finds what the parallel run built.
    assert parallel[0] == timed[0] and (parallel[2], timed[2]) == ([], ["object-1"])
    # The parallel run keeps pytest's numbered folder; the timed run takes none, so it never deletes the other's.
    assert parallel[1] == [] and [b.parent for b in timed[1]] == [parallel[0].parent]
    # Each step starts empty and removes its folder.
    assert again[0] != parallel[0] and again[2] == []
    assert not parallel[0].parent.exists() and not again[0].parent.exists()


def test_under_coverage_the_timed_tests_stay_in_the_one_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    envs: list[dict[str, str]] = []
    calls = _fake_pytest(monkeypatch, codes=(1, 0), envs=envs)
    ctx = gate.Ctx(root=tmp_path, lane="full", ci=False, base=None, pr=None)
    # Their limits are off under coverage, so nothing waits for the other steps either.
    ctx.alone.clear()
    out = gate.step_tests(ctx)
    [run] = calls
    # One run builds its own templates, as on main.
    assert gate.TEMPLATES_ENV not in envs[0]
    assert "-m" not in run[3:] and run[run.index("-n") + 1] == "auto"
    assert "--cov" in run and any(a.startswith("--cov-report=json:") for a in run) and "--cov-append" not in run
    # Its one run is the lane's one share, so the shard plugin runs as it does in each of CI's covered shares.
    assert "--shard=1/1" in run and any(a.endswith("-1-of-1-tests.json") for a in run)
    assert out.status == "fail" and "a test broke" in out.detail


# What models-lock compiles, and how it leaves out a release without a wheel for a system: test_pins.py.


def test_models_lock_says_why_it_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import subprocess

    from devkit import cli

    monkeypatch.setattr(cli, "repo_root", lambda: tmp_path)
    (tmp_path / cli.MODELS).mkdir(parents=True)
    assert cli.main(["models-lock"]) == 2
    assert "no requirements-*.in" in capsys.readouterr().err
    (tmp_path / cli.MODELS / "requirements-cpu.in").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **_: subprocess.CompletedProcess(cmd, 1, "", "no solution"))
    assert cli.main(["models-lock"]) == 2
    assert "no solution" in capsys.readouterr().err


def test_the_models_lane_needs_the_models_and_holds_their_floors(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from devkit import evalfloors, gate

    ctx = gate.Ctx(root=repo, lane="models", ci=False, base=None, pr=None)
    monkeypatch.delenv(gate.MODELS_ENV, raising=False)
    assert gate.step_models(ctx).status == "fail"
    monkeypatch.setenv(gate.MODELS_ENV, str(repo / "models"))
    monkeypatch.setattr(gate, "cpu_name", lambda: "a test CPU")
    floors = repo / evalfloors.FLOORS_MODELS
    floors.parent.mkdir(parents=True)
    floors.write_text(json.dumps({"$above": "print", "typo": {"recall": 0.5, "mrr": 0.4}}), encoding="utf-8")
    (repo / evalfloors.FLOORS_RERANK).write_text(
        json.dumps({"$above": "print", "typo": {"recall": 0.9, "mrr": 0.7}}), encoding="utf-8"
    )

    def measured(embedder: float, reranked: float) -> Any:
        def ran(_ctx: gate.Ctx, cmd: list[str], *, env: dict[str, str] | None = None) -> tuple[int, list[str]]:
            # Both runs of the set, the embedder alone and the reranker on, each writing where the lane asks.
            assert env is not None and cmd[-2:] == ["-k", "embedder or reranker"]
            Path(env[evalfloors.REPORT_MODELS_ENV]).write_text(
                json.dumps({"typo": {"recall": embedder, "mrr": 0.4}}), encoding="utf-8"
            )
            Path(env[evalfloors.REPORT_RERANK_ENV]).write_text(
                json.dumps({"typo": {"recall": reranked, "mrr": 0.7}}), encoding="utf-8"
            )
            return 0, ["2 passed"]

        return ran

    monkeypatch.setattr(gate, "_run", measured(0.5, 0.9))
    out = gate.step_models(ctx)
    assert out.status == "ok" and "typo 0.50/0.40" in out.detail[0]
    assert out.detail[1] == "reranked on a test CPU: eval recall@10/MRR: typo 0.90/0.70"
    monkeypatch.setattr(gate, "_run", lambda _ctx, cmd, *, env=None: (1, ["a test failed"]))
    assert gate.step_models(ctx).status == "fail"
    monkeypatch.setattr(gate, "_run", measured(0.4, 0.9))
    assert gate.step_models(ctx).status == "fail"
    monkeypatch.setattr(gate, "_run", measured(0.5, 0.8))
    out = gate.step_models(ctx)
    assert out.status == "fail" and "eval typo recall: 0.80 is below its floor of 0.90" in out.detail


def test_the_cpu_is_named_from_its_system(monkeypatch: pytest.MonkeyPatch) -> None:
    import platform

    from devkit import gate

    name = gate.cpu_name()
    assert name and isinstance(name, str)
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    monkeypatch.setattr(platform, "processor", lambda: "")
    monkeypatch.setattr(platform, "machine", lambda: "")
    assert gate.cpu_name() == "an unknown CPU"
