"""FIX3 fix 1 (ii), lane F3-OSI-W: the Windows named-pipe endpoint and the connecting client's account
(interface contract FIX3-II r2, §2.3 and §2.4).

The broker listens on one named-pipe instance whose access list is set by its own security descriptor at creation:
the own account with full control, and each listed client account with FILE_GENERIC_READ | FILE_WRITE_DATA only, so no
client may create a pipe instance. A client opens the pipe at SECURITY_IDENTIFICATION level; the server reads the
client token's user SID through ImpersonateNamedPipeClient / OpenThreadToken / GetTokenInformation(TokenUser) /
ConvertSidToStringSidW and reverts at once; a failed RevertToSelf ends the process with exit 4 (§2.4).

The module imports only the standard library (ctypes over kernel32 and advapi32, as export.py does), never a wfcore
module, and raises only builtin exceptions (§2.3). The Win32 API is bound on first use, so importing the module off
Windows is harmless; every call there raises OSError. All I/O is overlapped, so every wait honours its deadline
(`time.monotonic()` seconds) and a timed-out operation is cancelled and settled before the call returns.
"""
from __future__ import annotations

import ctypes
import errno
import os
import re
import sys
import time
from typing import Any, Iterable

PLATFORM = "win32"

_ADDRESS = re.compile(r"\\\\\.\\pipe\\[A-Za-z0-9._-]{1,200}\Z")
_SID = re.compile(r"S-1-(0|[1-9][0-9]{0,9})(-(0|[1-9][0-9]{0,9})){1,15}\Z")
_STEP_S = 0.05  # the connect retry step (§1.4)
# How long a server close waits for the client to read a written response and close (one request, one response per
# connection, §1.1); DisconnectNamedPipe discards unread data. It equals the response write bound, WRITE_BOUND_S (§1.4).
_LINGER_S = 10.0
_BUFFER = 65536

# Win32 constants
_PIPE_ACCESS_DUPLEX = 0x00000003
_FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
_FILE_FLAG_OVERLAPPED = 0x40000000
_PIPE_TYPE_BYTE = 0x00000000
_PIPE_READMODE_BYTE = 0x00000000
_PIPE_WAIT = 0x00000000
_PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
_GENERIC_READ = 0x80000000
_FILE_WRITE_DATA = 0x00000002
_OPEN_EXISTING = 3
_SECURITY_SQOS_PRESENT = 0x00100000
_SECURITY_IDENTIFICATION = 0x00010000
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_SDDL_REVISION_1 = 1
_SE_KERNEL_OBJECT = 6
_DACL_SECURITY_INFORMATION = 0x00000004
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_ERROR_FILE_NOT_FOUND = 2
_ERROR_ACCESS_DENIED = 5
_ERROR_BROKEN_PIPE = 109
_ERROR_SEM_TIMEOUT = 121
_ERROR_INSUFFICIENT_BUFFER = 122
_ERROR_PIPE_BUSY = 231
_ERROR_NO_DATA = 232
_ERROR_PIPE_NOT_CONNECTED = 233
_ERROR_PIPE_CONNECTED = 535
_ERROR_OPERATION_ABORTED = 995
_ERROR_IO_PENDING = 997
_INVALID_HANDLE = ctypes.c_void_p(-1).value
# FILE_GENERIC_READ (0x120089) | FILE_WRITE_DATA (0x2): read and write, and no FILE_CREATE_PIPE_INSTANCE (0x4).
# `connect` asks for exactly these rights (GENERIC_READ maps to FILE_GENERIC_READ): GENERIC_WRITE would also ask for
# FILE_APPEND_DATA (= FILE_CREATE_PIPE_INSTANCE), FILE_WRITE_ATTRIBUTES and FILE_WRITE_EA, which the client ACE does not
# grant, so every listed account but the own one would be refused (Lazy WF-201 M1).
_CLIENT_OPEN = _GENERIC_READ | _FILE_WRITE_DATA
_CLIENT_RIGHTS = 0x0012008B

