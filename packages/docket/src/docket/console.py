"""The owner's yes at their own console, for a release (DR-0202) and for a vault.yaml change that isn't only stricter
(DR-0517, DR-0518): refused in Claude Code's shell and without a console on stdin and stdout, then a code drawn fresh
and typed back.

The entry points that ask, `release()` and `settings.rewrite()`, take no answer as a parameter, so a Python caller, a
pipe or Claude Code's shell gets a refusal, not a yes; `typed_back`'s `asker` is only the release's own name for
`ask`. None of it stops a process running as the user on purpose: it makes the yes a deliberate act, which the skills
forbid Claude. Callers use the module (`console.ask(...)`), so a test stands in for the owner in one place.
"""

from __future__ import annotations

import os
import secrets
import sys
from collections.abc import Callable, Sequence
from typing import Literal, TextIO

from docket.plaintext import printable

# Four letters with no look-alikes (no I or O), drawn fresh for each question.
CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_LENGTH = 4
RELEASE = "a release"
LOOSENING = "a vault.yaml change that isn't only stricter"

Answer = Literal["yes", "no", "stop"]


def problem(act: str) -> str | None:
    """Why this process can't ask the owner about `act`, or None. Claude Code's shell sets CLAUDECODE; a pipe, a
    redirect or a service has no console on stdin or stdout."""
    if os.environ.get("CLAUDECODE"):
        return (
            f"this shell is Claude Code's (CLAUDECODE is set): {act} is yours to confirm, so run the command in "
            "your own terminal"
        )
    if not (is_console(sys.stdin) and is_console(sys.stdout)):
        return (
            f"{act} asks you at a console: run it in your own terminal with nothing piped in or out (in Git Bash's "
            "own window, through winpty)"
        )
    return None


def is_console(stream: TextIO | None) -> bool:
    try:
        fd = stream.fileno() if stream is not None else -1
    except (AttributeError, OSError, ValueError):
        return False
    return fd >= 0 and _console_fd(fd)


if sys.platform == "win32":  # pragma: no cover - one of the two runs on each system; test_console runs both
    import ctypes
    import msvcrt
    from ctypes import wintypes

    def _console_fd(fd: int) -> bool:
        """A real console: GetConsoleMode succeeds on its handle. isatty alone is true for a redirect from NUL."""
        try:
            handle = msvcrt.get_osfhandle(fd)
        except OSError:
            return False
        mode = wintypes.DWORD()
        return bool(ctypes.windll.kernel32.GetConsoleMode(wintypes.HANDLE(handle), ctypes.byref(mode)))

else:  # pragma: no cover

    def _console_fd(fd: int) -> bool:
        return os.isatty(fd)


def code() -> str:
    return "".join(secrets.choice(CODE_LETTERS) for _ in range(CODE_LENGTH))


def ask(prompt: str) -> str:
    """The owner's answer, read at the console; the end of input stops."""
    try:
        return input(prompt)
    except EOFError:
        return "q"


def say(line: str = "") -> None:
    sys.stdout.write(printable(line) + "\n")
    sys.stdout.flush()


def typed_back(
    lines: Sequence[str], do: str, keep: str, kept: str, *, asker: Callable[[str], str] | None = None
) -> Answer:
    """Show the screen and ask: "yes" for the code typed back, "stop" for q, "no" for anything else. `do` is what the
    code does, `keep` what n does, `kept` what stays when the answer isn't the code. `asker` is the caller's own name
    for `ask`, for a caller whose tests stand in for the owner under that name."""
    for line in lines:
        say(line)
    drawn = code()
    answer = (asker if asker is not None else ask)(f"Type {drawn} to {do}, n to {keep}, q to stop: ").strip()
    if answer.lower() == "q":
        return "stop"
    if answer.upper() == drawn:
        return "yes"
    if answer.lower() != "n":
        say(f"that isn't the code; {kept}")
    return "no"
