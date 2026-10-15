"""T35 remaining requirement tests (owner authorization 2026-10-15, Plexy): the three T35 elements the coverage map
(fixtures/results/t09-closure-9d2210ee082a/coverage-map-t09-closure.md, T35 row) lists as "not asserted":

  R1  a manifest file missing from P1            (FIRST-RELEASE-SPEC.md section 4.4 rule 1, T35 row)
  R2  expected P4 updates and `wf verify`         (section 4.4 P4 row and rule 4; T35 "P4 verified by wf verify")
  R3  unexpected files in ledger/runtime folders  (section 4.4 P4 row: "Added or deleted files in the ledger/runtime
                                                   folders other than the listed ones -> violation")

Two kinds of evidence are kept apart in every test name and docstring:
  * SUPPLIED classification: `wf accept` classifies the `changed_paths` the reviewer supplies (pathpolicy.classify).
    It never reads the filesystem, so it discovers nothing on its own.
  * OBSERVED detection: `wf verify` reads the ledger and the export directory and reports what it finds.
A requirement whose mechanism the product does not have gets a `test_LIMIT_<subject>` observation test (White,
PR #15 correction 1/3; lane rule issue #13): it asserts the ACTUAL present behaviour, and its docstring carries the
spec sentence, the missing mechanism and "missing-implementation; T35 stays PARTIAL on this element". No decorator
of any kind; no expected PASS is invented; such a test fails the day the behaviour changes, so the limit cannot
drift silently. A LIMIT test closes no requirement: the remaining requirement assertions (three after the R3b ledger-listing mechanism landed) stay failing, separately
runnable, in tools/wf/tests/requirements_t35_unmet.py (owner correction 2026-10-15, issue #13). P3 stays
fail-closed; 8.3 short names and NTFS behaviour are not touched here (lane D evidence).

Fixtures: fresh scratch under WF_TEST_TMP (test_wf.Base), CONTRACT from test_wf (write jobs/j/work/**, read tools/wf/**,
watch ledger/**, presence_only fixtures/results/**, exclusions 01-PROJECT-BRIEF.md) or a variant built per test.
"""
from __future__ import annotations

import copy
import json
import os
import unittest

from test_wf import Base, CONTRACT, log  # noqa: E402
from wfcore import canon, paths, pathpolicy  # noqa: E402

EXPORT_FILES = ("contract.json", "events.jsonl", "head.json", "manifest.txt")


def _declarations(contract: dict) -> dict:
    return {"contract_sha256": canon.sha256_obj(contract), "baseline": copy.deepcopy(contract["baseline"])}


def _results(contract: dict, changed: list[str]) -> dict:
    return {"criteria": [{"id": "AC1", "pass": True, "actual": "ok"}], "environment": contract["environment"],
            "limits": "none", "changed_paths": changed, **_declarations(contract)}


class _Flow(Base):
    """Base with a contract variant: job, grant, one delivered claim; accept helper that records the result."""

    contract: dict = CONTRACT

    def setUp(self) -> None:
        super().setUp()
        if self.contract is not CONTRACT:
            with open(self.cpath, "w", encoding="utf-8") as fh:
                json.dump(self.contract, fh)

    def start(self) -> None:
        self.new_job(); self.activate()
        c, r = self.run_wf("append", "j", "--type", "delivered", "--content",
                           json.dumps({"claims": [{"claim": "done", "evidence": ["jobs/j/work/a.txt"]}]}))
        self.assertEqual(c, 0, r)

    def accept(self, changed: list[str], msg_id: str) -> dict:
        c, r = self.run_wf("accept", "j", "--results", json.dumps(_results(self.contract, changed)),
                           role="reviewer", session="R1", msg_id=msg_id)
        self.assertEqual(c, 0, r)
        log(f"{self._testMethodName}-accept-{msg_id}", {"changed": changed, "decision": r.get("decision"),
                                                        "reasons": r.get("reasons"), "classes": r.get("classes")})
        return r

    def verify(self) -> dict:
        c, v = self.run_wf("verify", "j")
        log(f"{self._testMethodName}-verify", v)
        return v


# ---------------------------------------------------------------------------------------------------- R1

