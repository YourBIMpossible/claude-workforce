"""FIX1 lane FXL, WF-025: identifiers, environment and docs (PACKAGE-FIX1.md items I1-I6; AMENDMENT FIX1-A2 items
I7, I8 live in test_gate_decide.py and here). Each test failed on the stacked base ec0d485 (red-before recorded in
from-lazy RESULTS.md) and passes with the fix. Runs inside the test_wf.py harness (fresh scratch ledger per test)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from unittest import mock

import test_wf
from test_wf import CONTRACT, Base

import wf
from wfcore import gatecore

REPO = test_wf.REPO
WF_PY = os.path.join(test_wf.WF_DIR, "wf.py")


# ------------------------------------------------------------------------------------------------ I1

class I1JobIdNaming(Base):
    def _new(self, job: str):
        return self.run_wf("job", "new", job, "--contract", self.cpath, role="coordinator", session="C1")

    def _malformed(self, job: str, text: str) -> None:
        c, r = self._new(job)
        self.assertEqual((c, r["code"]), (1, "rejected"), r)
        self.assertIn("rejected(malformed)", r["error"])
        self.assertIn(text, r["error"])

    def test_id_equal_to_an_existing_one_case_insensitively(self):
        self.assertEqual(self._new("job1")[0], 0)
        for other in ("JOB1", "Job1"):
            with self.subTest(job=other):
                self._malformed(other, "equals existing job 'job1' case-insensitively")
        c, r = self._new("job1")                       # the exact id keeps the E6 refusal
        self.assertEqual((c, r["error"]), (1, "job job1 already exists"))

    def test_trailing_dot_and_double_dot(self):
        for job in ("j.", "a..b", "j..", "..j"):
            with self.subTest(job=job):
                self._malformed(job, job)

    def test_windows_device_names_any_case_with_or_without_extension(self):
        for job in ("CON", "con", "PRN", "AUX", "aux.txt", "NUL", "nul.json", "COM1", "com9", "LPT1", "Lpt9.log"):
            with self.subTest(job=job):
                self._malformed(job, "Windows device name")

    def test_ordinary_ids_still_work(self):
        for job in ("COM10", "LPT0", "console", "nulx", "a.b", "a-b_c.1"):
            with self.subTest(job=job):
                c, r = self._new(job)
                self.assertEqual(c, 0, r)

    def test_the_rule_applies_to_job_new_only(self):
        """Other commands keep the plain id grammar: a read of `CON` on a ledger without it is `not found`."""
        self.assertEqual(self._new("j")[0], 0)
        c, r = self.run_wf("status", "CON", role="coordinator", session="C1")
        self.assertEqual((c, r["code"]), (1, "refused"), r)
        self.assertIn("job CON not found", r["error"])


# ------------------------------------------------------------------------------------------------ I2

class I2GitAttributes(unittest.TestCase):
    def test_test_data_is_byte_stable(self):
        path = os.path.join(REPO, ".gitattributes")
        self.assertTrue(os.path.isfile(path), path)
        with open(path, encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh]
        self.assertIn("tools/wf/tests/data/** -text", lines)
        git = shutil.which("git")
        if git is None or not os.path.isdir(os.path.join(REPO, ".git")) and not os.path.isfile(os.path.join(REPO, ".git")):
            self.skipTest("no git checkout to ask")
        out = subprocess.run([git, "check-attr", "text", "tools/wf/tests/data/grant_check_corpus.json"], cwd=REPO,
                             capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "tools/wf/tests/data/grant_check_corpus.json: text: unset", out.stderr)


# ------------------------------------------------------------------------------------------------ I3

class I3DbRequired(Base):
    def _run_without_db(self, *argv: str, env_db: str | None = None):
        env = {k: v for k, v in os.environ.items() if k != "WF_DB"}
        if env_db is not None:
            env["WF_DB"] = env_db
        with mock.patch.dict(os.environ, env, clear=True):
            return wf.run(["--role", "coordinator", "--session", "C1", *argv])

    def test_ledger_commands_without_db_or_wf_db_are_usage_errors(self):
        for argv in (("status", "j"), ("verify",), ("job", "new", "j", "--contract", self.cpath),
                     ("append", "j", "--type", "intent", "--content", "{}"), ("digest", "j")):
            with self.subTest(argv=argv):
                c, r = self._run_without_db(*argv)
                self.assertEqual((c, r["code"]), (2, "usage"), r)
                self.assertIn("rejected(usage): --db required", r["error"])

    def test_wf_db_environment_variable_is_accepted(self):
        c, r = self._run_without_db("job", "new", "j", "--contract", self.cpath, env_db=self.db)
        self.assertEqual((c, r["outcome"]), (0, "COMMITTED"), r)
        self.assertEqual(os.path.normcase(r["db"]), os.path.normcase(os.path.abspath(self.db)))
        c, r = self._run_without_db("status", "j", env_db=self.db)
        self.assertEqual(c, 0, r)

    def test_explicit_db_wins_over_wf_db(self):
        self.new_job()
        other = os.path.join(self.dir, "other", "ledger", "l.db")
        with mock.patch.dict(os.environ, {"WF_DB": other}):
            c, r = self.run_wf("status", "j", role="coordinator", session="C1")
        self.assertEqual(c, 0, r)

    def test_help_says_db_is_required(self):
        p = subprocess.run([sys.executable, "-I", "-B", WF_PY, "--help"], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0)
        self.assertIn("WF_DB", p.stdout)
        self.assertIn("required", " ".join(p.stdout.split()))


# ------------------------------------------------------------------------------------------------ I4

class I4ScopeOverlapWarning(Base):
    def _new_with(self, **change):
        c = json.loads(json.dumps(CONTRACT))
        c.update(change)
        return self.run_wf("job", "new", "j", "--contract", json.dumps(c), role="coordinator", session="C1")

    def test_overlapping_scopes_warn_and_do_not_refuse(self):
        c, r = self._new_with(read_scope=["jobs/j/**", "tools/wf/**"], write_scope=["jobs/j/work/**"])
        self.assertEqual((c, r["outcome"]), (0, "COMMITTED"), r)
        self.assertEqual(r["warnings"], ["read_scope overlaps write_scope: jobs/j/** ~ jobs/j/work/**"])

    def test_identical_glob_overlaps(self):
        c, r = self._new_with(read_scope=["jobs/j/work/**"], write_scope=["jobs/j/work/**"])
        self.assertEqual(c, 0, r)
        self.assertEqual(r["warnings"], ["read_scope overlaps write_scope: jobs/j/work/** ~ jobs/j/work/**"])

    def test_disjoint_scopes_have_no_warnings_key(self):
        c, r = self._new_with(read_scope=["tools/wf/**"], write_scope=["jobs/j/work/**"])
        self.assertEqual(c, 0, r)
        self.assertNotIn("warnings", r)
        c2, r2 = self.run_wf("job", "new", "k", "--contract",
                             json.dumps(dict(CONTRACT, read_scope=["jobs/jx/**"], write_scope=["jobs/j/**"])),
                             role="coordinator", session="C1")
        self.assertEqual(c2, 0, r2)
        self.assertNotIn("warnings", r2, "jobs/jx is not inside jobs/j: component boundary")


# ------------------------------------------------------------------------------------------------ I5

class I5Readme(unittest.TestCase):
    def setUp(self) -> None:
        with open(os.path.join(test_wf.WF_DIR, "README.md"), encoding="utf-8") as fh:
            self.text = fh.read()

    def test_stale_statements_are_gone(self):
        self.assertNotIn("R1 has no `wf bind`", self.text)

    def test_recover_reconcile_split_and_new_rules_are_documented(self):
        for needle in ("wf recover", "wf reconcile", "§3B", "R12", "WF_DB", "--db", "case-insensitively",
                       "device name", "read_scope overlaps write_scope", "wf bind"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.text)


# ------------------------------------------------------------------------------------------------ I6

class I6Reasons(unittest.TestCase):
    def test_hook_only_reasons_are_listed(self):
        for name in ("gate-config", "gate-integrity", "audit-unavailable"):
            self.assertIn(name, gatecore.REASONS)
        self.assertEqual(len(set(gatecore.REASONS)), len(gatecore.REASONS))


# ------------------------------------------------------------------------------------------------ I7

@unittest.skipUnless(sys.platform == "win32", "drive-less rooted paths are a Windows form")
class I7DrivelessRootedPath(Base):
    def _payload(self, raw: str) -> dict:
        return {"session_id": "B1", "hook_event_name": "PreToolUse", "tool_name": "Write",
                "tool_input": {"file_path": raw, "content": "x"}, "cwd": self.dir, "transcript_path": "t"}

    def test_rooted_path_resolves_against_the_cwd_drive(self):
        under_root = "\\" + os.path.join(os.path.splitdrive(self.dir)[1].lstrip("\\/"), "jobs", "j", "work", "a.txt")
        self.assertEqual(gatecore.target_paths(self._payload(under_root), self.dir), (["jobs/j/work/a.txt"], None))
        self.assertEqual(gatecore.target_paths(self._payload(under_root.replace("\\", "/")), self.dir),
                         (["jobs/j/work/a.txt"], None))

    def test_rooted_path_outside_the_root_is_outside_the_boundary(self):
        for raw in ("\\Windows\\x.txt", "/Windows/x.txt"):
            with self.subTest(raw=raw):
                self.assertEqual(gatecore.target_paths(self._payload(raw), self.dir), ([], "outside-boundary"))

    def test_relative_and_unc_forms_are_unchanged(self):
        self.assertEqual(gatecore.target_paths(self._payload("jobs\\j\\work\\b.txt"), self.dir),
                         (["jobs/j/work/b.txt"], None))
        # a UNC path keeps its existing answer (fail closed), as at the base
        self.assertEqual(gatecore.target_paths(self._payload("\\\\server\\share\\x.txt"), self.dir),
                         ([], "unresolvable-path"))


# ------------------------------------------------------------------------------------------------ review R (WF-027)

@unittest.skipUnless(sys.platform == "win32", "two-separator prefixes are a Windows UNC form")
class ReviewR1TwoSeparatorPrefixes(Base):
    """WF-027 R1: Windows treats any two leading separators as UNC; I7's drive-rooting must not apply to them."""

    def _payload(self, raw: str) -> dict:
        return {"session_id": "B1", "hook_event_name": "PreToolUse", "tool_name": "Write",
                "tool_input": {"file_path": raw, "content": "x"}, "cwd": self.dir, "transcript_path": "t"}

    def test_every_two_separator_form_keeps_the_base_outcome(self):
        tail = os.path.splitdrive(self.dir)[1].strip("\/").replace("\\", "/") + "/jobs/j/work/a.txt"
        for lead in ("\\\\", "//", "/\\", "\/"):
            with self.subTest(lead=lead):
                raw = lead + tail
                self.assertEqual(gatecore.target_paths(self._payload(raw), self.dir), ([], "unresolvable-path"))
                self.assertEqual(gatecore.target_paths(self._payload(raw.replace("/", "\\")[:2] + raw[2:]), self.dir),
                                 ([], "unresolvable-path"))


