"""B2 slice 2 — CLI end-to-end tests for the integrated wiring (INTERFACE-CONTRACT-S2.md, issue #32; lane S2W).

Covers through `wf.run`: recovery closure (`wf recover reconcile` idempotent acknowledgements, `wf recover close`
preconditions and effect, T30 c/d/e at CLI level, T16 second-trigger refusals), the 6.2 step-4 decision
(`wf recover decide`: preconditions, unresolvable-path disposition rule, digest inspection-limitation item), the
replacement bind at generation+1 (first event must be `ack-reconcile`, stale/unbound generations) and the digest /
status / verify surfaces of section 5. Module-level behaviour belongs to test_t30_recover_close.py / test_t06 (S2R),
test_t33_replacement.py / test_t32 / test_t35_changeset (S2B) and test_t30_recovery_faults.py (S2C, subprocess kills).
Every assertion here is against the contract text; the expected refusal codes are the contract's.
"""
from __future__ import annotations

import json
import os
import unittest

from test_b2s1_wiring import GRANT_ARS, STOP, B2S1  # noqa: E402  (fixture + helpers of the slice-1 e2e file)

GRANT_REPL = json.loads(json.dumps(GRANT_ARS))
GRANT_REPL["ars"]["replacement"] = {"allowed": True, "max": 1, "expiry": "2030-01-01T00:00:00Z"}


