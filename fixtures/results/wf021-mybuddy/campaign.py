"""WF-021 campaign harness (MyBuddy, Linux). Evidence only: drives the delivered CLI (`python -I tools/wf/wf.py`) against
fresh scratch ledgers under <repo>/fixtures/tmp/; never touches product code, a real ledger, settings or the network.

Subcommands (each writes one JSON evidence file to --out and prints a summary table):
  concurrency  T27 shape: W writer processes x S seconds on one job, R repetitions. Each writer loops
               `wf append j --type intent` (one fresh process per attempt, unique msg_id). Per run: attempts,
               committed, visible rejections by code, lost (reported committed but absent), duplicated (msg_id twice),
               unreported rows (present but the writer saw a failure), chain verdict from `wf verify`.
  interrupt    T28 shape: K SIGKILLs of a `wf append` process at seeded random points, split into three windows:
               "random" (uniform over the measured append duration, no fault), "in-txn" (fixture fault
               delay-precommit: the process sits inside BEGIN IMMEDIATE after INSERT + committing marker, killed at a
               random instant in that hold), "post-commit" (fault delay-postcommit: killed after COMMIT, before the
               marker delete / export refresh). After each kill: next-open outcome from the ledger itself
               (committed / rolled back / corrupt), pending markers, `wf verify` verdict; a left committing marker is
               then settled with `wf resolve` so the next kill starts from a clean state (outcome recorded).
  busy         Busy-handler give-up: a second connection holds BEGIN EXCLUSIVE; `wf append` is started and timed until
               it exits with lock-timeout. Records wall time (process start to exit) and the bound/elapsed the CLI
               itself reports. Nominal bound T_ACQ_BOUND = 5.0 s (wfcore/db.py).

The fault seam is honoured only for ledgers under fixtures/tmp (wfcore/faults.py); every scratch root is created there.
Stdlib only. Run from anywhere: python3 -I fixtures/results/wf021-mybuddy/campaign.py <subcommand> --out <file.json>
"""
from __future__ import annotations

import argparse
import json
import os
import random
import signal
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))  # fixtures/results/wf021-mybuddy -> repo root
WF_DIR = os.path.join(REPO, "tools", "wf")
WF = os.path.join(WF_DIR, "wf.py")
sys.path.insert(0, WF_DIR)
sys.path.insert(0, os.path.join(WF_DIR, "tests"))

INTENT = '{"path":"jobs/j/work/a.txt","sha_before":"","note":"%s"}'


# ---------------------------------------------------------------- fixture ledger (through the CLI)
def new_root(tag: str) -> str:
    root = os.path.join(REPO, "fixtures", "tmp", f"wf021-{tag}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:6]}")
    os.makedirs(root)
    return root


def wf(db: str, *argv: str, role: str = "builder", session: str = "B1", msg_id: str | None = None,
       faults: str | None = None, timeout: float = 120.0) -> tuple[int, dict, float]:
    env = {k: v for k, v in os.environ.items() if k != "WF_TEST_FAULTS"}
    if faults:
        env["WF_TEST_FAULTS"] = faults
    cmd = [sys.executable, "-I", "-B", WF, "--json", "--db", db, "--role", role, "--session", session]
    if msg_id:
        cmd += ["--msg-id", msg_id]
    t0 = time.monotonic()
    p = subprocess.run(cmd + list(argv), capture_output=True, text=True, timeout=timeout, env=env)
    dt = time.monotonic() - t0
    try:
        res = json.loads(p.stdout) if p.stdout.strip() else {}
    except ValueError:
        res = {"raw": p.stdout[-500:]}
    if p.returncode != 0 and p.stderr.strip():
        res.setdefault("_stderr", p.stderr[-500:])
    return p.returncode, res, dt