_API: Any = None


def _api() -> Any:
    """Bind the Win32 functions once, on first use. Off Windows this raises OSError."""
    global _API
    if _API is not None:
        return _API
    if sys.platform != "win32":
        raise OSError("peer_win is the Windows platform module; this host is " + sys.platform)
    from ctypes import wintypes as wt

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p), ("Offset", wt.DWORD),
                    ("OffsetHigh", wt.DWORD), ("hEvent", wt.HANDLE)]

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("nLength", wt.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p), ("bInheritHandle", wt.BOOL)]

    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wt.DWORD)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    H, D, B, P = wt.HANDLE, wt.DWORD, wt.BOOL, ctypes.c_void_p
    LPOV = ctypes.POINTER(OVERLAPPED)

    def bind(dll: Any, name: str, restype: Any, *argtypes: Any) -> Any:
        fn = getattr(dll, name)
        fn.restype = restype
        fn.argtypes = list(argtypes)
        return fn

    class Api:
        pass

    a = Api()
    a.OVERLAPPED, a.SECURITY_ATTRIBUTES, a.SID_AND_ATTRIBUTES, a.wt = OVERLAPPED, SECURITY_ATTRIBUTES, SID_AND_ATTRIBUTES, wt
    a.CreateNamedPipeW = bind(k32, "CreateNamedPipeW", H, wt.LPCWSTR, D, D, D, D, D, D, ctypes.POINTER(SECURITY_ATTRIBUTES))
    a.ConnectNamedPipe = bind(k32, "ConnectNamedPipe", B, H, LPOV)
    a.DisconnectNamedPipe = bind(k32, "DisconnectNamedPipe", B, H)
    a.CreateFileW = bind(k32, "CreateFileW", H, wt.LPCWSTR, D, D, P, D, D, H)
    a.ReadFile = bind(k32, "ReadFile", B, H, P, D, ctypes.POINTER(D), LPOV)
    a.WriteFile = bind(k32, "WriteFile", B, H, P, D, ctypes.POINTER(D), LPOV)
    a.GetOverlappedResult = bind(k32, "GetOverlappedResult", B, H, LPOV, ctypes.POINTER(D), B)
    a.CancelIoEx = bind(k32, "CancelIoEx", B, H, LPOV)
    a.CreateEventW = bind(k32, "CreateEventW", H, P, B, B, wt.LPCWSTR)
    a.ResetEvent = bind(k32, "ResetEvent", B, H)
    a.WaitForSingleObject = bind(k32, "WaitForSingleObject", D, H, D)
    a.WaitNamedPipeW = bind(k32, "WaitNamedPipeW", B, wt.LPCWSTR, D)
    a.GetNamedPipeClientProcessId = bind(k32, "GetNamedPipeClientProcessId", B, H, ctypes.POINTER(wt.ULONG))
    a.CloseHandle = bind(k32, "CloseHandle", B, H)
    a.GetCurrentProcess = bind(k32, "GetCurrentProcess", H)
    a.GetCurrentThread = bind(k32, "GetCurrentThread", H)
    a.LocalFree = bind(k32, "LocalFree", P, P)
    a.OpenProcessToken = bind(adv, "OpenProcessToken", B, H, D, ctypes.POINTER(H))
    a.OpenThreadToken = bind(adv, "OpenThreadToken", B, H, D, B, ctypes.POINTER(H))
    a.GetTokenInformation = bind(adv, "GetTokenInformation", B, H, ctypes.c_int, P, D, ctypes.POINTER(D))
    a.ConvertSidToStringSidW = bind(adv, "ConvertSidToStringSidW", B, P, ctypes.POINTER(wt.LPWSTR))
    a.ImpersonateNamedPipeClient = bind(adv, "ImpersonateNamedPipeClient", B, H)
    a.RevertToSelf = bind(adv, "RevertToSelf", B)
    a.ConvertStringSecurityDescriptorToSecurityDescriptorW = bind(
        adv, "ConvertStringSecurityDescriptorToSecurityDescriptorW", B, wt.LPCWSTR, D, ctypes.POINTER(P), ctypes.POINTER(D))
    a.GetSecurityInfo = bind(adv, "GetSecurityInfo", D, H, ctypes.c_int, D, ctypes.POINTER(P), ctypes.POINTER(P),
                             ctypes.POINTER(P), ctypes.POINTER(P), ctypes.POINTER(P))
    a.ConvertSecurityDescriptorToStringSecurityDescriptorW = bind(
        adv, "ConvertSecurityDescriptorToStringSecurityDescriptorW", B, P, D, D, ctypes.POINTER(wt.LPWSTR),
        ctypes.POINTER(wt.ULONG))
    _API = a
    return a


