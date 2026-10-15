"""Path policy for `wf accept` (spec section 4.4, T35). Owned by lane B.

Seam contract (frozen callers: commands.accept; commands.classify_paths is an alias of classify):
  classify(changed, contract) -> dict   keys "P1".."P5" are REQUIRED and hold the caller-supplied path strings
                                        (verbatim, in input order). The dict is recorded verbatim in the accept
                                        event under "changed_paths"; the further keys below are the per-path
                                        results, the resolution notes and the stated limitations.
  violations(classes) -> list[str]      fully formed reason strings; a non-empty list rejects the acceptance.

Classes (section 4.4), decided on the RESOLVED comparison form of each path, first match wins:
  P5  contract `exclusions`                               -> violation ("excluded path")
  P4  the fixed coordination set (`coordination=` globs)  -> allowed, result "coordination (P4, verified by verify)"
  P2  contract `presence_only` and `read_scope`           -> violation (read-only: any declared change)
  P3  contract `watch`                                    -> violation unless separately authorized (see below)
  P1  contract `write_scope`                              -> allowed (add / modify / delete)
  unclassified  anything else inside the boundary         -> violation ("unclassified in-boundary path")
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

P4 coordination (owner grant 2026-10-15 "T35 REQUIREMENT REPAIRS A1-A3 + T28", A2; tests/test_t35_changeset.py): P4
is the spec's FIXED coordination set (section 4.4 P4 row: the ledger database and its journal, the pending-marker
directory, the job export directory, runtime/gate/gate.db, runtime/observe/<job>.log), built by
`coordination_globs(job, ledger_rel, db_name)` and passed by the caller as `classify(..., coordination=...)`. The
coordination set PRECEDES P2/P3 by the spec: the ledger database and its journal are "allowed to change" even when
the contract watches `ledger/**` (the test fixture does), so a coordination path is never a P3 violation. A path in
no class is "unclassified": a separate violation result ("unclassified in-boundary path", key `unclassified`),
distinct from P5 (outside the boundary / excluded), which is untouched. `coordination=None` (no caller set, e.g. the
`classify_paths` alias used without a ledger) yields no P4 at all: every former P4 fallthrough is "unclassified".
The closeout bundle named by the P4 row has no path defined in the code and is therefore not in the set (recorded).

Hardening (owner grant 2026-10-15, tests/test_pathpolicy_hardening.py): malformed input fails closed instead of
classifying something else or raising. A `changed` that is not a list (a string was iterated per character; None
raised) and a non-string entry become one P5 violation each, recorded under their repr. A contract that is not an
object, a scope that is not a list, a non-string glob, an empty glob or a glob that climbs above the root is listed
under `contract_problems` and makes every entry a P5 violation (before: such globs were skipped, so an exclusion or
watch written that way silently lost its protection). Globs now collapse ".." like paths. COM0/LPT0 and the
superscript COM¹²³ / LPT¹²³ are device names. `violations` rejects a non-object classification, malformed result
rows and any contract problem. Glob `*` and `**` semantics are unchanged (fnmatch; `*` crosses separators and
"work**" also matches "workspace"): a change there is a policy decision for White, not a hardening.
"""
from __future__ import annotations

import fnmatch
import re

CLASSES = ("P1", "P2", "P3", "P4", "P5")
UNCLASSIFIED = "unclassified"
COORDINATION_RESULT = "coordination (P4, verified by verify)"


def coordination_globs(job: str, ledger_rel: str, db_name: str) -> list[str]:
    """Spec 4.4 P4 row as globs relative to the boundary root: the ledger database and its rollback journal, the
    pending-marker directory of the job, the job export directory, the gate database and the observe log. No
    closeout bundle path exists in the code, so none is listed."""
    lr = ledger_rel.replace("\\", "/").strip("/")
    return [f"{lr}/{db_name}", f"{lr}/{db_name}-journal", f"{lr}/pending/{job}/**", f"jobs/{job}/export/**",
            "runtime/gate/gate.db", f"runtime/observe/{job}.log"]

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

#: Win32 reserved device names: COM0-9 / LPT0-9 and the superscript forms COM¹ COM² COM³ / LPT¹ LPT² LPT³ included
#: (hardening 2026-10-15: COM0, LPT0 and the superscripts classified as P1 before).
_DIGITS = "0123456789¹²³"
_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL"} | {f"COM{d}" for d in _DIGITS} | {f"LPT{d}" for d in _DIGITS})
_SCOPE_KEYS = ("exclusions", "presence_only", "read_scope", "watch", "write_scope")
_MALFORMED_LIST = "changed-path list is "
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


