"""Path-policy hardening (owner grant 2026-10-15): malformed inputs and normalization bypasses of the approved
string-level policy in wfcore.pathpolicy. Each test names the regression it pins. Platform-independent, no
filesystem access by the policy (one test proves it again under the malformed inputs).
"""
from __future__ import annotations

import builtins
import json
import os
import unittest
from unittest import mock

from test_wf import Base, CONTRACT, DECLARATIONS  # noqa: E402
from wfcore import canon, commands, pathpolicy  # noqa: E402

P = pathpolicy.CLASSES
EXCL = "01-PROJECT-BRIEF.md"


def _lists(cl: dict) -> dict:
    return {k: cl[k] for k in P}


def _only(cl: dict, key: str, paths: list) -> None:
    for k in P:
        assert cl[k] == (paths if k == key else []), (k, cl[k])


class MalformedChangedList(Base):
    """`changed` that is not a list of strings must fail closed with rejection information, never classify
    something else and never crash the caller."""

    def test_regression_string_changed_was_classified_per_character(self):
        cl = pathpolicy.classify("jobs/j/work/a.txt", CONTRACT)
        _only(cl, "P5", ["jobs/j/work/a.txt"])
        self.assertEqual(cl["results"][0]["reason"],
                         "unresolvable or prohibited path: changed-path list is a string, not a list")
        v = pathpolicy.violations(cl)
        self.assertEqual(len(v), 1); self.assertIn("jobs/j/work/a.txt", v[0]); self.assertIn("fail closed", v[0])

    def test_regression_none_and_scalar_changed_raised(self):
        for bad in (None, 7, 1.5, True, b"jobs/j/work/a.txt", {"a": 1}, {"x"}):
            with self.subTest(changed=bad):
                cl = pathpolicy.classify(bad, CONTRACT)
                self.assertEqual(cl["P5"], [repr(bad)])
                self.assertEqual(cl["P1"] + cl["P2"] + cl["P3"] + cl["P4"] + cl["unclassified"], [])
                self.assertIn("not a list", cl["results"][0]["reason"])
                v = pathpolicy.violations(cl)
                self.assertEqual(len(v), 1); self.assertIn(repr(bad), v[0])
                json.dumps(cl)  # the record must stay serializable for the accept event

    def test_regression_non_string_entries_leaked_raw_objects_into_the_record(self):
        cl = pathpolicy.classify([b"jobs/j/work/a.txt", {"a"}, None, 3, "jobs/j/work/ok.txt"], CONTRACT)
        self.assertEqual(cl["P5"], ["b'jobs/j/work/a.txt'", "{'a'}", "None", "3"])
        self.assertEqual(cl["P1"], ["jobs/j/work/ok.txt"])
        for r in cl["results"][:4]:
            self.assertEqual(r["result"], "violation"); self.assertIn("not a string", r["reason"])
            self.assertIsInstance(r["path"], str)
        json.dumps(cl); canon.sha256_obj(cl)
        v = pathpolicy.violations(cl)
        self.assertEqual(len(v), 1)
        for s in ("b'jobs/j/work/a.txt'", "{'a'}", "None", "3"):
            self.assertIn(s, v[0])

    def test_empty_whitespace_and_control_entries(self):
        for p in ("", " ", "\t", " ", "\n", "jobs/j/work/\x1f", "jobs/j/work/a\x7f"):
            with self.subTest(path=repr(p)):
                cl = pathpolicy.classify([p], CONTRACT)
                _only(cl, "P5", [p])
                self.assertEqual(cl["results"][0]["result"], "violation")
                v = pathpolicy.violations(cl)
                self.assertEqual(len(v), 1); self.assertIn("fail closed", v[0])
        cl = pathpolicy.classify([], CONTRACT)
        self.assertEqual(_lists(cl), {k: [] for k in P}); self.assertEqual(pathpolicy.violations(cl), [])


