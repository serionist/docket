"""Windows Hello through UserConsentVerifier, with ctypes and no package (DR-0573), and when it counts as set up
(DR-0574).

The flows here (`availability`, `verify`) run against `Api`, a thin layer over the system's calls, so a test counts
every interface acquired and released and every async object closed; `_Win32` is that layer on Windows. The
interfaces and their IIDs are the SDK's (`UserConsentVerifierInterop.idl`, `windows.security.credentials.ui.h`):

- the activation factory of `Windows.Security.Credentials.UI.UserConsentVerifier` gives `IUserConsentVerifierStatics`,
  whose `CheckAvailabilityAsync` says whether Hello is set up, and `IUserConsentVerifierInterop`, whose
  `RequestVerificationForWindowAsync` is a desktop process's way to ask;
- each answer is an `IAsyncOperation`, polled through `IAsyncInfo` while the thread pumps its window's messages.

The dialog is anchored to an invisible window Docket makes over the console's window, never Windows Terminal's own.
On a cancel or a timeout the operation is cancelled and pumped until the cancel lands, the windows Docket's anchor
owns are closed, Hello's dialog among them, and only then is the operation closed and the anchor destroyed.
Every DLL loads from System32 only. Face recognition is presence, not consent, and is accepted (the README's table).
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from enum import IntEnum
from typing import Literal, Protocol

from docket import osprompt
from docket.osprompt import Answer, Found, NoPrompt, Prompt, Refusal, Refused, Verified

NAME = "Windows Hello"
CLASS = "Windows.Security.Credentials.UI.UserConsentVerifier"
STATICS = "af4f3f91-564c-4ddc-b8b5-973447627c65"
INTEROP = "39e050c3-4e74-441a-8dc0-b81104df949c"
ASYNC_INFO = "00000036-0000-0000-c000-000000000046"
# IAsyncOperation<UserConsentVerificationResult> and <UserConsentVerifierAvailability>, as the SDK's header has them.
VERIFICATION_OP = "fd596ffd-2318-558f-9dbe-d21df43764a5"
AVAILABILITY_OP = "ddd384f3-d818-5d83-ab4b-32119c28587c"
# AsyncStatus.
STARTED, COMPLETED, CANCELED = 0, 1, 2
# How long the availability check may take, which shows nothing.
CHECK_TIMEOUT = 10.0
PUMP_MS = 50
# After a cancel: the most time the operation gets to leave Started, and how long messages go on flowing after it does,
# so the cancel reaches Hello's own dialog before the operation is closed. Hello's status turns Canceled on this side
# at once; on Windows 11 the dialog stayed up when the operation was closed straight after (#143's hardware check).
CANCEL_GRACE = 2.0
CANCEL_LINGER = 0.5

Anchor = Literal["own", "foreign"]


class Availability(IntEnum):
    AVAILABLE = 0
    DEVICE_NOT_PRESENT = 1
    NOT_CONFIGURED_FOR_USER = 2
    DISABLED_BY_POLICY = 3
    DEVICE_BUSY = 4


class Result(IntEnum):
    VERIFIED = 0
    DEVICE_NOT_PRESENT = 1
    NOT_CONFIGURED_FOR_USER = 2
    DISABLED_BY_POLICY = 3
    DEVICE_BUSY = 4
    RETRIES_EXHAUSTED = 5
    CANCELED = 6


NO_INTEROP = "this Windows has no Windows Hello prompt for desktop programs (it takes Windows 10 1709 or later)"
POLICY_OFF = "Windows Hello is turned off by policy on this machine"
NO_DEVICE = "this machine has no Windows Hello, and neither a TPM nor a biometric device"
NOT_SET_UP = (
    "Windows Hello isn't set up for you, on a machine with a TPM or a biometric device: set it up in Settings, "
    "Accounts, Sign-in options, or see the README's console-code marker"
)
BUSY = "Windows Hello is busy; try again"


class Api(Protocol):
    """The system calls the flows need. Pointers are ints; a failed call raises `OSError`."""

    def init(self) -> None: ...
    def uninit(self) -> None: ...
    def factory(self, iid: str) -> int: ...
    def query(self, ptr: int, iid: str) -> int | None: ...
    def release(self, ptr: int) -> None: ...
    def check_availability(self, statics: int) -> int: ...
    def request(self, interop: int, window: int, line: str) -> int: ...
    def status(self, info: int) -> int: ...
    def error_code(self, info: int) -> int: ...
    def cancel(self, info: int) -> None: ...
    def close(self, info: int) -> None: ...
    def results(self, op: int) -> int: ...
    def window(self, anchor: Anchor) -> tuple[int, bool]: ...
    def dismiss(self, window: int) -> int: ...
    def destroy(self, window: int) -> None: ...
    def pump(self, ms: int) -> None: ...
    def tpm(self) -> bool: ...
    def biometric(self) -> bool: ...


def found_by(answer: Availability | None, device: bool) -> Found:
    """What an availability answer means (DR-0574). `answer` is None on a Windows without the interop interface;
    `device` is whether the machine has a TPM or a biometric device, where an owner's process could have deleted the
    Hello container to make Hello answer that it isn't set up."""
    if answer is None:
        return NoPrompt(NO_INTEROP)
    if answer is Availability.AVAILABLE:
        return Prompt(NAME, ask)
    if answer is Availability.DISABLED_BY_POLICY:
        return NoPrompt(POLICY_OFF)
    if answer is Availability.DEVICE_BUSY:
        return Refusal(BUSY)
    return Refusal(NOT_SET_UP) if device else NoPrompt(NO_DEVICE)


