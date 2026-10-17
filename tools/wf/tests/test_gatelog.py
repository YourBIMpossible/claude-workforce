"""Gate log tests (B2 slice 4a, lane S4L; INTERFACE-CONTRACT-S4A sections 3 and 6, section 7 S4L).

Chain (prev/self, genesis), `verify` (present, rows, chain problems, unlogged attempts), tamper detection (altered
row, removed row, triggers refusing update/delete), `attempts` filtering (job, since_seq, decoded jobs/paths), the
audit-failures file and `unlogged_attempts`, write failures raising GateLogError with nothing appended, concurrent
writers, the S4A-A1 `probe_fault` column, read-only behaviour of verify/attempts, and the frozen import set.
Fixture roots only (WF_TEST_TMP); nothing is deleted.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import sqlite3
import threading
import unittest

from test_wf import Base  # noqa: E402
from wfcore import canon, gatelog  # noqa: E402

PAYLOAD_SHA = hashlib.sha256(b"payload").hexdigest()
TS = "2026-10-17T08:00:00Z"


def decision(*, decision="allow", reason="allowed", channel="preventable", tool="Write", session="B1",
             jobs=("j",), paths=("jobs/j/work/a.txt",), **extra) -> dict:
    d = {"decision": decision, "reason": reason, "channel": channel, "session": session, "tool_name": tool,
         "jobs": list(jobs), "paths": list(paths), "command_sha256": None, "command_length": None, "limitation": None}
    d.update(extra)
    return d


class GateLog(Base):
    def root(self) -> str:
        return self.dir

    def gate_db(self) -> str:
        return os.path.join(self.root(), "runtime", "gate", "gate.db")

    def failures(self) -> str:
        return os.path.join(self.root(), "runtime", "gate", "gate-audit-failures.log")

    def raw_rows(self) -> list[dict]:
        conn = sqlite3.connect(self.gate_db())
        try:
            cur = conn.execute(f"SELECT {','.join(gatelog.COLUMNS)} FROM attempts ORDER BY seq")
            return [dict(zip(gatelog.COLUMNS, r)) for r in cur.fetchall()]
        finally:
            conn.close()

    def tree(self) -> set[str]:
        out = set()
        for d, dirs, files in os.walk(self.root()):
            for n in dirs + files:
                out.add(os.path.relpath(os.path.join(d, n), self.root()).replace("\\", "/"))
        return out

    # ---- constants and location
    def test_frozen_names(self):
        self.assertEqual(gatelog.GATE_DB_REL, "runtime/gate/gate.db")
        self.assertEqual(gatelog.FAILURES_REL, "runtime/gate/gate-audit-failures.log")
        self.assertTrue(issubclass(gatelog.GateLogError, Exception))

    # ---- chain
    def test_record_chains_rows_from_genesis(self):
        seqs = [gatelog.record(self.root(), decision(paths=(f"jobs/j/work/{i}.txt",)), payload_sha256=PAYLOAD_SHA,
                               ts=TS) for i in range(3)]
        self.assertEqual(seqs, [1, 2, 3])
        rows = self.raw_rows()
        self.assertEqual(rows[0]["prev"], "0" * 64)
        for prev, row in zip(rows, rows[1:]):
            self.assertEqual(row["prev"], prev["self"])
        for row in rows:
            expected = canon.sha256_text(canon.canonical({k: row[k] for k in gatelog.COLUMNS if k != "self"}))
            self.assertEqual(row["self"], expected)
        self.assertEqual(json.loads(rows[2]["paths_json"]), ["jobs/j/work/2.txt"])
        self.assertEqual((rows[0]["session_id"], rows[0]["tool_name"], rows[0]["channel"], rows[0]["decision"],
                          rows[0]["reason"], rows[0]["payload_sha256"]),
                         ("B1", "Write", "preventable", "allow", "allowed", PAYLOAD_SHA))
        v = gatelog.verify(self.root())
        self.assertEqual(v, {"present": True, "rows": 3, "chain_problems": [], "unlogged_attempts": 0})

    def test_shell_row_keeps_hash_and_length_only(self):
        cmd_sha = hashlib.sha256(b"rm -rf x").hexdigest()
        gatelog.record(self.root(), decision(channel="shell", tool="Bash", paths=(), command_sha256=cmd_sha,
                                             command_length=8, reason="shell-not-preventable",
                                             limitation="shell commands are recorded as invocations only"),
                       payload_sha256=PAYLOAD_SHA, ts=TS)
        row = self.raw_rows()[0]
        self.assertEqual((row["command_sha256"], row["command_length"], row["paths_json"]), (cmd_sha, 8, "[]"))
        self.assertNotIn("rm -rf", json.dumps(row))

    def test_probe_fault_recorded_and_hashed(self):
        gatelog.record(self.root(), decision(probe_fault="timeout"), payload_sha256=PAYLOAD_SHA, ts=TS)
        gatelog.record(self.root(), decision(), payload_sha256=PAYLOAD_SHA, ts=TS)
        rows = self.raw_rows()
        self.assertEqual([r["probe_fault"] for r in rows], ["timeout", None])
        self.assertEqual(gatelog.verify(self.root())["chain_problems"], [])
        self.assertEqual([a["probe_fault"] for a in gatelog.attempts(self.root())], ["timeout", None])

    # ---- tamper detection
    def test_triggers_refuse_update_and_delete(self):
        gatelog.record(self.root(), decision(), payload_sha256=PAYLOAD_SHA, ts=TS)
        conn = sqlite3.connect(self.gate_db())
        try:
            for sql in ("UPDATE attempts SET reason='x'", "DELETE FROM attempts"):
                with self.assertRaises(sqlite3.DatabaseError):
                    conn.execute(sql)
        finally:
            conn.close()

    def _tamper(self, sql: str) -> None:
        conn = sqlite3.connect(self.gate_db(), isolation_level=None)
        try:
            conn.execute("DROP TRIGGER attempts_no_update")
            conn.execute("DROP TRIGGER attempts_no_delete")
            conn.execute(sql)
        finally:
            conn.close()

    def test_altered_row_detected(self):
        for _ in range(3):
            gatelog.record(self.root(), decision(), payload_sha256=PAYLOAD_SHA, ts=TS)
        self._tamper("UPDATE attempts SET decision='allow', reason='altered' WHERE seq=2")
        v = gatelog.verify(self.root())
        self.assertEqual(v["rows"], 3)
        self.assertEqual(v["chain_problems"], ["seq 2: self does not match the row content"])

    def test_removed_row_detected(self):
        for _ in range(3):
            gatelog.record(self.root(), decision(), payload_sha256=PAYLOAD_SHA, ts=TS)
        self._tamper("DELETE FROM attempts WHERE seq=2")
        problems = gatelog.verify(self.root())["chain_problems"]
        self.assertIn("seq 3: expected seq 2 (gap or reorder)", problems)
        self.assertIn("seq 3: prev does not match the previous row's self", problems)

    def test_unreadable_gate_db_reported(self):
        os.makedirs(os.path.dirname(self.gate_db()))
        with open(self.gate_db(), "wb") as fh:
            fh.write(b"this is not a sqlite database" * 64)
        v = gatelog.verify(self.root())
        self.assertTrue(v["present"])
        self.assertEqual(len(v["chain_problems"]), 1)
        self.assertTrue(v["chain_problems"][0].startswith("gate log unreadable: "), v)

    # ---- attempts
    def test_attempts_filtering_and_decoding(self):
        gatelog.record(self.root(), decision(jobs=("j",)), payload_sha256=PAYLOAD_SHA, ts=TS)
        gatelog.record(self.root(), decision(jobs=("k",), paths=("jobs/k/work/b.txt",)), payload_sha256=PAYLOAD_SHA,
                       ts=TS)
        gatelog.record(self.root(), decision(jobs=("j", "k"), decision="deny", reason="unbound-session"),
                       payload_sha256=PAYLOAD_SHA, ts=TS)
        gatelog.record(self.root(), decision(jobs=(), paths=(), channel="other", tool="Read", reason="not-mediated"),
                       payload_sha256=PAYLOAD_SHA, ts=TS)
        self.assertEqual([a["seq"] for a in gatelog.attempts(self.root())], [1, 2, 3, 4])
        self.assertEqual([a["seq"] for a in gatelog.attempts(self.root(), "j")], [1, 3])
        self.assertEqual([a["seq"] for a in gatelog.attempts(self.root(), "k")], [2, 3])
        self.assertEqual([a["seq"] for a in gatelog.attempts(self.root(), "j", since_seq=1)], [3])
        self.assertEqual([a["seq"] for a in gatelog.attempts(self.root(), since_seq=4)], [])
        a = gatelog.attempts(self.root(), "k")[0]
        self.assertEqual((a["jobs"], a["paths"]), (["k"], ["jobs/k/work/b.txt"]))
        self.assertEqual(set(gatelog.COLUMNS) | {"jobs", "paths"}, set(a))

    def test_read_functions_write_nothing(self):
        before = self.tree()
        self.assertEqual(gatelog.attempts(self.root()), [])
        self.assertEqual(gatelog.verify(self.root()), {"present": False, "rows": 0, "chain_problems": [],
                                                       "unlogged_attempts": 0})
        self.assertEqual(self.tree(), before)
        gatelog.record(self.root(), decision(), payload_sha256=PAYLOAD_SHA, ts=TS)
        with open(self.gate_db(), "rb") as fh:
            data = fh.read()
        before = self.tree()
        gatelog.verify(self.root())
        gatelog.attempts(self.root(), "j")
        self.assertEqual(self.tree(), before)
        with open(self.gate_db(), "rb") as fh:
            self.assertEqual(fh.read(), data)

    # ---- write failures and the failures file
    def test_invalid_decision_raises_and_appends_nothing(self):
        gatelog.record(self.root(), decision(), payload_sha256=PAYLOAD_SHA, ts=TS)
        bad = [(decision(decision="maybe"), PAYLOAD_SHA, TS), (decision(reason=""), PAYLOAD_SHA, TS),
               (decision(), "not-a-hash", TS), (decision(), PAYLOAD_SHA, ""), (decision(jobs=[1]), PAYLOAD_SHA, TS),
               (decision(command_length=-1), PAYLOAD_SHA, TS), ("not a dict", PAYLOAD_SHA, TS)]
        for d, sha, ts in bad:
            with self.assertRaises(gatelog.GateLogError):
                gatelog.record(self.root(), d, payload_sha256=sha, ts=ts)
        self.assertEqual(gatelog.verify(self.root())["rows"], 1)

    def test_gate_db_write_failure_raises_gatelogerror(self):
        os.makedirs(self.gate_db())  # a directory where gate.db should be: SQLite cannot open it
        with self.assertRaises(gatelog.GateLogError) as cm:
            gatelog.record(self.root(), decision(), payload_sha256=PAYLOAD_SHA, ts=TS)
        self.assertIsNotNone(cm.exception.__cause__)

    def test_failures_file_and_unlogged_attempts(self):
        gatelog.record(self.root(), decision(), payload_sha256=PAYLOAD_SHA, ts=TS)
        err = gatelog.GateLogError("gate log write failed: OperationalError: disk I/O error")
        self.assertTrue(gatelog.record_failure(self.root(), decision(decision="deny", reason="audit-unavailable"),
                                               err, ts=TS))
        self.assertTrue(gatelog.record_failure(self.root(), {"decision": "allow", "session": "B2",
                                                             "tool_name": "Bash"}, OSError("disk full"), ts=TS))
        with open(self.failures(), encoding="utf-8") as fh:
            lines = [json.loads(x) for x in fh.read().splitlines()]
        self.assertEqual(lines[0], {"ts": TS, "session_id": "B1", "tool_name": "Write", "decision": "deny",
                                    "reason": "audit-unavailable", "error_class": "GateLogError",
                                    "error": "gate log write failed: OperationalError: disk I/O error"})
        self.assertEqual((lines[1]["reason"], lines[1]["error_class"]), (None, "OSError"))
        v = gatelog.verify(self.root())
        self.assertEqual((v["rows"], v["chain_problems"], v["unlogged_attempts"]), (1, [], 2))

    def test_unlogged_attempts_counted_without_gate_db(self):
        self.assertTrue(gatelog.record_failure(self.root(), decision(), RuntimeError("x"), ts=TS))
        self.assertEqual(gatelog.verify(self.root()), {"present": False, "rows": 0, "chain_problems": [],
                                                       "unlogged_attempts": 1})

    def test_record_failure_is_best_effort(self):
        os.makedirs(os.path.join(self.root(), "runtime"))
        with open(os.path.join(self.root(), "runtime", "gate"), "w", encoding="utf-8") as fh:
            fh.write("a file where the gate directory should be")
        self.assertFalse(gatelog.record_failure(self.root(), decision(), RuntimeError("x"), ts=TS))
        with self.assertRaises(gatelog.GateLogError):
            gatelog.record(self.root(), decision(), payload_sha256=PAYLOAD_SHA, ts=TS)

    # ---- concurrency
    def test_concurrent_writers_keep_one_chain(self):
        errors: list[BaseException] = []

        def writer(n: int) -> None:
            try:
                for i in range(5):
                    gatelog.record(self.root(), decision(session=f"S{n}", paths=(f"jobs/j/work/{n}-{i}.txt",)),
                                   payload_sha256=PAYLOAD_SHA, ts=TS)
            except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
                errors.append(exc)

        gatelog.record(self.root(), decision(), payload_sha256=PAYLOAD_SHA, ts=TS)  # create the file first
        threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        v = gatelog.verify(self.root())
        self.assertEqual((v["rows"], v["chain_problems"]), (31, []))
        self.assertEqual([r["seq"] for r in self.raw_rows()], list(range(1, 32)))


class GateLogImports(unittest.TestCase):
    def test_gatelog_imports_within_the_frozen_set(self):
        with open(gatelog.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        local, top = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    local.update([node.module.split(".")[0]] if node.module else [a.name for a in node.names])
                else:
                    top.add((node.module or "").split(".")[0])
            elif isinstance(node, ast.Import):
                top.update(a.name.split(".")[0] for a in node.names)
        self.assertEqual(local, {"canon"})
        self.assertFalse(local & {"commands", "txn", "gatecore", "db"})
        import sys
        self.assertLessEqual(top, set(sys.stdlib_module_names) | {"__future__"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
