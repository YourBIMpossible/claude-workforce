"""B-export acceptance tests for `wf export <job>` (approved 2026-10-13; eight criteria of the rev2 candidate).

Runs inside the test_wf.py harness (Base: fresh scratch dir per test under WF_TEST_TMP, refuse-existing, logs under
WF_TEST_LOGDIR). The command is ledger-read-only and export-writing: every test asserts head (seq, self) and row
count identity across the export, and that no marker appears; the ledger file bytes are compared where the command
reports an error. "No filesystem writes" is never asserted: the export directory changes by design.
Criterion 3 deletes exactly one sentinel file this test created (option 7A) after the containment assertions.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from test_wf import TMP, Base  # noqa: E402  (sets sys.path to the wf package)
from wfcore import commands, db as dbm, markers, paths  # noqa: E402

STALE = "export events.jsonl is stale relative to the ledger"


def _sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class ExportCmd(Base):
    def _head(self):
        st = commands.status(self.db, "j")
        return st["head"]["seq"], st["head"]["self"], st["events"]

    def _export_problems(self):
        return commands.verify(self.db, "j")["jobs"]["j"]["export"]

    def _assert_ledger_identity(self, before, note=""):
        self.assertEqual(self._head(), before, note)
        self.assertEqual(markers.list_markers(self.db, "j"), [])

    def _export_ok(self):
        before = self._head()
        c, r = self.run_wf("export", "j")
        self.assertEqual(c, 0, r)
        self.assertTrue(r["ok"]); self.assertEqual(r["code"], "ok"); self.assertEqual(r["problems"], [])
        self.assertNotIn("error", r["export"], r["export"])
        self.assertIsNone(r["msg_id"])  # no event, no message
        self._assert_ledger_identity(before, "export changed the ledger head or added a marker")
        self.assertTrue(commands.verify(self.db, "j")["clean"])
        return r

    def test_AC1_stale_events_repaired(self):
        """Criterion 1: altered events.jsonl -> verify not clean, lists the stale problem; export -> ok; verify clean;
        head (seq, self) identical before and after."""
        self.new_job(); self.activate(); self.intent()
        ev = os.path.join(paths.export_dir(self.db, "j"), "events.jsonl")
        with open(ev, "a", encoding="utf-8") as fh:
            fh.write("{}\n")
        v = commands.verify(self.db, "j")
        self.assertFalse(v["clean"])
        self.assertIn(STALE, v["jobs"]["j"]["export"])
        self.assertTrue(any(p.startswith("export file differs from manifest") for p in v["jobs"]["j"]["export"]))
        self._export_ok()
        self.assertEqual(self._export_problems(), [])

    def test_AC2_missing_manifest_and_missing_dir(self):
        """Criterion 2: missing manifest, then missing export directory -> same recovery; head identical."""
        self.new_job(); self.activate(); self.intent()
        out = paths.export_dir(self.db, "j")
        os.remove(os.path.join(out, "manifest.txt"))
        self.assertTrue(any(p.startswith("export manifest missing") for p in self._export_problems()))
        self._export_ok()
        shutil.rmtree(out)
        self.assertFalse(os.path.exists(out))
        self.assertTrue(any(p.startswith("export manifest missing") for p in self._export_problems()))
        r = self._export_ok()
        self.assertEqual(os.path.normcase(r["export"]["dir"]), os.path.normcase(out))
        self.assertEqual(sorted(os.listdir(out)), ["contract.json", "events.jsonl", "head.json", "manifest.txt"])

    def _block_export_dir(self) -> str:
        out = paths.export_dir(self.db, "j")
        shutil.rmtree(out)  # the export dir this test's own setup created seconds earlier (T31b precedent)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("block")
        return out

    def _remove_sentinel_7A(self, out: str) -> None:
        """Option 7A: delete one regular file this test created at its own export path, after containment."""
        real_out, real_dir, real_tmp = os.path.realpath(out), os.path.realpath(self.dir), os.path.realpath(TMP)
        real_fx = os.path.realpath(paths.FIXTURES_TMP)
        sep = os.sep
        self.assertTrue(real_out.startswith(real_dir + sep), (real_out, real_dir))          # (a) inside per-test dir
        self.assertTrue(real_dir.startswith(real_tmp + sep), (real_dir, real_tmp))          # (a) inside fresh WF_TEST_TMP
        self.assertTrue(real_tmp.startswith(real_fx + sep), (real_tmp, real_fx))            # (a) under fixtures/tmp
        self.assertTrue(os.path.isfile(out) and not os.path.islink(out), out)               # (b) regular file, not a link
        self.assertEqual(os.lstat(out).st_mode & 0o170000, 0o100000)                        # (b) not a reparse/dir target
        self.assertFalse(os.path.isdir(out))
        with open(out, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "block")                                             # (c) the sentinel content
        real_db = os.path.realpath(self.db)
        self.assertFalse(real_out.startswith(real_db))                                      # (d) ledger not a prefix
        self.assertNotEqual(real_out, real_db)
        self.assertFalse(real_out.startswith(os.path.realpath(os.path.dirname(real_db)) + sep))  # not under ledger/
        for kept in ("results", "runner"):  # cannot target preserved evidence or the runner
            self.assertFalse(real_out.startswith(os.path.realpath(os.path.join(paths.REPO_ROOT, "fixtures", kept)) + sep))
        self.assertFalse(real_out.startswith(os.path.realpath(os.path.join(paths.REPO_ROOT, "ledger")) + sep))
        self.assertFalse(real_out.startswith(os.path.realpath(os.path.join(paths.REPO_ROOT, "tools")) + sep))
        os.remove(out)  # the one authorized deletion: a single regular file, no recursion, no wildcard
        self.assertFalse(os.path.lexists(out))

    def test_AC3_t31b_scenario_export_repairs_in_place(self):
        """Criterion 3: export dir replaced by a file -> append COMMITTED with export error, verify not clean; the
        sentinel is removed under 7A; export -> ok; verify clean; no new row (head seq unchanged by the export)."""
        self.new_job(); self.activate()
        out = self._block_export_dir()
        seq_before = self._head()[0]
        c, r = self.intent(note="refresh-fails")
        self.assertEqual(c, 0, r); self.assertEqual(r["outcome"], "COMMITTED")
        self.assertIn("error", r["export"], r["export"])
        self.assertEqual(self._head()[0], seq_before + 1)
        self.assertEqual(markers.list_markers(self.db, "j"), [])
        self.assertFalse(commands.verify(self.db, "j")["clean"])
        self._remove_sentinel_7A(out)
        head_before = self._head()
        self._export_ok()
        self.assertEqual(self._head(), head_before)
        self.assertEqual(self._head()[0], seq_before + 1)  # the export added no row
        self.assertTrue(os.path.isdir(out))

    def test_AC4_refresh_failure_surfaced(self):
        """Criterion 4: with the blocking file still in place, export -> exit 1, code export-error, result carries the
        refresh error text; ledger unchanged (head, rows, bytes); nothing deleted by the command."""
        self.new_job(); self.activate(); self.intent()
        out = self._block_export_dir()
        before = self._head(); db_sha = _sha(self.db)
        c, r = self.run_wf("export", "j")
        self.assertEqual(c, 1, r)
        self.assertFalse(r["ok"]); self.assertEqual(r["code"], "export-error")
        self.assertIn("error", r["export"]); self.assertEqual(r["error"], r["export"]["error"])
        self.assertRegex(r["export"]["error"], r"^[A-Za-z]+Error: ")
        self.assertEqual(r["problems"], [])
        self._assert_ledger_identity(before)
        self.assertEqual(_sha(self.db), db_sha)
        self.assertTrue(os.path.isfile(out))  # the command removes nothing
        with open(out, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "block")
        self.assertFalse(commands.verify(self.db, "j")["clean"])
        self._remove_sentinel_7A(out)  # cleanup of this test's own sentinel, same containment

    def test_AC5_unknown_job_refused(self):
        """Criterion 5: unknown job -> refused, exit 1, no directory created."""
        self.new_job(); self.activate()
        before = self._head(); db_sha = _sha(self.db)
        c, r = self.run_wf("export", "nope")
        self.assertEqual(c, 1, r); self.assertEqual(r["code"], "refused"); self.assertIn("not found", r["error"])
        self.assertFalse(os.path.exists(paths.export_dir(self.db, "nope")))
        self.assertFalse(os.path.exists(os.path.join(paths.jobs_dir(self.db), "nope")))
        self._assert_ledger_identity(before)
        self.assertEqual(_sha(self.db), db_sha)

    def test_AC6_bounded_read_under_held_write_lock(self):
        """Criterion 6: export while another connection holds the write lock waits boundedly (same mechanism as
        status): completes once a short hold ends; returns lock-timeout within T_ACQ_BOUND for a longer hold."""
        self.new_job(); self.activate(); self.intent()
        holder = sqlite3.connect(self.db, isolation_level=None, check_same_thread=False)
        holder.execute("BEGIN EXCLUSIVE")
        threading.Timer(0.5, lambda: holder.execute("ROLLBACK")).start()
        t0 = time.monotonic()
        self._export_ok()
        self.assertGreaterEqual(time.monotonic() - t0, 0.4)
        time.sleep(0.1)
        hold = dbm.T_ACQ_BOUND + 1.5
        holder.execute("BEGIN EXCLUSIVE")
        timer = threading.Timer(hold, lambda: holder.execute("ROLLBACK")); timer.start()
        t0 = time.monotonic()
        c, r = self.run_wf("export", "j")
        elapsed = time.monotonic() - t0
        self.assertEqual(c, 1, r); self.assertEqual(r["code"], "lock-timeout")
        self.assertLess(elapsed, hold, "export waited past the acquisition bound")
        timer.join(); time.sleep(0.1); holder.close()
        self.assertTrue(commands.verify(self.db, "j")["clean"])


if __name__ == "__main__":
    unittest.main()
