from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

REAL_ROOT = Path(__file__).resolve().parents[3]


def run_git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8").stdout


@pytest.fixture(scope="session")
def repo_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The repo below, made once per worker: a test's copy costs a folder copy where the init and the commit cost six
    git calls."""
    root = tmp_path_factory.mktemp("repo-template") / "repo"
    (root / "docs").mkdir(parents=True)
    shutil.copy(REAL_ROOT / "docs" / "docs-regime.yaml", root / "docs" / "docs-regime.yaml")
    (root / "README.md").write_text("# test\n", encoding="utf-8")
    run_git(root, "init", "-q", "-b", "main")
    run_git(root, "config", "user.name", "Ada Example")
    run_git(root, "config", "user.email", "ada@example.invalid")
    run_git(root, "config", "commit.gpgsign", "false")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-q", "-m", "Start")
    return root


@pytest.fixture
def repo(repo_template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway git repo with this repo's docs regime, which the tools treat as their root: a copy of one made
    once per worker."""
    root = tmp_path / "repo"
    shutil.copytree(repo_template, root)
    monkeypatch.setenv("DOCKET_REPO_ROOT", str(root))
    monkeypatch.delenv("DOCKET_DENYLIST", raising=False)
    return root


@pytest.fixture
def git() -> Callable[..., str]:
    """Run git in a folder: git(root, "status")."""
    return run_git
