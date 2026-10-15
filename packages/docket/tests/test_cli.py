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
from docket.vault.cuts import Cut
from docket.vault.gate import Gate
from docket.vault.records import RawRecord
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
    capsys.readouterr()
    # vault status says how it found the vault, so a setup can tell another project's default vault from its own.
    for where, how in (
        (tmp_path, "./vault"),
        (path / "raw", "this folder or one above it"),
        (tmp_path / "elsewhere", f"the default vault in {userconfig.config_path()}"),
    ):
        where.mkdir(exist_ok=True)
        monkeypatch.chdir(where)
        assert main(["vault", "status"]) == 0
        assert f"found    by {how}\n" in capsys.readouterr().out
    assert main(["vault", "status", "--vault", str(path)]) == 0
    assert "found    by --vault\n" in capsys.readouterr().out
    monkeypatch.setenv("DOCKET_VAULT", str(path))
    assert main(["vault", "status"]) == 0
    out = capsys.readouterr().out
    assert str(path.resolve()) in out
    assert "found    by DOCKET_VAULT\n" in out


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
    Wizard(Script(), given(parent / "vault", yes=True, storage="repo")).run()
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
        Wizard(Script(), given(parent / "vault", yes=True, storage="repo")).run()
    assert not (parent / "vault").exists()
    script = Script()
    Wizard(script, given(parent / "vault", yes=True, storage="repo", allow_dirty_parent=True)).run()
    assert (parent / "vault" / "vault.yaml").is_file()
    assert f"override: --allow-dirty-parent: {parent} has uncommitted changes" in script.said[0].splitlines()


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
    script = Script()
    assert Wizard(script, flagged, unknown).run().git.has_remote()  # type: ignore[arg-type]
    assert f"override: --remote-is-private: Docket can't check {url} itself" in script.said[0].splitlines()


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
    Wizard(Script(), given(parent / "vault", yes=True, storage="repo")).run()
    monkeypatch.setenv("DOCKET_CONFIG_DIR", str(tmp_path / "no-config"))
    assert userconfig.default_vault() is None
    monkeypatch.chdir(parent)
    assert main(["vault", "status"]) == 0
    assert str((parent / "vault").resolve()) in capsys.readouterr().out


def test_the_console_speaks_utf8_through_a_pipe(tmp_path: Path) -> None:
    import os
    import subprocess

    path = tmp_path / "Árvíztűrő vault"
    env = {**os.environ, "PYTHONUTF8": "0", "PYTHONIOENCODING": ""}
    flags = ["init", "--yes", "--path", str(path), "--remote", "none", *FLAGS]
    init = subprocess.run([sys.executable, "-m", "docket", *flags], capture_output=True, env=env, check=False)
    assert init.returncode == 0, init.stderr.decode("utf-8", "replace")
    proc = subprocess.run(
        [sys.executable, "-m", "docket", "vault", "status", "--vault", str(path)],
        capture_output=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0
    assert "Árvíztűrő vault" in proc.stdout.decode("utf-8")


# -- docket staging --------------------------------------------------------------------------------------------------


def staging_vault(tmp_path: Path) -> tuple[Path, Gate]:
    path = tmp_path / "vault"
    flags = ["chat=work" if flag == "chat" else flag for flag in FLAGS]
    main(["init", "--yes", "--path", str(path), "--remote", "none", *flags])
    return path, Gate(Vault.open(path))


def call(n: int, text: str) -> RawRecord:
    return make_record(n, rid=f"calls:rec-{n}", source="calls", kind="recording", text=text)


def test_staging_list_show_classify_and_promote(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, gate = staging_vault(tmp_path)
    v = ["--vault", str(path)]
    assert main(["staging", "list", *v]) == 0
    assert "nothing waits" in capsys.readouterr().out
    gate.stage([make_record(1, title="Weekly sync"), call(2, text="work part. holiday part.")])
    assert main(["staging", "list", *v]) == 0
    out = capsys.readouterr().out
    assert "ready    chat" in out
    assert "pending  calls" in out
    assert "Weekly sync" in out
    assert out.strip().endswith("1 pending, 1 ready")
    assert main(["staging", "list", "--state", "pending", "--source", "calls", *v]) == 0
    assert "chat" not in capsys.readouterr().out.replace("chat:", "")
    assert main(["staging", "classify", "calls:rec-2", "work", "--cut", "11-24", "--by", "claude", *v]) == 0
    assert capsys.readouterr().out.startswith("ready")
    assert main(["staging", "show", "calls:rec-2", *v]) == 0
    out = capsys.readouterr().out
    assert "by        claude" in out
    assert "cuts      11-24 char" in out
    assert main(["staging", "promote", *v]) == 0
    out = capsys.readouterr().out
    assert "calls: 1 written (raw/calls/" in out
    assert "chat: 1 written" in out
    assert main(["staging", "promote", *v]) == 0
    assert capsys.readouterr().out.strip() == "nothing ready"
    gate.stage([call(2, text="work part. holiday part. and more")])
    assert main(["staging", "show", "calls:rec-2", *v]) == 0
    out = capsys.readouterr().out
    assert "cut before, in raw run" in out
    assert "work part. [cut]" in out
    assert main(["staging", "classify", "calls:rec-2", "private", *v]) == 0
    assert capsys.readouterr().out.strip() == "dropped"


def test_promote_reports_held_dropped_and_redacted_and_names_no_dropped_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, gate = staging_vault(tmp_path)
    gate.stage(
        [
            make_record(1, text="password: " + "hunter2-" + "Example"),
            make_record(2, text="about the zebrafinch"),
            call(3, text="a call"),
        ]
    )
    gate.classify("calls:rec-3", "work")
    key = next(e.key for e in gate.list().entries if e.staged.record.source == "calls")
    staged = gate.staging.read(gate.staging.dir / "calls" / f"{key}.json")
    gate.staging.write(staged.model_copy(update={"cuts": [Cut(start=0, end=10, unit="ms")]}))
    yaml = path / "vault.yaml"
    yaml.write_text(
        yaml.read_text(encoding="utf-8").replace("  keywords: []", "  keywords:\n  - zebrafinch"), encoding="utf-8"
    )
    capsys.readouterr()
    assert main(["staging", "promote", "--vault", str(path)]) == 0
    out = capsys.readouterr().out
    assert "chat: 1 written" in out
    assert "redacted: password 1" in out
    assert f"held      {key[:12]} (calls): calls:rec-3 has no segments to cut by time" in out
    assert "dropped   never-store 1" in out
    assert "zebrafinch" not in out
    assert "1791000002" not in out
    assert main(["staging", "list", "--vault", str(path)]) == 0
    assert "held: calls:rec-3 has no segments to cut by time" in capsys.readouterr().out
    assert main(["staging", "show", key[:8], "--vault", str(path)]) == 0
    assert "problem   calls:rec-3 has no segments" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["staging", "show", "calls:rec-9"], "nothing staged as calls:rec-9"),
        (["staging", "list", "--state", "done"], "pending or ready"),
        (["staging", "classify", "calls:rec-1", "maybe"], "work or private"),
        (["staging", "classify", "calls:rec-1", "work", "--by", "robot"], "user or claude"),
        (["staging", "classify", "calls:rec-1", "work", "--cut", "5"], "a cut is <start>-<end>"),
    ],
)
def test_staging_mistakes_exit_2_with_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], args: list[str], message: str
) -> None:
    path, gate = staging_vault(tmp_path)
    gate.stage([call(1, text="x")])
    assert main([*args, "--vault", str(path)]) == 2
    assert message in capsys.readouterr().err


