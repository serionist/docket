"""The less travelled paths: failures, missing tools, and the interactive prompts."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import empty_vault, make_config, make_record, sh
from filelock import FileLock

from docket import userconfig
from docket.cli import TyperPrompter, main
from docket.errors import DocketError
from docket.vault import wizard
from docket.vault.config import Owner, RedactionPattern, VaultConfig
from docket.vault.git import Git, toplevel
from docket.vault.lock import VaultLock
from docket.vault.rawlog import parse_run_file
from docket.vault.records import canonical_json
from docket.vault.wizard import Given, Wizard

RUN_AT = datetime(2026, 10, 14, 15, 50, tzinfo=UTC)


def test_a_bad_redaction_pattern_and_machine_name_are_refused() -> None:
    with pytest.raises(ValueError, match="regular expression"):
        RedactionPattern(name="x", pattern="(unclosed")
    assert RedactionPattern(name="card", pattern=r"\d{16}").pattern == r"\d{16}"
    with pytest.raises(ValueError, match="machine name"):
        VaultConfig(owner=Owner(name="A", time_zone="UTC"), primary_machine="Bad Name")


def test_a_user_config_that_isnt_a_map_reads_as_empty() -> None:
    userconfig.config_path().parent.mkdir(parents=True, exist_ok=True)
    userconfig.config_path().write_text("- a list\n", encoding="utf-8")
    assert userconfig.load() == {}
    assert userconfig.default_vault() is None


def test_a_failing_git_command_is_one_docket_error(tmp_path: Path) -> None:
    with pytest.raises(DocketError, match="git log"):
        Git(tmp_path).out("log")


def test_without_git_lfs_the_vault_still_starts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    assert not Git(tmp_path).lfs_install()


def test_a_commit_with_nothing_new_is_none(tmp_path: Path) -> None:
    vault = empty_vault(tmp_path, make_config(), "v")
    assert vault.commit([], "nothing") is None


def test_a_repo_without_commits_has_nothing_changed(tmp_path: Path) -> None:
    sh(tmp_path, "git", "init", "-q")
    assert Git(tmp_path).changed_against_head("raw") == []


def test_toplevel_of_a_path_that_doesnt_exist_yet(tmp_path: Path) -> None:
    sh(tmp_path, "git", "init", "-q")
    assert toplevel(tmp_path / "a" / "b") == tmp_path.resolve()
    assert toplevel(tmp_path.parent) != tmp_path.resolve()


def test_a_held_lock_times_out_with_a_clear_message(tmp_path: Path) -> None:
    sh(tmp_path, "git", "init", "-q")
    held = FileLock(str(tmp_path / ".git" / "docket.lock"))
    with held, pytest.raises(DocketError, match="another docket process"), VaultLock(tmp_path / ".git", timeout=0.1):
        pass


def test_a_file_outside_raw_is_no_run_file(tmp_path: Path) -> None:
    assert parse_run_file(tmp_path / "raw", tmp_path / "elsewhere" / "2026-10-14T1550-box.jsonl") is None


def test_blank_lines_in_a_run_file_are_skipped(tmp_path: Path) -> None:
    vault = empty_vault(tmp_path, make_config(), "v")
    folder = vault.root / "raw" / "chat" / "2026" / "10"
    folder.mkdir(parents=True)
    line = make_record(1).model_copy(update={"v": "sha256:x"}).to_json()
    (folder / "2026-10-14T1550-test-box.jsonl").write_text(f"\n{line}\n\n", encoding="utf-8")
    assert vault.check() == []
    assert vault.status().records["chat"] == 1
    assert len(vault.rawlog.read(make_record(1).rid)) == 1


def test_carriage_returns_are_escaped() -> None:
    assert canonical_json("a\rb") == '"a\\rb"'


def test_status_reports_overlaps_and_uncommitted_runs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = empty_vault(tmp_path, make_config(), "v")
    vault.rawlog.write_run("chat", [make_record(1)], RUN_AT)
    copy = next((vault.root / "raw").rglob("*.jsonl"))
    (copy.parent / "2026-10-14T1551-other-box.jsonl").write_text(copy.read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["vault", "status", "--vault", str(vault.root)]) == 0
    out = capsys.readouterr().out
    assert "records in more than one run (two machines overlapped): 1" in out
    assert "uncommitted run files: 2" in out


def test_the_typer_prompter_asks_confirms_and_says(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("typer.prompt", lambda question, default=None: f"{question}:{default}")
    monkeypatch.setattr("typer.confirm", lambda question, default=False: not default)
    said: list[str] = []
    monkeypatch.setattr("typer.echo", said.append)
    p = TyperPrompter()
    assert p.ask("Name", "Ada") == "Name:Ada"
    assert p.confirm("Sure?", False) is True
    p.say("hi")
    assert said == ["hi"]


def test_a_broken_time_zone_lookup_means_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> str:
        raise RuntimeError("no zone")

    monkeypatch.setattr("tzlocal.get_localzone_name", broken)
    assert wizard.local_time_zone() is None


def test_a_remote_that_answers_anonymously_is_public(monkeypatch: pytest.MonkeyPatch) -> None:
    def ok(*_: Any, **__: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(subprocess, "run", ok)
    assert wizard.remote_visibility("https://example.invalid/open.git") == "public"

    def slow(*_: Any, **__: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("git", 30)

    monkeypatch.setattr(subprocess, "run", slow)
    assert wizard.remote_visibility("https://example.invalid/slow.git") == "unknown"


def test_gh_is_ready_only_when_signed_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    assert not wizard._gh_ready()  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr("shutil.which", lambda _name: "gh")
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: subprocess.CompletedProcess([], 0))
    assert wizard._gh_ready()  # pyright: ignore[reportPrivateUsage]


def _given(path: Path, **extra: Any) -> Given:
    base: dict[str, Any] = {
        "path": path, "owner": "Ada", "emails": ["ada@example.invalid"], "languages": ["en"],
        "time_zone": "UTC", "machine": "test-box", "sources": ["chat"], "yes": True,
    }  # fmt: skip
    base.update(extra)
    return Given(**base)


def test_a_new_github_repo_is_created_private(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    real_run = subprocess.run

    def fake(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "gh":
            calls.append(cmd)
            # The first create fails (the name is taken), the second succeeds.
            return subprocess.CompletedProcess(cmd, 1 if len(calls) == 1 else 0, "", "name taken")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(wizard, "_gh_ready", lambda: True)
    monkeypatch.setattr(subprocess, "run", fake)

    class Say:
        said: list[str] = []  # noqa: RUF012 - one instance per test

        def ask(self, question: str, default: str | None = None) -> str:
            return default or ""

        def confirm(self, question: str, default: bool = False) -> bool:
            return default

        def say(self, text: str) -> None:
            self.said.append(text)

    prompter = Say()
    Wizard(prompter, _given(tmp_path / "v", remote="github:my-vault")).run()
    assert calls[0][:4] == ["gh", "repo", "create", "my-vault"]
    assert "--private" in calls[0]
    assert any("GitHub repo isn't" in s for s in prompter.said)
    Wizard(prompter, _given(tmp_path / "w", remote="github", yes=False)).run()
    assert calls[1][3] == "docket-vault"


def test_empty_source_names_are_skipped_and_unknown_ones_are_mixed() -> None:
    sources = wizard._parse_sources(["chat", " ", "newthing", "calls=private"])  # pyright: ignore[reportPrivateUsage]
    assert {k: v.sensitivity for k, v in sources.items()} == {"chat": "mixed", "newthing": "mixed", "calls": "private"}


def test_ignore_lines_are_added_once(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("node_modules/", encoding="utf-8")
    wizard.nest_in_parent(tmp_path, tmp_path / "vault")
    wizard.nest_in_parent(tmp_path, tmp_path / "vault")
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == "node_modules/\n/vault/\n"


def test_yes_takes_the_defaults_and_keeps_the_first_default_vault(tmp_path: Path) -> None:
    class Quiet:
        def ask(self, question: str, default: str | None = None) -> str:
            raise AssertionError("asked under --yes")

        def confirm(self, question: str, default: bool = False) -> bool:
            raise AssertionError("asked under --yes")

        def say(self, text: str) -> None:
            pass

    first = Wizard(Quiet(), _given(tmp_path / "a", languages=[], sources=[], remote=None)).run()
    assert first.config.owner.languages == ["en"]
    assert set(first.config.sources) == set(wizard.KNOWN_SOURCES)
    Wizard(Quiet(), _given(tmp_path / "b", remote=None)).run()
    assert userconfig.default_vault() == (tmp_path / "a").resolve()


def test_a_vault_without_its_machine_file_falls_back_to_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from docket.vault import machine

    monkeypatch.delenv("DOCKET_MACHINE")
    monkeypatch.setattr("socket.gethostname", lambda: "HOST-B")
    (tmp_path / ".git").mkdir()
    assert machine.resolve(tmp_path) == "host-b"


def test_a_rebase_without_its_head_name_is_never_taken_for_docket_s(tmp_path: Path) -> None:
    from docket.vault.vault import _started_with  # pyright: ignore[reportPrivateUsage]

    state = tmp_path / "rebase-apply"
    state.mkdir()
    marker = tmp_path / "sync-rebase"
    marker.write_text("origin/main", encoding="utf-8")
    assert not _started_with(state, marker)
    (state / "head-name").write_text("refs/heads/main", encoding="utf-8")
    assert _started_with(state, marker)


def test_a_read_only_file_is_made_writable_and_deleted(tmp_path: Path) -> None:
    import os
    import stat

    from docket.vault.vault import retry_writable

    target = tmp_path / "object"
    target.write_text("x", encoding="utf-8")
    target.chmod(stat.S_IREAD)
    retry_writable(os.unlink, str(target), PermissionError())
    assert not target.exists()
