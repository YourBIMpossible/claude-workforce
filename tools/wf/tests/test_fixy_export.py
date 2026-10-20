"""PACKAGE FIX2 batch Y, lane F2YL (WF-146; AMENDMENT FIX2-A22): export retries that can fire, and an atomic manifest.

  Y1  check's reads (manifest.txt, the entries' sha and size, events.jsonl) retry a real Windows sharing or lock
      violation. CPython's open() and read() report one as PermissionError errno 13 with `winerror` None (WF-127 F1),
      so a read-only probe of the same path (CreateFileW, GENERIC_READ, full sharing, read to the end) classifies it:
      only 32 (ERROR_SHARING_VIOLATION) and 33 (ERROR_LOCK_VIOLATION) are retried, within Z3's bound (5 attempts in
      all, 0.1 s apart). Any other error, and every error off Windows, behaves as before.
  Y2  one retry budget per refresh: the sleeps of all its files total at most 0.4 s; each file keeps the 5-attempt
      cap; after the budget, a file's next violation fails as before (the refresh note's `error`).
  Y3  manifest.txt is written like the other export files: staged `manifest.txt.<pid>-<hex8>.tmp` (exclusive create,
      fsync), then `os.replace` with Z3's replace retry, inside Y2's budget. A failed replace leaves the old manifest.

Real handles on Windows (CreateFileW share modes, msvcrt byte-range locks, an icacls deny); simulated failures
elsewhere. The sleep goes through a stand-in for export's `time`, so it is counted and no test waits. Each test failed
on the base 1439f94 (red-before in from-lazy RESULTS.md) unless its docstring says it pins unchanged behaviour."""
from __future__ import annotations

import csv
import ctypes
import errno
import json
import os
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from test_wf import Base
from wfcore import export, manifest, paths

WINDOWS = sys.platform == "win32"
GENERIC_READ, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL = 0x80000000, 3, 0x80
SHARE_READ_WRITE = 0x1 | 0x2


def sharing(winerror: int | None, err: int = 13) -> PermissionError:
    exc = PermissionError(err, "simulated: the file is in use by another process")
    exc.winerror = winerror
    return exc


def hold(path: str, share: int):
    """Open `path` with CreateFileW and the given share mode; returns an idempotent release()."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k32.CreateFileW(path, GENERIC_READ, share, None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
    if h in (None, ctypes.c_void_p(-1).value):
        raise OSError(ctypes.get_last_error(), f"CreateFileW failed for {path}")
    state = {"open": True}

    def release() -> None:
        if state["open"]:
            state["open"] = False
            k32.CloseHandle(h)
    return release


def lock_bytes(path: str):
    """An exclusive byte-range lock on the first bytes of `path`, from a handle of its own; returns release()."""
    import msvcrt
    fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
    n = max(1, min(64, os.path.getsize(path)))
    msvcrt.locking(fd, msvcrt.LK_NBLCK, n)
    state = {"open": True}

    def release() -> None:
        if state["open"]:
            state["open"] = False
            os.lseek(fd, 0, 0)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, n)
            os.close(fd)
    return release


def system32(exe: str) -> str:
    """The Windows tool `exe` by its System32 path, so no same-named program elsewhere is run instead."""
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", exe)


def account_sid() -> str | None:
    """This account's SID from `whoami /user /fo csv /nh` (one row: "<domain>\\<user>","S-1-…"), or None."""
    try:
        run = subprocess.run([system32("whoami.exe"), "/user", "/fo", "csv", "/nh"], capture_output=True, text=True)
    except OSError:
        return None
    rows = list(csv.reader(run.stdout.splitlines())) if run.returncode == 0 else []
    sid = rows[0][-1].strip() if rows and rows[0] else ""
    return sid if sid.startswith("S-1-") else None


class _Export(Base):
    """A job with an export (every commit refreshes it); `patched` sets export's platform test and counts its sleeps.
    `create=True` lets the unchanged-behaviour pins run on the base."""

    def setUp(self) -> None:
        super().setUp()
        self.new_job()
        self.activate()
        self.out = paths.export_dir(self.db, "j")
        self.sleeps: list[float] = []
        self.assertEqual(export.check(self.db, "j"), [])

    def patched(self, windows: bool = True, sleep=None):
        return mock.patch.multiple(export, create=True, _WINDOWS=windows,
                                   time=SimpleNamespace(sleep=sleep or self.sleeps.append))

    def sleep_then(self, release):
        def sleep(s: float) -> None:
            self.sleeps.append(s)
            release()
        return sleep

    def staging_left(self) -> list[str]:
        return sorted(n for n in os.listdir(self.out) if n.endswith(".tmp"))


# ------------------------------------------------------------------------------------------------ Y1: classification

