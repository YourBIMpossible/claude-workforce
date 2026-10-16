"""Explicit role binding and the ARS (B2 slice 1, lane S1B; spec FIRST-RELEASE-SPEC.md Rev 5.3 §2B l.34-46, §4.1
l.169, §5 rules 4-5, §6.2 step 2, case T32 l.307). Frozen interface: GitHub issue #27 "B2 slice 1 - frozen interface
contract" §1 (event schemas), §2 (ARS schema), §3 (preconditions, actor check, bootstrap), §6 (signatures).

What this module decides, as pure checks over rows already in the ledger (it never writes; White's wiring calls it
inside the write transaction, before `validate.build_row`):

* `active_roles` - the explicit binding table, built from `bind`, `ack-assignment` and `stop-confirmed` rows in seq
  order. An empty table is IMPLICIT MODE: no `wf bind` ever ran for the job, and every check here is a no-op, so the
  existing observed-header rules in `validate.build_row` (claimant conflict, stale generation, role-blocked ...) are
  the only rules, exactly as before. A job never returns to implicit mode.
* `validate_bind` - the `wf bind` preconditions in the contract's order (§3); returns the `bind` row content.
* `check_actor` - explicit-mode actor check for every role-bearing mutation (§3): unbound role, stopped session,
  stale / unbound higher generation, and a foreign session at the bound generation (raised as
  `validate.WfClaimantConflict` so the existing conflict blocker row and blocking apply unchanged).
* `check_ack_assignment`, `check_stop_confirmed`, `validate_content` - type-specific rules and schemas (§1, §3).
* `digest_rows` - the binding table for `wf digest` (§4.1 l.182).

Not here (other lanes / excluded by the owner): reconcile and recover (S1R), command wiring and CLI (S1W),
replacement / generation+1 (T33), the gate (T13), time-based expiry. A second binding for a role is refused
(`rejected(bound)`), so the table holds at most one binding per role.

Recorded decisions (issue #27 §9): D-S1-1 the bootstrap identity is the contract author session; D-S1-2 an
`ack-assignment` is recorded and shown, and is not a precondition for the session's other events.
"""
from __future__ import annotations

import fnmatch
import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from .db import WfRejected, rows
from .validate import WfClaimantConflict, existing_state

__all__ = ["ARS_ROLES", "CONTENT_TYPES", "Binding", "parse_ars", "declared_subset", "active_roles", "validate_bind",
           "check_actor", "validate_content", "check_ack_assignment", "check_stop_confirmed", "digest_rows"]

ARS_ROLES: tuple[str, ...] = ("coordinator", "builder", "reviewer")
CONTENT_TYPES: tuple[str, ...] = ("bind", "ack-assignment", "stop-confirmed")
ROLE_SPEC_KEYS: tuple[str, ...] = ("tools", "actions", "paths")
SLICE_GENERATION = 1  # generation+1 is replacement (T33), outside this slice

#: §1 schemas: exact key sets (the wiring injects `contract_sha256` into every row's content before validation).
_SCHEMA_KEYS: dict[str, frozenset[str]] = {
    "bind": frozenset({"role", "session", "generation", "tools", "paths", "grant_seq"}),
    "ack-assignment": frozenset({"role", "session", "bind_seq"}),
    "stop-confirmed": frozenset({"role", "session", "generation", "how", "when", "owner_check", "owner_turn"}),
}
_INJECTED = frozenset({"contract_sha256"})


@dataclass(frozen=True)
class Binding:
    role: str
    session: str
    generation: int
    bind_seq: int
    ack_seq: int | None
    stopped_seq: int | None


# ---------------------------------------------------------------------------------------------- small helpers

def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _str_list(v: object) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def _nonempty_str(v: object) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _content(r: dict[str, Any]) -> dict[str, Any]:
    try:
        c = json.loads(r["content"])
    except (TypeError, ValueError):
        return {}
    return c if isinstance(c, dict) else {}