def has_device(api: Api) -> bool:
    """A TPM or a fingerprint or face unit. A call that fails counts as one, so breaking it never gives the code."""
    try:
        return api.tpm() or api.biometric()
    except OSError:
        return True


def availability(api: Api) -> Availability | None:
    """Hello's own answer, or None without the interop interface. Shows nothing."""
    api.init()
    try:
        statics = api.factory(STATICS)
        try:
            interop = api.query(statics, INTEROP)
            if interop is None:
                return None
            api.release(interop)
            value = wait(api, api.check_availability(statics), time.monotonic() + CHECK_TIMEOUT, threading.Event())
            if value is None:
                raise OSError("Windows Hello's availability didn't answer in time")
            return Availability(value)
        finally:
            api.release(statics)
    finally:
        api.uninit()


def verify(api: Api, line: str, deadline: float, cancel: threading.Event, anchor: Anchor = "own") -> int | None:
    """Ask Hello, the dialog anchored to Docket's own window (or, for the hardware test's comparison, the terminal's or
    the foreground one): the result, or None when cancelled or out of time."""
    api.init()
    try:
        window, owned = api.window(anchor)
        try:
            interop = api.factory(INTEROP)
            try:
                # Only Docket's own window is dismissed: the terminal's owns windows of its own.
                dismiss = (lambda: api.dismiss(window)) if owned else None
                return wait(api, api.request(interop, window, line), deadline, cancel, dismiss)
            finally:
                api.release(interop)
        finally:
            # Last, once the operation is closed: the anchor goes after the dialog it owns.
            if owned:
                api.destroy(window)
    finally:
        api.uninit()


def wait(
    api: Api, op: int, deadline: float, cancel: threading.Event, dismiss: Callable[[], int] | None = None
) -> int | None:
    """An async operation's value, pumping messages while it runs; None once cancelled or past the deadline. A
    cancelled operation is cancelled, pumped until the cancel lands, its dialog dismissed (`dismiss`), and only then
    closed; every interface is released on every path."""
    try:
        info = api.query(op, ASYNC_INFO)
        if info is None:
            raise OSError("the operation has no IAsyncInfo")
        try:
            while (state := api.status(info)) == STARTED:
                if cancel.is_set() or time.monotonic() > deadline:
                    api.cancel(info)
                    _settle(api, info)
                    if dismiss is not None and dismiss():
                        _pump_for(api, CANCEL_LINGER)
                    return None
                api.pump(PUMP_MS)
            if state == COMPLETED:
                return api.results(op)
            if state == CANCELED:
                return None
            raise OSError(f"Windows answered with error {api.error_code(info) & 0xFFFFFFFF:#010x}")
        finally:
            api.close(info)
            api.release(info)
    finally:
        api.release(op)


def _settle(api: Api, info: int) -> None:
    """Pump while a cancelled operation leaves Started, since Close is refused while it runs, then a little longer, so
    the cancel reaches Hello's dialog: at most CANCEL_GRACE in all."""
    end = time.monotonic() + CANCEL_GRACE
    while api.status(info) == STARTED and time.monotonic() < end:
        api.pump(PUMP_MS)
    _pump_for(api, min(CANCEL_LINGER, max(end - time.monotonic(), 0.0)))


def _pump_for(api: Api, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        api.pump(PUMP_MS)


def answer(result: int | None) -> Answer:
    if result == Result.VERIFIED:
        return Verified()
    if result is None:
        return Refused("no answer in time, or cancelled")
    reasons = {
        Result.CANCELED: "cancelled",
        Result.RETRIES_EXHAUSTED: "too many tries",
        Result.DEVICE_BUSY: "Windows Hello is busy",
    }
    return Refused(reasons.get(Result(result), "Windows Hello isn't available now"))


def ask(act: str) -> Answer:  # pragma: no cover - shows the real prompt; the maintainer's hardware test runs it
    return ask_with(_api(), act)


def ask_with(api: Api, act: str, anchor: Anchor = "own") -> Answer:
    """Ask Hello about `act`, such as "release a held recording": the dialog reads "Docket: <act>"."""
    deadline = time.monotonic() + osprompt.TIMEOUT

    def work(cancel: threading.Event) -> Answer:
        try:
            return answer(verify(api, f"Docket: {act}", deadline, cancel, anchor))
        except OSError as e:
            return Refused(f"Windows Hello failed: {e}")

    return osprompt.cancellable(work)


def find_with(api: Api) -> Found:
    try:
        found = availability(api)
    except OSError as e:
        return Refusal(f"Windows Hello's availability can't be read: {e}")
    return found_by(found, has_device(api))


def find() -> Found:  # pragma: no cover - the system's own answer; test_osprompt_system runs it on Windows
    return find_with(_api())


def _api() -> Api:  # pragma: no cover
    if sys.platform != "win32":
        raise OSError("Windows Hello is Windows'")
    from docket.osprompt import win32  # noqa: PLC0415

    return win32.Win32()