def make_job(root: str) -> str:
    from test_wf import CONTRACT, GRANT  # the suite's own fixture contract and grant
    db = os.path.join(root, "ledger", "l.db")
    cpath, gpath = os.path.join(root, "contract.json"), os.path.join(root, "grant.json")
    with open(cpath, "w", encoding="utf-8") as fh:
        json.dump(CONTRACT, fh)
    with open(gpath, "w", encoding="utf-8") as fh:
        json.dump(GRANT, fh)
    for argv in (("job", "new", "j", "--contract", cpath), ("grant", "propose", "j", "--grant", gpath)):
        c, r, _ = wf(db, *argv, role="coordinator", session="C1")
        assert c == 0, r
        h = r.get("block_hash")
    c, r, _ = wf(db, "grant", "activate", "j", "--owner-hash", h[:8], "--quote", f"approve G1 {h[:8]}", "--turn",
                 "owner turn: activate on job j", role="coordinator", session="C1")
    assert c == 0, r
    return db


def ledger_rows(db: str) -> list[dict]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    try:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute("SELECT job, seq, type, msg_id, prev, self FROM events WHERE job='j' ORDER BY seq")]
    finally:
        con.close()


def verify(db: str) -> dict:
    c, r, _ = wf(db, "verify", "j", role="coordinator", session="C1")
    jj = (r.get("jobs") or {}).get("j", {})
    return {"exit": c, "clean": r.get("clean"), "chain_problems": jj.get("problems"),
            "ledger_problems": r.get("ledger_problems"), "pending_markers": jj.get("pending_markers"),
            "export": jj.get("export")}


def pending_markers(db: str) -> list[dict]:
    from wfcore import markers
    return [{"seq": m.seq, "attempt_id": m.attempt_id} for m in markers.list_markers(db, "j")]


# ---------------------------------------------------------------- concurrency (step 4)
def writer(db: str, wid: int, seconds: float, out: list, stop_at: float) -> None:
    n = 0
    while time.monotonic() < stop_at:
        n += 1
        mid = f"w{wid}-{n}-{uuid.uuid4().hex[:8]}"
        c, r, dt = wf(db, "append", "j", "--type", "intent", "--content", INTENT % mid, msg_id=mid)
        out.append({"w": wid, "msg_id": mid, "exit": c, "outcome": r.get("outcome"), "code": r.get("code"),
                    "seq": r.get("seq"), "dt": round(dt, 4), "error": (r.get("error") or "")[:200]})


