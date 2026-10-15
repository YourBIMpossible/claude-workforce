"""T27 / T31 transaction-sequence requirement closure (spec lines 91-103, 302, 306). Owner grant 2026-10-15 (MyBuddy).

Adds only the checks the existing suite does not make (test_b1_gaps T27/T31 races, test_wf T31 schema insert,
TConflict, T27Markers are not repeated):
  R1  Exact committed set under contention, two jobs at once: every worker collected, process closure, each job's
      new rows == its committed workers, seq consecutive, prev/self recomputed independently of `wf verify`.
  R2  Reader holding a read transaction while a writer commits (T27 clause not covered elsewhere): the writer
      commits within its held bound, the reader's snapshot is unchanged while held, and the writer's own oplog
      shows whether COMMIT was retried on BUSY (recorded).
  R3  Different event types competing for the same next seq, deterministically ordered (T31 "the loser ... takes
      seq+1 after reading the new head"): the first writer holds the reservation (fixture fault delay-precommit;
      its committing marker is the start signal), the second waits on BEGIN IMMEDIATE, reads the new head and
      takes seq+1. Its oplog proves the wait and the head it read.
  R4  Constraint-rejection branch (T31 "fails with a constraint error, rolls back, ... rejected(constraint) ...
      writes no ledger row"): driven through txn.write_event's public `build` callback with a row at an existing
      (job, seq) and with a duplicated prev; ROLLBACK, no row, no marker, chain valid.
  R5  "No function opens a second write connection or calls BEGIN IMMEDIATE again while one is held" (spec line
      103): every mutating command run in-process with instrumented sqlite3 connections (factory on the one
      connect entry point in wfcore.db); asserts at most one connection in a write reservation at any instant and
      never a BEGIN IMMEDIATE on a connection already in a transaction. Not a source scan.

  R6  T28 "wf never deletes or moves a journal file" (issue #14 item 2): the real hot journal left by the
      kill-before-commit seam is watched by identity (inode/size) while every Python-level removal/rename entry
      point (os.remove/unlink/rename/replace) is instrumented; status, verify, digest and one append run
      in-process. Any removal of the journal by wf's Python code would be recorded; the engine's own removal
      (SQLite rolls a hot journal back and unlinks it in DELETE mode, inside the C library) is not visible to the
      hook and is the stated observation limit.
  R7  D-018 observation (issue #14 item 4): whether the journal is still present after the next open is pinned as
      an observation with exact wording, not as a pass/fail requirement.
  The seams are fault injection (D-018): none of this is OS-kill or power-loss qualification.

Export freshness is recorded, never asserted as part of sequence success (lane A's export regression is separate).
All data under this test's fresh WF_TEST_TMP scratch; no sleep over one second; every wait is bounded."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
import platform

from test_wf import Base, WF_DIR, log  # noqa: E402  (sets sys.path to the wf package)
import wf  # noqa: E402
from wfcore import canon, commands, db as dbm, markers, paths, txn  # noqa: E402
from wfcore.db import OpLog, WfRejected  # noqa: E402
from wfcore.faults import Faults  # noqa: E402

INTENT = '{"path":"jobs/j/work/a.txt","sha_before":"","note":"%s"}'
BLOCKER = '{"reason":"sequence test"}'
POLL = 0.01


def wait_until(pred, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        time.sleep(POLL)


class _Procs(Base):
    """Process/chain helpers shared by the test classes below (no tests of its own)."""
    # ---------------------------------------------------------------- process helpers
    def spawn(self, *argv: str, job_db: str | None = None, role: str = "builder", session: str = "B1",
              msg_id: str | None = None, faults: str | None = None, oplog: bool = True) -> subprocess.Popen:
        env = dict(os.environ)
        env.pop("WF_TEST_FAULTS", None)
        if faults:
            env["WF_TEST_FAULTS"] = faults
        cmd = [sys.executable, "-I", "-B", os.path.join(WF_DIR, "wf.py"), "--db", job_db or self.db, "--role", role,
               "--session", session, "--json"]
        if oplog:
            cmd.append("--oplog")
        if msg_id:
            cmd += ["--msg-id", msg_id]
        return subprocess.Popen(cmd + list(argv), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

    def collect(self, procs: list[subprocess.Popen], timeout: float = 30.0) -> list[dict]:
        """Every worker collected within one shared deadline; process closure asserted for each."""
        out = []
        deadline = time.monotonic() + timeout
        for i, p in enumerate(procs):
            try:
                so, se = p.communicate(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                p.kill(); so, se = p.communicate()
                self.fail(f"worker {i} did not exit within {timeout}s")
            try:
                res = json.loads(so) if so.strip() else {}
            except ValueError:
                res = {"raw": so}
            out.append({"i": i, "code": p.returncode, "res": res, "stderr": se[-1500:]})
        for i, p in enumerate(procs):
            self.assertIsNotNone(p.poll(), f"worker {i} still running")
        return out

    def rows(self, job: str = "j") -> list[dict]:
        conn = dbm.connect(self.db, create=False)
        try:
            return dbm.rows(conn, job)
        finally:
            conn.close()

    def assert_chain_recomputed(self, rows: list[dict], job: str) -> None:
        """Independent of wf verify: consecutive seq from 1, prev links, self recomputed from the row."""
        self.assertEqual([r["seq"] for r in rows], list(range(1, len(rows) + 1)), job)
        prev = canon.GENESIS
        for r in rows:
            self.assertEqual(r["prev"], prev, (job, r["seq"]))
            self.assertEqual(r["content_sha256"], canon.sha256_text(r["content"]), (job, r["seq"]))
            self.assertEqual(r["self"], canon.event_self_hash(dict(r)), (job, r["seq"]))
            prev = r["self"]

    def ledger_verify_clean_export_recorded(self, tag: str) -> dict:
        """Chain/schema/integrity must be clean; export freshness is recorded separately (lane A territory)."""
        r = commands.verify(self.db, None)
        self.assertEqual(r["ledger_problems"], {"schema": [], "integrity": []}, r)
        for j, v in r["jobs"].items():
            self.assertEqual(v["problems"], [], (j, v))
            self.assertEqual(v["pending_markers"], [], (j, v))
        log(f"{self._testMethodName}-{tag}", {"export": {j: v["export"] for j, v in r["jobs"].items()}, "clean": r["clean"]})
        return r


class TxSeq(_Procs):
    # ---------------------------------------------------------------- R1
    def test_R1_two_jobs_contended_exact_committed_set(self):
        """Six writers at once: three per job, mixed types. Each exits 0 (committed) or lock-timeout (spec-permitted,
        writes nothing). Per job: new rows == committed workers exactly (msg_id, type, seq), seqs consecutive
        from the old head, chain recomputed; no row from a failed worker anywhere."""
        for j in ("j", "k"):
            self.new_job(j); self.activate(j)
        heads = {j: commands.status(self.db, j)["head"]["seq"] for j in ("j", "k")}
        plan = [(j, t, f"r1{j}{t[0]}{os.urandom(3).hex()}") for j in ("j", "k") for t in ("intent", "blocker", "intent")]
        procs = [self.spawn("append", j, "--type", t, "--content", INTENT % mid if t == "intent" else BLOCKER, msg_id=mid)
                 for j, t, mid in plan]
        res = self.collect(procs, timeout=40.0)
        committed: dict[str, dict[str, tuple[str, int]]] = {"j": {}, "k": {}}
        failed = []
        for (j, t, mid), w in zip(plan, res):
            if w["code"] == 0:
                self.assertEqual(w["res"].get("outcome"), "COMMITTED", w)
                committed[j][mid] = (t, w["res"]["seq"])
            else:
                self.assertEqual(w["res"].get("code"), "lock-timeout", w)
                failed.append(mid)
        total = sum(len(c) for c in committed.values())
        self.assertGreaterEqual(total, 2, res)
        for j in ("j", "k"):
            rows = self.rows(j)
            self.assertEqual(len(rows), heads[j] + len(committed[j]), j)
            new = {r["msg_id"]: (r["type"], r["seq"]) for r in rows if r["seq"] > heads[j]}
            self.assertEqual(new, committed[j], j)  # exact committed set, nothing lost or duplicated
            self.assertEqual(sorted(s for _, s in new.values()),
                             list(range(heads[j] + 1, heads[j] + len(committed[j]) + 1)), j)
            self.assertTrue(all(mid not in new for mid in failed), j)
            self.assert_chain_recomputed(rows, j)
        log(f"{self._testMethodName}-summary", {"committed": committed, "lock_timeout": failed,
                                                "begin_attempts": [w["res"].get("ops", []) and
                                                                   max((o.get("attempts", 0) for o in w["res"]["ops"]
                                                                        if o["op"] == "begin_immediate"), default=None)
                                                                   for w in res]})
        self.ledger_verify_clean_export_recorded("verify")

    # ---------------------------------------------------------------- R2
    def test_R2_reader_holding_read_transaction_while_writer_commits(self):
        """A read snapshot (dbm.read_snapshot: BEGIN DEFERRED + SHARED lock) is held in-process for 0.6 s while a
        fresh writer process appends. Asserts: the writer commits (exit 0, outcome COMMITTED) within the bound; the
        reader's view does not change while held (head unchanged at the end of the hold); after release the new
        row is visible and the chain recomputes. Recorded (not asserted: timing-dependent): commit_busy count and
        commit_attempts in the writer's oplog, which show the reader actually delayed COMMIT."""
        self.new_job(); self.activate()
        head = commands.status(self.db, "j")["head"]["seq"]
        mid = "r2" + os.urandom(4).hex()
        held = threading.Event(); release = threading.Event()
        seen: dict[str, object] = {}

        def reader() -> None:
            conn = dbm.connect(self.db, create=False)
            try:
                with dbm.read_snapshot(conn):
                    seen["at_start"] = dbm.head(conn, "j")[0]
                    held.set()
                    release.wait(5.0)
                    seen["at_end"] = dbm.head(conn, "j")[0]  # same snapshot: must equal at_start
                seen["after_release"] = None
            finally:
                conn.close()

        t = threading.Thread(target=reader, daemon=True); t.start()
        self.assertTrue(held.wait(5.0), "reader did not take its snapshot")
        p = self.spawn("append", "j", "--type", "intent", "--content", INTENT % "during-read", msg_id=mid)
        time.sleep(0.6)  # bounded hold, below T_HELD_BOUND (2.0 s) so the writer can still commit
        release.set(); t.join(5.0); self.assertFalse(t.is_alive())
        res = self.collect([p], timeout=20.0)[0]
        self.assertEqual(res["code"], 0, res)
        self.assertEqual(res["res"].get("outcome"), "COMMITTED", res)
        self.assertEqual(res["res"]["seq"], head + 1)
        self.assertEqual(seen["at_start"], head); self.assertEqual(seen["at_end"], head)
        rows = self.rows()
        self.assertEqual(rows[-1]["msg_id"], mid); self.assert_chain_recomputed(rows, "j")
        ops = res["res"].get("ops", [])
        busy = sum(1 for o in ops if o["op"] == "commit_busy")
        log(f"{self._testMethodName}-timing", {"commit_busy_ops": busy, "commit_attempts": res["res"].get("commit_attempts"),
                                               "begin_attempts": [o.get("attempts") for o in ops if o["op"] == "begin_immediate"]})
        self.assertLessEqual(res["res"].get("commit_attempts", 1) * dbm.SLEEP_STEP, dbm.T_HELD_BOUND + 0.5)
        self.ledger_verify_clean_export_recorded("verify")

    # ---------------------------------------------------------------- R3
    def test_R3_different_types_serialized_loser_takes_next_seq(self):
        """Deterministic T31 ordering. Writer A (intent) holds the reservation for 0.8 s via the fixture fault
        delay-precommit; its committing marker (written before the delay) is the signal that A holds the lock.
        Writer B (blocker) is started only after that signal, so B must wait on BEGIN IMMEDIATE, then read the new
        head and take seq+1. Asserts: A at head+1, B at head+2; B's oplog shows >1 begin_immediate attempt and a
        `head` op equal to head+1 (it read A's row before building its own); exactly one row per (job, seq)."""
        self.new_job(); self.activate()
        head = commands.status(self.db, "j")["head"]["seq"]
        pdir = paths.pending_dir(self.db, "j")
        ids = {"A": "r3a" + os.urandom(3).hex(), "B": "r3b" + os.urandom(3).hex()}
        a = self.spawn("append", "j", "--type", "intent", "--content", INTENT % "holder", msg_id=ids["A"],
                       faults="delay-precommit:800")
        wait_until(lambda: os.path.isdir(pdir) and any(n.startswith(f"{head + 1}-") and n.endswith(".json")
                                                      for n in os.listdir(pdir)),
                   10.0, "writer A's committing marker")
        self.assertIsNone(a.poll(), "writer A finished before B could contend")
        b = self.spawn("append", "j", "--type", "blocker", "--content", BLOCKER, msg_id=ids["B"])
        ra, rb = self.collect([a, b], timeout=30.0)
        for r in (ra, rb):
            self.assertEqual(r["code"], 0, r); self.assertEqual(r["res"]["outcome"], "COMMITTED", r)
        self.assertEqual(ra["res"]["seq"], head + 1); self.assertEqual(rb["res"]["seq"], head + 2)
        ops_b = rb["res"]["ops"]
        begin = [o for o in ops_b if o["op"] == "begin_immediate"]
        self.assertEqual(len(begin), 1, ops_b); self.assertGreater(begin[0]["attempts"], 1, "B did not wait on the reservation")
        self.assertEqual([o["seq"] for o in ops_b if o["op"] == "head"], [head + 1])  # B read A's committed head
        rows = self.rows()
        self.assertEqual([(r["type"], r["msg_id"]) for r in rows[-2:]], [("intent", ids["A"]), ("blocker", ids["B"])])
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(conn.execute("SELECT seq, COUNT(*) FROM events WHERE job='j' GROUP BY seq HAVING COUNT(*)>1").fetchall(), [])
        finally:
            conn.close()
        self.assert_chain_recomputed(rows, "j")
        self.assertEqual(markers.list_markers(self.db, "j"), [])
        log(f"{self._testMethodName}-oplog", {"A_faults": ra["res"].get("faults_used"), "B_begin": begin[0]})
        self.ledger_verify_clean_export_recorded("verify")

    # ---------------------------------------------------------------- R4
    def test_R4_constraint_rejection_branch_rolls_back_writes_nothing(self):
        """txn.write_event with a `build` that returns (a) a row keyed at an existing (job, seq) [PRIMARY KEY] and
        (b) a fresh seq whose prev duplicates an existing prev [UNIQUE (job, prev)]. Each raises WfRejected with
        code "rejected", reason "constraint", the sqlite error text carried; oplog shows begin_immediate then
        rollback and never insert/marker_written; no marker file; rows unchanged; chain recomputed and verify clean.
        The CLI cannot reach this branch (build_row always takes head+1), so write_event is the interface used."""
        self.new_job(); self.activate(); self.intent(note="base")
        before = self.rows(); head_row = before[-1]

        def build_dup_key(conn, head_seq, head_self):
            r = dict(head_row); r["msg_id"] = "r4dupkey"; r["prev"] = "f" * 64  # same (job, seq), unused prev
            r["self"] = canon.event_self_hash(r); return r

        def build_dup_prev(conn, head_seq, head_self):
            r = dict(before[-2]); r["seq"] = head_seq + 1; r["msg_id"] = "r4dupprev"  # prev already used by head_row
            r["self"] = canon.event_self_hash(r); return r

        for name, build, needle in (("dup-key", build_dup_key, "events.job, events.seq"),
                                    ("dup-prev", build_dup_prev, "events.job, events.prev")):
            with self.subTest(case=name):
                oplog = OpLog()
                with self.assertRaises(WfRejected) as cm:
                    txn.write_event(self.db, "j", Faults(), oplog, build=build, session="B1")
                exc = cm.exception
                self.assertEqual((exc.code, exc.reason), ("rejected", "constraint"), vars(exc))
                self.assertIn("UNIQUE constraint failed", str(exc)); self.assertIn(needle, str(exc))
                names = [o["op"] for o in oplog.ops]
                self.assertIn("begin_immediate", names); self.assertIn("rollback", names); self.assertIn("close", names)
                for forbidden in ("insert", "marker_written", "commit"):
                    self.assertNotIn(forbidden, names, names)
                self.assertLess(names.index("begin_immediate"), names.index("rollback"))
                self.assertEqual(markers.list_markers(self.db, "j"), [])
                self.assertEqual(self.rows(), before)
        self.assert_chain_recomputed(self.rows(), "j")
        r = self.ledger_verify_clean_export_recorded("verify")
        self.assertTrue(r["clean"], r)

    # ---------------------------------------------------------------- R5
    def test_R5_no_second_write_connection_per_command(self):
        """Instrumented connections (sqlite3 factory installed on wfcore.db's connect call) record every
        BEGIN IMMEDIATE / BEGIN DEFERRED / COMMIT / ROLLBACK with the connection identity. For each mutating command
        run in-process: no BEGIN IMMEDIATE while another connection holds a write reservation; no BEGIN on a
        connection already in a transaction; every reservation ends with COMMIT or ROLLBACK on the same connection
        and the connection is closed; the count of distinct connections that took a write reservation is recorded."""
        events: list[tuple[str, int]] = []
        holders: set[int] = set()
        violations: list[str] = []

        class Tracked(sqlite3.Connection):
            def execute(self, sql, *a, **k):
                s = sql.strip().upper() if isinstance(sql, str) else ""
                if s.startswith("BEGIN"):
                    if self.in_transaction:
                        violations.append(f"BEGIN while in_transaction on {id(self)}: {s}")
                    if s.startswith("BEGIN IMMEDIATE"):
                        if holders - {id(self)}:
                            violations.append(f"second write reservation {id(self)} while {holders} held")
                        r = super().execute(sql, *a, **k)  # may raise BUSY; only a success marks the holder
                        holders.add(id(self)); events.append(("W", id(self))); return r
                    events.append(("R", id(self)))
                elif s.startswith(("COMMIT", "ROLLBACK")):
                    if id(self) in holders:
                        holders.discard(id(self)); events.append(("E", id(self)))
                return super().execute(sql, *a, **k)

            def close(self):
                if id(self) in holders:
                    violations.append(f"connection {id(self)} closed while holding a write reservation")
                events.append(("C", id(self))); return super().close()

        real_connect = dbm.sqlite3.connect
        dbm.sqlite3.connect = lambda *a, **k: real_connect(*a, **{**k, "factory": Tracked})
        try:
            steps = [("job-new", lambda: self.new_job()), ("grant", lambda: self.activate()),
                     ("append", lambda: self.intent(note="r5")),
                     ("delivered", lambda: self.run_wf("append", "j", "--type", "delivered", "--content",
                                                       '{"claims":[{"claim":"c","evidence":["jobs/j/work/a.txt"]}]}')),
                     ("duplicate", lambda: (self.intent(note="r5", msg_id="r5dup"), self.intent(note="r5", msg_id="r5dup"))),
                     ("verify", lambda: self.run_wf("verify", "j"))]
            per_step = {}
            for name, fn in steps:
                events.clear()
                fn()
                writers = {c for k, c in events if k == "W"}
                per_step[name] = {"write_reservations": len([1 for k, _ in events if k == "W"]),
                                  "distinct_writer_connections": len(writers),
                                  "read_snapshots": len([1 for k, _ in events if k == "R"])}
                self.assertEqual(holders, set(), (name, events))
                self.assertEqual(violations, [], name)
                for c in writers:  # every writer connection released and closed
                    self.assertIn(("E", c), events, name); self.assertIn(("C", c), events, name)
            self.assertEqual(per_step["verify"]["write_reservations"], 0)
            self.assertEqual(per_step["append"]["write_reservations"], 1)
            self.assertEqual(per_step["append"]["distinct_writer_connections"], 1)
        finally:
            dbm.sqlite3.connect = real_connect
        log(f"{self._testMethodName}-reservations", per_step)
        self.assertTrue(commands.verify(self.db, "j")["clean"])


