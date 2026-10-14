from __future__ import annotations

import runpy
import shutil
import sys
from pathlib import Path

import pytest
from docket_testkit import make_record, sh

from docket import __version__, userconfig
from docket.cli import main
from docket.errors import DocketError
from docket.vault.vault import Vault
from docket.vault.wizard import Given, Visibility, Wizard

FLAGS = [
    "--owner", "Ada Example", "--email", "ada@example.invalid", "--language", "en", "--language", "hu",
    "--time-zone", "Europe/Budapest", "--machine", "test-box", "--source", "chat", "--source", "calls=mixed",
]  # fmt: skip


class Script:
    """A prompter that answers from a script and records what the wizard said."""

    def __init__(self, answers: list[str] | None = None, confirms: list[bool] | None = None) -> None:
        self.answers = list(answers or [])
        self.confirms = list(confirms or [])
        self.said: list[str] = []

    def ask(self, question: str, default: str | None = None) -> str:
        return self.answers.pop(0) if self.answers else (default or "")

    def confirm(self, question: str, default: bool = False) -> bool:
        return self.confirms.pop(0) if self.confirms else default

    def say(self, text: str) -> None:
        self.said.append(text)


def given(path: Path, **extra: object) -> Given:
    base: dict[str, object] = {
        "path": path,
        "owner": "Ada Example",
        "emails": ["ada@example.invalid"],
        "languages": ["en"],
        "time_zone": "Europe/Budapest",
        "machine": "test-box",
        "sources": ["chat"],
        "remote": "none",
    }
    base.update(extra)
    return Given(**base)  # type: ignore[arg-type]


def parent_repo(tmp_path: Path) -> Path:
    parent = tmp_path / "work"
    parent.mkdir()
    sh(parent, "git", "init", "-q", "-b", "main")
    (parent / "notes.md").write_text("my notes\n", encoding="utf-8")
    sh(parent, "git", "add", "-A")
    sh(parent, "git", "-c", "user.name=Ada", "-c", "user.email=ada@example.invalid", "commit", "-qm", "notes")
    return parent


def always(answer: Visibility) -> object:
    return lambda _url: answer


# -- the command -----------------------------------------------------------------------------------------------------


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"docket {__version__}"


def test_no_arguments_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    main([])
    assert "init" in capsys.readouterr().out


