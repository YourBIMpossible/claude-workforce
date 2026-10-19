"""WF-022 step 2: build the disposable pilot scope (documentation-consistency repair, FIRST-RELEASE-SPEC section 12 (b)).

Copies three repository documents into <root>/pristine (reference, never edited) and <root>/scope (the job's write
scope), then injects five deliberate inconsistencies into the scope copy. Every injection is an exact, single
occurrence replacement, so the repair target is unambiguous and the repaired files must equal the pristine copies byte
for byte. Run: python -I -B make_scope.py <repo root> <root>   (refuses if <root> already exists).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

FILES = {  # scope name -> repository path
    "README.md": "tools/wf/README.md",
    "PROTOCOL.md": "protocol/PROTOCOL.md",
    "RESTORE-RUNBOOK.md": "tools/wf/docs/RESTORE-RUNBOOK.md",
}
# id, scope file, kind, exact text in the pristine file, injected text
INJECTIONS = [
    ("I1", "README.md", "wrong file name", "`wfcore\\db.py`", "`wfcore\\dbase.py`"),
    ("I2", "README.md", "wrong command name", "| `resolve <job> <seq>", "| `settle <job> <seq>"),
    ("I3", "PROTOCOL.md", "stale heading reference", 'README, "Attempt ids and pending-write\n  markers"',
     'README, "Attempt identifiers and pending\n  markers"'),
    ("I4", "RESTORE-RUNBOOK.md", "wrong option name", "verify --restored <restored copy>", "verify --restore <restored copy>"),
    ("I5", "PROTOCOL.md", "wrong file name", "`tools/wf/README.md`", "`tools/wf/READ-ME.md`"),
]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    repo, root = Path(sys.argv[1]), Path(sys.argv[2])
    if root.exists():
        raise SystemExit(f"refused: {root} exists (build once; never overwritten)")
    pristine, scope = root / "pristine", root / "scope"
    pristine.mkdir(parents=True)
    scope.mkdir()
    record: dict = {"repo_files": {}, "pristine": {}, "injections": [], "scope_after_injection": {}}
    for name, rel in FILES.items():
        src = repo / rel
        record["repo_files"][rel] = sha(src)
        shutil.copyfile(src, pristine / name)
        shutil.copyfile(src, scope / name)
        record["pristine"][name] = sha(pristine / name)
    for iid, name, kind, find, repl in INJECTIONS:
        p = scope / name
        text = p.read_bytes().decode("utf-8")
        nl = "\r\n" if "\r\n" in text else "\n"
        f, r = find.replace("\n", nl), repl.replace("\n", nl)
        n = text.count(f)
        if n != 1:
            raise SystemExit(f"{iid}: anchor matched {n} times in {name} (must be exactly 1)")
        if text.count(r) != 0:
            raise SystemExit(f"{iid}: injected text already present in {name}")
        p.write_bytes(text.replace(f, r).encode("utf-8"))
        line = text[: text.index(f)].count(nl) + 1
        record["injections"].append({"id": iid, "file": f"scope/{name}", "line": line, "kind": kind,
                                     "correct": find, "injected": repl})
    for name in FILES:
        record["scope_after_injection"][name] = sha(scope / name)
    (root / "scope-record.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    print(json.dumps(record, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
