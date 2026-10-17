"""T06 reconcile + ack-reconcile (lane S1R, revised for lane S2R, Zen): module tests for `wfcore.reconcile` against
the slice-1 contract (issue #27 §1 schemas, §4 report / ack rules and CLASSES, §6 signatures and state keys, §9
D-S1-3) as amended by INTERFACE-CONTRACT-S2.md A-2 (write scope walked by compare; the old "not discoverable" pin is
removed), A-3 (precedence; matches-baseline with receipt evidence is an exception) and A-4 (unresolvable paths).
Spec: FIRST-RELEASE-SPEC.md §4.1 l.178, §6.2 steps 3 and 5, case T06 l.281.

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
from unittest import mock

from test_wf import Base, CONTRACT  # noqa: E402
from wfcore import changeset, db as dbm, gatelog, reconcile, txn, validate  # noqa: E402
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
        # S2 A-2: the old D-S1-5 "write scope not walked / not discoverable" limitation is gone; the A-3 meaning of
        # matches-baseline is stated; the compare limitations are carried too
        self.assertFalse(any("not discoverable" in x or "write scope is not walked" in x
                             for x in content["limitations"]), content["limitations"])
        self.assertTrue(any("never asserts that no operation executed" in x for x in content["limitations"]))
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
        # ... and once removed it is absent now and absent in the baseline: S2 A-3 (3) matches-baseline even though a
        # receipt exists; with receipt evidence it is an exception (D-S2-4) and keeps that evidence
        self.ack(res["seq"], [])
        os.remove(os.path.join(self.root(), *STRAY.split("/")))
        res2 = self.report()
        content2 = self.report_content(res2["seq"])
        cl2 = self.by_path(content2)
        self.assertEqual(cl2[STRAY]["class"], "matches-baseline")
        self.assertEqual((cl2[STRAY]["sha_now"], cl2[STRAY]["sha_baseline"], cl2[STRAY]["sha_receipt"]),
                         (None, None, _sha("gone")))
        self.assertIsNotNone(cl2[STRAY]["receipt_seq"])
        self.assertEqual(content2["exceptions"], [{"id": "X1", "path": STRAY, "class": "matches-baseline"}])

    def test_A3_receipt_then_reverted_is_matches_baseline_exception_with_receipt_evidence(self) -> None:
        """S2 A-3 (3) + D-S2-4: a receipt says the file changed, the disk equals the recorded baseline. The class
        states only current equality with the baseline; the receipt evidence survives and the row is an exception
        the ack must name."""
        self.new_job(); self.activate()
        rows = self.rows()
        base = self.baseline()
        base = {**base, "entries": {**base["entries"], WORK: {"sha256": _sha("v0"), "size": 2}}}
        self.write_file(WORK, "v0")                                      # disk == recorded baseline
        self.builder_intent()
        rec = self.builder_receipt(_sha("v1"))                           # receipt claims v1
        rows = self.rows()
        content = reconcile.compute_report(self.root(), CONTRACT, base, rows, role="builder", binding=("B1", 1),
                                           head_seq=rows[-1]["seq"])
        c = self.by_path(content)[WORK]
        self.assertEqual(c["class"], "matches-baseline")
        self.assertEqual((c["receipt_seq"], c["sha_receipt"], c["sha_now"], c["sha_baseline"]),
                         (rec["seq"], _sha("v1"), _sha("v0"), _sha("v0")))
        self.assertIsNotNone(c["intent_seq"])
        self.assertEqual(content["exceptions"], [{"id": "X1", "path": WORK, "class": "matches-baseline"}])
        reconcile.validate_content("reconcile-report", content, CONTRACT, None)

    def test_A3_zero_receipt_baseline_equal_is_matches_baseline_not_exception(self) -> None:
        """S2 A-3 (3): a compare-listed path with no intent/receipt whose content equals the recorded baseline is
        matches-baseline and NOT an exception (no receipt evidence). Fed through `changeset.compare` output at module
        level, since only compare can list such a path."""
        self.new_job(); self.activate()
        self.write_file(STRAY, "same")
        base = self.baseline()
        base = {**base, "entries": {**base["entries"], STRAY: {"sha256": _sha("same"), "size": 4}}}
        real = changeset.compare

        def listing(root: str, contract: dict, baseline: dict) -> dict:
            out = real(root, contract, baseline)
            return {**out, "modified": sorted(set(out["modified"]) | {STRAY})}
        rows = self.rows()
        with mock.patch.object(changeset, "compare", listing):
            content = reconcile.compute_report(self.root(), CONTRACT, base, rows, role="builder", binding=("B1", 1),
                                               head_seq=rows[-1]["seq"])
        c = self.by_path(content)[STRAY]
        self.assertEqual((c["class"], c["receipt_seq"], c["intent_seq"]), ("matches-baseline", None, None))
        self.assertEqual(content["exceptions"], [])

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

    @staticmethod
    def _hand_row(seq: int, etype: str, path: str, sha_after: str | None = None) -> dict[str, Any]:
        """A builder intent/receipt row built by hand: the CLI refuses an escaping path before any row exists, so
        S2 A-4 is reachable only at module level (White's WF-001 note)."""
        content: dict[str, Any] = {"path": path, "sha_before": "", "note": "n"}
        if sha_after is not None:
            content["sha_after"] = sha_after
        return {"seq": seq, "type": etype, "author_role": "builder", "content": json.dumps(content)}

    def test_A4_unresolvable_paths_are_matches_neither_with_null_shas_and_exact_limitation(self) -> None:
        self.new_job(); self.activate()
        head = self.rows()[-1]["seq"]
        odd = ["../outside.txt", "C:/abs/file.txt", "/rooted/file.txt"]
        rows = [*self.rows()]
        for i, p in enumerate(odd):
            rows.append(self._hand_row(head + 1 + 2 * i, "intent", p))
            rows.append(self._hand_row(head + 2 + 2 * i, "receipt", p, _sha("x")))
        content = reconcile.compute_report(self.root(), CONTRACT, self.baseline(), rows, role="builder",
                                           binding=("B1", 1), head_seq=rows[-1]["seq"])
        for p in odd:
            c = self.by_path(content)[p]
            self.assertEqual((c["class"], c["sha_now"], c["sha_baseline"]), ("matches-neither", None, None), c)
            self.assertIn(reconcile.UNRESOLVABLE.format(p), content["limitations"])
        self.assertIn("unresolvable path ../outside.txt: filesystem state unknown; inspection limitation",
                      content["limitations"])                            # the A-4 string, verbatim
        self.assertEqual(sorted(e["path"] for e in content["exceptions"]), sorted(odd))
        self.assertEqual(sorted(reconcile.unresolvable_paths(content)), sorted(odd))
        # an unresolvable intent without receipt is still A-4 (state unknown), not attempt-without-completion
        rows2 = [*self.rows(), self._hand_row(head + 1, "intent", "../only-intent.txt")]
        c2 = reconcile.compute_report(self.root(), CONTRACT, self.baseline(), rows2, role="builder", binding=None,
                                      head_seq=head + 1)
        self.assertEqual(self.by_path(c2)["../only-intent.txt"]["class"], "matches-neither")

    def test_A4_path_crossing_a_junction_is_unresolvable_and_never_followed(self) -> None:
        try:
            import _winapi
            create = _winapi.CreateJunction
        except (ImportError, AttributeError) as exc:
            self.skipTest(f"no junction support here: {exc}")
        self.new_job(); self.activate()
        target = os.path.join(self.dir, "elsewhere")
        os.makedirs(target)
        with open(os.path.join(target, "f.txt"), "w", encoding="utf-8") as fh:
            fh.write("behind the junction")
        os.makedirs(os.path.join(self.root(), "jobs", "j", "work"), exist_ok=True)
        create(target, os.path.join(self.root(), "jobs", "j", "work", "jn"))
        rows = [*self.rows(), self._hand_row(self.head() + 1, "intent", "jobs/j/work/jn/f.txt"),
                self._hand_row(self.head() + 2, "receipt", "jobs/j/work/jn/f.txt", _sha("behind the junction"))]
        content = reconcile.compute_report(self.root(), CONTRACT, self.baseline(), rows, role="builder", binding=None,
                                           head_seq=rows[-1]["seq"])
        c = self.by_path(content)["jobs/j/work/jn/f.txt"]
        self.assertEqual((c["class"], c["sha_now"]), ("matches-neither", None))   # not matches-receipt via the link
        self.assertEqual(reconcile.unresolvable_paths(content), ["jobs/j/work/jn/f.txt"])

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

    def test_A2_unrecorded_write_scope_change_is_matches_neither(self) -> None:
        """S2 A-2 replaces the old D-S1-5 pin (removed: it asserted the gap A-2 closes). Once `changeset.compare`
        walks the write scope (lane S2B), an unrecorded P1 change reaches reconcile as an added path and is
        matches-neither. compare is S2B's file, not on main yet, so its A-2 output is fed here as compare's documented
        result shape; the end-to-end walk is S2B's test_t35_changeset and the combined candidate."""
        self.new_job(); self.activate()
        silent = "jobs/j/work/silent.txt"
        self.write_file(silent, "no intent, no receipt")
        real = changeset.compare

        def a2(root: str, contract: dict, baseline: dict) -> dict:
            out = real(root, contract, baseline)
            return out if silent in out["added"] else {**out, "added": sorted([*out["added"], silent])}
        with mock.patch.object(changeset, "compare", a2):
            res = self.report(binding=None)
        content = self.report_content(res["seq"])
        c = self.by_path(content)[silent]
        self.assertEqual((c["class"], c["sha_now"], c["sha_baseline"], c["intent_seq"], c["receipt_seq"]),
                         ("matches-neither", _sha("no intent, no receipt"), None, None, None))
        self.assertEqual(content["exceptions"], [{"id": "X1", "path": silent, "class": "matches-neither"}])
        self.assertEqual((content["session"], content["generation"]), (None, None))
        self.assertFalse(any("not discoverable" in x for x in content["limitations"]))

    @unittest.skipUnless("write_scope" in changeset._SCOPE_KEYS,
                         "S2 A-2 changeset.compare (lane S2B) not on this base: runs in the combined candidate")
    def test_A2_unrecorded_write_scope_change_through_real_compare(self) -> None:
        self.new_job(); self.activate()
        self.write_file("jobs/j/work/silent2.txt", "x")
        content = self.compute(binding=None)
        self.assertEqual(self.by_path(content)["jobs/j/work/silent2.txt"]["class"], "matches-neither")


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


class GateAttempts(_Engine):
    """B2 slice 4a (INTERFACE-CONTRACT-S4A section 5, lane S4L): `compute_report(..., gate_attempts=...)`."""

    @staticmethod
    def attempt(path: str = WORK, *, ts: str = "2000-01-01T00:00:00Z", decision: str = "allow",
                channel: str = "preventable", seq: int = 1) -> dict:
        return {"seq": seq, "ts": ts, "session_id": "B1", "tool_name": "Write", "channel": channel,
                "decision": decision, "reason": "x", "jobs": ["j"], "paths": [path]}

    def compute_gate(self, attempts: list[dict] | None) -> dict:
        rows = self.rows()
        return reconcile.compute_report(self.root(), CONTRACT, self.baseline(), rows, role="builder",
                                        binding=("B1", 1), head_seq=rows[-1]["seq"], gate_attempts=attempts)

    def test_none_keeps_behaviour_and_says_not_consulted(self) -> None:
        self.new_job(); self.activate()
        self.builder_intent()
        content = self.compute_gate(None)
        self.assertEqual(content, self.compute())                       # default == explicit None
        self.assertIn(reconcile.GATE_NOT_CONSULTED, content["limitations"])
        self.assertIn("no gate in R1: gate_log is null (unavailable), never a count of attempts", content["limitations"])
        self.assertNotIn(reconcile.GATE_SOURCE, content["limitations"])
        self.assertIsNone(content["gate_log"])
        self.assertEqual(content["exceptions"], [{"id": "X1", "path": WORK, "class": "attempt-without-completion"}])

    def test_allowed_attempt_without_receipt_is_attempt_without_completion(self) -> None:
        self.new_job(); self.activate()
        content = self.compute_gate([self.attempt()])
        row = self.by_path(content)[WORK]
        self.assertEqual((row["class"], row["intent_seq"], row["receipt_seq"]), ("attempt-without-completion", None, None))
        self.assertEqual(content["exceptions"], [{"id": "X1", "path": WORK, "class": "attempt-without-completion"}])
        self.assertIn(reconcile.GATE_SOURCE, content["limitations"])
        self.assertIn("gate attempts", reconcile.GATE_SOURCE)
        self.assertNotIn(reconcile.GATE_NOT_CONSULTED, content["limitations"])
        self.assertFalse(any(x.startswith("no gate in R1") for x in content["limitations"]))
        self.assertIsNone(content["gate_log"])                            # schema: gate_log stays null
        self.assertEqual(self.compute_gate([])["exceptions"], [])         # supplied but empty: nothing added

    def test_later_receipt_completes_the_attempt_earlier_receipt_does_not(self) -> None:
        self.new_job(); self.activate()
        self.builder_intent()
        self.write_file(WORK, "done")
        self.builder_receipt(_sha("done"))
        content = self.compute_gate([self.attempt(ts="2000-01-01T00:00:00Z")])      # receipt is later: completed
        self.assertEqual(self.by_path(content)[WORK]["class"], "matches-receipt")
        self.assertEqual(content["exceptions"], [])
        content = self.compute_gate([self.attempt(ts="2999-01-01T00:00:00Z")])      # attempt after the receipt
        self.assertEqual(self.by_path(content)[WORK]["class"], "attempt-without-completion")
        content = self.compute_gate([self.attempt(ts="not a time")])                # unreadable: never completed
        self.assertEqual(self.by_path(content)[WORK]["class"], "attempt-without-completion")

    def test_denials_shell_other_and_out_of_scope_attempts_never_classify(self) -> None:
        self.new_job(); self.activate()
        attempts = [self.attempt(decision="deny"), self.attempt(channel="shell"), self.attempt(channel="other"),
                    self.attempt("tools/wf/outside-write-scope.txt"), self.attempt("ledger/l.db")]
        content = self.compute_gate(attempts)
        self.assertEqual(content["classification"], [])
        self.assertEqual(content["exceptions"], [])
        self.assertIn(reconcile.GATE_SOURCE, content["limitations"])

    def test_rows_from_the_real_gate_log_through_the_report_write(self) -> None:
        self.new_job(); self.activate()
        root = self.root()
        d = {"decision": "allow", "reason": "allowed", "channel": "preventable", "session": "B1", "tool_name": "Write",
             "jobs": ["j"], "paths": [WORK], "command_sha256": None, "command_length": None, "limitation": None}
        gatelog.record(root, d, payload_sha256=_sha("payload"), ts="2000-01-01T00:00:00Z")
        gatelog.record(root, dict(d, channel="shell", tool_name="Bash", paths=[]), payload_sha256=_sha("p2"),
                       ts="2000-01-01T00:00:01Z")
        attempts = gatelog.attempts(root, "j")
        self.assertEqual(len(attempts), 2)
        res = self._write(etype="reconcile-report", role="coordinator", session="C1",
                          content_fn=lambda conn, rows, head: reconcile.compute_report(
                              root, CONTRACT, self.baseline(), rows, role="builder", binding=("B1", 1),
                              head_seq=head, gate_attempts=attempts))
        content = self.report_content(res["seq"])                          # accepted by the section 4 schema
        self.assertEqual(content["exceptions"], [{"id": "X1", "path": WORK, "class": "attempt-without-completion"}])
        self.assertEqual(set(content) - {"contract_sha256"}, REPORT_KEYS)


if __name__ == "__main__":
    unittest.main()