class R1ManifestFileMissingFromP1(_Flow):
    """Section 4.4 rule 1: "Manifest completeness is not the test. A missing P1 file is not a failure; a missing
    P2/P3 file is a deletion violation." Baseline manifest names files; the P1 one never exists on disk."""

    contract = dict(CONTRACT, baseline={"manifest": [{"path": "jobs/j/work/gone.txt", "sha256": "a" * 64},
                                                     {"path": "tools/wf/wfcore/db.py", "sha256": "b" * 64}],
                                        "git_revision": None, "uncommitted_state": None})

    def test_supplied_P1_deletion_of_a_manifest_file_is_allowed(self):
        """R1 positive. Detects: an accept that requires every manifest file to exist, or that treats a P1 deletion
        as a violation. The file jobs/j/work/gone.txt is in the baseline manifest and is absent on disk."""
        self.start()
        self.assertFalse(os.path.exists(os.path.join(self.dir, "jobs", "j", "work", "gone.txt")))
        r = self.accept(["jobs/j/work/gone.txt"], "R1a")
        self.assertEqual(r["decision"], "accepted", r)
        self.assertEqual(r["classes"]["P1"], ["jobs/j/work/gone.txt"])
        row = [x for x in r["classes"]["results"] if x["path"] == "jobs/j/work/gone.txt"][0]
        self.assertEqual(row["result"], "allowed")

    def test_supplied_P2_deletion_of_a_manifest_file_is_a_violation(self):
        """R1 negative. Detects: a deletion in the read scope slipping through as allowed. The reviewer supplies the
        deleted P2 manifest path; classification (not filesystem observation) rejects it."""
        self.start()
        r = self.accept(["tools/wf/wfcore/db.py"], "R1b")
        self.assertEqual(r["decision"], "rejected", r)
        self.assertEqual(r["classes"]["P2"], ["tools/wf/wfcore/db.py"])
        self.assertTrue(any("tools/wf/wfcore/db.py" in x and "(P2)" in x for x in r["reasons"]), r["reasons"])

    def test_manifest_presence_is_not_checked_by_accept_at_all(self):
        """R1 scope statement. Detects: accept starting to read the baseline manifest against the filesystem (which
        rule 1 forbids as the test) - an accept with no changed paths must accept although both manifest files are
        absent on disk. Also records that the classifier observed nothing: results list is empty."""
        self.start()
        for rel in ("jobs/j/work/gone.txt", "tools/wf/wfcore/db.py"):
            self.assertFalse(os.path.exists(os.path.join(self.dir, *rel.split("/"))))
        r = self.accept([], "R1c")
        self.assertEqual(r["decision"], "accepted", r)
        self.assertEqual(r["classes"]["results"], [])
        self.assertIn(pathpolicy.NOT_DETECTED, r["classes"]["limitations"])

    def test_LIMIT_undeclared_P2_manifest_deletion_is_accepted(self):
        """R1 limit (observed present behaviour). Spec sentence (section 4.4): accept "computes the set of changed
        paths inside the monitored boundary"; rule 1: "a missing P2/P3 file is a deletion violation". Present
        behaviour: an undeclared deletion of a P2 manifest file (tools/wf/wfcore/db.py in the baseline, absent on
        disk, not supplied) is ACCEPTED. Missing mechanism: a baseline-manifest-versus-filesystem comparison inside
        the boundary; `commands.accept` classifies only reviewer-supplied `changed_paths` and reads no file.
        missing-implementation; T35 stays PARTIAL on this element."""
        self.start()
        self.assertFalse(os.path.exists(os.path.join(self.dir, "tools", "wf", "wfcore", "db.py")))
        r = self.accept([], "R1d")
        self.assertEqual(r["decision"], "accepted", r)
        self.assertEqual(r["classes"]["results"], [])


# ---------------------------------------------------------------------------------------------------- R2

