"""The account-checking broker (PACKAGE-FIX3-II, lane F3-BRK; INTERFACE-CONTRACT-FIX3-II r2 sections 1.8, 3.5 and 5).

`Broker.serve` takes one connection at a time (SII:256) and answers it in section 1.8's order; the first failure
answers:
1. read the frame within READ_BOUND_S (a header over 3 MiB is answered `rejected(wire)` without reading the body; an
   EOF partway through the frame closes with no answer);
2. read the peer's account from the connection (`peer.identify`), after the whole frame: `rejected(caller-unattributed)`;
3. admission, the own account or a `--client-account` id: `rejected(caller-unattributed)`;
4. decode and check the request: `rejected(wire)`;
5. a stop: from the own account, `STOPPING`, then the broker stops; from any other, `rejected(caller-mismatch)`;
6. the wfcore versions: `rejected(version-mismatch)`;
7. `run(argv, inputs, db=…, caller=…)`, whose result is answered unchanged; an exception in it is `internal-error`,
   exit 4, as `main()` answers one;
8. encode (truncating past 8 MiB), send within WRITE_BOUND_S, and log one line.

The broker never reads the client's environment, command line, process tree or paths; the account comes only from
the connection. It dispatches only through `run`; production passes `wf.run_brokered`.

The log (section 3.5; D27): one JSON line per connection in `log_path`, with the keys of LOG_KEYS, null where the step
that sets one was not reached, and never an argv value or an input. `reason` (AMENDMENT-FIX3-II-A2 item 3) names a
refusal: the `<name>` of a `rejected(<name>)` result, null otherwise. At LOG_MAX_BYTES the file is renamed to its `.1`
name, replacing the previous one, and a new file starts. Rotation touches those two files only. Refusals are recorded
here and nowhere else: no refusal writes a ledger row, a marker or an export (section 5).

Imports (section 6.1): stdlib, `.wire` and `.peer`. MUST NOT import `.commands` or `.txn`.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Callable, Sequence

from . import peer, wire

READ_BOUND_S = 10.0    # from accept until the whole request frame is read (section 1.4)
WRITE_BOUND_S = 10.0   # for the response write (section 1.4)
ACCEPT_STEP_S = 1.0    # serve() waits for a connection in steps of this, re-checking for a stop between them
LOG_MAX_BYTES = 16 * 1024 * 1024
LOG_KEYS = ("ts", "peer", "pid", "op", "cmd", "job", "msg_id", "exit", "code", "reason", "ms")
EXIT_ERR, EXIT_INTERNAL = 1, 4
_REFUSAL = re.compile(r"rejected\(([a-z][a-z0-9-]*)\)(?::|\Z)")

RunFn = Callable[..., tuple[int, dict]]


def now_ts() -> str:
    """validate.now_ts()'s form (UTC, `YYYY-MM-DDTHH:MM:SS.mmmZ`), which this module may not import (section 6.1)."""
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int((t % 1) * 1000):03d}Z"


def previous_log_path(log_path: str) -> str:
    """The one rotated file: `broker-log.jsonl` -> `broker-log.1.jsonl`, beside it."""
    stem, ext = os.path.splitext(log_path)
    return f"{stem}.1{ext}"


def refusal_name(result: dict) -> str | None:
    """AMENDMENT-FIX3-II-A2 item 3: the name in a refusal's `rejected(<name>)` error (`caller-mismatch`, `wire`, `usage`,
    …), without the wrapper; None for any other result, whose `code` then says what it was."""
    error = result.get("error")
    if result.get("ok") is False and isinstance(error, str):
        match = _REFUSAL.match(error)
        if match:
            return match.group(1)
    return None


def _msg_id(argv: list[str]) -> str | None:
    for a in argv:
        if a.startswith("--msg-id="):
            return a[len("--msg-id="):]
    return None


