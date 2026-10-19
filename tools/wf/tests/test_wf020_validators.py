"""WF-020 (Lazy): direct unit tests of schema validators whose refusals cannot be reached through the CLI at 2dedc94
(the wiring builds the content itself, or an earlier check refuses first), so no CLI test could cover them. They pin the
validators' current contract: validate.validate_content / validate.build_row early checks, and the reconcile.py
validators. Pure functions on dicts; no ledger is written."""
from __future__ import annotations

import unittest

import test_wf  # noqa: F401  (puts tools/wf on sys.path, as the other tests do)
from wfcore import reconcile, validate
from wfcore.db import WfRejected

SHA = "a" * 64
CONTRACT = {"write_scope": ["jobs/j/work/**"]}


def raises(testcase, fn, text, *a, **kw):
    with testcase.assertRaises(WfRejected) as cm:
        fn(*a, **kw)
    testcase.assertIn(text, str(cm.exception))
    return cm.exception


class ValidateContent(unittest.TestCase):
    def test_grant_state_must_be_proposed_or_activated(self):
        c = {"grant_id": "G", "state": "revoked"}
        raises(self, validate.validate_content, "grant state 'revoked' not in this slice", "grant", c, CONTRACT, None)

    def test_activated_grant_block_hash_must_be_64_hex(self):
        c = {"grant_id": "G", "state": "activated", "actions": ["write"], "resources": ["r"], "exclusions": [],
             "ars": {"roles": {}}, "delegation": {}, "expiry": "2999-01-01T00:00:00Z", "revocation_rule": "owner",
             "block_hash": "XYZ"}
        raises(self, validate.validate_content, "block_hash must be 64 hex", "grant", c, CONTRACT, None)

    def test_handoff_action_must_be_in_the_activated_grant(self):
        c = {"to_role": "builder", "write_scope": [], "actions": ["delete"], "note": "n"}
        grant_state = {"state": "activated", "actions": ["write"]}
        raises(self, validate.validate_content, "handoff action 'delete' is not in the activated grant", "handoff", c,
               CONTRACT, grant_state)
        raises(self, validate.validate_content, "handoff action 'delete' is not in the activated grant", "handoff", c,
               CONTRACT, {"state": "proposed", "actions": ["delete"]})

    def test_accept_decision_and_close_basis_values(self):
        raises(self, validate.validate_content, "decision must be accepted|rejected", "accept",
               {"decision": "maybe"}, CONTRACT, None)
        raises(self, validate.validate_content, "close basis must be accepted|abandoned", "close",
               {"intervention": "x", "basis": "bored"}, CONTRACT, None)

    def test_unknown_event_type(self):
        raises(self, validate.validate_content, "unknown event type 'wat'", "wat", {}, CONTRACT, None)


class BuildRowEarlyChecks(unittest.TestCase):
    """The checks before the ledger is consulted (conn is never used on these paths)."""

    def build(self, **kw):
        args = dict(etype="intent", msg_id="m", author_role="builder", session_id="B1", generation=1, reply_to=None,
                    grant_ref=None, content={"path": "p", "sha_before": "", "note": "n"}, contract_sha=SHA,
                    contract_content=CONTRACT)
        args.update(kw)
        head_seq = args.pop("head_seq", 3)
        return validate.build_row(None, "j", head_seq, "h" * 64, **args)

    def test_unknown_event_type_and_non_object_content(self):
        raises(self, self.build, "unknown event type 'wat'", etype="wat")
        raises(self, self.build, "content must be a JSON object", content=[1])

    def test_non_contract_event_on_an_empty_job_has_no_contract(self):
        raises(self, self.build, "job has no contract", head_seq=0)
        raises(self, self.build, "job has no contract", contract_sha=None)

    def test_contract_event_without_a_pin_is_refused(self):
        raises(self, self.build, "contract pin missing", etype="contract", author_role="coordinator", head_seq=0,
               contract_sha=None, contract_content=None, content={"contract": {}})


