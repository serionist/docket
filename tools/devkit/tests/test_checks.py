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