class R2ExpectedP4UpdatesAndVerify(_Flow):
    """Section 4.4 P4 row: ledger database and journal, jobs/<job>/export/ are "allowed to change; not compared by
    hash against baseline. Checked instead by wf verify". Contract variant: the ledger is NOT in `watch`, so a
    ledger path has no P3 claim and the P4 question is asked cleanly."""

    contract = dict(CONTRACT, watch=["runtime/gate/**"])

    def _export_dir(self) -> str:
        return paths.export_dir(self.db, "j")

    def test_coordination_files_change_across_the_flow_and_verify_stays_clean(self):
        """R2 positive (observed). Detects: a flow in which the ledger or export files do not change (nothing was
        coordinated) or in which verify flags the expected P4 updates as problems. Hashes are taken by the test
        for evidence only; accept never compares them."""
        self.start()
        before = {f: canon.sha256_file(os.path.join(self._export_dir(), f)) for f in EXPORT_FILES}
        db_before = canon.sha256_file(self.db)
        r = self.accept(["jobs/j/work/a.txt"], "R2a")
        self.assertEqual(r["decision"], "accepted", r)
        after = {f: canon.sha256_file(os.path.join(self._export_dir(), f)) for f in EXPORT_FILES}
        self.assertNotEqual(db_before, canon.sha256_file(self.db))
        for f in ("events.jsonl", "head.json", "manifest.txt"):
            self.assertNotEqual(before[f], after[f], f)
        self.assertEqual(before["contract.json"], after["contract.json"])
        v = self.verify()
        self.assertTrue(v["clean"], v)
        self.assertEqual(v["jobs"]["j"]["export"], [])
        log(f"{self._testMethodName}-hashes", {"before": before, "after": after})

    def test_accept_does_not_hash_compare_P4_but_verify_detects_the_tamper(self):
        """R2 discriminating pair. Detects (a) accept beginning to read or hash-compare export files (the spec says
        not compared by hash), and (b) verify failing to report an export file whose bytes differ from its
        manifest. The same tampered state is accepted by accept (nothing supplied) and reported by verify."""
        self.start()
        head = os.path.join(self._export_dir(), "head.json")
        with open(head, "a", encoding="utf-8") as fh:
            fh.write("tampered\n")
        v = self.verify()
        self.assertFalse(v["clean"], v)
        self.assertIn("export file differs from manifest: head.json", v["jobs"]["j"]["export"])
        r = self.accept(["jobs/j/work/a.txt"], "R2b")
        self.assertEqual(r["decision"], "accepted", r)  # supplied classification only; the tamper is not supplied
        self.assertNotIn("head.json", json.dumps(r["classes"]))

    def test_supplied_P4_path_is_classified_not_observed(self):
        """R2 scope statement (supplied). Detects: the classifier claiming to have observed a coordination update.
        A reviewer-supplied export path is classified by scope alone; the file's real state plays no part (the
        same path is classified identically whether or not the file exists)."""
        self.start()
        existing = "jobs/j/export/head.json"
        absent = "jobs/j/export/never-written.json"
        self.assertTrue(os.path.isfile(os.path.join(self.dir, *existing.split("/"))))
        self.assertFalse(os.path.exists(os.path.join(self.dir, *absent.split("/"))))
        cl = pathpolicy.classify([existing, absent], self.contract)
        self.assertEqual([x["class"] for x in cl["results"]], ["P4", "P4"])
        self.assertEqual([x["result"] for x in cl["results"]], ["violation", "violation"])
        self.assertIn("no path was read", cl["comparison"])

    def test_LIMIT_supplied_coordination_update_is_rejected_as_P4(self):
        """R2 limit (observed present behaviour). Spec sentence (section 4.4 P4 row): "expected coordination updates
        (ledger database and its journal, jobs/<job>/export/, ...) allowed to change; not compared by hash".
        Present behaviour: a reviewer who truthfully supplies the ledger database or an export file as changed is
        REJECTED; both classify P4 "outside every declared scope" and `pathpolicy.violations` rejects every P4
        entry. Missing mechanism: a coordination-path class in pathpolicy (contract vocabulary decision; no
        contract field or built-in list names the coordination paths). missing-implementation; T35 stays
        PARTIAL on this element."""
        self.start()
        r = self.accept(["jobs/j/work/a.txt", "jobs/j/export/head.json", "ledger/l.db"], "R2d")
        self.assertEqual(r["decision"], "rejected", r)
        self.assertEqual(r["classes"]["P4"], ["jobs/j/export/head.json", "ledger/l.db"])
        self.assertEqual(r["classes"]["P1"], ["jobs/j/work/a.txt"])
        self.assertTrue(any("(P4)" in x and "ledger/l.db" in x for x in r["reasons"]), r["reasons"])


# ---------------------------------------------------------------------------------------------------- R3

