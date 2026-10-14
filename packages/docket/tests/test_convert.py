"""`docket vault convert`: a nested vault becomes a folder of the repo around it with its history, after a dry run and
the user's yes; a crash at any step is finished by a second run, or undone before the host commit."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import REC, git, host_repo, make_record, note, sh, users_work, write_raw
from filelock import FileLock

from docket.cli import main
from docket.errors import DocketError
from docket.vault import convert as cv
from docket.vault.config import CONFIG_FILE
from docket.vault.docstore import DocStore
from docket.vault.git import Git
from docket.vault.host import Visibility
from docket.vault.vault import Vault
from docket.vault.wizard import Given, Wizard

DOC = "recording-note:2026-10-14-supplier-call"
LFS_OBJECT = "ab/cd/abcd" + "0" * 60


class Script:
    def __init__(self, confirms: list[bool] | None = None) -> None:
        self.confirms = list(confirms or [])
        self.said: list[str] = []

    def ask(self, question: str, default: str | None = None) -> str:
        return default or ""

    def confirm(self, question: str, default: bool = False) -> bool:
        return self.confirms.pop(0) if self.confirms else default

    def say(self, text: str) -> None:
        self.said.append(text)


def local(_url: str) -> Visibility:
    return "local"


def nested(tmp_path: Path) -> tuple[Path, Path]:
    """The shape `docket init` made before folders: a vault that is a repo of its own, with no remote, inside a host
    whose .gitignore and .ignore keep it out; raw runs, a doc at rev 2, an LFS object, a record waiting in staging,
    and the user's own staged and unstaged work in the host."""
    top = host_repo(tmp_path)
    given = Given(
        path=top / "notes" / "vault", owner="Ada Example", emails=["ada@example.invalid"], languages=["en"],
        time_zone="Europe/Budapest", machine="test-box", sources=["chat", "calls=mixed"], storage="repo",
        remote="none", yes=True,
    )  # fmt: skip
    vault = Wizard(Script(), given, local).run()
    git(top, "add", ".gitignore", ".ignore")
    git(top, "commit", "-qm", "Keep the vault out")
    write_raw(vault.root)
    vault.commit([], "Raw: the fixture")
    store = DocStore(vault)
    store.put(note())
    store.put(note(lines=["- Ben put the consultant at 10 million forints, for the quarter. @12:08"], rev="1"))
    lfs = vault.root / ".git" / "lfs" / "objects" / LFS_OBJECT
    lfs.parent.mkdir(parents=True, exist_ok=True)
    lfs.write_bytes(b"encrypted audio")
    (vault.root / "staging" / "chat").mkdir(parents=True)
    (vault.root / "staging" / "chat" / "k.json").write_text("{}\n", encoding="utf-8")
    (top / "README.md").write_text("hello, staged\n", encoding="utf-8")
    git(top, "add", "README.md")
    (top / "other.txt").write_text("other, unstaged\n", encoding="utf-8")
    return top, vault.root


def run(root: Path, script: Script | None = None, **opts: Any) -> Script:
    script = script or Script()
    cv.convert(root, script, cv.Options(**{"yes": True, **opts}), local)
    return script


def snapshot(top: Path, root: Path) -> tuple[str, bytes, str, str]:
    return (
        git(top, "rev-parse", "HEAD"),
        (top / ".git" / "index").read_bytes(),
        git(top, "status", "--porcelain", "--ignored"),
        git(root, "rev-parse", "HEAD"),
    )


def merges(top: Path) -> int:
    return len(git(top, "rev-list", "--merges", "HEAD").split())


def check_converted(top: Path, root: Path, merged: int = 1) -> Vault:
    vault = Vault.open(root)
    assert vault.config.storage == "folder"
    assert not (root / ".git").exists()
    assert merges(top) == merged
    revs = git(top, "log", "--format=%s", "--", f"notes/vault/processed/recording-note/2026/10/{DOC.split(':')[1]}.md")
    assert revs.splitlines() == [f"Doc: {DOC} rev 2", f"Doc: {DOC} rev 1"]
    assert "10 million forints. @12:08" in DocStore(vault).at_rev(DOC, 1).render(rev=1)
    assert not [line for line in git(top, "status", "--porcelain").splitlines() if "notes/vault" in line]
    return vault


def test_the_dry_run_changes_nothing(tmp_path: Path) -> None:
    top, root = nested(tmp_path)
    before = snapshot(top, root)
    script = run(root, dry_run=True)
    assert snapshot(top, root) == before
    shown = script.said[0]
    assert "history:  the 4 commit(s) join main's history under notes/vault/" in shown
    assert "lfs:      1 object(s) copied" in shown
    assert ".gitignore drops /notes/vault/" in shown
    assert "refused" not in shown


