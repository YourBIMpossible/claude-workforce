"""Gate replay of the slice-3 grant-check corpus (B2 slice 4a, lane S4G, Plexy): INTERFACE-CONTRACT-S4A.md §7 S4G
and owner item 4 ("reuse slice 3 grant decision logic and replay its unchanged 47-case corpus").

`tools/wf/tests/data/grant_check_corpus.json` is loaded unchanged: its sha256 is asserted equal to main's at the
slice-4a base (main 0060997, the file as integrated in slice 3). Every case's `rows_fixture` is materialised into a
fixture SQLite ledger (schema from `db.connect`, rows hash-chained exactly like `txn.write_event`, the contracts row
present) under the test temp root. For every case where the checked role has an explicit binding and the action is
`write` (the gate's only action), the gate's decision for that bound session and the case's path equals
`grant.check`'s allow / deny on the same rows. Hard limits honoured: fixtures only, no hook registered or invoked.
"""
from __future__ import annotations

import hashlib
import json
import os
import unittest

from test_t34_grant_lifecycle import JOB, binding_of, build_rows  # noqa: E402
from test_wf import Base, CONTRACT  # noqa: E402
from wfcore import db as dbm, gatecore, grant  # noqa: E402
from wfcore.canon import canonical, event_self_hash, sha256_text  # noqa: E402

CORPUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "grant_check_corpus.json")
#: sha256 of tools/wf/tests/data/grant_check_corpus.json on main 00609971c46885d6bed7761589f2b2ef1affca7b
MAIN_CORPUS_SHA256 = "103e0e1434c43f16e90a727530dd61ef84b6be84e8f5ce0e0d133138deb6ca03"


def load() -> list[dict]:
    with open(CORPUS, "r", encoding="utf-8") as fh:
        return json.load(fh)


def materialise(rows: list[dict], db_path: str) -> None:
    """Write fixture rows into a new ledger: chained prev/self, content_sha256, and the contracts row."""
    conn = dbm.connect(db_path, create=True)
    try:
        prev = dbm.GENESIS
        with conn:
            first = json.loads(rows[0]["content"])
            conn.execute("INSERT INTO contracts (job, sha256, content, created_ts) VALUES (?,?,?,?)",
                         (JOB, first["contract_sha256"], canonical(first["contract"]), rows[0]["ts"]))
            for r in rows:
                row = dict(r, content_sha256=sha256_text(r["content"]), prev=prev)
                row["self"] = event_self_hash(row)
                conn.execute("INSERT INTO events (job,seq,type,msg_id,author_role,session_id,generation,reply_to,"
                             "grant_ref,ts,content,content_sha256,prev,self) VALUES (:job,:seq,:type,:msg_id,"
                             ":author_role,:session_id,:generation,:reply_to,:grant_ref,:ts,:content,:content_sha256,"
                             ":prev,:self)", row)
                prev = row["self"]
    finally:
        conn.close()


class CorpusReplay(Base):
    def test_corpus_is_unchanged_from_main(self):
        with open(CORPUS, "rb") as fh:
            self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), MAIN_CORPUS_SHA256)
        self.assertEqual(len(load()), 47)

    def test_gate_equals_grant_check_for_every_bound_case(self):
        replayed = []
        for i, c in enumerate(load()):
            rows = build_rows(c["rows_fixture"])
            binding = binding_of(rows, c["role"])
            if binding is None or c["action"] != "write":
                continue
            session = next(json.loads(r["content"])["session"] for r in reversed(rows)
                           if r["type"] == "bind" and json.loads(r["content"])["role"] == c["role"])
            root = os.path.join(self.dir, f"case{i:02d}")
            db_path = os.path.join(root, "ledger", "l.db")
            os.makedirs(os.path.dirname(db_path))
            materialise(rows, db_path)
            want = grant.check(rows, job=JOB, now=c["now"], role=c["role"], action="write", path=c["path"],
                               contract=CONTRACT, binding=binding)
            payload = {"session_id": session, "hook_event_name": "PreToolUse", "tool_name": "Write",
                       "tool_input": {"file_path": os.path.join(root, *c["path"].split("/")), "content": "x"},
                       "cwd": root}
            got = gatecore.decide(payload, root=root, db_path=db_path, now=c["now"], gate_files=[])
            with self.subTest(case=c["name"]):
                self.assertEqual(got["decision"] == "allow", want["allow"], (got, want))
                self.assertEqual(c["expect_allow"], want["allow"])            # the corpus expectation still holds
                if want["allow"] or got["reason"] in gatecore.REASONS and got["reason"].startswith(("path", "binding",
                                                                                                    "role", "unbound")):
                    self.assertEqual(got["reason"], want["reason"])
            replayed.append(c["name"])
        self.assertGreaterEqual(len(replayed), 3, replayed)
        self.assertIn("allow_bound_role_explicit", replayed)
        self.assertIn("deny_binding_outside_new_ars_after_renewal", replayed)
        self.assertIn("allow_binding_inside_new_ars_after_renewal", replayed)

    def test_materialised_ledgers_verify(self):
        """The fixture ledgers are real chains (so rule 2 is exercised honestly, not bypassed)."""
        c = next(x for x in load() if x["name"] == "allow_bound_role_explicit")
        db_path = os.path.join(self.dir, "ledger", "l.db")
        os.makedirs(os.path.dirname(db_path))
        materialise(build_rows(c["rows_fixture"]), db_path)
        conn = dbm.connect(db_path, create=False)
        try:
            self.assertEqual(dbm.verify_chain(conn, JOB), [])
            self.assertEqual(dbm.jobs(conn), [JOB])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
