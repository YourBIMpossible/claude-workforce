"""T14 (event half; gate half on fixture attempts, S4A section 5) and T22: `wfcore.observe` module tests (B2 slice 3, lane S3O; INTERFACE-CONTRACT-S3 sections 5,
6 and 8 S3O).

Covers each class (active, slow, silent, blocked, unknown) with its evidence, basis and uncertainty; default and
contract thresholds; invalid thresholds falling back with a note; the gate statement on every result; `append_log`
bounded at 500 entries, writing only `runtime/observe/<job>.log` (AMENDMENT S3-A3; directory listing before/after) and
never the ledger (row count and head unchanged); and the T22 refusals available at module level (no output, contact,
resume or launch parameter; no forbidden imports). The CLI half (`wf observe` argparse refusals) is S3W's
test_b2s3_wiring.py. Nothing here deletes test artifacts: they stay under WF_TEST_TMP.
"""
from __future__ import annotations

import ast
import copy
import inspect
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from test_wf import Base, dbm, paths  # noqa: E402
from wfcore import observe  # noqa: E402

NOW = "2026-10-17T12:00:00Z"
RECORDED_ONLY = "classification uses recorded events only; a session can be working without writing events"
SILENT_NOTE = "silent does not establish that the process stopped"
GATE = "gate evidence unavailable in this release: tool activity between events is not observed"
RESULT_KEYS = {"class", "evidence", "basis", "uncertainty", "gate_log", "thresholds", "now"}
EVIDENCE_KEYS = {"seq", "type", "ts", "role", "session"}
GATE_EVIDENCE_KEYS = EVIDENCE_KEYS | {"source", "tool_name", "channel"}


NOW_DT = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)


def ts(seconds_before_now: float) -> str:
    """A ledger-style timestamp (milliseconds, as validate.now_ts writes) `seconds_before_now` before NOW."""
    t = NOW_DT - timedelta(milliseconds=round(seconds_before_now * 1000))
    return t.strftime("%Y-%m-%dT%H:%M:%S") + f".{t.microsecond // 1000:03d}Z"


def row(seq: int, etype: str, role: str, age: float, session: str = "S1") -> dict:
    return {"job": "j", "seq": seq, "type": etype, "author_role": role, "session_id": session, "ts": ts(age),
            "content": "{}", "msg_id": f"m{seq}"}


def gts(seconds_before_now: float) -> str:
    """A gate-log timestamp (whole seconds, as the hook writes) `seconds_before_now` before NOW."""
    return (NOW_DT - timedelta(seconds=int(seconds_before_now))).strftime("%Y-%m-%dT%H:%M:%SZ")


def gate_attempt(seq: int, *, age: float, session: str = "B1", channel: str = "preventable", tool: str = "Write",
                 decision: str = "allow") -> dict:
    """A row in the gatelog.attempts shape."""
    return {"seq": seq, "ts": gts(age), "session_id": session, "tool_name": tool, "channel": channel,
            "decision": decision, "reason": "x", "jobs": ["j"], "paths": [], "jobs_json": '["j"]', "paths_json": "[]"}


def base_rows() -> list[dict]:
    return [row(1, "contract", "coordinator", 5000, "C1"), row(2, "grant", "coordinator", 4900, "C1"),
            row(3, "grant", "coordinator", 4800, "C1")]


