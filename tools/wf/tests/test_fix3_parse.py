"""PACKAGE FIX3 part 1, lane F3CL (WF-176): fix 2, the wf.py repeat refusal (SPEC §2, design 2-i; ACCEPTANCE CLI-1
to CLI-13, WF-166e).

Each global option that takes a value (--db, --role, --session, --generation, --msg-id) may be given once, before the
command or after it. A repeat in either position, or across both, makes run() return
{"ok": false, "code": "usage", "error": "rejected(usage): option(s) --X given more than once"} with exit 2, naming each
repeated option in the order it first appears; neither value is used and nothing runs. Abbreviations of the global
options are unknown (allow_abbrev=False on the top parser and on the re-parse parser). Flags may repeat, and the
WF_DB/WF_ROLE/WF_SESSION defaults are not repeats.

Each row runs on its own scratch ledger (Base: a fresh folder per test) holding job `j`, made by `job new` as
coordinator C1. Every row gives --db itself, once unless the row repeats it, and WF_DB, WF_ROLE and WF_SESSION are
unset. A refusal row asserts exit 2, code `usage`, the "given more than once" text with each option's name, and an
unchanged ledger (its row count and head), so it is red where the same command runs or writes.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import uuid
from contextlib import closing
from unittest import mock

import test_wf
from test_wf import Base

import wf

WF_PY = os.path.join(test_wf.WF_DIR, "wf.py")
ENV_DEFAULTS = ("WF_DB", "WF_ROLE", "WF_SESSION")


class RepeatRefusal(Base):
    def setUp(self) -> None:
        super().setUp()
        saved = {k: os.environ.pop(k) for k in ENV_DEFAULTS if k in os.environ}
        self.addCleanup(os.environ.update, saved)
        self.new_job()
        self.blocker = os.path.join(self.dir, "blocker.json")
        with open(self.blocker, "w", encoding="utf-8") as fh:
            json.dump({"reason": "FIX3 fix 2 test"}, fh)
        self.missing = os.path.join(self.dir, "missing", "l.db")

    def ledger(self) -> tuple[int, tuple | None]:
        """The ledger's row count and job j's head (seq, self), read-only."""
        uri = pathlib.Path(self.db).as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as con:
            n = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            head = con.execute("SELECT seq, self FROM events WHERE job='j' ORDER BY seq DESC LIMIT 1").fetchone()
        return n, head

    def assertRefused(self, argv: list[str], *options: str) -> dict:
        before = self.ledger()
        code, res = wf.run(argv)
        self.assertEqual(code, 2, res)
        self.assertEqual((res.get("ok"), res.get("code")), (False, "usage"), res)
        noun = "option" if len(options) == 1 else "options"
        self.assertEqual(res.get("error"), f"rejected(usage): {noun} {', '.join(options)} given more than once", res)
        self.assertEqual(self.ledger(), before, "a refused repeat must not write")
        return res

    def assertRuns(self, argv: list[str]) -> dict:
        code, res = wf.run(argv)
        self.assertEqual(code, 0, res)
        self.assertTrue(res.get("ok"), res)
        return res

    def author_of_last_row(self) -> tuple[str, str]:
        uri = pathlib.Path(self.db).as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as con:
            return con.execute("SELECT author_role, session_id FROM events WHERE job='j' ORDER BY seq DESC LIMIT 1"
                               ).fetchone()

    # CLI-1: a trailing --role/--session no longer overrides the leading one
    def test_cli01_trailing_override_on_a_read(self):
        self.assertRefused(["--db", self.db, "--role", "builder", "--session", "B1", "status", "j",
                            "--role", "coordinator", "--session", "C1"], "--role", "--session")

    def test_cli01w_trailing_override_on_a_write(self):
        self.assertRefused(["--db", self.db, "--role", "builder", "--session", "B1", "--json", "append", "j",
                            "--type", "blocker", "--content", self.blocker, "--role", "coordinator", "--session", "C1"],
                           "--role", "--session")

    # CLI-2: a trailing --db no longer replaces the leading one, and neither ledger is used
    def test_cli02_trailing_db(self):
        self.assertRefused(["--db", self.missing, "status", "j", "--db", self.db], "--db")
        self.assertFalse(os.path.exists(os.path.dirname(self.missing)))

    # CLI-3: the subparser takes `owner` as the job and leaves --role to the re-parse, which counts it
    def test_cli03_subparser_positional_quirk(self):
        self.assertRefused(["--db", self.db, "--role", "builder", "--session", "B1", "append", "--role", "owner", "j",
                            "--type", "blocker", "--content", self.blocker], "--role")

    # CLI-4: abbreviations of the global options are unknown options, before the command and after it
    def _usage_exit(self, argv: list[str]) -> str:
        before = self.ledger()
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            wf.run(argv)
        self.assertEqual(cm.exception.code, 2, err.getvalue())
        self.assertEqual(self.ledger(), before)
        return err.getvalue()

    def test_cli04_leading_abbreviations_are_unknown(self):
        err = self._usage_exit(["--db", self.db, "--ro", "owner", "--sess", "X", "status", "j"])
        self.assertIn("invalid choice: 'owner'", err)

    def test_cli04b_trailing_abbreviation_is_unknown(self):
        err = self._usage_exit(["--db", self.db, "status", "j", "--ro", "owner"])
        self.assertIn("unrecognized arguments: --ro owner", err)

    def test_subcommand_options_keep_their_abbreviations(self):
        self.assertRuns(["--db", self.db, "--role", "builder", "--session", "B1", "append", "j", "--ty", "blocker",
                         "--cont", self.blocker])

    # CLI-5: a trailing global with no leading copy keeps working
    def test_cli05_trailing_only(self):
        self.assertRuns(["--db", self.db, "status", "j", "--generation", "5", "--json"])

    # CLI-6: a repeat before the command is refused too (no last-wins)
    def test_cli06a_repeat_before_the_command_on_a_write(self):
        self.assertRefused(["--db", self.db, "--role", "builder", "--session", "B1", "--role", "coordinator",
                            "--session", "C1", "--json", "append", "j", "--type", "blocker", "--content", self.blocker],
                           "--role", "--session")

    def test_cli06b_repeat_db_before_the_command(self):
        self.assertRefused(["--db", self.missing, "--db", self.db, "status", "j"], "--db")
        self.assertFalse(os.path.exists(os.path.dirname(self.missing)))

    # CLI-8: the pilot's call form (each global once, before the command) works
    def test_cli08_pilot_call_form(self):
        b1 = str(uuid.uuid4())
        self.assertRuns(["--db", self.db, "--role", "builder", "--session", b1, "--generation", "1", "--json", "append",
                         "j", "--type", "blocker", "--content", self.blocker])
        self.assertEqual(self.author_of_last_row(), ("builder", b1))

    # CLI-9: the WF_* defaults fill an option only when the command line does not give it; they are not repeats
    def test_cli09_environment_defaults_are_not_repeats(self):
        env = {"WF_DB": self.missing, "WF_ROLE": "reviewer", "WF_SESSION": "R9"}
        with mock.patch.dict(os.environ, env):
            self.assertRuns(["--db", self.db, "--role", "builder", "--session", "B1", "--json", "append", "j",
                             "--type", "blocker", "--content", self.blocker])
        self.assertEqual(self.author_of_last_row(), ("builder", "B1"))
        self.assertFalse(os.path.exists(os.path.dirname(self.missing)))

    def test_cli09b_environment_defaults_fill_an_absent_option(self):
        with mock.patch.dict(os.environ, {"WF_DB": self.db, "WF_ROLE": "builder", "WF_SESSION": "B7"}):
            self.assertRuns(["--json", "append", "j", "--type", "blocker", "--content", self.blocker])
        self.assertEqual(self.author_of_last_row(), ("builder", "B7"))

    # CLI-10: --generation and --msg-id are counted as well
    def test_cli10a_generation(self):
        self.assertRefused(["--db", self.db, "--generation", "1", "status", "j", "--generation", "2"], "--generation")

    def test_cli10b_msg_id(self):
        self.assertRefused(["--db", self.db, "--role", "builder", "--session", "B1", "--msg-id", "m1", "--msg-id", "m2",
                            "--json", "append", "j", "--type", "blocker", "--content", self.blocker], "--msg-id")

    # CLI-11: the --opt=value form is an occurrence
    def test_cli11_equals_form_counts(self):
        self.assertRefused(["--db", self.db, "--role=builder", "--session", "B1", "--json", "append", "j", "--type",
                            "blocker", "--content", self.blocker, "--role", "coordinator", "--session", "C1"],
                           "--role", "--session")

    # CLI-12: Base.run_wf skips a default the caller passes, so the suite's own calls are not repeats
    def test_cli12_run_wf_skips_a_passed_default(self):
        c, r = self.run_wf("--role", "coordinator", "--session", "C9", "status", "j")
        self.assertEqual(c, 0, r)
        c, r = self.run_wf("--db", self.db, "--msg-id", "m9", "append", "j", "--type", "blocker", "--content",
                           self.blocker, msg_id="m8")
        self.assertEqual(c, 0, r)
        self.assertEqual(r["msg_id"], "m9")

    # CLI-13: a repeated flag carries no value and stays allowed
    def test_cli13_repeated_flag(self):
        self.assertRuns(["--db", self.db, "--json", "status", "j", "--json"])

    def test_names_every_repeat_in_first_appearance_order(self):
        self.assertRefused(["--db", self.db, "--session", "B1", "--role", "builder", "status", "j", "--role", "x",
                            "--db", self.db, "--session", "y"], "--db", "--session", "--role")

    def test_a_value_that_reads_like_an_option_does_not_count(self):
        content = json.dumps({"reason": "--role owner --session X --db elsewhere"})
        self.assertRuns(["--db", self.db, "--role", "builder", "--session", "B1", "append", "j", "--type", "blocker",
                         "--content", content])
        self.assertEqual(self.author_of_last_row(), ("builder", "B1"))

    def test_refusal_reaches_main_as_one_json_body(self):
        """SPEC §2: the refusal uses run()'s result channel, so main() writes it as JSON under --json (not argparse's
        stderr-only SystemExit)."""
        before = self.ledger()
        env = {k: v for k, v in os.environ.items() if k not in ENV_DEFAULTS and k != "WF_TEST_FAULTS"}
        p = subprocess.run([sys.executable, "-I", "-B", WF_PY, "--db", self.db, "--role", "builder", "--session", "B1",
                            "--json", "append", "j", "--type", "blocker", "--content", self.blocker,
                            "--role", "coordinator", "--session", "C1"],
                           capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(p.returncode, 2, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual(out, {"ok": False, "code": "usage",
                               "error": "rejected(usage): options --role, --session given more than once"})
        self.assertEqual(self.ledger(), before)
