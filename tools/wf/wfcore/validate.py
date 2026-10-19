"""Validator (FB1.4 D2): R07 contract fields, event-type schemas, §5 message rules against the head."""
from __future__ import annotations

import fnmatch
import json
import os
import re
import sqlite3
import time
from typing import Any

from .canon import canonical, event_self_hash, sha256_text
from .db import WfRejected, rows

ROLES = ("coordinator", "builder", "reviewer", "owner")
ROLE_FOR_TYPE: dict[str, tuple[str, ...]] = {
    "contract": ("coordinator",),
    "grant": ("coordinator",),
    "intent": ("builder",),
    "receipt": ("builder",),
    "blocker": ("coordinator", "builder", "reviewer"),
    "handoff": ("builder", "coordinator"),
    "delivered": ("builder",),
    "accept": ("reviewer",),
    "close": ("coordinator",),
    # B2 slice 1 (INTERFACE-CONTRACT.md section 7, additive; carried by the S1R delivery, registered in OWNERS.md)
    "bind": ("coordinator",),
    "ack-assignment": ("coordinator", "builder", "reviewer"),
    "stop-confirmed": ("coordinator",),
    "reconcile-report": ("coordinator",),
    "ack-reconcile": ("coordinator", "builder", "reviewer"),
    "recovery-opened": ("coordinator",),
    "exception-acknowledged": ("coordinator",),
    "recovery-closed": ("coordinator",),
    "recovery-decision": ("coordinator",),
    "grant-revoked": ("coordinator", "builder", "reviewer", "owner"),
    "grant-expired": ("coordinator", "owner"),
    "job-cancelled": ("coordinator", "builder", "reviewer", "owner"),
    "intervention": ("coordinator", "owner"),
}
APPEND_TYPES = ("intent", "receipt", "blocker", "handoff", "delivered", "ack-assignment", "stop-confirmed",
                "ack-reconcile")
ENVIRONMENT = "disposable local scope"

CONTRACT_REQUIRED: dict[str, type | tuple[type, ...]] = {
    "owner_request": dict,            # {"quote": str, "source_turn": str}
    "interpretation": str,
    "baseline": dict,                 # {"manifest": [...], "git_revision": str|None, "uncommitted_state": bool|None}
    "outcome": str,
    "acceptance_criteria": list,      # [{"id","check","expected"}]
    "read_scope": list,
    "write_scope": list,
    "watch": list,
    "presence_only": list,
    "allowed_tools": dict,            # {role: [tool,...]}
    "limits": (str, dict, list),
    "exclusions": list,
    "evidence": list,
    "closeout_rules": (str, dict, list),
    "recovery_rules": (str, dict, list),
    "observe_thresholds": dict,
    "environment": str,
}

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def now_ts() -> str:
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int((t % 1) * 1000):03d}Z"


def check_job_id(job: str) -> None:
    if not _ID.match(job or ""):
        raise WfRejected("malformed", f"job id {job!r} must match {_ID.pattern}")


_DEVICE = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", re.IGNORECASE)


def check_new_job_id(job: str, existing: list[str]) -> None:
    """PACKAGE-FIX1 I1, at `job new` only: a job id names directories (`jobs/<id>/`, `ledger/pending/<id>/`), so it is
    refused when a filesystem would merge it with another name or a device, on every platform (fail closed, as spec
    section 4.4 names scope paths): a trailing dot or space, `..` anywhere, a Windows device name with or without an
    extension, or an existing id that differs only by case."""
    check_job_id(job)
    if job.endswith((".", " ")):
        raise WfRejected("malformed", f"job id {job!r} must not end in a dot or a space")
    if ".." in job:
        raise WfRejected("malformed", f"job id {job!r} must not contain '..'")
    if _DEVICE.match(job):
        raise WfRejected("malformed", f"job id {job!r} is a Windows device name")
    for other in existing:
        if other != job and other.casefold() == job.casefold():
            raise WfRejected("malformed", f"job id {job!r} equals existing job {other!r} case-insensitively")


