"""WF-021 step 3 (MyBuddy): case-sensitivity assumptions in tools/wf pinned as OBSERVED on Linux.

Rows refer to fixtures/results/wf021-mybuddy/PLATFORM-REVIEW.md. These are characterization tests: they pin what the
product does today on a case-sensitive filesystem so a change is deliberate. Where the observed behaviour is a
finding (fail-open), the test name says `observed` and the docstring names the finding; a fix must update the test.
Linux-only: the behaviour depends on a case-sensitive filesystem (ext4); skipped elsewhere with that reason.
"""
from __future__ import annotations

import os
import sys
import unittest

from test_wf import CONTRACT, Base  # noqa: E402  (sets sys.path; fresh WF_TEST_TMP)
from wfcore import changeset, grant, pathpolicy, reconcile  # noqa: E402

LINUX = sys.platform.startswith("linux")
SKIP = "case-sensitive filesystem semantics pinned on Linux only"


def _write(path: str, text: str = "x") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "x", encoding="utf-8") as fh:
        fh.write(text)


@unittest.skipUnless(LINUX, SKIP)
class CaseOnLinux(Base):
    def contract(self, **scopes) -> dict:
        c = dict(CONTRACT)
        c.update({"read_scope": [], "watch": [], "presence_only": [], "write_scope": ["jobs/j/work/**"]})
        c.update(scopes)
        return c

    def test_row16_observed_upper_case_scope_glob_walks_nothing_and_reports_nothing(self):
        """Row #16 (finding, fail-open on Linux): scope `Docs/**` is looked up as `docs`; the real `Docs/` is never
        walked and the baseline reports neither a problem nor a limitation for it. Control: a lower-case scope is
        captured normally."""
        os.makedirs(os.path.join(self.dir, "ledger"))
        _write(os.path.join(self.dir, "Docs", "a.txt"))
        _write(os.path.join(self.dir, "docs2", "a.txt"))
        b = changeset.baseline(self.dir, self.contract(read_scope=["Docs/**"]))
        self.assertEqual(b["entries"], {})
        self.assertEqual(b["problems"], [])
        self.assertFalse(any("Docs" in x or "docs" in x for x in b["limitations"]), b["limitations"])
        control = changeset.baseline(self.dir, self.contract(read_scope=["docs2/**"]))
        self.assertEqual(list(control["entries"]), ["docs2/a.txt"])

    def test_row17_observed_reconcile_lower_cases_paths_and_cannot_hash_upper_case_files(self):
        """Row #17 (finding, fail-open on Linux): reconcile relates `jobs/j/work/New.txt` as `jobs/j/work/new.txt`
        and then finds no file there, so the current content hash is None although the file exists."""
        _write(os.path.join(self.dir, "jobs", "j", "work", "New.txt"), "y")
        rel = reconcile._relate("jobs/j/work/New.txt")
        self.assertEqual(rel, "jobs/j/work/new.txt")
        self.assertIsNone(reconcile._sha_now(self.dir, rel))
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "jobs", "j", "work", "New.txt")))

    def test_row18_observed_reconcile_cannot_tell_case_variants_apart(self):
        """Row #18: two different Linux files differing only in case relate to the same comparison form."""
        self.assertEqual(reconcile._relate("jobs/j/work/A.txt"), reconcile._relate("jobs/j/work/a.txt"))

    def test_row19_observed_case_variant_directory_classified_P1_allowed(self):
        """Row #19 (policy decision, NEEDS OWNER): `JOBS/J/WORK/a.txt` is a different directory on ext4 but is
        classified P1 (allowed) because classification folds case."""
        classes = pathpolicy.classify(["JOBS/J/WORK/a.txt"], dict(CONTRACT))
        self.assertEqual(classes["P1"], ["JOBS/J/WORK/a.txt"])
        self.assertEqual(classes["results"][0]["result"], "allowed")

    def test_row20_grant_globs_are_case_sensitive_on_every_platform(self):
        """Row #20 (finding on Windows; correct on Linux): resource and exclusion globs in grant.check use
        `fnmatchcase`, so `SECRET/` matches the resource glob but not the exclusion `secret/**`. On ext4 these are
        distinct directories (correct); on NTFS they are the same directory (the exclusion is bypassed)."""
        path = "jobs/j/work/SECRET/key.txt"
        self.assertTrue(grant._glob_in(path, ["jobs/j/work/**"]))
        self.assertFalse(grant._glob_in(path, ["jobs/j/work/secret/**"]))

    def test_row25_observed_upper_case_ledger_directory_is_the_ledger_marker(self):
        """Row #25: a directory named `LEDGER` is accepted as the ledger / boundary marker on Linux too."""
        db = os.path.join(self.dir, "LEDGER", "l.db")
        self.assertEqual(changeset.boundary_root(db), self.dir)


if __name__ == "__main__":
    unittest.main(verbosity=2)
