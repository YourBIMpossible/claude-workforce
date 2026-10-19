"""WF-029 (Lazy): injected-failure tests for the transaction paths that WF-020 left unexecuted (from-lazy
WF-020\\REMAINING-GAPS.md rows on txn.py, db.py and wf.py). Base: fix1/lazy-ids 96fb5c4. Current behaviour is
asserted; a contradiction with the spec is an expectedFailure whose docstring names the observation (a FINDING).

Failures are injected only on fixtures/tmp ledgers: a connection proxy whose COMMIT or ROLLBACK raises, a SHARED
reader that keeps COMMIT busy past T_HELD_BOUND (the test_wf.T29 pattern), a write reservation held past T_ACQ_BOUND,
patched seams (markers.*, dbm.*), or the WF_TEST_FAULTS fault seam. Runs inside the test_wf.py harness."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import sys
from unittest import mock

import test_wf
from test_wf import Base

import wf
from wfcore import db as dbm
from wfcore import markers, paths, txn
from wfcore.db import OpLog, WfLockTimeout, WfRefused

INTENT = {"path": "jobs/j/work/a.txt", "sha_before": "", "note": "n"}


class _Conn:
    """sqlite3 connection proxy: `fail` maps an upper-cased statement to the exception it raises (each time)."""

    def __init__(self, conn: sqlite3.Connection, fail: dict[str, BaseException]) -> None:
        self._c = conn
        self._fail = fail

    def __getattr__(self, name):
        return getattr(self._c, name)

    def execute(self, sql, *a):
        if isinstance(sql, str) and sql.strip().upper() in self._fail:
            raise self._fail[sql.strip().upper()]
        return self._c.execute(sql, *a)


def failing_connect(fail: dict[str, BaseException], which: int = 1):
    """dbm.connect whose `which`-th connection (1-based) is a failing proxy; the others are real."""
    real = dbm.connect
    made: list = []

    def connect(*a, **kw):
        conn = real(*a, **kw)
        made.append(conn)
        return _Conn(conn, fail) if len(made) == which else conn
    return connect


def io_error() -> sqlite3.OperationalError:
    return sqlite3.OperationalError("disk I/O error")


class Fixture(Base):
    def setUp(self) -> None:
        super().setUp()
        self.new_job()
        self.activate()

    def coord(self, *argv: str):
        return self.run_wf(*argv, role="coordinator", session="C1")

    def head(self) -> tuple[int, str]:
        conn = dbm.connect(self.db, create=False)
        try:
            return dbm.head(conn, "j")
        finally:
            conn.close()

    def reader(self) -> sqlite3.Connection:
        holder = sqlite3.connect(self.db, isolation_level=None)
        holder.execute("BEGIN")
        holder.execute("SELECT count(*) FROM events").fetchone()
        return holder

    @contextlib.contextmanager
    def held_reader(self):
        holder = self.reader()
        try:
            yield holder
        finally:
            holder.execute("COMMIT")
            holder.close()

    def put_marker(self, seq: int, state: str, self_: str, prev: str, extra: dict | None = None) -> markers.MarkerRef:
        ref = markers.MarkerRef(paths.pending_dir(self.db, "j"), seq, markers.new_attempt_id())
        m = markers.make("j", seq, ref.attempt_id, self_, prev, state, 1, "s", self.db, "c")
        m.update(extra or {})
        markers.write_atomic(ref, m)
        return ref

    def next_marker(self) -> markers.MarkerRef:
        seq, head_self = self.head()
        return self.put_marker(seq + 1, "committing", "f" * 64, head_self)

    def conflict(self, *extra: str, **kw):
        return self.run_wf("append", "j", "--type", "intent", "--content", json.dumps(dict(INTENT, note="second")),
                           *extra, session="B2", **kw)


# ----------------------------------------------------------------------------------------- write_event COMMIT

class CommitFailures(Fixture):
    def test_non_busy_commit_error_is_commit_uncertain_and_auto_resolved(self):
        """txn.py:170 (REMAINING-GAPS row 35): a non-BUSY error at COMMIT -> commit-uncertain; wf runs one resolve."""
        self.assertEqual(self.intent()[0], 0)
        with mock.patch.object(txn.dbm, "connect", side_effect=failing_connect({"COMMIT": io_error()})):
            c, r = self.intent(note="io")
        self.assertEqual((c, r["code"]), (1, "commit-uncertain"), r)
        self.assertIn("COMMIT raised", r["error"])
        self.assertEqual(r["auto_resolve"]["outcome"], "NOT-COMMITTED", r)
        self.assertEqual(markers.list_markers(self.db, "j"), [])

    def test_auto_resolve_that_itself_fails_is_reported_as_error(self):
        """wf.py:260-261 (row 58): the automatic resolve raising a WfError is reported `auto_resolve.outcome: ERROR`."""
        self.assertEqual(self.intent()[0], 0)
        with mock.patch.object(txn.dbm, "connect", side_effect=failing_connect({"COMMIT": io_error()})), \
                mock.patch.object(wf.commands, "resolve", side_effect=WfRefused("injected resolve failure")):
            c, r = self.intent(note="io")
        self.assertEqual((c, r["code"]), (1, "commit-uncertain"), r)
        self.assertEqual(r["auto_resolve"], {"outcome": "ERROR", "error": "injected resolve failure"})
        self.assertEqual(len(markers.list_markers(self.db, "j")), 1, "the committing marker stays for a later resolve")

    def test_job_abandon_non_busy_commit_error(self):
        """txn.py:314 (row 39): `job abandon` contracts-update COMMIT error -> commit-uncertain (no seq, no auto-resolve).
        The first connection is job_abandon's recovery-open read; the second is the contracts update."""
        with mock.patch.object(txn.dbm, "connect", side_effect=failing_connect({"COMMIT": io_error()}, which=2)):
            c, r = self.coord("job", "abandon", "j", "--reason", "r")
        self.assertEqual((c, r["code"]), (1, "commit-uncertain"), r)
        self.assertIn("COMMIT raised disk I/O error", r["error"])
        self.assertNotIn("auto_resolve", r)
        c, r = self.intent(note="still open")
        self.assertEqual((c, r["outcome"]), (0, "COMMITTED"), r)

    def test_reservation_not_obtained_while_another_writer_holds_it(self):
        """db.py:202 (row 33): BEGIN IMMEDIATE busy past T_ACQ_BOUND (5 s) -> lock-timeout 'reservation not obtained'."""
        holder = sqlite3.connect(self.db, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        try:
            c, r = self.intent(note="waits")
        finally:
            holder.execute("ROLLBACK")
            holder.close()
        self.assertEqual((c, r["code"]), (1, "lock-timeout"), r)
        self.assertIn("reservation not obtained within", r["error"])


class ConflictCommitFailures(Fixture):
    """Claimant conflict (B1 holds builder generation 1, B2 claims it) combined with a COMMIT failure."""

    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.intent()[0], 0)

    def test_conflict_without_marker_and_non_busy_commit_error_is_unrecorded(self):
        """txn.py:169 (row 34): marker-unwritable (fault seam) + non-BUSY COMMIT error -> conflict-unrecorded."""
        before = self.head()[0]
        with mock.patch.dict(os.environ, {"WF_TEST_FAULTS": "marker-unwritable"}), \
                mock.patch.object(txn.dbm, "connect", side_effect=failing_connect({"COMMIT": io_error()})):
            c, r = self.conflict()
        self.assertEqual((c, r["code"], r["conflict_outcome"]), (1, "rejected", "conflict-unrecorded"), r)
        self.assertIn("COMMIT raised", r["error"])
        self.assertIn("is NOT recorded", r["error"])
        self.assertEqual(self.head()[0], before)

    def test_conflict_without_marker_and_busy_commit_is_unrecorded(self):
        """txn.py:178 (row 36): marker-unwritable + COMMIT busy past T_HELD_BOUND -> conflict-unrecorded."""
        before = self.head()[0]
        with mock.patch.dict(os.environ, {"WF_TEST_FAULTS": "marker-unwritable"}), self.held_reader():
            c, r = self.conflict()
        self.assertEqual((c, r["conflict_outcome"]), (1, "conflict-unrecorded"), r)
        self.assertIn("COMMIT busy past", r["error"])
        self.assertEqual(self.head()[0], before)

    def test_conflict_pending_marker_rewrite_failure_is_logged_and_the_committing_marker_stands(self):
        """txn.py:186 (row 38): busy COMMIT, the conflict-pending rewrite fails -> commit-timeout, op logged."""
        real = markers.write_atomic

        def flaky(ref, data):
            if data.get("state") == "conflict-pending":
                raise OSError("injected rewrite failure")
            return real(ref, data)

        with mock.patch.object(markers, "write_atomic", side_effect=flaky), self.held_reader():
            c, r = self.conflict("--oplog")
        self.assertEqual((c, r["code"], r["conflict_outcome"]), (1, "commit-timeout", "conflict-pending"), r)
        failed = [o for o in r["ops"] if o.get("op") == "marker_conflict_pending_failed"]
        self.assertEqual(len(failed), 1, r["ops"])
        self.assertIn("injected rewrite failure", failed[0]["error"])
        self.assertFalse(any(o.get("op") == "marker_conflict_pending" for o in r["ops"]), r["ops"])
        (ref,) = markers.list_markers(self.db, "j")
        self.assertEqual(markers.read(ref)["state"], "committing")

    def test_conflict_give_up_rollback_failure_is_commit_uncertain(self):
        """txn.py:192 (row 37): busy COMMIT, then ROLLBACK raises -> commit-uncertain, conflict-pending marker kept."""
        with mock.patch.object(txn.dbm, "connect", side_effect=failing_connect({"ROLLBACK": io_error()})), \
                self.held_reader():
            c, r = self.conflict()
        self.assertEqual((c, r["code"]), (1, "commit-uncertain"), r)
        self.assertIn("give-up: ROLLBACK raised", r["error"])
        self.assertEqual(r["conflict_outcome"], "conflict-pending")
        (ref,) = markers.list_markers(self.db, "j")                        # the conflict-pending marker is kept
        m = markers.read(ref)
        self.assertEqual((m["state"], m["rev"]), ("conflict-pending", 2))
        self.assertIn("conflict", m)