class Classify(unittest.TestCase):
    def check_common(self, res: dict, now: str = NOW) -> None:
        self.assertEqual(set(res), RESULT_KEYS)
        self.assertIn(res["class"], observe.CLASSES)
        self.assertIn(RECORDED_ONLY, res["uncertainty"])
        if res["gate_log"]["available"]:                                # only when gate attempts were supplied
            self.assertEqual(set(res["gate_log"]), {"available", "statement"})
            self.assertTrue(res["gate_log"]["statement"].startswith("gate attempts consulted: "))
        else:
            self.assertEqual(res["gate_log"], {"available": False, "statement": GATE})
        self.assertEqual(res["now"], now)
        self.assertIsInstance(res["basis"], str)
        self.assertTrue(res["basis"])
        for e in res["evidence"]:
            self.assertEqual(set(e), GATE_EVIDENCE_KEYS if e.get("source") == "gate-log" else EVIDENCE_KEYS)

    def classify(self, rows, th=None, now=NOW):
        res = observe.classify(rows, now=now, thresholds=dict(observe.DEFAULT_THRESHOLDS) if th is None else th)
        self.check_common(res, now)
        self.assertFalse(res["gate_log"]["available"])
        return res

    def classify_gate(self, rows, attempts):
        res = observe.classify(rows, now=NOW, thresholds=dict(observe.DEFAULT_THRESHOLDS), gate_attempts=attempts)
        self.check_common(res)
        self.assertTrue(res["gate_log"]["available"])
        return res

    def test_frozen_constants(self):
        self.assertEqual(observe.CLASSES, ("active", "slow", "silent", "blocked", "unknown"))
        self.assertEqual(observe.DEFAULT_THRESHOLDS, {"slow_s": 600.0, "silent_s": 1800.0})
        self.assertEqual(observe.LOG_MAX_ENTRIES, 500)
        self.assertEqual(observe.GATE_UNAVAILABLE, GATE)

    def test_active(self):
        res = self.classify(base_rows() + [row(4, "intent", "builder", 10, "B1")])
        self.assertEqual(res["class"], "active")
        self.assertEqual(res["evidence"], [{"seq": 4, "type": "intent", "ts": ts(10), "role": "builder", "session": "B1"}])
        self.assertIn("seq 4", res["basis"])
        self.assertNotIn(SILENT_NOTE, res["uncertainty"])
        self.assertEqual(res["thresholds"], {"slow_s": 600.0, "silent_s": 1800.0})

    def test_slow(self):
        res = self.classify(base_rows() + [row(4, "receipt", "builder", 700)])
        self.assertEqual(res["class"], "slow")
        self.assertEqual(res["evidence"][0]["seq"], 4)
        self.assertNotIn(SILENT_NOTE, res["uncertainty"])

    def test_silent(self):
        res = self.classify(base_rows() + [row(4, "intent", "builder", 2000)])
        self.assertEqual(res["class"], "silent")
        self.assertEqual(res["evidence"][0]["seq"], 4)
        self.assertIn(SILENT_NOTE, res["uncertainty"])
        self.assertIn(RECORDED_ONLY, res["uncertainty"])

    def test_threshold_edges(self):
        self.assertEqual(self.classify([row(1, "intent", "builder", 599.5)])["class"], "active")
        self.assertEqual(self.classify([row(1, "intent", "builder", 600)])["class"], "slow")
        self.assertEqual(self.classify([row(1, "intent", "builder", 1799.5)])["class"], "slow")
        self.assertEqual(self.classify([row(1, "intent", "builder", 1800)])["class"], "silent")

    def test_blocked_by_latest_execution_event(self):
        rows = base_rows() + [row(4, "intent", "builder", 100), row(5, "blocker", "reviewer", 50, "R1")]
        res = self.classify(rows)
        self.assertEqual(res["class"], "blocked")
        self.assertEqual(res["evidence"], [{"seq": 5, "type": "blocker", "ts": ts(50), "role": "reviewer", "session": "R1"}])
        self.assertIn("seq 5", res["basis"])
        # blocked does not depend on age: an old blocker is still blocked
        self.assertEqual(self.classify(base_rows() + [row(4, "blocker", "builder", 99999)])["class"], "blocked")
        # a later execution event clears it
        res = self.classify(rows + [row(6, "receipt", "builder", 10)])
        self.assertEqual(res["class"], "active")
        self.assertEqual(res["evidence"][0]["seq"], 6)
        # non-execution events after the blocker do not clear it
        self.assertEqual(self.classify(rows + [row(6, "ack-assignment", "builder", 1)])["class"], "blocked")

    def test_unknown_without_builder_event(self):
        res = self.classify([])
        self.assertEqual(res["class"], "unknown")
        self.assertEqual(res["evidence"], [])
        res = self.classify(base_rows() + [row(4, "handoff", "coordinator", 5, "C1")])
        self.assertEqual(res["class"], "unknown")
        self.assertIn("builder", res["basis"])

    def test_latest_builder_event_by_seq(self):
        rows = [row(5, "receipt", "builder", 3000), row(4, "intent", "builder", 10), row(6, "handoff", "coordinator", 1, "C1")]
        res = self.classify(rows)
        self.assertEqual(res["evidence"][0]["seq"], 5)
        self.assertEqual(res["class"], "silent")

    def test_contract_thresholds(self):
        th, notes = observe.thresholds({"observe_thresholds": {"slow_s": 60, "silent_s": 120, "held_s": 2.0}})
        self.assertEqual((th, notes), ({"slow_s": 60.0, "silent_s": 120.0}, []))
        self.assertEqual(self.classify([row(1, "intent", "builder", 30)], th)["class"], "active")
        self.assertEqual(self.classify([row(1, "intent", "builder", 90)], th)["class"], "slow")
        res = self.classify([row(1, "intent", "builder", 130)], th)
        self.assertEqual(res["class"], "silent")
        self.assertEqual(res["thresholds"], {"slow_s": 60.0, "silent_s": 120.0})

    def test_default_thresholds_named_when_absent(self):
        for contract in ({}, {"outcome": "x"}, None, "not a dict"):
            th, notes = observe.thresholds(contract)
            self.assertEqual(th, observe.DEFAULT_THRESHOLDS)
            self.assertEqual(len(notes), 1)
            self.assertIn("absent", notes[0])
            self.assertIn("slow_s=600", notes[0])
            self.assertIn("silent_s=1800", notes[0])
        th["slow_s"] = 1.0  # a returned dict is a copy, never the module default
        self.assertEqual(observe.DEFAULT_THRESHOLDS["slow_s"], 600.0)

    def test_invalid_thresholds_fall_back_with_note(self):
        bad = [{"held_s": 2.0},                       # the test_wf contract fixture: no slow_s/silent_s
               {"slow_s": 1800, "silent_s": 600},     # slow_s not below silent_s
               {"slow_s": 600, "silent_s": 600},
               {"slow_s": True, "silent_s": 1800},
               {"slow_s": "600", "silent_s": 1800},
               {"slow_s": -1, "silent_s": 1800},
               {"slow_s": 0, "silent_s": 1800},
               {"slow_s": float("nan"), "silent_s": 1800},
               {"slow_s": 600, "silent_s": float("inf")},
               [600, 1800], "600"]
        for src in bad:
            th, notes = observe.thresholds({"observe_thresholds": src})
            self.assertEqual(th, observe.DEFAULT_THRESHOLDS, src)
            self.assertEqual(len(notes), 1, src)
            self.assertIn("invalid", notes[0])
            self.assertIn("slow_s=600, silent_s=1800", notes[0])

    def test_classify_with_invalid_thresholds_uses_defaults_and_says_so(self):
        res = self.classify([row(1, "intent", "builder", 700)], th={"slow_s": 5})
        self.assertEqual(res["class"], "slow")
        self.assertEqual(res["thresholds"], observe.DEFAULT_THRESHOLDS)
        self.assertTrue(any("defaults used" in u for u in res["uncertainty"]))

    def test_unreadable_time_is_unknown_with_note(self):
        res = self.classify([row(1, "intent", "builder", 10)], now="2026-10-17 12:00:00")
        self.assertEqual(res["class"], "unknown")
        self.assertTrue(any("now is not" in u for u in res["uncertainty"]))
        bad = row(1, "intent", "builder", 10)
        bad["ts"] = "yesterday"
        res = self.classify([bad])
        self.assertEqual(res["class"], "unknown")
        self.assertEqual(res["evidence"][0]["seq"], 1)
        self.assertTrue(any("unreadable timestamp" in u for u in res["uncertainty"]))

    def test_future_event_noted_not_negative(self):
        res = self.classify([row(1, "intent", "builder", -30)])
        self.assertEqual(res["class"], "active")
        self.assertTrue(any("later than now" in u for u in res["uncertainty"]))
        # sub-second difference from the seconds-truncated `now` is expected and not noted
        res = self.classify([row(1, "intent", "builder", -0.5)])
        self.assertFalse(any("later than now" in u for u in res["uncertainty"]))

    def test_inputs_not_mutated(self):
        rows = base_rows() + [row(4, "intent", "builder", 10)]
        th = {"slow_s": 60, "silent_s": 120}
        before = (copy.deepcopy(rows), copy.deepcopy(th))
        observe.classify(rows, now=NOW, thresholds=th)
        self.assertEqual((rows, th), before)

    # ---- B2 slice 4a (INTERFACE-CONTRACT-S4A section 5, lane S4L): gate_attempts
    def test_gate_attempts_none_is_todays_behaviour(self):
        rows = base_rows() + [row(4, "intent", "builder", 10, "B1")]
        res = observe.classify(rows, now=NOW, thresholds=dict(observe.DEFAULT_THRESHOLDS), gate_attempts=None)
        self.assertEqual(res, observe.classify(rows, now=NOW, thresholds=dict(observe.DEFAULT_THRESHOLDS)))
        self.assertEqual(res["gate_log"], {"available": False, "statement": GATE})

    def test_gate_log_available_only_when_attempts_supplied(self):
        rows = base_rows() + [row(4, "intent", "builder", 10, "B1")]
        res = self.classify_gate(rows, [])
        self.assertTrue(res["gate_log"]["available"])
        self.assertIn("0 attempt(s)", res["gate_log"]["statement"])
        self.assertIn("never as file changes", res["gate_log"]["statement"])
        self.assertEqual((res["class"], res["evidence"][0]["seq"]), ("active", 4))

    def test_recent_builder_gate_attempt_is_activity(self):
        rows = base_rows() + [row(4, "intent", "builder", 5000, "B1")]          # silent by ledger alone
        res = self.classify_gate(rows, [gate_attempt(7, age=20, channel="preventable", tool="Write")])
        self.assertEqual(res["class"], "active")
        self.assertEqual(res["evidence"][0], {"seq": 7, "type": "gate-attempt", "ts": gts(20), "role": "builder",
                                              "session": "B1", "source": "gate-log", "tool_name": "Write",
                                              "channel": "preventable"})
        self.assertEqual(res["evidence"][1]["seq"], 4)
        self.assertIn("gate attempt", res["basis"])
        self.assertNotIn(SILENT_NOTE, res["uncertainty"])

    def test_shell_invocation_counts_as_activity_only(self):
        rows = base_rows() + [row(4, "intent", "builder", 700, "B1")]           # slow by ledger alone
        res = self.classify_gate(rows, [gate_attempt(3, age=30, channel="shell", tool="Bash")])
        self.assertEqual(res["class"], "active")
        self.assertEqual(res["evidence"][0]["channel"], "shell")
        self.assertIn("never as file changes", res["gate_log"]["statement"])

    def test_older_gate_attempt_does_not_override_newer_event(self):
        rows = base_rows() + [row(4, "receipt", "builder", 10, "B1")]
        res = self.classify_gate(rows, [gate_attempt(2, age=4000)])
        self.assertEqual(res["class"], "active")
        self.assertEqual([e.get("source") for e in res["evidence"]], [None, "gate-log"])
        self.assertIn("seq 4", res["basis"])

    def test_attempts_from_non_builder_sessions_are_not_attributed(self):
        rows = base_rows() + [row(4, "intent", "builder", 5000, "B1")]
        res = self.classify_gate(rows, [gate_attempt(1, age=5, session="X9"), gate_attempt(2, age=5, session="C1")])
        self.assertEqual(res["class"], "silent")
        self.assertIn(observe.GATE_UNATTRIBUTED, res["uncertainty"])
        self.assertEqual(len(res["evidence"]), 1)
        res = self.classify_gate(base_rows(), [gate_attempt(1, age=5, session="B1")])   # no builder event at all
        self.assertEqual(res["class"], "unknown")
        self.assertIn(observe.GATE_UNATTRIBUTED, res["uncertainty"])

    def test_gate_attempts_never_decide_blocked(self):
        rows = base_rows() + [row(4, "intent", "builder", 100, "B1"), row(5, "blocker", "builder", 50, "B1")]
        res = self.classify_gate(rows, [gate_attempt(9, age=1)])
        self.assertEqual(res["class"], "blocked")
        self.assertEqual(res["evidence"][0]["seq"], 5)

    def test_gate_attempts_input_not_mutated(self):
        attempts = [gate_attempt(1, age=10)]
        before = copy.deepcopy(attempts)
        self.classify_gate(base_rows() + [row(4, "intent", "builder", 10, "B1")], attempts)
        self.assertEqual(attempts, before)


