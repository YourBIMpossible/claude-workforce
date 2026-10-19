"""WF-047 (Plexy): extended DRY rehearsal of the slice-6 pilot flow on the FIX1 head (dfd6e85). NOT a pilot.

Disposable fixture only: the boundary root is a new folder under <repo>/fixtures/tmp (the only place the test fault seam
`WF_TEST_FAULTS` is honoured); no gate, no profile change, no real ledger. Every OWNER TURN is SIMULATED by the agent.

One job, `pilot-docfix-1`, built from the WF-044 drafts (PILOT-CONTRACT-DRAFT.json / PILOT-GRANT-DRAFT.json, inner
objects, placeholders filled with SIMULATED values) on the WF-022 scope (make_scope.py, copied from wf022/zen c92340c):
  A  create, grant, bind (coordinator with explicit --path, builder B1)            -- WF-022 flow, FIX1 checks (I3, I4, I1)
  B  repair I1
  C  interruption 1: kill during the I2 receipt append (fault delay-precommit), then the S1 clash probe
  D  recovery 1: status, probe, verify, resolve, recover begin (+ second begin: T16), recover reconcile/close, reconcile
  E  replacement (WF-044 gap a): stop-confirmed B1, post-stop reconcile, recover decide resume, bind B2 at generation 2,
     second replacement triggers (T16), stale B1, B2 ack-reconcile, B2 re-records the I2 receipt
  F  repair I3 (B2)
  G  interruption 2 (WF-044 gap b): kill during the I4 receipt append by B2; recovery 2 as in D; B2 acks and re-records
  H  repair I5, delivered, reviewer bind, acceptance checks, accept
  I  checkpoint (WF-044 gap c): checkpoint, verify --checkpoint (match), altered value (mismatch), close, export,
     final checkpoint after close, verify --checkpoint (match), digest

Every call is one `python -I -B tools/wf/wf.py --db <root>/ledger/wf.db --role R --session S [--generation N] --json ...`
process, logged to <root>/flow-log.jsonl with its expectation; any call whose result differs from the expectation is
listed as UNEXPECTED (a finding). Run: python -I -B rehearse_pilot.py <repo root> <root under fixtures/tmp>
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
JOB = "pilot-docfix-1"
C, B1, B2, B3, R1 = "C-pilot", "B1-pilot", "B2-pilot", "B3-pilot", "R1-pilot"
SIM = "SIMULATED by the agent in rehearsal WF-047; in the pilot the owner types this"
EXPIRY = "2026-12-31T00:00:00Z"


def sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class Rehearsal:
    def __init__(self, repo: Path, root: Path) -> None:
        self.repo, self.root = repo, root
        self.db = root / "ledger" / "wf.db"
        self.log = root / "flow-log.jsonl"
        self.n = 0
        self.unexpected: list[dict] = []
        sys.path.insert(0, str(repo / "tools" / "wf"))

    # ------------------------------------------------------------------ one logged call
    def call(self, phase: str, role: str, session: str, *args: str, expect: tuple = ("ok",), gen: int = 1,
             env_extra: dict | None = None, with_db: bool = True, owner_turn: str | None = None, note: str = "") -> dict:
        cmd = [sys.executable, "-I", "-B", str(self.repo / "tools" / "wf" / "wf.py")]
        if with_db:
            cmd += ["--db", str(self.db)]
        cmd += ["--role", role, "--session", session, "--generation", str(gen), "--json", *args]
        env = dict(os.environ)
        env.pop("WF_DB", None)
        env.pop("WF_TEST_FAULTS", None)
        env.update(env_extra or {})
        t0 = time.monotonic()
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(self.root), env=env, timeout=300)
        secs = round(time.monotonic() - t0, 3)
        try:
            res = json.loads(p.stdout) if p.stdout.strip() else {}
        except ValueError:
            res = {"raw": p.stdout[:400]}
        ok, why = self._matches(p.returncode, res, expect)
        return self._record(phase, role, session, gen, args, p.returncode, res, secs, expect, ok, why, with_db,
                            owner_turn, note, p.stderr)

    @staticmethod
    def _matches(code: int, res: dict, expect: tuple) -> tuple[bool, str]:
        kind = expect[0]
        err = str(res.get("error", ""))
        if kind == "ok":
            return code == 0 and res.get("ok") is not False, "exit 0"
        if kind == "rejected":
            return code == 1 and f"rejected({expect[1]})" in err, f"exit 1 rejected({expect[1]})"
        if kind == "code":
            return code == expect[1] and res.get("code") == expect[2], f"exit {expect[1]} code {expect[2]}"
        if kind == "refused":
            return code == 1 and res.get("code") == "refused" and expect[1] in err, f"exit 1 refused '{expect[1]}'"
        raise ValueError(kind)

    def _record(self, phase, role, session, gen, args, code, res, secs, expect, ok, why, with_db, owner_turn, note,
                stderr) -> dict:
        self.n += 1
        keys = ("ok", "outcome", "code", "decision", "seq", "clean", "error", "exceptions", "pending_markers", "axes",
                "warnings", "binding", "self", "checkpoint", "conflict_outcome")
        summary = {k: res[k] for k in keys if k in res}
        entry = {"n": self.n, "phase": phase, "role": role, "session": session, "generation": gen,
                 "argv": ["wf", *args], "with_db": with_db, "exit": code, "summary": summary, "seconds": secs,
                 "expected": why, "as_expected": ok, "owner_turn": owner_turn, "note": note,
                 "stderr_tail": (stderr or "").strip()[-300:]}
        with self.log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
        out = self.root / "build" / f"out-{self.n:03d}-{args[0] if args else 'x'}.json"
        out.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
        flag = "" if ok else "   <-- UNEXPECTED"
        print(f"[{self.n:03d} {phase}] {role}/{session} g{gen} wf {' '.join(a if len(a) < 60 else a[:57] + '...' for a in args)}"
              f" -> exit {code} {json.dumps(summary, default=str)[:220]}{flag}")
        if not ok:
            self.unexpected.append(entry)
        return res

    def note(self, phase: str, what: str, data: dict) -> None:
        self.n += 1
        entry = {"n": self.n, "phase": phase, "role": "harness", "session": None, "argv": [what], "exit": None,
                 "summary": data, "seconds": None, "expected": None, "as_expected": True, "note": what}
        with self.log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
        print(f"[{self.n:03d} {phase}] {what}: {json.dumps(data, default=str)[:300]}")

    # ------------------------------------------------------------------ helpers
    def markers(self) -> list[Path]:
        d = self.root / "ledger" / "pending" / JOB
        return sorted(d.glob("*.json")) if d.is_dir() else []

    def edit(self, inj: dict) -> tuple[str, str]:
        """Restore one planted inconsistency on disk; returns (sha_before, sha_after)."""
        p = self.root / inj["file"]
        before = sha_file(p)
        text = p.read_bytes().decode("utf-8")
        nl = "\r\n" if "\r\n" in text else "\n"
        f, r = inj["injected"].replace("\n", nl), inj["correct"].replace("\n", nl)
        assert text.count(f) == 1, (inj["id"], text.count(f))
        p.write_bytes(text.replace(f, r).encode("utf-8"))
        return before, sha_file(p)

    def intent(self, phase, session, gen, inj, sha_before):
        return self.call(phase, "builder", session, "append", JOB, "--type", "intent", "--content",
                         json.dumps({"path": inj["file"], "sha_before": sha_before, "note": f"repair {inj['id']}"}), gen=gen)

    def receipt_content(self, inj, before, after, note=""):
        return json.dumps({"path": inj["file"], "sha_before": before, "sha_after": after,
                           "note": note or f"repaired {inj['id']}"})

    def repair(self, phase, session, gen, inj):
        before = sha_file(self.root / inj["file"])
        self.intent(phase, session, gen, inj, before)
        b, a = self.edit(inj)
        self.call(phase, "builder", session, "append", JOB, "--type", "receipt", "--content",
                  self.receipt_content(inj, b, a), gen=gen)

    def kill_during_receipt(self, phase, session, gen, inj) -> tuple[str, str]:
        """intent -> edit -> receipt append held by delay-precommit after its marker is written -> killed."""
        before = sha_file(self.root / inj["file"])
        self.intent(phase, session, gen, inj, before)
        b, a = self.edit(inj)
        cmd = [sys.executable, "-I", "-B", str(self.repo / "tools" / "wf" / "wf.py"), "--db", str(self.db),
               "--role", "builder", "--session", session, "--generation", str(gen), "--json",
               "append", JOB, "--type", "receipt", "--content", self.receipt_content(inj, b, a)]
        env = dict(os.environ)
        env.pop("WF_DB", None)
        env["WF_TEST_FAULTS"] = "delay-precommit:8000"
        t0 = time.monotonic()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(self.root), env=env)
        marker_at = None
        while time.monotonic() - t0 < 6.0:
            if self.markers():
                marker_at = round(time.monotonic() - t0, 3)
                break
            time.sleep(0.02)
        time.sleep(0.3)                                   # inside the 8 s hold, after the marker exists
        exited_before = proc.poll() is not None
        proc.kill()
        out, err = proc.communicate(timeout=30)
        secs = round(time.monotonic() - t0, 3)
        self._record(phase, "builder", session, gen, ("append", JOB, "--type", "receipt", "(killed)"), proc.returncode,
                     {"stdout": out.strip()[:200]}, secs, ("kill",), not exited_before and marker_at is not None,
                     "killed after the committing marker appeared", True, None,
                     f"staged kill: WF_TEST_FAULTS=delay-precommit:8000, marker after {marker_at}s, killed (TerminateProcess)",
                     err)
        return b, a

    def recovery(self, phase: str, which: str) -> dict:
        """status -> probe -> verify -> resolve -> recover begin (+ second begin) -> recover reconcile/close."""
        st = self.call(phase, "coordinator", C, "status", JOB)
        pm = st.get("pending_markers") or []
        self.call(phase, "builder", "PROBE", "append", JOB, "--type", "blocker", "--content", '{"reason":"probe"}',
                  expect=("code", 1, "uncertain-write-pending"), note="any append is refused while a write is uncertain")
        self.call(phase, "coordinator", C, "verify", JOB, note="expected clean:false while the marker exists")
        m = pm[0] if pm else {}
        res = self.call(phase, "coordinator", C, "resolve", JOB, str(m.get("seq")), "--attempt", str(m.get("attempt_id")),
                        owner_turn=f"owner-present decision on the uncertain write ({SIM})")
        self.call(phase, "coordinator", C, "verify", JOB)
        turn = f"owner turn for job {JOB}: recover {which} ({SIM})"
        opened = self.call(phase, "coordinator", C, "recover", "begin", JOB, "--reason",
                           f"staged kill during a receipt append ({which})", "--turn", turn, owner_turn=turn)
        self.call(phase, "coordinator", C, "recover", "begin", JOB, "--reason", "second trigger for the same interruption",
                  "--turn", turn, expect=("rejected", "recovery-open"),
                  note="T16: a second recovery trigger for the same interruption is refused")
        self.call(phase, "coordinator", C, "reconcile", JOB, expect=("rejected", "recovery-open"),
                  note="ordinary reconcile is refused while the recovery is open (as WF-022 row 18)")
        self.call(phase, "coordinator", C, "recover", "reconcile", JOB, "--turn", turn, owner_turn=turn)
        self.call(phase, "coordinator", C, "recover", "close", JOB, "--turn", turn, owner_turn=turn)
        return {"resolve": res, "opened": opened}


def main() -> int:
    repo, root = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    tmp = (repo / "fixtures" / "tmp").resolve()
    if os.path.commonpath([str(root), str(tmp)]) != str(tmp):
        raise SystemExit(f"refused: {root} is not under {tmp} (fixture-only rehearsal)")
    if root.exists():
        raise SystemExit(f"refused: {root} exists (one rehearsal per root)")
    subprocess.run([sys.executable, "-I", "-B", str(HERE / "make_scope.py"), str(repo), str(root)], check=True,
                   capture_output=True)
    (root / "ledger").mkdir()
    (root / "build").mkdir()
    rh = Rehearsal(repo, root)
    from wfcore.canon import sha256_obj  # noqa: E402  (the delivered canonical hash, for the accept declarations)

    rec = json.loads((root / "scope-record.json").read_text(encoding="utf-8"))
    inj = {i["id"]: i for i in rec["injections"]}
    rh.note("setup", "scope built by make_scope.py (copied from wf022/zen c92340c)",
            {"repo_files": rec["repo_files"], "injection_lines": {k: (v["file"], v["line"]) for k, v in inj.items()}})

    contract = json.loads((HERE / "PILOT-CONTRACT-DRAFT.json").read_text(encoding="utf-8"))["contract"]
    contract["owner_request"] = {"quote": f"Repair the five documentation inconsistencies in the copied folder (job {JOB})",
                                 "source_turn": f"owner turn: pilot request ({SIM})"}
    contract["limits"] = "rehearsal WF-047: disposable local copy only; stance P-SUPERVISED; posture E1 (SIMULATED)"
    grant = json.loads((HERE / "PILOT-GRANT-DRAFT.json").read_text(encoding="utf-8"))["grant"]
    grant["grant_id"] = "G-pilot-docfix"
    grant["expiry"] = EXPIRY
    grant["ars"]["replacement"]["expiry"] = EXPIRY
    (root / "build" / "contract.json").write_text(json.dumps(contract, indent=1), encoding="utf-8")
    (root / "build" / "grant.json").write_text(json.dumps(grant, indent=1), encoding="utf-8")

    # ---------------------------------------------------------------- A: create, grant, bind
    rh.call("A-fix1", "coordinator", C, "status", JOB, with_db=False, expect=("code", 2, "usage"),
            note="FIX1 I3: no --db and no WF_DB -> refused before anything is read or written")
    new = rh.call("A-create", "coordinator", C, "job", "new", JOB, "--contract", str(root / "build" / "contract.json"),
                  owner_turn=f"OT1 contract: the owner's request and acceptance list ({SIM})")
    rh.note("A-fix1", "FIX1 I4 overlap warning on the draft scopes", {"warnings_key_present": "warnings" in new,
                                                                      "warnings": new.get("warnings")})
    prop = rh.call("A-grant", "coordinator", C, "grant", "propose", JOB, "--grant", str(root / "build" / "grant.json"))
    h = str(prop.get("block_hash", ""))
    (root / "build" / "grant-block.txt").write_text(str(prop.get("block_text", "")), encoding="utf-8")
    quote = f"approve {grant['grant_id']} {h[:8]}"
    rh.call("A-grant", "coordinator", C, "grant", "activate", JOB, "--owner-hash", h[:8], "--quote", quote, "--turn",
            f"owner turn: grant confirmation for {JOB} ({SIM})",
            owner_turn=f"OT2 grant confirmation line: '{quote}' ({SIM})")
    rh.call("A-bind", "coordinator", C, "bind", JOB, "--bind-role", "coordinator", "--bind-session", C,
            "--bind-generation", "1", "--path", "scope/**")
    rh.call("A-bind", "coordinator", C, "bind", JOB, "--bind-role", "builder", "--bind-session", B1,
            "--bind-generation", "1", "--tool", "Write", "--tool", "Edit", "--path", "scope/**")
    rh.call("A-bind", "coordinator", C, "status", JOB)

    # ---------------------------------------------------------------- B: repair I1
    rh.repair("B-repair", B1, 1, inj["I1"])

    # ---------------------------------------------------------------- C: interruption 1 (+ S1 clash probe)
    i2_before, i2_after = rh.kill_during_receipt("C-kill-1", B1, 1, inj["I2"])
    rh.note("C-kill-1", "after kill 1", {"markers": [m.name for m in rh.markers()]})
    rh.call("C-fix1", "coordinator", C, "job", "new", JOB.upper(), "--contract", str(root / "build" / "contract.json"),
            expect=("rejected", "malformed"),
            note="FIX1 I1 + review S1: a case-variant id is refused rejected(malformed) even while the clashing job "
                 "has a committing marker, and that marker is left in place")
    rh.note("C-fix1", "marker still present after the clash probe", {"markers": [m.name for m in rh.markers()]})

    # ---------------------------------------------------------------- D: recovery 1
    rh.recovery("D-recover-1", "1")
    rep1 = rh.call("D-recover-1", "coordinator", C, "reconcile", JOB,
                   note="ordinary reconcile after recover close (WF022-F7 reading a: role-level gaps live here)")

    # ---------------------------------------------------------------- E: replacement (gap a) + second triggers (T16)
    stop = {"role": "builder", "session": B1, "generation": 1, "how": "process killed at the staged step (rehearsal)",
            "when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "owner_check": "owner confirmed no child process and no outstanding operation (SIMULATED)",
            "owner_turn": f"owner turn: stop confirmed for {JOB} ({SIM})"}
    rh.call("E-replace", "coordinator", C, "append", JOB, "--type", "stop-confirmed", "--content", json.dumps(stop),
            owner_turn=f"(e) stop-confirmed: no child process / outstanding operation ({SIM})")
    rh.call("E-replace", "builder", B1, "append", JOB, "--type", "blocker", "--content", '{"reason":"after stop"}',
            expect=("rejected", "stopped-session"), note="the stopped builder can no longer write")
    rep2 = rh.call("E-replace", "coordinator", C, "reconcile", JOB, note="post-stop reconcile report (needed by decide)")
    rh.call("E-replace", "coordinator", C, "recover", "decide", JOB, "--decision", "resume", "--turn",
            f"owner: resume {JOB} with a replacement builder ({SIM})",
            owner_turn=f"recovery decision 'resume' ({SIM})")
    rh.call("E-replace", "coordinator", C, "bind", JOB, "--bind-role", "builder", "--bind-session", B2,
            "--bind-generation", "2", "--tool", "Write", "--tool", "Edit", "--path", "scope/**")
    rh.call("E-T16", "coordinator", C, "bind", JOB, "--bind-role", "builder", "--bind-session", B3,
            "--bind-generation", "2", "--tool", "Write", "--tool", "Edit", "--path", "scope/**",
            expect=("rejected", "bound"), note="T16: a second replacement at the same generation is refused")
    rh.call("E-T16", "coordinator", C, "bind", JOB, "--bind-role", "builder", "--bind-session", B3,
            "--bind-generation", "3", "--tool", "Write", "--tool", "Edit", "--path", "scope/**",
            expect=("rejected", "termination-unconfirmed"),
            note="T16: a further replacement without a new stop-confirmed is refused (one generation bump)")
    rh.call("E-replace", "builder", B1, "append", JOB, "--type", "blocker", "--content", '{"reason":"stale"}',
            expect=("rejected", "stale"), note="T04: the replaced session is stale")
    ids = [e["id"] for e in rep2.get("exceptions", [])]
    rh.call("E-replace", "builder", B2, "ack-reconcile", JOB, "--report", str(rep2.get("seq")), "--exceptions",
            json.dumps(ids), "--note", "X: the I2 edit is on disk (sha matches the intent's target); the receipt was "
            "lost in the killed append", gen=2,
            note="D-X1 judgement (who decides is an owner choice, OWNER-DECISIONS line 5)")
    rh.call("E-replace", "builder", B2, "append", JOB, "--type", "receipt", "--content",
            rh.receipt_content(inj["I2"], i2_before, i2_after, "I2 receipt re-recorded by the replacement builder"),
            gen=2)

    # ---------------------------------------------------------------- F: repair I3 (B2)
    rh.repair("F-repair", B2, 2, inj["I3"])

    # ---------------------------------------------------------------- G: interruption 2 (gap b)
    i4_before, i4_after = rh.kill_during_receipt("G-kill-2", B2, 2, inj["I4"])
    rh.recovery("G-recover-2", "2")
    rep3 = rh.call("G-recover-2", "coordinator", C, "reconcile", JOB)
    ids3 = [e["id"] for e in rep3.get("exceptions", [])]
    rh.call("G-recover-2", "builder", B2, "ack-reconcile", JOB, "--report", str(rep3.get("seq")), "--exceptions",
            json.dumps(ids3), "--note", "X: the I4 edit is on disk; the receipt was lost in the second killed append",
            gen=2)
    rh.call("G-recover-2", "builder", B2, "append", JOB, "--type", "receipt", "--content",
            rh.receipt_content(inj["I4"], i4_before, i4_after, "I4 receipt re-recorded after the second recovery"),
            gen=2)

    # ---------------------------------------------------------------- H: finish, review, accept
    rh.repair("H-repair", B2, 2, inj["I5"])
    rh.call("H-deliver", "builder", B2, "append", JOB, "--type", "delivered", "--content",
            json.dumps({"claims": [{"claim": "I1-I5 restored after two interrupted appends and one replacement; scope "
                                             "equals pristine", "evidence": ["check-acceptance.ps1", "ledger receipts"]}]}),
            gen=2)
    rh.call("H-review", "coordinator", C, "bind", JOB, "--bind-role", "reviewer", "--bind-session", R1,
            "--bind-generation", "1", "--tool", "Read", "--path", "scope/**", "--path", "pristine/**")
    chk = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                          str(HERE / "check-acceptance.ps1"), "-Root", str(root)], capture_output=True, text=True)
    checks = dict(line.split(" ", 1) for line in chk.stdout.splitlines() if line.startswith("AC"))
    rh.note("H-review", "check-acceptance.ps1 (reviewer)", {"exit": chk.returncode, **checks})
    results = {"criteria": [{"id": f"AC{i}", "pass": checks.get(f"AC{i}", "").strip() == "True" if i < 7 else True,
                             "actual": checks.get(f"AC{i}", "see accept changeset").strip()} for i in range(1, 8)],
               "changed_paths": sorted({i["file"] for i in rec["injections"]}),
               "environment": "disposable local scope", "limits": contract["limits"],
               "contract_sha256": sha256_obj(contract), "baseline": contract["baseline"]}
    (root / "build" / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    rh.call("H-accept", "reviewer", R1, "accept", JOB, "--results", str(root / "build" / "results.json"))

    # ---------------------------------------------------------------- I: checkpoint (gap c), close
    cp = rh.call("I-checkpoint", "coordinator", C, "checkpoint", JOB,
                 owner_turn=f"OT3 the owner pastes '{JOB} <seq>:<self>' as the retained checkpoint ({SIM})")
    cpv = f"{cp.get('seq')}:{cp.get('self')}"
    rh.call("I-checkpoint", "coordinator", C, "verify", JOB, "--checkpoint", cpv,
            note="retained checkpoint matches the chain")
    bad = f"{cp.get('seq')}:{'0' * 64}"
    rh.call("I-checkpoint", "coordinator", C, "verify", JOB, "--checkpoint", bad,
            note="an altered retained value is reported as a mismatch (clean false)")
    rh.call("I-close", "coordinator", C, "close", JOB, "--intervention",
            "owner-present recovery twice (resolve, recover begin/reconcile/close) and one replacement; minutes unknown "
            "(rehearsal, SIMULATED)", owner_turn=f"OT4 intervention declaration, minutes unknown ({SIM})")
    rh.call("I-close", "coordinator", C, "export", JOB)
    fin = rh.call("I-close", "coordinator", C, "checkpoint", JOB,
                  owner_turn=f"OT3' final retained checkpoint after close ({SIM})")
    rh.call("I-close", "coordinator", C, "verify", JOB, "--checkpoint", f"{fin.get('seq')}:{fin.get('self')}")
    rh.call("I-close", "coordinator", C, "digest", JOB, owner_turn=f"OT5 the owner reads the digest ({SIM})")

    # every logged wf call carried --db except the one deliberate I3 probe
    calls = [json.loads(line) for line in rh.log.read_text(encoding="utf-8").splitlines()]
    no_db = [c["n"] for c in calls if c["role"] != "harness" and not c.get("with_db", True)]
    rh.note("end", "summary", {"calls": sum(1 for c in calls if c["role"] != "harness"), "without_db": no_db,
                               "unexpected": [(u["n"], u["phase"], u["argv"][:3], u["exit"], u["summary"].get("error"))
                                              for u in rh.unexpected],
                               "scope_equals_pristine": all(sha_file(root / "scope" / n) == sha_file(root / "pristine" / n)
                                                            for n in ("README.md", "PROTOCOL.md", "RESTORE-RUNBOOK.md"))})
    return 0 if not rh.unexpected else 3


if __name__ == "__main__":
    sys.exit(main())
