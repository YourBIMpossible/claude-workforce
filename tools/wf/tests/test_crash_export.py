"""XC1 (owner approval 2026-10-13): crash-shaped stale-export tests, tests only. No product code is changed.

Puts a scratch ledger into the two crash shapes the product's own commit path can produce through existing fault
seams, then records what `wf verify` reports and what `wf export` does in each:

* X1, after COMMIT and before the export refresh (`commit-error:after-commit`, txn.py): the row is committed, the
  `committing` marker stays, events.jsonl lacks the new event. `wf export` repairs the export only; it does not
  resolve the pending write; `wf resolve` does (COMMITTED).
* X2, before COMMIT (`kill-before-commit`, os._exit(3) after the INSERT and the marker write, txn.py): no new row,
  marker pending; the rollback journal's presence and size are recorded in the summary log, not asserted. `wf export`
  publishes only the committed head; `wf resolve` returns NOT-COMMITTED.

Every wf call, including setup, is a fresh `python -I wf.py` process (own connection and fault env); the fault env
is set on the single faulted call only. Each call is logged to WF_TEST_LOGDIR (argv, fault env, exit code, result,
stderr tail). Ledger content is read only through those fresh processes; the test process touches the filesystem
directly only to read marker bytes, export files, the ledger file's sha256 and, in X2, the rollback journal's
presence and size.

Scope (D-018): seam-injected evidence for the exact injected scenario; not an OS kill or power loss. Export repair
is not pending-write recovery, and nothing here claims automatic export freshness, backup, restore or recovery.
Runs inside the test_wf.py harness (Base: fresh scratch dir per test under WF_TEST_TMP, refuse-existing)."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unittest

from test_wf import Base, WF_DIR, log  # noqa: E402  (sets sys.path to the wf package)
from wfcore import markers, paths  # noqa: E402

STALE = "export events.jsonl is stale relative to the ledger"
EXPORT_FILES = ("contract.json", "events.jsonl", "head.json", "manifest.txt")
INTENT = '{"path":"jobs/j/work/a.txt","sha_before":"","note":"%s"}'


def _sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class CrashExport(Base):
    def setUp(self) -> None:
        super().setUp()
        self._calls = 0

    def wf(self, tag: str, *argv: str, role: str = "builder", session: str = "B1", faults: str | None = None,
           timeout: float = 60.0) -> tuple[int, dict]:
        """One fresh wf process; WF_TEST_FAULTS is set for this call only. Returns (exit code, parsed JSON)."""
        env = dict(os.environ)
        env.pop("WF_TEST_FAULTS", None)
        if faults:
            env["WF_TEST_FAULTS"] = faults
        cmd = [sys.executable, "-I", os.path.join(WF_DIR, "wf.py"), "--db", self.db, "--role", role,
               "--session", session, "--json", *argv]
        self._calls += 1
        name = f"{self._testMethodName}-{self._calls:02d}-{tag}"
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env, timeout=timeout)
        except subprocess.TimeoutExpired:
            log(name, {"argv": list(argv), "faults": faults, "timeout_s": timeout})
            self.fail(f"wf {tag} did not exit within {timeout}s")
        try:
            res = json.loads(p.stdout) if p.stdout.strip() else {}
        except ValueError:
            res = {"raw": p.stdout}
        log(name, {"argv": list(argv), "faults": faults, "code": p.returncode, "res": res, "stderr": p.stderr[-2000:]})
        return p.returncode, res

    def setup_job(self) -> None:
        c, r = self.wf("job-new", "job", "new", "j", "--contract", self.cpath, role="coordinator", session="C1")
        self.assertEqual(c, 0, r)
        c, r = self.wf("grant-propose", "grant", "propose", "j", "--grant", self.gpath, role="coordinator", session="C1")
        self.assertEqual(c, 0, r)
        h = r["block_hash"]
        c, r = self.wf("grant-activate", "grant", "activate", "j", "--owner-hash", h[:8], "--quote",
                       f"approve G1 {h[:8]}", "--turn", "owner turn", role="coordinator", session="C1")
        self.assertEqual(c, 0, r)
        c, r = self.wf("append-before", "append", "j", "--type", "intent", "--content", INTENT % "before")
        self.assertEqual(c, 0, r)

    def status(self, tag: str) -> dict:
        c, r = self.wf(tag, "status", "j")
        self.assertEqual(c, 0, r)
        return r

    def verify(self, tag: str) -> dict:
        c, r = self.wf(tag, "verify", "j")
        self.assertEqual(c, 0, r)
        return r

    def export_state(self) -> tuple[dict[str, str], int]:
        """sha256 of the four export files and the events.jsonl line count."""
        d = paths.export_dir(self.db, "j")
        with open(os.path.join(d, "events.jsonl"), "rb") as fh:
            lines = fh.read().count(b"\n")
        return {n: _sha(os.path.join(d, n)) for n in EXPORT_FILES}, lines

    def only_marker(self) -> tuple[markers.MarkerRef, bytes]:
        """The single pending marker (filesystem read), asserted `committing`; returns its ref and exact bytes."""
        ms = markers.list_markers(self.db, "j")
        self.assertEqual(len(ms), 1, ms)
        raw = markers.read_bytes(ms[0])
        self.assertIsNotNone(raw)
        self.assertEqual(json.loads(raw)["state"], "committing")
        return ms[0], raw

    def assert_pending_not_clean(self, v: dict) -> None:
        j = v["jobs"]["j"]
        self.assertTrue(j["pending_markers"], v)
        self.assertEqual([m["state"] for m in j["pending_markers"]], ["committing"], v)
        self.assertFalse(v["clean"], v)

    # ---------------------------------------------------------------- X1: after COMMIT, before export refresh
    def test_X1_after_commit_crash_stale_export_repaired_not_recovered(self):
        """XC1/X1: committed row, valid chain, stale export, pending marker; export repairs only the export."""
        self.setup_job()
        before = self.status("status-before")
        old_seq, old_self = before["head"]["seq"], before["head"]["self"]
        self.assertEqual(before["events"], old_seq)
        self.assertEqual(before["pending_markers"], [])

        # 1. faulted append: COMMIT executed, WfCommitUncertain raised, auto-resolve skipped by the injected fault
        rc, res = self.wf("append-faulted", "append", "j", "--type", "intent", "--content", INTENT % "after-commit-fault",
                          faults="commit-error:after-commit")
        self.assertEqual(res.get("code"), "commit-uncertain", res)
        self.assertEqual((res.get("auto_resolve") or {}).get("outcome"), "SKIPPED", res)
        self.assertNotEqual(rc, 0, res)
        db_sha_post_fault = _sha(self.db)

        # 2. head advanced by exactly one; one `committing` marker; export still has the old events
        after = self.status("status-after-fault")
        self.assertEqual(after["head"]["seq"], old_seq + 1, after)
        self.assertNotEqual(after["head"]["self"], old_self)
        self.assertEqual(after["events"], old_seq + 1)
        marker, marker_raw = self.only_marker()
        self.assertEqual((marker.seq, marker.attempt_id), (int(res["seq"]), res["attempt_id"]))
        stale_shas, stale_lines = self.export_state()
        self.assertEqual(stale_lines, old_seq, "export events.jsonl must still lack the new event")

        # 3. verify before export: valid chain, stale export, pending marker, not clean
        v1 = self.verify("verify-before-export")
        self.assertEqual(v1["jobs"]["j"]["problems"], [], v1)
        self.assertEqual(v1["ledger_problems"], {"schema": [], "integrity": []}, v1)
        self.assertIn(STALE, v1["jobs"]["j"]["export"], v1)
        self.assert_pending_not_clean(v1)

        # 4. wf export: ok, ledger and marker untouched, export now holds every committed event
        db_sha_before_export = _sha(self.db)
        rc, ex = self.wf("export", "export", "j")
        db_sha_after_export = _sha(self.db)
        self.assertEqual(rc, 0, ex)
        self.assertEqual(ex.get("code"), "ok", ex)
        self.assertEqual(ex.get("problems"), [], ex)
        self.assertIsNone(ex.get("msg_id"), ex)
        head_after_export = self.status("status-after-export")
        self.assertEqual((head_after_export["head"]["seq"], head_after_export["head"]["self"]),
                         (after["head"]["seq"], after["head"]["self"]))
        self.assertEqual(db_sha_after_export, db_sha_before_export, "wf export changed the ledger file bytes")
        marker_after, marker_raw_after = self.only_marker()
        self.assertEqual(marker_after, marker)
        self.assertEqual(marker_raw_after, marker_raw, "wf export changed the marker bytes")
        export_shas, export_lines = self.export_state()
        self.assertEqual(export_lines, old_seq + 1)
        self.assertNotEqual(export_shas["events.jsonl"], stale_shas["events.jsonl"])

        # 5. verify after export: export repaired, marker still pending (export is not recovery)
        v2 = self.verify("verify-after-export")
        self.assertEqual(v2["jobs"]["j"]["problems"], [], v2)
        self.assertEqual(v2["jobs"]["j"]["export"], [], v2)
        self.assert_pending_not_clean(v2)

        # 6. explicit resolution: COMMITTED, verify clean, export files byte-identical to those after step 4
        rc, rs = self.wf("resolve", "resolve", "j", str(marker.seq), "--attempt", marker.attempt_id)
        self.assertEqual(rc, 0, rs)
        self.assertEqual(rs.get("outcome"), "COMMITTED", rs)
        v3 = self.verify("verify-after-resolve")
        self.assertTrue(v3["clean"], v3)
        self.assertEqual(markers.list_markers(self.db, "j"), [])
        resolved_shas, resolved_lines = self.export_state()
        self.assertEqual(resolved_shas, export_shas, "resolve rewrote the export files differently")
        self.assertEqual(resolved_lines, old_seq + 1)
        final = self.status("status-after-resolve")
        self.assertEqual((final["head"]["seq"], final["head"]["self"]), (after["head"]["seq"], after["head"]["self"]))
        log(f"{self._testMethodName}-summary", {
            "old_head": {"seq": old_seq, "self": old_self}, "new_head": after["head"], "marker": marker.path,
            "marker_state": json.loads(marker_raw)["state"], "db_sha_post_fault": db_sha_post_fault,
            "db_sha_before_export": db_sha_before_export, "db_sha_after_export": db_sha_after_export,
            "stale_export_shas": stale_shas, "export_shas_after_export": export_shas,
            "export_shas_after_resolve": resolved_shas, "stale_lines": stale_lines, "export_lines": export_lines,
            "verify_before_export_export_problems": v1["jobs"]["j"]["export"],
            "verify_before_export_ledger_problems": v1["ledger_problems"],
            "verify_after_export_ledger_problems": v2["ledger_problems"]})

    # ---------------------------------------------------------------- X2: before COMMIT, process killed
    def test_X2_before_commit_kill_export_publishes_only_committed(self):
        """XC1/X2: killed before COMMIT; export publishes only the committed head, resolve returns NOT-COMMITTED."""
        self.setup_job()
        before = self.status("status-before")
        old_seq, old_self = before["head"]["seq"], before["head"]["self"]
        self.assertEqual(before["pending_markers"], [])
        journal = self.db + "-journal"
        self.assertFalse(os.path.exists(journal))
        db_sha_before_kill = _sha(self.db)

        # 1. killed append: exit 3; one `committing` marker; head unchanged
        rc, res = self.wf("append-killed", "append", "j", "--type", "intent", "--content", INTENT % "killed",
                          faults="kill-before-commit")
        self.assertEqual(rc, 3, res)
        marker, marker_raw = self.only_marker()
        journal_present = os.path.exists(journal)
        journal_size = os.path.getsize(journal) if journal_present else 0
        db_sha_after_kill = _sha(self.db)
        head = self.status("status-after-kill")
        self.assertEqual((head["head"]["seq"], head["head"]["self"]), (old_seq, old_self), head)
        self.assertEqual(head["events"], old_seq)

        # 2. wf export: ok, no uncommitted row published, head and marker unchanged (no db byte assertion: the
        #    hot journal may be acted on at open)
        rc, ex = self.wf("export", "export", "j")
        self.assertEqual(rc, 0, ex)
        self.assertEqual(ex.get("code"), "ok", ex)
        export_shas, export_lines = self.export_state()
        self.assertEqual(export_lines, old_seq, "export published a row that never committed")
        head_after_export = self.status("status-after-export")
        self.assertEqual((head_after_export["head"]["seq"], head_after_export["head"]["self"]), (old_seq, old_self))
        marker_after, marker_raw_after = self.only_marker()
        self.assertEqual(marker_after, marker)
        self.assertEqual(marker_raw_after, marker_raw, "wf export changed the marker bytes")

        # 3. verify: valid job, fresh export, marker pending, not clean
        v1 = self.verify("verify-after-export")
        self.assertEqual(v1["jobs"]["j"]["problems"], [], v1)
        self.assertEqual(v1["jobs"]["j"]["export"], [], v1)
        self.assert_pending_not_clean(v1)

        # 4. explicit resolution: NOT-COMMITTED, verify clean
        rc, rs = self.wf("resolve", "resolve", "j", str(marker.seq), "--attempt", marker.attempt_id)
        self.assertEqual(rc, 0, rs)
        self.assertEqual(rs.get("outcome"), "NOT-COMMITTED", rs)
        v2 = self.verify("verify-after-resolve")
        self.assertTrue(v2["clean"], v2)
        self.assertEqual(markers.list_markers(self.db, "j"), [])
        log(f"{self._testMethodName}-summary", {
            "head": {"seq": old_seq, "self": old_self}, "marker": marker.path,
            "marker_state": json.loads(marker_raw)["state"], "journal_present_after_kill": journal_present,
            "journal_size_after_kill": journal_size, "db_sha_before_kill": db_sha_before_kill,
            "db_sha_after_kill": db_sha_after_kill, "export_shas_after_export": export_shas,
            "export_lines": export_lines, "verify_ledger_problems": v1["ledger_problems"]})


if __name__ == "__main__":
    unittest.main()
