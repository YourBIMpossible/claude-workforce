"""Unit tests for the wf R1 slice (FB1.4 D8). Run: python -I -m unittest discover -s tools/wf/tests
Logs go to WF_TEST_LOGDIR (default tools\\wf\\tests\\logs). Ledgers live under WF_TEST_TMP (default
fixtures\\tmp\\unittest, must be under fixtures\\tmp) so the fault seam is honoured. Test ids T01.. follow
FB1.4 §4.6 / D2–D7 numbering where a mechanism exists in this
slice; ids that name out-of-scope mechanisms (gate, replay, recovery, bind) are documented as record-and-verify
readings in the docstring and not claimed.

Map from these FB1.4 ids to FIRST-RELEASE-SPEC Rev 5.3 §8 cases (WF-019 finding F7, WF-026; behaviour, not name).
The `test_spec_Txx` methods below already use spec numbering.
  test_T01_canonical_and_self_hash ............. no spec case (canonical JSON / self hash; basis of T23, T36 level 1)
  test_T02_schema_constraints_and_immutability . no spec case of its own (schema, triggers, journal mode; supports T23, T31)
  test_T03_contract_required_fields ............ spec T03 (contract source turn) and T09 (contract missing-field label)
  test_T04_message_rules ....................... spec T01, T02, T04, T05, T08, T10
  test_T05_grant_ordering ...................... spec T03 (no-provenance) and T29 (unbound-approval, wrong owner hash)
  test_T05b_block_hash_repeatable_on_fresh_ledger  no spec case (block-hash determinism)
  test_T07_grant_resource_widening ............. spec T10
  test_T08_full_flow_status_accept_close ....... spec T20 (close before accept), T24 (same session), T35 (P-classes)
  test_T09_abandon_then_close .................. no spec case (abandon flow)
  test_T10_verify_detects_tamper ............... spec T23 (row edit) and the `wf` half of T19 (append refused on a bad chain)
  test_T27_guard_cases ......................... spec T28 (uncertain-write markers, §4.4a)
  test_T28_resolve_selection ................... no spec case (marker resolution)
  test_T29_commit_timeout_give_up .............. spec T27 (commit-timeout result)
  test_T29b_reads_wait_for_write_lock .......... spec T27 (bounded reader)
  test_T30_commit_error_fault_and_resolve ...... spec T28(b), by proxy (IOERR during commit)
  test_T31_faults_not_allowed_on_product_path .. no spec case (fault-seam guard)
  test_T31b_export_refresh_failure_keeps_commit  spec T28(c)"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WF_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(WF_DIR))
sys.path.insert(0, WF_DIR)

import wf  # noqa: E402
from wfcore import canon, commands, db as dbm, manifest, markers, paths, txn  # noqa: E402
from wfcore.db import OpLog, WfRefused, WfRejected  # noqa: E402
from wfcore.faults import Faults  # noqa: E402

LOGDIR = os.environ.get("WF_TEST_LOGDIR") or os.path.join(HERE, "logs")
# FB1.4-A4 §5: the default scratch root is unique per run; nothing under an earlier run is reused or deleted.
TMP = os.path.abspath(os.environ.get("WF_TEST_TMP")
                      or os.path.join(paths.FIXTURES_TMP, "unittest-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
                                      + "-" + os.urandom(3).hex()))
if not paths.is_under(TMP, paths.FIXTURES_TMP):
    raise SystemExit(f"WF_TEST_TMP must be under {paths.FIXTURES_TMP} (fault seam): {TMP}")

CONTRACT = {
    "owner_request": {"quote": "Approve FB1.4 (sha256 bae4b01dfdf0)", "source_turn": "owner turn 2026-10-11"},
    "interpretation": "unit test contract", "baseline": {"manifest": [], "git_revision": None, "uncommitted_state": None},
    "outcome": "events recorded",
    "acceptance_criteria": [{"id": "AC1", "check": "wf verify j --json", "expected": "clean true"}],
    "read_scope": ["tools/wf/**"], "write_scope": ["jobs/j/work/**"], "watch": ["ledger/**"],
    "presence_only": ["fixtures/results/**"], "allowed_tools": {"builder": ["Write"], "reviewer": ["Read"]},
    "limits": "unit", "exclusions": ["01-PROJECT-BRIEF.md"], "evidence": ["ledger"],
    "closeout_rules": "close after accept", "recovery_rules": "none", "observe_thresholds": {"held_s": 2.0},
    "environment": "disposable local scope",
}
# T24 (lane A, 2026-10-13): `wf accept` results must declare the contract hash and the baseline; absent = rejected.
# Fixtures that exercise accept for other reasons carry correct declarations.
DECLARATIONS = {"contract_sha256": canon.sha256_obj(CONTRACT), "baseline": CONTRACT["baseline"]}
GRANT = {"grant_id": "G1", "actions": ["write"], "resources": ["jobs/j/work/**"], "exclusions": [],
         "ars": {"a": "owner"}, "delegation": {}, "expiry": "2026-12-31T00:00:00Z", "revocation_rule": "owner"}


# FIX2 #40: a test's scratch folder is named by a short id of its full test id, not by its method name, so the
# deepest scratch path no longer grows with the test name (MAX_PATH on Windows hosts without long paths). Each run
# writes the folder -> test id map to SCRATCH_MAP, one line per test.
SCRATCH_MAP = os.path.join(TMP, "scratch-map.tsv")
_SCRATCH_IDS: dict[str, str] = {}  # short folder name -> full test id, for this process's run
LOG_NAME_MAX = 64  # FIX2 #40: longer log names are shortened, so a test-name-derived log path cannot reach MAX_PATH


def scratch_name(test_id: str) -> str:
    """The short scratch-folder name for a full test id (`TestCase.id()`): `t-` and 8 hex of its sha256."""
    return "t-" + hashlib.sha256(test_id.encode("utf-8")).hexdigest()[:8]


def log(name: str, data) -> None:
    os.makedirs(LOGDIR, exist_ok=True)
    if len(name) > LOG_NAME_MAX:
        name = name[:LOG_NAME_MAX - 9] + "~" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    with open(os.path.join(LOGDIR, f"{name}.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, default=str)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = scratch_name(self.id())
        holder = _SCRATCH_IDS.setdefault(self.scratch, self.id())
        if holder != self.id():  # FIX2 #40: two tests in one run never share a folder
            raise SystemExit(f"refused: scratch name {self.scratch} collides: {holder} and {self.id()}")
        self.dir = os.path.join(TMP, self.scratch)
        if os.path.exists(self.dir):  # FB1.4-A4 §5: refuse-existing, never rmtree
            raise SystemExit(f"refused: {self.dir} exists (use a fresh WF_TEST_TMP)")
        os.makedirs(self.dir)
        with open(SCRATCH_MAP, "a", encoding="utf-8") as fh:
            fh.write(f"{self.scratch}\t{self.id()}\n")
        self.db = os.path.join(self.dir, "ledger", "l.db")
        self.cpath = os.path.join(self.dir, "contract.json")
        self.gpath = os.path.join(self.dir, "grant.json")
        with open(self.cpath, "w", encoding="utf-8") as fh:
            json.dump(CONTRACT, fh)
        with open(self.gpath, "w", encoding="utf-8") as fh:
            json.dump(GRANT, fh)
        os.environ.pop("WF_TEST_FAULTS", None)

    def run_wf(self, *argv: str, role="builder", session="B1", **kw):
        base = ["--db", self.db, "--role", role, "--session", session]
        if "msg_id" in kw:
            base += ["--msg-id", kw["msg_id"]]
        code, res = wf.run(base + list(argv))
        log(f"{self.scratch}-{len(os.listdir(LOGDIR)) if os.path.isdir(LOGDIR) else 0}", {"argv": argv, "code": code, "res": res})
        return code, res

    def new_job(self, job="j"):
        code, res = self.run_wf("job", "new", job, "--contract", self.cpath, role="coordinator", session="C1")
        self.assertEqual(code, 0, res)
        return res

    def activate(self, job="j"):
        code, res = self.run_wf("grant", "propose", job, "--grant", self.gpath, role="coordinator", session="C1")
        self.assertEqual(code, 0, res)
        h = res["block_hash"]
        code, res = self.run_wf("grant", "activate", job, "--owner-hash", h[:8], "--quote", f"approve G1 {h[:8]}",
                                "--turn", "owner turn", role="coordinator", session="C1")
        self.assertEqual(code, 0, res)
        return h

    def intent(self, job="j", session="B1", **kw):
        return self.run_wf("append", job, "--type", "intent", "--content",
                           json.dumps({"path": "jobs/j/work/a.txt", "sha_before": "", "note": kw.pop("note", "n")}),
                           session=session, **kw)


class T01Canon(unittest.TestCase):
    def test_T01_canonical_and_self_hash(self):
        a = canon.canonical({"b": 1, "a": [1, 2]})
        self.assertEqual(a, '{"a":[1,2],"b":1}')
        row = {"job": "j", "seq": 1, "type": "contract", "msg_id": "m", "author_role": "coordinator", "session_id": "C",
               "generation": 1, "reply_to": None, "grant_ref": None, "ts": "t", "content_sha256": "c", "prev": canon.GENESIS}
        self.assertEqual(len(canon.event_self_hash(row)), 64)
        row2 = dict(row, ts="t2")
        self.assertNotEqual(canon.event_self_hash(row), canon.event_self_hash(row2))


class T02Schema(Base):
    def test_T02_schema_constraints_and_immutability(self):
        conn = dbm.connect(self.db)
        self.assertEqual(dbm.constraints_present(conn), [])
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "delete")
        conn.close()
        self.new_job()
        conn = dbm.connect(self.db, create=False)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE events SET ts='x' WHERE seq=1")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM events WHERE seq=1")
        with self.assertRaises(sqlite3.IntegrityError):  # UNIQUE(job, prev)
            r = dbm.row_at(conn, "j", 1)
            r2 = dict(r, seq=2, self="x" * 64)
            conn.execute("INSERT INTO events VALUES (:job,:seq,:type,:msg_id,:author_role,:session_id,:generation,:reply_to,:grant_ref,:ts,:content,:content_sha256,:prev,:self)", r2)
        conn.close()


class T03Validate(Base):
    def test_T03_contract_required_fields(self):
        bad = dict(CONTRACT)
        del bad["acceptance_criteria"]
        with open(self.cpath, "w", encoding="utf-8") as fh:
            json.dump(bad, fh)
        code, res = self.run_wf("job", "new", "j", "--contract", self.cpath, role="coordinator", session="C1")
        self.assertEqual(code, 1)
        self.assertIn("rejected(malformed)", res["error"])
        bad = dict(CONTRACT, owner_request={"quote": "q", "source_turn": ""})
        with open(self.cpath, "w", encoding="utf-8") as fh:
            json.dump(bad, fh)
        code, res = self.run_wf("job", "new", "j", "--contract", self.cpath, role="coordinator", session="C1")
        self.assertIn("rejected(no-provenance)", res["error"])

    def test_T04_message_rules(self):
        """§5: duplicate-ignored, conflict, malformed, stale, widening, claimant-conflict, role."""
        self.new_job(); self.activate()
        c, r = self.intent(msg_id="M1"); self.assertEqual(r["outcome"], "COMMITTED")
        c, r = self.intent(msg_id="M1"); self.assertEqual((c, r["outcome"]), (0, "DUPLICATE-IGNORED"))
        c, r = self.intent(msg_id="M1", note="other"); self.assertIn("rejected(conflict)", r["error"])
        c, r = self.run_wf("append", "j", "--type", "intent", "--content", '{"path":"p"}'); self.assertIn("rejected(malformed)", r["error"])
        c, r = self.run_wf("append", "j", "--type", "blocker", "--content", '{"reason":"r"}', "--generation", "2")
        self.assertEqual(r["outcome"], "COMMITTED")
        c, r = self.run_wf("append", "j", "--type", "blocker", "--content", '{"reason":"r2"}', "--generation", "1")
        self.assertIn("rejected(stale)", r["error"])
        c, r = self.run_wf("append", "j", "--type", "handoff", "--content",
                           '{"to_role":"coordinator","write_scope":["ledger/**"],"note":"n"}', "--generation", "2")
        self.assertIn("rejected(widening)", r["error"])
        c, r = self.run_wf("append", "j", "--type", "intent", "--content", '{"path":"p","sha_before":"","note":"n"}', "--generation", "2", session="B2")
        self.assertIn("rejected(claimant-conflict)", r["error"])
        # FB1.4-A4 §3: the conflict is recorded as a blocker row at head+1 and blocks the role (both sessions)
        self.assertEqual((c, r["conflict_outcome"]), (1, "COMMITTED"), r)
        row = commands.status(self.db, "j")["head"]
        self.assertEqual(row["seq"], r["conflict_seq"])
        c, r2 = self.run_wf("status", "j"); self.assertEqual(r2["axes"]["execution"], "blocked")
        self.assertEqual(r2["axes"]["blocked_roles"], ["builder"])
        c, r = self.run_wf("append", "j", "--type", "intent", "--content", '{"path":"p","sha_before":"","note":"n"}',
                           role="reviewer", session="R1")
        self.assertIn("rejected(role)", r["error"])

    def test_T05_grant_ordering(self):
        self.new_job()
        c, r = self.run_wf("grant", "activate", "j", "--owner-hash", "deadbeef", "--quote", "q", "--turn", "t",
                           role="coordinator", session="C1")
        self.assertEqual(r["code"], "refused")  # nothing proposed
        c, r = self.run_wf("grant", "propose", "j", "--grant", self.gpath, role="coordinator", session="C1")
        h = r["block_hash"]
        self.assertIn("GRANT CONFIRMATION BLOCK", r["block_text"])
        c, r = self.run_wf("grant", "activate", "j", "--owner-hash", "deadbeef", "--quote", "q", "--turn", "t",
                           role="coordinator", session="C1")
        self.assertIn("rejected(unbound-approval)", r["error"])
        c, r = self.run_wf("grant", "activate", "j", "--owner-hash", h[:8], "--quote", "q", "--turn", "",
                           role="coordinator", session="C1")
        self.assertIn("rejected(no-provenance)", r["error"])
        c, r = self.run_wf("grant", "activate", "j", "--owner-hash", h[:8], "--quote", "q", "--turn", "t",
                           role="coordinator", session="C1")
        self.assertEqual(r["outcome"], "COMMITTED")
        c, r = self.run_wf("status", "j")
        self.assertEqual(r["axes"]["authority"], "in-force")

    def test_T05b_block_hash_repeatable_on_fresh_ledger(self):
        """TD-1 repeatability: the owner's hash, given once, matches the block on a fresh ledger.
        Fixture correction (White, A1 wiring, owner grant 2026-10-15 ~15:05): the fresh ledger lives in its own
        `fresh/ledger/` directory. The spec places the ledger at `ledger/` (FIRST-RELEASE-SPEC.md section 3 storage
        design, section 4.3 boundary) and `wf job new` now captures the changed-set baseline over the monitored
        boundary, whose root is the parent of a directory named `ledger` (frozen interface, issue #13): a ledger in
        a directory named `ledger2` has no establishable boundary and `wf job new` refuses with that exact blocker
        (test_t35_wiring.py). The hash-repeatability assertion is unchanged."""
        self.new_job()
        h1 = self.activate()
        self.db = os.path.join(self.dir, "fresh", "ledger", "l.db")
        time.sleep(0.01)
        self.new_job()
        code, res = self.run_wf("grant", "propose", "j", "--grant", self.gpath, role="coordinator", session="C1")
        self.assertEqual(res["block_hash"], h1)
        code, res = self.run_wf("grant", "activate", "j", "--owner-hash", h1[:12], "--quote", "q", "--turn", "t",
                                role="coordinator", session="C1")
        self.assertEqual(code, 0, res)
        code, res = self.run_wf("grant", "activate", "j", "--owner-hash", "0" * 12, "--quote", "q", "--turn", "t",
                                role="coordinator", session="C1")
        self.assertNotEqual(code, 0, res)

    def test_T07_grant_resource_widening(self):
        self.new_job()
        with open(self.gpath, "w", encoding="utf-8") as fh:
            json.dump(dict(GRANT, resources=["ledger/**"]), fh)
        c, r = self.run_wf("grant", "propose", "j", "--grant", self.gpath, role="coordinator", session="C1")
        self.assertIn("rejected(widening)", r["error"])


class T08Flow(Base):
    def test_T08_full_flow_status_accept_close(self):
        """T08–T10, T23, T24: axes, accept classes P1–P5, same-session refusal, close rules."""
        self.new_job(); self.activate()
        self.intent()
        c, r = self.run_wf("status", "j"); self.assertEqual(r["axes"]["execution"], "active")
        self.run_wf("append", "j", "--type", "blocker", "--content", '{"reason":"x"}')
        c, r = self.run_wf("status", "j"); self.assertEqual(r["axes"]["execution"], "interrupted")
        c, r = self.run_wf("accept", "j", "--results", "{}", role="reviewer", session="R1")
        self.assertEqual(r["code"], "refused")  # malformed results: no event
        self.run_wf("append", "j", "--type", "delivered", "--content", '{"claims":[{"claim":"c","evidence":["e"]}]}')
        results = {"criteria": [{"id": "AC1", "pass": True, "actual": "ok"}], "changed_paths": ["jobs/j/work/a.txt"],
                   "environment": "disposable local scope", "limits": "none", **DECLARATIONS}
        c, r = self.run_wf("accept", "j", "--results", json.dumps(results), role="reviewer", session="B1")
        self.assertEqual(r["code"], "refused"); self.assertIn("same-session", r["error"])
        c, r = self.run_wf("close", "j", "--intervention", "none", role="coordinator", session="C1")
        self.assertEqual(r["code"], "refused")
        bad = dict(results, changed_paths=["jobs/j/work/a.txt", "01-PROJECT-BRIEF.md", "elsewhere.txt"])
        c, r = self.run_wf("accept", "j", "--results", json.dumps(bad), role="reviewer", session="R1", msg_id="A1")
        self.assertEqual((c, r["decision"]), (0, "rejected"))
        # A2 (spec 4.4 lines 216/223): P4 is the fixed coordination set; a path in no scope is "unclassified"
        self.assertEqual(r["classes"]["P5"], ["01-PROJECT-BRIEF.md"]); self.assertEqual(r["classes"]["unclassified"], ["elsewhere.txt"])
        c, r = self.run_wf("status", "j"); self.assertEqual(r["axes"]["acceptance"], "rejected")
        c, r = self.run_wf("accept", "j", "--results", json.dumps(results), role="reviewer", session="R1", msg_id="A2")
        self.assertEqual(r["decision"], "accepted")
        c, r = self.run_wf("verify", "j"); self.assertTrue(r["clean"], r)
        c, r = self.run_wf("checkpoint", "j"); self.assertEqual(c, 0)
        c, r = self.run_wf("close", "j", "--intervention", "none", role="coordinator", session="C1")
        self.assertEqual(r["outcome"], "COMMITTED")
        c, r = self.intent(); self.assertIn("rejected(closed)", r["error"])
        c, r = self.run_wf("digest", "j"); self.assertTrue(r["closed"]); self.assertEqual(r["counts"]["accept"], 2)

    def test_T09_abandon_then_close(self):
        self.new_job(); self.activate(); self.intent()
        c, r = self.run_wf("job", "abandon", "j", "--reason", "owner stop")
        self.assertEqual(c, 0)
        c, r = self.intent(); self.assertEqual(r["code"], "refused")
        c, r = self.run_wf("status", "j"); self.assertEqual(r["axes"]["execution"], "abandoned")
        c, r = self.run_wf("close", "j", "--intervention", "owner abandoned", role="coordinator", session="C1")
        self.assertEqual(r["outcome"], "COMMITTED")  # close with basis abandoned is the one event the gate admits
        c, r = self.run_wf("digest", "j"); self.assertTrue(r["closed"])

    def test_T10_verify_detects_tamper(self):
        self.new_job(); self.activate(); self.intent()
        conn = sqlite3.connect(self.db, isolation_level=None)
        conn.execute("DROP TRIGGER events_no_update")
        conn.execute("UPDATE events SET content = replace(content, 'n', 'N') WHERE seq = 4")
        conn.close()
        c, r = self.run_wf("verify", "j")
        self.assertFalse(r["clean"])
        self.assertTrue(any("content_sha256" in p for p in r["jobs"]["j"]["problems"]))
        self.assertIn("trigger missing: events_no_update", r["ledger_problems"]["schema"])
        c, r = self.intent(); self.assertEqual(r["code"], "refused")  # held-phase head check refuses to extend


class SpecMapGaps(Base):
    """Cases the FB1.4 deliverables table maps to spec test ids (FIRST-RELEASE-SPEC test map) that the per-module
    tests above did not exercise. Spec ids whose behaviour is outside this slice (observe, reconcile, recovery,
    bind, replacement, approval invalidation, backup/restore) are not tested here."""

    def test_spec_T09_missing_author_or_session(self):
        self.new_job(); self.activate()
        content = '{"path":"p","sha_before":"","note":"n"}'
        for base in (["--db", self.db, "--role", "builder"], ["--db", self.db, "--session", "B1"]):
            saved = {k: os.environ.pop(k, None) for k in ("WF_ROLE", "WF_SESSION")}
            try:
                code, res = wf.run(base + ["append", "j", "--type", "intent", "--content", content])
            finally:
                os.environ.update({k: v for k, v in saved.items() if v is not None})
            self.assertNotEqual(code, 0, res)
        self.assertEqual(commands.status(self.db, "j")["head"]["seq"], 3)

    def test_spec_T21_close_needs_intervention_declaration(self):
        self.new_job(); self.activate()
        self.run_wf("job", "abandon", "j", "--reason", "r")
        c, r = self.run_wf("close", "j", "--intervention", "  ", role="coordinator", session="C1")
        self.assertNotEqual(c, 0, r)
        with self.assertRaises(SystemExit) as cm:  # argparse: --intervention is required (usage, exit 2)
            self.run_wf("close", "j", role="coordinator", session="C1")
        self.assertEqual(cm.exception.code, 2)
        self.assertFalse(self.run_wf("digest", "j")[1]["closed"])

    def test_spec_T24_wrong_environment_rejected(self):
        self.new_job(); self.activate(); self.intent()
        self.run_wf("append", "j", "--type", "delivered", "--content", '{"claims":[{"claim":"c","evidence":["e"]}]}')
        results = {"criteria": [{"id": "AC1", "pass": True, "actual": "ok"}], "changed_paths": ["jobs/j/work/a.txt"],
                   "environment": "production", "limits": "none", **DECLARATIONS}
        c, r = self.run_wf("accept", "j", "--results", json.dumps(results), role="reviewer", session="R1")
        self.assertEqual(r["decision"], "rejected"); self.assertIn("environment", r["reasons"][0])

    def test_spec_T23_delete_mid_chain_detected(self):
        self.new_job(); self.activate(); self.intent(); self.intent(note="second")
        conn = sqlite3.connect(self.db, isolation_level=None)
        conn.execute("DROP TRIGGER events_no_delete")
        conn.execute("DELETE FROM events WHERE job='j' AND seq=4")
        conn.close()
        c, r = self.run_wf("verify", "j")
        self.assertFalse(r["clean"]); self.assertTrue(r["jobs"]["j"]["problems"], r)

    def test_spec_T31_insert_at_existing_seq_refused_by_schema(self):
        self.new_job(); self.activate()
        conn = sqlite3.connect(self.db, isolation_level=None)
        row = conn.execute("SELECT * FROM events WHERE job='j' AND seq=3").fetchone()
        cols = [d[0] for d in conn.execute("SELECT * FROM events LIMIT 0").description]
        changed = list(row); changed[cols.index("prev")] = "0" * 64; changed[cols.index("type")] = "blocker"
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(f"INSERT INTO events ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", changed)
        conn.close()
        self.assertTrue(commands.verify(self.db, "j")["clean"])

    def test_spec_T35_path_classes_P1_to_P5(self):
        cl = commands.classify_paths(["jobs/j/work/a.txt", "fixtures/results/x.json", "ledger/l.db",
                                      "01-PROJECT-BRIEF.md", "elsewhere.txt"], CONTRACT)
        # seam (dispatch-ready): P1..P5 are the required keys; further keys are lane B's per-path record.
        # A2 (spec 4.4 lines 216/223): P4 is the fixed coordination set (none passed here); a path in no contract
        # scope is "unclassified", a separate violation, no longer P4.
        self.assertEqual({k: cl[k] for k in ("P1", "P2", "P3", "P4", "P5", "unclassified")},
                         {"P1": ["jobs/j/work/a.txt"], "P2": ["fixtures/results/x.json"], "P3": ["ledger/l.db"],
                          "P4": [], "P5": ["01-PROJECT-BRIEF.md"], "unclassified": ["elsewhere.txt"]})

    def test_verify_compares_receipts_to_work_files(self):
        self.new_job(); self.activate()
        work = os.path.join(self.dir, "jobs", "j", "work"); os.makedirs(work, exist_ok=True)
        f = os.path.join(work, "a.txt")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("hello")
        sha = canon.sha256_file(f)
        self.run_wf("append", "j", "--type", "receipt", "--content",
                    json.dumps({"path": "jobs/j/work/a.txt", "sha_before": "", "sha_after": sha, "note": "n"}))
        self.run_wf("append", "j", "--type", "receipt", "--content",
                    json.dumps({"path": "../outside.txt", "sha_before": "", "sha_after": "x", "note": "n"}))
        rec = {x["path"]: x for x in commands.verify(self.db, "j")["jobs"]["j"]["receipts"]}
        self.assertTrue(rec["jobs/j/work/a.txt"]["match"]); self.assertEqual(rec["jobs/j/work/a.txt"]["sha_now"], sha)
        self.assertFalse(rec["../outside.txt"]["match"]); self.assertIsNone(rec["../outside.txt"]["sha_now"])
        with open(f, "a", encoding="utf-8") as fh:
            fh.write("!")
        rec = {x["path"]: x for x in commands.verify(self.db, "j")["jobs"]["j"]["receipts"]}
        self.assertFalse(rec["jobs/j/work/a.txt"]["match"])

    def test_connect_wait_counts_against_acquisition_bound(self):
        """§4.5: an EXCLUSIVE holder blocks connection setup too; the write still gives up within T_ACQ (+0.4 s),
        not T_ACQ for connect plus T_ACQ for BEGIN IMMEDIATE."""
        self.new_job(); self.activate()
        import threading
        holder = sqlite3.connect(self.db, isolation_level=None, check_same_thread=False)
        holder.execute("BEGIN EXCLUSIVE")
        threading.Timer(7.5, lambda: holder.execute("ROLLBACK")).start()
        t0 = time.monotonic()
        c, r = self.intent()
        el = time.monotonic() - t0
        time.sleep(7.7 - el if el < 7.7 else 0); holder.close()
        self.assertEqual(r.get("code"), "lock-timeout", r)
        self.assertGreaterEqual(el, 5.0); self.assertLessEqual(el, 5.4)


class T27Markers(Base):
    def test_T27_guard_cases(self):
        """§4.4a: committed marker cleared; aborting+no row cleared; committing+no row refuses; malformed refuses."""
        self.new_job(); self.activate(); c, r = self.intent()
        pdir = paths.pending_dir(self.db, "j")
        # committed marker (row exists, self matches)
        ref = markers.MarkerRef(pdir, r["seq"], markers.new_attempt_id())
        markers.write_atomic(ref, markers.make("j", r["seq"], ref.attempt_id, r["self"], "p", "committing", 1, "s", self.db, "c"))
        c, r2 = self.intent(); self.assertEqual(r2["outcome"], "COMMITTED")
        self.assertEqual(r2["cleared"][0]["cleared"], "committed"); self.assertFalse(os.path.exists(ref.path))
        # aborting + no row
        ref = markers.MarkerRef(pdir, 99, markers.new_attempt_id())
        markers.write_atomic(ref, markers.make("j", 99, ref.attempt_id, "x", "p", "aborting", 2, "s", self.db, "c"))
        c, r3 = self.intent(); self.assertEqual(r3["cleared"][0]["cleared"], "aborting")
        # committing + no row → fail closed
        ref = markers.MarkerRef(pdir, 99, markers.new_attempt_id())
        markers.write_atomic(ref, markers.make("j", 99, ref.attempt_id, "x", "p", "committing", 1, "s", self.db, "c"))
        c, r4 = self.intent(); self.assertEqual(c, 1); self.assertIn("uncertain-write pending: j/99/" + ref.attempt_id, r4["error"])
        c, st = self.run_wf("status", "j"); self.assertIn("uncertain-write", st["axes"]["execution"])
        # resolve → NOT-COMMITTED impossible (prev mismatch) → STILL-UNCERTAIN; CONFLICT needs a row
        c, rr = self.run_wf("resolve", "j", "99", "--attempt", ref.attempt_id); self.assertEqual(rr["outcome"], "STILL-UNCERTAIN")
        markers.delete(ref)
        # malformed marker
        bad = markers.MarkerRef(pdir, 5, markers.new_attempt_id())
        os.makedirs(pdir, exist_ok=True)
        with open(bad.path, "w") as fh:
            fh.write("{not json")
        c, r5 = self.intent(); self.assertEqual(c, 1); self.assertIn("marker unreadable", r5["error"])
        os.remove(bad.path)

    def test_T28_resolve_selection(self):
        self.new_job(); self.activate()
        c, r = self.run_wf("resolve", "j", "7"); self.assertEqual(r["outcome"], "ALREADY-CLEARED")
        pdir = paths.pending_dir(self.db, "j")
        a = markers.MarkerRef(pdir, 7, markers.new_attempt_id()); b = markers.MarkerRef(pdir, 7, markers.new_attempt_id())
        for ref in (a, b):
            markers.write_atomic(ref, markers.make("j", 7, ref.attempt_id, "x", "p", "committing", 1, "s", self.db, "c"))
        c, r = self.run_wf("resolve", "j", "7"); self.assertEqual((c, r["outcome"]), (2, "AMBIGUOUS"))
        self.assertEqual(len(r["candidates"]), 2)
        c, r = self.run_wf("resolve", "j", "7", "--attempt", a.attempt_id, "--disposition", "owner note")
        self.assertEqual(r["outcome"], "DISPOSITION"); self.assertEqual(markers.read(a)["rev"], 2)
        self.assertTrue(os.path.exists(a.path) and os.path.exists(b.path))
        c, r = self.run_wf("resolve", "j", "7", "--attempt", "zz"); self.assertEqual(c, 2)

    def test_T29_commit_timeout_give_up(self):
        """TC-2 in-process: SHARED holder outlasts held bound → WfCommitTimeout in [2.0, 2.2], marker aborting then gone."""
        self.new_job(); self.activate()
        holder = sqlite3.connect(self.db, isolation_level=None)
        holder.execute("BEGIN"); holder.execute("SELECT count(*) FROM events").fetchone()  # SHARED lock held
        t0 = time.monotonic()
        c, r = self.intent()
        el = time.monotonic() - t0
        holder.execute("COMMIT"); holder.close()
        self.assertEqual(r["code"], "commit-timeout", r)
        self.assertGreaterEqual(r["elapsed"], 2.0); self.assertLessEqual(r["elapsed"], 2.2)
        self.assertFalse(r["in_transaction"]); self.assertFalse(os.path.exists(r["marker_path"]))
        self.assertEqual(markers.list_markers(self.db, "j"), [])
        c, r = self.intent(); self.assertEqual(r["outcome"], "COMMITTED")

    def test_T29b_reads_wait_for_write_lock(self):
        """Regression (TC-5/TC-8 cycle 2): with busy_timeout 0, reads outside a reservation must wait
        boundedly for a held write lock, not fail with 'database is locked'."""
        self.new_job(); self.activate()
        import threading
        holder = sqlite3.connect(self.db, isolation_level=None, check_same_thread=False)
        holder.execute("BEGIN EXCLUSIVE")
        threading.Timer(0.5, lambda: holder.execute("ROLLBACK")).start()
        t0 = time.monotonic()
        st = commands.status(self.db, "j")
        self.assertGreaterEqual(time.monotonic() - t0, 0.4)
        self.assertEqual(st["job"], "j")
        holder.execute("BEGIN EXCLUSIVE")
        threading.Timer(0.5, lambda: holder.execute("ROLLBACK")).start()
        from wfcore import export
        note = export.refresh(self.db, "j")
        self.assertNotIn("error", note, note)
        time.sleep(0.1); holder.close()
        self.assertTrue(commands.verify(self.db, "j")["clean"])

    def test_T31b_export_refresh_failure_keeps_commit(self):
        """A committed write stays committed and is reported COMMITTED when the export refresh fails; the failure
        is reported in the result, verify flags the stale export, and the next write's refresh repairs it."""
        self.new_job(); self.activate()
        from wfcore import export
        out = paths.export_dir(self.db, "j")
        shutil.rmtree(out)
        with open(out, "w", encoding="utf-8") as fh:  # a file where the export directory must be: refresh fails
            fh.write("block")
        head_before = commands.status(self.db, "j")["head"]["seq"]
        c, r = self.intent(note="refresh-fails")
        self.assertEqual(c, 0, r); self.assertEqual(r["outcome"], "COMMITTED")
        self.assertIn("error", r["export"], r["export"])
        self.assertEqual(commands.status(self.db, "j")["head"]["seq"], head_before + 1)
        self.assertEqual(markers.list_markers(self.db, "j"), [])
        self.assertFalse(commands.verify(self.db, "j")["clean"])
        os.remove(out)
        c, r = self.intent(note="refresh-ok")
        self.assertEqual(c, 0, r); self.assertNotIn("error", r["export"], r["export"])
        self.assertTrue(commands.verify(self.db, "j")["clean"])

    def test_T30_commit_error_fault_and_resolve(self):
        """TC-3 in-process: commit-error:IOERR → uncertain, marker committing; resolve → NOT-COMMITTED;
        after-commit → resolve COMMITTED. Recovery (T30 in the test map) is out of scope; this is resolution only."""
        self.new_job(); self.activate()
        os.environ["WF_TEST_FAULTS"] = "commit-error:IOERR"
        c, r = self.intent()
        os.environ.pop("WF_TEST_FAULTS")
        self.assertEqual(r["code"], "commit-uncertain"); self.assertEqual(r["auto_resolve"]["outcome"], "SKIPPED")
        self.assertEqual(markers.read(markers.MarkerRef(paths.pending_dir(self.db, "j"), r["seq"], r["attempt_id"]))["state"], "committing")
        c, rr = self.run_wf("resolve", "j", str(r["seq"]), "--attempt", r["attempt_id"])
        self.assertEqual(rr["outcome"], "NOT-COMMITTED"); self.assertTrue(rr["in_transaction_at_decision"])
        os.environ["WF_TEST_FAULTS"] = "commit-error:after-commit"
        c, r = self.intent()
        os.environ.pop("WF_TEST_FAULTS")
        self.assertEqual(r["code"], "commit-uncertain")
        c, rr = self.run_wf("resolve", "j", str(r["seq"]), "--attempt", r["attempt_id"])
        self.assertEqual(rr["outcome"], "COMMITTED")
        c, v = self.run_wf("verify", "j"); self.assertTrue(v["clean"], v)

    def test_T31_faults_not_allowed_on_product_path(self):
        os.environ["WF_TEST_FAULTS"] = "delay-precommit:1"
        code, res = wf.run(["--db", os.path.join(REPO, "ledger", "never-created.db"), "status", "j"])
        os.environ.pop("WF_TEST_FAULTS")
        self.assertEqual((code, res["code"]), (2, "faults-not-allowed"))
        self.assertFalse(os.path.exists(os.path.join(REPO, "ledger", "never-created.db")))


class TManifest(unittest.TestCase):
    def test_TM1_four_field_line_malformed(self):
        p = os.path.join(TMP, "tm1.txt"); os.makedirs(TMP, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("a" * 64 + "  1  x.txt\n" + "b" * 64 + "  2  extra  y.txt\n")
        with self.assertRaises(manifest.ManifestMalformed) as cm:
            manifest.read(p)
        self.assertEqual(cm.exception.line_no, 2); self.assertIn("MALFORMED line 2", str(cm.exception))

    def test_TM2_three_field_lines_verified(self):
        d = os.path.join(TMP, "tm2-" + os.urandom(4).hex()); os.makedirs(d)  # fresh subdir (A4 §5), no rmtree
        with open(os.path.join(d, "f.txt"), "w") as fh:
            fh.write("hello")
        entries = manifest.build(d, ["f.txt", "missing.txt"])
        manifest.write(os.path.join(d, "m.txt"), entries)
        back = manifest.read(os.path.join(d, "m.txt"))
        self.assertEqual(back, entries); self.assertEqual(len(back), 1)
        with open(os.path.join(d, "m.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read().count("  "), 2)


class TConflict(Base):
    """FB1.4-A4 §3 checks C1–C5b: a second claimant is recorded as a conflict blocker inside the same
    reservation; both sessions (and every later generation of the role) are blocked; failure paths fail closed."""

    RESULTS = json.dumps({"criteria": [{"id": "AC1", "pass": True, "actual": "ok"}], "changed_paths": ["jobs/j/work/a.txt"],
                          "environment": "disposable local scope", "limits": "none", **DECLARATIONS})

    def sub_wf(self, *argv: str, role="builder", session="B1", faults: str | None = None, timeout=30):
        """A fresh wf process (its own connection, marker guard and fault env)."""
        import subprocess
        env = dict(os.environ)
        env.pop("WF_TEST_FAULTS", None)
        if faults:
            env["WF_TEST_FAULTS"] = faults
        cmd = [sys.executable, "-I", os.path.join(WF_DIR, "wf.py"), "--db", self.db, "--role", role, "--session", session,
               "--json", *argv]
        p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
        try:
            res = json.loads(p.stdout) if p.stdout.strip() else {}
        except ValueError:
            res = {"raw": p.stdout}
        log(f"{self.scratch}-sub-{len(os.listdir(LOGDIR))}", {"argv": argv, "code": p.returncode, "res": res, "stderr": p.stderr[-2000:]})
        return p.returncode, res

    def conflict(self, session="B2", generation="1", **kw):
        return self.run_wf("append", "j", "--type", "intent", "--content",
                           json.dumps({"path": "jobs/j/work/a.txt", "sha_before": "", "note": "second claimant"}),
                           "--generation", generation, session=session, **kw)

    def assert_blocked(self, session="B1", generation="1", role="builder", fresh=False):
        fn = self.sub_wf if fresh else self.run_wf
        c, r = fn("append", "j", "--type", "intent", "--content",
                  json.dumps({"path": "jobs/j/work/a.txt", "sha_before": "", "note": "after"}),
                  "--generation", generation, role=role, session=session)
        self.assertEqual(c, 1, r); self.assertIn("rejected(role-blocked)", r["error"], r)

    def test_C1_pause_holder_locked_out_then_blocked(self):
        """C1: with the conflict write paused pre-commit, S1 in a second process gets lock-timeout within
        T_ACQ_BOUND; after the pause the conflict row is committed and S1 → role-blocked."""
        import threading
        self.new_job(); self.activate(); self.intent()
        os.environ["WF_TEST_FAULTS"] = "delay-precommit:7000"  # longer than T_ACQ_BOUND: S1 must time out
        out: dict = {}
        th = threading.Thread(target=lambda: out.update(zip(("c", "r"), self.conflict())))
        th.start(); time.sleep(0.4)
        t0 = time.monotonic()
        c, r = self.sub_wf("append", "j", "--type", "intent", "--content", '{"path":"p","sha_before":"","note":"n"}')
        el = time.monotonic() - t0
        th.join(); os.environ.pop("WF_TEST_FAULTS", None)
        self.assertEqual(r["code"], "lock-timeout", r); self.assertLessEqual(el, txn.T_ACQ_BOUND + 2.0)
        self.assertEqual((out["c"], out["r"]["conflict_outcome"]), (1, "COMMITTED"), out["r"])
        self.assertEqual(commands.status(self.db, "j")["head"]["seq"], out["r"]["conflict_seq"])
        self.assert_blocked(fresh=True)
        self.assertTrue(commands.verify(self.db, "j")["clean"])

    def test_C2a_commit_timeout_conflict_pending_then_resolve(self):
        """C2a: a SHARED reader forces the conflict COMMIT busy past T_HELD_BOUND → marker conflict-pending, no row;
        fresh process: S1 append and accept → uncertain-write-pending; resolve re-records; then S1 → role-blocked."""
        self.new_job(); self.activate(); self.intent()
        holder = sqlite3.connect(self.db, isolation_level=None)
        holder.execute("BEGIN"); holder.execute("SELECT count(*) FROM events").fetchone()
        c, r = self.conflict()
        holder.execute("COMMIT"); holder.close()
        self.assertEqual((c, r["code"], r["conflict_outcome"]), (1, "commit-timeout", "conflict-pending"), r)
        ms = markers.list_markers(self.db, "j"); self.assertEqual(len(ms), 1); ref = ms[0]
        self.assertEqual(ref.path, r["marker_path"])
        m = markers.read(ref); self.assertEqual((m["state"], m["rev"]), ("conflict-pending", 2)); self.assertIn("conflict", m)
        chk = sqlite3.connect(self.db)
        try:
            self.assertIsNone(dbm.row_at(chk, "j", ref.seq))
        finally:
            chk.close()
        c, rr = self.sub_wf("append", "j", "--type", "intent", "--content", '{"path":"p","sha_before":"","note":"n"}')
        self.assertEqual(rr["code"], "uncertain-write-pending", rr)
        c, rr = self.sub_wf("accept", "j", "--results", self.RESULTS, role="reviewer", session="R1")
        self.assertEqual(rr["code"], "uncertain-write-pending", rr)
        c, rr = self.sub_wf("resolve", "j", str(ref.seq), "--attempt", ref.attempt_id)
        self.assertEqual((c, rr["outcome"]), (0, "NOT-COMMITTED"), rr); self.assertEqual(rr["re_recorded"]["self"], m["self"])
        self.assertFalse(os.path.exists(ref.path)); self.assertEqual(markers.list_markers(self.db, "j"), [])
        self.assertEqual(commands.status(self.db, "j")["head"]["self"], m["self"])
        self.assert_blocked(fresh=True); self.assert_blocked(session="B2")
        self.assertTrue(commands.verify(self.db, "j")["clean"])

    def test_C2b_kill_before_commit_then_resolve(self):
        """C2b: kill-before-commit during the conflict write → committing marker; fresh process refuses every
        write uncertain-write-pending; resolve NOT-COMMITTED re-records; both roles blocked afterwards."""
        self.new_job(); self.activate(); self.intent()
        c, r = self.sub_wf("append", "j", "--type", "intent", "--content", '{"path":"p","sha_before":"","note":"x"}',
                           session="B2", faults="kill-before-commit")
        self.assertEqual(c, 3, r)
        ms = markers.list_markers(self.db, "j"); self.assertEqual(len(ms), 1)
        m = markers.read(ms[0]); self.assertEqual(m["state"], "committing"); self.assertIn("conflict", m)
        c, rr = self.sub_wf("append", "j", "--type", "intent", "--content", '{"path":"p","sha_before":"","note":"n"}')
        self.assertEqual(rr["code"], "uncertain-write-pending", rr)
        c, rr = self.sub_wf("append", "j", "--type", "blocker", "--content", '{"reason":"r"}', role="coordinator", session="C1")
        self.assertEqual(rr["code"], "uncertain-write-pending", rr)
        c, rr = self.sub_wf("resolve", "j", str(ms[0].seq), "--attempt", ms[0].attempt_id)
        self.assertEqual((c, rr["outcome"]), (0, "NOT-COMMITTED"), rr); self.assertIn("re_recorded", rr)
        self.assertEqual(markers.list_markers(self.db, "j"), [])
        self.assert_blocked(fresh=True); self.assert_blocked(session="B2", fresh=True)
        self.assertTrue(commands.verify(self.db, "j")["clean"])

    def test_C3_later_generations_all_blocked(self):
        """C3: after a committed conflict, S1/S2 at G and G+1 and S3 at G+2 → role-blocked; other roles unaffected;
        accept → role-blocked."""
        self.new_job(); self.activate(); self.intent()
        c, r = self.conflict(); self.assertEqual((c, r["conflict_outcome"]), (1, "COMMITTED"), r)
        for s, g in (("B1", "1"), ("B1", "2"), ("B2", "1"), ("B2", "2"), ("B3", "3")):
            self.assert_blocked(session=s, generation=g)
        c, r = self.run_wf("append", "j", "--type", "blocker", "--content", '{"reason":"coord note"}', role="coordinator", session="C1")
        self.assertEqual(r["outcome"], "COMMITTED", r)
        c, r = self.run_wf("accept", "j", "--results", self.RESULTS, role="reviewer", session="R1")
        self.assertEqual(c, 1); self.assertIn("rejected(role-blocked)", r["error"], r)
        c, r = self.run_wf("status", "j"); self.assertEqual(r["axes"]["execution"], "blocked"); self.assertEqual(r["axes"]["blocked_roles"], ["builder"])
        c, r = self.run_wf("digest", "j"); self.assertEqual(r["axes"]["execution"], "blocked")

    def test_C4_existing_rules_with_conflict_row(self):
        """C4: repeat → DUPLICATE-IGNORED (no second row, exit 1); append blocker reason claimant-conflict →
        reserved-reason; close refused, succeeds after abandon; verify clean; checkpoint triple unchanged by resolve."""
        self.new_job(); self.activate(); self.intent()
        c, r = self.conflict(msg_id="X1"); self.assertEqual((c, r["conflict_outcome"]), (1, "COMMITTED"), r)
        n = commands.status(self.db, "j")["events"]
        c, r = self.conflict(msg_id="X1")
        self.assertEqual((c, r["conflict_outcome"]), (1, "DUPLICATE-IGNORED"), r); self.assertEqual(commands.status(self.db, "j")["events"], n)
        c, r = self.run_wf("append", "j", "--type", "blocker", "--content", '{"reason":"claimant-conflict"}', role="coordinator", session="C1")
        self.assertIn("rejected(reserved-reason)", r["error"], r)
        c, r = self.run_wf("append", "j", "--type", "blocker", "--content", '{"reason":"ok"}', role="coordinator", session="C1", msg_id="claimant-conflict:zz")
        self.assertIn("rejected(reserved-reason)", r["error"], r)
        c, r = self.run_wf("close", "j", "--intervention", "none", role="coordinator", session="C1")
        self.assertEqual(r["code"], "refused", r)
        c, r = self.run_wf("verify", "j"); self.assertTrue(r["clean"], r)
        c, cp1 = self.run_wf("checkpoint", "j"); self.assertEqual(c, 0)
        c, rr = self.run_wf("resolve", "j", str(n), "--attempt", "0" * 32); self.assertEqual(rr["outcome"], "ALREADY-CLEARED")
        c, cp2 = self.run_wf("checkpoint", "j")
        self.assertEqual({k: cp1[k] for k in ("head", "export_sha256", "contract_sha256") if k in cp1},
                         {k: cp2[k] for k in ("head", "export_sha256", "contract_sha256") if k in cp2})
        c, r = self.run_wf("job", "abandon", "j", "--reason", "conflict unresolved"); self.assertEqual(c, 0, r)
        c, r = self.run_wf("close", "j", "--intervention", "owner abandoned", role="coordinator", session="C1")
        self.assertEqual(r["outcome"], "COMMITTED", r)
        c, r = self.run_wf("verify", "j"); self.assertTrue(r["clean"], r)

    def test_C5_marker_unwritable_commits_marker_less(self):
        """C5: conflict marker write fails → COMMITTED-NO-MARKER, no marker, row present; fault removed; fresh
        process as S1 same gen and G+1 → role-blocked; accept → role-blocked; status blocked; verify clean."""
        self.new_job(); self.activate(); self.intent()
        os.environ["WF_TEST_FAULTS"] = "marker-unwritable"
        c, r = self.conflict()
        os.environ.pop("WF_TEST_FAULTS", None)
        self.assertEqual((c, r["conflict_outcome"]), (1, "COMMITTED-NO-MARKER"), r)
        self.assertEqual(markers.list_markers(self.db, "j"), [])
        self.assertEqual(commands.status(self.db, "j")["head"]["self"], r["conflict_self"])
        self.assert_blocked(fresh=True); self.assert_blocked(generation="2", fresh=True)
        c, rr = self.sub_wf("accept", "j", "--results", self.RESULTS, role="reviewer", session="R1")
        self.assertEqual(c, 1); self.assertIn("rejected(role-blocked)", rr["error"], rr)
        c, rr = self.sub_wf("status", "j"); self.assertEqual(rr["axes"]["execution"], "blocked")
        c, rr = self.sub_wf("verify", "j"); self.assertTrue(rr["clean"], rr)
        # an ordinary (non-conflict) row keeps the marker-before-COMMIT rule: marker-unwritable refuses it
        os.environ["WF_TEST_FAULTS"] = "marker-unwritable"
        c, rr = self.run_wf("append", "j", "--type", "blocker", "--content", '{"reason":"r"}', role="coordinator", session="C1")
        os.environ.pop("WF_TEST_FAULTS", None)
        self.assertEqual(rr["code"], "refused"); self.assertIn("marker-unwritable", rr["error"])

    def test_C5b_BOUNDARY_marker_unwritable_and_commit_fails(self):
        """C5b BOUNDARY (A4 §3/§13a, accepted by the owner; NOT a T01 pass): marker unwritable AND COMMIT fails →
        exit 1 conflict-unrecorded, no row, no marker; a fresh S1 append succeeds. Asserts the honest report."""
        self.new_job(); self.activate(); self.intent()
        n = commands.status(self.db, "j")["events"]
        os.environ["WF_TEST_FAULTS"] = "marker-unwritable,commit-error:IOERR"
        c, r = self.conflict()
        os.environ.pop("WF_TEST_FAULTS", None)
        self.assertEqual((c, r["code"], r["conflict_outcome"]), (1, "rejected", "conflict-unrecorded"), r)
        self.assertIn("B1", r["error"]); self.assertIn("B2", r["error"])
        self.assertEqual(markers.list_markers(self.db, "j"), [])
        self.assertEqual(commands.status(self.db, "j")["events"], n)
        c, rr = self.sub_wf("append", "j", "--type", "intent", "--content", '{"path":"p","sha_before":"","note":"n"}')
        self.assertEqual((c, rr["outcome"]), (0, "COMMITTED"), rr)  # BOUNDARY: holder continues; recorded, not enforced
        log(f"{self.scratch}-BOUNDARY", {"label": "BOUNDARY", "t01": "not satisfied for this attempt (A4 §13a)", "res": r})


if __name__ == "__main__":
    unittest.main()
