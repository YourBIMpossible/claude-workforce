"""T36 a–e matrix and T37 export tail — lane S5M (MyBuddy), WF-012. Contract: INTERFACE-CONTRACT-S5.md section 4
(T36 re-check through `wf verify` and `backup.verify_copy`; T37 export tail), section 5 (`backup.verify_copy`,
`LIMIT_NO_CHECKPOINT`), section 6 (S5M row). Spec rows T36 (line 311) and T37 (line 312).

Three classes:
  T36ViaVerify      the matrix through `wf verify` (runs on main today; the retained checkpoint comes from `wf digest`).
  T36ViaVerifyCopy  the same matrix through `backup.verify_copy(path, checkpoint=(job, seq, self))` on a copy of the
                    fixture ledger placed in a separate location (fixture-only; restore itself is an owner procedure).
                    Skipped with "backup module not present on this tree" until S5B lands.
  T37ExportTail     missing export recreated by `wf export`; stale export reported; export tampered after generation
                    reported by the manifest hash recorded at export; the export is never authority.

Tampering is done with direct SQL on the fixture ledger only (immutability triggers dropped and re-created, as the
existing T36 tests do). No platform-dependent file semantics are relied on: every operation is an ordinary
read/write/copy/remove on files under WF_TEST_TMP, so no platform skip is needed; the platform is recorded in the
evidence log of every test. Existing T36 assertions (test_b1_gaps, test_t36_verify_checkpoint) are not repeated
verbatim: this file is the slice-5 re-check that runs the whole a–e ladder on one fixture through both surfaces.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sqlite3
import sys
import unittest

from test_wf import Base, log  # noqa: E402  (sets sys.path to the wf package; fresh WF_TEST_TMP)
from wfcore import canon, commands, db as dbm, export, paths  # noqa: E402

BACKUP_PRESENT = importlib.util.find_spec("wfcore.backup") is not None
SKIP_BACKUP = "backup module not present on this tree (wfcore/backup.py lands on S5B)"
PLAT = sys.platform
BANNED = (r"\bauthentic\b", r"tamper[- ]?proof")
STALE = "export events.jsonl is stale relative to the ledger"


class _Ladder(Base):
    """Shared fixture and tamper helpers (no tests of its own)."""

    def setUp(self) -> None:
        super().setUp()
        self.evidence: dict = {"platform": PLAT, "python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version}

    def tearDown(self) -> None:
        log(f"{self._testMethodName}-s5m", self.evidence)

    # ---- fixture
    def chain(self) -> tuple[int, str]:
        """Job j with a grant and two intents (head seq 5); returns the retained checkpoint (seq, self) from digest."""
        self.new_job(); self.activate(); self.intent(note="one"); self.intent(note="two")
        h = commands.digest(self.db, "j")["head"]
        self.assertEqual(h["seq"], 5)
        return h["seq"], h["self"]

    def rows(self) -> list[dict]:
        conn = dbm.connect(self.db, create=False)
        try:
            return dbm.rows(conn, "j")
        finally:
            conn.close()

    def head(self) -> tuple[int, str]:
        r = self.rows()[-1]
        return r["seq"], r["self"]

    def raw(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db, isolation_level=None)

    def refresh_export(self) -> None:
        ref = export.refresh(self.db, "j"); self.assertNotIn("error", ref, ref)

    # ---- T36 tampers (fixture ledger only; triggers dropped then re-created)
    def bit_flip(self, seq: int = 4) -> None:
        """(a): change one stored content byte without recomputing any hash."""
        conn = self.raw()
        try:
            conn.execute("DROP TRIGGER events_no_update")
            conn.execute("UPDATE events SET content = replace(content, 'one', 'onE') WHERE job='j' AND seq=?", (seq,))
            dbm.ensure_schema(conn)
        finally:
            conn.close()
        self.refresh_export()

    def consistent_rewrite(self, seq: int = 4) -> None:
        """(b): change row `seq` and recompute content_sha256/self for it and prev/self for every later row."""
        rows = self.rows()
        conn = self.raw()
        try:
            conn.execute("DROP TRIGGER events_no_update")
            prev = rows[seq - 2]["self"] if seq > 1 else canon.GENESIS
            for r in rows[seq - 1:]:
                r = dict(r)
                if r["seq"] == seq:
                    body = json.loads(r["content"]); body["note"] = "REWRITTEN"
                    r["content"] = canon.canonical(body); r["content_sha256"] = canon.sha256_text(r["content"])
                r["prev"] = prev; r["self"] = canon.event_self_hash(r)
                conn.execute("UPDATE events SET content=:content, content_sha256=:content_sha256, prev=:prev, self=:self"
                             " WHERE job=:job AND seq=:seq", r)
                prev = r["self"]
            dbm.ensure_schema(conn)
        finally:
            conn.close()
        self.refresh_export()

    def delete_tail(self) -> None:
        """(c): remove the head row."""
        conn = self.raw()
        try:
            conn.execute("DROP TRIGGER events_no_delete")
            conn.execute("DELETE FROM events WHERE job='j' AND seq=(SELECT MAX(seq) FROM events WHERE job='j')")
            dbm.ensure_schema(conn)
        finally:
            conn.close()
        self.refresh_export()

    # ---- wording checks
    def assert_no_banned_words(self, out, name: str = "") -> None:
        text = json.dumps(out, default=str)
        for pat in BANNED:
            self.assertIsNone(re.search(pat, text, re.IGNORECASE), (name, pat))


# ====================================================================== T36 through `wf verify`
class T36ViaVerify(_Ladder):
    def verify(self, cp: tuple[int, str] | None = None) -> dict:
        argv = ["verify", "j"] + (["--checkpoint", f"{cp[0]}:{cp[1]}"] if cp else [])
        c, r = self.run_wf(*argv)
        self.assertEqual(c, 0, r)                          # verify reports; it never raises on a finding
        self.assert_no_banned_words(r, "verify")
        return r

    def test_T36a_bit_flip_detected_at_level_1_2(self):
        seq, h = self.chain()
        self.bit_flip()
        r = self.verify()
        self.assertFalse(r["clean"], r)
        self.assertTrue(r["jobs"]["j"]["problems"], r["jobs"]["j"])      # chain / content hash problem
        self.assertEqual(r["ledger_problems"], {"schema": [], "integrity": []})
        r2 = self.verify((seq, h))                                        # the checkpoint still matches the head row
        self.assertFalse(r2["clean"]); self.assertIn("checkpoint", r2)
        self.evidence["a_problems"] = r["jobs"]["j"]["problems"]

    def test_T36b_consistent_rewrite_passes_without_checkpoint_internal_consistency_only(self):
        seq, h = self.chain()
        self.consistent_rewrite()
        self.assertNotEqual(self.head()[1], h)
        r = self.verify()
        self.assertTrue(r["clean"], r)
        self.assertEqual(r["jobs"]["j"]["problems"], [])
        self.assertIn("internal consistency only", r["ladder"])
        self.assertNotIn("checkpoint", r)
        self.evidence["b_ladder"] = r["ladder"]

    def test_T36c_tail_deletion_passes_without_checkpoint_internal_consistency_only(self):
        seq, h = self.chain()
        self.delete_tail()
        self.assertEqual(self.head()[0], seq - 1)
        r = self.verify()
        self.assertTrue(r["clean"], r)
        self.assertEqual(r["jobs"]["j"]["problems"], [])
        self.assertIn("internal consistency only", r["ladder"])

    def test_T36d_rewrite_and_tail_deletion_detected_with_retained_checkpoint(self):
        seq, h = self.chain()
        self.consistent_rewrite()
        r = self.verify((seq, h))
        self.assertFalse(r["clean"], r)
        self.assertEqual(r["checkpoint"]["status"], "mismatch", r["checkpoint"])
        self.assertTrue(any("retained checkpoint" in p for p in r["jobs"]["j"]["problems"]), r["jobs"]["j"])
        self.delete_tail()                                                # (c) on top: head now below seq
        r2 = self.verify((seq, h))
        self.assertFalse(r2["clean"], r2)
        self.assertEqual(r2["checkpoint"]["status"], "missing", r2["checkpoint"])
        self.evidence["d"] = {"rewrite": r["checkpoint"]["status"], "tail": r2["checkpoint"]["status"]}

    def test_T36e_altered_checkpoint_not_detected_and_named_trusted(self):
        seq, h = self.chain()
        self.consistent_rewrite()
        altered = self.head()                                             # the rewriter also altered the retained value
        self.assertNotEqual(altered[1], h)
        r = self.verify(altered)
        self.assertTrue(r["clean"], r)
        self.assertEqual(r["checkpoint"]["status"], "match", r["checkpoint"])
        self.assertIn("trusted as supplied", json.dumps(r["checkpoint"]))
        self.assertIn("not detectable", json.dumps(r["checkpoint"]))
        self.assertEqual(r["checkpoint"]["disclaimer"], "not an authenticity claim")
        self.evidence["e"] = r["checkpoint"]["statement"]


# ====================================================================== T36 through `backup.verify_copy`
@unittest.skipUnless(BACKUP_PRESENT, SKIP_BACKUP)
class T36ViaVerifyCopy(_Ladder):
    """The restored-copy verifier (section 2) on a copy of the fixture ledger in a separate location. The copy is
    taken with no writer running (plain file copy of a quiescent fixture, not `wf backup`: that path is S5B's)."""

    def setUp(self) -> None:
        super().setUp()
        self.backup = importlib.import_module("wfcore.backup")
        self.copy_dir = os.path.join(self.dir, "restored-elsewhere"); os.makedirs(self.copy_dir)

    def copy(self) -> str:
        p = os.path.join(self.copy_dir, f"copy-{len(os.listdir(self.copy_dir))}.db")
        shutil.copyfile(self.db, p)
        return p

    def verify_copy(self, path: str, cp: tuple[int, str] | None = None) -> tuple[dict, str]:
        before = os.stat(path).st_mtime_ns, os.path.getsize(path)
        r = self.backup.verify_copy(path, checkpoint=(("j", cp[0], cp[1]) if cp else None))
        self.assertEqual((os.stat(path).st_mtime_ns, os.path.getsize(path)), before, "verify_copy never writes")
        self.assert_no_banned_words(r, "verify_copy")
        text = json.dumps(r, default=str)
        self.evidence.setdefault("verify_copy", []).append(text[:600])
        return r, text

    def assert_status(self, text: str, status: str, *not_status: str) -> None:
        self.assertIn(status, text)
        for s in not_status:
            self.assertNotIn(s, text, (status, text))

    def test_T36a_bit_flip_detected_in_copy(self):
        seq, h = self.chain(); self.bit_flip()
        r, text = self.verify_copy(self.copy())
        chains = json.dumps(r.get("chains", r), default=str)
        self.assertNotEqual(chains, json.dumps({"j": []}), "a chain problem must be reported for job j")
        self.assertTrue(any(k in text for k in ("content_sha256", "self", "hash", "mismatch", "chain")), text)

    def test_T36b_c_rewrite_and_tail_deletion_pass_without_checkpoint_with_limit(self):
        seq, h = self.chain()
        self.consistent_rewrite(); p1 = self.copy()
        self.delete_tail(); p2 = self.copy()
        for p in (p1, p2):
            with self.subTest(copy=os.path.basename(p)):
                r, text = self.verify_copy(p)
                self.assertEqual(r.get("chains", {}).get("j", ["<chains key missing>"]), [], text)
                self.assertIn(self.backup.LIMIT_NO_CHECKPOINT, text)
                self.assertIn("internally consistent only", self.backup.LIMIT_NO_CHECKPOINT)
                self.assertIn("not detectable", self.backup.LIMIT_NO_CHECKPOINT)

    def test_T36d_rewrite_differs_and_tail_deletion_missing_with_checkpoint(self):
        seq, h = self.chain()
        self.consistent_rewrite()
        r, text = self.verify_copy(self.copy(), (seq, h))
        self.assert_status(text, "differs", "matches", "missing")
        self.delete_tail()
        r2, text2 = self.verify_copy(self.copy(), (seq, h))
        self.assert_status(text2, "missing", "matches", "differs")

    def test_T36e_altered_checkpoint_matches_copy_and_names_trusted(self):
        seq, h = self.chain()
        self.consistent_rewrite()
        altered = self.head()
        r, text = self.verify_copy(self.copy(), altered)
        self.assert_status(text, "matches", "differs", "missing")
        self.assertIn("trusted", text.lower(), "the output names the retained value as trusted (section 4 e)")

    def test_T36_intact_copy_matches_retained_checkpoint(self):
        seq, h = self.chain()
        r, text = self.verify_copy(self.copy(), (seq, h))
        self.assert_status(text, "matches", "differs", "missing")
        self.assertEqual(r.get("chains", {}).get("j", ["<chains key missing>"]), [], text)


# ====================================================================== T37 export tail
class T37ExportTail(_Ladder):
    def export_dir(self) -> str:
        return paths.export_dir(self.db, "j")

    def export_problems(self) -> list[str]:
        return commands.verify(self.db, "j")["jobs"]["j"]["export"]

    def manifest_sha(self) -> str:
        with open(os.path.join(self.export_dir(), "manifest.txt"), "rb") as fh:
            return canon.sha256_text(fh.read().decode("utf-8"))

    def export_ok(self, head_before: tuple[int, str]) -> dict:
        c, r = self.run_wf("export", "j")
        self.assertEqual(c, 0, r); self.assertEqual(r["problems"], [], r)
        self.assertEqual(self.head(), head_before, "wf export never changes the ledger")
        self.assertEqual(self.export_problems(), [])
        return r

    def test_T37_missing_export_recreated_by_wf_export(self):
        self.chain(); head = self.head()
        shutil.rmtree(self.export_dir())
        probs = self.export_problems()
        self.assertTrue(any(p.startswith("export manifest missing") for p in probs), probs)
        self.assertFalse(commands.verify(self.db, "j")["clean"])
        self.export_ok(head)
        self.assertTrue(all(os.path.isfile(os.path.join(self.export_dir(), f)) for f in ("manifest.txt", "events.jsonl")))
        self.assertTrue(commands.verify(self.db, "j")["clean"])

    def test_T37_stale_export_reported_then_regenerated(self):
        """A consistent but outdated export (taken before the last event, manifest matching its own files)."""
        self.new_job(); self.activate(); self.intent(note="one")
        snapshot = os.path.join(self.dir, "export-old"); shutil.copytree(self.export_dir(), snapshot)
        self.intent(note="two"); head = self.head()
        shutil.rmtree(self.export_dir()); shutil.copytree(snapshot, self.export_dir())
        probs = self.export_problems()
        self.assertIn(STALE, probs, probs)
        self.assertFalse(any(p.startswith("export file differs from manifest") for p in probs), probs)  # stale, not tampered
        self.assertFalse(commands.verify(self.db, "j")["clean"])
        self.export_ok(head)
        self.evidence["stale_problems"] = probs

    def test_T37_export_tampered_after_generation_reported_by_recorded_hash(self):
        self.chain(); head = self.head()
        recorded = self.manifest_sha()
        ev = os.path.join(self.export_dir(), "events.jsonl")
        with open(ev, "a", encoding="utf-8") as fh:
            fh.write('{"forged":true}\n')
        probs = self.export_problems()
        self.assertIn("export file differs from manifest: events.jsonl", probs, probs)
        self.assertIn(STALE, probs, probs)
        self.assertEqual(self.manifest_sha(), recorded, "the manifest recorded at export is what the comparison uses")
        self.assertEqual(self.head(), head)                               # the forged line never reached the ledger
        self.export_ok(head)
        with open(ev, encoding="utf-8") as fh:
            self.assertNotIn("forged", fh.read())
        self.evidence["tamper_problems"] = probs

    def test_T37_export_is_never_authority(self):
        """A forged export cannot change what wf reports: status/digest/verify read the ledger; the export is
        regenerated from the ledger and the forged content disappears; the chain and head are unchanged throughout."""
        seq, h = self.chain(); rows = self.rows()
        ev = os.path.join(self.export_dir(), "events.jsonl")
        with open(ev, "w", encoding="utf-8") as fh:                      # replace the whole export content
            fh.write(canon.canonical({**rows[-1], "content": '{"forged":"authority"}'}) + "\n")
        st = commands.status(self.db, "j"); dg = commands.digest(self.db, "j")
        self.assertEqual((st["head"]["seq"], st["head"]["self"]), (seq, h))
        self.assertEqual((dg["head"]["seq"], dg["head"]["self"]), (seq, h))
        self.assertEqual(dg["events"], len(rows))
        self.assertNotIn("forged", json.dumps(dg) + json.dumps(st))
        v = commands.verify(self.db, "j")
        self.assertEqual(v["jobs"]["j"]["problems"], [])                  # the ledger chain is untouched
        self.assertFalse(v["clean"])                                      # only the export is reported
        self.export_ok((seq, h))
        self.assertEqual(self.rows(), rows)


if __name__ == "__main__":
    unittest.main(verbosity=2)
