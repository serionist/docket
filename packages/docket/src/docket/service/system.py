"""What the service asks of the operating system: the commands it runs, which system this is, the user's SID on
Windows, and files only the user can read. Everything takes its runner and system as arguments, so each system's
path runs in the tests of every other."""

from __future__ import annotations

import csv
import ctypes
import io
import os
import platform
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from docket.errors import DocketError

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]

# "Windows", "Darwin" or "Linux".
SYSTEM = platform.system()
# This process's SID once `whoami` gave it through the default runner.
_SID: list[str] = []


def run(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run a command with an argument list, never a shell; a missing program is exit code 127, as a shell says."""
    try:
        return subprocess.run(  # noqa: S603 - an argument list Docket built, never a shell
            list(args), capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
        )
    except FileNotFoundError as e:
        return subprocess.CompletedProcess(list(args), 127, "", str(e))


def windows_sid(runner: Runner = run) -> str:
    """The current user's SID, from `whoami /user`: a name can be renamed or carry a domain prefix, a SID can't. A
    process's user never changes, so the default runner's answer is kept for the process; a runner passed in, as a
    test's, is asked every time and its answer never kept (DR-0767)."""
    default = runner is run
    if default and _SID:
        return _SID[0]
    proc = runner(["whoami", "/user", "/fo", "csv", "/nh"])
    rows = list(csv.reader(io.StringIO(proc.stdout)))
    sid = rows[0][1].strip() if proc.returncode == 0 and rows and len(rows[0]) > 1 else ""
    if not sid.startswith("S-1-"):
        raise DocketError(f"can't find this user's SID (whoami /user exited {proc.returncode})")
    if default:
        _SID[:] = [sid]
    return sid


# SetNamedSecurityInfoW's object type and what it sets: a file's DACL, protected from its folder's.
SE_FILE_OBJECT = 1
DACL_SECURITY_INFORMATION = 0x4
PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000


def set_owner_only_dacl(path: Path, sid: str) -> None:  # pragma: no cover - Windows' own API; its tests run there
    """Replace a file's DACL with one entry, full control for `sid`, protected from inheritance. icacls can't: its
    grant leaves the explicit entries a file got at creation (an elevated process's default DACL adds SYSTEM,
    Administrators and OWNER RIGHTS)."""
    # Both exist only in Windows' ctypes, so they are looked up, not named, for the type checker on other systems.
    dll: Any = vars(ctypes)["WinDLL"]
    last_error: Any = vars(ctypes)["get_last_error"]
    advapi32, kernel32 = dll("advapi32", use_last_error=True), dll("kernel32", use_last_error=True)
    descriptor = ctypes.c_void_p()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f"D:P(A;;FA;;;{sid})", 1, ctypes.byref(descriptor), None
    ):
        raise DocketError(f"can't make {path} private to this user: Windows error {last_error()}")
    try:
        present, defaulted, dacl = ctypes.c_int(), ctypes.c_int(), ctypes.c_void_p()
        advapi32.GetSecurityDescriptorDacl(
            descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
        )
        info = DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION
        error = advapi32.SetNamedSecurityInfoW(str(path), SE_FILE_OBJECT, info, None, None, dacl, None)
        if error:
            raise DocketError(f"can't make {path} private to this user: Windows error {error}")
    finally:
        kernel32.LocalFree(descriptor)


def owner_only(path: Path, *, system: str = SYSTEM, runner: Runner = run) -> None:
    """Make a file readable by its owner alone: 0600 on macOS and Linux; on Windows, where the mode means nothing, a
    DACL whose one entry is the user, protected from the folder's."""
    if system == "Windows":
        set_owner_only_dacl(path, windows_sid(runner))
    else:
        path.chmod(0o600)


def write_private(path: Path, text: str, *, system: str = SYSTEM, runner: Runner = run) -> None:
    """Write a file only its owner can read: made private while still empty, filled, then renamed over the old one,
    so the text is never in a file anyone else could open."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    try:
        os.close(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        owner_only(tmp, system=system, runner=runner)
        with tmp.open("w", encoding="utf-8", newline="\n") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def others_can_read(path: Path, *, system: str = SYSTEM) -> bool:
    """Whether group or others can read a file. Only macOS and Linux can say from the mode; on Windows the DACL
    `owner_only` set is the control."""
    return system != "Windows" and bool(path.stat().st_mode & 0o077)
