"""PACKAGE FIX2 batch H, lane F2HZ (WF-119; AMENDMENT FIX2-A15): wording.

  #39 (+#53)  a second `job abandon` on an abandoned job is refused `job <j> already abandoned`, not `not found`;
              an unknown job is still `not found`; neither writes anything and the first reason is kept.
  #41         the digest's needs_owner entry for an unreconciled role whose binding is stop-confirmed names the
              report and the stop, and recommends what can actually acknowledge it: a replacement bind when one is
              possible, otherwise no session (replacement limit reached / not permitted / expired).
  A4 N3       a duplicate key's path shows a key that is not a plain identifier in bracket form with JSON quoting
              (`$["a.b"]`), so it cannot be confused with nested keys (`$.a.b`).
"""
from __future__ import annotations

import json
import os
import sqlite3
import unittest
from contextlib import closing

import test_b2s2_wiring as s2  # noqa: E402  (slice-1/2 CLI helpers; see the detach loop at the end)
from test_wf import Base  # noqa: E402

PATH_A = "jobs/j/work/a.txt"


class F2Abandon(Base):
    """#39 (+#53): the second abandon names the real reason."""

    def abandon(self, job: str, reason: str):
        return self.run_wf("job", "abandon", job, "--reason", reason, role="coordinator", session="C1")

    def abandon_reason(self, job: str):
        with closing(sqlite3.connect(self.db)) as con:
            return con.execute("SELECT abandon_reason FROM contracts WHERE job=?", (job,)).fetchone()[0]

    def test_second_abandon_is_refused_already_abandoned(self):
        self.new_job()
        c, r = self.abandon("j", "first")
        self.assertEqual(c, 0, r)
        c2, r2 = self.abandon("j", "second")
        self.assertEqual((c2, r2.get("code"), r2.get("error")), (1, "refused", "job j already abandoned"), r2)
        self.assertEqual(self.abandon_reason("j"), "first")

    def test_abandon_of_unknown_job_is_still_not_found(self):
        self.new_job()
        c, r = self.abandon("k", "r")
        self.assertEqual((c, r.get("code"), r.get("error")), (1, "refused", "job k not found"), r)
        self.assertIsNone(self.abandon_reason("j"))


