"""The OS prompt: the owner's yes from the system itself, where this machine has one set up (DR-0572 to DR-0575).

`find()` says, without showing anything, what this machine asks:

- a `Prompt`: Windows Hello, LocalAuthentication (Touch ID, the watch or the login password) or polkit, each asked
  with the act's own line;
- `NoPrompt`: the console code, and why;
- a `Refusal`: nothing can be asked, and why.

The marker an admin or root makes comes first (`marker`, DR-0574), then the system's own answer. Once a prompt is set
up, nothing here falls back to the code: a cancel, a failure or a timeout is a `Refused`. Only `docket.console` imports
this package (a test scans for it), so no page, route or tool asks or answers the prompt, and nothing here reads a
flag, a variable or a config key.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

# How long a prompt waits for the owner before it counts as a no.
TIMEOUT = 120.0
# How long a cancelled prompt gets to wind down before the question gives up on it.
WIND_DOWN = 10.0


@dataclass(frozen=True)
class Verified:
    """The system says the owner answered yes."""


@dataclass(frozen=True)
class Refused:
    """Anything but a yes from the prompt: cancelled, failed, no desktop, no answer in time."""

    why: str


Answer = Verified | Refused


@dataclass(frozen=True)
class Prompt:
    """A prompt set up on this machine. `ask` takes the act, such as "release a held recording", which each system
    words its own way, and blocks until the system answers."""

    name: str
    ask: Callable[[str], Answer] = field(compare=False)


@dataclass(frozen=True)
class NoPrompt:
    """No prompt here, and nothing an owner's process could have done made it so: the console code, and why."""

    why: str


@dataclass(frozen=True)
class Refusal:
    """Nothing can be asked on this machine now, and why; the way out is named in `why`."""

    why: str


Found = Prompt | NoPrompt | Refusal


def find() -> Found:
    """What this machine asks: the marker first, then the system. Shows nothing."""
    from docket.osprompt import marker  # noqa: PLC0415 - the marker's system calls load only when asked

    verdict = marker.here()
    if verdict.counts:
        return NoPrompt(f"an admin's marker at {verdict.path} says so")
    return system()


def status() -> list[str]:
    """`docket vault status`'s lines: which yes this machine asks and, for a marker that doesn't count, why not."""
    from docket.osprompt import marker  # noqa: PLC0415

    verdict = marker.here()
    found = NoPrompt(f"an admin's marker at {verdict.path} says so") if verdict.counts else system()
    lines = [describe(found)]
    if verdict.present and not verdict.counts:
        lines.append(f"the marker at {verdict.path} doesn't count: {verdict.why}")
    return lines


def describe(found: Found) -> str:
    if isinstance(found, Prompt):
        return f"asked by {found.name}"
    if isinstance(found, NoPrompt):
        return f"the console code: {found.why}"
    return f"refused: {found.why}"


def system() -> Found:
    """The system's own answer, without the marker."""
    if sys.platform == "win32":  # pragma: no cover - one of the three runs on each system; test_osprompt_system runs it
        from docket.osprompt import windows  # noqa: PLC0415

        return windows.find()
    elif sys.platform == "darwin":  # pragma: no cover  # noqa: RET505 - one branch per system, each excluded
        from docket.osprompt import macos  # noqa: PLC0415

        return macos.find()
    else:  # pragma: no cover
        from docket.osprompt import linux  # noqa: PLC0415

        return linux.find()


def cancellable(work: Callable[[threading.Event], Answer]) -> Answer:
    """Run a prompt on a thread of its own, so Ctrl+C at the console reaches this one: the prompt is told to cancel,
    given a moment to wind down, and the interrupt goes on."""
    cancel = threading.Event()
    out: list[Answer] = []
    failed: list[BaseException] = []

    def run() -> None:
        try:
            out.append(work(cancel))
        except BaseException as e:  # handed to the asking thread, which raises it there
            failed.append(e)

    thread = threading.Thread(target=run, name="docket-os-prompt", daemon=True)
    thread.start()
    try:
        while thread.is_alive():
            thread.join(0.1)
    except KeyboardInterrupt:
        cancel.set()
        thread.join(WIND_DOWN)
        raise
    if failed:
        raise failed[0]
    return out[0]
