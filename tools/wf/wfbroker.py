"""wfbroker — the account-checking broker for `wf --broker` (PACKAGE-FIX3-II, lane F3-BRK; INTERFACE-CONTRACT-FIX3-II r2
section 3.5; SII:246-264).

Serve:  python -I tools/wf/wfbroker.py --db <ledger> --address <addr> [--client-account <id>]...
Stop:   python -I tools/wf/wfbroker.py --stop --address <addr>    (sends op "stop"; only the broker's own account may)

The broker runs as the owner account and is the only writer of the ledger. Before serving it checks, in order, and
refuses to start with `{"ok": false, "code": "broker-config", "check": <name>, "error": ...}` on stdout, exit 1:
env (WF_ROLE, WF_SESSION, WF_DB or WF_BROKER present), faults (WF_TEST_FAULTS on a ledger not under fixtures/tmp),
ledger (it does not open and read), root (no boundary root for the log), own-account, client-account, address, and
address-in-use. Then it prints one ready line, `{"ok": true, "code": "broker-ready", ...}`, serves one request at a
time, and after a stop prints `{"ok": true, "code": "broker-stopped", "served": <n>}`, exit 0.

The listener comes only from `peer.listen`; no environment variable, option or file selects a test fake (section 2.5).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys

WF_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WF_DIR)  # -I omits the script dir

import wf  # noqa: E402
from wfcore import broker, changeset, db as dbm, paths, peer, wire  # noqa: E402

EXIT_OK, EXIT_ERR, EXIT_USAGE, EXIT_INTERNAL = 0, 1, 2, 4
ENV_REFUSED = ("WF_ROLE", "WF_SESSION", "WF_DB", "WF_BROKER")  # section 3.5 `env`: any value, even an empty one
LOG_REL = ("runtime", "broker", "broker-log.jsonl")  # under the boundary root (section 3.5)


class ConfigError(Exception):
    def __init__(self, check: str, error: str) -> None:
        super().__init__(error)
        self.check = check


def address_for_root(root: str, platform: str) -> str:
    """Section 1.10: the run sheets' address for a boundary root. win32: `\\\\.\\pipe\\wf-broker-` and the first 16 hex
    of the sha256 of `paths.norm(root)`; linux: `<abspath(root)>/runtime/broker/wf.sock`."""
    if platform == "win32":
        return "\\\\.\\pipe\\wf-broker-" + hashlib.sha256(paths.norm(root).encode("utf-8")).hexdigest()[:16]
    if platform == "linux":
        return os.path.join(os.path.abspath(root), "runtime", "broker", "wf.sock")
    raise ValueError(f"unsupported platform {platform!r}")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wfbroker", description="Serve wf --broker requests for one ledger, or stop it.",
                                allow_abbrev=False)
    p.add_argument("--db", help="the ledger to serve (required to serve)")
    p.add_argument("--address", required=True, help="the endpoint: a named pipe (Windows) or a socket path (Linux)")
    p.add_argument("--client-account", dest="client_accounts", action="append", default=[], metavar="ID",
                   help="a worker account allowed to connect (repeatable); the broker's own account always is")
    p.add_argument("--stop", action="store_true", help="ask the broker at --address to stop (own account only)")
    return p


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, sort_keys=False) + "\n")
    sys.stdout.flush()


def _check_ledger(db: str) -> None:
    try:
        conn = dbm.connect(db, create=False)
        try:
            with dbm.read_snapshot(conn):
                dbm.jobs(conn)
        finally:
            conn.close()
    except (dbm.WfError, sqlite3.Error, OSError) as exc:
        raise ConfigError("ledger", f"the ledger does not open and read: {type(exc).__name__}: {exc}") from exc


def start(args: argparse.Namespace) -> broker.Broker:
    """Section 3.5's start checks, in order; then the listener, from `peer.listen` only. Raises ConfigError."""
    present = [name for name in ENV_REFUSED if name in os.environ]
    if present:
        raise ConfigError("env", f"{', '.join(present)} must not be set for the broker (its run takes no default)")
    db = paths.db_path(args.db)
    if os.environ.get("WF_TEST_FAULTS", "").strip() and not paths.faults_allowed(db):
        raise ConfigError("faults", f"WF_TEST_FAULTS is set but ledger {db} is not under {paths.FIXTURES_TMP}")
    _check_ledger(db)
    root = changeset.boundary_root(db)
    if root is None:
        raise ConfigError("root", f"ledger {db} has no boundary root (its directory is not named ledger), so the "
                                  f"broker has no log location")
    try:
        own = peer.own_account()
        platform = peer.host_platform()
    except OSError as exc:
        raise ConfigError("own-account", f"this process's account cannot be read: {exc}") from exc
    clients = list(args.client_accounts)
    for cid in clients:
        if not peer.is_account_id(cid, platform):
            raise ConfigError("client-account", f"{cid!r} is not a {platform} account id")
        if cid == own:
            raise ConfigError("client-account", f"{cid} is the broker's own account, which is always admitted")
    repeated = sorted({cid for cid in clients if clients.count(cid) > 1})
    if repeated:
        raise ConfigError("client-account", f"given more than once: {', '.join(repeated)}")
    if not wire.is_address(args.address, platform):
        raise ConfigError("address", f"{args.address!r} is not a {wire.ADDRESS_FORM[platform]}")
    try:
        listener = peer.listen(args.address, clients)
    except FileExistsError as exc:
        raise ConfigError("address-in-use", f"another broker serves {args.address}: {exc}") from exc
    except (ValueError, OSError) as exc:
        raise ConfigError("address", f"cannot listen at {args.address}: {type(exc).__name__}: {exc}") from exc
    return broker.Broker(db=db, listener=listener, own_account=own, wfcore=wire.wfcore_sha256(WF_DIR),
                         client_accounts=clients, run=wf.run_brokered, log_path=os.path.join(root, *LOG_REL))


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.stop:
        if args.db is not None or args.client_accounts:
            parser.error("--stop takes only --address")
        try:
            platform = peer.host_platform()
        except OSError as exc:
            parser.error(str(exc))
        if not wire.is_address(args.address, platform):
            parser.error(f"--address {args.address!r} is not a {wire.ADDRESS_FORM[platform]}")
        code, res = wf.client.call(args.address, {"v": wire.PROTOCOL_VERSION, "op": "stop", "argv": [], "inputs": {},
                                                  "wfcore": wire.wfcore_sha256(WF_DIR)})
        _emit(res)
        return code
    if args.db is None:
        parser.error("--db is required to serve")
    try:
        b = start(args)
    except ConfigError as exc:
        _emit({"ok": False, "code": "broker-config", "check": exc.check, "error": str(exc)})
        return EXIT_ERR
    _emit({"ok": True, "code": "broker-ready", "address": args.address, "pid": os.getpid(), "account": b.own_account,
           "wfcore": b.wfcore, "db": b.db})
    try:
        served = b.serve()
    except Exception as exc:  # noqa: BLE001 -- the listener failed outside any one connection
        _emit({"ok": False, "code": "internal-error", "error": f"{type(exc).__name__}: {exc}"})
        return EXIT_INTERNAL
    _emit({"ok": True, "code": "broker-stopped", "served": served})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
