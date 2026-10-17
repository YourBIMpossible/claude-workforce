"""T30 recovery closure + recovery-decision (B2 slice 2, lane S2R, Zen): module tests for the S2 parts of
`wfcore.reconcile` against INTERFACE-CONTRACT-S2.md §1 (`exception-acknowledged`, `recovery-closed`,
`recovery-decision`), §4 (recover reconcile / close / decide, T30 c, d, e and the T16 second trigger), §5 (A-4
disposition), §6 signatures, §7 (validate.py dispatch) and D-S2-1 / D-S2-5. Spec: FIRST-RELEASE-SPEC.md §3B, §6.2
step 4, cases T16, T30.

Fixture ledgers are built through `wf.run` with `test_wf.Base` (fresh WF_TEST_TMP, refuse-existing, never delete).
White's S2W wiring (`wf recover reconcile|close|decide`) is not on this branch, so a TEST-LOCAL engine writes the rows
through the real `txn.write_event` with a `build` running the steps the contract gives the wiring: the recovery gate
(open recovery refuses everything except `recovery-opened`, `exception-acknowledged`, `recovery-closed`), the
reconcile actor check, the type validator from this module, `chain_problems` before a close, then
`validate.build_row` (§7 dispatch on this branch). It is not evidence for the wiring. Kill/re-run fault injection
(T30 b) is lane S2C; T30 f and h are excluded by the contract.
"""
from __future__ import annotations

import json
import os
import sqlite3
import unittest
from typing import Any, Callable

from test_wf import Base, CONTRACT  # noqa: E402
from wfcore import changeset, db as dbm, paths, reconcile, txn, validate  # noqa: E402
from wfcore.db import OpLog, WfRejected  # noqa: E402
from wfcore.faults import Faults  # noqa: E402

