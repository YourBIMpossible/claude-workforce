"""WF-020 (Lazy): regression tests for refusals/rejections in wfcore/commands.py and validate.py that no earlier test
reached (from-lazy WF-020 GAP-LIST.md). Written at 2dedc94; updated for fix1/integration dfd6e85 (WF-020 review F1,
WF-039 W39-S2/S4: the F11 pin now asserts FIX1 E6). Where a path is surprising the docstring says so (FINDINGS.md
carries the disposition).

Runs inside the test_wf.py harness (fresh scratch ledger per test under WF_TEST_TMP)."""
from __future__ import annotations

import json
import os

import test_wf
from test_wf import CONTRACT, DECLARATIONS, Base

from wfcore import db as dbm

OWNER_J = "owner turn j"


class Fixture(Base):
    """Ledger with job j and an activated grant; helpers return (code, result)."""

    def setUp(self) -> None:
        super().setUp()
        self.new_job()
        self.activate()

    def coord(self, *argv: str):
        return self.run_wf(*argv, role="coordinator", session="C1")

    def reviewer(self, *argv: str):
        return self.run_wf(*argv, role="reviewer", session="R1")

    def head_seq(self, job: str = "j") -> int:
        c, r = self.coord("verify", job)
        self.assertEqual(c, 0, r)
        return r["jobs"][job]["head"]["seq"]

    def refused(self, code_res, exit_code: int, code: str, text: str):
        c, r = code_res
        self.assertEqual((c, r["code"]), (exit_code, code), r)
        self.assertIn(text, r["error"])
        return r


class JobNewInputs(Base):
    def test_bad_job_id_is_malformed(self):
        c, r = self.run_wf("job", "new", "bad id!", "--contract", self.cpath, role="coordinator", session="C1")
        self.assertEqual((c, r["code"]), (1, "rejected"))
        self.assertIn("rejected(malformed): job id 'bad id!' must match", r["error"])

    def test_contract_argument_that_is_neither_file_nor_json_is_usage(self):
        for arg in ("not json {", os.path.join(self.dir, "missing.json")):
            with self.subTest(arg=arg):
                c, r = self.run_wf("job", "new", "j", "--contract", arg, role="coordinator", session="C1")
                self.assertEqual((c, r["code"]), (2, "usage"))
                self.assertIn("not a file and not JSON", r["error"])
        self.assertFalse(os.path.exists(self.db))

    def test_contract_json_that_is_not_an_object(self):
        c, r = self.run_wf("job", "new", "j", "--contract", "[1,2]", role="coordinator", session="C1")
        self.assertEqual((c, r["code"]), (1, "rejected"))
        self.assertIn("contract must be a JSON object", r["error"])

    def _bad_contract(self, text: str, **change):
        c = json.loads(json.dumps(CONTRACT))
        c.update(change)
        code, r = self.run_wf("job", "new", "j2", "--contract", json.dumps(c), role="coordinator", session="C1")
        self.assertEqual((code, r["code"]), (1, "rejected"), r)
        self.assertIn(text, r["error"])

    def test_contract_field_validation(self):
        cases = [
            ("field 'limits' has wrong type", dict(limits=5)),
            ("owner_request.quote must be a non-empty verbatim string",
             dict(owner_request={"quote": " ", "source_turn": "t"})),
            ("environment must be 'disposable local scope'", dict(environment="production")),
            ("at least one acceptance criterion is required", dict(acceptance_criteria=[])),
            ("each acceptance criterion needs string id, check (runnable) and expected",
             dict(acceptance_criteria=["x"])),
            ("duplicate criterion id 'A'", dict(acceptance_criteria=[{"id": "A", "check": "c", "expected": "e"},
                                                                      {"id": "A", "check": "c", "expected": "e"}])),
            ("baseline.manifest must be a list",
             dict(baseline={"manifest": "x", "git_revision": None, "uncommitted_state": None})),
            ("allowed_tools: unknown role 'wizard' or tools not a list", dict(allowed_tools={"wizard": ["Write"]})),
            ("read_scope entries must be strings", dict(read_scope=[1])),
        ]
        for text, change in cases:
            with self.subTest(text=text):
                self._bad_contract(text, **change)


