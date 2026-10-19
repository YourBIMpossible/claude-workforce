"""WF-047 (Plexy): extended DRY rehearsal of the slice-6 pilot flow on the FIX1 head (dfd6e85). NOT a pilot.

Disposable fixture only: the boundary root is a new folder under <repo>/fixtures/tmp (the only place the test fault seam
`WF_TEST_FAULTS` is honoured); no gate, no profile change, no real ledger. Every OWNER TURN is SIMULATED by the agent.

One job, `pilot-docfix-1`, built from the WF-044 drafts (PILOT-CONTRACT-DRAFT.json / PILOT-GRANT-DRAFT.json, inner
objects, placeholders filled with SIMULATED values) on the WF-022 scope (make_scope.py, copied from wf022/zen c92340c):
  A  create, grant, bind (coordinator with explicit --path, builder B1)            -- WF-022 flow, FIX1 checks (I3, I4, I1)
  B  repair I1
  C  interruption 1: kill during the I2 receipt append (fault delay-precommit), then the S1 clash probe
  D  recovery 1: status, probe, stop-confirmed B1 refused while the write is uncertain, verify, resolve, verify,
     stop-confirmed B1 at the earliest accepted point (after resolve, before recover begin; WF-049 L49-S1),
     recover begin (+ second begin: T16), recover reconcile/close, retained checkpoint, ONE post-stop reconcile
  E  replacement (WF-044 gap a): recover decide resume citing that report, bind B2 at generation 2,
     second replacement triggers (T16; WF-044 gap b), stale B1, B2 ack-reconcile, B2 re-records the I2 receipt
  F  repair I3 (B2)
  G  second interruption (extra coverage): kill during the I4 receipt append by B2; recovery 2 as in D, with
     stop-confirmed refused during the open recovery; retained checkpoint; B2 acks and re-records
  H  repair I5, delivered; replacement max (WF-044 gap b, L49-S3): stop B2, reconcile, decide resume, bind B3 at
     generation 3 refused by `replacement.max`; reviewer bind, acceptance checks (AC1..AC6 complete or stop), accept
  I  checkpoint (WF-044 gap c): retained checkpoint before close, altered value (mismatch), close, export, final
     checkpoint after close, re-verify EVERY retained checkpoint (L49-S4), digest; then, on a copy of the root, the
     deleted-last-row case: plain verify stays clean, verify --checkpoint reports `missing`

Every call is one `python -I -B tools/wf/wf.py --db <root>/ledger/wf.db --role R --session S [--generation N] --json ...`
process, logged to <root>/flow-log.jsonl with its expectation: the exit code and refusal code, plus the result fields
the runbook relies on (`clean`, `exceptions`, `decision`, `outcome`, `checkpoint.status`, ...; WF-049 L49-S2). Any call
whose result differs from its expectation is listed as UNEXPECTED (a finding).
Run: python -I -B rehearse_pilot.py <repo root> <root under fixtures/tmp>   (the root and <root>-x4b must not exist)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
JOB = "pilot-docfix-1"
C, B1, B2, B3, R1 = "C-pilot", "B1-pilot", "B2-pilot", "B3-pilot", "R1-pilot"
SIM = "SIMULATED by the agent in rehearsal WF-047; in the pilot the owner types this"
EXPIRY = "2026-12-31T00:00:00Z"
CHECKS_PS1 = HERE / "check-acceptance.ps1"   # beside this driver, not under the repo argument (WF-040 L40-S1)
AC_IDS = [f"AC{i}" for i in range(1, 7)]
MISSING = object()


@dataclass(frozen=True)
class Contains:
    """Field expectation: the field is a string containing `text`."""
    text: str

    def __repr__(self) -> str:
        return f"~{self.text!r}"


def sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def field(res: dict, dotted: str) -> object:
    cur: object = res
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return MISSING
        cur = cur[part]
    return cur


def field_failures(res: dict, fields: dict[str, object]) -> list[str]:
    out = []
    for name, want in fields.items():
        got = field(res, name)
        ok = (isinstance(got, str) and want.text in got) if isinstance(want, Contains) else (got is not MISSING and got == want)
        if not ok:
            out.append(f"{name}: expected {want!r}, got {'(absent)' if got is MISSING else repr(got)[:160]}")
    return out


def run_checks(root: Path) -> tuple[int, dict[str, str]]:
    """Run check-acceptance.ps1 on `root` and return its AC1..AC6 verdicts; stop the run when it did not really run
    (WF-040 L40-S1 / WF-049 L49-S2: a failed check run would otherwise make `accept` record `rejected`, exit 0)."""
    if not CHECKS_PS1.is_file():
        raise SystemExit(f"check-acceptance.ps1 not found beside the driver: {CHECKS_PS1}")
    chk = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(CHECKS_PS1),
                          "-Root", str(root)], capture_output=True, text=True, timeout=120)
    checks: dict[str, str] = {}
    for line in chk.stdout.splitlines():
        parts = line.strip().split(" ")
        if len(parts) == 2 and parts[0] in AC_IDS and parts[1] in ("True", "False"):
            checks[parts[0]] = parts[1]
    if chk.returncode not in (0, 1) or sorted(checks) != sorted(AC_IDS):
        raise SystemExit(f"check-acceptance.ps1 did not report AC1..AC6 (exit {chk.returncode}): "
                         f"stdout {chk.stdout[-300:]!r} stderr {chk.stderr[-300:]!r}")
    return chk.returncode, checks


class Rehearsal:
    def __init__(self, repo: Path, root: Path) -> None:
        self.repo, self.root = repo, root
        self.db = root / "ledger" / "wf.db"
        self.log = root / "flow-log.jsonl"
        self.n = 0
        self.unexpected: list[dict] = []
        self.field_checked = 0
        self.retained: list[tuple[str, str]] = []
        sys.path.insert(0, str(repo / "tools" / "wf"))

    # ------------------------------------------------------------------ one logged call
    def call(self, phase: str, role: str, session: str, *args: str, expect: tuple = ("ok",), gen: int = 1,
             fields: dict[str, object] | None = None, db: Path | None = None, env_extra: dict | None = None,
             with_db: bool = True, owner_turn: str | None = None, note: str = "") -> dict:
        cmd = [sys.executable, "-I", "-B", str(self.repo / "tools" / "wf" / "wf.py")]
        if with_db:
            cmd += ["--db", str(db or self.db)]
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
        fails = field_failures(res, fields or {})
        if fields:
            self.field_checked += 1
            why += "; " + "; ".join(f"{k}={v!r}" for k, v in fields.items())
        return self._record(phase, role, session, gen, args, p.returncode, res, secs, why, ok and not fails, fails,
                            with_db, owner_turn, note, p.stderr, db)

    @staticmethod
    def _matches(code: int, res: dict, expect: tuple) -> tuple[bool, str]:
        kind = expect[0]
        err = str(res.get("error", ""))
        if kind == "ok":
            return code == 0 and res.get("ok") is not False, "exit 0"
        if kind == "rejected":
            return (code == 1 and res.get("code") == "rejected" and f"rejected({expect[1]})" in err,
                    f"exit 1 rejected({expect[1]})")
        if kind == "code":
            return code == expect[1] and res.get("code") == expect[2], f"exit {expect[1]} code {expect[2]}"
        if kind == "refused":
            return code == 1 and res.get("code") == "refused" and expect[1] in err, f"exit 1 refused '{expect[1]}'"
        raise ValueError(kind)

    def _record(self, phase, role, session, gen, args, code, res, secs, why, ok, fails, with_db, owner_turn, note,
                stderr, db=None) -> dict:
        self.n += 1
        keys = ("ok", "outcome", "code", "decision", "seq", "report_seq", "stop_seq", "clean", "error", "exceptions",
                "pending_markers", "axes", "warnings", "binding", "self", "checkpoint", "conflict_outcome")
        summary = {k: res[k] for k in keys if k in res}
        entry = {"n": self.n, "phase": phase, "role": role, "session": session, "generation": gen,
                 "argv": ["wf", *args], "with_db": with_db, "db": "copy" if db is not None else "rehearsal",
                 "exit": code, "summary": summary, "seconds": secs, "expected": why, "as_expected": ok,
                 "field_failures": fails, "owner_turn": owner_turn, "note": note,
                 "stderr_tail": (stderr or "").strip()[-300:]}
        with self.log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
        out = self.root / "build" / f"out-{self.n:03d}-{args[0] if args else 'x'}.json"
        out.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
        flag = "" if ok else f"   <-- UNEXPECTED {fails}"
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
                         json.dumps({"path": inj["file"], "sha_before": sha_before, "note": f"repair {inj['id']}"}), gen=gen,
                         fields={"outcome": "COMMITTED"})

    def receipt_content(self, inj, before, after, note=""):
        return json.dumps({"path": inj["file"], "sha_before": before, "sha_after": after,
                           "note": note or f"repaired {inj['id']}"})

    def repair(self, phase, session, gen, inj):
        before = sha_file(self.root / inj["file"])
        self.intent(phase, session, gen, inj, before)
        b, a = self.edit(inj)
        self.call(phase, "builder", session, "append", JOB, "--type", "receipt", "--content",
                  self.receipt_content(inj, b, a), gen=gen, fields={"outcome": "COMMITTED"})

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
                     {"stdout": out.strip()[:200]}, secs, "killed after the committing marker appeared",
                     not exited_before and marker_at is not None, [], True, None,
                     f"staged kill: WF_TEST_FAULTS=delay-precommit:8000, marker after {marker_at}s, killed (TerminateProcess)",
                     err)
        return b, a

    def stop_content(self, session: str, gen: int, how: str) -> str:
        return json.dumps({"role": "builder", "session": session, "generation": gen, "how": how,
                           "when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           "owner_check": "owner confirmed no child process and no outstanding operation (SIMULATED)",
                           "owner_turn": f"owner turn: stop confirmed for {JOB} ({SIM})"})

    def recovery(self, phase: str, which: str, *, stop: str | None = None, stop_probe: str | None = None) -> dict:
        """status -> probe -> [stop refused] -> verify -> resolve -> verify -> [stop accepted] -> recover begin
        (+ second begin, ordinary reconcile, [stop refused]) -> recover reconcile/close.
        `stop`: the stop-confirmed content of the killed builder, recorded at the earliest accepted point (L49-S1).
        `stop_probe`: a stop-confirmed content tried while the recovery is open (expected refused)."""
        st = self.call(phase, "coordinator", C, "status", JOB)
        pm = st.get("pending_markers") or []
        self.call(phase, "builder", "PROBE", "append", JOB, "--type", "blocker", "--content", '{"reason":"probe"}',
                  expect=("code", 1, "uncertain-write-pending"), note="any append is refused while a write is uncertain")
        if stop is not None:
            self.call(phase, "coordinator", C, "append", JOB, "--type", "stop-confirmed", "--content", stop,
                      expect=("code", 1, "uncertain-write-pending"),
                      note="L49-S1 (Lazy X1): the owner's stop line cannot be recorded straight after the kill")
        self.call(phase, "coordinator", C, "verify", JOB, fields={"clean": False},
                  note="clean:false while the marker exists")
        m = pm[0] if pm else {}
        res = self.call(phase, "coordinator", C, "resolve", JOB, str(m.get("seq")), "--attempt", str(m.get("attempt_id")),
                        fields={"outcome": "NOT-COMMITTED"},
                        owner_turn=f"owner accepts COMMITTED or NOT-COMMITTED for the uncertain write ({SIM})")
        self.call(phase, "coordinator", C, "verify", JOB, fields={"clean": True})
        stop_seq = None
        if stop is not None:
            sres = self.call(phase, "coordinator", C, "append", JOB, "--type", "stop-confirmed", "--content", stop,
                             fields={"outcome": "COMMITTED"},
                             owner_turn=f"(e) stop-confirmed: no child process / outstanding operation ({SIM})",
                             note="L49-S1: earliest accepted point, after `wf resolve` and before `recover begin`")
            stop_seq = sres.get("seq")
        turn = f"owner turn for job {JOB}: recover {which} ({SIM})"
        opened = self.call(phase, "coordinator", C, "recover", "begin", JOB, "--reason",
                           f"staged kill during a receipt append ({which})", "--turn", turn, owner_turn=turn,
                           fields={"outcome": "COMMITTED", "exceptions": []})
        self.call(phase, "coordinator", C, "recover", "begin", JOB, "--reason", "second trigger for the same interruption",
                  "--turn", turn, expect=("rejected", "recovery-open"),
                  note="T16: a second recovery trigger for the same interruption is refused")
        self.call(phase, "coordinator", C, "reconcile", JOB, expect=("rejected", "recovery-open"),
                  note="ordinary reconcile is refused while the recovery is open (as WF-022 row 18)")
        if stop_probe is not None:
            self.call(phase, "coordinator", C, "append", JOB, "--type", "stop-confirmed", "--content", stop_probe,
                      expect=("rejected", "recovery-open"),
                      note="L49-S1 (Lazy X3): the owner's stop line cannot be recorded during an open recovery")
        self.call(phase, "coordinator", C, "recover", "reconcile", JOB, "--turn", turn, owner_turn=turn)
        self.call(phase, "coordinator", C, "recover", "close", JOB, "--turn", turn, owner_turn=turn,
                  fields={"outcome": "COMMITTED"})
        return {"resolve": res, "opened": opened, "stop_seq": stop_seq}

    def retain(self, phase: str, label: str) -> str:
        """G-28: take a checkpoint the owner retains, and verify it straight away."""
        cp = self.call(phase, "coordinator", C, "checkpoint", JOB,
                       owner_turn=f"retained checkpoint ({label}): the owner pastes '{JOB} <seq>:<self>' ({SIM})")
        value = f"{cp.get('seq')}:{cp.get('self')}"
        self.call(phase, "coordinator", C, "verify", JOB, "--checkpoint", value,
                  fields={"clean": True, "checkpoint.status": "match"}, note=f"retained checkpoint {label}")
        self.retained.append((label, value))
        return value


def main() -> int:
    repo, root = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    tmp = (repo / "fixtures" / "tmp").resolve()
    xroot = root.with_name(root.name + "-x4b")
    if os.path.commonpath([str(root), str(tmp)]) != str(tmp):
        raise SystemExit(f"refused: {root} is not under {tmp} (fixture-only rehearsal)")
    if root.exists() or xroot.exists():
        raise SystemExit(f"refused: {root} or {xroot} exists (one rehearsal per root)")
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
                  fields={"outcome": "COMMITTED", "seq": 1},
                  owner_turn=f"OT1 contract: the owner's request and acceptance list ({SIM})")
    rh.note("A-fix1", "FIX1 I4 overlap warning on the draft scopes", {"warnings_key_present": "warnings" in new,
                                                                      "warnings": new.get("warnings")})
    prop = rh.call("A-grant", "coordinator", C, "grant", "propose", JOB, "--grant", str(root / "build" / "grant.json"))
    h = str(prop.get("block_hash", ""))
    (root / "build" / "grant-block.txt").write_text(str(prop.get("block_text", "")), encoding="utf-8")
    quote = f"approve {grant['grant_id']} {h[:8]}"
    rh.call("A-grant", "coordinator", C, "grant", "activate", JOB, "--owner-hash", h[:8], "--quote", quote, "--turn",
            f"owner turn: grant confirmation for {JOB} ({SIM})", fields={"outcome": "COMMITTED"},
            owner_turn=f"OT2 grant confirmation line: '{quote}' ({SIM})")
    rh.call("A-bind", "coordinator", C, "bind", JOB, "--bind-role", "coordinator", "--bind-session", C,
            "--bind-generation", "1", "--path", "scope/**", fields={"binding.role": "coordinator"})
    rh.call("A-bind", "coordinator", C, "bind", JOB, "--bind-role", "builder", "--bind-session", B1,
            "--bind-generation", "1", "--tool", "Write", "--tool", "Edit", "--path", "scope/**",
            fields={"binding.session": B1, "binding.generation": 1})
    rh.call("A-bind", "coordinator", C, "status", JOB, fields={"pending_markers": []})

    # ---------------------------------------------------------------- B: repair I1
    rh.repair("B-repair", B1, 1, inj["I1"])

    # ---------------------------------------------------------------- C: interruption 1 (+ S1 clash probe)
    i2_before, i2_after = rh.kill_during_receipt("C-kill-1", B1, 1, inj["I2"])
    rh.note("C-kill-1", "after kill 1", {"markers": [m.name for m in rh.markers()]})
    rh.call("C-fix1", "coordinator", C, "job", "new", JOB.upper(), "--contract", str(root / "build" / "contract.json"),
            expect=("rejected", "malformed"), fields={"error": Contains("case-insensitively")},
            note="FIX1 I1 + review S1: a case-variant id is refused rejected(malformed) even while the clashing job "
                 "has a committing marker, and that marker is left in place")
    rh.note("C-fix1", "marker still present after the clash probe", {"markers": [m.name for m in rh.markers()]})

    # ---------------------------------------------------------------- D: recovery 1, stop B1 (L49-S1), one reconcile
    stop1 = rh.stop_content(B1, 1, "process killed at the staged step (rehearsal)")
    d = rh.recovery("D-recover-1", "1", stop=stop1)
    rh.retain("D-checkpoint", "after recovery 1")
    x1_i2 = [{"class": "attempt-without-completion", "id": "X1", "path": inj["I2"]["file"]}]
    rep1 = rh.call("D-recover-1", "coordinator", C, "reconcile", JOB, fields={"outcome": "COMMITTED", "exceptions": x1_i2},
                   note="the one post-stop reconcile; recover decide cites it (L49-S1: no pre-stop report)")

    # ---------------------------------------------------------------- E: replacement (gap a) + second triggers (T16)
    rh.call("E-replace", "builder", B1, "append", JOB, "--type", "blocker", "--content", '{"reason":"after stop"}',
            expect=("rejected", "stopped-session"), note="the stopped builder can no longer write")
    rh.call("E-replace", "coordinator", C, "recover", "decide", JOB, "--decision", "resume", "--turn",
            f"owner: resume {JOB} with a replacement builder ({SIM})",
            fields={"decision": "resume", "report_seq": rep1.get("seq"), "stop_seq": d["stop_seq"]},
            owner_turn=f"recovery decision 'resume' ({SIM})")
    rh.call("E-replace", "coordinator", C, "bind", JOB, "--bind-role", "builder", "--bind-session", B2,
            "--bind-generation", "2", "--tool", "Write", "--tool", "Edit", "--path", "scope/**",
            fields={"binding.session": B2, "binding.generation": 2})
    rh.call("E-T16", "coordinator", C, "bind", JOB, "--bind-role", "builder", "--bind-session", B3,
            "--bind-generation", "2", "--tool", "Write", "--tool", "Edit", "--path", "scope/**",
            expect=("rejected", "bound"), note="T16: a second replacement at the same generation is refused")
    rh.call("E-T16", "coordinator", C, "bind", JOB, "--bind-role", "builder", "--bind-session", B3,
            "--bind-generation", "3", "--tool", "Write", "--tool", "Edit", "--path", "scope/**",
            expect=("rejected", "termination-unconfirmed"),
            note="T16: a further replacement without a new stop-confirmed is refused (the stop check comes first, "
                 "bind.py:412; the max itself is reached in H-max)")
    rh.call("E-replace", "builder", B1, "append", JOB, "--type", "blocker", "--content", '{"reason":"stale"}',
            expect=("rejected", "stale"), note="T04: the replaced session is stale")
    ids = [e["id"] for e in rep1.get("exceptions", [])]
    rh.call("E-replace", "builder", B2, "ack-reconcile", JOB, "--report", str(rep1.get("seq")), "--exceptions",
            json.dumps(ids), "--note", "X: the I2 edit is on disk (sha matches the intent's target); the receipt was "
            "lost in the killed append", gen=2, fields={"outcome": "COMMITTED"},
            note="D-X1 judgement (who decides is an owner choice, OWNER-DECISIONS line 7)")
    rh.call("E-replace", "builder", B2, "append", JOB, "--type", "receipt", "--content",
            rh.receipt_content(inj["I2"], i2_before, i2_after, "I2 receipt re-recorded by the replacement builder"),
            gen=2, fields={"outcome": "COMMITTED"})

    # ---------------------------------------------------------------- F: repair I3 (B2)
    rh.repair("F-repair", B2, 2, inj["I3"])

    # ---------------------------------------------------------------- G: second interruption (extra coverage)
    i4_before, i4_after = rh.kill_during_receipt("G-kill-2", B2, 2, inj["I4"])
    rh.recovery("G-recover-2", "2", stop_probe=rh.stop_content(B2, 2, "probe during the open recovery (rehearsal)"))
    rh.retain("G-checkpoint", "after recovery 2")
    x1_i4 = [{"class": "attempt-without-completion", "id": "X1", "path": inj["I4"]["file"]}]
    rep3 = rh.call("G-recover-2", "coordinator", C, "reconcile", JOB, fields={"outcome": "COMMITTED", "exceptions": x1_i4})
    ids3 = [e["id"] for e in rep3.get("exceptions", [])]
    rh.call("G-recover-2", "builder", B2, "ack-reconcile", JOB, "--report", str(rep3.get("seq")), "--exceptions",
            json.dumps(ids3), "--note", "X: the I4 edit is on disk; the receipt was lost in the second killed append",
            gen=2, fields={"outcome": "COMMITTED"})
    rh.call("G-recover-2", "builder", B2, "append", JOB, "--type", "receipt", "--content",
            rh.receipt_content(inj["I4"], i4_before, i4_after, "I4 receipt re-recorded after the second recovery"),
            gen=2, fields={"outcome": "COMMITTED"})

    # ---------------------------------------------------------------- H: finish, replacement max, review, accept
    rh.repair("H-repair", B2, 2, inj["I5"])
    rh.call("H-deliver", "builder", B2, "append", JOB, "--type", "delivered", "--content",
            json.dumps({"claims": [{"claim": "I1-I5 restored after two interrupted appends and one replacement; scope "
                                             "equals pristine", "evidence": ["check-acceptance.ps1", "ledger receipts"]}]}),
            gen=2, fields={"outcome": "COMMITTED"})
    s2 = rh.call("H-max", "coordinator", C, "append", JOB, "--type", "stop-confirmed", "--content",
                 rh.stop_content(B2, 2, "owner stopped the replacement builder after delivery (rehearsal)"),
                 fields={"outcome": "COMMITTED"}, owner_turn=f"(e) stop-confirmed for B2 ({SIM})")
    rep4 = rh.call("H-max", "coordinator", C, "reconcile", JOB, fields={"outcome": "COMMITTED", "exceptions": []},
                   note="post-stop reconcile for B2")
    rh.call("H-max", "coordinator", C, "recover", "decide", JOB, "--decision", "resume", "--turn",
            f"owner: resume {JOB} with a second replacement builder ({SIM})",
            fields={"decision": "resume", "report_seq": rep4.get("seq"), "stop_seq": s2.get("seq")},
            owner_turn=f"recovery decision 'resume' for B2 ({SIM})")
    rh.call("H-max", "coordinator", C, "bind", JOB, "--bind-role", "builder", "--bind-session", B3,
            "--bind-generation", "3", "--tool", "Write", "--tool", "Edit", "--path", "scope/**",
            expect=("rejected", "delegation"),
            fields={"error": Contains("replacement count 1 for builder has reached max 1")},
            note="WF-044 gap b / L49-S3: with a stop, a post-stop report and a resume decision, the grant's "
                 "replacement.max (1) refuses a second replacement")
    rh.call("H-review", "coordinator", C, "bind", JOB, "--bind-role", "reviewer", "--bind-session", R1,
            "--bind-generation", "1", "--tool", "Read", "--path", "scope/**", "--path", "pristine/**",
            fields={"binding.role": "reviewer"})
    chk_exit, checks = run_checks(root)
    rh.note("H-review", "check-acceptance.ps1 (reviewer; AC1..AC6 complete or the run stops)", {"exit": chk_exit, **checks})
    results = {"criteria": [{"id": f"AC{i}", "pass": checks[f"AC{i}"] == "True" if i < 7 else True,
                             "actual": checks[f"AC{i}"] if i < 7 else "see accept changeset"} for i in range(1, 8)],
               "changed_paths": sorted({i["file"] for i in rec["injections"]}),
               "environment": "disposable local scope", "limits": contract["limits"],
               "contract_sha256": sha256_obj(contract), "baseline": contract["baseline"]}
    (root / "build" / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    rh.call("H-accept", "reviewer", R1, "accept", JOB, "--results", str(root / "build" / "results.json"),
            fields={"outcome": "COMMITTED", "decision": "accepted"})

    # ---------------------------------------------------------------- I: checkpoint (gap c), close, re-verify
    cpv = rh.retain("I-checkpoint", "before close")
    bad = f"{cpv.split(':', 1)[0]}:{'0' * 64}"
    rh.call("I-checkpoint", "coordinator", C, "verify", JOB, "--checkpoint", bad,
            fields={"clean": False, "checkpoint.status": "mismatch"},
            note="an altered retained value is reported as a mismatch (clean false) with exit 0")
    rh.call("I-close", "coordinator", C, "close", JOB, "--intervention",
            "owner-present recovery twice (resolve, recover begin/reconcile/close) and one replacement; minutes unknown "
            "(rehearsal, SIMULATED)", fields={"outcome": "COMMITTED"},
            owner_turn=f"OT4 intervention declaration, minutes unknown ({SIM})")
    rh.call("I-close", "coordinator", C, "export", JOB, fields={"code": "ok"})
    fin = rh.retain("I-close", "final, after close")
    for label, value in rh.retained:
        rh.call("I-reverify", "coordinator", C, "verify", JOB, "--checkpoint", value,
                fields={"clean": True, "checkpoint.status": "match"},
                note=f"G-28 at close: re-verify the retained checkpoint '{label}' against the closed chain (L49-S4)")
    rh.call("I-close", "coordinator", C, "digest", JOB, fields={"pending_markers": []},
            owner_turn=f"OT5 the owner reads the digest ({SIM})")

    # ---------------------------------------------------------------- I: deleted last row, on a copy (Lazy X4b)
    shutil.copytree(root, xroot)
    xdb = xroot / "ledger" / "wf.db"
    con = sqlite3.connect(str(xdb))
    try:
        trig = con.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='events_no_delete'").fetchone()[0]
        last = con.execute("SELECT MAX(seq) FROM events WHERE job=?", (JOB,)).fetchone()[0]
        con.execute("DROP TRIGGER events_no_delete")
        con.execute("DELETE FROM events WHERE job=? AND seq=?", (JOB, last))
        con.execute(trig)
        con.commit()
        restored = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name='events_no_delete'"
                               ).fetchone()[0] == 1
    finally:
        con.close()
    rh.note("I-missing", "copy of the root: delete trigger dropped, last row deleted, trigger restored (Lazy X4b)",
            {"copy": xroot.name, "deleted_seq": last, "final_checkpoint_seq": int(fin.split(":", 1)[0]),
             "trigger_restored": restored})
    rh.call("I-missing", "coordinator", C, "export", JOB, db=xdb, fields={"code": "ok"},
            note="export regenerated on the copy, so the export manifest matches the shortened chain")
    rh.call("I-missing", "coordinator", C, "verify", JOB, db=xdb, fields={"clean": True},
            note="documented limit: plain verify cannot see a deleted last row")
    rh.call("I-missing", "coordinator", C, "verify", JOB, "--checkpoint", fin, db=xdb,
            fields={"clean": False, "checkpoint.status": "missing"},
            note="the retained final checkpoint reports the lost tail")
    rh.call("I-missing", "coordinator", C, "verify", "--restored", str(xdb), db=xdb, fields={"clean": True},
            note="restored-copy check without a checkpoint: the same limit")
    rh.call("I-missing", "coordinator", C, "verify", "--restored", str(xdb), "--job", JOB, "--checkpoint", fin, db=xdb,
            fields={"clean": False, "checkpoint.status": "missing"},
            note="restored-copy check with the retained final checkpoint")

    # every logged wf call carried --db except the one deliberate I3 probe
    calls = [json.loads(line) for line in rh.log.read_text(encoding="utf-8").splitlines()]
    no_db = [c["n"] for c in calls if c["role"] != "harness" and not c.get("with_db", True)]
    rh.note("end", "summary", {"calls": sum(1 for c in calls if c["role"] != "harness"),
                               "calls_with_field_checks": rh.field_checked, "without_db": no_db,
                               "retained_checkpoints": [label for label, _ in rh.retained],
                               "unexpected": [(u["n"], u["phase"], u["argv"][:3], u["exit"], u["summary"].get("error"),
                                               u.get("field_failures")) for u in rh.unexpected],
                               "scope_equals_pristine": all(sha_file(root / "scope" / n) == sha_file(root / "pristine" / n)
                                                            for n in ("README.md", "PROTOCOL.md", "RESTORE-RUNBOOK.md"))})
    return 0 if not rh.unexpected else 3


if __name__ == "__main__":
    sys.exit(main())
