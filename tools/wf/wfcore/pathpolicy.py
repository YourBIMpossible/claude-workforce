"""Path policy for `wf accept` (spec section 4.4, T35). Owned by lane B.

Seam contract (frozen callers: commands.accept; commands.classify_paths is an alias of classify):
  classify(changed, contract) -> dict   keys "P1".."P5" are REQUIRED and hold the caller-supplied path strings
                                        (verbatim, in input order). The dict is recorded verbatim in the accept
                                        event under "changed_paths"; the further keys below are the per-path
                                        results, the resolution notes and the stated limitations.
  violations(classes) -> list[str]      fully formed reason strings; a non-empty list rejects the acceptance.

Classes (section 4.4), decided on the RESOLVED comparison form of each path, first match wins:
  P5  contract `exclusions`                               -> violation ("excluded path")
  P2  contract `presence_only` and `read_scope`           -> violation (read-only: any declared change)
  P3  contract `watch`                                    -> violation unless separately authorized (see below)
  P1  contract `write_scope`                              -> allowed (add / modify / delete)
  P4  anything else inside the boundary                   -> violation ("outside every declared scope")
plus, placed in P5 because they cannot be placed inside the boundary:
  - a path that resolves outside the boundary (".." above the root)  -> "resolves outside boundary", violation
  - a path that cannot be resolved or is a prohibited form            -> violation (fail closed), never skipped

Change kind (added / modified / deleted) is NOT representable through this interface: `changed` is a flat list of
path strings with no kind. Every rule above holds for every kind, so no decision depends on it; the record says so.

P3 authorization: section 4.4 allows a watched path to change only when "the contract names the path as separately
authorized". No field of the existing contract schema (validate.CONTRACT_REQUIRED) or of the grant content
(resources are confined to the write scope) can express that, and this function receives no grant. The check
therefore fails closed: every P3 change is a violation, and `limitations` records that authorization cannot be
expressed. Worker-supplied results text is never authorization proof.

Resolution (pure string handling; no filesystem access, no file content read or hashed, presence-only paths never
read): separators to "\\", "." and empty components dropped, ".." collapsed, case folded for comparison, a drive
letter upper-cased in the reported form. Prohibited or unresolvable forms (fail closed): empty path, control
characters, wildcard / illegal characters, absolute, rooted, UNC or "\\\\?\\" paths (the scopes are relative to a
boundary root this function does not know), device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9, with or without an
extension), alternate-data-stream syntax (":" in a component), a component ending in "." or " " (the platform may
strip it; not resolved here), and 8.3 short-name forms ("~<digits>"; not resolvable without the filesystem).
NOT done here, and recorded as limitations: links and junctions are not resolved; whether NTFS strips trailing
dots/spaces or expands short names is not established by this code (lane D evidence, G-13 / G-29); outside-boundary
changes are not detected (section 4.3).
"""
from __future__ import annotations

import fnmatch
import re

CLASSES = ("P1", "P2", "P3", "P4", "P5")

#: Recorded verbatim in every classification (section 4.3 / 4.4 rule 5, T35).
NOT_DETECTED = "outside-boundary changes are not detected: the comparison covers the monitored boundary only"
LIMITATIONS: tuple[str, ...] = (
    NOT_DETECTED,
    "P3 authorization: the contract schema has no field naming a separately authorized watched path, so every "
    "P3 change is a violation (fail closed); worker-supplied results text is never authorization proof",
    "change kind (added / modified / deleted) is not carried by the changed-path list; every class rule applies to "
    "every kind",
    "resolution is string-only: links and junctions are not resolved; trailing dots or spaces and 8.3 short names "
    "are refused, not normalized (whether the platform resolves them is not established here); no file is read, "
    "hashed or stat-ed, presence-only paths included",
)

_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)})
_ILLEGAL = re.compile(r'[<>"|?*]')
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SHORT = re.compile(r"~\d+$")
_DRIVE = re.compile(r"^[A-Za-z]:")


class _Resolution:
    __slots__ = ("resolved", "cmp", "outside", "problem")

    def __init__(self, resolved: str | None, cmp: str | None, outside: bool, problem: str | None) -> None:
        self.resolved, self.cmp, self.outside, self.problem = resolved, cmp, outside, problem


def resolve(path: object) -> _Resolution:
    """Resolve one caller-supplied path string. `problem` set = unresolvable / prohibited (fail closed)."""
    if not isinstance(path, str):
        return _Resolution(None, None, False, "not a string")
    if path == "" or path.strip() == "":
        return _Resolution(None, None, False, "empty path")
    if _CONTROL.search(path):
        return _Resolution(None, None, False, "control character in path")
    p = path.replace("/", "\\")
    if p.startswith("\\\\"):
        return _Resolution(None, None, False, "UNC or device-namespace prefix cannot be related to the boundary")
    if _DRIVE.match(p):
        drive = p[0].upper() + ":"
        return _Resolution(drive + p[2:], None, False,
                           "absolute path cannot be related to the relative monitored boundary")
    if p.startswith("\\"):
        return _Resolution(None, None, False, "rooted path cannot be related to the relative monitored boundary")
    stack: list[str] = []
    depth = 0
    outside = False
    for comp in p.split("\\"):
        if comp in ("", "."):
            continue
        if comp == "..":
            if stack:
                stack.pop()
            else:
                depth -= 1
                outside = True
            continue
        if ":" in comp:
            return _Resolution(None, None, outside, "alternate data stream syntax")
        if _ILLEGAL.search(comp):
            return _Resolution(None, None, outside, "wildcard or illegal character")
        if comp[-1] in ". ":
            return _Resolution(None, None, outside, "trailing dot or space (not resolved by this check)")
        stem = comp.split(".", 1)[0].strip()
        if stem.upper() in _RESERVED:
            return _Resolution(None, None, outside, "device name")
        if _SHORT.search(stem) and len(stem) <= 8:
            return _Resolution(None, None, outside, "short (8.3) name cannot be resolved without the filesystem")
        stack.append(comp)
    if outside:
        shown = "\\".join([".."] * (-depth) + stack)
        return _Resolution(shown, None, True, None)
    if not stack:
        return _Resolution(None, None, False, "path resolves to the boundary root itself")
    resolved = "\\".join(stack)
    return _Resolution(resolved, resolved.lower(), False, None)


