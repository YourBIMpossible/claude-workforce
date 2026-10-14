"""T24 (lane A): acceptance declaration checks. `wf accept` rejects when the submitted results omit or mismatch
the contract hash or the baseline (FIRST-RELEASE-SPEC.md row T24; seam docstring on validate.accept_declarations).

Unit tests call accept_declarations directly; flow tests run the full `wf accept` through test_wf.Base with a
delivered builder event (session B1) and a different reviewer session (R1), as in test_seam_dispatch.test_S4.
"""
from __future__ import annotations

import copy
import json
import unittest

from test_wf import Base, CONTRACT, dbm  # noqa: E402
from wfcore import validate  # noqa: E402

CSHA = "a" * 64


def good_results(csha: str) -> dict:
    return {"criteria": [{"id": c["id"], "pass": True, "actual": c["expected"]} for c in CONTRACT["acceptance_criteria"]],
            "environment": CONTRACT["environment"], "limits": "none", "changed_paths": ["jobs/j/work/a.txt"],
            "contract_sha256": csha, "baseline": copy.deepcopy(CONTRACT["baseline"])}


class T24Unit(unittest.TestCase):
    def test_correct_declarations_return_no_reasons(self):
        self.assertEqual(validate.accept_declarations(good_results(CSHA), dict(CONTRACT), CSHA), [])

    def test_canonical_equality_ignores_key_order_and_whitespace(self):
        contract = dict(CONTRACT, baseline={"manifest": [{"p": "a", "h": "1"}], "git_revision": "r", "uncommitted_state": False})
        results = good_results(CSHA)
        results["baseline"] = {"uncommitted_state": False, "git_revision": "r", "manifest": [{"h": "1", "p": "a"}]}
        self.assertEqual(validate.accept_declarations(results, contract, CSHA), [])

    def test_wrong_contract_hash_rejected(self):
        reasons = validate.accept_declarations(good_results("b" * 64), dict(CONTRACT), CSHA)
        self.assertEqual(len(reasons), 1, reasons)
        self.assertIn("contract_sha256", reasons[0])
        self.assertIn("mismatch", reasons[0])

    def test_missing_contract_hash_rejected(self):
        results = good_results(CSHA)
        del results["contract_sha256"]
        reasons = validate.accept_declarations(results, dict(CONTRACT), CSHA)
        self.assertEqual(reasons, ["declaration missing: contract_sha256"])

    def test_missing_baseline_rejected(self):
        results = good_results(CSHA)
        del results["baseline"]
        reasons = validate.accept_declarations(results, dict(CONTRACT), CSHA)
        self.assertEqual(reasons, ["declaration missing: baseline"])

    def test_each_baseline_subfield_mismatch_rejected_with_field_named(self):
        contract = dict(CONTRACT, baseline={"manifest": [{"path": "a", "sha256": "1"}], "git_revision": "abc",
                                            "uncommitted_state": False})
        for field, wrong in (("manifest", [{"path": "a", "sha256": "2"}]), ("git_revision", "def"),
                             ("uncommitted_state", True)):
            with self.subTest(field=field):
                results = good_results(CSHA)
                results["baseline"] = copy.deepcopy(contract["baseline"])
                results["baseline"][field] = wrong
                reasons = validate.accept_declarations(results, contract, CSHA)
                self.assertEqual(len(reasons), 1, reasons)
                self.assertIn(f"baseline.{field}", reasons[0])
                self.assertIn("mismatch", reasons[0])

    def test_each_baseline_subfield_missing_rejected_with_field_named(self):
        for field in ("manifest", "git_revision", "uncommitted_state"):
            with self.subTest(field=field):
                results = good_results(CSHA)
                del results["baseline"][field]
                reasons = validate.accept_declarations(results, dict(CONTRACT), CSHA)
                self.assertEqual(reasons, [f"declaration missing: baseline.{field}"])

    def test_extra_baseline_field_rejected_with_field_named(self):
        results = good_results(CSHA)
        results["baseline"]["extra"] = 1
        reasons = validate.accept_declarations(results, dict(CONTRACT), CSHA)
        self.assertEqual(len(reasons), 1, reasons)
        self.assertIn("baseline.extra", reasons[0])

    def test_non_dict_baseline_rejected_without_exception(self):
        for bad in ("abc", 7, None, [], True):
            with self.subTest(baseline=bad):
                results = good_results(CSHA)
                results["baseline"] = bad
                reasons = validate.accept_declarations(results, dict(CONTRACT), CSHA)
                self.assertEqual(reasons, ["declaration mismatch: baseline is not an object"])

    def test_bad_ordinary_input_types_return_reasons_not_exceptions(self):
        self.assertTrue(validate.accept_declarations("not an object", dict(CONTRACT), CSHA))  # type: ignore[arg-type]
        self.assertTrue(validate.accept_declarations({}, dict(CONTRACT), CSHA))
        results = good_results(CSHA)
        results["contract_sha256"] = 12345
        self.assertIn("contract_sha256", validate.accept_declarations(results, dict(CONTRACT), CSHA)[0])
        results = good_results(CSHA)
        results["baseline"]["manifest"] = float("nan")  # not canonicalisable (allow_nan=False)
        reasons = validate.accept_declarations(results, dict(CONTRACT), CSHA)
        self.assertEqual(len(reasons), 1, reasons)
        self.assertIn("baseline.manifest", reasons[0])
        self.assertTrue(validate.accept_declarations(good_results(CSHA), {}, CSHA))  # contract without baseline
        self.assertTrue(validate.accept_declarations(good_results(CSHA), "no", CSHA))  # type: ignore[arg-type]

    def test_hash_and_baseline_both_wrong_gives_both_reasons(self):
        results = good_results("b" * 64)
        del results["baseline"]
        reasons = validate.accept_declarations(results, dict(CONTRACT), CSHA)
        self.assertEqual(len(reasons), 2, reasons)
        self.assertTrue(any("contract_sha256" in r for r in reasons))
        self.assertTrue(any("baseline" in r for r in reasons))


