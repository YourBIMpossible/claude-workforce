"""WF-021 step 3 (MyBuddy): links, pid liveness, file modes and file-name encoding pinned as OBSERVED on Linux.

Rows refer to fixtures/results/wf021-mybuddy/PLATFORM-REVIEW.md (characterization tests; a fix must update them).
All cases are Linux-only (POSIX symlinks without privilege, os.kill liveness branch, umask modes, byte file names) and
skip elsewhere with that reason. Fixtures live under the test's own WF_TEST_TMP directory.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import unittest

from test_wf import CONTRACT, Base  # noqa: E402  (sets sys.path; fresh WF_TEST_TMP)
from wfcore import backup, changeset, export, observe, paths, reconcile  # noqa: E402

LINUX = sys.platform.startswith("linux")
SKIP = "POSIX symlink / pid / mode / byte-name semantics pinned on Linux only"


def _has_job(db: str) -> bool:
    import sqlite3  # noqa: PLC0415
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute("SELECT count(*) FROM contracts WHERE job='j'").fetchone()[0] > 0
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()


@unittest.skipUnless(LINUX, SKIP)
class LinksOnLinux(Base):
    def test_row35_reconcile_treats_a_symlinked_component_as_crossing_a_link(self):
        """Row #35: the Linux twin of the Windows-only junction test: a symlink component under the root is a
        link crossing (state unknown, never followed); a real directory is not."""
        outside = os.path.join(self.dir, "outside")
        os.makedirs(outside)
        work = os.path.join(self.dir, "jobs", "j", "work")
        os.makedirs(work)
        os.symlink(outside, os.path.join(work, "ln"), target_is_directory=True)
        os.makedirs(os.path.join(work, "real"))
        self.assertTrue(reconcile._crosses_link(self.dir, "jobs/j/work/ln/f.txt"))
        self.assertFalse(reconcile._crosses_link(self.dir, "jobs/j/work/real/f.txt"))
        self.assertIsNone(reconcile._sha_now(self.dir, "jobs/j/work/ln"))

    def test_row36_observe_refuses_a_symlinked_job_directory_and_writes_nothing_outside(self):
        """Row #36: the Linux twin of the Windows-only junctioned-job test."""
        self.new_job()
        outside = os.path.join(self.dir, "outside")
        os.makedirs(outside)
        jobs = paths.jobs_dir(self.db)
        os.makedirs(jobs, exist_ok=True)
        jdir = os.path.join(jobs, "j")
        if os.path.isdir(jdir) and not os.path.islink(jdir):
            os.rename(jdir, jdir + "-kept")              # keep, never delete
        os.symlink(outside, jdir, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            observe.append_log(self.db, "j", {"probe": True})
        self.assertEqual(os.listdir(outside), [])


@unittest.skipUnless(LINUX, SKIP)
class PidLivenessOnLinux(Base):
    def test_row70_foreign_dead_pid_staging_is_an_orphan_and_live_pid_is_not(self):
        """Row #70: the os.kill(pid, 0) branch of export._pid_alive, exercised with a foreign pid. A staging file
        named after an exited child is reported as preserved; one named after a running child is not."""
        out = os.path.join(self.dir, "export")
        os.makedirs(out)
        p_dead = subprocess.Popen([sys.executable, "-c", "pass"])
        p_dead.wait()
        p_live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            for pid in (p_dead.pid, p_live.pid):
                with open(os.path.join(out, f"head.json.{pid}-deadbeef.tmp"), "x", encoding="utf-8") as fh:
                    fh.write("{}")
            found = export.orphans(out)
        finally:
            p_live.kill()
            p_live.wait()
        self.assertIn(f"{export.PRESERVED}head.json.{p_dead.pid}-deadbeef.tmp", found)
        self.assertNotIn(f"{export.PRESERVED}head.json.{p_live.pid}-deadbeef.tmp", found)


@unittest.skipUnless(LINUX, SKIP)
class ModesAndNamesOnLinux(Base):
    def test_row74_observed_backup_file_mode_follows_umask_with_execute_bits(self):
        """Row #74 (finding, low): the backup is created with os.open's default mode 0o777, so under the process
        umask it carries execute bits and is readable by others when the umask allows."""
        self.new_job()
        dest = os.path.join(self.dir, "backup.db")
        old = os.umask(0o022)
        try:
            backup.backup(self.db, dest, writer_stopped=True)
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(os.stat(dest).st_mode), 0o755)

    def test_row43_observed_non_utf8_file_name_reaches_wf_job_new_as_an_uncaught_unicode_error(self):
        """Row #43 (finding, medium on Linux; re-verified): a file name that is not valid UTF-8 inside a hashed scope
        is walked into a surrogate-escaped key (no problem reported), and hashing the canonical baseline then raises
        UnicodeEncodeError. `wf.run(["job", "new", ...])` lets it escape (only WfError is caught): a traceback, not a
        structured refusal, and no job is created."""
        os.makedirs(os.path.join(self.dir, "ledger"))
        work = os.path.join(self.dir, "jobs", "j", "work")
        os.makedirs(work)
        fd = os.open(os.path.join(os.fsencode(work), b"\xff.txt"), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        os.close(fd)
        b = changeset.baseline(self.dir, dict(CONTRACT))
        self.assertEqual((list(b["entries"]), b["problems"]), (["jobs/j/work/\udcff.txt"], []))
        import wf  # noqa: PLC0415 - the CLI entry point, in process
        with self.assertRaises(UnicodeEncodeError):
            wf.run(["--db", self.db, "--role", "coordinator", "--session", "C1", "job", "new", "j",
                    "--contract", self.cpath])
        self.assertFalse(os.path.exists(self.db) and os.path.getsize(self.db) > 0 and _has_job(self.db))

if __name__ == "__main__":
    unittest.main(verbosity=2)
