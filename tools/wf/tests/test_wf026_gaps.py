"""WF-026 (Plexy): tests for the WF-019 proposed gaps (PROPOSED-TESTS.md, finding F8), on fixtures only.

A test that passes pins current behaviour for a clause of FIRST-RELEASE-SPEC Rev 5.3 §8 that had no test at 2dedc94.
A test where the product contradicts the spec asserts the spec, is marked `unittest.expectedFailure`, and states the
observed behaviour in its docstring as a finding. No product code is changed by this file.

Own helpers (not a subclass of B2S1/B2S2): subclassing those would re-run their test methods under the same names and
collide on the per-method fixture folders (AMENDMENT S4A-A3 item 4).
"""
from __future__ import annotations

import json
import os
import unittest

from test_b2s1_wiring import GRANT_ARS, STOP  # noqa: E402  (fixture constants only; no test class imported)
from test_wf import Base, GRANT  # noqa: E402
from wfcore import gatecore  # noqa: E402

NOW = "2026-10-19T00:00:00Z"
DELIVERED = '{"claims":[{"claim":"c","evidence":["e"]}]}'
GRANT_REPL = json.loads(json.dumps(GRANT_ARS))
GRANT_REPL["ars"]["replacement"] = {"allowed": True, "max": 1, "expiry": "2030-01-01T00:00:00Z"}


class _Wf026(Base):
    def write_grant(self, name: str, grant: dict) -> str:
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(grant, fh)
        return p

    def coord(self, *argv: str, **kw):
        return self.run_wf(*argv, role="coordinator", session="C1", **kw)

    def propose(self, gpath: str) -> str:
        c, r = self.coord("grant", "propose", "j", "--grant", gpath)
        self.assertEqual(c, 0, r)
        return r["block_hash"]

    def activate_hash(self, h: str):
        return self.coord("grant", "activate", "j", "--owner-hash", h[:8], "--quote", f"approve G1 {h[:8]}",
                          "--turn", "owner turn")

    def bind(self, brole: str, bsession: str, *extra: str):
        return self.coord("bind", "j", "--bind-role", brole, "--bind-session", bsession, *extra)

    def assert_rejected(self, code_res, reason: str):
        code, res = code_res
        self.assertEqual(code, 1, res)
        self.assertIn(f"rejected({reason})", res.get("error", ""), res)

    def head(self) -> int:
        c, r = self.run_wf("status", "j")
        self.assertEqual(c, 0, r)
        return r["events"]

    def explicit(self, gpath: str):
        self.new_job()
        c, r = self.activate_hash(self.propose(gpath))
        self.assertEqual(c, 0, r)
        for role, session in (("coordinator", "C1"), ("builder", "B1")):
            c, r = self.bind(role, session)
            self.assertEqual(c, 0, r)

    def write_work(self, rel: str, text: str) -> None:
        p = os.path.join(self.dir, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)

    def stop_builder(self) -> int:
        c, r = self.coord("append", "j", "--type", "stop-confirmed", "--content", json.dumps(STOP))
        self.assertEqual(c, 0, r)
        return r["seq"]


