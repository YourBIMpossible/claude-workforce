"""WF-029 (Lazy): injected-failure tests for backup.py, export.py, manifest.py, markers.py, changeset.py and
pathpolicy.py paths that WF-020 left unexecuted (from-lazy WF-020\\REMAINING-GAPS.md). Base: fix1/lazy-ids 96fb5c4.
Current behaviour is asserted; a contradiction with the spec or the code's own docstring is an expectedFailure whose
docstring names the observation (a FINDING). Injection only on fixtures/tmp ledgers. Runs in the test_wf.py harness."""
from __future__ import annotations

import builtins
import json
import os
import sqlite3
import subprocess
import sys
import unittest
from unittest import mock

import test_wf
from test_wf import Base

from wfcore import backup, changeset, export, manifest, markers, paths, pathpolicy
from wfcore import db as dbm

SHA = "a" * 64


class Fixture(Base):
    def setUp(self) -> None:
        super().setUp()
        self.new_job()
        self.activate()

    def coord(self, *argv: str):
        return self.run_wf(*argv, role="coordinator", session="C1")


# ------------------------------------------------------------------------------------------- backup.py

class _NoBackup:
    """Read-only source connection proxy whose engine backup fails."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def __getattr__(self, name):
        return getattr(self._c, name)

    def backup(self, *a, **kw):
        raise sqlite3.OperationalError("injected backup failure")


class BackupFailures(Fixture):
    def dest(self) -> str:
        return os.path.join(self.dir, "b.db")

    def test_destination_created_between_check_and_open_is_dest_exists(self):
        """backup.py:112 (row 51)."""
        with mock.patch.object(backup.os, "open", side_effect=FileExistsError("raced")):
            c, r = self.coord("backup", "j", "--to", self.dest())
        self.assertEqual((c, r["code"], r["error"]), (1, "refused", "dest-exists"), r)

    def test_parent_removed_between_check_and_open_is_dest_parent_missing(self):
        """backup.py:114 (row 52)."""
        with mock.patch.object(backup.os, "open", side_effect=FileNotFoundError("raced")):
            c, r = self.coord("backup", "j", "--to", self.dest())
        self.assertEqual((c, r["code"], r["error"]), (1, "refused", "dest-parent-missing"), r)

    def test_engine_backup_failure_is_reported_and_the_partial_file_left(self):
        """backup.py:121 (row 53)."""
        real = backup._open_ro
        made = []

        def open_ro(path):
            conn = real(path)
            made.append(path)
            return _NoBackup(conn) if len(made) == 1 else conn

        with mock.patch.object(backup, "_open_ro", side_effect=open_ro):
            c, r = self.coord("backup", "j", "--to", self.dest())
        self.assertEqual((c, r["code"]), (1, "error"), r)
        self.assertIn("engine backup failed (OperationalError: injected backup failure)", r["error"])
        self.assertTrue(os.path.exists(self.dest()), "the partial destination is left in place (documented)")

    @unittest.expectedFailure
    def test_busy_copy_check_is_a_lock_timeout_refusal(self):
        """FINDING (backup.py:87, row 50): backup._check documents that a busy/lock error "still raises
        (db.WfLockTimeout)", but it re-raises the raw sqlite3.OperationalError, which is not a WfError: `wf backup`
        then ends as an unexpected exception (since FIX1 E1: `internal-error`, exit 4) instead of the `lock-timeout`
        refusal (exit 1). Observed: OperationalError escapes wf.run."""
        with mock.patch.object(backup.db, "integrity", side_effect=sqlite3.OperationalError("database is locked")):
            c, r = self.coord("backup", "j", "--to", self.dest())
        self.assertEqual((c, r["code"]), (1, "lock-timeout"), r)


# ------------------------------------------------------------------------------------------- export.py

class _Entry:
    def __init__(self, name: str, fail: bool = False) -> None:
        self.name = name
        self._fail = fail

    def is_symlink(self):
        if self._fail:
            raise OSError("injected stat failure")
        return False

    def is_junction(self):
        return False

    def is_dir(self, follow_symlinks=True):
        return False

    def is_file(self, follow_symlinks=True):
        return False


class _Scan:
    def __init__(self, entries) -> None:
        self._e = entries

    def __enter__(self):
        return iter(self._e)

    def __exit__(self, *a):
        return False


class ExportChecks(Fixture):
    def export_problems(self) -> list[str]:
        c, r = self.coord("verify", "j")
        self.assertEqual(c, 0, r)
        return r["jobs"]["j"]["export"]

    def test_staging_file_of_a_dead_process_is_reported_preserved(self):
        """export.py:128-129 (row 60): a `<file>.<pid>-<hex8>.tmp` whose writer pid is gone."""
        dead = 4194303 if sys.platform == "win32" else 2 ** 22 + 1
        name = f"events.jsonl.{dead}-0123abcd.tmp"
        with open(os.path.join(paths.export_dir(self.db, "j"), name), "w") as fh:
            fh.write("partial")
        self.assertIn(export.PRESERVED + name, self.export_problems())

    def test_entry_that_is_neither_link_dir_nor_file(self):
        """export.py:190 (row 61) and :192 (row 62, OSError while classifying)."""
        out = paths.export_dir(self.db, "j")
        real = export.os.scandir

        def scandir(p="."):
            if os.path.normcase(os.path.abspath(p)) == os.path.normcase(os.path.abspath(out)):
                return _Scan([_Entry("odd-device"), _Entry("unstattable", fail=True)])
            return real(p)

        with mock.patch.object(export.os, "scandir", side_effect=scandir):
            probs = export.strays(out, set(export.FILES))
        self.assertEqual(probs, ["unexpected export entry: odd-device", "unexpected export entry: unstattable"])


# ------------------------------------------------------------------------------------------- manifest.py

class ManifestRows(unittest.TestCase):
    def test_format_line_refuses_a_bad_sha(self):
        """manifest.py format_line (row 63)."""
        with self.assertRaises(ValueError) as cm:
            manifest.format_line(manifest.Entry("A" * 64, 1, "f"))
        self.assertIn("sha256 must be 64 lowercase hex", str(cm.exception))

    def test_read_refuses_a_whitespace_only_line(self):
        """manifest.py read (row 64)."""
        d = os.path.join(test_wf.TMP, "wf029-manifest")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "manifest.txt")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f"{SHA}  1  a\n \t \n")
        with self.assertRaises(manifest.ManifestMalformed) as cm:
            manifest.read(path)
        self.assertEqual((cm.exception.line_no, cm.exception.reason), (2, "whitespace-only line"))


# ------------------------------------------------------------------------------------------- markers.py

class MarkerRows(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.pend = paths.pending_dir(self.db, "j")
        os.makedirs(self.pend, exist_ok=True)
        self.ref = markers.MarkerRef(self.pend, 7, "ab" * 16)

    def test_marker_naming_another_attempt_is_malformed(self):
        """markers.py:156 (row 71): guard refuses `uncertain-write pending ... (marker malformed)`."""
        markers.write_atomic(self.ref, {"attempt_id": "cd" * 16, "seq": 7})
        c, r = self.intent()
        self.assertEqual((c, r["code"]), (1, "uncertain-write-pending"), r)
        self.assertIn("(marker malformed)", r["error"])

    def test_persistent_sharing_violation_on_replace_is_marker_unwritable(self):
        """markers.py:57 (row 72): os.replace keeps raising PermissionError past SHARE_RETRY_S -> re-raised; the write
        refuses `marker-unwritable` and nothing is appended."""
        conn = dbm.connect(self.db, create=False)
        try:
            before = dbm.head(conn, "j")[0]
        finally:
            conn.close()
        with mock.patch.object(markers, "SHARE_RETRY_S", 0.05), \
                mock.patch.object(markers.os, "replace", side_effect=PermissionError(32, "sharing violation")):
            c, r = self.intent(note="locked marker")
        self.assertEqual((c, r["code"]), (1, "refused"), r)
        self.assertIn("marker-unwritable", r["error"])
        conn = dbm.connect(self.db, create=False)
        try:
            self.assertEqual(dbm.head(conn, "j")[0], before)
        finally:
            conn.close()

    def test_persistent_sharing_violation_on_read_is_reported_marker_corrupt(self):
        """markers.py:81 (row 73): read() re-raises PermissionError after SHARE_RETRY_S; describe reports it."""
        markers.write_atomic(self.ref, {"attempt_id": self.ref.attempt_id, "seq": 7, "state": "committing"})
        real_open = builtins.open
        target = os.path.normcase(self.ref.path)

        def fake_open(file, *a, **kw):
            if isinstance(file, str) and os.path.normcase(file) == target:
                raise PermissionError(32, "sharing violation")
            return real_open(file, *a, **kw)

        with mock.patch.object(markers, "SHARE_RETRY_S", 0.05), mock.patch("builtins.open", side_effect=fake_open):
            (entry,) = markers.describe(self.db, "j")
        self.assertEqual(entry["state"], "marker-corrupt")
        self.assertIn("PermissionError", entry["problem"])

    def test_marker_vanished_between_list_and_read_is_unreadable(self):
        """markers.py:211 (row 74): listed, then gone before read -> state `unreadable`."""
        markers.write_atomic(self.ref, {"attempt_id": self.ref.attempt_id, "seq": 7})
        with mock.patch.object(markers, "read", return_value=None):
            (entry,) = markers.describe(self.db, "j")
        self.assertEqual(entry["state"], "unreadable")


# ------------------------------------------------------------------------------------------- changeset / pathpolicy

class ChangesetRows(Base):
    """Baseline walk of the harness contract's read scope `tools/wf/**` (relative to the scratch boundary root)."""

    def scope(self) -> str:
        d = os.path.join(self.dir, "tools", "wf")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "a.txt"), "w") as fh:
            fh.write("a")
        with open(os.path.join(d, "b.txt"), "w") as fh:
            fh.write("b")
        return d

    def baseline(self) -> dict:
        self.new_job()
        with open(os.path.join(self.dir, "jobs", "j", "export", "events.jsonl"), encoding="utf-8") as fh:
            return json.loads(json.loads(fh.readline())["content"])["baseline_manifest"]

    def test_file_entry_that_is_a_link_is_skipped(self):
        """changeset.py:134 (row 66): a file entry seen as a link (file symlinks need a privilege here: injected)."""
        d = self.scope()
        real = changeset._is_link
        link = os.path.normcase(os.path.join(d, "b.txt"))
        with mock.patch.object(changeset, "_is_link", side_effect=lambda p: os.path.normcase(p) == link or real(p)):
            base = self.baseline()
        self.assertEqual(sorted(base["entries"]), ["tools/wf/a.txt"])
        self.assertIn("skipped link or reparse point: tools/wf/b.txt", base["limitations"])

    @unittest.skipUnless(sys.platform == "win32", "uses an NTFS junction")
    def test_scope_prefix_that_is_a_junction_is_skipped(self):
        """changeset.py:155 (row 67): the static prefix `tools/wf` itself is a junction."""
        behind = os.path.join(self.dir, "behind")
        os.makedirs(behind)
        with open(os.path.join(behind, "secret.txt"), "w") as fh:
            fh.write("s")
        os.makedirs(os.path.join(self.dir, "tools"))
        made = subprocess.run(["cmd", "/c", "mklink", "/J", os.path.join(self.dir, "tools", "wf"), behind],
                              capture_output=True, text=True)
        if made.returncode != 0:
            self.skipTest(f"junction not creatable: {made.stdout.strip()}")
        base = self.baseline()
        self.assertEqual(base["entries"], {})
        self.assertIn("skipped link or reparse point: tools/wf", base["limitations"])

    def test_unresolvable_name_inside_the_boundary_refuses_job_new(self):
        """changeset.py:138 (row 68): pathpolicy cannot resolve an in-boundary name -> problem -> job new refuses."""
        self.scope()
        real = pathpolicy.resolve

        def resolve(p):
            r = real(p)
            if p == "tools/wf/b.txt":
                r.problem = "injected device name"
            return r

        with mock.patch.object(changeset.pathpolicy, "resolve", side_effect=resolve):
            c, r = self.run_wf("job", "new", "j", "--contract", self.cpath, role="coordinator", session="C1")
        self.assertEqual((c, r["code"]), (1, "refused"), r)
        self.assertIn("unresolvable path inside the boundary: tools/wf/b.txt (injected device name)", r["error"])

    def test_entry_whose_lstat_fails_is_treated_as_a_link(self):
        """changeset.py:77 (row 69): an unreadable entry is not traversed and recorded as skipped."""
        d = self.scope()
        real = changeset.os.lstat
        bad = os.path.normcase(os.path.join(d, "b.txt"))

        def lstat(p, *a, **kw):
            if os.path.normcase(str(p)) == bad:
                raise PermissionError("injected")
            return real(p, *a, **kw)

        with mock.patch.object(changeset.os, "lstat", side_effect=lstat):
            base = self.baseline()
        self.assertEqual(sorted(base["entries"]), ["tools/wf/a.txt"])
        self.assertIn("skipped link or reparse point: tools/wf/b.txt", base["limitations"])


class PathpolicyRow(unittest.TestCase):
    def test_resolve_refuses_a_non_string(self):
        """pathpolicy.py:115 (row 65): direct API (classify handles non-strings before calling resolve)."""
        r = pathpolicy.resolve(5)
        self.assertEqual(r.problem, "not a string")
