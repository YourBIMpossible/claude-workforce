"""WF-047: render one Markdown table per phase from a rehearsal flow-log.jsonl (values copied, not transcribed).
Run: python -I -B render_tables.py <flow-log.jsonl>   (prints Markdown to stdout)"""
from __future__ import annotations

import json
import sys

PHASE_TITLES = {
    "setup": "Setup", "A-fix1": "A. FIX1 checks", "A-create": "A. Create", "A-grant": "A. Grant", "A-bind": "A. Bind",
    "B-repair": "B. Repair I1", "C-kill-1": "C. Interruption 1", "C-fix1": "C. FIX1 S1 clash probe",
    "D-recover-1": "D. Recovery 1", "E-replace": "E. Replacement (WF-044 gap a)", "E-T16": "E. Second replacement triggers (T16)",
    "F-repair": "F. Repair I3", "G-kill-2": "G. Interruption 2 (WF-044 gap b)", "G-recover-2": "G. Recovery 2",
    "H-repair": "H. Repair I5", "H-deliver": "H. Deliver", "H-review": "H. Review", "H-accept": "H. Accept",
    "I-checkpoint": "I. Checkpoint (WF-044 gap c)", "I-close": "I. Close", "end": "End",
}


def cell(s: object, n: int = 150) -> str:
    t = str(s).replace("|", "\\|").replace("\n", " ")
    return t if len(t) <= n else t[: n - 3] + "..."


def main() -> int:
    rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]
    order: list[str] = []
    for r in rows:
        if r["phase"] not in order:
            order.append(r["phase"])
    for ph in order:
        print(f"\n### {PHASE_TITLES.get(ph, ph)}\n")
        print("| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |")
        print("|---|---|---|---|---|---|---|---|")
        for r in (x for x in rows if x["phase"] == ph):
            if r["role"] == "harness":
                print(f"| {r['n']} | harness | {cell(r['argv'][0], 80)} | — | {cell(json.dumps(r['summary'], default=str), 220)} | — | — | |")
                continue
            argv = " ".join(a if len(a) <= 50 else a[:47] + "..." for a in r["argv"])
            if not r.get("with_db", True):
                argv += " (no --db)"
            s = r["summary"]
            res = ", ".join(f"{k}={cell(v, 70)}" for k, v in s.items() if k not in ("axes", "pending_markers", "self"))
            if "pending_markers" in s:
                res += f", pending_markers={len(s['pending_markers'])}"
            if "axes" in s:
                res += f", execution={s['axes'].get('execution')}"
            mark = "yes" if r["as_expected"] else "**NO**"
            tail = r.get("owner_turn") or r.get("note") or ""
            ot = ("**OWNER TURN (SIMULATED)**: " + cell(r["owner_turn"], 110)) if r.get("owner_turn") else cell(tail, 110)
            print(f"| {r['n']} | {r['role']} / {r['session']} / g{r.get('generation', 1)} | `{cell(argv, 120)}` | "
                  f"{r['exit']} | {cell(res, 200)} | {r['expected']} → {mark} | {r['seconds']} | {ot} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
