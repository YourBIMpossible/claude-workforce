"""DA1 field-by-field T26 tests for `wf digest` (FIRST-RELEASE-SPEC.md:301; DIGEST-ASSIGNMENT-DA1.md §4).
Run with the full suite: python -I -B -m unittest discover -s tools/wf/tests -v
Each test uses a fresh scratch ledger from test_wf.Base (refuse-existing, never deleted)."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import unittest
from unittest import mock

from test_wf import Base  # noqa: E402  (sets sys.path to the wf package)
from wfcore import canon, commands, db as dbm  # noqa: E402

RESULTS_OK = {"criteria": [{"id": "AC1", "pass": True, "actual": "ok"}], "changed_paths": ["jobs/j/work/a.txt"],
              "environment": "disposable local scope", "limits": "none"}
SENTINEL = "RAW-CONTENT-SENTINEL-5e1d"


class T26Digest(Base):
    def digest(self) -> dict:
        code, res = self.run_wf("digest", "j")
        self.assertEqual(code, 0, res)
        return res

    def deliver(self, evidence=("fixtures/results/x.txt",)):
        code, res = self.run_wf("append", "j", "--type", "delivered", "--content",
                                json.dumps({"claims": [{"claim": "c", "evidence": list(evidence)}]}))
        self.assertEqual(code, 0, res)

    def accept(self, results=RESULTS_OK):
        code, res = self.run_wf("accept", "j", "--results", json.dumps(results), role="reviewer", session="R1")
        self.assertEqual(code, 0, res)
        return res

    def items(self, d: dict) -> list[str]:
        return [n["item"] for n in d["needs_owner"]]

    def test_T26a_head_matches_status_and_chain(self):
        self.new_job(); self.activate(); self.intent()
        d = self.digest()
        _, st = self.run_wf("status", "j")
        self.assertEqual({k: d["head"][k] for k in ("seq", "self")}, st["head"])
        self.assertEqual(d["head"]["seq"], d["events"])
        self.assertEqual(d["head"]["db_size_bytes"], os.path.getsize(self.db))
        self.assertIn("retained checkpoint", d["head"]["note"])

    def test_T26a2_db_size_read_inside_snapshot(self):
        """Review F1 (da1-live-review-2): db_size_bytes is read while the snapshot's SHARED lock is held."""
        self.new_job(); self.activate(); self.intent()
        state = {"in": False, "calls": []}
        real_snap, real_size = dbm.read_snapshot, commands.os.path.getsize

        @contextlib.contextmanager
        def snap(conn, *a, **k):
            with real_snap(conn, *a, **k) as c:
                state["in"] = True
                try:
                    yield c
                finally:
                    state["in"] = False

        def size(p):
            if os.path.abspath(p) == os.path.abspath(self.db):
                state["calls"].append(state["in"])
            return real_size(p)

        with mock.patch.object(dbm, "read_snapshot", snap), mock.patch.object(commands.os.path, "getsize", size):
            d = commands.digest(self.db, "j")
        self.assertEqual(state["calls"], [True])
        self.assertEqual(d["head"]["db_size_bytes"], os.path.getsize(self.db))

    def test_T26b_sections_across_job_states(self):
        self.new_job()
        d = self.digest()
        self.assertEqual(d["done"], []); self.assertEqual(self.items(d), ["no grant"])
        self.assertEqual(d["active"]["axes"]["authority"], "no-grant")
        self.run_wf("grant", "propose", "j", "--grant", self.gpath, role="coordinator", session="C1")
        d = self.digest()
        self.assertEqual(self.items(d), ["grant G1 proposed"]); self.assertEqual(d["grants"], [])
        self.activate_existing()
        d = self.digest()
        self.assertEqual([x["item"] for x in d["done"]], ["grant G1 activated"]); self.assertEqual(self.items(d), [])
        self.intent()
        self.deliver()
        d = self.digest()
        self.assertEqual([x["item"] for x in d["done"]], ["grant G1 activated", "delivered"])
        self.assertEqual(self.items(d), ["review"])
        self.assertEqual(d["active"]["latest_event_seq"]["execution"], d["head"]["seq"])
        self.accept()
        d = self.digest()
        self.assertEqual(d["done"][-1]["item"], "review recorded: accepted"); self.assertEqual(self.items(d), ["close"])
        code, res = self.run_wf("close", "j", "--intervention", "none", role="coordinator", session="C1")
        self.assertEqual(code, 0, res)
        d = self.digest()
        self.assertTrue(d["closed"]); self.assertEqual(d["done"][-1]["item"], "closed (accepted)")
        self.assertEqual(self.items(d), [])

    def activate_existing(self):
        """Activate the latest proposed grant via the block hash the propose step printed."""
        code, res = self.run_wf("grant", "propose", "j", "--grant", self.gpath, role="coordinator", session="C1")
        h = res.get("block_hash")
        self.assertIsNotNone(h, res)
        code, res = self.run_wf("grant", "activate", "j", "--owner-hash", h[:8], "--quote", f"approve G1 {h[:8]}",
                                "--turn", "owner turn", role="coordinator", session="C1")
        self.assertEqual(code, 0, res)

    def test_T26c_every_needs_owner_item_has_recommendation_and_evidence(self):
        self.new_job(); self.activate(); self.intent()
        self.run_wf("append", "j", "--type", "blocker", "--content", '{"reason":"x"}')
        self.deliver()
        bad = dict(RESULTS_OK, changed_paths=["elsewhere.txt"])
        self.accept(bad)
        d = self.digest()
        self.assertEqual(self.items(d), ["rejected"])
        self.new_job("k")
        for job in ("j", "k"):
            _, dd = self.run_wf("digest", job)
            self.assertTrue(dd["needs_owner"])
            for n in dd["needs_owner"]:
                self.assertTrue(n["recommendation"].strip(), n); self.assertTrue(n["evidence"], n)

    def test_T26c2_interrupted_needs_owner(self):
        self.new_job(); self.activate(); self.intent()
        self.run_wf("append", "j", "--type", "blocker", "--content", '{"reason":"x"}')
        d = self.digest()
        self.assertEqual(self.items(d), ["interrupted"])
        self.assertEqual(d["needs_owner"][0]["evidence"], [f"ledger:j#{d['head']['seq']}"])

    def test_T26d_activated_grant_quote_block_hash_turn(self):
        self.new_job()
        h = self.activate()
        d = self.digest()
        self.assertEqual(len(d["grants"]), 1)
        g = d["grants"][0]
        self.assertEqual(g["grant_id"], "G1"); self.assertEqual(g["quote"], f"approve G1 {h[:8]}")
        self.assertEqual(g["block_hash"], h); self.assertEqual(g["source_turn"], "owner turn")
        self.assertEqual(g["current_state"], "activated"); self.assertEqual(g["proposed_seq"], g["activation_seq"] - 1)

    def test_T26e_ars_verbatim(self):
        self.new_job(); self.activate()
        d = self.digest()
        with open(self.gpath, encoding="utf-8") as fh:
            want = json.load(fh)["ars"]
        self.assertEqual([canon.canonical(a["ars"]) for a in d["ars"]], [canon.canonical(want)])
        self.assertEqual(d["ars"][0]["grant_id"], "G1")

    def test_T26f_binding_table(self):
        self.new_job(); self.activate()
        self.intent()
        self.intent(note="second")
        self.deliver()
        self.accept()
        d = self.digest()
        b = d["bindings"]
        self.assertEqual(b["source"], "observed-event-headers (no wf bind in R1)")
        rows = {(r["role"], r["session"], r["generation"]): r for r in b["rows"]}
        self.assertEqual(set(rows), {("coordinator", "C1", 1), ("builder", "B1", 1), ("reviewer", "R1", 1)})
        self.assertEqual((rows[("coordinator", "C1", 1)]["first_seq"], rows[("coordinator", "C1", 1)]["last_seq"]), (1, 3))
        self.assertEqual((rows[("builder", "B1", 1)]["first_seq"], rows[("builder", "B1", 1)]["last_seq"], rows[("builder", "B1", 1)]["events"]), (4, 6, 3))
        self.assertEqual(rows[("reviewer", "R1", 1)]["first_seq"], 7)
        self.assertIn("caller-supplied", d["identity_note"])

    def test_T26f2_binding_generation_column(self):
        self.new_job(); self.activate(); self.intent()
        code, res = self.run_wf("append", "j", "--type", "intent", "--content",
                                json.dumps({"path": "jobs/j/work/a.txt", "sha_before": "", "note": "gen2"}),
                                "--generation", "2", session="B2")
        self.assertEqual(code, 0, res)
        d = self.digest()
        rows = sorted((r["session"], r["generation"], r["first_seq"]) for r in d["bindings"]["rows"] if r["role"] == "builder")
        self.assertEqual(rows, [("B1", 1, 4), ("B2", 2, 5)])

    def test_T26g_evidence_paths_with_seq(self):
        self.new_job(); self.activate()
        code, res = self.run_wf("append", "j", "--type", "receipt", "--content",
                    json.dumps({"path": "jobs/j/work/a.txt", "sha_before": "", "sha_after": "00", "note": "r"}))
        self.assertEqual(code, 0, res)
        self.deliver(evidence=("fixtures/results/e1.txt", "fixtures/results/e2.txt"))
        d = self.digest()
        got = [(e["path"], e["type"]) for e in d["evidence"]]
        self.assertEqual(got, [("jobs/j/work/a.txt", "receipt"), ("fixtures/results/e1.txt", "delivered"),
                               ("fixtures/results/e2.txt", "delivered")])
        self.assertEqual(d["evidence"][1]["seq"], d["head"]["seq"])

    def test_T26h_no_raw_relay(self):
        self.new_job(); self.activate()
        self.run_wf("append", "j", "--type", "intent", "--content",
                    json.dumps({"path": "jobs/j/work/a.txt", "sha_before": "", "note": SENTINEL}))
        d = self.digest()
        self.assertNotIn("tail", d)
        self.assertNotIn(SENTINEL, json.dumps(d))
        self.assertEqual(set(d), {"job", "closed", "contract_sha256", "events", "counts", "axes", "pending_markers",
                                  "head", "done", "active", "needs_owner", "grants", "ars", "bindings", "evidence",
                                  "identity_note", "ok", "db", "msg_id"})

    def test_T26i_pending_marker_recommends_resolve(self):
        self.new_job(); self.activate()
        os.environ["WF_TEST_FAULTS"] = "commit-error:IOERR"
        try:
            c, r = self.intent()
        finally:
            os.environ.pop("WF_TEST_FAULTS", None)
        self.assertEqual(r["code"], "commit-uncertain")
        d = self.digest()
        self.assertEqual(len(d["pending_markers"]), 1)
        n = d["needs_owner"][0]
        self.assertTrue(n["item"].startswith("pending write marker"))
        self.assertIn(f"wf resolve j {r['seq']}", n["recommendation"])
        self.assertEqual(n["evidence"], [d["pending_markers"][0]["path"]])

    def test_T26j_digest_is_read_only(self):
        self.new_job(); self.activate(); self.intent(); self.deliver()
        def h() -> str:
            with open(self.db, "rb") as fh:
                return hashlib.sha256(fh.read()).hexdigest()
        before = h()
        self.digest(); self.digest()
        self.assertEqual(h(), before)


if __name__ == "__main__":
    unittest.main()