class R3UnexpectedFilesInLedgerAndRuntimeFolders(_Flow):
    """Section 4.4 P4 row, last sentence: "Added or deleted files in the ledger/runtime folders other than the
    listed ones -> violation". Contract variant without a ledger watch entry (as R2) so that classification of a
    stray ledger path is not pre-empted by P3."""

    contract = dict(CONTRACT, watch=["runtime/gate/**"])

    def test_supplied_stray_export_and_ledger_files_are_violations(self):
        """R3 positive (supplied). Detects: a stray file under jobs/<job>/export/ or ledger/ classifying as allowed
        when the reviewer reports it. Both are P4 "outside every declared scope" and reject, with the paths named."""
        self.start()
        r = self.accept(["jobs/j/work/a.txt", "jobs/j/export/stray.txt", "ledger/stray.db"], "R3a")
        self.assertEqual(r["decision"], "rejected", r)
        self.assertEqual(r["classes"]["P4"], ["jobs/j/export/stray.txt", "ledger/stray.db"])
        self.assertTrue(any("jobs/j/export/stray.txt" in x and "ledger/stray.db" in x for x in r["reasons"]),
                        r["reasons"])

    def test_supplied_stray_under_a_watched_ledger_is_P3_fail_closed(self):
        """R3 with the default contract (ledger/** watched): the stray ledger file is P3 and rejects fail-closed;
        the P2/P3 rule is not weakened by the P4 question. Detects: a stray under a watched folder being allowed."""
        self.contract = CONTRACT
        with open(self.cpath, "w", encoding="utf-8") as fh:
            json.dump(CONTRACT, fh)
        self.start()
        r = self.accept(["ledger/stray.db"], "R3b")
        self.assertEqual(r["decision"], "rejected", r)
        self.assertEqual(r["classes"]["P3"], ["ledger/stray.db"])
        self.assertTrue(any("(P3" in x and "fail closed" in x for x in r["reasons"]), r["reasons"])

    def test_verify_reports_a_deleted_listed_export_file(self):
        """R3 observed, deletion half: a listed export file deleted -> verify names it (manifest-driven). Detects:
        verify ignoring a missing manifest-listed file. (test_export_cmd covers manifest/dir removal; this is the
        single-file case named by the P4 row.)"""
        self.start()
        os.remove(os.path.join(paths.export_dir(self.db, "j"), "head.json"))
        v = self.verify()
        self.assertFalse(v["clean"], v)
        self.assertIn("export file missing: head.json", v["jobs"]["j"]["export"])

    def test_LIMIT_added_stray_file_in_the_export_folder_is_unreported_by_verify(self):
        """R3 limit (observed present behaviour). Spec sentence (section 4.4 P4 row): "Added or deleted files in the
        ledger/runtime folders other than the listed ones -> violation". Present behaviour: an added file in
        jobs/<job>/export/ that the manifest does not list is NOT reported by `wf verify` (clean stays true, export
        problems empty): export.check walks the manifest entries only (`for e in entries`) and never lists the
        directory. Missing mechanism: a directory listing compared against the manifest or the fixed FILES set in
        export.check. missing-implementation; T35 stays PARTIAL on this element."""
        self.start()
        stray = os.path.join(paths.export_dir(self.db, "j"), "stray.txt")
        with open(stray, "w", encoding="utf-8") as fh:
            fh.write("unexpected\n")
        v = self.verify()
        self.assertTrue(v["clean"], v)
        self.assertEqual(v["jobs"]["j"]["export"], [])
        self.assertNotIn("stray.txt", json.dumps(v))

    def test_stray_files_survive_export_refresh_and_remain_unreported(self):
        """R3 observed, current behaviour pinned honestly: after `wf export` regenerates the four files, the stray
        file is still there and verify is still clean. Detects: a silent behaviour change in either direction
        (export starting to delete strays, or verify starting to report them) without the gap tests above
        flipping. Not a requirement PASS; a record of the present limit."""
        self.start()
        stray = os.path.join(paths.export_dir(self.db, "j"), "stray.txt")
        with open(stray, "w", encoding="utf-8") as fh:
            fh.write("unexpected\n")
        c, e = self.run_wf("export", "j")
        self.assertEqual(c, 0, e)
        self.assertTrue(os.path.isfile(stray))
        self.assertEqual(sorted(os.listdir(paths.export_dir(self.db, "j"))), sorted([*EXPORT_FILES, "stray.txt"]))
        v = self.verify()
        self.assertTrue(v["clean"], v)
        self.assertEqual(v["jobs"]["j"]["export"], [])


if __name__ == "__main__":
    unittest.main()
