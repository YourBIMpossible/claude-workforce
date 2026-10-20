"""PACKAGE FIX2 batch Z, lane F2ZZ (WF-129; AMENDMENT FIX2-A17): wording and one input check.

  Z1  the digest's stopped-role entry names the grant state when no grant is in force:
      "replacement not permitted (grant <state>)" (Lazy WF-120 N1).
  Z4  an intent's `sha_before` is 64 lower-case hex or "", else rejected(malformed) and nothing is written
      (Plexy WF-118 Left-Flag; receipts keep FIX2 #35's rule).
  Z5  (rename only, in test_wf021_fs_semantics.py.)
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import unittest
from contextlib import closing

import test_b2s2_wiring as s2  # noqa: E402  (slice-1/2 CLI helpers; see the detach loop at the end)
from test_wf import Base  # noqa: E402

PATH_A = "jobs/j/work/a.txt"
TURN = "owner turn for job j"


class FZStoppedRoleGrantState(s2.B2S2):
    """Z1: with the grant revoked, the stopped builder's entry says which grant state blocks a replacement."""

    def test_revoked_grant_is_named(self):
        self.explicit_repl_job()  # GRANT_REPL: a replacement would otherwise be possible
        c, r = self.intent()
        self.assertEqual(c, 0, r)
        self.write_work(PATH_A, "changed")
        self.reconcile()
        stop_seq = self.stop_builder("B1")
        rep = self.reconcile()
        self.assertGreater(rep["seq"], stop_seq)
        c, r = self.coord("grant", "revoke", "j", "--reason", "stop", "--turn", TURN)
        self.assertEqual(c, 0, r)
        c, d = self.run_wf("digest", "j")
        self.assertEqual(c, 0, d)
        items = [n for n in d["needs_owner"] if n["item"].startswith(f"unreconciled report {rep['seq']} for a stopped")]
        self.assertEqual(len(items), 1, d["needs_owner"])
        self.assertEqual(items[0]["item"], f"unreconciled report {rep['seq']} for a stopped builder; "
                                           "replacement not permitted (grant revoked)")
        self.assertIn("no session can acknowledge", items[0]["recommendation"])


class FZIntentShaBefore(Base):
    """Z4: an intent's sha_before has the receipt's format rule."""

    def setUp(self) -> None:
        super().setUp()
        self.new_job(); self.activate()

    def count(self) -> int:
        with closing(sqlite3.connect(self.db)) as con:
            return con.execute("SELECT count(*) FROM events").fetchone()[0]

    def append_intent(self, sha_before: str):
        content = json.dumps({"path": PATH_A, "sha_before": sha_before, "note": "n"})
        return self.run_wf("append", "j", "--type", "intent", "--content", content)

    def test_malformed_sha_before_is_refused_and_nothing_written(self):
        good = hashlib.sha256(b"old").hexdigest()
        for bad in ("XYZ", good[:63], good.upper(), good + "\n", " " + good):
            with self.subTest(bad=bad):
                before = self.count()
                c, r = self.append_intent(bad)
                self.assertEqual((c, r.get("code")), (1, "rejected"), r)
                self.assertIn("rejected(malformed)", r["error"])
                self.assertIn("intent sha_before", r["error"])
                self.assertEqual(self.count(), before)

    def test_empty_and_hex_sha_before_are_committed(self):
        for ok in ("", hashlib.sha256(b"old").hexdigest()):
            with self.subTest(ok=ok):
                c, r = self.append_intent(ok)
                self.assertEqual(c, 0, r)
                self.assertEqual(r["outcome"], "COMMITTED", r)


for _name in [n for n in dir(s2.B2S2) if n.startswith("test_") and n not in FZStoppedRoleGrantState.__dict__]:
    setattr(FZStoppedRoleGrantState, _name, None)  # the slice-1/2 tests run in their own modules


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
