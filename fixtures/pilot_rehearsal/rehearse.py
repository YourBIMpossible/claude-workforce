"""Disposable Workforce CLI rehearsal (NOT a pilot; TEST FIXTURES only).

Drives the real `tools/wf/wf.py` as a subprocess through the workflow the current CLI supports, inside a fresh
synthetic boundary, and judges OUTCOMES (decisions, axes, chain cleanliness), never exit codes alone.

    python -I -B fixtures/pilot_rehearsal/rehearse.py --root <fresh scratch dir> [--runs 2]

Each run gets its own scratch root `<root>/run-<n>/` holding a boundary `boundary/` with `ledger/l.db`, baseline
files materialised BEFORE `wf job new`, a positive job (accepted, checkpointed, closed) and a negative job (a failed
criterion cannot be accepted; close refuses). Every command, exit code and JSON result goes to
`<root>/run-<n>/transcript.json` together with the exact code revision (`git rev-parse HEAD` of this checkout) and
the interpreter version. Nothing is deleted; two runs with distinct roots never share a path.

Everything "owner" here is a labelled TEST FIXTURE: the grant activation quote and turn are synthetic strings,
the session ids are declared values, not authenticated identities. This script proves nothing about enforcement,
identity or R1 acceptance; it proves the current CLI sequence runs end to end on a disposable scope and that the
rehearsal checks catch a failed criterion.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
WF = os.path.join(REPO, "tools", "wf", "wf.py")
PY = sys.executable

ENVIRONMENT = "disposable local scope"
LABEL = "TEST FIXTURE (synthetic; not owner authorization, not authenticated identity)"
SESSIONS = {"coordinator": "RH-C1", "builder": "RH-B1", "reviewer": "RH-R1"}  # declared values, distinct by role


def contract(job: str) -> dict:
    return {
        "owner_request": {"quote": f"{LABEL}: rehearse job {job}", "source_turn": f"{LABEL}: synthetic turn"},
        "interpretation": "rehearsal of the supported CLI workflow on a disposable boundary",
        "baseline": {"manifest": ["src/baseline.txt"], "git_revision": None, "uncommitted_state": None},
        "outcome": "events recorded; chain clean; job closed",
        "acceptance_criteria": [{"id": "AC1", "check": "wf verify <job> --json", "expected": "clean true"}],
        "read_scope": ["src/**"], "write_scope": [f"jobs/{job}/work/**"], "watch": ["ledger/**"],
        "presence_only": ["docs/**"], "allowed_tools": {"builder": ["Write"], "reviewer": ["Read"]},
        "limits": "rehearsal only", "exclusions": ["README.md"], "evidence": ["ledger"],
        "closeout_rules": "close after accept", "recovery_rules": "none (no recovery exercised)",
        "observe_thresholds": {"held_s": 2.0}, "environment": ENVIRONMENT,
    }


def grant(job: str) -> dict:
    return {"grant_id": "RH-G1", "actions": ["write"], "resources": [f"jobs/{job}/work/**"], "exclusions": [],
            "ars": {"approver": LABEL}, "delegation": {}, "expiry": "2026-12-31T00:00:00Z", "revocation_rule": "owner"}


def canonical_sha256(obj: object) -> str:
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class Rehearsal:
    def __init__(self, run_root: str) -> None:
        self.run_root = run_root
        self.boundary = os.path.join(run_root, "boundary")
        self.db = os.path.join(self.boundary, "ledger", "l.db")
        self.commands: list[dict] = []
        self.checks: list[dict] = []
        os.makedirs(self.boundary, exist_ok=False)

    # ---- plumbing
    def wf(self, *argv: str, role: str = "", session: str = "", msg_id: str | None = None) -> tuple[int, dict]:
        cmd = [PY, "-I", "-B", WF, "--json", "--db", self.db]
        if role:
            cmd += ["--role", role, "--session", session]
        if msg_id:
            cmd += ["--msg-id", msg_id]
        cmd += list(argv)
        env = {k: v for k, v in os.environ.items() if not k.startswith("WF_")}
        cp = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO, env=env, timeout=120, check=False)
        try:
            res = json.loads(cp.stdout) if cp.stdout.strip() else {}
        except ValueError:
            res = {"_unparsed_stdout": cp.stdout}
        self.commands.append({"argv": list(argv), "role": role, "session": session, "msg_id": msg_id,
                              "full_command": cmd[1:], "exit": cp.returncode, "result": res,
                              "stderr_tail": cp.stderr[-800:]})
        return cp.returncode, res

    def check(self, name: str, ok: bool, detail: object = None) -> bool:
        self.checks.append({"check": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    def write(self, rel: str, text: str) -> str:
        p = os.path.join(self.boundary, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return p

    # ---- steps
    def materialise_baseline(self) -> None:
        """Baseline files exist BEFORE job creation: a read_scope file (hashed) and a presence_only file (never read)."""
        self.write("src/baseline.txt", "baseline content v1\n")
        self.write("docs/present.md", "presence-only; never opened by the tool\n")

    def job_new(self, job: str) -> dict:
        c = contract(job)
        cpath = self.write(f"fixtures-{job}/contract.json", json.dumps(c, indent=1))
        code, res = self.wf("job", "new", job, "--contract", cpath, role="coordinator", session=SESSIONS["coordinator"])
        self.check(f"{job}: job new committed", code == 0 and res.get("outcome") == "COMMITTED", res)
        self.check(f"{job}: contract pin equals canonical sha", res.get("contract_sha256") == canonical_sha256(c), res.get("contract_sha256"))
        self.check(f"{job}: baseline captured read_scope entry", isinstance(res.get("baseline_entries"), int) and res["baseline_entries"] >= 1, res.get("baseline_entries"))
        return c

    def activate_grant(self, job: str) -> None:
        gpath = self.write(f"fixtures-{job}/grant.json", json.dumps(grant(job), indent=1))
        code, res = self.wf("grant", "propose", job, "--grant", gpath, role="coordinator", session=SESSIONS["coordinator"])
        h = str(res.get("block_hash", ""))
        self.check(f"{job}: grant proposed with block hash", code == 0 and len(h) == 64, res)
        code, res = self.wf("grant", "activate", job, "--owner-hash", h[:8],
                            "--quote", f"{LABEL}: approve RH-G1 {h[:8]}", "--turn", f"{LABEL}: synthetic owner turn",
                            role="coordinator", session=SESSIONS["coordinator"])
        self.check(f"{job}: grant activated", code == 0 and res.get("outcome") == "COMMITTED", res)

    def builder_work(self, job: str) -> str:
        b = SESSIONS["builder"]
        rel = f"jobs/{job}/work/a.txt"
        code, res = self.wf("append", job, "--type", "intent",
                            "--content", json.dumps({"path": rel, "sha_before": "", "note": "rehearsal write"}),
                            role="builder", session=b)
        self.check(f"{job}: intent committed", code == 0 and res.get("outcome") == "COMMITTED", res)
        p = self.write(rel, f"written by {b} for {job}\n")
        code, res = self.wf("append", job, "--type", "receipt",
                            "--content", json.dumps({"path": rel, "sha_before": "", "sha_after": sha256_file(p),
                                                     "note": "rehearsal write done"}),
                            role="builder", session=b)
        self.check(f"{job}: receipt committed", code == 0 and res.get("outcome") == "COMMITTED", res)
        code, res = self.wf("append", job, "--type", "delivered",
                            "--content", json.dumps({"claims": [{"claim": "a.txt written", "evidence": [rel]}]}),
                            role="builder", session=b)
        self.check(f"{job}: delivered committed", code == 0 and res.get("outcome") == "COMMITTED", res)
        code, res = self.wf("status", job)
        axes = res.get("axes", {})
        self.check(f"{job}: axes after delivery (execution delivered, acceptance pending, authority in-force)",
                   axes.get("execution") == "delivered" and axes.get("acceptance") == "pending" and axes.get("authority") == "in-force", axes)
        return rel

    def accept(self, job: str, c: dict, rel: str, criterion_pass: bool, msg_id: str) -> dict:
        results = {"criteria": [{"id": "AC1", "pass": criterion_pass, "actual": "clean true" if criterion_pass else "clean false"}],
                   "environment": ENVIRONMENT, "limits": "rehearsal only", "changed_paths": [rel],
                   "contract_sha256": canonical_sha256(c), "baseline": c["baseline"]}
        code, res = self.wf("accept", job, "--results", json.dumps(results), role="reviewer", session=SESSIONS["reviewer"], msg_id=msg_id)
        return res

    def positive(self, job: str) -> None:
        c = self.job_new(job)
        self.activate_grant(job)
        rel = self.builder_work(job)
        # same-session refusal: the builder's session may not review its own work
        code, res = self.wf("accept", job, "--results", json.dumps({"criteria": [], "environment": ENVIRONMENT, "limits": "x", "changed_paths": []}),
                            role="reviewer", session=SESSIONS["builder"])
        self.check(f"{job}: builder session refused as reviewer", code != 0 and "same-session" in str(res.get("error", "")), res.get("error"))
        res = self.accept(job, c, rel, True, f"{job}-accept-ok")
        self.check(f"{job}: accepted", res.get("decision") == "accepted", res.get("reasons"))
        self.check(f"{job}: a.txt classified P1", res.get("classes", {}).get("P1") == [rel], res.get("classes"))
        self.check(f"{job}: no undeclared observed change", res.get("undeclared", None) == [] or res.get("undeclared") is None, res.get("undeclared"))
        code, res = self.wf("verify", job)
        self.check(f"{job}: verify clean after accept", res.get("clean") is True, {k: res.get(k) for k in ("clean", "jobs", "ledger_directory")})
        code, cp = self.wf("checkpoint", job)
        self.check(f"{job}: checkpoint retained", code == 0 and isinstance(cp.get("seq"), int) and len(str(cp.get("self", ""))) == 64, cp)
        code, res = self.wf("verify", job, "--checkpoint", f"{cp.get('seq')}:{cp.get('self')}")
        self.check(f"{job}: verify against retained checkpoint clean", res.get("clean") is True and "checkpoint" in res, res.get("checkpoint"))
        code, res = self.wf("digest", job)
        self.check(f"{job}: digest before close shows not closed", res.get("closed") is False, {k: res.get(k) for k in ("closed", "counts")})
        code, res = self.wf("close", job, "--intervention", "none", role="coordinator", session=SESSIONS["coordinator"])
        self.check(f"{job}: close committed", code == 0 and res.get("outcome") == "COMMITTED", res)
        code, res = self.wf("digest", job)
        self.check(f"{job}: digest after close shows closed", res.get("closed") is True, {k: res.get(k) for k in ("closed", "counts")})
        code, res = self.wf("append", job, "--type", "intent", "--content", json.dumps({"path": rel, "sha_before": "", "note": "after close"}),
                            role="builder", session=SESSIONS["builder"])
        self.check(f"{job}: append after close rejected", code != 0 and "closed" in str(res.get("error", "")), res.get("error"))
        code, res = self.wf("verify", job)
        self.check(f"{job}: verify clean after close", res.get("clean") is True, res.get("jobs"))

    def negative(self, job: str) -> None:
        """A failed criterion cannot be accepted: decision rejected, acceptance axis rejected, close refused."""
        c = self.job_new(job)
        self.activate_grant(job)
        rel = self.builder_work(job)
        res = self.accept(job, c, rel, False, f"{job}-accept-fail")
        self.check(f"{job}: failed criterion rejected", res.get("decision") == "rejected", res.get("reasons"))
        self.check(f"{job}: rejection names the criterion", any("AC1" in r for r in res.get("reasons", [])), res.get("reasons"))
        code, res = self.wf("status", job)
        self.check(f"{job}: acceptance axis rejected", res.get("axes", {}).get("acceptance") == "rejected", res.get("axes"))
        code, res = self.wf("close", job, "--intervention", "none", role="coordinator", session=SESSIONS["coordinator"])
        self.check(f"{job}: close refused while rejected", code != 0 and res.get("code") == "refused", res.get("error"))
        code, res = self.wf("digest", job)
        self.check(f"{job}: digest shows not closed", res.get("closed") is False, {k: res.get(k) for k in ("closed", "counts")})
        code, res = self.wf("verify", job)
        self.check(f"{job}: chain still clean after rejection", res.get("clean") is True, res.get("jobs"))

    def run(self) -> dict:
        self.materialise_baseline()
        self.positive("rhpos")
        self.negative("rhneg")
        code, res = self.wf("verify")
        self.check("whole-ledger verify clean", res.get("clean") is True, {k: res.get(k) for k in ("clean", "ledger_directory")})
        return self.transcript()

    def transcript(self) -> dict:
        failed = [c for c in self.checks if not c["ok"]]
        return {"label": LABEL, "code_revision": git_head(), "python": sys.version, "platform": sys.platform,
                "run_root": self.run_root, "sessions": SESSIONS, "finished_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                "commands": self.commands, "checks": self.checks,
                "verdict": "REHEARSAL OK" if not failed else f"REHEARSAL FAILED ({len(failed)} checks)"}


def git_head() -> str:
    try:
        cp = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, timeout=30, check=False)
        return cp.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, help="fresh directory for this rehearsal's scratch roots (must not exist)")
    ap.add_argument("--runs", type=int, default=2)
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root)
    if os.path.exists(root):
        print(f"refused: {root} exists (use a fresh root; nothing is deleted)", file=sys.stderr)
        return 2
    os.makedirs(root)
    rc = 0
    for n in range(1, a.runs + 1):
        run_root = os.path.join(root, f"run-{n}")
        os.makedirs(run_root)
        t = Rehearsal(run_root).run()
        with open(os.path.join(run_root, "transcript.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(t, fh, indent=1, sort_keys=True, default=str)
        print(f"run-{n}: {t['verdict']} ({len(t['commands'])} commands, {len(t['checks'])} checks) rev {t['code_revision'][:12]}")
        for c in t["checks"]:
            if not c["ok"]:
                print(f"  FAIL {c['check']}: {json.dumps(c['detail'], default=str)[:300]}")
                rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
