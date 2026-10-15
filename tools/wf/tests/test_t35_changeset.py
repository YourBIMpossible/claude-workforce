"""T35 repair A1 (changeset.py) and A2 (P4 coordination semantics): owner grant 2026-10-15 "T35 REQUIREMENT REPAIRS
A1-A3 + T28", assignment on issue #13 (White, 22:01Z). Lane B (Plexy).

Part 1 - module level (`wfcore.changeset`): baseline / compare over fixtures under WF_TEST_TMP. Red-then-green.
Part 2 - module level (`wfcore.pathpolicy`): coordination_globs, P4 precedence over P3, unclassified, violations.
Part 3 - end to end through `wf accept` with White's wiring (commands.job_new captures the baseline into the contract
         event payload; commands.accept classifies the union of supplied and observed changes with the coordination
         set). The two requirement assertions R1 and R2 are moved here from requirements_t35_unmet.py (same PR, per
         the assignment), assertion text unchanged. They need the wiring: on a tree without it they FAIL, and that
         is the honest state (no decorator, no skip).

What the walk does and does not do is asserted, never assumed: presence-only files are never opened (a guard makes
any open/hash of that path an error); links are skipped and recorded; nothing outside the boundary root is read;
the ledger directory and job export directories are never walked (coordination set, checked by wf verify).
"""
from __future__ import annotations

import builtins
import json
import os
import unittest

from test_t35_remaining_requirements import _Flow  # noqa: E402
from test_wf import Base, CONTRACT, log  # noqa: E402
from wfcore import canon, changeset, pathpolicy  # noqa: E402

# CONTRACT (test_wf): write jobs/j/work/**, read tools/wf/**, watch ledger/**, presence_only fixtures/results/**,
# exclusions 01-PROJECT-BRIEF.md.
P2_A = "tools/wf/a.py"
P2_B = "tools/wf/sub/b.py"
PRES = "fixtures/results/r.json"


def _write(root: str, rel: str, text: str) -> str:
    full = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(text)
    return full


