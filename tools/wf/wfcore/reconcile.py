"""Reconciliation and recovery-begin (B2 slice 1, lane S1R, Zen; spec FIRST-RELEASE-SPEC.md §3B l.131-145, §4.1
l.178, §6.2 steps 3-5, cases T06 l.281, T16, T30). Frozen interface: GitHub issue #27 "B2 slice 1 - frozen
interface contract" ("v1") §1, §4, §5, §6, §9, AMENDMENT A2 (provenance is a substring test), as amended by
INTERFACE-CONTRACT-S2.md (B2 slice 2, lane S2R): A-2 (compare walks the write scope; the old D-S1-5 limitation is
gone), A-3 (classification precedence; `matches-baseline` with receipt evidence is an exception), A-4 (unresolvable
paths), §1 (`exception-acknowledged`, `recovery-closed`, `recovery-decision`), §4-§6 (recovery closure, decision).

What this module decides, as pure checks and computations over rows already read inside the write transaction
(it never writes; White's wiring calls it before `validate.build_row`):

* `recovery_open(rows)` / `execution_axis(rows)` - the open `recovery-opened` row and the `recovering` axis (§5).
* `compute_report(...)` - the §4 `reconcile-report` content: `classification` of every in-scope path into the
  frozen CLASSES (S2 A-3 precedence), `exceptions` (ids `X1..` in path order) for every matches-neither,
  attempt-without-completion and receipt-bearing matches-baseline item, `gate_log: null` (no gate in R1: unavailable, never "no attempts"), `problems` from `changeset.compare`
  (B2 slice 4a, S4L: optional `gate_attempts` from the gate log add allowed preventable attempts with no later
  receipt as attempt-without-completion; `gate_log` stays null and `limitations` names the source),
  (non-empty -> `WfRefused`, the caller appends nothing), `limitations`.
* `state(rows, role)` / `check_actor(rows, role=, etype=)` - unreconciled = the role's latest report has no later
  `ack-reconcile` naming its seq (D-S1-3: zero exceptions still need the ack); while unreconciled every mutation of
  that role except `ack-reconcile` is `rejected(unreconciled, report_seq=...)`.
* `validate_ack(...)` - §4: no-report / stale-report / invalid-ack (own role and session, the role's bound or
  implicit session, exact exception-id set, no second ack). The ack never makes its author unreconciled.
* `validate_recover_begin(...)`, `recoverable_exceptions(db_path, job)`, `chain_problems(conn, job)` - §5.
* S2: `recovery_row`, `acknowledged`, `pending_exceptions`, `validate_exception_ack`, `validate_recover_close`
  (recovery closure, T30 c/d/e), `validate_recovery_decision` (§6.2 step 4, D-S2-1/D-S2-5), `unresolvable_paths`
  (A-4), `recovery_audit` (opened -> acknowledged* -> closed per recovery).
* `validate_content(...)` - §1 schemas of the six reconcile/recovery event types (exact key sets).

In-scope paths (S2 A-2): the union of the `changeset.compare` walk and every path named by an intent or receipt.
Since A-2, `changeset.compare` (lane S2B) also walks the write scope, so an unrecorded write-scope change reaches
this module as an added/modified/deleted path; this module never walks anything itself. Paths are related to the
boundary root with stdlib string normalisation only; a path that is absolute, drive-qualified, escapes with `..`, or
crosses a link / reparse point is unresolvable (A-4): `matches-neither`, `sha_now` and `sha_baseline` null, and the
exact limitation string `UNRESOLVABLE`.

Not here (other lanes / excluded): bind and ARS (S1B), command wiring and CLI (S1W), replacement / generation+1
(T33), `recover reconcile` / `recover close` (T30), observe, backup / restore, the gate.
"""
from __future__ import annotations

import fnmatch
import json
import os
import posixpath
import sqlite3
import stat
from datetime import datetime, timezone
from typing import Any

from . import changeset, export, gatelog, pathpolicy
from .canon import sha256_file
from .db import WfRefused, WfRejected, integrity, verify_chain