def test_python_dash_m_runs_the_command(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["docket", "--version"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("docket", run_name="__main__")
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.startswith("docket ")


def test_init_status_and_check_from_flags(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "vault"
    assert main(["init", "--yes", "--path", str(path), "--remote", "none", *FLAGS]) == 0
    vault = Vault.open(path)
    assert set(vault.config.sources) == {"chat", "calls"}
    assert vault.config.sources["calls"].sensitivity == "mixed"
    vault.write_run("chat", [make_record(1)])
    capsys.readouterr()
    assert main(["vault", "status", "--vault", str(path)]) == 0
    assert "records  chat: 1" in capsys.readouterr().out
    assert main(["vault", "check", "--vault", str(path)]) == 0
    assert "vault ok" in capsys.readouterr().out


def test_check_exits_1_on_a_problem(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "vault"
    main(["init", "--yes", "--path", str(path), "--remote", "none", *FLAGS])
    (path / "raw" / "stray.txt").write_text("x", encoding="utf-8")
    assert main(["vault", "check", "--vault", str(path)]) == 1
    assert "isn't a run file" in capsys.readouterr().out


def test_an_error_is_one_line_and_exit_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["vault", "status", "--vault", str(tmp_path)]) == 2
    assert capsys.readouterr().err.startswith("docket: no vault at or above")


def test_the_vault_is_found_from_here_a_child_env_and_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "vault"
    main(["init", "--yes", "--path", str(path), "--remote", "none", *FLAGS])
    assert userconfig.default_vault() == path.resolve()
    for where in (tmp_path, path / "raw", tmp_path / "elsewhere"):
        where.mkdir(exist_ok=True)
        monkeypatch.chdir(where)
        assert main(["vault", "status"]) == 0
    monkeypatch.setenv("DOCKET_VAULT", str(path))
    assert main(["vault", "status"]) == 0
    assert str(path.resolve()) in capsys.readouterr().out


def test_with_no_vault_anywhere_the_command_says_how_to_make_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["vault", "status"]) == 2
    assert "docket init" in capsys.readouterr().err


# -- the wizard ------------------------------------------------------------------------------------------------------


def test_the_wizard_asks_and_shows_a_summary_first(tmp_path: Path) -> None:
    answers = [str(tmp_path / "v"), "Ben Example", "ben@example.invalid", "en, hu", "Europe/Budapest", "ben-box"]
    script = Script(answers=[*answers, "chat, calls=mixed", "none"], confirms=[True])
    vault = Wizard(script, Given()).run()
    assert vault.config.owner.name == "Ben Example"
    assert vault.config.owner.languages == ["en", "hu"]
    assert vault.config.primary_machine == "ben-box"
    assert "machine:  ben-box (primary)" in script.said[0]


def test_nothing_is_written_before_the_confirmation(tmp_path: Path) -> None:
    with pytest.raises(DocketError, match="nothing created"):
        Wizard(Script(confirms=[False]), given(tmp_path / "v")).run()
    assert not (tmp_path / "v").exists()


def test_a_nested_vault_ignores_itself_in_the_parent(tmp_path: Path) -> None:
    parent = parent_repo(tmp_path)
    Wizard(Script(), given(parent / "vault", yes=True)).run()
    assert "/vault/" in (parent / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert (parent / ".ignore").read_text(encoding="utf-8").splitlines() == [
        "!/vault/",
        "/vault/*",
        "!/vault/processed/",
    ]
    assert sh(parent, "git", "status", "--porcelain").split() == ["??", ".gitignore", "??", ".ignore"]
    rg = shutil.which("rg")
    if rg:
        (parent / "vault" / "processed" / "doc.md").write_text("x\n", encoding="utf-8")
        listed = [f.replace("\\", "/") for f in sh(parent, rg, "--files").split()]
        assert "vault/processed/doc.md" in listed
        assert not any(f.startswith("vault/raw") for f in listed)


def test_a_dirty_parent_is_refused(tmp_path: Path) -> None:
    parent = parent_repo(tmp_path)
    (parent / "notes.md").write_text("changed\n", encoding="utf-8")
    with pytest.raises(DocketError, match="uncommitted changes"):
        Wizard(Script(), given(parent / "vault", yes=True)).run()
    assert not (parent / "vault").exists()
    Wizard(Script(), given(parent / "vault", yes=True, allow_dirty_parent=True)).run()
    assert (parent / "vault" / "vault.yaml").is_file()


def test_a_folder_the_parent_tracks_is_refused(tmp_path: Path) -> None:
    parent = parent_repo(tmp_path)
    (parent / "old").mkdir()
    (parent / "old" / "a.md").write_text("x\n", encoding="utf-8")
    sh(parent, "git", "add", "-A")
    sh(parent, "git", "-c", "user.name=Ada", "-c", "user.email=ada@example.invalid", "commit", "-qm", "old")
    with pytest.raises(DocketError, match="already tracks files under old"):
        Wizard(Script(), given(parent / "old", yes=True)).run()


def test_a_public_remote_is_refused(tmp_path: Path) -> None:
    url = "https://example.invalid/r.git"
    with pytest.raises(DocketError, match="public repo"):
        Wizard(Script(), given(tmp_path / "v", yes=True, remote=url), always("public")).run()  # type: ignore[arg-type]
    assert not (tmp_path / "v").exists()


def test_a_remote_docket_cant_check_needs_the_user_s_word(tmp_path: Path) -> None:
    url = "git@example.invalid:me/vault.git"
    unknown = always("unknown")
    with pytest.raises(DocketError, match="remote-is-private"):
        Wizard(Script(), given(tmp_path / "a", yes=True, remote=url), unknown).run()  # type: ignore[arg-type]
    asked = Wizard(Script(confirms=[True, True]), given(tmp_path / "b", remote=url), unknown).run()  # type: ignore[arg-type]
    assert asked.git.has_remote()
    flagged = given(tmp_path / "c", yes=True, remote=url, remote_is_private=True)
    assert Wizard(Script(), flagged, unknown).run().git.has_remote()  # type: ignore[arg-type]


def test_a_local_remote_is_fine(tmp_path: Path) -> None:
    sh(tmp_path, "git", "init", "-q", "--bare", "r.git")
    vault = Wizard(Script(), given(tmp_path / "v", yes=True, remote=str(tmp_path / "r.git"))).run()
    assert vault.git.has_remote()


def test_remote_visibility_fails_closed() -> None:
    from docket.vault.wizard import remote_visibility

    assert remote_visibility("git@example.invalid:me/v.git") == "unknown"
    assert remote_visibility("ssh://git@example.invalid/me/v.git") == "unknown"
    assert remote_visibility("https://example.invalid/nobody/nothing.git") == "unknown"
    assert remote_visibility(".") == "local"


def test_github_needs_the_github_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("docket.vault.wizard._gh_ready", lambda: False)
    with pytest.raises(DocketError, match="GitHub CLI"):
        Wizard(Script(), given(tmp_path / "v", yes=True, remote="github")).run()


def test_yes_without_a_default_asks_for_the_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("docket.vault.wizard.local_time_zone", lambda: None)
    with pytest.raises(DocketError, match="give it as a flag"):
        Wizard(Script(), given(tmp_path / "v", yes=True, time_zone=None)).run()


def test_a_bad_answer_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    with pytest.raises(DocketError, match="IANA"):
        Wizard(Script(), given(tmp_path / "v", yes=True, time_zone="Central Europe Daylight Time")).run()
    with pytest.raises(DocketError, match="sensitivity"):
        Wizard(Script(), given(tmp_path / "v", yes=True, sources=["chat=secret"])).run()
    assert not (tmp_path / "v").exists()


def test_a_nested_vault_is_found_from_the_parent_s_root_without_a_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    parent = parent_repo(tmp_path)
    Wizard(Script(), given(parent / "vault", yes=True)).run()
    monkeypatch.setenv("DOCKET_CONFIG_DIR", str(tmp_path / "no-config"))
    assert userconfig.default_vault() is None
    monkeypatch.chdir(parent)
    assert main(["vault", "status"]) == 0
    assert str((parent / "vault").resolve()) in capsys.readouterr().out
