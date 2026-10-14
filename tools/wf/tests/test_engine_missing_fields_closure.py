"""T09 closure package (owner-approved 2026-10-13): the sub-requirements of adopted T09 variant 1-A that
tools/wf/tests/test_engine_missing_fields.py (Part B) left open. Coordinator-tested; NOT reviewer-verified.

Four groups, one class:
  G1 engine MISSING (not empty): validate.build_row with None for msg_id / author_role / session_id ->
     WfRejected reason "malformed", text "missing field <name>"; head unchanged.
  G2 append path, ledger unchanged: commands._append (the function wf append calls after the CLI check) with an empty
     and a whitespace-only role / session -> WfRejected malformed; head, rows and verify unchanged.
  G3 CLI whitespace: `wf append` in a fresh process with --role " " and --session " " -> exit 2, code "usage",
     "--<f> is required for this command"; head unchanged. Depends on the one approved wf.py `_need` change and is
     expected to FAIL on the pre-change revision (that failing run is preserved as evidence).
  G4 payload / contract missing-field label: validate_content and validate_contract raise WfRejected reason
     "malformed" when a required key is absent; the exact text is recorded, not prescribed.

Not asserted, not claimed: ledger immutability, other malformed cases beyond the listed keys, engine or B1 acceptance.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from test_wf import Base, CONTRACT, WF_DIR, log  # noqa: E402  (sets sys.path to the wf package)
from wfcore import commands, db as dbm, validate  # noqa: E402
from wfcore.db import OpLog, WfRejected  # noqa: E402
from wfcore.faults import Faults  # noqa: E402

FIELDS = ("msg_id", "author_role", "session_id")
INTENT = {"path": "jobs/j/work/a.txt", "sha_before": "", "note": "t09 closure"}


class T09Closure(Base):
    def _head(self) -> tuple[int, str]:
        conn = dbm.connect(self.db, create=False)
        try:
            return dbm.head(conn, "j")
        finally:
            conn.close()

    def _rows(self) -> list[dict]:
        conn = dbm.connect(self.db, create=False)
        try:
            return dbm.rows(conn, "j")
        finally:
            conn.close()

    # ---- G1 ----
    def test_T09_G1_engine_rejects_missing_none_identity_fields(self):
        self.new_job(); self.activate()
        conn = dbm.connect(self.db, create=False)
        try:
            head_seq, head_self = dbm.head(conn, "j")
            c = dbm.contract(conn, "j")
            valid = dict(etype="intent", msg_id="m-g1", author_role="builder", session_id="B1", generation=1,
                         reply_to=None, grant_ref=None, content=dict(INTENT),
                         contract_sha=c["sha256"], contract_content=json.loads(c["content"]))
            observed = []
            for field in FIELDS:
                with self.subTest(field=field, value=None):
                    bad = dict(valid, **{field: None})
                    with self.assertRaises(WfRejected) as cm:
                        validate.build_row(conn, "j", head_seq, head_self, **bad)
                    exc = cm.exception
                    self.assertEqual(exc.reason, "malformed", exc)
                    self.assertEqual(str(exc), f"rejected(malformed): missing field {field}", exc)
                    observed.append({"field": field, "value": None, "message": str(exc)})
            self.assertEqual(dbm.head(conn, "j"), (head_seq, head_self))
        finally:
            conn.close()
        log(f"{self._testMethodName}-summary", {"head": [head_seq, head_self], "observed": observed})

    # ---- G2 ----
    def test_T09_G2_append_path_rejects_blank_role_or_session_ledger_unchanged(self):
        self.new_job(); self.activate()
        head_before = self._head()
        rows_before = self._rows()
        verify_before = self.run_wf("verify", "j")[1]
        self.assertTrue(verify_before.get("clean"), verify_before)
        observed = []
        for field in ("role", "session"):
            for value in ("", " ", "\t"):
                with self.subTest(field=field, value=repr(value)):
                    kw = dict(etype="intent", content=dict(INTENT), msg_id=f"m-g2-{field}-{len(observed)}",
                              role="builder", session="B1", generation=1)
                    kw[field] = value
                    with self.assertRaises(WfRejected) as cm:
                        commands._append(self.db, "j", Faults(), OpLog(), **kw)
                    exc = cm.exception
                    self.assertEqual(exc.reason, "malformed", exc)
                    expect = "author_role" if field == "role" else "session_id"
                    self.assertEqual(str(exc), f"rejected(malformed): missing field {expect}", exc)
                    observed.append({"field": field, "value": value, "message": str(exc)})
        self.assertEqual(self._head(), head_before)
        self.assertEqual(self._rows(), rows_before)
        verify_after = self.run_wf("verify", "j")[1]
        self.assertTrue(verify_after.get("clean"), verify_after)
        self.assertEqual(verify_after.get("head"), verify_before.get("head"), (verify_before, verify_after))
        log(f"{self._testMethodName}-summary", {"head_before": list(head_before), "head_after": list(self._head()),
                                                "rows": len(rows_before), "observed": observed,
                                                "verify_before": verify_before, "verify_after": verify_after})

    # ---- G3 ----
    def test_T09_G3_cli_whitespace_role_or_session_is_usage_error(self):
        self.new_job(); self.activate()
        head_before = self._head()
        env = {k: v for k, v in os.environ.items() if k not in ("WF_ROLE", "WF_SESSION")}
        observed = []
        for missing, role, session in (("role", " ", "B1"), ("session", "builder", " "), ("role", "\t", " ")):
            with self.subTest(missing=missing, role=repr(role), session=repr(session)):
                argv = [sys.executable, "-I", "-B", os.path.join(WF_DIR, "wf.py"), "--json", "--db", self.db, "--role", role,
                        "--session", session, "append", "j", "--type", "intent", "--content", json.dumps(INTENT)]
                p = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=60)
                try:
                    res = json.loads(p.stdout)
                except json.JSONDecodeError:
                    res = {"stdout": p.stdout, "stderr": p.stderr}
                observed.append({"missing": missing, "role": role, "session": session, "exit": p.returncode, "res": res})
                self.assertEqual(p.returncode, 2, observed[-1])
                self.assertFalse(res.get("ok", True), res)
                self.assertEqual(res.get("code"), "usage", res)
                self.assertIn(f"--{missing} is required for this command", res.get("error", ""), res)
        self.assertEqual(self._head(), head_before)
        log(f"{self._testMethodName}-summary", {"head_before": list(head_before), "head_after": list(self._head()),
                                                "observed": observed})

    # ---- G4 ----
    def test_T09_G4_payload_and_contract_missing_field_label(self):
        observed = []
        for key in ("path", "sha_before", "note"):
            with self.subTest(kind="payload", key=key):
                content = {k: v for k, v in INTENT.items() if k != key}
                with self.assertRaises(WfRejected) as cm:
                    validate.validate_content("intent", content, dict(CONTRACT), None)
                exc = cm.exception
                self.assertEqual(exc.reason, "malformed", exc)
                self.assertEqual(str(exc), f"rejected(malformed): content missing {key!r}", exc)
                observed.append({"kind": "payload", "key": key, "message": str(exc)})
        for key in sorted(validate.CONTRACT_REQUIRED):
            with self.subTest(kind="contract", key=key):
                contract = {k: v for k, v in CONTRACT.items() if k != key}
                with self.assertRaises(WfRejected) as cm:
                    validate.validate_contract(contract)
                exc = cm.exception
                self.assertEqual(exc.reason, "malformed", exc)
                self.assertEqual(str(exc), f"rejected(malformed): contract missing field {key!r}", exc)
                observed.append({"kind": "contract", "key": key, "message": str(exc)})
        log(f"{self._testMethodName}-summary", {"observed": observed, "contract_required": sorted(validate.CONTRACT_REQUIRED)})


if __name__ == "__main__":
    unittest.main()