def _recovery_open(rs: list[dict[str, Any]]) -> bool:
    """§3B l.131/136: a `recovery-opened` row with no later `recovery-closed` (none exists in this slice)."""
    opened = closed = 0
    for r in rs:
        if r["type"] == "recovery-opened":
            opened = r["seq"]
        elif r["type"] == "recovery-closed":
            closed = r["seq"]
    return opened > closed


# ---------------------------------------------------------------------------------------------- ARS (§2)

def parse_ars(ars: object) -> dict[str, dict[str, list[str]]]:
    """`grant.ars` -> {role: {"tools", "actions", "paths"}}. Fails closed with `rejected(ars)`: `ars` must be an
    object with a non-empty `roles` object; every role key one of ARS_ROLES; every role spec an object holding
    exactly lists of strings under `tools`, `actions` and `paths` (all three required). Other top-level keys
    (`session_requirements`, `replacement`) are informational in this slice and are not interpreted."""
    if not isinstance(ars, dict):
        raise WfRejected("ars", "grant ars is not an object")
    roles = ars.get("roles")
    if not isinstance(roles, dict) or not roles:
        raise WfRejected("ars", "grant ars has no non-empty 'roles' object")
    out: dict[str, dict[str, list[str]]] = {}
    for role, spec in roles.items():
        if role not in ARS_ROLES:
            raise WfRejected("ars", f"ars role {role!r} is not one of {list(ARS_ROLES)}")
        if not isinstance(spec, dict):
            raise WfRejected("ars", f"ars role {role!r}: spec is not an object")
        for key in ROLE_SPEC_KEYS:
            if key not in spec:
                raise WfRejected("ars", f"ars role {role!r}: missing {key!r}")
            if not _str_list(spec[key]):
                raise WfRejected("ars", f"ars role {role!r}: {key!r} must be a list of strings")
        out[role] = {k: list(spec[k]) for k in ROLE_SPEC_KEYS}
    return out


def declared_subset(declared_tools: list[str], declared_paths: list[str], role_spec: dict) -> list[str]:
    """§2 subset rule. [] = every declared tool is in the role's ARS tools and every declared path glob equals an ARS
    glob or `fnmatch.fnmatchcase`-matches one (pure string match, no filesystem). Otherwise one reason per item."""
    reasons: list[str] = []
    tools = role_spec.get("tools", []) if isinstance(role_spec, dict) else []
    globs = role_spec.get("paths", []) if isinstance(role_spec, dict) else []
    for t in declared_tools:
        if not isinstance(t, str) or t not in tools:
            reasons.append(f"tool {t!r} is not in the ARS tools {list(tools)}")
    for g in declared_paths:
        if not isinstance(g, str) or not (g in globs or any(fnmatch.fnmatchcase(g, a) for a in globs)):
            reasons.append(f"path {g!r} is not inside the ARS paths {list(globs)}")
    return reasons


# ---------------------------------------------------------------------------------------------- binding table (§3)

def active_roles(conn: sqlite3.Connection, job: str) -> dict[str, Binding]:
    """The explicit binding table; {} = implicit mode. Works on a write-transaction or read-snapshot connection.

    `bind` rows (author coordinator) create a binding; only the first per role counts (a second is refused at bind
    time). An `ack-assignment` sets `ack_seq` when its role, session and `bind_seq` match the binding (first match).
    A `stop-confirmed` sets `stopped_seq` when its role, session and generation match the binding (first match)."""
    table: dict[str, Binding] = {}
    for r in rows(conn, job):
        t = r["type"]
        if t not in CONTENT_TYPES:
            continue
        c = _content(r)
        role = c.get("role")
        if t == "bind":
            if r["author_role"] != "coordinator" or role not in ARS_ROLES or role in table:
                continue
            gen = c.get("generation")
            if not _nonempty_str(c.get("session")) or not _is_int(gen):
                continue
            table[role] = Binding(role, c["session"], gen, r["seq"], None, None)
            continue
        b = table.get(role) if isinstance(role, str) else None
        if b is None:
            continue
        if t == "ack-assignment":
            if b.ack_seq is None and c.get("session") == b.session and c.get("bind_seq") == b.bind_seq:
                table[role] = Binding(b.role, b.session, b.generation, b.bind_seq, r["seq"], b.stopped_seq)
        elif t == "stop-confirmed":
            if b.stopped_seq is None and c.get("session") == b.session and c.get("generation") == b.generation:
                table[role] = Binding(b.role, b.session, b.generation, b.bind_seq, b.ack_seq, r["seq"])
    return table


