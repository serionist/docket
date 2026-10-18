"""The OS prompt's decisions (DR-0572 to DR-0574), with the system's calls faked, on every system: what each answer
means, Windows' flows counting every interface and async object, macOS's codes, polkit's command and exit codes."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import empty_vault, make_config

from docket import osprompt
from docket.cli import main

# win32 too: its calls are Windows', and on every other system importing it defines nothing.
from docket.osprompt import NoPrompt, Prompt, Refusal, Refused, Verified, linux, macos, marker, win32, windows
from docket.osprompt.windows import Availability, Result

# -- what this machine asks ----------------------------------------------------------------------------------------


def test_win32_imports_on_every_system() -> None:
    assert win32.__name__ == "docket.osprompt.win32"


def test_vault_status_names_the_yes_and_a_marker_that_doesnt_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = empty_vault(tmp_path, make_config())
    monkeypatch.setattr(osprompt, "status", lambda: ["refused: set it up", "the marker at /m doesn't count: a link"])
    assert main(["vault", "status", "--vault", str(vault.root)]) == 0
    out = capsys.readouterr().out
    assert "yes      refused: set it up\nmarker   the marker at /m doesn't count: a link\n" in out


def _real_find(monkeypatch: pytest.MonkeyPatch, verdict: marker.Verdict, found: osprompt.Found) -> None:
    """The package's own find and status, with the marker's verdict and the system's answer given."""
    monkeypatch.setattr(osprompt, "find", _find)
    monkeypatch.setattr(osprompt, "status", _status)
    monkeypatch.setattr(marker, "here", lambda: verdict)
    monkeypatch.setattr(osprompt, "system", lambda: found)


# The package's own, taken at import, before the autouse fixture puts the tests' stand-in in their place.
_find = osprompt.find
_status = osprompt.status


def test_a_marker_that_counts_gives_the_code_before_the_system_is_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    hello = Prompt("Windows Hello", lambda act: Verified())
    _real_find(monkeypatch, marker.Verdict("C:/m", present=True, counts=True), hello)
    assert osprompt.find() == NoPrompt("an admin's marker at C:/m says so")
    assert osprompt.status() == ["the console code: an admin's marker at C:/m says so"]


def test_without_a_marker_the_system_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    hello = Prompt("Windows Hello", lambda act: Verified())
    _real_find(monkeypatch, marker.Verdict("C:/m"), hello)
    assert osprompt.find() is hello
    assert osprompt.status() == ["asked by Windows Hello"]


def test_a_marker_that_doesnt_count_says_why_and_leaves_the_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    _real_find(monkeypatch, marker.Verdict("/m", present=True, why="you could replace /etc"), Refusal("set it up"))
    assert osprompt.find() == Refusal("set it up")
    assert osprompt.status() == ["refused: set it up", "the marker at /m doesn't count: you could replace /etc"]


def test_cancellable_hands_back_the_answer_and_the_error() -> None:
    assert osprompt.cancellable(lambda cancel: Verified()) == Verified()

    def fails(cancel: threading.Event) -> osprompt.Answer:
        raise OSError("broken")

    with pytest.raises(OSError, match="broken"):
        osprompt.cancellable(fails)


def test_ctrl_c_tells_the_prompt_to_cancel_and_goes_on(monkeypatch: pytest.MonkeyPatch) -> None:
    told = threading.Event()

    def waits(cancel: threading.Event) -> osprompt.Answer:
        cancel.wait(5)
        told.set()
        return Refused("cancelled")

    real_join = threading.Thread.join
    calls: list[float | None] = []

    def join(self: threading.Thread, timeout: float | None = None) -> None:
        calls.append(timeout)
        if len(calls) == 1:
            raise KeyboardInterrupt
        real_join(self, timeout)

    monkeypatch.setattr(threading.Thread, "join", join)
    with pytest.raises(KeyboardInterrupt):
        osprompt.cancellable(waits)
    assert told.is_set()
    assert calls[1] == osprompt.WIND_DOWN


