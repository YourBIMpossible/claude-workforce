"""check_owned_paths.py — fail when a lane's branch touches paths outside its owned set (OWNERS.md).
Usage: python -I tools/check_owned_paths.py --lane A --base <ref> --head <ref> [--owners OWNERS.md]
Exit 0: every changed path is owned by the lane. Exit 1: violations listed. Exit 2: usage/parse error.
The first matching row in OWNERS.md wins, so specific rows must precede the `**` catch-all.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import subprocess
import sys


def parse_owners(path: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in open(path, encoding="utf-8"):
        if not line.startswith("|") or line.startswith("| Path") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 2:
            continue
        rows.append((cells[0].strip("`"), cells[1]))
    if not rows:
        raise ValueError("no owner rows parsed")
    return rows


def owner_of(path: str, rows: list[tuple[str, str]]) -> str:
    p = path.replace("\\", "/")
    for glob, owner in rows:
        if glob == "**" or p == glob or fnmatch.fnmatchcase(p, glob):
            return owner
    return "D"


def changed(base: str, head: str, cwd: str) -> list[str]:
    out = subprocess.run(["git", "diff", "--name-only", f"{base}...{head}"], cwd=cwd, capture_output=True,
                         text=True, check=True).stdout
    return [l.strip() for l in out.splitlines() if l.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--owners", default=None)
    a = ap.parse_args()
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()
    try:
        rows = parse_owners(a.owners or os.path.join(root, "OWNERS.md"))
        files = changed(a.base, a.head, root)
    except (ValueError, subprocess.CalledProcessError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    bad = [(f, owner_of(f, rows)) for f in files if owner_of(f, rows) != a.lane]
    for f in files:
        print(f"{'OK  ' if owner_of(f, rows) == a.lane else 'FAIL'} {f} (owner {owner_of(f, rows)})")
    print(f"{len(files)} changed path(s), {len(bad)} outside lane {a.lane}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
