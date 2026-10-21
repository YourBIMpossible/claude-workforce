"""The peer identity seam (PACKAGE-FIX3-II, lane F3-BRK, P0; INTERFACE-CONTRACT-FIX3-II r2 sections 2.1, 2.2 and 3.1).

A brokered `wf` call carries a `Caller`: the `PeerIdentity` the broker read from the connection, plus the broker's
own account and wfcore sha. `commands` (lane F3-ACC) decides the claim from it; direct mode passes no caller.

The account is read from the connection by the platform module, `peer_win` (lane F3-OSI-W: the named-pipe client's
token user SID) or `peer_linux` (lane F3-OSI-L: `SO_PEERCRED`'s uid). This module only dispatches to it, lazily, and
checks the id's form. Nothing here reads the client's environment, command line, process tree or paths (SII:61).

Callers use the functions as module attributes (`peer.own_account()`), so tests can patch them (section 2.2).
Imports (section 6.1): stdlib only, and `.peer_win` / `.peer_linux` lazily, at call time.
"""
from __future__ import annotations

import importlib
import re
import sys
from dataclasses import dataclass
from typing import Protocol, Sequence

PLATFORMS = ("win32", "linux")
_MODULES = {"win32": "peer_win", "linux": "peer_linux"}
# Section 2.2a: the canonical id forms, so ids compare as strings. A SID string as ConvertSidToStringSidW writes it;
# a decimal uid with no sign or leading zero, at most 4294967294 ((uid_t) -1 is "no user").
_SID = re.compile(r"S-1-(0|[1-9][0-9]{0,9})(-(0|[1-9][0-9]{0,9})){1,15}")
_UID = re.compile(r"0|[1-9][0-9]{0,9}")
UID_MAX = 4294967294


@dataclass(frozen=True)
class PeerIdentity:
    """Section 2.1: the connecting client, as the broker read it from the connection."""
    account: str          # a SID string on Windows, a decimal uid on Linux; it passes is_account_id
    pid: int | None       # the client's process id, for the log only; it never decides anything
    platform: str         # "win32" or "linux" (host_platform())


@dataclass(frozen=True)
class Caller:
    """Section 3.1: what `run_brokered` passes to `commands` as `caller`. Direct mode passes None instead."""
    peer: PeerIdentity
    broker_account: str   # the broker's own account id, read once at start
    broker_wfcore: str    # the broker's wfcore sha (section 1.7)

    @property
    def own(self) -> bool:
        """The owner account: the client runs as the broker's own account (SII:93)."""
        return self.peer.account == self.broker_account


class Channel(Protocol):
    """Section 2.3: one connection, client side. ServerChannel has the same methods plus peer_account()."""

    def recv_exact(self, n: int, deadline: float) -> bytes: ...

    def send_all(self, data: bytes, deadline: float) -> None: ...

    def close(self) -> None: ...


class ServerChannel(Channel, Protocol):
    def peer_account(self) -> tuple[str, int | None]: ...


class Listener(Protocol):
    address: str

    def accept(self, deadline: float) -> ServerChannel: ...

    def close(self) -> None: ...


def host_platform() -> str:
    """The host's platform name, `win32` or `linux`; any other host raises OSError (R1 supports Windows and Linux)."""
    if sys.platform == "win32":
        return "win32"
    if sys.platform.startswith("linux"):
        return "linux"
    raise OSError(f"unsupported platform {sys.platform!r}: the broker supports win32 and linux")


def is_account_id(s: str, platform: str) -> bool:
    """True only for the canonical id form of `platform` (section 2.2a); False for anything else."""
    if not isinstance(s, str):
        return False
    if platform == "win32":
        return _SID.fullmatch(s) is not None
    if platform == "linux":
        return _UID.fullmatch(s) is not None and int(s) <= UID_MAX
    return False


def _platform_module():
    """The platform module for this host, imported at call time. A missing one raises OSError (chained)."""
    plat = host_platform()
    name = _MODULES[plat]
    try:
        return importlib.import_module(f".{name}", __package__)
    except ImportError as exc:
        raise OSError(f"the {plat} peer module (wfcore.{name}) is not available: {exc}") from exc


def own_account() -> str:
    """This process's account id, from the platform module. Any failure raises OSError (chained), and so does an id
    that is not in the host's canonical form: the account map compares ids as strings (section 2.2a)."""
    plat = host_platform()
    try:
        account = _platform_module().own_account()
    except OSError:
        raise
    except Exception as exc:  # noqa: BLE001 -- section 2.2: any failure is an OSError
        raise OSError(f"cannot read this process's account: {type(exc).__name__}: {exc}") from exc
    if not is_account_id(account, plat):
        raise OSError(f"this process's account {account!r} is not a {plat} account id")
    return account


def listen(address: str, client_accounts: Sequence[str]) -> Listener:
    """Section 2.2: the platform module's listen, which raises as section 2.3 says."""
    return _platform_module().listen(address, client_accounts)


def connect(address: str, deadline: float) -> Channel:
    """Section 2.2: the platform module's connect, which raises as section 2.3 says."""
    return _platform_module().connect(address, deadline)


def identify(channel: ServerChannel) -> PeerIdentity:
    """Section 2.2: the client on `channel`, read after the whole request frame (section 1.8). A failure to read it,
    or an account not in the host's form, raises OSError, which the broker answers `rejected(caller-unattributed)`."""
    plat = host_platform()
    try:
        account, pid = channel.peer_account()
    except OSError:
        raise
    except Exception as exc:  # noqa: BLE001 -- a malformed answer is a failure to read the peer (fail closed)
        raise OSError(f"cannot read the peer's account: {type(exc).__name__}: {exc}") from exc
    if not is_account_id(account, plat):
        raise OSError(f"the peer's account {account!r} is not a {plat} account id")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid < 0:
        pid = None  # the pid is for the log only (section 2.1), so an unusable one is dropped, never decisive
    return PeerIdentity(account=account, pid=pid, platform=plat)