# -- Windows -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "device", "found"),
    [
        (None, True, NoPrompt(windows.NO_INTEROP)),
        (Availability.AVAILABLE, True, "prompt"),
        (Availability.DISABLED_BY_POLICY, True, NoPrompt(windows.POLICY_OFF)),
        (Availability.DEVICE_BUSY, False, Refusal(windows.BUSY)),
        (Availability.NOT_CONFIGURED_FOR_USER, True, Refusal(windows.NOT_SET_UP)),
        (Availability.DEVICE_NOT_PRESENT, True, Refusal(windows.NOT_SET_UP)),
        (Availability.NOT_CONFIGURED_FOR_USER, False, NoPrompt(windows.NO_DEVICE)),
        (Availability.DEVICE_NOT_PRESENT, False, NoPrompt(windows.NO_DEVICE)),
    ],
)
def test_each_hello_answer_maps_to_the_prompt_the_code_or_a_refusal(
    answer: Availability | None, device: bool, found: object
) -> None:
    """A deleted Hello container on a machine with a TPM or a biometric device refuses, never gives the code
    (DR-0574)."""
    got = windows.found_by(answer, device)
    if found == "prompt":
        assert got == Prompt(windows.NAME, windows.ask)
    else:
        assert got == found


class FakeApi:
    """windows.Api with every pointer counted: each acquired one must be released, each async object closed."""

    def __init__(
        self,
        *,
        statuses: list[int] | None = None,
        value: int = 0,
        interop: bool = True,
        info: bool = True,
        error: int = 0,
        tpm: bool | Exception = False,
        biometric: bool = False,
        fail: str | None = None,
        lands_after: int = 0,
    ) -> None:
        self.statuses = statuses if statuses is not None else [windows.COMPLETED]
        self.value = value
        self.has_interop = interop
        self.has_info = info
        self.error = error
        self._tpm = tpm
        self._biometric = biometric
        self.fail = fail
        self.live: set[int] = set()
        self.closed: list[int] = []
        self.cancelled = 0
        self.windows: list[int] = []
        self.calls: list[str] = []
        # Every call that winds an operation down, in order, for the timeout's sequence.
        self.log: list[str] = []
        self.lands_after = lands_after
        self.joined = 0
        self.lines: list[str] = []
        self._next = 100

    def _new(self) -> int:
        self._next += 1
        self.live.add(self._next)
        return self._next

    def _maybe_fail(self, name: str) -> None:
        self.calls.append(name)
        if self.fail == name:
            raise OSError(f"{name} failed")

    def init(self) -> None:
        self.joined += 1

    def uninit(self) -> None:
        self.joined -= 1

    def factory(self, iid: str) -> int:
        self._maybe_fail("factory")
        return self._new()

    def query(self, ptr: int, iid: str) -> int | None:
        assert ptr in self.live
        if iid == windows.INTEROP and not self.has_interop:
            return None
        if iid == windows.ASYNC_INFO and not self.has_info:
            return None
        return self._new()

    def release(self, ptr: int) -> None:
        assert ptr in self.live, ptr
        self.live.remove(ptr)
        self.log.append("release")

    def check_availability(self, statics: int) -> int:
        self._maybe_fail("check")
        return self._new()

    def request(self, interop: int, window: int, line: str) -> int:
        self._maybe_fail("request")
        self.lines.append(line)
        return self._new()

    def status(self, info: int) -> int:
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def error_code(self, info: int) -> int:
        return self.error

    def cancel(self, info: int) -> None:
        self.cancelled += 1
        self.log.append("cancel")
        self.statuses = [*[windows.STARTED] * self.lands_after, windows.CANCELED]

    def close(self, info: int) -> None:
        self.closed.append(info)
        self.log.append("close")

    def results(self, op: int) -> int:
        return self.value

    def window(self, anchor: windows.Anchor) -> tuple[int, bool]:
        self.windows.append(1 if anchor == "own" else 2)
        return (1, True) if anchor == "own" else (2, False)

    def destroy(self, window: int) -> None:
        self.windows.remove(window)
        self.log.append("destroy")

    def pump(self, ms: int) -> None:
        if self.log and self.log[-1] != "pump":
            self.log.append("pump")

    def tpm(self) -> bool:
        if isinstance(self._tpm, Exception):
            raise self._tpm
        return self._tpm

    def biometric(self) -> bool:
        return self._biometric

    def balanced(self) -> bool:
        return not self.live and self.joined == 0 and not [w for w in self.windows if w == 1]


