"""T09 engine-level check (Part B, owner-approved 2026-10-13): validate.build_row rejects an empty or blank
msg_id, author_role or session_id with WfRejected reason "malformed" and the text "missing field <name>".

Scope. One test method, one subTest per (field, value). build_row is called directly with otherwise valid inputs
taken from a real scratch ledger (job created and grant activated through the wf CLI in fresh processes), so the
call reaches the first validation branch of build_row (validate.py: the missing-field loop, which runs before any use
of the connection). The CLI cannot reach that branch for an empty value because wf.py `_need` rejects it first.

Not asserted, not claimed. This test does not append anything, does not prove ledger immutability, does not cover any
other malformed-input case (generation, role, type, content), and does not change the T09 coverage word or gap text.
A head read before and after the calls is recorded only to show that build_row returned without writing a row.
"""
from __future__ import annotations

import json
import unittest

from test_wf import Base, log  # noqa: E402  (sets sys.path to the wf package)
from wfcore import db as dbm, validate  # noqa: E402
from wfcore.db import WfRejected  # noqa: E402

FIELDS = ("msg_id", "author_role", "session_id")
BLANKS = ("", " ", "   ", "\t", "\n", " \t\r\n ")


class T09EngineMissingFields(Base):
    def test_T09_engine_build_row_rejects_missing_identity_fields(self):
        self.new_job()
        self.activate()
        conn = dbm.connect(self.db, create=False)
        try:
            head_seq, head_self = dbm.head(conn, "j")
            self.assertGreaterEqual(head_seq, 1, "job must have a contract row before build_row is exercised")
            c = dbm.contract(conn, "j")
            self.assertIsNotNone(c, "contract row missing")
            valid = dict(etype="intent", msg_id="m-t09-engine-1", author_role="builder", session_id="B1", generation=1,
                         reply_to=None, grant_ref=None,
                         content={"path": "jobs/j/work/a.txt", "sha_before": "", "note": "t09 engine control"},
                         contract_sha=c["sha256"], contract_content=json.loads(c["content"]))

            # Control: the same inputs with every identity field present build a row at head+1 (nothing is written).
            row = validate.build_row(conn, "j", head_seq, head_self, **valid)
            self.assertEqual(row["seq"], head_seq + 1, row)
            self.assertEqual(row["prev"], head_self, row)

            observed = []
            for field in FIELDS:
                for value in BLANKS:
                    with self.subTest(field=field, value=repr(value)):
                        bad = dict(valid, **{field: value})
                        with self.assertRaises(WfRejected) as cm:
                            validate.build_row(conn, "j", head_seq, head_self, **bad)
                        exc = cm.exception
                        self.assertEqual(exc.reason, "malformed", exc)
                        self.assertEqual(exc.code, "rejected", exc)
                        self.assertEqual(str(exc), f"rejected(malformed): missing field {field}", exc)
                        observed.append({"field": field, "value": value, "reason": exc.reason, "code": exc.code,
                                         "message": str(exc)})

            # Observation only (not an immutability claim): no row was written by any of the calls above.
            self.assertEqual(dbm.head(conn, "j"), (head_seq, head_self))
        finally:
            conn.close()
        log(f"{self._testMethodName}-summary", {
            "head_before": {"seq": head_seq, "self": head_self}, "control_row_seq": row["seq"],
            "fields": FIELDS, "blank_values": [repr(v) for v in BLANKS], "subtests": len(observed),
            "observed": observed, "head_after": {"seq": head_seq, "self": head_self}})


if __name__ == "__main__":
    unittest.main()
