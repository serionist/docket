"""The checks of a host repo before a folder vault goes there: who can read its remotes, who else commits, its state,
its ignore rules and git-lfs. None of them touches the network here: visibility is passed in, or the probe is
watched."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import git, host_repo, sh

from docket.vault import host
from docket.vault.host import HostReport, Lfs


def test_ssh_remotes_are_probed_over_https() -> None:
    assert host.probe_url("git@example.invalid:me/vault.git") == "https://example.invalid/me/vault.git"
    assert host.probe_url("ssh://git@example.invalid:2222/me/vault.git") == "https://example.invalid/me/vault.git"
    assert host.probe_url("ssh://example.invalid/me/vault.git") == "https://example.invalid/me/vault.git"
    assert host.probe_url("https://example.invalid/me/vault.git") == "https://example.invalid/me/vault.git"


def test_the_probe_runs_with_the_user_s_config_shut_out(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    real = subprocess.run

    def fake(args: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.update(args=args, cwd=kw["cwd"], env=kw["env"])
        seen["config"] = Path(kw["env"]["GIT_CONFIG_GLOBAL"]).read_text(encoding="utf-8")
        seen["repo"] = real(
            ["git", "rev-parse", "--git-dir"], cwd=kw["cwd"], env=kw["env"], capture_output=True, check=False
        ).returncode
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake)
    assert host.remote_visibility("git@example.invalid:me/vault.git") == "public"
    assert seen["args"][-1] == "https://example.invalid/me/vault.git"
    assert seen["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
    assert seen["config"] == ""
    assert seen["repo"] != 0  # outside any repo


def test_a_probe_that_hangs_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(args: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(args, 30)

    monkeypatch.setattr(subprocess, "run", hang)
    assert host.remote_visibility("https://example.invalid/me/vault.git") == "unknown"
    assert host.remote_visibility(".") == "local"


def test_remote_urls_are_read_as_git_will_use_them(tmp_path: Path) -> None:
    top = host_repo(tmp_path)
    git(top, "config", "url.https://example.invalid/.insteadOf", "short:")
    git(top, "config", "url.ssh://git@example.invalid/.pushInsteadOf", "short:")
    git(top, "remote", "add", "mirror", "short:me/notes.git")
    urls = host.remote_urls(top)
    assert str(tmp_path / "origin.git") in urls
    assert "https://example.invalid/me/notes.git" in urls
    assert "ssh://git@example.invalid/me/notes.git" in urls


def test_other_committers_are_named_and_bots_are_not(tmp_path: Path) -> None:
    top = host_repo(tmp_path)
    for who in ("Ben Sample <ben@example.invalid>", "helper[bot] <1+helper[bot]@users.noreply.github.com>"):
        (top / "x.txt").write_text(who, encoding="utf-8")
        git(top, "add", "-A")
        git(top, "commit", "-qm", "theirs", f"--author={who}")
    assert host.others(top, ["ADA@example.invalid"]) == ["ben@example.invalid"]


def test_state_problems(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    sh(empty, "git", "init", "-q")
    assert host.state_problems(empty) == [f"{empty} has no commit yet; make one first"]
    top = host_repo(tmp_path)
    assert host.state_problems(top) == []
    git(top, "checkout", "-q", "--detach")
    (top / ".git" / "MERGE_HEAD").write_text(git(top, "rev-parse", "HEAD"), encoding="utf-8")
    assert host.state_problems(top) == [
        f"{top} isn't on a branch (a detached HEAD)",
        f"a merge is in progress in {top}",
    ]


def test_lfs_summaries() -> None:
    assert "not installed" in Lfs(False, False, "missing").summary()
    assert Lfs(True, True, "ok").summary() == "set up"
    assert "add `git lfs pre-push" in Lfs(True, True, "foreign").summary()
    assert "git lfs install --local" in Lfs(True, False, "missing").summary()


def test_lfs_state_and_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top = host_repo(tmp_path)
    monkeypatch.setattr("docket.vault.host.shutil.which", lambda _name: None)
    lfs = host.lfs_state(top)
    assert lfs == Lfs(False, False, "missing")
    assert host.setup_lfs(top, lfs) == lfs.summary()
    assert host.setup_lfs(top, Lfs(True, True, "ok")) is None
    hook = top / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    assert host.lfs_state(top).hook == "foreign"
    hook.write_text('#!/bin/sh\ngit lfs pre-push "$@"\n', encoding="utf-8")
    assert host.lfs_state(top).hook == "ok"


@pytest.mark.skipif(shutil.which("git-lfs") is None, reason="needs git-lfs")
def test_setup_lfs_installs_its_hooks_in_the_host(tmp_path: Path) -> None:
    top = host_repo(tmp_path)
    before = host.lfs_state(top)
    assert before.hook == "missing"
    assert host.setup_lfs(top, before) is None
    assert host.lfs_state(top) == Lfs(True, True, "ok")
    hook = top / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    foreign = host.lfs_state(top)
    assert host.setup_lfs(top, foreign) == foreign.summary()


def test_a_failed_lfs_install_is_said(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top = tmp_path / "not-a-repo"
    top.mkdir()
    message = host.setup_lfs(top, Lfs(True, False, "missing"))
    assert message is not None
    assert "failed" in message


def test_check_gathers_every_finding(tmp_path: Path) -> None:
    top = host_repo(tmp_path)
    git(top, "remote", "add", "public", "https://example.invalid/open.git")
    git(top, "remote", "add", "private", "git@example.invalid:me/closed.git")
    (top / ".gitignore").write_text("*.age\n", encoding="utf-8")
    seen: dict[str, host.Visibility] = {
        "https://example.invalid/open.git": "public",
        "git@example.invalid:me/closed.git": "unknown",
    }
    report = host.check(top, "notes/vault/", ["ada@example.invalid"], lambda u: seen.get(u, "local"))
    assert report.branch == "main"
    assert report.public == ["https://example.invalid/open.git"]
    assert report.unknown == ["git@example.invalid:me/closed.git"]
    assert report.ignored == ["media/s/2026/01/x.age (.gitignore:1: *.age)"]
    # The convert's own line, which it takes out, doesn't count.
    (top / ".gitignore").write_text("/notes/vault/\n", encoding="utf-8")
    assert len(host.check(top, "notes/vault/", [], lambda _u: "local").ignored) == 8
    assert host.check(top, "notes/vault/", [], lambda _u: "local", allowed_rule="/notes/vault/").ignored == []
    blocking = report.blocking()
    assert blocking[0].startswith("https://example.invalid/open.git can be read without signing in")
    lines = report.lines()
    assert "(public)" in lines[0]
    assert "you confirm it's private" in lines[0]
    assert HostReport(top, "main").lines()[:2] == ["remotes:  none public", "shared:   no other committers"]


def test_a_host_on_no_branch(tmp_path: Path) -> None:
    top = host_repo(tmp_path)
    git(top, "checkout", "-q", "--detach")
    assert host.check(top, "notes/vault/", [], lambda _u: "local").branch == "(no branch)"
    assert os.environ.get("GIT_CONFIG_NOSYSTEM") == "1"
