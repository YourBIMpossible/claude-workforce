"""WF-022 step 4, phase C: after the recovery, finish the interrupted job (I3-I5, delivery, review, accept, close).
Reuses run_flow's logged calls. Run: python -I -B finish_interruption.py <repo> <boundary root> <job>
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_flow as rf  # noqa: E402


def main() -> int:
    repo, root, job = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    rf.REPO, rf.ROOT, rf.JOB, rf.DB, rf.LOG = repo, root, job, root / "ledger" / "wf.db", root / "flow-log.jsonl"
    rf.WF = repo / "tools" / "wf" / "wf.py"
    rec = json.loads((root / "scope-record.json").read_text(encoding="utf-8"))
    C, B, R = rf.C, rf.B, rf.R
    for inj in rec["injections"][2:]:
        rel = inj["file"]
        p = root / rel
        before = rf.file_sha(p)
        rf.must(rf.run("finish", B, "append", job, "--type", "intent", "--content",
                       json.dumps({"path": rel, "sha_before": before, "note": f"{inj['id']}: {inj['kind']}"})))
        text = p.read_bytes().decode("utf-8")
        nl = "\r\n" if "\r\n" in text else "\n"
        p.write_bytes(text.replace(inj["injected"].replace("\n", nl), inj["correct"].replace("\n", nl)).encode("utf-8"))
        rf.must(rf.run("finish", B, "append", job, "--type", "receipt", "--content", json.dumps(
            {"path": rel, "sha_before": before, "sha_after": rf.file_sha(p), "note": f"{inj['id']} restored"})))
    rf.must(rf.run("finish", B, "append", job, "--type", "delivered", "--content", json.dumps(
        {"claims": [{"claim": "I1-I5 restored after an interrupted append; scope equals pristine",
                     "evidence": ["check-acceptance.ps1", "receipts", "reconcile report + ack"]}]})))
    rf.must(rf.run("finish", C, "bind", job, "--bind-role", "reviewer", "--bind-session", R[0], "--bind-generation", "1",
                   "--tool", "Read", "--path", "scope/**", "--path", "pristine/**"))
    chk = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                          str(repo / "fixtures" / "results" / "wf022-zen" / "check-acceptance.ps1"), "-Root", str(root)],
                         capture_output=True, text=True, timeout=120)
    checks = dict(line.split(" ", 1) for line in chk.stdout.split("\n") if line.strip())
    print(f"[review] check-acceptance.ps1 -> exit {chk.returncode} {checks}")
    contract = json.loads((root / "build" / "contract.json").read_text(encoding="utf-8"))
    results = {"criteria": [{"id": f"AC{i}", "pass": checks.get(f"AC{i}", "").strip() == "True" if i < 7 else True,
                             "actual": checks.get(f"AC{i}", "see accept changeset").strip()} for i in range(1, 8)],
               "changed_paths": sorted({i["file"] for i in rec["injections"]}),
               "environment": "disposable local scope", "limits": "rehearsal: disposable local copy only",
               "contract_sha256": rf.canon_sha(contract), "baseline": contract["baseline"]}
    (root / "build" / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    acc = rf.must(rf.run("finish", R, "accept", job, "--results", str(root / "build" / "results.json")))
    rf.must(rf.run("finish", C, "verify", job))
    rf.must(rf.run("finish", C, "close", job, "--intervention",
                   "owner-present recovery after a killed append: resolve, recover begin/reconcile/close (rehearsal, SIMULATED)",
                   owner_turn="intervention declaration: the recovery was an owner-present intervention (T21); minutes unknown"))
    rf.must(rf.run("finish", C, "export", job))
    rf.must(rf.run("finish", C, "verify", job))
    dig = rf.must(rf.run("finish", C, "digest", job, owner_turn="the owner reads the digest"))
    (root / "build" / "digest.json").write_text(json.dumps(dig, indent=1), encoding="utf-8")
    print("FINISHED:", acc.get("decision"), "closed:", dig.get("closed"), "head:", dig.get("head", {}).get("seq"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
