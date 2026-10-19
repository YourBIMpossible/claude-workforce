"""WF-034 (Lazy): the WF-011 "later batch" hardening of T30/T37, as new, stricter tests. The existing cases in
test_t30_race_secondwriter_identity.py are unchanged; these re-express them more strictly (base fix1/lazy-ids 5c47587).

1. T30(f) second writer: `ok` is False and the exact refusal code, label and text, not only a non-zero exit.
2. T30(a) race: on TimeoutExpired both children are killed, and no child outlives the test.
3. T30(a) race with a start barrier: both children block on a barrier file before importing wf, so the two
   `recover begin` calls really overlap; each round records whether the two execution windows overlapped.
4. `wf verify --restored`: the documented "not detectable" limitation text for a whole-chain rewrite without a
   checkpoint (RESTORE-RUNBOOK.md, backup.LIMIT_NO_CHECKPOINT).
5. D-S5-1 (INTERFACE-CONTRACT-S5 §7, "No identity is stored in events"): no event content carries a ledger, machine,
   user or process identity, and session ids appear only under the content keys the schemas define.

Fixture ledgers only (WF_TEST_TMP under fixtures\\tmp); nothing here deletes test artifacts."""
from __future__ import annotations

import getpass
import json
import os
import re
import socket
import subprocess
import sys
import time
import unittest

import test_gate_decide as G
from test_t30_race_secondwriter_identity import FAULT, T30Base
from test_wf import DECLARATIONS, WF_DIR, log

from wfcore import backup, faults, gatecore

WF_PY = os.path.join(WF_DIR, "wf.py")
RACE_ROUNDS = 8
CHILD_TIMEOUT_S = 120

# A child that blocks on a barrier file BEFORE wf is imported, records its own run window, then runs wf.py as
# __main__ (wf.py puts its own directory on sys.path). argv: barrier window_file wf.py <wf args...>
LAUNCHER = (
    "import os, sys, time, runpy\n"
    "barrier, window = sys.argv[1], sys.argv[2]\n"
    "while not os.path.exists(barrier):\n"
    "    time.sleep(0.0005)\n"
    "start = time.time()\n"
    "sys.argv = sys.argv[3:]\n"
    "try:\n"
    "    runpy.run_path(sys.argv[0], run_name='__main__')\n"
    "except SystemExit as exc:\n"
    "    code = exc.code\n"
    "else:\n"
    "    code = 0\n"
    "with open(window, 'w') as fh:\n"
    "    fh.write(f'{start} {time.time()}')\n"
    "sys.exit(code)\n"
)


