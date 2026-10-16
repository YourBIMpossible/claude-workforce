"""T06 reconcile + ack-reconcile (B2 slice 1, lane S1R, Zen): module tests for `wfcore.reconcile` against the frozen
interface contract (GitHub issue #27 §1 schemas, §4-§5 reconcile / ack rules, §6 signatures, D-S1-5 bounded
enumeration). Spec: FIRST-RELEASE-SPEC.md §4.1 l.178, §6.2 step 3, case T06 l.281.

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
from wfcore.db import OpLog, WfRejected  # noqa: E402
from wfcore.faults import Faults  # noqa: E402

WORK = "jobs/j/work/a.txt"
STRAY = "tools/wf/stray.txt"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _Engine(Base):
    """Base + S1R ledger helpers. Job "j": contract by coordinator C1, grant activated; builder is the implicit B1
    (observed header) unless `binding` says otherwise."""

    def setUp(self) -> None:
        super().setUp()
        self._n = 0

    # -- ledger reads
    def rows(self) -> list[dict[str, Any]]:
        c = dbm.connect(self.db, create=False)
        try:
            return dbm.rows(c, "j")
        finally:
            c.close()

    def head(self) -> int:
        return self.rows()[-1]["seq"] if self.rows() else 0

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

    # -- writes through the real txn.write_event (contract §6 steps 1, 4, 5, 6 for the S1R types)
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

    def report(self, *, binding: tuple[str, int] | None = ("B1", 1)) -> dict:
        res = self._write(etype="reconcile-report", role="coordinator", session="C1",
                          content_fn=lambda conn, rows, head: reconcile.compute_report(
                              self.root(), CONTRACT, self.baseline(), rows, role="builder", binding=binding,
                              head_seq=head))
        return res

    def ack(self, report_seq: int, ids: list[str], *, role: str = "builder", session: str = "B1",
            binding_session: str | None = "B1", note: str | None = None) -> dict:
        content: dict[str, Any] = {"role": role, "session": session, "report_seq": report_seq, "exceptions": ids}
        if note is not None:
            content["note"] = note
        return self._write(etype="ack-reconcile", role=role, session=session, binding_session=binding_session,
                           content_fn=lambda conn, rows, head: content)

    def builder_intent(self, note: str = "n", session: str = "B1") -> Any:
        return self._write(etype="intent", role="builder", session=session,
                           content_fn=lambda conn, rows, head: {"path": WORK, "sha_before": "", "note": note})

    def builder_receipt(self, sha_after: str, session: str = "B1") -> Any:
        return self._write(etype="receipt", role="builder", session=session,
                           content_fn=lambda conn, rows, head: {"path": WORK, "sha_before": "", "sha_after": sha_after,
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


class ReportClassification(_Engine):
    """D-S1-5: what the report examines, how it classifies, and that it is deterministic."""

    def test_intent_without_receipt_and_stray_read_scope_change(self) -> None:
        self.new_job(); self.activate()
        c, r = self.intent()                                  # intent for WORK, no receipt (via the CLI, implicit B1)
        self.assertEqual(c, 0, r)
        self.write_file(WORK, "changed")
        self.write_file(STRAY, "unrecorded")                  # read-scope change nobody named
        res = self.report()
        content = self.report_content(res["seq"])
        classes = {e["path"]: e["class"] for e in content["exceptions"]}
        self.assertEqual(classes, {WORK: "attempt-without-completion", STRAY: "matches-neither"})
        ids = [e["id"] for e in content["exceptions"]]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(ids, [f"attempt-without-completion:{WORK}", f"matches-neither:{STRAY}"])
        self.assertEqual(content["through_seq"], res["seq"] - 1)
        self.assertEqual(content["role"], "builder")
        self.assertEqual(content["binding"], {"session": "B1", "generation": 1})
        # the attempt exception records the file's present state (changed without a receipt)
        ev = next(e for e in content["exceptions"] if e["path"] == WORK)["evidence"]
        self.assertEqual(ev["now"]["sha256"], _sha("changed"))
        self.assertIsNone(ev["last_receipt_seq"])
        # D-S1-5: the examined set is stated; no write-scope walk; gate log explicitly unavailable
        self.assertEqual(content["examined"]["intent_receipt_paths"], [WORK])
        self.assertFalse(content["examined"]["write_scope_walk"])
        self.assertEqual(content["gate_log"]["available"], False)
        self.assertIn("UNKNOWN", content["gate_log"]["statement"])
        self.assertTrue(any("write-scope file changed with neither an intent nor a receipt" in x
                            for x in content["limitations"]))
        self.assertEqual(content["changeset"]["added"], [STRAY])

    def test_receipt_completes_intent_and_mismatch_is_classified(self) -> None:
        self.new_job(); self.activate()
        self.builder_intent()
        self.write_file(WORK, "changed")
        self.builder_receipt(_sha("changed"))
        res = self.report()
        self.assertEqual(self.report_content(res["seq"])["exceptions"], [])
        self.ack(res["seq"], [])
        self.write_file(WORK, "changed again without a receipt")
        res2 = self.report()
        content = self.report_content(res2["seq"])
        self.assertEqual([e["class"] for e in content["exceptions"]], ["completion-mismatch"])
        self.assertEqual(content["exceptions"][0]["evidence"]["sha_after"], _sha("changed"))

    def test_receipt_without_intent_and_unresolvable_path(self) -> None:
        self.new_job(); self.activate()
        self.builder_receipt(_sha("x"))                                   # no intent named WORK
        self._write(etype="intent", role="builder", session="B1",
                    content_fn=lambda conn, rows, head: {"path": "../outside.txt", "sha_before": "", "note": "n"})
        res = self.report()
        by_path: dict[str, set[str]] = {}
        for e in self.report_content(res["seq"])["exceptions"]:
            by_path.setdefault(e["path"], set()).add(e["class"])
        # one path, two exceptions with distinct ids: no intent named it, and the file lacks the receipted sha
        self.assertEqual(by_path[WORK], {"completion-without-attempt", "completion-mismatch"})
        self.assertEqual(by_path["../outside.txt"], {"unresolvable-path"})

    def test_report_is_deterministic(self) -> None:
        self.new_job(); self.activate()
        self.builder_intent()
        self.write_file(WORK, "changed")
        self.write_file(STRAY, "u")
        rows = self.rows()
        a = reconcile.compute_report(self.root(), CONTRACT, self.baseline(), rows, role="builder", binding=("B1", 1),
                                     head_seq=rows[-1]["seq"])
        b = reconcile.compute_report(self.root(), CONTRACT, self.baseline(), rows, role="builder", binding=("B1", 1),
                                     head_seq=rows[-1]["seq"])
        self.assertEqual(a, b)
        self.assertEqual([e["id"] for e in a["exceptions"]], sorted(e["id"] for e in a["exceptions"]))

    def test_read_only_target_role_refused(self) -> None:
        self.new_job(); self.activate()
        rows = self.rows()
        with self.assertRaises(WfRejected) as cm:
            reconcile.compute_report(self.root(), CONTRACT, self.baseline(), rows, role="reviewer", binding=None,
                                     head_seq=rows[-1]["seq"])
        self.assertEqual(cm.exception.reason, "role")

    def test_write_scope_change_without_intent_or_receipt_is_not_discovered(self) -> None:
        """D-S1-5 limit, pinned: a write-scope file nobody named is not an exception and the limit is stated."""
        self.new_job(); self.activate()
        self.write_file("jobs/j/work/silent.txt", "no intent, no receipt")
        res = self.report(binding=None)
        content = self.report_content(res["seq"])
        self.assertEqual(content["exceptions"], [])
        self.assertIsNone(content["binding"])
        self.assertTrue(any("not discoverable" in x for x in content["limitations"]))


class AckRules(_Engine):
    def _reported(self) -> tuple[int, list[str]]:
        self.new_job(); self.activate()
        self.builder_intent()
        self.write_file(WORK, "changed")
        self.write_file(STRAY, "u")
        res = self.report()
        return res["seq"], [e["id"] for e in self.report_content(res["seq"])["exceptions"]]

    def test_unreconciled_role_refused_until_exact_ack(self) -> None:
        seq, ids = self._reported()
        self.assertTrue(reconcile.state(self.rows(), "builder")["unreconciled"])
        self.assertFalse(reconcile.state(self.rows(), "coordinator")["unreconciled"])
        self.assert_rejected(self.builder_intent, "unreconciled", "blocked")
        self.assert_rejected(self.builder_receipt, "unreconciled", _sha("changed"))
        self.assert_rejected(self.ack, "invalid-ack", seq, [])                       # not the exact set
        self.assert_rejected(self.ack, "invalid-ack", seq, ids + ids)                # duplicates
        self.assert_rejected(self.ack, "invalid-ack", seq, ids + ["matches-neither:other"])
        self.assert_rejected(self.ack, "stale-report", seq - 1, ids)                 # not the latest report
        self.assert_rejected(self.ack, "stale-report", seq + 7, ids)
        self.assert_rejected(self.ack, "invalid-ack", seq, ids, session="B2", binding_session="B1")  # foreign session
        self.assert_rejected(self.ack, "invalid-ack", seq, ids, binding_session=None)  # role has no session
        self.assert_rejected(self.ack, "no-report", seq, ids, role="coordinator", session="C1",
                             binding_session="C1")                                   # not the reconciled role
        res = self.ack(seq, list(reversed(ids)), note="acknowledged")               # order-free exact set
        self.assertEqual(res["outcome"], "COMMITTED")
        st = reconcile.state(self.rows(), "builder")
        self.assertEqual((st["latest_report_seq"], st["acked_seq"], st["unreconciled"]), (seq, res["seq"], False))

    def test_ack_does_not_invalidate_itself_and_second_ack_refused(self) -> None:
        seq, ids = self._reported()
        self.ack(seq, ids)
        self.assertFalse(reconcile.state(self.rows(), "builder")["unreconciled"])
        r = self.builder_intent("resume")                                           # the role works again
        self.assertEqual(r["outcome"], "COMMITTED")
        self.assert_rejected(self.ack, "invalid-ack", seq, ids)                      # already acknowledged

    def test_no_report_and_zero_exception_report_still_needs_ack(self) -> None:
        self.new_job(); self.activate()
        self.assert_rejected(self.ack, "no-report", 1, [])
        self.builder_intent()
        self.write_file(WORK, "done")
        self.builder_receipt(_sha("done"))
        res = self.report()
        self.assertEqual(self.report_content(res["seq"])["exceptions"], [])
        self.assert_rejected(self.builder_intent, "unreconciled", "again")
        self.assert_rejected(self.ack, "invalid-ack", res["seq"], ["something"])
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
        rows = self.rows()
        rep = reconcile.compute_report(self.root(), CONTRACT, self.baseline(), rows, role="builder", binding=None,
                                       head_seq=rows[-1]["seq"])
        reconcile.validate_content("reconcile-report", rep, CONTRACT, None)
        for bad in ({**rep, "role": "reviewer"}, {**rep, "gate_log": {"available": True}},
                    {**rep, "exceptions": [{"id": "x", "class": "nope", "path": "p"}]}, {k: v for k, v in rep.items() if k != "examined"}):
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
