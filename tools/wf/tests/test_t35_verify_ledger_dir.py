"""A3 wiring (White, owner grant 2026-10-15 ~15:05 "T35 REQUIREMENT REPAIRS A1-A3 + T28"): `wf verify` lists the
ledger directory against the legitimate set and reports everything else; it never deletes, moves, normalizes or
follows anything.

Requirement: FIRST-RELEASE-SPEC.md section 4.4 P4 row: "Added or deleted files in the ledger/runtime folders other
than the listed ones -> violation". Legitimate in the ledger directory: the database, its `-journal`, `pending/`.
A neighbouring SQLite database (and its journal) sharing the directory is reported under `ledger_directory.neighbors`,
informational, never stray (owner decision: a shared directory is a legitimate layout).

R3b moved here from requirements_t35_unmet.py (PR #18) in the same delivery as the mechanism; its assertions are
unchanged. The export-folder half (R3a) stays open until Zen's export.check listing (issue #7) lands.
"""
from __future__ import annotations

import json
import os
import sqlite3
import unittest

from test_t35_remaining_requirements import _Flow  # noqa: E402
from test_wf import CONTRACT, log  # noqa: E402
from wfcore import paths  # noqa: E402

STRAY = "unexpected ledger-directory file: "


class VerifyLedgerDirectoryListing(_Flow):
    contract = dict(CONTRACT, watch=["runtime/gate/**"])

    def _write(self, name: str, data: bytes = b"unexpected\n") -> str:
        p = os.path.join(paths.ledger_dir(self.db), name)
        with open(p, "wb") as fh:
            fh.write(data)
        return p

    def test_R3b_added_stray_file_beside_the_ledger_is_a_violation(self):
        """Requirement: FIRST-RELEASE-SPEC.md section 4.4 P4 row (line 216): "Added or deleted files in the
        ledger/runtime folders other than the listed ones -> violation".
        Expected: after ledger/stray.db is added beside the ledger database, `wf verify` reports clean false and
        names stray.db. (Assertions as in requirements_t35_unmet.py R3b; moved on landing.)
        Mechanism: commands._ledger_dir_listing, run by verify before the per-job checks."""
        self.start()
        stray = os.path.join(paths.ledger_dir(self.db), "stray.db")
        with open(stray, "w", encoding="utf-8") as fh:
            fh.write("unexpected\n")
        v = self.verify()
        self.assertFalse(v["clean"], v)
        self.assertIn("stray.db", json.dumps(v))
        self.assertEqual(v["ledger_directory"]["problems"], [STRAY + "stray.db"])

    def test_clean_ledger_directory_has_no_directory_problems(self):
        """The legitimate set alone (database, `-journal`, pending/) keeps clean true and `directory` empty."""
        self.start()
        self._write("l.db-journal", b"")
        os.makedirs(os.path.join(paths.ledger_dir(self.db), "pending"), exist_ok=True)
        v = self.verify()
        self.assertTrue(v["clean"], v)
        self.assertEqual(v["ledger_directory"]["problems"], [])
        self.assertEqual(v["ledger_directory"]["neighbors"], [])

    def test_neighbouring_sqlite_ledger_and_its_journal_are_informational_not_stray(self):
        """A second SQLite database (and its `-journal`) sharing the directory is another ledger: listed under
        `neighbor_ledgers`, not a directory problem, clean unaffected (shared-directory layout, as the real
        ledger/ holds demo.db and demo-002.db side by side)."""
        self.start()
        other = os.path.join(paths.ledger_dir(self.db), "other.db")
        con = sqlite3.connect(other)
        con.execute("create table t (x)")
        con.commit()
        con.close()
        self._write("other.db-journal", b"")
        v = self.verify()
        self.assertTrue(v["clean"], v)
        self.assertEqual(v["ledger_directory"]["problems"], [])
        self.assertEqual(v["ledger_directory"]["neighbors"], ["other.db", "other.db-journal"])

    def test_journal_without_its_database_is_stray(self):
        """`ghost.db-journal` with no `ghost.db` beside it is not a neighbour's journal: reported."""
        self.start()
        self._write("ghost.db-journal", b"")
        v = self.verify()
        self.assertFalse(v["clean"], v)
        self.assertEqual(v["ledger_directory"]["problems"], [STRAY + "ghost.db-journal"])

    def test_non_sqlite_db_named_file_is_stray(self):
        """The `.db` suffix is not the test: a non-SQLite file called `fake.db` is stray (header check)."""
        self.start()
        self._write("fake.db", b"not a database\n")
        v = self.verify()
        self.assertFalse(v["clean"], v)
        self.assertEqual(v["ledger_directory"]["problems"], [STRAY + "fake.db"])
        self.assertEqual(v["ledger_directory"]["neighbors"], [])

    def test_stray_directory_is_reported_and_not_descended(self):
        """A stray directory is named with a trailing `/`; its contents are never listed or read."""
        self.start()
        d = os.path.join(paths.ledger_dir(self.db), "scratch")
        os.makedirs(d)
        with open(os.path.join(d, "inner-secret.txt"), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        v = self.verify()
        self.assertFalse(v["clean"], v)
        self.assertEqual(v["ledger_directory"]["problems"], [STRAY + "scratch/"])
        self.assertNotIn("inner-secret", json.dumps(v))

    def test_symlink_in_ledger_directory_is_reported_and_not_followed(self):
        """A link is named with a trailing `@` whatever it points at; the target is never opened (a link to a real
        SQLite file does not become a neighbour). Skipped where the account cannot create symlinks."""
        self.start()
        target = os.path.join(self.dir, "outside.db")
        con = sqlite3.connect(target)
        con.execute("create table t (x)")
        con.commit()
        con.close()
        link = os.path.join(paths.ledger_dir(self.db), "link.db")
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink not creatable here: {exc.__class__.__name__}")
        v = self.verify()
        self.assertFalse(v["clean"], v)
        self.assertEqual(v["ledger_directory"]["problems"], [STRAY + "link.db@"])
        self.assertEqual(v["ledger_directory"]["neighbors"], [])

    def test_verify_reports_only_and_leaves_the_directory_untouched(self):
        """Report, never repair: after two verify runs the stray file, its bytes and the directory listing are
        unchanged (no deletion, move or normalization)."""
        self.start()
        stray = self._write("stray.txt", b"keep me\n")
        before = sorted(os.listdir(paths.ledger_dir(self.db)))
        v1 = self.verify()
        v2 = self.verify()
        self.assertFalse(v1["clean"] or v2["clean"], (v1, v2))
        self.assertEqual(sorted(os.listdir(paths.ledger_dir(self.db))), before)
        with open(stray, "rb") as fh:
            self.assertEqual(fh.read(), b"keep me\n")
        log(f"{self._testMethodName}-listing", {"before": before, "directory": v2["ledger_directory"]["problems"]})


if __name__ == "__main__":
    unittest.main()
