"""B2 slice 4a — CLI tests for the gate consumers (INTERFACE-CONTRACT-S4A.md section 5; lane S4W).

`wf gate print-install` prints only (nothing written anywhere) and states that it is not an installation or a
qualification; `wf verify` reports the gate log (presence, chain problems, unlogged attempts) and compares a deployed
hook file's hash without repairing it; `wf digest` raises "gate log incomplete"; `wf reconcile` and `wf observe` use
gate attempts only when the gate log exists and verifies. The gate decision, the gate log and the hook are tested by
their own lanes (S4G, S4L, S4M). No live hook is registered or invoked here.
"""
from __future__ import annotations

import hashlib
import json
import os
import unittest

from test_wf import Base  # noqa: E402

try:
    from wfcore import gatelog  # noqa: E402
except ImportError:  # pragma: no cover - only before the S4L lane is present
    gatelog = None

GATE_PRESENT = gatelog is not None
SKIP = "gate log module not present on this tree (lane S4L)"


def _listing(top: str) -> list[str]:
    out = []
    for d, _dirs, files in os.walk(top):
        for f in files:
            out.append(os.path.relpath(os.path.join(d, f), top))
    return sorted(out)


class B2S4A(Base):
    def root(self) -> str:
        return self.dir

    def record(self, *, decision="allow", channel="preventable", tool="Write", path="jobs/j/work/a.txt",
               session="B1", reason="allowed"):
        d = {"decision": decision, "reason": reason, "channel": channel, "session": session, "tool_name": tool,
             "jobs": ["j"], "paths": [path] if channel == "preventable" else [], "command_sha256": None,
             "command_length": None, "limitation": None}
        return gatelog.record(self.root(), d, payload_sha256=hashlib.sha256(b"p").hexdigest(),
                              ts="2026-10-17T00:00:00Z")

    # ---- print-install prints only
    def test_print_install_writes_nothing(self):
        hooks = os.path.join(self.dir, "fixture-hooks")
        before = _listing(self.dir)
        code, r = self.run_wf("gate", "print-install", "--hooks-dir", hooks, "--root", self.dir)
        if code != 0 and "hook source not found" in r.get("error", ""):
            self.skipTest("hook file not present on this tree (lane S4L)")
        self.assertEqual(code, 0, r)
        self.assertFalse(os.path.exists(hooks))                       # nothing created
        self.assertEqual(_listing(self.dir), before)                  # nothing written anywhere under the fixture
        self.assertEqual(r["written"], [])
        self.assertIn("not installed, not qualified", r["note"])
        hook = r["settings_snippet"]["hooks"]["PreToolUse"][0]
        self.assertEqual(hook["matcher"], "Write|Edit|NotebookEdit|Bash|PowerShell")
        self.assertEqual(hook["hooks"][0]["onFailure"], "block")
        self.assertEqual(len(r["hook_sha256"]), 64)
        self.assertEqual(r["config"]["hook_sha256"], r["hook_sha256"])

    # ---- verify
    def test_verify_reports_gate_absent_and_hook_hash(self):
        self.new_job()
        c, v = self.run_wf("verify", "j")
        self.assertEqual(c, 0, v)
        self.assertFalse(v["gate"]["present"])
        self.assertTrue(v["clean"])
        f = os.path.join(self.dir, "deployed_hook.py")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("print('x')\n")
        c, v = self.run_wf("verify", "j", "--gate-file", f, "--gate-sha", "0" * 64)
        self.assertEqual(c, 0, v)
        dh = v["gate"]["deployed_hook"]
        self.assertFalse(dh["matches"])
        self.assertIn("never repaired", dh["note"])
        with open(f, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "print('x')\n")              # untouched

    @unittest.skipUnless(GATE_PRESENT, SKIP)
    def test_gate_log_failures_make_digest_incomplete(self):
        self.new_job()
        self.activate()
        self.record()
        c, v = self.run_wf("verify", "j")
        self.assertTrue(v["gate"]["present"])
        self.assertEqual(v["gate"]["chain_problems"], [])
        self.assertTrue(v["clean"])                                    # a gate-log state never flips ledger clean
        ok = gatelog.record_failure(self.root(), {"decision": "allow", "session": "B1", "tool_name": "Write"},
                                    OSError("disk full"), ts="2026-10-17T00:00:01Z")
        self.assertTrue(ok)
        c, v = self.run_wf("verify", "j")
        self.assertGreaterEqual(v["gate"]["unlogged_attempts"], 1)
        c, d = self.run_wf("digest", "j")
        self.assertTrue(d["active"]["gate"]["incomplete"])
        self.assertIn("gate log incomplete", [n["item"] for n in d["needs_owner"]])

    # ---- reconcile and observe consume verified gate attempts
    @unittest.skipUnless(GATE_PRESENT, SKIP)
    def test_reconcile_uses_gate_attempts(self):
        self.new_job()
        self.activate()
        c, r = self.run_wf("reconcile", "j", role="coordinator", session="C1")
        self.assertEqual(c, 0, r)
        self.assertEqual(r["exceptions"], [])                           # nothing recorded yet
        c, r = self.run_wf("append", "j", "--type", "ack-reconcile", "--content",
                           json.dumps({"role": "builder", "session": "B1", "report_seq": r["seq"], "exceptions": []}))
        self.record(path="jobs/j/work/a.txt")                          # an allowed write attempt, no receipt
        c, r = self.run_wf("reconcile", "j", role="coordinator", session="C1")
        self.assertEqual(c, 0, r)
        self.assertIn(("jobs/j/work/a.txt", "attempt-without-completion"),
                      [(e["path"], e["class"]) for e in r["exceptions"]])

    @unittest.skipUnless(GATE_PRESENT, SKIP)
    def test_observe_gate_log_available_only_when_verified(self):
        self.new_job()
        self.activate()
        c, o = self.run_wf("observe", "j")
        self.assertEqual(c, 0, o)
        self.assertFalse(o["gate_log"]["available"])
        self.record(channel="shell", tool="Bash", path="")
        c, o = self.run_wf("observe", "j")
        self.assertEqual(c, 0, o)
        self.assertTrue(o["gate_log"]["available"])
        self.assertTrue(o["gate"]["present"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
