"""Per-attempt pending-write markers and the reservation-serialized guard (FB1.4 §4.4a)."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from . import paths
from .canon import canonical
from .db import OpLog, WfRefused, row_at

_NAME = re.compile(r"^(\d+)-([0-9a-f]{32})\.json$")


class UncertainWritePending(WfRefused):
    code = "uncertain-write-pending"


@dataclass(frozen=True)
class MarkerRef:
    dir: str
    seq: int
    attempt_id: str

    @property
    def path(self) -> str:
        return os.path.join(self.dir, f"{self.seq}-{self.attempt_id}.json")

    @property
    def tmp(self) -> str:
        return self.path + f".tmp-{self.attempt_id}"

    @property
    def log(self) -> str:
        return os.path.join(self.dir, f"{self.seq}-{self.attempt_id}.resolve.log")


def new_attempt_id() -> str:
    return os.urandom(16).hex()


SHARE_RETRY_S = 0.5  # Windows sharing violations (WinError 32) while another process has the marker open


def _retry_share(fn, *args):
    """Run an unlink/replace, retrying only PermissionError for a bounded window; other errors propagate."""
    deadline = time.monotonic() + SHARE_RETRY_S
    while True:
        try:
            return fn(*args)
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.002)


def write_atomic(ref: MarkerRef, data: dict[str, Any]) -> None:
    os.makedirs(ref.dir, exist_ok=True)
    text = canonical(data)
    with open(ref.tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    _retry_share(os.replace, ref.tmp, ref.path)


def read(ref: MarkerRef) -> dict[str, Any] | None:
    deadline = time.monotonic() + SHARE_RETRY_S
    while True:
        try:
            with open(ref.path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return None
        except PermissionError:  # owner mid-replace/delete; vanishes or settles shortly
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.002)


def read_bytes(ref: MarkerRef) -> bytes | None:
    try:
        with open(ref.path, "rb") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def delete(ref: MarkerRef) -> bool:
    """Delete by exact filename; missing tolerated. Returns True if a file was removed."""
    try:
        _retry_share(os.remove, ref.path)
        return True
    except FileNotFoundError:
        return False


def list_markers(db: str, job: str) -> list[MarkerRef]:
    d = paths.pending_dir(db, job)
    if not os.path.isdir(d):
        return []
    out = []
    for name in sorted(os.listdir(d)):
        m = _NAME.match(name)
        if m:
            out.append(MarkerRef(d, int(m.group(1)), m.group(2)))
    return out


def list_all(db: str) -> list[tuple[str, MarkerRef]]:
    base = os.path.join(paths.ledger_dir(db), "pending")
    if not os.path.isdir(base):
        return []
    out = []
    for job in sorted(os.listdir(base)):
        for ref in list_markers(db, job):
            out.append((job, ref))
    return out


def make(job: str, seq: int, attempt_id: str, self_hash: str, prev: str, state: str, rev: int,
         session: str, db: str, contract_sha: str) -> dict[str, Any]:
    return {"job": job, "seq": seq, "attempt_id": attempt_id, "self": self_hash, "prev": prev,
            "state": state, "rev": rev, "pid": os.getpid(), "session": session,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z", "db_path": os.path.abspath(db),
            "contract_sha": contract_sha}


def append_log(ref: MarkerRef, entry: dict[str, Any]) -> None:
    os.makedirs(ref.dir, exist_ok=True)
    with open(ref.log, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(canonical({"ts": time.time(), **entry}) + "\n")


def guard(conn: sqlite3.Connection, db: str, job: str, oplog: OpLog, bypass: str = "") -> list[dict[str, Any]]:
    """Run under the caller's BEGIN IMMEDIATE, before SELECT head.
    Clears provably finished attempts by exact filename; raises UncertainWritePending otherwise.
    `bypass` (fixture-only fault) skips the marker with that attempt id."""
    cleared: list[dict[str, Any]] = []
    for ref in list_markers(db, job):
        if bypass and ref.attempt_id == bypass:
            oplog.add("guard_bypass", attempt_id=ref.attempt_id)
            continue
        try:
            m = read(ref)
        except (OSError, ValueError) as exc:
            raise UncertainWritePending(f"uncertain-write pending: {job}/{ref.seq}/{ref.attempt_id} (marker unreadable: {exc})",
                                        job=job, seq=ref.seq, attempt_id=ref.attempt_id)
        if m is None:
            continue  # vanished between listing and read: its owner finished it
        if not isinstance(m, dict) or m.get("attempt_id") != ref.attempt_id or m.get("seq") != ref.seq:
            raise UncertainWritePending(f"uncertain-write pending: {job}/{ref.seq}/{ref.attempt_id} (marker malformed)",
                                        job=job, seq=ref.seq, attempt_id=ref.attempt_id)
        row = row_at(conn, job, ref.seq)
        if row is not None and row["self"] == m.get("self"):
            delete(ref)
            oplog.add("guard_clear", outcome="committed", attempt_id=ref.attempt_id, seq=ref.seq)
            cleared.append({"attempt_id": ref.attempt_id, "seq": ref.seq, "cleared": "committed"})
            continue
        if m.get("state") == "aborting" and row is None:
            delete(ref)
            oplog.add("guard_clear", outcome="aborting", attempt_id=ref.attempt_id, seq=ref.seq)
            cleared.append({"attempt_id": ref.attempt_id, "seq": ref.seq, "cleared": "aborting"})
            continue
        # `committing` and `conflict-pending` (FB1.4-A4 §3: a conflict row whose COMMIT went busy) both refuse
        # until `wf resolve` settles them; a conflict marker is never cleared without its row present.
        raise UncertainWritePending(f"uncertain-write pending: {job}/{ref.seq}/{ref.attempt_id}",
                                    job=job, seq=ref.seq, attempt_id=ref.attempt_id, state=m.get("state"))
    return cleared


def describe(db: str, job: str) -> list[dict[str, Any]]:
    """Read-only listing for status/digest/verify: attempt id, seq, state, rev."""
    out = []
    for ref in list_markers(db, job):
        m = read(ref) or {}
        out.append({"seq": ref.seq, "attempt_id": ref.attempt_id, "state": m.get("state", "unreadable"),
                    "rev": m.get("rev"), "self": m.get("self"), "path": ref.path, "conflict": "conflict" in m})
    return out
