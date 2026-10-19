"""WF-021 step 3 (MyBuddy), re-based on FIX1 (`fix1/integration` dfd6e85, WF-021 review F1 / Lazy WF-038): links,
pid liveness, file modes and file-name encoding pinned as they are AFTER FIX1.

Rows refer to fixtures/results/wf021-mybuddy/PLATFORM-REVIEW.md; the pre-FIX1 observations stay on branch
`wf021/mybuddy` 88ea43f. Skips are per test and name the capability or rule they need (review L38-M1): symlink rows
skip only when this account cannot create a symlink (probe); the pid-liveness row runs on every platform (it covers the
`_pid_alive` branch of the host it runs on); umask modes need POSIX; byte file names need a filesystem that stores
undecodable bytes (Linux). Fixtures live under the test's own WF_TEST_TMP directory.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import unittest

from test_wf import CONTRACT, Base  # noqa: E402  (sets sys.path; fresh WF_TEST_TMP)
from wfcore import backup, changeset, export, observe, paths, reconcile  # noqa: E402

POSIX = os.name == "posix"
BYTE_NAMES = sys.platform.startswith("linux")
SKIP_POSIX_MODES = "needs POSIX file modes and umask; Windows honours only the read-only bit"
SKIP_BYTE_NAMES = "needs a filesystem that stores undecodable bytes in names (Linux); NTFS names are UTF-16"


def _has_job(db: str) -> bool:
    import sqlite3  # noqa: PLC0415
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute("SELECT count(*) FROM contracts WHERE job='j'").fetchone()[0] > 0
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()


class Links(Base):
    """Runs wherever this account can create symlinks (probe in setUp): Linux, and Windows with Developer Mode or the
    symlink privilege; skipped only when the probe fails (review L38-M1, as WF-030 W30-R1)."""

    def setUp(self) -> None:
        super().setUp()
        target = os.path.join(self.dir, "probe-target")
        os.makedirs(target)
        probe = os.path.join(self.dir, "symlink-probe")
        try:
            os.symlink(target, probe, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"cannot create symlinks here: {type(exc).__name__}: {exc}")
        (os.rmdir if os.name == "nt" else os.unlink)(probe)   # the link only, never the target

    def test_row35_reconcile_treats_a_symlinked_component_as_crossing_a_link(self):
        """Row #35 (unchanged by FIX1): the symlink twin of the Windows-only junction test: a symlink component under
        the root is a link crossing (state unknown, never followed); a real directory is not."""
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
        """Row #36 (unchanged by FIX1): the symlink twin of the Windows-only junctioned-job test."""
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


class PidLiveness(Base):
    def test_row70_foreign_dead_pid_staging_is_an_orphan_and_live_pid_is_not(self):
        """Row #70 (unchanged by FIX1; runs on every platform, review L38-M1): export._pid_alive with a foreign pid,
        through the branch of the host it runs on: `os.kill(pid, 0)` on POSIX, OpenProcess/GetExitCodeProcess on
        Windows. A staging file named after an exited child is reported as preserved; one named after a running child
        is not."""
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


class ModesAndNames(Base):
    @unittest.skipUnless(POSIX, SKIP_POSIX_MODES)
    def test_row74_backup_file_is_owner_read_write_only(self):
        """Row #74 after FIX1 P5 (WF-021 F9 fixed): the backup is created with mode 0o600, so under umask 022 it has
        no execute bits and is not readable by others (before FIX1: 0o777 & ~umask = 0o755)."""
        self.new_job()
        dest = os.path.join(self.dir, "backup.db")
        old = os.umask(0o022)
        try:
            backup.backup(self.db, dest, writer_stopped=True)
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(os.stat(dest).st_mode), 0o600)

    @unittest.skipUnless(BYTE_NAMES, SKIP_BYTE_NAMES)
    def test_row43_non_utf8_file_name_is_a_structured_refusal_at_job_new(self):
        """Row #43 after FIX1 P4 + review R2/R3 (WF-021 F6 fixed): a file name that is not valid UTF-8 inside a hashed
        scope is a baseline problem naming the escaped bytes (`\\xff`), never an entry; `wf job new` refuses with a
        structured answer (exit 1, `refused`) and no job is created. Before FIX1 the surrogate key reached the
        canonical hash and UnicodeEncodeError escaped `wf.run`."""
        os.makedirs(os.path.join(self.dir, "ledger"))
        work = os.path.join(self.dir, "jobs", "j", "work")
        os.makedirs(work)
        fd = os.open(os.path.join(os.fsencode(work), b"\xff.txt"), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        os.close(fd)
        b = changeset.baseline(self.dir, dict(CONTRACT))
        self.assertEqual(b["entries"], {})
        self.assertEqual(b["problems"], ["file name is not valid Unicode (undecodable bytes or a lone surrogate) "
                                         "inside the boundary: jobs/j/work/\\xff.txt"])
        import wf  # noqa: PLC0415 - the CLI entry point, in process
        code, res = wf.run(["--db", self.db, "--role", "coordinator", "--session", "C1", "job", "new", "j",
                            "--contract", self.cpath])
        self.assertEqual((code, res.get("code")), (1, "refused"), res)
        self.assertIn("jobs/j/work/\\xff.txt", res["error"])
        self.assertFalse(os.path.exists(self.db) and os.path.getsize(self.db) > 0 and _has_job(self.db))

if __name__ == "__main__":
    unittest.main(verbosity=2)