def validate_bind(conn: sqlite3.Connection, job: str, *, role: str, session: str, generation: int,
                  tools: list[str], paths: list[str], author_session: str, grant: dict | None,
                  grant_seq: int | None, state: dict) -> dict:
    """`wf bind` preconditions in the contract's §3 order; returns the `bind` row content (§1). Each failure is a
    `WfRejected` and nothing is written. Defaults for omitted --tool/--path are applied by the caller (wiring).

    Order: input shape (malformed) -> job has a contract (malformed) -> not closed (closed) -> recovery not open
    (recovery-open) -> actor (explicit mode: the author is the bound, not-stopped coordinator: unbound-session /
    stopped-session) -> activated grant (no-grant) -> ARS parses and names the role (ars) -> declared tools/paths
    inside the ARS (ars) -> role not already bound (bound) -> bootstrap when the table is empty (bootstrap) ->
    reviewer precondition (reviewer-precondition) -> generation 1 (generation)."""
    if role not in ARS_ROLES:
        raise WfRejected("malformed", f"bind role {role!r} is not one of {list(ARS_ROLES)}")
    if not _nonempty_str(session):
        raise WfRejected("malformed", "bind session must be a non-empty string")
    if not _nonempty_str(author_session):
        raise WfRejected("malformed", "invoking session must be a non-empty string")
    if not _is_int(generation):
        raise WfRejected("malformed", "bind generation must be an integer")
    if not _str_list(tools) or not _str_list(paths):
        raise WfRejected("malformed", "bind tools and paths must be lists of strings")
    rs = rows(conn, job)
    if not rs or rs[0]["type"] != "contract":
        raise WfRejected("malformed", "job has no contract")
    if state.get("closed"):
        raise WfRejected("closed", "job is closed")
    if _recovery_open(rs):
        raise WfRejected("recovery-open", "a recovery is open for this job; no binding is written")
    table = active_roles(conn, job)
    if table:
        coord = table.get("coordinator")
        if coord is None or coord.session != author_session:
            raise WfRejected("unbound-session", f"session {author_session!r} is not the bound coordinator")
        if coord.stopped_seq is not None:
            raise WfRejected("stopped-session", f"coordinator session {author_session!r} was stopped "
                                                f"(stop-confirmed seq {coord.stopped_seq})")
    if not isinstance(grant, dict) or grant.get("state") != "activated" or not _is_int(grant_seq):
        raise WfRejected("no-grant", "binding requires an activated grant")
    ars = parse_ars(grant.get("ars"))
    if role not in ars:
        raise WfRejected("ars", f"role {role!r} is not in the grant's ARS")
    reasons = declared_subset(tools, paths, ars[role])
    if reasons:
        raise WfRejected("ars", "; ".join(reasons))
    if role in table:
        b = table[role]
        raise WfRejected("bound", f"role {role} is already bound to session {b.session!r} generation {b.generation} "
                                  f"(bind seq {b.bind_seq}); replacement (generation+1) is outside this slice (T33)")
    if not table:
        contract_session = rs[0]["session_id"]
        if role != "coordinator" or session != author_session or session != contract_session:
            raise WfRejected("bootstrap", "the first binding must bind role coordinator to the invoking session, "
                                          "which must be the contract author session (D-S1-1)")
    if role == "reviewer":
        if not state.get("delivered"):
            raise WfRejected("reviewer-precondition", "a reviewer is bound only after delivery")
        builder = table["builder"].session if "builder" in table else state.get("builder_session")
        if builder is not None and session == builder:
            raise WfRejected("reviewer-precondition", f"reviewer session {session!r} is the builder session")
    if generation != SLICE_GENERATION:
        raise WfRejected("generation", f"generation {generation}: only generation {SLICE_GENERATION} is bound in "
                                       "this slice (generation+1 is replacement, T33)")
    return {"role": role, "session": session, "generation": generation, "tools": list(tools), "paths": list(paths),
            "grant_seq": grant_seq}


