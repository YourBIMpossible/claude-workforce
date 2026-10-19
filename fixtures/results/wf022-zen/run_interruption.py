"""WF-022 step 4, phase A: the step-3 flow to mid-repair, then a kill during an append.

The boundary root must lie under <repo>/fixtures/tmp so that the TEST-ONLY fault seam is honoured (README, "Fault
seam"): `WF_TEST_FAULTS=delay-precommit:8000` holds the receipt append for 8 s after its pending-write marker is
written and before COMMIT, and the process tree is killed with `taskkill /F /T` inside that window. This makes "kill
during an append" deterministic; a real kill can land anywhere. Phase B (recovery) is run one logged call at a time
with wfcall.py. Run: python -I -B run_interruption.py <repo> <boundary root> <job>
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_flow as rf  # noqa: E402  (reuses its contract/grant shapes and its logged run())


def main() -> int:
    repo, root, job = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    if not str(root.resolve()).lower().startswith(str((repo / "fixtures" / "tmp").resolve()).lower()):
        raise SystemExit("refused: the root must be under <repo>/fixtures/tmp (fault seam rule)")
    rf.REPO, rf.ROOT, rf.JOB, rf.DB, rf.LOG = repo, root, job, root / "ledger" / "wf.db", root / "flow-log.jsonl"
    rf.WF = repo / "tools" / "wf" / "wf.py"
    if rf.DB.exists():
        raise SystemExit(f"refused: {rf.DB} exists")
    rec = json.loads((root / "scope-record.json").read_text(encoding="utf-8"))
    C, B = rf.C, rf.B
    # contract / grant identical in shape to run_flow.main (read_scope = pristine only)
    src = Path(rf.__file__).read_text(encoding="utf-8")
    assert '"read_scope": ["pristine/**"]' in src
    build = root / "build"
    build.mkdir(exist_ok=True)
    contract = {
        "owner_request": {"quote": f"Repair the five documentation inconsistencies in the copied folder (job {job})",
                          "source_turn": rf.OWNER_TURN_TEXT.replace(rf.JOB, job)},
        "interpretation": "restore each planted inconsistency I1-I5 in scope/** to its pristine text; change nothing else",
        "baseline": {"manifest": [], "git_revision": None, "uncommitted_state": None},
        "outcome": "scope/** equals pristine/** byte for byte; AC1-AC7 pass",
        "acceptance_criteria": [{"id": f"AC{i}", "check": f"check-acceptance.ps1 AC{i}" if i < 7 else
                                 "wf accept changeset: only scope/** changed", "expected": "True"} for i in range(1, 8)],
        "read_scope": ["pristine/**"], "write_scope": ["scope/**"], "watch": ["ledger/**"],
        "presence_only": [], "allowed_tools": {"builder": ["Write", "Edit"], "reviewer": ["Read"]},
        "limits": "rehearsal: disposable local copy only", "exclusions": ["pristine/**"],
        "evidence": ["ledger", "check-acceptance.ps1 output"], "closeout_rules": "close after accept",
        "recovery_rules": "owner-present manual recovery (spec 3B)", "observe_thresholds": {"slow_s": 600, "silent_s": 1800},
        "environment": "disposable local scope",
    }
    grant = {"grant_id": "G-docfix", "actions": ["write"], "resources": ["scope/**"], "exclusions": [],
             "ars": {"roles": {"coordinator": {"tools": ["Read"], "actions": ["bind"], "paths": ["scope/**"]},
                               "builder": {"tools": ["Write", "Edit"], "actions": ["write"], "paths": ["scope/**"]},
                               "reviewer": {"tools": ["Read"], "actions": ["review"], "paths": ["scope/**", "pristine/**"]}},
                     "replacement": {"allowed": True, "max": 1, "expiry": rf.FAR}},
             "delegation": {}, "expiry": rf.FAR, "revocation_rule": "owner"}
    (build / "contract.json").write_text(json.dumps(contract, indent=1), encoding="utf-8")
    (build / "grant.json").write_text(json.dumps(grant, indent=1), encoding="utf-8")
    turn = f"owner turn for job {job} (WF-022 rehearsal, SIMULATED)"
    rf.must(rf.run("create", C, "job", "new", job, "--contract", str(build / "contract.json"), owner_turn="contract (OT1)"))
    h = rf.must(rf.run("grant", C, "grant", "propose", job, "--grant", str(build / "grant.json")))["block_hash"]
    rf.must(rf.run("grant", C, "grant", "activate", job, "--owner-hash", h[:8], "--quote", f"approve G-docfix {h[:8]}",
                   "--turn", turn, owner_turn="grant block hash + quote (OT2)"))
    rf.must(rf.run("bind", C, "bind", job, "--bind-role", "coordinator", "--bind-session", C[0], "--bind-generation", "1",
                   "--path", "scope/**"))
    rf.must(rf.run("bind", C, "bind", job, "--bind-role", "builder", "--bind-session", B[0], "--bind-generation", "1",
                   "--tool", "Write", "--tool", "Edit", "--path", "scope/**"))
    inj1, inj2 = rec["injections"][0], rec["injections"][1]
    for inj, kill in ((inj1, False), (inj2, True)):
        rel = inj["file"]
        p = root / rel
        before = rf.file_sha(p)
        rf.must(rf.run("repair", B, "append", job, "--type", "intent", "--content",
                       json.dumps({"path": rel, "sha_before": before, "note": f"{inj['id']}: {inj['kind']}"})))
        text = p.read_bytes().decode("utf-8")
        nl = "\r\n" if "\r\n" in text else "\n"
        p.write_bytes(text.replace(inj["injected"].replace("\n", nl), inj["correct"].replace("\n", nl)).encode("utf-8"))
        after = rf.file_sha(p)
        content = json.dumps({"path": rel, "sha_before": before, "sha_after": after, "note": f"{inj['id']} restored"})
        if not kill:
            rf.must(rf.run("repair", B, "append", job, "--type", "receipt", "--content", content))
            continue
        env = dict(os.environ, WF_TEST_FAULTS="delay-precommit:8000")
        cmd = [sys.executable, "-I", "-B", str(rf.WF), "--db", str(rf.DB), "--role", B[1], "--session", B[0], "--json",
               "append", job, "--type", "receipt", "--content", content]
        t0 = time.monotonic()
        proc = subprocess.Popen(cmd, cwd=str(root), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(2.5)                                    # inside the 8 s window: marker written, COMMIT not yet run
        k = subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, text=True)
        out, err = proc.communicate(timeout=30)
        entry = {"phase": "kill", "role": B[1], "session": B[0], "argv": ["wf", "append", job, "--type", "receipt",
                 "--content", content], "exit": proc.returncode, "summary": {"stdout": out[:200], "taskkill_rc": k.returncode,
                 "taskkill": k.stdout.strip()[:200]}, "seconds": round(time.monotonic() - t0, 3), "owner_turn": None,
                 "note": "WF_TEST_FAULTS=delay-precommit:8000 (test-only seam); killed with taskkill /F /T after 2.5 s",
                 "stderr_tail": err.strip()[-300:]}
        with rf.LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        print(f"[kill] receipt append for {inj['id']} killed: exit {proc.returncode}, taskkill rc {k.returncode} "
              f"{k.stdout.strip()[:120]}; stdout {out[:120]!r}")
        pend = root / "ledger" / "pending" / job
        print("pending markers:", sorted(os.listdir(pend)) if pend.exists() else "none")
        print("file on disk after kill:", rel, "sha", after, "(edited; receipt not committed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
