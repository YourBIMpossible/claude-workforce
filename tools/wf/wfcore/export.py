"""Per-job export refresh (FB1.4 D7): events.jsonl + head.json + manifest.txt under jobs\\<job>\\export."""
from __future__ import annotations

import ctypes
import errno
import functools
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from typing import Any, Callable, TypeVar

from . import db as dbm
from . import manifest, paths
from .canon import canonical, sha256_file

_T = TypeVar("_T")

FILES = ("events.jsonl", "head.json", "contract.json")

#: FIX2 Z3 (AMENDMENT FIX2-A17): `_retry_sharing` makes at most SHARING_ATTEMPTS attempts in all, SHARING_SLEEP_S
#: seconds apart, so it adds at most (SHARING_ATTEMPTS - 1) * SHARING_SLEEP_S = 0.4 s to one call.
SHARING_ATTEMPTS = 5
SHARING_SLEEP_S = 0.1
#: The `winerror` values every retried call retries: 32 ERROR_SHARING_VIOLATION and 33 ERROR_LOCK_VIOLATION.
SHARING_WINERRORS: frozenset[int] = frozenset({32, 33})
#: `_write_atomic`'s `os.replace` also retries 5 ERROR_ACCESS_DENIED: Windows reports a replace onto a file that
#: another process holds open without delete sharing that way (Plexy WF-114, measured). Reads never retry 5.
REPLACE_WINERRORS: frozenset[int] = SHARING_WINERRORS | {5}
#: FIX2 Y2 (AMENDMENT FIX2-A22): one `refresh` may sleep at most this many times on sharing retries, across all its
#: files (0.4 s with SHARING_SLEEP_S), so it extends its write reservation by at most 0.4 s, not 0.4 s per file.
REFRESH_SLEEPS = SHARING_ATTEMPTS - 1
_WINDOWS = sys.platform == "win32"


PRESERVED = "export temp file preserved: "
#: Files the product itself writes into the export dir outside the manifest (never strays): the retained
#: checkpoint written by `wf checkpoint` (commands.checkpoint, T36). Deviation from the A3 inventory as stated
#: (manifest.txt + entries + staging): without it `wf checkpoint` followed by `wf verify` reports its own file
#: and `wf close` refuses (test_wf T08/C4). Reported to White for the paired integration decision.
PRODUCT_FILES: frozenset[str] = frozenset({"checkpoint.json"})
#: Staging names: the legacy fixed `<file>.tmp` and the per-writer `<file>.<pid>-<hex8>.tmp`, for the export
#: files, manifest.txt (FIX2 Y3) and the product-written checkpoint, all staged through `_write_atomic`. FIX2 #61:
#: `\Z`, not `$`, which also matches before a trailing newline.
_STAGING = re.compile(r"^(?P<base>" + "|".join(re.escape(f) for f in (*FILES, "manifest.txt", *sorted(PRODUCT_FILES)))
                      + r")(?:\.(?P<pid>\d+)-[0-9a-f]{8})?\.tmp\Z")
#: Staging paths this process currently has open (active, never an orphan). Module-level so every refresh in
#: this process agrees; a path is removed as soon as its write completes or fails.
_ACTIVE: set[str] = set()
_ACTIVE_LOCK = threading.Lock()


class _RetryBudget:
    """FIX2 Y2: the sharing-retry sleeps one `refresh` may still spend, shared by all of its file writes."""

    __slots__ = ("sleeps",)

    def __init__(self, sleeps: int = REFRESH_SLEEPS) -> None:
        self.sleeps = sleeps


def _retry_sharing(fn: Callable[..., _T], *args: Any, winerrors: frozenset[int] = SHARING_WINERRORS,
                   probe: str | None = None, budget: _RetryBudget | None = None) -> _T:
    """FIX2 Z3 (AMENDMENT FIX2-A17), Y1 and Y2 (A22): `fn(*args)`, retried on a Windows sharing violation only.

    A virus scanner, an indexer, a backup agent or another `wf` reading the export can hold an export file open for a
    moment without the sharing a replace or a read needs, and Windows then fails the call at once instead of waiting.
    A `PermissionError` whose Windows error (`_violation`; a read passes `probe`, its path) is in `winerrors` is
    retried: SHARING_ATTEMPTS attempts in all with SHARING_SLEEP_S between them, so at most 0.4 s is added to one call,
    and a `budget` (one per refresh) caps the sleeps of all the calls that share it. Any other error, every error off
    Windows, and a violation once the budget is spent are raised as they come. After the last attempt its error is
    raised, so the outcome is exactly the one without the retry: `refresh` reports it in its note, `check` as `export
    file unreadable: ...`. The sleep runs where the caller is: inside `refresh` that is under the write lock it holds
    (FIX2 #45 (b))."""
    attempt = 1
    while True:
        try:
            return fn(*args)
        except PermissionError as exc:
            if (not _WINDOWS or _violation(exc, probe) not in winerrors or attempt >= SHARING_ATTEMPTS
                    or (budget is not None and budget.sleeps <= 0)):
                raise
        attempt += 1
        if budget is not None:
            budget.sleeps -= 1
        time.sleep(SHARING_SLEEP_S)


