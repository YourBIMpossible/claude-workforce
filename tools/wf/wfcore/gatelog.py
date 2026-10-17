"""The gate log (B2 slice 4a, lane S4L; INTERFACE-CONTRACT-S4A sections 3 and 6, spec 3B gate-log tail, 4.2, 4.3).

`runtime/gate/gate.db` under the boundary root is a separate SQLite file with one hash-chained table, `attempts`.
The gate hook is its only writer (`record`); `wf` only reads it (`verify`, `attempts`). Each row's `self` is the
SHA-256 of the canonical JSON of the row without `self`; `prev` is the previous row's `self` (genesis: 64 zeros).
Update and delete are refused by triggers, as in the ledger.

Audit failures (section 3): when `record` fails, the hook calls `record_failure`, which appends one JSON line to
`runtime/gate/gate-audit-failures.log` (best effort). `verify` reports that file's line count as `unlogged_attempts`,
so nothing can claim every attempt was logged after a failure.

Amendment S4A-A1: a row also carries `probe_fault` (null unless a configured probe was active), so the log records
an active probe. It is part of the hashed row.

Imports (section 6): stdlib and `.canon` only. Must not import `.commands`, `.txn`, `.gatecore`, `.db`.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sqlite3
from typing import Any

from .canon import GENESIS, canonical, sha256_text

GATE_DB_REL = "runtime/gate/gate.db"
FAILURES_REL = "runtime/gate/gate-audit-failures.log"

#: Busy wait for the gate.db write lock: concurrent hook processes queue on it instead of failing at once.
WRITE_TIMEOUT_S = 3.0
READ_TIMEOUT_S = 2.0

COLUMNS: tuple[str, ...] = ("seq", "ts", "session_id", "tool_name", "channel", "decision", "reason", "jobs_json",
                            "paths_json", "payload_sha256", "command_sha256", "command_length", "probe_fault",
                            "prev", "self")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
  seq INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  session_id TEXT,
  tool_name TEXT,
  channel TEXT,
  decision TEXT NOT NULL CHECK (decision IN ('allow', 'deny')),
  reason TEXT NOT NULL,
  jobs_json TEXT NOT NULL,
  paths_json TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  command_sha256 TEXT,
  command_length INTEGER,
  probe_fault TEXT,
  prev TEXT NOT NULL,
  self TEXT NOT NULL UNIQUE
);
CREATE TRIGGER IF NOT EXISTS attempts_no_update BEFORE UPDATE ON attempts
BEGIN SELECT RAISE(ABORT, 'gate attempts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS attempts_no_delete BEFORE DELETE ON attempts
BEGIN SELECT RAISE(ABORT, 'gate attempts are immutable'); END;
"""
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class GateLogError(Exception):
    """Any failure to append a gate-log row (the hook then applies the section 3 write-failure rule)."""


def _db_path(root: str) -> str:
    return os.path.join(root, *GATE_DB_REL.split("/"))


def _failures_path(root: str) -> str:
    return os.path.join(root, *FAILURES_REL.split("/"))


def row_self(row: dict[str, Any]) -> str:
    """`self` of a gate-log row: SHA-256 of the canonical JSON of every column except `self`."""
    return sha256_text(canonical({k: row.get(k) for k in COLUMNS if k != "self"}))


