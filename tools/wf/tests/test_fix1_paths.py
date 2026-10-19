"""PACKAGE FIX1 lane FXM (WF-024, MyBuddy): path and platform semantics, items P1-P5.

Ports the WF-021 characterization tests (branch wf021/mybuddy, not merged) as ordinary tests of the fixed behaviour:
  P1  WF-021 F1/F2/F5  case is folded only where the platform folds it (pathpolicy.CASE_FOLDING, os.path.normcase).
  P2  WF-021 F3        drive-less rooted Windows path: moved to lane FXL (AMENDMENT FIX1-A2.1, WF-025 item I7).
  P3  WF-021 F4        grant exclusions use the same per-platform case rule as P1 (deny side only).
  P4  WF-021 F6        a non-UTF-8 file name in a scope is a baseline problem with its escaped name, not a traceback.
  P5  WF-021 F9        backup files are created with mode 0o600 on POSIX.

Two kinds of case: filesystem cases run only on the platform whose semantics they pin (case-sensitive ext4 / byte
names / umask on Linux; NTFS on Windows) and skip elsewhere with that reason; string-level cases simulate the other
platform's case rule by patching `pathpolicy.CASE_FOLDING` and run everywhere. Fixtures live under the test's own
WF_TEST_TMP directory.
"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
import unittest
from unittest import mock

from test_wf import CONTRACT, GRANT, Base  # noqa: E402  (sets sys.path; fresh WF_TEST_TMP)
from wfcore import backup, changeset, grant, pathpolicy, reconcile  # noqa: E402

LINUX = sys.platform.startswith("linux")
WINDOWS = os.name == "nt"
POSIX = os.name == "posix"
SKIP_LINUX = "case-sensitive filesystem / byte file name semantics pinned on Linux only"
SKIP_WINDOWS = "case-insensitive NTFS semantics pinned on Windows only"
SKIP_POSIX = "POSIX file modes; Windows backup files are unchanged"
SKIP_POSIX_NAMES = "POSIX surrogate-escaped byte names"
SKIP_NTFS_NAMES = "NTFS UTF-16 names with lone surrogates exist on Windows only"


def _write(path: str, text: str = "x") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "x", encoding="utf-8") as fh:
        fh.write(text)


def _folding(value: bool):
    """Simulate the other platform's case rule (string level only; the filesystem is not changed)."""
    return mock.patch.object(pathpolicy, "CASE_FOLDING", value, create=True)


def _has_job(db: str) -> bool:
    if not os.path.isfile(db):
        return False
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute("SELECT count(*) FROM contracts WHERE job='j'").fetchone()[0] > 0
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()


def _contract(**scopes) -> dict:
    c = dict(CONTRACT)
    c.update({"read_scope": [], "watch": [], "presence_only": [], "write_scope": ["jobs/j/work/**"]})
    c.update(scopes)
    return c


# ---------------------------------------------------------------------------------------------------------- P1