def validate_contract(c: Any) -> None:
    if not isinstance(c, dict):
        raise WfRejected("malformed", "contract must be a JSON object")
    for key, typ in CONTRACT_REQUIRED.items():
        if key not in c:
            raise WfRejected("malformed", f"contract missing field {key!r}")
        if not isinstance(c[key], typ):
            raise WfRejected("malformed", f"contract field {key!r} has wrong type")
    oreq = c["owner_request"]
    if not isinstance(oreq.get("quote"), str) or not oreq["quote"].strip():
        raise WfRejected("malformed", "owner_request.quote must be a non-empty verbatim string")
    if not isinstance(oreq.get("source_turn"), str) or not oreq["source_turn"].strip():
        raise WfRejected("no-provenance", "owner_request.source_turn is required")
    if c["environment"] != ENVIRONMENT:
        raise WfRejected("malformed", f"environment must be {ENVIRONMENT!r}")
    if not c["acceptance_criteria"]:
        raise WfRejected("malformed", "at least one acceptance criterion is required")
    seen = set()
    for crit in c["acceptance_criteria"]:
        if not isinstance(crit, dict) or not all(isinstance(crit.get(k), str) and crit[k].strip() for k in ("id", "check", "expected")):
            raise WfRejected("malformed", "each acceptance criterion needs string id, check (runnable) and expected")
        if crit["id"] in seen:
            raise WfRejected("malformed", f"duplicate criterion id {crit['id']!r}")
        seen.add(crit["id"])
    if not isinstance(c["baseline"].get("manifest"), list):
        raise WfRejected("malformed", "baseline.manifest must be a list")
    for role, tools in c["allowed_tools"].items():
        if role not in ROLES or not isinstance(tools, list):
            raise WfRejected("malformed", f"allowed_tools: unknown role {role!r} or tools not a list")
    for key in ("read_scope", "write_scope", "watch", "presence_only", "exclusions"):
        if not all(isinstance(x, str) for x in c[key]):
            raise WfRejected("malformed", f"{key} entries must be strings")


def _req(content: dict, key: str, typ: type | tuple[type, ...], nonempty: bool = True) -> Any:
    if key not in content:
        raise WfRejected("malformed", f"content missing {key!r}")
    v = content[key]
    if not isinstance(v, typ):
        raise WfRejected("malformed", f"content field {key!r} has wrong type")
    if nonempty and isinstance(v, (str, list, dict)) and not v:
        raise WfRejected("malformed", f"content field {key!r} is empty")
    return v


#: Review WF-027 S5: fold case only where the platform folds it (Windows), as FIX1 P1 does in pathpolicy
#: (`CASE_FOLDING`/`fold` there, lane FXM). The same rule, kept here so lane FXL needs no FXM file; tests patch it.
CASE_FOLDING = os.path.normcase("A") == "a"


def _fold(s: str) -> str:
    return s.lower() if CASE_FOLDING else s


def _path_in_scope(path: str, scope: list[str]) -> bool:
    p = _fold(path.replace("/", "\\"))
    for g in scope:
        gg = _fold(g.replace("/", "\\"))
        if fnmatch.fnmatchcase(p, gg) or p == gg or p.startswith(gg.rstrip("*\\") + "\\"):
            return True
    return False