class MalformedContract(Base):
    """A contract whose scopes cannot be read must never let a path classify as allowed."""

    def test_regression_non_dict_contract_raised(self):
        for bad in (None, [], "x", 3):
            with self.subTest(contract=bad):
                cl = pathpolicy.classify(["jobs/j/work/a.txt"], bad)
                _only(cl, "P5", ["jobs/j/work/a.txt"])
                self.assertIn("contract is not an object", cl["results"][0]["reason"])
                self.assertIn("contract is not an object", cl["contract_problems"][0])
                v = pathpolicy.violations(cl)
                self.assertTrue(any("jobs/j/work/a.txt" in x for x in v), v)
                self.assertTrue(any("contract" in x for x in v), v)

    def test_regression_non_list_scope_was_ignored_so_watched_path_became_P1(self):
        for key, bad in (("watch", {"x": 1}), ("watch", "ledger/**"), ("presence_only", "fixtures/results/**"),
                         ("exclusions", None), ("read_scope", 5)):
            with self.subTest(scope=key, value=bad):
                c = dict(CONTRACT, write_scope=["**"]); c[key] = bad
                cl = pathpolicy.classify(["ledger/l.db", "fixtures/results/x", EXCL, "tools/wf/a"], c)
                self.assertEqual(cl["P1"], [], cl)
                self.assertEqual(len(cl["P5"]), 4)
                self.assertTrue(any(key in x and "not a list" in x for x in cl["contract_problems"]), cl)
                self.assertTrue(pathpolicy.violations(cl))

    def test_regression_non_string_glob_was_skipped_so_excluded_path_became_P1(self):
        for bad in ({"g": EXCL}, None, 3, [EXCL]):
            with self.subTest(glob=bad):
                c = dict(CONTRACT, write_scope=["**"], exclusions=[bad, "other.md"])
                cl = pathpolicy.classify([EXCL, "jobs/j/work/a.txt"], c)
                self.assertEqual(cl["P1"], [], cl)
                self.assertEqual(cl["P5"], [EXCL, "jobs/j/work/a.txt"])
                self.assertTrue(any("exclusions" in x and "not a string" in x for x in cl["contract_problems"]), cl)
                v = pathpolicy.violations(cl)
                self.assertTrue(any(EXCL in x for x in v), v)

    def test_regression_dot_dot_in_glob_was_silently_ineffective(self):
        # exclusions "jobs/j/work/../other/**" must exclude jobs/j/other/** (it was matching nothing)
        c = dict(CONTRACT, write_scope=["jobs/j/**"], exclusions=["jobs/j/work/../other/**"])
        cl = pathpolicy.classify(["jobs/j/other/x", "jobs/j/work/x"], c)
        self.assertEqual((cl["P5"], cl["P1"]), (["jobs/j/other/x"], ["jobs/j/work/x"]))
        # a glob that climbs above the root cannot describe anything inside the boundary: contract problem
        c = dict(CONTRACT, write_scope=["../**"])
        cl = pathpolicy.classify(["jobs/j/work/x"], c)
        self.assertEqual(cl["P1"], [])
        self.assertTrue(any("write_scope" in x and "outside the boundary" in x for x in cl["contract_problems"]), cl)
        self.assertTrue(pathpolicy.violations(cl))

    def test_empty_and_dot_globs_match_nothing(self):
        c = dict(CONTRACT, write_scope=["", ".", "./", "/"], exclusions=[])
        cl = pathpolicy.classify(["jobs/j/work/x"], c)
        self.assertEqual(cl["P5"], ["jobs/j/work/x"])  # a malformed scope list fails the whole classification closed
        self.assertTrue(any("write_scope" in x and "matches nothing" in x for x in cl["contract_problems"]), cl)
        self.assertTrue(any("contract scopes malformed" in x for x in pathpolicy.violations(cl)))

    @mock.patch.object(pathpolicy, "CASE_FOLDING", True)  # FIX1 A2.3: these assert the Windows (folding) rule
    def test_scope_globs_normalize_separators_case_and_trailing_separator(self):
        c = dict(CONTRACT, write_scope=["JOBS\\J\\Work\\"], exclusions=["01-project-brief.MD/"])
        cl = pathpolicy.classify(["jobs/j/work/sub/x", "01-PROJECT-BRIEF.md", "jobs/j/work"], c)
        # the directory named by a glob is itself in scope (pre-existing equality rule), its siblings are not
        self.assertEqual((cl["P1"], cl["P5"]), (["jobs/j/work/sub/x", "jobs/j/work"], ["01-PROJECT-BRIEF.md"]))
        self.assertEqual(pathpolicy.classify(["jobs/j/workx"], c)["unclassified"], ["jobs/j/workx"])