@unittest.skipUnless(LINUX, SKIP_LINUX)
class P1CaseOnLinux(Base):
    def test_p1_upper_case_scope_glob_walks_the_directory_as_written(self):
        """F1 (row 16): `Docs/**` walks `Docs/` itself; before, it was looked up as `docs` and nothing was walked."""
        os.makedirs(os.path.join(self.dir, "ledger"))
        _write(os.path.join(self.dir, "Docs", "a.txt"))
        _write(os.path.join(self.dir, "docs", "b.txt"))
        b = changeset.baseline(self.dir, _contract(read_scope=["Docs/**"]))
        self.assertEqual((list(b["entries"]), b["problems"]), (["Docs/a.txt"], []))

    def test_p1_upper_case_scope_change_is_detected_by_compare(self):
        """F1: a change under an upper-case scope is observed by `compare` (it was invisible before)."""
        os.makedirs(os.path.join(self.dir, "ledger"))
        _write(os.path.join(self.dir, "Docs", "a.txt"), "one")
        contract = _contract(read_scope=["Docs/**"])
        b = changeset.baseline(self.dir, contract)
        with open(os.path.join(self.dir, "Docs", "a.txt"), "w", encoding="utf-8") as fh:
            fh.write("two")
        _write(os.path.join(self.dir, "Docs", "new.txt"))
        c = changeset.compare(self.dir, contract, b)
        self.assertEqual((c["added"], c["modified"], c["deleted"], c["problems"]),
                         (["Docs/new.txt"], ["Docs/a.txt"], [], []))

    def test_p1_reconcile_relates_the_on_disk_case_and_hashes_the_file(self):
        """F2 (row 17): `jobs/j/work/New.txt` keeps its case for I/O, so its current hash is read."""
        _write(os.path.join(self.dir, "jobs", "j", "work", "New.txt"), "y")
        rel = reconcile._relate("jobs/j/work/New.txt")
        self.assertEqual(rel, "jobs/j/work/New.txt")
        self.assertIsNotNone(reconcile._sha_now(self.dir, rel))

    def test_p1_reconcile_tells_case_variants_apart(self):
        """F2 (row 18): two Linux files differing only in case relate to different paths."""
        self.assertNotEqual(reconcile._relate("jobs/j/work/A.txt"), reconcile._relate("jobs/j/work/a.txt"))

    def test_p1_case_variant_directory_is_not_the_write_scope(self):
        """F5 (row 19): `JOBS/J/WORK/a.txt` is a different directory on ext4 and is no longer classified P1."""
        classes = pathpolicy.classify(["JOBS/J/WORK/a.txt"], dict(CONTRACT))
        self.assertEqual(classes["P1"], [])
        self.assertEqual(classes["unclassified"], ["JOBS/J/WORK/a.txt"])
        self.assertEqual(classes["results"][0]["result"], "violation")

    def test_p1_case_variant_of_an_exclusion_is_not_excluded_on_linux(self):
        """F5: the exclusion `01-PROJECT-BRIEF.md` names that file only; `01-project-brief.md` is another file."""
        classes = pathpolicy.classify(["01-project-brief.md", "01-PROJECT-BRIEF.md"], dict(CONTRACT))
        self.assertEqual(classes["P5"], ["01-PROJECT-BRIEF.md"])
        self.assertEqual(classes["unclassified"], ["01-project-brief.md"])


class P1CaseRuleStrings(unittest.TestCase):
    """String-level: the case rule follows `CASE_FOLDING`; the folding platform (Windows) keeps its old behaviour."""

    def test_p1_case_folding_follows_the_platform(self):
        self.assertEqual(pathpolicy.CASE_FOLDING, os.path.normcase("A") == "a")

    def test_p1_folding_platform_classifies_case_variants_as_before(self):
        with _folding(True):
            classes = pathpolicy.classify(["JOBS/J/WORK/a.txt", "01-project-brief.md"], dict(CONTRACT))
        self.assertEqual(classes["P1"], ["JOBS/J/WORK/a.txt"])
        self.assertEqual(classes["P5"], ["01-project-brief.md"])

    def test_p1_case_sensitive_platform_keeps_case_in_the_comparison_form(self):
        with _folding(False):
            self.assertEqual(pathpolicy.resolve("Jobs/J/a.TXT").cmp, "Jobs\\J\\a.TXT")
        with _folding(True):
            self.assertEqual(pathpolicy.resolve("Jobs/J/a.TXT").cmp, "jobs\\j\\a.txt")

    def test_p1_reconcile_relation_keeps_the_written_case_on_every_platform(self):
        """The related path is used for I/O; on a folding platform the filesystem folds it, so no `.lower()`."""
        for value in (True, False):
            with _folding(value):
                self.assertEqual(reconcile._relate("jobs\\j\\work\\New.txt"), "jobs/j/work/New.txt")


@unittest.skipUnless(WINDOWS, SKIP_WINDOWS)
class P1CaseOnWindows(Base):
    def test_p1_upper_case_scope_glob_still_walks_the_directory(self):
        """NTFS keys stay folded (unchanged from the base): the walk starts at the folded prefix `docs`, so the
        recorded key is `docs/a.txt` although the directory on disk is `Docs/` (review F1-1)."""
        os.makedirs(os.path.join(self.dir, "ledger"))
        _write(os.path.join(self.dir, "Docs", "a.txt"))
        b = changeset.baseline(self.dir, _contract(read_scope=["Docs/**"]))
        self.assertEqual((list(b["entries"]), b["problems"]), (["docs/a.txt"], []))

    def test_p1_case_variant_directory_is_still_the_write_scope(self):
        classes = pathpolicy.classify(["JOBS/J/WORK/a.txt"], dict(CONTRACT))
        self.assertEqual(classes["P1"], ["JOBS/J/WORK/a.txt"])


# ---------------------------------------------------------------------------------------------------------- P3

