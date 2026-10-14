"""Path policy for `wf accept` (spec section 4.4, T35). Owned by lane B after the dispatch-ready seam commit.

Seam contract (frozen callers: commands.accept):
  classify(changed, contract) -> dict   keys "P1".."P5" (lists of the caller-supplied path strings) are REQUIRED;
                                        the dict is recorded verbatim in the accept event under "changed_paths",
                                        so a lane may add further keys (per-path results, resolution notes, the
                                        "outside-boundary changes are not detected" sentence) without a caller edit.
  violations(classes) -> list[str]      fully formed reason strings; a non-empty list rejects the acceptance.
                                        Today: P4 + P5 only, text unchanged from the pre-seam implementation.

Seam state (before lane B): behaviour identical to the pre-seam commands.classify_paths / _match. P2 and P3 changes
are NOT yet violations; no path resolution (case folding, "..", links, trailing dots, alternate streams,
unresolvable paths) is performed; presence-only paths are classified by glob only. These are lane B's deliverable
(spec line 310), not implemented here.
"""
from __future__ import annotations

import fnmatch

CLASSES = ("P1", "P2", "P3", "P4", "P5")


def _match(path: str, globs: list[str]) -> bool:
    p = path.replace("/", "\\").lower()
    for g in globs:
        gg = g.replace("/", "\\").lower()
        if p == gg or fnmatch.fnmatchcase(p, gg) or (gg.endswith("**") and p.startswith(gg[:-2])) \
                or p.startswith(gg.rstrip("*\\") + "\\"):
            return True
    return False


def classify(changed: list[str], contract: dict) -> dict[str, list[str]]:
    """Section 4.4 classes: P1 in write scope · P2 presence-only touched · P3 watch changed ·
    P4 outside every scope · P5 excluded path."""
    out: dict[str, list[str]] = {k: [] for k in CLASSES}
    for p in changed:
        if _match(p, contract.get("exclusions", [])):
            out["P5"].append(p)
        elif _match(p, contract.get("presence_only", [])):
            out["P2"].append(p)
        elif _match(p, contract.get("write_scope", [])):
            out["P1"].append(p)
        elif _match(p, contract.get("watch", [])):
            out["P3"].append(p)
        else:
            out["P4"].append(p)
    return out


def violations(classes: dict) -> list[str]:
    """Reason strings that reject the acceptance. Pre-lane-B: P4 and P5 paths only."""
    bad = list(classes.get("P4", [])) + list(classes.get("P5", []))
    if not bad:
        return []
    return ["changed paths outside scope (P4) or excluded (P5): " + ", ".join(bad)]
