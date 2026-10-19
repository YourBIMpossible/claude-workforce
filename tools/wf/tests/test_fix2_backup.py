"""PACKAGE FIX2 batch C, lane F2CP (WF-101; AMENDMENT FIX2-A3): the `wf backup` destination and source.

  #3       a destination named like a device (CON, nul.txt) or ending in a dot or a space is refused, on every
           platform, by commands.backup_cmd before any filesystem access (AMENDMENT FIX2-A5: backup.py may not
           import validate).
  #22+#31  the real ledger directory, dirname(realpath(db)), is protected too (W30-F1: a symlinked --db file).
  #23      the destination is compared and opened as realpath(dest), with no lexical abspath first (W30-F2), and the
           verification never leaks a raw sqlite3 error (no exit 4).
  #26+#32  Windows: a local drive's admin share (\\\\localhost\\C$, \\\\127.0.0.1\\C$, \\\\?\\UNC\\localhost\\C$) and a
           \\\\?\\C: path are compared as that drive's path; any other UNC or device path is refused (fail closed).
  #28      a busy check of the copy is lock-timeout, exit 1 (W29-F1), not internal-error, exit 4.
  #50      a --db that cannot be opened read-only (a UNC --db) is refused with its reason, for `backup <job>` and --all.
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

from test_wf import Base  # noqa: E402
import wf  # noqa: E402
from wfcore import backup  # noqa: E402

WINDOWS = os.name == "nt"
POSIX = os.name == "posix"


class _Backup(Base):
    """A ledger with job j (contract, grant proposed and activated) and an empty `out` directory beside it."""

    def setUp(self) -> None:
        super().setUp()
        self.new_job()
        self.activate()
        self.ledger = os.path.dirname(self.db)
        self.out = os.path.join(self.dir, "out")
        os.makedirs(self.out)
        self.ledger_before = sorted(os.listdir(self.ledger))

    def cli(self, *argv: str, db_path: str | None = None) -> tuple[int, dict]:
        """`wf ... --json` through wf.main, where an unexpected exception becomes exit 4 (FIX1 E1)."""
        args = ["wf.py", "--db", db_path or self.db, "--role", "coordinator", "--session", "C1", "--json", *argv]
        out = io.StringIO()
        with mock.patch.object(sys, "argv", args), redirect_stdout(out):
            code = wf.main()
        return code, json.loads(out.getvalue())

    def backup_to(self, dest: str, *extra: str, db_path: str | None = None) -> tuple[int, dict]:
        return self.cli("backup", "j", "--to", dest, "--writer-stopped", *extra, db_path=db_path)

    def assert_refused(self, dest: str, reason: str, *, db_path: str | None = None) -> None:
        code, res = self.backup_to(dest, db_path=db_path)
        self.assertEqual((code, res.get("code"), res.get("error")), (1, "refused", reason), res)
        self.assertEqual(os.listdir(self.out), [], "a refusal writes nothing")
        self.assertEqual(sorted(os.listdir(self.ledger)), self.ledger_before, "nothing lands in the ledger directory")

    def can_symlink(self) -> bool:
        probe = os.path.join(self.dir, "symlink-probe")
        try:
            os.symlink(self.ledger, probe, target_is_directory=True)
        except (OSError, NotImplementedError):
            return False
        (os.rmdir if WINDOWS else os.unlink)(probe)
        return True

    @staticmethod
    def unc(path: str, host: str = "localhost", prefix: str = "\\\\") -> str:
        drive, rest = os.path.splitdrive(os.path.abspath(path))
        return f"{prefix}{host}\\{drive[0]}$" + rest


class C3Names(_Backup):
    def test_device_name_destination_is_refused(self):
        for name in ("CON", "nul.txt", "Aux.db", "LPT1"):
            with self.subTest(name=name):
                self.assert_refused(os.path.join(self.out, name), "dest-device-name")

    def test_trailing_dot_or_space_is_refused(self):
        for name in ("name.", "name ", "b.db."):
            with self.subTest(name=name):
                self.assert_refused(os.path.join(self.out, name), "dest-trailing-dot-or-space")


class C22RealLedgerDir(_Backup):
    def test_db_file_symlink_protects_the_real_ledger(self):
        """W30-F1: `--db <root>/elsewhere/l-alias.db` is a symlink to the ledger file; the real ledger directory
        is protected as well as dirname(abspath(db))."""
        if not self.can_symlink():
            self.skipTest("cannot create symlinks here")
        elsewhere = os.path.join(self.dir, "elsewhere")
        os.makedirs(elsewhere)
        link = os.path.join(elsewhere, "l-alias.db")
        os.symlink(self.db, link)
        self.assert_refused(os.path.join(self.ledger, "b.db"), "dest-inside-ledger", db_path=link)


class C23RealDest(_Backup):
    @unittest.skipUnless(POSIX, "the kernel resolves `lnk/..` physically on POSIX; Win32 collapses it lexically")
    def test_dotdot_after_a_symlink_is_refused(self):
        """W30-F2: `<root>/lnk -> <root>/ledger/sub`; `--to <root>/lnk/../b.db` is `<root>/ledger/b.db`."""
        sub = os.path.join(self.ledger, "sub")
        os.makedirs(sub)
        lnk = os.path.join(self.dir, "lnk")
        os.symlink(sub, lnk)
        self.ledger_before = sorted(os.listdir(self.ledger))
        self.assert_refused(lnk + "/../b.db", "dest-inside-ledger")

    def test_copy_reopen_failure_is_not_exit_4(self):
        """The verification reopens the copy read-only; an engine error there is a refusal with its reason."""
        real = sqlite3.connect
        dest = os.path.join(self.out, "b.db")

        def connect(target, *args, **kwargs):
            if kwargs.get("uri") and "/out/b.db" in str(target):
                raise sqlite3.OperationalError("unable to open database file")
            return real(target, *args, **kwargs)

        with mock.patch.object(backup.sqlite3, "connect", side_effect=connect):
            code, res = self.backup_to(dest)
        self.assertEqual(code, 1, res)
        self.assertNotEqual(res.get("code"), "internal-error", res)
        self.assertIn("OperationalError: unable to open database file", res.get("error", ""))


@unittest.skipUnless(WINDOWS, "UNC, admin-share and \\\\?\\ paths are Windows forms")
class C26WindowsAliases(_Backup):
    def test_admin_share_aliases_of_the_ledger_are_refused(self):
        dest = os.path.join(self.ledger, "b.db")
        for alias in (self.unc(dest), self.unc(dest, "127.0.0.1"), self.unc(dest, "localhost", "\\\\?\\UNC\\")):
            with self.subTest(alias=alias):
                self.assert_refused(alias, "dest-inside-ledger")

    def test_admin_share_alias_outside_is_written(self):
        code, res = self.backup_to(self.unc(os.path.join(self.out, "b.db")))
        self.assertEqual((code, res.get("clean")), (0, True), res)
        self.assertTrue(os.path.isfile(os.path.join(self.out, "b.db")))

    def test_other_unc_destination_is_refused(self):
        self.assert_refused("\\\\localhost\\wf-no-such-share\\b.db", "dest-path-unresolvable")

    def test_long_path_prefix_is_a_drive_path(self):
        self.assert_refused("\\\\?\\" + os.path.join(self.ledger, "b.db"), "dest-inside-ledger")
        code, res = self.backup_to("\\\\?\\" + os.path.join(self.out, "b.db"))
        self.assertEqual((code, res.get("clean")), (0, True), res)
        self.assertTrue(os.path.isfile(os.path.join(self.out, "b.db")))


class C28BusyCheck(_Backup):
    def test_busy_copy_check_is_lock_timeout(self):
        """W29-F1's shape: the engine reports `database is locked` while the copy is checked."""
        with mock.patch.object(backup.db, "integrity", side_effect=sqlite3.OperationalError("database is locked")):
            code, res = self.backup_to(os.path.join(self.out, "b.db"))
        self.assertEqual((code, res.get("code")), (1, "lock-timeout"), res)


@unittest.skipUnless(WINDOWS, "a UNC --db exists on Windows only")
class C50UncDb(_Backup):
    def test_unc_db_is_refused_for_job_and_all(self):
        unc_db = self.unc(self.db)
        if not os.path.isfile(unc_db):
            self.skipTest(f"admin share not reachable: {unc_db}")
        for scope in (["j"], ["--all"]):
            with self.subTest(scope=scope):
                code, res = self.cli("backup", *scope, "--to", os.path.join(self.out, "b.db"), db_path=unc_db)
                self.assertEqual((code, res.get("code")), (1, "refused"), res)
                self.assertIn("read-only", res.get("error", ""))
                self.assertEqual(os.listdir(self.out), [])


if __name__ == "__main__":
    unittest.main()
