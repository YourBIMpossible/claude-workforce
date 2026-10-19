"""WF-020 (Lazy): transaction / `wf resolve` / `job abandon` failure paths that no earlier test exercised
(from-lazy WF-020 GAP-LIST.md section 3). Current behaviour at 2dedc94 is asserted; a path whose observed behaviour
contradicts the code's own stated rule is an expectedFailure with the observation in its docstring (a FINDING).

Failures are injected by patching the module seams (`markers.delete`, `markers.write_atomic`, `dbm.connect`) or by
holding a SHARED reader (the pattern used by test_wf.T29). Runs inside the test_wf.py harness."""
from __future__ import annotations

import json
import sqlite3
import unittest
from unittest import mock

import test_wf
from test_wf import Base

from wfcore import db as dbm
from wfcore import markers, paths, txn

INTENT = json.dumps({"path": "jobs/j/work/a.txt", "sha_before": "", "note": "n"})


class Fixture(Base):
    def setUp(self) -> None:
        super().setUp()
        self.new_job()
        self.activate()
        self.assertEqual(self.intent()[0], 0)

    def head(self) -> tuple[int, str]:
        conn = dbm.connect(self.db, create=False)
        try:
            return dbm.head(conn, "j")
        finally:
            conn.close()

    def put_marker(self, seq: int, state: str, self_: str, prev: str, extra: dict | None = None) -> markers.MarkerRef:
        ref = markers.MarkerRef(paths.pending_dir(self.db, "j"), seq, markers.new_attempt_id())
        m = markers.make("j", seq, ref.attempt_id, self_, prev, state, 1, "s", self.db, "c")
        m.update(extra or {})
        markers.write_atomic(ref, m)
        return ref

    def resolve(self, seq: int, *extra: str):
        return self.run_wf("resolve", "j", str(seq), *extra, role="coordinator", session="C1")

    def hold_shared_reader(self) -> sqlite3.Connection:
        holder = sqlite3.connect(self.db, isolation_level=None)
        holder.execute("BEGIN")
        holder.execute("SELECT count(*) FROM events").fetchone()
        return holder


class ResolveOutcomes(Fixture):
    def test_marker_for_an_existing_seq_with_a_different_self_is_a_conflict(self):
        seq, _ = self.head()
        ref = self.put_marker(seq, "committing", "f" * 64, "p" * 64)
        c, r = self.resolve(seq)
        self.assertEqual((c, r["outcome"]), (0, "CONFLICT"), r)
        self.assertEqual(r["head_seq"], seq)
        self.assertIn("row_self", r)

    def test_marker_far_beyond_the_head_stays_uncertain_and_is_kept(self):
        seq, head_self = self.head()
        ref = self.put_marker(seq + 5, "committing", "f" * 64, head_self)
        c, r = self.resolve(seq + 5)
        self.assertEqual((c, r["outcome"]), (1, "STILL-UNCERTAIN"), r)
        self.assertEqual(r["reason"], f"head {seq} vs seq {seq + 5} without a matching row")
        self.assertFalse(r["ok"])
        self.assertTrue(__import__("os").path.exists(ref.path), "an undecided marker is never deleted")

    def test_next_seq_committing_marker_that_never_committed_is_not_committed(self):
        seq, head_self = self.head()
        ref = self.put_marker(seq + 1, "committing", "f" * 64, head_self)
        c, r = self.resolve(seq + 1)
        self.assertEqual((c, r["outcome"]), (0, "NOT-COMMITTED"), r)
        self.assertFalse(__import__("os").path.exists(ref.path))

    def test_incomplete_conflict_marker_stays_uncertain(self):
        seq, head_self = self.head()
        ref = self.put_marker(seq + 1, "conflict-pending", "f" * 64, head_self, {"conflict": {"role": "builder"}})
        c, r = self.resolve(seq + 1)
        self.assertEqual((c, r["outcome"]), (1, "STILL-UNCERTAIN"), r)
        self.assertIn("conflict marker incomplete: 'attempted_msg_id'", r["reason"])
        self.assertTrue(__import__("os").path.exists(ref.path))

    @unittest.expectedFailure
    def test_conflict_pending_marker_with_an_empty_conflict_payload_is_not_silently_dropped(self):
        """FINDING (low; needs a tampered or truncated marker): a `conflict-pending` marker whose `conflict` object is
        empty is treated as an ordinary attempt: resolve answers NOT-COMMITTED and deletes it, so no conflict row is
        re-recorded and the claimant-conflict block is lost. markers.guard states that a conflict marker is never
        cleared without its row present. Observed: (0, NOT-COMMITTED), marker deleted, no row."""
        seq, head_self = self.head()
        ref = self.put_marker(seq + 1, "conflict-pending", "f" * 64, head_self, {"conflict": {}})
        c, r = self.resolve(seq + 1)
        self.assertNotEqual(r.get("outcome"), "NOT-COMMITTED", r)
        self.assertTrue(__import__("os").path.exists(ref.path))

    def test_disposition_for_a_marker_that_is_already_gone(self):
        c, r = self.resolve(7, "--attempt", "ab" * 16, "--disposition", "owner note")
        self.assertEqual((c, r["outcome"]), (0, "ALREADY-CLEARED"), r)