def _norm_glob(g: str) -> str:
    gg = g.replace("/", "\\").lower()
    parts = [c for c in gg.split("\\") if c not in ("", ".")]
    return "\\".join(parts)


def _match(cmp: str, globs: list[str]) -> bool:
    """Glob semantics unchanged from the pre-lane-B seam, applied to the resolved comparison form."""
    for g in globs:
        if not isinstance(g, str):
            continue
        gg = _norm_glob(g)
        if not gg:
            continue
        if cmp == gg or fnmatch.fnmatchcase(cmp, gg) or (gg.endswith("**") and cmp.startswith(gg[:-2])) \
                or cmp.startswith(gg.rstrip("*\\") + "\\"):
            return True
    return False


def _rule(cmp: str, contract: dict) -> tuple[str, str, str]:
    """(class, result, reason) for a resolved in-boundary path."""
    def scope(key: str) -> list[str]:
        v = contract.get(key, [])
        return v if isinstance(v, list) else []
    if _match(cmp, scope("exclusions")):
        return "P5", "violation", "excluded path (P5)"
    if _match(cmp, scope("presence_only")):
        return "P2", "violation", "presence-only read-only path changed (P2); never read"
    if _match(cmp, scope("read_scope")):
        return "P2", "violation", "read-only path changed (P2)"
    if _match(cmp, scope("watch")):
        return "P3", "violation", "watched configuration changed; no separate authorization can be expressed (P3)"
    if _match(cmp, scope("write_scope")):
        return "P1", "allowed", "in write scope (P1)"
    return "P4", "violation", "outside every declared scope (P4)"


def classify(changed: list[str], contract: dict) -> dict:
    """Section 4.4 classes over the resolved paths; P1..P5 hold the caller-supplied strings verbatim."""
    out: dict = {k: [] for k in CLASSES}
    results: list[dict] = []
    for p in changed:
        r = resolve(p)
        if r.problem is not None:
            cls, result, reason = "P5", "violation", f"unresolvable or prohibited path: {r.problem}"
        elif r.outside:
            cls, result, reason = "P5", "outside boundary (not detected)", "resolves outside boundary (P5)"
        else:
            cls, result, reason = _rule(r.cmp or "", contract)
        out[cls].append(p)
        results.append({"path": p, "resolved": r.resolved, "class": cls, "result": result, "reason": reason})
    out["results"] = results
    out["limitations"] = list(LIMITATIONS)
    out["comparison"] = "string resolution only; no path was read, hashed or stat-ed"
    return out


def violations(classes: dict) -> list[str]:
    """Reason strings that reject the acceptance; every string names the offending paths.

    With per-path `results` (a classify() output) the reasons are grouped by kind. Without them (a bare class dict)
    the rule is applied to the lists: P2, P3, P4 and P5 entries are violations, fail closed."""
    results = classes.get("results")
    if not isinstance(results, list):
        p2 = [str(x) for x in classes.get("P2", [])]
        p3 = [str(x) for x in classes.get("P3", [])]
        bad = [str(x) for x in classes.get("P4", [])] + [str(x) for x in classes.get("P5", [])]
        out: list[str] = []
        if p2:
            out.append("read-only paths changed (P2): " + ", ".join(p2))
        if p3:
            out.append("watched configuration changed without separate authorization (P3): " + ", ".join(p3))
        if bad:
            out.append("changed paths outside scope (P4) or excluded (P5): " + ", ".join(bad))
        return out
    p2: list[str] = []
    p3: list[str] = []
    scope_bad: list[str] = []
    escaped: list[str] = []
    unresolved: list[str] = []
    for r in results:
        if not isinstance(r, dict) or r.get("result") == "allowed":
            continue
        path, cls, reason = str(r.get("path")), r.get("class"), str(r.get("reason", ""))
        if reason.startswith("unresolvable"):
            unresolved.append(f"{path} ({reason.split(': ', 1)[-1]})")
        elif reason.startswith("resolves outside boundary"):
            escaped.append(path)
        elif cls == "P2":
            p2.append(path)
        elif cls == "P3":
            p3.append(path)
        elif cls in ("P4", "P5"):
            scope_bad.append(path)
        else:
            unresolved.append(f"{path} (unknown result {r.get('result')!r})")
    out = []
    if p2:
        out.append("read-only paths changed (P2): " + ", ".join(p2))
    if p3:
        out.append("watched configuration changed without separate authorization (P3; the contract cannot express "
                   "one, fail closed): " + ", ".join(p3))
    if scope_bad:
        out.append("changed paths outside scope (P4) or excluded (P5): " + ", ".join(scope_bad))
    if escaped:
        out.append("paths resolving outside the monitored boundary (P5, not in scope; outside-boundary changes are "
                   "not detected): " + ", ".join(escaped))
    if unresolved:
        out.append("unresolvable or prohibited paths (fail closed): " + ", ".join(unresolved))
    return out