def test_the_vault_becomes_a_folder_with_its_history(tmp_path: Path) -> None:
    top, root = nested(tmp_path)
    work = users_work(top)
    script = run(root)
    vault = check_converted(top, root)
    # The merge adds nothing of its own: outside the vault it is its first parent, inside its second.
    assert git(top, "diff", "HEAD^1", "HEAD", "--", ".", ":!notes/vault") == ""
    assert git(top, "diff", "HEAD^2", "HEAD", "--", "notes/vault") == ""
    assert git(top, "log", "-1", "--format=%s", "HEAD^2") == "Make the vault a folder of this repo\n"
    assert (top / ".git" / "lfs" / "objects" / LFS_OBJECT).read_bytes() == b"encrypted audio"
    assert (root / "staging" / "chat" / "k.json").is_file()
    assert git(top, "check-ignore", "notes/vault/staging/chat/k.json").strip()
    assert "/notes/vault/" not in (top / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert (top / ".ignore").read_text(encoding="utf-8") == ""
    assert users_work(top)[0] == work[0]
    assert "M .gitignore" in git(top, "status", "--porcelain")
    assert (top / ".git" / "docket" / "notes" / "vault" / "machine").read_text(encoding="utf-8").strip() == "test-box"
    assert "Left for you to commit: .gitignore, .ignore" in script.said[-1]
    attrs = git(top, "check-attr", "filter", "merge", "--", "notes/vault/media/x.age", "notes/vault/raw/a/x.jsonl")
    assert attrs.splitlines() == [
        "notes/vault/media/x.age: filter: lfs",
        "notes/vault/media/x.age: merge: lfs",
        "notes/vault/raw/a/x.jsonl: filter: unspecified",
        "notes/vault/raw/a/x.jsonl: merge: union",
    ]
    assert vault.check() == []
    vault.write_run("chat", [make_record(9)])
    assert git(top, "log", "-1", "--format=%s").startswith("Raw: chat")
    # A .gitignore edit undone by hand: check says what it is.
    (top / ".gitignore").write_text("/notes/vault/\n", encoding="utf-8")
    assert "commit that edit" in str(vault.check()[0])


def test_a_pull_rebase_after_the_convert_keeps_the_vault_a_folder(tmp_path: Path) -> None:
    top, root = nested(tmp_path)
    other = tmp_path / "other"
    sh(tmp_path, "git", "clone", "-q", str(tmp_path / "origin.git"), str(other))
    (other / "upstream.txt").write_text("x\n", encoding="utf-8")
    git(other, "add", "-A")
    git(other, "commit", "-qm", "Upstream work")
    git(other, "push", "-q", "origin", "main")
    run(root)
    git(top, "add", "-A")
    git(top, "commit", "-qm", "My work, and the vault out of .gitignore")
    git(top, "pull", "-q", "--rebase", "origin", "main")
    assert merges(top) == 0
    vault = Vault.open(root)
    assert vault.config.storage == "folder"
    assert "Make the vault a folder of this repo" in git(top, "log", "--format=%s")
    assert DocStore(vault).at_rev(DOC, 1).meta.id == DOC
    attrs = git(top, "check-attr", "filter", "merge", "--", "notes/vault/media/x.age", "notes/vault/raw/a/x.jsonl")
    assert "notes/vault/media/x.age: filter: lfs" in attrs
    assert "notes/vault/raw/a/x.jsonl: filter: unspecified" in attrs
    assert "notes/vault/raw/a/x.jsonl: merge: union" in attrs
    assert vault.rawlog.latest(REC) is not None


def refused(root: Path, match: str, **opts: Any) -> None:
    with pytest.raises(DocketError, match=match):
        run(root, **opts)


def test_a_vault_with_a_remote_is_refused(tmp_path: Path) -> None:
    top, root = nested(tmp_path)
    git(root, "remote", "add", "origin", str(tmp_path / "elsewhere.git"))
    before = snapshot(top, root)
    refused(root, "retire the other clones first|Retire the other clones first")
    assert snapshot(top, root) == before


def test_what_else_refuses_a_convert(tmp_path: Path) -> None:
    top, root = nested(tmp_path)
    (root / "raw" / "stray.txt").write_text("x\n", encoding="utf-8")
    refused(root, "uncommitted changes")
    (root / "raw" / "stray.txt").unlink()
    with FileLock(str(root / ".git" / "docket.lock")):
        refused(root, "another docket process")
    with pytest.raises(DocketError, match="read without signing in"):
        git(top, "remote", "add", "open", "https://example.invalid/open.git")
        cv.convert(root, Script(), cv.Options(yes=True), lambda u: "public" if "open" in u else "local")
    git(top, "remote", "remove", "open")
    (top / "theirs.txt").write_text("x\n", encoding="utf-8")
    git(top, "add", "theirs.txt")
    git(top, "commit", "-qm", "theirs", "--author=Ben Sample <ben@example.invalid>")
    refused(root, "allow-shared")
    run(root, dry_run=True, allow_shared=True)
    sha = git(root, "rev-parse", "HEAD").strip()
    git(top, "update-index", "--add", "--cacheinfo", f"160000,{sha},notes/vault")
    refused(root, "submodule")


def test_a_folder_vault_or_a_loose_folder_isnt_converted(tmp_path: Path) -> None:
    top, root = nested(tmp_path)
    text = (root / CONFIG_FILE).read_text(encoding="utf-8").replace("storage: repo", "storage: folder")
    (root / CONFIG_FILE).write_text(text, encoding="utf-8")
    git(root, "commit", "-qam", "say folder")
    refused(root, "already says storage: folder")
    loose = tmp_path / "loose" / "vault"
    loose.mkdir(parents=True)
    with pytest.raises(DocketError, match="isn't inside a git repo"):
        cv.locate(loose)
    (top / "plain").mkdir()
    with pytest.raises(DocketError, match=r"no [.]git of its own"):
        cv.survey(cv.locate(top / "plain"), cv.Options(), local)


def test_a_linked_worktree_is_refused(tmp_path: Path) -> None:
    top, _ = nested(tmp_path)
    git(top, "commit", "-qm", "staged work")
    git(top, "worktree", "add", "-q", str(tmp_path / "wt"), "-b", "side")
    (tmp_path / "wt" / "notes" / "vault").mkdir(parents=True)
    with pytest.raises(DocketError, match="linked worktree"):
        cv.locate(tmp_path / "wt" / "notes" / "vault")


def test_no_means_nothing_changes(tmp_path: Path) -> None:
    top, root = nested(tmp_path)
    before = snapshot(top, root)
    with pytest.raises(DocketError, match="nothing changed"):
        run(root, Script(confirms=[False]), yes=False)
    assert snapshot(top, root) == before


def crash(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    def boom(*_args: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cv, name, boom)


@pytest.mark.parametrize("after", [1, 2, 3, 4, 5])
def test_a_crash_after_any_step_is_finished_by_a_second_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after: int
) -> None:
    top, root = nested(tmp_path)
    crash(monkeypatch, ["_rewrite", "_move", "_write_folder_files", "_merge", "_finish"][after - 1])
    with pytest.raises(KeyboardInterrupt):
        run(root)
    monkeypatch.undo()
    state = json.loads((top / ".git" / "docket" / "notes" / "vault" / "convert.json").read_text(encoding="utf-8"))
    assert state["step"] == after + 1
    if after >= 3:
        with pytest.raises(DocketError, match=r"--abort"):
            Vault.open(root)
    script = run(root)
    assert script.said[0].startswith("Finishing the convert")
    check_converted(top, root)


def test_a_crash_between_update_ref_and_the_rename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top, root = nested(tmp_path)

    def interrupted(_source: Path, _target: Path) -> bool:
        raise KeyboardInterrupt

    real = cv._try_rename  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(cv, "_try_rename", lambda s, t: interrupted(s, t) if t.name == "index" else real(s, t))
    with pytest.raises(KeyboardInterrupt):
        run(root)
    monkeypatch.undo()
    lock = top / ".git" / "index.lock"
    assert lock.exists()
    with pytest.raises(DocketError, match=r"index\.lock is 0 minute\(s\) old; if no git is running, delete it"):
        run(root)
    lock.unlink()
    run(root)
    check_converted(top, root)


def test_a_crash_after_the_rename_before_the_state_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top, root = nested(tmp_path)
    real = cv.State.save

    def save(self: cv.State, where: cv.Where) -> None:
        if self.step == 6:
            raise KeyboardInterrupt
        real(self, where)

    monkeypatch.setattr(cv.State, "save", save)
    with pytest.raises(KeyboardInterrupt):
        run(root)
    monkeypatch.undo()
    run(root)
    check_converted(top, root)


def test_a_rename_that_keeps_failing_stops_at_step_5(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top, root = nested(tmp_path)
    real = cv._try_rename  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(cv, "_try_rename", lambda s, t: False if t.name == "index" else real(s, t))
    with pytest.raises(DocketError, match="a program holds it"):
        run(root)
    monkeypatch.undo()
    assert not (top / ".git" / "index.lock").exists()
    assert merges(top) == 1
    run(root)
    check_converted(top, root)


def test_the_repo_moving_during_the_merge_is_said(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top, root = nested(tmp_path)
    real = Git.run

    def moved(self: Git, *args: str, **kw: Any) -> subprocess.CompletedProcess[str]:
        if args[:1] == ("update-ref",) and "HEAD" in args:
            return subprocess.CompletedProcess(list(args), 1, "", "cannot lock ref")
        return real(self, *args, **kw)

    monkeypatch.setattr(Git, "run", moved)
    with pytest.raises(DocketError, match="moved while the vault was being merged"):
        run(root)
    monkeypatch.undo()
    assert not (top / ".git" / "index.lock").exists()
    run(root)
    check_converted(top, root)


def test_abort_undoes_a_convert_stopped_before_the_host_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top, root = nested(tmp_path)
    crash(monkeypatch, "_merge")
    with pytest.raises(KeyboardInterrupt):
        run(root)
    monkeypatch.undo()
    assert not (root / ".git").exists()
    script = run(root, abort=True)
    assert "moved" in script.said[-1]
    assert "deleted refs/docket/convert/source" in script.said[-1]
    assert "removed the 1 LFS object(s) it copied" in script.said[-1]
    assert (root / ".git").is_dir()
    assert not (top / ".git" / "lfs" / "objects" / LFS_OBJECT).exists()
    assert git(top, "for-each-ref", "refs/docket") == ""
    assert not (root / ".ignore").exists()
    assert git(root, "status", "--porcelain") == ""
    assert Vault.open(root).config.storage == "repo"
    with pytest.raises(DocketError, match="no convert"):
        run(root, abort=True)


def test_abort_after_the_host_commit_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top, root = nested(tmp_path)
    crash(monkeypatch, "_finish")
    with pytest.raises(KeyboardInterrupt):
        run(root)
    monkeypatch.undo()
    with pytest.raises(DocketError, match="already made its commit"):
        run(root, abort=True)
    script = run(root, dry_run=True)
    assert "stopped at step 6" in script.said[0]
    run(root)
    check_converted(top, root)


def test_abort_before_anything_moved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top, root = nested(tmp_path)
    crash(monkeypatch, "_rewrite")
    with pytest.raises(KeyboardInterrupt):
        run(root)
    monkeypatch.undo()
    script = run(root, abort=True)
    assert "Undid the convert" in script.said[-1]
    assert git(top, "for-each-ref", "refs/docket") == ""


def test_a_writer_that_slips_in_before_the_move(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top, root = nested(tmp_path)
    real = cv._rewrite  # pyright: ignore[reportPrivateUsage]

    def rewrite_then_write(where: cv.Where, state: cv.State) -> None:
        real(where, state)
        Vault.open(root).write_run("chat", [make_record(9)])

    monkeypatch.setattr(cv, "_rewrite", rewrite_then_write)
    with pytest.raises(DocketError, match="changed while it was being converted"):
        run(root)
    monkeypatch.undo()
    assert (root / ".git").is_dir()
    run(root)
    vault = check_converted(top, root)
    assert vault.rawlog.latest(make_record(9).rid) is not None


def test_a_rename_that_fails_and_a_backup_already_there(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top, root = nested(tmp_path)
    monkeypatch.setattr(cv, "RENAME_SECONDS", 0.2)
    real = Path.replace

    def held(self: Path, target: Path) -> Path:
        if self.name == ".git":
            raise PermissionError("in use")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", held)
    with pytest.raises(DocketError, match="couldn't move"):
        run(root)
    monkeypatch.undo()
    assert (root / ".git").is_dir()
    backup = top / ".git" / "docket" / "notes" / "vault" / "before-folder.git"
    backup.mkdir()
    with pytest.raises(DocketError, match="already there"):
        run(root)
    backup.rmdir()
    run(root)
    check_converted(top, root)


def test_a_failing_fast_import_stops_before_anything_moves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top, root = nested(tmp_path)
    real = subprocess.run

    def failing(args: list[str], **kw: Any) -> subprocess.CompletedProcess[bytes]:
        if args[:2] == ["git", "fast-import"]:
            return subprocess.CompletedProcess(args, 1, b"", b"fatal: broken stream")
        return real(args, **kw)

    monkeypatch.setattr(subprocess, "run", failing)
    with pytest.raises(DocketError, match="broken stream"):
        run(root)
    monkeypatch.undo()
    assert (root / ".git").is_dir()
    run(root)
    check_converted(top, root)


def test_a_vault_an_older_docket_wrote_with_a_merge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top, root = nested(tmp_path)
    text = (root / CONFIG_FILE).read_text(encoding="utf-8").replace("storage: repo\n", "")
    (root / CONFIG_FILE).write_text(text, encoding="utf-8")
    (root / ".ignore").write_text("/*\n", encoding="utf-8")
    old_attributes = "* text=auto eol=lf\n*.jsonl merge=union\nmedia/** filter=lfs diff=lfs merge=lfs -text\n"
    (root / ".gitattributes").write_text(old_attributes, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "As an older Docket wrote it")
    (top / ".ignore").write_text("", encoding="utf-8")
    git(top, "commit", "-qm", "No search rules", "--", ".ignore")
    git(root, "checkout", "-q", "-b", "side")
    (root / "entities" / "a.yaml").write_text("a: 1\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "side")
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--no-ff", "-m", "Merge side", "side")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Ada Example")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "ada@example.invalid")
    script = run(root, dry_run=True)
    assert "refs/heads/side" in script.said[0]
    monkeypatch.setattr("docket.vault.host.shutil.which", lambda _name: None)
    script = run(root)
    assert any(line.startswith("git-lfs: not installed") for line in script.said)
    check_converted(top, root, merged=2)
    assert (root / ".ignore").read_text(encoding="utf-8") == "/*\n!/processed/\n"
    assert merges(top) == 2
    assert len(git(top, "rev-list", "--merges", "HEAD^2").split()) == 1
    assert "storage: folder" in (root / CONFIG_FILE).read_text(encoding="utf-8")
    # The reset line goes first: appended last, it would cancel the vault's own lines.
    lines = (root / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert lines[1] == "* !filter !diff !merge !working-tree-encoding"
    # The vault's own lines stay as they were, and the one it lacked (the hold lists' merge rule) comes after them.
    assert lines[2:] == [*old_attributes.splitlines(), "state/holds/*.txt merge=union"]
    attrs = git(top, "check-attr", "filter", "merge", "--", "notes/vault/media/x.age", "notes/vault/raw/a/x.jsonl")
    assert "notes/vault/media/x.age: filter: lfs" in attrs
    assert "notes/vault/raw/a/x.jsonl: merge: union" in attrs


def test_an_ignore_rule_the_vault_s_line_hid_is_said_after(tmp_path: Path) -> None:
    top, root = nested(tmp_path)
    (top / ".gitignore").write_text("/notes/vault/\n*.age\n", encoding="utf-8")
    git(top, "commit", "-qm", "ignore media", "--", ".gitignore")
    script = run(root)
    assert any("media/s/2026/01/x.age: an ignore rule still hides it" in line for line in script.said)


def test_helpers() -> None:
    assert cv._with_storage("schema_version: 1\nowner: x\n") == "schema_version: 1\nstorage: folder\nowner: x\n"  # pyright: ignore[reportPrivateUsage]
    assert cv._with_storage("# mine\nowner: x\n") == "storage: folder\n# mine\nowner: x\n"  # pyright: ignore[reportPrivateUsage]


def test_lfs_store_and_locks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sh(tmp_path, "git", "init", "-q", "repo")
    repo = tmp_path / "repo"
    git(repo, "config", "lfs.storage", "elsewhere")
    assert cv._lfs_store(repo / ".git", Git(repo)) == repo / ".git" / "elsewhere" / "objects"  # pyright: ignore[reportPrivateUsage]
    assert not cv._lock_held(tmp_path / "missing" / "docket.lock")  # pyright: ignore[reportPrivateUsage]
    assert cv._lfs_missing(tmp_path / "missing", tmp_path) == []  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(cv, "RENAME_SECONDS", 0.2)
    lock = tmp_path / "index.lock"
    lock.write_text("", encoding="utf-8")
    with pytest.raises(DocketError, match=r"is there, 0 minute\(s\) old"):
        cv._take(lock)  # pyright: ignore[reportPrivateUsage]


def test_the_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    top, root = nested(tmp_path)
    monkeypatch.setattr("docket.vault.convert.remote_visibility", local)
    monkeypatch.setattr(cv.convert, "__defaults__", (local,))
    assert main(["vault", "convert", "--vault", str(root), "--dry-run"]) == 0
    assert "history:" in capsys.readouterr().out
    monkeypatch.chdir(top)
    assert main(["vault", "convert", "--yes"]) == 0
    check_converted(top, root)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DOCKET_CONFIG_DIR", str(tmp_path / "no-config"))
    assert main(["vault", "convert", "--yes"]) == 2
    assert "no vault here" in capsys.readouterr().err
