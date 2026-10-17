"""B2 slice 1 — CLI end-to-end tests for the integrated wiring (INTERFACE-CONTRACT.md, issue #27; lane S1W).

Covers through `wf.run`: T32 bind + ARS (bootstrap, duplicate/foreign binding, unbound role use, ARS subset,
reviewer preconditions, ack-assignment, stop-confirmed), T06 reconcile + ack-reconcile (unreconciled use,
invalid acks, non-self-invalidation), T16-a recover begin (preconditions, recovering execution axis, every
ordinary mutation refused while open, invalid chain appends nothing) and the implicit-mode regression (a job
without `wf bind` behaves exactly as before). Module-level behaviour is tested in test_t32_bind.py (S1B) and
test_t06_reconcile.py / test_t16a_recover_begin.py (S1R).
"""
from __future__ import annotations

import json
import os
import sqlite3
import unittest

from test_wf import DECLARATIONS, Base, GRANT  # noqa: E402  (fixture, fresh WF_TEST_TMP, refuse-existing)
from wfcore import commands  # noqa: E402

GRANT_ARS = dict(GRANT, ars={
    "roles": {"coordinator": {"tools": ["Read"], "actions": ["read"], "paths": ["tools/wf/**"]},
              "builder": {"tools": ["Write"], "actions": ["write"], "paths": ["jobs/j/work/**"]},
              "reviewer": {"tools": ["Read"], "actions": ["read"], "paths": ["tools/wf/**"]}},
    "session_requirements": {"one_live_session_per_role": True, "reviewer_fresh": True},
    "replacement": {"allowed": False, "max": 0}})
DELIVERED = '{"claims":[{"claim":"c","evidence":["e"]}]}'
STOP = {"role": "builder", "session": "B1", "generation": 1, "how": "process exit observed", "when": "2026-10-15T22:00:00Z",
        "owner_check": "owner confirmed no child process and no outstanding operation", "owner_turn": "owner turn j stop"}


