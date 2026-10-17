"""The test kit's shared templates: built once per session for every worker, cleared after a failed build, never
written by a test, and copied so that a copy behaves as a fresh build would (DR-0531, DR-0221)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import docket_testkit as kit
import pytest
from docket_testkit import empty_vault, folder_vault, git, host_repo, make_record, users_work

from docket.vault.config import VaultConfig

TWO_WORKERS = """
import sys, time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import docket_testkit as kit


class Factory:
    def getbasetemp(self) -> Path:
        return Path(sys.argv[2])


def build(folder: Path) -> Path:
    with (Path(sys.argv[2]) / "builds.txt").open("a", encoding="utf-8") as f:
        f.write("built\\n")
    time.sleep(1.0)
    (folder / "made.txt").write_text("made", encoding="utf-8")
    return folder


print(kit.shared(build, "two-workers", Factory()))
"""


class Factory:
    """A temp factory whose base is this test's own folder, so its templates stay out of the session's."""

    def __init__(self, base: Path) -> None:
        self.base = base

    def getbasetemp(self) -> Path:
        return self.base


@pytest.fixture
def factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> pytest.TempPathFactory:
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.delenv(kit.TEMPLATES_ENV, raising=False)
    return cast(pytest.TempPathFactory, Factory(tmp_path))


def test_two_workers_asking_at_once_build_a_template_once(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTEST_XDIST_WORKER", kit.TEMPLATES_ENV)}
    script = tmp_path / "worker.py"
    script.write_text(TWO_WORKERS, encoding="utf-8")
    tests = str(Path(kit.__file__).parent)
    args = [sys.executable, str(script), tests, str(tmp_path)]
    workers = [subprocess.Popen(args, env=env, stdout=subprocess.PIPE, text=True) for _ in range(2)]
    outputs = [w.communicate(timeout=120)[0].strip() for w in workers]
    assert [w.returncode for w in workers] == [0, 0]
    assert outputs[0] == outputs[1]
    assert (tmp_path / "builds.txt").read_text(encoding="utf-8") == "built\n"


def test_a_failed_build_is_cleared_and_the_next_gives_its_own_result(factory: pytest.TempPathFactory) -> None:
    def failing(folder: Path) -> Path:
        (folder / "half.txt").write_text("half", encoding="utf-8")
        raise RuntimeError("the build broke")

    with pytest.raises(RuntimeError, match="the build broke"):
        kit.shared(failing, "once", factory)

    def whole(folder: Path) -> Path:
        (folder / "whole.txt").write_text("whole", encoding="utf-8")
        return folder

    made = kit.shared(whole, "once", factory)
    assert sorted(p.name for p in made.iterdir()) == ["whole.txt"]
    assert kit.shared(failing, "once", factory) == made


def test_a_template_in_the_folder_the_gate_names_is_built_once_for_both_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(kit.TEMPLATES_ENV, str(tmp_path / "named"))
    builds: list[Path] = []

    def build(folder: Path) -> Path:
        builds.append(folder)
        (folder / "made.txt").write_text("made", encoding="utf-8")
        return folder

    # Two sessions, each with its own temp folder: the parallel run, then the timed run.
    parallel, alone = (cast(pytest.TempPathFactory, Factory(tmp_path / run)) for run in ("parallel", "alone"))
    made = kit.shared(build, "named", parallel)
    assert kit.shared(build, "named", alone) == made
    assert len(builds) == 1 and made.parent == kit.templates_folder(alone) == tmp_path / "named"
    (made / "made.txt").write_text("written", encoding="utf-8")
    # The timed run's check reads the same folder, so a write there into what the parallel run built is named.
    changes = kit.template_changes(kit.templates_folder(alone))
    assert [line.split("/", 1)[1] for line in changes] == ["made.txt: changed"]


def test_a_write_into_a_template_is_named_and_a_reader_s_cache_is_not(
    tmp_path: Path, factory: pytest.TempPathFactory
) -> None:
    def build(folder: Path) -> Path:
        (folder / "a.txt").write_text("a", encoding="utf-8")
        (folder / "b.txt").write_text("b", encoding="utf-8")
        (folder / "sub").mkdir()
        return folder

    made = kit.shared(build, "kept", factory)
    assert kit.template_changes(tmp_path / "templates") == []
    (made / "new.txt").write_text("new", encoding="utf-8")
    (made / "a.txt").write_text("a, longer", encoding="utf-8")
    (made / "b.txt").unlink()
    (made / "sub" / "__pycache__").mkdir()
    (made / "sub" / "__pycache__" / "m.cpython-312.pyc").write_bytes(b"")
    (made / "index.sqlite-wal").write_bytes(b"")
    assert [line.split("/", 1)[1] for line in kit.template_changes(tmp_path / "templates")] == [
        "a.txt: changed",
        "b.txt: removed",
        "new.txt: added",
    ]


def test_builders_of_one_name_in_two_modules_and_two_mapper_sets_get_templates_of_their_own(
    factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    def make(folder: Path) -> Any:
        (folder / "vault").mkdir()
        return SimpleNamespace(root=folder / "vault")

    def other(folder: Path) -> Any:
        return make(folder)

    other.__qualname__ = make.__qualname__
    other.__module__ = "another_module"
    assert kit.template_with(factory, make) != kit.template_with(factory, other)

    def project(folder: Path, mappers: dict[str, str]) -> Any:
        (folder / "project" / "vault").mkdir(parents=True)
        return SimpleNamespace(root=folder / "project" / "vault")

    monkeypatch.setattr(kit, "consuming_project", project)
    assert kit.template_project(factory, {"memo": "A = 1"}) != kit.template_project(factory, {"memo": "A = 2"})


def test_a_copied_host_pushes_to_its_own_origin(tmp_path: Path) -> None:
    top = host_repo(tmp_path)
    assert Path(git(top, "remote", "get-url", "origin").strip()).resolve() == (tmp_path / "origin.git").resolve()
    (top / "new.txt").write_text("new\n", encoding="utf-8")
    git(top, "add", "new.txt")
    git(top, "commit", "-qm", "New")
    git(top, "push", "-q", "origin", "main")
    assert git(tmp_path / "origin.git", "log", "-1", "--format=%s", "main").strip() == "New"
    template = kit.shared(kit._build_host_repo, "host-repo")  # pyright: ignore[reportPrivateUsage]
    assert git(template.parent / "origin.git", "log", "-1", "--format=%s", "main").strip() == "Host start"


def test_a_copied_folder_vault_holds_the_user_s_work_and_a_settled_index(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    top = vault.home.top
    staged, unstaged = users_work(top)
    assert "hello, staged" in staged
    assert "my own note" in staged
    assert "other, unstaged" in unstaged
    index = top / ".git" / "index"
    before = index.read_bytes()
    git(top, "status", "--porcelain")
    assert index.read_bytes() == before


@pytest.mark.skipif(shutil.which("git-lfs") is None, reason="needs git-lfs")
def test_a_copy_s_first_status_hashes_nothing_again(tmp_path: Path, config: VaultConfig) -> None:
    vault = empty_vault(tmp_path, config)
    trace = tmp_path / "trace.txt"
    env = {**os.environ, "GIT_TRACE": str(trace)}
    subprocess.run(["git", "status", "--porcelain"], cwd=vault.root, env=env, check=True, capture_output=True)
    assert "filter-process" not in trace.read_text(encoding="utf-8", errors="replace")


def test_a_same_size_rewrite_within_the_second_of_a_commit_is_seen(tmp_path: Path, config: VaultConfig) -> None:
    vault = empty_vault(tmp_path, config)
    for n in range(1, 6):
        vault.write_run("chat", [make_record(n)], datetime(2026, 10, 14, 15, n, tzinfo=UTC))
        run = sorted((vault.root / "raw" / "chat").rglob("*.jsonl"))[-1]
        data = run.read_bytes()
        run.write_bytes(data.replace(b"message", b"MESSAGE", 1))
        assert vault.git.changed_against_head("raw") == [run.relative_to(vault.root).as_posix()]
        run.write_bytes(data)
