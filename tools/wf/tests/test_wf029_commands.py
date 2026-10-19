"""WF-029 (Lazy): injected-failure tests for the wfcore/commands.py error branches that WF-020 left unexecuted
(from-lazy WF-020\\REMAINING-GAPS.md). Base: fix1/lazy-ids 96fb5c4. Current behaviour is asserted. Injection only on
fixtures/tmp ledgers: patched seams (changeset.*, dbm.*, observe.append_log, os.scandir, sqlite3.connect for the
second-writer probe, an absent backup module) or the WF_TEST_FAULTS fault seam. Runs inside the test_wf.py harness."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from unittest import mock

import test_wf
from test_wf import DECLARATIONS, Base

from wfcore import changeset, commands, observe
from wfcore import db as dbm
from wfcore.db import OpLog, WfUsage

RESULTS = {"criteria": [{"id": "AC1", "pass": True, "actual": "ok"}], "changed_paths": ["jobs/j/work/a.txt"],
           "environment": "disposable local scope", "limits": "none", **DECLARATIONS}
DELIVERED = '{"claims":[{"claim":"c","evidence":["e"]}]}'
TURN = "owner turn j"


class Fixture(Base):
    def setUp(self) -> None:
        super().setUp()
        self.new_job()
        self.activate()

    def coord(self, *argv: str):
        return self.run_wf(*argv, role="coordinator", session="C1")

    def accept(self, results: dict):
        return self.run_wf("accept", "j", "--results", json.dumps(results), role="reviewer", session="R1")

    def deliver(self) -> None:
        self.assertEqual(self.run_wf("append", "j", "--type", "delivered", "--content", DELIVERED)[0], 0)


def rows_without_baseline(real):
    """dbm.rows whose first (contract) row has its `baseline_manifest` removed: a job that predates capture."""
    def rows(conn, job, *a, **kw):
        out = [dict(r) for r in real(conn, job, *a, **kw)]
        if out and out[0]["type"] == "contract":
            c = json.loads(out[0]["content"])
            c.pop("baseline_manifest", None)
            out[0]["content"] = json.dumps(c)
        return out
    return rows


# ------------------------------------------------------------------------------------------- job new / grant

class JobNewAndGrant(Base):
    def test_baseline_problems_refuse_job_new(self):
        """commands.py:229 (rows 2 and 70): changeset.baseline reporting a problem -> fail closed, no job."""
        with mock.patch.object(commands.changeset, "baseline", return_value={"problems": ["injected walk problem"]}):
            c, r = self.run_wf("job", "new", "j", "--contract", self.cpath, role="coordinator", session="C1")
        self.assertEqual((c, r["code"]), (1, "refused"), r)
        self.assertIn("changed-set baseline could not be captured (fail closed, no event recorded): injected walk problem",
                      r["error"])
        c, r = self.run_wf("status", "j", role="coordinator", session="C1")
        self.assertEqual((c, r["code"]), (1, "refused"), r)
        self.assertIn("ledger not found", r["error"])                 # the refusal created nothing, not even a ledger
        self.assertFalse(os.path.exists(self.db))

    def test_proposed_grant_row_not_readable_after_commit(self):
        """commands.py:310 (row 3, `pragma: no cover`): the just-committed grant row reads back as missing."""
        self.new_job()
        with mock.patch.object(commands.dbm, "row_at", return_value=None):
            c, r = self.run_wf("grant", "propose", "j", "--grant", self.gpath, role="coordinator", session="C1")
        self.assertEqual((c, r["code"]), (1, "refused"), r)
        self.assertEqual(r["error"], "proposed grant row j/2 not readable")


# ------------------------------------------------------------------------------------------- verify

class VerifyBranches(Fixture):
    def test_checkpoint_without_a_job_at_the_api(self):
        """commands.py:701 (row 4): the CLI refuses earlier (checkpointcmp.parse); the API keeps its own guard."""
        with self.assertRaises(WfUsage) as cm:
            commands.verify(self.db, None, checkpoint=(1, "a" * 64))
        self.assertIn("--checkpoint requires a job argument", str(cm.exception))

    def test_unreadable_ledger_directory_is_reported(self):
        """commands.py:775 (row 5): os.scandir on the ledger directory fails -> problem, clean false."""
        real = os.scandir
        ledger_dir = os.path.normcase(os.path.dirname(self.db))

        def scandir(p="."):
            if os.path.normcase(os.path.abspath(p)) == ledger_dir:
                raise PermissionError("injected")
            return real(p)

        with mock.patch.object(commands.os, "scandir", side_effect=scandir):
            c, r = self.coord("verify", "j")
        self.assertEqual(c, 0, r)
        self.assertEqual(r["ledger_directory"]["problems"], ["ledger directory unreadable: PermissionError"])
        self.assertFalse(r["clean"])


# ------------------------------------------------------------------------------------------- accept

class AcceptBranches(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.deliver()

    def test_failed_and_missing_criteria(self):
        """commands.py:947 (row 10)."""
        c, r = self.accept(dict(RESULTS, criteria=[{"id": "AC1", "pass": False, "actual": "no"}]))
        self.assertEqual((c, r["decision"]), (0, "rejected"), r)
        self.assertIn("criteria failed or missing: AC1", r["reasons"])

    def test_results_naming_unknown_criteria(self):
        """commands.py:950 (row 11)."""
        c, r = self.accept(dict(RESULTS, criteria=RESULTS["criteria"] + [{"id": "AC9", "pass": True}]))
        self.assertEqual((c, r["decision"]), (0, "rejected"), r)
        self.assertIn("results name criteria not in the contract: AC9", r["reasons"])

    def test_no_boundary_at_accept_fails_closed(self):
        """commands.py:878 (row 6)."""
        with mock.patch.object(commands.changeset, "boundary_root", return_value=None):
            c, r = self.accept(RESULTS)
        self.assertEqual((c, r["decision"]), (0, "rejected"), r)
        self.assertTrue(any(x.startswith("changed-set boundary cannot be established") for x in r["reasons"]), r)

    def test_job_without_baseline_evidence_fails_closed(self):
        """commands.py:887 (row 7)."""
        with mock.patch.object(commands.dbm, "rows", side_effect=rows_without_baseline(dbm.rows)):
            c, r = self.accept(RESULTS)
        self.assertEqual((c, r["decision"]), (0, "rejected"), r)
        self.assertIn(commands._NO_BASELINE, r["reasons"])

    def test_incomplete_comparison_fails_closed(self):
        """commands.py:892 (row 8)."""
        cmp = {"added": [], "modified": [], "deleted": [], "problems": ["injected compare problem"], "limitations": []}
        with mock.patch.object(commands.changeset, "compare", return_value=cmp):
            c, r = self.accept(RESULTS)
        self.assertEqual((c, r["decision"]), (0, "rejected"), r)
        self.assertIn("changed-set comparison incomplete (fail closed): injected compare problem", r["reasons"])


# ------------------------------------------------------------------------------------------- reconcile

class ReconcileBranches(Fixture):
    def test_no_boundary_refuses_reconcile(self):
        """commands.py:1055 (row 14)."""
        with mock.patch.object(commands.changeset, "boundary_root", return_value=None):
            c, r = self.coord("reconcile", "j")
        self.assertEqual((c, r["code"]), (1, "refused"), r)
        self.assertIn("changed-set boundary cannot be established", r["error"])

    def test_job_without_baseline_refuses_reconcile(self):
        """commands.py:1068 (row 16)."""
        with mock.patch.object(commands.dbm, "rows", side_effect=rows_without_baseline(dbm.rows)):
            c, r = self.coord("reconcile", "j")
        self.assertEqual((c, r["code"], r["error"]), (1, "refused", commands._NO_BASELINE), r)


# ------------------------------------------------------------------------------------------- second-writer probe

class _Probe:
    def __init__(self, begin_exc: BaseException | None) -> None:
        self._exc = begin_exc

    def execute(self, sql, *a):
        if sql == "BEGIN IMMEDIATE" and self._exc is not None:
            raise self._exc
        return None

    def close(self) -> None:
        return None


class SecondWriterProbe(Fixture):
    """`recover begin` under WF_TEST_FAULTS=recover-second-writer opens a second connection with
    sqlite3.connect(db, timeout=0, isolation_level=None) (no autocommit/uri, unlike every other connection)."""

    def _begin(self, probe):
        real = sqlite3.connect

        def connect(*a, **kw):
            if kw.get("timeout") == 0 and "autocommit" not in kw and not kw.get("uri") and len(a) == 1:
                return probe()
            return real(*a, **kw)

        with mock.patch.dict(os.environ, {"WF_TEST_FAULTS": "recover-second-writer"}), \
                mock.patch.object(commands.sqlite3, "connect", side_effect=connect):
            return self.coord("recover", "begin", "j", "--reason", "r", "--turn", TURN)

    def _nothing_appended(self, before: int) -> None:
        conn = dbm.connect(self.db, create=False)
        try:
            self.assertEqual(dbm.head(conn, "j")[0], before)
        finally:
            conn.close()

    def _head(self) -> int:
        conn = dbm.connect(self.db, create=False)
        try:
            return dbm.head(conn, "j")[0]
        finally:
            conn.close()

    def test_probe_cannot_open_a_second_connection(self):
        """commands.py:1154 (row 18)."""
        before = self._head()

        def fail():
            raise sqlite3.OperationalError("unable to open database file")
        c, r = self._begin(fail)
        self.assertEqual((c, r["code"]), (1, "refused"), r)
        self.assertIn("second-writer probe could not open a second connection", r["error"])
        self._nothing_appended(before)

    def test_probe_fails_for_another_reason(self):
        """commands.py:1166 (row 19)."""
        before = self._head()
        c, r = self._begin(lambda: _Probe(sqlite3.OperationalError("disk I/O error")))
        self.assertEqual((c, r["code"], r["second_writer"]), (1, "refused", "error"), r)
        self.assertIn("second-writer probe failed for another reason (disk I/O error)", r["error"])
        self._nothing_appended(before)

    def test_probe_that_obtains_the_reservation_is_reported_as_broken(self):
        """commands.py:1173 (row 20)."""
        before = self._head()
        c, r = self._begin(lambda: _Probe(None))
        self.assertEqual((c, r["code"], r["second_writer"]), (1, "refused", "acquired"), r)
        self.assertIn("the single-writer reservation did not hold", r["error"])
        self._nothing_appended(before)


# ------------------------------------------------------------------------------------------- backup / observe

class BackupAndObserve(Fixture):
    def test_backup_of_a_ledger_file_that_is_not_sqlite(self):
        """commands.py:1203 (row 21)."""
        db = os.path.join(self.dir, "junk", "ledger", "l.db")
        os.makedirs(os.path.dirname(db))
        with open(db, "w") as fh:
            fh.write("not a database")
        c, r = test_wf.wf.run(["--db", db, "--role", "coordinator", "--session", "C1", "backup", "j", "--to",
                               os.path.join(self.dir, "b.db")])
        self.assertEqual((c, r["code"]), (1, "refused"), r)
        self.assertIn("ledger not readable (DatabaseError: file is not a database)", r["error"])

    def test_backup_module_absent(self):
        """commands.py:1248 (row 22): a tree without wfcore/backup.py refuses backup and verify --restored."""
        import wfcore
        saved = wfcore.__dict__.pop("backup", None)  # `from . import backup` returns a bound attribute first
        try:
            with mock.patch.dict(sys.modules, {"wfcore.backup": None}):
                c, r = self.coord("backup", "j", "--to", os.path.join(self.dir, "b.db"))
                c2, r2 = self.coord("verify", "--restored", self.db)
        finally:
            if saved is not None:
                wfcore.backup = saved
        for code, res in ((c, r), (c2, r2)):
            self.assertEqual((code, res["code"]), (1, "refused"), res)
            self.assertIn("backup module not present on this tree", res["error"])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "b.db")))

    def test_observation_log_failure_refuses(self):
        """commands.py:1558 (row 28)."""
        with mock.patch.object(commands.observe, "append_log", side_effect=OSError("injected log failure")):
            c, r = self.coord("observe", "j")
        self.assertEqual((c, r["code"]), (1, "refused"), r)
        self.assertEqual(r["error"], "observation log not written: injected log failure")