@unittest.skipUnless(sys.platform == "win32", "pending\JOB1 and pending\job1 are one directory only on NTFS")
class ReviewS1ClashBeforeMarkerGuard(Base):
    """WF-027 S1: the case-insensitive clash is refused before markers.guard can act on the other job's markers."""

    def _marker(self, state: str) -> str:
        from wfcore import markers, paths
        ref = markers.MarkerRef(paths.pending_dir(self.db, "job1"), 9, "ab" * 16)
        markers.write_atomic(ref, markers.make("job1", 9, ref.attempt_id, "f" * 64, "e" * 64, state, 1, "s", self.db,
                                               "c"))
        return ref.path

    def _new(self, job: str):
        return self.run_wf("job", "new", job, "--contract", self.cpath, role="coordinator", session="C1")

    def test_clash_with_a_committing_marker_present_is_malformed_not_uncertain(self):
        self.assertEqual(self._new("job1")[0], 0)
        path = self._marker("committing")
        c, r = self._new("JOB1")
        self.assertEqual((c, r["code"]), (1, "rejected"), r)
        self.assertIn("equals existing job 'job1' case-insensitively", r["error"])
        self.assertTrue(os.path.exists(path))

    def test_clash_does_not_delete_the_other_jobs_aborting_marker(self):
        self.assertEqual(self._new("job1")[0], 0)
        path = self._marker("aborting")
        c, r = self._new("JOB1")
        self.assertEqual((c, r["code"]), (1, "rejected"), r)
        self.assertTrue(os.path.exists(path), "job1's marker must survive a refused `job new JOB1`")