def _winerror(code: int, what: str) -> OSError:
    """The builtin OSError subclass for a Win32 error code (OSError maps winerror to errno and subclass)."""
    return OSError(None, f"{what}: {ctypes.FormatError(code).strip()}", None, code)


def _last(what: str) -> OSError:
    return _winerror(ctypes.get_last_error(), what)


def _ms_left(deadline: float) -> int:
    return max(0, int((deadline - time.monotonic()) * 1000))


def _check_address(address: str) -> None:
    if not isinstance(address, str) or not _ADDRESS.match(address):
        raise ValueError(f"not a local pipe address (\\\\.\\pipe\\ then 1-200 of [A-Za-z0-9._-]): {address!r}")


def _check_ids(ids: Iterable[str]) -> list[str]:
    out = []
    for i in ids:
        if not isinstance(i, str) or not _SID.match(i):
            raise ValueError(f"not a canonical SID string: {i!r}")
        out.append(i)
    return out


def _token_user_sid(api: Any, token: Any) -> str:
    """The token's user SID as its canonical string."""
    need = api.wt.DWORD(0)
    api.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(need))
    if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER or need.value == 0:
        raise _last("GetTokenInformation(TokenUser) size")
    buf = ctypes.create_string_buffer(need.value)
    if not api.GetTokenInformation(token, _TOKEN_USER, buf, need, ctypes.byref(need)):
        raise _last("GetTokenInformation(TokenUser)")
    user = ctypes.cast(buf, ctypes.POINTER(api.SID_AND_ATTRIBUTES)).contents
    text = api.wt.LPWSTR()
    if not api.ConvertSidToStringSidW(user.Sid, ctypes.byref(text)):
        raise _last("ConvertSidToStringSidW")
    try:
        return str(text.value)
    finally:
        api.LocalFree(text)


