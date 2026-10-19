"""PACKAGE FIX2 batch G, lane F2GL (WF-112; AMENDMENT FIX2-A10): the export.

  #45  option (b): `export.refresh` runs under the write lock. A connection of its own holds BEGIN IMMEDIATE (RESERVED)
       from before its snapshot until manifest.txt is written, then rolls back, so refreshes serialize with each other
       and with commits and the newest commit's refresh lands last. Readers (SHARED) are not blocked. The forced
       interleavings are `test_wf020_adversarial.ExportRefreshRace` (decorators removed); the natural rate and the
       large-ledger lock timing are measured outside the suite (from-lazy WF-112).
  #51  an export file that cannot be read (held exclusively by another process) is a recoverable exception naming the
       file and the OS error, for `wf verify` and for `wf recover begin`'s reads, never exit 4.
  #61  `_STAGING` anchors at the true end, so `events.jsonl.tmp\\n` is not taken for a staging file.
Each test failed on the base 0a2e67b (red-before in from-lazy RESULTS.md) unless its docstring says it pins
unchanged behaviour. Runs in the test_wf.py harness (a fresh scratch ledger per test under WF_TEST_TMP)."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import sys
import threading
import unittest
from unittest import mock

from test_wf import Base

import wf
from wfcore import export, paths, reconcile


class Ledger(Base):
    def setUp(self) -> None:
        super().setUp()
        self.new_job()
        self.activate()
        c, r = self.intent(note="first")
        self.assertEqual(c, 0, r)
        self.out = paths.export_dir(self.db, "j")

    def main(self, *argv: str, role: str = "coordinator", session: str = "C1") -> tuple[int, dict]:
        """wf.main() in-process, so an exception that escapes `run()` shows as the CLI's exit 4."""
        out = io.StringIO()
        argv_ = ["wf.py", "--db", self.db, "--role", role, "--session", session, "--json", *argv]
        with mock.patch.object(sys, "argv", argv_), contextlib.redirect_stdout(out):
            code = wf.main()
        return code, json.loads(out.getvalue())


@contextlib.contextmanager
def held_exclusively(path: str):
    """Another holder of `path` that shares nothing: on Windows a CreateFileW handle with share mode 0 (every other open
    fails with a sharing violation); elsewhere export's own reads of that file are made to raise PermissionError."""
    if os.name == "nt":
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = ctypes.c_void_p
        k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                    ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        h = k32.CreateFileW(path, 0x80000000, 0, None, 3, 0x80, None)  # GENERIC_READ, no sharing, OPEN_EXISTING
        if h is None or h == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_last_error(), f"could not hold {path}")
        try:
            yield
        finally:
            k32.CloseHandle(h)
        return
    target = os.path.abspath(path)
    real_open, real_sha = open, export.sha256_file

    def denied(p) -> bool:
        return os.path.abspath(p) == target

    def fake_open(p, *a, **kw):
        if denied(p):
            raise PermissionError(13, "held exclusively by another process (mock)", p)
        return real_open(p, *a, **kw)

    def fake_sha(p):
        if denied(p):
            raise PermissionError(13, "held exclusively by another process (mock)", p)
        return real_sha(p)

    with mock.patch.object(export, "open", fake_open, create=True), mock.patch.object(export, "sha256_file", fake_sha), \
            mock.patch.object(export.manifest, "open", fake_open, create=True):
        yield


# ------------------------------------------------------------------------------------------------ #45 option (b)

