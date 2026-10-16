"""Reconciliation and recovery-begin (B2 slice 1, lane S1R, Zen; spec FIRST-RELEASE-SPEC.md §3B l.131-145, §4.1
l.178, §6.2 steps 3 and 5, cases T06 l.281 and T16-a). Frozen interface: GitHub issue #27 "B2 slice 1 - frozen
interface contract" §1 (event schemas), §4 (reconcile / ack), §5 (recover begin), §6 (signatures and allowed
imports), §9 decisions D-S1-3 / D-S1-5, and AMENDMENT A2 (compare never walks the write scope; provenance is a
substring test). Revised to the frozen text in S1R correction cycle 1 (White's F1 on PR #30).

What this module decides, as pure checks and computations over rows already read inside the write transaction
(it never writes; White's wiring calls it before `validate.build_row`):

* `recovery_open(rows)` / `execution_axis(rows)` - the open `recovery-opened` row and the `recovering` axis (§5).
* `compute_report(...)` - the §4 `reconcile-report` content: `classification` of every in-scope path into the
  frozen CLASSES, `exceptions` (ids `X1..` in path order) for every matches-neither and attempt-without-completion
  item, `gate_log: null` (no gate in R1: unavailable, never "no attempts"), `problems` from `changeset.compare`
  (non-empty -> `WfRefused`, the caller appends nothing), `limitations`.
* `state(rows, role)` / `check_actor(rows, role=, etype=)` - unreconciled = the role's latest report has no later
  `ack-reconcile` naming its seq (D-S1-3: zero exceptions still need the ack); while unreconciled every mutation of
  that role except `ack-reconcile` is `rejected(unreconciled, report_seq=...)`.
* `validate_ack(...)` - §4: no-report / stale-report / invalid-ack (own role and session, the role's bound or
  implicit session, exact exception-id set, no second ack). The ack never makes its author unreconciled.
* `validate_recover_begin(...)`, `recoverable_exceptions(db_path, job)`, `chain_problems(conn, job)` - §5.
* `validate_content(...)` - §1 schemas of the three S1R event types (exact key sets).

D-S1-5 / A2 (bounded enumeration, stated in every report's `limitations`): in-scope paths are the union of the
`changeset.compare` walk (read_scope / watch / presence_only only) and every path named by an intent or receipt.
The write scope is not walked: a write-scope file changed with neither an intent nor a receipt naming it is not
discoverable here. Paths are related to the boundary root with stdlib string normalisation only (no link is
followed); a path that cannot be related to the root is classified matches-neither with `sha_now: null` and named
in `limitations`.

Not here (other lanes / excluded): bind and ARS (S1B), command wiring and CLI (S1W), replacement / generation+1
(T33), `recover reconcile` / `recover close` (T30), observe, backup / restore, the gate.
"""
from __future__ import annotations

import json
import os
import posixpath
import sqlite3
import stat
from typing import Any

from . import changeset, export
from .canon import sha256_file
from .db import WfRefused, WfRejected, integrity, verify_chain

__all__ = ["CONTENT_TYPES", "CLASSES", "REPORT_ROLE", "compute_report", "state", "check_actor", "validate_ack",
           "recovery_open", "recoverable_exceptions", "validate_recover_begin", "chain_problems", "validate_content",
           "execution_axis"]

CONTENT_TYPES: tuple[str, ...] = ("reconcile-report", "ack-reconcile", "recovery-opened")
CLASSES: tuple[str, ...] = ("matches-receipt", "matches-baseline", "matches-neither", "attempt-without-completion")
EXCEPTION_CLASSES: tuple[str, ...] = ("matches-neither", "attempt-without-completion")
#: The only writing role in R1, hence the only reconcilable role in this slice (§9).
REPORT_ROLE = "builder"
_LIMITS: tuple[str, ...] = (
    "D-S1-5: in-scope paths are the union of changeset.compare (read_scope/watch/presence_only walk) and every "
    "path named by an intent or receipt; the write scope is not walked",
    "D-S1-5: a write-scope file changed with neither an intent nor a receipt naming it is not discoverable here",
    "no gate in R1: gate_log is null (unavailable), never a count of attempts",
    "links and reparse points are never followed; a path behind one is reported with sha_now null",
)
_REPORT_KEYS = frozenset({"role", "session", "generation", "through_seq", "classification", "exceptions", "gate_log",
                          "problems", "limitations"})
_CLASSIFICATION_KEYS = frozenset({"path", "class", "receipt_seq", "intent_seq", "sha_now", "sha_baseline",
                                  "sha_receipt"})