def check_actor(conn: sqlite3.Connection, job: str, *, role: str, session: str, generation: int, etype: str,
                msg_id: str, contract_sha: str) -> None:
    """Explicit-mode actor check (§3), run before `validate.build_row`. No-op in implicit mode and for role owner.
    unbound role -> rejected(unbound-session); the stopped session of its binding -> rejected(stopped-session);
    generation below the binding -> rejected(stale); above -> rejected(unbound-session); a different session at the
    bound generation -> WfClaimantConflict (txn records the existing conflict blocker; both are blocked)."""
    table = active_roles(conn, job)
    if not table or role == "owner":
        return
    b = table.get(role)
    if b is None:
        raise WfRejected("unbound-session", f"role {role} has no binding (unbound role use)")
    if b.stopped_seq is not None and session == b.session:
        raise WfRejected("stopped-session", f"session {session!r} was stopped for role {role} "
                                            f"(stop-confirmed seq {b.stopped_seq})")
    if generation < b.generation:
        raise WfRejected("stale", f"generation {generation} below the bound generation {b.generation} for role {role}")
    if generation > b.generation:
        raise WfRejected("unbound-session", f"role {role} has no binding at generation {generation}")
    if session != b.session:
        raise WfClaimantConflict(role=role, generation=generation, holder_session=b.session, claimant_session=session,
                                 attempted_type=etype, attempted_msg_id=msg_id, contract_sha=contract_sha)


# ---------------------------------------------------------------------------------------------- schemas (§1)

def validate_content(etype: str, content: dict, contract: dict, grant_state: dict | None) -> None:
    """Schemas of `bind`, `ack-assignment`, `stop-confirmed`; any defect is `rejected(malformed)`. Exact key sets:
    a key outside the §1 schema (other than the injected `contract_sha256`) is malformed."""
    if etype not in CONTENT_TYPES:
        raise WfRejected("malformed", f"{etype!r} is not a bind event type")
    if not isinstance(content, dict):
        raise WfRejected("malformed", "content must be a JSON object")
    want = _SCHEMA_KEYS[etype]
    missing = sorted(want - set(content))
    extra = sorted(set(content) - want - _INJECTED)
    if missing:
        raise WfRejected("malformed", f"{etype} content missing {missing}")
    if extra:
        raise WfRejected("malformed", f"{etype} content has unknown keys {extra}")
    if content["role"] not in ARS_ROLES:
        raise WfRejected("malformed", f"{etype} role {content['role']!r} is not one of {list(ARS_ROLES)}")
    if not _nonempty_str(content["session"]):
        raise WfRejected("malformed", f"{etype} session must be a non-empty string")
    if etype == "bind":
        if not _is_int(content["generation"]) or content["generation"] < 1:
            raise WfRejected("malformed", "bind generation must be an integer >= 1")
        if not _str_list(content["tools"]) or not _str_list(content["paths"]):
            raise WfRejected("malformed", "bind tools and paths must be lists of strings")
        if not _is_int(content["grant_seq"]) or content["grant_seq"] < 1:
            raise WfRejected("malformed", "bind grant_seq must be an integer >= 1")
    elif etype == "ack-assignment":
        if not _is_int(content["bind_seq"]) or content["bind_seq"] < 1:
            raise WfRejected("malformed", "ack-assignment bind_seq must be an integer >= 1")
    else:  # stop-confirmed
        if not _is_int(content["generation"]) or content["generation"] < 1:
            raise WfRejected("malformed", "stop-confirmed generation must be an integer >= 1")
        for k in ("how", "when"):
            if not isinstance(content[k], str):
                raise WfRejected("malformed", f"stop-confirmed {k} must be a string")
        for k in ("owner_check", "owner_turn"):
            if not _nonempty_str(content[k]):
                raise WfRejected("malformed", f"stop-confirmed {k} must be a non-empty string")