def _norm_glob(g: str) -> tuple[str | None, str | None]:
    """(comparison-form glob, problem). Same normalization as a path: separators, case, "." dropped, ".."
    collapsed. A glob that climbs above the root, or that is empty after normalization, is a problem (hardening
    2026-10-15: a ".." glob matched nothing, so an exclusion written with ".." was silently ineffective)."""
    parts: list[str] = []
    for c in g.replace("/", "\\").lower().split("\\"):
        if c in ("", "."):
            continue
        if c == "..":
            if not parts:
                return None, "climbs outside the boundary"
            parts.pop()
            continue
        parts.append(c)
    if not parts:
        return None, "matches nothing (empty after normalization)"
    return "\\".join(parts), None


def _scopes(contract: object) -> tuple[dict[str, list[str]], list[str]]:
    """Normalized scope globs per key and the contract problems that make the classification fail closed:
    the contract is not an object, a scope is not a list, an entry is not a string, a glob is empty or escapes."""
    problems: list[str] = []
    scopes: dict[str, list[str]] = {k: [] for k in _SCOPE_KEYS}
    if not isinstance(contract, dict):
        return scopes, ["contract is not an object"]
    for key in _SCOPE_KEYS:
        raw = contract.get(key, [])
        if not isinstance(raw, list):
            problems.append(f"{key}: scope is not a list")
            continue
        for i, g in enumerate(raw):
            if not isinstance(g, str):
                problems.append(f"{key}: entry {i} is not a string")
                continue
            gg, problem = _norm_glob(g)
            if gg is None:
                problems.append(f"{key}: entry {i} {problem}")
                continue
            scopes[key].append(gg)
    return scopes, problems


def _match(cmp: str, globs: list[str]) -> bool:
    """Glob semantics unchanged from the pre-lane-B seam, applied to the resolved comparison forms."""
    for gg in globs:
        if cmp == gg or fnmatch.fnmatchcase(cmp, gg) or (gg.endswith("**") and cmp.startswith(gg[:-2])) \
                or cmp.startswith(gg.rstrip("*\\") + "\\"):
            return True
    return False


def _rule(cmp: str, scopes: dict[str, list[str]], coordination: list[str]) -> tuple[str, str, str]:
    """(class, result, reason) for a resolved in-boundary path: P5, P4 coordination, P2, P3, P1, unclassified."""
    if _match(cmp, scopes["exclusions"]):
        return "P5", "violation", "excluded path (P5)"
    if _match(cmp, coordination):
        return "P4", COORDINATION_RESULT, "coordination path (P4); allowed to change, checked by wf verify"
    if _match(cmp, scopes["presence_only"]):
        return "P2", "violation", "presence-only read-only path changed (P2); never read"
    if _match(cmp, scopes["read_scope"]):
        return "P2", "violation", "read-only path changed (P2)"
    if _match(cmp, scopes["watch"]):
        return "P3", "violation", "watched configuration changed; no separate authorization can be expressed (P3)"
    if _match(cmp, scopes["write_scope"]):
        return "P1", "allowed", "in write scope (P1)"
    return UNCLASSIFIED, "violation", "unclassified in-boundary path"


def _entries(changed: object) -> tuple[list[tuple[str, object]], str | None]:
    """(entries as (recorded string, raw value), problem). A `changed` that is not a list or tuple is one
    malformed entry (hardening 2026-10-15: a string was iterated per character; None raised)."""
    if isinstance(changed, (list, tuple)):
        return [(p if isinstance(p, str) else repr(p), p) for p in changed], None
    if isinstance(changed, str):
        return [(changed, None)], _MALFORMED_LIST + "a string, not a list"
    return [(repr(changed), None)], _MALFORMED_LIST + f"{type(changed).__name__}, not a list"


def classify(changed: list[str], contract: dict, *, coordination: list[str] | None = None) -> dict:
    """Section 4.4 classes over the resolved paths; P1..P5 and `unclassified` hold the caller-supplied strings
    verbatim. `coordination` is the P4 coordination glob set (coordination_globs); None = no P4 class.

    Malformed input never classifies as allowed and never raises: a non-list `changed` or a non-string entry is
    recorded as a P5 violation under its repr; a contract whose scopes cannot be read makes every entry a P5
    violation and is listed under `contract_problems` (fail closed); a malformed coordination set likewise."""
    out: dict = {k: [] for k in (*CLASSES, UNCLASSIFIED)}
    results: list[dict] = []
    scopes, problems = _scopes(contract)
    coord: list[str] = []
    if coordination is not None:
        if not isinstance(coordination, list) or not all(isinstance(g, str) for g in coordination):
            problems.append("coordination: set is not a list of strings")
        else:
            for i, g in enumerate(coordination):
                gg, problem = _norm_glob(g)
                if gg is None:
                    problems.append(f"coordination: entry {i} {problem}")
                else:
                    coord.append(gg)
    entries, list_problem = _entries(changed)
    for shown, raw in entries:
        if list_problem is not None:
            r, problem = None, list_problem
        elif not isinstance(raw, str):
            r, problem = None, "not a string"  # exact text pinned by tests/test_t35_path_policy.py
        else:
            r = resolve(raw)
            problem = r.problem
        if problem is None and problems:
            problem = "contract scopes malformed: " + problems[0] if problems[0] != "contract is not an object" \
                else "contract is not an object"
        if problem is not None:
            cls, result, reason = "P5", "violation", f"unresolvable or prohibited path: {problem}"
        elif r is not None and r.outside:
            cls, result, reason = "P5", "outside boundary (not detected)", "resolves outside boundary (P5)"
        else:
            cls, result, reason = _rule((r.cmp if r is not None else None) or "", scopes, coord)
        out[cls].append(shown)
        results.append({"path": shown, "resolved": r.resolved if r is not None else None, "class": cls,
                        "result": result, "reason": reason})
    out["results"] = results
    out["contract_problems"] = problems
    out["limitations"] = list(LIMITATIONS)
    out["comparison"] = "string resolution only; no path was read, hashed or stat-ed"
    return out