class ReviewS2OverlapNormalisesDots(Base):
    """WF-027 S2: `.` and `..` are resolved (pathpolicy._norm_glob) before prefixes are compared."""

    def _new_with(self, read_scope):
        c = json.loads(json.dumps(CONTRACT))
        c.update(read_scope=read_scope, write_scope=["jobs/j/work/**"])
        return self.run_wf("job", "new", "j", "--contract", json.dumps(c), role="coordinator", session="C1")

    def test_dot_and_dotdot_globs_warn(self):
        for g in ("./jobs/j/**", "tools/../jobs/j/**", "jobs/./j/work/x/../**"):
            with self.subTest(glob=g):
                from wfcore import commands
                self.assertEqual(commands._scope_overlaps([g], ["jobs/j/work/**"]), [f"{g} ~ jobs/j/work/**"])

    def test_job_new_reports_it(self):
        c, r = self._new_with(["tools/../jobs/j/**"])
        self.assertEqual(c, 0, r)
        self.assertEqual(r["warnings"], ["read_scope overlaps write_scope: tools/../jobs/j/** ~ jobs/j/work/**"])

    def test_a_glob_climbing_out_of_the_boundary_does_not_warn(self):
        from wfcore import commands
        self.assertEqual(commands._scope_overlaps(["../jobs/j/**"], ["jobs/j/work/**"]), [])


