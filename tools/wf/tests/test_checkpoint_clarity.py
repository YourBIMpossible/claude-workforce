"""Checkpoint-comparison output clarity (owner wording policy, 2026-10-15). Asserts that `wf verify --checkpoint`
output states, concisely and without repetition: chain verification is internal consistency, not authenticity;
the supplied checkpoint is trusted as supplied; an older matching checkpoint need not equal the head; a match proves
nothing about later events; an altered checkpoint agreeing with an altered chain may escape detection. Also pins
that the comparison itself (clean/problem calculation, missing/mismatch, no-checkpoint output, signatures) is
unchanged from the lane C delivery. Tampering happens only in this test's fresh scratch ledger."""
from __future__ import annotations

import inspect
import json
import re
import sqlite3
import unittest

from test_wf import Base  # noqa: E402  (sets sys.path to the wf package)
from wfcore import canon, checkpointcmp, commands, db as dbm, export  # noqa: E402

ZERO = "0" * 64
BANNED = (r"\bauthentic\b", r"tamper[- ]?proof")
REQUIRED = {  # policy point -> text that must appear in the serialized checkpoint block
    "internal consistency, not authorship": "internal consistency only, never who wrote an event",
    "trusted as supplied": "trusted as supplied",
    "older checkpoint need not equal head": "need not equal the current head",
    "no proof about later events": "proves nothing about events after its seq",
    "altered checkpoint may escape detection": "altered checkpoint that agrees with an altered chain is not detectable",
}


