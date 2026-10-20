"""T16-a recover begin (B2 slice 1, lane S1R, Zen): module tests for the recovery parts of `wfcore.reconcile`
against the frozen interface contract (GitHub issue #27 §1 `recovery-opened` schema and recoverable-exception
shape, §5 recover-begin rules, §6 signatures; AMENDMENT A2: the provenance check is a substring test). Spec:
FIRST-RELEASE-SPEC.md §3B l.131-145. Revised in S1R cycle 1 (White F1 on PR #30).

Fixture ledgers are built through `wf.run` with `test_wf.Base`. The S1W wiring (`wf recover begin`, `_guard`) is
White's and not on this branch; the TEST-LOCAL engine below runs the S1R steps the wiring runs: collect
`recoverable_exceptions` read-only, inside the transaction `validate_recover_begin`, `chain_problems` (an invalid
chain raises `rejected(invalid-chain)` and nothing is appended), then `validate.build_row`. Not evidence for the
wiring; `recover reconcile` / `recover close` are outside this slice.
"""
from __future__ import annotations

import json
import os
import sqlite3
import unittest
from typing import Any

from test_wf import Base  # noqa: E402
from wfcore import db as dbm, export, paths, reconcile, txn, validate  # noqa: E402
from wfcore.db import OpLog, WfRejected  # noqa: E402
from wfcore.faults import Faults  # noqa: E402


class _Engine(Base):
    def rows(self) -> list[dict[str, Any]]:
        c = dbm.connect(self.db, create=False)
        try:
            return dbm.rows(c, "j")
        finally:
            c.close()

    def head(self) -> int:
        rs = self.rows()
        return rs[-1]["seq"] if rs else 0

    def begin(self, reason: str = "stale export", turn: str = "owner: recover j now", role: str = "coordinator",
              session: str = "C1") -> dict:
        exceptions = reconcile.recoverable_exceptions(self.db, "j")

        def build(conn: sqlite3.Connection, head_seq: int, head_self: str) -> dict:
            c = dbm.contract(conn, "j")
            assert c is not None
            csha, contract = c["sha256"], json.loads(c["content"])
            rows = dbm.rows(conn, "j")
            content = {"reason": reason, "owner_turn": turn, "exceptions": exceptions,
                       "chain_head": {"seq": head_seq, "self": head_self}}
            reconcile.validate_recover_begin(rows, job="j", content=content)
            problems = reconcile.chain_problems(conn, "j")
            if problems:
                raise WfRejected("invalid-chain", "chain invalid; recovery not opened (nothing appended): "
                                 + "; ".join(problems[:5]), problems=problems)
            return validate.build_row(conn, "j", head_seq, head_self, etype="recovery-opened", msg_id=f"rec-{head_seq}",
                                      author_role=role, session_id=session, generation=1, reply_to=None,
                                      grant_ref=None, content=content, contract_sha=csha, contract_content=contract)
        return txn.write_event(self.db, "j", Faults(), OpLog(), build=build, session=session)

    def ordinary(self, etype: str = "intent", role: str = "builder", session: str = "B1") -> dict:
        """An ordinary mutation through the recovery gate the wiring applies to every role-bearing write."""
        def build(conn: sqlite3.Connection, head_seq: int, head_self: str) -> dict:
            rows = dbm.rows(conn, "j")
            opened = reconcile.recovery_open(rows)
            if opened is not None:
                raise WfRejected("recovery-open", f"recovery is open since seq {opened}", opened_seq=opened)
            c = dbm.contract(conn, "j")
            assert c is not None
            return validate.build_row(conn, "j", head_seq, head_self, etype=etype, msg_id=f"o-{head_seq}",
                                      author_role=role, session_id=session, generation=1, reply_to=None,
                                      grant_ref=None, content={"path": "jobs/j/work/a.txt", "sha_before": "",
                                                               "note": "n"},
                                      contract_sha=c["sha256"], contract_content=json.loads(c["content"]))
        return txn.write_event(self.db, "j", Faults(), OpLog(), build=build, session=session)

    def assert_rejected(self, fn: Any, reason: str, *a: Any, **k: Any) -> WfRejected:
        before = self.head()
        with self.assertRaises(WfRejected) as cm:
            fn(*a, **k)
        self.assertEqual(cm.exception.reason, reason, str(cm.exception))
        self.assertEqual(self.head(), before, "a refused write must append nothing")
        return cm.exception


