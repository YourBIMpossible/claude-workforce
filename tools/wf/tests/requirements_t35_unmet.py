"""Unresolved-requirement record for T35 (owner correction 2026-10-15 ~14:50; issue #13, lane B2 Plexy).

This file is NOT part of the normal regression suite: its name does not match the discover pattern `test_*.py`.
Run it on its own:

    python -I -B -m unittest discover -s tools/wf/tests -p "requirements_*.py" -v

It is EXPECTED to report FAILED (failures=4) until the mechanisms land. Each test is the requirement assertion from
commit 64df3cf (tools/wf/tests/test_t35_remaining_requirements.py at that revision) with the `expectedFailure`
decorator removed and the assertion unchanged. A test here PASSING means the mechanism landed: the test then moves
into the normal suite in the same PR that lands the mechanism. Nothing here is weakened, skipped or relabelled.

Status is reported in two lines: normal regression suite (`test_*.py`) and unresolved requirement tests (this file,
4, T35). The `test_LIMIT_*` observation tests in test_t35_remaining_requirements.py pin the PRESENT behaviour; they
do not close any requirement below.

Fixtures and helpers are those of test_t35_remaining_requirements (`_Flow`: fresh WF_TEST_TMP scratch, contract
variant, job + grant + delivered claim, CLI accept and verify through test_wf.Base.run_wf).
"""
from __future__ import annotations

import json
import os
import unittest

from test_t35_remaining_requirements import _Flow  # noqa: E402
from test_wf import CONTRACT  # noqa: E402
from wfcore import paths  # noqa: E402


class R1UndeclaredP2ManifestDeletion(_Flow):
    contract = dict(CONTRACT, baseline={"manifest": [{"path": "jobs/j/work/gone.txt", "sha256": "a" * 64},
                                                     {"path": "tools/wf/wfcore/db.py", "sha256": "b" * 64}],
                                        "git_revision": None, "uncommitted_state": None})

    def test_R1_undeclared_P2_manifest_deletion_is_rejected(self):
        """Requirement: FIRST-RELEASE-SPEC.md section 4.4 (line 209) "It computes the set of changed paths inside the
        monitored boundary"; rule 1 (line 220) "a missing P2/P3 file is a deletion violation"; result list (line
        223); T35 row (line 310) "a manifest file missing from P1".
        Expected: a P2 manifest file (tools/wf/wfcore/db.py, in the baseline manifest, absent on disk, NOT supplied
        by the reviewer) makes `wf accept` decide "rejected".
        Gap: wfcore/commands.py line 564 classifies only the reviewer-supplied `changed_paths`; no changed-set
        computation (baseline manifest versus filesystem inside the boundary) exists anywhere.
        Observed at rev12 1b005a42f204 (main 17f7ef2) and at this head: decision "accepted", classes.results [] ->
        AssertionError: 'accepted' != 'rejected'.
        OPEN - not closed by test_LIMIT_undeclared_P2_manifest_deletion_is_accepted in
        test_t35_remaining_requirements.py."""
        self.start()
        self.assertFalse(os.path.exists(os.path.join(self.dir, "tools", "wf", "wfcore", "db.py")))
        r = self.accept([], "R1d")
        self.assertEqual(r["decision"], "rejected", r)


class R2SuppliedCoordinationUpdate(_Flow):
    contract = dict(CONTRACT, watch=["runtime/gate/**"])

    def test_R2_supplied_coordination_update_is_accepted_as_P4(self):
        """Requirement: FIRST-RELEASE-SPEC.md section 4.4 P4 row (line 216): "Ledger database and its journal,
        runtime/gate/gate.db, jobs/<job>/export/, ... Allowed to change; not compared by hash against baseline.
        Checked instead by wf verify"; rule 4 (line 223) result "coordination (P4, verified by verify)".
        Expected: a reviewer who truthfully supplies jobs/j/export/head.json and ledger/l.db as changed (contract
        without a ledger watch) gets "accepted".
        Gap: wfcore/pathpolicy.py line 204 defines P4 as "outside every declared scope" and `violations` rejects
        every P4 entry; no fixed coordination set exists.
        Observed at rev12 1b005a42f204 (main 17f7ef2) and at this head: decision "rejected", P4 =
        ["jobs/j/export/head.json", "ledger/l.db"], reason "changed paths outside scope (P4) or excluded (P5):
        jobs/j/export/head.json, ledger/l.db" -> AssertionError: 'rejected' != 'accepted'.
        OPEN - not closed by test_LIMIT_supplied_coordination_update_is_rejected_as_P4 in
        test_t35_remaining_requirements.py."""
        self.start()
        r = self.accept(["jobs/j/work/a.txt", "jobs/j/export/head.json", "ledger/l.db"], "R2d")
        self.assertEqual(r["decision"], "accepted", r)


class R3UnexpectedFilesInLedgerAndRuntimeFolders(_Flow):
    contract = dict(CONTRACT, watch=["runtime/gate/**"])

    def test_R3a_added_stray_file_in_the_export_folder_is_reported_by_verify(self):
        """Requirement: FIRST-RELEASE-SPEC.md section 4.4 P4 row (line 216): "Added or deleted files in the
        ledger/runtime folders other than the listed ones -> violation"; T35 row (line 310) "unexpected files in
        ledger/runtime folders".
        Expected: after an unlisted file jobs/j/export/stray.txt is added, `wf verify` reports clean false and an
        export problem naming stray.txt.
        Gap: wfcore/export.py check() (line 172 at rev12 1b005a42f204) walks the manifest entries only and never
        lists the directory.
        Observed at rev12 1b005a42f204 (main 17f7ef2) and at this head: clean true, export problems [] ->
        AssertionError: True is not false.
        OPEN - not closed by test_LIMIT_added_stray_file_in_the_export_folder_is_unreported_by_verify in
        test_t35_remaining_requirements.py."""
        self.start()
        stray = os.path.join(paths.export_dir(self.db, "j"), "stray.txt")
        with open(stray, "w", encoding="utf-8") as fh:
            fh.write("unexpected\n")
        v = self.verify()
        self.assertFalse(v["clean"], v)
        self.assertTrue(any("stray.txt" in p for p in v["jobs"]["j"]["export"]), v)

    def test_R3b_added_stray_file_beside_the_ledger_is_a_violation(self):
        """Requirement: FIRST-RELEASE-SPEC.md section 4.4 P4 row (line 216): "Added or deleted files in the
        ledger/runtime folders other than the listed ones -> violation".
        Expected: after ledger/stray.db is added beside the ledger database, `wf verify` reports clean false and
        names stray.db.
        Gap: wfcore/commands.py verify() (line 430 ff.) checks schema, integrity, chain, export and markers and
        never lists the ledger directory; no command observes the ledger folder.
        Observed at rev12 1b005a42f204 (main 17f7ef2) and at this head: clean true, "stray.db" absent from the
        result -> AssertionError: True is not false.
        OPEN - not closed by test_LIMIT_added_stray_file_in_the_ledger_folder_is_unobserved_by_verify in
        test_t35_remaining_requirements.py."""
        self.start()
        stray = os.path.join(paths.ledger_dir(self.db), "stray.db")
        with open(stray, "w", encoding="utf-8") as fh:
            fh.write("unexpected\n")
        v = self.verify()
        self.assertFalse(v["clean"], v)
        self.assertIn("stray.db", json.dumps(v))


if __name__ == "__main__":
    unittest.main()