class F2StoppedRoleDigest(s2.B2S2):
    """#41: an unreconciled report of a stop-confirmed builder is not shown as the stopped session's to acknowledge."""

    def needs(self) -> list[dict]:
        c, d = self.run_wf("digest", "j")
        self.assertEqual(c, 0, d)
        return d["needs_owner"]

    def stopped_with_report(self, *, repl: bool) -> dict:
        """Bound builder B1 (generation 1), one change, a report, the stop, and the post-stop report (unacknowledged).
        Returns the post-stop report (`wf reconcile` result)."""
        if repl:
            self.explicit_repl_job()
        else:
            self.explicit_job()
        c, r = self.intent()
        self.assertEqual(c, 0, r)
        self.write_work(PATH_A, "changed")
        self.reconcile()
        stop_seq = self.stop_builder("B1")
        rep = self.reconcile()
        self.assertGreater(rep["seq"], stop_seq)
        return rep

    def one_item(self, needs: list[dict], prefix: str) -> dict:
        found = [n for n in needs if n["item"].startswith(prefix)]
        self.assertEqual(len(found), 1, needs)
        return found[0]

    def test_stopped_builder_without_replacement_names_no_session(self):
        seq = self.stopped_with_report(repl=False)["seq"]  # GRANT_ARS: replacement allowed false
        needs = self.needs()
        self.assertNotIn("unreconciled builder", [n["item"] for n in needs])
        n = self.one_item(needs, f"unreconciled report {seq} for a stopped builder")
        self.assertEqual(n["item"], f"unreconciled report {seq} for a stopped builder; replacement not permitted")
        self.assertIn("B1", n["reason"])
        self.assertIn("stop-confirmed", n["reason"])
        self.assertIn("no session can acknowledge", n["recommendation"])
        self.assertNotIn("the bound session runs", n["recommendation"])

    def test_stopped_builder_with_replacement_recommends_the_replacement(self):
        seq = self.stopped_with_report(repl=True)["seq"]  # GRANT_REPL: allowed, max 1, none used
        n = self.one_item(self.needs(), f"unreconciled report {seq} for a stopped builder")
        self.assertEqual(n["item"], f"unreconciled report {seq} for a stopped builder")
        self.assertIn("recover decide", n["recommendation"])
        self.assertIn("generation 2", n["recommendation"])
        self.assertIn("ack-reconcile", n["recommendation"])
        self.assertNotIn("the bound session runs", n["recommendation"])

    def test_stopped_replacement_after_the_limit_says_limit_reached(self):
        """R47-5: B2 (generation 2, the one allowed replacement) is stopped; no generation-3 bind is possible."""
        rep = self.stopped_with_report(repl=True)
        c, r = self.coord("recover", "decide", "j", "--decision", "resume", "--turn", "owner: resume j")
        self.assertEqual(c, 0, r)
        c, r = self.bind("builder", "B2", "--bind-generation", "2")
        self.assertEqual(c, 0, r)
        ids = json.dumps([e["id"] for e in rep["exceptions"]])
        c, r = self.run_wf("ack-reconcile", "j", "--report", str(rep["seq"]), "--exceptions", ids, "--generation", "2",
                           session="B2")
        self.assertEqual(c, 0, r)
        stop2 = self.stop_builder("B2", generation=2)
        rep2 = self.reconcile()
        self.assertGreater(rep2["seq"], stop2)
        n = self.one_item(self.needs(), f"unreconciled report {rep2['seq']} for a stopped builder")
        self.assertEqual(n["item"],
                         f"unreconciled report {rep2['seq']} for a stopped builder; replacement limit reached")
        self.assertIn("B2", n["reason"])
        self.assertIn("no session can acknowledge", n["recommendation"])


class F2DuplicateKeyPath(Base):
    """A4 N3: the duplicate key's path cannot be confused with nested keys."""

    def refused_at(self, text: str) -> str:
        self.new_job(); self.activate()
        path = os.path.join(self.dir, "dup.json")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        code, res = self.run_wf("append", "j", "--type", "intent", "--content", path)
        self.assertEqual((code, res.get("code")), (1, "rejected"), res)
        self.assertIn("rejected(malformed)", res["error"])
        return res["error"]

    def body(self, extra: str) -> str:
        return '{"path": "jobs/j/work/a.txt", "sha_before": "", "note": "n", ' + extra + "}"

    def test_dotted_key_is_bracketed(self):
        err = self.refused_at(self.body('"a.b": {"x": 1, "x": 2}'))
        self.assertIn('$["a.b"]: duplicate key \'x\'', err)

    def test_nested_plain_keys_keep_dot_form(self):
        err = self.refused_at(self.body('"a": {"b": {"x": 1, "x": 2}}'))
        self.assertIn("$.a.b: duplicate key 'x'", err)

    def test_control_character_key_is_escaped_not_dropped(self):
        err = self.refused_at(self.body('"\\u0001": {"x": 1, "x": 2}'))
        self.assertIn('$["\\u0001"]: duplicate key \'x\'', err)
        self.assertNotIn("$.?", err)

    def test_non_ascii_key_is_bracketed_with_ascii_escapes(self):
        err = self.refused_at(self.body('"caf\\u00e9": [{"x": 1, "x": 2}]'))
        self.assertIn('$["caf\\u00e9"][0]: duplicate key \'x\'', err)


for _name in [n for n in dir(s2.B2S2) if n.startswith("test_") and n not in F2StoppedRoleDigest.__dict__]:
    setattr(F2StoppedRoleDigest, _name, None)  # the slice-1/2 tests run in their own modules


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