class ChangesetModule(Base):
    """`self.dir` (fresh scratch) is the boundary root; `self.db` = <dir>/ledger/l.db as in every Base test."""

    def _seed(self) -> None:
        _write(self.dir, P2_A, "a"); _write(self.dir, P2_B, "b"); _write(self.dir, PRES, "{}")
        _write(self.dir, "ledger/l.db", "not walked"); _write(self.dir, "jobs/j/export/head.json", "not walked")
        _write(self.dir, "jobs/j/work/w.txt", "write scope: not captured")

    def test_boundary_root_rule(self):
        """Parent of a directory named `ledger` (same rule as paths.jobs_dir); any other name -> None (blocker)."""
        self.assertEqual(changeset.boundary_root(self.db), self.dir)
        self.assertIsNone(changeset.boundary_root(os.path.join(self.dir, "store", "l.db")))
        self.assertIsNone(changeset.boundary_root(os.path.join(self.dir, "l.db")))

    def test_baseline_captures_P2_hashes_presence_only_existence_and_nothing_else(self):
        self._seed()
        b = changeset.baseline(self.dir, CONTRACT)
        self.assertEqual(b["problems"], [])
        self.assertEqual(sorted(b["entries"]), [PRES, P2_A, P2_B])
        self.assertEqual(b["entries"][PRES], {"present": True})
        self.assertEqual(b["entries"][P2_A], {"sha256": canon.sha256_bytes(b"a"), "size": 1})
        self.assertNotIn("ledger/l.db", b["entries"]); self.assertNotIn("jobs/j/export/head.json", b["entries"])
        self.assertNotIn("jobs/j/work/w.txt", b["entries"])
        self.assertEqual(b["scopes"], {"read_scope": ["tools/wf/**"], "watch": ["ledger/**"],
                                       "presence_only": ["fixtures/results/**"]})
        self.assertIn(pathpolicy.NOT_DETECTED, b["limitations"])
        self.assertTrue(any("ledger/**" in x and "not walked" in x for x in b["limitations"]), b["limitations"])
        json.dumps(b); canon.sha256_obj(b)  # chain-protected payload must canonicalise
        log(f"{self._testMethodName}-baseline", b)

    def test_compare_detects_added_modified_deleted_P2_and_presence_only_deletion(self):
        self._seed()
        b = changeset.baseline(self.dir, CONTRACT)
        _write(self.dir, P2_A, "aa")                                   # modified
        os.remove(os.path.join(self.dir, *P2_B.split("/")))            # deleted
        _write(self.dir, "tools/wf/c.py", "c")                         # added
        os.remove(os.path.join(self.dir, *PRES.split("/")))            # presence-only deleted
        _write(self.dir, "jobs/j/work/new.txt", "P1: not observed")
        _write(self.dir, "ledger/l.db", "changed but not walked")
        c = changeset.compare(self.dir, CONTRACT, b)
        self.assertEqual((c["added"], c["modified"], c["deleted"]), (["tools/wf/c.py"], [P2_A], [PRES, P2_B]))
        self.assertEqual(c["problems"], [])
        self.assertIn(pathpolicy.NOT_DETECTED, c["limitations"])

    def test_compare_unchanged_tree_reports_nothing(self):
        self._seed()
        b = changeset.baseline(self.dir, CONTRACT)
        c = changeset.compare(self.dir, CONTRACT, b)
        self.assertEqual((c["added"], c["modified"], c["deleted"], c["problems"]), ([], [], [], []))

    def test_presence_only_path_is_never_opened_or_hashed(self):
        """Guard: any open() or sha256 of the presence-only file is an error; the walk must still record it."""
        self._seed()
        target = os.path.abspath(os.path.join(self.dir, *PRES.split("/")))
        real_open, real_sha = builtins.open, changeset.sha256_file

        def guard_open(file, *a, **k):
            if os.path.abspath(str(file)) == target:
                raise AssertionError("presence-only path opened")
            return real_open(file, *a, **k)

        def guard_sha(path):
            if os.path.abspath(path) == target:
                raise AssertionError("presence-only path hashed")
            return real_sha(path)
        builtins.open, changeset.sha256_file = guard_open, guard_sha
        try:
            b = changeset.baseline(self.dir, CONTRACT)
            _write(self.dir, P2_A, "changed")
            c = changeset.compare(self.dir, CONTRACT, b)
        finally:
            builtins.open, changeset.sha256_file = real_open, real_sha
        self.assertEqual(b["entries"][PRES], {"present": True})
        self.assertEqual((c["modified"], c["problems"]), ([P2_A], []))
        self.assertNotIn(PRES, c["modified"])  # presence-only content is never compared

    def test_presence_only_content_change_is_not_a_modification(self):
        self._seed()
        b = changeset.baseline(self.dir, CONTRACT)
        _write(self.dir, PRES, '{"changed": true}')
        c = changeset.compare(self.dir, CONTRACT, b)
        self.assertEqual((c["added"], c["modified"], c["deleted"]), ([], [], []))

    def test_unreadable_P2_file_is_a_problem_not_skipped(self):
        self._seed()
        target = os.path.abspath(os.path.join(self.dir, *P2_A.split("/")))
        real_sha = changeset.sha256_file

        def boom(path):
            if os.path.abspath(path) == target:
                raise PermissionError("locked")
            return real_sha(path)
        changeset.sha256_file = boom
        try:
            b = changeset.baseline(self.dir, CONTRACT)
        finally:
            changeset.sha256_file = real_sha
        self.assertEqual(b["problems"], [f"unreadable: {P2_A} (PermissionError)"])
        self.assertNotIn(P2_A, b["entries"]); self.assertIn(P2_B, b["entries"])

    def test_symlink_is_skipped_and_recorded_never_followed(self):
        self._seed()
        outside = os.path.join(self.dir, "outside-the-walk"); os.makedirs(outside)
        _write(outside, "secret.py", "must not be hashed")
        link = os.path.join(self.dir, "tools", "wf", "link")
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink not creatable here: {type(exc).__name__}")
        b = changeset.baseline(self.dir, CONTRACT)
        self.assertEqual(b["problems"], [])
        self.assertFalse(any(k.startswith("tools/wf/link") for k in b["entries"]), b["entries"])
        self.assertIn("skipped link or reparse point: tools/wf/link", b["limitations"])

    def test_malformed_baseline_is_a_problem(self):
        self._seed()
        for bad in (None, [], "x", {}, {"entries": []}, {"entries": {"tools/wf/a.py": "a"}},
                    {"entries": {"tools/wf/a.py": {"sha256": 1, "size": "x"}}}):
            with self.subTest(baseline=bad):
                c = changeset.compare(self.dir, CONTRACT, bad)
                self.assertTrue(c["problems"], c)
                self.assertTrue(all(x.startswith("baseline malformed") for x in c["problems"]), c)
                self.assertEqual((c["added"], c["modified"], c["deleted"]), ([], [], []))

    def test_whole_tree_glob_and_malformed_contract_fail_closed(self):
        self._seed()
        for key, bad in (("read_scope", ["**"]), ("watch", ["*"]), ("presence_only", ["**/x"])):
            with self.subTest(scope=key):
                b = changeset.baseline(self.dir, dict(CONTRACT, **{key: bad}))
                self.assertTrue(any("no static prefix" in x and key in x for x in b["problems"]), b["problems"])
        self.assertEqual(changeset.baseline(self.dir, None)["problems"], ["contract is not an object"])
        b = changeset.baseline(self.dir, dict(CONTRACT, read_scope=["../other/**"]))
        self.assertTrue(any("contract scopes" in x for x in b["problems"]), b["problems"])

    def test_nothing_outside_the_root_is_read(self):
        self._seed()
        root = os.path.abspath(self.dir)
        real_open, real_sha = builtins.open, changeset.sha256_file
        touched: list[str] = []

        def guard_open(file, *a, **k):
            touched.append(os.path.abspath(str(file)))
            return real_open(file, *a, **k)

        def guard_sha(path):
            touched.append(os.path.abspath(path))
            return real_sha(path)
        builtins.open, changeset.sha256_file = guard_open, guard_sha
        try:
            changeset.compare(self.dir, CONTRACT, changeset.baseline(self.dir, CONTRACT))
        finally:
            builtins.open, changeset.sha256_file = real_open, real_sha
        self.assertTrue(touched)
        self.assertTrue(all(t.startswith(root + os.sep) for t in touched), touched)


