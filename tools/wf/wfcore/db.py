"""SQLite ledger: schema (D1), bounded reservation (§4.2), head and chain checks (D4 core)."""
from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, TypeVar

_T = TypeVar("_T")

from .canon import GENESIS, canonical, event_self_hash, sha256_text

T_ACQ_BOUND = 5.0
T_HELD_BOUND = 2.0
T_RESOLVE_BOUND = 10.0
SLEEP_STEP = 0.010

SCHEMA = """
CREATE TABLE IF NOT EXISTS contracts (
  job TEXT PRIMARY KEY,
  sha256 TEXT NOT NULL,
  content TEXT NOT NULL,
  created_ts TEXT NOT NULL,
  abandoned_ts TEXT,
  abandon_reason TEXT
);
CREATE TABLE IF NOT EXISTS events (
  job TEXT NOT NULL,
  seq INTEGER NOT NULL,
  type TEXT NOT NULL,
  msg_id TEXT NOT NULL,
  author_role TEXT NOT NULL,
  session_id TEXT NOT NULL,
  generation INTEGER NOT NULL,
  reply_to TEXT,
  grant_ref TEXT,
  ts TEXT NOT NULL,
  content TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  prev TEXT NOT NULL,
  self TEXT NOT NULL,
  PRIMARY KEY (job, seq),
  UNIQUE (job, prev)
) WITHOUT ROWID;
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are immutable'); END;
"""

EVENT_COLS = ["job", "seq", "type", "msg_id", "author_role", "session_id", "generation",
              "reply_to", "grant_ref", "ts", "content", "content_sha256", "prev", "self"]


class WfError(Exception):
    exit_code = 1
    code = "error"

    def __init__(_exc, message: str, **info: Any):  # noqa: N805 — `self` is a legitimate info key (event hash)
        super(WfError, _exc).__init__(message)
        _exc.info = info


class WfLockTimeout(WfError):
    code = "lock-timeout"


class WfCommitTimeout(WfError):
    code = "commit-timeout"


class WfCommitUncertain(WfError):
    code = "commit-uncertain"


class WfRejected(WfError):
    """Validation rejection: rejected(<reason>)."""
    code = "rejected"

    def __init__(self, reason: str, message: str = "", **info: Any):
        super().__init__(f"rejected({reason})" + (f": {message}" if message else ""), **info)
        self.reason = reason


class WfRefused(WfError):
    """Command refused (state does not permit it)."""
    code = "refused"


class WfUsage(WfError):
    exit_code = 2
    code = "usage"


@dataclass
class OpLog:
    ops: list[dict[str, Any]] = field(default_factory=list)
    t0: float = field(default_factory=time.monotonic)

    def add(self, name: str, **kw: Any) -> None:
        self.ops.append({"op": name, "t_mono": round(time.monotonic() - self.t0, 6),
                         "wall": time.time(), **kw})


def connect(db: str, create: bool = True, bound: float = T_ACQ_BOUND) -> sqlite3.Connection:
    """One connection; manual transactions (autocommit mode); busy_timeout 0. The setup pragmas read the
    database header, so they are retried on BUSY within `bound` (raises WfLockTimeout), never left to fail
    instantly while another process holds the write lock."""
    exists = os.path.exists(db)
    if not exists and not create:
        raise WfRefused(f"ledger not found: {db}")
    os.makedirs(os.path.dirname(os.path.abspath(db)), exist_ok=True)
    conn = sqlite3.connect(db, timeout=0, isolation_level=None, autocommit=True)
    try:
        def setup() -> None:
            conn.execute("PRAGMA busy_timeout = 0")
            conn.execute("PRAGMA journal_mode = DELETE")
            conn.execute("PRAGMA synchronous = FULL")
            conn.execute("PRAGMA foreign_keys = ON")
            if create and not exists:
                conn.executescript(SCHEMA)
        retry_busy(setup, bound, "connection setup")
    except BaseException:
        conn.close()
        raise
    return conn


def retry_busy(fn: Callable[[], _T], bound: float, what: str) -> _T:
    """Run fn, retrying only SQLITE_BUSY/LOCKED for up to `bound` seconds. Raises WfLockTimeout."""
    start = time.monotonic()
    deadline = start + bound
    while True:
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if not is_busy(exc):
                raise
            now = time.monotonic()
            if now >= deadline:
                raise WfLockTimeout(f"{what}: database busy for {bound} s ({_errname(exc)})",
                                    bound=bound, elapsed=now - start, last_error=_errname(exc)) from exc
            time.sleep(min(SLEEP_STEP, max(0.0, deadline - now)))


@contextmanager
def read_snapshot(conn: sqlite3.Connection, bound: float = T_ACQ_BOUND) -> Iterator[sqlite3.Connection]:
    """Consistent read: BEGIN DEFERRED and take the SHARED lock with a bounded retry, hold it for the
    body, then ROLLBACK. While held, a concurrent writer cannot COMMIT (it retries within its own held
    bound), so read bodies must stay short."""
    def take() -> None:
        conn.execute("BEGIN DEFERRED")
        try:
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    retry_busy(take, bound, "read lock")
    try:
        yield conn
    finally:
        if conn.in_transaction:
            conn.execute("ROLLBACK")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _errname(exc: sqlite3.Error) -> str:
    return getattr(exc, "sqlite_errorname", None) or type(exc).__name__


