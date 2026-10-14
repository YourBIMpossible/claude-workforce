"""T35 path policy (spec section 4.4): classification P1-P5, P2/P3 rejection, resolution cases (lane B).

Platform-independent: every path is a string the policy resolves by itself; no test touches the filesystem through
the policy, and one test proves the policy never does (open / stat / exists are made to raise). The Windows
behaviour of NTFS for trailing dots, short names, links and junctions is lane D's evidence, not asserted here.
"""
from __future__ import annotations

import builtins
import json
import os
import unittest

from test_wf import Base, CONTRACT  # noqa: E402
from wfcore import commands, pathpolicy  # noqa: E402

# CONTRACT (test_wf): write jobs/j/work/**, read tools/wf/**, watch ledger/**, presence_only fixtures/results/**,
# exclusions 01-PROJECT-BRIEF.md.
RESULTS_OK = {"criteria": [{"id": "AC1", "pass": True, "actual": "ok"}], "changed_paths": ["jobs/j/work/a.txt"],
              "environment": "disposable local scope", "limits": "none"}


def _result(cl: dict, path: str) -> dict:
    rows = [r for r in cl["results"] if r["path"] == path]
    assert len(rows) == 1, (path, cl["results"])
    return rows[0]


class T35Classes(Base):
    def test_plain_P1_behaviour_is_compatible(self):
        cl = commands.classify_paths(["jobs/j/work/a.txt", "fixtures/results/x.json", "ledger/l.db",
                                      "01-PROJECT-BRIEF.md", "elsewhere.txt"], CONTRACT)
        self.assertIs(commands.classify_paths, pathpolicy.classify)
        self.assertEqual({k: cl[k] for k in pathpolicy.CLASSES},
                         {"P1": ["jobs/j/work/a.txt"], "P2": ["fixtures/results/x.json"], "P3": ["ledger/l.db"],
                          "P4": ["elsewhere.txt"], "P5": ["01-PROJECT-BRIEF.md"]})
        self.assertEqual(_result(cl, "jobs/j/work/a.txt")["result"], "allowed")
        self.assertEqual(pathpolicy.violations(pathpolicy.classify(["jobs/j/work/a.txt", "jobs/j/work/sub/b.md"],
                                                                   CONTRACT)), [])

    def test_each_class_and_every_change_kind(self):
        # The interface carries no add/modify/delete kind: the same string stands for each kind, and the record
        # states the limitation. Every class rule is therefore asserted once per class, for all kinds at once.
        cl = pathpolicy.classify(["jobs/j/work/new.txt", "tools/wf/wf.py", "fixtures/results/r.json",
                                  "ledger/l.db", "elsewhere.txt", "01-PROJECT-BRIEF.md"], CONTRACT)
        self.assertEqual([r["class"] for r in cl["results"]], ["P1", "P2", "P2", "P3", "P4", "P5"])
        self.assertTrue(any("change kind" in x for x in cl["limitations"]))
        v = pathpolicy.violations(cl)
        self.assertTrue(any("tools/wf/wf.py" in x and "fixtures/results/r.json" in x and "(P2)" in x for x in v), v)
        self.assertTrue(any("ledger/l.db" in x and "(P3" in x for x in v), v)
        self.assertTrue(any("elsewhere.txt" in x and "01-PROJECT-BRIEF.md" in x and "(P4)" in x for x in v), v)
        self.assertFalse(any("jobs/j/work/new.txt" in x for x in v), v)

    def test_P2_change_rejects(self):
        for p in ("fixtures/results/x.json", "tools/wf/wfcore/db.py"):
            with self.subTest(path=p):
                cl = pathpolicy.classify([p], CONTRACT)
                self.assertEqual(cl["P2"], [p])
                v = pathpolicy.violations(cl)
                self.assertEqual(len(v), 1); self.assertIn(p, v[0]); self.assertIn("P2", v[0])
        self.assertIn("never read", _result(pathpolicy.classify(["fixtures/results/x.json"], CONTRACT),
                                            "fixtures/results/x.json")["reason"])

    def test_P3_change_rejects_and_authorization_is_fail_closed(self):
        # The contract schema (validate.CONTRACT_REQUIRED) has no field naming a separately authorized P3 path and
        # classify receives no grant: the only honest outcome is a violation plus the recorded limitation.
        cl = pathpolicy.classify(["ledger/l.db"], CONTRACT)
        self.assertEqual(cl["P3"], ["ledger/l.db"])
        v = pathpolicy.violations(cl)
        self.assertEqual(len(v), 1); self.assertIn("ledger/l.db", v[0]); self.assertIn("P3", v[0])
        self.assertTrue(any("P3 authorization" in x and "fail closed" in x for x in cl["limitations"]))
        self.assertTrue(any("never authorization proof" in x for x in cl["limitations"]))

    def test_watch_wins_over_write_scope(self):
        c = dict(CONTRACT, write_scope=["cfg/**"], watch=["cfg/settings.json"])
        cl = pathpolicy.classify(["cfg/settings.json", "cfg/other.txt"], c)
        self.assertEqual((cl["P3"], cl["P1"]), (["cfg/settings.json"], ["cfg/other.txt"]))

    def test_P4_is_not_hash_compared_and_nothing_is_read(self):
        def boom(*a, **k):
            raise AssertionError("the path policy must not touch the filesystem")
        saved = (builtins.open, os.stat, os.path.exists, os.path.realpath)
        builtins.open, os.stat, os.path.exists, os.path.realpath = boom, boom, boom, boom
        try:
            cl = pathpolicy.classify(["elsewhere.txt", "jobs/j/work/a.txt", "fixtures/results/x.json"], CONTRACT)
            v = pathpolicy.violations(cl)
        finally:
            builtins.open, os.stat, os.path.exists, os.path.realpath = saved
        self.assertEqual(cl["P4"], ["elsewhere.txt"])
        self.assertIn("no path was read", cl["comparison"])
        self.assertTrue(any("elsewhere.txt" in x for x in v), v)

    def test_P5_records_the_not_detected_limitation(self):
        cl = pathpolicy.classify(["01-PROJECT-BRIEF.md", "../escaped.txt"], CONTRACT)
        self.assertEqual(cl["P5"], ["01-PROJECT-BRIEF.md", "../escaped.txt"])
        self.assertIn(pathpolicy.NOT_DETECTED, cl["limitations"])
        self.assertEqual(_result(cl, "../escaped.txt")["result"], "outside boundary (not detected)")
        v = pathpolicy.violations(cl)
        self.assertTrue(any("../escaped.txt" in x and "outside the monitored boundary" in x for x in v), v)

    def test_case_differences_resolve_to_the_same_class(self):
        cl = pathpolicy.classify(["JOBS/J/WORK/A.TXT", "Ledger/L.DB", "01-project-brief.MD"], CONTRACT)
        self.assertEqual((cl["P1"], cl["P3"], cl["P5"]), (["JOBS/J/WORK/A.TXT"], ["Ledger/L.DB"],
                                                          ["01-project-brief.MD"]))
        c = dict(CONTRACT, write_scope=["Jobs\\J\\Work\\**"])
        self.assertEqual(pathpolicy.classify(["jobs/j/work/x"], c)["P1"], ["jobs/j/work/x"])

    def test_separators_and_dot_collapse(self):
        cl = pathpolicy.classify(["jobs\\j\\work\\a.txt", "./jobs/j/./work//a.txt", "jobs/j/work/../work/a.txt",
                                  "jobs/j/work/sub/../a.txt"], CONTRACT)
        self.assertEqual(len(cl["P1"]), 4)
        self.assertEqual({r["resolved"] for r in cl["results"]}, {"jobs\\j\\work\\a.txt"})

    def test_dot_dot_boundary_escape_is_P5_not_in_scope(self):
        for p in ("../jobs/j/work/a.txt", "jobs/../../jobs/j/work/a.txt", "..", "jobs/j/work/../../../../x"):
            with self.subTest(path=p):
                cl = pathpolicy.classify([p], CONTRACT)
                self.assertEqual(cl["P5"], [p]); self.assertEqual(cl["P1"], [])
                self.assertEqual(_result(cl, p)["reason"], "resolves outside boundary (P5)")
                self.assertTrue(pathpolicy.violations(cl))

    def test_alternate_data_stream_syntax_fails_closed(self):
        for p in ("jobs/j/work/a.txt:stream", "jobs/j/work/a.txt:$DATA", "jobs/j/work/a.txt::$DATA",
                  "jobs/j:x/work/a.txt"):
            with self.subTest(path=p):
                cl = pathpolicy.classify([p], CONTRACT)
                self.assertEqual(cl["P5"], [p])
                r = _result(cl, p)
                self.assertEqual(r["result"], "violation"); self.assertIn("alternate data stream", r["reason"])
                v = pathpolicy.violations(cl)
                self.assertEqual(len(v), 1); self.assertIn(p, v[0]); self.assertIn("alternate data stream", v[0])

    def test_device_names_and_unresolved_forms_fail_closed(self):
        cases = {
            "jobs/j/work/CON": "device name", "jobs/j/work/con.txt": "device name", "NUL": "device name",
            "jobs/j/work/lpt1.log": "device name", "jobs/j/work/COM9": "device name",
            "": "empty path", "   ": "empty path", "jobs/j/work/a\x00.txt": "control character",
            "jobs/j/work/*.txt": "wildcard", "jobs/j/work/a?.txt": "wildcard",
            "C:/jobs/j/work/a.txt": "absolute path", "c:\\x": "absolute path", "/jobs/j/work/a.txt": "rooted",
            "\\\\server\\share\\a": "UNC", "\\\\?\\C:\\a": "UNC",
            "jobs/j/work/a.txt.": "trailing dot or space", "jobs/j/work/a.txt ": "trailing dot or space",
            "jobs/j/work/PROGRA~1/a": "short (8.3) name", "./": "boundary root itself",
            ".": "boundary root itself",
        }
        for p, why in cases.items():
            with self.subTest(path=p):
                cl = pathpolicy.classify([p], CONTRACT)
                self.assertEqual(cl["P5"], [p]); self.assertEqual(cl["P1"] + cl["P2"] + cl["P3"] + cl["P4"], [])
                r = _result(cl, p)
                self.assertEqual(r["result"], "violation"); self.assertIn(why, r["reason"])
                v = pathpolicy.violations(cl)
                self.assertEqual(len(v), 1); self.assertIn("fail closed", v[0]); self.assertIn(why, v[0])
                if p.strip():
                    self.assertIn(p, v[0])
        self.assertEqual(pathpolicy.classify(["jobs/j/work/console.txt", "jobs/j/work/a~b/c"], CONTRACT)["P1"],
                         ["jobs/j/work/console.txt", "jobs/j/work/a~b/c"])  # not device names, not short names
        self.assertEqual(pathpolicy.resolve("C:/x").resolved, "C:\\x")  # drive letter normalized in the report
        self.assertEqual(pathpolicy.classify([42], CONTRACT)["results"][0]["reason"],
                         "unresolvable or prohibited path: not a string")

    def test_limitations_are_honest_about_links_and_ntfs(self):
        lim = " ".join(pathpolicy.classify([], CONTRACT)["limitations"])
        self.assertIn("junctions are not resolved", lim)
        self.assertIn("not established here", lim)

    def test_violation_strings_name_every_offending_path(self):
        paths = ["tools/wf/a.py", "ledger/x", "elsewhere.txt", "01-PROJECT-BRIEF.md", "../e", "jobs/j/work/CON",
                 "jobs/j/work/ok.txt"]
        v = pathpolicy.violations(pathpolicy.classify(paths, CONTRACT))
        joined = "\n".join(v)
        for p in paths[:-1]:
            self.assertIn(p, joined, p)
        self.assertNotIn("jobs/j/work/ok.txt", joined)
        self.assertEqual(len(v), 5)  # P2, P3, P4/P5, escape, unresolvable: one string per kind

    def test_violations_on_a_bare_class_dict_fails_closed(self):
        self.assertEqual(pathpolicy.violations({"P1": ["x"], "P4": [], "P5": []}), [])
        self.assertTrue(pathpolicy.violations({"P4": ["elsewhere/x"], "P5": []}))
        v = pathpolicy.violations({"P2": ["r"], "P3": ["w"]})
        self.assertEqual(len(v), 2); self.assertIn("r", v[0]); self.assertIn("w", v[1])

    def test_accept_rejects_P2_and_P3_and_records_the_limitation(self):
        self.new_job(); self.activate()
        self.run_wf("append", "j", "--type", "delivered", "--content",
                    json.dumps({"claims": [{"claim": "done", "evidence": ["jobs/j/work/a.txt"]}]}))
        for p, tag in (("fixtures/results/x.json", "(P2)"), ("ledger/l.db", "(P3")):
            with self.subTest(path=p):
                bad = dict(RESULTS_OK, changed_paths=["jobs/j/work/a.txt", p])
                c, r = self.run_wf("accept", "j", "--results", json.dumps(bad), role="reviewer", session="R1",
                                   msg_id="A-" + tag[1:3])
                self.assertEqual((c, r["decision"]), (0, "rejected"), r)
                self.assertTrue(any(p in x and tag in x for x in r["reasons"]), r["reasons"])
                self.assertIn(pathpolicy.NOT_DETECTED, r["classes"]["limitations"])
        c, r = self.run_wf("accept", "j", "--results", json.dumps(RESULTS_OK), role="reviewer", session="R1",
                           msg_id="A-ok")
        self.assertEqual((c, r["decision"]), (0, "accepted"), r)
        self.assertEqual(r["classes"]["P1"], ["jobs/j/work/a.txt"])
        self.assertIn(pathpolicy.NOT_DETECTED, r["classes"]["limitations"])


if __name__ == "__main__":
    unittest.main()
