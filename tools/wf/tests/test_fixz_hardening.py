"""PACKAGE FIX2 batch Z, lane F2ZL (WF-127; AMENDMENT FIX2-A17): hardening.

  Z3  `export._write_atomic`'s `os.replace`, and `export.check`'s reads of manifest.txt, the manifest entries and
      events.jsonl, retry a Windows sharing violation only: a `PermissionError` whose `winerror` is 32
      (ERROR_SHARING_VIOLATION) or 33 (ERROR_LOCK_VIOLATION), and for the replace also 5 (ERROR_ACCESS_DENIED).
      SHARING_ATTEMPTS = 5 attempts in all, SHARING_SLEEP_S = 0.1 s between them. Any other error, and anything off
      Windows, is not retried; after the last attempt the outcome is today's (the refresh note's `error`;
      `export file unreadable: ...` from check).
  Z7  test only: a JSON input file over MAX_JSON_INPUT_BYTES whose size `os.path.getsize` under-reports is still
      refused by the post-read measure in `commands._load_json`, and nothing is written.

Violations are simulated by patching the call, with `export._WINDOWS` set (any platform), and on Windows also produced
for real with a handle from CreateFileW that lacks the sharing the call needs. Measured on Windows (WF-127 F1): a real
replace onto a held file reports `winerror` 5 and is retried; a real read of a held or locked file raises errno 13 with
no `winerror`, so the read retry does not fire for it (pinned). The sleep goes through a stand-in for export's `time`,
so it is counted and no test waits. Each Z3 test failed on the base 0b6031e (red-before in
from-lazy RESULTS.md) unless its docstring says it pins unchanged behaviour; Z7 pins unchanged behaviour."""
from __future__ import annotations

import builtins
import ctypes
import json
import os
import sqlite3
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from test_wf import Base
from wfcore import commands, export, manifest, paths

WINDOWS = sys.platform == "win32"


def sharing(winerror: int | None) -> PermissionError:
    """A PermissionError as Windows raises it for `winerror` (None: a PermissionError with no winerror)."""
    exc = PermissionError(13, "simulated: the file is in use by another process")
    exc.winerror = winerror
    return exc


class _Export(Base):
    """A job with an export (every commit refreshes it); `windows` and `sleeps` patch export's platform test and
    sleep. `create=True` lets the unchanged-behaviour pins run on the base, which has neither name."""

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

    def failing(self, real, name: str, errors: list[BaseException], calls: list[str]):
        """Wrap `real` so that its calls on a path whose basename is `name` raise `errors` in order, then pass."""
        pending = list(errors)
        to_dst = real is os.replace  # decided now: once patched, `os.replace` is `call` itself

        def call(*args, **kwargs):
            target = args[1] if to_dst else args[0]
            if os.path.basename(target) == name:
                calls.append(name)
                if pending:
                    raise pending.pop(0)
            return real(*args, **kwargs)
        return call

    def staging_left(self) -> list[str]:
        return sorted(n for n in os.listdir(self.out) if n.endswith(".tmp"))


# ------------------------------------------------------------------------------------------------ Z3: the replace

class Z3Replace(_Export):
    def refresh_with(self, errors: list[BaseException], windows: bool = True):
        calls: list[str] = []
        with self.patched(windows), mock.patch.object(os, "replace",
                                                      self.failing(os.replace, "head.json", errors, calls)):
            note = export.refresh(self.db, "j")
        return note, calls

    def test_one_violation_then_success(self):
        note, calls = self.refresh_with([sharing(32)])
        self.assertNotIn("error", note, note)
        self.assertEqual((len(calls), self.sleeps), (2, [0.1]))
        self.assertEqual(export.check(self.db, "j"), [])
        self.assertEqual(self.staging_left(), [])

    def test_access_denied_is_retried_for_the_replace(self):
        """Winerror 5 is how Windows reports a replace onto a file held open without delete sharing."""
        note, calls = self.refresh_with([sharing(5), sharing(5)])
        self.assertNotIn("error", note, note)
        self.assertEqual((len(calls), self.sleeps), (3, [0.1, 0.1]))

    def test_persistent_violation_gives_todays_outcome_after_five_attempts(self):
        errors = [sharing(33) for _ in range(6)]
        note, calls = self.refresh_with(errors)
        self.assertEqual((len(calls), self.sleeps), (5, [0.1] * 4))
        self.assertEqual(note["error"], f"PermissionError: {errors[4]}")     # the last attempt's error, as today
        self.assertEqual(set(note), {"dir", "error"}, note)
        self.assertEqual(self.staging_left(), [])                           # this writer's staging file discarded

    def test_other_permission_error_is_not_retried(self):
        """Pins unchanged behaviour: one attempt, today's note."""
        err = sharing(None)
        note, calls = self.refresh_with([err])
        self.assertEqual((len(calls), self.sleeps, note.get("error")), (1, [], f"PermissionError: {err}"))

    def test_not_retried_off_windows(self):
        """Pins unchanged behaviour: off Windows (`_WINDOWS` false) a winerror-32 error gets one attempt."""
        err = sharing(32)
        note, calls = self.refresh_with([err], windows=False)
        self.assertEqual((len(calls), self.sleeps, note.get("error")), (1, [], f"PermissionError: {err}"))

    def test_other_os_error_is_not_retried(self):
        """Pins unchanged behaviour: a FileNotFoundError is not a sharing violation."""
        err = FileNotFoundError(2, "simulated: gone")
        note, calls = self.refresh_with([err])
        self.assertEqual((len(calls), self.sleeps, note.get("error")), (1, [], f"FileNotFoundError: {err}"))