class Broker:
    def __init__(self, *, db: str, listener: Any, own_account: str, wfcore: str, client_accounts: Sequence[str],
                 run: RunFn, log_path: str) -> None:
        self.db = db
        self.listener = listener
        self.own_account = own_account
        self.wfcore = wfcore
        self.client_accounts = frozenset(client_accounts)
        self.run = run
        self.log_path = log_path
        self.served = 0
        self._stopping = False

    # --- the loop -------------------------------------------------------------------------------------------------

    def serve(self) -> int:
        """Answer connections one at a time until the own account asks the broker to stop. Returns `served`: the
        connections accepted, the stop's included. The listener is closed on the way out."""
        try:
            while not self._stopping:
                try:
                    channel = self.listener.accept(time.monotonic() + ACCEPT_STEP_S)
                except TimeoutError:
                    continue
                self.served += 1
                self.handle(channel)
        finally:
            self.listener.close()
        return self.served

    def handle(self, channel: Any) -> None:
        """One connection, section 1.8's steps 1-8. An exception outside step 7 closes the connection and is logged
        (code `internal-error`, exit null), and the broker keeps serving."""
        start = time.monotonic()
        entry: dict[str, Any] = dict.fromkeys(LOG_KEYS)
        entry["ts"] = now_ts()
        try:
            answer = self._answer(channel, entry, start)
            if answer is not None:
                exit_code, result, peer_account = answer
                data, exit_code, _ = wire.response(exit_code, result, peer_account=peer_account,
                                                   broker_account=self.own_account, broker_wfcore=self.wfcore)
                entry["exit"] = exit_code
                code = result.get("code", result.get("outcome"))
                entry["code"] = code if isinstance(code, str) else None
                entry["reason"] = refusal_name(result)
                try:
                    channel.send_all(data, time.monotonic() + WRITE_BOUND_S)
                except OSError:  # TimeoutError, BrokenPipeError, ConnectionError: the outcome stands (A1 item 1)
                    entry["code"] = "response-unsent"
        except Exception:  # noqa: BLE001 -- section 1.8: outside step 7, close and log; the broker keeps serving
            entry["code"] = "internal-error"
        finally:
            channel.close()
            entry["ms"] = int((time.monotonic() - start) * 1000)
            self._log(entry)

    # --- section 1.8 ----------------------------------------------------------------------------------------------

    def _answer(self, channel: Any, entry: dict, start: float) -> tuple[int, dict, str | None] | None:
        """(exit, result, the peer account for the response) for one connection, or None when no answer is sent."""
        try:  # step 1
            body = wire.read_frame(channel, start + READ_BOUND_S, wire.MAX_REQUEST_BYTES)
        except wire.FrameTooLarge as exc:
            return EXIT_ERR, wire.rejected("wire", f"{exc}; the body was not read"), None
        except TimeoutError:
            return EXIT_ERR, wire.rejected("wire", f"request incomplete within {READ_BOUND_S:g} s"), None
        except (EOFError, OSError):  # the client closed or broke partway through the frame: nothing to answer
            return None
        try:  # step 2
            ident = peer.identify(channel)
        except OSError as exc:
            return EXIT_ERR, wire.rejected("caller-unattributed", f"the peer's account cannot be read: {exc}",
                                           account=None), None
        entry["peer"], entry["pid"] = ident.account, ident.pid
        if ident.account != self.own_account and ident.account not in self.client_accounts:  # step 3
            return EXIT_ERR, wire.rejected("caller-unattributed", "this account is not admitted by the broker",
                                           account=ident.account), ident.account
        try:  # step 4
            request = wire.check_request(wire.decode(body))
        except wire.WireError as exc:
            return EXIT_ERR, wire.rejected("wire", str(exc)), ident.account
        entry["op"] = request["op"]
        if request["op"] == "stop":  # step 5
            if ident.account != self.own_account:
                return EXIT_ERR, wire.rejected("caller-mismatch", "only the broker's own account may stop it",
                                               account=ident.account), ident.account
            self._stopping = True
            return 0, {"ok": True, "outcome": "STOPPING"}, ident.account
        entry["cmd"], entry["job"] = wire.describe(request["argv"])
        entry["msg_id"] = _msg_id(request["argv"])
        if request["wfcore"] != self.wfcore:  # step 6
            return EXIT_ERR, wire.rejected("version-mismatch", "the client's wfcore differs from the broker's",
                                           client_wfcore=request["wfcore"], broker_wfcore=self.wfcore), ident.account
        caller = peer.Caller(peer=ident, broker_account=self.own_account, broker_wfcore=self.wfcore)
        try:  # step 7
            exit_code, result = self.run(request["argv"], request["inputs"], db=self.db, caller=caller)
            if isinstance(exit_code, bool) or exit_code not in wire.EXIT_CODES or not isinstance(result, dict):
                raise TypeError(f"run returned ({exit_code!r}, {type(result).__name__}), not (0-4, dict)")
        except (Exception, SystemExit) as exc:  # noqa: BLE001 -- answered as main() answers a crash (wf.py:345-346)
            return EXIT_INTERNAL, {"ok": False, "code": "internal-error",
                                   "error": f"{type(exc).__name__}: {exc}"}, ident.account
        return exit_code, result, ident.account

    # --- the log --------------------------------------------------------------------------------------------------

    def _log(self, entry: dict) -> None:
        """Append one line; rotate first when the file has reached LOG_MAX_BYTES. A log failure is reported on stderr
        and never stops the broker."""
        line = (json.dumps(entry, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
        try:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            try:
                size = os.path.getsize(self.log_path)
            except FileNotFoundError:
                size = 0
            if size >= LOG_MAX_BYTES:
                os.replace(self.log_path, previous_log_path(self.log_path))
            with open(self.log_path, "ab") as fh:
                fh.write(line)
        except OSError as exc:
            print(f"broker-log: not written ({type(exc).__name__}: {exc}): {line.decode('ascii').rstrip()}",
                  file=sys.stderr, flush=True)
