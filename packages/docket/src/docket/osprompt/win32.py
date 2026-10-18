"""Windows' own calls for the OS prompt and its marker, through ctypes: WinRT's UserConsentVerifier, the TPM and
biometric checks, the window the dialog is anchored to, the token, and each marker entry's owner and rights.

Every DLL loads from System32 only (`LoadLibraryExW` with `LOAD_LIBRARY_SEARCH_SYSTEM32`, ctypes' `winmode`), never
from Python's own folder, which a process running as the owner can write; `dll` is the one place a DLL is loaded, and
a test scans for it. Each call that fails raises `OSError`.
"""

from __future__ import annotations

import sys
import uuid
from functools import cache
from typing import Any

if sys.platform == "win32":  # pragma: no cover - Windows' own API; test_osprompt_system runs it on Windows
    import ctypes
    from ctypes import wintypes

    from docket.osprompt.windows import AVAILABILITY_OP, CLASS, VERIFICATION_OP, Anchor

    SYSTEM32 = 0x00000800

    @cache
    def dll(name: str) -> Any:
        found = ctypes.WinDLL(name, use_last_error=True, winmode=SYSTEM32)
        # What returns a handle or a pointer says so, or ctypes would cut it to a C int on 64 bits.
        for function, restype in _RETURNS.get(name, ()):
            getattr(found, function).restype = restype
        return found

    def _fail(what: str, code: int | None = None) -> OSError:
        code = ctypes.get_last_error() if code is None else code
        return OSError(f"{what} failed: {code & 0xFFFFFFFF:#010x}")

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    def guid(text: str) -> GUID:
        return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)

    def _method(ptr: int, index: int, *argtypes: Any) -> Any:
        """A COM method of the interface at `ptr`, by its slot in the vtable."""
        table = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(table[index])

    def _check(hr: int, what: str) -> None:
        if hr < 0:
            raise _fail(what, hr)

    _RETURNS: dict[str, tuple[tuple[str, Any], ...]] = {
        "kernel32": (("GetCurrentProcess", wintypes.HANDLE), ("GetConsoleWindow", wintypes.HWND),
                     ("CreateFileW", wintypes.HANDLE)),
        "user32": (("GetForegroundWindow", wintypes.HWND), ("GetAncestor", wintypes.HWND),
                   ("GetWindow", wintypes.HWND), ("CreateWindowExW", wintypes.HWND)),
        "tbs": (("Tbsi_GetDeviceInfo", ctypes.c_uint32),),
        "advapi32": (("GetSidSubAuthorityCount", ctypes.POINTER(ctypes.c_ubyte)),
                     ("GetSidSubAuthority", ctypes.POINTER(wintypes.DWORD)), ("GetSecurityInfo", wintypes.DWORD)),
    }  # fmt: skip

    E_NOINTERFACE = -2147467262  # 0x80004002
    RPC_E_CHANGED_MODE = -2147417850  # 0x80010106
    RO_INIT_MULTITHREADED = 1
    WS_POPUP = 0x80000000
    WS_EX_TOOLWINDOW = 0x00000080
    GA_ROOTOWNER = 3
    GW_OWNER = 4
    WM_CLOSE = 0x0010
    QS_ALLINPUT = 0x04FF
    PM_REMOVE = 1
    ASFW_ANY = 0xFFFFFFFF
    TBS_E_TPM_NOT_FOUND = 0x8028400F
    WINBIO_TYPE_FACIAL_FEATURES = 0x00000002
    WINBIO_TYPE_FINGERPRINT = 0x00000008

    class Win32:
        """`windows.Api` on Windows. One instance asks once, on one thread: the window and the apartment are that
        thread's."""

        def __init__(self) -> None:
            self._joined = False

        # -- the apartment and the interfaces

        def init(self) -> None:
            hr = dll("combase").RoInitialize(RO_INIT_MULTITHREADED)
            if hr == RPC_E_CHANGED_MODE:
                return
            _check(hr, "RoInitialize")
            self._joined = True

        def uninit(self) -> None:
            if self._joined:
                dll("combase").RoUninitialize()
                self._joined = False

        def factory(self, iid: str) -> int:
            combase = dll("combase")
            name = ctypes.c_void_p()
            _check(combase.WindowsCreateString(CLASS, len(CLASS), ctypes.byref(name)), "WindowsCreateString")
            try:
                out = ctypes.c_void_p()
                wanted = guid(iid)
                _check(
                    combase.RoGetActivationFactory(name, ctypes.byref(wanted), ctypes.byref(out)),
                    "RoGetActivationFactory",
                )
                return int(out.value or 0)
            finally:
                combase.WindowsDeleteString(name)

        def query(self, ptr: int, iid: str) -> int | None:
            out = ctypes.c_void_p()
            wanted = guid(iid)
            hr = _method(ptr, 0, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
                ptr, ctypes.byref(wanted), ctypes.byref(out)
            )
            if hr == E_NOINTERFACE:
                return None
            _check(hr, "QueryInterface")
            return int(out.value or 0)

        def release(self, ptr: int) -> None:
            if ptr:
                _method(ptr, 2)(ptr)

        def check_availability(self, statics: int) -> int:
            out = ctypes.c_void_p()
            hr = _method(statics, 6, ctypes.POINTER(ctypes.c_void_p))(statics, ctypes.byref(out))
            _check(hr, "CheckAvailabilityAsync")
            return self._as(int(out.value or 0), AVAILABILITY_OP)

        def request(self, interop: int, window: int, line: str) -> int:
            combase = dll("combase")
            message = ctypes.c_void_p()
            _check(combase.WindowsCreateString(line, len(line), ctypes.byref(message)), "WindowsCreateString")
            try:
                dll("user32").AllowSetForegroundWindow(wintypes.DWORD(ASFW_ANY))
                out = ctypes.c_void_p()
                wanted = guid(VERIFICATION_OP)
                hr = _method(
                    interop, 6, wintypes.HWND, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)
                )(interop, window, message, ctypes.byref(wanted), ctypes.byref(out))
                _check(hr, "RequestVerificationForWindowAsync")
                return int(out.value or 0)
            finally:
                combase.WindowsDeleteString(message)

        def _as(self, op: int, iid: str) -> int:
            """The operation as the IAsyncOperation<T> whose GetResults its caller reads."""
            try:
                typed = self.query(op, iid)
            finally:
                self.release(op)
            if typed is None:
                raise OSError("the operation isn't the IAsyncOperation asked for")
            return typed

        def status(self, info: int) -> int:
            out = ctypes.c_int()
            _check(_method(info, 7, ctypes.POINTER(ctypes.c_int))(info, ctypes.byref(out)), "IAsyncInfo.Status")
            return out.value

        def error_code(self, info: int) -> int:
            out = ctypes.c_long()
            _check(_method(info, 8, ctypes.POINTER(ctypes.c_long))(info, ctypes.byref(out)), "IAsyncInfo.ErrorCode")
            return out.value

        def cancel(self, info: int) -> None:
            _method(info, 9)(info)

        def close(self, info: int) -> None:
            # Refused only while the operation runs, which _settle waits out; nothing to do about it then.
            _method(info, 10)(info)

        def results(self, op: int) -> int:
            out = ctypes.c_int()
            _check(_method(op, 8, ctypes.POINTER(ctypes.c_int))(op, ctypes.byref(out)), "GetResults")
            return out.value

        # -- the window the dialog is anchored to

        def window(self, anchor: Anchor) -> tuple[int, bool]:
            user32 = dll("user32")
            target = _console_window()
            if anchor == "foreign":
                return target or int(user32.GetForegroundWindow() or 0), False
            rect = wintypes.RECT()
            if target:
                user32.GetWindowRect(wintypes.HWND(target), ctypes.byref(rect))
            hwnd = user32.CreateWindowExW(
                wintypes.DWORD(WS_EX_TOOLWINDOW),
                "STATIC",
                "Docket",
                wintypes.DWORD(WS_POPUP),
                rect.left,
                rect.top,
                max(rect.right - rect.left, 1),
                max(rect.bottom - rect.top, 1),
                None,
                None,
                None,
                None,
            )
            if not hwnd:
                raise _fail("CreateWindowExW")
            return int(hwnd), True

        def dismiss(self, window: int) -> int:
            """Close each window Docket's anchor owns, Hello's dialog among them, as its own close button would: it can
            only cancel. How many there were."""
            user32 = dll("user32")
            found: list[int] = []

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def each(hwnd: int | None, _: int) -> bool:
                if hwnd and int(user32.GetWindow(wintypes.HWND(hwnd), GW_OWNER) or 0) == window:
                    found.append(int(hwnd))
                return True

            user32.EnumWindows(each, 0)
            for hwnd in found:
                user32.PostMessageW(wintypes.HWND(hwnd), WM_CLOSE, 0, 0)
            return len(found)

        def destroy(self, window: int) -> None:
            dll("user32").DestroyWindow(wintypes.HWND(window))

        def pump(self, ms: int) -> None:
            user32 = dll("user32")
            user32.MsgWaitForMultipleObjects(0, None, False, ms, QS_ALLINPUT)
            msg = wintypes.MSG()
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

        # -- what the machine has

        def tpm(self) -> bool:
            info = (ctypes.c_uint32 * 4)(1, 0, 0, 0)
            rc = dll("tbs").Tbsi_GetDeviceInfo(ctypes.sizeof(info), ctypes.byref(info))
            if rc == 0:
                return True
            if rc == TBS_E_TPM_NOT_FOUND:
                return False
            raise _fail("Tbsi_GetDeviceInfo", rc)

        def biometric(self) -> bool:
            winbio = dll("winbio")
            for factor in (WINBIO_TYPE_FINGERPRINT, WINBIO_TYPE_FACIAL_FEATURES):
                units = ctypes.c_void_p()
                count = ctypes.c_size_t()
                hr = winbio.WinBioEnumBiometricUnits(factor, ctypes.byref(units), ctypes.byref(count))
                if units.value:
                    winbio.WinBioFree(units)
                _check(hr, "WinBioEnumBiometricUnits")
                if count.value:
                    return True
            return False

    def _console_window() -> int:
        """The console's window, or under Windows Terminal the terminal window that owns its hidden pseudo-window."""
        user32 = dll("user32")
        hwnd = dll("kernel32").GetConsoleWindow()
        if not hwnd:
            return 0
        root = user32.GetAncestor(wintypes.HWND(hwnd), GA_ROOTOWNER)
        for candidate in (root, hwnd):
            if candidate and user32.IsWindowVisible(wintypes.HWND(candidate)):
                return int(candidate)
        return 0

    # -- the token and the marker's entries

    TOKEN_QUERY = 0x0008
    TOKEN_DUPLICATE = 0x0002
    TOKEN_GROUPS = 2
    TOKEN_ELEVATION_TYPE = 18
    TOKEN_LINKED_TOKEN = 19
    TOKEN_ELEVATION = 20
    TOKEN_INTEGRITY_LEVEL = 25
    TOKEN_IS_APP_CONTAINER = 29
    ELEVATION_DEFAULT, ELEVATION_FULL = 1, 2
    SECURITY_IDENTIFICATION = 1
    TOKEN_IMPERSONATION = 2
    SE_GROUP_USE_FOR_DENY_ONLY = 0x00000010
    READ_CONTROL = 0x00020000
    FILE_READ_ATTRIBUTES = 0x0080
    FILE_SHARE_ALL = 0x7
    OPEN_EXISTING = 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
    INVALID_HANDLE = ctypes.c_void_p(-1).value
    SE_FILE_OBJECT = 1
    OWNER_SECURITY_INFORMATION = 0x1
    GROUP_SECURITY_INFORMATION = 0x2
    DACL_SECURITY_INFORMATION = 0x4
    LABEL_SECURITY_INFORMATION = 0x10
    MAXIMUM_ALLOWED = 0x02000000
    ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND = 2, 3

    class SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class GenericMapping(ctypes.Structure):
        _fields_ = [
            ("GenericRead", wintypes.DWORD),
            ("GenericWrite", wintypes.DWORD),
            ("GenericExecute", wintypes.DWORD),
            ("GenericAll", wintypes.DWORD),
        ]

    FILE_MAPPING = GenericMapping(0x120089, 0x120116, 0x1200A0, 0x1F01FF)

    class ByHandleInfo(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    def program_data() -> str:
        """ProgramData from the known-folder call, never the environment."""
        out = ctypes.c_wchar_p()
        folder = guid("62ab5d82-fdc1-4dc3-a9dd-070d1d495d97")
        hr = dll("shell32").SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(out))
        try:
            _check(hr, "SHGetKnownFolderPath")
            return str(out.value)
        finally:
            dll("ole32").CoTaskMemFree(out)

    def sid_text(sid: int) -> str:
        out = ctypes.c_wchar_p()
        if not dll("advapi32").ConvertSidToStringSidW(ctypes.c_void_p(sid), ctypes.byref(out)):
            raise _fail("ConvertSidToStringSidW")
        try:
            return str(out.value)
        finally:
            dll("kernel32").LocalFree(out)

    def _info(token: int, kind: int) -> ctypes.Array[ctypes.c_char]:
        advapi32 = dll("advapi32")
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(wintypes.HANDLE(token), kind, None, 0, ctypes.byref(size))
        buf = ctypes.create_string_buffer(max(size.value, 4))
        if not advapi32.GetTokenInformation(wintypes.HANDLE(token), kind, buf, size, ctypes.byref(size)):
            raise _fail("GetTokenInformation")
        return buf

    def _dword(token: int, kind: int) -> int:
        return int(wintypes.DWORD.from_buffer(_info(token, kind)).value)

    def close_handle(handle: int) -> None:
        dll("kernel32").CloseHandle(wintypes.HANDLE(handle))

    def own_token() -> tuple[int | None, bool]:
        """The token the marker is judged with, and whether the owner is an admin with UAC off (no unelevated token):
        the linked unelevated token when Docket runs elevated, else a copy of its own. The caller closes it."""
        advapi32 = dll("advapi32")
        process = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
            wintypes.HANDLE(dll("kernel32").GetCurrentProcess()), TOKEN_QUERY | TOKEN_DUPLICATE, ctypes.byref(process)
        ):
            raise _fail("OpenProcessToken")
        try:
            kind = _dword(int(process.value or 0), TOKEN_ELEVATION_TYPE)
            elevated = _dword(int(process.value or 0), TOKEN_ELEVATION) != 0
            if kind == ELEVATION_FULL:
                linked = wintypes.HANDLE.from_buffer(_info(int(process.value or 0), TOKEN_LINKED_TOKEN))
                return int(linked.value or 0), False
            if kind == ELEVATION_DEFAULT and elevated:
                return None, True
            copy = wintypes.HANDLE()
            if not advapi32.DuplicateTokenEx(
                process, TOKEN_QUERY, None, SECURITY_IDENTIFICATION, TOKEN_IMPERSONATION, ctypes.byref(copy)
            ):
                raise _fail("DuplicateTokenEx")
            return int(copy.value or 0), False
        finally:
            close_handle(int(process.value or 0))

    def token_facts(token: int) -> tuple[bool, int, bool, tuple[str, ...]]:
        """Restricted, its integrity's RID, an AppContainer, and its deny-only groups' SIDs."""
        advapi32 = dll("advapi32")
        restricted = bool(advapi32.IsTokenRestricted(wintypes.HANDLE(token)))
        # The label's SID points into its buffer, so the buffer stays referenced while it is read.
        label_buffer = _info(token, TOKEN_INTEGRITY_LEVEL)
        label = SidAndAttributes.from_buffer(label_buffer)
        count = advapi32.GetSidSubAuthorityCount(ctypes.c_void_p(label.Sid))[0]
        integrity = int(advapi32.GetSidSubAuthority(ctypes.c_void_p(label.Sid), count - 1)[0])
        container = _dword(token, TOKEN_IS_APP_CONTAINER) != 0
        groups = _info(token, TOKEN_GROUPS)
        n = int(wintypes.DWORD.from_buffer(groups).value)
        offset = ctypes.sizeof(ctypes.c_void_p)  # the count, padded to the array's alignment
        array = (SidAndAttributes * n).from_buffer(groups, offset)
        deny = tuple(sid_text(int(g.Sid)) for g in array if g.Attributes & SE_GROUP_USE_FOR_DENY_ONLY)
        return restricted, integrity, container, deny

    def entry_facts(path: str, token: int | None) -> tuple[str, bool, int, int] | None:
        """An entry's owner SID, whether it is a reparse point, its hard links and the rights `token` has on it
        (none asked without a token, the admin floor); None when it isn't there."""
        kernel32 = dll("kernel32")
        advapi32 = dll("advapi32")
        handle = kernel32.CreateFileW(
            path,
            READ_CONTROL | FILE_READ_ATTRIBUTES,
            FILE_SHARE_ALL,
            None,
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle in (None, INVALID_HANDLE):
            code = ctypes.get_last_error()
            if code in (ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND):
                return None
            raise _fail(f"opening {path}", code)
        try:
            info = ByHandleInfo()
            if not kernel32.GetFileInformationByHandle(wintypes.HANDLE(handle), ctypes.byref(info)):
                raise _fail("GetFileInformationByHandle")
            owner = ctypes.c_void_p()
            descriptor = ctypes.c_void_p()
            what = (
                OWNER_SECURITY_INFORMATION
                | GROUP_SECURITY_INFORMATION
                | DACL_SECURITY_INFORMATION
                | LABEL_SECURITY_INFORMATION
            )
            rc = advapi32.GetSecurityInfo(
                wintypes.HANDLE(handle), SE_FILE_OBJECT, what, ctypes.byref(owner), None, None, None,
                ctypes.byref(descriptor),
            )  # fmt: skip
            if rc != 0:
                raise _fail("GetSecurityInfo", rc)
            try:
                granted = 0 if token is None else _granted(descriptor.value or 0, token)
                return (
                    sid_text(owner.value or 0),
                    bool(info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT),
                    int(info.nNumberOfLinks),
                    granted,
                )
            finally:
                kernel32.LocalFree(descriptor)
        finally:
            close_handle(int(handle))

    def _granted(descriptor: int, token: int) -> int:
        privileges = ctypes.create_string_buffer(256)
        size = wintypes.DWORD(ctypes.sizeof(privileges))
        granted = wintypes.DWORD()
        status = wintypes.BOOL()
        mapping = GenericMapping(FILE_MAPPING.GenericRead, FILE_MAPPING.GenericWrite, FILE_MAPPING.GenericExecute,
                                 FILE_MAPPING.GenericAll)  # fmt: skip
        if not dll("advapi32").AccessCheck(
            ctypes.c_void_p(descriptor),
            wintypes.HANDLE(token),
            MAXIMUM_ALLOWED,
            ctypes.byref(mapping),
            privileges,
            ctypes.byref(size),
            ctypes.byref(granted),
            ctypes.byref(status),
        ):
            raise _fail("AccessCheck")
        return int(granted.value) if status.value else 0
