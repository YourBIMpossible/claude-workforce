"""WF-021 step 3 (MyBuddy), re-based on FIX1 (`fix1/integration` dfd6e85, WF-021 review F1 / Lazy WF-038):
path-form behaviour of tools/wf pinned as it is AFTER FIX1.

Rows refer to fixtures/results/wf021-mybuddy/PLATFORM-REVIEW.md; the pre-FIX1 observations stay on branch
`wf021/mybuddy` 88ea43f. String-level gatecore cases run on every platform (pure string handling). Cases that need a
POSIX path rule skip on the exact rule they need (`os.sep` / `os.path.isabs` semantics), not on a class-wide platform
test (review L38-M1).
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

POSIX_PATHS = os.name == "posix"
SKIP_POSIX = "needs POSIX path rules: `\\` is a file-name character and `F:\\...` is not absolute"


def _payload(path: str, cwd: str) -> dict:
    return {"session_id": "B1", "hook_event_name": "PreToolUse", "tool_name": "Write",
            "tool_input": {"file_path": path, "content": "x"}, "cwd": cwd}


class GatePathStrings(Base):
    def test_row3_driveless_rooted_path_is_rooted_at_the_cwd_drive(self):
        """Row #3 after FIX1 I7 + FXL review R1 (WF-021 F3 fixed; string-level, runs everywhere): with Windows-form
        cwd/root `C:\\root`, the drive-less rooted `\\jobs\\j\\work\\a.txt` is `C:\\jobs\\...`, outside the boundary
        (before FIX1 it resolved in-root). A drive-less path that does lie under the root resolves in-root, and the
        two-separator forms (`/\\srv...`, `\\/srv...`, `\\\\srv...`) keep the fail-closed answer (R1)."""
        def tp(raw: str):
            return gatecore.target_paths(_payload(raw, "C:\\root"), "C:\\root")
        self.assertEqual(tp("\\jobs\\j\\work\\a.txt"), ([], "outside-boundary"))
        self.assertEqual(tp("\\root\\jobs\\j\\work\\a.txt"), (["jobs/j/work/a.txt"], None))
        for raw in ("/\\srv\\repo\\jobs\\j\\work\\a.txt", "\\/srv/repo/jobs/j/work/a.txt", "\\\\srv\\repo\\x.txt"):
            with self.subTest(raw=raw):
                self.assertEqual(tp(raw), ([], "unresolvable-path"))

    @unittest.skipUnless(POSIX_PATHS, SKIP_POSIX)
    def test_row1_backslash_in_a_posix_relative_path_is_unresolvable_and_forward_slash_resolves(self):
        """Row #1 (unchanged by FIX1; FXL I8 split test_target_paths the same way): on POSIX `jobs\\j\\work\\b.txt` is
        one file name, not a path; the gate refuses it (fail closed). The forward-slash form resolves in-root."""
        self.assertEqual(gatecore.target_paths(_payload("jobs\\j\\work\\b.txt", self.dir), self.dir),
                         ([], "unresolvable-path"))
        self.assertEqual(gatecore.target_paths(_payload("jobs/j/work/b.txt", self.dir), self.dir),
                         (["jobs/j/work/b.txt"], None))


@unittest.skipUnless(POSIX_PATHS, SKIP_POSIX)
class WindowsNameRulesOnPosix(Base):
    def test_row32_observed_legal_linux_device_like_name_fails_closed_in_baseline_and_gate(self):
        """Row #32 (unchanged by FIX1; WF-021 F8 stays open, NEEDS OWNER): `aux.c` is a legal POSIX file name but a
        Windows device name; the changeset baseline reports it as a problem (so `wf job new` refuses) and the gate
        refuses it. POSIX only: the file cannot be created on Windows."""
        os.makedirs(os.path.join(self.dir, "ledger"))
        p = os.path.join(self.dir, "jobs", "j", "work", "aux.c")
        os.makedirs(os.path.dirname(p))
        with open(p, "x", encoding="utf-8") as fh:
            fh.write("int main(){}\n")
        b = changeset.baseline(self.dir, dict(CONTRACT))
        self.assertTrue(any("aux.c" in x for x in b["problems"]), b["problems"])
        self.assertEqual(gatecore.target_paths(_payload(p, self.dir), self.dir), ([], "unresolvable-path"))

    def test_row12_windows_form_hook_config_is_gate_config_on_linux(self):
        """Row #12 (unchanged by FIX1): a hook config written for Windows (`F:\\...` paths) is not absolute on POSIX;
        the deployed hook refuses with `gate-config` and decides nothing."""
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
