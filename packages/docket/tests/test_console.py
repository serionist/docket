"""The owner's yes at their own console (DR-0518): what counts as a console, the code, the question."""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
from docket_testkit import consuming_project, git, sh

from docket import console
from docket.sources import release as releasing
from docket.vault.config import CONFIG_FILE, VaultConfig


def test_the_null_device_a_pipe_or_no_stream_is_no_console() -> None:
    with Path(os.devnull).open(encoding="utf-8") as nul:
        assert not console.is_console(nul)
    assert not console.is_console(io.StringIO())
    assert not console.is_console(None)


def test_with_a_console_on_both_sides_and_outside_claude_code_the_question_is_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(console, "is_console", lambda stream: True)
    assert console.problem(console.RELEASE) is None
    monkeypatch.setenv("CLAUDECODE", "1")
    assert console.problem(console.LOOSENING) == (
        "this shell is Claude Code's (CLAUDECODE is set): a vault.yaml change that isn't only stricter is yours to "
        "confirm, so run the command in your own terminal"
    )


def test_without_a_console_each_act_says_where_to_run_it() -> None:
    assert console.problem(console.RELEASE) == (
        "a release asks you at a console: run it in your own terminal with nothing piped in or out (in Git Bash's own "
        "window, through winpty)"
    )


def test_the_end_of_input_stops_and_a_code_is_four_fresh_letters(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_more(prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", no_more)
    assert console.ask("?") == "q"
    codes = {console.code() for _ in range(20)}
    assert all(re.fullmatch(r"[A-HJ-NP-Z]{4}", c) for c in codes)
    assert len(codes) > 1


@pytest.mark.parametrize(("typed", "answer"), [("code", "yes"), ("n", "no"), ("y", "no"), ("q", "stop")])
def test_the_code_typed_back_is_the_only_yes(
    typed: str, answer: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def person(prompt: str) -> str:
        found = re.search(r"Type ([A-Z]{4}) to make this change, n to leave it, q to stop: ", prompt)
        assert found is not None, prompt
        return found.group(1).lower() if typed == "code" else typed

    monkeypatch.setattr(console, "ask", person)
    assert console.typed_back(["the screen \x1b[2J"], "make this change", "leave it", "it stays") == answer
    out = capsys.readouterr().out
    assert out.startswith("the screen \\x1b[2J\n")
    assert ("that isn't the code; it stays" in out) == (typed == "y")


def test_the_release_keeps_its_two_names_for_a_copied_reference_test(monkeypatch: pytest.MonkeyPatch) -> None:
    """A project's copy of the Plaud reference's tests stands in for the owner as releasing._console_problem and
    releasing._ask: both stay, each only a call through to docket.console."""
    monkeypatch.setattr(console, "problem", lambda act: f"asked about {act}")
    monkeypatch.setattr(console, "ask", lambda prompt: f"read {prompt}")
    assert releasing._console_problem() == "asked about a release"  # pyright: ignore[reportPrivateUsage]
    assert releasing._ask("?") == "read ?"  # pyright: ignore[reportPrivateUsage]


# -- a real pipe, a real terminal ------------------------------------------------------------------------------------


def _project(tmp_path: Path) -> Path:
    found = consuming_project(tmp_path, {})
    path = found.root / CONFIG_FILE
    config = VaultConfig.load(path)
    config.redaction.phone = True
    path.write_text(config.dump(), encoding="utf-8", newline="\n")
    git(found.root, "commit", "-qm", "phone on", "--", CONFIG_FILE)
    (found.root.parent / "papers").mkdir()
    return found.root


def _docket(*args: str, **kwargs: object) -> subprocess.Popen[bytes]:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    return subprocess.Popen([sys.executable, "-m", "docket", *args], env=env, **kwargs)  # type: ignore[call-overload]


@pytest.mark.parametrize(
    "command",
    [
        ["redaction", "set", "phone", "off"],
        ["source", "new", "diary"],
        ["source", "new", "papers", "--folder", "papers"],
    ],
    ids=["redaction off", "source new", "folder source new"],
)
def test_each_loosening_command_with_a_y_piped_in_is_refused_and_writes_nothing(
    tmp_path: Path, command: list[str]
) -> None:
    root = _project(tmp_path)
    before = (root / CONFIG_FILE).read_bytes()
    at = sh(root, "git", "rev-parse", "HEAD")
    proc = _docket(
        *command,
        "--vault",
        str(root),
        cwd=root.parent,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _, err = proc.communicate(b"y\n", timeout=120)
    assert proc.returncode == 2, err
    assert b"asks you at a console: run it in your own terminal with nothing piped in or out" in err
    assert (root / CONFIG_FILE).read_bytes() == before
    assert sh(root, "git", "rev-parse", "HEAD") == at
    assert not (root.parent / ".claude" / "skills" / "sources" / "diary").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="a pseudo-terminal is POSIX's; Windows is proved by GetConsoleMode")
def test_at_a_real_terminal_the_code_typed_back_makes_the_change(tmp_path: Path) -> None:
    # The type checker reads what follows as POSIX only, as it runs.
    assert sys.platform != "win32"
    import pty
    import select

    root = _project(tmp_path)
    main_fd, child_fd = pty.openpty()
    proc = _docket(
        "redaction",
        "set",
        "phone",
        "off",
        "--vault",
        str(root),
        stdin=child_fd,
        stdout=child_fd,
        stderr=subprocess.PIPE,
    )
    os.close(child_fd)
    seen = b""
    deadline = time.monotonic() + 60
    try:
        while (found := re.search(rb"Type ([A-Z]{4}) to", seen)) is None:
            assert time.monotonic() < deadline, seen
            if select.select([main_fd], [], [], 1)[0]:
                seen += os.read(main_fd, 4096)
        os.write(main_fd, found.group(1) + b"\n")
        assert proc.wait(timeout=60) == 0
    finally:
        os.close(main_fd)
        if proc.poll() is None:
            proc.kill()
    assert not VaultConfig.load(root / CONFIG_FILE).redaction.phone
    assert "confirmed at the console" in sh(root, "git", "log", "-1", "--format=%B")