# ------------------------------------------------------------------------------------------------ Z3: check's reads

class Z3CheckReads(_Export):
    def check_with(self, target, name: str, errors: list[BaseException], windows: bool = True):
        calls: list[str] = []
        owner, attr = target
        real = builtins.open if attr == "open" else getattr(owner, attr)   # export's `open` is the builtin
        with self.patched(windows), mock.patch.object(owner, attr, self.failing(real, name, errors, calls),
                                                      create=attr == "open"):
            problems = export.check(self.db, "j")
        return problems, calls

    def test_manifest_read_violation_then_success(self):
        problems, calls = self.check_with((manifest, "read"), "manifest.txt", [sharing(33)])
        self.assertEqual((problems, len(calls), self.sleeps), ([], 2, [0.1]))

    def test_manifest_read_persistent_violation(self):
        errors = [sharing(32) for _ in range(6)]
        problems, calls = self.check_with((manifest, "read"), "manifest.txt", errors)
        self.assertEqual((len(calls), self.sleeps), (5, [0.1] * 4))
        self.assertEqual(problems, [f"export file unreadable: manifest.txt (PermissionError: {errors[4]})"])

    def test_entry_read_violation_then_success(self):
        problems, calls = self.check_with((export, "sha256_file"), "head.json", [sharing(32)])
        self.assertEqual((problems, len(calls), self.sleeps), ([], 2, [0.1]))

    def test_entry_read_persistent_violation(self):
        errors = [sharing(33) for _ in range(6)]
        problems, calls = self.check_with((export, "sha256_file"), "head.json", errors)
        self.assertEqual((len(calls), self.sleeps), (5, [0.1] * 4))
        self.assertEqual(problems, [f"export file unreadable: head.json (PermissionError: {errors[4]})"])

    def test_events_read_violation_then_success(self):
        """The final events.jsonl read (after its manifest hash), through export's own `open`."""
        problems, calls = self.check_with((export, "open"), "events.jsonl", [sharing(32)])
        self.assertEqual((problems, len(calls), self.sleeps), ([], 2, [0.1]))

    def test_access_denied_is_not_retried_for_a_read(self):
        """Pins unchanged behaviour: winerror 5 is retried for the replace only."""
        err = sharing(5)
        problems, calls = self.check_with((export, "sha256_file"), "head.json", [err])
        self.assertEqual((len(calls), self.sleeps), (1, []))
        self.assertEqual(problems, [f"export file unreadable: head.json (PermissionError: {err})"])

    def test_read_not_retried_off_windows(self):
        """Pins unchanged behaviour."""
        err = sharing(32)
        problems, calls = self.check_with((manifest, "read"), "manifest.txt", [err], windows=False)
        self.assertEqual((len(calls), self.sleeps), (1, []))
        self.assertEqual(problems, [f"export file unreadable: manifest.txt (PermissionError: {err})"])

    def test_constants(self):
        self.assertEqual((export.SHARING_ATTEMPTS, export.SHARING_SLEEP_S), (5, 0.1))
        self.assertEqual((export.SHARING_WINERRORS, export.REPLACE_WINERRORS), ({32, 33}, {5, 32, 33}))
        self.assertLessEqual((export.SHARING_ATTEMPTS - 1) * export.SHARING_SLEEP_S, 0.4 + 1e-9)


