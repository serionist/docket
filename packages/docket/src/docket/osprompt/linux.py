"""polkit on Linux (DR-0573): `/usr/bin/pkcheck`, by its absolute path, for Docket's own process, with the action
`io.github.serionist.docket.confirm`.

The action file ships beside this module and the owner installs it once with sudo; polkit reads it only from its own
folders, which are root's. `--enable-internal-agent` lets pkcheck ask at the console when no desktop agent is
registered, as over SSH. The action asks `auth_self` every time, never `auth_self_keep`, which would pass again for
five minutes; its message is fixed in the file, as polkit refuses details from a caller that isn't root.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from docket import osprompt
from docket.osprompt import Answer, Found, NoPrompt, Prompt, Refused, Verified

NAME = "polkit"
ACTION = "io.github.serionist.docket.confirm"
POLICY = Path(__file__).with_name(f"{ACTION.rsplit('.', 1)[0]}.policy")
FOLDERS = (
    "/usr/share/polkit-1/actions",
    "/etc/polkit-1/actions",
    "/run/polkit-1/actions",
    "/usr/local/share/polkit-1/actions",
)
PKCHECK = "/usr/bin/pkcheck"
# pkcheck's exit codes, from its manual.
NOT_AUTHORIZED, NO_AGENT, DISMISSED = 1, 2, 3


def install_line() -> str:
    return f"sudo install -m 0644 {POLICY} /usr/share/polkit-1/actions/"


def find(folders: Sequence[str] = FOLDERS, pkcheck: str = PKCHECK) -> Found:
    if not Path(pkcheck).is_file():
        return NoPrompt(f"polkit isn't installed here ({pkcheck} is missing)")
    if not any(Path(folder, POLICY.name).is_file() for folder in folders):
        return NoPrompt(f"polkit doesn't know Docket's action yet: {install_line()}")
    return Prompt(NAME, ask)


def command(pid: int, start: int, uid: int, pkcheck: str = PKCHECK) -> list[str]:
    return [
        pkcheck,
        "--action-id",
        ACTION,
        "--process",
        f"{pid},{start},{uid}",
        "--allow-user-interaction",
        "--enable-internal-agent",
    ]


def answer(code: int | None) -> Answer:
    """What pkcheck's exit code says; None when it didn't end in time."""
    if code == 0:
        return Verified()
    reasons = {
        None: "no answer in time",
        NOT_AUTHORIZED: "polkit said no",
        NO_AGENT: "no authentication agent could ask: run the command in a terminal of your own",
        DISMISSED: "dismissed",
    }
    return Refused(reasons.get(code, f"pkcheck failed with exit code {code}"))


Runner = Callable[..., "subprocess.CompletedProcess[bytes]"]


def run(cmd: list[str], timeout: float, runner: Runner = subprocess.run) -> int | None:
    """pkcheck on the console Docket runs at, so its text agent can ask there; None on the timeout."""
    try:
        # A fixed command by its absolute path, never a shell.
        return runner(cmd, timeout=timeout, check=False).returncode
    except subprocess.TimeoutExpired:
        return None


def ask(act: str) -> Answer:  # pragma: no cover - asks polkit for real; CI runs it with a rule that answers
    del act  # polkit shows its action's own message
    if sys.platform == "win32":
        raise OSError("polkit is Linux's")
    return answer(run(command(os.getpid(), start_time(), os.getuid()), osprompt.TIMEOUT))


def start_time(stat: str = "/proc/self/stat") -> int:
    """The process's start time in clock ticks: field 22 of its stat line, read after the name in parentheses, which
    may hold spaces or parentheses itself."""
    text = Path(stat).read_text(encoding="utf-8")
    return int(text[text.rindex(")") + 2 :].split()[19])