def validate_content(etype: str, content: dict, contract: dict, grant_state: dict | None) -> None:
    """Event-type schema checks. `grant_state` = latest grant content (or None)."""
    if etype == "contract":
        _req(content, "contract", dict)
    elif etype == "grant":
        _req(content, "grant_id", str)
        state = _req(content, "state", str)
        if state not in ("proposed", "activated"):
            raise WfRejected("malformed", f"grant state {state!r} not in this slice")
        for k in ("actions", "resources", "exclusions"):
            _req(content, k, list, nonempty=(k != "exclusions"))
        _req(content, "ars", dict)
        _req(content, "delegation", dict, nonempty=False)
        _req(content, "expiry", str)
        _req(content, "revocation_rule", str)
        if state == "activated":
            bh = _req(content, "block_hash", str)
            if not _HEX64.match(bh):
                raise WfRejected("malformed", "block_hash must be 64 hex")
            _req(content, "source_turn", str)
            _req(content, "quote", str)
        for res in content["resources"]:
            if not _path_in_scope(res, contract["write_scope"]) and res not in contract["write_scope"]:
                raise WfRejected("widening", f"grant resource {res!r} is outside the contract write scope")
    elif etype == "intent":
        _req(content, "path", str)
        _req(content, "sha_before", str, nonempty=False)
        _req(content, "note", str)
    elif etype == "receipt":
        _req(content, "path", str)
        _req(content, "sha_before", str, nonempty=False)
        _req(content, "sha_after", str, nonempty=False)
        _req(content, "note", str)
    elif etype == "blocker":
        _req(content, "reason", str)
    elif etype == "handoff":
        to_role = _req(content, "to_role", str)
        if to_role not in ROLES:
            raise WfRejected("malformed", f"handoff to unknown role {to_role!r}")
        ws = _req(content, "write_scope", list, nonempty=False)
        for p in ws:
            if not isinstance(p, str) or not _path_in_scope(p, contract["write_scope"]):
                raise WfRejected("widening", f"handoff write_scope {p!r} exceeds the contract write scope")
        acts = content.get("actions", [])
        if acts:
            allowed = set((grant_state or {}).get("actions", [])) if grant_state and grant_state.get("state") == "activated" else set()
            for a in acts:
                if a not in allowed:
                    raise WfRejected("widening", f"handoff action {a!r} is not in the activated grant")
        _req(content, "note", str)
    elif etype == "delivered":
        claims = _req(content, "claims", list)
        for cl in claims:
            if not isinstance(cl, dict) or not isinstance(cl.get("claim"), str) or not cl["claim"].strip():
                raise WfRejected("malformed", "each claim needs a 'claim' string")
            ev = cl.get("evidence")
            if not isinstance(ev, list) or not ev or not all(isinstance(e, str) and e.strip() for e in ev):
                raise WfRejected("malformed", f"claim {cl.get('claim')!r} has no evidence refs")
    elif etype == "accept":
        if _req(content, "decision", str) not in ("accepted", "rejected"):
            raise WfRejected("malformed", "decision must be accepted|rejected")
        _req(content, "reason", str, nonempty=False)
        _req(content, "criteria", list, nonempty=False)
        _req(content, "environment", str)
        _req(content, "limits", str)
        _req(content, "changed_paths", (list, dict), nonempty=False)
        _req(content, "builder_session", str, nonempty=False)
    elif etype == "close":
        _req(content, "intervention", str)
        if _req(content, "basis", str) not in ("accepted", "abandoned"):
            raise WfRejected("malformed", "close basis must be accepted|abandoned")
        _req(content, "verify", str)
    elif etype in ("bind", "ack-assignment", "stop-confirmed"):
        from . import bind  # B2 slice 1, section 7: S1B schemas (module delivered by lane S1B)
        bind.validate_content(etype, content, contract, grant_state)
    elif etype in ("reconcile-report", "ack-reconcile", "recovery-opened", "exception-acknowledged", "recovery-closed",
                   "recovery-decision"):
        from . import reconcile  # B2 slice 1, section 7: S1R schemas
        reconcile.validate_content(etype, content, contract, grant_state)
    elif etype in ("grant-revoked", "grant-expired", "job-cancelled", "intervention"):
        from . import grant  # B2 slice 3, section 7
        grant.validate_content(etype, content, contract, grant_state)
    else:
        raise WfRejected("malformed", f"unknown event type {etype!r}")


CONFLICT_REASON = "claimant-conflict"
CONFLICT_MSG_PREFIX = "claimant-conflict:"


def is_conflict_row(r: dict[str, Any]) -> bool:
    """A conflict blocker (FB1.4-A4 §3): type blocker, reason claimant-conflict."""
    if r["type"] != "blocker":
        return False
    try:
        return json.loads(r["content"]).get("reason") == CONFLICT_REASON
    except (ValueError, AttributeError):
        return False


def existing_state(conn: sqlite3.Connection, job: str) -> dict[str, Any]:
    """Derived facts needed by the message rules: msg_ids, role bindings, generations, grant, closed,
    blocked roles (conflict rows feed neither bind nor builder_session)."""
    st: dict[str, Any] = {"msg": {}, "bind": {}, "gen": {}, "grant": None, "closed": False,
                          "builder_session": None, "accept": None, "delivered": False, "blocked_roles": set(),
                          "msg_seq": {}}
    for r in rows(conn, job):
        st["msg"][r["msg_id"]] = r["content_sha256"]
        st["msg_seq"][r["msg_id"]] = r["seq"]
        if is_conflict_row(r):
            st["blocked_roles"].add(json.loads(r["content"]).get("role"))
            continue
        key = (r["author_role"], r["generation"])
        st["bind"].setdefault(key, r["session_id"])
        st["gen"][r["author_role"]] = max(st["gen"].get(r["author_role"], 0), r["generation"])
        if r["type"] == "grant":
            st["grant"] = json.loads(r["content"])
        if r["type"] == "close":
            st["closed"] = True
        if r["author_role"] == "builder":
            st["builder_session"] = r["session_id"]
        if r["type"] == "accept":
            st["accept"] = json.loads(r["content"])
        if r["type"] == "delivered":
            st["delivered"] = True
    return st


class DuplicateIgnored(Exception):
    """Same msg_id and same content: no row, exit 0."""