def _str_list(value: Any, what: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        raise GateLogError(f"decision {what} must be a list of strings")
    return list(value)


def _opt_str(value: Any, what: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise GateLogError(f"decision {what} must be a string or null")
    return value


def _row_values(decision: dict, payload_sha256: str, ts: str) -> dict[str, Any]:
    if not isinstance(decision, dict):
        raise GateLogError("decision must be a dict")
    if decision.get("decision") not in ("allow", "deny"):
        raise GateLogError("decision.decision must be 'allow' or 'deny'")
    if not isinstance(decision.get("reason"), str) or not decision["reason"]:
        raise GateLogError("decision.reason must be a non-empty string")
    if not isinstance(payload_sha256, str) or not _HEX64.match(payload_sha256):
        raise GateLogError("payload_sha256 must be 64 lowercase hex characters")
    if not isinstance(ts, str) or not ts:
        raise GateLogError("ts must be a non-empty string")
    length = decision.get("command_length")
    if length is not None and (isinstance(length, bool) or not isinstance(length, int) or length < 0):
        raise GateLogError("decision.command_length must be a non-negative integer or null")
    return {
        "ts": ts,
        "session_id": _opt_str(decision.get("session"), "session"),
        "tool_name": _opt_str(decision.get("tool_name"), "tool_name"),
        "channel": _opt_str(decision.get("channel"), "channel"),
        "decision": decision["decision"],
        "reason": decision["reason"],
        "jobs_json": canonical(_str_list(decision.get("jobs"), "jobs")),
        "paths_json": canonical(_str_list(decision.get("paths"), "paths")),
        "payload_sha256": payload_sha256,
        "command_sha256": _opt_str(decision.get("command_sha256"), "command_sha256"),
        "command_length": length,
        "probe_fault": _opt_str(decision.get("probe_fault"), "probe_fault"),
    }


def record(root: str, decision: dict, *, payload_sha256: str, ts: str) -> int:
    """Append one chained row for `decision` (the gatecore.decide shape) and return its seq. Creates
    `runtime/gate/gate.db` on first use. Raises GateLogError on any failure, with nothing appended."""
    values = _row_values(decision, payload_sha256, ts)
    path = _db_path(root)
    conn = None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path, timeout=WRITE_TIMEOUT_S, isolation_level=None)
        conn.executescript(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        last = conn.execute("SELECT seq, self FROM attempts ORDER BY seq DESC LIMIT 1").fetchone()
        row = dict(values, seq=(last[0] + 1) if last else 1, prev=last[1] if last else GENESIS)
        row["self"] = row_self(row)
        conn.execute(f"INSERT INTO attempts ({','.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))})",
                     [row[c] for c in COLUMNS])
        conn.execute("COMMIT")
        return int(row["seq"])
    except GateLogError:
        raise
    except Exception as exc:  # sqlite3.Error, OSError, ...: the caller applies the write-failure rule
        if conn is not None and conn.in_transaction:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass  # the connection is closed below; SQLite discards the open transaction
        raise GateLogError(f"gate log write failed: {type(exc).__name__}: {exc}") from exc
    finally:
        if conn is not None:
            conn.close()


def record_failure(root: str, decision: dict, error: BaseException, *, ts: str) -> bool:
    """Best-effort append of one JSON line to `runtime/gate/gate-audit-failures.log`; True if written."""
    d = decision if isinstance(decision, dict) else {}
    line = {"ts": ts, "session_id": d.get("session"), "tool_name": d.get("tool_name"),
            "decision": d.get("decision"), "reason": d.get("reason"),
            "error_class": type(error).__name__, "error": str(error)}
    try:
        text = json.dumps(line, sort_keys=True, ensure_ascii=False, default=str) + "\n"
        path = _failures_path(root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return True
    except Exception:  # noqa: BLE001 - best effort by contract; the hook reports False on stderr
        return False


def _connect_ro(path: str) -> sqlite3.Connection:
    uri = pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=READ_TIMEOUT_S, isolation_level=None)


def _read_rows(path: str) -> list[dict[str, Any]]:
    conn = _connect_ro(path)
    try:
        conn.execute("BEGIN")
        cur = conn.execute(f"SELECT {','.join(COLUMNS)} FROM attempts ORDER BY seq")
        rows = [dict(zip(COLUMNS, r)) for r in cur.fetchall()]
        conn.execute("ROLLBACK")
        return rows
    finally:
        conn.close()


def _failure_lines(root: str) -> int:
    path = _failures_path(root)
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        return sum(1 for ln in fh if ln.strip())


def verify(root: str) -> dict:
    """Read-only check of the gate log: {"present", "rows", "chain_problems": [...], "unlogged_attempts": int}.
    `chain_problems` names every seq gap, `prev` break and `self` mismatch; an unreadable file is one problem.
    `unlogged_attempts` is the failures file's line count (0 when absent), reported whether or not gate.db exists.
    Like the ledger chain, removing rows from the end of the log cannot be detected from the log alone."""
    path = _db_path(root)
    out: dict[str, Any] = {"present": os.path.lexists(path), "rows": 0, "chain_problems": [], "unlogged_attempts": 0}
    try:
        out["unlogged_attempts"] = _failure_lines(root)
    except OSError as exc:
        out["chain_problems"].append(f"gate audit-failures file unreadable: {type(exc).__name__}: {exc}")
    if not out["present"]:
        return out
    try:
        rows = _read_rows(path)
    except (sqlite3.Error, OSError) as exc:
        out["chain_problems"].append(f"gate log unreadable: {type(exc).__name__}: {exc}")
        return out
    out["rows"] = len(rows)
    prev = GENESIS
    for expected, row in enumerate(rows, start=1):
        seq = row["seq"]
        if seq != expected:
            out["chain_problems"].append(f"seq {seq}: expected seq {expected} (gap or reorder)")
        if row["prev"] != prev:
            out["chain_problems"].append(f"seq {seq}: prev does not match the previous row's self")
        if row["self"] != row_self(row):
            out["chain_problems"].append(f"seq {seq}: self does not match the row content")
        prev = row["self"]
    return out


def _decode_list(text: Any) -> list[str] | None:
    try:
        value = json.loads(text) if isinstance(text, str) else None
    except ValueError:
        return None
    return value if isinstance(value, list) else None


def attempts(root: str, job: str | None = None, since_seq: int = 0) -> list[dict]:
    """Read-only rows with seq > `since_seq`, oldest first; `job` keeps rows whose jobs list names it. Each row has
    every column plus `jobs` and `paths` (decoded `jobs_json` / `paths_json`; None when undecodable). An absent
    gate.db gives []."""
    path = _db_path(root)
    if not os.path.lexists(path):
        return []
    out = []
    for row in _read_rows(path):
        if row["seq"] <= since_seq:
            continue
        row["jobs"] = _decode_list(row["jobs_json"])
        row["paths"] = _decode_list(row["paths_json"])
        if job is not None and job not in (row["jobs"] or []):
            continue
        out.append(row)
    return out
