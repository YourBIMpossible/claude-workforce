"""T36 (d)/(e): `wf verify --checkpoint seq:hash` compared with the stored chain (spec 3C level 3, lines 141,
153-156, 180; D-019 wording). Lane C, owns only wfcore/checkpointcmp.py and this file.

What is proved here: the comparison finds the retained seq in the current chain and compares its stored `self`
with the retained value; a match at the head or at an older seq (after legitimate appends) keeps `clean`; a missing
seq (tail deletion, seq beyond head) or a different hash (consistent whole-chain rewrite, bit-for-bit different
value) makes `clean` false with a problem naming checkpoint seq, expected hash, found value and ladder level.
What is NOT proved, and stated by the output itself: an altered checkpoint that agrees with an altered chain passes
(T36 (e)); the retained value is trusted as supplied; nothing here is an authenticity claim.

Tampering happens only inside this test's fresh scratch ledger (triggers dropped, rows rewritten by direct SQL, as
test_b1_gaps T36b/c do), never on any other file."""
from __future__ import annotations

import json
import re
import sqlite3
import unittest

from test_wf import Base, log  # noqa: E402  (sets sys.path to the wf package)
from wfcore import canon, checkpointcmp, commands, db as dbm, export  # noqa: E402
from wfcore.db import WfUsage  # noqa: E402

ZERO = "0" * 64