class ObserveLog(Base):
    def tree(self) -> set[str]:
        out = set()
        for root, dirs, files in os.walk(self.dir):
            for name in dirs + files:
                out.add(os.path.relpath(os.path.join(root, name), self.dir).replace("\\", "/"))
        return out

    def ledger_identity(self):
        conn = dbm.connect(self.db, create=False)
        try:
            return dbm.head(conn, "j"), len(dbm.rows(conn, "j"))
        finally:
            conn.close()

    def ledger_job(self):
        self.new_job()
        self.activate()
        code, res = self.intent()
        self.assertEqual(code, 0, res)

    def read_lines(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read().splitlines()

    def test_log_path_under_runtime_observe(self):
        """AMENDMENT S3-A3 (FIX2 #36): the log is `runtime/observe/<job>.log` under the boundary root."""
        p = observe.log_path(self.db, "j")
        root = os.path.dirname(paths.jobs_dir(self.db))
        self.assertEqual(p, os.path.join(root, "runtime", "observe", "j.log"))
        self.assertTrue(p.replace("\\", "/").endswith("runtime/observe/j.log"))
        self.assertFalse(paths.is_under(p, paths.jobs_dir(self.db)))

    def test_append_writes_only_the_log_and_never_the_ledger(self):
        self.ledger_job()
        ledger_before = self.ledger_identity()
        with open(self.db, "rb") as fh:
            db_bytes = fh.read()
        before = self.tree()
        p = observe.append_log(self.db, "j", {"job": "j", "class": "active", "evidence_seqs": [4]})
        added = self.tree() - before
        self.assertEqual(added, {"runtime", "runtime/observe", "runtime/observe/j.log"})
        self.assertEqual(before - self.tree(), set())
        self.assertEqual(p, observe.log_path(self.db, "j"))
        self.assertEqual(self.ledger_identity(), ledger_before)          # row count and head unchanged
        with open(self.db, "rb") as fh:
            self.assertEqual(fh.read(), db_bytes)
        self.assertEqual([json.loads(x) for x in self.read_lines(p)], [{"job": "j", "class": "active", "evidence_seqs": [4]}])
        before = self.tree()
        observe.append_log(self.db, "j", {"n": 2})
        self.assertEqual(self.tree(), before)                            # second run: no new file, no staging left
        self.assertEqual(os.listdir(os.path.dirname(p)), ["j.log"])
        self.assertEqual(len(self.read_lines(p)), 2)
        self.assertEqual(self.ledger_identity(), ledger_before)

    def test_bounded_at_500_newest_kept(self):
        p = observe.log_path(self.db, "j")
        os.makedirs(os.path.dirname(p))
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("".join(json.dumps({"n": i}) + "\n" for i in range(600)))
        observe.append_log(self.db, "j", {"n": 600})
        lines = [json.loads(x) for x in self.read_lines(p)]
        self.assertEqual(len(lines), 500)
        self.assertEqual(lines[0], {"n": 101})
        self.assertEqual(lines[-1], {"n": 600})
        for i in range(601, 610):
            observe.append_log(self.db, "j", {"n": i})
        lines = [json.loads(x) for x in self.read_lines(p)]
        self.assertEqual(len(lines), 500)
        self.assertEqual((lines[0], lines[-1]), ({"n": 110}, {"n": 609}))
        self.assertEqual(os.listdir(os.path.dirname(p)), ["j.log"])

    def test_grows_to_bound_from_empty(self):
        p = None
        for i in range(observe.LOG_MAX_ENTRIES + 3):
            p = observe.append_log(self.db, "j", {"n": i})
        lines = [json.loads(x) for x in self.read_lines(p)]
        self.assertEqual(len(lines), 500)
        self.assertEqual((lines[0], lines[-1]), ({"n": 3}, {"n": 502}))

    def test_failed_replace_keeps_old_log_and_leaves_no_staging_file(self):
        p = observe.append_log(self.db, "j", {"n": 1})
        before = self.tree()
        with mock.patch.object(observe.os, "replace", side_effect=PermissionError("simulated replace failure")):
            with self.assertRaises(PermissionError):
                observe.append_log(self.db, "j", {"n": 2})
        self.assertEqual(self.tree(), before)
        self.assertEqual([json.loads(x) for x in self.read_lines(p)], [{"n": 1}])

    def test_unserialisable_entry_writes_nothing(self):
        before = self.tree()
        for entry in ({"x": object()}, {"x": float("nan")}, ["not", "a", "dict"]):
            with self.assertRaises((TypeError, ValueError)):
                observe.append_log(self.db, "j", entry)
        self.assertEqual(self.tree(), before)

    def test_bad_job_ids_refused_before_any_write(self):
        before = self.tree()
        for job in ("", "..", "../x", "a/b", "a\\b", "C:x", ".hidden", "x" * 65, None):
            with self.assertRaises(ValueError, msg=repr(job)):
                observe.append_log(self.db, job, {"n": 1})
            with self.assertRaises(ValueError, msg=repr(job)):
                observe.log_path(self.db, job)
        self.assertEqual(self.tree(), before)

    def test_log_that_is_not_a_regular_file_refused(self):
        p = observe.log_path(self.db, "j")
        os.makedirs(p)  # a directory where the log should be
        with self.assertRaises(ValueError):
            observe.append_log(self.db, "j", {"n": 1})
        self.assertEqual(os.listdir(p), [])

    @unittest.skipUnless(sys.platform == "win32", "junction creation uses the Windows-only _winapi.CreateJunction")
    def test_junctioned_runtime_dir_refused(self):
        """AMENDMENT S3-A3: the log's directories are now `runtime` and `runtime/observe`; a junction at `runtime` is
        refused, as one at `jobs/<job>` was."""
        import _winapi
        outside = os.path.join(self.dir, "outside")
        os.makedirs(outside)
        _winapi.CreateJunction(outside, os.path.join(os.path.dirname(paths.jobs_dir(self.db)), "runtime"))
        with self.assertRaises(ValueError):
            observe.append_log(self.db, "j", {"n": 1})
        self.assertEqual(os.listdir(outside), [])                        # nothing written through the junction

    @unittest.skipUnless(sys.platform == "win32", "junction creation uses the Windows-only _winapi.CreateJunction")
    def test_junctioned_runtime_observe_dir_refused(self):
        """AMENDMENT S3-A3: a junction at `runtime/observe` is refused, as one at `jobs/<job>/observe` was."""
        import _winapi
        outside = os.path.join(self.dir, "outside")
        os.makedirs(outside)
        runtime = os.path.join(os.path.dirname(paths.jobs_dir(self.db)), "runtime")
        os.makedirs(runtime)
        _winapi.CreateJunction(outside, os.path.join(runtime, "observe"))
        with self.assertRaises(ValueError):
            observe.append_log(self.db, "k", {"n": 1})
        self.assertEqual(os.listdir(outside), [])


class ModuleT22(unittest.TestCase):
    """T22 at module level: observe exposes no way to write elsewhere, contact, resume or launch."""
    FORBIDDEN_PARAMS = {"out", "output", "dest", "destination", "file", "path", "target", "contact", "resume",
                        "launch", "session", "worker", "url", "host"}

    def test_frozen_signatures(self):
        sig = {name: list(inspect.signature(getattr(observe, name)).parameters)
               for name in ("thresholds", "classify", "log_path", "append_log")}
        self.assertEqual(sig, {"thresholds": ["contract"], "classify": ["rows", "now", "thresholds", "gate_attempts"],
                               "log_path": ["db_path", "job"], "append_log": ["db_path", "job", "entry"]})
        p = inspect.signature(observe.classify).parameters
        self.assertEqual([p["now"].kind, p["thresholds"].kind, p["gate_attempts"].kind],
                         [inspect.Parameter.KEYWORD_ONLY] * 3)
        self.assertIsNone(p["gate_attempts"].default)                    # S4A section 6: default = today

    def test_no_output_contact_or_resume_surface(self):
        for name, fn in inspect.getmembers(observe, inspect.isfunction):
            if fn.__module__ != observe.__name__:
                continue
            for verb in ("contact", "resume", "launch", "recover", "bind", "send"):
                self.assertNotIn(verb, name.lower(), name)
            if not name.startswith("_"):
                self.assertFalse(set(inspect.signature(fn).parameters) & self.FORBIDDEN_PARAMS, name)

    def test_imports_within_the_frozen_set(self):
        with open(observe.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        local, top = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    local.update((node.module or "").split(".")[:1] if node.module else [a.name for a in node.names])
                else:
                    top.add((node.module or "").split(".")[0])
            elif isinstance(node, ast.Import):
                top.update(a.name.split(".")[0] for a in node.names)
        self.assertLessEqual(local, {"db", "validate", "paths"})
        self.assertFalse(local & {"commands", "txn", "bind", "reconcile", "grant"})
        self.assertFalse(top & {"socket", "subprocess", "http", "urllib", "sqlite3", "multiprocessing", "asyncio"})
        self.assertLessEqual(top, set(sys.stdlib_module_names) | {"__future__"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
