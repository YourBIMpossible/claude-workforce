"""WF-021 step 3 (MyBuddy), re-based on FIX1 (`fix1/integration` dfd6e85, WF-021 review F1 / Lazy WF-038):
case-sensitivity behaviour of tools/wf pinned as it is AFTER FIX1.

Rows refer to fixtures/results/wf021-mybuddy/PLATFORM-REVIEW.md. The pre-FIX1 observations (the WF-021 findings) are
kept on branch `wf021/mybuddy` 88ea43f as the historical record; each test here names the FIX1 item that changed it.
Skips are per test and name the exact rule they need (review L38-M1): only rows 16 and 17 depend on a case-sensitive
filesystem; rows 18, 19, 20 and 25 are string/path logic and run everywhere, with the expectation that follows the
platform's case rule (`pathpolicy.CASE_FOLDING`, FIX1 P1).
"""
from __future__ import annotations

import os
import sys
import unittest

from test_wf import CONTRACT, Base  # noqa: E402  (sets sys.path; fresh WF_TEST_TMP)
from wfcore import changeset, grant, pathpolicy, reconcile  # noqa: E402

CASE_SENSITIVE_FS = sys.platform.startswith("linux")
SKIP_CASE_FS = "needs a case-sensitive filesystem (Docs/ and docs/ as two directories); NTFS folds case"


def _write(path: str, text: str = "x") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "x", encoding="utf-8") as fh:
        fh.write(text)


class Case(Base):
    def contract(self, **scopes) -> dict:
        c = dict(CONTRACT)
        c.update({"read_scope": [], "watch": [], "presence_only": [], "write_scope": ["jobs/j/work/**"]})
        c.update(scopes)
        return c

    @unittest.skipUnless(CASE_SENSITIVE_FS, SKIP_CASE_FS)
    def test_row16_upper_case_scope_glob_walks_the_directory_as_written(self):
        """Row #16 after FIX1 P1 (WF-021 F1 fixed): scope `Docs/**` walks `Docs/` itself; a separate lower-case
        `docs2/` is captured under its own scope. Before FIX1 the glob was looked up as `docs` and nothing was walked."""
        os.makedirs(os.path.join(self.dir, "ledger"))
        _write(os.path.join(self.dir, "Docs", "a.txt"))
        _write(os.path.join(self.dir, "docs2", "a.txt"))
        b = changeset.baseline(self.dir, self.contract(read_scope=["Docs/**"]))
        self.assertEqual((list(b["entries"]), b["problems"]), (["Docs/a.txt"], []))
        control = changeset.baseline(self.dir, self.contract(read_scope=["docs2/**"]))
        self.assertEqual(list(control["entries"]), ["docs2/a.txt"])

    @unittest.skipUnless(CASE_SENSITIVE_FS, SKIP_CASE_FS)
    def test_row17_reconcile_keeps_the_written_case_and_hashes_the_file(self):
        """Row #17 after FIX1 P1 (WF-021 F2 fixed): `jobs/j/work/New.txt` keeps its case for I/O, so the current hash
        is read. Before FIX1 it was related as `.../new.txt` and `sha_now` was None while the file existed."""
        _write(os.path.join(self.dir, "jobs", "j", "work", "New.txt"), "y")
        rel = reconcile._relate("jobs/j/work/New.txt")
        self.assertEqual(rel, "jobs/j/work/New.txt")
        self.assertIsNotNone(reconcile._sha_now(self.dir, rel))

    def test_row18_reconcile_relation_keeps_case_on_every_platform(self):
        """Row #18 after FIX1 P1: `_relate` no longer folds case on any platform (it is the I/O form; comparison uses
        `pathpolicy.fold`), so two names differing only in case relate to two different strings. String logic: runs
        everywhere (review L38-M1)."""
        self.assertNotEqual(reconcile._relate("jobs/j/work/A.txt"), reconcile._relate("jobs/j/work/a.txt"))
        self.assertEqual(reconcile._relate("jobs\\j\\work\\A.txt"), "jobs/j/work/A.txt")

    def test_row19_case_variant_directory_follows_the_platform_case_rule(self):
        """Row #19 after FIX1 P1 (WF-021 F5 decided by P1): on a case-folding platform (Windows) `JOBS/J/WORK/a.txt` is
        the same directory as the write scope and stays P1 allowed; on a case-sensitive one (Linux) it is another
        directory and is now an unclassified violation (before FIX1 it was P1 allowed on Linux too). Runs everywhere;
        on Windows it documents the Windows meaning (review L38-M1)."""
        classes = pathpolicy.classify(["JOBS/J/WORK/a.txt"], dict(CONTRACT))
        if pathpolicy.CASE_FOLDING:
            self.assertEqual((classes["P1"], classes["results"][0]["result"]), (["JOBS/J/WORK/a.txt"], "allowed"))
        else:
            self.assertEqual((classes["unclassified"], classes["results"][0]["result"]),
                             (["JOBS/J/WORK/a.txt"], "violation"))

    def test_row20_grant_exclusion_follows_the_platform_case_rule_and_resources_stay_case_sensitive(self):
        """Row #20 after FIX1 P3 (WF-021 F4 fixed in `grant._excluded`, review L38-S3): on Windows `SECRET/` IS the
        excluded directory `secret/**`, so the exclusion applies; on Linux it is another directory and does not.
        Resource matching (`grant._glob_in`) stays case-sensitive on every platform (deny side folds only). Runs
        everywhere; on Windows it is the WF-021 F4 regression test (review L38-M1)."""
        path = "jobs/j/work/SECRET/key.txt"
        self.assertEqual(grant._excluded(path, ["jobs/j/work/secret/**"]), pathpolicy.CASE_FOLDING)
        self.assertTrue(grant._excluded(path, ["jobs/j/work/SECRET/**"]))
        self.assertTrue(grant._glob_in(path, ["jobs/j/work/**"]))
        self.assertFalse(grant._glob_in("JOBS/j/work/a.txt", ["jobs/j/work/**"]))

    def test_row25_upper_case_ledger_directory_is_the_ledger_marker(self):
        """Row #25 (unchanged by FIX1): a directory named `LEDGER` is accepted as the ledger / boundary marker on every
        platform (changeset.boundary_root, same rule as paths.jobs_dir). Runs everywhere (review L38-M1)."""
        db = os.path.join(self.dir, "LEDGER", "l.db")
        self.assertEqual(changeset.boundary_root(db), self.dir)


if __name__ == "__main__":
    unittest.main(verbosity=2)
