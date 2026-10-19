"""PACKAGE FIX2 batch B, lane F2BP (WF-098; AMENDMENT FIX2-A2): input and output validation.

  #2   every JSON input wf parses from the user is bounded at 1,048,576 UTF-8 bytes, measured before parsing, and
       refuses a duplicate key at any depth; both are rejected(malformed) and write no row.
  #19  an argv element holding a lone surrogate (POSIX undecodable bytes) is rejected(usage), exit 2, by position,
       before any command runs.
  #20  validation regexes anchor at the true end (\\Z / fullmatch), so "j\\n" is not a job id.
  #30  validate._fold uses pathpolicy.fold: one case rule, one patch point.
  #33  plain-text output never fails on the stream's encoding (errors="backslashreplace"), above all after a commit.
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
import unittest
from contextlib import closing, redirect_stdout
from unittest import mock

from test_wf import WF_DIR, Base  # noqa: E402
import wf  # noqa: E402
from wfcore import commands, pathpolicy, validate  # noqa: E402
from wfcore.db import WfRejected  # noqa: E402

LIMIT = 1_048_576
WF_PY = os.path.join(WF_DIR, "wf.py")


class Fix2Base(Base):
    def event_count(self) -> int:
        if not os.path.exists(self.db):
            return 0
        with closing(sqlite3.connect(self.db)) as con:
            return con.execute("SELECT count(*) FROM events").fetchone()[0]

    def intent_file(self, name: str, size: int) -> str:
        """An intent content file of exactly `size` UTF-8 bytes (ASCII padding in `note`)."""
        stub = json.dumps({"path": "jobs/j/work/a.txt", "sha_before": "", "note": ""})
        text = json.dumps({"path": "jobs/j/work/a.txt", "sha_before": "", "note": "x" * (size - len(stub))})
        self.assertEqual(len(text.encode("utf-8")), size)
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return path

    def raw_file(self, name: str, text: str) -> str:
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return path


class F2Size(Fix2Base):
    """#2: the 1 MiB bound on user JSON."""

    def test_json_of_exactly_1_mib_is_accepted(self):
        self.new_job(); self.activate()
        code, res = self.run_wf("append", "j", "--type", "intent", "--content", self.intent_file("at.json", LIMIT))
        self.assertEqual((code, res.get("outcome")), (0, "COMMITTED"), res)

    def test_json_over_1_mib_is_refused_and_writes_no_row(self):
        self.new_job(); self.activate()
        before = self.event_count()
        code, res = self.run_wf("append", "j", "--type", "intent", "--content", self.intent_file("over.json", LIMIT + 1))
        self.assertEqual((code, res.get("code")), (1, "rejected"), res)
        self.assertIn("rejected(malformed)", res["error"])
        self.assertIn(str(LIMIT + 1), res["error"])
        self.assertEqual(self.event_count(), before)

    def test_size_is_measured_before_parsing(self):
        """An oversized file that is not JSON at all gets the size refusal, not a parse error."""
        path = self.raw_file("junk.json", "{" + "x" * LIMIT)
        with self.assertRaises(WfRejected) as cm:
            commands._load_json(path)
        self.assertIn(f"{LIMIT + 1} bytes", str(cm.exception))

    def test_inline_json_is_bounded_too(self):
        stub = json.dumps({"n": ""})
        at = json.dumps({"n": "x" * (LIMIT - len(stub))})
        self.assertEqual(commands._load_json(at)["n"], "x" * (LIMIT - len(stub)))
        with self.assertRaises(WfRejected) as cm:
            commands._load_json(json.dumps({"n": "x" * (LIMIT - len(stub) + 1)}))
        self.assertIn("rejected(malformed)", str(cm.exception))

    def test_contract_over_1_mib_creates_no_job(self):
        text = json.dumps({"pad": "x" * LIMIT})
        path = self.raw_file("big-contract.json", text)
        code, res = self.run_wf("job", "new", "j", "--contract", path, role="coordinator", session="C1")
        self.assertEqual((code, res.get("code")), (1, "rejected"), res)
        self.assertIn(f"{len(text.encode('utf-8'))} bytes", res["error"])
        self.assertEqual(self.event_count(), 0)


class F2DuplicateKeys(Fix2Base):
    """#2: a duplicate key at any depth is refused, naming the key and its path."""

    def refused(self, text: str, key: str, where: str) -> None:
        self.new_job(); self.activate()
        before = self.event_count()
        code, res = self.run_wf("append", "j", "--type", "intent", "--content", self.raw_file("dup.json", text))
        self.assertEqual((code, res.get("code")), (1, "rejected"), res)
        self.assertIn("rejected(malformed)", res["error"])
        self.assertIn(f"duplicate key {key!r}", res["error"])
        self.assertIn(where, res["error"])
        self.assertEqual(self.event_count(), before)

    def test_top_level_duplicate_is_refused(self):
        self.refused('{"path": "jobs/j/work/a.txt", "sha_before": "", "note": "a", "note": "b"}', "note", "$")

    def test_nested_duplicate_is_refused(self):
        self.refused('{"path": "jobs/j/work/a.txt", "sha_before": "", "note": "n", "x": {"y": {"k": 1, "k": 2}}}',
                     "k", "$.x.y")

    def test_duplicate_in_an_array_of_objects_is_refused(self):
        self.refused('{"path": "jobs/j/work/a.txt", "sha_before": "", "note": "n", "x": [{"k": 1}, {"k": 1, "k": 2}]}',
                     "k", "$.x[1]")

    def test_inline_duplicate_is_refused(self):
        with self.assertRaises(WfRejected) as cm:
            commands._load_json('{"a": {"b": 1, "b": 1}}')
        self.assertIn("duplicate key 'b'", str(cm.exception))


