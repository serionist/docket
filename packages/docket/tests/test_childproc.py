"""The flags a console program starts with: CREATE_NO_WINDOW only in a process without a console, asked once, and
passed by every git launch in the package (DR-0535)."""

from __future__ import annotations

import ast
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from docket import childproc
from docket.vault import home
from docket.vault.git import Git, toplevel

SRC = Path(childproc.__file__).parent
FLAG = 0x08000000


@pytest.fixture
def no_console(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    """This process as the service under pythonw: no console, and a flag to pass. Yields the creation flags each
    subprocess.run got; on a system without the flag the call goes ahead without it."""
    asked: list[int] = []
    real, on_windows = subprocess.run, bool(getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def run(*args: Any, **kwargs: Any) -> Any:
        asked.append(kwargs.get("creationflags", 0))
        if not on_windows:
            kwargs.pop("creationflags", None)
        return real(*args, **kwargs)

    monkeypatch.setattr(childproc, "CREATE_NO_WINDOW", FLAG)
    monkeypatch.setattr(childproc, "_has_console", lambda: False)
    monkeypatch.setattr(subprocess, "run", run)
    childproc.console_flags.cache_clear()
    yield asked
    childproc.console_flags.cache_clear()


def test_a_process_without_a_console_starts_git_with_no_window(no_console: list[int]) -> None:
    assert childproc.console_flags() == FLAG


def test_a_process_with_a_console_passes_no_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(childproc, "CREATE_NO_WINDOW", FLAG)
    monkeypatch.setattr(childproc, "_has_console", lambda: True)
    childproc.console_flags.cache_clear()
    try:
        assert childproc.console_flags() == 0
    finally:
        childproc.console_flags.cache_clear()


def test_the_console_is_asked_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(childproc, "_has_console", lambda: calls.append(1) or False)
    childproc.console_flags.cache_clear()
    try:
        for _ in range(3):
            childproc.console_flags()
        assert calls == [1]
    finally:
        childproc.console_flags.cache_clear()


def test_git_runs_with_the_flag_when_there_is_no_console(no_console: list[int], tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    no_console.clear()
    Git(tmp_path).out("rev-parse", "--is-inside-work-tree")
    assert home.rev_parse(tmp_path, "--show-toplevel") is not None
    assert toplevel(tmp_path) is not None
    assert no_console == [FLAG, FLAG, FLAG]


def _git_launches(tree: ast.AST) -> Iterator[ast.Call]:
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in ("run", "Popen") or not node.args or not isinstance(node.args[0], ast.List):
            continue
        first = node.args[0].elts[0] if node.args[0].elts else None
        if isinstance(first, ast.Constant) and first.value == "git":
            yield node


def test_every_git_launch_passes_the_console_flags() -> None:
    """A git call added later without the flag would start a console of its own under the service."""
    missing = [
        f"{path.relative_to(SRC)}:{call.lineno}"
        for path in sorted(SRC.rglob("*.py"))
        if "templates" not in path.parts
        for call in _git_launches(ast.parse(path.read_text(encoding="utf-8")))
        if not any(k.arg == "creationflags" for k in call.keywords)
    ]
    assert missing == []
    assert sum(1 for path in SRC.rglob("*.py") for _ in _git_launches(ast.parse(path.read_text(encoding="utf-8"))))
