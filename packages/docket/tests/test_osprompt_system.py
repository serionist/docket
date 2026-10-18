"""The OS prompt against the system itself (DR-0573, DR-0574): CI runs these on each runner with DOCKET_TEST_OSPROMPT
set, and a developer's machine never does. No test shows a prompt a person must answer: Windows asks a runner with no
Hello, macOS asks on a context invalidated first, and Linux asks polkit with a rule that answers.

Windows also runs the README's own marker lines on the real ProgramData path, judged with a token filtered as UAC
filters an admin's. Linux's stages come from the workflow, which makes the marker and installs the action file and its
rule with sudo between runs: OSPROMPT_TEST_STAGE is bare, marker, polkit-yes or polkit-no.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from docket import osprompt
from docket.osprompt import Refused, linux, macos, marker, windows

pytestmark = pytest.mark.osprompt
README = Path(__file__).parents[1] / "README.md"
STAGE = os.environ.get("OSPROMPT_TEST_STAGE", "bare")


def stage(name: str) -> pytest.MarkDecorator:
    return pytest.mark.skipif(sys.platform != "linux" or name != STAGE, reason=f"Linux's {name} stage")


# -- Windows -------------------------------------------------------------------------------------------------------

# Defined on Windows only, where the type checker knows win32's names; elsewhere these tests don't exist.
if sys.platform == "win32":

    def test_hellos_availability_and_the_device_check_answer_on_windows() -> None:
        from docket.osprompt import win32

        found = windows.availability(win32.Win32())
        device = windows.has_device(win32.Win32())
        assert windows.find_with(win32.Win32()) == windows.found_by(found, device)

    def test_a_request_on_a_runner_without_hello_is_never_a_yes() -> None:
        """The interop call, Docket's own window, the polling and the close, on the runner, where no one is there."""
        from docket.osprompt import win32

        result = windows.verify(win32.Win32(), "Docket: a CI test", time.monotonic() + 30, threading.Event())
        assert windows.answer(result) != osprompt.Verified()

    def _readme_lines() -> str:
        text = README.read_text(encoding="utf-8")
        found = re.search(r"<!-- the Windows marker's lines -->\s*```powershell\n(.*?)```", text, re.S)
        assert found is not None, "the README's Windows marker block"
        return found.group(1)

    def _powershell(script: str) -> None:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True)

    @pytest.fixture
    def readme_marker() -> Iterator[Path]:
        from docket.osprompt import win32

        folder = Path(win32.program_data(), "Docket")
        assert not folder.exists(), "the runner's ProgramData must start without Docket's folder"
        _powershell(_readme_lines())
        try:
            yield folder / marker.FILE
        finally:
            _powershell(f"Remove-Item -Recurse -Force '{folder}'")

    def _token(
        disable: Sequence[str] = ("S-1-5-32-544",), restrict: Sequence[str] = (), integrity: int = 0x2000
    ) -> int:
        """A copy of this process's token as UAC filters an admin's: DISABLE_MAX_PRIVILEGE, the SIDs given deny-only,
        the integrity given; as an identification token, which AccessCheck takes."""
        import ctypes
        from ctypes import wintypes

        from docket.osprompt import win32

        advapi32, kernel32 = win32.dll("advapi32"), win32.dll("kernel32")

        class SidAndAttributes(ctypes.Structure):
            _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

        def sids(texts: Sequence[str]) -> ctypes.Array[SidAndAttributes]:
            out = (SidAndAttributes * max(len(texts), 1))()
            for i, text in enumerate(texts):
                sid = ctypes.c_void_p()
                assert advapi32.ConvertStringSidToSidW(text, ctypes.byref(sid)), text
                out[i].Sid = sid.value
            return out

        process = wintypes.HANDLE()
        assert advapi32.OpenProcessToken(wintypes.HANDLE(kernel32.GetCurrentProcess()), 0xF01FF, ctypes.byref(process))
        made = wintypes.HANDLE()
        off, only = sids(disable), sids(restrict)
        assert advapi32.CreateRestrictedToken(
            process, 0x1, len(disable), off, 0, None, len(restrict), only if restrict else None, ctypes.byref(made)
        ), ctypes.get_last_error()
        label = sids([f"S-1-16-{integrity}"])
        label[0].Attributes = 0x20  # SE_GROUP_INTEGRITY
        size = ctypes.sizeof(SidAndAttributes) + advapi32.GetLengthSid(ctypes.c_void_p(label[0].Sid))
        assert advapi32.SetTokenInformation(made, 25, label, size), ctypes.get_last_error()
        copy = wintypes.HANDLE()
        assert advapi32.DuplicateTokenEx(made, 0x0008, None, 1, 2, ctypes.byref(copy)), ctypes.get_last_error()
        win32.close_handle(int(made.value or 0))
        win32.close_handle(int(process.value or 0))
        return int(copy.value or 0)

    def test_the_readmes_marker_counts_judged_as_an_unelevated_owner(readme_marker: Path) -> None:
        from docket.osprompt import win32

        token = _token()
        try:
            assert marker.windows_verdict(token) == marker.Verdict(str(readme_marker), present=True, counts=True)
        finally:
            win32.close_handle(token)
        facts = win32.entry_facts(str(readme_marker), None)
        assert facts is not None
        assert facts[0] == "S-1-5-32-544", "the README's lines make Administrators the file's owner"
        # On the runner, an admin with UAC off: the root floor, owners and links only.
        assert marker.here().counts

    @pytest.mark.parametrize(
        ("change", "why"),
        [
            ("icacls '{folder}' /inheritance:e", "lets you add files"),
            ("icacls '{file}' /setowner $env:USERNAME", "isn't owned by"),
        ],
        ids=["the inherited create right back", "owned by the runner's user"],
    )
    def test_a_readme_marker_changed_after_doesnt_count(readme_marker: Path, change: str, why: str) -> None:
        from docket.osprompt import win32

        _powershell(change.format(folder=readme_marker.parent, file=readme_marker))
        token = _token()
        try:
            verdict = marker.windows_verdict(token)
        finally:
            win32.close_handle(token)
        assert not verdict.counts
        assert why in verdict.why

    @pytest.mark.parametrize(
        ("token", "why"),
        [
            ({"restrict": ("S-1-1-0",)}, "a restricted token"),
            ({"integrity": 0x1000}, "below Medium integrity"),
            ({"disable": ("S-1-5-32-544", "S-1-5-32-545")}, "S-1-5-32-545 turned off"),
        ],
        ids=["restricted", "low integrity", "Users deny-only"],
    )
    def test_a_token_that_hides_the_owners_rights_refuses_the_readmes_marker(
        readme_marker: Path, token: dict[str, object], why: str
    ) -> None:
        from docket.osprompt import win32

        handle = _token(**token)  # pyright: ignore[reportArgumentType]
        try:
            verdict = marker.windows_verdict(handle)
        finally:
            win32.close_handle(handle)
        assert not verdict.counts
        assert why in verdict.why