class NormalizationBypass(Base):
    @mock.patch.object(pathpolicy, "CASE_FOLDING", True)  # FIX1 A2.3: these assert the Windows (folding) rule
    def test_mixed_separators_and_case_in_both_path_and_glob(self):
        c = dict(CONTRACT, write_scope=["Jobs/J\\Work/**"], watch=["LEDGER\\**"])
        cl = pathpolicy.classify(["JOBS\\j/WORK\\A.txt", "jobs/j/./work//b", "Ledger\\L.DB"], c)
        self.assertEqual((cl["P1"], cl["P3"]), (["JOBS\\j/WORK\\A.txt", "jobs/j/./work//b"], ["Ledger\\L.DB"]))

    def test_dot_dot_escapes_including_round_trips(self):
        for p in ("../jobs/j/work/a", "jobs/../../x/jobs/j/work/a", "jobs/j/work/../../../..", "..\\..\\x",
                  "jobs/j/work/a/../../../../../jobs/j/work/a"):
            with self.subTest(path=p):
                cl = pathpolicy.classify([p], CONTRACT)
                _only(cl, "P5", [p])
                self.assertEqual(cl["results"][0]["result"], "outside boundary (not detected)")
                self.assertTrue(pathpolicy.violations(cl))
        cl = pathpolicy.classify(["jobs/j/work/sub/../a", "jobs/x/../j/work/a"], CONTRACT)
        self.assertEqual(cl["P1"], ["jobs/j/work/sub/../a", "jobs/x/../j/work/a"])

    def test_sibling_prefix_confusion(self):
        # a sibling whose name starts with the allowed directory name is not inside it
        for p in ("jobs/j/workspace/x", "jobs/j/work2/x", "jobs/j/work.txt", "jobs/j/work-old/x", "jobs/jx/work/a",
                  "jobs/j/wor/k/a"):
            with self.subTest(path=p):
                cl = pathpolicy.classify([p], CONTRACT)
                _only(cl, "unclassified", [p])
        for glob in ("jobs/j/work", "jobs/j/work/", "jobs/j/work/*", "jobs/j/work/**"):
            with self.subTest(glob=glob):
                c = dict(CONTRACT, write_scope=[glob])
                cl = pathpolicy.classify(["jobs/j/workspace/x", "jobs/j/work/x"], c)
                self.assertEqual((cl["unclassified"], cl["P1"]), (["jobs/j/workspace/x"], ["jobs/j/work/x"]), glob)
        # P2/P3/P5 siblings likewise stay out of the protected class and are unclassified, never P1
        cl = pathpolicy.classify(["ledgers/x", "fixtures/results-old/x", "01-PROJECT-BRIEF.md.bak"], CONTRACT)
        _only(cl, "unclassified", ["ledgers/x", "fixtures/results-old/x", "01-PROJECT-BRIEF.md.bak"])

    def test_regression_com0_lpt0_and_superscript_device_names_were_P1(self):
        for p in ("jobs/j/work/COM0", "jobs/j/work/lpt0.txt", "jobs/j/work/COM¹", "jobs/j/work/LPT².log",
                  "jobs/j/work/com³"):
            with self.subTest(path=p):
                cl = pathpolicy.classify([p], CONTRACT)
                _only(cl, "P5", [p])
                self.assertIn("device name", cl["results"][0]["reason"])
        self.assertEqual(pathpolicy.classify(["jobs/j/work/COM10", "jobs/j/work/command"], CONTRACT)["P1"],
                         ["jobs/j/work/COM10", "jobs/j/work/command"])

    def test_device_and_stream_syntax_combined_with_escapes_and_case(self):
        for p, why in (("../CON", "device name"), ("jobs/j/work/../../../NUL.txt", "device name"),
                       ("JOBS/J/WORK/A.TXT:STREAM", "alternate data stream"), ("..\\x:y", "alternate data stream"),
                       ("jobs/j/work/con:$DATA", "alternate data stream"), ("jobs/j/work/con.", "trailing dot"),
                       ("jobs/j/work/. ", "trailing dot or space"), ("jobs/j/work/.. ", "trailing dot or space")):
            with self.subTest(path=p):
                cl = pathpolicy.classify([p], CONTRACT)
                _only(cl, "P5", [p])
                self.assertEqual(cl["results"][0]["result"], "violation")
                self.assertIn(why, cl["results"][0]["reason"])

    def test_nothing_is_read_under_malformed_input(self):
        def boom(*a, **k):
            raise AssertionError("the path policy must not touch the filesystem")
        saved = (builtins.open, os.stat, os.path.exists, os.path.realpath, os.listdir)
        builtins.open, os.stat, os.path.exists, os.path.realpath, os.listdir = (boom,) * 5
        try:
            cl = pathpolicy.classify([None, "", "../x", "jobs/j/work/CON", "fixtures/results/x", "ledger/l.db"],
                                     dict(CONTRACT, exclusions=[None]))
            pathpolicy.violations(cl)
        finally:
            builtins.open, os.stat, os.path.exists, os.path.realpath, os.listdir = saved
        self.assertEqual(cl["P1"], [])