def _kill_all(procs: list[subprocess.Popen]) -> None:
    for p in procs:
        if p.poll() is None:
            p.kill()
    for p in procs:
        try:
            p.wait(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - a child that survives kill is reported below
            pass


# ----------------------------------------------------------------------------------------------- item 1
@unittest.skipUnless(FAULT in faults.KNOWN, "fault point not present on this tree")
class T30fSecondWriterStrict(T30Base):
    def test_refusal_is_ok_false_with_the_exact_code_label_and_text(self):
        self.job_on(self.db)
        before = self.job_rows()
        p = subprocess.run(self.recover_argv("j"), capture_output=True, timeout=CHILD_TIMEOUT_S, cwd=self.dir,
                           env=self.env(**{faults.ENV: FAULT}))
        res = json.loads(p.stdout.decode("utf-8"))
        self.assertEqual(p.returncode, 1, res)
        self.assertIs(res["ok"], False)
        self.assertEqual(res["code"], "refused")
        self.assertEqual(res["second_writer"], "refused")
        self.assertEqual(res["fault"], FAULT)
        self.assertRegex(res["error"], r"^second-writer: a second BEGIN IMMEDIATE was refused busy/locked \(database "
                                       r"is locked\) while this command held the reservation; recovery not opened "
                                       r"\(nothing appended\)$")
        self.assertEqual(p.stderr.decode("utf-8", "replace").strip(), "", "a refusal is a result, not a traceback")
        self.assertEqual([(r["seq"], r["self"]) for r in self.job_rows()], [(r["seq"], r["self"]) for r in before])


# ----------------------------------------------------------------------------------------------- items 2 and 3
class T30aRaceStrict(T30Base):
    def _round(self, job: str, n: int) -> dict:
        barrier = os.path.join(self.dir, f"barrier-{n}")
        windows = [os.path.join(self.dir, f"window-{n}-{i}") for i in range(2)]
        before = self.job_rows(job)
        procs = [subprocess.Popen([sys.executable, "-I", "-c", LAUNCHER, barrier, windows[i], *self.recover_argv(job)[2:]],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env(), cwd=self.dir)
                 for i in range(2)]
        results = []
        try:
            time.sleep(0.5)                                  # both children are started and spinning on the barrier
            with open(barrier, "w") as fh:
                fh.write("go")
            for p in procs:
                out, err = p.communicate(timeout=CHILD_TIMEOUT_S)
                results.append((p.returncode, json.loads(out.decode("utf-8")) if out.strip() else {},
                                err.decode("utf-8", "replace")))
        except subprocess.TimeoutExpired:
            _kill_all(procs)
            self.fail(f"round {n}: a child did not finish within {CHILD_TIMEOUT_S} s; both were killed")
        finally:
            _kill_all(procs)                                 # item 2: never leave a child behind, on any path
            self.assertTrue(all(p.poll() is not None for p in procs), "a child process outlived the round")
        spans = []
        for w in windows:
            with open(w, encoding="utf-8") as fh:
                a, b = fh.read().split()
            spans.append((float(a), float(b)))
        overlapped = max(s[0] for s in spans) < min(s[1] for s in spans)
        winners = [r for r in results if r[0] == 0]
        losers = [r for r in results if r[0] != 0]
        self.assertEqual((len(winners), len(losers)), (1, 1), results)
        loser = losers[0]
        self.assertEqual(loser[0], 1, loser)
        self.assertIs(loser[1]["ok"], False, loser)
        if loser[1]["code"] == "rejected":
            self.assertTrue(loser[1]["error"].startswith("rejected(recovery-open): recovery is already open"), loser)
            outcome = "refused recovery-open"
        else:
            self.assertEqual(loser[1]["code"], "lock-timeout", loser)
            outcome = "lock-timeout"
        after = self.job_rows(job)
        self.assertEqual(len(after), len(before) + 1)
        self.assertEqual([r["type"] for r in after].count("recovery-opened"), 1)
        self.assertEqual(self.chain_problems(job), [])
        return {"round": n, "overlapped": overlapped, "outcome": outcome, "spans": spans}

    def test_barrier_started_race_opens_exactly_one_recovery_and_reports_overlap(self):
        rounds = []
        for n in range(RACE_ROUNDS):
            job = f"b{n}"
            self.job_on(self.db, job)
            rounds.append(self._round(job, n))
        overlaps = sum(1 for r in rounds if r["overlapped"])
        log(f"{self._testMethodName}-overlap", {"rounds": RACE_ROUNDS, "overlapped": overlaps, "detail": rounds})
        print(f"\nWF-034 item 3: {overlaps}/{RACE_ROUNDS} rounds overlapped; outcomes "
              f"{[r['outcome'] for r in rounds]}", file=sys.stderr)
        self.assertEqual(len(rounds), RACE_ROUNDS)

    def test_a_child_that_hangs_is_killed_with_its_sibling(self):
        """Item 2 on the timeout path itself: a child that never passes its barrier times out; both are killed."""
        barrier = os.path.join(self.dir, "never")
        windows = [os.path.join(self.dir, f"w{i}") for i in range(2)]
        self.job_on(self.db)
        procs = [subprocess.Popen([sys.executable, "-I", "-c", LAUNCHER, barrier, windows[i], *self.recover_argv("j")[2:]],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env(), cwd=self.dir)
                 for i in range(2)]
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                procs[0].communicate(timeout=1.0)
        finally:
            _kill_all(procs)
            for p in procs:
                p.stdout.close()
                p.stderr.close()
        self.assertTrue(all(p.poll() is not None for p in procs), "a child process outlived the test")
        self.assertFalse(any(os.path.exists(w) for w in windows), "neither child ran wf")


# ----------------------------------------------------------------------------------------------- item 4
class VerifyRestoredLimitStrict(T30Base):
    """The T30(h) setup (test_t30_race_secondwriter_identity.T30hReplacement, repeated here so its own tests are not
    collected twice): the ledger is replaced by another internally consistent ledger of the same job."""

    def setUp(self) -> None:
        super().setUp()
        self.job_on(self.db, note="original")
        other_dir = os.path.join(self.dir, "replacement", "ledger")
        os.makedirs(other_dir)
        other = os.path.join(other_dir, "l.db")
        self.job_on(other, note="replacement")
        os.replace(other, self.db)
        self.assertEqual(self.chain_problems(), [])

    def test_restored_check_of_a_rewritten_chain_states_the_documented_limit(self):
        code, res = self.run_wf("verify", "--restored", self.db, role="coordinator", session="C1")
        self.assertEqual(code, 0, res)
        self.assertTrue(res["clean"], res)                    # the rewrite is consistent: nothing to find
        self.assertIsNone(res["checkpoint"])
        self.assertIn(backup.LIMIT_NO_CHECKPOINT, res["limitations"])
        self.assertEqual(backup.LIMIT_NO_CHECKPOINT,
                         "internally consistent only; a consistent whole-chain rewrite or tail deletion is not "
                         "detectable without an independently retained checkpoint")
        runbook = os.path.join(WF_DIR, "docs", "RESTORE-RUNBOOK.md")
        with open(runbook, encoding="utf-8") as fh:
            doc = " ".join(fh.read().split())
        self.assertIn("Without a retained checkpoint, a consistent whole-chain rewrite or a tail deletion is "
                      "**not detectable**.", doc)


# ----------------------------------------------------------------------------------------------- item 5
#: Content keys that may carry a session id, each defined by a schema: bind / ack-assignment / stop-confirmed
#: (bind._SCHEMA_KEYS), reconcile-report / ack-reconcile (reconcile._REPORT_KEYS, _ACK_KEYS), the claimant-conflict
#: blocker (README "Claimant conflict": holder_session, claimant_session), accept (commands.accept: builder_session).
SESSION_KEYS = frozenset({"session", "holder_session", "claimant_session", "builder_session"})
#: Key tokens that would name a ledger, machine, user or process identity (D-S5-1).
IDENTITY_TOKENS = frozenset({"host", "hostname", "machine", "computer", "computername", "user", "username", "login",
                             "uid", "gid", "sid", "pid", "ino", "inode", "dev", "device", "identity", "cwd", "db",
                             "home", "account"})


def _walk(obj, where="$"):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield where, k, v
            yield from _walk(v, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{where}[{i}]")


class NoIdentityInEvents(G._Gate):
    def _lifecycle(self) -> None:
        """Explicit job j through most event types, and an implicit job k with a claimant conflict, a recovery opened
        and closed, an intervention and a cancellation."""
        self.explicit()
        ok, run = self.ok, self.run_wf
        rows = self.ledger_rows()
        bind_seq = max(r["seq"] for r in rows if r["type"] == "bind" and json.loads(r["content"])["role"] == "builder")
        ok(run("append", "j", "--type", "ack-assignment", "--content",
               json.dumps({"role": "builder", "session": "B1", "bind_seq": bind_seq})))
        ok(run("append", "j", "--type", "intent", "--content",
               json.dumps({"path": "jobs/j/work/a.txt", "sha_before": "", "note": "n"})))
        ok(run("append", "j", "--type", "delivered", "--content", '{"claims":[{"claim":"c","evidence":["e"]}]}'))
        self.bind("reviewer", "R1")                                  # a reviewer is bound only after delivery
        rep = ok(run("reconcile", "j", role="coordinator", session="C1"))
        ok(run("ack-reconcile", "j", "--report", str(rep["seq"]), "--exceptions",
               json.dumps([e["id"] for e in rep["exceptions"]])))
        results = {"criteria": [{"id": "AC1", "pass": True, "actual": "ok"}], "changed_paths": [],
                   "environment": "disposable local scope", "limits": "none", **DECLARATIONS}
        ok(run("accept", "j", "--results", json.dumps(results), role="reviewer", session="R1"))
        ok(run("log-intervention", "j", "--label", "l", "--necessity", "unknown", "--minutes", "1",
               role="coordinator", session="C1"))
        self.stop("B1")
        # job k, implicit mode (a grant without ARS roles): first builder session K1, a claimant conflict by K2, a
        # recovery opened and closed, a cancellation
        import test_wf
        gk = os.path.join(self.dir, "grant-implicit.json")
        with open(gk, "w", encoding="utf-8") as fh:
            json.dump(test_wf.GRANT, fh)
        ok(run("job", "new", "k", "--contract", self.cpath, role="coordinator", session="C1"))
        h = ok(run("grant", "propose", "k", "--grant", gk, role="coordinator", session="C1"))["block_hash"]
        ok(run("grant", "activate", "k", "--owner-hash", h[:8], "--quote", f"approve G1 {h[:8]}", "--turn",
               "owner turn k", role="coordinator", session="C1"))
        ok(run("append", "k", "--type", "intent", "--content",
               json.dumps({"path": "jobs/k/work/a.txt", "sha_before": "", "note": "n"}), session="K1"))
        c, r = run("append", "k", "--type", "intent", "--content",
                   json.dumps({"path": "jobs/k/work/a.txt", "sha_before": "", "note": "second"}), session="K2")
        self.assertEqual(r.get("conflict_outcome"), "COMMITTED", r)
        ok(run("recover", "begin", "k", "--reason", "r", "--turn", "owner turn k", role="coordinator", session="C1"))
        ok(run("recover", "close", "k", "--turn", "owner turn k", role="coordinator", session="C1"))
        ok(run("cancel", "k", "--reason", "r", "--turn", "owner turn k", role="coordinator", session="C1"))

    def test_no_identity_in_any_event_content(self):
        self._lifecycle()
        conn = G.dbm.connect(self.db, create=False)
        try:
            rows = [r for j in G.dbm.jobs(conn) for r in G.dbm.rows(conn, j)]
        finally:
            conn.close()
        types = sorted({r["type"] for r in rows})
        ident = gatecore.ledger_identity(self.db)
        forbidden_values = {v for v in (socket.gethostname(), getpass.getuser(), os.environ.get("COMPUTERNAME"),
                                        os.environ.get("USERNAME"), os.environ.get("USER"), os.path.expanduser("~"),
                                        os.path.abspath(self.db), os.path.dirname(os.path.abspath(self.db)),
                                        os.path.abspath(self.dir))
                            if v and len(v) >= 3}
        problems = []
        session_keys_seen = set()
        for r in rows:
            content = json.loads(r["content"])
            for where, key, value in _walk(content):
                tokens = set(re.split(r"[_\-]", str(key).lower()))
                if "session" in tokens:
                    session_keys_seen.add(key)
                    if key not in SESSION_KEYS:
                        problems.append(f"seq {r['seq']} {r['type']} {where}.{key}: session id under an undefined key")
                elif tokens & IDENTITY_TOKENS:
                    problems.append(f"seq {r['seq']} {r['type']} {where}.{key}: identity-like key")
            text = r["content"]
            for v in forbidden_values:
                if v.lower() in text.lower() or json.dumps(v)[1:-1].lower() in text.lower():
                    problems.append(f"seq {r['seq']} {r['type']}: content contains {v!r}")
            for k in ("dev", "ino"):
                if re.search(rf'"{k}"\s*:\s*{ident[k]}\b', text):
                    problems.append(f"seq {r['seq']} {r['type']}: ledger {k} {ident[k]}")
        log(f"{self._testMethodName}-scan", {"types": types, "rows": len(rows), "session_keys": sorted(session_keys_seen),
                                              "problems": problems})
        self.assertGreaterEqual(len(types), 14, types)               # the scan covered a wide set of event types
        self.assertEqual(problems, [])
        self.assertTrue(session_keys_seen <= SESSION_KEYS, session_keys_seen)