# ---------------------------------------------------------------------------------------------- type rules (§3)

def check_ack_assignment(conn: sqlite3.Connection, job: str, *, role: str, session: str, content: dict) -> None:
    """`ack-assignment`: the role has a binding; content role == author role; content session == author session ==
    binding session; `bind_seq` == the binding's bind_seq; not already acknowledged. Else rejected(invalid-ack).
    In implicit mode there is no assignment to acknowledge (rejected(invalid-ack))."""
    table = active_roles(conn, job)
    b = table.get(role)
    if b is None:
        raise WfRejected("invalid-ack", f"role {role} has no binding to acknowledge")
    if not isinstance(content, dict) or content.get("role") != role:
        raise WfRejected("invalid-ack", "ack-assignment role must be the author role")
    if content.get("session") != session or session != b.session:
        raise WfRejected("invalid-ack", f"ack-assignment must come from the bound session {b.session!r} and name it")
    if content.get("bind_seq") != b.bind_seq:
        raise WfRejected("invalid-ack", f"ack-assignment bind_seq must be {b.bind_seq}")
    if b.ack_seq is not None:
        raise WfRejected("invalid-ack", f"binding already acknowledged at seq {b.ack_seq} (duplicate acknowledgement)")


def check_stop_confirmed(conn: sqlite3.Connection, job: str, *, content: dict) -> None:
    """`stop-confirmed` names a live binding. Explicit mode: the role's binding has exactly content.session and
    content.generation and is not already stopped. Implicit mode: role builder and session == the implicit builder
    session, not already stopped. Anything else is rejected(invalid-stop)."""
    if not isinstance(content, dict):
        raise WfRejected("invalid-stop", "content must be a JSON object")
    role, session, generation = content.get("role"), content.get("session"), content.get("generation")
    table = active_roles(conn, job)
    if table:
        b = table.get(role) if isinstance(role, str) else None
        if b is None or b.session != session or b.generation != generation:
            raise WfRejected("invalid-stop", f"no binding of role {role!r} to session {session!r} generation {generation!r}")
        if b.stopped_seq is not None:
            raise WfRejected("invalid-stop", f"session {session!r} already stopped (seq {b.stopped_seq})")
        return
    st = existing_state(conn, job)
    if role != "builder" or st.get("builder_session") is None or session != st["builder_session"]:
        raise WfRejected("invalid-stop", f"implicit mode: only the observed builder session "
                                         f"{st.get('builder_session')!r} can be stopped")
    for r in rows(conn, job):
        if r["type"] == "stop-confirmed":
            c = _content(r)
            if c.get("role") == role and c.get("session") == session and c.get("generation") == generation:
                raise WfRejected("invalid-stop", f"session {session!r} already stopped (seq {r['seq']})")


def digest_rows(conn: sqlite3.Connection, job: str, reconciled: dict[str, bool | None]) -> list[dict]:
    """§4.1 l.182 binding table rows (explicit mode), in ARS_ROLES order; [] in implicit mode."""
    table = active_roles(conn, job)
    out: list[dict] = []
    for role in ARS_ROLES:
        b = table.get(role)
        if b is None:
            continue
        out.append({"role": b.role, "session": b.session, "generation": b.generation, "bind_seq": b.bind_seq,
                    "acknowledged": b.ack_seq is not None, "ack_seq": b.ack_seq,
                    "stopped": b.stopped_seq is not None, "stopped_seq": b.stopped_seq,
                    "reconciled": reconciled.get(role) if isinstance(reconciled, dict) else None})
    return out
