"""PACKAGE FIX2 batch R, lane F2RP (WF-118; AMENDMENT FIX2-A14): the fixes after the owner's decisions.

  #35 (A)        a `receipt`'s `sha_before` is 64 lower-case hex or "", and when the job has an intent for the same path
                 it equals the latest such intent's `sha_before`; otherwise `rejected(receipt-mismatch)` and nothing is
                 written. Paths compare as reconcile compares intent and receipt paths: the exact `path` string.
  #36 (A) + #9   `wf observe` writes `runtime/observe/<job>.log` under the boundary root (AMENDMENT S3-A3), and
                 nothing under `jobs/<job>/`.
  #37 (A) + #8   `recover begin` lists a broken or unreadable gate-log tail as one `gate-log` exception (AMENDMENT
  + #52          S1-G1); an absent or healthy gate.db adds nothing; the recovery-opened row has no `gate_log` field.
Each test failed on the base 03cedfe (red-before in from-plexy RESULTS.md) unless its docstring says it pins unchanged
behaviour. Runs in the test_wf.py harness (a fresh scratch ledger per test under WF_TEST_TMP)."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sqlite3
import unittest

from test_wf import CONTRACT, Base
from wfcore import changeset, gatelog, observe, paths, pathpolicy

WINDOWS = os.name == "nt"
TURN = "owner turn: recover job j"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


OLD, NEW = sha(b"old\n"), sha(b"new\n")


class _Job(Base):
    def setUp(self) -> None:
        super().setUp()
        self.new_job()
        self.activate()

    def coord(self, *argv: str):
        return self.run_wf(*argv, role="coordinator", session="C1")

    def append(self, etype: str, path: str, sha_before: str, **extra: str):
        content = {"path": path, "sha_before": sha_before, "note": "n", **extra}
        return self.run_wf("append", "j", "--type", etype, "--content", json.dumps(content))

    def receipt(self, sha_before: str, path: str = "jobs/j/work/a.txt"):
        return self.append("receipt", path, sha_before, sha_after=NEW)

    def events(self) -> int:
        return len(self.rows())

    def rows(self) -> list[dict]:
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in c.execute("SELECT * FROM events WHERE job='j' ORDER BY seq")]
        finally:
            c.close()

    def pending(self) -> list[str]:
        d = paths.pending_dir(self.db, "j")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []


# ------------------------------------------------------------------------------------------------ #35 (A)

class F35ReceiptSha(_Job):
    def refused(self, res) -> None:
        code, r = res
        self.assertEqual((code, r.get("code")), (1, "rejected"), r)
        self.assertIn("rejected(receipt-mismatch)", r["error"])

    def test_receipt_must_match_the_intent(self):
        c, r = self.append("intent", "jobs/j/work/a.txt", OLD)
        self.assertEqual(c, 0, r)
        n = self.events()
        for bad in ("", OLD[:40], "not-a-hash", OLD.upper(), NEW):
            with self.subTest(sha_before=bad):
                self.refused(self.receipt(bad))
                self.assertEqual(self.events(), n, "nothing is appended")
                self.assertEqual(self.pending(), [], "no marker is left")
        c, r = self.receipt(OLD)
        self.assertEqual((c, r.get("outcome")), (0, "COMMITTED"), r)
        self.assertEqual(self.events(), n + 1)

    def test_only_the_latest_intent_counts(self):
        self.assertEqual(self.append("intent", "jobs/j/work/a.txt", OLD)[0], 0)
        self.assertEqual(self.append("intent", "jobs/j/work/a.txt", NEW)[0], 0)
        self.refused(self.receipt(OLD))
        c, r = self.receipt(NEW)
        self.assertEqual((c, r.get("outcome")), (0, "COMMITTED"), r)

    def test_empty_sha_matches_an_empty_intent(self):
        """A new file: the intent and the receipt both say sha_before "" (the common test_wf flow)."""
        self.assertEqual(self.append("intent", "jobs/j/work/a.txt", "")[0], 0)
        c, r = self.receipt("")
        self.assertEqual((c, r.get("outcome")), (0, "COMMITTED"), r)

    def test_receipt_without_an_intent(self):
        """Pins unchanged behaviour for a well-formed sha_before: a receipt whose path has no intent is still
        allowed (reconcile reports it as before). The format rule applies to every receipt."""
        self.assertEqual(self.append("intent", "jobs/j/work/a.txt", OLD)[0], 0)
        for path, value in (("jobs/j/work/b.txt", OLD), ("jobs/j/work/c.txt", "")):
            with self.subTest(path=path):
                c, r = self.receipt(value, path=path)
                self.assertEqual((c, r.get("outcome")), (0, "COMMITTED"), r)
        self.refused(self.receipt("not-a-hash", path="jobs/j/work/d.txt"))

    def test_paths_compare_as_reconcile_does(self):
        """The exact `path` string, as reconcile keys intents and receipts: a differently spelled path has no intent."""
        self.assertEqual(self.append("intent", "jobs/j/work/a.txt", OLD)[0], 0)
        c, r = self.receipt(NEW, path="jobs\\j\\work\\a.txt")
        self.assertEqual((c, r.get("outcome")), (0, "COMMITTED"), r)


# ------------------------------------------------------------------------------------------------ #36 (A) + #9

def _files(top: str) -> set[str]:
    out = set()
    for d, _dirs, names in os.walk(top):
        for name in names:
            out.add(os.path.relpath(os.path.join(d, name), top).replace("\\", "/"))
    return out


class F36ObserveLogPath(Base):
    def job(self, write_scope: list[str]) -> None:
        cpath = os.path.join(self.dir, "contract-scope.json")
        with open(cpath, "w", encoding="utf-8") as fh:
            json.dump(dict(CONTRACT, write_scope=write_scope), fh)
        c, r = self.run_wf("job", "new", "j", "--contract", cpath, role="coordinator", session="C1")
        self.assertEqual(c, 0, r)
        self.activate()
        root = changeset.boundary_root(self.db)
        self.root = root

    def test_log_path_is_runtime_observe(self):
        self.job(["jobs/j/work/**"])
        self.assertEqual(observe.log_path(self.db, "j"), os.path.join(self.root, "runtime", "observe", "j.log"))

    def test_observe_adds_only_the_runtime_log(self):
        self.job(["jobs/j/work/**"])
        self.assertEqual(self.intent()[0], 0)
        before = _files(self.root)
        c, o = self.run_wf("observe", "j", role="coordinator", session="C1")
        self.assertEqual(c, 0, o)
        self.assertEqual(o["log"], os.path.join(self.root, "runtime", "observe", "j.log"))
        self.assertEqual(_files(self.root) - before, {"runtime/observe/j.log"})
        self.assertFalse(os.path.exists(os.path.join(self.root, "jobs", "j", "observe")))

    def test_classify_puts_the_log_in_p4(self):
        self.job(["jobs/j/work/**"])
        self.assertEqual(self.intent()[0], 0)
        c, o = self.run_wf("observe", "j", role="coordinator", session="C1")
        rel = os.path.relpath(o["log"], self.root).replace("\\", "/")
        res = pathpolicy.classify([rel], dict(CONTRACT, write_scope=["jobs/j/work/**"]),
                                  coordination=pathpolicy.coordination_globs("j", "ledger", "l.db"))
        self.assertEqual([r["class"] for r in res["results"]], ["P4"], res)

    def test_reconcile_has_no_matches_neither_with_a_job_wide_scope(self):
        """WF-059 item36: with write scope jobs/j/**, the log no longer lands inside the scope."""
        self.job(["jobs/j/**"])
        os.makedirs(os.path.join(self.root, "jobs", "j", "work"), exist_ok=True)
        self.assertEqual(self.intent()[0], 0)
        with open(os.path.join(self.root, "jobs", "j", "work", "a.txt"), "wb") as fh:
            fh.write(b"new\n")
        c, r = self.run_wf("append", "j", "--type", "receipt", "--content", json.dumps(
            {"path": "jobs/j/work/a.txt", "sha_before": "", "sha_after": NEW, "note": "n"}))
        self.assertEqual(c, 0, r)
        self.assertEqual(self.run_wf("observe", "j", role="coordinator", session="C1")[0], 0)
        c, r = self.run_wf("reconcile", "j", role="coordinator", session="C1")
        self.assertEqual(c, 0, r)
        self.assertEqual([e for e in r.get("exceptions") or [] if e.get("class") == "matches-neither"], [], r)

    def test_a_linked_runtime_observe_is_refused(self):
        """The log is never written outside runtime/observe/: a link there is refused, as jobs/<job>/observe was."""
        self.job(["jobs/j/work/**"])
        outside = os.path.join(os.path.dirname(self.dir), f"{self.scratch}-elsewhere")
        os.makedirs(outside)
        os.makedirs(os.path.join(self.root, "runtime"), exist_ok=True)
        link = os.path.join(self.root, "runtime", "observe")
        try:
            if WINDOWS:
                import _winapi
                _winapi.CreateJunction(outside, link)
            else:
                os.symlink(outside, link, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"link not creatable here: {exc}")
        with self.assertRaises(ValueError):
            observe.append_log(self.db, "j", {"x": 1})
        self.assertEqual(os.listdir(outside), [])


# ------------------------------------------------------------------------------------------------ #37 (A) + #8 + #52

DECISION = {"decision": "deny", "reason": "outside grant", "channel": "preventable", "session": "B1",
            "tool_name": "Write", "jobs": ["j"], "paths": ["jobs/j/work/a.txt"], "command_sha256": None,
            "command_length": None, "limitation": None}


class F37GateLogTail(_Job):
    def setUp(self) -> None:
        super().setUp()
        self.root = changeset.boundary_root(self.db)
        self.gate_db = os.path.join(self.root, "runtime", "gate", "gate.db")

    def gate_rows(self, n: int = 3) -> None:
        for i in range(n):
            gatelog.record(self.root, DECISION, payload_sha256=sha(b"payload"), ts=f"2026-10-19T00:00:0{i}Z")

    def tamper_tail(self) -> None:
        c = sqlite3.connect(self.gate_db, isolation_level=None)
        try:
            c.execute("DROP TRIGGER attempts_no_update")
            c.execute("UPDATE attempts SET decision='allow', reason='altered' WHERE seq=3")
        finally:
            c.close()

    def begin(self) -> dict:
        c, r = self.coord("recover", "begin", "j", "--reason", "gate tail check", "--turn", TURN)
        self.assertEqual((c, r.get("outcome")), (0, "COMMITTED"), r)
        return r

    def recovery_row(self) -> dict:
        return json.loads([r for r in self.rows() if r["type"] == "recovery-opened"][-1]["content"])

    def gate_items(self, r: dict) -> list[dict]:
        return [e for e in r["exceptions"] if e["class"] == "gate-log"]

    def test_tampered_tail_is_listed(self):
        self.gate_rows()
        self.tamper_tail()
        r = self.begin()
        items = self.gate_items(r)
        self.assertEqual(len(items), 1, r["exceptions"])
        self.assertIn("seq 3: self does not match the row content", items[0]["detail"])
        row = self.recovery_row()
        self.assertEqual(row["exceptions"], r["exceptions"])
        self.assertNotIn("gate_log", row)

    def test_unreadable_gate_db_is_listed(self):
        self.gate_rows()
        with open(self.gate_db, "wb") as fh:
            fh.write(b"this is not a sqlite database" * 64)
        items = self.gate_items(self.begin())
        self.assertEqual(len(items), 1)
        self.assertIn("gate log unreadable", items[0]["detail"])

    def test_gate_db_held_exclusively_is_listed(self):
        self.gate_rows()
        if WINDOWS:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateFileW.restype = ctypes.c_void_p
            k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
            k32.CloseHandle.argtypes = [ctypes.c_void_p]
            h = k32.CreateFileW(self.gate_db, 0x80000000, 0, None, 3, 0x80, None)  # GENERIC_READ, share mode 0
            self.assertNotIn(h, (None, ctypes.c_void_p(-1).value), "could not hold gate.db")
            try:
                items = self.gate_items(self.begin())
            finally:
                k32.CloseHandle(h)
        else:
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                self.skipTest("root reads a mode-000 file")
            os.chmod(self.gate_db, 0)
            try:
                items = self.gate_items(self.begin())
            finally:
                os.chmod(self.gate_db, 0o600)
        self.assertEqual(len(items), 1)
        self.assertIn("gate log unreadable", items[0]["detail"])

    def test_absent_or_healthy_adds_nothing(self):
        """Pins unchanged behaviour for an absent gate.db; a healthy one adds nothing either."""
        self.assertFalse(os.path.exists(self.gate_db))
        r = self.begin()
        self.assertEqual(self.gate_items(r), [])
        self.assertEqual(self.coord("recover", "reconcile", "j", "--turn", TURN)[0], 0)
        self.assertEqual(self.coord("recover", "close", "j", "--turn", TURN)[0], 0)
        self.gate_rows()
        self.assertEqual(gatelog.verify(self.root)["chain_problems"], [])
        self.assertEqual(self.gate_items(self.begin()), [])

    def test_gate_item_follows_the_export_items(self):
        self.gate_rows()
        self.tamper_tail()
        with open(os.path.join(paths.export_dir(self.db, "j"), "head.json"), "a", encoding="utf-8") as fh:
            fh.write("tampered\n")
        ex = self.begin()["exceptions"]
        self.assertEqual([e["id"] for e in ex], [f"R{i}" for i in range(1, len(ex) + 1)])
        self.assertEqual(ex[-1]["class"], "gate-log")
        self.assertTrue(all(e["class"] in ("export", "orphan") for e in ex[:-1]) and len(ex) >= 2, ex)

    def test_acknowledge_then_close(self):
        """End to end through the CLI: the listed gate-log item is acknowledged by `recover reconcile`, and
        `recover close` then closes the recovery although gate.db is still broken (close checks the ledger chain
        and the acknowledgements, not the gate log)."""
        self.gate_rows()
        self.tamper_tail()
        self.assertEqual(len(self.gate_items(self.begin())), 1)
        c, r = self.coord("recover", "reconcile", "j", "--turn", TURN)
        self.assertEqual(c, 0, r)
        acks = [json.loads(x["content"]) for x in self.rows() if x["type"] == "exception-acknowledged"]
        self.assertIn("gate-log", {a.get("class") for a in acks})
        self.assertNotEqual(gatelog.verify(self.root)["chain_problems"], [])          # still broken
        c, r = self.coord("recover", "close", "j", "--turn", TURN)
        self.assertEqual((c, r.get("outcome")), (0, "COMMITTED"), r)


if __name__ == "__main__":
    unittest.main()