class B2S2(B2S1):
    """Inherits the slice-1 helpers only; the inherited test methods are detached below so discovery runs them once
    (in test_b2s1_wiring) and this class never reuses their fixture directories."""

    def setUp(self) -> None:
        super().setUp()
        self.gpath_repl = os.path.join(self.dir, "grant_repl.json")
        with open(self.gpath_repl, "w", encoding="utf-8") as fh:
            json.dump(GRANT_REPL, fh)

    # ---- helpers
    def activate_repl(self, job="j"):
        code, res = self.run_wf("grant", "propose", job, "--grant", self.gpath_repl, role="coordinator", session="C1")
        self.assertEqual(code, 0, res)
        h = res["block_hash"]
        code, res = self.run_wf("grant", "activate", job, "--owner-hash", h[:8], "--quote", f"approve G1 {h[:8]}",
                                "--turn", "owner turn", role="coordinator", session="C1")
        self.assertEqual(code, 0, res)

    def explicit_repl_job(self):
        self.new_job()
        self.activate_repl()
        c, r = self.bind("coordinator", "C1")
        self.assertEqual(c, 0, r)
        c, r = self.bind("builder", "B1")
        self.assertEqual(c, 0, r)

    def coord(self, *argv, **kw):
        return self.run_wf(*argv, role="coordinator", session="C1", **kw)

    def open_recovery(self, reason="stale export"):
        c, r = self.coord("recover", "begin", "j", "--reason", reason, "--turn", "owner: recover j now")
        self.assertEqual(c, 0, r)
        return r["seq"]

    def stop_builder(self, session="B1", generation=1):
        stop = dict(STOP, session=session, generation=generation)
        c, r = self.coord("append", "j", "--type", "stop-confirmed", "--content", json.dumps(stop))
        self.assertEqual(c, 0, r)
        return r["seq"]

    def reconcile(self):
        c, r = self.coord("reconcile", "j")
        self.assertEqual(c, 0, r)
        return r

    # ---- recovery closure (section 4; T30 c, d, e; T16)
    def test_recover_reconcile_and_close_cycle(self):
        self.explicit_job()
        c, r = self.intent()
        self.assertEqual(c, 0, r)
        self.assert_rejected(self.coord("recover", "reconcile", "j", "--turn", "owner: recover j"), "no-recovery")
        self.assert_rejected(self.coord("recover", "close", "j", "--turn", "owner: recover j"), "no-recovery")
        # an orphan export temp file is a recoverable exception (export.orphans): the opened row lists it
        c, r = self.run_wf("export", "j")
        self.assertEqual(c, 0, r)
        r0_export_dir = r["export"]["dir"]
        with open(os.path.join(r0_export_dir, "head.json.tmp"), "xb") as fh:
            fh.write(b"orphan" + bytes([10]))
        opened = self.open_recovery()
        c, st = self.run_wf("status", "j")
        self.assertTrue(st["recovery"]["open"])
        listed = st["recovery"]["exceptions"]
        self.assertGreaterEqual(listed, 1)
        self.assertEqual(st["recovery"]["pending"], listed)
        self.assertEqual(st["recovery"]["acknowledged"], 0)
        # close refuses while anything is unacknowledged; nothing appended (T30 d)
        n = self.events()
        self.assert_rejected(self.coord("recover", "close", "j", "--turn", "owner: recover j"), "unacknowledged")
        self.assertEqual(self.events(), n)
        # provenance and role refusals
        self.assert_rejected(self.coord("recover", "reconcile", "j", "--turn", "owner says go"), "no-provenance")
        self.assert_rejected(self.run_wf("recover", "reconcile", "j", "--turn", "owner: recover j"), "role")
        # ordinary mutations refuse while open (T30 c first half); recovery rows pass the gate
        self.assert_rejected(self.intent(note="during"), "recovery-open")
        self.assert_rejected(self.coord("recover", "decide", "j", "--decision", "abandon", "--turn", "owner: j"),
                             "recovery-open")
        c, r = self.coord("recover", "reconcile", "j", "--turn", "owner: recover j reconcile")
        self.assertEqual(c, 0, r)
        self.assertEqual(len(r["acknowledged"]), listed)
        self.assertEqual(r["already"], [])
        self.assertEqual(self.events(), n + listed)
        # idempotent: a second run appends nothing and reports every id as already acknowledged
        c, r2 = self.coord("recover", "reconcile", "j", "--turn", "owner: recover j again")
        self.assertEqual(c, 0, r2)
        self.assertEqual(r2["acknowledged"], [])
        self.assertEqual(sorted(r2["already"]), sorted(r["acknowledged"]))
        self.assertEqual(self.events(), n + listed)
        c, st = self.run_wf("status", "j")
        self.assertEqual((st["recovery"]["acknowledged"], st["recovery"]["pending"]), (listed, 0))
        c, d = self.run_wf("digest", "j")
        audit = d["active"]["recovery"]["audit"]
        self.assertEqual(audit[0], {"type": "recovery-opened", "seq": opened})
        self.assertEqual([a["type"] for a in audit[1:]], ["exception-acknowledged"] * listed)
        # second begin while open refused (T16 / T30 a single-process half)
        self.assert_rejected(self.coord("recover", "begin", "j", "--reason", "x", "--turn", "owner: recover j"),
                             "recovery-open")
        # close
        c, r = self.coord("recover", "close", "j", "--turn", "owner: recover j close")
        self.assertEqual(c, 0, r)
        self.assertEqual(r["opened_seq"], opened)
        closed = r["seq"]
        c, st = self.run_wf("status", "j")
        self.assertEqual(st["recovery"], {"open": False})
        self.assertNotEqual(st["axes"]["execution"], "recovering")
        c, v = self.run_wf("verify", "j")
        self.assertFalse(v["clean"])          # recovery acknowledged the orphan, it never repaired it (3B)
        self.assertTrue(any("preserved" in p for p in v["jobs"]["j"]["export"]), v["jobs"]["j"]["export"])
        os.remove(os.path.join(r0_export_dir, "head.json.tmp"))   # the owner's procedure, outside wf
        c, v = self.run_wf("verify", "j")
        self.assertIsNone(v["jobs"]["j"]["recovery_open"])
        self.assertTrue(v["clean"])
        c, d = self.run_wf("digest", "j")
        self.assertEqual(d["active"]["recovery"]["audit"][-1], {"type": "recovery-closed", "seq": closed})
        self.assertNotIn("recovery open", [x["item"] for x in d["needs_owner"]])
        # ordinary mutations run again (T30 c second half); closure commands refuse with nothing open
        c, r = self.intent(note="after close")
        self.assertEqual(c, 0, r)
        self.assert_rejected(self.coord("recover", "close", "j", "--turn", "owner: recover j"), "no-recovery")
        # a new recovery can be opened later (audit shows both)
        opened2 = self.open_recovery("second")
        c, d = self.run_wf("digest", "j")
        self.assertEqual([a["seq"] for a in d["active"]["recovery"]["audit"] if a["type"] == "recovery-opened"],
                         [opened, opened2])

    def test_recover_close_refuses_invalid_chain_and_appends_nothing(self):
        self.explicit_job()
        self.open_recovery()
        c, r = self.coord("recover", "reconcile", "j", "--turn", "owner: recover j")
        self.assertEqual(c, 0, r)
        n = self.events()
        import sqlite3
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("DROP TRIGGER events_no_update")  # the bounded head check cannot see a middle-row tamper
            conn.execute("UPDATE events SET content = content || ' ' WHERE job = 'j' AND seq = 2")
            conn.commit()
        finally:
            conn.close()
        self.assert_rejected(self.coord("recover", "close", "j", "--turn", "owner: recover j"), "invalid-chain")
        c, st = self.run_wf("status", "j")
        self.assertEqual(st["events"], n)
        self.assertTrue(st["recovery"]["open"])

    # ---- recovery decision (section 1, 3, 5)
    def test_recover_decide_preconditions_and_resume_path(self):
        self.explicit_repl_job()
        c, r = self.intent()
        self.assertEqual(c, 0, r)
        self.write_work("jobs/j/work/a.txt", "changed")
        # no report yet -> no-report; wrong role -> role; missing provenance -> no-provenance
        self.assert_rejected(self.coord("recover", "decide", "j", "--decision", "resume", "--turn", "owner: j"), "no-report")
        self.assert_rejected(self.run_wf("recover", "decide", "j", "--decision", "resume", "--turn", "owner: j"), "role")
        self.assert_rejected(self.coord("recover", "decide", "j", "--decision", "resume", "--turn", "owner: go"),
                             "no-provenance")
        rep = self.reconcile()
        report_seq = rep["seq"]
        # termination unconfirmed -> refused (6.2 step 2 precedes step 4)
        self.assert_rejected(self.coord("recover", "decide", "j", "--decision", "resume", "--turn", "owner: j"),
                             "termination-unconfirmed")
        stop_seq = self.stop_builder("B1")
        # the report predates the stop: replacement needs a post-stop report, so reconcile again
        rep2 = self.reconcile()
        self.assertGreater(rep2["seq"], stop_seq)
        # restore without paths -> invalid-decision; restore of a non-report path -> invalid-decision
        self.assert_rejected(self.coord("recover", "decide", "j", "--decision", "restore", "--turn", "owner: j"),
                             "invalid-decision")
        self.assert_rejected(self.coord("recover", "decide", "j", "--decision", "restore", "--restore-path", "nope.txt",
                                        "--turn", "owner: j"), "invalid-decision")
        c, r = self.coord("recover", "decide", "j", "--decision", "resume", "--turn", "owner: resume j", "--note", "ok")
        self.assertEqual(c, 0, r)
        self.assertEqual((r["decision"], r["report_seq"], r["stop_seq"]), ("resume", rep2["seq"], stop_seq))
        # replacement bind at generation 2 (section 3): refusals then success
        self.assert_rejected(self.bind("builder", "B1", "--bind-generation", "2"), "delegation")   # same session
        c, r = self.bind("builder", "B2", "--bind-generation", "2")
        self.assertEqual(c, 0, r)
        self.assertEqual(r["binding"], {"role": "builder", "session": "B2", "generation": 2})
        c, d = self.run_wf("digest", "j")
        rows = {row["role"]: row for row in d["bindings"]["rows"]}
        self.assertEqual(rows["builder"]["session"], "B2")
        self.assertEqual(rows["builder"]["generation"], 2)
        # the new session's first event must be the ack of the post-stop report (unreconciled rule); a generation-1
        # event by B2 is stale first (bind.check_actor precedes reconcile.check_actor in the guard)
        self.assert_rejected(self.intent(session="B2", note="first"), "stale")
        self.assert_rejected(self.run_wf("append", "j", "--type", "intent", "--content",
                                         '{"path":"jobs/j/work/a.txt","note":"g2"}', "--generation", "2", session="B2"),
                             "unreconciled")
        ids = json.dumps([e["id"] for e in rep2["exceptions"]])
        # the replaced session is stale once generation 2 is bound (5 rule 4); only the new one can acknowledge
        self.assert_rejected(self.run_wf("ack-reconcile", "j", "--report", str(rep2["seq"]), "--exceptions", ids),
                             "stale")
        c, r = self.run_wf("ack-reconcile", "j", "--report", str(rep2["seq"]), "--exceptions", ids, "--generation", "2",
                           session="B2")
        self.assertEqual(c, 0, r)
        c, r = self.run_wf("append", "j", "--type", "intent", "--content", '{"path":"jobs/j/work/a.txt","note":"g2"}',
                           "--generation", "2", session="B2")
        self.assertEqual(c, 0, r)
        # generation below the binding -> stale; above -> unbound-session (5 rule 4 / contract 3)
        self.assert_rejected(self.intent(session="B2", note="gen1"), "stale")
        self.assert_rejected(self.run_wf("append", "j", "--type", "intent", "--content", '{"path":"jobs/j/work/a.txt","note":"g3"}',
                                         "--generation", "3", session="B2"), "unbound-session")
        # a second replacement for the same stop is refused (the decision is consumed / the role is bound again)
        self.assert_rejected(self.bind("builder", "B3", "--bind-generation", "3"), "termination-unconfirmed")

    def test_recover_decide_unresolvable_paths_need_disposition(self):
        self.explicit_job()
        c, r = self.run_wf("append", "j", "--type", "intent", "--content", '{"path":"../outside.txt","note":"escape"}')
        if c != 0:
            self.skipTest("the engine refuses escaping intent paths before reconcile; A-4 is then unreachable at CLI level: "
                          + r.get("error", ""))
        rep = self.reconcile()
        c, d = self.run_wf("digest", "j")
        items = [x["item"] for x in d["needs_owner"]]
        self.assertIn("inspection limitation: unresolvable path(s)", items)
        ids = json.dumps([e["id"] for e in rep["exceptions"]])
        c, r = self.run_wf("ack-reconcile", "j", "--report", str(rep["seq"]), "--exceptions", ids)
        self.assertEqual(c, 0, r)
        c, d = self.run_wf("digest", "j")   # the ack does not clear the limitation (A-4)
        self.assertIn("inspection limitation: unresolvable path(s)", [x["item"] for x in d["needs_owner"]])
        stop_seq = self.stop_builder("B1")
        rep2 = self.reconcile()
        self.assert_rejected(self.coord("recover", "decide", "j", "--decision", "abandon", "--turn", "owner: j"),
                             "invalid-decision")                       # unresolved not covered
        self.assert_rejected(self.coord("recover", "decide", "j", "--decision", "abandon", "--turn", "owner: j",
                                        "--unresolved", "../outside.txt=guess"), "invalid-decision")
        c, r = self.coord("recover", "decide", "j", "--decision", "abandon", "--turn", "owner: abandon j",
                          "--unresolved", "../outside.txt=owner-inspected")
        self.assertEqual(c, 0, r)
        c, d = self.run_wf("digest", "j")
        self.assertNotIn("inspection limitation: unresolvable path(s)", [x["item"] for x in d["needs_owner"]])

    # ---- CLI surface
    def test_recover_verbs_registered_and_usage_errors(self):
        code, res = self.coord("recover", "decide", "j", "--decision", "resume", "--turn", "owner: j",
                               "--unresolved", "nodisposition")
        self.assertEqual(code, 2, res)
        self.assertEqual(res.get("code"), "usage", res)


for _name in [n for n in dir(B2S1) if n.startswith("test_") and n not in B2S2.__dict__]:
    setattr(B2S2, _name, None)  # not a test here
del B2S1  # keep discovery from collecting the slice-1 class a second time through this module


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