class MissingJobInExistingLedger(Fixture):
    """Job k does not exist while the ledger does. The refusal wording differs by command (inventory rows 13-14, 55-58)."""

    def test_read_commands_say_job_not_found(self):
        for argv in (("status", "k"), ("digest", "k"), ("observe", "k"), ("export", "k"),
                     ("grant-check", "k", "--check-role", "builder", "--action", "write", "--path", "jobs/k/work/a")):
            with self.subTest(argv=argv):
                self.refused(self.coord(*argv), 1, "refused", "job k not found")

    def test_writers_say_job_has_no_contract(self):
        for argv in (("cancel", "k", "--reason", "r", "--turn", "owner turn k"),
                     ("grant", "revoke", "k", "--reason", "r", "--turn", "owner turn k"),
                     ("grant", "expire", "k", "--reason", "r", "--turn", "owner turn k"),
                     ("recover", "begin", "k", "--reason", "r", "--turn", "owner turn k"),
                     ("recover", "decide", "k", "--decision", "resume", "--turn", "owner turn k"),
                     ("recover", "close", "k", "--turn", "owner turn k"),
                     ("bind", "k", "--bind-role", "builder", "--bind-session", "S"),
                     ("reconcile", "k")):
            with self.subTest(argv=argv):
                self.refused(self.coord(*argv), 1, "refused", "job k has no contract")

    def test_recover_reconcile_says_no_recovery_open_not_no_contract(self):
        self.refused(self.coord("recover", "reconcile", "k", "--turn", "owner turn k"), 1, "rejected",
                     "no recovery is open for this job")

    def test_abandon_close_backup_and_verify_on_a_missing_job(self):
        self.refused(self.coord("job", "abandon", "k", "--reason", "r"), 1, "refused", "job k not found")
        self.refused(self.coord("close", "k", "--intervention", "x"), 1, "refused", "close refused: verify not clean")
        self.refused(self.coord("backup", "k", "--to", os.path.join(self.dir, "b.db")), 1, "refused",
                     "job k is not in the ledger; nothing written")
        c, r = self.coord("verify", "k")
        # verify exits 0 even when unclean (RESTORE-RUNBOOK.md: read `clean`, not the exit code)
        self.assertEqual(c, 0, r)
        self.assertFalse(r["clean"])
        self.assertEqual(r["jobs"]["k"]["problems"], ["no contract row"])

    def test_resolve_of_a_seq_with_no_marker_is_already_cleared(self):
        c, r = self.coord("resolve", "j", "99")
        self.assertEqual(c, 0, r)
        self.assertEqual(r["outcome"], "ALREADY-CLEARED")

    def _all_rows(self) -> tuple[int, list[str]]:
        conn = dbm.connect(self.db, create=False)
        try:
            return conn.execute("SELECT count(*) FROM events").fetchone()[0], dbm.jobs(conn)
        finally:
            conn.close()

    def test_no_refusal_changed_the_ledger(self):
        """W39-S4: the refusals on job k write nothing anywhere: job j's head, the ledger's total row count and its job
        list are unchanged, and job k still does not exist."""
        before, rows_before = self.head_seq(), self._all_rows()
        self.refused(self.coord("cancel", "k", "--reason", "r", "--turn", "owner turn k"), 1, "refused",
                     "job k has no contract")
        self.refused(self.coord("bind", "k", "--bind-role", "builder", "--bind-session", "S"), 1, "refused",
                     "job k has no contract")
        self.assertEqual(self.head_seq(), before)
        self.assertEqual(self._all_rows(), rows_before)
        self.assertEqual(rows_before[1], ["j"])
        self.refused(self.coord("status", "k"), 1, "refused", "job k not found")


class JobNewOnExistingJob(Fixture):
    def test_second_job_new_is_refused_as_already_exists(self):
        """FIX1 E6 (W39-S2: at 2dedc94 this pinned `rejected(malformed): contract event must be the first event`, which
        shadowed the existing-job refusal): a second `job new j` is `refused` with `job j already exists`. Exit 1,
        nothing appended."""
        before = self.head_seq()
        r = self.refused(self.coord("job", "new", "j", "--contract", self.cpath), 1, "refused", "job j already exists")
        self.assertEqual(r["error"], "job j already exists")
        self.assertEqual(self.head_seq(), before)