class T24Flow(Base):
    """Full `wf accept` flow: builder B1 delivers, reviewer R1 accepts."""

    def deliver(self) -> str:
        res = self.new_job()
        csha = res["contract_sha256"]
        self.activate()
        c, r = self.run_wf("--role", "builder", "--session", "B1", "append", "j", "--type", "delivered",
                           "--content", json.dumps({"claims": [{"claim": "done", "evidence": ["jobs/j/work/a.txt"]}]}))
        self.assertEqual(c, 0, r)
        return csha

    def accept(self, results: dict) -> tuple[int, dict]:
        return self.run_wf("--role", "reviewer", "--session", "R1", "accept", "j", "--results", json.dumps(results))

    def recorded_accept(self) -> dict:
        conn = dbm.connect(self.db, create=False)
        try:
            rows = [r for r in dbm.rows(conn, "j") if r["type"] == "accept"]
        finally:
            conn.close()
        self.assertEqual(len(rows), 1)
        return json.loads(rows[0]["content"])

    def test_correct_declarations_accepted(self):
        csha = self.deliver()
        c, res = self.accept(good_results(csha))
        self.assertEqual(c, 0, res)
        self.assertEqual(res.get("decision"), "accepted", res)
        self.assertEqual(res.get("reasons"), [], res)
        self.assertEqual(self.recorded_accept()["decision"], "accepted")

    def test_wrong_contract_hash_rejected(self):
        csha = self.deliver()
        c, res = self.accept(good_results("f" * 64))
        self.assertEqual(res.get("decision"), "rejected", res)
        self.assertTrue(any("contract_sha256" in r for r in res.get("reasons", [])), res)
        self.assertIn("contract_sha256", self.recorded_accept()["reason"])
        self.assertNotEqual(csha, "f" * 64)

    def test_missing_contract_hash_rejected(self):
        csha = self.deliver()
        results = good_results(csha)
        del results["contract_sha256"]
        c, res = self.accept(results)
        self.assertEqual(res.get("decision"), "rejected", res)
        self.assertIn("declaration missing: contract_sha256", res.get("reasons", []), res)

    def test_missing_baseline_rejected(self):
        csha = self.deliver()
        results = good_results(csha)
        del results["baseline"]
        c, res = self.accept(results)
        self.assertEqual(res.get("decision"), "rejected", res)
        self.assertIn("declaration missing: baseline", res.get("reasons", []), res)

    def test_baseline_subfield_mismatch_rejected_with_field_named(self):
        csha = self.deliver()
        results = good_results(csha)
        results["baseline"]["git_revision"] = "not-the-contract-revision"
        c, res = self.accept(results)
        self.assertEqual(res.get("decision"), "rejected", res)
        self.assertTrue(any("baseline.git_revision" in r for r in res.get("reasons", [])), res)

    def test_non_dict_baseline_rejected_without_exception(self):
        csha = self.deliver()
        results = good_results(csha)
        results["baseline"] = "not an object"
        c, res = self.accept(results)
        self.assertEqual(res.get("decision"), "rejected", res)
        self.assertIn("declaration mismatch: baseline is not an object", res.get("reasons", []), res)

    def test_recorded_reasons_contain_declaration_reasons(self):
        csha = self.deliver()
        results = good_results("0" * 64)
        results["baseline"]["uncommitted_state"] = True
        c, res = self.accept(results)
        self.assertEqual(res.get("decision"), "rejected", res)
        expected = validate.accept_declarations(results, dict(CONTRACT), csha)
        self.assertTrue(expected)
        for reason in expected:
            self.assertIn(reason, res.get("reasons", []), res)
            self.assertIn(reason, self.recorded_accept()["reason"])

    def test_existing_checks_unchanged_environment_mismatch_still_rejects(self):
        csha = self.deliver()
        results = good_results(csha)
        results["environment"] = "somewhere else"
        c, res = self.accept(results)
        self.assertEqual(res.get("decision"), "rejected", res)
        self.assertTrue(any("environment" in r for r in res.get("reasons", [])), res)


if __name__ == "__main__":
    unittest.main()