def test_availability_reads_hellos_answer_and_releases_everything() -> None:
    api = FakeApi(statuses=[windows.STARTED, windows.COMPLETED], value=Availability.NOT_CONFIGURED_FOR_USER)
    assert windows.availability(api) is Availability.NOT_CONFIGURED_FOR_USER
    assert api.balanced()
    assert len(api.closed) == 1


def test_without_the_interop_interface_availability_is_none() -> None:
    api = FakeApi(interop=False)
    assert windows.availability(api) is None
    assert api.balanced()
    assert windows.find_with(api) == NoPrompt(windows.NO_INTEROP)


def test_an_availability_check_that_never_answers_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(windows, "CHECK_TIMEOUT", -1.0)
    api = FakeApi(statuses=[windows.STARTED])
    with pytest.raises(OSError, match="didn't answer in time"):
        windows.availability(api)
    assert api.cancelled == 1
    assert api.balanced()
    assert windows.find_with(FakeApi(statuses=[windows.STARTED])) == Refusal(
        "Windows Hello's availability can't be read: Windows Hello's availability didn't answer in time"
    )


class Slow(FakeApi):
    """An operation that runs on for one look after its cancel, then stops."""

    def cancel(self, info: int) -> None:
        self.cancelled += 1
        self.log.append("cancel")
        self.statuses = [windows.STARTED, windows.CANCELED]


def test_a_cancelled_operation_is_pumped_until_it_stops_then_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(osprompt, "TIMEOUT", -1.0)
    api = Slow(statuses=[windows.STARTED])
    assert windows.ask_with(api, "x") == Refused(windows.GAVE_UP)
    assert api.cancelled == 1
    assert api.balanced()


@pytest.fixture
def quick_linger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(windows, "CANCEL_LINGER", 0.01)


