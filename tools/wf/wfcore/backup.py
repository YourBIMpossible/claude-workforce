"""Engine backup and restored-copy verification (B2 slice 5, lane S5B, Plexy): INTERFACE-CONTRACT-S5.md §1, §2, §5;
spec Rev 5.3 §3C, §4.1 (`wf backup`), §10 (no `wf restore`), T37.

backup(db_path, dest_path, *, writer_stopped=False)
    Copies the ledger with the SQLite engine backup interface (`sqlite3.Connection.backup`), never a file copy. The
    source is opened read-only and the copy runs inside one bounded read snapshot (`db.read_snapshot`), so the backup
    is one consistent state even while another process writes. The destination is created exclusively: it is refused
    when it exists (`dest-exists`), when its parent directory is missing (`dest-parent-missing`) or when it lies inside
    the ledger directory (`dest-inside-ledger`, D-S5-3); the checks run in that order and every refusal raises
    `db.WfRefused(<reason>)` before anything is written. After the copy the backup is reopened read-only and checked:
    engine integrity, the full chain of every job from seq 1, each job's head, and whether the source still holds that
    (seq, self). Head equality with the source is asserted only when the caller states the writer is stopped;
    otherwise `head_equal` is None and LIMIT_LIVE_HEAD is reported. The backup is never modified after the copy and a
    failed verification (or a failed copy) never deletes it: the result reports, the owner decides.

verify_copy(path, *, checkpoint=None)
    Read-only check of a restored copy (restore itself stays an owner procedure). Integrity and full-chain checks for
    every job; with a retained checkpoint (job, seq, self) the status is `matches`, `differs` or `missing` (no row at
    seq: the copy is truncated below it). Without one, LIMIT_NO_CHECKPOINT states what cannot be detected. It never
    writes, repairs or deletes.

Imports: stdlib, `.db`, `.canon`, `.checkpointcmp` (parse only). Never `.commands`, `.txn` or `.gatecore`.
"""
from __future__ import annotations

import os
import pathlib
import sqlite3
from typing import Any

from . import checkpointcmp, db
from .canon import GENESIS

LIMIT_NO_CHECKPOINT = ("internally consistent only; a consistent whole-chain rewrite or tail deletion is not "
                       "detectable without an independently retained checkpoint")
LIMIT_LIVE_HEAD = ("head is present in source history; equality with the live head is not asserted while a writer may "
                   "be active")
#: Stated whenever a checkpoint is compared (T36 (e)): the comparison trusts the retained value as supplied.
LIMIT_CHECKPOINT_TRUSTED = ("the retained checkpoint is trusted as supplied; an altered checkpoint that agrees with an "
                            "altered chain is not detectable by this comparison")

CHECKPOINT_STATUSES = ("matches", "differs", "missing")


def _norm(path: str) -> str:
    """Absolute, symlink-resolved, case-folded where the platform folds case: the containment comparison form."""
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _inside(path: str, directory: str) -> bool:
    p, d = _norm(path), _norm(directory)
    try:
        return os.path.commonpath([p, d]) == d
    except ValueError:                                                   # different drives: not inside
        return False


def _open_ro(path: str) -> sqlite3.Connection:
    """Read-only engine connection (URI mode=ro): no schema, pragma or journal write is possible through it."""
    if not os.path.isfile(path):
        raise db.WfRefused(f"ledger not found: {path}", path=path)
    uri = pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=0, isolation_level=None, check_same_thread=True)


def _jobs(conn: sqlite3.Connection) -> list[str]:
    """Every job with a contract row or any event (a job whose contract row is missing is still checked)."""
    found = set(db.jobs(conn)) | {r[0] for r in conn.execute("SELECT DISTINCT job FROM events").fetchall()}
    return sorted(found)


def _check(conn: sqlite3.Connection) -> dict[str, Any]:
    """Integrity, full chain from seq 1 and head per job, read inside one bounded snapshot. A file the engine cannot
    read (malformed image, not a database) is reported as `readable` false with the engine error, never raised past
    the caller's result; busy/lock timeouts still raise (db.WfLockTimeout)."""
    try:
        with db.read_snapshot(conn):
            integrity_problems = db.integrity(conn)
            jobs = _jobs(conn)
            chains = {j: db.verify_chain(conn, j, 1) for j in jobs}
            heads = {}
            for j in jobs:
                seq, self_hash = db.head(conn, j)
                heads[j] = {"seq": seq, "self": self_hash}
    except sqlite3.DatabaseError as exc:
        if db.is_busy(exc):
            raise
        return {"readable": False, "error": f"{type(exc).__name__}: {exc}", "integrity": False,
                "integrity_problems": [str(exc)], "chains": {}, "heads": {}}
    return {"readable": True, "error": None, "integrity": integrity_problems == [],
            "integrity_problems": integrity_problems, "chains": chains, "heads": heads}