class CoordinationSemantics(Base):
    CO = pathpolicy.coordination_globs("j", "ledger", "l.db")

    def test_coordination_globs_are_the_spec_P4_row(self):
        self.assertEqual(self.CO, ["ledger/l.db", "ledger/l.db-journal", "ledger/pending/j/**", "jobs/j/export/**",
                                   "runtime/gate/gate.db", "runtime/observe/j.log"])
        self.assertEqual(pathpolicy.coordination_globs("k", "store\\ledger\\", "w.db")[0], "store/ledger/w.db")

    def test_coordination_precedes_P3_and_is_allowed(self):
        """Spec 4.4 P4 row: the ledger db and journal are allowed to change although CONTRACT watches ledger/**."""
        cl = pathpolicy.classify(["ledger/l.db", "ledger/l.db-journal", "ledger/pending/j/m.json",
                                  "jobs/j/export/head.json", "runtime/gate/gate.db", "runtime/observe/j.log",
                                  "ledger/stray.db", "jobs/j/work/a.txt"], CONTRACT, coordination=self.CO)
        self.assertEqual(cl["P4"], ["ledger/l.db", "ledger/l.db-journal", "ledger/pending/j/m.json",
                                    "jobs/j/export/head.json", "runtime/gate/gate.db", "runtime/observe/j.log"])
        self.assertEqual((cl["P3"], cl["P1"], cl["unclassified"]), (["ledger/stray.db"], ["jobs/j/work/a.txt"], []))
        for r in cl["results"]:
            if r["class"] == "P4":
                self.assertEqual(r["result"], pathpolicy.COORDINATION_RESULT)
        v = pathpolicy.violations(cl)
        self.assertEqual(len(v), 1); self.assertIn("ledger/stray.db", v[0]); self.assertIn("(P3", v[0])
        self.assertNotIn("l.db-journal", v[0])

    def test_unclassified_is_a_separate_violation_and_P5_is_untouched(self):
        cl = pathpolicy.classify(["elsewhere.txt", "01-PROJECT-BRIEF.md", "../x", "jobs/j/export/h.json"],
                                 CONTRACT, coordination=self.CO)
        self.assertEqual((cl["unclassified"], cl["P5"], cl["P4"]),
                         (["elsewhere.txt"], ["01-PROJECT-BRIEF.md", "../x"], ["jobs/j/export/h.json"]))
        v = pathpolicy.violations(cl)
        self.assertTrue(any(x.startswith("unclassified in-boundary paths") and "elsewhere.txt" in x for x in v), v)
        self.assertTrue(any("(P5)" in x and "01-PROJECT-BRIEF.md" in x for x in v), v)
        self.assertFalse(any("jobs/j/export/h.json" in x for x in v), v)

    def test_without_a_coordination_set_there_is_no_P4(self):
        cl = pathpolicy.classify(["ledger/l.db", "jobs/j/export/head.json", "elsewhere.txt"], CONTRACT)
        self.assertEqual((cl["P4"], cl["P3"], cl["unclassified"]),
                         ([], ["ledger/l.db"], ["jobs/j/export/head.json", "elsewhere.txt"]))
        self.assertTrue(pathpolicy.violations(cl))

    def test_malformed_coordination_set_fails_closed(self):
        for bad in ("ledger/l.db", [None], ["../x"], [""], 3):
            with self.subTest(coordination=bad):
                cl = pathpolicy.classify(["jobs/j/work/a.txt"], CONTRACT, coordination=bad)
                self.assertEqual(cl["P1"], []); self.assertEqual(cl["P5"], ["jobs/j/work/a.txt"])
                self.assertTrue(any(x.startswith("coordination") for x in cl["contract_problems"]), cl)
                self.assertTrue(pathpolicy.violations(cl))

    def test_bare_dict_fallback_keeps_P4_and_unclassified_as_violations(self):
        self.assertTrue(pathpolicy.violations({"P4": ["elsewhere/x"], "P5": []}))
        self.assertTrue(pathpolicy.violations({"unclassified": ["x"]}))
        self.assertEqual(pathpolicy.violations({"P1": ["x"], "P4": [], "P5": [], "unclassified": []}), [])