_EXCEPTION_KEYS = frozenset({"id", "path", "class"})
_ACK_KEYS = frozenset({"role", "session", "report_seq", "exceptions"})
_ACK_OPTIONAL = frozenset({"note"})
_RECOVERY_KEYS = frozenset({"reason", "owner_turn", "exceptions", "chain_head"})
_RECOVERABLE_KEYS = frozenset({"id", "class", "detail"})
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


def _relate(path: str) -> str | None:
    """Relate a ledger path to the boundary root with stdlib normalisation only: forward slashes, no drive, no
    root, no escape above the root; the result is the comparison form (forward slash, lower). None = cannot."""
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        return None
    p = path.replace("\\", "/")
    if p.startswith("/") or (len(p) > 1 and p[1] == ":"):
        return None
    norm = posixpath.normpath(p)
    if norm in (".", "") or norm == ".." or norm.startswith("../"):
        return None
    return norm.lower()


def _is_link(abs_path: str) -> bool:
    try:
        st = os.lstat(abs_path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _sha_now(root: str, rel: str | None) -> str | None:
    """sha256 of the file now, or None when the path cannot be related, is absent, is a link, or is unreadable."""
    if rel is None:
        return None
    abs_path = os.path.join(root, *rel.split("/"))
    if _is_link(abs_path) or not os.path.isfile(abs_path):
        return None
    try:
        return sha256_file(abs_path)
    except OSError:
        return None


# ---------------------------------------------------------------------------------------------- recovery (§5)

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
    """`"recovering"` while a recovery is open, else None (the caller's own axis value stands)."""
    return "recovering" if recovery_open(rows) is not None else None


# ---------------------------------------------------------------------------------------------- state (§4)

def state(rows: list[dict[str, Any]], role: str) -> dict[str, Any]:
    """{"latest_report_seq", "latest_ack_seq", "unreconciled", "report"}: the latest `reconcile-report` for `role`,
    the latest `ack-reconcile` of that role whose `report_seq` names it, unreconciled iff the report has no such
    ack, and the report content (or None)."""
    latest: int | None = None
    report: dict[str, Any] | None = None
    ack: int | None = None
    for r in rows:
        c = _content(r)
        if r["type"] == "reconcile-report" and c.get("role") == role:
            latest, report, ack = int(r["seq"]), c, None
        elif r["type"] == "ack-reconcile" and c.get("role") == role and latest is not None \
                and c.get("report_seq") == latest:
            ack = int(r["seq"])
    return {"latest_report_seq": latest, "latest_ack_seq": ack,
            "unreconciled": latest is not None and ack is None, "report": report}


def check_actor(rows: list[dict[str, Any]], *, role: str, etype: str) -> None:
    """While `role` is unreconciled, every mutation by that role other than its `ack-reconcile` is refused."""
    if etype == "ack-reconcile":
        return
    st = state(rows, role)
    if st["unreconciled"]:
        raise WfRejected("unreconciled", f"role {role} has an unacknowledged reconcile report at seq "
                                         f"{st['latest_report_seq']}; only `ack-reconcile` for it may be written",
                         report_seq=st["latest_report_seq"])


# ---------------------------------------------------------------------------------------------- report (§4 / T06)

def compute_report(root: str, contract: dict, baseline_manifest: dict, rows: list[dict[str, Any]], *, role: str,
                   binding: tuple[str, int] | None, head_seq: int) -> dict[str, Any]:
    """The §4 `reconcile-report` content for `role` covering the ledger through `head_seq`.

    Classification per in-scope path (§4 rules; attempt-without-completion is decided first because it holds
    "regardless of sha"): an intent with no later receipt for the path -> attempt-without-completion; else a latest
    receipt whose sha_after equals sha_now -> matches-receipt; else no receipt and sha_now equals the baseline sha
    (or absent now and absent in the baseline) -> matches-baseline; else matches-neither. Deterministic: paths
    sorted, exception ids X1.. in that order. `problems` non-empty (compare failed closed) -> `WfRefused`.
    """
    if role != REPORT_ROLE:
        raise WfRejected("role", f"nothing to reconcile for read-only role {role!r} (only the builder writes)")
    cmp = changeset.compare(root, contract, baseline_manifest)
    if cmp.get("problems"):
        raise WfRefused("reconcile refused (fail closed, nothing appended): changed-set comparison problems: "
                        + "; ".join(map(str, cmp["problems"])))
    entries = baseline_manifest.get("entries", {}) if isinstance(baseline_manifest, dict) else {}
    intents: dict[str, int] = {}                         # path -> latest intent seq
    receipts: dict[str, tuple[int, Any]] = {}            # path -> (latest receipt seq, sha_after)
    for r in rows:
        if int(r["seq"]) > head_seq or r["author_role"] != role or r["type"] not in ("intent", "receipt"):
            continue
        c = _content(r)
        path = c.get("path")
        if not isinstance(path, str) or not path:
            continue
        if r["type"] == "intent":
            intents[path] = int(r["seq"])
        else:
            receipts[path] = (int(r["seq"]), c.get("sha_after"))
    in_scope = sorted(set(intents) | set(receipts) | set(cmp.get("added", [])) | set(cmp.get("modified", []))
                      | set(cmp.get("deleted", [])))
    limitations = [*_LIMITS, *cmp.get("limitations", [])]
    classification: list[dict[str, Any]] = []
    for path in in_scope:
        rel = _relate(path)
        if rel is None:
            limitations.append(f"path {path!r} cannot be related to the boundary root; classified matches-neither")
        sha_now = _sha_now(root, rel)
        base = entries.get(path) if isinstance(entries, dict) else None
        if base is None and rel is not None:   # baseline keys are repo-relative forward-slash strings
            base = next((e for k, e in entries.items() if isinstance(k, str) and k.lower() == rel), None)
        sha_baseline = base.get("sha256") if isinstance(base, dict) else None
        in_baseline = isinstance(base, dict)
        intent_seq = intents.get(path)
        receipt_seq, sha_receipt = receipts.get(path, (None, None))
        if intent_seq is not None and (receipt_seq is None or receipt_seq < intent_seq):
            cls = "attempt-without-completion"
        elif rel is None:
            cls = "matches-neither"
        elif receipt_seq is not None and sha_now is not None and sha_now == sha_receipt:
            cls = "matches-receipt"
        elif receipt_seq is None and ((sha_now is not None and sha_now == sha_baseline)
                                      or (sha_now is None and not in_baseline)):
            cls = "matches-baseline"
        else:
            cls = "matches-neither"
        classification.append({"path": path, "class": cls, "receipt_seq": receipt_seq, "intent_seq": intent_seq,
                               "sha_now": sha_now, "sha_baseline": sha_baseline, "sha_receipt": sha_receipt})
    exceptions = [{"id": f"X{i}", "path": c["path"], "class": c["class"]}
                  for i, c in enumerate((c for c in classification if c["class"] in EXCEPTION_CLASSES), start=1)]
    return {
        "role": role,
        "session": binding[0] if binding is not None else None,
        "generation": binding[1] if binding is not None else None,
        "through_seq": head_seq,
        "classification": classification,
        "exceptions": exceptions,
        "gate_log": None,
        "problems": list(cmp.get("problems", [])),
        "limitations": limitations,
    }


# ---------------------------------------------------------------------------------------------- ack (§4 / T06)

def validate_ack(rows: list[dict[str, Any]], *, role: str, session: str, binding_session: str | None,
                 content: dict) -> None:
    """`ack-reconcile` rules (§4), in order: shape and own role/session (invalid-ack) -> a report for the role exists
    (no-report) -> `report_seq` is the latest report (stale-report) -> not already acknowledged (invalid-ack) ->
    the author is the role's bound or implicit session (invalid-ack) -> exact exception-id set (invalid-ack)."""
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
    if st["latest_ack_seq"] is not None:
        raise WfRejected("invalid-ack", f"report {seq} already acknowledged at seq {st['latest_ack_seq']}")
    if binding_session is None or session != binding_session:
        raise WfRejected("invalid-ack", f"only the session the ledger attributes to role {role} "
                                        f"({binding_session!r}) may acknowledge its report; {session!r} is not it")
    ids = content.get("exceptions")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
        raise WfRejected("invalid-ack", "exceptions must be a list of distinct exception ids")
    expected = sorted(e.get("id") for e in (st["report"] or {}).get("exceptions", []) if isinstance(e, dict))
    if sorted(ids) != expected:
        raise WfRejected("invalid-ack", f"exception set must equal the report's exactly: expected {expected}, got "
                                        f"{sorted(ids)}")


# ---------------------------------------------------------------------------------------------- recover begin (§5)

def validate_recover_begin(rows: list[dict[str, Any]], *, job: str, content: dict) -> None:
    """§5 preconditions: recovery not already open (recovery-open); the owner turn contains the job id (A2:
    substring; no-provenance); §1 shape (malformed); `chain_head` names the current head (verified)."""
    opened = recovery_open(rows)
    if opened is not None:
        raise WfRejected("recovery-open", f"recovery is already open since seq {opened}", opened_seq=opened)
    if not isinstance(content, dict):
        raise WfRejected("malformed", "content must be a JSON object")
    if not _nonempty_str(content.get("reason")):
        raise WfRejected("malformed", "recovery reason must be a non-empty string")
    turn = content.get("owner_turn")
    if not _nonempty_str(turn) or job not in turn:
        raise WfRejected("no-provenance", f"the owner turn must name the job {job!r}")
    ex = content.get("exceptions")
    if not isinstance(ex, list) or not all(isinstance(e, dict) and set(e) == _RECOVERABLE_KEYS for e in ex):
        raise WfRejected("malformed", "recovery exceptions must be a list of {id, class, detail}")
    head = content.get("chain_head")
    if not isinstance(head, dict) or not _is_int(head.get("seq")) or not isinstance(head.get("self"), str):
        raise WfRejected("malformed", "chain_head must be {seq, self}")
    if rows and (head["seq"] != int(rows[-1]["seq"]) or head["self"] != rows[-1]["self"]):
        raise WfRejected("malformed", "chain_head does not name the current head")


def recoverable_exceptions(db_path: str, job: str) -> list[dict[str, Any]]:
    """§1/§5: the recoverable exceptions found now, read-only and outside the write transaction: every
    `export.check` problem (class `export`), with the preserved-temp lines `export.orphans` reports carried as class
    `orphan`; ids `R1..` in order. The gate log is not an exception: it is the row's `gate_log: null`."""
    out: list[dict[str, Any]] = []
    for p in export.check(db_path, job):
        cls = "orphan" if p.startswith(export.PRESERVED) else "export"
        out.append({"id": f"R{len(out) + 1}", "class": cls, "detail": p})
    return out


def chain_problems(conn: sqlite3.Connection, job: str) -> list[str]:
    """§5 full chain verification: `dbm.verify_chain(conn, job)` + `dbm.integrity(conn, quick=True)`; [] = valid."""
    return [*verify_chain(conn, job), *integrity(conn, quick=True)]


# ---------------------------------------------------------------------------------------------- schemas (§1 / §4)

def validate_content(etype: str, content: dict, contract: dict, grant_state: dict | None) -> None:
    """Schemas of `reconcile-report` (§4), `ack-reconcile` and `recovery-opened` (§1); any defect is
    `rejected(malformed)`. Exact key sets plus the injected `contract_sha256`; `ack-reconcile` may carry `note`."""
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
        if content["session"] is not None and not _nonempty_str(content["session"]):
            raise WfRejected("malformed", "session must be a non-empty string or null")
        if content["generation"] is not None and not _is_int(content["generation"]):
            raise WfRejected("malformed", "generation must be an integer or null")
        if not _is_int(content["through_seq"]) or content["through_seq"] < 1:
            raise WfRejected("malformed", "through_seq must be an integer >= 1")
        cl = content["classification"]
        if not isinstance(cl, list) or not all(isinstance(c, dict) and set(c) == _CLASSIFICATION_KEYS
                                               and _nonempty_str(c["path"]) and c["class"] in CLASSES for c in cl):
            raise WfRejected("malformed", "classification must be a list of {path, class, receipt_seq, intent_seq, "
                                          "sha_now, sha_baseline, sha_receipt} with a frozen class")
        ex = content["exceptions"]
        if not isinstance(ex, list) or not all(isinstance(e, dict) and set(e) == _EXCEPTION_KEYS
                                               and _nonempty_str(e["id"]) and _nonempty_str(e["path"])
                                               and e["class"] in EXCEPTION_CLASSES for e in ex):
            raise WfRejected("malformed", "exceptions must be a list of {id, path, class} with class matches-neither "
                                          "or attempt-without-completion")
        if len({e["id"] for e in ex}) != len(ex):
            raise WfRejected("malformed", "exception ids must be distinct")
        if content["gate_log"] is not None:
            raise WfRejected("malformed", "gate_log must be null (no gate in R1)")
        if not isinstance(content["problems"], list) or not isinstance(content["limitations"], list):
            raise WfRejected("malformed", "problems and limitations must be lists")
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
        ex = content["exceptions"]
        if not isinstance(ex, list) or not all(isinstance(e, dict) and set(e) == _RECOVERABLE_KEYS
                                               and _nonempty_str(e["id"]) and _nonempty_str(e["class"])
                                               and isinstance(e["detail"], str) for e in ex):
            raise WfRejected("malformed", "exceptions must be a list of {id, class, detail}")
        head = content["chain_head"]
        if not isinstance(head, dict) or not _is_int(head.get("seq")) or not isinstance(head.get("self"), str):
            raise WfRejected("malformed", "chain_head must be {seq, self}")