class GrantInputs(Fixture):
    def test_activate_needs_quote_and_a_hex_hash_of_8_chars(self):
        self.refused(self.coord("grant", "activate", "j", "--owner-hash", "abcdef12", "--quote", " ", "--turn", "t"),
                     1, "rejected", "rejected(no-provenance): --quote")
        for bad in ("abc", "zzzzzzzz"):
            with self.subTest(hash=bad):
                self.refused(self.coord("grant", "activate", "j", "--owner-hash", bad, "--quote", "q", "--turn", "t"),
                             1, "rejected", "owner hash must be at least 8 hex characters")

    def test_propose_rejects_a_grant_that_is_not_an_object(self):
        self.refused(self.coord("grant", "propose", "j", "--grant", "[1]"), 1, "rejected",
                     "grant must be a JSON object")

    def test_cancel_turn_must_name_the_job(self):
        self.refused(self.coord("cancel", "j", "--reason", "r", "--turn", "not naming"), 1, "rejected",
                     "owner_turn must be a non-empty owner turn that names the job 'j'")


class AppendValidation(Fixture):
    def test_unknown_type_is_usage(self):
        c, r = self.run_wf("append", "j", "--type", "nonsense", "--content", "{}")
        self.assertEqual((c, r["code"]), (2, "usage"))
        self.assertIn("--type must be one of intent, receipt, blocker", r["error"])
        c, r = self.run_wf("append", "j", "--type", "recovery-opened", "--content", "{}", role="coordinator",
                           session="C1")
        self.assertEqual((c, r["code"]), (2, "usage"))

    def test_content_that_is_not_json_is_usage_and_not_an_object_is_malformed(self):
        c, r = self.run_wf("append", "j", "--type", "intent", "--content", "zzz{")
        self.assertEqual((c, r["code"]), (2, "usage"))
        self.assertIn("not a file and not JSON", r["error"])
        for t, role, ses in (("intent", "builder", "B1"), ("stop-confirmed", "coordinator", "C1"),
                             ("ack-reconcile", "builder", "B1")):
            with self.subTest(type=t):
                c, r = self.run_wf("append", "j", "--type", t, "--content", "[1]", role=role, session=ses)
                self.assertEqual((c, r["code"]), (1, "rejected"), r)
                self.assertIn("content must be a JSON object", r["error"])

    def test_payload_field_validation(self):
        cases = [
            ("intent", {}, "content missing 'path'"),
            ("intent", {"path": 5, "sha_before": "", "note": "n"}, "content field 'path' has wrong type"),
            ("intent", {"path": "", "sha_before": "", "note": "n"}, "content field 'path' is empty"),
        ]
        for t, body, text in cases:
            with self.subTest(body=body):
                c, r = self.run_wf("append", "j", "--type", t, "--content", json.dumps(body))
                self.assertEqual((c, r["code"]), (1, "rejected"), r)
                self.assertIn(text, r["error"])

    def test_handoff_and_delivered_payloads(self):
        c, r = self.coord("append", "j", "--type", "handoff", "--content",
                          json.dumps({"to_role": "wizard", "actions": ["write"]}))
        self.assertEqual((c, r["code"]), (1, "rejected"), r)
        self.assertIn("handoff to unknown role 'wizard'", r["error"])
        c, r = self.run_wf("append", "j", "--type", "delivered", "--content", json.dumps({"claims": ["x"]}))
        self.assertIn("each claim needs a 'claim' string", r["error"])
        c, r = self.run_wf("append", "j", "--type", "delivered", "--content",
                           json.dumps({"claims": [{"claim": "c", "evidence": []}]}))
        self.assertIn("claim 'c' has no evidence refs", r["error"])

    def test_generation_and_role_validation(self):
        body = json.dumps({"path": "jobs/j/work/a.txt", "sha_before": "", "note": "n"})
        code, res = test_wf.wf.run(["--db", self.db, "--role", "builder", "--session", "B1", "--generation", "0",
                                    "append", "j", "--type", "intent", "--content", body])
        self.assertEqual((code, res["code"]), (1, "rejected"), res)
        self.assertIn("generation must be an integer >= 1", res["error"])
        code, res = test_wf.wf.run(["--db", self.db, "--role", "wizard", "--session", "B1", "append", "j", "--type",
                                    "intent", "--content", body])
        self.assertEqual((code, res["code"]), (1, "rejected"), res)
        self.assertIn("unknown role", res["error"])


