from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from devkit import commits, content, coverage, gate
from devkit.cli import cmd_commit_msg


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
            "packages/app/src/app/a.py": {"summary": {"covered_lines": 9, "num_statements": 10}},
            "tools/kit/src/kit/b.py": {"summary": {"covered_lines": 5, "num_statements": 10}},
            "tests/test_x.py": {"summary": {"covered_lines": 1, "num_statements": 1}},
        }
    }
    measures = coverage.measure(report)
    assert set(measures) == {"app", "kit"}
    failures, rise = coverage.judge(measures, {"app": 80, "kit": 60, "gone": 10})
    assert failures == ["kit: 50.0% is below its floor of 60%", "gone: has a floor but nothing was measured"]
    assert rise == ["app: 90.0%, floor 80%: raise it to 90"]


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
