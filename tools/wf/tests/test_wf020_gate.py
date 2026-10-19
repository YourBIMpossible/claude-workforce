"""WF-020 (Lazy): gate (gatecore / gatelog / gate print-install) paths that no earlier test exercised
(from-lazy WF-020 GAP-LIST.md section 5). Written at 2dedc94; updated for fix1/integration dfd6e85 (WF-020 review F1,
WF-039 W39-S2: the F12 pin now asserts FIX1 I6). Fixture ledgers are built with the real CLI via
test_gate_decide._Gate; `decide` is called in-process; no hook is registered or run here (the hook source is only read)."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import unittest

import test_gate_decide as G
from test_gate_decide import GATE_GRANT, ROLES, _Gate
from test_wf import WF_DIR

from wfcore import gatecore, gatelog


class GrantCheckReasonsThroughDecide(_Gate):
    """gatecore.py:425 maps every grant.check reason to a deny; five of them had no decide-level test."""

    def _decide_builder(self) -> dict:
        return self.d(self.p("jobs/j/work/a.txt"))

    def test_action_not_granted(self):
        self.grant_obj = dict(GATE_GRANT, actions=["read"])
        self.setUp_grant()
        self.explicit()
        r = self._decide_builder()
        self.assertEqual((r["decision"], r["reason"]), ("deny", "action-not-granted"), r)

    def test_path_outside_role_is_unreachable_through_bind_because_bind_refuses_first(self):
        """grant.check's `path-outside-role` (gatecore.py:425) cannot be produced from a ledger built with the CLI:
        `wf bind` already refuses a binding whose default path set is not inside the role's ARS paths, so the gate's
        second line of defence is never exercised end to end. Defence in depth, NOT A DEFECT; the decide-level reason
        stays covered only by the grant-check corpus (test_grant_check_corpus)."""
        roles = dict(ROLES, builder=dict(ROLES["builder"], paths=["jobs/j/work/sub/**"]))
        self.grant_obj = dict(GATE_GRANT, ars=dict(GATE_GRANT["ars"], roles=roles))
        self.setUp_grant()
        self.new_job()
        self.activate()
        self.bind("coordinator", "C1")
        c, r = self.run_wf("bind", "j", "--bind-role", "builder", "--bind-session", "B1", role="coordinator",
                           session="C1")
        self.assertEqual((c, r["code"]), (1, "rejected"), r)
        self.assertIn("rejected(ars): path 'jobs/j/work/**' is not inside the ARS paths", r["error"])

    def test_path_outside_binding(self):
        self.new_job()
        self.activate()
        self.bind("coordinator", "C1")
        self.ok(self.run_wf("bind", "j", "--bind-role", "builder", "--bind-session", "B1", "--path", "jobs/j/work/sub/**",
                            role="coordinator", session="C1"))
        r = self._decide_builder()
        self.assertEqual((r["decision"], r["reason"]), ("deny", "path-outside-binding"), r)
        inside = self.d(self.p("jobs/j/work/sub/a.txt"))
        self.assertEqual((inside["decision"], inside["reason"]), ("allow", "allowed"), inside)

    def setUp_grant(self) -> None:
        with open(self.gpath, "w", encoding="utf-8") as fh:
            json.dump(self.grant_obj, fh)


class ReasonsTuple(unittest.TestCase):
    def test_reasons_list_the_three_hook_only_reasons(self):
        """FIX1 I6 (W39-S2: at 2dedc94 this pinned their absence, finding F12): the hook-only reasons gate-config,
        gate-integrity and audit-unavailable are in gatecore.REASONS, and every reason the hook source raises through
        `_Deny("<reason>"` is in it, so REASONS is the one vocabulary of gate reasons."""
        for name in ("gate-config", "gate-integrity", "audit-unavailable"):
            self.assertIn(name, gatecore.REASONS)
        with open(os.path.join(WF_DIR, "gate", "wf_gate_hook.py"), encoding="utf-8") as fh:
            raised = set(re.findall(r'_Deny\("([a-z-]+)"', fh.read()))
        self.assertTrue(raised, "the hook source raises _Deny with a literal reason")
        self.assertEqual(raised - set(gatecore.REASONS), set())


class GatelogRowValidation(_Gate):
    def test_decision_row_fields_must_be_strings_or_null(self):
        self.explicit()
        r = self.d(self.p("jobs/j/work/a.txt"))
        for field in ("session", "tool_name", "channel", "command_sha256"):
            with self.subTest(field=field):
                with self.assertRaises(gatelog.GateLogError) as cm:
                    gatelog.record(self.dir, dict(r, **{field: 5}), payload_sha256="0" * 64, ts="2026-10-18T00:00:00Z")
                self.assertIn("must be a string or null", str(cm.exception))

    def test_verify_reports_an_unreadable_audit_failures_file(self):
        failures = gatelog._failures_path(self.dir)
        os.makedirs(failures)                      # a directory where the failures file should be
        out = gatelog.verify(self.dir)
        self.assertTrue(any(p.startswith("gate audit-failures file unreadable:") for p in out["chain_problems"]), out)
        self.assertEqual(out["unlogged_attempts"], 0)