class WfClaimantConflict(WfRejected):
    """Second claimant for a bound (role, generation). Carries what txn needs to record the conflict row
    inside the same reservation (FB1.4-A4 §3)."""

    def __init__(self, *, role: str, generation: int, holder_session: str, claimant_session: str,
                 attempted_type: str, attempted_msg_id: str, contract_sha: str):
        super().__init__("claimant-conflict",
                         f"role {role} generation {generation} is held by another session")
        self.role = role
        self.generation = generation
        self.holder_session = holder_session
        self.claimant_session = claimant_session
        self.attempted_type = attempted_type
        self.attempted_msg_id = attempted_msg_id
        self.contract_sha = contract_sha

    def conflict_content(self) -> dict[str, Any]:
        return {"reason": CONFLICT_REASON, "role": self.role, "generation": self.generation,
                "holder_session": self.holder_session, "claimant_session": self.claimant_session,
                "attempted_type": self.attempted_type, "attempted_msg_id": self.attempted_msg_id,
                "contract_sha256": self.contract_sha}

    def conflict_msg_id(self) -> str:
        return CONFLICT_MSG_PREFIX + self.attempted_msg_id


def conflict_row(exc: WfClaimantConflict, job: str, head_seq: int, head_self: str) -> dict[str, Any]:
    """The conflict blocker row at seq head+1: the rejected claimant's role/session/generation."""
    content_text = canonical(exc.conflict_content())
    row = {
        "job": job, "seq": head_seq + 1, "type": "blocker", "msg_id": exc.conflict_msg_id(),
        "author_role": exc.role, "session_id": exc.claimant_session, "generation": exc.generation,
        "reply_to": None, "grant_ref": None, "ts": now_ts(), "content": content_text,
        "content_sha256": sha256_text(content_text), "prev": head_self,
    }
    row["self"] = event_self_hash(row)
    return row


def conflict_row_from_marker(marker: dict[str, Any]) -> dict[str, Any]:
    """Re-create the conflict row from a `conflict` marker (resolve NOT-COMMITTED re-record). The ts is the
    marker's `ts`, so the hash is recomputed from the recorded fields."""
    c = marker["conflict"]
    content_text = canonical(c)
    row = {
        "job": marker["job"], "seq": marker["seq"], "type": "blocker",
        "msg_id": CONFLICT_MSG_PREFIX + c["attempted_msg_id"], "author_role": c["role"],
        "session_id": c["claimant_session"], "generation": c["generation"], "reply_to": None, "grant_ref": None,
        "ts": marker["row_ts"], "content": content_text, "content_sha256": sha256_text(content_text),
        "prev": marker["prev"],
    }
    row["self"] = event_self_hash(row)
    return row


def build_row(conn: sqlite3.Connection, job: str, head_seq: int, head_self: str, *, etype: str, msg_id: str,
              author_role: str, session_id: str, generation: int, reply_to: str | None, grant_ref: str | None,
              content: dict, contract_sha: str | None, contract_content: dict | None) -> dict[str, Any]:
    """Apply §5 rules and schemas; return the complete row (seq = head+1)."""
    for name, val in (("msg_id", msg_id), ("author_role", author_role), ("session_id", session_id)):
        if not isinstance(val, str) or not val.strip():
            raise WfRejected("malformed", f"missing field {name}")
    if not isinstance(generation, int) or generation < 1:
        raise WfRejected("malformed", "generation must be an integer >= 1")
    if author_role not in ROLES:
        raise WfRejected("malformed", f"unknown role {author_role!r}")
    if etype not in ROLE_FOR_TYPE:
        raise WfRejected("malformed", f"unknown event type {etype!r}")
    if author_role not in ROLE_FOR_TYPE[etype]:
        raise WfRejected("role", f"{etype} may not be written by role {author_role}")
    if not isinstance(content, dict):
        raise WfRejected("malformed", "content must be a JSON object")
    if etype == "contract":
        if head_seq != 0:
            raise WfRejected("malformed", "contract event must be the first event")
    else:
        if head_seq == 0 or contract_sha is None or contract_content is None:
            raise WfRejected("malformed", "job has no contract")
    content = dict(content)
    if contract_sha is not None:
        content["contract_sha256"] = contract_sha
    if content.get("contract_sha256") is None:
        raise WfRejected("malformed", "contract pin missing")
    st = existing_state(conn, job)
    if st["closed"]:
        raise WfRejected("closed", "job is closed")
    if etype == "blocker" and content.get("reason") == CONFLICT_REASON:
        raise WfRejected("reserved-reason", f"blocker reason {CONFLICT_REASON!r} is written by wf only")
    if msg_id.startswith(CONFLICT_MSG_PREFIX):
        raise WfRejected("reserved-reason", f"msg_id prefix {CONFLICT_MSG_PREFIX!r} is written by wf only")
    content_text = canonical(content)
    csha = sha256_text(content_text)
    if msg_id in st["msg"]:
        if st["msg"][msg_id] == csha:
            raise DuplicateIgnored(msg_id)
        raise WfRejected("conflict", f"msg_id {msg_id} already recorded with different content")
    if (CONFLICT_MSG_PREFIX + msg_id) in st["msg"]:
        # Repeated conflicting attempt: its conflict row is already recorded; no second row, still exit 1.
        raise WfRejected("claimant-conflict", f"conflict for {msg_id} already recorded",
                         conflict_seq=st["msg_seq"][CONFLICT_MSG_PREFIX + msg_id], conflict_outcome="DUPLICATE-IGNORED")
    if author_role in st["blocked_roles"]:
        raise WfRejected("role-blocked", f"role {author_role} is blocked by a claimant conflict (any session, any generation)")
    if etype == "accept" and st["blocked_roles"]:
        raise WfRejected("role-blocked", f"accept refused while roles are blocked: {sorted(st['blocked_roles'])}")
    cur_gen = st["gen"].get(author_role, 0)
    if generation < cur_gen:
        raise WfRejected("stale", f"generation {generation} below current {cur_gen} for role {author_role}")
    bound = st["bind"].get((author_role, generation))
    if bound is not None and bound != session_id:
        raise WfClaimantConflict(role=author_role, generation=generation, holder_session=bound,
                                 claimant_session=session_id, attempted_type=etype, attempted_msg_id=msg_id,
                                 contract_sha=content["contract_sha256"])
    validate_content(etype, content, contract_content or content.get("contract", {}), st["grant"])
    row = {
        "job": job, "seq": head_seq + 1, "type": etype, "msg_id": msg_id, "author_role": author_role,
        "session_id": session_id, "generation": generation, "reply_to": reply_to, "grant_ref": grant_ref,
        "ts": now_ts(), "content": content_text, "content_sha256": csha, "prev": head_self,
    }
    row["self"] = event_self_hash(row)
    return row