def own_account() -> str:
    """The SID string of the account this process runs as (OpenProcessToken, TokenUser). Failure: OSError."""
    api = _api()
    token = api.wt.HANDLE()
    if not api.OpenProcessToken(api.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise _last("OpenProcessToken")
    try:
        sid = _token_user_sid(api, token)
    finally:
        api.CloseHandle(token)
    if not _SID.match(sid):
        raise OSError(f"own account id is not a canonical SID: {sid!r}")
    return sid


def _revert_to_self() -> bool:
    return bool(_api().RevertToSelf())


class _Io:
    """Overlapped reads and writes on one pipe handle, each bounded by a deadline, through one manual-reset event."""

    def __init__(self, handle: Any) -> None:
        api = _api()
        self._api = api
        self._h = handle
        self._event = api.CreateEventW(None, True, False, None)
        if not self._event:
            raise _last("CreateEventW")

    def _wait(self, ov: Any, deadline: float, what: str) -> int:
        """Wait for a started operation; on the deadline cancel it and settle it. Returns the bytes transferred."""
        api, n = self._api, self._api.wt.DWORD(0)
        r = api.WaitForSingleObject(self._event, _ms_left(deadline))
        if r == _WAIT_OBJECT_0:
            if not api.GetOverlappedResult(self._h, ctypes.byref(ov), ctypes.byref(n), False):
                raise _last(what)
            return n.value
        if r != _WAIT_TIMEOUT:
            err = _last("WaitForSingleObject")
            api.CancelIoEx(self._h, ctypes.byref(ov))
            api.GetOverlappedResult(self._h, ctypes.byref(ov), ctypes.byref(n), True)
            raise err
        api.CancelIoEx(self._h, ctypes.byref(ov))
        if api.GetOverlappedResult(self._h, ctypes.byref(ov), ctypes.byref(n), True):
            return n.value  # it completed just before the cancel: the bytes count
        err = ctypes.get_last_error()
        if err == _ERROR_OPERATION_ABORTED:
            raise TimeoutError(f"{what}: deadline passed")
        raise _winerror(err, what)

    def _op(self, fn: Any, buf: Any, size: int, deadline: float, what: str) -> int:
        api = self._api
        ov = api.OVERLAPPED()
        api.ResetEvent(self._event)
        ov.hEvent = self._event
        # The byte count comes from GetOverlappedResult: with an OVERLAPPED, the direct count argument is NULL.
        if fn(self._h, buf, size, None, ctypes.byref(ov)):
            got = api.wt.DWORD(0)
            if not api.GetOverlappedResult(self._h, ctypes.byref(ov), ctypes.byref(got), False):
                raise _last(what)
            return got.value
        err = ctypes.get_last_error()
        if err != _ERROR_IO_PENDING:
            raise _winerror(err, what)
        return self._wait(ov, deadline, what)

    def read_some(self, size: int, deadline: float) -> bytes:
        """One ReadFile of at most `size` bytes. EOFError when the peer has closed; TimeoutError at the deadline."""
        buf = ctypes.create_string_buffer(size)
        try:
            got = self._op(self._api.ReadFile, buf, size, deadline, "ReadFile")
        except OSError as exc:
            if getattr(exc, "winerror", None) in (_ERROR_BROKEN_PIPE, _ERROR_PIPE_NOT_CONNECTED, _ERROR_NO_DATA):
                raise EOFError("the peer closed the pipe") from exc
            raise
        return buf.raw[:got]

    def recv_exact(self, n: int, deadline: float) -> bytes:
        if n < 0:
            raise ValueError("n must be >= 0")
        out = bytearray()
        while len(out) < n:
            try:
                chunk = self.read_some(min(n - len(out), _BUFFER), deadline)
            except EOFError as exc:
                raise EOFError(f"the peer closed after {len(out)} of {n} bytes") from exc
            out += chunk
            if not chunk and time.monotonic() >= deadline:
                raise TimeoutError("ReadFile: deadline passed")
        return bytes(out)

    def send_all(self, data: bytes, deadline: float) -> None:
        view = memoryview(bytes(data))
        while view:
            chunk = view[:_BUFFER].tobytes()
            buf = ctypes.create_string_buffer(chunk, len(chunk))
            try:
                put = self._op(self._api.WriteFile, buf, len(chunk), deadline, "WriteFile")
            except OSError as exc:
                if getattr(exc, "winerror", None) in (_ERROR_BROKEN_PIPE, _ERROR_NO_DATA, _ERROR_PIPE_NOT_CONNECTED):
                    raise BrokenPipeError(f"the peer closed the pipe: {exc}") from exc
                raise
            view = view[put:]

    def close_event(self) -> None:
        if self._event:
            self._api.CloseHandle(self._event)
            self._event = None


class Channel:
    """The client end of a pipe, opened by `connect`."""

    def __init__(self, handle: Any) -> None:
        self._h = handle
        self._io = _Io(handle)
        self._closed = False

    def _open(self) -> _Io:
        # A closed handle's value may already name another object, so a closed channel never reaches the API.
        if self._closed:
            raise OSError("the channel is closed")
        return self._io

    def recv_exact(self, n: int, deadline: float) -> bytes:
        return self._open().recv_exact(n, deadline)

    def send_all(self, data: bytes, deadline: float) -> None:
        self._open().send_all(data, deadline)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._io.close_event()
            _api().CloseHandle(self._h)
        except Exception:  # noqa: BLE001 - close never raises (§2.3)
            pass


class ServerChannel:
    """The server end of the listener's one pipe instance for one connected client. Closing it waits (bounded) for
    the client to finish reading what was written, then disconnects the client and re-arms the instance."""

    def __init__(self, listener: "Listener") -> None:
        self._listener = listener
        self._h = listener._h
        self._io = _Io(self._h)
        self._wrote = False
        self._closed = False

    def _open(self) -> _Io:
        # The pipe handle is the listener's: once either is closed, the channel never reaches the API.
        if self._closed or self._listener._closed:
            raise OSError("the channel or its listener is closed")
        return self._io

    def recv_exact(self, n: int, deadline: float) -> bytes:
        return self._open().recv_exact(n, deadline)

    def send_all(self, data: bytes, deadline: float) -> None:
        io = self._open()
        if data:
            self._wrote = True
        io.send_all(data, deadline)

    def peer_account(self) -> tuple[str, int | None]:
        """(the client token's user SID string, the client pid or None). Call it after reading the request frame.
        A failed RevertToSelf ends the process with exit 4 (§2.4), so nothing else runs as the client."""
        self._open()
        api = _api()
        if not api.ImpersonateNamedPipeClient(self._h):
            raise _last("ImpersonateNamedPipeClient")
        try:
            token = api.wt.HANDLE()
            if not api.OpenThreadToken(api.GetCurrentThread(), _TOKEN_QUERY, True, ctypes.byref(token)):
                raise _last("OpenThreadToken")
            try:
                account = _token_user_sid(api, token)
            finally:
                api.CloseHandle(token)
        finally:
            if not _revert_to_self():
                os._exit(4)
        if not _SID.match(account):
            raise OSError(f"client account id is not a canonical SID: {account!r}")
        pid = api.wt.ULONG(0)
        return account, (int(pid.value) if api.GetNamedPipeClientProcessId(self._h, ctypes.byref(pid)) else None)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        listener = self._listener
        try:
            if not listener._closed:  # a closed listener's handle value may already name another object
                if self._wrote:
                    # Linger: read until the client closes its end, so a written response is never discarded by the
                    # disconnect. Bounded; any extra bytes the client sends are read a buffer at a time and dropped.
                    deadline = time.monotonic() + _LINGER_S
                    while time.monotonic() < deadline:
                        try:
                            self._io.read_some(_BUFFER, deadline)
                        except (EOFError, TimeoutError, OSError):
                            break
                _api().DisconnectNamedPipe(self._h)
        except Exception:  # noqa: BLE001 - close never raises (§2.3)
            pass
        try:
            self._io.close_event()
        except Exception:  # noqa: BLE001 - close never raises (§2.3)
            pass
        listener._busy = False
        if not listener._closed:
            try:
                listener._arm()  # re-arm: a disconnected instance takes no client until a connect is pending
            except Exception:  # noqa: BLE001 - close never raises; the next accept arms again
                pass


class Listener:
    """The broker's single named-pipe instance (FILE_FLAG_FIRST_PIPE_INSTANCE), with its access list set at creation."""

    def __init__(self, address: str, handle: Any) -> None:
        self.address = address
        self._h = handle
        self._event = _api().CreateEventW(None, True, False, None)
        if not self._event:
            err = _last("CreateEventW")
            _api().CloseHandle(handle)
            raise err
        self._busy = False
        self._closed = False
        self._ov: Any = None  # the pending overlapped ConnectNamedPipe; kept alive until it completes or is settled
        self._connected = False  # a client is connected that no channel serves yet

    def _arm(self) -> None:
        """Start an overlapped ConnectNamedPipe, unless one is pending or a client is already connected."""
        if self._ov is not None or self._connected:
            return
        api = _api()
        while True:
            ov = api.OVERLAPPED()
            api.ResetEvent(self._event)
            ov.hEvent = self._event
            if api.ConnectNamedPipe(self._h, ctypes.byref(ov)):
                self._connected = True
                return
            err = ctypes.get_last_error()
            if err == _ERROR_PIPE_CONNECTED:
                self._connected = True
                return
            if err == _ERROR_IO_PENDING:
                self._ov = ov
                return
            if err == _ERROR_NO_DATA:
                # A client connected and left before the connect: disconnect it, and the next connect is pending.
                api.DisconnectNamedPipe(self._h)
                continue
            raise _winerror(err, "ConnectNamedPipe")

    def accept(self, deadline: float) -> ServerChannel:
        """Wait for a client to connect to the instance. TimeoutError at the deadline, with the connect left pending
        for the next accept; OSError otherwise."""
        api = _api()
        if self._closed:
            raise OSError("listener is closed")
        if self._busy:
            raise OSError("the pipe instance still serves a channel; close it first")
        self._arm()
        if not self._connected:
            r = api.WaitForSingleObject(self._event, _ms_left(deadline))
            if r == _WAIT_TIMEOUT:
                raise TimeoutError("accept: deadline passed")
            if r != _WAIT_OBJECT_0:
                raise _last("WaitForSingleObject")
            ov, self._ov = self._ov, None
            n = api.wt.DWORD(0)
            if not api.GetOverlappedResult(self._h, ctypes.byref(ov), ctypes.byref(n), False):
                raise _last("ConnectNamedPipe")
        self._connected = False
        self._busy = True
        return ServerChannel(self)

    def _dacl_sddl(self) -> str:
        """The pipe's DACL as SDDL, read back from the handle (for the lane's own test of the access list)."""
        api = _api()
        p_dacl, p_sd = ctypes.c_void_p(), ctypes.c_void_p()
        rc = api.GetSecurityInfo(self._h, _SE_KERNEL_OBJECT, _DACL_SECURITY_INFORMATION, None, None,
                                 ctypes.byref(p_dacl), None, ctypes.byref(p_sd))
        if rc != 0:
            raise _winerror(rc, "GetSecurityInfo")
        try:
            text = api.wt.LPWSTR()
            if not api.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                    p_sd, _SDDL_REVISION_1, _DACL_SECURITY_INFORMATION, ctypes.byref(text), None):
                raise _last("ConvertSecurityDescriptorToStringSecurityDescriptorW")
            try:
                return str(text.value)
            finally:
                api.LocalFree(text)
        finally:
            api.LocalFree(p_sd)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            api = _api()
            if self._ov is not None:  # settle the pending connect before its OVERLAPPED is released
                ov, self._ov = self._ov, None
                n = api.wt.DWORD(0)
                api.CancelIoEx(self._h, ctypes.byref(ov))
                api.GetOverlappedResult(self._h, ctypes.byref(ov), ctypes.byref(n), True)
            api.CloseHandle(self._h)
            api.CloseHandle(self._event)
        except Exception:  # noqa: BLE001 - close never raises (§2.3)
            pass


