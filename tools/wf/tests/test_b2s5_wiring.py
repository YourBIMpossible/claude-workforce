"""B2 slice 5: CLI tests for the S5W wiring (INTERFACE-CONTRACT-S5 sections 1-3 and 5; lane S5W).

`wf backup` (coordinator or owner; refusals write nothing; ledger unchanged), `wf verify --restored` (read-only,
checkpoint matches/differs/missing, "not detectable" without a checkpoint) and the test-only fault point
`recover-second-writer` in `recover begin` (refused busy/locked, nothing appended, chain verifies). The backup
module itself is tested by lane S5B and T30 by lane S5R.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import unittest
from unittest import mock

from test_wf import Base  # noqa: E402

from wfcore import db as dbm  # noqa: E402
from wfcore import faults  # noqa: E402

try:
    from wfcore import backup  # noqa: E402
except ImportError:  # pragma: no cover - only before the S5B lane is present
    backup = None

SKIP = "backup module not present on this tree (lane S5B)"
FAULT = "recover-second-writer"


def _sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _listing(top: str) -> list[str]:
    out = []
    for d, _dirs, files in os.walk(top):
        for f in files:
            out.append(os.path.relpath(os.path.join(d, f), top))
    return sorted(out)


class B2S5Wiring(Base):
    def rows(self, job: str = "j") -> list[tuple[int, str, str]]:
        conn = sqlite3.connect(self.db)
        try:
            return [tuple(r) for r in conn.execute("SELECT seq, self, type FROM events WHERE job=? ORDER BY seq",
                                                   (job,))]
        finally:
            conn.close()

    def out(self, name: str) -> str:
        d = os.path.join(self.dir, "out")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, name)

    # ---- wf backup
    @unittest.skipUnless(backup is not None, SKIP)
    def test_s5w_backup_cli_copies_and_verifies(self):
        self.new_job()
        before = _sha(self.db)
        dest = self.out("b1.db")
        code, r = self.run_wf("backup", "--all", "--to", dest, role="coordinator", session="C1")
        self.assertEqual(code, 0, r)
        self.assertTrue(os.path.isfile(dest))
        self.assertTrue(r["integrity"], r)
        self.assertEqual(r["chains"], {"j": []})
        self.assertTrue(r["head_in_source"]["j"])
        self.assertIsNone(r["head_equal"])                                   # writer not stated stopped
        self.assertTrue(any("not asserted" in lim for lim in r["limitations"]), r)
        self.assertTrue(r["clean"])
        self.assertEqual(r["scope"], "all")
        self.assertIsNone(r["msg_id"])                                       # read-only command
        self.assertEqual(_sha(self.db), before)                             # ledger unchanged
        code, r = self.run_wf("backup", "j", "--to", self.out("b2.db"), "--writer-stopped", role="owner", session="O1")
        self.assertEqual(code, 0, r)
        self.assertEqual(r["scope"], "j")
        self.assertEqual(r["head_equal"], {"j": True})
        self.assertEqual(r["limitations"], [])

    @unittest.skipUnless(backup is not None, SKIP)
    def test_s5w_backup_cli_refusals_write_nothing(self):
        self.new_job()
        existing = self.out("exists.db")
        with open(existing, "wb") as fh:
            fh.write(b"keep")
        cases = [
            (existing, "dest-exists"),
            (os.path.join(self.dir, "no-such-dir", "b.db"), "dest-parent-missing"),
            (os.path.join(os.path.dirname(self.db), "b.db"), "dest-inside-ledger"),
        ]
        ledger_sha = _sha(self.db)
        for dest, reason in cases:
            before = _listing(self.dir)
            code, r = self.run_wf("backup", "--all", "--to", dest, role="coordinator", session="C1")
            self.assertNotEqual(code, 0, r)
            self.assertIn(reason, r["error"], r)
            self.assertEqual(_listing(self.dir), before, reason)            # nothing written anywhere
            self.assertEqual(_sha(self.db), ledger_sha, reason)             # ledger bytes unchanged
        with open(existing, "rb") as fh:
            self.assertEqual(fh.read(), b"keep")

    def test_s5w_backup_cli_role_scope_and_unknown_job(self):
        self.new_job()
        target = self.out("x.db")
        code, r = self.run_wf("backup", "--all", "--to", target, role="builder", session="B1")
        self.assertNotEqual(code, 0, r)
        self.assertIn("rejected(role)", r["error"])
        code, r = self.run_wf("backup", "j", "--all", "--to", target, role="coordinator", session="C1")
        self.assertEqual(code, 2, r)                                          # usage: exactly one of job / --all
        code, r = self.run_wf("backup", "--to", target, role="coordinator", session="C1")
        self.assertEqual(code, 2, r)
        if backup is not None:
            code, r = self.run_wf("backup", "nojob", "--to", target, role="coordinator", session="C1")
            self.assertNotEqual(code, 0, r)
            self.assertIn("not in the ledger", r["error"])
        self.assertFalse(os.path.exists(target))

    @unittest.skipUnless(backup is not None, SKIP)
    def test_s5w_backup_cli_clean_requires_heads_to_match_source(self):
        self.new_job()
        self.new_job("k")
        real = backup.backup

        def diverged(key, job):
            def fake(db_path, dest_path, *, writer_stopped=False):
                res = real(db_path, dest_path, writer_stopped=writer_stopped)
                res[key][job] = False
                return res
            return fake

        cases = [("head_in_source", "k", ("--all",)),                        # the head is not in the source
                 ("head_in_source", "k", ("j",)),                            # clean covers jobs outside the scope
                 ("head_equal", "j", ("--all", "--writer-stopped"))]         # stated stopped, head differs
        for i, (key, job, args) in enumerate(cases):
            with mock.patch.object(backup, "backup", diverged(key, job)):
                code, r = self.run_wf("backup", *args, "--to", self.out(f"d{i}.db"), role="owner", session="O1")
            self.assertEqual(code, 0, r)
            self.assertTrue(r["integrity"], r)
            self.assertTrue(r["all_chains_clean"], r)
            self.assertFalse(r["clean"], (key, job, args, r))
        code, r = self.run_wf("backup", "j", "--to", self.out("ok.db"), "--writer-stopped", role="owner", session="O1")
        self.assertEqual(code, 0, r)
        self.assertTrue(r["clean"], r)                                       # unmodified: every head matches

    # ---- wf verify --restored
    @unittest.skipUnless(backup is not None, SKIP)
    def test_s5w_verify_restored_checkpoint_statuses(self):
        self.new_job()
        rows = self.rows()
        seq, self_hash = rows[-1][0], rows[-1][1]
        dest = self.out("r.db")
        code, r = self.run_wf("backup", "--all", "--to", dest, role="owner", session="O1")
        self.assertEqual(code, 0, r)
        copy_sha = _sha(dest)
        code, v = self.run_wf("verify", "--restored", dest)
        self.assertEqual(code, 0, v)
        self.assertTrue(v["clean"], v)
        self.assertIsNone(v["checkpoint"])
        self.assertTrue(any("not detectable" in lim for lim in v["limitations"]), v)
        code, v = self.run_wf("verify", "--restored", dest, "--job", "j", "--checkpoint", f"{seq}:{self_hash}")
        self.assertEqual(code, 0, v)
        self.assertEqual(v["checkpoint"]["status"], "matches")
        self.assertTrue(v["clean"])
        self.assertTrue(any("trusted as supplied" in lim for lim in v["limitations"]), v)
        code, v = self.run_wf("verify", "--restored", dest, "--job", "j", "--checkpoint", f"{seq}:{'0' * 64}")
        self.assertEqual(code, 0, v)
        self.assertEqual(v["checkpoint"]["status"], "differs")
        self.assertFalse(v["clean"])
        code, v = self.run_wf("verify", "--restored", dest, "--job", "j", "--checkpoint", f"{seq + 5}:{self_hash}")
        self.assertEqual(code, 0, v)
        self.assertEqual(v["checkpoint"]["status"], "missing")
        self.assertFalse(v["clean"])
        self.assertEqual(_sha(dest), copy_sha)                               # never written, repaired or deleted

    def test_s5w_verify_restored_usage(self):
        self.new_job()
        code, v = self.run_wf("verify", "--restored", self.db, "--checkpoint", "1:" + "0" * 64)
        self.assertEqual(code, 2, v)                                         # --checkpoint needs --job
        code, v = self.run_wf("verify", "--restored", self.db, "--gate-file", self.db, "--gate-sha", "0" * 64)
        self.assertEqual(code, 2, v)
        code, v = self.run_wf("verify", "j", "--job", "j")
        self.assertEqual(code, 2, v)                                         # --job only with --restored
        code, v = self.run_wf("verify", "j", "--restored", self.db)
        self.assertEqual(code, 2, v)                                         # a job is never silently ignored
        code, v = self.run_wf("verify", "--restored", self.db, "--job", "j")
        self.assertEqual(code, 2, v)
        absent = os.path.join(self.dir, "absent.db")
        code, v = self.run_wf("verify", "--restored", absent)
        self.assertNotEqual(code, 0, v)
        self.assertFalse(os.path.exists(absent))

    # ---- fault point recover-second-writer (T30 f)
    def test_s5w_fault_point_known(self):
        self.assertIn(FAULT, faults.KNOWN)

    def test_s5w_recover_second_writer_refuses_and_appends_nothing(self):
        self.new_job()
        self.activate()
        before = self.rows()
        with mock.patch.dict(os.environ, {faults.ENV: FAULT}):
            code, r = self.run_wf("recover", "begin", "j", "--reason", "fault test", "--turn",
                                  "owner turn naming job j", role="coordinator", session="C1")
        self.assertNotEqual(code, 0, r)
        self.assertIs(r["ok"], False)
        self.assertEqual(r["code"], "refused", r)
        self.assertEqual(r.get("second_writer"), "refused", r)
        self.assertTrue("busy" in r["error"].lower() or "locked" in r["error"].lower(), r)
        self.assertIn("nothing appended", r["error"])
        after = self.rows()
        self.assertEqual(after, before)
        self.assertNotIn("recovery-opened", [t for _s, _h, t in after])
        conn = dbm.connect(self.db, create=False)
        try:
            self.assertEqual(dbm.verify_chain(conn, "j"), [])
        finally:
            conn.close()
        code, v = self.run_wf("verify", "j")
        self.assertEqual(code, 0, v)
        self.assertEqual(v["jobs"]["j"]["problems"], [])
        code, r = self.run_wf("recover", "begin", "j", "--reason", "no fault", "--turn", "owner turn naming job j",
                              role="coordinator", session="C1")
        self.assertEqual(code, 0, r)                                         # without the fault the command works

    def test_s5w_recover_second_writer_refused_outside_fixtures(self):
        product = os.path.join(os.path.dirname(os.path.abspath(dbm.__file__)), "not-a-fixture.db")
        with mock.patch.dict(os.environ, {faults.ENV: FAULT}):
            with self.assertRaises(faults.FaultsNotAllowed):
                faults.load(product)
        self.assertFalse(os.path.exists(product))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
