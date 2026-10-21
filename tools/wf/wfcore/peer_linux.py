"""The Linux side of the broker's peer seam (PACKAGE FIX3-II lane F3-OSI-L; interface contract r2 §2.3, §2.4).

An AF_UNIX stream socket at a filesystem path. The listener binds it, sets its mode to 0660, and only then listens,
so no client can connect before the mode is set. A stale socket file (one whose connect is refused) is replaced; a
live one is refused. The peer's account is the uid that `SO_PEERCRED` reports, which the kernel records at
`connect()`; the pid is for the log only. Nothing about the client beyond that is read.

This module imports only the standard library and no `wfcore` module, and raises only builtin exceptions, so it
depends on no other lane (contract §2.3, D12). `peer.py` imports it lazily, on Linux only.

Deadlines are `time.monotonic()` values. The only mode set is the socket's own 0660, at creation; the owner, the
group and the directory are never changed (contract §2.4, D24). The directory's access list is setup S8.
"""
from __future__ import annotations

import errno
import os
import socket
import stat
import struct
import time
from collections.abc import Sequence

PLATFORM = "linux"

#: Contract §1.10: an absolute path of at most 107 bytes of UTF-8 (`sun_path` is 108 bytes, with its NUL).
_SUN_PATH_MAX_BYTES = 107
#: Contract §2.2a: the largest uid accepted as an account id ((uid_t)-1 is not an account).
_UID_MAX = 4294967294
#: Contract §2.4: the socket file's mode, set after bind and before listen.
_SOCKET_MODE = 0o660
#: Contract §2.4: "Backlog ≥ 4".
_BACKLOG = 16
#: Contract §1.4: the connect retry step.
_CONNECT_RETRY_S = 0.05
#: `struct ucred {pid_t pid; uid_t uid; gid_t gid;}` (Linux socket(7)).
_UCRED = struct.Struct("=iII")
_NO_UID = 0xFFFFFFFF


def _new_socket() -> socket.socket:
    """A fresh AF_UNIX stream socket (close-on-exec, as Python makes every socket). Tests patch this seam."""
    return socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)


def _address_problem(address: object) -> str | None:
    """Why `address` is not a Linux broker address (contract §1.10), or None when it is one."""
    if not isinstance(address, str):
        return f"the address must be a str, not {type(address).__name__}"
    if not address:
        return "the address is empty"
    if "\0" in address:
        return "the address holds a NUL character (abstract socket names are not used)"
    if not os.path.isabs(address):
        return f"the address must be an absolute path: {address!r}"
    try:
        size = len(address.encode("utf-8"))
    except UnicodeEncodeError:
        return f"the address is not valid UTF-8: {address!r}"
    if size > _SUN_PATH_MAX_BYTES:
        return f"the address is {size} bytes of UTF-8, over the {_SUN_PATH_MAX_BYTES}-byte sun_path limit"
    return None


def _is_uid(value: object) -> bool:
    """Contract §2.2a's Linux id form: a canonical decimal uid of at most _UID_MAX."""
    if not isinstance(value, str) or not value or not value.isascii() or not value.isdigit():
        return False
    if len(value) > 10 or (len(value) > 1 and value[0] == "0"):
        return False
    return int(value) <= _UID_MAX


def _remaining(deadline: float, what: str) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError(errno.ETIMEDOUT, f"{what}: deadline reached")
    return left


def own_account() -> str:
    """This process's account: its effective uid, as a decimal string."""
    try:
        return str(os.geteuid())
    except Exception as exc:  # os.geteuid exists only on POSIX
        raise OSError(f"own account unreadable: {exc}") from exc


class _SocketChannel:
    """One connected stream. Every call is bounded by its deadline; `close` is idempotent and never raises."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._closed = False

    def recv_exact(self, n: int, deadline: float) -> bytes:
        """Exactly n bytes. EOFError when the peer closes first; TimeoutError at the deadline; OSError."""
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise ValueError(f"recv_exact needs a byte count >= 0, not {n!r}")
        buf = bytearray(n)
        view = memoryview(buf)
        got = 0
        while got < n:
            self._sock.settimeout(_remaining(deadline, "recv"))
            count = self._sock.recv_into(view[got:])
            if count == 0:
                raise EOFError(f"the peer closed after {got} of {n} bytes")
            got += count
        return bytes(buf)

    def send_all(self, data: bytes, deadline: float) -> None:
        """All of data. BrokenPipeError or ConnectionError when the peer is gone; TimeoutError at the deadline."""
        view = memoryview(data).cast("B")
        sent = 0
        flags = getattr(socket, "MSG_NOSIGNAL", 0)
        while sent < len(view):
            self._sock.settimeout(_remaining(deadline, "send"))
            sent += self._sock.send(view[sent:], flags)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._sock.close()
        except OSError:
            pass


class Channel(_SocketChannel):
    """The client's end, from `connect`."""


class ServerChannel(_SocketChannel):
    """The broker's end, from `Listener.accept`: a Channel plus the peer's identity."""

    def peer_account(self) -> tuple[str, int | None]:
        """(the account id, the pid) from SO_PEERCRED. The broker calls it only after the whole frame is read."""
        raw = self._sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _UCRED.size)
        if len(raw) != _UCRED.size:
            raise OSError(errno.EPROTO, f"SO_PEERCRED returned {len(raw)} bytes, not {_UCRED.size}")
        pid, uid, _gid = _UCRED.unpack(raw)
        if uid == _NO_UID:
            raise OSError(errno.ENOTCONN, "the peer's credentials are not available")
        return str(uid), (pid if pid > 0 else None)


