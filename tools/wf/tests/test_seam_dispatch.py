"""Dispatch-ready seam (2026-10-13, lane D): the interfaces lanes A/B/C implement against, and the properties that must
hold before AND after those lanes land. Nothing here asserts lane behaviour; it asserts the seam never lies.

  S1 `wf verify --checkpoint` with malformed syntax -> usage error (exit 2, code "usage"), no ledger access needed.
  S2 `--checkpoint` without a job -> usage error.
  S3 a well-formed checkpoint is either compared (result carries a "checkpoint" key) or explicitly refused as not
     implemented; it is never silently ignored (ok result without a "checkpoint" key).
  S4 commands.classify_paths is the pathpolicy.classify alias; pathpolicy.violations on P4/P5 rejects; the accept
     decision takes validate.accept_declarations into account (a monkeypatched reason rejects).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from test_wf import Base, CONTRACT, WF_DIR  # noqa: E402
from wfcore import checkpointcmp, commands, pathpolicy, validate  # noqa: E402
from wfcore.db import WfUsage  # noqa: E402

GOOD_HASH = "a" * 64


class SeamDispatch(Base):
    def _verify(self, *extra: str) -> tuple[int, dict]:
        argv = [sys.executable, "-I", "-B", os.path.join(WF_DIR, "wf.py"), "--json", "--db", self.db, "verify", *extra]
        p = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        try:
            return p.returncode, json.loads(p.stdout)
        except json.JSONDecodeError:
            return p.returncode, {"stdout": p.stdout, "stderr": p.stderr}

    def test_S1_malformed_checkpoint_is_usage_error(self):
        self.new_job(); self.activate()
        for bad in ("abc", "1", "1:zz", "1:" + "A" * 64, ":" + GOOD_HASH, "0:" + GOOD_HASH):
            with self.subTest(value=bad):
                code, res = self._verify("j", "--checkpoint", bad)
                self.assertEqual(code, 2, res)
                self.assertEqual(res.get("code"), "usage", res)
        with self.assertRaises(WfUsage):
            checkpointcmp.parse("1-2", "j")

    def test_S2_checkpoint_without_job_is_usage_error(self):
        self.new_job(); self.activate()
        code, res = self._verify("--checkpoint", "1:" + GOOD_HASH)
        self.assertEqual(code, 2, res)
        self.assertEqual(res.get("code"), "usage", res)

    def test_S3_wellformed_checkpoint_is_compared_or_refused_never_ignored(self):
        self.new_job(); self.activate()
        code, res = self._verify("j", "--checkpoint", "1:" + GOOD_HASH)
        if res.get("ok") is False and res.get("code") == "refused":
            self.assertIn("not implemented", res.get("error", ""), res)
            self.assertIn("no checkpoint verification was performed", res.get("error", ""), res)
            self.assertEqual(code, 1, res)
        else:
            self.assertIn("checkpoint", res, ("a supplied checkpoint must leave a trace in the result", res))
        code2, plain = self._verify("j")
        self.assertEqual(code2, 0, plain)
        self.assertTrue(plain.get("clean"), plain)
        self.assertNotIn("checkpoint", plain)

    def test_S4_accept_seams_are_wired(self):
        self.assertIs(commands.classify_paths, pathpolicy.classify)
        classes = pathpolicy.classify(["jobs/j/work/a.txt", "ledger/l.db", "elsewhere/x"], dict(CONTRACT))
        self.assertEqual(set(classes), {"P1", "P2", "P3", "P4", "P5"})
        self.assertEqual(pathpolicy.violations({"P1": ["x"], "P4": [], "P5": []}), [])
        self.assertTrue(pathpolicy.violations({"P4": ["elsewhere/x"], "P5": []}))
        self.assertEqual(validate.accept_declarations({}, dict(CONTRACT), "0" * 64).__class__, list)
        # a returned reason must reach the decision: monkeypatch, run an otherwise-acceptable accept, expect rejected
        self.new_job(); self.activate()
        self.run_wf("--role", "builder", "--session", "B1", "append", "j", "--type", "delivered",
                    "--content", json.dumps({"claims": [{"claim": "done", "evidence": ["jobs/j/work/a.txt"]}]}))
        results = {"criteria": [{"id": c["id"], "pass": True, "actual": c["expected"]} for c in CONTRACT["acceptance_criteria"]],
                   "environment": CONTRACT["environment"], "limits": "none", "changed_paths": ["jobs/j/work/a.txt"]}
        orig = validate.accept_declarations
        validate.accept_declarations = lambda r, c, s: ["declaration test: contract_sha256 missing"]
        try:
            c, res = self.run_wf("--role", "reviewer", "--session", "R1", "accept", "j", "--results", json.dumps(results))
        finally:
            validate.accept_declarations = orig
        self.assertEqual(res.get("decision"), "rejected", res)
        self.assertIn("declaration test: contract_sha256 missing", res.get("reasons", []), res)


if __name__ == "__main__":
    unittest.main()