# ----------------------------------------------------------------------------------------- resolve

class ResolveFailures(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.intent()[0], 0)

    def resolve(self, seq: int):
        return self.coord("resolve", "j", str(seq))

    def test_connection_setup_busy_is_reservation_not_obtained(self):
        """txn.py:387 (row 40)."""
        ref = self.next_marker()
        with mock.patch.object(txn.dbm, "connect", side_effect=WfLockTimeout("injected connect busy")):
            c, r = self.resolve(ref.seq)
        self.assertEqual((c, r["outcome"]), (1, "RESERVATION-NOT-OBTAINED"), r)
        self.assertIn("connection setup stayed busy", r["note"])
        self.assertTrue(os.path.exists(ref.path))

    def test_reservation_held_by_another_writer_is_reservation_not_obtained(self):
        """txn.py:395 (row 41): a real BEGIN IMMEDIATE holder for T_ACQ_BOUND (5 s)."""
        ref = self.next_marker()
        holder = sqlite3.connect(self.db, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        try:
            c, r = self.resolve(ref.seq)
        finally:
            holder.execute("ROLLBACK")
            holder.close()
        self.assertEqual((c, r["outcome"]), (1, "RESERVATION-NOT-OBTAINED"), r)
        # txn.py:395, not :387 (which says "connection setup stayed busy"): the BEGIN IMMEDIATE wait gave up
        self.assertEqual(r["note"], "no change; an originator still holding the reservation looks like this")
        self.assertIn("reservation not obtained within", r["detail"])
        self.assertTrue(os.path.exists(ref.path))

    def test_marker_vanishing_between_m0_and_m1_is_already_cleared(self):
        """txn.py:402 (row 42)."""
        ref = self.next_marker()
        m = markers.read(ref)
        with mock.patch.object(markers, "read_checked", side_effect=[m, None]):
            c, r = self.resolve(ref.seq)
        self.assertEqual((c, r["outcome"]), (0, "ALREADY-CLEARED"), r)

    def test_budget_exceeded_is_still_uncertain(self):
        """txn.py:416-417 (row 43): a decision taken after the resolve budget is never trusted (API: resolve_bound)."""
        ref = self.next_marker()
        r = txn.resolve(self.db, "j", ref.seq, None, OpLog(), resolve_bound=-1.0)
        self.assertEqual((r["outcome"], r["reason"]), ("STILL-UNCERTAIN", "resolve budget exceeded"))
        self.assertTrue(os.path.exists(ref.path))

    def test_invalid_chain_is_still_uncertain(self):
        """txn.py:420 (row 44)."""
        ref = self.next_marker()
        with mock.patch.object(txn.dbm, "verify_chain", return_value=["injected chain problem"]):
            c, r = self.resolve(ref.seq)
        self.assertEqual((c, r["outcome"], r["reason"]), (1, "STILL-UNCERTAIN", "chain invalid"), r)
        self.assertEqual(r["chain_problems"], ["injected chain problem"])

    def test_quick_check_failure_is_still_uncertain(self):
        """txn.py:431 (row 45): head matches the marker's prev but quick_check reports damage."""
        ref = self.next_marker()
        with mock.patch.object(txn.dbm, "integrity", return_value=["injected page error"]):
            c, r = self.resolve(ref.seq)
        self.assertEqual((c, r["outcome"], r["reason"]), (1, "STILL-UNCERTAIN", "quick_check not ok"), r)
        self.assertEqual(r["quick_check"], ["injected page error"])
        self.assertTrue(os.path.exists(ref.path))

    def test_marker_gone_before_delete_is_still_uncertain(self):
        """txn.py:479 (row 48)."""
        ref = self.next_marker()
        with mock.patch.object(markers, "delete", return_value=False):
            c, r = self.resolve(ref.seq)
        self.assertEqual((c, r["outcome"]), (1, "STILL-UNCERTAIN"), r)
        self.assertEqual(r["reason"], "marker vanished before delete under reservation")

    def test_marker_delete_error_is_still_uncertain(self):
        """txn.py:483 (row 49)."""
        ref = self.next_marker()
        with mock.patch.object(markers, "delete", side_effect=OSError("injected delete error")):
            c, r = self.resolve(ref.seq)
        self.assertEqual((c, r["outcome"]), (1, "STILL-UNCERTAIN"), r)
        self.assertEqual(r["reason"], "delete failed: injected delete error")
        self.assertTrue(os.path.exists(ref.path))


class ResolveConflictMarker(Fixture):
    """A genuine conflict-pending marker (test_wf.C2a: a SHARED reader keeps the conflict COMMIT busy)."""

    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.intent()[0], 0)
        holder = self.reader()
        try:
            c, r = self.run_wf("append", "j", "--type", "intent", "--content", json.dumps(dict(INTENT, note="second")),
                               session="B2")
        finally:
            holder.execute("COMMIT")
            holder.close()
        self.assertEqual(r.get("conflict_outcome"), "conflict-pending", r)
        (self.ref,) = markers.list_markers(self.db, "j")

    def test_conflict_marker_whose_self_does_not_match_its_fields(self):
        """txn.py:447 (row 46)."""
        m = markers.read(self.ref)
        m["self"] = "0" * 64
        markers.write_atomic(self.ref, m)
        c, r = self.coord("resolve", "j", str(self.ref.seq))
        self.assertEqual((c, r["outcome"]), (1, "STILL-UNCERTAIN"), r)
        self.assertEqual(r["reason"], "conflict marker self does not match its recorded fields")
        self.assertTrue(os.path.exists(self.ref.path))

    def test_conflict_row_re_record_that_cannot_commit(self):
        """txn.py:471 (row 47): the re-inserted conflict row's COMMIT fails (non-BUSY) -> rolled back, still uncertain."""
        with mock.patch.object(txn.dbm, "connect", side_effect=failing_connect({"COMMIT": io_error()})):
            c, r = self.coord("resolve", "j", str(self.ref.seq))
        self.assertEqual((c, r["outcome"]), (1, "STILL-UNCERTAIN"), r)
        self.assertEqual(r["reason"], "conflict row re-record did not COMMIT within the resolve budget")
        self.assertTrue(os.path.exists(self.ref.path))
        c, r = self.coord("resolve", "j", str(self.ref.seq))                # the marker is still resolvable
        self.assertEqual((c, r["outcome"]), (0, "NOT-COMMITTED"), r)


# ----------------------------------------------------------------------------------------- fault seam

class FaultSeamArgument(Fixture):
    def test_non_integer_delay_argument_is_an_internal_error(self):
        """faults.py:41 (row 76; the WF-020 inventory cited the 2dedc94 line 34): `delay-precommit:abc` raises ValueError from int(); test seam only (fixtures-tmp
        ledgers). Since FIX1 E1 the CLI reports it as `internal-error`, exit 4, instead of a traceback."""
        with mock.patch.dict(os.environ, {"WF_TEST_FAULTS": "delay-precommit:abc"}):
            with self.assertRaises(ValueError):
                self.intent(note="bad seam arg")
            out = io.StringIO()
            argv = ["wf.py", "--db", self.db, "--role", "builder", "--session", "B1", "--json", "append", "j", "--type",
                    "intent", "--content", json.dumps(INTENT)]
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out):
                code = wf.main()
        self.assertEqual(code, 4)
        self.assertEqual(json.loads(out.getvalue())["code"], "internal-error")