class Y1Classification(_Export):
    """Simulated on any OS: an errno-13 PermissionError with `winerror` None is classified by the probe."""

    def check_with(self, probe_code: int, errors: list[BaseException], windows: bool = True):
        calls, probes = [], []
        real, pending = export.sha256_file, list(errors)

        def failing(p):
            if os.path.basename(p) == "head.json":
                calls.append(p)
                if pending:
                    raise pending.pop(0)
            return real(p)

        def probe(p):
            probes.append(os.path.basename(p))
            return probe_code
        with self.patched(windows), mock.patch.object(export, "sha256_file", failing), \
                mock.patch.object(export, "_probe_sharing", probe, create=True):
            problems = export.check(self.db, "j")
        return problems, calls, probes

    def test_probe_32_is_retried(self):
        problems, calls, probes = self.check_with(32, [sharing(None)])
        self.assertEqual((problems, len(calls), self.sleeps, probes), ([], 2, [0.1], ["head.json"]))

    def test_probe_33_is_retried(self):
        problems, calls, probes = self.check_with(33, [sharing(None), sharing(None)])
        self.assertEqual((problems, len(calls), self.sleeps), ([], 3, [0.1, 0.1]))

    def test_probe_5_is_not_retried(self):
        """Pins unchanged behaviour: access denied is today's `export file unreadable` at once."""
        err = sharing(None)
        problems, calls, _ = self.check_with(5, [err])
        self.assertEqual((len(calls), self.sleeps), (1, []))
        self.assertEqual(problems, [f"export file unreadable: head.json (PermissionError: {err})"])

    def test_probe_0_is_not_retried(self):
        """Pins unchanged behaviour: the probe reads the file now (the holder let go), so the first error stands."""
        problems, calls, _ = self.check_with(0, [sharing(None)])
        self.assertEqual((len(calls), self.sleeps, len(problems)), (1, [], 1))

    def test_no_probe_off_windows(self):
        """Pins unchanged behaviour: off Windows the probe is never called and nothing is retried."""
        problems, calls, probes = self.check_with(32, [sharing(None)], windows=False)
        self.assertEqual((len(calls), self.sleeps, probes, len(problems)), (1, [], [], 1))

    def test_no_probe_for_another_errno(self):
        """Pins unchanged behaviour: only errno 13 (EACCES) without a `winerror` is probed."""
        problems, calls, probes = self.check_with(32, [sharing(None, err=errno.EPERM)])
        self.assertEqual((len(calls), self.sleeps, probes, len(problems)), (1, [], [], 1))


# ------------------------------------------------------------------------------------------------ Y1: real handles

@unittest.skipUnless(WINDOWS, "share modes, byte-range locks and ACLs are Windows'")
class Y1RealHandles(_Export):
    def events(self) -> str:
        return os.path.join(self.out, "events.jsonl")

    def test_share_mode_0_released_in_the_window_is_clean(self):
        release = hold(self.events(), 0)
        try:
            with self.patched(sleep=self.sleep_then(release)):
                problems = export.check(self.db, "j")
        finally:
            release()
        self.assertEqual((problems, self.sleeps), ([], [0.1]))

    def test_share_mode_0_throughout_is_todays_outcome_after_five(self):
        release = hold(self.events(), 0)
        try:
            with self.patched():
                problems = export.check(self.db, "j")
        finally:
            release()
        self.assertEqual(self.sleeps, [0.1] * 4)
        self.assertEqual(len(problems), 1, problems)
        self.assertTrue(problems[0].startswith("export file unreadable: events.jsonl (PermissionError: [Errno 13]"), problems)

    def test_byte_range_lock_released_in_the_window_is_clean(self):
        """Measured (WF-127 measure127): a read of a locked range is errno 13 with no `winerror`; the probe's read
        gets 33, so it is retried."""
        release = lock_bytes(self.events())
        try:
            with self.patched(sleep=self.sleep_then(release)):
                problems = export.check(self.db, "j")
        finally:
            release()
        self.assertEqual((problems, self.sleeps), ([], [0.1]))

    def test_byte_range_lock_throughout_is_todays_outcome_after_five(self):
        release = lock_bytes(self.events())
        try:
            with self.patched():
                problems = export.check(self.db, "j")
        finally:
            release()
        self.assertEqual(self.sleeps, [0.1] * 4)
        self.assertEqual(len(problems), 1, problems)
        self.assertTrue(problems[0].startswith("export file unreadable: events.jsonl (PermissionError: [Errno 13]"), problems)

    def test_access_denied_is_not_retried(self):
        """Pins unchanged behaviour: an ACL that denies this account reading head.json is errno 13 with no `winerror`
        too; the probe gets 5, so it is today's `export file unreadable` at once. The deny names the account's SID:
        `%USERNAME%` resolves to the computer on a host whose account and computer names are equal, and the read then
        succeeds (Plexy WF-147 F1). If the file is still readable after the deny, the test skips with the reason."""
        target = os.path.join(self.out, "head.json")
        sid = account_sid()
        if sid is None:
            self.skipTest("whoami /user gave no SID for this account")
        principal = f"*{sid}"
        icacls = system32("icacls.exe")
        deny = subprocess.run([icacls, target, "/deny", f"{principal}:(R)"], capture_output=True, text=True)
        if deny.returncode != 0:
            self.skipTest(f"icacls could not deny read: {deny.stdout.strip()} {deny.stderr.strip()}")
        try:
            try:
                with open(target, "rb") as fh:
                    fh.read(1)
            except PermissionError:
                pass
            else:
                self.skipTest(f"head.json is still readable after icacls /deny {principal}:(R)")
            with self.patched():
                problems = export.check(self.db, "j")
        finally:
            subprocess.run([icacls, target, "/remove:d", principal], capture_output=True, text=True)
        self.assertEqual(self.sleeps, [])
        self.assertEqual(len(problems), 1, problems)
        self.assertTrue(problems[0].startswith("export file unreadable: head.json (PermissionError: [Errno 13]"), problems)

    def test_the_probe_reads_only(self):
        """The probe classifies without changing anything: 0 for a readable file, 32 under share mode 0, 33 under a
        byte-range lock, 5 for a directory, 2 for a missing file (which it does not create)."""
        ev = self.events()
        before = (os.path.getsize(ev), os.stat(ev).st_mtime_ns)
        self.assertEqual(export._probe_sharing(ev), 0)
        release = hold(ev, 0)
        try:
            self.assertEqual(export._probe_sharing(ev), 32)
        finally:
            release()
        release = lock_bytes(ev)
        try:
            self.assertEqual(export._probe_sharing(ev), 33)
        finally:
            release()
        self.assertEqual(export._probe_sharing(self.out), 5)
        missing = os.path.join(self.out, "no-such-file.json")
        self.assertEqual(export._probe_sharing(missing), 2)
        self.assertFalse(os.path.lexists(missing))
        self.assertEqual((os.path.getsize(ev), os.stat(ev).st_mtime_ns), before)