class CheckpointClarity(Base):
    def chain(self, n_intents: int = 2) -> tuple[int, str]:
        self.new_job(); self.activate()
        for i in range(n_intents):
            self.intent(note=f"n{i}")
        d = commands.digest(self.db, "j")["head"]
        return d["seq"], d["self"]

    def verify_cp(self, seq: int, h: str) -> dict:
        code, res = self.run_wf("verify", "j", "--checkpoint", f"{seq}:{h}")
        self.assertEqual(code, 0, res)
        return res

    def rewrite_head(self) -> str:
        """Consistent rewrite of the head row (triggers dropped then restored); returns the new head self."""
        conn = dbm.connect(self.db, create=False)
        try:
            rows = dbm.rows(conn, "j")
        finally:
            conn.close()
        r = dict(rows[-1]); body = json.loads(r["content"]); body["note"] = "REWRITTEN"
        r["content"] = canon.canonical(body); r["content_sha256"] = canon.sha256_text(r["content"])
        r["self"] = canon.event_self_hash(r)
        raw = sqlite3.connect(self.db, isolation_level=None)
        try:
            raw.execute("DROP TRIGGER events_no_update")
            raw.execute("UPDATE events SET content=:content, content_sha256=:content_sha256, self=:self"
                        " WHERE job=:job AND seq=:seq", r)
            dbm.ensure_schema(raw)
        finally:
            raw.close()
        ref = export.refresh(self.db, "j"); self.assertNotIn("error", ref, ref)
        return r["self"]

    def assert_policy(self, res: dict) -> None:
        cp_text = json.dumps(res["checkpoint"])
        for point, needle in REQUIRED.items():
            self.assertIn(needle, cp_text, point)
            self.assertEqual(cp_text.count(needle), 1, f"{point}: stated once, not repeated")
        whole = json.dumps(res)
        for pat in BANNED:
            self.assertIsNone(re.search(pat, whole, re.IGNORECASE), pat)
        self.assertEqual(res["checkpoint"]["disclaimer"], "not an authenticity claim")
        self.assertEqual(whole.count("not an authenticity claim"), 2)  # commands.verify ladder + checkpoint block

    # ---------------------------------------------------------------- wording
    def test_match_at_head_states_policy_and_head(self):
        seq, h = self.chain()
        res = self.verify_cp(seq, h)
        self.assertTrue(res["clean"], res)
        self.assert_policy(res)
        self.assertEqual(res["checkpoint"]["statement"],
                         f"the current chain contains seq {seq} with the retained self hash (seq {seq} is the current head)")

    def test_older_match_says_head_moved_and_later_events_not_covered(self):
        self.new_job(); self.activate()
        d = commands.digest(self.db, "j")["head"]; seq, h = d["seq"], d["self"]
        self.intent(note="a"); self.intent(note="b")
        res = self.verify_cp(seq, h)
        self.assertTrue(res["clean"], res)
        self.assertEqual(res["checkpoint"]["status"], "match")
        self.assertEqual(res["checkpoint"]["statement"],
                         f"the current chain contains seq {seq} with the retained self hash; the head is now seq 5 "
                         f"and events {seq + 1}-5 are not covered by this checkpoint")
        self.assert_policy(res)

    def test_non_match_outputs_state_policy_too(self):
        seq, h = self.chain()
        for status, args in (("missing", (seq + 1, h)), ("mismatch", (seq, ZERO))):
            with self.subTest(status=status):
                res = self.verify_cp(*args)
                self.assertFalse(res["clean"], res)
                self.assertEqual(res["checkpoint"]["status"], status)
                self.assertIn(f"({status})", res["checkpoint"]["statement"])
                self.assert_policy(res)

    def test_altered_checkpoint_agreeing_with_altered_chain_passes_and_says_so(self):
        seq, orig = self.chain()
        new = self.rewrite_head()
        self.assertNotEqual(orig, new)
        res = self.verify_cp(seq, new)  # the rewriter also altered the retained value
        self.assertTrue(res["clean"], res)
        self.assertEqual(res["checkpoint"]["status"], "match")
        self.assert_policy(res)
        self.assertFalse(self.verify_cp(seq, orig)["clean"])  # the genuine retained value still detects it

    def test_module_text_is_concise_and_free_of_positive_claims(self):
        for name in ("SCOPE", "TRUST", "UNDETECTABLE", "LEVEL_3", "DISCLAIMER"):
            text = getattr(checkpointcmp, name)
            self.assertIsInstance(text, str, name)
            self.assertLess(len(text), 240, f"{name}: keep limitation text short")
            for pat in BANNED:
                self.assertIsNone(re.search(pat, text, re.IGNORECASE), name)

    # ---------------------------------------------------------------- unchanged behaviour
    def test_comparison_behaviour_unchanged(self):
        seq, h = self.chain()
        ok = self.verify_cp(seq, h)
        self.assertEqual(ok["jobs"]["j"]["problems"], [])
        miss = self.verify_cp(seq + 1, h)
        bad = self.verify_cp(seq, ZERO)
        for res, status in ((miss, "missing"), (bad, "mismatch")):
            self.assertFalse(res["clean"])
            probs = res["jobs"]["j"]["problems"]
            self.assertEqual(len(probs), 1, probs)
            self.assertIn(f"seq {res['checkpoint']['seq']}", probs[0])
            self.assertIn(f"expected self {res['checkpoint']['expected_self']}", probs[0])
            self.assertIn(checkpointcmp.LEVEL_3, probs[0]); self.assertIn(status, probs[0])
        self.assertEqual(set(ok["checkpoint"]), {"job", "seq", "expected_self", "found_self", "head_seq", "status",
                                                 "level_reached", "levels", "statement", "scope", "trust", "limits",
                                                 "disclaimer"})

    def test_no_checkpoint_output_and_signatures_unchanged(self):
        self.chain()
        code, res = self.run_wf("verify", "j")
        self.assertEqual(code, 0, res)
        self.assertTrue(res["clean"])
        self.assertNotIn("checkpoint", res); self.assertNotIn("checkpoint_job", res)
        self.assertEqual(set(res), {"db", "ledger_problems", "jobs", "clean", "ladder", "ok", "msg_id"})
        r = {"jobs": {}, "clean": True}
        self.assertIs(checkpointcmp.compare(r, None, {}), r)
        self.assertEqual(list(inspect.signature(checkpointcmp.parse).parameters), ["text", "job"])
        self.assertEqual(list(inspect.signature(checkpointcmp.compare).parameters), ["result", "checkpoint", "chains"])
        self.assertEqual(checkpointcmp.parse("3:" + ZERO, "j"), (3, ZERO))
        code, res = self.run_wf("verify", "j", "--checkpoint", "3-" + ZERO)
        self.assertEqual((code, res.get("code")), (2, "usage"), res)


if __name__ == "__main__":
    unittest.main(verbosity=2)