class PostCommitMarkerDelete(Fixture):
    def test_failed_marker_delete_after_commit_is_reported_as_marker_left_not_as_failure(self):
        before, _ = self.head()
        with mock.patch.object(markers, "delete", side_effect=OSError("injected delete failure")):
            c, r = self.intent(note="delete-fails")
        self.assertEqual((c, r["outcome"]), (0, "COMMITTED"), r)
        self.assertIn("marker_left", r)
        self.assertEqual(self.head()[0], before + 1)
        left = markers.list_markers(self.db, "j")
        self.assertEqual(len(left), 1)
        # the next write clears a lingering committing marker whose self matches the committed row
        c, r = self.intent(note="next")
        self.assertEqual((c, r["outcome"]), (0, "COMMITTED"), r)
        self.assertEqual(markers.list_markers(self.db, "j"), [])
        self.assertEqual([x["cleared"] for x in r["cleared"]], ["committed"])


class _RollbackFails:
    """Connection proxy whose ROLLBACK raises (txn give-up path)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def __getattr__(self, name):
        return getattr(self._c, name)

    def execute(self, sql, *a):
        if isinstance(sql, str) and sql.strip().upper() == "ROLLBACK":
            raise sqlite3.OperationalError("injected rollback failure")
        return self._c.execute(sql, *a)


class ResolveRollbackFailure(Fixture):
    @unittest.expectedFailure
    def test_resolve_reports_a_failing_rollback_instead_of_raising(self):
        """FINDING (low; needs a failing ROLLBACK): txn.resolve ends with an unguarded `dbm.rollback(conn)` (txn.py:484)
        while every other ROLLBACK in txn.py is wrapped into WfCommitUncertain or logged. A failing ROLLBACK there lets a
        raw sqlite3.OperationalError escape `wf resolve` (traceback, no result). Observed: OperationalError raised."""
        seq, head_self = self.head()
        self.put_marker(seq + 1, "committing", "f" * 64, head_self)
        real_connect = dbm.connect
        with mock.patch.object(txn.dbm, "connect", side_effect=lambda *a, **kw: _RollbackFails(real_connect(*a, **kw))):
            c, r = self.resolve(seq + 1)
        self.assertIn(c, (0, 1), r)


class GiveUpFailures(Fixture):
    """COMMIT stays busy past T_HELD_BOUND (a SHARED reader), then a step of the give-up sequence fails. Each case
    takes about T_HELD_BOUND (2 s) of wall time."""

    def test_aborting_marker_cannot_be_written(self):
        real = markers.write_atomic
        calls = []

        def flaky(ref, data):
            calls.append(data.get("state"))
            if data.get("state") == "aborting":
                raise OSError("injected aborting-marker failure")
            return real(ref, data)

        holder = self.hold_shared_reader()
        try:
            with mock.patch.object(markers, "write_atomic", side_effect=flaky):
                c, r = self.intent(note="giveup")
        finally:
            holder.execute("COMMIT")
            holder.close()
        self.assertEqual((c, r["code"]), (1, "commit-uncertain"), r)
        self.assertIn("give-up: could not mark own marker aborting", r["error"])
        self.assertEqual(calls, ["committing", "aborting"])
        # commit-uncertain triggers wf's one automatic `resolve`, which settles the surviving committing marker
        self.assertEqual(r["auto_resolve"]["outcome"], "NOT-COMMITTED", r)
        self.assertEqual(markers.list_markers(self.db, "j"), [])
        c, r2 = self.intent(note="after")
        self.assertEqual((c, r2["outcome"]), (0, "COMMITTED"), r2)

    def test_rollback_raises_during_give_up(self):
        real_connect = dbm.connect
        made = []

        def connect(*a, **kw):          # only the writer's connection is faulty; the automatic resolve gets a real one
            conn = real_connect(*a, **kw)
            made.append(conn)
            return _RollbackFails(conn) if len(made) == 1 else conn

        holder = self.hold_shared_reader()
        try:
            with mock.patch.object(txn.dbm, "connect", side_effect=connect):
                c, r = self.intent(note="rollback")
        finally:
            holder.execute("COMMIT")
            holder.close()
        self.assertEqual((c, r["code"]), (1, "commit-uncertain"), r)
        self.assertIn("give-up: ROLLBACK raised", r["error"])
        self.assertIn("auto_resolve", r)


class JobAbandonCommit(Fixture):
    def test_abandon_commit_busy_past_the_bound_is_commit_timeout(self):
        holder = self.hold_shared_reader()
        try:
            c, r = self.run_wf("job", "abandon", "j", "--reason", "r", role="coordinator", session="C1")
        finally:
            holder.execute("COMMIT")
            holder.close()
        self.assertEqual((c, r["code"]), (1, "commit-timeout"), r)

    def test_abandon_then_abandon_again(self):
        c, r = self.run_wf("job", "abandon", "j", "--reason", "r", role="coordinator", session="C1")
        self.assertEqual(c, 0, r)
        c2, r2 = self.run_wf("job", "abandon", "j", "--reason", "again", role="coordinator", session="C1")
        self.assertIn(c2, (0, 1))
        c3, r3 = self.intent(note="after-abandon")
        self.assertEqual((c3, r3["code"]), (1, "refused"), r3)
        self.assertIn("is abandoned", r3["error"])