class AcceptCloseBindRecoverInputs(Fixture):
    def test_accept_inputs(self):
        self.refused(self.run_wf("accept", "j", "--results", "{}", role="builder", session="B1"), 1, "rejected",
                     "accept may not be written by role builder")
        self.refused(self.reviewer("accept", "j", "--results", "{}"), 1, "refused",
                     "object with 'criteria' list required")
        ok = {"environment": "e", "limits": "l", "changed_paths": [],
              "criteria": [{"id": "AC1", "passed": True, "evidence": "x"}], **DECLARATIONS}
        self.refused(self.reviewer("accept", "j", "--results", json.dumps({k: v for k, v in ok.items() if k != "limits"})),
                     1, "refused", "malformed results: missing 'limits'")
        self.refused(self.reviewer("accept", "j", "--results", json.dumps(ok)), 1, "refused", "nothing delivered yet")
        c, r = self.reviewer("accept", "j", "--results", "zzz")
        self.assertEqual((c, r["code"]), (2, "usage"))

    def test_close_refuses_while_acceptance_is_pending(self):
        self.refused(self.coord("close", "j", "--intervention", "x"), 1, "refused",
                     "acceptance is 'pending' and job not abandoned")

    def test_bind_inputs(self):
        self.refused(self.run_wf("bind", "j", "--bind-role", "builder", "--bind-session", "S9", role="builder",
                                 session="B1"), 1, "rejected", "bind may not be written by role builder")
        c, r = self.coord("bind", "j", "--bind-role", "builder", "--bind-session", " ")
        self.assertEqual((c, r["code"]), (2, "usage"))
        self.assertIn("--bind-session is required", r["error"])

    def test_ack_reconcile_exceptions_must_be_a_json_list_of_ids(self):
        for bad in ("{}", "[1]"):
            with self.subTest(exceptions=bad):
                self.refused(self.run_wf("ack-reconcile", "j", "--report", "1", "--exceptions", bad), 1, "rejected",
                             "--exceptions must be a JSON list of exception ids")

    def test_recover_input_checks(self):
        c, r = self.coord("recover", "begin", "j", "--reason", " ", "--turn", "t")
        self.assertEqual((c, r["code"]), (2, "usage"))
        self.assertIn("--reason is required", r["error"])
        self.refused(self.coord("recover", "begin", "j", "--reason", "r", "--turn", " "), 1, "rejected",
                     "rejected(no-provenance): --turn")
        self.refused(self.coord("recover", "reconcile", "j", "--turn", " "), 1, "rejected",
                     "rejected(no-provenance): --turn")
        self.refused(self.run_wf("recover", "close", "j", "--turn", OWNER_J, role="builder", session="B1"), 1,
                     "rejected", "recover close may not be written by role builder")
        c, r = self.coord("recover", "decide", "j", "--decision", "resume", "--turn", OWNER_J, "--unresolved",
                          "nodisposition")
        self.assertEqual((c, r["code"]), (2, "usage"))
        self.assertIn("--unresolved expects PATH=DISPOSITION", r["error"])

    def test_log_intervention_minutes(self):
        c, r = self.coord("log-intervention", "j", "--label", "l", "--necessity", "unknown", "--minutes", "abc")
        self.assertEqual((c, r["code"]), (2, "usage"))
        self.refused(self.coord("log-intervention", "j", "--label", "l", "--necessity", "unknown", "--minutes", "-1"),
                     1, "rejected", "intervention minutes must be an integer >= 0")