def good_report(**change):
    r = {"role": reconcile.REPORT_ROLE, "session": "B1", "generation": 1, "through_seq": 3, "classification": [],
         "exceptions": [], "gate_log": None, "problems": [], "limitations": []}
    r.update(change)
    return r


class ReconcileContent(unittest.TestCase):
    def check(self, etype, content, text):
        raises(self, reconcile.validate_content, text, etype, content, CONTRACT, None)

    def test_reconcile_report_fields(self):
        reconcile.validate_content("reconcile-report", good_report(), CONTRACT, None)    # the base case is valid
        cases = [
            (dict(session=""), "session must be a non-empty string or null"),
            (dict(generation="1"), "generation must be an integer or null"),
            (dict(through_seq=0), "through_seq must be an integer >= 1"),
            (dict(exceptions=[{"id": "X1", "path": "a", "class": "matches-neither"},
                              {"id": "X1", "path": "b", "class": "matches-neither"}]), "exception ids must be distinct"),
            (dict(problems="x"), "problems and limitations must be lists"),
            (dict(limitations={}), "problems and limitations must be lists"),
        ]
        for change, text in cases:
            with self.subTest(change=change):
                self.check("reconcile-report", good_report(**change), text)

    def test_content_that_is_not_an_object_and_unknown_types(self):
        self.check("reconcile-report", [1], "content must be a JSON object")
        self.check("append", {}, "'append' is not a reconcile event type")

    def test_ack_reconcile_fields(self):
        ok = {"role": "builder", "session": "B1", "report_seq": 4, "exceptions": []}
        reconcile.validate_content("ack-reconcile", ok, CONTRACT, None)
        self.check("ack-reconcile", dict(ok, role=""), "ack-reconcile role and session must be non-empty strings")
        self.check("ack-reconcile", dict(ok, session=""), "ack-reconcile role and session must be non-empty strings")
        self.check("ack-reconcile", dict(ok, report_seq=0), "report_seq must be an integer >= 1")
        self.check("ack-reconcile", dict(ok, exceptions=[1]), "exceptions must be a list of exception ids")


class ReconcileRecoveryValidators(unittest.TestCase):
    ROW = {"seq": 5, "self": "s" * 64, "type": "intent", "content": {}}

    def test_ack_shape_and_identity(self):
        for content, text in (([1], "content must be a JSON object"),
                              ({"role": "reviewer", "session": "B1"}, "must name the author's own role and session"),
                              ({"role": "builder", "session": "other"}, "must name the author's own role and session")):
            with self.subTest(content=content):
                raises(self, reconcile.validate_ack, text, [], role="builder", session="B1", binding_session=None,
                       content=content)

    def test_recover_begin_shape(self):
        good = {"reason": "r", "owner_turn": "turn for j", "exceptions": [],
                "chain_head": {"seq": 5, "self": "s" * 64}}
        for content, text in (([1], "content must be a JSON object"),
                              (dict(good, chain_head={"seq": "5", "self": "s"}), "chain_head must be {seq, self}"),
                              (dict(good, chain_head={"seq": 5}), "chain_head must be {seq, self}")):
            with self.subTest(content=content):
                raises(self, reconcile.validate_recover_begin, text, [self.ROW], job="j", content=content)

    def test_exception_ack_and_recovery_closed_shape(self):
        raises(self, reconcile.validate_exception_ack, "exception-acknowledged needs opened_seq (int), id, class and detail",
               [], job="j", content={"opened_seq": "1", "id": "X1", "class": "c", "detail": "d"})
        raises(self, reconcile.validate_exception_ack, "exception-acknowledged needs opened_seq (int), id, class and detail",
               [], job="j", content=[1])
        raises(self, reconcile.validate_recover_close, "recovery-closed needs opened_seq (int)", [], job="j",
               content={"opened_seq": "1"})
        raises(self, reconcile.validate_recover_close, "recovery-closed needs opened_seq (int)", [], job="j",
               content=[1])
