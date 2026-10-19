"""WF-047: render one Markdown table per phase from a rehearsal flow-log.jsonl (values copied, not transcribed).
Run: python -I -B render_tables.py <flow-log.jsonl> [<out.md>]
Writes UTF-8 to <out.md>, or to stdout when it is omitted (stdout is reconfigured to UTF-8, so a redirect or pipe
on a cp1252 console works too; WF-049 L49-S6)."""
from __future__ import annotations

import json
import sys
from typing import TextIO

PHASE_TITLES = {
    "setup": "Setup", "A-fix1": "A. FIX1 checks", "A-create": "A. Create", "A-grant": "A. Grant", "A-bind": "A. Bind",
    "B-repair": "B. Repair I1", "C-kill-1": "C. Interruption 1", "C-fix1": "C. FIX1 S1 clash probe",
    "D-recover-1": "D. Recovery 1 (stop B1 after resolve, one post-stop reconcile)",
    "D-checkpoint": "D. Retained checkpoint after recovery 1",
    "E-replace": "E. Replacement (WF-044 gap a)", "E-T16": "E. Second replacement triggers (T16; WF-044 gap b)",
    "F-repair": "F. Repair I3", "G-kill-2": "G. Second interruption (extra coverage)",
    "G-recover-2": "G. Recovery 2 (extra coverage)", "G-checkpoint": "G. Retained checkpoint after recovery 2",
    "H-repair": "H. Repair I5", "H-deliver": "H. Deliver",
    "H-max": "H. Replacement max reached (WF-044 gap b)", "H-review": "H. Review", "H-accept": "H. Accept",
    "I-checkpoint": "I. Checkpoint (WF-044 gap c)", "I-close": "I. Close",
    "I-reverify": "I. Re-verify every retained checkpoint at close (G-28)",
    "I-missing": "I. Deleted last row, on a copy of the root", "end": "End",
}


def cell(s: object, n: int = 150) -> str:
    t = str(s).replace("|", "\\|").replace("\n", " ")
    return t if len(t) <= n else t[: n - 3] + "..."


def render(rows: list[dict], out: TextIO) -> None:
    order: list[str] = []
    for r in rows:
        if r["phase"] not in order:
            order.append(r["phase"])
    for ph in order:
        out.write(f"\n### {PHASE_TITLES.get(ph, ph)}\n\n")
        out.write("| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |\n")
        out.write("|---|---|---|---|---|---|---|---|\n")
        for r in (x for x in rows if x["phase"] == ph):
            if r["role"] == "harness":
                out.write(f"| {r['n']} | harness | {cell(r['argv'][0], 80)} | — | "
                          f"{cell(json.dumps(r['summary'], default=str), 220)} | — | — | |\n")
                continue
            argv = " ".join(a if len(a) <= 50 else a[:47] + "..." for a in r["argv"])
            if not r.get("with_db", True):
                argv += " (no --db)"
            if r.get("db") == "copy":
                argv += " (--db: the copy)"
            s = dict(r["summary"])
            cp = s.pop("checkpoint", None)
            res = ", ".join(f"{k}={cell(v, 70)}" for k, v in s.items() if k not in ("axes", "pending_markers", "self"))
            if isinstance(cp, dict):
                res += f", checkpoint.status={cp.get('status')} (seq {cp.get('seq')}, head {cp.get('head_seq')})"
            if "pending_markers" in s:
                res += f", pending_markers={len(s['pending_markers'])}"
            if "axes" in s:
                res += f", execution={s['axes'].get('execution')}"
            mark = "yes" if r["as_expected"] else "**NO** " + cell("; ".join(r.get("field_failures") or []), 120)
            ot = ("**OWNER TURN (SIMULATED)**: " + cell(r["owner_turn"], 110)) if r.get("owner_turn") \
                else cell(r.get("note") or "", 110)
            out.write(f"| {r['n']} | {r['role']} / {r['session']} / g{r.get('generation', 1)} | `{cell(argv, 120)}` | "
                      f"{r['exit']} | {cell(res, 220)} | {cell(r['expected'], 160)} → {mark} | {r['seconds']} | {ot} |\n")


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        return 2
    with open(sys.argv[1], encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh]
    if len(sys.argv) == 3:
        with open(sys.argv[2], "w", encoding="utf-8", newline="\n") as out:
            render(rows, out)
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        render(rows, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