# ------------------------------------------------------------------------------------------------ Y2: the budget

class Y2Budget(_Export):
    def refresh_with(self, fails: dict[str, int]):
        """export.refresh with `os.replace` onto each named file failing `fails[name]` times (winerror 32)."""
        real, left, calls = os.replace, dict(fails), []

        def flaky(src, dst):
            name = os.path.basename(dst)
            calls.append(name)
            if left.get(name, 0) > 0:
                left[name] -= 1
                raise sharing(32)
            return real(src, dst)
        with self.patched(), mock.patch.object(os, "replace", flaky):
            note = export.refresh(self.db, "j")
        return note, calls

    def test_one_budget_for_the_whole_refresh(self):
        """Every export file fails 4 times: events.jsonl spends the 0.4 s budget and succeeds on its 5th attempt;
        head.json's first violation then fails as before (the note's `error`), with 0.4 s slept in all."""
        note, calls = self.refresh_with({"events.jsonl": 4, "head.json": 4, "contract.json": 4, "manifest.txt": 4})
        self.assertEqual(self.sleeps, [0.1] * 4)
        self.assertEqual(calls, ["events.jsonl"] * 5 + ["head.json"])
        self.assertEqual(note.get("error"), f"PermissionError: {sharing(32)}", note)

    def test_each_file_keeps_its_five_attempt_cap(self):
        """Pins unchanged behaviour: one file failing throughout gets 5 attempts and 4 sleeps, then today's note."""
        note, calls = self.refresh_with({"events.jsonl": 9})
        self.assertEqual((calls, self.sleeps), (["events.jsonl"] * 5, [0.1] * 4))
        self.assertIn("error", note)

    def test_the_budget_is_per_refresh(self):
        """Pins unchanged behaviour: a second refresh starts with its own 0.4 s."""
        for _ in range(2):
            note, _calls = self.refresh_with({"events.jsonl": 4})
            self.assertNotIn("error", note, note)
        self.assertEqual(self.sleeps, [0.1] * 8)

    def test_spread_violations_share_the_budget(self):
        """Two violations on each of the four files (8 in all): the first four are retried, the fifth fails."""
        note, calls = self.refresh_with({"events.jsonl": 2, "head.json": 2, "contract.json": 2, "manifest.txt": 2})
        self.assertEqual(self.sleeps, [0.1] * 4)
        self.assertEqual(calls, ["events.jsonl"] * 3 + ["head.json"] * 3 + ["contract.json"])
        self.assertIn("error", note)


# ------------------------------------------------------------------------------------------------ Y3: the manifest