__all__ = ["CONTENT_TYPES", "CLASSES", "REPORT_ROLE", "DECISIONS", "DISPOSITIONS", "UNRESOLVABLE", "compute_report",
           "state", "check_actor", "validate_ack", "recovery_open", "recoverable_exceptions", "validate_recover_begin",
           "chain_problems", "validate_content", "execution_axis", "recovery_row", "acknowledged",
           "pending_exceptions", "validate_exception_ack", "validate_recover_close", "validate_recovery_decision",
           "unresolvable_paths", "recovery_audit"]

CONTENT_TYPES: tuple[str, ...] = ("reconcile-report", "ack-reconcile", "recovery-opened", "exception-acknowledged",
                                  "recovery-closed", "recovery-decision")
CLASSES: tuple[str, ...] = ("matches-receipt", "matches-baseline", "matches-neither", "attempt-without-completion")
#: Always exceptions. `matches-baseline` is an exception only when its row carries `receipt_seq` (S2 A-3, D-S2-4).
EXCEPTION_CLASSES: tuple[str, ...] = ("matches-neither", "attempt-without-completion")
#: Classes allowed in a report's `exceptions` list (the receipt rule above decides which baseline rows enter).
_EXCEPTION_LIST_CLASSES: tuple[str, ...] = (*EXCEPTION_CLASSES, "matches-baseline")
DECISIONS: tuple[str, ...] = ("resume", "restore", "abandon")
DISPOSITIONS: tuple[str, ...] = ("owner-inspected", "restore", "abandon-path")
#: S2 A-4 limitation, verbatim; `{}` is the path as the intent/receipt names it.
UNRESOLVABLE = "unresolvable path {}: filesystem state unknown; inspection limitation"
_UNRESOLVABLE_PREFIX, _UNRESOLVABLE_SUFFIX = UNRESOLVABLE.split("{}")
#: The only writing role in R1, hence the only reconcilable role in this slice (§9).
REPORT_ROLE = "builder"
_R1_GATE_LIMIT = "no gate in R1: gate_log is null (unavailable), never a count of attempts"
#: B2 slice 4a (INTERFACE-CONTRACT-S4A section 5): the limitation when `gate_attempts` is None.
GATE_NOT_CONSULTED = "gate log not consulted"
#: ... and the one naming the source when attempts are supplied (replaces the R1 line above).
GATE_SOURCE = ("gate attempts consulted (source: gate attempts): an allowed preventable write attempt on a write-scope "
               "path with no receipt for that path at or after the attempt is attempt-without-completion; shell "
               "invocations never classify a path; attempt times have one-second resolution; gate_log stays null")
