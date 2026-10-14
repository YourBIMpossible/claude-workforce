"""RE2 (owner approval 2026-10-12): tests-only closure of spec section 13 B1 missing-test sub-items that an
existing mechanism can prove unambiguously. Spec rows are quoted from FIRST-RELEASE-SPEC.md by line number
(copies in fixtures/results/re1-d93ae4e89a74/spec-b1-rows.md). Sub-items that need an owner interpretation
(T04 "kept as evidence", T09 usage-error vs rejected(malformed) wording, T27/T31 "blocker" wording, T28 seams vs
real kill, T36 "internally consistent only" wording) are recorded as observed behaviour only; nothing here treats
an observation as proof that such a requirement passes. No wfcore/wf.py change is made or needed.

Runs inside the test_wf.py harness (Base: fresh scratch dir per test under WF_TEST_TMP, logs under
WF_TEST_LOGDIR). Fresh wf processes are started with python -I so each has its own connection and fault env."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import unittest

from test_wf import Base, LOGDIR, WF_DIR, log  # noqa: E402  (sets sys.path to the wf package)
from wfcore import canon, commands, db as dbm, export, markers  # noqa: E402

INTENT = '{"path":"jobs/j/work/a.txt","sha_before":"","note":"%s"}'
BLOCKER = '{"reason":"race test"}'


class B1Gaps(Base):
    def spawn(self, *argv: str, role: str = "builder", session: str = "B1", msg_id: str | None = None,
              faults: str | None = None, job_db: str | None = None) -> subprocess.Popen:
        """Start (do not wait for) a fresh wf process; the caller collects it with `collect`."""
        env = dict(os.environ)
        env.pop("WF_TEST_FAULTS", None)
        if faults:
            env["WF_TEST_FAULTS"] = faults
        cmd = [sys.executable, "-I", os.path.join(WF_DIR, "wf.py"), "--db", job_db or self.db, "--role", role,
               "--session", session, "--json"]
        if msg_id:
            cmd += ["--msg-id", msg_id]
        cmd += list(argv)
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

    def collect(self, procs: list[subprocess.Popen], tag: str, timeout: float = 30.0) -> list[dict]:
        """Bounded wait for every worker; every exit code is captured and each process is verified exited."""
        out = []
        deadline = time.monotonic() + timeout
        for i, p in enumerate(procs):
            left = max(0.1, deadline - time.monotonic())
            try:
                so, se = p.communicate(timeout=left)
            except subprocess.TimeoutExpired:
                p.kill(); so, se = p.communicate()
                self.fail(f"worker {i} did not exit within {timeout}s")
            try:
                res = json.loads(so) if so.strip() else {}
            except ValueError:
                res = {"raw": so}
            out.append({"i": i, "code": p.returncode, "res": res, "stderr": se[-2000:]})
        for i, p in enumerate(procs):
            self.assertIsNotNone(p.poll(), f"worker {i} still running")
        log(f"{self._testMethodName}-{tag}", out)
        return out

    def chain_rows(self, job: str = "j") -> list[dict]:
        conn = dbm.connect(self.db, create=False)
        try:
            return dbm.rows(conn, job)
        finally:
            conn.close()

    def raw(self) -> sqlite3.Connection:
        """Direct tampering connection (triggers dropped by the caller as the spec rows require)."""
        return sqlite3.connect(self.db, isolation_level=None)

    def verify(self, job: str = "j") -> dict:
        c, r = self.run_wf("verify", job)
        return r

    def assert_chain_valid_export_observed(self, workers: list[dict], job: str = "j") -> None:
        """Required outcome: chain valid (no chain problem, no schema/integrity problem). Export freshness is a
        separate, already-asserted mechanism (test_T31b: a failed export refresh keeps the commit and is reported;
        the next write repairs it). Concurrent writers may race on the export files, so this records each worker's
        export result and, if any refresh failed, requires verify to report only export-freshness problems (or none)
        and the next write to repair them; otherwise verify must be clean outright."""
        r = self.verify(job)
        self.assertEqual(r["jobs"][job]["problems"], [], r)
        self.assertEqual(r["ledger_problems"], {"schema": [], "integrity": []}, r)
        self.assertEqual(r["jobs"][job]["pending_markers"], [], r)
        export_errors = [w["res"]["export"].get("error") for w in workers
                         if w["code"] == 0 and isinstance(w["res"].get("export"), dict) and "error" in w["res"]["export"]]
        log(f"{self._testMethodName}-export", {"export_errors": export_errors, "verify_export": r["jobs"][job]["export"],
                                                "clean": r["clean"]})
        if not export_errors:
            self.assertTrue(r["clean"], r)
            return
        # A refresh that lost the os.replace race is reported by the worker (T31b mechanism). The final export state
        # depends on which refresh landed last: clean (a later worker rewrote it), stale, or manifest mismatch. Any
        # problem reported must be one of export.check's export problems (never a chain problem), and the next write
        # must repair it. Observed state is logged above for the owner.
        allowed = ("export events.jsonl is stale relative to the ledger", "export file differs from manifest: ",
                   "export file missing: ", "export manifest missing: ")
        for prob in r["jobs"][job]["export"]:
            self.assertTrue(prob.startswith(allowed), prob)
        self.assertEqual(r["clean"], r["jobs"][job]["export"] == [], r)
        c, rr = self.intent(job, note="repair")  # the next write refreshes the export (T31b mechanism)
        self.assertEqual(c, 0, rr)
        self.assertTrue(self.verify(job)["clean"])

    # ---------------------------------------------------------------- T09 (spec line 284)
    def test_T09_missing_role_or_session_exact_result_and_storage_rule(self):
        """Spec T09 (FIRST-RELEASE-SPEC.md line 284): "Entry missing author or session fields" -> Expected
        `rejected(malformed)`. Proves (existing mechanisms): (1) a wf append without --role and one without --session
        exit non-zero with exit code 2 (usage), write no ledger row (head unchanged) and return a machine-readable
        code and error text, both recorded in the log for the owner; (2) the storage rule: an event row with NULL
        author_role or NULL session_id is refused by the schema (NOT NULL -> sqlite3.IntegrityError).
        NOT proved (owner decision): whether code "usage" / text "--role is required for this command" satisfies the
        spec wording `rejected(malformed)`. This test records the wording; it does not claim the wording passes."""
        import wf
        self.new_job(); self.activate()
        head_before = commands.status(self.db, "j")["head"]["seq"]
        content = INTENT % "t09"
        observed = []
        for missing, base in (("session", ["--db", self.db, "--role", "builder"]),
                              ("role", ["--db", self.db, "--session", "B1"])):
            saved = {k: os.environ.pop(k, None) for k in ("WF_ROLE", "WF_SESSION")}
            try:
                code, res = wf.run(base + ["append", "j", "--type", "intent", "--content", content])
            finally:
                os.environ.update({k: v for k, v in saved.items() if v is not None})
            observed.append({"missing": missing, "code": code, "res": res})
            self.assertEqual(code, wf.EXIT_USAGE, res)
            self.assertFalse(res.get("ok", True), res)
            self.assertEqual(res.get("code"), "usage", res)
            self.assertIn(f"--{missing} is required", res.get("error", ""), res)
        log(f"{self._testMethodName}-observed", observed)
        self.assertEqual(commands.status(self.db, "j")["head"]["seq"], head_before)  # no row written
        rows = self.chain_rows()
        tmpl = dict(rows[-1]); tmpl["seq"] = head_before + 1; tmpl["prev"] = rows[-1]["self"]
        conn = self.raw()
        try:
            for col in ("author_role", "session_id"):
                r = dict(tmpl); r[col] = None
                with self.assertRaises(sqlite3.IntegrityError, msg=col):
                    conn.execute(
                        "INSERT INTO events (job,seq,type,msg_id,author_role,session_id,generation,reply_to,grant_ref,ts,"
                        "content,content_sha256,prev,self) VALUES (:job,:seq,:type,:msg_id,:author_role,:session_id,"
                        ":generation,:reply_to,:grant_ref,:ts,:content,:content_sha256,:prev,:self)", r)
        finally:
            conn.close()
        self.assertEqual(commands.status(self.db, "j")["head"]["seq"], head_before)
        self.assertTrue(self.verify()["clean"])

    # ---------------------------------------------------------------- T23 (spec line 298)
    def test_T23_reordered_rows_detected(self):
        """Spec T23 (line 298): "Event row ... reordered; triggers dropped then rows edited" -> `wf verify` fails
        (chain). Proves: after dropping the update trigger and swapping the seq of two mid-chain rows (via a temporary
        seq), verify reports chain problems for the job, reports the missing trigger, and is not clean. Edited and
        deleted-mid-chain rows are already asserted by test_T10_verify_detects_tamper and
        test_spec_T23_delete_mid_chain_detected in test_wf.py (not duplicated here)."""
        self.new_job(); self.activate(); self.intent(note="a"); self.intent(note="b")
        rows = self.chain_rows(); self.assertEqual([r["seq"] for r in rows], [1, 2, 3, 4, 5])
        conn = self.raw()
        try:
            conn.execute("DROP TRIGGER events_no_update")
            conn.execute("UPDATE events SET seq = 1000 WHERE job='j' AND seq = 4")
            conn.execute("UPDATE events SET seq = 4 WHERE job='j' AND seq = 5")
            conn.execute("UPDATE events SET seq = 5 WHERE job='j' AND seq = 1000")
        finally:
            conn.close()
        after = self.chain_rows()
        self.assertEqual([r["msg_id"] for r in after], [rows[0]["msg_id"], rows[1]["msg_id"], rows[2]["msg_id"],
                                                        rows[4]["msg_id"], rows[3]["msg_id"]])  # rows really swapped
        r = self.verify()
        self.assertFalse(r["clean"], r)
        probs = r["jobs"]["j"]["problems"]
        self.assertTrue(probs, r)
        self.assertTrue(any("prev" in p or "self" in p for p in probs), probs)
        self.assertIn("trigger missing: events_no_update", r["ledger_problems"]["schema"])

    # ---------------------------------------------------------------- T27 (spec line 302)
    def test_T27_n_simultaneous_appends_one_job(self):
        """Spec T27 (line 302): "N simultaneous `wf append` calls on one job" -> "Distinct consecutive `seq`, valid
        `prev` chain, no lost or duplicated event ...; writers serialize through BEGIN IMMEDIATE; a writer that
        cannot get the reservation within the busy timeout fails visibly". Proves, with N=4 fresh processes started
        together: every worker exits within the bound; each worker either committed (exit 0, seq returned) or failed
        visibly with code lock-timeout (spec-permitted); at least two committed; committed seqs are exactly
        head+1..head+k with no gap and no duplicate; each committed worker's msg_id appears exactly once in the chain
        and no other row was added; verify reports no chain/schema/integrity problem (valid prev chain); export
        freshness is recorded separately (concurrent export refreshes may race; see assert_chain_valid_export_observed).
        NOT proved: "fails visibly with a blocker"
        as a blocker row (owner wording decision), gate-log writes (no gate in this slice), "no function opens a
        second write connection" (no observation seam)."""
        self.new_job(); self.activate()
        head = commands.status(self.db, "j")["head"]["seq"]
        n = 4
        ids = [f"t27w{i}{os.urandom(4).hex()}" for i in range(n)]
        procs = [self.spawn("append", "j", "--type", "intent", "--content", INTENT % f"w{i}", msg_id=ids[i])
                 for i in range(n)]
        res = self.collect(procs, "workers", timeout=40.0)
        committed, timed_out = {}, []
        for w in res:
            if w["code"] == 0:
                self.assertTrue(w["res"].get("ok"), w)
                committed[ids[w["i"]]] = w["res"]["seq"]
            else:
                self.assertEqual(w["res"].get("code"), "lock-timeout", w)  # the only permitted visible failure
                timed_out.append(w["i"])
        log(f"{self._testMethodName}-summary", {"committed": committed, "lock_timeout_workers": timed_out})
        self.assertGreaterEqual(len(committed), 2, res)
        k = len(committed)
        self.assertEqual(sorted(committed.values()), list(range(head + 1, head + k + 1)))
        rows = self.chain_rows()
        self.assertEqual(len(rows), head + k)  # nothing lost, nothing extra
        new_rows = {r["msg_id"]: r["seq"] for r in rows if r["seq"] > head}
        self.assertEqual(new_rows, committed)  # every committed worker present exactly once at its seq
        for i in timed_out:
            self.assertNotIn(ids[i], new_rows)  # a visible failure wrote nothing
        self.assert_chain_valid_export_observed(res)

    def test_T27_simultaneous_mutations_two_jobs(self):
        """Spec T27 (line 302): "simultaneous mutations on two jobs" -> distinct consecutive seq, valid prev chain,
        no lost or duplicated event. Proves: two jobs in one ledger, one fresh process appending to each at the same
        time; both exit within the bound; each committed (or failed visibly with lock-timeout, spec-permitted; at
        least one committed); each job's head advanced by exactly its committed count; the new row of each job
        carries that worker's msg_id; verify (whole ledger) is clean."""
        self.new_job("j"); self.activate("j"); self.new_job("k"); self.activate("k")
        heads = {j: commands.status(self.db, j)["head"]["seq"] for j in ("j", "k")}
        ids = {"j": "t27j" + os.urandom(4).hex(), "k": "t27k" + os.urandom(4).hex()}
        procs = [self.spawn("append", j, "--type", "intent", "--content", INTENT % j, msg_id=ids[j]) for j in ("j", "k")]
        res = self.collect(procs, "workers", timeout=40.0)
        done = 0
        for j, w in zip(("j", "k"), res):
            rows = self.chain_rows(j)
            if w["code"] == 0:
                done += 1
                self.assertEqual(w["res"]["seq"], heads[j] + 1, w)
                self.assertEqual(len(rows), heads[j] + 1)
                self.assertEqual(rows[-1]["msg_id"], ids[j])
            else:
                self.assertEqual(w["res"].get("code"), "lock-timeout", w)
                self.assertEqual(len(rows), heads[j])
        self.assertGreaterEqual(done, 1, res)
        r = commands.verify(self.db, None)
        self.assertTrue(r["clean"], r)
        self.assertEqual(sorted(r["jobs"]), ["j", "k"])

    # ---------------------------------------------------------------- T28 (spec line 303)
    def test_T28_hot_journal_at_next_open_rolls_back(self):
        """Spec T28 (line 303): "hot journal present at next open" -> "(a),(b) next open rolls back to a valid prior
        state, no partial event". Proves: a fresh wf process killed (fault kill-before-commit: os._exit after the
        INSERT and the committing marker, before COMMIT; journal_mode DELETE, txn.py) leaves a real rollback journal
        `l.db-journal` on disk (asserted non-empty BEFORE any wf open; nothing is written by the test itself); the
        next open (`wf verify`) sees head unchanged, no partial row, no chain problem, the committing marker still
        pending (so not clean); `wf resolve` returns NOT-COMMITTED and verify is then clean. Observations recorded,
        not asserted: whether the journal file still exists after the open and its size, and whether the main db
        file bytes were changed by the killed process (if not, the journal had nothing to undo and the "rollback" is
        not observable from outside); "wf never deletes or moves a journal file" is not provable from outside. NOT proved (owner
        decision): whether the os._exit fault seam satisfies "process killed"; `wf export` regenerates
        (no export subcommand: missing-implementation)."""
        self.new_job(); self.activate(); self.intent(note="before")
        head_before = commands.status(self.db, "j")["head"]["seq"]
        rows_before = self.chain_rows()
        journal = self.db + "-journal"
        self.assertFalse(os.path.exists(journal))
        db_sha_before = canon.sha256_file(self.db)
        p = self.spawn("append", "j", "--type", "intent", "--content", INTENT % "killed", faults="kill-before-commit")
        res = self.collect([p], "killed", timeout=30.0)
        self.assertEqual(res[0]["code"], 3, res)
        hot = os.path.exists(journal)
        size = os.path.getsize(journal) if hot else 0
        log(f"{self._testMethodName}-journal", {"path": journal, "present_after_kill": hot, "size": size,
                                                 "ledger_dir": sorted(os.listdir(os.path.dirname(self.db))),
                                                 "db_file_changed_by_killed_process": canon.sha256_file(self.db) != db_sha_before})
        self.assertTrue(hot, "no rollback journal on disk after the kill: evidence gap, not a product finding")
        self.assertGreater(size, 0)
        ms = markers.list_markers(self.db, "j"); self.assertEqual(len(ms), 1)
        r = self.verify()  # the next open
        self.assertEqual(r["jobs"]["j"]["head"]["seq"], head_before)
        self.assertEqual(r["jobs"]["j"]["problems"], [])
        self.assertEqual(r["ledger_problems"], {"schema": [], "integrity": []})
        self.assertTrue(r["jobs"]["j"]["pending_markers"], r)
        self.assertFalse(r["clean"])
        self.assertEqual(self.chain_rows(), rows_before)  # no partial event
        log(f"{self._testMethodName}-after-open", {"journal_present_after_open": os.path.exists(journal),
                                                    "journal_size_after_open": os.path.getsize(journal) if os.path.exists(journal) else None,
                                                    "db_file_changed_since_before_kill": canon.sha256_file(self.db) != db_sha_before})
        c, rr = self.run_wf("resolve", "j", str(ms[0].seq), "--attempt", ms[0].attempt_id)
        self.assertEqual((c, rr["outcome"]), (0, "NOT-COMMITTED"), rr)
        self.assertTrue(self.verify()["clean"])

    # ---------------------------------------------------------------- T31 (spec line 306)
    def test_T31_different_types_race_for_same_seq(self):
        """Spec T31 (line 306): "two commands with different event types ... simultaneously target the same next
        sequence" -> "Exactly one row at that (job, seq); the loser either takes seq+1 after reading the new head or
        fails with a constraint error, rolls back ...; chain unchanged and valid". Proves: an intent and a blocker
        from two fresh processes started together; both exit within the bound; each either committed at a distinct
        seq or failed visibly (code lock-timeout or constraint, writing nothing); at least one committed; exactly one
        row per (job, seq) (PRIMARY KEY query); if both committed their seqs are head+1 and head+2 (loser took
        seq+1); every committed worker's msg_id is present exactly once; verify reports no chain/schema/integrity
        problem (export freshness recorded separately, see assert_chain_valid_export_observed). NOT proved: "raises a
        blocker" as a blocker row for a constraint-error loser (owner wording decision); "no reliance on filename
        uniqueness" (no observation seam). Direct insert at an existing (job, seq) is already asserted by
        test_spec_T31_insert_at_existing_seq_refused_by_schema (not duplicated)."""
        self.new_job(); self.activate()
        head = commands.status(self.db, "j")["head"]["seq"]
        ids = {"intent": "t31i" + os.urandom(4).hex(), "blocker": "t31b" + os.urandom(4).hex()}
        procs = [self.spawn("append", "j", "--type", "intent", "--content", INTENT % "race", msg_id=ids["intent"]),
                 self.spawn("append", "j", "--type", "blocker", "--content", BLOCKER, msg_id=ids["blocker"])]
        res = self.collect(procs, "workers", timeout=40.0)
        committed = {}
        for t, w in zip(("intent", "blocker"), res):
            if w["code"] == 0:
                committed[ids[t]] = (t, w["res"]["seq"])
            else:
                self.assertIn(w["res"].get("code"), ("lock-timeout", "rejected"), w)
                if w["res"].get("code") == "rejected":
                    self.assertIn("constraint", w["res"].get("error", ""), w)
        log(f"{self._testMethodName}-summary", {"committed": committed})
        self.assertGreaterEqual(len(committed), 1, res)
        self.assertEqual(sorted(s for _, s in committed.values()), list(range(head + 1, head + len(committed) + 1)))
        conn = self.raw()
        try:
            dup = conn.execute("SELECT seq, COUNT(*) FROM events WHERE job='j' GROUP BY seq HAVING COUNT(*) > 1").fetchall()
        finally:
            conn.close()
        self.assertEqual(dup, [])  # exactly one row per (job, seq)
        rows = self.chain_rows()
        self.assertEqual(len(rows), head + len(committed))
        new_rows = {r["msg_id"]: (r["type"], r["seq"]) for r in rows if r["seq"] > head}
        self.assertEqual(new_rows, committed)
        self.assert_chain_valid_export_observed(res)

    # ---------------------------------------------------------------- T36 (spec line 311)
    def _rewrite_from(self, seq: int, new_note: str) -> None:
        """Consistent rewrite: change row `seq`'s content, recompute its content_sha256 and self, then re-link every
        later row (prev, self). Triggers dropped first, as T36 (b) requires. Export refreshed afterwards so that
        only the chain is under test (export staleness is T28/T31b territory, already asserted)."""
        rows = self.chain_rows()
        conn = self.raw()
        try:
            conn.execute("DROP TRIGGER events_no_update")
            prev = rows[seq - 2]["self"] if seq > 1 else canon.GENESIS
            for r in rows[seq - 1:]:
                r = dict(r)
                if r["seq"] == seq:  # keep the stored shape (contract pin, canonical JSON); change one field
                    body = json.loads(r["content"]); body["note"] = new_note
                    r["content"] = canon.canonical(body)
                    r["content_sha256"] = canon.sha256_text(r["content"])
                r["prev"] = prev
                r["self"] = canon.event_self_hash(r)
                conn.execute("UPDATE events SET content=:content, content_sha256=:content_sha256, prev=:prev, self=:self"
                             " WHERE job=:job AND seq=:seq", r)
                prev = r["self"]
        finally:
            conn.close()
        ref = export.refresh(self.db, "j"); self.assertNotIn("error", ref, ref)

    def test_T36b_consistent_whole_chain_rewrite_passes_verify_without_checkpoint(self):
        """Spec T36 (line 311) (b): "rewrite the whole chain consistently (recompute all hashes)" -> "(b) ... pass
        `wf verify` without a checkpoint and the output says the chain is internally consistent only". Proves: after
        changing a mid-chain row's content and recomputing content_sha256/self/prev for it and every later row (direct
        SQL, triggers dropped; schema problems are then expected and are the only problems), verify reports NO chain
        problem for the job (the rewrite is not detected by level-1 verify, as the spec says), and the output's
        `ladder` statement is recorded verbatim and contains no authenticity claim. This is internal-consistency
        behaviour only: it is NOT evidence that comparison with an independently retained checkpoint (T36 (d)/(e))
        exists; `commands.verify(db, job)` takes no checkpoint input (missing-implementation). NOT proved (owner
        decision): whether the ladder wording "level-1 record-and-verify ...; not an authenticity claim" satisfies
        "says the chain is internally consistent only"."""
        self.new_job(); self.activate(); self.intent(note="orig"); self.intent(note="tail")
        before = self.chain_rows()
        self._rewrite_from(4, "REWRITTEN")
        after = self.chain_rows()
        self.assertNotEqual(before[3]["content"], after[3]["content"])
        self.assertNotEqual([r["self"] for r in before[3:]], [r["self"] for r in after[3:]])  # hashes really changed
        r = self.verify()
        log(f"{self._testMethodName}-ladder", {"ladder": r["ladder"], "problems": r["jobs"]["j"]["problems"],
                                               "schema": r["ledger_problems"]["schema"]})
        self.assertEqual(r["jobs"]["j"]["problems"], [])  # (b) passes level-1 chain verification
        self.assertEqual(r["jobs"]["j"]["export"], [])
        self.assertEqual(r["jobs"]["j"]["head"], {"seq": 5, "self": after[-1]["self"]})
        self.assertEqual(r["ledger_problems"]["schema"], ["trigger missing: events_no_update"])  # the only problem
        self.assertIn("level-1", r["ladder"]); self.assertIn("not an authenticity claim", r["ladder"])
        self._assert_no_banned_words(r)

    def test_T36c_tail_deletion_passes_verify_without_checkpoint(self):
        """Spec T36 (line 311) (c): "delete the tail" -> "(c) pass `wf verify` without a checkpoint and the output
        says the chain is internally consistent only". Proves: after dropping the delete trigger and deleting the
        head row (export refreshed so only the chain is under test), verify reports NO chain problem, head is the
        previous row, the ladder statement is recorded; the missing trigger is the only reported problem. Internal
        consistency only; not evidence of checkpoint comparison (see T36b docstring). Wording: owner decision."""
        self.new_job(); self.activate(); self.intent(note="a"); self.intent(note="b")
        before = self.chain_rows(); self.assertEqual(before[-1]["seq"], 5)
        conn = self.raw()
        try:
            conn.execute("DROP TRIGGER events_no_delete")
            conn.execute("DELETE FROM events WHERE job='j' AND seq=5")
        finally:
            conn.close()
        ref = export.refresh(self.db, "j"); self.assertNotIn("error", ref, ref)
        r = self.verify()
        log(f"{self._testMethodName}-ladder", {"ladder": r["ladder"], "problems": r["jobs"]["j"]["problems"]})
        self.assertEqual(r["jobs"]["j"]["problems"], [])
        self.assertEqual(r["jobs"]["j"]["export"], [])
        self.assertEqual(r["jobs"]["j"]["head"], {"seq": 4, "self": before[3]["self"]})
        self.assertEqual(r["ledger_problems"]["schema"], ["trigger missing: events_no_delete"])
        self._assert_no_banned_words(r)

    def test_T36_verify_output_never_claims_authentic_or_tamper_proof(self):
        """Spec T36 (line 311): "no output ever uses 'authentic' or 'tamper-proof'". Proves, for verify output on a
        clean ledger, on a tampered ledger (bit flip, T10 pattern) and for status/digest/checkpoint output: the
        serialized JSON contains neither the standalone word "authentic" nor "tamper-proof" (case-insensitive,
        word-boundary match). Recorded, NOT asserted either way (owner decision): the ladder text contains the word
        "authenticity" inside "not an authenticity claim"; whether that inflected form falls under the spec's word
        ban is for the owner."""
        self.new_job(); self.activate(); self.intent(note="n")
        outputs = {"verify_clean": self.verify(), "status": commands.status(self.db, "j"),
                   "digest": commands.digest(self.db, "j"), "checkpoint": commands.checkpoint(self.db, "j")}
        conn = self.raw()
        try:
            conn.execute("DROP TRIGGER events_no_update")
            conn.execute("UPDATE events SET content = replace(content, 'n', 'N') WHERE seq = 4")
        finally:
            conn.close()
        outputs["verify_tampered"] = self.verify()
        self.assertFalse(outputs["verify_tampered"]["clean"])
        text = json.dumps(outputs)
        log(f"{self._testMethodName}-words", {"authenticity_occurrences": len(re.findall(r"authenticity", text)),
                                              "ladder": outputs["verify_clean"]["ladder"]})
        for name, out in outputs.items():
            self._assert_no_banned_words(out, name)

    def _assert_no_banned_words(self, out: dict, name: str = "verify") -> None:
        text = json.dumps(out)
        self.assertIsNone(re.search(r"\bauthentic\b", text, re.IGNORECASE), name)
        self.assertIsNone(re.search(r"tamper[- ]?proof", text, re.IGNORECASE), name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