def listen(address: str, client_accounts: Iterable[str]) -> Listener:
    """Create the broker's pipe: the first and only instance, local clients only, with a protected DACL that gives the
    own account full control and each listed account read and write only (§2.4). FileExistsError when the address is
    in use; ValueError for a bad address or id; OSError otherwise."""
    _check_address(address)
    clients = _check_ids(client_accounts)
    own = own_account()
    sddl = f"D:P(A;;FA;;;{own})" + "".join(
        f"(A;;0x{_CLIENT_RIGHTS:08x};;;{sid})" for sid in dict.fromkeys(clients) if sid != own)
    return Listener(address, _create_pipe(address, sddl))


def _create_pipe(address: str, sddl: str) -> Any:
    """The first and only instance of the pipe at `address`, with the security descriptor `sddl` set at creation.
    FileExistsError when the address is in use; OSError otherwise. `listen` builds the §2.4 access list; the lane's
    test also uses it for a pipe whose access list holds only the client rights (Lazy WF-201 M1)."""
    api = _api()
    p_sd = ctypes.c_void_p()
    if not api.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, _SDDL_REVISION_1, ctypes.byref(p_sd), None):
        raise _last("ConvertStringSecurityDescriptorToSecurityDescriptorW")
    try:
        sa = api.SECURITY_ATTRIBUTES(ctypes.sizeof(api.SECURITY_ATTRIBUTES), p_sd, False)
        handle = api.CreateNamedPipeW(
            address, _PIPE_ACCESS_DUPLEX | _FILE_FLAG_FIRST_PIPE_INSTANCE | _FILE_FLAG_OVERLAPPED,
            _PIPE_TYPE_BYTE | _PIPE_READMODE_BYTE | _PIPE_WAIT | _PIPE_REJECT_REMOTE_CLIENTS,
            1, _BUFFER, _BUFFER, 0, ctypes.byref(sa))
        err = ctypes.get_last_error()
    finally:
        api.LocalFree(p_sd)
    if handle is None or handle == _INVALID_HANDLE:
        if err in (_ERROR_ACCESS_DENIED, _ERROR_PIPE_BUSY):
            raise FileExistsError(None, f"the pipe address is in use: {address}", None, err)
        raise _winerror(err, "CreateNamedPipeW")
    return handle