def _refuse_dest(db_path: str, dest_path: str) -> None:
    if os.path.lexists(dest_path):
        raise db.WfRefused("dest-exists", dest=dest_path)
    parent = os.path.dirname(os.path.abspath(dest_path))
    if not os.path.isdir(parent):
        raise db.WfRefused("dest-parent-missing", dest=dest_path, parent=parent)
    if _inside(dest_path, os.path.dirname(os.path.abspath(db_path))):
        raise db.WfRefused("dest-inside-ledger", dest=dest_path, ledger_dir=os.path.dirname(os.path.abspath(db_path)))


def backup(db_path: str, dest_path: str, *, writer_stopped: bool = False) -> dict:
    """§1: engine backup from a read connection to a new file, then read-only verification of the copy."""
    _refuse_dest(db_path, dest_path)
    src = _open_ro(db_path)
    try:
        try:
            fd = os.open(dest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0))
        except FileExistsError:                                          # created between the check and now
            raise db.WfRefused("dest-exists", dest=dest_path) from None
        except FileNotFoundError:                                        # parent removed between the check and now
            raise db.WfRefused("dest-parent-missing", dest=dest_path) from None
        os.close(fd)
        dst = sqlite3.connect(dest_path, timeout=0, isolation_level=None)
        try:
            with db.read_snapshot(src):
                src.backup(dst)
        except sqlite3.Error as exc:
            raise db.WfError(f"engine backup failed ({type(exc).__name__}: {exc}); the partial destination is left "
                             f"in place and was not verified", dest=dest_path) from exc
        finally:
            dst.close()
    finally:
        src.close()

    copy = _open_ro(dest_path)
    try:
        checked = _check(copy)
    finally:
        copy.close()

    src = _open_ro(db_path)
    try:
        head_in_source: dict[str, bool] = {}
        head_equal: dict[str, bool] | None = {} if writer_stopped else None
        with db.read_snapshot(src):
            for job, h in checked["heads"].items():
                if h["seq"] == 0:
                    head_in_source[job] = h["self"] == GENESIS
                else:
                    row = db.row_at(src, job, h["seq"])
                    head_in_source[job] = row is not None and row["self"] == h["self"]
                if head_equal is not None:
                    head_equal[job] = db.head(src, job) == (h["seq"], h["self"])
    finally:
        src.close()

    return {
        "dest": os.path.abspath(dest_path),
        "bytes": os.path.getsize(dest_path),
        "integrity": checked["integrity"],
        "chains": checked["chains"],
        "heads": checked["heads"],
        "head_in_source": head_in_source,
        "head_equal": head_equal,
        "writer_active": False if writer_stopped else "unknown",
        "limitations": [] if writer_stopped else [LIMIT_LIVE_HEAD],
    }


def verify_copy(path: str, *, checkpoint: tuple[str, int, str] | None = None) -> dict:
    """§2: read-only integrity and full-chain check of a restored copy, with an optional retained checkpoint."""
    if checkpoint is not None:
        if not (isinstance(checkpoint, tuple) and len(checkpoint) == 3):
            raise db.WfUsage("checkpoint must be a (job, seq, self) tuple")
        job, seq, self_hash = checkpoint
        if not isinstance(seq, int) or isinstance(seq, bool):
            raise db.WfUsage("checkpoint seq must be an int")
        seq, self_hash = checkpointcmp.parse(f"{seq}:{self_hash}", job)  # same syntax rule as `wf verify --checkpoint`
    conn = _open_ro(path)
    try:
        checked = _check(conn)
        cp = None
        if checkpoint is not None:
            row, head_seq = None, None
            if checked["readable"]:
                with db.read_snapshot(conn):
                    row = db.row_at(conn, job, seq)
                    head_seq = db.head(conn, job)[0]
            # An unreadable copy cannot show the checkpointed row: reported as missing, never as a match.
            status = "missing" if row is None else ("matches" if row["self"] == self_hash else "differs")
            cp = {"job": job, "seq": seq, "supplied": self_hash, "found": None if row is None else row["self"],
                  "head_seq": head_seq, "status": status}
    finally:
        conn.close()
    clean = (checked["integrity"] and not any(checked["chains"].values())
             and (cp is None or cp["status"] == "matches"))
    return {
        "path": os.path.abspath(path),
        "readable": checked["readable"],
        "error": checked["error"],
        "integrity": checked["integrity"],
        "integrity_problems": checked["integrity_problems"],
        "chains": checked["chains"],
        "heads": checked["heads"],
        "checkpoint": cp,
        "clean": clean,
        "limitations": [LIMIT_NO_CHECKPOINT] if cp is None else [LIMIT_CHECKPOINT_TRUSTED],
    }