class P3GrantExclusionCase(Base):
    def _grant(self) -> None:
        g = dict(GRANT, exclusions=["jobs/j/work/secret/**"])
        with open(self.gpath, "w", encoding="utf-8") as fh:
            json.dump(g, fh)
        self.new_job()
        self.activate()

    def _check(self, path: str) -> tuple[int, dict]:
        return self.run_wf("grant-check", "j", "--check-role", "builder", "--action", "write", "--path", path)

    def test_p3_folding_platform_excludes_a_case_variant(self):
        """F4 (row 20): on Windows `SECRET/` is the same directory as `secret/`, so the exclusion applies."""
        self._grant()
        with _folding(True):
            code, res = self._check("jobs/j/work/SECRET/key.txt")
        self.assertEqual((code, res.get("reason")), (1, "path-excluded"), res)

    def test_p3_case_sensitive_platform_excludes_only_the_written_case(self):
        """F4: on ext4 `SECRET/` is another directory: allowed; the written case is excluded on both rules."""
        self._grant()
        with _folding(False):
            code, res = self._check("jobs/j/work/SECRET/key.txt")
            self.assertEqual((code, res.get("allow")), (0, True), res)
            code, res = self._check("jobs/j/work/secret/key.txt")
            self.assertEqual((code, res.get("reason")), (1, "path-excluded"), res)

    def test_p3_resources_stay_case_sensitive(self):
        """Folding is deny-side only: a resource glob never matches a case variant (allow side unchanged)."""
        with _folding(True):
            self.assertFalse(grant._glob_in("JOBS/j/work/a.txt", ["jobs/j/work/**"]))
            self.assertTrue(grant._excluded("JOBS/j/work/a.txt", ["jobs/j/work/**"]))
        with _folding(False):
            self.assertFalse(grant._excluded("JOBS/j/work/a.txt", ["jobs/j/work/**"]))


# ---------------------------------------------------------------------------------------------------------- P4

