"""WF-020 (Lazy): regression tests for wf.py argument handling and usage errors that no earlier test exercised.

Each test asserts the CURRENT behaviour at 2dedc94 (see from-lazy WF-020 COMMAND-INVENTORY.md / GAP-LIST.md). They run
inside the test_wf.py harness (fresh ledger per test under WF_TEST_TMP) and add no product code.

Covers: argparse required subcommands/options/choices/types (SystemExit 2), the `_need` role/session checks of every
writer command, the verify/backup usage refusals, main()'s exit codes.
"""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys

import test_wf
from test_wf import Base

import wf

WF_PY = os.path.join(test_wf.WF_DIR, "wf.py")


class Argparse(Base):
    def _exit2(self, *argv: str) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            wf.run(["--db", self.db, "--role", "coordinator", "--session", "C1", *argv])
        self.assertEqual(cm.exception.code, 2, (argv, err.getvalue()))

    def test_required_subcommands_and_options(self):
        cases = [
            ("job",), ("grant",), ("gate",), ("recover",),
            ("job", "new", "j"),                                              # --contract
            ("job", "abandon", "j"),                                          # --reason
            ("grant", "propose", "j"),                                        # --grant
            ("grant", "activate", "j"),                                       # --owner-hash
            ("grant", "revoke", "j", "--reason", "r"), ("grant", "revoke", "j", "--turn", "t"),
            ("grant", "expire", "j", "--reason", "r"), ("grant", "expire", "j", "--turn", "t"),
            ("append", "j", "--type", "intent"), ("append", "j", "--content", "{}"),
            ("backup", "j"),                                                  # --to
            ("accept", "j"),                                                  # --results
            ("close", "j"),                                                   # --intervention
            ("bind", "j", "--bind-role", "builder"), ("bind", "j", "--bind-session", "S"),
            ("ack-reconcile", "j", "--report", "1"), ("ack-reconcile", "j", "--exceptions", "[]"),
            ("grant-check", "j", "--check-role", "builder", "--action", "write"),
            ("grant-check", "j", "--check-role", "builder", "--path", "p"),
            ("grant-check", "j", "--action", "write", "--path", "p"),
            ("cancel", "j", "--reason", "r"), ("cancel", "j", "--turn", "t"),
            ("log-intervention", "j", "--label", "l", "--necessity", "unknown"),
            ("log-intervention", "j", "--label", "l", "--minutes", "1"),
            ("log-intervention", "j", "--necessity", "unknown", "--minutes", "1"),
            ("gate", "print-install", "--hooks-dir", "h"), ("gate", "print-install", "--root", "r"),
            ("recover", "begin", "j", "--reason", "r"), ("recover", "begin", "j", "--turn", "t"),
            ("recover", "reconcile", "j"), ("recover", "close", "j"),
            ("recover", "decide", "j", "--decision", "resume"), ("recover", "decide", "j", "--turn", "t"),
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                self._exit2(*argv)

    def test_missing_positional_job(self):
        for argv in (("status",), ("digest",), ("checkpoint",), ("export",), ("observe",), ("reconcile",), ("cancel",),
                     ("accept", "--results", "{}"), ("close", "--intervention", "x"),
                     ("job", "new", "--contract", "{}"), ("recover", "begin", "--reason", "r", "--turn", "t")):
            with self.subTest(argv=argv):
                self._exit2(*argv)

    def test_choices_and_types(self):
        for argv in (("bind", "j", "--bind-role", "owner", "--bind-session", "S"),
                     ("log-intervention", "j", "--label", "l", "--necessity", "maybe", "--minutes", "1"),
                     ("recover", "decide", "j", "--decision", "undo", "--turn", "t"),
                     ("bind", "j", "--bind-role", "builder", "--bind-session", "S", "--bind-generation", "x"),
                     ("ack-reconcile", "j", "--report", "x", "--exceptions", "[]"),
                     ("resolve", "j", "abc")):
            with self.subTest(argv=argv):
                self._exit2(*argv)

    def test_global_generation_must_be_int(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            wf.run(["--db", self.db, "--generation", "x", "status", "j"])
        self.assertEqual(cm.exception.code, 2)

    def test_no_command_is_a_usage_exit(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            wf.run(["--db", self.db])
        self.assertEqual(cm.exception.code, 2)


class MainExitCodes(Base):
    def _main(self, *argv: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.pop("WF_TEST_FAULTS", None)
        return subprocess.run([sys.executable, "-I", "-B", WF_PY, *argv], capture_output=True, text=True, timeout=60,
                              env=env)

    def test_help_exits_zero(self):
        p = self._main("--help")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("wf", p.stdout)

    def test_no_arguments_exit_two(self):
        self.assertEqual(self._main().returncode, 2)

    def test_unknown_command_exits_two(self):
        self.assertEqual(self._main("frobnicate").returncode, 2)

    def test_json_flag_prints_one_object_on_usage_error(self):
        p = self._main("--db", self.db, "--role", "", "--session", "x", "--json", "job", "new", "j", "--contract",
                       self.cpath)
        self.assertEqual(p.returncode, 2, p.stderr)
        import json
        out = json.loads(p.stdout)
        self.assertFalse(out["ok"])
        self.assertEqual(out["code"], "usage")


class NeedRoleAndSession(Base):
    """Every writer command refuses a blank --role / --session with a usage error (exit 2) before touching the ledger."""

    def _argv_table(self) -> list[tuple[str, tuple[str, ...], bool]]:
        dest = os.path.join(self.dir, "b.db")
        return [
            ("job new", ("job", "new", "j", "--contract", self.cpath), True),
            ("grant propose", ("grant", "propose", "j", "--grant", self.gpath), True),
            ("grant activate", ("grant", "activate", "j", "--owner-hash", "abcdef12"), True),
            ("grant revoke", ("grant", "revoke", "j", "--reason", "r", "--turn", "t"), True),
            ("grant expire", ("grant", "expire", "j", "--reason", "r", "--turn", "t"), True),
            ("cancel", ("cancel", "j", "--reason", "r", "--turn", "t"), True),
            ("log-intervention", ("log-intervention", "j", "--label", "l", "--necessity", "unknown", "--minutes", "1"),
             True),
            ("accept", ("accept", "j", "--results", "{}"), True),
            ("close", ("close", "j", "--intervention", "x"), True),
            ("bind", ("bind", "j", "--bind-role", "builder", "--bind-session", "S"), True),
            ("reconcile", ("reconcile", "j"), True),
            ("ack-reconcile", ("ack-reconcile", "j", "--report", "1", "--exceptions", "[]"), True),
            ("recover begin", ("recover", "begin", "j", "--reason", "r", "--turn", "t"), True),
            ("recover reconcile", ("recover", "reconcile", "j", "--turn", "t"), True),
            ("recover close", ("recover", "close", "j", "--turn", "t"), True),
            ("recover decide", ("recover", "decide", "j", "--decision", "resume", "--turn", "t"), True),
            ("backup", ("backup", "j", "--to", dest), False),            # role only
        ]

    def test_blank_role_is_a_usage_error_for_every_writer(self):
        for name, argv, _needs_session in self._argv_table():
            with self.subTest(command=name):
                code, res = wf.run(["--db", self.db, "--role", "", "--session", "S1", *argv])
                self.assertEqual(code, 2, res)
                self.assertEqual(res["code"], "usage", res)
                self.assertIn("--role is required", res["error"])
        self.assertFalse(os.path.exists(self.db), "a blank-role call must not create the ledger")

    def test_blank_session_is_a_usage_error_where_a_session_is_needed(self):
        for name, argv, needs_session in self._argv_table():
            if not needs_session:
                continue
            with self.subTest(command=name):
                code, res = wf.run(["--db", self.db, "--role", "coordinator", "--session", "", *argv])
                self.assertEqual(code, 2, res)
                self.assertIn("--session is required", res["error"])
        self.assertFalse(os.path.exists(self.db))

    def test_blank_session_does_not_stop_backup_at_the_cli_layer(self):
        # backup needs only --role at wf.py; the later "ledger not found" refusal proves the usage check passed
        code, res = wf.run(["--db", self.db, "--role", "coordinator", "--session", "", "backup", "j", "--to",
                            os.path.join(self.dir, "b.db")])
        self.assertEqual(code, 1, res)
        self.assertEqual(res["code"], "refused")
        self.assertIn("ledger not found", res["error"])


class VerifyAndBackupUsage(Base):
    def test_verify_flag_combinations(self):
        c, r = self.run_wf("verify", "j", "--job", "k", role="coordinator", session="C1")
        self.assertEqual((c, r["code"]), (2, "usage"))
        self.assertIn("--job applies only with --restored", r["error"])
        c, r = self.run_wf("verify", "--restored", self.dir, "--gate-file", "x", role="coordinator", session="C1")
        self.assertEqual((c, r["code"]), (2, "usage"))
        self.assertIn("do not apply to --restored", r["error"])
        c, r = self.run_wf("verify", "--restored", self.dir, "--gate-sha", "a" * 64, role="coordinator", session="C1")
        self.assertEqual((c, r["code"]), (2, "usage"))
        c, r = self.run_wf("verify", "j", "--restored", self.dir, "--job", "k", role="coordinator", session="C1")
        self.assertEqual((c, r["code"]), (2, "usage"))
        self.assertIn("give the job once", r["error"])

    def test_verify_checkpoint_requires_a_job(self):
        c, r = self.run_wf("verify", "--checkpoint", "1:" + "a" * 64, role="coordinator", session="C1")
        self.assertEqual((c, r["code"]), (2, "usage"))
        self.assertIn("--checkpoint requires a job argument", r["error"])

    def test_blank_backup_target_and_blank_restored_path(self):
        c, r = self.run_wf("backup", "j", "--to", " ", role="coordinator", session="C1")
        self.assertEqual((c, r["code"]), (2, "usage"))
        self.assertIn("--to <path> is required", r["error"])
        c, r = self.run_wf("verify", "--restored", " ", role="coordinator", session="C1")
        self.assertEqual((c, r["code"]), (2, "usage"))
        self.assertIn("--restored <path> is required", r["error"])

    def test_verify_and_backup_against_a_missing_ledger_refuse(self):
        for argv in (("verify", "j"), ("verify",), ("status", "j"), ("digest", "j"), ("checkpoint", "j"),
                     ("observe", "j"), ("backup", "j", "--to", os.path.join(self.dir, "b.db"))):
            with self.subTest(argv=argv):
                c, r = self.run_wf(*argv, role="coordinator", session="C1")
                self.assertEqual((c, r["code"]), (1, "refused"), r)
                self.assertIn("ledger not found", r["error"])
                self.assertFalse(os.path.exists(self.db), "read-only commands must not create the ledger")