TURN = "owner: recover j now"
GATE_PASS = ("recovery-opened", "exception-acknowledged", "recovery-closed")
STOP = {"role": "builder", "session": "B1", "generation": 1, "how": "process exit observed",
        "when": "2026-10-16T22:00:00Z", "owner_check": "owner confirmed no child process", "owner_turn": "stop j"}


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

    def write(self, etype: str, content_fn: Callable[[list[dict], int, str], dict], *, role: str = "coordinator",
              session: str = "C1", check: Callable[[list[dict], dict], None] | None = None,
              chain_check: bool = False) -> dict:
        def build(conn: sqlite3.Connection, head_seq: int, head_self: str) -> dict:
            c = dbm.contract(conn, "j")
            assert c is not None
            rows = dbm.rows(conn, "j")
            if etype not in GATE_PASS:
                opened = reconcile.recovery_open(rows)
                if opened is not None:
                    raise WfRejected("recovery-open", f"recovery is open since seq {opened}", opened_seq=opened)
            reconcile.check_actor(rows, role=role, etype=etype)
            content = content_fn(rows, head_seq, head_self)
            if check is not None:
                check(rows, content)
            if chain_check:
                problems = reconcile.chain_problems(conn, "j")
                if problems:
                    raise WfRejected("invalid-chain", "; ".join(problems[:5]), problems=problems)
            return validate.build_row(conn, "j", head_seq, head_self, etype=etype, msg_id=f"{etype}-{head_seq}",
                                      author_role=role, session_id=session, generation=1, reply_to=None,
                                      grant_ref=None, content=content, contract_sha=c["sha256"],
                                      contract_content=json.loads(c["content"]))
        return txn.write_event(self.db, "j", Faults(), OpLog(), build=build, session=session)

    # -- recovery steps (what `wf recover begin|reconcile|close` do, per S2 §4)
    def stale_export(self) -> None:
        with open(os.path.join(paths.export_dir(self.db, "j"), "events.jsonl"), "a", encoding="utf-8") as fh:
            fh.write("tampered\n")

    def orphan(self) -> None:
        with open(os.path.join(paths.export_dir(self.db, "j"), "head.json.tmp"), "xb") as fh:
            fh.write(b"orphan\n")

    def begin(self, turn: str = TURN) -> dict:
        exceptions = reconcile.recoverable_exceptions(self.db, "j")
        return self.write("recovery-opened", lambda rows, hs, hself: {
            "reason": "owner-reported interruption", "owner_turn": turn, "exceptions": exceptions,
            "chain_head": {"seq": hs, "self": hself}},
            check=lambda rows, c: reconcile.validate_recover_begin(rows, job="j", content=c), chain_check=True)

    def ack_one(self, item: dict, *, turn: str = TURN, opened_seq: int | None = None, **override: Any) -> dict:
        def content(rows: list[dict], hs: int, hself: str) -> dict:
            rec = reconcile.recovery_row(rows)
            seq = opened_seq if opened_seq is not None else (rec["seq"] if rec else 1)
            return {"opened_seq": seq, "id": item["id"], "class": item["class"], "detail": item["detail"],
                    "owner_turn": turn, **override}
        return self.write("exception-acknowledged", content,
                          check=lambda rows, c: reconcile.validate_exception_ack(rows, job="j", content=c))

    def recover_reconcile(self, turn: str = TURN) -> dict[str, list[str]]:
        """`wf recover reconcile`: read-only pending list, then one transaction per pending item (idempotent)."""
        done: list[str] = []
        for item in reconcile.pending_exceptions(self.rows()):
            self.ack_one(item, turn=turn)
            done.append(item["id"])
        return {"acknowledged": done}

    def close(self, turn: str = TURN, **override: Any) -> dict:
        def content(rows: list[dict], hs: int, hself: str) -> dict:
            rec = reconcile.recovery_row(rows)
            listed = [e["id"] for e in (rec["content"]["exceptions"] if rec else [])]
            return {"opened_seq": rec["seq"] if rec else 1, "acknowledged": sorted(listed),
                    "chain_head": {"seq": hs, "self": hself}, "owner_turn": turn, **override}
        return self.write("recovery-closed", content,
                          check=lambda rows, c: reconcile.validate_recover_close(rows, job="j", content=c),
                          chain_check=True)

    def ordinary(self) -> dict:
        return self.write("intent", lambda rows, hs, hself: {"path": "jobs/j/work/a.txt", "sha_before": "",
                                                              "note": "n"}, role="builder", session="B1")

    def assert_rejected(self, fn: Callable[..., Any], reason: str, *a: Any, **k: Any) -> WfRejected:
        before = self.head()
        with self.assertRaises(WfRejected) as cm:
            fn(*a, **k)
        self.assertEqual(cm.exception.reason, reason, str(cm.exception))
        self.assertEqual(self.head(), before, "a refused write must append nothing")
        return cm.exception

    def opened_with_two(self) -> int:
        self.new_job(); self.activate()
        c, r = self.intent()
        self.assertEqual(c, 0, r)
        self.stale_export()
        self.orphan()
        res = self.begin()
        listed = json.loads(self.rows()[-1]["content"])["exceptions"]
        self.ids = [e["id"] for e in listed]                  # stale export (2 problems) + orphan -> R1..R3
        self.assertEqual(self.ids, [f"R{i}" for i in range(1, len(listed) + 1)])
        self.assertGreaterEqual(len(listed), 2)
        self.assertIn("orphan", {e["class"] for e in listed})
        return int(res["seq"])