class F2RefreshLock(Ledger):
    def test_refresh_holds_the_write_lock(self):
        """While a refresh is between its snapshot and its last write, a writer cannot take BEGIN IMMEDIATE, and a
        reader still reads."""
        real = export._write_atomic
        held, release = threading.Event(), threading.Event()
        box: dict = {}

        def paused(*args, **kwargs):
            if threading.current_thread() is box.get("t") and not held.is_set():
                held.set()
                release.wait(30)
            return real(*args, **kwargs)

        with mock.patch.object(export, "_write_atomic", side_effect=paused):
            t = threading.Thread(target=lambda: box.update(note=export.refresh(self.db, "j")))
            box["t"] = t
            t.start()
            try:
                self.assertTrue(held.wait(30), "the refresh never reached its first export write")
                con = sqlite3.connect(self.db, timeout=0.2)
                try:
                    self.assertGreater(con.execute("SELECT count(*) FROM events").fetchone()[0], 0)  # a reader reads
                    with self.assertRaises(sqlite3.OperationalError, msg="a writer got the lock during a refresh"):
                        con.execute("BEGIN IMMEDIATE")
                finally:
                    if con.in_transaction:
                        con.rollback()
                    con.close()
            finally:
                release.set()
                t.join(30)
        self.assertNotIn("error", box["note"], box["note"])
        c, r = self.run_wf("verify", "j", role="coordinator", session="C1")
        self.assertTrue(r["clean"], r)

    def test_verify_clean_after_eight_concurrent_appends(self):
        """Pins acceptance 4 in-process (it can pass at the base, where this host's natural rate is low): 8 writer
        threads append at once and verify is clean with no `wf export`. The CLI natural rate is measured outside."""
        start = threading.Barrier(8)
        results: list = []

        def writer(i: int) -> None:
            start.wait(10)
            results.append(self.intent(note=f"w{i}"))

        ts = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(60)
        self.assertEqual(sorted(r[1].get("outcome") for r in results), ["COMMITTED"] * 8, results)
        c, r = self.run_wf("verify", "j", role="coordinator", session="C1")
        self.assertTrue(r["clean"], r)
        self.assertEqual(r["jobs"]["j"]["export"], [])


# ------------------------------------------------------------------------------------------------ #51 (N-b)

class F2UnreadableExport(Ledger):
    def unreadable(self, name: str) -> tuple[int, dict, list]:
        with held_exclusively(os.path.join(self.out, name)):
            code, r = self.main("verify", "j")
        return code, r, r.get("jobs", {}).get("j", {}).get("export", [])

    def test_verify_reports_unreadable_events_jsonl(self):
        code, r, problems = self.unreadable("events.jsonl")
        self.assertEqual((code, r.get("clean")), (0, False), r)
        hits = [p for p in problems if p.startswith("export file unreadable: events.jsonl")]
        self.assertEqual(len(hits), 1, problems)
        self.assertIn("Error", hits[0])  # the OS error is named

    def test_verify_reports_unreadable_head_and_manifest(self):
        for name in ("head.json", "manifest.txt"):
            with self.subTest(name=name):
                code, r, problems = self.unreadable(name)
                self.assertEqual((code, r.get("clean")), (0, False), r)
                self.assertTrue(any(p.startswith(f"export file unreadable: {name}") for p in problems), problems)

    def test_recover_begin_lists_unreadable_export(self):
        """`wf recover begin` collects the recoverable exceptions through `reconcile.recoverable_exceptions` before its
        transaction; an unreadable export is one of them (class `export`), never exit 4."""
        with held_exclusively(os.path.join(self.out, "events.jsonl")):
            found = reconcile.recoverable_exceptions(self.db, "j")
        self.assertTrue(any(e["class"] == "export" and e["detail"].startswith("export file unreadable: events.jsonl")
                            for e in found), found)


# ------------------------------------------------------------------------------------------------ #61 (export.py)

class F2Staging(unittest.TestCase):
    def test_trailing_newline_is_not_staging(self):
        for name in ("events.jsonl.tmp\n", "contract.json.tmp\n", "head.json.123-0a1b2c3d.tmp\n"):
            with self.subTest(name=name):
                self.assertIsNone(export._STAGING.match(name))
        for name in ("events.jsonl.tmp", "head.json.123-0a1b2c3d.tmp", "checkpoint.json.9-ffffffff.tmp"):
            with self.subTest(name=name):
                self.assertIsNotNone(export._STAGING.match(name))


if __name__ == "__main__":
    unittest.main()
