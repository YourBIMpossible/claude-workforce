"""T30 (b) fault-injected recovery re-runs and T30 (a) second `recover begin` — lane S2C (MyBuddy), WF-003.
Contract: INTERFACE-CONTRACT-S2.md section 4 (fault points) and section 8 S2C; spec FIRST-RELEASE-SPEC.md T30.

Every case drives `python -I tools/wf/wf.py ...` as a fresh subprocess with `WF_TEST_FAULTS` set on a fixtures-tmp
ledger (the only place the fault seam is honoured). The kill is the wiring's `os._exit(3)` after the named
transaction committed; a killed process exits 3 and prints nothing, so the ledger rows are the evidence.

Subcases (spec T30, reported by letter):
  (a) `test_T30a_second_recover_begin_refused_while_open`   two `recover begin` from one process sequence: the
      second is `rejected(recovery-open)`, exactly one `recovery-opened` row.
  (b) `test_T30b_kill_after_open`                            recoverer killed right after `recovery-opened`.
      `test_T30b_kill_mid_reconcile_k0`                      killed before any acknowledgement committed (k = 0).
      `test_T30b_kill_mid_reconcile_k1`                      killed after one acknowledgement (k = 1).
      `test_T30b_kill_mid_reconcile_all_but_one`             killed after all but one (k = n-1).
      `test_T30b_kill_before_close`                          killed before `recovery-closed`.
      After each kill the same commands are re-run (`recover reconcile`, `recover close`); the invariant is the
      contract's: the same final state as an unkilled run, exactly one `recovery-opened`, each listed exception id
      acknowledged exactly once, exactly one `recovery-closed`, chain valid, no partial row.
  (c), (d), (e), (g), (i) belong to S2R / S2W (test_t30_recover_close.py, test_b2s2_wiring.py); not here.
  (f) recovery helper opening a second write connection and (h) database replaced between commands: NOT RUN
      (excluded by the contract; listed so the closeout can say so).

Wiring dependency: the `recover reconcile | close` verbs and the fault names land on White's
`sprint/white-b2s2-wiring`. On a tree without them every test here is skipped with the reason "wiring not present
on this tree"; a skip is not completion. Nothing outside this file is touched; fixtures live under WF_TEST_TMP.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from test_wf import Base, WF_DIR  # noqa: E402  (sets sys.path to the wf package; fresh WF_TEST_TMP)
from test_b2s1_wiring import GRANT_ARS  # noqa: E402  (slice-1 ARS grant fixture; no test class imported)
from wfcore import commands, db as dbm, faults as faultmod  # noqa: E402

FAULTS = ("recover-kill-after-open", "recover-kill-mid-reconcile", "recover-kill-before-close")
WIRED = all(n in faultmod.KNOWN for n in FAULTS) and all(
    hasattr(commands, f) for f in ("recover_reconcile", "recover_close"))
SKIP = "wiring not present on this tree (sprint/white-b2s2-wiring: recover reconcile/close verbs and fault names)"
TURN = "owner: recover j (T30 fault re-run)"
ORPHANS = ("events.jsonl.tmp", "head.json.tmp", "contract.json.tmp")  # legacy fixed staging names = orphans
RECOVERY_TYPES = ("recovery-opened", "exception-acknowledged", "recovery-closed")
KILLED = 3  # wiring: os._exit(3)


@unittest.skipUnless(WIRED, SKIP)
class T30RecoveryFaults(Base):
    # ---------------------------------------------------------------- fixture
    def setUp(self) -> None:
        super().setUp()
        self.gpath_ars = os.path.join(self.dir, "grant_ars.json")
        with open(self.gpath_ars, "w", encoding="utf-8") as fh:
            json.dump(GRANT_ARS, fh)

    def coord(self, *argv: str, **kw):
        return self.run_wf(*argv, role="coordinator", session="C1", **kw)

    def job_with_exceptions(self, n: int = len(ORPHANS)) -> list[str]:
        """Explicit job (ARS grant, coordinator + builder bound), one intent, a fresh export, then `n` orphan
        staging files: `recover begin` lists exactly n recoverable exceptions R1..Rn."""
        self.new_job()
        c, r = self.coord("grant", "propose", "j", "--grant", self.gpath_ars); self.assertEqual(c, 0, r)
        h = r["block_hash"]
        c, r = self.coord("grant", "activate", "j", "--owner-hash", h[:8], "--quote", f"approve G1 {h[:8]}",
                          "--turn", "owner turn"); self.assertEqual(c, 0, r)
        for brole, bsess in (("coordinator", "C1"), ("builder", "B1")):
            c, r = self.coord("bind", "j", "--bind-role", brole, "--bind-session", bsess); self.assertEqual(c, 0, r)
        c, r = self.intent(); self.assertEqual(c, 0, r)
        c, r = self.run_wf("export", "j"); self.assertEqual(c, 0, r)
        out = r["export"]["dir"]
        for name in ORPHANS[:n]:
            with open(os.path.join(out, name), "xb") as fh:
                fh.write(b"orphan\n")
        return [f"R{i + 1}" for i in range(n)]

    # ---------------------------------------------------------------- subprocess driver
    def wf_proc(self, *argv: str, faults: str | None = None, timeout: float = 60.0) -> tuple[int, dict]:
        """One fresh coordinator process. Exit code and parsed JSON ({} when the process printed nothing, e.g. killed)."""
        env = dict(os.environ)
        env.pop("WF_TEST_FAULTS", None)
        if faults:
            env["WF_TEST_FAULTS"] = faults
        cmd = [sys.executable, "-I", "-B", os.path.join(WF_DIR, "wf.py"), "--json", "--db", self.db,
               "--role", "coordinator", "--session", "C1", *argv]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        try:
            res = json.loads(p.stdout) if p.stdout.strip() else {}
        except ValueError:
            res = {"raw": p.stdout}
        res.setdefault("_stderr", p.stderr[-1500:])
        return p.returncode, res

    def begin(self, faults: str | None = None) -> tuple[int, dict]:
        return self.wf_proc("recover", "begin", "j", "--reason", "orphan staging files", "--turn", TURN, faults=faults)

    def reconcile(self, faults: str | None = None) -> tuple[int, dict]:
        return self.wf_proc("recover", "reconcile", "j", "--turn", TURN, faults=faults)

    def close(self, faults: str | None = None) -> tuple[int, dict]:
        return self.wf_proc("recover", "close", "j", "--turn", TURN, faults=faults)

    # ---------------------------------------------------------------- ledger inspection
    def rows(self) -> list[dict]:
        conn = dbm.connect(self.db, create=False)
        try:
            return dbm.rows(conn, "j")
        finally:
            conn.close()

    def recovery_rows(self) -> list[dict]:
        return [r for r in self.rows() if r["type"] in RECOVERY_TYPES]

    def acked_ids(self) -> list[str]:
        return [json.loads(r["content"])["id"] for r in self.rows() if r["type"] == "exception-acknowledged"]

    def count(self, etype: str) -> int:
        return sum(1 for r in self.rows() if r["type"] == etype)

    @staticmethod
    def trace(rows: list[dict]) -> list[tuple]:
        """Position-independent recovery trace: (type, id-or-None, class, sorted acknowledged list). Seq, ts, msg_id
        and hashes differ between runs by construction; the contract's "same final state" is about these rows."""
        out = []
        for r in rows:
            c = json.loads(r["content"])
            if r["type"] == "recovery-opened":
                out.append(("recovery-opened", tuple((e["id"], e["class"], e["detail"]) for e in c["exceptions"])))
            elif r["type"] == "exception-acknowledged":
                out.append(("exception-acknowledged", c["id"], c["class"], c["detail"], c["owner_turn"]))
            else:
                out.append(("recovery-closed", tuple(c["acknowledged"]), c["owner_turn"]))
        return out

    def assert_invariant(self, ids: list[str]) -> list[tuple]:
        """Contract section 4 T30(b) re-run invariant on the current ledger."""
        self.assertEqual(self.count("recovery-opened"), 1, "exactly one recovery-opened")
        self.assertEqual(sorted(self.acked_ids()), sorted(ids), "each listed id acknowledged exactly once")
        self.assertEqual(self.count("recovery-closed"), 1, "exactly one recovery-closed")
        closed = [r for r in self.rows() if r["type"] == "recovery-closed"][0]
        self.assertEqual(json.loads(closed["content"])["acknowledged"], sorted(ids))
        c, st = self.coord("status", "j"); self.assertEqual(c, 0, st)
        self.assertEqual(st["recovery"], {"open": False})
        v = commands.verify(self.db, "j")
        self.assertEqual(v["jobs"]["j"]["problems"], [], v)              # chain valid, no partial row
        self.assertEqual(v["ledger_problems"], {"schema": [], "integrity": []})
        self.assertIsNone(v["jobs"]["j"].get("recovery_open"))
        return self.trace(self.recovery_rows())

    def reference_trace(self, ids: list[str]) -> list[tuple]:
        """The unkilled cycle on a second, independent ledger in this test's scratch (begin → reconcile → close)."""
        saved_db, saved_dir = self.db, self.dir
        self.dir = os.path.join(saved_dir, "reference"); os.makedirs(self.dir)
        self.db = os.path.join(self.dir, "ledger", "l.db")
        try:
            got = self.job_with_exceptions(len(ids)); self.assertEqual(got, ids)
            for step in (self.begin, self.reconcile, self.close):
                c, r = step(); self.assertEqual(c, 0, r)
            return self.assert_invariant(ids)
        finally:
            self.db, self.dir = saved_db, saved_dir

    def assert_killed(self, code_res: tuple[int, dict]) -> None:
        code, res = code_res
        self.assertEqual(code, KILLED, res)
        self.assertNotIn("ok", res, "a killed process prints no result")

    # ---------------------------------------------------------------- T30 (a)
    def test_T30a_second_recover_begin_refused_while_open(self):
        ids = self.job_with_exceptions()
        c, r = self.begin(); self.assertEqual(c, 0, r)
        self.assertEqual([e["id"] for e in r["exceptions"]], ids)
        n = len(self.rows())
        c, r2 = self.begin()
        self.assertEqual(c, 1, r2)
        self.assertEqual(r2.get("code"), "rejected", r2)
        self.assertIn("rejected(recovery-open)", r2.get("error", ""), r2)
        self.assertEqual(len(self.rows()), n)                      # nothing appended
        self.assertEqual(self.count("recovery-opened"), 1)
        c, st = self.coord("status", "j")
        self.assertEqual((st["recovery"]["open"], st["recovery"]["exceptions"], st["recovery"]["pending"]),
                         (True, len(ids), len(ids)))

    # ---------------------------------------------------------------- T30 (b)
    def test_T30b_kill_after_open(self):
        ids = self.job_with_exceptions()
        self.assert_killed(self.begin(faults="recover-kill-after-open"))
        self.assertEqual(self.count("recovery-opened"), 1)        # the open transaction had committed
        self.assertEqual(self.acked_ids(), [])
        c, st = self.coord("status", "j"); self.assertTrue(st["recovery"]["open"], st)
        c, r = self.reconcile(); self.assertEqual(c, 0, r)
        self.assertEqual((sorted(r["acknowledged"]), r["already"]), (sorted(ids), []))
        c, r = self.close(); self.assertEqual(c, 0, r)
        self.assertEqual(self.assert_invariant(ids), self.reference_trace(ids))

    def _mid_reconcile(self, k: int) -> None:
        ids = self.job_with_exceptions()
        c, r = self.begin(); self.assertEqual(c, 0, r)
        self.assert_killed(self.reconcile(faults=f"recover-kill-mid-reconcile:{k}"))
        self.assertEqual(self.acked_ids(), ids[:k], f"exactly k={k} acknowledgements committed before the kill")
        self.assertEqual(self.count("recovery-closed"), 0)
        c, st = self.coord("status", "j")
        self.assertEqual((st["recovery"]["acknowledged"], st["recovery"]["pending"]), (k, len(ids) - k))
        c, r = self.reconcile(); self.assertEqual(c, 0, r)       # re-run appends only the missing ones
        self.assertEqual((r["acknowledged"], r["already"]), (ids[k:], ids[:k]))
        c, r = self.close(); self.assertEqual(c, 0, r)
        self.assertEqual(self.assert_invariant(ids), self.reference_trace(ids))

    def test_T30b_kill_mid_reconcile_k0(self):
        """k = 0: the recoverer dies before any acknowledgement commits (contract: "arg = number of acks to commit
        before exiting")."""
        self._mid_reconcile(0)

    def test_T30b_kill_mid_reconcile_k1(self):
        self._mid_reconcile(1)

    def test_T30b_kill_mid_reconcile_all_but_one(self):
        self._mid_reconcile(len(ORPHANS) - 1)

    def test_T30b_kill_before_close(self):
        ids = self.job_with_exceptions()
        c, r = self.begin(); self.assertEqual(c, 0, r)
        c, r = self.reconcile(); self.assertEqual(c, 0, r)
        n = len(self.rows())
        self.assert_killed(self.close(faults="recover-kill-before-close"))
        self.assertEqual(len(self.rows()), n)                      # nothing appended by the killed close
        self.assertEqual(self.count("recovery-closed"), 0)
        c, st = self.coord("status", "j"); self.assertTrue(st["recovery"]["open"], st)
        c, r = self.reconcile(); self.assertEqual(c, 0, r)       # idempotent: nothing left to acknowledge
        self.assertEqual((r["acknowledged"], sorted(r["already"])), ([], sorted(ids)))
        c, r = self.close(); self.assertEqual(c, 0, r)
        self.assertEqual(self.assert_invariant(ids), self.reference_trace(ids))

    def test_T30b_unknown_fault_name_refused_on_fixture_ledger(self):
        """The seam rejects a name outside KNOWN before anything runs (guards the three names being registered)."""
        self.job_with_exceptions()
        c, r = self.begin(faults="recover-kill-nowhere")
        self.assertEqual((c, r.get("code")), (2, "faults-not-allowed"), r)
        self.assertEqual(self.count("recovery-opened"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
