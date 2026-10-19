"""FIX2 batch S (lane F2SP, Plexy, WF-106): AMENDMENT FIX2-A7 #4, #5 and #56 (AMENDMENT S4A-A6).

* #4 (WF-021 F10): rule 1 compares the identity of a target with each configured gate file, not only its normalised
  absolute spelling, so an alias of a gate file is `protected-path` for everyone before any allow: a symlink, a
  directory junction, an 8.3 short name, `\\\\?\\C:\\...`, a UNC admin share `\\\\localhost\\C$\\...`. A form this host
  cannot create is skipped with the reason. An alias whose identity cannot be resolved is treated as the gate file
  (fail closed). The direct path is unchanged.
* #5 (WF-021 F12): `wf gate print-install` names `sys.executable` in the hook command and quotes the interpreter and
  the hook path; the command parses back to the intended argv (CommandLineToArgvW on Windows, shlex on POSIX). It still
  prints only.
* #56 (G-3, S4A-A6): a closed job (its ledger holds a `close` event) is `deny` `job-closed` on the preventable channel
  for every session that touches it; the shell channel is unchanged; `recovery-open`, `cancelled` and
  `authority-<state>` keep precedence, on every touched job, before rules 5-8.
* WF-106 review F1 (coordinator, MUST-FIX): rule 1 never stats or realpaths a UNC target whose host is not this
  machine (nor a device path other than a drive form); rule 0 decides it as before FIX2 (`unresolvable-path`). An
  admin share of this machine is mapped lexically to its drive, so no SMB call is made for it either.

Fixtures only: ledgers are built with the real `wf` CLI in a fresh WF_TEST_TMP scratch folder; `decide` is called
in-process; no hook is registered or installed and nothing is written outside the scratch folder.
"""
from __future__ import annotations

import ctypes
import os
import re
import shlex
import socket
import sys
import unittest
import uuid
from typing import Any
from unittest import mock

from test_gate_decide import NOW, TURN, _Gate  # noqa: E402
from wfcore import commands, gatecore  # noqa: E402
from wfcore import db as dbm  # noqa: E402
from wfcore.canon import canonical, event_self_hash, sha256_text  # noqa: E402
from wfcore.validate import now_ts  # noqa: E402

WINDOWS = os.name == "nt"
SESSIONS = (("B1", "Write"), ("B1", "Edit"), ("U9", "Write"), ("U9", "NotebookEdit"))


def _argv_windows(command: str) -> list[str]:
    """argv as Windows itself splits a command line (shell32 CommandLineToArgvW)."""
    shell32, kernel32 = ctypes.windll.shell32, ctypes.windll.kernel32
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    n = ctypes.c_int(0)
    arr = shell32.CommandLineToArgvW(command, ctypes.byref(n))
    if not arr:
        raise OSError(ctypes.get_last_error(), "CommandLineToArgvW failed")
    try:
        return [arr[i] for i in range(n.value)]
    finally:
        kernel32.LocalFree(ctypes.cast(arr, ctypes.c_void_p))


# ===================================================================================================== #4

