"""WF-021 step 3 (MyBuddy): path-form assumptions in tools/wf pinned as OBSERVED.

Rows refer to fixtures/results/wf021-mybuddy/PLATFORM-REVIEW.md (characterization tests; a fix must update them).
String-level gatecore cases run on every platform (they are pure string handling); cases that need POSIX file names
are Linux-only and skip elsewhere with that reason.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest

from test_wf import CONTRACT, WF_DIR, Base  # noqa: E402  (sets sys.path; fresh WF_TEST_TMP)
from wfcore import changeset, gatecore  # noqa: E402

LINUX = sys.platform.startswith("linux")
SKIP = "POSIX file-name semantics pinned on Linux only"


def _payload(path: str, cwd: str) -> dict:
    return {"session_id": "B1", "hook_event_name": "PreToolUse", "tool_name": "Write",
            "tool_input": {"file_path": path, "content": "x"}, "cwd": cwd}


class GatePathStrings(Base):
    def test_row3_observed_driveless_rooted_windows_path_resolves_inside_the_root(self):
        """Row #3 (finding on Windows, string-level so it runs anywhere): with Windows-form cwd/root `C:\\root`, the
        drive-less rooted path `\\jobs\\j\\work\\a.txt` is treated as relative to the cwd and comes back in-root.
        On Windows that path names `C:\\jobs\\...` (outside the boundary)."""
        self.assertEqual(gatecore.target_paths(_payload("\\jobs\\j\\work\\a.txt", "C:\\root"), "C:\\root"),
                         (["jobs/j/work/a.txt"], None))

    @unittest.skipUnless(LINUX, SKIP)
    def test_row1_backslash_in_a_posix_relative_path_is_unresolvable_and_forward_slash_resolves(self):
        """Row #1 / step-1 failure: on Linux `jobs\\j\\work\\b.txt` is one file name, not a path; the gate refuses it
        (fail closed). The forward-slash form resolves in-root."""
        self.assertEqual(gatecore.target_paths(_payload("jobs\\j\\work\\b.txt", self.dir), self.dir),
                         ([], "unresolvable-path"))
        self.assertEqual(gatecore.target_paths(_payload("jobs/j/work/b.txt", self.dir), self.dir),
                         (["jobs/j/work/b.txt"], None))


@unittest.skipUnless(LINUX, SKIP)
class WindowsNameRulesOnLinux(Base):
    def test_row32_observed_legal_linux_device_like_name_fails_closed_in_baseline_and_gate(self):
        """Row #32 (availability cost, fail closed): `aux.c` is a legal Linux file name but a Windows device name;
        the changeset baseline reports it as a problem (so `wf job new` would refuse) and the gate refuses it."""
        os.makedirs(os.path.join(self.dir, "ledger"))
        p = os.path.join(self.dir, "jobs", "j", "work", "aux.c")
        os.makedirs(os.path.dirname(p))
        with open(p, "x", encoding="utf-8") as fh:
            fh.write("int main(){}\n")
        b = changeset.baseline(self.dir, dict(CONTRACT))
        self.assertTrue(any("aux.c" in x for x in b["problems"]), b["problems"])
        self.assertEqual(gatecore.target_paths(_payload(p, self.dir), self.dir), ([], "unresolvable-path"))

    def test_row12_windows_form_hook_config_is_gate_config_on_linux(self):
        """Row #12: a hook config written for Windows (`F:\\...` paths) is not absolute on Linux; the deployed hook
        refuses with `gate-config` and decides nothing."""
        hooks = os.path.join(self.dir, "hooks")
        os.makedirs(hooks)
        hook = os.path.join(hooks, "wf_gate_hook.py")
        shutil.copyfile(os.path.join(WF_DIR, "gate", "wf_gate_hook.py"), hook)
        cfg = {"root": "F:\\wf", "db": "F:\\wf\\ledger\\l.db", "wf_dir": "F:\\wf\\tools\\wf",
               "wfcore_sha256": "0" * 64, "read_timeout_s": 2.0, "hook_sha256": "0" * 64}
        with open(os.path.join(hooks, "wf_gate.json"), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        p = subprocess.run([sys.executable, "-I", hook], input=json.dumps(_payload("/x/a.txt", self.dir)),
                           capture_output=True, text=True, timeout=60, cwd=self.dir, env={"PATH": os.environ.get("PATH", "")})
        self.assertEqual(p.returncode, 2, (p.stdout, p.stderr))
        self.assertEqual(json.loads(p.stdout), {"decision": "block", "reason": "gate-config"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
