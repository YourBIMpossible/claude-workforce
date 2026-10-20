"""PACKAGE FIX2 batch A, lane F2AL (WF-103; AMENDMENT FIX2-A4): the resolve and write error contract.

  #1   a conflict marker whose `conflict` payload is empty, null, not an object or missing its claimant fields is still
       a conflict marker: refused and kept (fail closed), never resolved or cleared as an ordinary attempt. It is
       cleared only by proof, its row present with the marker's `self`, as a well-formed one is.
  #18  the resolve ROLLBACKs after a vanished marker (ALREADY-CLEARED) and after a failed conflict re-record are guarded
       as FIX1 E5 is: a failure is recorded as `rollback_error`, the decided outcome stands, never exit 4.
  #21  (wf.py, AMENDMENT FIX2-A9) an auto-resolve that raises something other than WfError after a commit-uncertain
       outcome is recorded beside it (`auto_resolve`: ERROR, "<type>: <message>"); the uncertain result stands.
  #46  `write_event`'s generic handler guards its own marker delete and closes the connection in `finally`: a failing
       delete is exit 1 `refused` `marker-unwritable`, never exit 4.
  #47  the DUPLICATE-IGNORED handler guards its ROLLBACK and closes in `finally`, so the write lock is never left held.
  #48  the generic handler runs for any exception, KeyboardInterrupt and SystemExit included, and re-raises it unchanged.
Each test failed or errored on the base b5b5881 (red-before in from-lazy RESULTS.md) unless its docstring says it pins
unchanged behaviour. Runs in the test_wf.py harness (a fresh scratch ledger per test under WF_TEST_TMP)."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import sys
import time
import unittest
from unittest import mock

from test_wf import Base

import wf
from wfcore import db as dbm
from wfcore import markers, paths, txn, validate


class _Fails:
    """A connection whose `execute` raises a non-BUSY OperationalError for the named statements."""

    def __init__(self, conn: sqlite3.Connection, statements: tuple[str, ...]) -> None:
        self._c = conn
        self._s = statements

    def __getattr__(self, name):
        return getattr(self._c, name)

    def execute(self, sql, *a):
        if isinstance(sql, str) and sql.strip().upper() in self._s:
            raise sqlite3.OperationalError(f"injected {sql.strip().upper()} failure")
        return self._c.execute(sql, *a)


def _failing(*statements: str):
    real = dbm.connect
    return mock.patch.object(txn.dbm, "connect", side_effect=lambda *a, **kw: _Fails(real(*a, **kw), statements))


class Ledger(Base):
    def setUp(self) -> None:
        super().setUp()
        self.new_job()
        self.activate()

    def coord(self, *argv: str):
        return self.run_wf(*argv, role="coordinator", session="C1")

    def main(self, *argv: str, role: str = "coordinator", session: str = "C1") -> tuple[int, dict]:
        """wf.main() in-process, so an exception that escapes `run()` shows as the CLI's exit 4."""
        out = io.StringIO()
        argv_ = ["wf.py", "--db", self.db, "--role", role, "--session", session, "--json", "--oplog", *argv]
        with mock.patch.object(sys, "argv", argv_), contextlib.redirect_stdout(out):
            code = wf.main()
        return code, json.loads(out.getvalue())

    def head(self) -> tuple[int, str]:
        conn = dbm.connect(self.db, create=False)
        try:
            return dbm.head(conn, "j")
        finally:
            conn.close()

    def row_at(self, seq: int):
        conn = dbm.connect(self.db, create=False)
        try:
            return dbm.row_at(conn, "j", seq)
        finally:
            conn.close()

    def marker(self, state: str, extra: dict | None = None, self_hash: str = "f" * 64, seq: int | None = None):
        """A marker at head + 1 (or `seq`) with no row, written as `write_event` writes one."""
        hs, hself = self.head()
        seq = hs + 1 if seq is None else seq
        ref = markers.MarkerRef(paths.pending_dir(self.db, "j"), seq, markers.new_attempt_id())
        m = markers.make("j", seq, ref.attempt_id, self_hash, hself, state, 2, "B9", self.db, "c")
        m.update(extra or {})
        markers.write_atomic(ref, m)
        return ref, m

    def second_writer(self) -> tuple[int, dict, float]:
        t0 = time.monotonic()
        c, r = self.intent(note="second writer")
        return c, r, time.monotonic() - t0

    def ops(self, r: dict) -> list[str]:
        return [o.get("op") for o in r.get("ops", [])]

    def lock_free(self) -> bool:
        """True when a new writer gets the reservation at once (no connection left holding it)."""
        con = sqlite3.connect(self.db, timeout=0.2)
        try:
            con.execute("BEGIN IMMEDIATE")
            con.rollback()
            return True
        except sqlite3.OperationalError:
            return False
        finally:
            con.close()


