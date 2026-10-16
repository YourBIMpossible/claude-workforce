"""Per-job export refresh (FB1.4 D7): events.jsonl + head.json + manifest.txt under jobs\\<job>\\export."""
from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import sys
import threading
from typing import Any

from . import db as dbm
from . import manifest, paths
from .canon import canonical, sha256_file

FILES = ("events.jsonl", "head.json", "contract.json")


PRESERVED = "export temp file preserved: "
#: Files the product itself writes into the export dir outside the manifest (never strays): the retained
#: checkpoint written by `wf checkpoint` (commands.checkpoint, T36). Deviation from the A3 inventory as stated
#: (manifest.txt + entries + staging): without it `wf checkpoint` followed by `wf verify` reports its own file
#: and `wf close` refuses (test_wf T08/C4). Reported to White for the paired integration decision.
PRODUCT_FILES: frozenset[str] = frozenset({"checkpoint.json"})
#: Staging names: the legacy fixed `<file>.tmp` and the per-writer `<file>.<pid>-<hex8>.tmp`, for the export
#: files and the product-written checkpoint (also staged through `_write_atomic`).
_STAGING = re.compile(r"^(?P<base>" + "|".join(re.escape(f) for f in (*FILES, *sorted(PRODUCT_FILES)))
                      + r")(?:\.(?P<pid>\d+)-[0-9a-f]{8})?\.tmp$")
#: Staging paths this process currently has open (active, never an orphan). Module-level so every refresh in
#: this process agrees; a path is removed as soon as its write completes or fails.
_ACTIVE: set[str] = set()
_ACTIVE_LOCK = threading.Lock()


def _write_atomic(path: str, text: str) -> None:
    """Stage `<path>.<pid>-<random>.tmp` exclusively, fsync, then `os.replace` it over `path`.

    The staging name is unique per writer, so concurrent refreshes never share a temp file, and it is created
    with O_CREAT|O_EXCL (mode "x"), so an existing entry of any kind at that name (file, symlink) is never
    truncated, replaced or followed. A pre-existing `<path>.tmp` or another writer's staging file is left
    untouched: nothing here deletes, repairs or retries them. If THIS writer's publish fails (write, fsync or the
    os.replace race against a concurrent refresh), the writer discards its own in-flight staging file so a lost
    race leaves nothing behind; the failure itself is still raised and reported. Only a staging file whose writer
    died (or whose discard failed) is left behind, and that one is reported as an orphan by the next refresh/check.
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
        os.replace(tmp, path)
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
    """Best effort: is `pid` a running process? True on any doubt (never claims an orphan it cannot prove)."""
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
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
    reported, never raised past the caller's committed write."""
    out = paths.export_dir(db_path, job)
    try:
        os.makedirs(out, exist_ok=True)
        conn = dbm.connect(db_path, create=False, bound=dbm.T_HELD_BOUND)
        try:
            with dbm.read_snapshot(conn, bound=dbm.T_HELD_BOUND):
                rows = dbm.rows(conn, job)
                c = dbm.contract(conn, job)
        finally:
            conn.close()
        _write_atomic(os.path.join(out, "events.jsonl"), "".join(canonical(r) + "\n" for r in rows))
        head = rows[-1] if rows else None
        _write_atomic(os.path.join(out, "head.json"), canonical(
            {"job": job, "seq": head["seq"] if head else 0, "self": head["self"] if head else None,
             "events": len(rows)}) + "\n")
        _write_atomic(os.path.join(out, "contract.json"), canonical(
            {"job": job, "sha256": c["sha256"], "abandoned_ts": c["abandoned_ts"],
             "abandon_reason": c["abandon_reason"], "content": json.loads(c["content"])} if c else {"job": job}) + "\n")
        entries = manifest.build(out, list(FILES))
        manifest.write(os.path.join(out, "manifest.txt"), entries)
        note: dict[str, Any] = {"dir": out, "events": len(rows),
                                "manifest_sha256": sha256_file(os.path.join(out, "manifest.txt"))}
    except (OSError, sqlite3.Error, dbm.WfError, ValueError) as exc:
        note = {"dir": out, "error": f"{type(exc).__name__}: {exc}"}
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
    plus one `export temp file preserved: <name>` problem per abandoned staging file (see `orphans`)."""
    out = paths.export_dir(db_path, job)
    preserved = orphans(out)  # reported on every run while the file remains
    problems: list[str] = []
    mpath = os.path.join(out, "manifest.txt")
    if not os.path.isfile(mpath):
        return [f"export manifest missing: {mpath}", *preserved, *strays(out, set(FILES))]
    try:
        entries = manifest.read(mpath)
    except manifest.ManifestMalformed as exc:
        return [str(exc), *preserved, *strays(out, set(FILES))]
    preserved += strays(out, {e.relpath for e in entries})
    for e in entries:
        full = os.path.join(out, e.relpath)
        if not os.path.isfile(full):
            problems.append(f"export file missing: {e.relpath}")
            continue
        if sha256_file(full) != e.sha256 or os.path.getsize(full) != e.size:
            problems.append(f"export file differs from manifest: {e.relpath}")
    ev = os.path.join(out, "events.jsonl")
    if os.path.isfile(ev):
        conn = dbm.connect(db_path, create=False)
        try:
            with dbm.read_snapshot(conn):
                expected = "".join(canonical(r) + "\n" for r in dbm.rows(conn, job))
        finally:
            conn.close()
        with open(ev, "r", encoding="utf-8") as fh:
            if fh.read() != expected:
                problems.append("export events.jsonl is stale relative to the ledger")
    return problems + preserved