@unittest.skipUnless(LINUX, SKIP_LINUX)
class P4NonUtf8Names(Base):
    def _byte_file(self) -> None:
        os.makedirs(os.path.join(self.dir, "ledger"))
        work = os.path.join(self.dir, "jobs", "j", "work")
        os.makedirs(work)
        fd = os.open(os.path.join(os.fsencode(work), b"\xff.txt"), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        os.close(fd)

    def test_p4_non_utf8_name_is_a_baseline_problem_with_its_escaped_name(self):
        """F6 (row 43): reported as a problem (fail closed) with the escaped name; no surrogate key is recorded."""
        self._byte_file()
        b = changeset.baseline(self.dir, dict(CONTRACT))
        self.assertEqual(b["entries"], {})
        self.assertEqual(len(b["problems"]), 1, b["problems"])
        self.assertIn("jobs/j/work/\\xff.txt", b["problems"][0])
        json.dumps(b, ensure_ascii=False).encode("utf-8")  # the problem text itself is encodable

    def test_p4_job_new_refuses_with_a_structured_answer(self):
        """F6: `wf job new` refuses (no traceback, no job) instead of raising UnicodeEncodeError."""
        self._byte_file()
        code, res = self.run_wf("job", "new", "j", "--contract", self.cpath, role="coordinator", session="C1")
        self.assertNotEqual(code, 0, res)
        self.assertFalse(res.get("ok", True), res)
        self.assertIn("\\xff.txt", json.dumps(res))
        self.assertFalse(_has_job(self.db))

    def test_p4_non_utf8_name_after_job_new_is_a_compare_problem(self):
        """F6: a byte name that appears later makes `compare` fail closed, so reconcile/accept refuse."""
        os.makedirs(os.path.join(self.dir, "ledger"))
        os.makedirs(os.path.join(self.dir, "jobs", "j", "work"))
        b = changeset.baseline(self.dir, dict(CONTRACT))
        fd = os.open(os.path.join(os.fsencode(self.dir), b"jobs/j/work/\xfe.txt"), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        os.close(fd)
        c = changeset.compare(self.dir, dict(CONTRACT), b)
        self.assertEqual(c["added"], [])
        self.assertTrue(any("\\xfe.txt" in p for p in c["problems"]), c["problems"])


@unittest.skipUnless(LINUX, SKIP_LINUX)
class P4NonUtf8EveryMessage(Base):
    """Review R2 (WF-027): every text the walk records names a non-UTF-8 path in its escaped form, so the baseline
    (stored with canon.canonical, ensure_ascii=False) never holds a surrogate and `job new` never crashes."""

    def setUp(self) -> None:
        super().setUp()
        os.makedirs(os.path.join(self.dir, "ledger"))
        self.work = os.path.join(self.dir, "jobs", "j", "work")
        os.makedirs(self.work)
        self.bwork = os.fsencode(self.work)
        target = os.path.join(self.dir, "elsewhere")
        os.makedirs(target)
        with open(os.path.join(target, "t.txt"), "x", encoding="utf-8") as fh:
            fh.write("t")
        self.btarget = os.fsencode(target)

    def _job_new(self) -> tuple[int, dict]:
        return self.run_wf("job", "new", "j", "--contract", self.cpath, role="coordinator", session="C1")

    def _baseline_is_canonical(self) -> dict:
        from wfcore import canon  # noqa: PLC0415
        b = changeset.baseline(self.dir, dict(CONTRACT))
        canon.canonical(b).encode("utf-8")  # raised UnicodeEncodeError before R2
        return b

    def test_r2_non_utf8_file_symlink_is_a_limitation_with_its_escaped_name(self):
        os.symlink(os.path.join(self.btarget, b"t.txt"), os.path.join(self.bwork, b"\xfe-link"))
        b = self._baseline_is_canonical()
        self.assertIn("skipped link or reparse point: jobs/j/work/\\xfe-link", b["limitations"])
        code, res = self._job_new()
        self.assertEqual(code, 0, res)

    def test_r2_non_utf8_directory_symlink_is_a_limitation_with_its_escaped_name(self):
        os.symlink(self.btarget, os.path.join(self.bwork, b"\xfd-dir"))
        b = self._baseline_is_canonical()
        self.assertIn("skipped link or reparse point: jobs/j/work/\\xfd-dir", b["limitations"])
        code, res = self._job_new()
        self.assertEqual(code, 0, res)

    def test_r2_non_utf8_unresolvable_name_is_refused_with_its_escaped_name(self):
        """A name that is both non-UTF-8 and unresolvable (trailing dot) is a problem naming `\\xfc.`."""
        os.close(os.open(os.path.join(self.bwork, b"\xfc."), os.O_WRONLY | os.O_CREAT | os.O_EXCL))
        b = self._baseline_is_canonical()
        self.assertTrue(any("jobs/j/work/\\xfc." in p for p in b["problems"]), b["problems"])
        code, res = self._job_new()
        self.assertEqual((code, res.get("code")), (1, "refused"), res)
        self.assertIn("\\xfc.", res["error"])
        self.assertNotIn("\udcfc", res["error"])

    def test_r2_file_under_a_non_utf8_directory_is_refused_with_its_escaped_name(self):
        sub = os.path.join(self.bwork, b"\xfb")
        os.makedirs(sub)
        os.close(os.open(os.path.join(sub, b"a.txt"), os.O_WRONLY | os.O_CREAT | os.O_EXCL))
        self._baseline_is_canonical()
        code, res = self._job_new()
        self.assertEqual((code, res.get("code")), (1, "refused"), res)
        self.assertIn("jobs/j/work/\\xfb/a.txt", res["error"])


class P4ShownNameRules(unittest.TestCase):
    """Review R3: the display form of a walked name is chosen explicitly per platform, never by os.fsencode's error
    handler. POSIX: an undecodable byte (surrogate-escaped as U+DC80..U+DCFF) shows as `\\xNN`. Windows: NTFS names are
    UTF-16, so every lone surrogate shows as `\\udNNN`. Any other lone surrogate shows as `\\udNNN` everywhere."""

    def test_r3_escape_rules_for_both_platforms(self):
        """String level, runs everywhere: the pure rule with the platform given explicitly (no os.name mock)."""
        esc = changeset._escape_name
        for windows in (False, True):
            with self.subTest(windows=windows):
                self.assertEqual(esc("jobs/j/a.txt", windows=windows), "jobs/j/a.txt")
                self.assertEqual(esc("jobs/j/caf\u00e9.txt", windows=windows), "jobs/j/caf\u00e9.txt")
                self.assertEqual(esc("jobs/j/\ud800.txt", windows=windows), "jobs/j/\\ud800.txt")
        self.assertEqual(esc("jobs/j/\udcff.txt", windows=False), "jobs/j/\\xff.txt")
        self.assertEqual(esc("jobs/j/\udcff.txt", windows=True), "jobs/j/\\udcff.txt")
        self.assertEqual(esc("a\udced\udca0\udc80b", windows=False), "a\\xed\\xa0\\x80b")
        self.assertEqual(esc("a\ud83d\ude00b", windows=True), "a\\ud83d\\ude00b")   # a split pair stays visible

    def test_r3_shown_is_total_and_always_encodable(self):
        for name in ("plain", "\udc80", "\ud800", "\udfff", "x\udcff\ud800y", "\U0001f600"):
            with self.subTest(name=ascii(name)):
                changeset._shown(name).encode("utf-8")

    @unittest.skipUnless(POSIX, SKIP_POSIX_NAMES)
    def test_r3_shown_on_posix_escapes_bytes_as_x(self):
        self.assertEqual(changeset._shown("jobs/j/\udcff.txt"), "jobs/j/\\xff.txt")
        self.assertEqual(changeset._shown("jobs/j/\ud800.txt"), "jobs/j/\\ud800.txt")

    @unittest.skipUnless(WINDOWS, SKIP_NTFS_NAMES)
    def test_r3_shown_on_windows_escapes_lone_surrogates_as_u(self):
        self.assertEqual(changeset._shown("jobs/j/\udcff.txt"), "jobs/j/\\udcff.txt")
        self.assertEqual(changeset._shown("jobs/j/\ud800.txt"), "jobs/j/\\ud800.txt")
        self.assertEqual(changeset._shown("jobs/j/caf\u00e9.txt"), "jobs/j/caf\u00e9.txt")


_NOT_UNICODE = "file name is not valid Unicode (undecodable bytes or a lone surrogate) inside the boundary: "


@unittest.skipUnless(LINUX, SKIP_LINUX)
class P4MessageTextOnLinux(Base):
    def test_r3_problem_text_names_the_rule_and_the_escaped_bytes(self):
        os.makedirs(os.path.join(self.dir, "ledger"))
        work = os.path.join(self.dir, "jobs", "j", "work")
        os.makedirs(work)
        os.close(os.open(os.path.join(os.fsencode(work), b"\xff.txt"), os.O_WRONLY | os.O_CREAT | os.O_EXCL))
        b = changeset.baseline(self.dir, dict(CONTRACT))
        self.assertEqual(b["problems"], [_NOT_UNICODE + "jobs/j/work/\\xff.txt"])


@unittest.skipUnless(WINDOWS, SKIP_NTFS_NAMES)
class P4LoneSurrogateOnNtfs(Base):
    """Review R3, real NTFS files (Zen's probe): names holding a lone UTF-16 surrogate in a recorded scope."""

    def test_r3_ntfs_lone_surrogate_names_are_refused_with_u_escapes_and_the_baseline_hashes(self):
        from wfcore import canon  # noqa: PLC0415
        os.makedirs(os.path.join(self.dir, "ledger"))
        work = os.path.join(self.dir, "jobs", "j", "work")
        os.makedirs(work)
        for name in ("a\udc80b.txt", "a\ud800b.txt"):
            with open(os.path.join(work, name), "x", encoding="utf-8") as fh:
                fh.write("x")
        b = changeset.baseline(self.dir, dict(CONTRACT))
        self.assertEqual(b["entries"], {})
        self.assertEqual(sorted(b["problems"]), sorted([_NOT_UNICODE + "jobs/j/work/a\\udc80b.txt",
                                                        _NOT_UNICODE + "jobs/j/work/a\\ud800b.txt"]))
        canon.sha256_text(canon.canonical(b))           # raised UnicodeEncodeError at base 6d73687
        code, res = self.run_wf("job", "new", "j", "--contract", self.cpath, role="coordinator", session="C1")
        self.assertEqual((code, res.get("code")), (1, "refused"), res)
        self.assertIn("a\\udc80b.txt", res["error"])


# ---------------------------------------------------------------------------------------------------------- P5

@unittest.skipUnless(POSIX, SKIP_POSIX)
class P5BackupMode(Base):
    def _backup(self, umask: int) -> int:
        self.new_job()
        dest = os.path.join(self.dir, f"backup-{umask:03o}.db")
        old = os.umask(umask)
        try:
            backup.backup(self.db, dest, writer_stopped=True)
        finally:
            os.umask(old)
        return stat.S_IMODE(os.stat(dest).st_mode)

    def test_p5_backup_is_owner_read_write_only_under_umask_022(self):
        """F9 (row 74): 0o600, not 0o755 (no execute bits, not world-readable)."""
        self.assertEqual(self._backup(0o022), 0o600)

    def test_p5_backup_is_owner_read_write_only_under_umask_000(self):
        self.assertEqual(self._backup(0o000), 0o600)


if __name__ == "__main__":
    unittest.main(verbosity=2)
