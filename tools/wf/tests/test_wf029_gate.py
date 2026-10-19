"""WF-029 (Lazy): injected-failure tests for the gatecore.decide fail-closed paths and the reconcile.py validator
row that WF-020 left unexecuted (from-lazy WF-020\\REMAINING-GAPS.md rows 55, 77-80). Base: fix1/lazy-ids 96fb5c4.
Fixture ledgers are built with the real CLI (test_gate_decide._Gate); decide runs in-process; nothing is registered."""
from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from test_gate_decide import _Gate

from wfcore import gatecore, reconcile
from wfcore.db import WfRejected


class DecideFailClosed(_Gate):
    def setUp(self) -> None:
        super().setUp()
        self.explicit()
        self.write = self.p("jobs/j/work/a.txt")

    def test_target_paths_reporting_a_malformed_payload_denies(self):
        """gatecore.py:344 (row 77): rule 0 passed, then target_paths answers malformed-payload."""
        with mock.patch.object(gatecore, "target_paths", return_value=([], "malformed-payload")):
            self.deny(self.write, "malformed-payload")

    def test_ledger_that_cannot_be_opened_read_only_denies(self):
        """gatecore.py:216 (row 78): sqlite3.connect(mode=ro) raises -> ledger-unreadable."""
        with mock.patch.object(gatecore.sqlite3, "connect", side_effect=sqlite3.OperationalError("unable to open")):
            self.deny(self.write, "ledger-unreadable")

    def test_identity_check_that_cannot_stat_the_ledger_denies(self):
        """gatecore.py:357 (row 79): expected_identity given, ledger_identity raises OSError -> ledger-unreadable."""
        with mock.patch.object(gatecore, "ledger_identity", side_effect=OSError("injected stat failure")):
            self.deny(self.write, "ledger-unreadable", expected_identity={"dev": 1, "ino": 1, "size": 0})

    def test_sql_error_while_reading_the_chain_tail_denies(self):
        """gatecore.py:388 (row 80): a busy error -> ledger-locked; any other sqlite error -> ledger-unreadable."""
        for exc, reason in ((sqlite3.OperationalError("database is locked"), "ledger-locked"),
                            (sqlite3.DatabaseError("database disk image is malformed"), "ledger-unreadable")):
            with self.subTest(reason=reason), mock.patch.object(gatecore.dbm, "head", side_effect=exc):
                r = self.deny(self.write, reason)
                self.assertEqual(r["jobs"], ["j"])


class RecoveryClosedShape(unittest.TestCase):
    def test_chain_head_must_be_seq_and_self(self):
        """reconcile.py:508 (row 55): recovery-closed content whose chain_head is not {seq, self}; reached only past
        the open-recovery, acknowledged and `acknowledged`-list checks."""
        rows = [{"seq": 1, "self": "a" * 64, "type": "contract", "content": "{}"},
                {"seq": 2, "self": "b" * 64, "type": "recovery-opened",
                 "content": '{"exceptions": [], "owner_turn": "turn j", "reason": "r"}'}]
        for head in ({"seq": "2", "self": "b" * 64}, {"seq": 2}, "2"):
            with self.subTest(head=head), self.assertRaises(WfRejected) as cm:
                reconcile.validate_recover_close(rows, job="j", content={"opened_seq": 2, "acknowledged": [],
                                                                          "chain_head": head,
                                                                          "owner_turn": "turn j"})
            self.assertIn("chain_head must be {seq, self}", str(cm.exception))
