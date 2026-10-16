"""Reconciliation and recovery-begin (B2 slice 1, lane S1R, Zen; spec FIRST-RELEASE-SPEC.md §3B l.131-145, §4.1
l.178, §6.2 step 3, cases T06 l.281 and T16-a). Frozen interface: GitHub issue #27 "B2 slice 1 - frozen interface
contract" §1 (event schemas), §4-§6 (reconcile, ack, recover begin, signatures), §7 (validate.py additive edit),
§9 decisions (D-S1-5 bounded enumeration). The call signatures below are the ones White's wiring
(`sprint/white-b2s1-wiring`, commands.py) invokes; the wiring is the integrator's, not this module's.

What this module decides, as pure checks and pure computations over rows already in the ledger (it never writes;
White's wiring calls it inside the write transaction, before `validate.build_row`):

* `recovery_open(rows)` / `execution_axis(rows)` - the open `recovery-opened` row (no later `recovery-closed`;
  none exists in this slice) and the `recovering` execution axis it implies (T16-a).
* `compute_report(...)` - the `reconcile-report` content for one target role (builder): deterministic
  classification of every discoverable change into exceptions with stable ids (`<class>:<path>`), the examined
  set stated explicitly, the gate log stated as UNAVAILABLE (never "no gate attempts"), `through_seq` = the head
  the report covers.
* `state(rows, role)` / `check_actor(rows, role=, etype=)` - unreconciled = the role's latest report has no
  acknowledgement; while unreconciled every mutation of that role except `ack-reconcile` is `rejected(unreconciled)`.
* `validate_ack(...)` - the `ack-reconcile` rules: the reconciled role's own bound (or implicit) session, the latest
  report (`stale-report` for any other seq, `no-report` when none exists), the exact exception-id set, no second
  acknowledgement. The acknowledgement never makes its author unreconciled (it is not a report).
* `validate_recover_begin(...)`, `recoverable_exceptions(db, job)`, `chain_problems(conn, job)` - `wf recover begin`
  preconditions (recovery not already open; the owner turn names the job), the read-only exception list recorded
  with the opening, and the whole-chain check whose failure appends nothing (`rejected(invalid-chain)`, wiring).
* `validate_content(...)` - §1 schemas of the three S1R event types (exact key sets).

D-S1-5 (bounded enumeration; stated in every report under `examined`): the report hashes exactly (a) the paths
named by the builder's `intent` and `receipt` rows (resolved with `pathpolicy.resolve`, read relative to the boundary
root, never followed through links) and (b) the `changeset.compare` walk, which is the static-prefix walk of
`read_scope` / `watch` / `presence_only` only. It does NOT walk the write scope: a write-scope file changed with
neither an intent nor a receipt naming it is not discoverable here and is listed under `limitations`, never claimed.

Not here (other lanes / excluded): bind and ARS (S1B), command wiring and CLI (S1W), replacement / generation+1
(T33), `recover reconcile` / `recover close` (T30 campaign), observe, backup / restore, the gate.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

from . import changeset, export, markers, pathpolicy
from .canon import sha256_file, sha256_text
from .db import WfRejected, contract as _contract_row, rows as _rows, verify_chain

__all__ = ["CONTENT_TYPES", "CLASSES", "REPORT_ROLE", "recovery_open", "execution_axis", "state", "check_actor",
           "compute_report", "validate_ack", "validate_content", "validate_recover_begin", "recoverable_exceptions",
           "chain_problems", "exception_id"]

CONTENT_TYPES: tuple[str, ...] = ("reconcile-report", "ack-reconcile", "recovery-opened")
#: The only role that writes in R1, hence the only reconcilable role in this slice (read-only roles have nothing
#: to reconcile; the wiring refuses other target roles before calling here).
REPORT_ROLE = "builder"
CLASSES: tuple[str, ...] = (
    "attempt-without-completion",   # an intent for a path with no later receipt for it (file state recorded)
    "completion-without-attempt",   # a receipt for a path that no earlier intent named
    "completion-mismatch",          # a receipt whose sha_after is not the file's current sha256 (or file absent)
    "matches-neither",              # a walked change (added / modified / deleted) no intent or receipt names
    "unresolvable-path",            # an intent/receipt path that cannot be related to the boundary (fail closed)
)
GATE_LOG_UNAVAILABLE = "gate log unavailable in R1: gate attempts are UNKNOWN (not 'none')"
METHOD = ("intent/receipt paths hashed now (sha256) relative to the boundary root; changeset.compare walk of "
          "read_scope/watch/presence_only against the contract-event baseline; no write-scope walk")
_LIMITS: tuple[str, ...] = (
    "a write-scope file changed with neither an intent nor a receipt naming it is not discoverable by this report",
    pathpolicy.NOT_DETECTED,
    "links and reparse points are never followed; a change behind one is not observed",
    "a change made after this report's walk is not covered (through_seq bounds the ledger, not the filesystem)",
)

_REPORT_KEYS = frozenset({"role", "binding", "through_seq", "method", "examined", "exceptions", "changeset",
                          "gate_log", "limitations"})
_ACK_KEYS = frozenset({"role", "session", "report_seq", "exceptions"})
_ACK_OPTIONAL = frozenset({"note"})
_RECOVERY_KEYS = frozenset({"reason", "owner_turn", "exceptions", "chain_head"})
_INJECTED = frozenset({"contract_sha256"})


# ---------------------------------------------------------------------------------------------- helpers

def _content(r: dict[str, Any]) -> dict[str, Any]:
    try:
        c = json.loads(r["content"])
    except (TypeError, ValueError):
        return {}
    return c if isinstance(c, dict) else {}


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _nonempty_str(v: object) -> bool:
    return isinstance(v, str) and bool(v.strip())


def exception_id(cls: str, path: str) -> str:
    """Deterministic id of one exception: its class and the path as the ledger names it."""
    return f"{cls}:{path}"


# ---------------------------------------------------------------------------------------------- recovery (§3B)

def recovery_open(rows: list[dict[str, Any]]) -> int | None:
    """seq of the open `recovery-opened` row (no later `recovery-closed`), else None."""
    opened: int | None = None
    for r in rows:
        if r["type"] == "recovery-opened":
            opened = int(r["seq"])
        elif r["type"] == "recovery-closed":
            opened = None
    return opened


def execution_axis(rows: list[dict[str, Any]]) -> str | None:
    """`"recovering"` while a recovery is open (T16-a execution axis), else None (the caller's own value stands)."""
    return "recovering" if recovery_open(rows) is not None else None


# ---------------------------------------------------------------------------------------------- report / ack state

def state(rows: list[dict[str, Any]], role: str) -> dict[str, Any]:
    """Reconciliation state of `role`: the latest `reconcile-report` targeting it, the acknowledgement of that
    report if any, and `unreconciled` (a latest report without acknowledgement). A role with no report is reconciled."""
    latest: int | None = None
    acked: int | None = None
    for r in rows:
        c = _content(r)
        if r["type"] == "reconcile-report" and c.get("role") == role:
            latest, acked = int(r["seq"]), None
        elif r["type"] == "ack-reconcile" and c.get("role") == role and latest is not None \
                and c.get("report_seq") == latest and acked is None:
            acked = int(r["seq"])
    return {"role": role, "latest_report_seq": latest, "acked_seq": acked,
            "unreconciled": latest is not None and acked is None}


def check_actor(rows: list[dict[str, Any]], *, role: str, etype: str) -> None:
    """While `role` is unreconciled, every mutation by that role other than its `ack-reconcile` is refused.
    The report author (coordinator) is never blocked by a report about another role."""
    if etype == "ack-reconcile":
        return
    st = state(rows, role)
    if st["unreconciled"]:
        raise WfRejected("unreconciled", f"role {role} has an unacknowledged reconcile report at seq "
                                         f"{st['latest_report_seq']}; only `ack-reconcile` for that report may be written")


# ---------------------------------------------------------------------------------------------- report (§4 / T06)

def _file_state(root: str, rel_resolved: str) -> dict[str, Any]:
    """Current state of one path under the boundary root, never following a link or reparse point."""
    abs_path = os.path.join(root, *rel_resolved.split("\\"))
    if changeset._is_link(abs_path):
        return {"exists": True, "link": True, "sha256": None}
    if not os.path.isfile(abs_path):
        return {"exists": False, "link": False, "sha256": None}
    try:
        return {"exists": True, "link": False, "sha256": sha256_file(abs_path)}
    except OSError as exc:
        return {"exists": True, "link": False, "sha256": None, "unreadable": type(exc).__name__}


def compute_report(root: str, contract: dict, baseline: dict, rows: list[dict[str, Any]], *, role: str,
                   binding: tuple[str, int] | None, head_seq: int) -> dict[str, Any]:
    """The `reconcile-report` content for `role` covering the ledger through `head_seq`.

    Deterministic: exceptions are sorted by id; each carries its evidence. `binding` is the (session, generation)
    the ledger attributes to the role (explicit or implicit), or None when it has none yet.
    """
    if role != REPORT_ROLE:
        raise WfRejected("role", f"nothing to reconcile for read-only role {role!r} (only the builder writes)")
    intents: dict[str, list[int]] = {}        # path -> seqs of intents
    receipts: dict[str, list[dict[str, Any]]] = {}
    unresolvable: dict[str, str] = {}
    resolved_of: dict[str, str] = {}
    for r in rows:
        if int(r["seq"]) > head_seq or r["author_role"] != role or r["type"] not in ("intent", "receipt"):
            continue
        c = _content(r)
        path = c.get("path")
        if not isinstance(path, str):
            continue
        res = pathpolicy.resolve(path)
        if res.problem is not None or res.outside or res.resolved is None:
            unresolvable[path] = res.problem or "outside the boundary"
            continue
        resolved_of[path] = res.resolved
        if r["type"] == "intent":
            intents.setdefault(path, []).append(int(r["seq"]))
        else:
            receipts.setdefault(path, []).append({"seq": int(r["seq"]), "sha_after": c.get("sha_after")})

    exceptions: list[dict[str, Any]] = []

    def add(cls: str, path: str, evidence: dict[str, Any]) -> None:
        exceptions.append({"id": exception_id(cls, path), "class": cls, "path": path, "evidence": evidence})

    for path, problem in sorted(unresolvable.items()):
        add("unresolvable-path", path, {"problem": problem})
    examined_paths: list[str] = []
    for path in sorted(set(intents) | set(receipts)):
        examined_paths.append(path)
        now = _file_state(root, resolved_of[path])
        last_intent = max(intents.get(path, [0]))
        last_receipt = max((x["seq"] for x in receipts.get(path, [])), default=0)
        if intents.get(path) and last_intent > last_receipt:
            add("attempt-without-completion", path,
                {"intent_seqs": intents[path], "last_receipt_seq": last_receipt or None, "now": now})
        if receipts.get(path):
            if not intents.get(path):
                add("completion-without-attempt", path, {"receipt_seqs": [x["seq"] for x in receipts[path]]})
            final = max(receipts[path], key=lambda x: x["seq"])
            if last_receipt >= last_intent and now.get("sha256") != final.get("sha_after"):
                add("completion-mismatch", path,
                    {"receipt_seq": final["seq"], "sha_after": final.get("sha_after"), "now": now})
    cmp = changeset.compare(root, contract, baseline)
    named = {resolved_of[p].lower() for p in resolved_of}
    for kind in ("added", "modified", "deleted"):
        for rel in cmp.get(kind, []):
            res = pathpolicy.resolve(rel)
            key = (res.resolved or rel).lower()
            if key in named:
                continue
            add("matches-neither", rel, {"change": kind})
    exceptions.sort(key=lambda e: e["id"])
    return {
        "role": role,
        "binding": {"session": binding[0], "generation": binding[1]} if binding is not None else None,
        "through_seq": head_seq,
        "method": METHOD,
        "examined": {"intent_receipt_paths": examined_paths,
                     "walked_scopes": ["read_scope", "watch", "presence_only"],
                     "write_scope_walk": False},
        "exceptions": exceptions,
        "changeset": {"added": cmp.get("added", []), "modified": cmp.get("modified", []),
                      "deleted": cmp.get("deleted", []), "problems": cmp.get("problems", [])},
        "gate_log": {"available": False, "statement": GATE_LOG_UNAVAILABLE},
        "limitations": [*_LIMITS, *cmp.get("limitations", [])],
    }


# ---------------------------------------------------------------------------------------------- ack (§5 / T06)

def validate_ack(rows: list[dict[str, Any]], *, role: str, session: str, binding_session: str | None,
                 content: dict) -> None:
    """`ack-reconcile` rules, in order: shape (invalid-ack) -> the content names the author role and session
    (invalid-ack) -> a report for that role exists (no-report) -> `report_seq` is the latest report (stale-report)
    -> not already acknowledged (invalid-ack) -> the author is the role's session (`binding_session`; invalid-ack)
    -> the exception ids equal the report's set exactly, no duplicates (invalid-ack)."""
    if not isinstance(content, dict):
        raise WfRejected("invalid-ack", "content must be a JSON object")
    if content.get("role") != role or content.get("session") != session:
        raise WfRejected("invalid-ack", "ack-reconcile must name the author's own role and session")
    st = state(rows, role)
    if st["latest_report_seq"] is None:
        raise WfRejected("no-report", f"no reconcile report for role {role} to acknowledge")
    seq = content.get("report_seq")
    if not _is_int(seq) or seq != st["latest_report_seq"]:
        raise WfRejected("stale-report", f"report_seq {seq!r} is not the latest reconcile report for role {role} "
                                         f"(seq {st['latest_report_seq']})")
    if st["acked_seq"] is not None:
        raise WfRejected("invalid-ack", f"report {seq} already acknowledged at seq {st['acked_seq']}")
    if binding_session is None or session != binding_session:
        raise WfRejected("invalid-ack", f"only the session the ledger attributes to role {role} "
                                        f"({binding_session!r}) may acknowledge its report; {session!r} is not it")
    ids = content.get("exceptions")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
        raise WfRejected("invalid-ack", "exceptions must be a list of distinct exception ids")
    report = next(r for r in rows if int(r["seq"]) == seq)
    expected = sorted(e.get("id") for e in _content(report).get("exceptions", []) if isinstance(e, dict))
    if sorted(ids) != expected:
        raise WfRejected("invalid-ack", f"exception set must equal the report's exactly: expected {expected}, got "
                                        f"{sorted(ids)}")


# ---------------------------------------------------------------------------------------------- recover begin (T16-a)

def validate_recover_begin(rows: list[dict[str, Any]], *, job: str, content: dict) -> None:
    """`wf recover begin` preconditions: recovery not already open (recovery-open); the owner turn names the job
    (no-provenance); the content has the §1 shape and its chain_head is the current head."""
    opened = recovery_open(rows)
    if opened is not None:
        raise WfRejected("recovery-open", f"recovery is already open since seq {opened}", opened_seq=opened)
    if not isinstance(content, dict):
        raise WfRejected("malformed", "content must be a JSON object")
    if not _nonempty_str(content.get("reason")):
        raise WfRejected("malformed", "recovery reason must be a non-empty string")
    turn = content.get("owner_turn")
    if not _nonempty_str(turn) or job not in turn.split():
        raise WfRejected("no-provenance", f"the owner turn must name the job {job!r} as a word")
    if not isinstance(content.get("exceptions"), list):
        raise WfRejected("malformed", "recovery exceptions must be a list")
    head = content.get("chain_head")
    if not isinstance(head, dict) or not _is_int(head.get("seq")) or not isinstance(head.get("self"), str):
        raise WfRejected("malformed", "chain_head must be {seq, self}")
    if rows and (head["seq"] != int(rows[-1]["seq"]) or head["self"] != rows[-1]["self"]):
        raise WfRejected("malformed", "chain_head does not name the current head")


def recoverable_exceptions(db: str, job: str) -> list[dict[str, Any]]:
    """Read-only exception list recorded with `recovery-opened`: export problems (stale export, strays, preserved
    temps), pending markers, and the gate log, which is UNAVAILABLE in R1 (stated, never counted as zero attempts)."""
    out: list[dict[str, Any]] = []
    for p in export.check(db, job):
        out.append({"kind": "export", "detail": p})
    for m in markers.describe(db, job):
        out.append({"kind": "pending-marker", "seq": m.get("seq"), "attempt_id": m.get("attempt_id"),
                    "state": m.get("state")})
    out.append({"kind": "gate-log", "available": False, "detail": GATE_LOG_UNAVAILABLE})
    return out


def chain_problems(conn: sqlite3.Connection, job: str) -> list[str]:
    """Whole-chain check (contract row sha, every row's prev/self/content/pin, contract-first rule); [] = valid."""
    problems: list[str] = []
    c = _contract_row(conn, job)
    if c is None:
        problems.append("no contract row")
    elif sha256_text(c["content"]) != c["sha256"]:
        problems.append("contract content does not match its sha256")
    problems += verify_chain(conn, job)
    rs = _rows(conn, job)
    if rs and rs[0]["type"] != "contract":
        problems.append("first event is not the contract event")
    return problems


# ---------------------------------------------------------------------------------------------- schemas (§1)

def validate_content(etype: str, content: dict, contract: dict, grant_state: dict | None) -> None:
    """Schemas of `reconcile-report`, `ack-reconcile`, `recovery-opened`; any defect is `rejected(malformed)`.
    Exact key sets (plus the injected `contract_sha256`; `ack-reconcile` may carry `note`)."""
    if etype not in CONTENT_TYPES:
        raise WfRejected("malformed", f"{etype!r} is not a reconcile event type")
    if not isinstance(content, dict):
        raise WfRejected("malformed", "content must be a JSON object")
    want, optional = {"reconcile-report": (_REPORT_KEYS, frozenset()), "ack-reconcile": (_ACK_KEYS, _ACK_OPTIONAL),
                      "recovery-opened": (_RECOVERY_KEYS, frozenset())}[etype]
    missing = sorted(want - set(content))
    extra = sorted(set(content) - want - optional - _INJECTED)
    if missing:
        raise WfRejected("malformed", f"{etype} content missing {missing}")
    if extra:
        raise WfRejected("malformed", f"{etype} content has unknown keys {extra}")
    if etype == "reconcile-report":
        if content["role"] != REPORT_ROLE:
            raise WfRejected("malformed", f"reconcile-report role must be {REPORT_ROLE!r}")
        if not _is_int(content["through_seq"]) or content["through_seq"] < 1:
            raise WfRejected("malformed", "through_seq must be an integer >= 1")
        ex = content["exceptions"]
        if not isinstance(ex, list) or not all(isinstance(e, dict) and _nonempty_str(e.get("id"))
                                               and e.get("class") in CLASSES and _nonempty_str(e.get("path"))
                                               for e in ex):
            raise WfRejected("malformed", "exceptions must be a list of {id, class, path, ...} with a known class")
        if len({e["id"] for e in ex}) != len(ex):
            raise WfRejected("malformed", "exception ids must be distinct")
        if not isinstance(content["gate_log"], dict) or content["gate_log"].get("available") is not False:
            raise WfRejected("malformed", "gate_log must state available: false in R1")
    elif etype == "ack-reconcile":
        if not _nonempty_str(content["role"]) or not _nonempty_str(content["session"]):
            raise WfRejected("malformed", "ack-reconcile role and session must be non-empty strings")
        if not _is_int(content["report_seq"]) or content["report_seq"] < 1:
            raise WfRejected("malformed", "report_seq must be an integer >= 1")
        if not isinstance(content["exceptions"], list) or not all(isinstance(i, str) for i in content["exceptions"]):
            raise WfRejected("malformed", "exceptions must be a list of exception ids")
        if "note" in content and not isinstance(content["note"], str):
            raise WfRejected("malformed", "note must be a string")
    else:  # recovery-opened
        if not _nonempty_str(content["reason"]) or not _nonempty_str(content["owner_turn"]):
            raise WfRejected("malformed", "reason and owner_turn must be non-empty strings")
        if not isinstance(content["exceptions"], list):
            raise WfRejected("malformed", "exceptions must be a list")
        head = content["chain_head"]
        if not isinstance(head, dict) or not _is_int(head.get("seq")) or not isinstance(head.get("self"), str):
            raise WfRejected("malformed", "chain_head must be {seq, self}")