def _claimant(**drop: bool) -> dict:
    c = {"reason": validate.CONFLICT_REASON, "role": "builder", "generation": 1, "holder_session": "B1",
         "claimant_session": "B9", "attempted_type": "intent", "attempted_msg_id": "m-claim", "contract_sha256": "c"}
    for k in drop:
        c.pop(k)
    return c


# ------------------------------------------------------------------------------------------------ #1 (WF-020 F8)

class F2Conflict(Ledger):
    def kept_and_refused(self, conflict) -> None:
        ref, _ = self.marker("conflict-pending", {"conflict": conflict, "row_ts": validate.now_ts()})
        c, r = self.coord("resolve", "j", str(ref.seq), "--attempt", ref.attempt_id)
        self.assertEqual((c, r.get("outcome")), (1, "STILL-UNCERTAIN"), r)
        self.assertRegex(r.get("reason", ""), r"conflict marker (malformed|incomplete)", r)
        self.assertTrue(os.path.exists(ref.path), "the conflict marker must be kept")
        self.assertIsNone(self.row_at(ref.seq))
        c, r = self.intent(note="blocked")
        self.assertEqual((c, r.get("code")), (1, "uncertain-write-pending"), r)
        self.assertTrue(os.path.exists(ref.path))

    def test_empty_conflict_kept_and_refused(self):
        self.kept_and_refused({})

    def test_null_conflict_kept_and_refused(self):
        self.kept_and_refused(None)

    def test_non_object_conflict_kept_and_refused(self):
        self.kept_and_refused([])

    def test_conflict_missing_claimant_kept_and_refused(self):
        """Already held at the base (conflict_row_from_marker raised KeyError: "incomplete"); pinned here."""
        self.kept_and_refused(_claimant(claimant_session=True, holder_session=True))

    def test_guard_never_clears_malformed_as_aborting(self):
        """A conflict marker is never cleared as an aborting attempt, whatever its state field says."""
        ref, _ = self.marker("aborting", {"conflict": {}})
        c, r = self.intent(note="blocked")
        self.assertEqual((c, r.get("code")), (1, "uncertain-write-pending"), r)
        self.assertIn("conflict marker malformed", r.get("error", ""), r)
        self.assertTrue(os.path.exists(ref.path))

    def test_well_formed_conflict_re_recorded(self):
        """Pins unchanged behaviour (passes at the base): a well-formed conflict marker without its row is re-recorded."""
        hs, hself = self.head()
        m = {"job": "j", "seq": hs + 1, "prev": hself, "conflict": _claimant(), "row_ts": validate.now_ts()}
        crow = validate.conflict_row_from_marker(m)
        ref, _ = self.marker("conflict-pending", {"conflict": m["conflict"], "row_ts": m["row_ts"]}, self_hash=crow["self"])
        c, r = self.coord("resolve", "j", str(ref.seq), "--attempt", ref.attempt_id)
        self.assertEqual((c, r.get("outcome"), r.get("re_recorded")), (0, "NOT-COMMITTED", {"seq": crow["seq"], "self": crow["self"]}), r)
        self.assertFalse(os.path.exists(ref.path))
        self.assertEqual(self.row_at(ref.seq)["self"], crow["self"])

    def test_malformed_conflict_cleared_by_its_row(self):
        """Pins unchanged behaviour (passes at the base): with its row present (same `self`), the marker is COMMITTED."""
        self.assertEqual(self.intent(note="the row")[0], 0)
        hs, hself = self.head()
        ref, _ = self.marker("conflict-pending", {"conflict": {}}, self_hash=hself, seq=hs)
        c, r = self.coord("resolve", "j", str(ref.seq), "--attempt", ref.attempt_id)
        self.assertEqual((c, r.get("outcome")), (0, "COMMITTED"), r)
        self.assertFalse(os.path.exists(ref.path))


# ------------------------------------------------------------------------------------------------ #18 (S7a)

