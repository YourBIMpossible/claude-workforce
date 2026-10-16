"""T06 reconcile + ack-reconcile (B2 slice 1, lane S1R, Zen): module tests for `wfcore.reconcile` against the frozen
interface contract (GitHub issue #27 §1 schemas, §4 report / ack rules and CLASSES, §6 signatures and state keys,
§9 D-S1-3 / D-S1-5; AMENDMENT A2: compare never walks the write scope). Spec: FIRST-RELEASE-SPEC.md §4.1 l.178,
§6.2 steps 3 and 5, case T06 l.281. Revised in S1R cycle 1 (White F1 on PR #30) to pin the frozen shapes.

Fixture ledgers are built through `wf.run` with `test_wf.Base` (fresh WF_TEST_TMP, refuse-existing, never delete);
`wf job new` on main captures the changed-set baseline into the contract event (A1), which `compute_report` reads.

The S1W wiring (`wf reconcile`, `wf ack-reconcile`, the `_guard`) is White's and is not on this branch, so this
file carries a TEST-LOCAL engine, `_Engine`, that writes the S1R rows through the real `txn.write_event` with a
`build` running the contract §6 steps this lane owns: recovery gate (`recovery_open`), `check_actor`, the
`ack-reconcile` rule (`validate_ack`), then `validate.build_row` (whose schema dispatch is the §7 edit on this
branch). It is not evidence for White's wiring.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import unittest
from typing import Any

from test_wf import Base, CONTRACT  # noqa: E402
from wfcore import changeset, db as dbm, reconcile, txn, validate  # noqa: E402
from wfcore.db import OpLog, WfRefused, WfRejected  # noqa: E402
from wfcore.faults import Faults  # noqa: E402

WORK = "jobs/j/work/a.txt"
STRAY = "tools/wf/stray.txt"
REPORT_KEYS = {"role", "session", "generation", "through_seq", "classification", "exceptions", "gate_log", "problems",
               "limitations"}
CLASSIFICATION_KEYS = {"path", "class", "receipt_seq", "intent_seq", "sha_now", "sha_baseline", "sha_receipt"}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _Engine(Base):
    """Base + S1R ledger helpers. Job "j": contract by coordinator C1, grant activated; builder is the implicit B1."""

    def setUp(self) -> None:
        super().setUp()
        self._n = 0

    def rows(self) -> list[dict[str, Any]]:
        c = dbm.connect(self.db, create=False)
        try:
            return dbm.rows(c, "j")
        finally:
            c.close()

    def head(self) -> int:
        rs = self.rows()
        return rs[-1]["seq"] if rs else 0

    def baseline(self) -> dict:
        return json.loads(self.rows()[0]["content"])["baseline_manifest"]

    def root(self) -> str:
        r = changeset.boundary_root(self.db)
        assert r is not None
        return r

    def write_file(self, rel: str, text: str) -> None:
        p = os.path.join(self.root(), *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)

    def _write(self, *, etype: str, role: str, session: str, content_fn: Any, generation: int = 1,
               msg_id: str | None = None, binding_session: str | None = "B1") -> Any:
        self._n += 1
        mid = msg_id or f"m{self._n}-{etype}"

        def build(conn: sqlite3.Connection, head_seq: int, head_self: str) -> dict:
            c = dbm.contract(conn, "j")
            assert c is not None
            csha, contract = c["sha256"], json.loads(c["content"])
            rows = dbm.rows(conn, "j")
            if etype != "recovery-opened":
                opened = reconcile.recovery_open(rows)
                if opened is not None:
                    raise WfRejected("recovery-open", f"recovery is open since seq {opened}", opened_seq=opened)
            reconcile.check_actor(rows, role=role, etype=etype)
            content = content_fn(conn, rows, head_seq)
            if etype == "ack-reconcile":
                reconcile.validate_ack(rows, role=role, session=session, binding_session=binding_session,
                                       content=content)
            return validate.build_row(conn, "j", head_seq, head_self, etype=etype, msg_id=mid, author_role=role,
                                      session_id=session, generation=generation, reply_to=None, grant_ref=None,
                                      content=content, contract_sha=csha, contract_content=contract)
        return txn.write_event(self.db, "j", Faults(), OpLog(), build=build, session=session)

    def compute(self, rows: list[dict[str, Any]] | None = None, *, binding: tuple[str, int] | None = ("B1", 1),
                role: str = "builder") -> dict:
        rows = rows if rows is not None else self.rows()
        return reconcile.compute_report(self.root(), CONTRACT, self.baseline(), rows, role=role, binding=binding,
                                        head_seq=rows[-1]["seq"])

    def report(self, *, binding: tuple[str, int] | None = ("B1", 1)) -> dict:
        return self._write(etype="reconcile-report", role="coordinator", session="C1",
                           content_fn=lambda conn, rows, head: reconcile.compute_report(
                               self.root(), CONTRACT, self.baseline(), rows, role="builder", binding=binding,
                               head_seq=head))

    def ack(self, report_seq: int, ids: list[str], *, role: str = "builder", session: str = "B1",
            binding_session: str | None = "B1", note: str | None = None) -> dict:
        content: dict[str, Any] = {"role": role, "session": session, "report_seq": report_seq, "exceptions": ids}
        if note is not None:
            content["note"] = note
        return self._write(etype="ack-reconcile", role=role, session=session, binding_session=binding_session,
                           content_fn=lambda conn, rows, head: content)

    def builder_intent(self, note: str = "n", session: str = "B1", path: str = WORK) -> Any:
        return self._write(etype="intent", role="builder", session=session,
                           content_fn=lambda conn, rows, head: {"path": path, "sha_before": "", "note": note})

    def builder_receipt(self, sha_after: str, session: str = "B1", path: str = WORK) -> Any:
        return self._write(etype="receipt", role="builder", session=session,
                           content_fn=lambda conn, rows, head: {"path": path, "sha_before": "", "sha_after": sha_after,
                                                                 "note": "n"})

    def assert_rejected(self, fn: Any, reason: str, *a: Any, **k: Any) -> WfRejected:
        before = self.head()
        with self.assertRaises(WfRejected) as cm:
            fn(*a, **k)
        self.assertEqual(cm.exception.reason, reason, str(cm.exception))
        self.assertEqual(self.head(), before, "a refused write must append nothing")
        return cm.exception

    def report_content(self, seq: int) -> dict:
        return json.loads(next(r for r in self.rows() if r["seq"] == seq)["content"])

    @staticmethod
    def by_path(content: dict) -> dict[str, dict]:
        return {c["path"]: c for c in content["classification"]}


class ReportClassification(_Engine):
    """§4 shape and rules; D-S1-5 / A2 bounds; determinism."""

    def test_intent_without_receipt_and_stray_read_scope_change(self) -> None:
        self.new_job(); self.activate()
        c, r = self.intent()                                  # intent for WORK (write scope), no receipt
        self.assertEqual(c, 0, r)
        self.write_file(WORK, "changed")
        self.write_file(STRAY, "unrecorded")                  # read-scope change nobody named (A2 item 1)
        res = self.report()
        content = self.report_content(res["seq"])
        self.assertEqual(set(content) - {"contract_sha256"}, REPORT_KEYS)   # build_row injects the pin
        self.assertEqual((content["role"], content["session"], content["generation"]), ("builder", "B1", 1))
        self.assertEqual(content["through_seq"], res["seq"] - 1)
        self.assertIsNone(content["gate_log"])
        self.assertEqual(content["problems"], [])
        cl = self.by_path(content)
        self.assertEqual(set(cl), {WORK, STRAY})
        self.assertTrue(all(set(c) == CLASSIFICATION_KEYS for c in content["classification"]))
        self.assertEqual(cl[WORK]["class"], "attempt-without-completion")
        self.assertEqual((cl[WORK]["intent_seq"], cl[WORK]["receipt_seq"], cl[WORK]["sha_now"]),
                         (r["seq"], None, _sha("changed")))
        self.assertEqual(cl[STRAY]["class"], "matches-neither")
        self.assertEqual((cl[STRAY]["sha_now"], cl[STRAY]["sha_baseline"], cl[STRAY]["intent_seq"]),
                         (_sha("unrecorded"), None, None))
        # exceptions: every matches-neither / attempt-without-completion item, ids X1.. in path order
        self.assertEqual(content["exceptions"], [{"id": "X1", "path": WORK, "class": "attempt-without-completion"},
                                                 {"id": "X2", "path": STRAY, "class": "matches-neither"}])
        # D-S1-5 stated in limitations; the compare limitations are carried too
        self.assertTrue(any("write scope is not walked" in x for x in content["limitations"]))
        self.assertTrue(any("outside-boundary changes are not detected" in x for x in content["limitations"]))

    def test_receipt_rules_matches_receipt_then_matches_neither(self) -> None:
        self.new_job(); self.activate()
        self.builder_intent()
        self.write_file(WORK, "changed")
        rec = self.builder_receipt(_sha("changed"))
        res = self.report()
        content = self.report_content(res["seq"])
        self.assertEqual(content["exceptions"], [])
        self.assertEqual(self.by_path(content)[WORK]["class"], "matches-receipt")
        self.assertEqual(self.by_path(content)[WORK]["receipt_seq"], rec["seq"])
        self.assertEqual(self.by_path(content)[WORK]["sha_receipt"], _sha("changed"))
        self.ack(res["seq"], [])
        self.write_file(WORK, "changed again without a receipt")
        res2 = self.report()
        content2 = self.report_content(res2["seq"])
        self.assertEqual(self.by_path(content2)[WORK]["class"], "matches-neither")
        self.assertEqual(content2["exceptions"], [{"id": "X1", "path": WORK, "class": "matches-neither"}])

    def test_matches_baseline_and_absent_absent(self) -> None:
        self.new_job(); self.activate()
        # a read-scope file present at baseline, named by a receipt that restores it: matches-receipt; the same
        # file with no receipt and unchanged content is never in-scope (compare lists no change) ...
        self.write_file(STRAY, "u")
        self.builder_receipt(_sha("gone"), path=STRAY)                  # receipt whose sha is not the file's
        self.write_file(STRAY, "gone")
        res = self.report()
        cl = self.by_path(self.report_content(res["seq"]))
        self.assertEqual(cl[STRAY]["class"], "matches-receipt")
        # ... and a path named by a receipt that is absent now and was absent in the baseline is matches-baseline
        # only without a receipt: here the receipt exists, so an absent file is matches-neither
        self.ack(res["seq"], [])
        os.remove(os.path.join(self.root(), *STRAY.split("/")))
        res2 = self.report()
        cl2 = self.by_path(self.report_content(res2["seq"]))
        self.assertEqual(cl2[STRAY]["class"], "matches-neither")
        self.assertIsNone(cl2[STRAY]["sha_now"])

    def test_matches_baseline_rule_is_not_reachable_from_the_in_scope_union(self) -> None:
        """Observation pinned for White: a path enters scope only through compare (which lists a path only when it
        differs from the baseline) or through an intent/receipt (an intent without later receipt is
        attempt-without-completion; a receipt excludes the rule), so matches-baseline cannot occur under the frozen
        rules. A receipt-named path absent now is matches-neither; a compare-listed path unchanged vs the baseline
        does not exist. Not a defect of this module; reported on #27."""
        self.new_job(); self.activate()
        self.builder_intent(path="jobs/j/work/never-made.txt")
        self.builder_receipt(_sha(""), path="jobs/j/work/never-made.txt")
        content = self.compute()
        c = self.by_path(content)["jobs/j/work/never-made.txt"]
        self.assertEqual((c["class"], c["sha_now"], c["sha_baseline"]), ("matches-neither", None, None))
        self.assertNotIn("matches-baseline", {x["class"] for x in content["classification"]})

    def test_attempt_without_completion_regardless_of_sha(self) -> None:
        """An older receipt whose sha matches the file does not clear a later intent (§6.2 step 3)."""
        self.new_job(); self.activate()
        self.write_file(WORK, "v1")
        self.builder_receipt(_sha("v1"))
        self.builder_intent("second attempt")                            # later intent, no receipt after it
        content = self.compute()
        c = self.by_path(content)[WORK]
        self.assertEqual(c["class"], "attempt-without-completion")
        self.assertEqual(c["sha_now"], c["sha_receipt"])                 # sha matches, class unchanged

    def test_unrelatable_path_is_matches_neither_with_null_sha_and_named_limitation(self) -> None:
        self.new_job(); self.activate()
        self.builder_intent(path="../outside.txt")
        self.builder_receipt(_sha("x"), path="../outside.txt")           # receipt after intent: not an attempt
        content = self.compute()
        c = self.by_path(content)["../outside.txt"]
        self.assertEqual((c["class"], c["sha_now"]), ("matches-neither", None))
        self.assertTrue(any("'../outside.txt' cannot be related" in x for x in content["limitations"]))
        self.assertEqual(content["exceptions"], [{"id": "X1", "path": "../outside.txt", "class": "matches-neither"}])

    def test_report_is_deterministic(self) -> None:
        self.new_job(); self.activate()
        self.builder_intent()
        self.write_file(WORK, "changed")
        self.write_file(STRAY, "u")
        rows = self.rows()
        self.assertEqual(self.compute(rows), self.compute(rows))
        ids = [e["id"] for e in self.compute(rows)["exceptions"]]
        self.assertEqual(ids, [f"X{i}" for i in range(1, len(ids) + 1)])
        paths = [c["path"] for c in self.compute(rows)["classification"]]
        self.assertEqual(paths, sorted(paths))

    def test_read_only_target_role_refused_and_compare_problems_refuse(self) -> None:
        self.new_job(); self.activate()
        rows = self.rows()
        with self.assertRaises(WfRejected) as cm:
            self.compute(rows, role="reviewer", binding=None)
        self.assertEqual(cm.exception.reason, "role")
        with self.assertRaises(WfRefused):                               # malformed baseline -> compare problems
            reconcile.compute_report(self.root(), CONTRACT, {"entries": "nope"}, rows, role="builder", binding=None,
                                     head_seq=rows[-1]["seq"])
        before = self.head()
        with self.assertRaises(WfRefused):
            self._write(etype="reconcile-report", role="coordinator", session="C1",
                        content_fn=lambda conn, rs, head: reconcile.compute_report(
                            self.root(), CONTRACT, {"entries": "nope"}, rs, role="builder", binding=None, head_seq=head))
        self.assertEqual(self.head(), before)                            # fail closed: nothing appended

    def test_write_scope_change_without_intent_or_receipt_is_not_discovered(self) -> None:
        """D-S1-5 / A2 limit, pinned: a write-scope file nobody named is not classified; the limit is stated."""
        self.new_job(); self.activate()
        self.write_file("jobs/j/work/silent.txt", "no intent, no receipt")
        res = self.report(binding=None)
        content = self.report_content(res["seq"])
        self.assertEqual(content["classification"], [])
        self.assertEqual(content["exceptions"], [])
        self.assertEqual((content["session"], content["generation"]), (None, None))
        self.assertTrue(any("not discoverable here" in x for x in content["limitations"]))


