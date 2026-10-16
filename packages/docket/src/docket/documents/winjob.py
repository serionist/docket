"""Windows job objects, for the documents' reader (DR-0485): the child caps its own memory in one, and the parent kills
it through another.

A venv's `python.exe` on Windows is a launcher that starts the real interpreter as a process of its own, often before
the parent can put the launcher in a job; so the memory cap is set by the interpreter itself, in a job it makes for
itself (nested jobs work from Windows 8). Every call raises `OSError` with Windows' error code when it fails.
"""

from __future__ import annotations

import sys

if sys.platform == "win32":  # pragma: no cover - Windows' own API; test_documents_extract runs it there
    import ctypes
    from ctypes import wintypes

    PROCESS_MEMORY = 0x100
    KILL_ON_CLOSE = 0x2000
    _EXTENDED_LIMITS = 9
    _SET_QUOTA_AND_TERMINATE = 0x0100 | 0x0001

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    _kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    _kernel32.QueryInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
    )
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.GetCurrentProcess.argtypes = ()
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    def _failed(what: str) -> OSError:
        code = ctypes.get_last_error()
        return OSError(code, f"Windows refused {what} ({code})")

    def create(flags: int, memory: int) -> int:
        """A job with these limit flags and, with `PROCESS_MEMORY`, this cap on each process's committed memory."""
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            raise _failed("a job object")
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = flags
        info.ProcessMemoryLimit = memory
        if not _kernel32.SetInformationJobObject(job, _EXTENDED_LIMITS, ctypes.byref(info), ctypes.sizeof(info)):
            error = _failed("a job object's limits")
            _kernel32.CloseHandle(job)
            raise error
        return int(job)

    def assign(job: int, pid: int | None = None) -> None:
        """Put a process in the job: another by its pid, or with none the calling process itself."""
        process = (
            _kernel32.GetCurrentProcess()
            if pid is None
            else _kernel32.OpenProcess(_SET_QUOTA_AND_TERMINATE, False, pid)
        )
        if not process:
            raise _failed("a process handle")
        try:
            if not _kernel32.AssignProcessToJobObject(job, process):
                raise _failed("a process its job")
        finally:
            if pid is not None:
                _kernel32.CloseHandle(process)

    def own_memory_limit() -> int | None:
        """The memory cap of the calling process's innermost job, or None when it has none."""
        info = _ExtendedLimits()
        if not _kernel32.QueryInformationJobObject(
            None, _EXTENDED_LIMITS, ctypes.byref(info), ctypes.sizeof(info), None
        ):
            raise _failed("its own job's limits")
        return int(info.ProcessMemoryLimit) if info.BasicLimitInformation.LimitFlags & PROCESS_MEMORY else None

    def close(handle: int) -> None:
        _kernel32.CloseHandle(handle)