class Wf026Validation(_Wf026):
    def test_wf026_T10_handoff_action_outside_activated_grant_is_widening(self):
        """T10 authority branch (validate.py:175-180): a handoff whose `actions` exceed the activated grant is
        `rejected(widening)` and appends nothing; an action inside the grant commits."""
        self.new_job(); self.activate()                                   # GRANT actions ["write"]
        n = self.head()
        bad = {"to_role": "reviewer", "write_scope": ["jobs/j/work/**"], "actions": ["deploy"], "note": "n"}
        self.assert_rejected(self.run_wf("append", "j", "--type", "handoff", "--content", json.dumps(bad)), "widening")
        self.assertEqual(self.head(), n)
        ok = dict(bad, actions=["write"])
        c, r = self.run_wf("append", "j", "--type", "handoff", "--content", json.dumps(ok))
        self.assertEqual(c, 0, r)

    def test_wf026_T20_delivered_claim_without_evidence_rejected(self):
        """T20: a delivered claim with no evidence refs is `rejected(malformed)` (validate.py:187-189), head
        unchanged."""
        self.new_job(); self.activate(); self.intent()
        n = self.head()
        self.assert_rejected(self.run_wf("append", "j", "--type", "delivered",
                                         "--content", '{"claims":[{"claim":"c","evidence":[]}]}'), "malformed")
        self.assertEqual(self.head(), n)

    def test_wf026_T20_accept_with_nothing_delivered_refused(self):
        """T20 ("delivered" precedes acceptance): `wf accept` with no delivered event is refused and appends
        nothing."""
        self.new_job(); self.activate(); self.intent()
        n = self.head()
        results = {"criteria": [{"id": "AC1", "pass": True, "actual": "ok"}], "changed_paths": [],
                   "environment": "disposable local scope", "limits": "none"}
        c, r = self.run_wf("accept", "j", "--results", json.dumps(results), role="reviewer", session="R1")
        self.assertNotEqual(c, 0, r)
        self.assertEqual(r.get("code"), "refused", r)
        self.assertEqual(self.head(), n)

    def test_wf026_T03_rejected_activation_leaves_no_authority(self):
        """T03: activation without a source turn is `rejected(no-provenance)`, head unchanged, authority stays
        `proposed`, and `wf grant-check` denies."""
        self.new_job()
        h = self.propose(self.gpath)
        n = self.head()
        self.assert_rejected(self.coord("grant", "activate", "j", "--owner-hash", h[:8], "--quote", "q", "--turn", ""),
                             "no-provenance")
        self.assertEqual(self.head(), n)
        c, st = self.run_wf("status", "j")
        self.assertEqual(st["axes"]["authority"], "proposed", st)
        c, gc = self.run_wf("grant-check", "j", "--check-role", "builder", "--action", "write",
                            "--path", "jobs/j/work/a.txt")
        self.assertEqual(c, 1, gc)
        self.assertFalse(gc["allow"], gc)

    def test_wf026_T05_same_intent_twice_one_row(self):
        """T05: the same message twice has one effect: the second is DUPLICATE-IGNORED, the event count is unchanged
        and the chain verifies."""
        self.new_job(); self.activate()
        c, r = self.intent(msg_id="M1"); self.assertEqual(r["outcome"], "COMMITTED", r)
        n = self.head()
        c, r = self.intent(msg_id="M1"); self.assertEqual((c, r["outcome"]), (0, "DUPLICATE-IGNORED"), r)
        self.assertEqual(self.head(), n)
        c, v = self.run_wf("verify", "j")
        self.assertTrue(v["clean"], v)


class Wf026Ledger(_Wf026):
    def test_wf026_T28_no_lock_file_is_ever_created(self):
        """T28 ("no lock file exists to go stale"): after a flow of mutations, reads, verify and export, the ledger
        folder holds only the database and the per-attempt pending-marker folder (FB1.4 §4.4a markers; cleared on
        every completed commit, so it holds no file here). No leftover journal, and no lock-named file anywhere under
        the root."""
        self.new_job(); self.activate(); self.intent()
        self.run_wf("append", "j", "--type", "delivered", "--content", DELIVERED)
        for argv in (("status", "j"), ("verify", "j"), ("digest", "j"), ("export", "j")):
            c, r = self.run_wf(*argv)
            self.assertEqual(c, 0, r)
        ledger_dir = os.path.dirname(self.db)
        self.assertLessEqual(set(os.listdir(ledger_dir)), {"l.db", "pending"})
        self.assertIn("l.db", os.listdir(ledger_dir))
        self.assertEqual([f for _, _, fs in os.walk(os.path.join(ledger_dir, "pending")) for f in fs], [])
        locks = [os.path.join(d, f) for d, _, fs in os.walk(self.dir) for f in fs if "lock" in f.lower()]
        self.assertEqual(locks, [])

    def test_wf026_T30i_recovery_cycle_is_never_accepted_as_stop_confirmed(self):
        """T30(i): a full recovery cycle (begin → reconcile → close) without `stop-confirmed` does not count as
        termination: a replacement bind at generation 2 is refused `termination-unconfirmed`."""
        self.explicit(self.write_grant("grant_repl.json", GRANT_REPL))
        c, r = self.intent(); self.assertEqual(c, 0, r)
        c, r = self.coord("recover", "begin", "j", "--reason", "drill", "--turn", "owner: recover j now")
        self.assertEqual(c, 0, r)
        c, r = self.coord("recover", "reconcile", "j", "--turn", "owner: recover j reconcile")
        self.assertEqual(c, 0, r)
        c, r = self.coord("recover", "close", "j", "--turn", "owner: recover j close")
        self.assertEqual(c, 0, r)
        n = self.head()
        self.assert_rejected(self.bind("builder", "B2", "--bind-generation", "2"), "termination-unconfirmed")
        self.assertEqual(self.head(), n)