class RecoveryClosure(_Engine):
    def test_T30c_ordinary_mutations_refused_while_open_and_run_after_close(self) -> None:
        opened = self.opened_with_two()
        self.assert_rejected(self.ordinary, "recovery-open")
        self.assertEqual(self.recover_reconcile(), {"acknowledged": self.ids})
        self.assert_rejected(self.ordinary, "recovery-open")                 # still open until close
        res = self.close()
        rows = self.rows()
        content = json.loads(rows[-1]["content"])
        self.assertEqual((content["opened_seq"], content["acknowledged"]), (opened, sorted(self.ids)))
        self.assertEqual(content["chain_head"], {"seq": res["seq"] - 1, "self": rows[-2]["self"]})
        self.assertIsNone(reconcile.recovery_open(rows))
        self.assertIsNone(reconcile.execution_axis(rows))
        self.assertIsNone(reconcile.recovery_row(rows))
        self.assertEqual(reconcile.pending_exceptions(rows), [])
        self.assertEqual(self.ordinary()["outcome"], "COMMITTED")            # T30(c) second half

    def test_T30d_close_refuses_unacknowledged_then_invalid_chain_nothing_appended(self) -> None:
        self.opened_with_two()
        first = reconcile.pending_exceptions(self.rows())[0]
        self.ack_one(first)
        exc = self.assert_rejected(self.close, "unacknowledged")
        for missing in self.ids[1:]:
            self.assertIn(missing, str(exc))
        self.recover_reconcile()
        n = self.head()
        conn = sqlite3.connect(self.db)
        try:  # tamper a middle row: only the whole-chain check sees it
            conn.execute("DROP TRIGGER events_no_update")
            conn.execute("UPDATE events SET content = replace(content, 'G1', 'G9') WHERE job='j' AND seq=2")
            conn.commit()
        finally:
            conn.close()
        self.assert_rejected(self.close, "invalid-chain")
        self.assertEqual(self.head(), n)
        self.assertIsNotNone(reconcile.recovery_open(self.rows()))           # no recovery-closed row

    def test_close_content_rules(self) -> None:
        self.opened_with_two()
        self.recover_reconcile()
        self.assert_rejected(self.close, "malformed", acknowledged=list(reversed(sorted(self.ids))))  # not sorted
        self.assert_rejected(self.close, "malformed", acknowledged=sorted(self.ids)[:1])            # not every id
        self.assert_rejected(self.close, "malformed", chain_head={"seq": 1, "self": "x"})
        self.assert_rejected(self.close, "no-provenance", "owner: proceed")             # turn lacks the job id
        self.assert_rejected(self.close, "no-recovery", opened_seq=1)                   # not the open recovery
        self.assertEqual(self.close()["outcome"], "COMMITTED")
        self.assert_rejected(self.close, "no-recovery")                                 # nothing open any more

    def test_exception_ack_rules_and_idempotent_reconcile(self) -> None:
        self.new_job(); self.activate()
        item = {"id": "R1", "class": "export", "detail": "d"}
        self.assert_rejected(self.ack_one, "no-recovery", item)                          # nothing open
        self.orphan()                                                                    # exactly one exception
        with open(os.path.join(paths.export_dir(self.db, "j"), "events.jsonl.tmp"), "xb") as fh:
            fh.write(b"second orphan")                                                 # and a second
        opened = int(self.begin()["seq"])
        listed = json.loads(self.rows()[-1]["content"])["exceptions"]
        r1, r2 = listed
        self.assert_rejected(self.ack_one, "no-recovery", r1, opened_seq=opened - 1)     # wrong opened_seq
        self.assert_rejected(self.ack_one, "unknown-exception", {**r1, "id": "R9"})
        self.assert_rejected(self.ack_one, "malformed", {**r1, "class": "export"})       # class not copied
        self.assert_rejected(self.ack_one, "malformed", {**r1, "detail": "other"})       # detail not copied
        self.assert_rejected(self.ack_one, "no-provenance", r1, turn="owner: proceed")
        res = self.ack_one(r1, note="inspected")
        self.assert_rejected(self.ack_one, "already-acknowledged", r1)
        self.assertEqual(reconcile.acknowledged(self.rows(), opened), {"R1": res["seq"]})
        self.assertEqual(reconcile.pending_exceptions(self.rows()), [r2])
        # `wf recover reconcile` re-run after a partial run appends only the missing one, then nothing
        self.assertEqual(self.recover_reconcile(), {"acknowledged": [r2["id"]]})
        n = self.head()
        self.assertEqual(self.recover_reconcile(), {"acknowledged": []})
        self.assertEqual(self.head(), n)
        self.assertEqual(sorted(reconcile.acknowledged(self.rows(), opened)), ["R1", "R2"])

    def test_T16_second_trigger_and_audit_over_two_recoveries(self) -> None:
        o1 = self.opened_with_two()
        self.assert_rejected(self.begin, "recovery-open")                    # second trigger while open
        self.recover_reconcile()
        c1 = self.close()
        self.assertIsNone(reconcile.recovery_open(self.rows()))
        o2 = int(self.begin()["seq"])                                        # a new recovery may open after close
        self.assertEqual(reconcile.recovery_open(self.rows()), o2)
        rec = reconcile.recovery_row(self.rows())
        self.assertEqual((rec["seq"], rec["type"]), (o2, "recovery-opened"))
        self.assertIsInstance(rec["content"], dict)
        self.recover_reconcile()
        c2 = self.close()
        audit = reconcile.recovery_audit(self.rows())
        n1 = len(self.ids)
        n2 = len(json.loads(next(r for r in self.rows() if r["seq"] == o2)["content"])["exceptions"])
        self.assertEqual([a["type"] for a in audit],
                         ["recovery-opened", *["exception-acknowledged"] * n1, "recovery-closed",
                          "recovery-opened", *["exception-acknowledged"] * n2, "recovery-closed"])
        self.assertEqual([a["seq"] for a in audit], sorted(a["seq"] for a in audit))
        self.assertEqual((audit[0]["seq"], audit[n1 + 1]["seq"], audit[n1 + 2]["seq"], audit[-1]["seq"]),
                         (o1, c1["seq"], o2, c2["seq"]))
        self.assertEqual([a.get("id") for a in audit[1:n1 + 1]], self.ids)
        self.assertNotIn("id", audit[0])

    def test_schemas_and_validate_dispatch(self) -> None:
        for t in ("exception-acknowledged", "recovery-closed", "recovery-decision"):
            self.assertEqual(validate.ROLE_FOR_TYPE[t], ("coordinator",))
            self.assertNotIn(t, validate.APPEND_TYPES)
            with self.assertRaises(WfRejected) as cm:
                validate.validate_content(t, {"x": 1}, CONTRACT, None)        # §7: routed here, exact keys
            self.assertEqual(cm.exception.reason, "malformed")
        ack = {"opened_seq": 3, "id": "R1", "class": "export", "detail": "d", "owner_turn": "j"}
        reconcile.validate_content("exception-acknowledged", {**ack, "note": "n", "contract_sha256": "x"}, {}, None)
        closed = {"opened_seq": 3, "acknowledged": ["R1", "R2"], "chain_head": {"seq": 5, "self": "a"},
                  "owner_turn": "j"}
        reconcile.validate_content("recovery-closed", closed, {}, None)
        dec = {"decision": "resume", "role": "builder", "report_seq": 4, "stop_seq": 3, "restore_paths": [],
               "unresolved": [], "owner_turn": "j"}
        reconcile.validate_content("recovery-decision", {**dec, "note": "n"}, {}, None)
        for etype, bad in (("exception-acknowledged", {**ack, "extra": 1}), ("exception-acknowledged", {**ack, "opened_seq": 0}),
                           ("exception-acknowledged", {**ack, "note": 3}), ("recovery-closed", {**closed, "acknowledged": ["R2", "R1"]}),
                           ("recovery-closed", {**closed, "chain_head": {"seq": "5"}}), ("recovery-closed", {**closed, "note": "n"}),
                           ("recovery-decision", {**dec, "unresolved": [{"path": "p"}]}),
                           ("recovery-decision", {k: v for k, v in dec.items() if k != "stop_seq"}),
                           ("recovery-decision", {**dec, "owner_turn": ""})):
            with self.assertRaises(WfRejected) as cm:
                reconcile.validate_content(etype, bad, {}, None)
            self.assertEqual(cm.exception.reason, "malformed", (etype, bad))