class RecoverBegin(_Engine):
    def test_open_recovery_recorded_with_exceptions_and_axis(self) -> None:
        self.new_job(); self.activate()
        c, r = self.intent()
        self.assertEqual(c, 0, r)
        out = paths.export_dir(self.db, "j")
        with open(os.path.join(out, "events.jsonl"), "a", encoding="utf-8") as fh:   # stale export
            fh.write("tampered\n")
        with open(os.path.join(out, "head.json.tmp"), "xb") as fh:                   # an orphan temp (export.orphans)
            fh.write(b"orphan\n")
        rows = self.rows()
        self.assertIsNone(reconcile.recovery_open(rows))
        self.assertIsNone(reconcile.execution_axis(rows))
        res = self.begin()
        self.assertEqual(res["outcome"], "COMMITTED")
        rows = self.rows()
        self.assertEqual(reconcile.recovery_open(rows), res["seq"])
        self.assertEqual(reconcile.execution_axis(rows), "recovering")
        content = json.loads(rows[-1]["content"])
        # S1-G1 (FIX2 #37/#52): the row carries no `gate_log` field; a gate-log problem is an exception item instead
        self.assertEqual(set(content) - {"contract_sha256"}, {"reason", "owner_turn", "exceptions", "chain_head"})
        self.assertEqual(content["chain_head"], {"seq": res["seq"] - 1, "self": rows[-2]["self"]})
        ex = content["exceptions"]
        self.assertTrue(ex and all(set(e) == {"id", "class", "detail"} for e in ex), ex)
        self.assertEqual([e["id"] for e in ex], [f"R{i}" for i in range(1, len(ex) + 1)])
        self.assertIn("export", {e["class"] for e in ex})
        self.assertIn("orphan", {e["class"] for e in ex})
        self.assertTrue(any("stale" in e["detail"] for e in ex if e["class"] == "export"))
        self.assertTrue(any(e["detail"].startswith(export.PRESERVED) for e in ex if e["class"] == "orphan"))
        self.assertFalse(any(e["class"] == "gate-log" for e in ex))                # no gate.db here: nothing (S1-G1)

    def test_every_ordinary_mutation_refused_while_open_and_second_begin_refused(self) -> None:
        self.new_job(); self.activate()
        self.begin()
        self.assert_rejected(self.ordinary, "recovery-open")
        self.assert_rejected(self.ordinary, "recovery-open", "blocker", "coordinator", "C1")
        self.assert_rejected(self.begin, "recovery-open")                    # validate_recover_begin itself
        reconcile.check_actor(self.rows(), role="builder", etype="intent")   # the recovery gate is recovery_open
        self.assertEqual(reconcile.execution_axis(self.rows()), "recovering")

    def test_preconditions_role_turn_and_shape(self) -> None:
        self.new_job(); self.activate()
        # A2: substring test for the job id; a turn without the letter j is the negative (one-letter fixture id)
        self.assert_rejected(self.begin, "no-provenance", "x", "owner: proceed")
        self.assert_rejected(self.begin, "role", "x", "owner: recover j", "builder", "B1")  # ROLE_FOR_TYPE (build_row)
        rows = self.rows()
        head = {"seq": rows[-1]["seq"], "self": rows[-1]["self"]}
        reconcile.validate_recover_begin(rows, job="j", content={"reason": "r", "owner_turn": "recover jx",
                                                                 "exceptions": [], "chain_head": head})  # substring
        for bad, reason in (({"reason": "", "owner_turn": "recover j", "exceptions": [], "chain_head": head}, "malformed"),
                            ({"reason": "r", "owner_turn": "recover j", "exceptions": [{"kind": "x"}], "chain_head": head},
                             "malformed"),
                            ({"reason": "r", "owner_turn": "recover j", "exceptions": [],
                              "chain_head": {"seq": 99, "self": "x"}}, "malformed")):
            with self.assertRaises(WfRejected) as cm:
                reconcile.validate_recover_begin(rows, job="j", content=bad)
            self.assertEqual(cm.exception.reason, reason)

    def test_invalid_chain_refused_and_nothing_appended(self) -> None:
        self.new_job(); self.activate()
        c, r = self.intent()
        self.assertEqual(c, 0, r)
        c, r = self.intent(note="two", msg_id="m2")
        self.assertEqual(c, 0, r)
        n = self.head()
        conn = sqlite3.connect(self.db)
        try:  # tamper a middle row: only a whole-chain check sees it
            conn.execute("DROP TRIGGER events_no_update")
            conn.execute("UPDATE events SET content = replace(content, 'G1', 'G9') WHERE job='j' AND seq=2")
            conn.commit()
        finally:
            conn.close()
        self.assert_rejected(self.begin, "invalid-chain")
        self.assertEqual(self.head(), n)
        self.assertIsNone(reconcile.recovery_open(self.rows()))
        c = dbm.connect(self.db, create=False)
        try:
            problems = reconcile.chain_problems(c, "j")
        finally:
            c.close()
        self.assertTrue(problems)
        self.assertTrue(any("seq 2" in p for p in problems), problems)

    def test_recoverable_exceptions_are_read_only_and_shaped(self) -> None:
        self.new_job(); self.activate()
        before = self.head()
        ex1 = reconcile.recoverable_exceptions(self.db, "j")
        ex2 = reconcile.recoverable_exceptions(self.db, "j")
        self.assertEqual(ex1, ex2)
        self.assertEqual(self.head(), before)
        self.assertEqual(ex1, [])                                            # clean export: no exceptions at all
        self.assertEqual(export.check(self.db, "j"), [])

    def test_schema_exact_keys(self) -> None:
        ok = {"reason": "r", "owner_turn": "recover j", "exceptions": [{"id": "R1", "class": "export", "detail": "d"}],
              "chain_head": {"seq": 1, "self": "a" * 64}}
        reconcile.validate_content("recovery-opened", {**ok, "contract_sha256": "x"}, {}, None)
        for bad in ({**ok, "extra": 1}, {k: v for k, v in ok.items() if k != "chain_head"}, {**ok, "reason": ""},
                    {**ok, "exceptions": "none"}, {**ok, "exceptions": [{"kind": "gate-log"}]},
                    {**ok, "chain_head": {"seq": "1"}}):
            with self.assertRaises(WfRejected) as cm:
                reconcile.validate_content("recovery-opened", bad, {}, None)
            self.assertEqual(cm.exception.reason, "malformed")


if __name__ == "__main__":
    unittest.main()