# ---------------------------------------------------------------- accept declarations (T24, lane A)

def accept_declarations(results: dict, contract: dict, contract_sha: str) -> list[str]:
    """T24 (spec line 299): compare the declarations in an accept `results` object with the job contract.

    Seam contract (frozen caller: commands.accept): returns reason strings; a non-empty list makes the acceptance
    decision "rejected" and the strings are appended to the recorded reason. Must not raise for ordinary input.

    Owner behaviour decisions (2026-10-13), binding for lane A:
      - Required declarations are `results["contract_sha256"]` (must equal `contract_sha`) and `results["baseline"]`
        (its manifest / git_revision / uncommitted_state must match `contract["baseline"]`).
      - A missing OR mismatching required declaration produces a rejection reason that names the field.
        Absent-declaration policy is decided: absent = rejected.
      - The existing reviewer / role / environment / criteria / path checks in commands.accept stay as they are.

    Seam state (before lane A): returns [] for every input, so pre-seam behaviour is unchanged. Lane A replaces this
    body only; signature and return type are fixed.
    """
    reasons: list[str] = []
    if not isinstance(results, dict):
        return ["declaration: results is not an object"]

    # contract_sha256: present, a string, equal to the pinned contract hash
    if "contract_sha256" not in results:
        reasons.append("declaration missing: contract_sha256")
    else:
        declared = results["contract_sha256"]
        if not isinstance(declared, str):
            reasons.append("declaration mismatch: contract_sha256 is not a string")
        elif declared != contract_sha:
            reasons.append("declaration mismatch: contract_sha256 does not equal the job contract hash")

    # baseline: present, an object, and every field (manifest, git_revision, uncommitted_state and any other
    # field on either side) canonically equal to the contract's baseline
    want = contract.get("baseline") if isinstance(contract, dict) else None
    if "baseline" not in results:
        reasons.append("declaration missing: baseline")
    elif not isinstance(results["baseline"], dict):
        reasons.append("declaration mismatch: baseline is not an object")
    elif not isinstance(want, dict):
        reasons.append("declaration mismatch: baseline (contract baseline is not an object)")
    else:
        got = results["baseline"]
        required = ("manifest", "git_revision", "uncommitted_state")
        for field in (*required, *sorted((set(got) | set(want)) - set(required))):
            if field not in got:
                reasons.append(f"declaration missing: baseline.{field}")
                continue
            if field not in want:
                reasons.append(f"declaration mismatch: baseline.{field} is not in the contract baseline")
                continue
            try:
                same = canonical(got[field]) == canonical(want[field])
            except (TypeError, ValueError):
                same = False
            if not same:
                reasons.append(f"declaration mismatch: baseline.{field} does not match the contract baseline")
    return reasons