class T28Journal(_Procs):
    """Issue #14 items 2 and 4. Precondition reused from test_b1_gaps T28: the kill-before-commit seam leaves a real
    rollback journal (`<db>-journal`, non-empty) on disk before any wf open."""

    def kill_before_commit(self) -> dict:
        self.new_job(); self.activate(); self.intent(note="before")
        journal = self.db + "-journal"
        self.assertFalse(os.path.exists(journal))
        p = self.spawn("append", "j", "--type", "intent", "--content", INTENT % "killed", faults="kill-before-commit",
                       oplog=False)
        res = self.collect([p], timeout=30.0)[0]
        self.assertEqual(res["code"], 3, res)
        self.assertTrue(os.path.exists(journal), "no hot journal after the kill: evidence gap, not a product finding")
        st = os.stat(journal)
        self.assertGreater(st.st_size, 0)
        return {"path": journal, "ino": st.st_ino, "size": st.st_size, "head": commands_head_before(self.db)}

    def test_R6_wf_never_deletes_or_moves_the_hot_journal(self):
        """Instrumented os.remove/os.unlink/os.rename/os.replace (the only removal/rename entry points the product
        uses: markers.delete -> os.remove, markers/export -> os.replace, export -> os.unlink). Runs status, verify,
        digest, one append (refused by the committing-marker guard, code uncertain-write-pending, §4.4a: nothing written), `wf resolve`
        (NOT-COMMITTED) and one append (COMMITTED at head+1), in-process, starting with the hot journal present. Asserts: no recorded call names the
        journal path (or any `*-journal` path under the ledger dir); every recorded call targets a marker or export
        file; the ledger database path itself is never removed or renamed; after the sequence the chain is the
        prior valid state (rolled back, no partial row) and the append committed at head+1. Observation limit: a
        journal that disappears without a recorded call was removed by SQLite itself (hot-journal rollback on open,
        DELETE journal mode, inside the engine), which the spec allows and this hook cannot see."""
        j = self.kill_before_commit()
        calls: list[tuple[str, str, str | None]] = []
        real = {n: getattr(os, n) for n in ("remove", "unlink", "rename", "replace")}

        def hook(name):
            def f(src, *a, **k):
                dst = a[0] if a and isinstance(a[0], (str, bytes, os.PathLike)) else None
                calls.append((name, os.fspath(src), os.fspath(dst) if dst is not None else None))
                return real[name](src, *a, **k)
            return f

        timeline = []
        for n in real:
            setattr(os, n, hook(n))
        try:
            ms = markers.list_markers(self.db, "j"); self.assertEqual(len(ms), 1)
            steps = (("status", lambda: commands.status(self.db, "j")), ("verify", lambda: commands.verify(self.db, "j")),
                     ("digest", lambda: commands.digest(self.db, "j")),
                     ("append-refused-by-marker-guard", lambda: self.intent(note="while-marker-pending")),
                     ("resolve", lambda: self.run_wf("resolve", "j", str(ms[0].seq), "--attempt", ms[0].attempt_id)),
                     ("append", lambda: self.intent(note="after-kill")))
            outcomes = {}
            for name, fn in steps:
                before = os.path.exists(j["path"]); n0 = len(calls)
                outcomes[name] = fn()
                timeline.append({"cmd": name, "journal_before": before, "journal_after": os.path.exists(j["path"]),
                                 "calls_during": calls[n0:]})
            c, r = outcomes["append-refused-by-marker-guard"]
            self.assertNotEqual(c, 0, r); self.assertEqual(r.get("code"), "uncertain-write-pending", r)  # §4.4a guard, no row
            c, r = outcomes["resolve"]; self.assertEqual((c, r["outcome"]), (0, "NOT-COMMITTED"), r)
            c, r = outcomes["append"]; self.assertEqual((c, r["outcome"]), (0, "COMMITTED"), r)
        finally:
            for n, f in real.items():
                setattr(os, n, f)
        ledger_dir = os.path.dirname(self.db)
        for name, src, dst in calls:
            for pth in (src, dst):
                if pth is None:
                    continue
                self.assertNotEqual(os.path.abspath(pth), os.path.abspath(j["path"]), (name, src, dst))
                self.assertFalse(pth.endswith("-journal"), (name, src, dst))
                self.assertNotEqual(os.path.abspath(pth), os.path.abspath(self.db), (name, src, dst))
                if paths.is_under(os.path.abspath(pth), ledger_dir):
                    self.assertIn(os.sep + "pending" + os.sep, os.path.abspath(pth), (name, src, dst))  # marker files only
        rows = self.rows()
        self.assertEqual(rows[-1]["seq"], j["head"] + 1)  # killed attempt rolled back, the later append took head+1
        self.assertEqual([r["content"] for r in rows if "killed" in r["content"]], [])
        self.assert_chain_recomputed(rows, "j")
        gone_without_call = [t["cmd"] for t in timeline if t["journal_before"] and not t["journal_after"] and
                             not any(c[1].endswith("-journal") for c in t["calls_during"])]
        log(f"{self._testMethodName}-timeline", {"journal": j, "timeline": timeline, "platform": platform.platform(),
                                                 "removed_by_engine_during": gone_without_call,
                                                 "limit": "a journal removed without a recorded Python call was removed "
                                                          "by SQLite (hot-journal rollback), not by wf; fault-seam "
                                                          "evidence (D-018), not OS-kill or power-loss qualification"})
        self.assertTrue(commands.verify(self.db, "j")["clean"])

    def test_R7_D018_journal_presence_after_next_open_observation(self):
        """D-018 retained observation, pinned as observation (not a requirement): after kill-before-commit the hot
        journal is present before any open (asserted precondition); the next open is `wf verify`; whether the
        journal is still present afterwards is recorded with this exact wording:
        "journal present after the next open: <True|False> (<platform>); when False the engine removed it on
        hot-journal rollback, wf issued no removal". The head is unchanged by the open and no partial row exists."""
        j = self.kill_before_commit()
        calls = []
        real_remove, real_unlink = os.remove, os.unlink
        os.remove = lambda p, *a, **k: (calls.append(os.fspath(p)), real_remove(p, *a, **k))[1]
        os.unlink = lambda p, *a, **k: (calls.append(os.fspath(p)), real_unlink(p, *a, **k))[1]
        try:
            r = commands.verify(self.db, "j")  # the next open
        finally:
            os.remove, os.unlink = real_remove, real_unlink
        present = os.path.exists(j["path"])
        same_file = present and os.stat(j["path"]).st_ino == j["ino"]
        wording = (f"journal present after the next open: {present} ({platform.system()} {platform.release()}, "
                   f"sqlite {sqlite3.sqlite_version}); "
                   + ("the file identity is unchanged (wf did not replace it)" if same_file else
                      "when False the engine removed it on hot-journal rollback, wf issued no removal"))
        self.assertEqual([c for c in calls if c.endswith("-journal")], [])
        self.assertEqual(r["jobs"]["j"]["head"]["seq"], j["head"]); self.assertEqual(r["jobs"]["j"]["problems"], [])
        self.assertEqual(len(self.rows()), j["head"])
        self.assertIsInstance(present, bool)
        log(f"{self._testMethodName}-observation", {"wording": wording, "present": present, "same_inode": same_file,
                                                    "removal_calls": calls, "D-018": "observation only; not pass/fail"})


def commands_head_before(db: str) -> int:
    return commands.status(db, "j")["head"]["seq"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
