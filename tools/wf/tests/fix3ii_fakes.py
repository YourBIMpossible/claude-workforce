"""Test fakes for PACKAGE-FIX3-II (lane F3-BRK; INTERFACE-CONTRACT-FIX3-II r2 section 2.5). Tests only.

`discover` does not collect this file (it does not match `test*.py`). No `wfcore` module, `wf.py` or `wfbroker.py`
imports it, and no environment variable, option or file selects a fake: a test injects one in-process, through
`broker.Broker(listener=…)`, `client.call(…, connect=…)`, or a `mock.patch` of `peer.listen` / `peer.connect`.

- MemoryTransport: `listen` and `connect` over `socket.socketpair()`; each connection's server end answers
  `peer_account()` with a scripted `(account, pid)` or raises a scripted OSError.
- ScriptedPeerListener(inner, script): wraps any Listener, so each accepted channel's `peer_account()` gives the next
  scripted entry (AII:33's "fed a fake account … in memory or over the real pipe or socket").
- BrokenSendListener(inner): every accepted channel's `send_all` fails, as a response write that breaks.
- FAKE: account ids in the host's form for the broker's own account and the workers. They are fixed test ids, so they
  differ from the account a test host runs as (a test asserts it where the platform module exists).
- direct_run: section 6.3's test `run`: a brokered write on F3-BRK's own head is `wf.run` on the ledger, with no caller.
"""
from __future__ import annotations

import collections
import socket
import threading
import time
from typing import Any, Callable, Iterable

import test_wf  # noqa: F401  (puts tools/wf on sys.path)

import wf
from wfcore import client, peer, wire

PeerItem = Any  # (account, pid) or an OSError instance


def fake_ids(platform: str) -> dict[str, str]:
    if platform == "win32":
        base = "S-1-5-21-3923483218-1810244171-2401512107-"
        return {"own": base + "3000", "B1": base + "3101", "B2": base + "3102", "R1": base + "3201",
                "unlisted": base + "3999"}
    return {"own": "3999000", "B1": "3999101", "B2": "3999102", "R1": "3999201", "unlisted": "3999999"}


PLATFORM = peer.host_platform()
FAKE = fake_ids(PLATFORM)


def address(name: str) -> str:
    """A short test address in the host's form (section 1.10); memory endpoints never touch the filesystem."""
    return f"\\\\.\\pipe\\wf-test-{name}" if PLATFORM == "win32" else f"/nonexistent/wf-test/{name}.sock"


