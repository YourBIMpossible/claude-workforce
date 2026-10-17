"""T30 (a) racing half, (f) second write connection, (h) database replaced between commands (B2 slice 5, lane S5R;
INTERFACE-CONTRACT-S5.md sections 3 and 6 S5R, decisions D-S5-1 and D-S5-2).

Fixture ledgers only (WF_TEST_TMP under fixtures\\tmp); no real ledger, no restore, no schema or transaction change.
* T30(a): two `wf recover begin` processes (`python -I tools/wf/wf.py`) started together on one fixture ledger, in
  several rounds: exactly one `recovery-opened` per round; the other process is refused `recovery-open` or exits
  busy, with nothing appended; the chain verifies.
* T30(f): the test-only fault point `recover-second-writer` (White, S5W, in `commands.recover_begin`) opens a
  second connection and attempts `BEGIN IMMEDIATE` while the reservation is held: the command refuses, nothing is
  appended, no `recovery-opened` row exists, the chain verifies. Skipped, named, until the fault point is on the tree.
* T30(h): the ledger file is replaced by another internally consistent ledger between commands. Detected with the
  retained checkpoint (`wf verify --checkpoint`) and with the gate's identity (`gatecore.ledger_identity` /
  `decide(expected_identity=...)`, S4G; skipped, named, when absent). Without either, `wf verify` passes and its
  output states that it shows internal consistency only (D-S5-1: no identity field in any event).
Nothing here deletes test artifacts.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest

from test_wf import WF_DIR, Base, dbm, log, wf  # noqa: E402
from wfcore import faults  # noqa: E402

try:
    from wfcore import gatecore  # noqa: E402
except ImportError:  # lane S4G not on this tree
    gatecore = None
try:
    from wfcore import backup  # noqa: E402
except ImportError:  # lane S5B not on this tree
    backup = None

WF_PY = os.path.join(WF_DIR, "wf.py")
FAULT = "recover-second-writer"
SKIP_FAULT = "fault point not present on this tree (recover-second-writer lands on sprint/white-b2s5-wiring, S5W)"
SKIP_GATE = "gatecore.ledger_identity not present on this tree (lane S4G)"
SKIP_BACKUP = "backup.verify_copy not present on this tree (lane S5B)"
ROUNDS = 4
BUSY_WORDS = ("busy", "locked", "lock-timeout", "lock timeout")


class T30Base(Base):
    def job_rows(self, job: str = "j", db: str | None = None) -> list[dict]:
        conn = dbm.connect(db or self.db, create=False)
        try:
            return dbm.rows(conn, job)
        finally:
            conn.close()

    def chain_problems(self, job: str = "j", db: str | None = None) -> list[str]:
        conn = dbm.connect(db or self.db, create=False)
        try:
            return dbm.verify_chain(conn, job)
        finally:
            conn.close()

    def recover_argv(self, job: str, *, db: str | None = None) -> list[str]:
        return [sys.executable, "-I", WF_PY, "--db", db or self.db, "--role", "coordinator", "--session", "C1", "--json",
                "recover", "begin", job, "--reason", "race test", "--turn", f"owner turn naming job {job}"]

    def env(self, **extra: str) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k != faults.ENV}
        env.update(extra)
        return env

    def run_wf_db(self, db: str, *argv: str, role: str = "coordinator", session: str = "C1"):
        return wf.run(["--db", db, "--role", role, "--session", session, *argv])

    def job_on(self, db: str, job: str = "j", note: str = "n") -> None:
        """Contract, grant activated, one builder intent, on ledger `db` (through `wf.run`)."""
        code, res = self.run_wf_db(db, "job", "new", job, "--contract", self.cpath)
        self.assertEqual(code, 0, res)
        code, res = self.run_wf_db(db, "grant", "propose", job, "--grant", self.gpath)
        self.assertEqual(code, 0, res)
        h = res["block_hash"]
        code, res = self.run_wf_db(db, "grant", "activate", job, "--owner-hash", h[:8], "--quote", f"approve {h[:8]}",
                                   "--turn", "owner turn")
        self.assertEqual(code, 0, res)
        code, res = self.run_wf_db(db, "append", job, "--type", "intent", "--content",
                                   json.dumps({"path": f"jobs/{job}/work/a.txt", "sha_before": "", "note": note}),
                                   role="builder", session="B1")
        self.assertEqual(code, 0, res)


# ----------------------------------------------------------------------------------------------- T30 (a)
class T30aRace(T30Base):
    def test_two_recover_begin_processes_open_exactly_one_recovery(self):
        outcomes = []
        for n in range(ROUNDS):
            job = f"r{n}"
            self.job_on(self.db, job)
            before = self.job_rows(job)
            procs = [subprocess.Popen(self.recover_argv(job), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      env=self.env(), cwd=self.dir) for _ in range(2)]
            results = []
            for p in procs:
                out, err = p.communicate(timeout=120)
                results.append((p.returncode, json.loads(out.decode("utf-8")) if out.strip() else {},
                                err.decode("utf-8", "replace")))
            winners = [r for r in results if r[0] == 0]
            losers = [r for r in results if r[0] != 0]
            self.assertEqual(len(winners), 1, results)
            self.assertEqual(len(losers), 1, results)
            error = (losers[0][1].get("error") or "") + losers[0][2]
            refused = "rejected(recovery-open" in error
            busy = any(w in error.lower() for w in BUSY_WORDS)
            self.assertTrue(refused or busy, f"loser must be refused recovery-open or exit busy: {losers[0]}")
            after = self.job_rows(job)
            opened = [r for r in after if r["type"] == "recovery-opened"]
            self.assertEqual(len(opened), 1, [r["type"] for r in after])
            self.assertEqual(len(after), len(before) + 1)               # the loser appended nothing
            self.assertEqual(opened[0]["seq"], winners[0][1]["seq"])
            self.assertEqual(self.chain_problems(job), [])
            outcomes.append("refused recovery-open" if refused else "busy")
        code, res = self.run_wf("verify")
        self.assertEqual(code, 0, res)
        self.assertTrue(all(not v["problems"] for v in res["jobs"].values()), res["jobs"])
        self.assertEqual(len(outcomes), ROUNDS)
        log(f"{self._testMethodName}-outcomes", outcomes)                 # which legal outcome each round took


# ----------------------------------------------------------------------------------------------- T30 (f)
@unittest.skipUnless(FAULT in faults.KNOWN, SKIP_FAULT)
class T30fSecondWriter(T30Base):
    def test_second_write_connection_cannot_take_the_reservation(self):
        self.job_on(self.db)
        before = self.job_rows()
        start = time.monotonic()
        p = subprocess.run(self.recover_argv("j"), capture_output=True, timeout=120, cwd=self.dir,
                           env=self.env(**{faults.ENV: FAULT}))
        elapsed = time.monotonic() - start
        res = json.loads(p.stdout.decode("utf-8")) if p.stdout.strip() else {}
        error = (res.get("error") or "") + p.stderr.decode("utf-8", "replace")
        self.assertNotEqual(p.returncode, 0, (res, error))               # the command refuses
        self.assertTrue(any(w in error.lower() for w in BUSY_WORDS), f"second BEGIN IMMEDIATE must fail busy/locked: "
                                                                      f"{error}")
        self.assertLess(elapsed, 60.0)                                   # within the bound, not a hang
        after = self.job_rows()
        self.assertEqual([(r["seq"], r["self"]) for r in after], [(r["seq"], r["self"]) for r in before])
        self.assertFalse(any(r["type"] == "recovery-opened" for r in after))
        self.assertEqual(self.chain_problems(), [])
        code, v = self.run_wf("verify", "j")
        self.assertEqual(code, 0, v)
        self.assertEqual(v["jobs"]["j"]["problems"], [])


# ----------------------------------------------------------------------------------------------- T30 (h)
class T30hReplacement(T30Base):
    def setUp(self) -> None:
        super().setUp()
        self.job_on(self.db, note="original")
        rows = self.job_rows()
        self.checkpoint = (rows[-1]["seq"], rows[-1]["self"])
        self.identity = gatecore.ledger_identity(self.db) if gatecore is not None else None
        # An internally consistent replacement: the same job built again in another file, then moved over the
        # ledger between two commands (no process holds the ledger at that moment).
        other_dir = os.path.join(self.dir, "replacement", "ledger")
        os.makedirs(other_dir)
        self.other = os.path.join(other_dir, "l.db")
        self.job_on(self.other, note="replacement")
        os.replace(self.other, self.db)
        self.assertEqual(self.chain_problems(), [])                      # consistent: the chain alone cannot tell
        # The next command after the swap: an export refresh (exports are derived, never authority), so the export
        # manifest matches the replacement and only the chain checks remain to tell the two ledgers apart.
        code, res = self.run_wf("export", "j", role="coordinator", session="C1")
        self.assertEqual(code, 0, res)

    def test_undetectable_without_checkpoint_or_identity_and_said_so(self):
        code, res = self.run_wf("verify", "j")
        self.assertEqual(code, 0, res)
        self.assertTrue(res["clean"], res)                                # the replacement passes level 1-2
        self.assertIn("internal consistency only", res["ladder"])         # ... and the output says what that means
        self.assertIn("not an authenticity claim", res["ladder"])
        self.assertNotIn("checkpoint", res)

    def test_detected_with_the_retained_checkpoint(self):
        seq, self_hash = self.checkpoint
        code, res = self.run_wf("verify", "j", "--checkpoint", f"{seq}:{self_hash}")
        self.assertFalse(res["clean"], res)
        self.assertEqual(res["checkpoint"]["status"], "mismatch")
        self.assertEqual(res["checkpoint"]["expected_self"], self_hash)
        self.assertNotEqual(res["checkpoint"]["found_self"], self_hash)
        self.assertTrue(any("retained checkpoint" in p for p in res["jobs"]["j"]["problems"]))
        self.assertIn("trusted as supplied", res["checkpoint"]["trust"])

    def test_detected_as_missing_when_the_replacement_is_shorter(self):
        seq, self_hash = self.checkpoint
        code, res = self.run_wf("verify", "j", "--checkpoint", f"{seq + 5}:{self_hash}")
        self.assertFalse(res["clean"], res)
        self.assertEqual(res["checkpoint"]["status"], "missing")

    @unittest.skipUnless(gatecore is not None and hasattr(gatecore, "ledger_identity"), SKIP_GATE)
    def test_detected_with_the_gate_identity(self):
        after = gatecore.ledger_identity(self.db)
        self.assertNotEqual((self.identity["dev"], self.identity["ino"]), (after["dev"], after["ino"]),
                            (self.identity, after))
        payload = {"session_id": "B1", "hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": self.dir,
                   "tool_input": {"file_path": os.path.join(self.dir, "jobs", "j", "work", "a.txt"), "content": "x"}}
        d = gatecore.decide(payload, root=self.dir, db_path=self.db, now=time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                                         time.gmtime()),
                            gate_files=[], expected_identity=self.identity)
        self.assertEqual((d["decision"], d["reason"]), ("deny", "ledger-identity"), d)

    @unittest.skipUnless(backup is not None and hasattr(backup, "verify_copy"), SKIP_BACKUP)
    def test_restored_copy_check_states_the_limit_without_checkpoint(self):
        res = backup.verify_copy(self.db)
        self.assertIn(backup.LIMIT_NO_CHECKPOINT, json.dumps(res))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
