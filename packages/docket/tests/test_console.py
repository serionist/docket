"""The owner's yes at their own console (DR-0518): what counts as a console, the code, the question."""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from docket_testkit import FakePrompt, consuming_project, git, sh

from docket import console, osprompt
from docket.errors import DocketError
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


# -- the OS prompt (DR-0572) ----------------------------------------------------------------------------------------


def test_with_a_prompt_set_up_enter_and_its_yes_are_the_only_yes(
    os_prompt: Callable[..., FakePrompt], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hello = os_prompt(osprompt.Verified())
    asked: list[str] = []
    monkeypatch.setattr(console, "ask", lambda prompt: asked.append(prompt) or "")
    assert console.confirm(["the screen"], "make this change", "leave it", "it stays", act="do a thing") == "yes"
    assert asked == ["Press Enter to make this change with Fake Hello, n to leave it, q to stop: "]
    assert hello.acts == ["do a thing"]
    assert capsys.readouterr().out == "the screen\n"


def test_a_prompt_that_doesnt_confirm_is_a_no_and_never_asks_the_code(
    os_prompt: Callable[..., FakePrompt], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    os_prompt(osprompt.Refused("cancelled"))
    asked: list[str] = []
    monkeypatch.setattr(console, "ask", lambda prompt: asked.append(prompt) or "")
    assert console.confirm([], "make this change", "leave it", "it stays", act="do a thing") == "no"
    assert len(asked) == 1
    assert "Type" not in asked[0]
    assert "Fake Hello didn't confirm it (cancelled); it stays" in capsys.readouterr().out


@pytest.mark.parametrize(("typed", "answer", "said"), [("n", "no", False), ("q", "stop", False), ("y", "no", True)])
def test_anything_but_enter_asks_no_prompt(
    typed: str, answer: str, said: bool, os_prompt: Callable[..., FakePrompt], capsys: pytest.CaptureFixture[str]
) -> None:
    hello = os_prompt(osprompt.Verified())
    assert console.confirm([], "make this change", "leave it", "it stays", act="do a thing", asker=lambda p: typed) == (
        answer
    )
    assert hello.acts == []
    assert ("that isn't Enter; it stays" in capsys.readouterr().out) == said


def test_without_a_prompt_the_screen_says_why_and_the_code_is_asked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(console, "ask", lambda prompt: "q")
    assert console.confirm(["the screen"], "make this change", "leave it", "it stays", act="do a thing") == "stop"
    assert capsys.readouterr().out == "the screen\nthe console code: no OS prompt in tests\n"


def test_a_machine_that_cant_ask_refuses_before_the_screen(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(osprompt, "find", lambda: osprompt.Refusal("set up Windows Hello"))
    with pytest.raises(DocketError, match="nothing was asked: set up Windows Hello"):
        console.confirm(["the screen"], "make this change", "leave it", "it stays", act="do a thing")
    assert capsys.readouterr().out == ""


def test_which_yes_is_the_prompts_own_status() -> None:
    assert console.which_yes() == ["the console code: no OS prompt in tests"]


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


# The child stands in for a machine without an OS prompt, as the tests' fixture does in this process: no test reaches
# the system's prompt, and the code is what the terminal test types back.
CHILD = (
    "import sys; from docket import osprompt; osprompt.find = lambda: osprompt.NoPrompt('a test'); "
    "from docket.cli import main; sys.exit(main(sys.argv[1:]))"
)


def _docket(*args: str, **kwargs: object) -> subprocess.Popen[bytes]:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    return subprocess.Popen([sys.executable, "-c", CHILD, *args], env=env, **kwargs)  # type: ignore[call-overload]


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
    # All three on the terminal, as in one: input() writes its prompt to stderr when no readline is loaded.
    proc = _docket(
        "redaction",
        "set",
        "phone",
        "off",
        "--vault",
        str(root),
        stdin=child_fd,
        stdout=child_fd,
        stderr=child_fd,
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
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        os.close(main_fd)
    assert not VaultConfig.load(root / CONFIG_FILE).redaction.phone
    assert "confirmed at the console" in sh(root, "git", "log", "-1", "--format=%B")