class Y3Manifest(_Export):
    def test_manifest_is_staged_and_replaced(self):
        real, seen = os.replace, []

        def recording(src, dst):
            seen.append((src, dst))
            return real(src, dst)
        with self.patched(), mock.patch.object(os, "replace", recording):
            note = export.refresh(self.db, "j")
        self.assertNotIn("error", note, note)
        mpath = os.path.join(self.out, "manifest.txt")
        staged = [s for s, d in seen if os.path.normcase(d) == os.path.normcase(mpath)]
        self.assertEqual(len(staged), 1, seen)
        self.assertEqual(os.path.dirname(staged[0]), os.path.dirname(mpath))
        self.assertRegex(os.path.basename(staged[0]), r"\Amanifest\.txt\.\d+-[0-9a-f]{8}\.tmp\Z")
        self.assertEqual((self.staging_left(), export.check(self.db, "j")), ([], []))

    def test_failed_replace_keeps_the_old_manifest(self):
        """An append changes events.jsonl; the refresh's replace onto manifest.txt fails (not a sharing violation):
        manifest.txt keeps its old bytes whole, this writer's staging file is discarded, and the error is the note."""
        mpath = os.path.join(self.out, "manifest.txt")
        with open(mpath, "rb") as fh:
            old = fh.read()
        real = os.replace

        def failing(src, dst):
            if os.path.normcase(dst) == os.path.normcase(mpath):
                raise OSError(errno.EIO, "simulated: the disk failed during the replace")
            return real(src, dst)
        with self.patched(), mock.patch.object(os, "replace", failing):
            code, res = self.intent()
        self.assertEqual(code, 0, res)
        self.assertIn("OSError", json.dumps(res.get("export")), res)
        with open(mpath, "rb") as fh:
            self.assertEqual(fh.read(), old)
        manifest.read(mpath)                                       # whole: it parses
        self.assertEqual(self.staging_left(), [])

    @unittest.skipUnless(WINDOWS, "share modes are Windows'")
    def test_reader_without_delete_sharing_is_retried(self):
        """A reader holds manifest.txt with read/write sharing and no delete sharing: the replace fails with
        `winerror` 5 and is retried; released during the first sleep, the refresh succeeds and verify is clean."""
        release = hold(os.path.join(self.out, "manifest.txt"), SHARE_READ_WRITE)
        try:
            with self.patched(sleep=self.sleep_then(release)):
                note = export.refresh(self.db, "j")
        finally:
            release()
        self.assertNotIn("error", note, note)
        self.assertEqual(self.sleeps, [0.1])
        self.assertEqual((self.staging_left(), export.check(self.db, "j")), ([], []))

    def test_manifest_staging_names_follow_the_rules(self):
        """`manifest.txt.<pid>-<hex8>.tmp` and the legacy `manifest.txt.tmp` are staging names (orphans' rules, not
        strays'); a trailing newline is not (#61)."""
        for name in ("manifest.txt.123-0123abcd.tmp", "manifest.txt.tmp"):
            with self.subTest(name=name):
                self.assertIsNotNone(export._STAGING.match(name))
        self.assertIsNone(export._STAGING.match("manifest.txt.tmp\n"))
        dead = os.path.join(self.out, "manifest.txt.4294967290-0123abcd.tmp")
        with open(dead, "w", encoding="utf-8") as fh:
            fh.write("left by a writer that died\n")
        self.assertIn(export.PRESERVED + os.path.basename(dead), export.orphans(self.out))
        self.assertFalse(any("manifest.txt.4294967290" in p for p in export.strays(self.out, set(export.FILES))))

    def test_a_pid_no_process_can_have_is_an_orphan(self):
        """WF-146c (MyBuddy WF-149(a)): a staging name may carry any digits. A pid no process can have (0; 2**31, past
        a POSIX pid_t; 2**32, past a Windows DWORD; 2**64) is not alive, so its file is an orphan and `check` reports
        it. Before, `_pid_alive` raised for it, and `check` with it: `OverflowError` from `os.kill` on Linux from
        2**31, `ctypes.ArgumentError` on Windows from 2**32 (and pid 0 was alive on POSIX: `kill(0, 0)` probes the
        process group)."""
        for pid in (0, 2**31, 2**32, 2**64):
            with self.subTest(pid=pid):
                self.assertFalse(export._pid_alive(pid))
        name = f"manifest.txt.{2**64}-0123abcd.tmp"
        with open(os.path.join(self.out, name), "w", encoding="utf-8") as fh:
            fh.write("left by a writer that died\n")
        self.assertIn(export.PRESERVED + name, export.orphans(self.out))
        self.assertIn(export.PRESERVED + name, export.check(self.db, "j"))

    def test_verify_clean_after_a_refresh(self):
        """Pins unchanged behaviour."""
        self.assertNotIn("error", export.refresh(self.db, "j"))
        self.assertEqual(export.check(self.db, "j"), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