def run_concurrency(writers: int, seconds: float, reps: int) -> dict:
    runs = []
    for rep in range(1, reps + 1):
        root = new_root(f"conc{rep}")
        db = make_job(root)
        head0 = len(ledger_rows(db))
        attempts: list[dict] = []
        stop_at = time.monotonic() + seconds
        t0 = time.time()
        threads = [threading.Thread(target=writer, args=(db, w, seconds, attempts, stop_at)) for w in range(writers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        wall = time.time() - t0
        rows = ledger_rows(db)
        new = [r for r in rows if r["seq"] > head0]
        committed = {a["msg_id"]: a["seq"] for a in attempts if a["exit"] == 0 and a["outcome"] == "COMMITTED"}
        failed = [a for a in attempts if not (a["exit"] == 0 and a["outcome"] == "COMMITTED")]
        row_ids = [r["msg_id"] for r in new]
        dup = sorted({m for m in row_ids if row_ids.count(m) > 1})
        lost = sorted(m for m in committed if m not in row_ids)
        unreported = sorted(m for m in row_ids if m not in committed)
        seq_ok = [r["seq"] for r in rows] == list(range(1, len(rows) + 1))
        seq_match = all(any(r["msg_id"] == m and r["seq"] == s for r in new) for m, s in committed.items())
        codes: dict[str, int] = {}
        for a in failed:
            k = a["code"] or a["outcome"] or f"exit{a['exit']}"
            codes[k] = codes.get(k, 0) + 1
        v = verify(db)
        runs.append({"rep": rep, "root": root, "writers": writers, "seconds": seconds, "wall_s": round(wall, 1),
                     "attempts": len(attempts), "committed": len(committed), "rejections": len(failed),
                     "rejection_pct": round(100.0 * len(failed) / max(1, len(attempts)), 3), "rejections_by_code": codes,
                     "lost": len(lost), "duplicated": len(dup), "unreported_rows": len(unreported),
                     "unreported_ids": unreported[:20], "rows_added": len(new), "seq_consecutive": seq_ok,
                     "committed_seq_matches_row": seq_match, "verify": v,
                     "dt_median_s": round(statistics.median([a["dt"] for a in attempts]), 4) if attempts else None,
                     "failed_samples": failed[:10]})
    return {"campaign": "concurrency", "runs": runs}


# ---------------------------------------------------------------- interruption (step 5)
def classify_after_kill(db: str, before: list[dict], mid: str) -> dict:
    try:
        rows = ledger_rows(db)
    except sqlite3.DatabaseError as exc:
        return {"outcome": "corrupt", "detail": f"{type(exc).__name__}: {exc}"}
    added = [r for r in rows if r["seq"] > len(before)]
    if rows[: len(before)] != before:
        return {"outcome": "corrupt", "detail": "prefix of the chain changed"}
    if not added:
        return {"outcome": "rolled back"}
    if len(added) == 1 and added[0]["msg_id"] == mid:
        return {"outcome": "committed"}
    return {"outcome": "corrupt", "detail": f"unexpected rows {[(r['seq'], r['msg_id']) for r in added]}"}


def one_kill(db: str, window: str, rng: random.Random, typical_s: float) -> dict:
    before = ledger_rows(db)
    mid = f"k-{uuid.uuid4().hex[:10]}"
    faults = {"random": None, "in-txn": "delay-precommit:600", "post-commit": "delay-postcommit:600"}[window]
    env = {k: v for k, v in os.environ.items() if k != "WF_TEST_FAULTS"}
    if faults:
        env["WF_TEST_FAULTS"] = faults
    cmd = [sys.executable, "-I", "-B", WF, "--json", "--db", db, "--role", "builder", "--session", "B1",
           "--msg-id", mid, "append", "j", "--type", "intent", "--content", INTENT % mid]
    pdir = os.path.join(os.path.dirname(db), "pending", "j")
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    t0 = time.monotonic()
    if window == "random":
        delay = rng.uniform(0.0, typical_s)
        time.sleep(delay)
    else:
        # wait (bounded) for the committing marker: the process is then inside the transaction (in-txn) or about to
        # COMMIT; for post-commit also wait for the row to be visible (COMMIT done), then kill inside the hold
        deadline = t0 + 20.0
        while time.monotonic() < deadline and p.poll() is None:
            have_marker = os.path.isdir(pdir) and any(n.startswith(f"{len(before) + 1}-") and n.endswith(".json") for n in os.listdir(pdir))
            if window == "in-txn" and have_marker:
                break
            if window == "post-commit":
                try:
                    if len(ledger_rows(db)) > len(before):
                        break
                except sqlite3.OperationalError:
                    pass
            time.sleep(0.005)
        time.sleep(rng.uniform(0.0, 0.5))           # inside the 600 ms hold
        delay = time.monotonic() - t0
    exited_before_kill = p.poll() is not None
    if not exited_before_kill:
        p.send_signal(signal.SIGKILL)
    so, se = p.communicate(timeout=30)
    rec = {"window": window, "faults": faults, "kill_at_s": round(delay, 4), "exited_before_kill": exited_before_kill,
           "returncode": p.returncode, "msg_id": mid}
    rec.update(classify_after_kill(db, before, mid))
    rec["markers_after_kill"] = pending_markers(db)
    rec["verify_after_kill"] = verify(db)
    settled = []
    for m in rec["markers_after_kill"]:
        c, r, _ = wf(db, "resolve", "j", str(m["seq"]), "--attempt", m["attempt_id"], role="coordinator", session="C1")
        settled.append({"seq": m["seq"], "exit": c, "outcome": r.get("outcome"), "code": r.get("code")})
    rec["resolve"] = settled
    if settled:
        rec["outcome_after_resolve"] = classify_after_kill(db, before, mid)["outcome"]
        rec["verify_after_resolve"] = verify(db)
    return rec


def run_interrupt(kills: int, seed: int) -> dict:
    rng = random.Random(seed)
    root = new_root("intr")
    db = make_job(root)
    durations = [wf(db, "append", "j", "--type", "intent", "--content", INTENT % f"warm{i}", msg_id=f"warm{i}")[2]
                 for i in range(10)]
    typical = statistics.median(durations)
    windows = (["random"] * (kills // 3) + ["in-txn"] * (kills // 3)
               + ["post-commit"] * (kills - 2 * (kills // 3)))
    rng.shuffle(windows)
    recs = [dict(one_kill(db, w, rng, typical), n=i + 1) for i, w in enumerate(windows)]
    final = verify(db)
    rows = ledger_rows(db)
    return {"campaign": "interrupt", "root": root, "seed": seed, "warmup_append_s": [round(d, 4) for d in durations],
            "typical_append_s": round(typical, 4), "kills": recs, "final_verify": final, "final_rows": len(rows),
            "final_seq_consecutive": [r["seq"] for r in rows] == list(range(1, len(rows) + 1))}


# ---------------------------------------------------------------- busy handler (step 6)
def run_busy(trials: int) -> dict:
    root = new_root("busy")
    db = make_job(root)
    out = []
    for i in range(trials):
        hold = sqlite3.connect(db, isolation_level=None, timeout=10)
        hold.execute("BEGIN EXCLUSIVE")
        try:
            mid = f"busy{i}-{uuid.uuid4().hex[:6]}"
            t0 = time.monotonic()
            c, r, dt = wf(db, "append", "j", "--type", "intent", "--content", INTENT % mid, msg_id=mid)
            wall = time.monotonic() - t0
        finally:
            hold.execute("ROLLBACK")
            hold.close()
        out.append({"trial": i + 1, "exit": c, "code": r.get("code"), "bound_reported": r.get("bound"),
                    "elapsed_reported": r.get("elapsed"), "last_error": r.get("last_error"),
                    "process_wall_s": round(wall, 4), "error": (r.get("error") or "")[:200]})
    walls = [o["process_wall_s"] for o in out]
    el = [o["elapsed_reported"] for o in out if isinstance(o["elapsed_reported"], (int, float))]
    # process start-up baseline: an uncontended read-only command
    base = [wf(db, "status", "j", role="coordinator", session="C1")[2] for _ in range(5)]
    return {"campaign": "busy", "root": root, "nominal_bound_s": 5.0, "trials": out,
            "process_wall_s": {"min": min(walls), "median": statistics.median(walls), "max": max(walls)},
            "elapsed_reported_s": ({"min": min(el), "median": statistics.median(el), "max": max(el)} if el else None),
            "uncontended_status_wall_s": [round(b, 4) for b in base]}


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("concurrency"); c.add_argument("--writers", type=int, default=8)
    c.add_argument("--seconds", type=float, default=300.0); c.add_argument("--reps", type=int, default=3)
    k = sub.add_parser("interrupt"); k.add_argument("--kills", type=int, default=30); k.add_argument("--seed", type=int, default=21)
    b = sub.add_parser("busy"); b.add_argument("--trials", type=int, default=10)
    for p in (c, k, b):
        p.add_argument("--out", required=True)
    a = ap.parse_args()
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if a.cmd == "concurrency":
        res = run_concurrency(a.writers, a.seconds, a.reps)
    elif a.cmd == "interrupt":
        res = run_interrupt(a.kills, a.seed)
    else:
        res = run_busy(a.trials)
    head = subprocess.run(["git", "-C", REPO, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    res.update(started_utc=started, finished_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), head=head,
               python=sys.version.split()[0], sqlite=sqlite3.sqlite_version, platform=sys.platform, argv=sys.argv[1:])
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, default=str)
    print(json.dumps({k: v for k, v in res.items() if k not in ("runs", "kills", "trials")}, default=str)[:1500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
