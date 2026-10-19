"""WF-022: renders <root>/flow-log.jsonl as the markdown command table used in RUNBOOK-EXECUTED.md.
Run: python -I -B render_table.py <root>   (prints the table; long --content bodies and --turn texts are shortened, <root> replaces the absolute root)."""
import json
import os
import sys

root = sys.argv[1]
rows = [json.loads(line) for line in open(os.path.join(root, "flow-log.jsonl"), encoding="utf-8")]
print("| # | Phase | Role / session | Command | Exit | Result | Seconds | Owner turn |")
print("|---|---|---|---|---|---|---|---|")
for i, r in enumerate(rows, 1):
    argv = r["argv"]
    cmd = " ".join(argv[1:]) if argv[0] == "wf" else " ".join(argv)
    cmd = cmd.replace(root, "<root>")
    k = cmd.find("--content ")
    if k >= 0:
        body = cmd[k + len("--content "):]
        cmd = cmd[:k] + "--content " + (body[:58] + "...}" if len(body) > 60 else body)
    t = cmd.find("--turn ")
    if t >= 0:
        cmd = cmd[:t] + '--turn "<owner turn text>"'
    cmd = cmd.replace("|", "/")
    s = r["summary"]
    if isinstance(s, dict):
        parts = [f"{key}={val}" for key, val in s.items() if key not in ("block_hash", "error")]
        if "block_hash" in s:
            parts.append(f"block_hash={s['block_hash'][:12]}...")
        if "error" in s:
            parts.append("error=" + str(s["error"])[:90])
        res = ", ".join(parts)
    else:
        res = str(s)
    ot = ("**OWNER TURN**: " + r["owner_turn"]) if r.get("owner_turn") else ""
    sec = "" if r.get("seconds") is None else r["seconds"]
    print(f"| {i} | {r['phase']} | {r['role']} / {r['session']} | `{cmd}` | {r['exit']} | {res.replace('|', '/')} | {sec} | {ot} |")
