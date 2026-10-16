"""The owner's yes at their own console (DR-0518): what counts as a console, the code, the question."""

from __future__ import annotations

import io
import os
import re
from pathlib import Path

import pytest

from docket import console
from docket.sources import release as releasing


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