# -- macOS ---------------------------------------------------------------------------------------------------------

macos_only = pytest.mark.skipif(sys.platform != "darwin", reason="macOS's own API")


@macos_only
def test_localauthentication_loads_and_answers_can_evaluate_on_macos() -> None:
    ok, code = macos._Objc().can_evaluate()  # pyright: ignore[reportPrivateUsage]
    assert isinstance(macos.found_by(ok, code), (osprompt.Prompt, osprompt.NoPrompt, osprompt.Refusal))


@macos_only
def test_the_reply_comes_back_through_the_block_on_an_invalidated_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reply's path with no dialog: a context invalidated first answers at once, with invalidContext."""
    monkeypatch.setattr(osprompt, "TIMEOUT", 30.0)
    answer = macos._Objc().evaluate("check a CI test", threading.Event(), invalidated=True)  # pyright: ignore[reportPrivateUsage]
    assert isinstance(answer, Refused)
    assert "code -10" in answer.why


# -- Linux, by the workflow's stage --------------------------------------------------------------------------------


@stage("bare")
def test_with_nothing_installed_linux_asks_the_code_and_has_no_marker() -> None:
    assert isinstance(linux.find(), osprompt.NoPrompt)
    assert marker.here() == marker.Verdict(str(marker.LINUX))


@stage("marker")
def test_a_marker_made_with_sudo_counts_and_one_the_user_owns_doesnt(tmp_path: Path) -> None:
    assert marker.here() == marker.Verdict(str(marker.LINUX), present=True, counts=True)
    mine = tmp_path / "docket" / marker.FILE
    mine.parent.mkdir()
    mine.write_text("", encoding="utf-8")
    verdict = marker.judge(str(mine), marker.posix_entries(mine, root=False), owner="root")
    assert not verdict.counts


@stage("polkit-yes")
def test_polkit_with_a_rule_that_says_yes_verifies() -> None:
    assert isinstance(linux.find(), osprompt.Prompt)
    assert linux.ask("a CI test") == osprompt.Verified()


@stage("polkit-no")
def test_polkit_with_a_rule_that_says_no_refuses() -> None:
    assert linux.ask("a CI test") == Refused("polkit said no")