def violations(classes: dict) -> list[str]:
    """Reason strings that reject the acceptance; every string names the offending paths.

    With per-path `results` (a classify() output) the reasons are grouped by kind: P4 coordination entries are
    never violations; `unclassified` entries always are. Without `results` (a bare class dict, the pre-coordination
    shape that classify() no longer produces) the rule is applied to the lists: P2, P3, P4, P5 and unclassified
    entries are violations, fail closed (a bare P4 list carries no coordination evidence). A classification that
    is not an object, a malformed result row and any `contract_problems` entry each reject as well."""
    if not isinstance(classes, dict):
        return [f"classification malformed: {type(classes).__name__} is not an object (fail closed)"]
    results = classes.get("results")
    if not isinstance(results, list):
        p2 = [str(x) for x in classes.get("P2", [])]
        p3 = [str(x) for x in classes.get("P3", [])]
        bad = [str(x) for x in classes.get("P4", [])] + [str(x) for x in classes.get("P5", [])]
        uncl = [str(x) for x in classes.get(UNCLASSIFIED, [])]
        out: list[str] = []
        if p2:
            out.append("read-only paths changed (P2): " + ", ".join(p2))
        if p3:
            out.append("watched configuration changed without separate authorization (P3): " + ", ".join(p3))
        if bad:
            out.append("changed paths outside scope (P4) or excluded (P5): " + ", ".join(bad))
        if uncl:
            out.append("unclassified in-boundary paths (violation): " + ", ".join(uncl))
        return out
    p2: list[str] = []
    p3: list[str] = []
    excluded: list[str] = []
    uncl: list[str] = []
    escaped: list[str] = []
    unresolved: list[str] = []
    malformed_rows: list[str] = []
    for i, r in enumerate(results):
        if not isinstance(r, dict):
            malformed_rows.append(f"row {i} ({type(r).__name__})")
            continue
        if r.get("result") == "allowed":
            continue
        path, cls, reason = str(r.get("path")), r.get("class"), str(r.get("reason", ""))
        if cls == "P4" and r.get("result") == COORDINATION_RESULT:
            continue
        if reason.startswith("unresolvable"):
            unresolved.append(f"{path} ({reason.split(': ', 1)[-1]})")
        elif reason.startswith("resolves outside boundary"):
            escaped.append(path)
        elif cls == "P2":
            p2.append(path)
        elif cls == "P3":
            p3.append(path)
        elif cls == "P5":
            excluded.append(path)
        elif cls == UNCLASSIFIED:
            uncl.append(path)
        else:
            unresolved.append(f"{path} (unknown result {r.get('result')!r})")
    out = []
    if p2:
        out.append("read-only paths changed (P2): " + ", ".join(p2))
    if p3:
        out.append("watched configuration changed without separate authorization (P3; the contract cannot express "
                   "one, fail closed): " + ", ".join(p3))
    if excluded:
        out.append("changed paths excluded by the contract (P5): " + ", ".join(excluded))
    if uncl:
        out.append("unclassified in-boundary paths (violation; in no contract scope and not a coordination path): "
                   + ", ".join(uncl))
    if escaped:
        out.append("paths resolving outside the monitored boundary (P5, not in scope; outside-boundary changes are "
                   "not detected): " + ", ".join(escaped))
    if unresolved:
        out.append("unresolvable or prohibited paths (fail closed): " + ", ".join(unresolved))
    if malformed_rows:
        out.append("malformed result row(s) in the classification (fail closed): " + ", ".join(malformed_rows))
    problems = classes.get("contract_problems")
    if problems:
        out.append("contract scopes malformed (fail closed): "
                   + "; ".join(str(x) for x in (problems if isinstance(problems, list) else [problems])))
    return out