class RecoveryDecision(_Engine):
    """`validate_recovery_decision` (S2 §1, §6, §6.2 step 4, D-S2-1, D-S2-5) over hand-built rows (pure function), plus
    one real decision written through the engine."""

    @staticmethod
    def _row(seq: int, etype: str, content: dict, role: str = "coordinator") -> dict[str, Any]:
        return {"seq": seq, "type": etype, "author_role": role, "content": json.dumps(content), "self": f"s{seq}"}

    def _rows(self, *, a4: tuple[str, ...] = ("../x.txt",), two_reports: bool = False) -> list[dict[str, Any]]:
        report = {"role": "builder", "session": "B1", "generation": 1, "through_seq": 2,
                  "classification": [{"path": p, "class": "matches-neither", "receipt_seq": None, "intent_seq": 2,
                                      "sha_now": None, "sha_baseline": None, "sha_receipt": None} for p in a4]
                  + [{"path": "jobs/j/work/a.txt", "class": "matches-neither", "receipt_seq": None, "intent_seq": None,
                      "sha_now": "a" * 64, "sha_baseline": None, "sha_receipt": None}],
                  "exceptions": [], "gate_log": None, "problems": [],
                  "limitations": [reconcile.UNRESOLVABLE.format(p) for p in a4]}
        rows = [self._row(1, "contract", {}), self._row(3, "stop-confirmed", STOP), self._row(4, "reconcile-report", report)]
        if two_reports:
            rows.append(self._row(5, "reconcile-report", {**report, "through_seq": 4}))
        return rows

    def _dec(self, **kw: Any) -> dict[str, Any]:
        return {"decision": "resume", "role": "builder", "report_seq": 4, "stop_seq": 3, "restore_paths": [],
                "unresolved": [{"path": "../x.txt", "disposition": "owner-inspected"}], "owner_turn": "decide j", **kw}

    def check(self, content: dict, *, rows: list[dict] | None = None, stopped_seq: int | None = 3) -> None:
        reconcile.validate_recovery_decision(rows if rows is not None else self._rows(), job="j",
                                             binding=("B1", 1), stopped_seq=stopped_seq, content=content)

    def refused(self, reason: str, content: dict, **kw: Any) -> None:
        with self.assertRaises(WfRejected) as cm:
            self.check(content, **kw)
        self.assertEqual(cm.exception.reason, reason, str(cm.exception))

    def test_valid_decisions(self) -> None:
        self.check(self._dec())
        self.check(self._dec(decision="restore", restore_paths=["jobs/j/work/a.txt"],
                             unresolved=[{"path": "../x.txt", "disposition": "restore"}]))
        self.check(self._dec(decision="abandon", unresolved=[{"path": "../x.txt", "disposition": "abandon-path"}],
                             note="owner abandons"))
        self.check(self._dec(unresolved=[]), rows=self._rows(a4=()))         # no A-4 path: nothing to cover
        self.assertEqual(reconcile.unresolvable_paths(json.loads(self._rows()[2]["content"])), ["../x.txt"])

    def test_refusals_in_contract_order(self) -> None:
        self.refused("malformed", self._dec(role="reviewer"))
        self.refused("malformed", {k: v for k, v in self._dec().items() if k != "restore_paths"})
        self.refused("malformed", self._dec(unresolved=[{"path": "../x.txt"}]))
        self.refused("malformed", self._dec(report_seq="4"))                          # non-None wrong type
        self.refused("malformed", self._dec(stop_seq=3.0))
        self.refused("no-report", self._dec(), rows=self._rows()[:2])
        # WF-001 F1: None means the wiring found no row to cite; the frozen refusals must be reachable
        self.refused("no-report", self._dec(report_seq=None), rows=self._rows()[:2])
        self.refused("stale-report", self._dec(report_seq=None))                       # a report exists
        self.refused("termination-unconfirmed", self._dec(stop_seq=None))              # report ok, no stop cited
        self.refused("termination-unconfirmed", self._dec(stop_seq=None), stopped_seq=None)
        self.refused("stale-report", self._dec(), rows=self._rows(two_reports=True))   # 4 is no longer latest
        self.refused("termination-unconfirmed", self._dec(), stopped_seq=None)
        self.refused("termination-unconfirmed", self._dec(stop_seq=2))
        self.refused("invalid-decision", self._dec(decision="retry"))
        self.refused("invalid-decision", self._dec(decision="restore"))                  # restore without paths
        self.refused("invalid-decision", self._dec(decision="restore", restore_paths=["jobs/j/work/other.txt"]))
        self.refused("invalid-decision", self._dec(unresolved=[]))                       # A-4 path not covered
        self.refused("invalid-decision", self._dec(unresolved=[{"path": "../x.txt", "disposition": "ignore"}]))
        self.refused("invalid-decision", self._dec(unresolved=[{"path": "../x.txt", "disposition": "restore"}] * 2))
        self.refused("invalid-decision", self._dec(unresolved=[{"path": "../x.txt", "disposition": "restore"},
                                                               {"path": "jobs/j/work/a.txt", "disposition": "restore"}]))
        self.refused("no-provenance", self._dec(owner_turn="decide now"))

    def test_real_decision_is_an_ordinary_mutation(self) -> None:
        self.new_job(); self.activate()
        c, r = self.intent()
        self.assertEqual(c, 0, r)
        stop = self.write("stop-confirmed", lambda rows, hs, hself: dict(STOP))
        root = changeset.boundary_root(self.db)
        assert root is not None
        base = json.loads(self.rows()[0]["content"])["baseline_manifest"]
        rep = self.write("reconcile-report", lambda rows, hs, hself: reconcile.compute_report(
            root, CONTRACT, base, rows, role="builder", binding=("B1", 1), head_seq=hs))
        dec = {"decision": "resume", "role": "builder", "report_seq": rep["seq"], "stop_seq": stop["seq"],
               "restore_paths": [], "unresolved": [], "owner_turn": "decide j"}

        def write_decision() -> dict:
            return self.write("recovery-decision", lambda rows, hs, hself: dict(dec),
                              check=lambda rows, c: reconcile.validate_recovery_decision(
                                  rows, job="j", binding=("B1", 1), stopped_seq=stop["seq"], content=c))
        self.begin()
        self.assert_rejected(write_decision, "recovery-open")                 # ordinary mutation (S2 §1)
        self.recover_reconcile()
        self.close()
        res = write_decision()
        self.assertEqual(res["outcome"], "COMMITTED")
        self.assertEqual(json.loads(self.rows()[-1]["content"])["decision"], "resume")
        # the report stays unacknowledged: its ack is reserved for the replacement session (S2 §3)
        self.assertTrue(reconcile.state(self.rows(), "builder")["unreconciled"])


if __name__ == "__main__":
    unittest.main()