_LIMITS: tuple[str, ...] = (
    "in-scope paths are the union of changeset.compare and every path named by an intent or receipt",
    _R1_GATE_LIMIT,
    "links and reparse points are never followed; a path crossing one is unresolvable (sha_now and sha_baseline null)",
    "matches-baseline states only that the current content equals the recorded baseline; it never asserts that no "
    "operation executed",
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
_EXC_ACK_KEYS = frozenset({"opened_seq", "id", "class", "detail", "owner_turn"})
_CLOSED_KEYS = frozenset({"opened_seq", "acknowledged", "chain_head", "owner_turn"})
_DECISION_KEYS = frozenset({"decision", "role", "report_seq", "stop_seq", "restore_paths", "unresolved",
                            "owner_turn"})
_NOTE = frozenset({"note"})
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
    root, no escape above the root; the result keeps the written case (forward slash) and is used for I/O; compare
    it with `pathpolicy.fold` (FIX1 P1, WF-021 F2: lower-casing it hid `New.txt` on a case-sensitive filesystem).
    None = cannot."""
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        return None
    p = path.replace("\\", "/")
    if p.startswith("/") or (len(p) > 1 and p[1] == ":"):
        return None
    norm = posixpath.normpath(p)
    if norm in (".", "") or norm == ".." or norm.startswith("../"):
        return None
    return norm


def _is_link(abs_path: str) -> bool:
    try:
        st = os.lstat(abs_path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _crosses_link(root: str, rel: str) -> bool:
    """True when any existing component of `rel` under `root` is a symlink or reparse point (never followed)."""
    cur = root
    for part in rel.split("/"):
        cur = os.path.join(cur, part)
        if not os.path.lexists(cur):
            return False
        if _is_link(cur):
            return True
    return False


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

def _parse_ts(value: object) -> datetime | None:
    """`YYYY-MM-DDTHH:MM:SS[.fff]Z` (ledger rows carry milliseconds, gate rows whole seconds) -> aware UTC."""
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        return datetime.fromisoformat(value[:-1]).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _open_gate_attempts(gate_attempts: list[dict] | None, contract: dict,
                        receipt_times: dict[str, list[datetime]]) -> set[str]:
    """S4A section 5: write-scope paths of allowed preventable gate attempts with no receipt at or after the
    attempt. An attempt whose time cannot be read is never treated as completed."""
    if not gate_attempts:
        return set()
    scope = [g for g in contract.get("write_scope", []) if isinstance(g, str)] if isinstance(contract, dict) else []
    open_paths: set[str] = set()
    for a in gate_attempts:
        if not isinstance(a, dict) or a.get("decision") != "allow" or a.get("channel") != "preventable":
            continue
        paths = a.get("paths")
        if paths is None and isinstance(a.get("paths_json"), str):
            try:
                paths = json.loads(a["paths_json"])
            except ValueError:
                paths = None
        at = _parse_ts(a.get("ts"))
        for path in paths if isinstance(paths, list) else []:
            if not isinstance(path, str) or not path or not any(fnmatch.fnmatchcase(path, g) for g in scope):
                continue
            if at is not None and any(t >= at for t in receipt_times.get(path, [])):
                continue
            open_paths.add(path)
    return open_paths


def compute_report(root: str, contract: dict, baseline_manifest: dict, rows: list[dict[str, Any]], *, role: str,
                   binding: tuple[str, int] | None, head_seq: int,
                   gate_attempts: list[dict] | None = None) -> dict[str, Any]:
    """The §4 `reconcile-report` content for `role` covering the ledger through `head_seq`.

    Per in-scope path: an unresolvable path (A-4) -> matches-neither, `sha_now`/`sha_baseline` null, limitation
    `UNRESOLVABLE`; else (A-3) (1) an intent with no later receipt -> attempt-without-completion, regardless of sha;
    (2) else the latest receipt exists and sha_now == sha_after -> matches-receipt; (3) else sha_now == sha_baseline,
    or absent now and absent in the baseline -> matches-baseline, whether or not a receipt exists; (4) else
    matches-neither. Exceptions: every matches-neither and attempt-without-completion row, and every matches-baseline
    row with a non-null `receipt_seq` (receipt says changed, disk says baseline). Deterministic: paths sorted,
    exception ids X1.. in that order. `problems` non-empty (compare failed closed) -> `WfRefused`.

    `gate_attempts` (S4A section 5): rows from `gatelog.attempts`. An `allow` on the `preventable` channel for a
    write-scope path with no receipt for that path at or after the attempt's time joins the in-scope set and takes
    rule (1) (attempt-without-completion). Shell and other-channel rows and denials never classify a path. None
    keeps today's behaviour and adds the limitation `GATE_NOT_CONSULTED`.
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
    receipt_times: dict[str, list[datetime]] = {}        # path -> receipt instants (gate rule)
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
            at = _parse_ts(r.get("ts"))
            if at is not None:
                receipt_times.setdefault(path, []).append(at)
    gate_open = _open_gate_attempts(gate_attempts, contract, receipt_times)
    in_scope = sorted(set(intents) | set(receipts) | set(cmp.get("added", [])) | set(cmp.get("modified", []))
                      | set(cmp.get("deleted", [])) | gate_open)
    if gate_attempts is None:
        limitations = [*_LIMITS, *cmp.get("limitations", []), GATE_NOT_CONSULTED]
    else:
        limitations = [*(x for x in _LIMITS if x != _R1_GATE_LIMIT), GATE_SOURCE, *cmp.get("limitations", [])]
    classification: list[dict[str, Any]] = []
    for path in in_scope:
        intent_seq = intents.get(path)
        receipt_seq, sha_receipt = receipts.get(path, (None, None))
        rel = _relate(path)
        if rel is None or _crosses_link(root, rel):          # A-4: state unknown, never guessed
            limitations.append(UNRESOLVABLE.format(path))
            classification.append({"path": path, "class": "matches-neither", "receipt_seq": receipt_seq,
                                   "intent_seq": intent_seq, "sha_now": None, "sha_baseline": None,
                                   "sha_receipt": sha_receipt})
            continue
        sha_now = _sha_now(root, rel)
        base = entries.get(path) if isinstance(entries, dict) else None
        if base is None:   # baseline keys are repo-relative forward-slash strings
            base = next((e for k, e in entries.items()
                         if isinstance(k, str) and pathpolicy.fold(k) == pathpolicy.fold(rel)), None)
        in_baseline = isinstance(base, dict)
        sha_baseline = base.get("sha256") if in_baseline else None
        if (intent_seq is not None and (receipt_seq is None or receipt_seq < intent_seq)) or path in gate_open:
            cls = "attempt-without-completion"
        elif receipt_seq is not None and sha_now is not None and sha_now == sha_receipt:
            cls = "matches-receipt"
        elif (sha_now is not None and sha_now == sha_baseline) or (sha_now is None and not in_baseline):
            cls = "matches-baseline"
        else:
            cls = "matches-neither"
        classification.append({"path": path, "class": cls, "receipt_seq": receipt_seq, "intent_seq": intent_seq,
                               "sha_now": sha_now, "sha_baseline": sha_baseline, "sha_receipt": sha_receipt})
    exceptional = (c for c in classification if c["class"] in EXCEPTION_CLASSES
                   or (c["class"] == "matches-baseline" and c["receipt_seq"] is not None))
    exceptions = [{"id": f"X{i}", "path": c["path"], "class": c["class"]} for i, c in enumerate(exceptional, start=1)]
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
    """§1/§5 with AMENDMENT S1-G1 (FIX2 #37, #8, #52): the recoverable exceptions found now, read-only and outside the
    write transaction: every `export.check` problem (class `export`), with the preserved-temp lines `export.orphans`
    reports carried as class `orphan`, then one item of class `gate-log` when `runtime/gate/gate.db` exists and
    `gatelog.verify` reports a chain problem or the file cannot be read (spec §3B, "an unreadable gate-log tail").
    An absent gate.db, or one with no boundary root, adds nothing. Ids `R1..` in order. The recovery-opened row
    carries no `gate_log` field; the `gate_log` field belongs to reconcile reports only."""
    out: list[dict[str, Any]] = []
    for p in export.check(db_path, job):
        cls = "orphan" if p.startswith(export.PRESERVED) else "export"
        out.append({"id": f"R{len(out) + 1}", "class": cls, "detail": p})
    problem = _gate_log_problem(db_path)
    if problem is not None:
        out.append({"id": f"R{len(out) + 1}", "class": "gate-log", "detail": problem})
    return out


def _gate_log_problem(db_path: str) -> str | None:
    """S1-G1: the gate log's chain problems joined into one detail, or None for an absent or healthy gate.db. A
    failure of the check itself is reported as the gate log being unreadable (fail closed: listed, never hidden)."""
    root = changeset.boundary_root(db_path)
    if root is None:
        return None
    try:
        v = gatelog.verify(root)
    except Exception as exc:  # noqa: BLE001 - an unexpected failure must list the gate log, not stop the recovery
        return f"gate log unreadable: {type(exc).__name__}: {exc}"
    if not v.get("present") or not v.get("chain_problems"):
        return None
    return "; ".join(v["chain_problems"])


def chain_problems(conn: sqlite3.Connection, job: str) -> list[str]:
    """§5 full chain verification: `dbm.verify_chain(conn, job)` + `dbm.integrity(conn, quick=True)`; [] = valid."""
    return [*verify_chain(conn, job), *integrity(conn, quick=True)]


# ---------------------------------------------------------------------------------------------- recovery closure (S2 §4)

def recovery_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The open `recovery-opened` row with its `content` parsed into a dict, or None when no recovery is open."""
    opened = recovery_open(rows)
    if opened is None:
        return None
    row = next(r for r in rows if int(r["seq"]) == opened)
    return {**row, "content": _content(row)}


def acknowledged(rows: list[dict[str, Any]], opened_seq: int) -> dict[str, int]:
    """id -> seq of the first `exception-acknowledged` row after `opened_seq` naming that recovery."""
    out: dict[str, int] = {}
    for r in rows:
        if r["type"] != "exception-acknowledged" or int(r["seq"]) <= opened_seq:
            continue
        c = _content(r)
        if c.get("opened_seq") == opened_seq and isinstance(c.get("id"), str) and c["id"] not in out:
            out[c["id"]] = int(r["seq"])
    return out


def pending_exceptions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Exceptions listed in the open `recovery-opened` row that no `exception-acknowledged` names yet ([] when none
    is open), in listed order."""
    rec = recovery_row(rows)
    if rec is None:
        return []
    done = acknowledged(rows, int(rec["seq"]))
    return [e for e in rec["content"].get("exceptions", []) if isinstance(e, dict) and e.get("id") not in done]


def _turn_names(job: str, turn: object) -> bool:
    return _nonempty_str(turn) and job in str(turn)


def validate_exception_ack(rows: list[dict[str, Any]], *, job: str, content: dict) -> None:
    """`exception-acknowledged` (S2 §1/§4), in order: shape (malformed) -> an open recovery whose seq is
    `opened_seq` (no-recovery) -> `id` listed in that row (unknown-exception) -> not yet acknowledged
    (already-acknowledged) -> `class`/`detail` copied exactly (malformed) -> owner turn names the job
    (no-provenance)."""
    if not isinstance(content, dict) or not _is_int(content.get("opened_seq")) \
            or not _nonempty_str(content.get("id")) or not isinstance(content.get("class"), str) \
            or not isinstance(content.get("detail"), str):
        raise WfRejected("malformed", "exception-acknowledged needs opened_seq (int), id, class and detail")
    rec = recovery_row(rows)
    if rec is None or int(rec["seq"]) != content["opened_seq"]:
        raise WfRejected("no-recovery", f"no open recovery at seq {content['opened_seq']}")
    listed = {e.get("id"): e for e in rec["content"].get("exceptions", []) if isinstance(e, dict)}
    item = listed.get(content["id"])
    if item is None:
        raise WfRejected("unknown-exception", f"exception {content['id']!r} is not listed in recovery-opened seq "
                                              f"{rec['seq']}")
    seen = acknowledged(rows, int(rec["seq"]))
    if content["id"] in seen:
        raise WfRejected("already-acknowledged", f"exception {content['id']!r} acknowledged at seq "
                                                 f"{seen[content['id']]}", acknowledged_seq=seen[content["id"]])
    if content["class"] != item.get("class") or content["detail"] != item.get("detail"):
        raise WfRejected("malformed", f"class and detail must be copied from exception {content['id']!r}")
    if not _turn_names(job, content.get("owner_turn")):
        raise WfRejected("no-provenance", f"the owner turn must name the job {job!r}")


def validate_recover_close(rows: list[dict[str, Any]], *, job: str, content: dict) -> None:
    """`recovery-closed` (S2 §4), in order: shape (malformed) -> open recovery is `opened_seq` (no-recovery) -> every
    listed id acknowledged (unacknowledged, `missing=[...]`) -> `acknowledged` is exactly every id, sorted
    (malformed) -> `chain_head` is the current head (malformed) -> owner turn names the job (no-provenance).
    The whole-chain check stays in the wiring (`chain_problems`)."""
    if not isinstance(content, dict) or not _is_int(content.get("opened_seq")):
        raise WfRejected("malformed", "recovery-closed needs opened_seq (int)")
    rec = recovery_row(rows)
    if rec is None or int(rec["seq"]) != content["opened_seq"]:
        raise WfRejected("no-recovery", f"no open recovery at seq {content['opened_seq']}")
    listed = [e.get("id") for e in rec["content"].get("exceptions", []) if isinstance(e, dict)]
    done = acknowledged(rows, int(rec["seq"]))
    missing = [i for i in listed if i not in done]
    if missing:
        raise WfRejected("unacknowledged", f"exceptions not acknowledged: {missing}", missing=missing)
    if content.get("acknowledged") != sorted(listed):
        raise WfRejected("malformed", f"acknowledged must be every listed id, sorted: {sorted(listed)}")
    head = content.get("chain_head")
    if not isinstance(head, dict) or not _is_int(head.get("seq")) or not isinstance(head.get("self"), str):
        raise WfRejected("malformed", "chain_head must be {seq, self}")
    if rows and (head["seq"] != int(rows[-1]["seq"]) or head["self"] != rows[-1]["self"]):
        raise WfRejected("malformed", "chain_head does not name the current head")
    if not _turn_names(job, content.get("owner_turn")):
        raise WfRejected("no-provenance", f"the owner turn must name the job {job!r}")


def unresolvable_paths(report: dict) -> list[str]:
    """The A-4 paths of one `reconcile-report` content, read from its verbatim `UNRESOLVABLE` limitations."""
    out: list[str] = []
    for line in report.get("limitations", []) if isinstance(report, dict) else []:
        if isinstance(line, str) and line.startswith(_UNRESOLVABLE_PREFIX) and line.endswith(_UNRESOLVABLE_SUFFIX):
            path = line[len(_UNRESOLVABLE_PREFIX):len(line) - len(_UNRESOLVABLE_SUFFIX)]
            if path not in out:
                out.append(path)
    return out


def _int_or_none(content: dict, key: str) -> bool:
    """The key is present and holds an int or None (None = the wiring found no row to cite; WF-001 F1)."""
    return key in content and (content[key] is None or _is_int(content[key]))


def validate_recovery_decision(rows: list[dict[str, Any]], *, job: str, binding: tuple[str, int] | None,
                               stopped_seq: int | None, content: dict) -> None:
    """`recovery-decision` (S2 §1, §6.2 step 4), in the contract's order: malformed (`report_seq` / `stop_seq` may be
    None: the wiring passes None when it finds no row to cite, so the two refusals below stay reachable; any other
    non-int is malformed) -> no-report (no reconcile-report
    for the role) -> stale-report (`report_seq` not the latest) -> termination-unconfirmed (`stopped_seq` None or
    != `stop_seq`) -> invalid-decision (decision outside DECISIONS; restore without `restore_paths`; a restore path
    not classified in the report; `unresolved` not naming every A-4 path of the report exactly once, naming a
    non-A-4 path, or an unknown disposition) -> no-provenance. `binding` is the role's (session, generation) as the
    wiring resolves it; the frozen refusal list derives nothing from it, so it is accepted and not read."""
    del binding
    if not isinstance(content, dict) or not _nonempty_str(content.get("decision")) \
            or content.get("role") != REPORT_ROLE or not _int_or_none(content, "report_seq") \
            or not _int_or_none(content, "stop_seq") or not isinstance(content.get("restore_paths"), list) \
            or not all(isinstance(p, str) for p in content["restore_paths"]) \
            or not isinstance(content.get("unresolved"), list) \
            or not all(isinstance(u, dict) and set(u) == {"path", "disposition"} and isinstance(u["path"], str)
                       and isinstance(u["disposition"], str) for u in content["unresolved"]) \
            or ("note" in content and not isinstance(content["note"], str)):
        raise WfRejected("malformed", "recovery-decision needs decision, role builder, report_seq, stop_seq, "
                                      "restore_paths (list[str]), unresolved (list[{path, disposition}])")
    st = state(rows, REPORT_ROLE)
    if st["latest_report_seq"] is None:
        raise WfRejected("no-report", f"no reconcile report for role {REPORT_ROLE}")
    if content["report_seq"] != st["latest_report_seq"]:
        raise WfRejected("stale-report", f"report_seq {content['report_seq']} is not the latest reconcile report "
                                         f"(seq {st['latest_report_seq']})")
    if stopped_seq is None or stopped_seq != content["stop_seq"]:
        raise WfRejected("termination-unconfirmed", f"stop_seq {content['stop_seq']} is not the stop-confirmed row "
                                                    f"of the role's current session ({stopped_seq!r})")
    if content["decision"] not in DECISIONS:
        raise WfRejected("invalid-decision", f"decision must be one of {list(DECISIONS)}")
    report = st["report"] or {}
    paths = {c.get("path") for c in report.get("classification", []) if isinstance(c, dict)}
    if content["decision"] == "restore" and not content["restore_paths"]:
        raise WfRejected("invalid-decision", "restore needs restore_paths")
    stray = [p for p in content["restore_paths"] if p not in paths]
    if stray:
        raise WfRejected("invalid-decision", f"restore_paths not in report {content['report_seq']}: {stray}")
    a4 = unresolvable_paths(report)
    named = [u["path"] for u in content["unresolved"]]
    bad = [u for u in content["unresolved"] if u["disposition"] not in DISPOSITIONS]
    if bad:
        raise WfRejected("invalid-decision", f"unknown disposition(s) {bad}; allowed {list(DISPOSITIONS)}")
    if sorted(named) != sorted(a4) or len(set(named)) != len(named):
        raise WfRejected("invalid-decision", f"unresolved must name every unresolvable path of report "
                                             f"{content['report_seq']} exactly once: {a4}")
    if not _turn_names(job, content.get("owner_turn")):
        raise WfRejected("no-provenance", f"the owner turn must name the job {job!r}")


def recovery_audit(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every recovery's trail in ledger order: `recovery-opened`, each `exception-acknowledged` (with its `id`),
    `recovery-closed`."""
    out: list[dict[str, Any]] = []
    for r in rows:
        if r["type"] == "exception-acknowledged":
            out.append({"type": r["type"], "seq": int(r["seq"]), "id": _content(r).get("id")})
        elif r["type"] in ("recovery-opened", "recovery-closed"):
            out.append({"type": r["type"], "seq": int(r["seq"])})
    return out


# ---------------------------------------------------------------------------------------------- schemas (§1 / §4)

def validate_content(etype: str, content: dict, contract: dict, grant_state: dict | None) -> None:
    """Schemas of `reconcile-report` (§4), `ack-reconcile` and `recovery-opened` (§1); any defect is
    `rejected(malformed)`. Exact key sets plus the injected `contract_sha256`; `ack-reconcile` may carry `note`."""
    if etype not in CONTENT_TYPES:
        raise WfRejected("malformed", f"{etype!r} is not a reconcile event type")
    if not isinstance(content, dict):
        raise WfRejected("malformed", "content must be a JSON object")
    want, optional = {"reconcile-report": (_REPORT_KEYS, frozenset()), "ack-reconcile": (_ACK_KEYS, _ACK_OPTIONAL),
                      "recovery-opened": (_RECOVERY_KEYS, frozenset()),
                      "exception-acknowledged": (_EXC_ACK_KEYS, _NOTE), "recovery-closed": (_CLOSED_KEYS, frozenset()),
                      "recovery-decision": (_DECISION_KEYS, _NOTE)}[etype]
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
                                               and e["class"] in _EXCEPTION_LIST_CLASSES for e in ex):
            raise WfRejected("malformed", "exceptions must be a list of {id, path, class} with class matches-neither, "
                                          "attempt-without-completion or matches-baseline")
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
    elif etype == "exception-acknowledged":
        if not _is_int(content["opened_seq"]) or content["opened_seq"] < 1 or not _nonempty_str(content["id"]) \
                or not isinstance(content["class"], str) or not isinstance(content["detail"], str) \
                or not _nonempty_str(content["owner_turn"]) or not isinstance(content.get("note", ""), str):
            raise WfRejected("malformed", "exception-acknowledged: opened_seq int >= 1, id, class, detail, owner_turn "
                                          "strings, note optional string")
    elif etype == "recovery-closed":
        head = content["chain_head"]
        if not _is_int(content["opened_seq"]) or content["opened_seq"] < 1 \
                or not isinstance(content["acknowledged"], list) \
                or not all(isinstance(i, str) for i in content["acknowledged"]) \
                or content["acknowledged"] != sorted(content["acknowledged"]) \
                or not isinstance(head, dict) or not _is_int(head.get("seq")) or not isinstance(head.get("self"), str) \
                or not _nonempty_str(content["owner_turn"]):
            raise WfRejected("malformed", "recovery-closed: opened_seq int >= 1, acknowledged sorted list[str], "
                                          "chain_head {seq, self}, owner_turn")
    elif etype == "recovery-decision":
        if not _nonempty_str(content["decision"]) or not _nonempty_str(content["role"]) \
                or not _is_int(content["report_seq"]) or not _is_int(content["stop_seq"]) \
                or not isinstance(content["restore_paths"], list) \
                or not all(isinstance(p, str) for p in content["restore_paths"]) \
                or not isinstance(content["unresolved"], list) \
                or not all(isinstance(u, dict) and set(u) == {"path", "disposition"} for u in content["unresolved"]) \
                or not _nonempty_str(content["owner_turn"]) or not isinstance(content.get("note", ""), str):
            raise WfRejected("malformed", "recovery-decision: decision, role, report_seq, stop_seq, restore_paths, "
                                          "unresolved [{path, disposition}], owner_turn; note optional")
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