def is_busy(exc: sqlite3.Error) -> bool:
    name = _errname(exc) or ""
    return name.startswith("SQLITE_BUSY") or name.startswith("SQLITE_LOCKED") or "locked" in str(exc).lower()


def begin_immediate(conn: sqlite3.Connection, oplog: OpLog, bound: float = T_ACQ_BOUND) -> float:
    """Bounded reservation. Returns monotonic time of success. Raises WfLockTimeout."""
    start = time.monotonic()
    deadline = start + bound
    last = ""
    attempts = 0
    while True:
        attempts += 1
        try:
            conn.execute("BEGIN IMMEDIATE")
            t = time.monotonic()
            oplog.add("begin_immediate", attempts=attempts, elapsed=round(t - start, 6))
            return t
        except sqlite3.OperationalError as exc:
            if not is_busy(exc):
                raise
            last = _errname(exc)
            now = time.monotonic()
            if now >= deadline:
                oplog.add("begin_immediate_giveup", attempts=attempts, elapsed=round(now - start, 6), last=last)
                raise WfLockTimeout(f"reservation not obtained within {bound} s (last {last})",
                                    bound=bound, elapsed=now - start, last_error=last)
            time.sleep(min(SLEEP_STEP, max(0.0, deadline - now)))


def rollback(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        conn.execute("ROLLBACK")


def head(conn: sqlite3.Connection, job: str) -> tuple[int, str]:
    row = conn.execute("SELECT seq, self FROM events WHERE job=? ORDER BY seq DESC LIMIT 1", (job,)).fetchone()
    return (row[0], row[1]) if row else (0, GENESIS)


def row_at(conn: sqlite3.Connection, job: str, seq: int) -> dict[str, Any] | None:
    r = conn.execute(f"SELECT {','.join(EVENT_COLS)} FROM events WHERE job=? AND seq=?", (job, seq)).fetchone()
    return dict(zip(EVENT_COLS, r)) if r else None


def rows(conn: sqlite3.Connection, job: str, from_seq: int = 1) -> list[dict[str, Any]]:
    cur = conn.execute(f"SELECT {','.join(EVENT_COLS)} FROM events WHERE job=? AND seq>=? ORDER BY seq", (job, from_seq))
    return [dict(zip(EVENT_COLS, r)) for r in cur.fetchall()]


def contract(conn: sqlite3.Connection, job: str) -> dict[str, Any] | None:
    r = conn.execute("SELECT job, sha256, content, created_ts, abandoned_ts, abandon_reason FROM contracts WHERE job=?", (job,)).fetchone()
    if not r:
        return None
    return {"job": r[0], "sha256": r[1], "content": r[2], "created_ts": r[3], "abandoned_ts": r[4], "abandon_reason": r[5]}


def jobs(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT job FROM contracts ORDER BY job").fetchall()]


def verify_chain(conn: sqlite3.Connection, job: str, from_seq: int = 1, prev_self: str | None = None) -> list[str]:
    """Level-1 check from from_seq to head. Returns a list of problems (empty = valid).
    prev_self: the self hash of seq from_seq-1 (GENESIS when from_seq == 1); None → read it."""
    problems: list[str] = []
    if prev_self is None:
        if from_seq == 1:
            prev_self = GENESIS
        else:
            r = row_at(conn, job, from_seq - 1)
            if r is None:
                return [f"no row at seq {from_seq - 1} to anchor verification"]
            prev_self = r["self"]
    expected_seq = from_seq
    pin = contract(conn, job)
    for r in rows(conn, job, from_seq):
        if r["seq"] != expected_seq:
            problems.append(f"sequence gap: expected {expected_seq}, found {r['seq']}")
            expected_seq = r["seq"]
        if r["prev"] != prev_self:
            problems.append(f"seq {r['seq']}: prev {r['prev'][:12]} != previous self {prev_self[:12]}")
        if sha256_text(r["content"]) != r["content_sha256"]:
            problems.append(f"seq {r['seq']}: content_sha256 mismatch")
        if event_self_hash(r) != r["self"]:
            problems.append(f"seq {r['seq']}: self hash mismatch")
        try:
            import json
            content = json.loads(r["content"])
            if canonical(content) != r["content"]:
                problems.append(f"seq {r['seq']}: content is not canonical JSON")
            if pin and content.get("contract_sha256") != pin["sha256"]:
                problems.append(f"seq {r['seq']}: contract pin {str(content.get('contract_sha256'))[:12]} != {pin['sha256'][:12]}")
        except ValueError:
            problems.append(f"seq {r['seq']}: content is not JSON")
        prev_self = r["self"]
        expected_seq += 1
    return problems


def constraints_present(conn: sqlite3.Connection) -> list[str]:
    problems = []
    trig = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    for t in ("events_no_update", "events_no_delete"):
        if t not in trig:
            problems.append(f"trigger missing: {t}")
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='events'").fetchone()
    if not sql or "PRIMARY KEY (job, seq)" not in sql[0] or "UNIQUE (job, prev)" not in sql[0]:
        problems.append("events table constraints differ from schema")
    jm = conn.execute("PRAGMA journal_mode").fetchone()[0]
    if str(jm).lower() == "wal":
        problems.append("journal_mode is WAL (rollback journal required)")
    return problems


def integrity(conn: sqlite3.Connection, quick: bool = False) -> list[str]:
    cmd = "PRAGMA quick_check" if quick else "PRAGMA integrity_check"
    res = [r[0] for r in conn.execute(cmd).fetchall()]
    return [] if res == ["ok"] else res
