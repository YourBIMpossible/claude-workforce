"""Unresolved-requirement record for T35 (owner correction 2026-10-15 ~14:50; issue #13, lane B2 Plexy).

This file is NOT part of the normal regression suite: its name does not match the discover pattern `test_*.py`.
Run it on its own:

    python -I -B -m unittest discover -s tools/wf/tests -p "requirements_*.py" -v

It is EXPECTED to report FAILED (failures=1) until the mechanism lands: R3a (export.check listing, Zen). R3b moved
to test_t35_verify_ledger_dir.py when the verify ledger-directory listing landed (A3); R1 and R2 moved to
test_t35_changeset.py with White's A1/A2 wiring (changed-set baseline in the contract event payload; P4 = the fixed
coordination set), assertions unchanged. Each remaining test is the requirement assertion from commit 64df3cf with
the `expectedFailure` decorator removed and the assertion unchanged. A test here PASSING means the mechanism
landed: the test then moves into the normal suite in the same PR that lands the mechanism. Nothing here is
weakened, skipped or relabelled.

Status is reported in two lines: normal regression suite (`test_*.py`) and unresolved requirement tests (this file,
1, T35). The `test_LIMIT_*` observation tests in test_t35_remaining_requirements.py pin the PRESENT behaviour; they
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


if __name__ == "__main__":
    unittest.main()