def _violation(exc: PermissionError, probe: str | None) -> int | None:
    """FIX2 Y1: the Windows error behind `exc`. Its `winerror` when set (`os.replace` sets it). CPython's `open()` and
    `read()` report a sharing or lock violation as errno 13 with `winerror` None (the C runtime maps both to EACCES;
    WF-127 F1), and so is an ACL denial; for such an error on a read, the code a read-only probe of the same path gets
    now (`_probe_sharing`) tells them apart."""
    code = getattr(exc, "winerror", None)
    if code is None and probe is not None and exc.errno == errno.EACCES:
        code = _probe_sharing(probe)
    return code


@functools.cache
def _kernel32() -> Any:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    k32.ReadFile.restype = ctypes.c_int
    k32.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
                             ctypes.c_void_p]
    k32.CloseHandle.restype = ctypes.c_int
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    return k32


def _probe_sharing(path: str) -> int:
    """FIX2 Y1 (Windows only): open `path` read-only with full sharing (CreateFileW, GENERIC_READ, OPEN_EXISTING) and
    read it to the end, then close it. Returns 0 if that works now, else the Win32 error: 32 (sharing violation) at the
    open, 33 (lock violation) at a read of a locked range, 5 (access denied), 2 (not found), and so on. It never writes
    and never creates, and it follows links as `open()` does (no FILE_FLAG_OPEN_REPARSE_POINT)."""
    k32 = _kernel32()
    h = k32.CreateFileW(path, 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
    if h is None or h == ctypes.c_void_p(-1).value:
        return ctypes.get_last_error()
    try:
        buf = ctypes.create_string_buffer(65536)
        got = ctypes.c_uint32(0)
        while True:
            if not k32.ReadFile(h, buf, len(buf), ctypes.byref(got), None):
                return ctypes.get_last_error()
            if got.value == 0:
                return 0
    finally:
        k32.CloseHandle(h)


def _write_atomic(path: str, text: str, budget: _RetryBudget | None = None) -> None:
    """Stage `<path>.<pid>-<random>.tmp` exclusively, fsync, then `os.replace` it over `path`.

    The staging name is unique per writer, so concurrent refreshes never share a temp file, and it is created
    with O_CREAT|O_EXCL (mode "x"), so an existing entry of any kind at that name (file, symlink) is never
    truncated, replaced or followed. A pre-existing `<path>.tmp` or another writer's staging file is left
    untouched: nothing here deletes, repairs or retries them. If THIS writer's publish fails (write, fsync or the
    os.replace race against a concurrent refresh), the writer discards its own in-flight staging file so a lost
    race leaves nothing behind; the failure itself is still raised and reported. Only a staging file whose writer
    died (or whose discard failed) is left behind, and that one is reported as an orphan by the next refresh/check.
    The `os.replace` retries a Windows sharing violation, `winerror` 32, 33 or 5 (`_retry_sharing`, FIX2 Z3), within
    the caller's `budget` when it passes one (`refresh`, FIX2 Y2).
    """
    tmp = f"{path}.{os.getpid()}-{secrets.token_hex(4)}.tmp"
    with _ACTIVE_LOCK:
        _ACTIVE.add(tmp)
    created = False
    try:
        with open(tmp, "x", encoding="utf-8", newline="\n") as fh:
            created = True
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        _retry_sharing(os.replace, tmp, path, winerrors=REPLACE_WINERRORS, budget=budget)
    except BaseException:
        if created:
            _discard_own_staging(tmp)
        raise
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE.discard(tmp)


def _discard_own_staging(tmp: str) -> None:
    """Remove the staging file THIS writer created and could not publish. Never applied to a pre-existing or
    foreign file. Best effort: if the unlink fails the file stays and is reported as an orphan next run."""
    try:
        os.unlink(tmp)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    """Best effort: is `pid` a running process? True on any doubt (never claims an orphan it cannot prove).

    A staging name may carry any digits, so a pid no process can have is not alive (FIX2 WF-146c): below 1 (on POSIX
    `kill(0, 0)` would probe the process group), past a Windows DWORD (ctypes would refuse it), or one `os.kill`
    cannot take (`OverflowError`, `ValueError`: past a POSIX pid_t)."""
    if pid == os.getpid():
        return True
    if pid < 1:
        return False
    if sys.platform == "win32":
        if pid > 0xFFFFFFFF:
            return False
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: exists, not ours -> treat as alive
        try:
            code = ctypes.c_ulong(0)
            k32.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(ctypes.c_void_p(h))
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (OverflowError, ValueError):
        return False
    except OSError:
        return True


def orphans(out: str) -> list[str]:
    """Names of abandoned staging files in the export dir, each as `export temp file preserved: <name>`.

    Abandoned = the legacy fixed `<file>.tmp`, a per-writer file whose pid is this process but which no refresh
    in this process has open, or a per-writer file whose pid is not running. A per-writer file whose pid is a
    running process is active staging and is not reported (limitation: pid reuse can make an abandoned file
    look active; the error is always on the side of not claiming an orphan). Never deletes or modifies anything.
    """
    try:
        names = os.listdir(out)
    except OSError:
        return []
    found: list[str] = []
    with _ACTIVE_LOCK:
        active = set(_ACTIVE)
    for name in sorted(names):
        m = _STAGING.match(name)
        if not m:
            continue
        full = os.path.join(out, name)
        pid = m.group("pid")
        if pid is None:
            found.append(PRESERVED + name)
        elif int(pid) == os.getpid():
            if full not in active:
                found.append(PRESERVED + name)
        elif not _pid_alive(int(pid)):
            found.append(PRESERVED + name)
    return found


def refresh(db_path: str, job: str) -> dict[str, Any]:
    """Read-only against the ledger; rewrites the export files and their manifest. Failures are
    reported, never raised past the caller's committed write.

    FIX2 #45, option (b): the refresh runs under the write lock. A connection of its own takes BEGIN IMMEDIATE (the
    reservation every writer takes, bounded by T_ACQ_BOUND) before its snapshot and holds it until manifest.txt is
    written, then rolls back; nothing is written to the ledger. Refreshes therefore serialize with each other and with
    commits: two refreshes never interleave (no torn export), and the last one to run reads the newest commit (no stale
    export left behind). Readers and the gate take SHARED and are not blocked. A caller must not hold the write lock
    itself (txn closes its write connection first); a refresh that cannot get the lock reports it like any failed
    refresh. Its four file writes (manifest.txt included, FIX2 Y3) share one sharing-retry budget (FIX2 Y2), so the
    retries extend the reservation by at most 0.4 s in all."""
    out = paths.export_dir(db_path, job)
    lock: sqlite3.Connection | None = None
    try:
        os.makedirs(out, exist_ok=True)
        lock = dbm.connect(db_path, create=False, bound=dbm.T_HELD_BOUND)
        dbm.begin_immediate(lock, dbm.OpLog())
        rows = dbm.rows(lock, job)  # the snapshot, read inside the reservation
        c = dbm.contract(lock, job)
        budget = _RetryBudget()  # FIX2 Y2: one retry budget for every file this refresh writes
        _write_atomic(os.path.join(out, "events.jsonl"), "".join(canonical(r) + "\n" for r in rows), budget)
        head = rows[-1] if rows else None
        _write_atomic(os.path.join(out, "head.json"), canonical(
            {"job": job, "seq": head["seq"] if head else 0, "self": head["self"] if head else None,
             "events": len(rows)}) + "\n", budget)
        _write_atomic(os.path.join(out, "contract.json"), canonical(
            {"job": job, "sha256": c["sha256"], "abandoned_ts": c["abandoned_ts"],
             "abandon_reason": c["abandon_reason"], "content": json.loads(c["content"])} if c else {"job": job}) + "\n",
            budget)
        entries = manifest.build(out, list(FILES))
        # FIX2 Y3: manifest.txt is staged and replaced like the other files (an interrupted write leaves the old one)
        manifest.write(os.path.join(out, "manifest.txt"), entries,
                       write_text=lambda p, text: _write_atomic(p, text, budget))
        note: dict[str, Any] = {"dir": out, "events": len(rows),
                                "manifest_sha256": sha256_file(os.path.join(out, "manifest.txt"))}
    except (OSError, sqlite3.Error, dbm.WfError, ValueError) as exc:
        note = {"dir": out, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if lock is not None:  # release the reservation: roll back (nothing was written), then close
            try:
                dbm.rollback(lock)
            except sqlite3.Error:
                pass
            try:
                lock.close()
            except sqlite3.Error:
                pass
    preserved = orphans(out)
    if preserved:
        note["preserved"] = preserved
    return note


def strays(out: str, listed: set[str]) -> list[str]:
    """A3 (spec 4.4 P4 row): every directory entry of the export dir that is neither `manifest.txt`, a listed
    file nor a recognised staging name (those keep their orphan/active semantics in `orphans`). One problem per
    entry, sorted by name; `os.scandir` without descending into subdirectories and without following links:
      unexpected export file: <name> · unexpected export directory: <name>/ · unexpected export link: <name>
      · unexpected export entry: <name> (anything else). Report only: nothing is deleted, moved or opened."""
    try:
        with os.scandir(out) as it:
            entries = sorted(it, key=lambda e: e.name)
    except OSError as exc:
        return [f"export directory unreadable: {type(exc).__name__}"]
    problems: list[str] = []
    for e in entries:
        name = e.name
        if name == "manifest.txt" or name in listed or name in PRODUCT_FILES or _STAGING.match(name):
            continue
        try:
            if e.is_symlink() or e.is_junction():
                problems.append(f"unexpected export link: {name}")
            elif e.is_dir(follow_symlinks=False):
                problems.append(f"unexpected export directory: {name}/")
            elif e.is_file(follow_symlinks=False):
                problems.append(f"unexpected export file: {name}")
            else:
                problems.append(f"unexpected export entry: {name}")
        except OSError:
            problems.append(f"unexpected export entry: {name}")
    return problems


def check(db_path: str, job: str) -> list[str]:
    """Verify-time consistency: manifest parses (3 fields), hashes match files, events.jsonl matches the ledger,
    plus one `export temp file preserved: <name>` problem per abandoned staging file (see `orphans`). The reads of
    manifest.txt, the entries and events.jsonl retry a Windows sharing or lock violation, 32 or 33 (`_retry_sharing`,
    FIX2 Z3). CPython's `open` and `read` report one as errno 13 with no `winerror` (WF-127 F1), so a read-only probe of
    the same path classifies it (FIX2 Y1); an access denial (5) and every other error are reported at once."""
    out = paths.export_dir(db_path, job)
    preserved = orphans(out)  # reported on every run while the file remains
    problems: list[str] = []
    mpath = os.path.join(out, "manifest.txt")
    if not os.path.isfile(mpath):
        return [f"export manifest missing: {mpath}", *preserved, *strays(out, set(FILES))]
    try:
        entries = _retry_sharing(manifest.read, mpath, probe=mpath)
    except manifest.ManifestMalformed as exc:
        return [str(exc), *preserved, *strays(out, set(FILES))]
    except OSError as exc:  # FIX2 #51: held by another process, for example
        return [_unreadable("manifest.txt", exc), *preserved, *strays(out, set(FILES))]
    preserved += strays(out, {e.relpath for e in entries})
    unreadable: set[str] = set()
    for e in entries:
        full = os.path.join(out, e.relpath)
        if not os.path.isfile(full):
            problems.append(f"export file missing: {e.relpath}")
            continue
        try:
            differs = _retry_sharing(_entry_differs, full, e, probe=full)
        except OSError as exc:  # FIX2 #51
            problems.append(_unreadable(e.relpath, exc))
            unreadable.add(e.relpath)
            continue
        if differs:
            problems.append(f"export file differs from manifest: {e.relpath}")
    ev = os.path.join(out, "events.jsonl")
    if os.path.isfile(ev) and "events.jsonl" not in unreadable:
        conn = dbm.connect(db_path, create=False)
        try:
            with dbm.read_snapshot(conn):
                expected = "".join(canonical(r) + "\n" for r in dbm.rows(conn, job))
        finally:
            conn.close()
        try:
            text = _retry_sharing(_read_text, ev, probe=ev)
        except OSError as exc:  # FIX2 #51
            problems.append(_unreadable("events.jsonl", exc))
        else:
            if text != expected:
                problems.append("export events.jsonl is stale relative to the ledger")
    return problems + preserved


def _entry_differs(full: str, e: manifest.Entry) -> bool:
    """One manifest entry against its file: the SHA-256 or the size differs."""
    return sha256_file(full) != e.sha256 or os.path.getsize(full) != e.size


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _unreadable(relpath: str, exc: OSError) -> str:
    """FIX2 #51 (N-b): an export file that cannot be read is a recoverable exception naming the file and the OS
    error, never an exit 4 (`wf verify`; `wf recover begin` lists it through `reconcile.recoverable_exceptions`)."""
    return f"export file unreadable: {relpath} ({type(exc).__name__}: {exc})"