class AckRules(_Engine):
    def _reported(self) -> tuple[int, list[str]]:
        self.new_job(); self.activate()
        self.builder_intent()
        self.write_file(WORK, "changed")
        self.write_file(STRAY, "u")
        res = self.report()
        return res["seq"], [e["id"] for e in self.report_content(res["seq"])["exceptions"]]

    def test_state_keys_and_unreconciled_predicate(self) -> None:
        seq, ids = self._reported()
        st = reconcile.state(self.rows(), "builder")
        self.assertEqual(set(st), {"latest_report_seq", "latest_ack_seq", "unreconciled", "report"})
        self.assertEqual((st["latest_report_seq"], st["latest_ack_seq"], st["unreconciled"]), (seq, None, True))
        self.assertEqual(st["report"], self.report_content(seq))
        self.assertEqual(reconcile.state(self.rows(), "coordinator"),
                         {"latest_report_seq": None, "latest_ack_seq": None, "unreconciled": False, "report": None})

    def test_unreconciled_role_refused_until_exact_ack(self) -> None:
        seq, ids = self._reported()
        exc = self.assert_rejected(self.builder_intent, "unreconciled", "blocked")
        self.assertIn("report_seq", str(exc) + json.dumps(getattr(exc, "info", {}), default=str))
        self.assert_rejected(self.builder_receipt, "unreconciled", _sha("changed"))
        self.assert_rejected(self.ack, "invalid-ack", seq, [])                       # not the exact set
        self.assert_rejected(self.ack, "invalid-ack", seq, ids + ids)                # duplicates
        self.assert_rejected(self.ack, "invalid-ack", seq, ids + ["X9"])
        self.assert_rejected(self.ack, "stale-report", seq - 1, ids)                 # not the latest report
        self.assert_rejected(self.ack, "stale-report", seq + 7, ids)
        self.assert_rejected(self.ack, "invalid-ack", seq, ids, session="B2", binding_session="B1")  # foreign session
        self.assert_rejected(self.ack, "invalid-ack", seq, ids, binding_session=None)  # role has no session
        self.assert_rejected(self.ack, "no-report", seq, ids, role="coordinator", session="C1",
                             binding_session="C1")                                   # not the reconciled role
        res = self.ack(seq, list(reversed(ids)), note="acknowledged")               # order-free exact set
        self.assertEqual(res["outcome"], "COMMITTED")
        st = reconcile.state(self.rows(), "builder")
        self.assertEqual((st["latest_report_seq"], st["latest_ack_seq"], st["unreconciled"]), (seq, res["seq"], False))

    def test_ack_does_not_invalidate_itself_and_second_ack_refused(self) -> None:
        seq, ids = self._reported()
        self.ack(seq, ids)
        self.assertFalse(reconcile.state(self.rows(), "builder")["unreconciled"])
        self.assertEqual(self.builder_intent("resume")["outcome"], "COMMITTED")      # the role works again
        self.assert_rejected(self.ack, "invalid-ack", seq, ids)                      # already acknowledged

    def test_no_report_and_zero_exception_report_still_needs_ack(self) -> None:
        """D-S1-3."""
        self.new_job(); self.activate()
        self.assert_rejected(self.ack, "no-report", 1, [])
        self.builder_intent()
        self.write_file(WORK, "done")
        self.builder_receipt(_sha("done"))
        res = self.report()
        self.assertEqual(self.report_content(res["seq"])["exceptions"], [])
        self.assert_rejected(self.builder_intent, "unreconciled", "again")
        self.assert_rejected(self.ack, "invalid-ack", res["seq"], ["X1"])
        self.ack(res["seq"], [])
        self.assertEqual(self.builder_intent("after")["outcome"], "COMMITTED")

    def test_second_report_supersedes_first(self) -> None:
        seq, ids = self._reported()
        res2 = self.report()                                                        # coordinator is never blocked
        self.assert_rejected(self.ack, "stale-report", seq, ids)
        ids2 = [e["id"] for e in self.report_content(res2["seq"])["exceptions"]]
        self.ack(res2["seq"], ids2)
        self.assertFalse(reconcile.state(self.rows(), "builder")["unreconciled"])


