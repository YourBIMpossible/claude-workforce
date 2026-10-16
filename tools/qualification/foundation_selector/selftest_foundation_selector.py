"""Self-tests of the foundation selector (validator + runner), on synthetic test trees and mappings only.

Run:  python -I -B -m unittest discover -s tools/qualification/foundation_selector -p "selftest_*.py" -v
(the name is outside the normal `test_*.py` discovery pattern of tools/wf/tests on purpose: nothing here is a
product requirement test). Each test builds a throwaway tests directory and mapping under a fresh temp dir and
drives runner.main; the real tools/wf/tests tree is touched only by the two read-only validation tests.

Properties proved:
  1. a misspelled or deleted test id fails validation (runner FAIL, exit 1, error names the id);
  2. a skipped test is not a pass (requirement INCOMPLETE, runner INCOMPLETE, complete false, exit 3);
  3. an empty selection is not completion (runner FAIL, exit 1);
  4. a failing child test produces a failing runner result (requirement FAIL, exit 1);
  5. a passing synthetic tree with no missing rows is the only way to PASS (exit 0);
  6. the shipped mapping validates against the real tests tree and names only existing tests.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import runner  # noqa: E402
import validate  # noqa: E402

SYN_TESTS = '''import unittest
class Syn(unittest.TestCase):
    def test_pass(self): self.assertTrue(True)
    def test_fail(self): self.assertEqual(1, 2)
    def test_skip(self): self.skipTest("fixture absent")
'''


def _mapping(tests: dict[str, list[str]], status: str = "mapped", missing: bool = False) -> dict:
    reqs = {}
    for rid, ts in tests.items():
        reqs[rid] = {"status": status, "layer": "V", "tests": ts, "sources": ["synthetic"],
                     "missing": [{"assertion": "x", "smallest_test": "y"}] if missing else []}
    return {"schema": "foundation-selector-mapping/1", "requirements": reqs}


class SelectorSelfTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="fsel-")
        self.tests_dir = os.path.join(self.root, "tests")
        os.makedirs(self.tests_dir)
        with open(os.path.join(self.tests_dir, "test_syn.py"), "w", encoding="utf-8") as fh:
            fh.write(SYN_TESTS)
        self.out_root = os.path.join(self.root, "runs")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)  # own temp dir only

    def _run(self, mapping: dict, extra: list[str] = ()) -> tuple[int, dict, str]:
        self._n = getattr(self, "_n", 0) + 1
        out_root = os.path.join(self.out_root, f"call-{self._n}")  # one out-root per call: exactly one run dir
        mpath = os.path.join(self.root, f"mapping-{self._n}.json")
        with open(mpath, "w", encoding="utf-8") as fh:
            json.dump(mapping, fh)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = runner.main(["--mapping", mpath, "--tests-dir", self.tests_dir, "--out-root", out_root, *extra])
        (run,) = os.listdir(out_root)
        with open(os.path.join(out_root, run, "results.json"), encoding="utf-8") as fh:
            return rc, json.load(fh), buf.getvalue()

    def test_1_misspelled_or_deleted_test_id_fails_validation(self):
        for bad in ("test_syn.Syn.test_pas", "test_syn.Syn.test_deleted", "test_gone.Syn.test_pass", "test_syn.Nope.test_pass"):
            with self.subTest(bad=bad):
                v = validate.validate(_mapping({"R1": [bad]}), self.tests_dir)
                self.assertFalse(v.ok)
                self.assertTrue(any(bad in e and "does not exist" in e for e in v.errors), v.errors)
        rc, doc, out = self._run(_mapping({"R1": ["test_syn.Syn.test_pass", "test_syn.Syn.test_deleted"]}))
        self.assertEqual(rc, 1)
        self.assertEqual((doc["result"], doc["complete"]), ("FAIL", False))
        self.assertIn("test_syn.Syn.test_deleted", "\n".join(doc["validation_errors"]))
        self.assertEqual(doc["counts"]["executed"], 0)  # nothing ran on an invalid mapping

    def test_2_skipped_test_is_not_a_pass(self):
        rc, doc, _ = self._run(_mapping({"R1": ["test_syn.Syn.test_pass", "test_syn.Syn.test_skip"]}))
        self.assertEqual(rc, 3)
        self.assertEqual((doc["result"], doc["complete"]), ("INCOMPLETE", False))
        self.assertEqual(doc["requirements"]["R1"]["verdict"], "INCOMPLETE")
        self.assertEqual(doc["tests"]["test_syn.Syn.test_skip"], "skipped")
        self.assertEqual(doc["counts"]["skipped_or_expected_failure"], 1)

    def test_3_empty_selection_is_not_completion(self):
        rc, doc, _ = self._run(_mapping({"R1": []}, status="missing", missing=True))
        self.assertEqual(rc, 1)
        self.assertEqual((doc["result"], doc["complete"]), ("FAIL", False))
        self.assertIn("empty selection", doc["why"])
        self.assertEqual(doc["requirements"]["R1"]["verdict"], "MISSING")
        rc, doc, _ = self._run(_mapping({"R1": []}))  # mapped with no tests: validation error, also FAIL
        self.assertEqual(rc, 1); self.assertEqual(doc["result"], "FAIL")

    def test_4_failing_child_test_fails_the_runner(self):
        rc, doc, _ = self._run(_mapping({"R1": ["test_syn.Syn.test_pass"], "R2": ["test_syn.Syn.test_fail"]}))
        self.assertEqual(rc, 1)
        self.assertEqual((doc["result"], doc["complete"]), ("FAIL", False))
        self.assertEqual(doc["requirements"]["R1"]["verdict"], "PASS")
        self.assertEqual(doc["requirements"]["R2"]["verdict"], "FAIL")
        self.assertEqual(doc["tests"]["test_syn.Syn.test_fail"], "FAIL")
        self.assertEqual(doc["requirement_verdicts"]["FAIL"], ["R2"])

    def test_5_only_all_pass_and_no_missing_rows_is_PASS(self):
        rc, doc, _ = self._run(_mapping({"R1": ["test_syn.Syn.test_pass"]}))
        self.assertEqual(rc, 0)
        self.assertEqual((doc["result"], doc["complete"]), ("PASS", True))
        rc, doc, _ = self._run(_mapping({"R1": ["test_syn.Syn.test_pass"]}, status="partial", missing=True))
        self.assertEqual(rc, 3)
        self.assertEqual((doc["result"], doc["complete"]), ("INCOMPLETE", False))
        self.assertEqual(doc["requirements"]["R1"]["verdict"], "PASS-PARTIAL")
        rc, doc, _ = self._run({"schema": "x", "requirements": {
            "R1": {"status": "mapped", "layer": "V", "tests": ["test_syn.Syn.test_pass"], "sources": ["s"], "missing": []},
            "R9": {"status": "missing", "layer": "V", "tests": [], "sources": ["s"],
                   "missing": [{"assertion": "a", "smallest_test": "t"}]}}})
        self.assertEqual(rc, 3)
        self.assertEqual(doc["requirement_verdicts"]["MISSING"], ["R9"])
        self.assertFalse(doc["complete"])

    def test_6_shipped_mapping_validates_against_the_real_tree(self):
        mapping = validate.load_mapping(os.path.join(HERE, "mapping.json"))
        v = validate.validate(mapping, runner.DEFAULT_TESTS)
        self.assertTrue(v.ok, v.errors)
        self.assertGreater(len(v.selection), 100)
        existing = validate.discover_test_ids(runner.DEFAULT_TESTS)
        self.assertTrue(set(v.selection) <= existing)
        for rid, row in mapping["requirements"].items():
            with self.subTest(requirement=rid):
                self.assertIn(row["status"], validate.STATUSES)
                self.assertTrue(row["sources"])
                if row["status"] in ("partial", "missing"):
                    self.assertTrue(row["missing"])


if __name__ == "__main__":
    unittest.main()