class ViolationsRobustness(Base):
    def test_regression_violations_on_non_dict_raised(self):
        for bad in (None, [], "x"):
            with self.subTest(classes=bad):
                v = pathpolicy.violations(bad)
                self.assertEqual(len(v), 1); self.assertIn("malformed", v[0])

    def test_malformed_result_rows_and_contract_problems_reject(self):
        v = pathpolicy.violations({"results": [None, 3, {"path": "p", "class": "P9", "result": "weird"},
                                               {"path": "q", "result": "allowed"}],
                                   "contract_problems": ["exclusions: entry 0 is not a string"]})
        joined = "\n".join(v)
        self.assertIn("p", joined); self.assertIn("exclusions: entry 0 is not a string", joined)
        self.assertNotIn("\nq", joined)
        self.assertTrue(any("malformed result row" in x for x in v), v)

    def test_preserved_P2_P3_fail_closed_and_bare_dict_fallback(self):
        cl = pathpolicy.classify(["fixtures/results/x.json", "tools/wf/a.py", "ledger/l.db"], CONTRACT)
        self.assertEqual((cl["P2"], cl["P3"]), (["fixtures/results/x.json", "tools/wf/a.py"], ["ledger/l.db"]))
        v = pathpolicy.violations(cl)
        self.assertTrue(any("(P2)" in x for x in v) and any("(P3" in x and "fail closed" in x for x in v), v)
        self.assertIn("never read", cl["results"][0]["reason"])
        self.assertIn(pathpolicy.NOT_DETECTED, cl["limitations"])
        self.assertTrue(any("never authorization proof" in x for x in cl["limitations"]))
        self.assertEqual(pathpolicy.violations({"P1": ["x"], "P4": [], "P5": []}), [])
        self.assertEqual(len(pathpolicy.violations({"P2": ["r"], "P3": ["w"], "P4": ["o"]})), 3)


class AcceptIntegration(Base):
    def test_accept_rejects_malformed_entries_and_records_them(self):
        self.new_job(); self.activate()
        self.run_wf("append", "j", "--type", "delivered", "--content",
                    json.dumps({"claims": [{"claim": "done", "evidence": ["jobs/j/work/a.txt"]}]}))
        base = {"criteria": [{"id": "AC1", "pass": True, "actual": "ok"}], "environment": "disposable local scope",
                "limits": "none", **DECLARATIONS}
        bad = dict(base, changed_paths=["jobs/j/work/a.txt", "", "jobs/j/work/COM0", None, 3])
        c, r = self.run_wf("accept", "j", "--results", json.dumps(bad), role="reviewer", session="R1", msg_id="H1")
        self.assertEqual((c, r["decision"]), (0, "rejected"), r)
        # commands.accept stringifies every entry before classify: None and 3 arrive as "None" / "3" (unclassified)
        self.assertEqual(r["classes"]["P5"], ["", "jobs/j/work/COM0"])
        self.assertEqual(r["classes"]["unclassified"], ["None", "3"])
        self.assertEqual(r["classes"]["P1"], ["jobs/j/work/a.txt"])
        self.assertTrue(any("fail closed" in x and "COM0" in x for x in r["reasons"]), r["reasons"])
        self.assertIs(commands.classify_paths, pathpolicy.classify)
        c, r = self.run_wf("accept", "j", "--results", json.dumps(dict(base, changed_paths=["jobs/j/work/a.txt"])),
                           role="reviewer", session="R1", msg_id="H2")
        self.assertEqual((c, r["decision"]), (0, "accepted"), r)


if __name__ == "__main__":
    unittest.main()