def test_an_unreadable_staging_file_is_listed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, gate = staging_vault(tmp_path)
    gate.stage([call(1, text="x")])
    (path / "staging" / "calls" / "broken.json").write_text("{", encoding="utf-8")
    assert main(["staging", "list", "--vault", str(path)]) == 0
    assert "unreadable" in capsys.readouterr().out


def test_a_cut_read_off_staging_show_lands_where_it_was_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, gate = staging_vault(tmp_path)
    private = "Also my hearing is on Friday, please keep quiet."
    lines = [
        "Standup:",
        "- shipped the parser",
        '- "review" pending\t(tab)',
        "- fixed the build\r",
        private,
        "Next: notes.",
    ]
    gate.stage([call(1, text="\n".join(lines))])
    v = ["--vault", str(path)]
    assert main(["staging", "show", "calls:rec-1", *v]) == 0
    shown = capsys.readouterr().out
    assert '"text"' not in shown
    start = int(next(ln for ln in shown.splitlines() if ln.endswith(private)).split()[0])
    assert main(["staging", "classify", "calls:rec-1", "work", "--cut", f"{start}-{start + len(private)}", *v]) == 0
    classified = capsys.readouterr().out
    assert "after the cuts:" in classified
    assert private not in classified
    assert "- fixed the build" in classified
    assert main(["staging", "show", "calls:rec-1", *v]) == 0
    assert "after the cuts:" in capsys.readouterr().out
    assert main(["staging", "promote", *v]) == 0
    [record] = [r for _, _, r in Vault.open(path).rawlog.iter_records()]
    assert record.text == "\n".join([*lines[:4], "[cut]", lines[5]])


def test_show_says_why_a_cut_can_t_be_made(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, gate = staging_vault(tmp_path)
    gate.stage([call(1, text="a recording")])
    key = next(e.key for e in gate.list().entries)
    staged = gate.staging.read(gate.staging.dir / "calls" / f"{key}.json")
    gate.staging.write(staged.model_copy(update={"cuts": [Cut(start=0, end=10, unit="ms")]}))
    assert main(["staging", "show", "calls:rec-1", "--vault", str(path)]) == 0
    assert "after the cuts: calls:rec-1 has no segments to cut by time" in capsys.readouterr().out
