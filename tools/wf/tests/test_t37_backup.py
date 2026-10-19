"""T37 backup and restored-copy verification (B2 slice 5, lane S5B, Plexy): INTERFACE-CONTRACT-S5.md §6 S5B.

- backup while a second process is writing: the backup verifies and its head is in the source history; head
  equality is asserted only after the writer stops (`writer_stopped=True`);
- every refusal (`dest-exists`, `dest-parent-missing`, `dest-inside-ledger`, missing source), nothing written;
- `verify_copy` without a checkpoint (LIMIT_NO_CHECKPOINT) and with one: matches, differs, missing;
- the backup file is unchanged after verify, and a failed verification never deletes it.

Fixture-only: every ledger lives under the test temp root (WF_TEST_TMP, fixtures tmp). Tampered copies are made
with raw sqlite3 on fixture backups only. No real restore, no real-ledger access.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

from test_wf import WF_DIR, Base  # noqa: E402
from wfcore import backup, db as dbm  # noqa: E402
from wfcore.canon import canonical, event_self_hash, sha256_text  # noqa: E402

WRITER = r'''
import json, os, sys, time
wf_dir, db, started, stop, limit = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
sys.path.insert(0, wf_dir)
import wf
n = 0
while n < limit and not os.path.exists(stop):
    content = json.dumps({"path": "jobs/j/work/a.txt", "sha_before": "", "note": f"w{n}"})
    code, res = wf.run(["--db", db, "--role", "builder", "--session", "B1", "--msg-id", f"W{n}",
                        "append", "j", "--type", "intent", "--content", content])
    if code != 0 or res.get("outcome") != "COMMITTED":
        print(json.dumps({"n": n, "failed": res})); sys.exit(3)
    n += 1
    if n == 1:
        open(started, "w").close()
    time.sleep(0.005)
print(json.dumps({"n": n}))
'''


def sha256_file(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def snapshot(path: str) -> tuple[str, int, int]:
    st = os.stat(path)
    return sha256_file(path), st.st_size, st.st_mtime_ns


def tamper(path: str, seq: int, *, consistent: bool, job: str = "j") -> None:
    """Fixture-only tamper of a backup copy: rewrite the note of the event at `seq`. consistent=True re-hashes that
    row and re-chains every later row (a consistent rewrite); False leaves the hashes stale (a detectable flip)."""
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("DROP TRIGGER events_no_update")
        rows = [dict(zip(dbm.EVENT_COLS, r)) for r in conn.execute(
            f"SELECT {','.join(dbm.EVENT_COLS)} FROM events WHERE job=? AND seq>=? ORDER BY seq", (job, seq))]
        content = json.loads(rows[0]["content"])
        content["note"] = "rewritten"
        rows[0]["content"] = canonical(content)
        prev = None
        for r in rows:
            if consistent:
                if prev is not None:
                    r["prev"] = prev
                r["content_sha256"] = sha256_text(r["content"])
                r["self"] = event_self_hash(r)
                prev = r["self"]
            conn.execute("UPDATE events SET content=?, content_sha256=?, prev=?, self=? WHERE job=? AND seq=?",
                         (r["content"], r["content_sha256"], r["prev"], r["self"], job, r["seq"]))
            if not consistent:
                break
    finally:
        conn.close()


class T37Backup(Base):
    def ledger(self, intents: int = 3) -> None:
        self.new_job(); self.activate()
        for i in range(intents):
            code, res = self.intent(msg_id=f"M{i}", note=f"n{i}")
            self.assertEqual(res["outcome"], "COMMITTED", res)

    def out(self, name: str) -> str:
        d = os.path.join(self.dir, "out")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, name)

    def source_head(self) -> tuple[int, str]:
        conn = dbm.connect(self.db, create=False)
        try:
            # W45-F1: a second process may be inside COMMIT (PENDING/EXCLUSIVE) and this connection has busy_timeout
            # 0; read in a bounded snapshot as the product does (db.read_snapshot retries BUSY within T_ACQ_BOUND).
            with dbm.read_snapshot(conn):
                return dbm.head(conn, "j")
        finally:
            conn.close()

    def test_source_head_waits_out_a_writer_holding_the_lock(self):
        """FIX2 #38 (MyBuddy WF-051, W45-F1): a writer inside its commit window holds the lock just after
        source_head's connection is set up, which is where the flake hit. source_head must wait the window out
        within T_ACQ_BOUND, not fail with `database is locked` (its connection has busy_timeout 0)."""
        self.ledger(intents=1)
        expected = self.source_head()
        holder = sqlite3.connect(self.db, timeout=0, isolation_level=None, check_same_thread=False)
        release = threading.Timer(0.5, holder.execute, ("COMMIT",))
        real_connect = dbm.connect

        def connect_then_lock(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            holder.execute("BEGIN EXCLUSIVE")
            release.start()
            return conn

        try:
            with mock.patch.object(dbm, "connect", connect_then_lock):
                self.assertEqual(self.source_head(), expected)
        finally:
            if release.ident is not None:
                release.join()
            if holder.in_transaction:
                holder.execute("ROLLBACK")
            holder.close()

    # --- §1 backup -----------------------------------------------------------------------------------------------
    def test_backup_while_second_process_writes(self):
        self.ledger(intents=1)
        started, stop = os.path.join(self.dir, "writer.started"), os.path.join(self.dir, "writer.stop")
        script = os.path.join(self.dir, "writer.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(WRITER)
        env = dict(os.environ); env.pop("WF_TEST_FAULTS", None)
        proc = subprocess.Popen([sys.executable, "-I", script, WF_DIR, self.db, started, stop, "100000"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        try:
            deadline = time.monotonic() + 30
            while not os.path.exists(started):
                if proc.poll() is not None:
                    self.fail(f"writer exited early: {proc.communicate()}")
                self.assertLess(time.monotonic(), deadline, "writer did not start")
                time.sleep(0.01)
            live = backup.backup(self.db, self.out("live.db"))              # writer active, not stated stopped
            still_writing = proc.poll() is None
            while self.source_head()[0] <= live["heads"]["j"]["seq"]:          # the writer moves past the backup
                self.assertIsNone(proc.poll(), "writer exited before writing past the backup head")
                self.assertLess(time.monotonic(), deadline + 30, "writer did not advance")
                time.sleep(0.01)
        finally:
            open(stop, "w").close()
            so, se = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 0, (so, se))
        written = json.loads(so)["n"]
        self.assertTrue(still_writing, f"writer must run during the backup ({written} written)")

        self.assertTrue(live["integrity"])
        self.assertEqual(live["chains"], {"j": []})
        self.assertEqual(live["head_in_source"], {"j": True})
        self.assertIsNone(live["head_equal"])                                  # not asserted while a writer may run
        self.assertEqual(live["writer_active"], "unknown")
        self.assertEqual(live["limitations"], [backup.LIMIT_LIVE_HEAD])
        self.assertEqual(backup.LIMIT_LIVE_HEAD, "head is present in source history; equality with the live head is "
                                                 "not asserted while a writer may be active")
        final_seq, final_self = self.source_head()
        self.assertGreater(final_seq, live["heads"]["j"]["seq"])               # source advanced past the backup
        self.assertGreater(final_seq, 3 + 1, written)                         # job, propose, activate, intent, writes

        stopped = backup.backup(self.db, self.out("stopped.db"), writer_stopped=True)   # writer has exited
        self.assertEqual(stopped["head_equal"], {"j": True})
        self.assertEqual(stopped["heads"]["j"], {"seq": final_seq, "self": final_self})
        self.assertEqual((stopped["writer_active"], stopped["limitations"]), (False, []))
        self.assertEqual(stopped["chains"], {"j": []})
        v = backup.verify_copy(stopped["dest"], checkpoint=("j", live["heads"]["j"]["seq"], live["heads"]["j"]["self"]))
        self.assertEqual(v["checkpoint"]["status"], "matches")                # the live backup's head is history

    def test_result_shape_and_engine_copy(self):
        self.ledger()
        before = snapshot(self.db)
        res = backup.backup(self.db, self.out("b.db"), writer_stopped=True)
        self.assertEqual(set(res), {"dest", "bytes", "integrity", "chains", "heads", "head_in_source", "head_equal",
                                    "writer_active", "limitations"})
        self.assertEqual(res["dest"], os.path.abspath(self.out("b.db")))
        self.assertEqual(res["bytes"], os.path.getsize(self.out("b.db")))
        self.assertEqual(snapshot(self.db)[:2], before[:2])                   # source content unchanged
        conn = dbm.connect(self.db, create=False)
        try:
            src_rows = dbm.rows(conn, "j")
        finally:
            conn.close()
        copy = sqlite3.connect(self.out("b.db"))
        try:
            got = [dict(zip(dbm.EVENT_COLS, r)) for r in copy.execute(
                f"SELECT {','.join(dbm.EVENT_COLS)} FROM events WHERE job='j' ORDER BY seq")]
            triggers = {r[0] for r in copy.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        finally:
            copy.close()
        self.assertEqual(got, src_rows)
        self.assertEqual(triggers, {"events_no_update", "events_no_delete"})  # schema carried by the engine copy
        self.assertEqual(res["heads"]["j"], {"seq": src_rows[-1]["seq"], "self": src_rows[-1]["self"]})

    def test_refusals_write_nothing(self):
        self.ledger(intents=1)
        ledger_dir = os.path.dirname(self.db)
        existing = self.out("exists.db")
        with open(existing, "wb") as fh:
            fh.write(b"keep")
        cases = [
            ("dest-exists", existing),
            ("dest-exists", self.db),                                          # the ledger itself
            ("dest-parent-missing", os.path.join(self.dir, "nope", "b.db")),
            ("dest-inside-ledger", os.path.join(ledger_dir, "b.db")),
            ("dest-inside-ledger", os.path.join(ledger_dir, ".", "b2.db")),
        ]
        if os.name == "nt":                                                    # case-folded containment on Windows
            cases.append(("dest-inside-ledger", os.path.join(ledger_dir.upper(), "b3.db")))
        before_src, before_dir = snapshot(self.db), sorted(os.listdir(ledger_dir))
        for reason, dest in cases:
            with self.subTest(reason=reason, dest=dest):
                with self.assertRaises(dbm.WfRefused) as cm:
                    backup.backup(self.db, dest)
                self.assertEqual(str(cm.exception), reason)
        self.assertEqual(snapshot(self.db), before_src)
        self.assertEqual(sorted(os.listdir(ledger_dir)), before_dir)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "nope")))
        with open(existing, "rb") as fh:
            self.assertEqual(fh.read(), b"keep")
        with self.assertRaises(dbm.WfRefused):                                 # missing source: no destination made
            backup.backup(os.path.join(self.dir, "absent", "l.db"), self.out("never.db"))
        self.assertFalse(os.path.exists(self.out("never.db")))

    def test_failed_verification_never_deletes(self):
        self.ledger()
        first = backup.backup(self.db, self.out("first.db"), writer_stopped=True)
        tamper(first["dest"], 4, consistent=False)                            # a broken chain as backup source
        res = backup.backup(first["dest"], os.path.join(self.dir, "second.db"), writer_stopped=True)
        self.assertTrue(res["chains"]["j"], res)
        self.assertTrue(any("seq 4" in p for p in res["chains"]["j"]), res["chains"])
        self.assertTrue(os.path.isfile(res["dest"]))
        self.assertEqual(res["bytes"], os.path.getsize(res["dest"]))

    # --- §2 verify_copy --------------------------------------------------------------------------------------------
    def test_verify_copy_without_checkpoint(self):
        self.ledger()
        dest = backup.backup(self.db, self.out("b.db"), writer_stopped=True)["dest"]
        v = backup.verify_copy(dest)
        self.assertTrue(v["clean"]); self.assertTrue(v["integrity"]); self.assertTrue(v["readable"])
        self.assertIsNone(v["checkpoint"])
        self.assertEqual(v["limitations"], [backup.LIMIT_NO_CHECKPOINT])
        self.assertEqual(backup.LIMIT_NO_CHECKPOINT, "internally consistent only; a consistent whole-chain rewrite or "
                                                     "tail deletion is not detectable without an independently "
                                                     "retained checkpoint")
        tamper(dest, 3, consistent=True)                                       # consistent rewrite passes, as stated
        v = backup.verify_copy(dest)
        self.assertTrue(v["clean"], v)
        self.assertEqual(v["chains"], {"j": []})
        self.assertEqual(v["limitations"], [backup.LIMIT_NO_CHECKPOINT])

    def test_verify_copy_with_checkpoint_matches_differs_missing(self):
        self.ledger()
        early = backup.backup(self.db, self.out("early.db"), writer_stopped=True)
        for i in range(3, 5):
            self.assertEqual(self.intent(msg_id=f"M{i}", note=f"n{i}")[1]["outcome"], "COMMITTED")
        head_seq, head_self = self.source_head()
        conn = dbm.connect(self.db, create=False)
        try:
            seq3 = dbm.row_at(conn, "j", 3)["self"]
        finally:
            conn.close()
        full = backup.backup(self.db, self.out("full.db"), writer_stopped=True)["dest"]

        v = backup.verify_copy(full, checkpoint=("j", head_seq, head_self))
        self.assertEqual(v["checkpoint"]["status"], "matches"); self.assertTrue(v["clean"])
        self.assertEqual(v["limitations"], [backup.LIMIT_CHECKPOINT_TRUSTED])
        self.assertIn("trusted as supplied", v["limitations"][0])
        v = backup.verify_copy(full, checkpoint=("j", 3, seq3))               # an older checkpoint still matches
        self.assertEqual(v["checkpoint"]["status"], "matches"); self.assertTrue(v["clean"])

        v = backup.verify_copy(full, checkpoint=("j", 3, "0" * 64))
        self.assertEqual((v["checkpoint"]["status"], v["checkpoint"]["found"]), ("differs", seq3))
        self.assertFalse(v["clean"])

        v = backup.verify_copy(early["dest"], checkpoint=("j", head_seq, head_self))   # truncated below seq
        self.assertEqual(v["checkpoint"]["status"], "missing")
        self.assertEqual(v["checkpoint"]["head_seq"], early["heads"]["j"]["seq"])
        self.assertLess(v["checkpoint"]["head_seq"], head_seq)
        self.assertIsNone(v["checkpoint"]["found"]); self.assertFalse(v["clean"])
        self.assertEqual(v["chains"], {"j": []})                               # internally consistent, yet detected

        rewritten = backup.backup(self.db, self.out("rewritten.db"), writer_stopped=True)["dest"]
        tamper(rewritten, 2, consistent=True)                                # consistent rewrite, checkpoint catches
        v = backup.verify_copy(rewritten, checkpoint=("j", 3, seq3))
        self.assertEqual(v["chains"], {"j": []})
        self.assertEqual(v["checkpoint"]["status"], "differs"); self.assertFalse(v["clean"])

        v = backup.verify_copy(full, checkpoint=("other", 1, seq3))           # no such job in the copy
        self.assertEqual(v["checkpoint"]["status"], "missing")
        for bad in (("j", 0, seq3), ("j", 3, "XYZ"), (None, 3, seq3), ("j", "3", seq3), ("j", 3)):
            with self.subTest(bad=bad), self.assertRaises(dbm.WfUsage):
                backup.verify_copy(full, checkpoint=bad)

    def test_verify_copy_reports_bit_flip_and_unreadable(self):
        self.ledger()
        dest = backup.backup(self.db, self.out("b.db"), writer_stopped=True)["dest"]
        tamper(dest, 4, consistent=False)
        v = backup.verify_copy(dest)
        self.assertFalse(v["clean"])
        self.assertTrue(any("seq 4" in p for p in v["chains"]["j"]), v["chains"])
        junk = self.out("junk.db")
        with open(junk, "wb") as fh:
            fh.write(b"not a database" * 100)
        before = snapshot(junk)
        v = backup.verify_copy(junk, checkpoint=("j", 1, "a" * 64))
        self.assertEqual((v["readable"], v["integrity"], v["clean"]), (False, False, False))
        self.assertEqual(v["checkpoint"]["status"], "missing")
        self.assertEqual(snapshot(junk), before)
        with self.assertRaises(dbm.WfRefused):
            backup.verify_copy(self.out("absent.db"))

    def test_backup_file_unchanged_after_verify(self):
        self.ledger()
        res = backup.backup(self.db, self.out("b.db"), writer_stopped=False)
        out_dir = os.path.dirname(res["dest"])
        before, listing = snapshot(res["dest"]), sorted(os.listdir(out_dir))
        self.assertEqual(listing, ["b.db"])                                    # no journal or side file left behind
        head = res["heads"]["j"]
        for cp in (None, ("j", head["seq"], head["self"]), ("j", 2, "f" * 64), ("j", 99, "f" * 64)):
            backup.verify_copy(res["dest"], checkpoint=cp)
        self.assertEqual(snapshot(res["dest"]), before)
        self.assertEqual(sorted(os.listdir(out_dir)), listing)

    def test_imports_are_restricted(self):
        """§5: stdlib, .db, .canon, .checkpointcmp only; never .commands, .txn or .gatecore."""
        with open(backup.__file__, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        local: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                local |= {node.module} if node.module else {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                self.assertIn(node.module.split(".")[0], sys.stdlib_module_names | {"__future__"})
            elif isinstance(node, ast.Import):
                for a in node.names:
                    self.assertIn(a.name.split(".")[0], sys.stdlib_module_names)
        self.assertLessEqual(local, {"db", "canon", "checkpointcmp"})
        self.assertFalse(local & {"commands", "txn", "gatecore"})


if __name__ == "__main__":
    unittest.main()
