"""`docket init` inside a repo: a folder of it by default, written only inside the vault's folder, refused or warned
by the host checks, and a repo of its own always offered."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from docket_testkit import git, host_repo, sh, users_work

from docket.cli import main
from docket.errors import DocketError
from docket.vault import git as gitmod
from docket.vault.host import Visibility
from docket.vault.vault import Vault
from docket.vault.wizard import Given, Wizard


class Script:
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
        "yes": True,
    }
    base.update(extra)
    return Given(**base)  # type: ignore[arg-type]


def seen_as(answer: Visibility) -> object:
    return lambda _url: answer


def local(_url: str) -> Visibility:
    return "local"


def test_inside_a_repo_the_vault_is_a_folder_of_it(tmp_path: Path) -> None:
    top = host_repo(tmp_path)
    (top / "README.md").write_text("hello, staged\n", encoding="utf-8")
    git(top, "add", "README.md")
    (top / "other.txt").write_text("other, unstaged\n", encoding="utf-8")
    before = users_work(top)
    script = Script()
    vault = Wizard(script, given(top / "notes" / "vault"), local).run()
    assert vault.config.storage == "folder"
    assert "storage:  a folder of" in script.said[0]
    files = git(top, "show", "--name-only", "--format=", "HEAD").split()
    assert files
    assert all(f.startswith("notes/vault/") for f in files)
    assert "notes/vault/.ignore" in files
    assert users_work(top) == before
    status = [line[3:] for line in git(top, "status", "--porcelain", "--ignored").splitlines()]
    assert sorted(status) == ["README.md", "other.txt"]
    attrs = git(top, "check-attr", "filter", "--", "notes/vault/media/x.age")
    assert attrs.strip().endswith("filter: lfs")
    assert not (top / ".gitattributes").exists()
    rg = shutil.which("rg")
    if rg:
        (vault.root / "processed" / "doc.md").write_text("x\n", encoding="utf-8")
        listed = [f.replace("\\", "/") for f in sh(top, rg, "--files").split()]
        assert "notes/vault/processed/doc.md" in listed
        assert not any(f.startswith("notes/vault/raw") for f in listed)


def test_a_public_host_remote_refuses_a_folder_and_offers_a_repo(tmp_path: Path) -> None:
    top = host_repo(tmp_path)
    script = Script()
    vault = Wizard(script, given(top / "vault", remote="none"), seen_as("public")).run()  # type: ignore[arg-type]
    assert vault.config.storage == "repo"
    assert any("Folder mode isn't possible here" in line for line in script.said)
    with pytest.raises(DocketError, match="isn't possible"):
        Wizard(Script(), given(top / "v2", storage="folder"), seen_as("public")).run()  # type: ignore[arg-type]
    with pytest.raises(DocketError, match="isn't possible"):
        Wizard(Script(answers=["folder"]), given(top / "v3", yes=False), seen_as("public")).run()  # type: ignore[arg-type]
    assert not (top / "v2").exists()


def test_a_host_remote_docket_cant_check_needs_the_user_s_word(tmp_path: Path) -> None:
    top = host_repo(tmp_path)
    unknown = seen_as("unknown")
    with pytest.raises(DocketError, match="remote-is-private"):
        Wizard(Script(), given(top / "a"), unknown).run()  # type: ignore[arg-type]
    asked = Wizard(Script(confirms=[True, True]), given(top / "b", yes=False), unknown).run()  # type: ignore[arg-type]
    assert asked.config.storage == "folder"
    flagged = Wizard(Script(), given(top / "c", remote_is_private=True), unknown).run()  # type: ignore[arg-type]
    assert flagged.config.storage == "folder"


def test_a_shared_host_warns_and_defaults_to_a_repo(tmp_path: Path) -> None:
    top = host_repo(tmp_path)
    (top / "theirs.txt").write_text("x\n", encoding="utf-8")
    git(top, "add", "-A")
    git(top, "commit", "-qm", "theirs", "--author=Ben Sample <ben@example.invalid>")
    script = Script()
    vault = Wizard(script, given(top / "a", remote="none"), local).run()
    assert vault.config.storage == "repo"
    assert any("ben@example.invalid also commit" in line for line in script.said)
    with pytest.raises(DocketError, match="allow-shared"):
        Wizard(Script(), given(top / "b", storage="folder"), local).run()
    allowed = Wizard(Script(), given(top / "c", storage="folder", allow_shared=True), local).run()
    assert allowed.config.storage == "folder"
    chosen = Wizard(Script(answers=["folder"], confirms=[True]), given(top / "d", yes=False), local).run()
    assert chosen.config.storage == "folder"


def summary_of(script: Script) -> str:
    return next(line for line in script.said if line.startswith("vault:    "))


def test_the_summary_names_each_override_a_flag_let_through(tmp_path: Path) -> None:
    top = host_repo(tmp_path)
    plain = Script()
    # A flag given where nothing needed it is no override.
    Wizard(plain, given(top / "a", storage="folder", allow_shared=True), local).run()
    assert "override:" not in summary_of(plain)
    (top / "theirs.txt").write_text("x\n", encoding="utf-8")
    git(top, "add", "-A")
    git(top, "commit", "-qm", "theirs", "--author=Ben Sample <ben@example.invalid>")
    script = Script()
    flags = given(top / "b", storage="folder", allow_shared=True, remote_is_private=True)
    Wizard(script, flags, seen_as("unknown")).run()  # type: ignore[arg-type]
    overrides = [line for line in summary_of(script).splitlines() if line.startswith("override: ")]
    assert overrides == [
        f"override: --allow-shared: {top} looks shared",
        f"override: --remote-is-private: Docket can't check {tmp_path / 'origin.git'} itself",
    ]


def test_no_sources_starts_with_none_and_never_beside_a_source(tmp_path: Path) -> None:
    top = host_repo(tmp_path)
    script = Script()
    vault = Wizard(script, given(top / "a", sources=[], no_sources=True), local).run()
    assert vault.config.sources == {}
    assert "sources:  none yet" in summary_of(script)
    with pytest.raises(DocketError, match="don't go together"):
        Wizard(Script(), given(top / "b", no_sources=True), local).run()
    assert not (top / "b").exists()
    assert main(["init", "--yes", "--path", str(top / "c"), "--no-sources", "--source", "chat"]) == 2
    assert not (top / "c").exists()


def test_a_host_rule_that_ignores_the_vault_refuses_a_folder(tmp_path: Path) -> None:
    top = host_repo(tmp_path)
    (top / ".gitignore").write_text("*.jsonl\n", encoding="utf-8")
    with pytest.raises(DocketError, match=r"ignore rules hide raw/s/2026/01/2026-01-01T0000-m\.jsonl"):
        Wizard(Script(), given(top / "vault", storage="folder"), local).run()


def test_storage_answers_are_checked(tmp_path: Path) -> None:
    with pytest.raises(DocketError, match="--storage is folder or repo"):
        Wizard(Script(), given(tmp_path / "v", storage="cloud"), local).run()
    with pytest.raises(DocketError, match="isn't inside a git repo"):
        Wizard(Script(), given(tmp_path / "v", storage="folder"), local).run()
    top = host_repo(tmp_path)
    with pytest.raises(DocketError, match="a folder or a repo"):
        Wizard(Script(answers=["somewhere"]), given(top / "v", yes=False), local).run()


def test_a_failed_folder_init_leaves_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top = host_repo(tmp_path)
    real = gitmod.Git.run

    def failing(self: gitmod.Git, *args: str, **kw: object) -> object:
        if "commit" in args:
            raise DocketError("git commit: the disk is full")
        return real(self, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(gitmod.Git, "run", failing)
    with pytest.raises(DocketError, match="disk is full"):
        Wizard(Script(), given(top / "notes" / "vault"), local).run()
    monkeypatch.undo()
    assert not (top / "notes" / "vault").exists()
    assert git(top, "status", "--porcelain") == ""
    assert not (top / ".git" / "docket" / "notes" / "vault").exists()


def test_what_git_lfs_and_the_attributes_need_is_said(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top = host_repo(tmp_path)
    (top / ".git" / "info").mkdir(exist_ok=True)
    (top / ".git" / "info" / "attributes").write_text("*.age -filter\n", encoding="utf-8")
    monkeypatch.setattr("docket.vault.host.shutil.which", lambda _name: None)
    script = Script()
    Wizard(script, given(top / "vault"), local).run()
    assert "git-lfs: not installed" in "\n".join(script.said)
    assert any(line.startswith("media/s/2026/01/x.age: git gives it filter: unset") for line in script.said)


def test_init_from_the_command_line_in_a_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    top = host_repo(tmp_path)
    monkeypatch.setattr("docket.vault.wizard.remote_visibility", local)
    monkeypatch.setattr("docket.vault.wizard.Wizard.__init__.__defaults__", (local,))
    flags = ["--owner", "Ada Example", "--email", "ada@example.invalid", "--time-zone", "Europe/Budapest"]
    path = top / "notes" / "vault"
    args = ["init", "--yes", "--path", str(path), "--storage", "folder", "--machine", "test-box", *flags]
    assert main(args) == 0
    assert Vault.open(path).config.storage == "folder"
    capsys.readouterr()
    assert main(["vault", "status", "--vault", str(path)]) == 0
    assert f"a folder of {top}" in capsys.readouterr().out
