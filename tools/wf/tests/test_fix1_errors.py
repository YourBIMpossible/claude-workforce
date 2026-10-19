"""FIX1 lane FXL, WF-023: the CLI error contract (PACKAGE-FIX1.md items E1-E6; WF-020 findings F10, F1/F2, F3/F4,
F5, F9, F11). Each test failed on the base 6d73687 (red-before recorded in from-lazy RESULTS.md) and passes with the
fix. Runs inside the test_wf.py harness (fresh scratch ledger per test under WF_TEST_TMP)."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import sys
import unittest
from unittest import mock

import test_wf
from test_wf import Base

import wf
from wfcore import db as dbm
from wfcore import manifest, markers, paths, txn

SHA = "a" * 64
INTENT = {"path": "jobs/j/work/a.txt", "sha_before": "", "note": "n"}


class Ledger(Base):
    def setUp(self) -> None:
        super().setUp()
        self.new_job()
        self.activate()

    def coord(self, *argv: str):
        return self.run_wf(*argv, role="coordinator", session="C1")

    def head_seq(self) -> int:
        conn = dbm.connect(self.db, create=False)
        try:
            return dbm.head(conn, "j")[0]
        finally:
            conn.close()


# ------------------------------------------------------------------------------------------------ E1 (F10)

class E1InternalErrorExit(unittest.TestCase):
    def _main(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with mock.patch.object(wf, "run", side_effect=RuntimeError("boom")), \
                mock.patch.object(sys, "argv", ["wf.py", *argv]), contextlib.redirect_stdout(out):
            code = wf.main()
        return code, out.getvalue()

    def test_unexpected_exception_is_one_json_object_and_exit_3(self):
        code, text = self._main("--json", "status", "j")
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(text), {"ok": False, "code": "internal-error", "error": "RuntimeError: boom"})

    def test_without_json_it_is_one_plain_line(self):
        code, text = self._main("status", "j")
        self.assertEqual(code, 3)
        self.assertEqual(text, "internal-error: RuntimeError: boom\n")

    def test_argparse_exits_are_unchanged(self):
        with contextlib.redirect_stderr(io.StringIO()), mock.patch.object(sys, "argv", ["wf.py"]):
            self.assertEqual(wf.main(), 2)


# ------------------------------------------------------------------------------------------------ E2 (F1, F2)

class E2NonFiniteAndSurrogates(Ledger):
    def _refused(self, content: str) -> None:
        before = self.head_seq()
        c, r = self.run_wf("append", "j", "--type", "intent", "--content", content)
        self.assertEqual((c, r["code"]), (1, "rejected"), r)
        self.assertIn("rejected(malformed)", r["error"])
        self.assertEqual(self.head_seq(), before, "nothing may be written")

    def test_nan_infinity_and_overflowing_numbers_are_malformed(self):
        for literal in ("NaN", "Infinity", "-Infinity", "1e999"):
            with self.subTest(literal=literal):
                self._refused('{"path":"jobs/j/work/a","sha_before":"","note":' + literal + "}")

    def test_lone_surrogates_in_values_and_keys_are_malformed(self):
        self._refused(json.dumps(dict(INTENT, note="\ud800")))
        self._refused(json.dumps(dict(INTENT, **{"\udfff": "x"})))

    def test_same_rule_for_a_content_file_and_for_a_contract(self):
        p = os.path.join(self.dir, "nan.json")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('{"path":"p","sha_before":"","note":NaN}')
        self._refused(p)
        bad = json.dumps(test_wf.CONTRACT)[:-1] + ',"extra":Infinity}'
        c, r = self.coord("job", "new", "j2", "--contract", bad)
        self.assertEqual((c, r["code"]), (1, "rejected"), r)
        self.assertIn("rejected(malformed)", r["error"])

    def test_ordinary_unicode_still_commits(self):
        c, r = self.run_wf("append", "j", "--type", "intent", "--content",
                           json.dumps(dict(INTENT, note="héllo \U0001F600")))
        self.assertEqual((c, r["outcome"]), (0, "COMMITTED"), r)


# ------------------------------------------------------------------------------------------------ E3 (F3, F4)

class E3CorruptMarkers(Ledger):
    def setUp(self) -> None:
        super().setUp()
        self.pend = paths.pending_dir(self.db, "j")
        os.makedirs(self.pend, exist_ok=True)
        self.marker = os.path.join(self.pend, f"7-{'ab' * 16}.json")

    def _damage(self, text: str) -> None:
        with open(self.marker, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def _reported(self, marks: list[dict]) -> None:
        (m,) = marks
        self.assertEqual((m["seq"], m["state"], m["path"]), (7, "marker-corrupt", self.marker), m)
        self.assertTrue(m["problem"].startswith("marker-corrupt: "), m)
        self.assertIn(self.marker, m["problem"])

    def test_read_commands_report_instead_of_crashing(self):
        for text in ("{not json", "[1]", "7"):
            with self.subTest(marker=text):
                self._damage(text)
                c, r = self.coord("status", "j")
                self.assertEqual(c, 0, r)
                self._reported(r["pending_markers"])
                c, r = self.coord("digest", "j")
                self.assertEqual(c, 0, r)
                c, r = self.coord("verify", "j")
                self.assertEqual(c, 0, r)
                self.assertFalse(r["clean"])
                self._reported(r["jobs"]["j"]["pending_markers"])

    def test_close_and_resolve_refuse_with_a_code(self):
        for text in ("{not json", "[1]"):
            with self.subTest(marker=text):
                self._damage(text)
                c, r = self.coord("close", "j", "--intervention", "x")
                self.assertEqual((c, r["code"]), (1, "refused"), r)
                c, r = self.coord("resolve", "j", "7")
                self.assertEqual((c, r["code"]), (1, "refused"), r)
                self.assertIn("marker-corrupt", r["error"])
                c, r = self.coord("resolve", "j", "7", "--attempt", "ab" * 16, "--disposition", "note")
                self.assertEqual((c, r["code"]), (1, "refused"), r)
                self.assertIn("marker-corrupt", r["error"])

    def test_append_still_refuses_uncertain_write_pending(self):
        self._damage("{not json")
        c, r = self.intent()
        self.assertEqual((c, r["code"]), (1, "uncertain-write-pending"), r)


# ------------------------------------------------------------------------------------------------ E4 (F5)

class E4ManifestAsciiDigits(unittest.TestCase):
    def test_non_ascii_digit_sizes_are_malformed(self):
        for size in ("²", "٣", "１"):          # superscript two, Arabic-Indic three, fullwidth one
            with self.subTest(size=size), self.assertRaises(manifest.ManifestMalformed) as cm:
                manifest.parse_line(1, f"{SHA}  {size}  f")
            self.assertEqual(cm.exception.reason, "field 2 is not a non-negative integer")

    def test_ascii_digits_still_parse(self):
        self.assertEqual(manifest.parse_line(1, f"{SHA}  042  f").size, 42)


# ------------------------------------------------------------------------------------------------ E5 (F9)

class _RollbackFails:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def __getattr__(self, name):
        return getattr(self._c, name)

    def execute(self, sql, *a):
        if isinstance(sql, str) and sql.strip().upper() == "ROLLBACK":
            raise sqlite3.OperationalError("injected rollback failure")
        return self._c.execute(sql, *a)


class E5ResolveRollback(Ledger):
    def test_failing_rollback_is_still_uncertain_not_a_raw_sqlite_error(self):
        self.assertEqual(self.intent()[0], 0)
        conn = dbm.connect(self.db, create=False)
        try:
            seq, head_self = dbm.head(conn, "j")
        finally:
            conn.close()
        ref = markers.MarkerRef(paths.pending_dir(self.db, "j"), seq + 1, markers.new_attempt_id())
        markers.write_atomic(ref, markers.make("j", seq + 1, ref.attempt_id, "f" * 64, head_self, "committing", 1, "s",
                                               self.db, "c"))
        real = dbm.connect
        with mock.patch.object(txn.dbm, "connect", side_effect=lambda *a, **kw: _RollbackFails(real(*a, **kw))):
            c, r = self.coord("resolve", "j", str(seq + 1))
        self.assertEqual((c, r["outcome"]), (1, "STILL-UNCERTAIN"), r)
        self.assertIn("ROLLBACK raised", r["reason"])


# ------------------------------------------------------------------------------------------------ E6 (F11)

class E6JobAlreadyExists(Ledger):
    def test_second_job_new_says_the_job_exists(self):
        before = self.head_seq()
        c, r = self.coord("job", "new", "j", "--contract", self.cpath)
        self.assertEqual((c, r["code"]), (1, "refused"), r)
        self.assertEqual(r["error"], "job j already exists")
        self.assertEqual(self.head_seq(), before)