class Wf026Authority(_Wf026):
    def test_wf026_T32_refused_bind_leaves_session_unbound_at_the_gate(self):
        """T32 (E half): a bind refused for tools wider than the ARS writes no assignment, and the gate decision core
        treats that session as unbound (`unbound-session`) for an in-scope write."""
        self.explicit(self.write_grant("grant_ars.json", GRANT_ARS))
        c, r = self.intent(); self.assertEqual(c, 0, r)
        c, r = self.run_wf("append", "j", "--type", "delivered", "--content", DELIVERED)
        self.assertEqual(c, 0, r)
        n = self.head()
        self.assert_rejected(self.bind("reviewer", "R1", "--tool", "Bash"), "ars")
        self.assertEqual(self.head(), n)
        payload = {"session_id": "R1", "hook_event_name": "PreToolUse", "tool_name": "Write",
                   "tool_input": {"file_path": os.path.join(self.dir, "jobs", "j", "work", "r.txt"), "content": "x"},
                   "cwd": self.dir, "transcript_path": "fixture", "permission_mode": "auto"}
        d = gatecore.decide(payload, root=self.dir, db_path=self.db, now=NOW, gate_files=[])
        self.assertEqual((d["decision"], d["reason"]), ("deny", "unbound-session"), d)

    def test_wf026_T33bcd_renewed_block_then_replacement_allowed(self):
        """T33(b): replacement refused under a delegation rule that disallows it; after a renewed block whose ARS
        permits replacement is confirmed, the same replacement succeeds at generation 2 (spec: "(b),(c),(d) refused
        and need a renewed block")."""
        self.explicit(self.write_grant("grant_ars.json", GRANT_ARS))   # replacement allowed False
        c, r = self.intent(); self.assertEqual(c, 0, r)
        self.write_work("jobs/j/work/a.txt", "changed")
        self.stop_builder()
        c, rep = self.coord("reconcile", "j"); self.assertEqual(c, 0, rep)
        c, r = self.coord("recover", "decide", "j", "--decision", "resume", "--turn", "owner: resume j")
        self.assertEqual(c, 0, r)
        self.assert_rejected(self.bind("builder", "B2", "--bind-generation", "2"), "delegation")
        c, r = self.activate_hash(self.propose(self.write_grant("grant_repl.json", GRANT_REPL)))
        self.assertEqual(c, 0, r)
        c, r = self.bind("builder", "B2", "--bind-generation", "2")
        self.assertEqual(c, 0, r)
        self.assertEqual(r["binding"], {"role": "builder", "session": "B2", "generation": 2})

    def test_wf026_T34_old_block_hash_rejected_after_amendment(self):
        """T34 ("a quote that predates the current block hash never carries over"): after the grant is amended by a
        new proposal, activation citing the previous block hash is `rejected(unbound-approval)`; the new hash
        activates."""
        self.new_job()
        h1 = self.propose(self.gpath)
        c, r = self.activate_hash(h1); self.assertEqual(c, 0, r)
        h2 = self.propose(self.write_grant("grant2.json", dict(GRANT, expiry="2026-12-30T00:00:00Z")))
        self.assertNotEqual(h1, h2)
        n = self.head()
        self.assert_rejected(self.activate_hash(h1), "unbound-approval")
        self.assertEqual(self.head(), n)
        c, r = self.activate_hash(h2); self.assertEqual(c, 0, r)
        c, st = self.run_wf("status", "j")
        self.assertEqual(st["axes"]["authority"], "in-force", st)


if __name__ == "__main__":
    unittest.main()
