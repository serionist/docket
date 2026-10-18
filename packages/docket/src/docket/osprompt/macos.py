"""LocalAuthentication on macOS, through rubicon-objc (DR-0573): `LAContext.evaluatePolicy` with
`LAPolicyDeviceOwnerAuthentication`, so Touch ID, an Apple Watch or the login password, a fresh context each time.

`found_by` and `answer` read what the system said; the calls themselves are `_Objc`'s, run on macOS only. Every code but
the ones listed means this caller can't use LocalAuthentication, and refuses, naming the marker as the way out: one
branch, with no second route to the code (DR-0573, DR-0574).
"""

from __future__ import annotations

import importlib
import sys
import threading
import time
from typing import Any

from docket import osprompt
from docket.osprompt import Answer, Found, NoPrompt, Prompt, Refusal, Refused, Verified

NAME = "Touch ID or your password"
# LAPolicyDeviceOwnerAuthentication: biometrics, the watch, or the login password.
POLICY = 2
# LAError's codes, as LocalAuthentication's header has them.
AUTHENTICATION_FAILED = -1
USER_CANCEL = -2
SYSTEM_CANCEL = -4
PASSCODE_NOT_SET = -5
APP_CANCEL = -9
INVALID_CONTEXT = -10
NOT_INTERACTIVE = -1004

NO_LOGIN = "this Mac has no login password, so LocalAuthentication has nothing to ask"
CANT_USE = (
    "this Python can't use LocalAuthentication (code {code}): see the README's console-code marker, which an admin "
    "makes with sudo"
)


def found_by(ok: bool, code: int | None) -> Found:
    """What `canEvaluatePolicy` said."""
    if ok:
        return Prompt(NAME, ask)
    if code == PASSCODE_NOT_SET:
        return NoPrompt(NO_LOGIN)
    return Refusal(CANT_USE.format(code=code))


def answer(ok: bool, code: int | None) -> Answer:
    """What `evaluatePolicy`'s reply said."""
    if ok:
        return Verified()
    if code in (USER_CANCEL, SYSTEM_CANCEL, APP_CANCEL):
        return Refused("cancelled, or no answer in time")
    if code == AUTHENTICATION_FAILED:
        return Refused("it didn't recognise you")
    if code == NOT_INTERACTIVE:
        return Refused("there is no desktop to ask on: run the command at the Mac itself")
    return Refused(CANT_USE.format(code=code))


def find() -> Found:  # pragma: no cover - the system's own answer; test_osprompt_system runs it on macOS
    try:
        objc = _Objc()
    except (ImportError, OSError, ValueError) as e:
        # Docket depends on rubicon-objc here: removing it must not give the code.
        return Refusal(f"LocalAuthentication can't be loaded ({e}); reinstall Docket, or see the README's marker")
    return found_by(*objc.can_evaluate())


def ask(act: str) -> Answer:  # pragma: no cover - shows the real prompt; the maintainer's hardware test runs it
    # macOS words it "<the program> is trying to <reason>", so the act leads.
    return osprompt.cancellable(lambda cancel: _Objc().evaluate(f"{act} in Docket", cancel))


class _Objc:  # pragma: no cover - macOS's own API; test_osprompt_system runs it there
    """The two calls, through rubicon-objc's `send_message` with every type given, since the library has no types for
    the checker."""

    ctypes: Any
    api: Any
    runtime: Any
    context: Any

    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise OSError("LocalAuthentication is macOS's")
        import ctypes  # noqa: PLC0415

        self.ctypes = ctypes
        self.api = importlib.import_module("rubicon.objc")
        self.runtime = importlib.import_module("rubicon.objc.runtime")
        self.runtime.load_library("LocalAuthentication")
        self.context = self.api.ObjCClass("LAContext")

    def _new(self) -> Any:
        return self.context.alloc().init()

    def _code(self, error: Any) -> int | None:
        if not error:
            return None
        return int(self.runtime.send_message(error, "code", restype=self.ctypes.c_long, argtypes=[]))

    def can_evaluate(self, context: Any = None) -> tuple[bool, int | None]:
        ctypes = self.ctypes
        error = self.runtime.objc_id()
        ok = self.runtime.send_message(
            context or self._new(),
            "canEvaluatePolicy:error:",
            ctypes.c_long(POLICY),
            ctypes.byref(error),
            restype=ctypes.c_bool,
            argtypes=[ctypes.c_long, ctypes.POINTER(self.runtime.objc_id)],
        )
        return bool(ok), None if ok else self._code(error.value)

    def evaluate(self, line: str, cancel: threading.Event, *, invalidated: bool = False) -> Answer:
        """Ask, and wait for the reply on LocalAuthentication's own queue. `invalidated` asks on a context invalidated
        first, which answers at once with no dialog: CI's way to run the reply's path."""
        ctypes = self.ctypes
        context = self._new()
        if invalidated:
            self.runtime.send_message(context, "invalidate", restype=None, argtypes=[])
        done = threading.Event()
        reply: list[tuple[bool, int | None]] = []

        def replied(success: bool, error: Any) -> None:
            reply.append((bool(success), self._code(error)))
            done.set()

        block = self.api.Block(replied, None, ctypes.c_bool, self.runtime.objc_id)
        self.runtime.send_message(
            context,
            "evaluatePolicy:localizedReason:reply:",
            ctypes.c_long(POLICY),
            self.api.at(line),
            block,
            restype=None,
            argtypes=[ctypes.c_long, self.runtime.objc_id, self.runtime.objc_block],
        )
        deadline = time.monotonic() + osprompt.TIMEOUT
        while not done.wait(0.1):
            if cancel.is_set() or time.monotonic() > deadline:
                # The reply comes with appCancel once the context is invalidated.
                self.runtime.send_message(context, "invalidate", restype=None, argtypes=[])
                done.wait(osprompt.WIND_DOWN)
                break
        return answer(*reply[0]) if reply else Refused("no answer in time, or cancelled")