class F2Argv(Fix2Base):
    """#19: an undecodable argv element is a usage error, by position, before any command runs."""

    def test_surrogate_in_argv_is_a_usage_error(self):
        argv = ["wf.py", "--db", self.db, "--role", "coordinator", "--session", "C\udcff", "--json",
                "job", "new", "j", "--contract", self.cpath]
        out = io.StringIO()
        with mock.patch.object(sys, "argv", argv), redirect_stdout(out):
            code = wf.main()
        res = json.loads(out.getvalue())
        self.assertEqual((code, res["code"]), (2, "usage"), res)
        self.assertIn("argument 6", res["error"])
        self.assertFalse(os.path.exists(self.db), "no command may run")

    @unittest.skipUnless(os.name == "posix", "undecodable argv bytes exist only on POSIX")
    def test_posix_bytes_argv_exits_2(self):
        cmd = [os.fsencode(sys.executable), b"-I", os.fsencode(WF_PY), b"--db", os.fsencode(self.db),
               b"--role", b"coordinator", b"--session", b"C\xff", b"--json", b"status", b"j"]
        p = subprocess.run(cmd, capture_output=True, timeout=60)
        self.assertEqual(p.returncode, 2, p.stderr[-2000:])
        self.assertEqual(json.loads(p.stdout)["code"], "usage")


class F2Anchors(Fix2Base):
    """#20: `$` matched before a trailing newline; the validation regexes now anchor at the true end."""

    def test_job_id_with_trailing_newline_is_refused(self):
        for job in ("j\n", "j\r\n"):
            with self.subTest(job=job):
                code, res = self.run_wf("job", "new", job, "--contract", self.cpath, role="coordinator", session="C1")
                self.assertEqual((code, res.get("code")), (1, "rejected"), res)
                self.assertIn("rejected(malformed)", res["error"])
        self.assertEqual(self.event_count(), 0)

    def test_patterns_do_not_match_a_trailing_newline(self):
        cases = [(validate._ID, "j"), (validate._HEX64, "0" * 64), (validate._DEVICE, "con"), (commands._HEX, "ab")]
        for pattern, ok in cases:
            with self.subTest(pattern=pattern.pattern):
                self.assertIsNotNone(pattern.match(ok))
                self.assertIsNone(pattern.match(ok + "\n"))


class F2Fold(unittest.TestCase):
    """#30: one case-folding rule (pathpolicy) and one patch point."""

    def test_fold_follows_pathpolicy_case_rule(self):
        with mock.patch.object(pathpolicy, "CASE_FOLDING", True):
            self.assertEqual(validate._fold("A"), "a")
        with mock.patch.object(pathpolicy, "CASE_FOLDING", False):
            self.assertEqual(validate._fold("A"), "A")

    def test_validate_keeps_no_copy_of_the_rule(self):
        self.assertFalse(hasattr(validate, "CASE_FOLDING"))


class F2Output(Fix2Base):
    """#33: plain-text output on a cp1252 pipe exits 0 with escapes, after the commit, with the row written once."""

    def run_cp1252(self, *argv: str) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
        env["PYTHONIOENCODING"] = "cp1252"  # honoured only without -I/-E, so this child runs with -s -B
        cmd = [sys.executable, "-s", "-B", WF_PY, "--db", self.db, "--role", "coordinator", "--session", "C1", *argv]
        return subprocess.run(cmd, capture_output=True, env=env, timeout=60)

    def grant_with_id(self, grant_id: str) -> str:
        with open(self.gpath, encoding="utf-8") as fh:
            g = json.load(fh)
        g["grant_id"] = grant_id
        return self.raw_file("grant-u.json", json.dumps(g))

    def test_grant_propose_with_non_cp1252_id_exits_0(self):
        self.new_job()
        p = self.run_cp1252("grant", "propose", "j", "--grant", self.grant_with_id("G✓中"))
        self.assertEqual(p.returncode, 0, p.stderr.decode("cp1252", "replace")[-2000:])
        text = p.stdout.decode("cp1252")
        self.assertIn("\\u2713", text)
        self.assertIn("\\u4e2d", text)
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM events WHERE type = 'grant'").fetchone()[0], 1)

    def test_json_output_stays_ascii(self):
        self.new_job()
        p = self.run_cp1252("--json", "grant", "propose", "j", "--grant", self.grant_with_id("G✓中"))
        self.assertEqual(p.returncode, 0, p.stderr.decode("cp1252", "replace")[-2000:])
        text = p.stdout.decode("ascii")  # json.dumps escapes every non-ASCII character
        self.assertIn("G\\u2713\\u4e2d", text)
        self.assertTrue(json.loads(text)["ok"])


if __name__ == "__main__":
    unittest.main()