# ------------------------------------------------------------------------------------------------ Z3: real handles

GENERIC_READ, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL = 0x80000000, 3, 0x80
FILE_SHARE_READ, FILE_SHARE_WRITE = 0x1, 0x2


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


@unittest.skipUnless(WINDOWS, "real sharing violations need Windows share modes")
class Z3RealHandles(_Export):
    def sleep_then_release(self, release):
        def sleep(s: float) -> None:
            self.sleeps.append(s)
            release()
        return sleep

    def test_replace_onto_a_file_held_without_delete_sharing(self):
        """Plexy WF-114's case: head.json held open (read/write sharing, no delete sharing); the replace fails with
        winerror 5, the handle is closed during the first sleep, and the second attempt succeeds."""
        release = hold(os.path.join(self.out, "head.json"), FILE_SHARE_READ | FILE_SHARE_WRITE)
        seen: list[int | None] = []
        real = os.replace

        def recording(src, dst):
            try:
                return real(src, dst)
            except PermissionError as exc:
                seen.append(exc.winerror)
                raise
        try:
            with self.patched(sleep=self.sleep_then_release(release)), mock.patch.object(os, "replace", recording):
                note = export.refresh(self.db, "j")
        finally:
            release()
        self.assertNotIn("error", note, note)
        self.assertEqual((seen, self.sleeps), ([5], [0.1]))
        self.assertEqual(export.check(self.db, "j"), [])

    def test_real_read_violation_carries_no_winerror_so_is_not_retried(self):
        """Pins unchanged behaviour, and records WF-127 finding F1: with events.jsonl held at share mode 0, CPython's
        `open` raises PermissionError errno 13 with `winerror` None (the C runtime maps ERROR_SHARING_VIOLATION to
        EACCES), so check's read retry, keyed on `winerror` by A17, does not fire: one attempt, no sleep, today's
        `export file unreadable` for the entry read (the later events.jsonl read is skipped as unreadable, as today).
        If this starts failing, reads report `winerror` and the retry is live: revisit F1."""
        release = hold(os.path.join(self.out, "events.jsonl"), 0)
        try:
            with self.patched():
                problems = export.check(self.db, "j")
        finally:
            release()
        self.assertEqual(self.sleeps, [])
        self.assertEqual(len(problems), 1, problems)
        self.assertTrue(problems[0].startswith("export file unreadable: events.jsonl (PermissionError: [Errno 13]"),
                        problems)


# ------------------------------------------------------------------------------------------------ Z7 (test only)

class Z7PostReadMeasure(Base):
    def test_under_reported_size_is_still_refused(self):
        """Pins unchanged behaviour (Lazy WF-099 N2): `getsize` reports 10 bytes for a JSON file larger than
        MAX_JSON_INPUT_BYTES; the measure of the bytes actually read (`_load_json`) refuses it, nothing is written."""
        self.new_job()
        self.activate()
        limit = commands.MAX_JSON_INPUT_BYTES
        path = os.path.join(self.dir, "big.json")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(json.dumps({"path": "jobs/j/work/a.txt", "sha_before": "", "note": "x" * (limit + 10)}))
        self.assertGreater(os.path.getsize(path), limit)
        real, seen = os.path.getsize, []

        def under_reported(p):
            if os.path.abspath(p) == os.path.abspath(path):
                seen.append(p)
                return 10
            return real(p)
        before = self.ledger_rows()
        with mock.patch.object(os.path, "getsize", under_reported):
            c, r = self.run_wf("append", "j", "--type", "intent", "--content", path)
        self.assertEqual(seen, [path], "the pre-read check did not see the under-reported size")
        self.assertEqual((c, r.get("code")), (1, "rejected"), r)
        self.assertEqual(r["error"], f"rejected(malformed): {path}: {limit + 1} bytes; JSON input is limited to "
                                     f"{limit} bytes")
        self.assertEqual(self.ledger_rows(), before)
        pend = paths.pending_dir(self.db, "j")
        self.assertEqual(os.listdir(pend) if os.path.isdir(pend) else [], [])

    def ledger_rows(self) -> int:
        con = sqlite3.connect(self.db)
        try:
            return con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        finally:
            con.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
