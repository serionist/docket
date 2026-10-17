"""The owner's yes at their own console, for a release (DR-0202), a vault.yaml change that isn't only stricter
(DR-0517, DR-0518), a recording's decisions confirmed as cuts and an export of the media keys (DR-0575): refused in
Claude Code's shell and without a console on stdin and stdout, then the screen, then the OS prompt where this machine
has one set up, or a code drawn fresh and typed back where it hasn't (DR-0572).

`confirm` is the one question. Once a prompt is set up, only its verified answer is a yes: a cancel, a failure or no
answer in time is a no, and the code is never asked then (DR-0574). The entry points that ask take no answer as a
parameter, so a Python caller, a pipe or Claude Code's shell gets a refusal, not a yes; `asker` is only the release's
own name for `ask`. The prompt says a person answered, not that Docket's code is intact: a process running as the user
can still patch Docket, the floor DR-0202 accepts. What it ends is a pseudo-terminal of an agent's own reading the code
and typing it back. Callers use the module (`console.ask(...)`), so a test stands in for the owner in one place.
"""

from __future__ import annotations

import getpass
import os
import secrets
import sys
from collections.abc import Callable, Sequence
from typing import Literal, TextIO

from docket import osprompt
from docket.errors import DocketError
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


def ask_hidden(prompt: str) -> str:
    """A secret the owner types at the console, not echoed; the end of input stops."""
    try:
        return getpass.getpass(prompt)
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


def confirm(
    lines: Sequence[str], do: str, keep: str, kept: str, *, act: str, asker: Callable[[str], str] | None = None
) -> Answer:
    """Show the screen and ask: "yes" only for the OS prompt's verified answer, or, where this machine has none, for the
    code typed back; "stop" for q, "no" for anything else. `act` is what the OS prompt names, such as "release a held
    recording". A machine that can't ask at all refuses before the screen is shown."""
    found = osprompt.find()
    if isinstance(found, osprompt.Refusal):
        raise DocketError(f"nothing was asked: {found.why}")
    if isinstance(found, osprompt.NoPrompt):
        return typed_back([*lines, f"the console code: {found.why}"], do, keep, kept, asker=asker)
    for line in lines:
        say(line)
    typed = (asker if asker is not None else ask)(f"Press Enter to {do} with {found.name}, n to {keep}, q to stop: ")
    typed = typed.strip().lower()
    if typed == "q":
        return "stop"
    if typed:
        if typed != "n":
            say(f"that isn't Enter; {kept}")
        return "no"
    answer = found.ask(act)
    if isinstance(answer, osprompt.Verified):
        return "yes"
    say(f"{found.name} didn't confirm it ({answer.why}); {kept}")
    return "no"


def which_yes() -> list[str]:
    """`docket vault status`'s lines for the owner's yes on this machine. Shows no prompt."""
    return osprompt.status()