class ReviewS5PathInScopeFolding(unittest.TestCase):
    """WF-027 S5: validate._path_in_scope folds case only where the platform folds it (same rule as P1)."""

    def test_folding_follows_the_platform_rule(self):
        from wfcore import validate
        scope = ["jobs/j/work/**"]
        with mock.patch.object(validate, "CASE_FOLDING", True):
            self.assertTrue(validate._path_in_scope("JOBS/J/WORK/a.txt", scope))
        with mock.patch.object(validate, "CASE_FOLDING", False):
            self.assertFalse(validate._path_in_scope("JOBS/J/WORK/a.txt", scope))
            self.assertFalse(validate._path_in_scope("jobs/j/work/a.txt", ["Jobs/J/Work/**"]))
            self.assertTrue(validate._path_in_scope("jobs/j/work/a.txt", scope))

    def test_default_rule_is_os_path_normcase(self):
        from wfcore import validate
        self.assertEqual(validate.CASE_FOLDING, os.path.normcase("A") == "a")


class ReviewS3S4Readme(I5Readme):
    def test_db_shown_as_required_in_the_usage_line(self):
        usage = [ln for ln in self.text.splitlines() if "wf.py" in ln and "<command>" in ln]
        self.assertTrue(usage, "usage line present")
        self.assertNotIn("[--db PATH]", usage[0])
        self.assertIn("--db PATH", usage[0])

    def test_case_folding_limitation_line(self):
        self.assertIn("Case folding follows `os.path.normcase`: Windows folds case; POSIX hosts, including "
                      "case-insensitive volumes such as default macOS APFS, compare case-sensitively. R1 supports "
                      "Windows and Linux.", " ".join(self.text.split()))
