"""Unresolved-requirement register for T35 (owner correction 2026-10-15 ~14:50; issue #13, lane B2 Plexy).

This file is NOT part of the normal regression suite: its name does not match the discover pattern `test_*.py`.
Run it on its own:

    python -I -B -m unittest discover -s tools/wf/tests -p "requirements_*.py" -v

EMPTY REGISTER (2026-10-15): every T35 requirement assertion recorded here from commit 64df3cf has moved, assertion
unchanged, into normal discovery when the real mechanism made it pass on Windows:
  R1  -> test_t35_changeset.py (changed-set baseline in the contract event, A1; PR #21 + wiring PR #22)
  R2  -> test_t35_changeset.py (P4 = the fixed coordination set, A2; PR #21 + wiring PR #22)
  R3a -> test_t35_stray_files.py (export.check lists the export directory, A3; Zen's A3 delivery)
  R3b -> test_t35_verify_ledger_dir.py (verify lists the ledger directory, A3; PR #20)
The file is kept (not deleted as cleanup) so the separately runnable register stays discoverable; it now reports
`Ran 0 tests` / OK. Status is still reported in two lines: normal regression suite and this register. Closing these
four assertions does not by itself establish full foundation completion (see the coverage map addendum).
"""
from __future__ import annotations

import unittest

if __name__ == "__main__":
    unittest.main()