class F2ResolveRollback(Ledger):
    def test_already_cleared_rollback_failure_recorded(self):
        """txn.py:401 at the base: the marker vanishes between M0 and M1, and that ROLLBACK fails."""
        ref, m = self.marker("committing")
        reads = iter([m, None])
        with _failing("ROLLBACK"), mock.patch.object(txn.markers, "read_checked", side_effect=lambda _r: next(reads)):
            code, r = self.main("resolve", "j", str(ref.seq), "--attempt", ref.attempt_id)
        self.assertEqual((code, r.get("outcome")), (0, "ALREADY-CLEARED"), r)
        self.assertEqual(r.get("rollback_error"), "OperationalError: injected ROLLBACK failure", r)
        self.assertTrue(self.lock_free(), "resolve's finally closed the connection")

    def test_rerecord_rollback_failure_stays_uncertain(self):
        """txn.py:469 at the base: the conflict re-record's COMMIT fails (non-BUSY), then its ROLLBACK fails."""
        hs, hself = self.head()
        m = {"job": "j", "seq": hs + 1, "prev": hself, "conflict": _claimant(), "row_ts": validate.now_ts()}
        crow = validate.conflict_row_from_marker(m)
        ref, _ = self.marker("conflict-pending", {"conflict": m["conflict"], "row_ts": m["row_ts"]}, self_hash=crow["self"])
        with _failing("COMMIT", "ROLLBACK"):
            code, r = self.main("resolve", "j", str(ref.seq), "--attempt", ref.attempt_id)
        self.assertEqual((code, r.get("outcome")), (1, "STILL-UNCERTAIN"), r)
        self.assertIn("did not COMMIT", r.get("reason", ""), r)
        self.assertEqual(r.get("rollback_error"), "OperationalError: injected ROLLBACK failure", r)
        self.assertTrue(os.path.exists(ref.path), "an unrecorded conflict marker is kept")
        self.assertTrue(self.lock_free(), "resolve's finally closed the connection")
        self.assertIsNone(self.row_at(ref.seq), "the closed connection rolled the re-insert back")


# ------------------------------------------------------------------------------------------------ #21 (S7d)

class F2AutoResolve(Ledger):
    def test_auto_resolve_error_keeps_uncertain_result(self):
        """A non-BUSY COMMIT error gives commit-uncertain (no injected fault, so wf.py auto-resolves); the auto-resolve
        then raises a non-WfError."""
        with _failing("COMMIT"), \
                mock.patch.object(wf.commands, "resolve", side_effect=RuntimeError("injected auto-resolve failure")):
            code, r = self.main("append", "j", "--type", "intent", "--content",
                                json.dumps({"path": "jobs/j/work/a.txt", "sha_before": "", "note": "n"}),
                                role="builder", session="B1")
        self.assertEqual((code, r.get("code")), (1, "commit-uncertain"), r)
        self.assertEqual(r.get("auto_resolve"), {"outcome": "ERROR", "error": "RuntimeError: injected auto-resolve failure"}, r)
        self.assertTrue(r.get("attempt_id"), r)
        self.assertTrue(r.get("action", "").startswith("wf resolve j "), r)


# ------------------------------------------------------------------------------------------------ #46 (W57-F1)

class F2DeleteGuard(Ledger):
    def test_marker_delete_failure_is_marker_unwritable(self):
        """Windows and POSIX: the marker write fails, then deleting our own marker raises PermissionError."""
        before = self.head()[0]
        with mock.patch.object(txn.markers, "write_atomic", side_effect=OSError("injected unwritable")), \
                mock.patch.object(txn.markers, "delete", side_effect=PermissionError("injected delete denied")):
            code, r = self.main("append", "j", "--type", "intent", "--content",
                                json.dumps({"path": "jobs/j/work/a.txt", "sha_before": "", "note": "n"}),
                                role="builder", session="B1")
        self.assertEqual((code, r.get("code")), (1, "refused"), r)
        self.assertTrue(r.get("error", "").startswith("marker-unwritable: "), r)
        self.assertIn("injected delete denied", r["error"])
        self.assertIn("close", self.ops(r), r)
        self.assertEqual(self.head()[0], before)
        c, r2, took = self.second_writer()
        self.assertEqual((c, r2.get("outcome")), (0, "COMMITTED"), r2)
        self.assertLess(took, 2.0, "the write lock was released")

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() != 0,
                         "POSIX permission bits, as a non-root user")
    def test_posix_unwritable_pending_dir(self):
        pending = paths.pending_dir(self.db, "j")
        os.makedirs(pending, exist_ok=True)
        os.chmod(pending, 0o400)
        self.addCleanup(os.chmod, pending, 0o700)
        code, r = self.main("append", "j", "--type", "intent", "--content",
                            json.dumps({"path": "jobs/j/work/a.txt", "sha_before": "", "note": "n"}),
                            role="builder", session="B1")
        self.assertEqual((code, r.get("code")), (1, "refused"), r)
        self.assertTrue(r.get("error", "").startswith("marker-unwritable: "), r)
        self.assertIn("close", self.ops(r), r)


# ------------------------------------------------------------------------------------------------ #47 (W57-F2)