@pytest.mark.usefixtures("quick_linger")
def test_on_a_timeout_the_cancel_lands_before_the_operation_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancel, pump until the cancel lands and a little longer, then close and release, and destroy the anchor last.
    Windows may still leave its dialog up (#143's hardware check), so the refusal says to cancel it."""
    monkeypatch.setattr(osprompt, "TIMEOUT", -1.0)
    api = FakeApi(statuses=[windows.STARTED], lands_after=2)
    assert windows.ask_with(api, "x") == Refused(windows.GAVE_UP)
    wound = api.log[api.log.index("cancel") :]
    assert wound[:3] == ["cancel", "pump", "close"]
    assert wound[-1] == "destroy"
    assert api.balanced()
    assert "cancel it" in windows.GAVE_UP


class Watched(FakeApi):
    """Each status read after the cancel, kept."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.after: list[int] = []

    def status(self, info: int) -> int:
        state = super().status(info)
        if "cancel" in self.log:
            self.after.append(state)
        return state


@pytest.mark.usefixtures("quick_linger")
def test_the_pump_runs_until_the_cancel_lands() -> None:
    api = Watched(statuses=[windows.STARTED], lands_after=3)
    cancel = threading.Event()
    cancel.set()
    assert windows.verify(api, "x", float("inf"), cancel) is None
    assert api.after == [windows.STARTED] * 3 + [windows.CANCELED]
    assert api.log.count("pump") >= 1
    assert api.balanced()


@pytest.mark.usefixtures("quick_linger")
def test_a_cancel_that_never_lands_is_given_up_after_the_grace_and_still_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(windows, "CANCEL_GRACE", 0.05)
    monkeypatch.setattr(osprompt, "TIMEOUT", -1.0)
    api = Stuck(statuses=[windows.STARTED])
    assert windows.ask_with(api, "x") == Refused(windows.GAVE_UP)
    wound = api.log[api.log.index("cancel") :]
    assert wound[:3] == ["cancel", "pump", "close"]
    assert api.balanced()


def test_find_with_weighs_the_device_only_when_hello_isnt_set_up() -> None:
    no_device = FakeApi(value=Availability.NOT_CONFIGURED_FOR_USER)
    assert windows.find_with(no_device) == NoPrompt(windows.NO_DEVICE)
    tpm = FakeApi(value=Availability.NOT_CONFIGURED_FOR_USER, tpm=True)
    assert windows.find_with(tpm) == Refusal(windows.NOT_SET_UP)
    biometric = FakeApi(value=Availability.DEVICE_NOT_PRESENT, biometric=True)
    assert windows.find_with(biometric) == Refusal(windows.NOT_SET_UP)
    assert isinstance(windows.find_with(FakeApi(value=Availability.AVAILABLE)), Prompt)


def test_a_device_check_that_fails_counts_as_a_device() -> None:
    assert windows.has_device(FakeApi(tpm=OSError("no TBS")))
    assert not windows.has_device(FakeApi())


def test_verify_asks_on_docket_s_own_window_and_releases_everything() -> None:
    api = FakeApi(statuses=[windows.STARTED, windows.STARTED, windows.COMPLETED], value=Result.VERIFIED)
    assert windows.ask_with(api, "release a held recording") == Verified()
    assert api.lines == ["Docket: release a held recording"]
    assert api.balanced()
    assert len(api.closed) == 1


def test_verify_can_anchor_to_the_terminals_window_for_the_hardware_test() -> None:
    api = FakeApi(value=Result.CANCELED)
    assert windows.ask_with(api, "x", anchor="foreign") == Refused("cancelled")
    assert api.windows == [2]
    assert not api.live


def test_a_prompt_past_its_deadline_is_cancelled_closed_and_a_no(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(osprompt, "TIMEOUT", -1.0)
    api = FakeApi(statuses=[windows.STARTED])
    assert windows.ask_with(api, "x") == Refused(windows.GAVE_UP)
    assert api.cancelled == 1
    assert api.balanced()
    assert len(api.closed) == 1


class Stuck(FakeApi):
    """An operation that stays Started after its cancel."""

    def cancel(self, info: int) -> None:
        self.cancelled += 1
        self.log.append("cancel")


def test_a_cancel_mid_prompt_waits_for_it_to_wind_down(monkeypatch: pytest.MonkeyPatch) -> None:
    cancel = threading.Event()
    cancel.set()
    api = Stuck(statuses=[windows.STARTED])
    monkeypatch.setattr(windows, "CANCEL_GRACE", 0.0)
    assert windows.verify(api, "x", float("inf"), cancel) is None
    assert api.balanced()


def test_a_prompt_that_errs_names_windows_code_and_releases_everything() -> None:
    api = FakeApi(statuses=[3], error=-2147024891)
    assert windows.ask_with(api, "x") == Refused("Windows Hello failed: Windows answered with error 0x80070005")
    assert api.balanced()


def test_a_request_or_an_operation_without_async_info_is_a_failure() -> None:
    api = FakeApi(fail="request")
    assert windows.ask_with(api, "x") == Refused("Windows Hello failed: request failed")
    assert api.balanced()
    api = FakeApi(info=False)
    assert windows.ask_with(api, "x") == Refused("Windows Hello failed: the operation has no IAsyncInfo")
    assert api.balanced()
    api = FakeApi(fail="factory")
    assert windows.ask_with(api, "x") == Refused("Windows Hello failed: factory failed")
    assert api.balanced()


def test_an_operation_cancelled_by_windows_is_a_no() -> None:
    api = FakeApi(statuses=[windows.CANCELED])
    assert windows.ask_with(api, "x") == Refused(windows.GAVE_UP)


@pytest.mark.parametrize(
    ("result", "why"),
    [
        (Result.CANCELED, "cancelled"),
        (Result.RETRIES_EXHAUSTED, "too many tries"),
        (Result.DEVICE_BUSY, "Windows Hello is busy"),
        (Result.NOT_CONFIGURED_FOR_USER, "Windows Hello isn't available now"),
    ],
)
def test_each_hello_result_but_verified_is_a_no(result: Result, why: str) -> None:
    assert windows.answer(result) == Refused(why)


# -- macOS ---------------------------------------------------------------------------------------------------------


def test_macos_can_evaluate_maps_to_the_prompt_the_code_or_the_marker() -> None:
    assert macos.found_by(True, None) == Prompt(macos.NAME, macos.ask)
    assert macos.found_by(False, macos.PASSCODE_NOT_SET) == NoPrompt(macos.NO_LOGIN)
    refused = macos.found_by(False, -1007)
    assert isinstance(refused, Refusal)
    assert "can't use LocalAuthentication (code -1007)" in refused.why
    assert "marker" in refused.why


@pytest.mark.parametrize(
    ("code", "why"),
    [
        (macos.USER_CANCEL, "cancelled, or no answer in time"),
        (macos.SYSTEM_CANCEL, "cancelled, or no answer in time"),
        (macos.APP_CANCEL, "cancelled, or no answer in time"),
        (macos.AUTHENTICATION_FAILED, "it didn't recognise you"),
        (macos.NOT_INTERACTIVE, "there is no desktop to ask on: run the command at the Mac itself"),
    ],
)
def test_macos_replies_but_success_are_a_no(code: int, why: str) -> None:
    assert macos.answer(False, code) == Refused(why)


def test_any_other_macos_code_refuses_and_names_the_marker() -> None:
    """One branch: a caller LocalAuthentication won't serve has no second route to the code (DR-0573)."""
    assert macos.answer(True, None) == Verified()
    refused = macos.answer(False, macos.INVALID_CONTEXT)
    assert isinstance(refused, Refused)
    assert "code -10" in refused.why
    assert "marker" in refused.why


# -- Linux ---------------------------------------------------------------------------------------------------------


def test_polkit_is_the_prompt_only_with_pkcheck_and_dockets_action_installed(tmp_path: Path) -> None:
    pkcheck = tmp_path / "pkcheck"
    actions = tmp_path / "actions"
    actions.mkdir()
    missing = linux.find((str(actions),), str(pkcheck))
    assert missing == NoPrompt(f"polkit isn't installed here ({pkcheck} is missing)")
    pkcheck.write_text("", encoding="utf-8")
    assert linux.find((str(actions),), str(pkcheck)) == NoPrompt(
        f"polkit doesn't know Docket's action yet: {linux.install_line()}"
    )
    (actions / linux.POLICY.name).write_text("", encoding="utf-8")
    assert linux.find((str(tmp_path / "none"), str(actions)), str(pkcheck)) == Prompt(linux.NAME, linux.ask)


def test_pkcheck_asks_for_dockets_own_process_by_its_absolute_path() -> None:
    assert linux.command(4321, 98765, 1000) == [
        "/usr/bin/pkcheck",
        "--action-id",
        "io.github.serionist.docket.confirm",
        "--process",
        "4321,98765,1000",
        "--allow-user-interaction",
        "--enable-internal-agent",
    ]


@pytest.mark.parametrize(
    ("code", "answer"),
    [
        (0, Verified()),
        (1, Refused("polkit said no")),
        (2, Refused("no authentication agent could ask: run the command in a terminal of your own")),
        (3, Refused("dismissed")),
        (127, Refused("pkcheck failed with exit code 127")),
        (None, Refused("no answer in time")),
    ],
)
def test_each_pkcheck_exit_code(code: int | None, answer: osprompt.Answer) -> None:
    assert linux.answer(code) == answer


def test_pkcheck_runs_with_a_timeout_and_a_timeout_is_no_answer() -> None:
    seen: list[dict[str, Any]] = []

    def ends(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        seen.append(kwargs)
        return subprocess.CompletedProcess(cmd, 3)

    def hangs(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(cmd, 1)

    assert linux.run(["pkcheck"], 5.0, ends) == 3
    assert seen == [{"timeout": 5.0, "check": False}]
    assert linux.run(["pkcheck"], 5.0, hangs) is None


def test_a_processs_start_time_is_read_past_a_name_holding_spaces_and_parentheses(tmp_path: Path) -> None:
    stat = tmp_path / "stat"
    fields = " ".join(str(n) for n in range(3, 53))
    stat.write_text(f"4321 (a (b) c) {fields}\n", encoding="utf-8")
    assert linux.start_time(str(stat)) == 22


def test_the_action_file_asks_every_time_and_never_keeps_an_answer() -> None:
    text = linux.POLICY.read_text(encoding="utf-8")
    assert f'<action id="{linux.ACTION}">' in text
    for kind in ("allow_any", "allow_inactive", "allow_active"):
        assert f"<{kind}>auth_self</{kind}>" in text
    assert "_keep" not in text.split("<policyconfig>", 1)[1]