class Schemas(_Engine):
    def test_schema_exact_keys_and_role_rules(self) -> None:
        self.new_job(); self.activate()
        ok_ack = {"role": "builder", "session": "B1", "report_seq": 1, "exceptions": []}
        reconcile.validate_content("ack-reconcile", {**ok_ack, "note": "x"}, CONTRACT, None)
        for bad in ({**ok_ack, "extra": 1}, {k: v for k, v in ok_ack.items() if k != "exceptions"},
                    {**ok_ack, "report_seq": 0}, {**ok_ack, "exceptions": [1]}, {**ok_ack, "note": 3}):
            with self.assertRaises(WfRejected) as cm:
                reconcile.validate_content("ack-reconcile", bad, CONTRACT, None)
            self.assertEqual(cm.exception.reason, "malformed")
        rep = self.compute(binding=None)
        reconcile.validate_content("reconcile-report", rep, CONTRACT, None)
        bad_reports = (
            {**rep, "role": "reviewer"},
            {**rep, "gate_log": {"available": False}},                               # must be null
            {**rep, "exceptions": [{"id": "X1", "class": "matches-receipt", "path": "p"}]},
            {**rep, "classification": [{"path": "p", "class": "nope", "receipt_seq": None, "intent_seq": None,
                                        "sha_now": None, "sha_baseline": None, "sha_receipt": None}]},
            {k: v for k, v in rep.items() if k != "problems"},
            {**rep, "method": "x"},
        )
        for bad in bad_reports:
            with self.assertRaises(WfRejected) as cm:
                reconcile.validate_content("reconcile-report", bad, CONTRACT, None)
            self.assertEqual(cm.exception.reason, "malformed")
        with self.assertRaises(WfRejected):
            reconcile.validate_content("intent", {}, CONTRACT, None)
        # the §7 edit: validate.py routes the three S1R types here and knows their roles
        for t, roles in (("reconcile-report", ("coordinator",)), ("recovery-opened", ("coordinator",)),
                         ("ack-reconcile", ("coordinator", "builder", "reviewer"))):
            self.assertEqual(validate.ROLE_FOR_TYPE[t], roles)
        self.assertIn("ack-reconcile", validate.APPEND_TYPES)
        self.assertNotIn("reconcile-report", validate.APPEND_TYPES)
        with self.assertRaises(WfRejected) as cm:
            validate.validate_content("ack-reconcile", {"role": "builder"}, CONTRACT, None)
        self.assertEqual(cm.exception.reason, "malformed")

    def test_report_author_role_enforced_by_build_row(self) -> None:
        self.new_job(); self.activate()
        with self.assertRaises(WfRejected) as cm:
            self._write(etype="reconcile-report", role="builder", session="B1",
                        content_fn=lambda conn, rows, head: reconcile.compute_report(
                            self.root(), CONTRACT, self.baseline(), rows, role="builder", binding=None, head_seq=head))
        self.assertEqual(cm.exception.reason, "role")


if __name__ == "__main__":
    unittest.main()
