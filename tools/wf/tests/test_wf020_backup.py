"""WF-020 (Lazy): `wf backup` destination checks and `wf verify --restored` against hostile paths and files
(from-lazy WF-020 GAP-LIST.md sections 3-4). Current behaviour at 2dedc94 is asserted. Runs in the test_wf.py harness."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest

from test_wf import Base


class BackupDestination(Base):
    def setUp(self) -> None:
        super().setUp()
        self.new_job()
        self.ledger_dir = os.path.dirname(self.db)

    def backup(self, dest: str):
        return self.run_wf("backup", "j", "--to", dest, role="coordinator", session="C1")

    def test_destination_inside_the_ledger_directory_is_refused_directly_via_dotdot(self):
        for dest in (os.path.join(self.ledger_dir, "b.db"),
                     os.path.join(self.dir, "x", "..", "ledger", "c.db")):
            with self.subTest(dest=dest):
                c, r = self.backup(dest)
                self.assertEqual((c, r["code"], r["error"]), (1, "refused", "dest-inside-ledger"), r)
        self.assertEqual(sorted(os.listdir(self.ledger_dir)), ["l.db", "pending"])

    @unittest.skipUnless(sys.platform == "win32", "uses an NTFS junction")
    def test_destination_inside_the_ledger_directory_is_refused_through_a_junction(self):
        link = os.path.join(self.dir, "jn")
        made = subprocess.run(["cmd", "/c", "mklink", "/J", link, self.ledger_dir], capture_output=True, text=True)
        if made.returncode != 0:
            self.skipTest(f"junction not creatable: {made.stdout.strip()}")
        c, r = self.backup(os.path.join(link, "b.db"))
        self.assertEqual((c, r["code"], r["error"]), (1, "refused", "dest-inside-ledger"), r)
        self.assertEqual(sorted(os.listdir(self.ledger_dir)), ["l.db", "pending"])

    def test_existing_destination_and_missing_parent_are_refused(self):
        ok = os.path.join(self.dir, "ok.db")
        c, r = self.backup(ok)
        self.assertEqual(c, 0, r)
        c, r = self.backup(ok)
        self.assertEqual((c, r["error"]), (1, "dest-exists"), r)
        c, r = self.backup(os.path.join(self.dir, "nodir", "b.db"))
        self.assertEqual((c, r["error"]), (1, "dest-parent-missing"), r)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "nodir")))


class RestoredCopyChecks(Base):
    def setUp(self) -> None:
        super().setUp()
        self.new_job()
        self.copy = os.path.join(self.dir, "ok.db")
        c, r = self.run_wf("backup", "j", "--to", self.copy, role="coordinator", session="C1")
        self.assertEqual(c, 0, r)

    def verify_restored(self, *argv: str):
        return self.run_wf("verify", "--restored", *argv, role="coordinator", session="C1")

    def test_good_copy_verifies_clean(self):
        c, r = self.verify_restored(self.copy)
        self.assertEqual(c, 0, r)
        self.assertTrue(r["clean"] and r["readable"] and r["integrity"], r)
        self.assertEqual(r["heads"]["j"]["seq"], 1)      # contract row only

    def test_job_with_restored_requires_a_checkpoint(self):
        c, r = self.verify_restored(self.copy, "--job", "j")
        self.assertEqual((c, r["code"]), (2, "usage"), r)
        self.assertIn("applies only with --checkpoint", r["error"])

    def test_missing_path_and_directory_are_refused(self):
        for p in (os.path.join(self.dir, "missing.db"), self.dir):
            with self.subTest(path=p):
                c, r = self.verify_restored(p)
                self.assertEqual((c, r["code"]), (1, "refused"), r)
                self.assertIn("restored copy not found", r["error"])

    def test_file_that_is_not_sqlite_exits_zero_but_is_not_clean(self):
        """RESTORE-RUNBOOK.md:36: `--restored` exits 0 whenever it could run; the caller must read `clean`."""
        junk = os.path.join(self.dir, "junk.db")
        with open(junk, "w") as fh:
            fh.write("not sqlite")
        c, r = self.verify_restored(junk)
        self.assertEqual(c, 0, r)
        self.assertFalse(r["clean"])
        self.assertFalse(r["readable"])
        self.assertIn("file is not a database", r["error"])