class F4GateFileIdentity(_Gate):
    """The hook file exists (as when installed); the config file does not yet, so a directory alias must still
    protect a gate file that is not written yet. Aliases live in a sibling folder outside the boundary root."""

    def setUp(self) -> None:
        super().setUp()
        self.explicit()
        self.hooks = os.path.dirname(self.hook)
        os.makedirs(self.hooks)
        with open(self.hook, "w", encoding="utf-8") as fh:
            fh.write("# fixture gate hook\n")
        self.alias_dir = os.path.join(os.path.dirname(self.dir), f"{self.scratch}-alias")
        os.makedirs(self.alias_dir)

    def denied(self, path: str) -> None:
        for session, tool in SESSIONS:
            with self.subTest(path=path, session=session, tool=tool):
                r = self.deny(self.p(absolute=path, session=session, tool=tool), "protected-path")
                self.assertEqual(r["paths"], [])

    def test_direct_path_unchanged(self):
        self.denied(self.hook)
        self.denied(self.cfg)
        other = os.path.join(self.hooks, "notes.txt")   # a sibling of the gate files is not a gate file
        self.allow(self.p(absolute=other, session="U9"), "outside-boundary")
        self.deny(self.p(absolute=other, session="B1"), "out-of-scope")

    def test_symlink_alias(self):
        file_link = os.path.join(self.alias_dir, "hook-link.py")
        dir_link = os.path.join(self.alias_dir, "hooks-link")
        try:
            os.symlink(self.hook, file_link)
            os.symlink(self.hooks, dir_link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink not creatable on this host ({type(exc).__name__}: {exc})")
        self.denied(file_link)
        self.denied(os.path.join(dir_link, "wf_gate_hook.py"))
        self.denied(os.path.join(dir_link, "wf_gate.json"))          # not written yet: protected through the alias
        self.allow(self.p(absolute=os.path.join(dir_link, "notes.txt"), session="U9"), "outside-boundary")

    def test_dangling_symlink_to_an_unwritten_gate_file(self):
        link = os.path.join(self.alias_dir, "cfg-link.json")
        try:
            os.symlink(self.cfg, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink not creatable on this host ({type(exc).__name__}: {exc})")
        self.assertFalse(os.path.exists(self.cfg))
        self.denied(link)

    def test_hard_link_alias(self):
        hard = os.path.join(self.alias_dir, "hook-hard.py")
        try:
            os.link(self.hook, hard)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"hard link not creatable on this host ({type(exc).__name__}: {exc})")
        self.denied(hard)

    def test_junction_alias(self):
        if not WINDOWS:
            self.skipTest("directory junctions exist on Windows only")
        import _winapi
        junction = os.path.join(self.alias_dir, "hooks-junction")
        try:
            _winapi.CreateJunction(self.hooks, junction)
        except OSError as exc:
            self.skipTest(f"junction not creatable on this host ({exc})")
        self.denied(os.path.join(junction, "wf_gate_hook.py"))
        self.denied(os.path.join(junction, "wf_gate.json"))           # not written yet: protected through the alias
        self.allow(self.p(absolute=os.path.join(junction, "notes.txt"), session="U9"), "outside-boundary")

    def test_short_name_alias(self):
        if not WINDOWS:
            self.skipTest("8.3 short names exist on Windows only")
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetShortPathNameW(self.hook, buf, len(buf))
        short = buf.value if n else ""
        if not short or os.path.basename(short).lower() == "wf_gate_hook.py":
            self.skipTest("8.3 short names are not generated on this volume")
        self.assertNotEqual(os.path.normcase(short), os.path.normcase(self.hook))
        self.denied(short)
        self.denied(os.path.join(os.path.dirname(short), "wf_gate.json"))   # short directory name, unwritten file

    def test_extended_length_prefix_alias(self):
        if not WINDOWS:
            self.skipTest("the \\\\?\\ prefix exists on Windows only")
        self.denied("\\\\?\\" + self.hook)
        self.denied("\\\\?\\" + self.cfg)

    def test_unc_admin_share_alias(self):
        if not WINDOWS or self.hook[1:2] != ":":
            self.skipTest("UNC admin shares exist on Windows drive paths only")
        unc = "\\\\localhost\\" + self.hook[0] + "$" + self.hook[2:]
        if not os.path.exists(unc):
            self.skipTest("the admin share \\\\localhost\\" + self.hook[0] + "$ is not reachable on this host")
        self.denied(unc)
        self.denied(os.path.join(os.path.dirname(unc), "wf_gate.json"))

    def test_unresolvable_alias_fails_closed(self):
        """An alias whose identity cannot be resolved (here: an injected access error) is the gate file."""
        alias = os.path.join(self.alias_dir, "unreadable", "hook.py")
        real = gatecore._identity

        def refuse(path: str) -> Any:
            if "unreadable" in path:
                raise PermissionError(13, "injected: access denied", path)
            return real(path)
        with mock.patch.object(gatecore, "_identity", refuse):
            self.denied(alias)
        with mock.patch.object(gatecore, "_identity", side_effect=PermissionError(13, "injected")):
            self.denied(os.path.join(self.dir, "jobs", "j", "work", "a.txt"))   # a gate file cannot be resolved
        self.allow(self.p(absolute=alias, session="U9"), "outside-boundary")  # resolvable again: no gate file

    def test_no_gate_files_needs_no_identity(self):
        with mock.patch.object(gatecore, "_identity", side_effect=PermissionError(13, "injected")):
            for gate_files in ([], None):
                with self.subTest(gate_files=gate_files):
                    r = gatecore.decide(self.p("jobs/j/work/a.txt"), root=self.dir, db_path=self.db, now=NOW,
                                        gate_files=gate_files)
                    self.assertEqual((r["decision"], r["reason"]), ("allow", "allowed"))


# ===================================================================================================== #4, review F1

_UNC = re.compile(r"^[\\/]{2}(?![?.][\\/][A-Za-z]:)")   # a UNC or device spelling other than \\?\X: / \\.\X:


class F1NoNetworkIO(_Gate):
    """WF-106 review F1: no `os.stat`, `os.lstat` or `os.path.realpath` call on a remote target; a local-machine admin
    share is resolved through its drive, never through SMB."""

    def setUp(self) -> None:
        super().setUp()
        if not WINDOWS:
            self.skipTest("UNC and device-namespace paths exist on Windows only")
        self.explicit()
        os.makedirs(os.path.dirname(self.hook))
        with open(self.hook, "w", encoding="utf-8") as fh:
            fh.write("# fixture gate hook\n")

    def watched(self, payload: dict) -> tuple[dict, list[str]]:
        """decide() with os.stat, os.lstat and os.path.realpath recorded: (result, every path they were given)."""
        seen: list[str] = []

        def spy(real: Any) -> Any:
            def call(path: Any, *a: Any, **k: Any) -> Any:
                seen.append(str(path))
                return real(path, *a, **k)
            return call
        with mock.patch.object(os, "stat", spy(os.stat)), mock.patch.object(os, "lstat", spy(os.lstat)), \
                mock.patch.object(os.path, "realpath", spy(os.path.realpath)):
            r = self.d(payload)
        return r, seen

    def check(self, path: str, reason: str, *, banned: str) -> None:
        for session in ("B1", "U9"):
            with self.subTest(path=path, session=session):
                r, seen = self.watched(self.p(absolute=path, session=session))
                self.assertEqual([s for s in seen if re.search(banned, s, re.IGNORECASE)], [])
                self.assertEqual((r["decision"], r["reason"]), ("deny", reason), r)

    def test_remote_unc_target_is_never_resolved(self):
        host = f"wf-no-such-host-{uuid.uuid4().hex[:8]}"
        for path in (f"\\\\{host}\\share\\x.txt", f"//{host}/share/x.txt", f"\\\\?\\UNC\\{host}\\share\\x.txt",
                     f"\\\\.\\UNC\\{host}\\share\\x.txt", f"\\\\{host}\\C$\\x.txt",
                     f"\\\\?\\GLOBALROOT\\Device\\Mup\\{host}\\share\\x.txt"):
            self.check(path, "unresolvable-path", banned=re.escape(host))     # the pre-FIX2 decision, no I/O

    def test_other_local_shares_and_device_paths_are_not_resolved(self):
        for path in ("\\\\localhost\\wf-share\\x.txt", "\\\\.\\pipe\\wf-no-such-pipe",
                     "\\\\?\\Volume{00000000-0000-0000-0000-000000000000}\\x.txt"):
            self.check(path, "unresolvable-path", banned=re.escape(path[:12]))

    def test_local_machine_admin_share_is_mapped_without_smb(self):
        drive, rest = self.hook[0], self.hook[2:]
        if self.hook[1:2] != ":":
            self.skipTest("the fixture root is not on a drive letter")
        names = ["localhost", "LocalHost", "127.0.0.1", "::1", socket.gethostname(), socket.gethostname().upper()]
        forms = [f"\\\\{n}\\{drive}$" + rest for n in names]
        forms += [f"\\\\?\\UNC\\localhost\\{drive.lower()}$" + rest, f"\\\\.\\UNC\\127.0.0.1\\{drive}$" + rest,
                  f"//localhost/{drive}$" + rest.replace("\\", "/"),
                  f"\\\\localhost\\{drive}$" + self.cfg[2:]]                    # a gate file not written yet
        for path in forms:
            self.check(path, "protected-path", banned=_UNC.pattern)


# ===================================================================================================== #5

class F5PrintInstallCommand(_Gate):
    def snippet_argv(self, hooks: str) -> tuple[list[str], dict]:
        r = commands.gate_print_install(hooks_dir=hooks, root=self.dir, db=self.db)
        command = r["settings_snippet"]["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        return (_argv_windows(command) if WINDOWS else shlex.split(command)), r

    def test_interpreter_and_hook_path_with_spaces_round_trip(self):
        hooks = os.path.join(self.dir, "hooks dir with spaces")
        exe = ("C:\\Program Files\\Py thon 3\\python.exe" if WINDOWS else "/opt/py thon 3/bin/python3")
        with mock.patch.object(sys, "executable", exe):
            argv, r = self.snippet_argv(hooks)
        self.assertEqual(argv, [exe, "-I", os.path.join(hooks, "wf_gate_hook.py")])
        self.assertEqual(r["hook_destination"], argv[2])
        self.assertEqual(r["written"], [])
        self.assertFalse(os.path.exists(hooks))                      # print only: nothing created

    def test_snippet_names_sys_executable(self):
        argv, _ = self.snippet_argv(os.path.join(self.dir, "h"))
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(argv[1:], ["-I", os.path.join(self.dir, "h", "wf_gate_hook.py")])

    def test_cli_snippet_round_trips(self):
        hooks = os.path.join(self.dir, "cli hooks")
        r = self.ok(self.run_wf("gate", "print-install", "--hooks-dir", hooks, "--root", self.dir))
        command = r["settings_snippet"]["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        argv = _argv_windows(command) if WINDOWS else shlex.split(command)
        self.assertEqual(argv, [sys.executable, "-I", os.path.join(hooks, "wf_gate_hook.py")])
        self.assertFalse(os.path.exists(hooks))

    def test_empty_interpreter_is_refused(self):
        with mock.patch.object(sys, "executable", ""):
            with self.assertRaises(commands.WfRefused):
                commands.gate_print_install(hooks_dir=os.path.join(self.dir, "h"), root=self.dir, db=self.db)


# ===================================================================================================== #56

class F56JobClosed(_Gate):
    def close(self, job: str = "j") -> None:
        self.ok(self.run_wf("job", "abandon", job, "--reason", "owner stop"))
        r = self.ok(self.run_wf("close", job, "--intervention", "none", role="coordinator", session="C1"))
        self.assertEqual(r["outcome"], "COMMITTED")

    def put_close(self) -> None:
        """A fully hashed, chained `close` row written past the CLI (test_t33_replacement.put_row's method): `wf`
        refuses `close` while a recovery is open and every event, `recovery-opened` included, once a job is closed,
        so a ledger holding both states exists only as a state fixture."""
        c = dbm.connect(self.db, create=False)
        try:
            with c:
                hs, hself = dbm.head(c, "j")
                text = canonical({"intervention": "none", "basis": "accepted", "verify": "clean",
                                  "head_at_close": hs, "contract_sha256": dbm.contract(c, "j")["sha256"]})
                row = {"job": "j", "seq": hs + 1, "type": "close", "msg_id": f"fixture-close-{hs + 1}",
                       "author_role": "coordinator", "session_id": "C1", "generation": 1, "reply_to": None,
                       "grant_ref": None, "ts": now_ts(), "content": text, "content_sha256": sha256_text(text),
                       "prev": hself}
                row["self"] = event_self_hash(row)
                c.execute("INSERT INTO events (job,seq,type,msg_id,author_role,session_id,generation,reply_to,"
                          "grant_ref,ts,content,content_sha256,prev,self) VALUES (:job,:seq,:type,:msg_id,"
                          ":author_role,:session_id,:generation,:reply_to,:grant_ref,:ts,:content,:content_sha256,"
                          ":prev,:self)", row)
        finally:
            c.close()

    def test_bound_builder_write_after_close_is_denied(self):
        self.explicit()
        for tool in ("Write", "Edit", "NotebookEdit"):
            self.allow(self.p("jobs/j/work/a.txt", tool=tool), "allowed")
        self.close()
        for tool in ("Write", "Edit", "NotebookEdit"):
            with self.subTest(tool=tool):
                r = self.deny(self.p("jobs/j/work/a.txt", tool=tool), "job-closed")
                self.assertEqual((r["jobs"], r["paths"]), (["j"], ["jobs/j/work/a.txt"]))

    def test_same_session_shell_is_unchanged(self):
        self.explicit()
        before = {tool: self.d(self.p(tool=tool)) for tool in ("Bash", "PowerShell")}
        self.close()
        for tool, old in before.items():
            with self.subTest(tool=tool):
                r = self.allow(self.p(tool=tool), "shell-not-preventable")
                self.assertEqual(r, old)
                self.assertEqual(r["limitation"], gatecore.SHELL_LIMITATION)

    def test_unbound_in_scope_write_on_closed_job_is_denied(self):
        self.explicit()
        for rel in ("jobs/j/work/a.txt", "jobs/j/notes.md"):
            self.deny(self.p(rel, session="U9"), "unbound-session")
        self.close()
        for rel in ("jobs/j/work/a.txt", "jobs/j/notes.md"):
            for tool in ("Write", "Edit"):
                with self.subTest(rel=rel, tool=tool):
                    r = self.deny(self.p(rel, session="U9", tool=tool), "job-closed")
                    self.assertEqual(r["channel"], "preventable")
        self.allow(self.p(tool="Bash", session="U9"), "shell-not-preventable")      # shell unchanged
        self.allow(self.p("README.md", session="U9"), "not-mediated")               # no job touched: unchanged
        outside = os.path.join(os.path.dirname(self.dir), "elsewhere", "x.txt")
        self.allow(self.p(absolute=outside, session="U9"), "outside-boundary")       # no job touched: unchanged

    def test_recovery_open_on_closed_job_still_wins(self):
        self.explicit()
        self.ok(self.run_wf("recover", "begin", "j", "--reason", "owner-reported interruption", "--turn", TURN,
                            role="coordinator", session="C1"))
        self.put_close()
        self.assertEqual([r["type"] for r in self.ledger_rows()][-2:], ["recovery-opened", "close"])
        c = dbm.connect(self.db, create=False)
        try:
            self.assertEqual(dbm.verify_chain(c, "j"), [])                      # a valid chain: rule 2 passes
        finally:
            c.close()
        for session in ("B1", "U9"):
            with self.subTest(session=session):
                self.deny(self.p("jobs/j/work/a.txt", session=session), "recovery-open")

    def test_cancelled_on_closed_job_still_wins(self):
        self.explicit()
        self.ok(self.run_wf("cancel", "j", "--reason", "stop", "--turn", TURN, role="owner", session="O1"))
        self.close()
        for session in ("B1", "U9"):
            with self.subTest(session=session):
                self.deny(self.p("jobs/j/work/a.txt", session=session), "cancelled")

    def test_existing_states_win_across_touched_jobs(self):
        """C1 is the coordinator of j and k: a Write into j touches both. k cancelled beats j closed, although j sorts
        first (S4A-A6: the existing rule-4 states keep precedence before job-closed)."""
        self.explicit()
        self.job_k()
        self.close("j")
        self.ok(self.run_wf("cancel", "k", "--reason", "stop", "--turn", "owner turn for job k", role="owner",
                            session="O1"))
        r = self.deny(self.p("jobs/j/work/a.txt", session="C1"), "cancelled")
        self.assertEqual(r["jobs"], ["j", "k"])

    def test_bound_session_outside_target_on_closed_job(self):
        """Rule 4 already applies to a bound session's target outside the boundary (S4A-A4); job-closed does too."""
        self.explicit()
        outside = os.path.join(os.path.dirname(self.dir), "elsewhere", "x.txt")
        self.deny(self.p(absolute=outside), "out-of-scope")
        self.close()
        self.deny(self.p(absolute=outside), "job-closed")

    def test_stopped_builder_after_close(self):
        """Run sheet 4.8.1b order (stop-confirm the builder, then close): its Write was not-current-binding and is
        job-closed now; its shell call is unchanged."""
        self.explicit()
        self.stop("B1")
        self.deny(self.p("jobs/j/work/a.txt"), "not-current-binding")
        shell = self.d(self.p(tool="Bash"))
        self.close()
        self.deny(self.p("jobs/j/work/a.txt"), "job-closed")
        self.assertEqual(self.d(self.p(tool="Bash")), shell)

    def test_reason_is_in_the_vocabulary(self):
        self.assertIn("job-closed", gatecore.REASONS)


if __name__ == "__main__":
    unittest.main()