class Listener:
    """The broker's socket. `close` closes it and unlinks the socket file it created, and never raises."""

    def __init__(self, sock: socket.socket, address: str, client_accounts: tuple[str, ...], ident: tuple[int, int]):
        self._sock = sock
        self._address = address
        # The ids the broker admits besides its own (contract §1.8 step 3). On Linux the socket does not apply
        # them; the directory's group (setup S8) and the broker's admission step do.
        self._client_accounts = client_accounts
        self._ident = ident
        self._closed = False

    @property
    def address(self) -> str:
        return self._address

    def accept(self, deadline: float) -> ServerChannel:
        """The next client. TimeoutError at the deadline; OSError."""
        self._sock.settimeout(_remaining(deadline, "accept"))
        conn, _ = self._sock.accept()
        return ServerChannel(conn)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._sock.close()
        except OSError:
            pass
        try:  # unlink only the file this listener made, not one that has since replaced it
            st = os.lstat(self._address)
            if stat.S_ISSOCK(st.st_mode) and (st.st_dev, st.st_ino) == self._ident:
                os.unlink(self._address)
        except OSError:
            pass


def _probe(address: str) -> str:
    """What answers at an existing path: "live", "stale" (refused), "gone" (vanished) or "not-socket"."""
    try:
        st = os.lstat(address)
    except FileNotFoundError:
        return "gone"
    if not stat.S_ISSOCK(st.st_mode):
        return "not-socket"
    probe = _new_socket()
    try:
        probe.setblocking(False)
        probe.connect(address)
        return "live"
    except ConnectionRefusedError:
        return "stale"
    except FileNotFoundError:
        return "gone"
    except (BlockingIOError, InterruptedError):
        return "live"  # a listener whose queue is full is still a listener
    finally:
        probe.close()


def listen(address: str, client_accounts: Sequence[str]) -> Listener:
    """Bind `address`, set mode 0660, then listen. A stale socket file is replaced; a live one is refused.

    FileExistsError when a live listener answers at the address; ValueError for a bad address or id; OSError
    otherwise (a path that exists and is not a socket is never removed).

    Whether a socket file is live is learned the way the contract defines it, by connecting: the probe connects
    and closes at once without sending, so a live broker sees one connection with no frame, which it closes with
    no answer (contract §1.8 step 1).
    """
    problem = _address_problem(address)
    if problem:
        raise ValueError(problem)
    if isinstance(client_accounts, (str, bytes)):
        raise ValueError("client_accounts must be a sequence of ids, not a single string")
    accounts = tuple(client_accounts)
    for value in accounts:
        if not _is_uid(value):
            raise ValueError(f"not a Linux account id (a canonical uid <= {_UID_MAX}): {value!r}")
    sock = _new_socket()
    bound = False
    try:
        for attempt in (1, 2):
            try:
                sock.bind(address)
                bound = True
                break
            except OSError as exc:
                if exc.errno != errno.EADDRINUSE:
                    raise
                state = _probe(address)
                if state == "live" or attempt == 2:
                    raise FileExistsError(errno.EADDRINUSE, "a broker is already listening at the address",
                                          address) from None
                if state == "not-socket":
                    raise OSError(errno.ENOTSOCK, "the address exists and is not a socket; it is left in place",
                                  address) from None
                if state == "stale":
                    try:
                        os.unlink(address)
                    except FileNotFoundError:
                        pass
        os.chmod(address, _SOCKET_MODE)
        st = os.lstat(address)
        if not stat.S_ISSOCK(st.st_mode) or stat.S_IMODE(st.st_mode) != _SOCKET_MODE or st.st_uid != os.geteuid():
            raise OSError(errno.EPERM, f"the socket file is not this account's mode-0660 socket: {oct(st.st_mode)}",
                          address)
        ident = (st.st_dev, st.st_ino)
        sock.listen(_BACKLOG)
    except BaseException:
        sock.close()
        if bound:  # the file this call just bound is not left behind
            try:
                if stat.S_ISSOCK(os.lstat(address).st_mode):
                    os.unlink(address)
            except OSError:
                pass
        raise
    return Listener(sock, address, accounts, ident)


def connect(address: str, deadline: float) -> Channel:
    """Connect to the broker, retrying "not found" and "busy" every _CONNECT_RETRY_S until the deadline.

    At the deadline: FileNotFoundError when the last attempt was not found or refused, TimeoutError when it was
    busy. Access denied raises PermissionError at once; any other OSError is raised as it is. A bad address is an
    OSError at once (EINVAL, or ENAMETOOLONG over the sun_path limit), since retrying cannot fix it.
    """
    problem = _address_problem(address)
    if problem:
        code = errno.ENAMETOOLONG if "sun_path" in problem else errno.EINVAL
        raise OSError(code, problem, address if isinstance(address, str) else None)
    last = "not-found"
    while True:
        left = deadline - time.monotonic()
        sock = _new_socket()
        try:
            if left > 0:
                sock.settimeout(left)
            else:
                sock.setblocking(False)
            sock.connect(address)
            return Channel(sock)
        except (FileNotFoundError, ConnectionRefusedError):
            sock.close()
            last = "not-found"
        except (BlockingIOError, InterruptedError, TimeoutError):
            sock.close()
            last = "busy"
        except BaseException:
            sock.close()
            raise
        left = deadline - time.monotonic()
        if left <= 0:
            if last == "busy":
                raise TimeoutError(errno.ETIMEDOUT, "the broker stayed busy until the deadline", address)
            raise FileNotFoundError(errno.ENOENT, "no broker answered at the address (not found or refused)",
                                    address)
        time.sleep(min(_CONNECT_RETRY_S, left))
