"""Per-job export refresh (FB1.4 D7): events.jsonl + head.json + manifest.txt under jobs\\<job>\\export."""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

from . import db as dbm
from . import manifest, paths
from .canon import canonical, sha256_file

FILES = ("events.jsonl", "head.json", "contract.json")


def _write_atomic(path: str, text: str) -> None:
    """Write `<path>.tmp` exclusively, fsync, then rename it over `path`.

    The temp path is created with O_CREAT|O_EXCL (mode "x"): a pre-existing `<path>.tmp` of any kind (file,
    symlink, directory entry left by a crashed or concurrent writer) makes `open` raise `FileExistsError`
    before a byte is written, so it is never truncated, replaced or followed. Nothing here deletes, repairs or
    retries; the caller's existing error path reports the failure and the artifact stays for inspection.
    """
    tmp = path + ".tmp"
    with open(tmp, "x", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


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
        return {"dir": out, "events": len(rows), "manifest_sha256": sha256_file(os.path.join(out, "manifest.txt"))}
    except (OSError, sqlite3.Error, dbm.WfError, ValueError) as exc:
        return {"dir": out, "error": f"{type(exc).__name__}: {exc}"}


def check(db_path: str, job: str) -> list[str]:
    """Verify-time consistency: manifest parses (3 fields), hashes match files, events.jsonl matches the ledger."""
    out = paths.export_dir(db_path, job)
    problems: list[str] = []
    mpath = os.path.join(out, "manifest.txt")
    if not os.path.isfile(mpath):
        return [f"export manifest missing: {mpath}"]
    try:
        entries = manifest.read(mpath)
    except manifest.ManifestMalformed as exc:
        return [str(exc)]
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
    return problems