def connect(address: str, deadline: float) -> Channel:
    """Open the pipe at identification level. Retries "not found" and "busy" every 0.05 s until the deadline, then
    raises FileNotFoundError or TimeoutError (the last seen); access denied raises PermissionError at once (§2.3). A
    malformed address is OSError(EINVAL) at once, since retrying cannot fix it: §2.3's `connect` row lists only
    OSErrors, and F3-OSI-L answers the same (WF-197-fix2). `listen` keeps ValueError."""
    try:
        _check_address(address)
    except ValueError as exc:
        raise OSError(errno.EINVAL, str(exc)) from None
    api = _api()
    last = "notfound"
    while True:
        handle = api.CreateFileW(address, _CLIENT_OPEN, 0, None, _OPEN_EXISTING,
                                 _FILE_FLAG_OVERLAPPED | _SECURITY_SQOS_PRESENT | _SECURITY_IDENTIFICATION, None)
        if handle is not None and handle != _INVALID_HANDLE:
            try:
                return Channel(handle)
            except Exception:
                api.CloseHandle(handle)
                raise
        err = ctypes.get_last_error()
        if err == _ERROR_ACCESS_DENIED:
            raise PermissionError(None, f"access denied by the pipe's access list: {address}", None, err)
        if err == _ERROR_FILE_NOT_FOUND:
            last = "notfound"
            left = deadline - time.monotonic()
            if left <= 0:
                break
            time.sleep(min(_STEP_S, left))
            continue
        if err == _ERROR_PIPE_BUSY:
            last = "busy"
            left = deadline - time.monotonic()
            if left <= 0:
                break
            if not api.WaitNamedPipeW(address, max(1, int(min(_STEP_S, left) * 1000))):
                werr = ctypes.get_last_error()
                if werr == _ERROR_FILE_NOT_FOUND:
                    last = "notfound"
                elif werr != _ERROR_SEM_TIMEOUT:
                    raise _winerror(werr, "WaitNamedPipeW")
            continue
        raise _winerror(err, "CreateFileW")
    if last == "busy":
        raise TimeoutError(f"the pipe stayed busy until the deadline: {address}")
    raise FileNotFoundError(None, f"no pipe at the address until the deadline: {address}", None, _ERROR_FILE_NOT_FOUND)