class F2DuplicateRollback(Ledger):
    def test_duplicate_rollback_failure_closes_conn(self):
        self.assertEqual(self.intent(msg_id="m-dup")[0], 0)
        with _failing("ROLLBACK"):
            code, r = self.main("--msg-id", "m-dup", "append", "j", "--type", "intent", "--content",
                                json.dumps({"path": "jobs/j/work/a.txt", "sha_before": "", "note": "n"}),
                                role="builder", session="B1")
        self.assertEqual((code, r.get("outcome")), (0, "DUPLICATE-IGNORED"), r)
        self.assertEqual(r.get("rollback_error"), "OperationalError: injected ROLLBACK failure", r)
        self.assertIn("close", self.ops(r), r)
        c, r2, took = self.second_writer()
        self.assertEqual((c, r2.get("outcome")), (0, "COMMITTED"), r2)
        self.assertLess(took, 2.0, "no database is locked: the write lock was released")


# ------------------------------------------------------------------------------------------------ #48 (W57-F3)

class F2BaseException(Ledger):
    def test_interrupt_rolls_back_closes_and_reraises(self):
        for exc in (KeyboardInterrupt("injected"), SystemExit("injected")):
            with self.subTest(exc=type(exc).__name__):
                before = self.head()[0]
                real = markers.write_atomic

                def write_then_raise(ref, data, _exc=exc):
                    real(ref, data)
                    raise _exc

                with mock.patch.object(txn.markers, "write_atomic", side_effect=write_then_raise):
                    with self.assertRaises(type(exc)) as cm:
                        self.intent(note="interrupted")
                self.assertIs(cm.exception, exc, "re-raised unchanged")
                del cm
                self.assertEqual(markers.list_markers(self.db, "j"), [], "no marker is left")
                self.assertEqual(self.head()[0], before)
                c, r, took = self.second_writer()
                self.assertEqual((c, r.get("outcome")), (0, "COMMITTED"), r)
                self.assertLess(took, 2.0, "no lock is left")


class F2ConflictInterrupt(Ledger):
    """Review WF-109 M1, with probe B (pre-existing at the base): an exception of any kind after our claimant-conflict
    marker is written never deletes that marker (FB1.4-A4 §3). It is kept as `conflict-pending` (rev 2), as the conflict
    give-up path keeps it, and `wf resolve` re-records the blocker row, so the role block stands. #48's ordinary case
    (no marker left) is `F2BaseException`, unchanged."""

    def claim(self, session: str, note: str):
        return self.run_wf("append", "j", "--type", "intent", "--content",
                           json.dumps({"path": "jobs/j/work/a.txt", "sha_before": "", "note": note}),
                           "--generation", "1", session=session)

    def kept_after(self, exc: BaseException) -> None:
        self.assertEqual(self.claim("B1", "first")[0], 0)  # B1 becomes the implicit builder
        real = markers.write_atomic

        def write_then_raise(ref, data, _exc=exc):
            real(ref, data)
            if isinstance(data, dict) and "conflict" in data and data.get("state") == "committing":
                raise _exc

        with mock.patch.object(txn.markers, "write_atomic", side_effect=write_then_raise):
            with self.assertRaises(type(exc)) as cm:
                self.claim("B2", "second claimant")
        self.assertIs(cm.exception, exc, "re-raised unchanged")
        exc.__traceback__ = None
        del cm
        left = markers.list_markers(self.db, "j")
        self.assertEqual(len(left), 1, "the conflict marker is kept")
        m = markers.read(left[0])
        self.assertEqual((m.get("state"), m.get("rev"), "conflict" in m), ("conflict-pending", 2, True), m)
        self.assertTrue(self.lock_free(), "the connection was closed")
        c, r = self.coord("resolve", "j", str(left[0].seq))
        self.assertEqual((c, r.get("outcome"), bool(r.get("re_recorded"))), (0, "NOT-COMMITTED", True), r)
        conn = dbm.connect(self.db, create=False)
        try:
            blockers = [row["seq"] for row in dbm.rows(conn, "j") if row["type"] == "blocker"]
        finally:
            conn.close()
        self.assertEqual(blockers, [left[0].seq])
        c, st = self.coord("status", "j")
        self.assertEqual(st["axes"].get("blocked_roles"), ["builder"], st)

    def test_keyboard_interrupt_keeps_conflict_marker(self):
        self.kept_after(KeyboardInterrupt("injected"))

    def test_system_exit_keeps_conflict_marker(self):
        self.kept_after(SystemExit("injected"))

    def test_runtime_error_keeps_conflict_marker(self):
        """Probe B: an ordinary exception at the same point deleted the conflict marker at the base too (exit 4)."""
        self.kept_after(RuntimeError("injected"))


if __name__ == "__main__":
    unittest.main()
