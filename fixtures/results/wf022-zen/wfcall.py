"""WF-022: one logged wf call (same log format as run_flow.py). Used for the step-4 recovery commands, which are decided
one at a time from what the previous output shows.
Run: python -I -B wfcall.py <repo> <boundary root> <phase> <role> <session> [--owner-turn TEXT] [--note TEXT] -- <wf args...>
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    argv = sys.argv[1:]
    sep = argv.index("--")
    head, wf_args = argv[:sep], argv[sep + 1:]
    repo, root, phase, role, session = Path(head[0]), Path(head[1]), head[2], head[3], head[4]
    opts = dict(zip(head[5::2], head[6::2]))
    cmd = [sys.executable, "-I", "-B", str(repo / "tools" / "wf" / "wf.py"), "--db", str(root / "ledger" / "wf.db"),
           "--role", role, "--session", session, "--json", *wf_args]
    t0 = time.monotonic()
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(root), timeout=300)
    secs = round(time.monotonic() - t0, 3)
    try:
        res = json.loads(p.stdout) if p.stdout.strip() else {}
    except ValueError:
        res = {"raw": p.stdout[:300]}
    keys = ("ok", "outcome", "code", "decision", "seq", "clean", "error", "exceptions", "pending_markers", "axes")
    summary = {k: res[k] for k in keys if k in res}
    entry = {"phase": phase, "role": role, "session": session, "argv": ["wf", *wf_args], "exit": p.returncode,
             "summary": summary, "seconds": secs, "owner_turn": opts.get("--owner-turn"), "note": opts.get("--note", ""),
             "stderr_tail": p.stderr.strip()[-300:]}
    with (root / "flow-log.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    print(json.dumps({"exit": p.returncode, "seconds": secs, **summary}, default=str)[:1500])
    out = root / "build" / f"out-{phase}-{wf_args[0]}-{int(time.time() * 1000)}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
