"""A1/A2 wiring (White, owner grant 2026-10-15 ~15:05 "T35 REQUIREMENT REPAIRS A1-A3 + T28"): the CLI-level
contract between `wf job new` / `wf accept` and `wfcore.changeset` (Plexy, issue #13).

Requirement: FIRST-RELEASE-SPEC.md section 4.3 (detection claims cover exactly the monitored boundary) and
section 4.4 (`wf accept` computes the changed set against a baseline captured at `wf job new`). Owner rules:
capture the baseline before work starts; store it chain-protected (here: in the contract event's content); when
the boundary cannot be established, report that exact blocker and scan nothing; fail closed.

Frozen interface (issue #13): `changeset.boundary_root(db)` is the parent of a directory named `ledger`, else
None. These tests exercise only the wiring; the end-to-end R1/R2 assertions move out of
`requirements_t35_unmet.py` when Plexy's module makes them pass through this wiring.
"""
from __future__ import annotations

import os
import sqlite3
import unittest
from contextlib import closing

from test_wf import Base  # noqa: E402

BLOCKER = "changed-set boundary cannot be established"


class JobNewBaselineWiring(Base):
    def test_job_new_refuses_when_the_ledger_directory_is_not_named_ledger(self):
        """A ledger at `ledger2/l.db` has no establishable monitored boundary: `wf job new` refuses with the exact
        blocker, records no contract and no event, and scans nothing (fail closed, no arbitrary scan)."""
        self.db = os.path.join(self.dir, "ledger2", "l.db")
        code, res = self.run_wf("job", "new", "j", "--contract", self.cpath, role="coordinator", session="C1")
        self.assertNotEqual(code, 0, res)
        self.assertIn(BLOCKER, res.get("error", ""), res)
        if os.path.exists(self.db):
            with closing(sqlite3.connect(self.db)) as con:
                events = con.execute("SELECT count(*) FROM events").fetchone()[0]
                contracts = con.execute("SELECT count(*) FROM contracts").fetchone()[0]
            self.assertEqual((events, contracts), (0, 0), "refusal must leave no record")

    def test_job_new_captures_the_baseline_in_the_contract_event(self):
        """With a `ledger/` directory the baseline is captured at job creation and rides in the contract event's
        content beside the contract (chain-protected); the result reports how many entries were captured."""
        res = self.new_job()
        self.assertIsInstance(res.get("baseline_entries"), int, res)
        with closing(sqlite3.connect(self.db)) as con:
            row = con.execute("SELECT content FROM events WHERE type = 'contract' ORDER BY seq LIMIT 1").fetchone()
        self.assertIsNotNone(row)
        self.assertIn('"baseline_manifest"', row[0])
        self.assertIn('"entries"', row[0])


if __name__ == "__main__":
    unittest.main()
