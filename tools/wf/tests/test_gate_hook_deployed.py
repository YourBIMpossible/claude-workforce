"""Deployed gate hook tests (B2 slice 4a, lane S4L; INTERFACE-CONTRACT-S4A section 4, section 7 S4L, amendment
S4A-A1).

Every case copies `tools/wf/gate/wf_gate_hook.py` into a fixture hooks directory under the test temp root (outside
`tools/wf`), writes `wf_gate.json` beside it, and runs `python -I <copied hook>` as a subprocess with a different
cwd (a fresh directory) and a scrubbed environment (PATH and SYSTEMROOT only). Nothing is registered with Claude Code
and nothing under the user profile or any settings file is touched.

Three layers:
* `HookConfigAndIntegrity` - missing/invalid config -> gate-config; tampered hook, tampered wfcore file, unexpected
  bytecode directory -> gate-integrity; S4A-A1 probe values that are not honoured -> gate-config. These checks run
  before the decision core is imported, so they run on any tree.
* `HookFlowTestCore` - the record and write-failure flow of section 3/4 (allow, deny, shell limitation on stdout and
  stderr, gate.db write failure on a preventable allow -> audit-unavailable plus a failures-file line, failure on
  shell allow and on deny, malformed payload) and the S4A-A1 probes inside `probe_root`. The decision core here is a
  TEST DOUBLE written only into a fixture copy of wfcore under the test temp root; it never ships and is not lane
  S4G's module.
* `HookWithRealGatecore` - the same deployment against the real `wfcore.gatecore` (lane S4G) on a fixture ledger:
  protected-path deny, outside-boundary allow (unbound), T25 out-of-scope deny (bound, S4A-A4), shell allow with
  SHELL_LIMITATION, audit-unavailable, probes. Skipped with a named reason until gatecore is present on the tree.
Exit codes are asserted for decisions (0 allow, 2 deny) and not for the forced-error probe (S4A-A1).

Hook output (AMENDMENT S4A-A6): every finished run is checked against Claude Code's hook-output schema - stdout is
empty (forced-error probe only) or exactly one JSON line, `{"reason": r}` with exit 0 or `{"decision": "block",
"reason": r}` with exit 2; never a top-level `decision` of "approve"/"allow", never `hookSpecificOutput`, no other
key. The wf record (`decision`, `reason`, `channel`, `logged`, plus `limitation` / `probe_fault`) is read from the last
stderr line `wf gate: record <json>`; `channel`/`logged`/`limitation`/`probe_fault` assertions read that record.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from test_wf import WF_DIR, Base  # noqa: E402
from wfcore import gatelog  # noqa: E402

HOOK_SRC = os.path.join(WF_DIR, "gate", "wf_gate_hook.py")
GATECORE_PRESENT = os.path.isfile(os.path.join(WF_DIR, "wfcore", "gatecore.py"))
SKIP_CORE = "gatecore not present on this tree (lane S4G); runs on the combined candidate"
SHELL_LIMITATION = ("shell commands are recorded as invocations only; their filesystem effects are not parsed, "
                    "prevented or comprehensively detected; changes inside the monitored boundary are detected later "
                    "by wf reconcile/accept only")
WIN = sys.platform == "win32"
RECORD_PREFIX = "wf gate: record "
ALLOW_KEYS, DENY_KEYS = {"reason"}, {"decision", "reason"}

TEST_CORE = '''"""TEST DOUBLE written by test_gate_hook_deployed.py into a fixture wfcore copy under the test temp root.
Never shipped; not lane S4G's gatecore. Just enough decision shape to drive the hook's record/failure flow."""
import hashlib
import os

SHELL_LIMITATION = %r


def target_paths(payload, root):
    ti = payload.get("tool_input") or {}
    p = ti.get("file_path") or ti.get("notebook_path")
    if not p:
        return [], None
    rel = os.path.relpath(p, root).replace("\\\\", "/")
    return ([], "outside the root") if rel.startswith("..") else ([rel], None)


def decide(payload, *, root, db_path, now, gate_files, read_timeout_s=2.0, expected_identity=None):
    tool = payload.get("tool_name")
    base = {"session": payload.get("session_id"), "tool_name": tool, "jobs": [], "paths": [],
            "command_sha256": None, "command_length": None, "limitation": None}
    if tool in ("Bash", "PowerShell"):
        cmd = (payload.get("tool_input") or {}).get("command", "")
        return dict(base, decision="allow", reason="shell-not-preventable", channel="shell",
                    command_sha256=hashlib.sha256(cmd.encode("utf-8")).hexdigest(), command_length=len(cmd),
                    limitation=SHELL_LIMITATION)
    if tool in ("Write", "Edit", "NotebookEdit"):
        targets, _problem = target_paths(payload, root)
        if any(t.startswith("ledger/") for t in targets):
            return dict(base, decision="deny", reason="protected-path", channel="preventable", paths=targets)
        return dict(base, decision="allow", reason="test-core-allow", channel="preventable", paths=targets, jobs=["j"])
    return dict(base, decision="allow", reason="not-mediated", channel="other")
''' % SHELL_LIMITATION


def sha256_file(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def expected_wfcore_sha256(wf_dir: str) -> str:
    """Independent re-implementation of the config value (`wf gate print-install`): sorted file names, one
    "<sha256>  <name>\\n" line per wfcore/*.py file, concatenated, SHA-256 of the UTF-8 text."""
    core = os.path.join(wf_dir, "wfcore")
    text = "".join(f"{sha256_file(os.path.join(core, n))}  {n}\n" for n in sorted(os.listdir(core)) if n.endswith(".py"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class HookBase(Base):
    wf_dir = WF_DIR

    def setUp(self) -> None:
        super().setUp()
        self.root = self.dir
        self.hooks = os.path.join(self.dir, "hooks-fixture")

    # ---- deployment
    def fixture_wf(self, *, test_core: bool) -> str:
        """A copy of tools/wf/wfcore/*.py under the test root; with `test_core`, gatecore.py is the TEST DOUBLE."""
        wf = os.path.join(self.dir, "wf-fixture")
        core = os.path.join(wf, "wfcore")
        os.makedirs(core)
        for name in os.listdir(os.path.join(WF_DIR, "wfcore")):
            if name.endswith(".py"):
                shutil.copyfile(os.path.join(WF_DIR, "wfcore", name), os.path.join(core, name))
        if test_core:
            with open(os.path.join(core, "gatecore.py"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(TEST_CORE)
        return wf

    def deploy(self, fixture_wf: str | None = None, **override) -> str:
        wf_dir = fixture_wf or self.wf_dir
        os.makedirs(self.hooks, exist_ok=True)
        hook = os.path.join(self.hooks, "wf_gate_hook.py")
        shutil.copyfile(HOOK_SRC, hook)
        cfg = {"root": self.root, "db": self.db, "wf_dir": wf_dir, "wfcore_sha256": expected_wfcore_sha256(wf_dir),
               "read_timeout_s": 2.0, "hook_sha256": sha256_file(hook)}
        for k, v in override.items():
            if v is None:
                cfg.pop(k, None)
            else:
                cfg[k] = v
        with open(os.path.join(self.hooks, "wf_gate.json"), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        self.hook = hook
        return hook

    def payload(self, tool: str = "Write", *, path: str | None = None, command: str | None = None,
                session: str = "B9", cwd: str | None = None) -> dict:
        ti: dict = {}
        if tool in ("Write", "Edit"):
            ti = {"file_path": path or os.path.join(self.root, "jobs", "j", "work", "a.txt"), "content": "one"}
        elif tool == "NotebookEdit":
            ti = {"notebook_path": path or os.path.join(self.root, "jobs", "j", "work", "n.ipynb")}
        elif tool in ("Bash", "PowerShell"):
            ti = {"command": command if command is not None else "echo hi > jobs/j/work/a.txt"}
        return {"session_id": session, "transcript_path": os.path.join(self.dir, "transcript.jsonl"),
                "cwd": cwd or self.root, "permission_mode": "auto", "hook_event_name": "PreToolUse",
                "tool_name": tool, "tool_input": ti, "tool_use_id": "toolu_fixture"}

    def run_hook(self, payload: dict | str | None = None,
                 timeout: float = 60.0) -> tuple[int, dict | None, str, str, dict | None]:
        """(exit code, stdout JSON object or None, stdout text, stderr text, wf record from stderr or None). Every
        finished run is checked against Claude Code's hook-output schema (S4A-A6) before it is returned."""
        cwd = tempfile.mkdtemp(prefix="elsewhere-", dir=self.dir)  # a different cwd
        env = {"PATH": os.environ.get("PATH", "")}
        if WIN:
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
        data = payload if isinstance(payload, str) else json.dumps(payload or self.payload())
        p = subprocess.run([sys.executable, "-I", self.hook], input=data.encode("utf-8"), capture_output=True,
                           timeout=timeout, cwd=cwd, env=env)
        out_text, err_text = p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
        lines = [ln for ln in out_text.splitlines() if ln.strip()]
        out = json.loads(lines[0]) if len(lines) == 1 else None
        err_lines = [ln for ln in err_text.splitlines() if ln.strip()]
        record = (json.loads(err_lines[-1][len(RECORD_PREFIX):])
                  if err_lines and err_lines[-1].startswith(RECORD_PREFIX) else None)
        self.assert_conforms(p.returncode, out_text, err_text)
        return p.returncode, out, out_text, err_text, record

    def assert_conforms(self, code: int, out_text: str, err_text: str = "") -> None:
        """Claude Code's PreToolUse hook-output schema as frozen by S4A-A6 items 2-3. Empty stdout is allowed (the
        forced-error probe writes nothing); anything else is exactly one JSON object line with the exact key set."""
        ctx = (code, out_text, err_text)
        lines = [ln for ln in out_text.splitlines() if ln.strip()]
        if not lines:
            return
        self.assertEqual(len(lines), 1, ctx)
        obj = json.loads(lines[0])
        self.assertIsInstance(obj, dict, ctx)
        self.assertNotIn("hookSpecificOutput", obj, ctx)
        self.assertNotIn(obj.get("decision"), ("approve", "allow"), ctx)
        self.assertIsInstance(obj.get("reason"), str, ctx)
        if code == 0:
            self.assertEqual(set(obj), ALLOW_KEYS, ctx)
        else:
            self.assertEqual(code, 2, ctx)
            self.assertEqual(set(obj), DENY_KEYS, ctx)
            self.assertEqual(obj["decision"], "block", ctx)

    def assert_decision(self, res, decision: str, reason: str, *, logged: bool | None = None) -> dict:
        """Exit code, exact stdout, the stderr record's decision/reason/logged, and on deny the first stderr line.
        Returns the record."""
        code, out, out_text, err, record = res
        self.assertIsNotNone(out, f"expected one JSON line on stdout; got {out_text!r}; stderr {err!r}")
        expected_out = {"reason": reason} if decision == "allow" else {"decision": "block", "reason": reason}
        self.assertEqual(out, expected_out, (out_text, err))
        self.assertEqual(code, 0 if decision == "allow" else 2, (out_text, err))
        self.assertIsNotNone(record, f"expected a last stderr line starting {RECORD_PREFIX!r}; stderr {err!r}")
        self.assertEqual((record["decision"], record["reason"]), (decision, reason), (record, err))
        if decision == "deny":
            first = [ln for ln in err.splitlines() if ln.strip()][0]
            self.assertTrue(first.startswith(f"wf gate: deny {reason}"), (first, err))
        if logged is not None:
            self.assertEqual(record["logged"], logged, (record, err))
        return record

    def runtime_tree(self) -> list[str]:
        top = os.path.join(self.root, "runtime")
        return sorted(os.path.relpath(os.path.join(d, f), top) for d, _s, fs in os.walk(top) for f in fs)

    def make_gate_db_unwritable(self) -> None:
        os.makedirs(os.path.join(self.root, "runtime", "gate", "gate.db"))  # a directory: SQLite cannot open it

    def failure_lines(self) -> list[dict]:
        with open(os.path.join(self.root, "runtime", "gate", "gate-audit-failures.log"), encoding="utf-8") as fh:
            return [json.loads(x) for x in fh.read().splitlines() if x.strip()]


# ------------------------------------------------------------------------------------------- before the core
class HookConfigAndIntegrity(HookBase):
    def test_wfcore_sha256_matches_the_print_install_algorithm(self):
        spec = importlib.util.spec_from_file_location("_wf_gate_hook_under_test", HOOK_SRC)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)          # importing the hook runs nothing (main only under __main__)
        self.assertEqual(mod.wfcore_sha256(WF_DIR), expected_wfcore_sha256(WF_DIR))
        self.assertTrue(callable(mod.main))

    def test_missing_config_is_gate_config(self):
        self.deploy()
        os.remove(os.path.join(self.hooks, "wf_gate.json"))
        res = self.run_hook()
        out = self.assert_decision(res, "deny", "gate-config", logged=False)
        self.assertEqual(out["channel"], "preventable")
        self.assertIn("not in the gate log", res[3])
        self.assertFalse(os.path.exists(os.path.join(self.root, "runtime")))     # nothing written

    def test_invalid_configs_are_gate_config(self):
        cases = [("not json", None), ("[1, 2]", None)]
        for raw, _ in cases:
            self.deploy()
            with open(os.path.join(self.hooks, "wf_gate.json"), "w", encoding="utf-8") as fh:
                fh.write(raw)
            self.assert_decision(self.run_hook(), "deny", "gate-config", logged=False)
        for override in ({"root": None}, {"root": "relative/root"}, {"wf_dir": 5}, {"hook_sha256": "ABC"},
                         {"wfcore_sha256": "0" * 63}, {"read_timeout_s": 0}, {"read_timeout_s": True}):
            self.deploy(**override)
            self.assert_decision(self.run_hook(), "deny", "gate-config", logged=False)
        self.assertFalse(os.path.exists(os.path.join(self.root, "runtime")))

    def test_tampered_hook_is_gate_integrity(self):
        hook = self.deploy()
        with open(hook, "a", encoding="utf-8") as fh:
            fh.write("\n# modified after the config was written\n")
        self.assert_decision(self.run_hook(), "deny", "gate-integrity", logged=False)
        self.assertFalse(os.path.exists(os.path.join(self.root, "runtime")))

    def test_tampered_wfcore_file_is_gate_integrity(self):
        wf = self.fixture_wf(test_core=True)
        self.deploy(wf)
        self.assert_decision(self.run_hook(), "allow", "test-core-allow", logged=True)   # untampered: decides
        with open(os.path.join(wf, "wfcore", "canon.py"), "a", encoding="utf-8") as fh:
            fh.write("\n# modified library\n")
        res = self.run_hook()
        self.assert_decision(res, "deny", "gate-integrity", logged=False)
        self.assertIn("wfcore tree hash differs", res[3])
        self.assertEqual(gatelog.verify(self.root)["rows"], 1)                        # the denial is not logged

    def test_added_wfcore_file_is_gate_integrity(self):
        wf = self.fixture_wf(test_core=True)
        self.deploy(wf)
        with open(os.path.join(wf, "wfcore", "extra.py"), "w", encoding="utf-8") as fh:
            fh.write("X = 1\n")
        self.assert_decision(self.run_hook(), "deny", "gate-integrity", logged=False)

    def test_unexpected_bytecode_directory_is_gate_integrity(self):
        self.deploy()
        os.makedirs(os.path.join(self.hooks, ".wf-gate-no-bytecode"))
        res = self.run_hook()
        self.assert_decision(res, "deny", "gate-integrity", logged=False)
        self.assertIn("bytecode", res[3])

    def test_probe_not_honoured_is_gate_config(self):
        outside = tempfile.mkdtemp(prefix="probe-elsewhere-", dir=self.dir)
        cases = [
            ({"probe_fault": "explode", "probe_root": self.root}, "unknown probe_fault"),
            ({"probe_fault": "error"}, "requires an absolute probe_root"),
            ({"probe_fault": "timeout", "probe_root": "relative"}, "requires an absolute probe_root"),
            ({"probe_fault": "timeout", "probe_root": self.root, "probe_sleep_s": 0}, "probe_sleep_s"),
            ({"probe_fault": "error", "probe_root": outside}, "cwd is not inside probe_root"),
            ({"probe_fault": "timeout", "probe_root": outside}, "cwd is not inside probe_root"),
        ]
        for override, why in cases:
            self.deploy(**override)
            res = self.run_hook()
            self.assert_decision(res, "deny", "gate-config", logged=False)
            self.assertIn(why, res[3])
        self.assertFalse(os.path.exists(os.path.join(self.root, "runtime")))


# ------------------------------------------------------------------------------- record flow (test double)
class HookFlowTestCore(HookBase):
    def setUp(self) -> None:
        super().setUp()
        self.wf_dir = self.fixture_wf(test_core=True)

    def test_allow_is_recorded_before_answering(self):
        self.deploy()
        payload = self.payload("Write")
        raw = json.dumps(payload)
        res = self.run_hook(raw)
        out = self.assert_decision(res, "allow", "test-core-allow", logged=True)
        self.assertEqual(set(out), {"decision", "reason", "channel", "logged"})
        rows = gatelog.attempts(self.root)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["decision"], rows[0]["reason"], rows[0]["session_id"], rows[0]["paths"]),
                         ("allow", "test-core-allow", "B9", ["jobs/j/work/a.txt"]))
        self.assertEqual(rows[0]["payload_sha256"], hashlib.sha256(raw.encode("utf-8")).hexdigest())
        self.assertEqual(gatelog.verify(self.root)["chain_problems"], [])
        self.assertEqual(self.runtime_tree(), [os.path.join("gate", "gate.db")])

    def test_deny_exit_2_and_recorded(self):
        self.deploy()
        res = self.run_hook(self.payload("Write", path=os.path.join(self.root, "ledger", "l.db")))
        self.assert_decision(res, "deny", "protected-path", logged=True)
        self.assertEqual([r["reason"] for r in gatelog.attempts(self.root)], ["protected-path"])

    def test_shell_limitation_in_record_and_stderr_and_no_command_text_stored(self):
        """S4A-A6: the limitation travels in the stderr record and its own stderr line; stdout is `{"reason"}`
        only (renamed from ..._on_stdout_and_stderr_...: the stdout half no longer holds by contract)."""
        self.deploy()
        command = "echo secret-looking-text > jobs/j/work/a.txt"
        res = self.run_hook(self.payload("Bash", command=command))
        out = self.assert_decision(res, "allow", "shell-not-preventable", logged=True)
        self.assertEqual(out["channel"], "shell")
        self.assertEqual(out["limitation"], SHELL_LIMITATION)
        self.assertNotIn(SHELL_LIMITATION, res[2])
        self.assertIn(f"wf gate: {SHELL_LIMITATION}", res[3])
        row = gatelog.attempts(self.root)[0]
        self.assertEqual((row["command_sha256"], row["command_length"]),
                         (hashlib.sha256(command.encode("utf-8")).hexdigest(), len(command)))
        with open(os.path.join(self.root, "runtime", "gate", "gate.db"), "rb") as fh:
            self.assertNotIn(b"secret-looking-text", fh.read())

    def test_write_failure_on_preventable_allow_is_audit_unavailable(self):
        self.deploy()
        self.make_gate_db_unwritable()
        res = self.run_hook(self.payload("Write"))
        out = self.assert_decision(res, "deny", "audit-unavailable", logged=False)
        self.assertEqual(out["channel"], "preventable")
        self.assertIn("never proceeds unrecorded", res[3])
        lines = self.failure_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual((lines[0]["decision"], lines[0]["reason"], lines[0]["session_id"], lines[0]["tool_name"],
                          lines[0]["error_class"]), ("deny", "audit-unavailable", "B9", "Write", "GateLogError"))
        self.assertEqual(gatelog.verify(self.root)["unlogged_attempts"], 1)

    def test_write_failure_on_shell_allow_stays_allow_and_is_reported(self):
        self.deploy()
        self.make_gate_db_unwritable()
        res = self.run_hook(self.payload("Bash"))
        self.assert_decision(res, "allow", "shell-not-preventable", logged=False)
        self.assertIn("not preventable", res[3])
        self.assertIn("gate log incomplete", res[3])
        self.assertEqual([x["decision"] for x in self.failure_lines()], ["allow"])

    def test_write_failure_on_deny_stays_deny(self):
        self.deploy()
        self.make_gate_db_unwritable()
        res = self.run_hook(self.payload("Write", path=os.path.join(self.root, "ledger", "l.db")))
        self.assert_decision(res, "deny", "protected-path", logged=False)
        self.assertIn("the deny stands", res[3])
        self.assertEqual([x["reason"] for x in self.failure_lines()], ["protected-path"])

    def test_failures_file_also_unwritable_is_said_on_stderr(self):
        self.deploy()
        self.make_gate_db_unwritable()
        os.makedirs(os.path.join(self.root, "runtime", "gate", "gate-audit-failures.log"))
        res = self.run_hook(self.payload("Write"))
        self.assert_decision(res, "deny", "audit-unavailable", logged=False)
        self.assertIn("could not be written either", res[3])

    def test_malformed_stdin_is_denied_and_recorded(self):
        self.deploy()
        for raw in ("not json", "[1, 2]"):
            res = self.run_hook(raw)
            self.assert_decision(res, "deny", "malformed-payload", logged=True)
        self.assertEqual([r["reason"] for r in gatelog.attempts(self.root)], ["malformed-payload"] * 2)

    def test_decision_core_failures_fail_closed_as_gate_error(self):
        core = os.path.join(self.wf_dir, "wfcore", "gatecore.py")
        for body, why in (("return {'decision': 'maybe'}", "unexpected shape"),
                          ("raise ValueError('boom')", "exception")):
            with open(core, "a", encoding="utf-8") as fh:
                fh.write(f"\n\ndef decide(payload, **kw):  # {why}\n    {body}\n")
            self.deploy()                                  # re-hash the fixture tree after the change
            res = self.run_hook(self.payload("Write"))
            self.assert_decision(res, "deny", "gate-error", logged=True)
        self.assertEqual([r["reason"] for r in gatelog.attempts(self.root)], ["gate-error", "gate-error"])

    def test_other_tool_allowed_and_recorded(self):
        self.deploy()
        out = self.assert_decision(self.run_hook(self.payload("Read")), "allow", "not-mediated", logged=True)
        self.assertEqual(out["channel"], "other")

    def test_stdout_conforms_to_claude_code_hook_schema(self):
        """AMENDMENT S4A-A6 (build-7 Q1b: Claude Code rejected `"decision": "allow"`). For an allow on each channel,
        a deny from the decision core, an audit-unavailable deny and a pre-core gate-config deny: stdout is exactly
        `{"reason": r}` (exit 0) or `{"decision": "block", "reason": r}` (exit 2); never "approve"/"allow", never
        `hookSpecificOutput` / `permissionDecision`; the wf record is the last stderr line."""
        cases = []
        self.deploy()
        cases.append(("allow preventable", self.run_hook(self.payload("Write")), 0, "test-core-allow"))
        cases.append(("allow shell", self.run_hook(self.payload("Bash")), 0, "shell-not-preventable"))
        cases.append(("allow other", self.run_hook(self.payload("Read")), 0, "not-mediated"))
        cases.append(("deny core", self.run_hook(self.payload("Write", path=os.path.join(self.root, "ledger", "l.db"))),
                      2, "protected-path"))
        cases.append(("deny malformed", self.run_hook("not json"), 2, "malformed-payload"))
        gate_db = os.path.join(self.root, "runtime", "gate", "gate.db")
        os.replace(gate_db, gate_db + ".kept")                      # the allows above created it; keep, don't delete
        self.make_gate_db_unwritable()
        cases.append(("deny audit-unavailable", self.run_hook(self.payload("Write")), 2, "audit-unavailable"))
        os.remove(os.path.join(self.hooks, "wf_gate.json"))
        cases.append(("deny gate-config", self.run_hook(), 2, "gate-config"))
        for name, (code, out, out_text, err, record), want_code, reason in cases:
            with self.subTest(case=name):
                self.assertEqual(code, want_code, (out_text, err))
                self.assertEqual(len([ln for ln in out_text.splitlines() if ln.strip()]), 1, out_text)
                self.assertNotIn("hookSpecificOutput", out_text)
                self.assertNotIn("permissionDecision", out_text)
                self.assertNotIn('"approve"', out_text)
                self.assertNotIn('"allow"', out_text)
                self.assertEqual(out, {"reason": reason} if want_code == 0 else {"decision": "block", "reason": reason})
                self.assertIsNotNone(record, err)
                self.assertEqual(record["reason"], reason)
                self.assertEqual(record["decision"], "allow" if want_code == 0 else "deny")

    # ---- S4A-A1 probes inside probe_root
    def test_probe_timeout_fires_inside_probe_root(self):
        self.deploy(probe_fault="timeout", probe_root=self.root, probe_sleep_s=30)
        with self.assertRaises(subprocess.TimeoutExpired):        # still running when the caller gives up
            self.run_hook(self.payload("Write"), timeout=3.0)
        self.deploy(probe_fault="timeout", probe_root=self.root, probe_sleep_s=0.2)
        res = self.run_hook(self.payload("Write"))
        out = self.assert_decision(res, "allow", "test-core-allow", logged=True)
        self.assertEqual(out["probe_fault"], "timeout")
        self.assertEqual(gatelog.attempts(self.root)[-1]["probe_fault"], "timeout")

    def test_probe_error_fires_inside_probe_root(self):
        self.deploy(probe_fault="error", probe_root=self.root)
        code, out, out_text, err, record = self.run_hook(self.payload("Write"))
        self.assertIn("wf gate probe: forced error", err)
        self.assertIsNone(out, out_text)                          # no decision line; exit code not asserted
        self.assertEqual(out_text.strip(), "")                    # S4A-A6: nothing on stdout, so nothing to misread
        self.assertIsNone(record, err)
        self.assertEqual(gatelog.verify(self.root)["rows"], 0)

    def test_probe_target_outside_probe_root_is_gate_config(self):
        probe_root = os.path.join(self.root, "jobs", "j")
        os.makedirs(probe_root)
        self.deploy(probe_fault="error", probe_root=probe_root)
        res = self.run_hook(self.payload("Write", path=os.path.join(self.root, "jobs", "k", "work", "a.txt"),
                                         cwd=probe_root))
        self.assert_decision(res, "deny", "gate-config", logged=False)
        self.assertIn("target path is not inside probe_root", res[3])


# ----------------------------------------------------------------------------------- real decision core
@unittest.skipUnless(GATECORE_PRESENT, SKIP_CORE)
class HookWithRealGatecore(HookBase):
    def setUp(self) -> None:
        super().setUp()
        self.new_job()           # fixture ledger at <root>/ledger/l.db with job j
        self.activate()

    def outside_path(self) -> str:
        d = tempfile.mkdtemp(prefix="outside-boundary-", dir=os.path.dirname(self.root))
        return os.path.join(d, "x.txt")

    def test_protected_path_deny(self):
        self.deploy()
        res = self.run_hook(self.payload("Write", path=os.path.join(self.root, "ledger", "l.db")))
        self.assert_decision(res, "deny", "protected-path", logged=True)

    def test_outside_boundary_allow_recorded(self):
        self.deploy()
        res = self.run_hook(self.payload("Write", path=self.outside_path()))
        out = self.assert_decision(res, "allow", "outside-boundary", logged=True)
        self.assertEqual(out["channel"], "preventable")
        self.assertEqual(gatelog.verify(self.root)["rows"], 1)

    def test_T25_bound_session_outside_boundary_denied_recorded(self):
        """T25 (AMENDMENT S4A-A4): the implicit builder B1 is bound to j; its write outside the boundary is exit 2
        `out-of-scope`, recorded; unbound B9 to the same target stays allow `outside-boundary`."""
        code, res = self.run_wf("append", "j", "--type", "intent", "--content",
                                '{"path":"jobs/j/work/a.txt","sha_before":"","note":"n"}')
        self.assertEqual(code, 0, res)
        self.deploy()
        target = self.outside_path()
        out = self.assert_decision(self.run_hook(self.payload("Write", path=target, session="B1")), "deny",
                                   "out-of-scope", logged=True)
        self.assertEqual(out["channel"], "preventable")
        self.assert_decision(self.run_hook(self.payload("Write", path=target, session="B9")), "allow",
                             "outside-boundary", logged=True)
        self.assertFalse(os.path.exists(target))
        self.assertEqual(gatelog.verify(self.root)["rows"], 2)

    def test_shell_allow_carries_limitation(self):
        from wfcore import gatecore  # noqa: PLC0415 - present only on the combined tree
        self.assertEqual(gatecore.SHELL_LIMITATION, SHELL_LIMITATION)
        self.deploy()
        res = self.run_hook(self.payload("Bash"))
        out = self.assert_decision(res, "allow", "shell-not-preventable", logged=True)
        self.assertEqual(out["limitation"], SHELL_LIMITATION)
        self.assertNotIn(SHELL_LIMITATION, res[2])                # S4A-A6: stdout is {"reason"} only
        self.assertIn(f"wf gate: {SHELL_LIMITATION}", res[3])

    def test_audit_unavailable_on_preventable_allow(self):
        self.deploy()
        self.make_gate_db_unwritable()
        res = self.run_hook(self.payload("Write", path=self.outside_path()))
        self.assert_decision(res, "deny", "audit-unavailable", logged=False)
        self.assertEqual(len(self.failure_lines()), 1)

    def test_probes_inside_probe_root(self):
        self.deploy(probe_fault="timeout", probe_root=self.root, probe_sleep_s=30)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_hook(self.payload("Write", path=os.path.join(self.root, "ledger", "l.db")), timeout=3.0)
        self.deploy(probe_fault="error", probe_root=self.root)
        code, out, out_text, err, record = self.run_hook(self.payload("Write", path=os.path.join(self.root, "ledger",
                                                                                                "l.db")))
        self.assertIn("wf gate probe: forced error", err)
        self.assertIsNone(out, out_text)
        self.assertIsNone(record, err)

    def test_deployed_hook_never_writes_the_ledger(self):
        conn = sqlite3.connect(self.db)
        try:
            before = conn.execute("SELECT count(*), max(seq) FROM events").fetchone()
        finally:
            conn.close()
        self.deploy()
        for p in (self.payload("Write", path=os.path.join(self.root, "ledger", "l.db")), self.payload("Bash")):
            self.run_hook(p)
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(conn.execute("SELECT count(*), max(seq) FROM events").fetchone(), before)
        finally:
            conn.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
