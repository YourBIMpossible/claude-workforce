"""Engine backup and restored-copy verification (B2 slice 5, lane S5B, Plexy): INTERFACE-CONTRACT-S5.md §1, §2, §5;
spec Rev 5.3 §3C, §4.1 (`wf backup`), §10 (no `wf restore`), T37.

backup(db_path, dest_path, *, writer_stopped=False)
    Copies the ledger with the SQLite engine backup interface (`sqlite3.Connection.backup`), never a file copy. The
    source is opened read-only and the copy runs inside one bounded read snapshot (`db.read_snapshot`), so the backup
    is one consistent state even while another process writes. The destination is created exclusively: it is refused
    when it is a UNC or device path that is not a local drive (`dest-path-unresolvable`; FIX2 #26+#32),
    when it exists (`dest-exists`), when its parent directory is missing (`dest-parent-missing`) or when it lies inside
    the ledger directory (`dest-inside-ledger`, D-S5-3), as given or as the --db file resolves (FIX2 #22+#31); the
    checks run in that order and every refusal raises `db.WfRefused(<reason>)` before anything is written. The
    destination is compared and opened as its realpath (FIX2 #23), so the checked file is the written one. After the copy the backup is reopened read-only and checked:
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
import re
import socket
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


#: FIX2 #26+#32 (Windows): `\\?\X:\...` / `\\.\X:\...` name drive X, and `\\<this host>\X$\...` (also behind
#: `\\?\UNC\` or `\\.\UNC\`) is drive X's administrative share. realpath keeps these forms, so before FIX2 they were
#: compared as another drive and never found inside the ledger directory.
_DRIVE_PREFIXED = re.compile(r"^[\\/]{2}[?.][\\/](?P<drive>[A-Za-z]:)(?P<rest>(?:[\\/].*)?)\Z", re.S)
_UNC = re.compile(r"^[\\/]{2}(?:[?.][\\/]UNC[\\/])?(?P<host>[^\\/]+)[\\/](?P<share>[^\\/]+)(?P<rest>(?:[\\/].*)?)\Z",
                  re.S | re.IGNORECASE)
_ADMIN_SHARE = re.compile(r"[A-Za-z]\$")
_LOOPBACK = re.compile(r"127(?:\.[0-9]{1,3}){3}|::1|\[::1\]|localhost", re.IGNORECASE)


def _is_this_host(host: str) -> bool:
    names = {socket.gethostname().lower(), os.environ.get("COMPUTERNAME", "").lower()} - {""}
    return bool(_LOOPBACK.fullmatch(host)) or host.lower() in names


def _local_form(path: str) -> str | None:
    r"""The path as a plain local path: on Windows a `\\?\X:` / `\\.\X:` path becomes `X:`, and a local drive's admin
    share becomes that drive. None for any other UNC or device path (another host, another share, a volume GUID):
    it cannot be proven outside the ledger directory. Other platforms have no such forms: the path is returned."""
    if os.name != "nt" or path[:1] not in ("\\", "/") or path[1:2] not in ("\\", "/"):
        return path
    m = _DRIVE_PREFIXED.match(path)
    if m:
        return m["drive"] + (m["rest"] or "\\")
    m = _UNC.match(path)
    if m and m["host"] not in ("?", ".") and _is_this_host(m["host"]) and _ADMIN_SHARE.fullmatch(m["share"]):
        return m["share"][0] + ":" + (m["rest"] or "\\")
    return None


def _norm(path: str) -> str:
    """Symlink-resolved, case-folded where the platform folds case: the containment comparison form. FIX2 #23:
    realpath on the path as given, with no lexical abspath first: POSIX resolves `lnk/..` through the link, as the
    kernel does; Windows collapses `..` lexically, as Win32 does."""
    return os.path.normcase(os.path.realpath(_local_form(path) or path))


def _inside(path: str, directory: str) -> bool:
    p, d = _norm(path), _norm(directory)
    try:
        return os.path.commonpath([p, d]) == d
    except ValueError:                                                   # different drives: not inside
        return False


def _open_ro(path: str) -> sqlite3.Connection:
    """Read-only engine connection (URI mode=ro): no schema, pragma or journal write is possible through it. A file
    the engine cannot open this way (FIX2 #50: a UNC --db gives `invalid uri authority`) is refused with its reason."""
    if not os.path.isfile(path):
        raise db.WfRefused(f"ledger not found: {path}", path=path)
    try:
        uri = pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro"
        return sqlite3.connect(uri, uri=True, timeout=0, isolation_level=None, check_same_thread=True)
    except (sqlite3.Error, ValueError) as exc:
        raise db.WfRefused(f"cannot open {path} read-only ({type(exc).__name__}: {exc})", path=path) from exc


def _jobs(conn: sqlite3.Connection) -> list[str]:
    """Every job with a contract row or any event (a job whose contract row is missing is still checked)."""
    found = set(db.jobs(conn)) | {r[0] for r in conn.execute("SELECT DISTINCT job FROM events").fetchall()}
    return sorted(found)


def _check(conn: sqlite3.Connection) -> dict[str, Any]:
    """Integrity, full chain from seq 1 and head per job, read inside one bounded snapshot. A file the engine cannot
    read (malformed image, not a database) is reported as `readable` false with the engine error, never raised past
    the caller's result; busy/lock timeouts raise db.WfLockTimeout (FIX2 #28: before, the raw engine error, exit 4)."""
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
            raise db.WfLockTimeout(f"check of the copy: database busy ({type(exc).__name__}: {exc})") from exc
        return {"readable": False, "error": f"{type(exc).__name__}: {exc}", "integrity": False,
                "integrity_problems": [str(exc)], "chains": {}, "heads": {}}
    return {"readable": True, "error": None, "integrity": integrity_problems == [],
            "integrity_problems": integrity_problems, "chains": chains, "heads": heads}


def _refuse_dest(db_path: str, dest_path: str) -> str:
    """Every destination refusal, before anything is written. Returns the file to write: the realpath of the
    destination in its local form (FIX2 #23, #26+#32), so the file checked here is the file written."""
    local = _local_form(dest_path)
    if local is None:
        raise db.WfRefused("dest-path-unresolvable", dest=dest_path,
                           why="a UNC or device path that is not a local drive cannot be proven outside the ledger")
    target = os.path.realpath(local)
    if os.path.lexists(dest_path) or os.path.lexists(target):
        raise db.WfRefused("dest-exists", dest=dest_path)
    parent = os.path.dirname(target)
    if not os.path.isdir(parent):
        raise db.WfRefused("dest-parent-missing", dest=dest_path, parent=parent)
    # FIX2 #22+#31: the ledger directory as given and as the --db file resolves (a symlinked --db file, W30-F1).
    for ledger_dir in (os.path.dirname(os.path.abspath(db_path)), os.path.dirname(os.path.realpath(db_path))):
        if _inside(target, ledger_dir):
            raise db.WfRefused("dest-inside-ledger", dest=dest_path, ledger_dir=ledger_dir)
    return target


def backup(db_path: str, dest_path: str, *, writer_stopped: bool = False) -> dict:
    """§1: engine backup from a read connection to a new file, then read-only verification of the copy. No engine
    error leaves as a raw sqlite3 error (FIX2 #23, #28): each is a refusal, a lock-timeout or a WfError."""
    target = _refuse_dest(db_path, dest_path)
    src = _open_ro(db_path)
    try:
        try:
            # 0o600: owner read/write only on POSIX (FIX1 P5, WF-021 F9: the default 0o777 & ~umask gave the copy
            # execute bits and world read). Windows honours only the read-only bit, which 0o600 leaves clear.
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError:                                          # created between the check and now
            raise db.WfRefused("dest-exists", dest=dest_path) from None
        except FileNotFoundError:                                        # parent removed between the check and now
            raise db.WfRefused("dest-parent-missing", dest=dest_path) from None
        os.close(fd)
        try:
            dst = sqlite3.connect(target, timeout=0, isolation_level=None)
        except sqlite3.Error as exc:
            raise db.WfError(f"engine backup failed ({type(exc).__name__}: {exc}); the empty destination is left in "
                             f"place and was not verified", dest=dest_path) from exc
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

    copy = _open_ro(target)
    try:
        checked = _check(copy)
    finally:
        copy.close()

    src = _open_ro(db_path)
    try:
        head_in_source: dict[str, bool] = {}
        head_equal: dict[str, bool] | None = {} if writer_stopped else None
        try:
            with db.read_snapshot(src):
                for job, h in checked["heads"].items():
                    if h["seq"] == 0:
                        head_in_source[job] = h["self"] == GENESIS
                    else:
                        row = db.row_at(src, job, h["seq"])
                        head_in_source[job] = row is not None and row["self"] == h["self"]
                    if head_equal is not None:
                        head_equal[job] = db.head(src, job) == (h["seq"], h["self"])
        except sqlite3.Error as exc:
            if db.is_busy(exc):
                raise db.WfLockTimeout(f"head lookups in the source: database busy ({type(exc).__name__}: "
                                       f"{exc})") from exc
            raise db.WfError(f"head lookups in the source failed ({type(exc).__name__}: {exc}); the backup was "
                             f"written and checked", dest=dest_path) from exc
    finally:
        src.close()

    return {
        "dest": os.path.abspath(dest_path),
        "bytes": os.path.getsize(target),
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
