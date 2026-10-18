"""WF-022 step 3: execute the delivered `wf` flow end to end on the disposable scope (rehearsal, NOT a pilot).

Every wf call is a separate `python -I -B tools/wf/wf.py --db <root>/ledger/wf.db --json ...` process; nothing is
imported from wfcore. Each call is logged (command, role/session, exit code, summary, wall time) to
<root>/flow-log.jsonl. Points that need the owner in a real pilot are marked OWNER TURN; in this rehearsal they are
SIMULATED by the agent and say so. Run: python -I -B run_flow.py <repo root> <boundary root> <job>
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

REPO, ROOT, JOB = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
PY = sys.executable
WF = REPO / "tools" / "wf" / "wf.py"
DB = ROOT / "ledger" / "wf.db"
LOG = ROOT / "flow-log.jsonl"
FAR = "2026-12-31T23:59:59Z"
C, B, R = ("C-wf022", "coordinator"), ("B-wf022", "builder"), ("R-wf022", "reviewer")
OWNER_TURN_TEXT = (f"owner turn for job {JOB} (WF-022 rehearsal, SIMULATED by the agent; in a pilot the owner types this "
                   f"turn after reading the Grant Confirmation Block)")


def canon_sha(obj: object) -> str:   # same canonical form as wfcore.canon.sha256_obj (sorted keys, no spaces)
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def file_sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run(phase: str, who: tuple[str, str], *argv: str, owner_turn: str | None = None, note: str = "") -> dict:
    cmd = [PY, "-I", "-B", str(WF), "--db", str(DB), "--role", who[1], "--session", who[0], "--json", *argv]
    t0 = time.monotonic()
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT), timeout=300)
    secs = round(time.monotonic() - t0, 3)
    try:
        res = json.loads(p.stdout) if p.stdout.strip() else {}
    except ValueError:
        res = {"raw": p.stdout[:300]}
    summary = {k: res[k] for k in ("ok", "outcome", "code", "decision", "seq", "clean", "error", "block_hash") if k in res}
    entry = {"phase": phase, "role": who[1], "session": who[0], "argv": ["wf", *argv], "exit": p.returncode,
             "summary": summary, "seconds": secs, "owner_turn": owner_turn, "note": note,
             "stderr_tail": p.stderr.strip()[-300:]}
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    print(f"[{phase}] {who[1]}/{who[0]} wf {' '.join(argv)[:110]} -> exit {p.returncode} {json.dumps(summary)[:200]} "
          f"({secs}s){'  OWNER TURN' if owner_turn else ''}")
    entry["result"] = res
    return entry


def must(e: dict) -> dict:
    if e["exit"] != 0:
        raise SystemExit(f"unexpected failure: {e['argv']} -> {e['summary']} {e['stderr_tail']}")
    return e["result"]


def main() -> int:
    if DB.exists():
        raise SystemExit(f"refused: {DB} exists (one rehearsal per root)")
    rec = json.loads((ROOT / "scope-record.json").read_text(encoding="utf-8"))
    contract = {
        "owner_request": {"quote": f"Repair the five documentation inconsistencies in the copied folder (job {JOB})",
                          "source_turn": OWNER_TURN_TEXT},
        "interpretation": "restore each planted inconsistency I1-I5 in scope/** to its pristine text; change nothing else",
        "baseline": {"manifest": [], "git_revision": None, "uncommitted_state": None},
        "outcome": "scope/** equals pristine/** byte for byte; AC1-AC7 pass",
        "acceptance_criteria": [{"id": f"AC{i}", "check": f"check-acceptance.ps1 AC{i}" if i < 7 else
                                 "wf accept changeset: only scope/** changed", "expected": "True"} for i in range(1, 8)],
        "read_scope": ["pristine/**"], "write_scope": ["scope/**"], "watch": ["ledger/**"],  # read_scope = must stay unchanged (P2 wins over P1, spec 4.4)
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
                     "replacement": {"allowed": True, "max": 1, "expiry": FAR}},
             "delegation": {}, "expiry": FAR, "revocation_rule": "owner"}
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    (build / "contract.json").write_text(json.dumps(contract, indent=1), encoding="utf-8")
    (build / "grant.json").write_text(json.dumps(grant, indent=1), encoding="utf-8")

    must(run("create", C, "job", "new", JOB, "--contract", str(build / "contract.json"),
             owner_turn="contract: the owner's request and acceptance list (owner_request.quote/source_turn)"))
    prop = must(run("grant", C, "grant", "propose", JOB, "--grant", str(build / "grant.json"),
                    note="prints the Grant Confirmation Block and its hash"))
    h = prop["block_hash"]
    (build / "grant-block.txt").write_text(prop.get("block_text", ""), encoding="utf-8")
    must(run("grant", C, "grant", "activate", JOB, "--owner-hash", h[:8], "--quote", f"approve G-docfix {h[:8]}",
             "--turn", OWNER_TURN_TEXT,
             owner_turn="the owner reads the Grant Confirmation Block and returns its hash prefix + quote (SIMULATED)"))
    must(run("bind", C, "bind", JOB, "--bind-role", "coordinator", "--bind-session", C[0], "--bind-generation", "1",
             "--path", "scope/**", note="explicit --path: the default (contract read_scope) exceeds this ARS role"))
    must(run("bind", C, "bind", JOB, "--bind-role", "builder", "--bind-session", B[0], "--bind-generation", "1",
             "--tool", "Write", "--tool", "Edit", "--path", "scope/**"))
    must(run("status", C, "status", JOB))

    for inj in rec["injections"]:   # the repair: one intent -> edit -> receipt per inconsistency
        rel = inj["file"]
        p = ROOT / rel
        before = file_sha(p)
        must(run("repair", B, "append", JOB, "--type", "intent", "--content",
                 json.dumps({"path": rel, "sha_before": before, "note": f"{inj['id']}: {inj['kind']}"})))
        text = p.read_bytes().decode("utf-8")
        nl = "\r\n" if "\r\n" in text else "\n"
        bad, good = inj["injected"].replace("\n", nl), inj["correct"].replace("\n", nl)
        assert text.count(bad) == 1, inj["id"]
        p.write_bytes(text.replace(bad, good).encode("utf-8"))
        after = file_sha(p)
        must(run("repair", B, "append", JOB, "--type", "receipt", "--content",
                 json.dumps({"path": rel, "sha_before": before, "sha_after": after, "note": f"{inj['id']} restored"})))
    must(run("deliver", B, "append", JOB, "--type", "delivered", "--content", json.dumps(
        {"claims": [{"claim": "I1-I5 restored; scope equals pristine", "evidence": ["check-acceptance.ps1", "receipts"]}]})))
    must(run("bind", C, "bind", JOB, "--bind-role", "reviewer", "--bind-session", R[0], "--bind-generation", "1",
             "--tool", "Read", "--path", "scope/**", "--path", "pristine/**",
             note="a reviewer can be bound only after delivery (reviewer-precondition)"))

    chk = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                          str(REPO / "fixtures" / "results" / "wf022-zen" / "check-acceptance.ps1"), "-Root", str(ROOT)],
                         capture_output=True, text=True, timeout=120)
    checks = dict(line.split(" ", 1) for line in chk.stdout.split("\n") if line.strip())
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"phase": "review", "role": "reviewer", "session": R[0], "argv": ["check-acceptance.ps1"],
                             "exit": chk.returncode, "summary": checks, "seconds": None, "owner_turn": None,
                             "note": "reviewer runs the acceptance checks (wf does not run them)"}) + "\n")
    print(f"[review] check-acceptance.ps1 -> exit {chk.returncode} {checks}")
    results = {"criteria": [{"id": f"AC{i}", "pass": checks.get(f"AC{i}", "").strip() == "True" if i < 7 else True,
                             "actual": checks.get(f"AC{i}", "see accept changeset").strip()} for i in range(1, 8)],
               "changed_paths": sorted({i["file"] for i in rec["injections"]}),
               "environment": "disposable local scope", "limits": "rehearsal: disposable local copy only",
               "contract_sha256": canon_sha(contract), "baseline": contract["baseline"]}
    (build / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    acc = must(run("accept", R, "accept", JOB, "--results", str(build / "results.json")))
    must(run("verify", C, "verify", JOB))
    must(run("checkpoint", C, "checkpoint", JOB, owner_turn="copy the head (seq, self) into an owner turn as the "
                                                         "retained checkpoint (spec 3C)"))
    must(run("close", C, "close", JOB, "--intervention", "none (rehearsal: no owner intervention during execution)",
             owner_turn="intervention declaration: the owner states minutes or 'none' (T21); SIMULATED as none"))
    must(run("export", C, "export", JOB))
    must(run("verify", C, "verify", JOB))
    dig = must(run("digest", C, "digest", JOB, owner_turn="the owner reads the digest (Done / Active / Needs-owner)"))
    (build / "digest.json").write_text(json.dumps(dig, indent=1), encoding="utf-8")
    (build / "accept.json").write_text(json.dumps(acc, indent=1), encoding="utf-8")
    print("FLOW COMPLETE:", acc.get("decision"), "closed:", dig.get("closed"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