class B2S1(Base):
    def setUp(self) -> None:
        super().setUp()
        self.gpath_ars = os.path.join(self.dir, "grant_ars.json")
        with open(self.gpath_ars, "w", encoding="utf-8") as fh:
            json.dump(GRANT_ARS, fh)

    # ---- helpers
    def activate_ars(self, job="j"):
        code, res = self.run_wf("grant", "propose", job, "--grant", self.gpath_ars, role="coordinator", session="C1")
        self.assertEqual(code, 0, res)
        h = res["block_hash"]
        code, res = self.run_wf("grant", "activate", job, "--owner-hash", h[:8], "--quote", f"approve G1 {h[:8]}",
                                "--turn", "owner turn", role="coordinator", session="C1")
        self.assertEqual(code, 0, res)

    def bind(self, brole: str, bsession: str, *extra: str, session="C1", role="coordinator", **kw):
        return self.run_wf("bind", "j", "--bind-role", brole, "--bind-session", bsession, *extra, role=role,
                           session=session, **kw)

    def explicit_job(self):
        """contract + ARS grant + coordinator self-bind + builder bind (the T32 happy path)."""
        self.new_job()
        self.activate_ars()
        c, r = self.bind("coordinator", "C1")
        self.assertEqual(c, 0, r)
        c, r = self.bind("builder", "B1")
        self.assertEqual(c, 0, r)
        return r["seq"]

    def assert_rejected(self, code_res, reason: str):
        code, res = code_res
        self.assertEqual(code, 1, res)
        self.assertEqual(res.get("code"), "rejected", res)
        self.assertIn(f"rejected({reason})", res["error"], res)

    def events(self, job="j") -> int:
        c, r = self.run_wf("status", job)
        self.assertEqual(c, 0, r)
        return r["events"]

    def write_work(self, rel="jobs/j/work/a.txt", text="x"):
        p = os.path.join(self.dir, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)

    # ---- regression: implicit mode
    def test_implicit_mode_unchanged(self):
        """A job without `wf bind` keeps the observed-header binding model, status gains only recovery:{open:False}."""
        self.new_job()
        self.activate()
        c, r = self.intent()
        self.assertEqual(c, 0, r)
        c, r = self.intent(session="B2")  # gen-1 second session -> claimant conflict exactly as before (T26f2 unaffected)
        self.assertEqual(c, 1, r)
        self.assertIn("claimant-conflict", r["error"])
        c, st = self.run_wf("status", "j")
        self.assertEqual(st["recovery"], {"open": False})
        c, d = self.run_wf("digest", "j")
        self.assertEqual(d["bindings"]["source"], commands.BINDING_SOURCE)
        c, v = self.run_wf("verify", "j")
        self.assertIsNone(v["jobs"]["j"]["recovery_open"])
        self.assertTrue(v["clean"])

    # ---- T32
    def test_T32_bootstrap_bind_ack_assignment(self):
        self.new_job()
        self.activate_ars()
        self.assert_rejected(self.bind("builder", "B1"), "bootstrap")           # first bind must be the coordinator self-bind
        self.assert_rejected(self.bind("coordinator", "C9"), "bootstrap")      # ... naming the contract author's session
        self.assert_rejected(self.bind("coordinator", "C1", session="C9"), "bootstrap")
        n = self.events()
        c, r = self.bind("coordinator", "C1")
        self.assertEqual(c, 0, r)
        self.assertEqual(r["binding"], {"role": "coordinator", "session": "C1", "generation": 1})
        self.assertEqual(self.events(), n + 1)
        c, r = self.bind("builder", "B1")
        self.assertEqual(c, 0, r)
        bind_seq = r["seq"]
        c, d = self.run_wf("digest", "j")
        self.assertEqual(d["bindings"]["source"], commands.BINDING_SOURCE_EXPLICIT)
        rows = {row["role"]: row for row in d["bindings"]["rows"]}
        self.assertEqual(set(rows), {"coordinator", "builder"})
        self.assertFalse(rows["builder"]["acknowledged"])
        # invalid acks: wrong bind_seq, foreign session, then the valid one, then a duplicate
        bad = json.dumps({"role": "builder", "session": "B1", "bind_seq": bind_seq - 1})
        self.assert_rejected(self.run_wf("append", "j", "--type", "ack-assignment", "--content", bad), "invalid-ack")
        good = json.dumps({"role": "builder", "session": "B1", "bind_seq": bind_seq})
        c, r = self.run_wf("append", "j", "--type", "ack-assignment", "--content", good)
        self.assertEqual(c, 0, r)
        self.assert_rejected(self.run_wf("append", "j", "--type", "ack-assignment", "--content", good, msg_id="again"),
                             "invalid-ack")
        c, d = self.run_wf("digest", "j")
        self.assertTrue({row["role"]: row for row in d["bindings"]["rows"]}["builder"]["acknowledged"])
        c, r = self.intent()  # bound, acknowledged builder works
        self.assertEqual(c, 0, r)
        # foreign session acking the bound role = second claimant: conflict row, role blocked (spec 5 rule 5)
        c, r = self.run_wf("append", "j", "--type", "ack-assignment", "--content", good, session="B2")
        self.assertEqual(c, 1, r)
        self.assertIn("rejected(claimant-conflict)", r["error"])
        c, st = self.run_wf("status", "j")
        self.assertEqual(st["axes"]["execution"], "blocked")

    def test_T32_refusals_no_grant_ars_duplicate_foreign(self):
        self.new_job()
        self.assert_rejected(self.bind("coordinator", "C1"), "no-grant")
        self.activate()  # degenerate fixture ARS {"a": "owner"} -> valid grant, unusable for bind
        self.assert_rejected(self.bind("coordinator", "C1"), "ars")
        self.assertEqual(self.events(), 3)

    def test_T32_ars_subset_duplicate_foreign_unbound(self):
        self.explicit_job()
        n = self.events()
        self.assert_rejected(self.bind("reviewer", "R1", "--tool", "Bash"), "ars")
        self.assert_rejected(self.bind("reviewer", "R1", "--path", "ledger/**"), "ars")
        self.assert_rejected(self.bind("builder", "B2"), "bound")                      # duplicate role: replacement excluded
        # slice 2 (INTERFACE-CONTRACT-S2 section 3): generation 2 is a replacement; with the builder not stop-confirmed
        # it is refused as termination-unconfirmed (slice 1 refused it as bound; the 'bound' refusal still covers a
        # duplicate generation-1 bind, asserted above)
        self.assert_rejected(self.bind("builder", "B1", "--bind-generation", "2"), "termination-unconfirmed")
        self.assert_rejected(self.bind("reviewer", "R1"), "reviewer-precondition")     # nothing delivered yet
        self.assertEqual(self.events(), n)                                            # T32: no assignment event written
        c, r = self.intent()
        self.assertEqual(c, 0, r)
        c, r = self.run_wf("append", "j", "--type", "delivered", "--content", DELIVERED)
        self.assertEqual(c, 0, r)
        self.assert_rejected(self.bind("reviewer", "B1"), "reviewer-precondition")     # reviewer = builder's session
        c, r = self.bind("reviewer", "R1")
        self.assertEqual(c, 0, r)
        # foreign session in a bound role -> claimant conflict (spec 5 rule 5), role blocked
        c, r = self.intent(session="B2")
        self.assertEqual(c, 1, r)
        self.assertIn("claimant-conflict", r["error"])
        c, st = self.run_wf("status", "j")
        self.assertEqual(st["axes"]["execution"], "blocked")

    def test_T32_unbound_role_use_and_generation(self):
        self.new_job()
        self.activate_ars()
        c, r = self.bind("coordinator", "C1")
        self.assertEqual(c, 0, r)
        self.assert_rejected(self.intent(), "unbound-session")                         # builder not bound
        c, r = self.bind("builder", "B1")
        self.assertEqual(c, 0, r)
        c, r = self.run_wf("append", "j", "--type", "intent", "--content",
                           '{"path":"jobs/j/work/a.txt","sha_before":"","note":"n"}', "--generation", "2")
        self.assertEqual(c, 1, r)
        self.assertIn("rejected(unbound-session)", r["error"])                          # no binding at generation 2
        c, r = self.intent()
        self.assertEqual(c, 0, r)

    def test_T32_stop_confirmed(self):
        self.explicit_job()
        c, r = self.intent()
        self.assertEqual(c, 0, r)
        c, r = self.run_wf("append", "j", "--type", "stop-confirmed", "--content", json.dumps(STOP))
        self.assertEqual(c, 1, r)  # builder may not write stop-confirmed (ROLE_FOR_TYPE)
        self.assertIn("rejected(role)", r["error"])
        c, r = self.run_wf("append", "j", "--type", "stop-confirmed", "--content", json.dumps(STOP),
                           role="coordinator", session="C1")
        self.assertEqual(c, 0, r)
        self.assert_rejected(self.intent(), "stopped-session")
        self.assert_rejected(self.bind("builder", "B2"), "bound")                      # no replacement in this slice
        c, d = self.run_wf("digest", "j")
        self.assertTrue({row["role"]: row for row in d["bindings"]["rows"]}["builder"]["stopped"])

    # ---- T06
    def test_T06_reconcile_ack_cycle(self):
        self.explicit_job()
        c, r = self.intent()                   # intent for jobs/j/work/a.txt, no receipt
        self.assertEqual(c, 0, r)
        self.write_work("jobs/j/work/a.txt", "changed")
        self.write_work("tools/wf/stray.txt", "unrecorded")   # read-scope change without receipt
        self.assert_rejected(self.run_wf("reconcile", "j"), "role")                              # builder cannot
        self.assert_rejected(self.run_wf("reconcile", "j", "--target-role", "reviewer", role="coordinator", session="C1"), "role")
        c, r = self.run_wf("reconcile", "j", role="coordinator", session="C1")
        self.assertEqual(c, 0, r)
        report_seq = r["seq"]
        ids = [e["id"] for e in r["exceptions"]]
        classes = {e["path"]: e["class"] for e in r["exceptions"]}
        self.assertEqual(classes.get("jobs/j/work/a.txt"), "attempt-without-completion")
        self.assertEqual(classes.get("tools/wf/stray.txt"), "matches-neither")
        self.assertEqual(r["through_seq"], report_seq - 1)
        # unreconciled: builder mutations refuse, read-only commands run, ack-reconcile is the only mutation
        self.assert_rejected(self.intent(), "unreconciled")
        self.assert_rejected(self.run_wf("append", "j", "--type", "delivered", "--content", DELIVERED), "unreconciled")
        c, st = self.run_wf("status", "j")
        self.assertEqual(c, 0, st)
        c, d = self.run_wf("digest", "j")
        self.assertIn("unreconciled builder", [n["item"] for n in d["needs_owner"]])
        self.assert_rejected(self.run_wf("ack-reconcile", "j", "--report", str(report_seq), "--exceptions", "[]"), "invalid-ack")
        self.assert_rejected(self.run_wf("ack-reconcile", "j", "--report", str(report_seq - 1), "--exceptions", json.dumps(ids)),
                             "stale-report")
        c, r = self.run_wf("ack-reconcile", "j", "--report", str(report_seq), "--exceptions", json.dumps(ids), session="C1",
                           role="coordinator")
        self.assertEqual(c, 1, r)  # the coordinator is not the reconciled role's session: no ack for it
        c, r = self.run_wf("ack-reconcile", "j", "--report", str(report_seq), "--exceptions", json.dumps(ids))
        self.assertEqual(c, 0, r)
        c, r = self.intent(note="resume")       # reconciled again; the ack did not make B1 unreconciled
        self.assertEqual(c, 0, r)
        c, d = self.run_wf("digest", "j")
        self.assertNotIn("unreconciled builder", [n["item"] for n in d["needs_owner"]])
        self.assertTrue({row["role"]: row for row in d["bindings"]["rows"]}["builder"]["reconciled"])
        # a second report with zero exceptions still needs an ack (via append --type ack-reconcile)
        os.remove(os.path.join(self.dir, "tools", "wf", "stray.txt"))
        c, r = self.run_wf("append", "j", "--type", "receipt", "--content",
                           '{"path":"jobs/j/work/a.txt","sha_before":"","sha_after":"' + self.sha("changed") + '","note":"n"}')
        self.assertEqual(c, 0, r)
        c, r = self.run_wf("reconcile", "j", role="coordinator", session="C1")
        self.assertEqual(c, 0, r)
        self.assertEqual(r["exceptions"], [])
        self.assert_rejected(self.intent(note="again"), "unreconciled")
        c, r = self.run_wf("append", "j", "--type", "ack-reconcile", "--content",
                           json.dumps({"role": "builder", "session": "B1", "report_seq": r["seq"], "exceptions": []}))
        self.assertEqual(c, 0, r)
        c, r = self.intent(note="after second ack")
        self.assertEqual(c, 0, r)

    @staticmethod
    def sha(text: str) -> str:
        import hashlib
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def test_T06_no_report_and_implicit_builder(self):
        self.new_job()
        self.activate()
        self.assert_rejected(self.run_wf("ack-reconcile", "j", "--report", "1", "--exceptions", "[]"), "no-report")
        c, r = self.intent()
        self.assertEqual(c, 0, r)
        c, r = self.run_wf("reconcile", "j", role="coordinator", session="C1")   # implicit builder B1 is reconcilable too
        self.assertEqual(c, 0, r)
        self.assert_rejected(self.intent(note="x"), "unreconciled")
        c, r2 = self.run_wf("ack-reconcile", "j", "--report", str(r["seq"]), "--exceptions",
                            json.dumps([e["id"] for e in r["exceptions"]]))
        self.assertEqual(c, 0, r2)

    # ---- T16-a
    def test_T16a_recover_begin_and_recovering_axis(self):
        self.explicit_job()
        c, r = self.intent()
        self.assertEqual(c, 0, r)
        n = self.events()
        c, r = self.run_wf("recover", "begin", "j", "--reason", "stale export", "--turn", "owner: proceed",
                           role="coordinator", session="C1")
        self.assertEqual(c, 1, r)
        self.assertIn("rejected(no-provenance)", r["error"])
        self.assert_rejected(self.run_wf("recover", "begin", "j", "--reason", "x", "--turn", "owner: recover j"), "role")
        self.assertEqual(self.events(), n)
        c, r = self.run_wf("recover", "begin", "j", "--reason", "stale export", "--turn", "owner: recover j now",
                           role="coordinator", session="C1")
        self.assertEqual(c, 0, r)
        opened = r["seq"]
        c, st = self.run_wf("status", "j")
        self.assertEqual(st["axes"]["execution"], "recovering")
        self.assertEqual(st["recovery"]["open"], True)
        self.assertEqual(st["recovery"]["opened_seq"], opened)
        # every ordinary mutation refuses while open; nothing appended
        self.assert_rejected(self.run_wf("recover", "begin", "j", "--reason", "x", "--turn", "owner: j", role="coordinator",
                                         session="C1"), "recovery-open")
        self.assert_rejected(self.intent(note="during"), "recovery-open")
        self.assert_rejected(self.bind("reviewer", "R1"), "recovery-open")
        self.assert_rejected(self.run_wf("reconcile", "j", role="coordinator", session="C1"), "recovery-open")
        self.assert_rejected(self.run_wf("append", "j", "--type", "stop-confirmed", "--content", json.dumps(STOP),
                                         role="coordinator", session="C1"), "recovery-open")
        self.assert_rejected(self.run_wf("job", "abandon", "j", "--reason", "r"), "recovery-open")
        self.assertEqual(self.events(), opened)
        c, d = self.run_wf("digest", "j")
        self.assertIn("recovery open", [x["item"] for x in d["needs_owner"]])
        self.assertEqual(d["active"]["recovery"]["opened_seq"], opened)
        c, v = self.run_wf("verify", "j")
        self.assertEqual(v["jobs"]["j"]["recovery_open"], opened)
        self.assertTrue(v["clean"])            # an open recovery is an exception, not a chain fault
        c, e = self.run_wf("export", "j")      # read-only commands still run
        self.assertEqual(c, 0, e)
        with self.assertRaises(SystemExit):    # recover reconcile / close are not registered in this slice
            self.run_wf("recover", "close", "j", role="coordinator", session="C1")

    def test_T16a_close_refused_while_open(self):
        """A closable (accepted) job: `close` reaches the gate and is rejected(recovery-open); nothing appended.
        (An abandoned job cannot open a recovery: txn refuses every write on it, existing behaviour.)"""
        self.new_job()
        self.activate()
        self.intent()
        self.run_wf("append", "j", "--type", "delivered", "--content", DELIVERED)
        results = {"criteria": [{"id": "AC1", "pass": True, "actual": "ok"}], "changed_paths": ["jobs/j/work/a.txt"],
                   "environment": "disposable local scope", "limits": "none", **DECLARATIONS}
        c, r = self.run_wf("accept", "j", "--results", json.dumps(results), role="reviewer", session="R1")
        self.assertEqual((c, r.get("decision")), (0, "accepted"), r)
        c, r = self.run_wf("recover", "begin", "j", "--reason", "x", "--turn", "owner: recover j", role="coordinator",
                           session="C1")
        self.assertEqual(c, 0, r)
        n = self.events()
        self.assert_rejected(self.run_wf("close", "j", "--intervention", "none", role="coordinator", session="C1"),
                             "recovery-open")
        self.assertEqual(self.events(), n)

    def test_T16a_invalid_chain_appends_nothing(self):
        self.new_job()
        self.activate()
        c, r = self.intent()
        self.assertEqual(c, 0, r)
        c, r = self.intent(note="two", msg_id="m2")
        self.assertEqual(c, 0, r)
        n = self.events()
        conn = sqlite3.connect(self.db)
        try:  # tamper a middle row (seq 2, the proposed grant): the bounded head check cannot see it
            conn.execute("DROP TRIGGER events_no_update")
            conn.execute("UPDATE events SET content = replace(content, 'G1', 'G9') WHERE job='j' AND seq=2")
            conn.commit()
        finally:
            conn.close()
        c, r = self.run_wf("recover", "begin", "j", "--reason", "x", "--turn", "owner: recover j", role="coordinator",
                           session="C1")
        self.assertEqual(c, 1, r)
        self.assertIn("rejected(invalid-chain)", r["error"])
        self.assertTrue(r.get("problems"))
        c, v = self.run_wf("verify", "j")
        self.assertEqual(v["jobs"]["j"]["head"]["seq"], n)   # no recovery-opened row
        self.assertIsNone(v["jobs"]["j"]["recovery_open"])
        self.assertFalse(v["clean"])


if __name__ == "__main__":
    unittest.main()