class T36Checkpoint(Base):
    # ---------------------------------------------------------------- helpers
    def chain_rows(self, job: str = "j") -> list[dict]:
        conn = dbm.connect(self.db, create=False)
        try:
            return dbm.rows(conn, job)
        finally:
            conn.close()

    def head(self, job: str = "j") -> tuple[int, str]:
        """Checkpoint value as the owner would copy it from `wf digest`."""
        d = commands.digest(self.db, job)
        return d["head"]["seq"], d["head"]["self"]

    def verify_cp(self, seq: int, self_hash: str, job: str = "j") -> tuple[int, dict]:
        return self.run_wf("verify", job, "--checkpoint", f"{seq}:{self_hash}")

    def setup_chain(self) -> list[dict]:
        self.new_job(); self.activate(); self.intent(note="orig"); self.intent(note="tail")
        rows = self.chain_rows()
        self.assertEqual(rows[-1]["seq"], 5)
        return rows

    def raw(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db, isolation_level=None)

    def rewrite_from(self, seq: int, new_note: str) -> None:
        """Consistent rewrite (T36 (b) pattern): change row `seq`, recompute its hashes and re-link every later row.
        The immutability triggers are dropped for the edit and re-created afterwards, so the rewritten ledger
        passes levels 1-2 outright and only the checkpoint comparison stands between it and `clean`."""
        rows = self.chain_rows()
        conn = self.raw()
        try:
            conn.execute("DROP TRIGGER events_no_update")
            prev = rows[seq - 2]["self"] if seq > 1 else canon.GENESIS
            for r in rows[seq - 1:]:
                r = dict(r)
                if r["seq"] == seq:
                    body = json.loads(r["content"]); body["note"] = new_note
                    r["content"] = canon.canonical(body)
                    r["content_sha256"] = canon.sha256_text(r["content"])
                r["prev"] = prev
                r["self"] = canon.event_self_hash(r)
                conn.execute("UPDATE events SET content=:content, content_sha256=:content_sha256, prev=:prev, self=:self"
                             " WHERE job=:job AND seq=:seq", r)
                prev = r["self"]
            dbm.ensure_schema(conn)  # triggers back: the rewrite leaves no level-1/2 trace
        finally:
            conn.close()
        ref = export.refresh(self.db, "j"); self.assertNotIn("error", ref, ref)

    def delete_tail(self, seq: int) -> None:
        conn = self.raw()
        try:
            conn.execute("DROP TRIGGER events_no_delete")
            conn.execute("DELETE FROM events WHERE job='j' AND seq=?", (seq,))
            dbm.ensure_schema(conn)
        finally:
            conn.close()
        ref = export.refresh(self.db, "j"); self.assertNotIn("error", ref, ref)

    def assert_problem(self, res: dict, seq: int, expected: str, status: str) -> str:
        """The one problem added for the checkpoint names seq, expected hash, found value and the ladder level."""
        probs = [p for p in res["jobs"]["j"]["problems"] if p.startswith("retained checkpoint")]
        self.assertEqual(len(probs), 1, res["jobs"]["j"]["problems"])
        p = probs[0]
        for needle in (f"seq {seq}", f"expected self {expected}", "found ", checkpointcmp.LEVEL_3, status):
            self.assertIn(needle, p)
        return p

    def assert_no_banned_words(self, out, name: str = "verify") -> None:
        text = json.dumps(out)
        self.assertIsNone(re.search(r"\bauthentic\b", text, re.IGNORECASE), name)
        self.assertIsNone(re.search(r"tamper[- ]?proof", text, re.IGNORECASE), name)

    def assert_limits_stated(self, cp: dict) -> None:
        self.assertIn("trusted as supplied", cp["trust"])
        self.assertIn("not detectable", cp["limits"])
        self.assertEqual(cp["disclaimer"], "not an authenticity claim")
        self.assertIn("no such claim", cp["levels"]["4"])

    # ---------------------------------------------------------------- matching
    def test_T36d_match_at_head(self):
        self.setup_chain()
        seq, h = self.head()
        self.assertEqual(seq, 5)
        code, res = self.verify_cp(seq, h)
        self.assertEqual(code, 0, res)
        self.assertTrue(res["clean"], res)
        self.assertEqual(res["checkpoint_job"], "j")
        cp = res["checkpoint"]
        self.assertEqual((cp["status"], cp["seq"], cp["expected_self"], cp["found_self"], cp["head_seq"]),
                         ("match", 5, h, h, 5))
        self.assertEqual(cp["level_reached"], checkpointcmp.LEVEL_3)
        self.assertIn("against the value as supplied", cp["levels"]["3"])
        self.assertEqual(res["jobs"]["j"]["problems"], [])
        self.assertIn("not an authenticity claim", res["ladder"])  # existing ladder text untouched
        self.assert_limits_stated(cp)
        self.assert_no_banned_words(res)
        log(f"{self._testMethodName}-checkpoint", cp)

    def test_T36d_older_checkpoint_valid_after_legitimate_appends(self):
        self.new_job(); self.activate()
        seq, h = self.head()  # retained at grant activation (spec: "When: at grant activation ...")
        self.assertEqual(seq, 3)
        self.intent(note="later-1"); self.intent(note="later-2")
        self.assertEqual(self.head()[0], 5)
        code, res = self.verify_cp(seq, h)
        self.assertEqual(code, 0, res)
        self.assertTrue(res["clean"], res)
        cp = res["checkpoint"]
        self.assertEqual((cp["status"], cp["seq"], cp["head_seq"], cp["found_self"]), ("match", 3, 5, h))
        self.assertEqual(res["jobs"]["j"]["head"]["seq"], 5)  # head advanced; the older checkpoint still holds
        self.assertEqual(res["jobs"]["j"]["problems"], [])

    # ---------------------------------------------------------------- not matching
    def test_T36d_sequence_beyond_head_is_missing(self):
        self.setup_chain()
        seq, h = self.head()
        code, res = self.verify_cp(seq + 1, h)
        self.assertEqual(code, 0, res)  # verify reports; the exit code semantics (0 with clean false) are unchanged
        self.assertFalse(res["clean"], res)
        cp = res["checkpoint"]
        self.assertEqual((cp["status"], cp["found_self"], cp["head_seq"]), ("missing", None, 5))
        p = self.assert_problem(res, seq + 1, h, "missing")
        self.assertIn("no stored event at seq 6 (current head seq 5)", p)
        self.assertEqual(res["ledger_problems"]["schema"], [])
        self.assert_no_banned_words(res)

    def test_T36d_same_sequence_different_hash_is_mismatch(self):
        self.setup_chain()
        seq, h = self.head()
        other = ("f" if h[0] != "f" else "0") + h[1:]
        code, res = self.verify_cp(seq, other)
        self.assertEqual(code, 0, res)
        self.assertFalse(res["clean"], res)
        cp = res["checkpoint"]
        self.assertEqual((cp["status"], cp["expected_self"], cp["found_self"]), ("mismatch", other, h))
        p = self.assert_problem(res, seq, other, "mismatch")
        self.assertIn(f"found self {h}", p)
        self.assertEqual(res["jobs"]["j"]["head"], {"seq": 5, "self": h})  # the chain itself is untouched

    def test_T36d_consistent_whole_chain_rewrite_detected_by_original_checkpoint(self):
        """T36 (d) with (b): the rewrite passes levels 1-2 (no chain, schema or integrity problem; this is T36b
        reproduced with the triggers restored) and is detected only by the checkpoint retained before it."""
        before = self.setup_chain()
        seq, h = self.head()
        self.rewrite_from(4, "REWRITTEN")
        after = self.chain_rows()
        self.assertNotEqual([r["self"] for r in before[3:]], [r["self"] for r in after[3:]])
        c0, plain = self.run_wf("verify", "j")  # (b): internally consistent, passes without a checkpoint
        self.assertEqual(c0, 0, plain)
        self.assertTrue(plain["clean"], plain)
        self.assertNotIn("checkpoint", plain)
        code, res = self.verify_cp(seq, h)  # (d): the retained head no longer matches
        self.assertEqual(code, 0, res)
        self.assertFalse(res["clean"], res)
        cp = res["checkpoint"]
        self.assertEqual((cp["status"], cp["expected_self"], cp["found_self"]), ("mismatch", h, after[-1]["self"]))
        self.assertEqual(cp["levels"]["1"], "chain consistency: passed")
        self.assertEqual(cp["levels"]["2"], "accidental-corruption check (integrity_check): passed")
        self.assert_problem(res, seq, h, "mismatch")
        # an older checkpoint taken before the rewritten row (seq 3) still matches: the rewrite started at seq 4
        c3, res3 = self.verify_cp(3, before[2]["self"])
        self.assertTrue(res3["clean"], res3)
        self.assertEqual(res3["checkpoint"]["status"], "match")
        log(f"{self._testMethodName}-rewrite", {"before_head": h, "after_head": after[-1]["self"],
                                                "problems": res["jobs"]["j"]["problems"]})

    def test_T36d_tail_deletion_detected_by_original_checkpoint(self):
        """T36 (d) with (c): deleting the head row drops the chain below the retained seq."""
        self.setup_chain()
        seq, h = self.head()
        self.delete_tail(5)
        c0, plain = self.run_wf("verify", "j")
        self.assertTrue(plain["clean"], plain)  # (c): undetectable without a checkpoint
        self.assertEqual(plain["jobs"]["j"]["head"]["seq"], 4)
        code, res = self.verify_cp(seq, h)
        self.assertEqual(code, 0, res)
        self.assertFalse(res["clean"], res)
        self.assertEqual((res["checkpoint"]["status"], res["checkpoint"]["head_seq"]), ("missing", 4))
        self.assert_problem(res, 5, h, "missing")

    # ---------------------------------------------------------------- (e) limits
    def test_T36e_altered_checkpoint_matching_altered_chain_passes_and_says_trusted(self):
        """T36 (e): "the checkpoint itself altered" -> "not detected, and the output states the retained value is
        trusted". The rewriter who also controls the retained copy substitutes the rewritten head; the comparison
        passes and says so. This test proves the limitation is stated, not that it is absent."""
        self.setup_chain()
        orig_seq, orig_h = self.head()
        self.rewrite_from(4, "REWRITTEN")
        altered_seq, altered_h = self.head()  # the altered "retained" value, copied from the altered chain
        self.assertEqual((orig_seq, altered_seq), (5, 5))
        self.assertNotEqual(orig_h, altered_h)
        code, res = self.verify_cp(altered_seq, altered_h)
        self.assertEqual(code, 0, res)
        self.assertTrue(res["clean"], res)  # not detected
        cp = res["checkpoint"]
        self.assertEqual(cp["status"], "match")
        self.assert_limits_stated(cp)
        self.assertIn("trusted as supplied", json.dumps(res))
        self.assertIn("altered checkpoint that agrees with an altered chain is not detectable", cp["limits"])
        self.assert_no_banned_words(res)
        log(f"{self._testMethodName}-limits", {"trust": cp["trust"], "limits": cp["limits"]})

    def test_T36_no_prohibited_positive_claim(self):
        """Spec T36: no output ever uses "authentic" or "tamper-proof". Checked on every checkpoint outcome
        (match, missing, mismatch) and on parse errors. "not an authenticity claim" is the D-019 disclaimer and is
        the only occurrence of the stem."""
        self.setup_chain()
        seq, h = self.head()
        outputs = {"match": self.verify_cp(seq, h)[1], "missing": self.verify_cp(seq + 1, h)[1],
                   "mismatch": self.verify_cp(seq, ZERO)[1]}
        for name, out in outputs.items():
            self.assert_no_banned_words(out, name)
            text = json.dumps(out)
            self.assertEqual(re.findall(r"\bauthentic\w*", text, re.IGNORECASE), ["authenticity", "authenticity"], name)
            self.assertEqual(text.count("not an authenticity claim"), 2, name)  # ladder text + checkpoint disclaimer
        for const in (checkpointcmp.TRUST, checkpointcmp.UNDETECTABLE, checkpointcmp.LEVEL_3):
            self.assert_no_banned_words(const, "constants")
        try:
            checkpointcmp.parse("x", "j")
        except WfUsage as exc:
            self.assert_no_banned_words(str(exc), "usage")

    # ---------------------------------------------------------------- unchanged paths
    def test_T36_no_checkpoint_behaviour_unchanged(self):
        self.setup_chain()
        code, res = self.run_wf("verify", "j")
        self.assertEqual(code, 0, res)
        self.assertTrue(res["clean"], res)
        self.assertNotIn("checkpoint", res)
        self.assertNotIn("checkpoint_job", res)
        self.assertEqual(set(res), {"db", "ledger_problems", "ledger_directory", "jobs", "clean", "ladder", "ok", "msg_id"})  # A3 verify listing added ledger_directory (spec 4.4 P4 row); still no checkpoint key
        # the seam itself: compare() with no checkpoint returns the same object, untouched
        r = {"jobs": {"j": {"problems": []}}, "clean": True}
        snapshot = json.dumps(r)
        self.assertIs(checkpointcmp.compare(r, None, {"j": [(1, ZERO)]}), r)
        self.assertEqual(json.dumps(r), snapshot)
        # whole-ledger verify (no job) is untouched too
        self.assertNotIn("checkpoint", commands.verify(self.db, None))

    def test_T36_parse_contract_unchanged(self):
        self.assertEqual(checkpointcmp.parse("7:" + ZERO, "j"), (7, ZERO))
        self.assertEqual(checkpointcmp.parse(" 7:" + ZERO + " ", "j"), (7, ZERO))
        for bad in ("7", "7:" + "0" * 63, "7:" + "G" * 64, "0:" + ZERO, "-1:" + ZERO, "a:" + ZERO):
            with self.subTest(value=bad), self.assertRaises(WfUsage):
                checkpointcmp.parse(bad, "j")
        for job in (None, "", "  "):
            with self.subTest(job=job), self.assertRaises(WfUsage):
                checkpointcmp.parse("7:" + ZERO, job)
        code, res = self.run_wf("verify", "j", "--checkpoint", "nope")
        self.assertEqual((code, res.get("code")), (2, "usage"), res)


if __name__ == "__main__":
    unittest.main(verbosity=2)
