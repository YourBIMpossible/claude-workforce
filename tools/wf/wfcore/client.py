"""The broker client (PACKAGE-FIX3-II, lane F3-BRK; INTERFACE-CONTRACT-FIX3-II r2 sections 1.4, 1.6, 2.3 and 5).

`call` sends one request and returns the response's (exit, result), so `wf.main()` prints and exits as it does for a
direct call. It never falls back to a direct call: a broker it cannot reach is `broker-unavailable`, and a response it
does not get in full is `broker-response-lost`, carrying the msg_id to re-send with (section 1.6).

Imports (section 6.1): stdlib, `.wire` and `.peer`.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from . import peer, wire

CONNECT_BOUND_S = 60.0    # from the call until the request is sent (section 1.4)
RESPONSE_BOUND_S = 120.0  # from the request being sent until the response is complete: over 3 x 17 s (section 1.4)
EXIT_ERR = 1


def _msg_id(request: dict) -> str | None:
    for a in request.get("argv", []):
        if isinstance(a, str) and a.startswith("--msg-id="):
            return a[len("--msg-id="):]
    return None


def unavailable(address: str, exc: BaseException) -> tuple[int, dict]:
    """Section 2.3's mapping: not found, busy, access denied; any other OSError is no-endpoint, with its text."""
    if isinstance(exc, PermissionError):
        reason = "access-denied"
    elif isinstance(exc, TimeoutError):
        reason = "busy"
    else:  # FileNotFoundError and any other OSError
        reason = "no-endpoint"
    return EXIT_ERR, {"ok": False, "code": "broker-unavailable", "reason": reason, "address": address,
                      "error": f"{type(exc).__name__}: {exc}"}


def lost(msg_id: str | None, error: str) -> tuple[int, dict]:
    """Section 5: no complete, well-formed response. The command may or may not have run; re-sending it with the same
    msg_id is safe, because the ledger takes a msg_id once (section 1.6)."""
    return EXIT_ERR, {"ok": False, "code": "broker-response-lost", "error": error, "msg_id": msg_id,
                      "action": f"re-send the same command with --msg-id {msg_id}"}


def call(address: str, request: dict, *, connect: Callable[[str, float], Any] | None = None) -> tuple[int, dict]:
    """Send `request` (section 1.2) to the broker at `address`; return (exit, result). `connect=None` is resolved as
    `peer.connect` at call time, so a patch of `peer.connect` applies (section 3.5)."""
    msg_id = _msg_id(request)
    body = wire.encode_request(request)
    if len(body) > wire.MAX_REQUEST_BYTES:  # checked before connecting: nothing is sent
        return EXIT_ERR, wire.rejected("wire", f"the request is {len(body)} bytes; the bound is "
                                               f"{wire.MAX_REQUEST_BYTES} bytes (section 1.4); nothing was sent")
    connect = connect if connect is not None else peer.connect
    deadline = time.monotonic() + CONNECT_BOUND_S
    try:
        channel = connect(address, deadline)
    except OSError as exc:
        return unavailable(address, exc)
    try:
        try:
            channel.send_all(wire.frame(body), deadline)
        except TimeoutError as exc:  # the connect bound runs until the request is sent (section 1.4)
            return unavailable(address, exc)
        except OSError as exc:  # the broker may have read the whole frame before the connection broke
            return lost(msg_id, f"the request could not be sent in full: {type(exc).__name__}: {exc}")
        deadline = time.monotonic() + RESPONSE_BOUND_S
        try:
            raw = wire.read_frame(channel, deadline, wire.MAX_RESPONSE_BYTES)
        except wire.FrameTooLarge as exc:
            return lost(msg_id, f"malformed response: {exc}")
        except EOFError:
            return lost(msg_id, "the broker closed the connection before a complete response")
        except TimeoutError:
            return lost(msg_id, f"no complete response within {RESPONSE_BOUND_S:g} s")
        except OSError as exc:
            return lost(msg_id, f"the response could not be read: {type(exc).__name__}: {exc}")
        try:
            resp = wire.check_response(wire.decode(raw))
        except wire.WireError as exc:
            return lost(msg_id, f"malformed response: {exc}")
        return resp["exit"], resp["result"]
    finally:
        channel.close()