class EndToEndWithWiring(_Flow):
    """R1 and R2 requirement assertions (from 64df3cf / requirements_t35_unmet.py), unchanged, now in normal
    discovery. They pass only with White's wiring (job_new baseline payload; accept union + coordination)."""

    contract = dict(CONTRACT, baseline={"manifest": [{"path": "jobs/j/work/gone.txt", "sha256": "a" * 64},
                                                     {"path": "tools/wf/wfcore/db.py", "sha256": "b" * 64}],
                                        "git_revision": None, "uncommitted_state": None})

    def test_R1_undeclared_P2_manifest_deletion_is_rejected(self):
        """Requirement: FIRST-RELEASE-SPEC.md section 4.4 (line 209) "computes the set of changed paths inside the
        monitored boundary"; rule 1 (line 220) "a missing P2/P3 file is a deletion violation"; T35 row line 310.
        Fixture: the P2 manifest file tools/wf/wfcore/db.py EXISTS at `wf job new` (captured into the baseline
        payload), is deleted afterwards and is NOT supplied by the reviewer. A declared manifest path that never
        existed at capture is not a deletion (White's wiring; fixtures declare manifests without materialising
        them), so the file is materialised first. Assertion unchanged from 64df3cf: decision rejected."""
        _write(self.dir, "tools/wf/wfcore/db.py", "baseline content")
        self.start()
        os.remove(os.path.join(self.dir, "tools", "wf", "wfcore", "db.py"))
        r = self.accept([], "R1d")
        self.assertEqual(r["decision"], "rejected", r)
        self.assertEqual(r["classes"]["P2"], ["tools/wf/wfcore/db.py"])
        self.assertTrue(any("tools/wf/wfcore/db.py" in x and "(P2)" in x for x in r["reasons"]), r["reasons"])
        c, st = self.run_wf("status", "j"); self.assertEqual(st["axes"]["acceptance"], "rejected")

    def test_R1_declared_P2_manifest_deletion_is_still_rejected_and_P1_deletion_accepted(self):
        """Companion: declaring the deletion changes nothing for P2 (still rejected); an undeclared P1 deletion of a
        write-scope file is allowed (rule 1: a missing P1 file is not a failure)."""
        _write(self.dir, "tools/wf/wfcore/db.py", "baseline content")
        _write(self.dir, "jobs/j/work/gone.txt", "p1")
        self.start()
        os.remove(os.path.join(self.dir, "jobs", "j", "work", "gone.txt"))
        r = self.accept([], "R1e")
        self.assertEqual(r["decision"], "accepted", r)
        os.remove(os.path.join(self.dir, "tools", "wf", "wfcore", "db.py"))
        r = self.accept(["tools/wf/wfcore/db.py"], "R1f")
        self.assertEqual(r["decision"], "rejected", r)

    def test_R2_supplied_coordination_update_is_accepted_as_P4(self):
        """Requirement: FIRST-RELEASE-SPEC.md section 4.4 P4 row (line 216) and rule 4 (line 223) "coordination
        (P4, verified by verify)". The fixture contract watches ledger/**; the coordination set precedes P3.
        Assertion unchanged from 64df3cf: decision accepted."""
        self.contract = CONTRACT
        with open(self.cpath, "w", encoding="utf-8") as fh:
            json.dump(CONTRACT, fh)
        self.start()
        r = self.accept(["jobs/j/work/a.txt", "jobs/j/export/head.json", "ledger/l.db"], "R2d")
        self.assertEqual(r["decision"], "accepted", r)
        self.assertEqual(r["classes"]["P4"], ["jobs/j/export/head.json", "ledger/l.db"])
        self.assertEqual(r["classes"]["P3"], [])

    def test_R2_stray_under_watched_ledger_still_rejects_fail_closed(self):
        self.contract = CONTRACT
        with open(self.cpath, "w", encoding="utf-8") as fh:
            json.dump(CONTRACT, fh)
        self.start()
        r = self.accept(["ledger/stray.db"], "R2e")
        self.assertEqual(r["decision"], "rejected", r)
        self.assertEqual(r["classes"]["P3"], ["ledger/stray.db"])


if __name__ == "__main__":
    unittest.main()
