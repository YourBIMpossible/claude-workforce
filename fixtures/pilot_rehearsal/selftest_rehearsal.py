"""Self-tests for the disposable rehearsal (TEST FIXTURES only; not part of the normal `test_*.py` suite).

    python -I -B -m unittest discover -s fixtures/pilot_rehearsal -p "selftest_*.py" -v

Runs the rehearsal twice into distinct fresh roots under fixtures/tmp and checks outcomes from the transcripts:
every check ok, both jobs present, the negative job rejected and never closed, no path shared between runs, and
the recorded code revision equal to this checkout's HEAD. Nothing is deleted afterwards.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.path.join(HERE, "rehearse.py")
TMP = os.path.join(REPO, "fixtures", "tmp")


class RehearsalSelfTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = os.path.join(TMP, "pilot_rehearsal-selftest-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
                                + "-" + os.urandom(3).hex())
        cls.proc = subprocess.run([sys.executable, "-I", "-B", SCRIPT, "--root", cls.root, "--runs", "2"],
                                  capture_output=True, text=True, cwd=REPO, timeout=600, check=False)
        cls.transcripts = []
        for n in (1, 2):
            with open(os.path.join(cls.root, f"run-{n}", "transcript.json"), encoding="utf-8") as fh:
                cls.transcripts.append(json.load(fh))

    def test_exit_zero_and_both_verdicts_ok(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stdout + self.proc.stderr)
        for t in self.transcripts:
            self.assertEqual(t["verdict"], "REHEARSAL OK", [c for c in t["checks"] if not c["ok"]])
            self.assertTrue(all(c["ok"] for c in t["checks"]))

    def test_outcomes_not_exit_codes(self):
        for t in self.transcripts:
            by = {c["check"]: c for c in t["checks"]}
            self.assertTrue(by["rhpos: accepted"]["ok"])
            self.assertTrue(by["rhpos: close committed"]["ok"])
            self.assertTrue(by["rhneg: failed criterion rejected"]["ok"])
            self.assertTrue(by["rhneg: close refused while rejected"]["ok"])
            self.assertTrue(by["rhneg: chain still clean after rejection"]["ok"])
            # the negative job's accept command exited 0 (a rejection is a recorded event); the OUTCOME is what counts
            neg_accept = [c for c in t["commands"] if c["argv"][:2] == ["accept", "rhneg"]]
            self.assertEqual(len(neg_accept), 1)
            self.assertEqual(neg_accept[0]["exit"], 0)
            self.assertEqual(neg_accept[0]["result"]["decision"], "rejected")

    def test_two_runs_do_not_share_paths_and_record_revision(self):
        a, b = self.transcripts
        self.assertNotEqual(a["run_root"], b["run_root"])
        self.assertFalse(a["run_root"].startswith(b["run_root"]) or b["run_root"].startswith(a["run_root"]))
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=False).stdout.strip()
        self.assertEqual(a["code_revision"], head)
        self.assertEqual(b["code_revision"], head)
        self.assertEqual(len(a["sessions"]), 3)
        self.assertEqual(len(set(a["sessions"].values())), 3)  # distinct declared sessions per role

    def test_fixture_labels_present(self):
        for t in self.transcripts:
            self.assertIn("TEST FIXTURE", t["label"])
            activate = [c for c in t["commands"] if c["argv"][:2] == ["grant", "activate"]]
            self.assertTrue(activate)
            self.assertTrue(all("TEST FIXTURE" in " ".join(c["argv"]) for c in activate))


if __name__ == "__main__":
    unittest.main()
