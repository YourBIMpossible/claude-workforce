"""B2 slice 3 — CLI end-to-end tests for the integrated wiring (INTERFACE-CONTRACT-S3.md + S3-A1, issue #37; lane S3W).

Through `wf.run`: the section 3 authority matrix at the CLI (W refused under revoked / expired / voided / cancelled,
O and L still run, renewal back to in-force), `wf grant revoke|expire`, `wf cancel` (limitation text, second cancel),
`wf grant-check` exit codes agreeing with the guard on the same authority state, `wf log-intervention` and the digest
summary, `wf observe` (class, evidence, uncertainty, gate statement, bounded log location, no ledger row), status /
digest `authority`. Module behaviour belongs to test_t34_grant_lifecycle.py / test_grant_check_corpus.py (S3G),
test_t14_observe.py (S3O) and test_t34_cli_matrix.py (S3M).
"""
from __future__ import annotations

import json
import os
import unittest

from test_wf import GRANT, Base  # noqa: E402

TURN = "owner turn for job j"


class B2S3(Base):
    # ---- helpers
    def coord(self, *argv, **kw):
        return self.run_wf(*argv, role="coordinator", session="C1", **kw)

    def assert_rejected(self, code_res, reason: str):
        code, res = code_res
        self.assertEqual(code, 1, res)
        self.assertIn(f"rejected({reason})", res.get("error", ""), res)

    def events(self) -> int:
        c, r = self.run_wf("status", "j")
        self.assertEqual(c, 0, r)
        return r["events"]

    def authority(self) -> str:
        c, r = self.run_wf("status", "j")
        self.assertEqual(c, 0, r)
        return r["authority"]["state"]

    def check(self, action="write", path="jobs/j/work/a.txt", role="builder"):
        return self.run_wf("grant-check", "j", "--check-role", role, "--action", action, "--path", path)

    def write_grant(self, name: str, **over) -> str:
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(dict(GRANT, **over), fh)
        return p

    def propose_activate(self, gpath: str):
        c, r = self.coord("grant", "propose", "j", "--grant", gpath)
        self.assertEqual(c, 0, r)
        h = r["block_hash"]
        c, r = self.coord("grant", "activate", "j", "--owner-hash", h[:8], "--quote", f"approve {h[:8]}",
                          "--turn", "owner turn")
        self.assertEqual(c, 0, r)

    # ---- matrix: revoke, renew
    def test_revoke_refuses_work_keeps_owner_control_and_renewal(self):
        self.new_job()
        self.assertEqual(self.authority(), "none")
        c, r = self.check()
        self.assertEqual((c, r.get("reason")), (1, "none"))                 # grant-check denies without a grant
        c, r = self.intent()                                                   # D-S3-1: legacy recording in `none`
        self.assertEqual(c, 0, r)
        self.activate()
        self.assertEqual(self.authority(), "in-force")
        c, r = self.check()
        self.assertEqual((c, r.get("allow")), (0, True), r)
        self.assert_rejected(self.coord("grant", "revoke", "j", "--reason", "stop", "--turn", "owner says stop"),
                             "no-provenance")
        c, r = self.coord("grant", "revoke", "j", "--reason", "stop", "--turn", TURN)
        self.assertEqual(c, 0, r)
        self.assertEqual(self.authority(), "revoked")
        c, r = self.check()
        self.assertEqual((c, r.get("reason")), (1, "revoked"))               # same state, same answer as the guard
        self.assert_rejected(self.intent(note="after revoke"), "authority-revoked")
        self.assert_rejected(self.coord("grant", "revoke", "j", "--reason", "again", "--turn", TURN), "no-active-grant")
        # O category still runs under revoked
        c, r = self.run_wf("append", "j", "--type", "blocker", "--content", '{"reason":"waiting for renewal"}')
        self.assertEqual(c, 0, r)
        c, r = self.coord("log-intervention", "j", "--label", "restart", "--necessity", "necessary", "--minutes", "5",
                          "--note", "renewal")
        self.assertEqual(c, 0, r)
        # renewal: propose (L) + owner confirmation + activate -> in force again; work resumes
        self.propose_activate(self.gpath)
        self.assertEqual(self.authority(), "in-force")
        c, r = self.intent(note="after renewal")
        self.assertEqual(c, 0, r)
        c, d = self.run_wf("digest", "j")
        self.assertEqual(d["active"]["authority"]["state"], "in-force")
        self.assertEqual((d["active"]["interventions"]["count"], d["active"]["interventions"]["minutes_known"]), (1, 5))

    def test_explicit_and_time_expiry_and_amendment_voids(self):
        self.new_job()
        self.activate()
        c, r = self.coord("grant", "expire", "j", "--reason", "owner ends it early", "--turn", TURN)
        self.assertEqual(c, 0, r)
        self.assertEqual(self.authority(), "expired")
        self.assert_rejected(self.intent(note="x"), "authority-expired")
        c, r = self.check()
        self.assertEqual((c, r.get("reason")), (1, "expired"))
        # a past-expiry revision is expired by time as soon as it is activated
        self.propose_activate(self.write_grant("g_past.json", grant_id="G2", expiry="2020-01-01T00:00:00Z"))
        c, st = self.run_wf("status", "j")
        self.assertEqual((st["authority"]["state"], st["authority"]["reason"]), ("expired", "time"))
        self.assert_rejected(self.intent(note="y"), "authority-expired")
        # a good revision, then a new proposal (amendment) voids it at once until activated
        self.propose_activate(self.write_grant("g3.json", grant_id="G3"))
        self.assertEqual(self.authority(), "in-force")
        c, r = self.coord("grant", "propose", "j", "--grant", self.write_grant("g4.json", grant_id="G4",
                                                                                actions=["write", "read"]))
        self.assertEqual(c, 0, r)
        h = r["block_hash"]
        self.assertEqual(self.authority(), "voided")
        self.assert_rejected(self.intent(note="z"), "authority-voided")
        c, r = self.check()
        self.assertEqual((c, r.get("reason")), (1, "voided"))
        c, r = self.coord("grant", "activate", "j", "--owner-hash", h[:8], "--quote", f"approve {h[:8]}",
                          "--turn", "owner turn")
        self.assertEqual(c, 0, r)
        self.assertEqual(self.authority(), "in-force")

    # ---- cancel
    def test_cancel_refuses_work_and_renewal_keeps_owner_control(self):
        self.new_job()
        self.activate()
        self.assert_rejected(self.coord("cancel", "j", "--reason", "stop", "--turn", "owner says stop"), "no-provenance")
        c, r = self.run_wf("cancel", "j", "--reason", "owner stops the job", "--turn", TURN)   # anyone may record it
        self.assertEqual(c, 0, r)
        self.assertIn("does not stop running processes", r["limitation"])
        self.assertEqual(self.authority(), "cancelled")
        self.assert_rejected(self.intent(note="after cancel"), "cancelled")
        self.assert_rejected(self.coord("grant", "propose", "j", "--grant", self.gpath), "cancelled")
        self.assert_rejected(self.run_wf("cancel", "j", "--reason", "again", "--turn", TURN), "cancelled")
        c, r = self.check()
        self.assertEqual((c, r.get("reason")), (1, "cancelled"))
        # O still runs: intervention, recovery open/close, abandon + close is the way out
        c, r = self.coord("log-intervention", "j", "--label", "cancel", "--necessity", "unknown", "--minutes", "unknown",
                          "--note", "")
        self.assertEqual(c, 0, r)
        c, r = self.coord("recover", "begin", "j", "--reason", "after cancel", "--turn", "owner: recover j")
        self.assertEqual(c, 0, r)
        c, r = self.coord("recover", "reconcile", "j", "--turn", "owner: recover j")
        self.assertEqual(c, 0, r)
        c, r = self.coord("recover", "close", "j", "--turn", "owner: recover j")
        self.assertEqual(c, 0, r)
        c, d = self.run_wf("digest", "j")
        self.assertIn("cancelled", [x["item"] for x in d["needs_owner"]])
        self.assertEqual((d["active"]["interventions"]["unknown_count"], d["active"]["interventions"]["minutes_known"]), (1, 0))
        c, st = self.run_wf("status", "j")
        self.assertIn("no enforcement on real workers", st["authority"]["limitation"])

    def test_log_intervention_validation(self):
        self.new_job()
        code, res = self.coord("log-intervention", "j", "--label", "x", "--necessity", "necessary", "--minutes", "ten",
                               "--note", "")
        self.assertEqual(code, 2, res)
        self.assert_rejected(self.run_wf("log-intervention", "j", "--label", "x", "--necessity", "necessary",
                                         "--minutes", "1", "--note", ""), "role")                  # builder may not

    # ---- observe
    def test_observe_read_only_bounded_log(self):
        self.new_job()
        self.activate()
        c, r = self.intent()
        self.assertEqual(c, 0, r)
        n = self.events()
        c, o = self.run_wf("observe", "j")
        self.assertEqual(c, 0, o)
        self.assertIn(o["class"], ("active", "slow", "silent", "blocked", "unknown"))
        self.assertEqual(o["class"], "active")
        self.assertTrue(o["evidence"] and o["evidence"][0]["type"] == "intent")
        self.assertFalse(o["gate_log"]["available"])
        self.assertTrue(any("recorded events only" in u for u in o["uncertainty"]))
        self.assertEqual(self.events(), n)                                        # no ledger row
        self.assertTrue(o["log"].replace("\\", "/").endswith("runtime/observe/j.log"), o["log"])  # S3-A3 (FIX2 #36)
        self.assertTrue(os.path.isfile(o["log"]))
        c, r = self.run_wf("append", "j", "--type", "blocker", "--content", '{"reason":"stuck"}')
        self.assertEqual(c, 0, r)
        c, o = self.run_wf("observe", "j")
        self.assertEqual(o["class"], "blocked")
        with open(o["log"], encoding="utf-8") as fh:
            self.assertEqual(len(fh.read().splitlines()), 2)
        # T22: no option to write elsewhere, contact or resume
        for bad in (["--out", "x"], ["--resume"], ["--contact", "zen"]):
            with self.assertRaises(SystemExit) as cm:   # argparse refuses the option (usage, exit 2)
                self.run_wf("observe", "j", *bad)
            self.assertEqual(cm.exception.code, 2)
        self.assertEqual(self.events(), n + 1)                                    # only the blocker row was added


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