class SocketChannel:
    """One end of a socket pair as section 2.3's Channel, and with a peer item as its ServerChannel."""

    def __init__(self, sock: socket.socket, peer_item: PeerItem = None) -> None:
        self._sock = sock
        self._peer = peer_item
        self._closed = False

    def recv_exact(self, n: int, deadline: float) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{len(buf)} of {n} bytes by the deadline")
            self._sock.settimeout(remaining)
            try:
                chunk = self._sock.recv(min(n - len(buf), 1 << 16))
            except socket.timeout:
                raise TimeoutError(f"{len(buf)} of {n} bytes by the deadline") from None
            if not chunk:
                raise EOFError(f"the peer closed after {len(buf)} of {n} bytes")
            buf += chunk
        return bytes(buf)

    def send_all(self, data: bytes, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("the deadline passed before sending")
        self._sock.settimeout(remaining)
        try:
            self._sock.sendall(data)
        except socket.timeout:
            raise TimeoutError("send did not finish by the deadline") from None

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._sock.close()
            except OSError:
                pass

    def peer_account(self) -> tuple[str, int | None]:
        if isinstance(self._peer, BaseException):
            raise self._peer
        if self._peer is None:
            raise OSError("no scripted peer for this connection")
        return self._peer


class MemoryListener:
    def __init__(self, address_: str, client_accounts: Iterable[str]) -> None:
        self.address = address_
        self.client_accounts = tuple(client_accounts)
        self.closed = False
        self._queue: collections.deque[SocketChannel] = collections.deque()
        self._cond = threading.Condition()

    def push(self, channel: SocketChannel) -> None:
        with self._cond:
            self._queue.append(channel)
            self._cond.notify()

    def accept(self, deadline: float) -> SocketChannel:
        with self._cond:
            while not self._queue:
                if self.closed:
                    raise OSError("listener closed")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("no connection by the deadline")
                self._cond.wait(remaining)
            return self._queue.popleft()

    def close(self) -> None:
        with self._cond:
            self.closed = True
            while self._queue:  # a connection never accepted is closed: its client sees EOF
                self._queue.popleft().close()
            self._cond.notify_all()


class MemoryTransport:
    """Section 2.5's MemoryTransport: one in-memory listener per address, as `peer.listen` and `peer.connect` give."""

    def __init__(self, default_peer: PeerItem = None) -> None:
        self.default_peer = default_peer
        self.listeners: dict[str, MemoryListener] = {}
        self._lock = threading.Lock()

    def listen(self, address_: str, client_accounts: Iterable[str]) -> MemoryListener:
        with self._lock:
            live = self.listeners.get(address_)
            if live is not None and not live.closed:
                raise FileExistsError(f"{address_} is in use")
            listener = self.listeners[address_] = MemoryListener(address_, client_accounts)
            return listener

    def connect(self, address_: str, deadline: float, peer_item: PeerItem = None) -> SocketChannel:
        with self._lock:
            listener = self.listeners.get(address_)
            if listener is None or listener.closed:
                raise FileNotFoundError(f"no listener at {address_}")
        a, b = socket.socketpair()
        listener.push(SocketChannel(b, self.default_peer if peer_item is None else peer_item))
        return SocketChannel(a)

    def connector(self, peer_item: PeerItem) -> Callable[[str, float], SocketChannel]:
        """A `connect` whose connections the broker reads as `peer_item`."""
        return lambda address_, deadline: self.connect(address_, deadline, peer_item)


class _ScriptedChannel:
    def __init__(self, inner: Any, item: PeerItem, send_error: BaseException | None = None) -> None:
        self._inner, self._item, self._send_error = inner, item, send_error

    def recv_exact(self, n: int, deadline: float) -> bytes:
        return self._inner.recv_exact(n, deadline)

    def send_all(self, data: bytes, deadline: float) -> None:
        if self._send_error is not None:
            raise self._send_error
        self._inner.send_all(data, deadline)

    def close(self) -> None:
        self._inner.close()

    def peer_account(self) -> tuple[str, int | None]:
        if self._item is _INNER:
            return self._inner.peer_account()
        if isinstance(self._item, BaseException):
            raise self._item
        return self._item


_INNER = object()


class ScriptedPeerListener:
    """Wraps a real Listener: each accepted channel's `peer_account()` gives the next scripted (account, pid), or raises
    the scripted OSError. An exhausted script raises OSError (fail closed)."""

    def __init__(self, inner: Any, script: Iterable[PeerItem]) -> None:
        self.inner = inner
        self.address = inner.address
        self._script = collections.deque(script)

    def accept(self, deadline: float) -> _ScriptedChannel:
        channel = self.inner.accept(deadline)
        item = self._script.popleft() if self._script else OSError("the peer script is exhausted")
        return _ScriptedChannel(channel, item)

    def close(self) -> None:
        self.inner.close()


class BrokenSendListener:
    """Wraps a Listener: each accepted channel's `send_all` raises BrokenPipeError, as a response write that breaks."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.address = inner.address

    def accept(self, deadline: float) -> _ScriptedChannel:
        return _ScriptedChannel(self.inner.accept(deadline), _INNER, BrokenPipeError("the response write broke"))

    def close(self) -> None:
        self.inner.close()


class ScriptChannel:
    """A client-side Channel with a canned reply, for `client.call`: it records what was sent and each deadline, and
    raises the scripted errors."""

    def __init__(self, reply: bytes = b"", *, recv_error: BaseException | None = None,
                 send_error: BaseException | None = None) -> None:
        self.reply, self.recv_error, self.send_error = reply, recv_error, send_error
        self.sent = b""
        self.deadlines: list[tuple[str, float]] = []
        self.closed = False

    def recv_exact(self, n: int, deadline: float) -> bytes:
        self.deadlines.append(("recv", deadline))
        if self.recv_error is not None:
            raise self.recv_error
        chunk, self.reply = self.reply[:n], self.reply[n:]
        if len(chunk) < n:
            raise EOFError(f"the peer closed after {len(chunk)} of {n} bytes")
        return chunk

    def send_all(self, data: bytes, deadline: float) -> None:
        self.deadlines.append(("send", deadline))
        if self.send_error is not None:
            raise self.send_error
        self.sent += data

    def close(self) -> None:
        self.closed = True


def ledger_state(db: str, job: str = "j") -> tuple[int, tuple[int, str]]:
    """(row count, (head seq, head self)) of a job, read-only: a refusal must leave both unchanged."""
    from wfcore import db as dbm
    conn = dbm.connect(db, create=False)
    try:
        with dbm.read_snapshot(conn):
            return len(dbm.rows(conn, job)), dbm.head(conn, job)
    finally:
        conn.close()


def direct_run(argv: list[str], inputs: dict[str, str], *, db: str, caller: peer.Caller) -> tuple[int, dict]:
    """Section 6.3: on F3-BRK's own head a write through the broker runs `wf.run` directly on the broker's ledger, with
    no caller. That tests the transport, the framing, re-send and serial dispatch, not the account check."""
    return wf.run(["--db", db] + list(argv) + [f"{opt}={text}" for opt, text in inputs.items()])


def stop_request(wfcore: str) -> dict:
    return {"v": wire.PROTOCOL_VERSION, "op": "stop", "argv": [], "inputs": {}, "wfcore": wfcore}


class RunningBroker:
    """`Broker.serve()` on a thread for the length of a `with` block; on the way out it is stopped by the own account
    (a stop request) unless the test stopped it already, and the thread must end."""

    def __init__(self, broker_: Any, transport: MemoryTransport, address_: str) -> None:
        self.broker = broker_
        self.transport = transport
        self.address = address_
        self.served: int | None = None
        self.error: BaseException | None = None
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self) -> None:
        try:
            self.served = self.broker.serve()
        except BaseException as exc:  # noqa: BLE001 -- surfaced by the test through `error`
            self.error = exc

    def __enter__(self) -> "RunningBroker":
        self._thread.start()
        return self

    def stop(self) -> tuple[int, dict]:
        res = client.call(self.address, stop_request(self.broker.wfcore),
                          connect=self.transport.connector((self.broker.own_account, 1)))
        self._thread.join(10)
        return res

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def __exit__(self, *exc: Any) -> None:
        if self._thread.is_alive():
            self.stop()
        if self._thread.is_alive():
            raise AssertionError("the broker thread did not end")
