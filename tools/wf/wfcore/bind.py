"""Explicit role binding, the ARS and replacement (B2 slices 1 and 2, lanes S1B / S2B; spec FIRST-RELEASE-SPEC.md
Rev 5.3 §2B l.34-46, §4.1 l.169, §5 rules 4-5, §6.2 steps 2-5, cases T32 l.307 and T33 l.308).

Governing text: slice-1 contract INTERFACE-CONTRACT.md ("v1": §1 event schemas, §2 ARS schema, §3 preconditions /
actor check / bootstrap, §6 signatures) as amended by INTERFACE-CONTRACT-S2.md ("S2": A-1 implicit-claimant
precondition, §2 ARS replacement rules, §3 replacement bind at generation+1, §6 signature additions), both frozen in
the WorkForce collab folder `contracts/b2s1`, `contracts/b2s2` (verified against SHA256SUMS before implementing).

What this module decides, as pure checks over rows already in the ledger (it never writes; the wiring calls it
inside the write transaction, before `validate.build_row`):

* `active_roles` - the explicit binding table, built from `bind`, `ack-assignment` and `stop-confirmed` rows in seq
  order, holding the LATEST binding per role (highest generation; S2 §6). An empty table is IMPLICIT MODE: no
  `wf bind` ever ran for the job, every check here is a no-op, and the existing observed-header rules in
  `validate.build_row` are the only rules, exactly as before. A job never returns to implicit mode.
* `validate_bind` - the `wf bind` preconditions in contract order (v1 §3 with S2 A-1 at generation 1; S2 §3 at
  generation >= 2); returns the `bind` row content including `replaces_seq`.
* `check_actor` - explicit-mode actor check (v1 §3, unchanged by S2): unbound role, stopped session, stale /
  unbound higher generation, and a foreign session at the bound generation (raised as `validate.WfClaimantConflict`).
* `parse_replacement`, `replacement_count`, `delegation_summary` - S2 §2 replacement rules.
* `check_ack_assignment`, `check_stop_confirmed`, `validate_content`, `digest_rows` - type rules, schemas, digest.

Replacement (S2 §3): only the builder role is replaceable (D-S2-2). The new session's first event is governed by the
existing unreconciled rule in reconcile.py (the post-stop report is unacknowledged, so only `ack-reconcile` by the
newly bound session passes); this module adds no second mechanism for it.

Not here (other lanes / excluded): reconcile, recover reconcile / close / decide (S2R), command wiring and CLI (S2W),
the gate (T13). This module reads `reconcile-report`, `ack-reconcile` and `recovery-decision` rows directly (it must
not import `.reconcile`, v1 §6); their shapes are the frozen S1/S2 §1 schemas.

Recorded decisions: D-S1-1 bootstrap identity = contract author session; D-S1-2 an `ack-assignment` is recorded and
shown, not a precondition; D-S2-1 a `recovery-decision` is a replacement precondition; D-S2-2 only the builder is
replaceable; D-S2-3 replacement count per role, expiry compared as a fixed-form UTC instant.
"""
from __future__ import annotations

import fnmatch
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from .db import WfRejected, rows
from .validate import WfClaimantConflict, existing_state, now_ts

__all__ = ["ARS_ROLES", "CONTENT_TYPES", "REPLACEMENT_KEYS", "REPLACEABLE_ROLES", "Binding", "parse_ars",
           "declared_subset", "parse_replacement", "replacement_count", "delegation_summary", "active_roles",
           "validate_bind", "check_actor", "validate_content", "check_ack_assignment", "check_stop_confirmed",
           "digest_rows"]

ARS_ROLES: tuple[str, ...] = ("coordinator", "builder", "reviewer")
CONTENT_TYPES: tuple[str, ...] = ("bind", "ack-assignment", "stop-confirmed")
ROLE_SPEC_KEYS: tuple[str, ...] = ("tools", "actions", "paths")
REPLACEMENT_KEYS: tuple[str, ...] = ("allowed", "max", "expiry")
REPLACEABLE_ROLES: tuple[str, ...] = ("builder",)  # D-S2-2
INITIAL_GENERATION = 1
#: S2 §2: the exact expiry form, an RFC 3339 UTC instant without fractions.
_EXPIRY = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

#: §1 schemas: exact key sets (the wiring injects `contract_sha256` into every row's content before validation).
#: `bind` may carry `replaces_seq` (S2 §6): absent or None at generation 1, an int at generation >= 2; and `account`
#: (FIX3-II r2 §4.2): the OS account the bound builder or reviewer session runs as, present only when given.
_SCHEMA_KEYS: dict[str, frozenset[str]] = {
    "bind": frozenset({"role", "session", "generation", "tools", "paths", "grant_seq"}),
    "ack-assignment": frozenset({"role", "session", "bind_seq"}),
    "stop-confirmed": frozenset({"role", "session", "generation", "how", "when", "owner_check", "owner_turn"}),
}
_OPTIONAL_KEYS: dict[str, frozenset[str]] = {"bind": frozenset({"replaces_seq", "account"})}
_INJECTED = frozenset({"contract_sha256"})


@dataclass(frozen=True)
class Binding:
    role: str
    session: str
    generation: int
    bind_seq: int
    ack_seq: int | None
    stopped_seq: int | None
    tools: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    replaces_seq: int | None = None
    account: str | None = None  # FIX3-II r2 §4.2 (D18): last, with a default, so positional constructions stay valid


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
    """§3B l.131/136: a `recovery-opened` row with no later `recovery-closed`."""
    opened = closed = 0
    for r in rs:
        if r["type"] == "recovery-opened":
            opened = r["seq"]
        elif r["type"] == "recovery-closed":
            closed = r["seq"]
    return opened > closed


def _within(declared: list[str], allowed: list[str] | tuple[str, ...]) -> list[str]:
    """Items of `declared` NOT contained in `allowed` (equal, or fnmatch-matched by an allowed glob)."""
    return [d for d in declared
            if not isinstance(d, str) or not (d in allowed or any(fnmatch.fnmatchcase(d, a) for a in allowed))]


def _ts_seconds(ts: str) -> str:
    """`validate.now_ts()` ("YYYY-MM-DDTHH:MM:SS.mmmZ") truncated to the S2 §2 expiry form, so that the fixed-form
    string comparison is exact at second resolution (a fractional suffix would otherwise sort before "Z")."""
    return ts[:19] + "Z"


# ---------------------------------------------------------------------------------------------- ARS (v1 §2, S2 §2)

def parse_ars(ars: object) -> dict[str, dict[str, list[str]]]:
    """`grant.ars` -> {role: {"tools", "actions", "paths"}}. Fails closed with `rejected(ars)`: `ars` must be an
    object with a non-empty `roles` object; every role key one of ARS_ROLES; every role spec an object holding
    exactly lists of strings under `tools`, `actions` and `paths` (all three required). `replacement` is parsed by
    `parse_replacement` only (S2 §2: a bad delegation block never breaks initial assignment); other top-level keys
    are informational."""
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
    """v1 §2 subset rule. [] = every declared tool is in the role's ARS tools and every declared path glob equals an
    ARS glob or `fnmatch.fnmatchcase`-matches one (pure string match, no filesystem). Otherwise one reason per item."""
    tools = role_spec.get("tools", []) if isinstance(role_spec, dict) else []
    globs = role_spec.get("paths", []) if isinstance(role_spec, dict) else []
    reasons = [f"tool {t!r} is not in the ARS tools {list(tools)}"
               for t in declared_tools if not isinstance(t, str) or t not in tools]
    reasons += [f"path {g!r} is not inside the ARS paths {list(globs)}" for g in _within(declared_paths, globs)]
    return reasons


def parse_replacement(ars: object) -> dict | None:
    """S2 §2 `grant.ars.replacement` -> {"allowed": bool, "max": int, "expiry": str}, or None when absent (absent is
    treated by the caller as replacement not permitted). Malformed -> `rejected(delegation)`: not an object; a key
    missing or extra; `allowed` not a bool; `max` not an int (a bool counts as wrong) or negative; `expiry` not a
    string of exactly the form YYYY-MM-DDTHH:MM:SSZ or not a real UTC instant."""
    if not isinstance(ars, dict) or "replacement" not in ars:
        return None
    rep = ars["replacement"]
    if not isinstance(rep, dict):
        raise WfRejected("delegation", "ars replacement is not an object")
    missing = sorted(set(REPLACEMENT_KEYS) - set(rep))
    extra = sorted(set(rep) - set(REPLACEMENT_KEYS))
    if missing or extra:
        raise WfRejected("delegation", f"ars replacement keys must be exactly {list(REPLACEMENT_KEYS)} "
                                       f"(missing {missing}, extra {extra})")
    if not isinstance(rep["allowed"], bool):
        raise WfRejected("delegation", "ars replacement 'allowed' must be a boolean")
    if not _is_int(rep["max"]) or rep["max"] < 0:
        raise WfRejected("delegation", "ars replacement 'max' must be a non-negative integer")
    exp = rep["expiry"]
    if not isinstance(exp, str) or not _EXPIRY.match(exp):
        raise WfRejected("delegation", "ars replacement 'expiry' must be a UTC instant YYYY-MM-DDTHH:MM:SSZ")
    try:
        time.strptime(exp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise WfRejected("delegation", f"ars replacement 'expiry' {exp!r} is not a valid instant") from None
    return {"allowed": rep["allowed"], "max": rep["max"], "expiry": exp}


def delegation_summary(grant: dict | None) -> dict:
    """S2 §6 digest view of `grant.ars.replacement`: {"present", "valid", "allowed", "max", "expiry", "problem"}.
    Absent -> present False, valid True, values None (replacement not permitted). Malformed -> valid False with the
    refusal text under `problem` (shown as `delegation: malformed`)."""
    out: dict[str, Any] = {"present": False, "valid": True, "allowed": None, "max": None, "expiry": None,
                           "problem": None}
    ars = grant.get("ars") if isinstance(grant, dict) else None
    if not isinstance(ars, dict) or "replacement" not in ars:
        return out
    out["present"] = True
    try:
        rep = parse_replacement(ars)
    except WfRejected as exc:
        out.update(valid=False, problem=str(exc))
        return out
    assert rep is not None
    out.update(allowed=rep["allowed"], max=rep["max"], expiry=rep["expiry"])
    return out


def replacement_count(conn: sqlite3.Connection, job: str, role: str) -> int:
    """S2 §2: the number of `bind` rows for `role` with generation >= 2 (replacement binds over the job's life)."""
    n = 0
    for r in rows(conn, job):
        if r["type"] == "bind" and r["author_role"] == "coordinator":
            c = _content(r)
            if c.get("role") == role and _is_int(c.get("generation")) and c["generation"] >= 2:
                n += 1
    return n


# ---------------------------------------------------------------------------------------------- binding table

def active_roles(conn: sqlite3.Connection, job: str) -> dict[str, Binding]:
    """The explicit binding table (latest binding per role); {} = implicit mode. Works on a write-transaction or a
    read-snapshot connection.

    A `bind` row (author coordinator) whose generation is higher than the role's current binding replaces it (a row
    at the same or a lower generation cannot be written through `validate_bind` and is ignored). An `ack-assignment`
    sets `ack_seq` on the current binding when role, session and `bind_seq` match (first match). A `stop-confirmed`
    sets `stopped_seq` on the current binding when role, session and generation match (first match)."""
    table: dict[str, Binding] = {}
    for r in rows(conn, job):
        t = r["type"]
        if t not in CONTENT_TYPES:
            continue
        c = _content(r)
        role = c.get("role")
        if t == "bind":
            if r["author_role"] != "coordinator" or role not in ARS_ROLES:
                continue
            gen = c.get("generation")
            if not _nonempty_str(c.get("session")) or not _is_int(gen):
                continue
            if role in table and gen <= table[role].generation:
                continue
            tools = tuple(c["tools"]) if _str_list(c.get("tools")) else ()
            paths = tuple(c["paths"]) if _str_list(c.get("paths")) else ()
            rs = c.get("replaces_seq") if _is_int(c.get("replaces_seq")) else None
            acct = c.get("account") if isinstance(c.get("account"), str) else None
            table[role] = Binding(role, c["session"], gen, r["seq"], None, None, tools, paths, rs, acct)
            continue
        b = table.get(role) if isinstance(role, str) else None
        if b is None:
            continue
        if t == "ack-assignment":
            if b.ack_seq is None and c.get("session") == b.session and c.get("bind_seq") == b.bind_seq:
                table[role] = Binding(b.role, b.session, b.generation, b.bind_seq, r["seq"], b.stopped_seq,
                                      b.tools, b.paths, b.replaces_seq, b.account)
        elif t == "stop-confirmed":
            if b.stopped_seq is None and c.get("session") == b.session and c.get("generation") == b.generation:
                table[role] = Binding(b.role, b.session, b.generation, b.bind_seq, b.ack_seq, r["seq"],
                                      b.tools, b.paths, b.replaces_seq, b.account)
    return table


def _latest_report(rs: list[dict[str, Any]], role: str) -> tuple[int | None, bool]:
    """(seq of the latest `reconcile-report` for `role`, whether an `ack-reconcile` of that role names it)."""
    latest: int | None = None
    acked = False
    for r in rs:
        if r["type"] == "reconcile-report":
            if _content(r).get("role") == role:
                latest, acked = r["seq"], False
        elif r["type"] == "ack-reconcile" and latest is not None:
            c = _content(r)
            if c.get("role") == role and c.get("report_seq") == latest:
                acked = True
    return latest, acked


def _resume_decision(rs: list[dict[str, Any]], *, role: str, report_seq: int, stop_seq: int) -> int | None:
    """Seq of a `recovery-decision` with decision "resume" for `role` citing exactly `report_seq` and `stop_seq`."""
    for r in rs:
        if r["type"] == "recovery-decision":
            c = _content(r)
            if (c.get("decision") == "resume" and c.get("role") == role and c.get("report_seq") == report_seq
                    and c.get("stop_seq") == stop_seq):
                return r["seq"]
    return None


def validate_bind(conn: sqlite3.Connection, job: str, *, role: str, session: str, generation: int,
                  tools: list[str], paths: list[str], author_session: str, grant: dict | None,
                  grant_seq: int | None, state: dict, account: str | None = None,
                  account_map: dict[str, tuple[str, ...]] | None = None) -> dict:
    """`wf bind` preconditions in contract order; returns the `bind` row content (§1 + `replaces_seq`). Each failure
    is a `WfRejected` and nothing is written. Defaults for omitted --tool/--path are applied by the caller (wiring).

    Common order: input shape (malformed) -> job has a contract (malformed) -> not closed (closed) -> recovery not
    open (recovery-open) -> actor (explicit mode: the author is the bound, not-stopped coordinator: unbound-session /
    stopped-session) -> activated grant (no-grant) -> ARS parses and names the role (ars) -> declared tools/paths
    inside the ARS (ars) -> the bind account (FIX3-II r2 §4.2, ars). Then:

    The bind account: an `account` on a coordinator bind is an input-shape refusal (malformed). `account_map` is the
    in-force grant's `ars.accounts` (`accounts.map_of`, passed in by the wiring: this module does not import
    `accounts`, D17). With a map, a builder or reviewer bind needs an `account` in `account_map[role]` that no other
    session's `bind` row of the job names, at any generation; with no map, any `account` is refused. A generation-2
    bind needs no extra rule: one id per session already forces a new id (SII:88). The row holds `account` only when
    it is given.

    generation 1 (v1 §3 + S2 A-1): role not already bound (bound) -> A-1 implicit claimant: for role builder, an
    implicit builder session other than `session` that no `stop-confirmed` names (implicit-claimant) -> bootstrap
    when the table is empty (bootstrap) -> reviewer precondition (reviewer-precondition).

    generation >= 2 (S2 §3, replacement; builder only, else generation): the role's current binding is at g-1
    (generation; already at g or above: bound - a decision is cited by exactly one bind) -> it is stop-confirmed
    (termination-unconfirmed) -> completed reconcile: the latest `reconcile-report` for the role is later than the
    stop and unacknowledged (no-reconcile) -> a `recovery-decision` "resume" citing that report and that stop
    (no-decision) -> delegation rules: replacement present, valid, allowed, count < max, now < expiry, declared
    tools/paths within the replaced binding's (delegation) -> new session differs from the replaced one (delegation).

    Case (b) of the slice-1 review (a different session bound as builder after implicit builder work) is refused at
    generation 1 by A-1 (`implicit-claimant`), unless a `stop-confirmed` names that implicit session."""
    if role not in ARS_ROLES:
        raise WfRejected("malformed", f"bind role {role!r} is not one of {list(ARS_ROLES)}")
    if not _nonempty_str(session):
        raise WfRejected("malformed", "bind session must be a non-empty string")
    if not _nonempty_str(author_session):
        raise WfRejected("malformed", "invoking session must be a non-empty string")
    if not _is_int(generation) or generation < INITIAL_GENERATION:
        raise WfRejected("malformed", "bind generation must be an integer >= 1")
    if not _str_list(tools) or not _str_list(paths):
        raise WfRejected("malformed", "bind tools and paths must be lists of strings")
    if account is not None and role == "coordinator":
        raise WfRejected("malformed", "a coordinator bind names no account (the coordinator runs as the owner account)")
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
    _check_bind_account(rs, role=role, session=session, account=account, account_map=account_map)
    content: dict[str, Any] = {"role": role, "session": session, "generation": generation, "tools": list(tools),
                               "paths": list(paths), "grant_seq": grant_seq}
    if account is not None:
        content["account"] = account
    if generation == INITIAL_GENERATION:
        _initial_bind(rs, table, role=role, session=session, author_session=author_session, state=state)
        content["replaces_seq"] = None
        return content
    content["replaces_seq"] = _replacement_bind(conn, job, rs, table, role=role, session=session,
                                                generation=generation, tools=tools, paths=paths, grant=grant)
    return content


def _check_bind_account(rs: list[dict[str, Any]], *, role: str, session: str, account: str | None,
                        account_map: dict[str, tuple[str, ...]] | None) -> None:
    """FIX3-II r2 §4.2: the bind account against the map in force; each failure is `rejected(ars)`."""
    if account_map is None:
        if account is not None:
            raise WfRejected("ars", "the grant in force has no ars.accounts, so a bind names no account")
        return
    if role == "coordinator":
        return
    if account is None:
        raise WfRejected("ars", f"the grant in force maps accounts, so a {role} bind must name the session's account")
    if account not in account_map.get(role, ()):
        raise WfRejected("ars", f"account {account} is not in the grant's ars.accounts[{role!r}]")
    for r in rs:
        if r["type"] != "bind" or r["author_role"] != "coordinator":
            continue
        c = _content(r)
        if (c.get("role") in ARS_ROLES and _nonempty_str(c.get("session")) and _is_int(c.get("generation"))
                and c.get("account") == account and c["session"] != session):
            raise WfRejected("ars", f"account {account} is already bound to session {c['session']!r} "
                                    f"(bind seq {r['seq']}); one account per session")


def _initial_bind(rs: list[dict[str, Any]], table: dict[str, Binding], *, role: str, session: str,
                  author_session: str, state: dict) -> None:
    """Generation-1 tail of `validate_bind`: bound -> A-1 implicit-claimant -> bootstrap -> reviewer precondition."""
    if role in table:
        b = table[role]
        raise WfRejected("bound", f"role {role} is already bound to session {b.session!r} generation {b.generation} "
                                  f"(bind seq {b.bind_seq}); a replacement binds generation {b.generation + 1} "
                                  "after stop-confirmed, reconcile and a resume decision (T33)")
    implicit = state.get("builder_session")
    if role == "builder" and implicit is not None and implicit != session:
        stopped = any(r["type"] == "stop-confirmed" and _content(r).get("role") == "builder"
                      and _content(r).get("session") == implicit for r in rs)
        if not stopped:
            raise WfRejected("implicit-claimant", f"session {implicit!r} already acted as builder (implicit holder) "
                                                  f"and no stop-confirmed names it; binding {session!r} is refused")
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


def _replacement_bind(conn: sqlite3.Connection, job: str, rs: list[dict[str, Any]], table: dict[str, Binding], *,
                      role: str, session: str, generation: int, tools: list[str], paths: list[str],
                      grant: dict) -> int:
    """Generation >= 2 tail of `validate_bind` (S2 §3 steps 1-6); returns `replaces_seq` (the g-1 bind seq)."""
    if role not in REPLACEABLE_ROLES:
        raise WfRejected("generation", f"only {list(REPLACEABLE_ROLES)} can be replaced in this slice (D-S2-2); "
                                       f"role {role} stays at generation {INITIAL_GENERATION}")
    cur = table.get(role)
    if cur is not None and cur.generation >= generation:
        raise WfRejected("bound", f"role {role} is already bound at generation {cur.generation} "
                                  f"(bind seq {cur.bind_seq}); a stop and decision are cited by one bind only")
    if cur is None or cur.generation != generation - 1:
        have = "no binding" if cur is None else f"generation {cur.generation}"
        raise WfRejected("generation", f"replacement needs a current binding at generation {generation - 1} "
                                       f"(role {role} has {have})")
    if cur.stopped_seq is None:
        raise WfRejected("termination-unconfirmed", f"session {cur.session!r} (generation {cur.generation}) has no "
                                                    "stop-confirmed; no override (T33 e)")
    report_seq, acked = _latest_report(rs, role)
    if report_seq is None or report_seq <= cur.stopped_seq or acked:
        state_txt = ("no reconcile-report" if report_seq is None else
                     f"latest report seq {report_seq} {'is acknowledged' if acked else 'precedes the stop'}")
        raise WfRejected("no-reconcile", f"replacement needs an unacknowledged reconcile-report for {role} after the "
                                         f"stop (seq {cur.stopped_seq}); {state_txt}")
    if _resume_decision(rs, role=role, report_seq=report_seq, stop_seq=cur.stopped_seq) is None:
        raise WfRejected("no-decision", f"replacement needs a recovery-decision 'resume' for {role} citing report "
                                        f"seq {report_seq} and stop seq {cur.stopped_seq}")
    rep = parse_replacement(grant.get("ars"))
    if rep is None:
        raise WfRejected("delegation", "the grant's ARS has no replacement block; replacement is not permitted")
    if not rep["allowed"]:
        raise WfRejected("delegation", "the grant's ARS does not allow replacement (allowed: false)")
    count = replacement_count(conn, job, role)
    if count >= rep["max"]:
        raise WfRejected("delegation", f"replacement count {count} for {role} has reached max {rep['max']}")
    now = _ts_seconds(now_ts())
    if now >= rep["expiry"]:
        raise WfRejected("delegation", f"replacement expired at {rep['expiry']} (now {now})")
    wider = [f"tool {t!r}" for t in _within(tools, cur.tools)] + [f"path {p!r}" for p in _within(paths, cur.paths)]
    if wider:
        raise WfRejected("delegation", "replacement may not widen the replaced binding: " + ", ".join(wider)
                                       + f" outside tools {list(cur.tools)} / paths {list(cur.paths)}")
    if session == cur.session:
        raise WfRejected("delegation", f"the replacement session must differ from the replaced session {session!r}")
    return cur.bind_seq


def check_actor(conn: sqlite3.Connection, job: str, *, role: str, session: str, generation: int, etype: str,
                msg_id: str, contract_sha: str) -> None:
    """Explicit-mode actor check (v1 §3; unchanged by S2), run before `validate.build_row`. No-op in implicit mode and
    for role owner. unbound role -> rejected(unbound-session); the stopped session of the current binding ->
    rejected(stopped-session); generation below the binding -> rejected(stale); above -> rejected(unbound-session);
    a different session at the bound generation -> WfClaimantConflict (txn records the existing conflict blocker)."""
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
    """Schemas of `bind`, `ack-assignment`, `stop-confirmed`; any defect is `rejected(malformed)`. Exact key sets: a
    key outside the schema (other than the injected `contract_sha256` and, for `bind`, `replaces_seq`) is malformed.
    `bind.replaces_seq`: absent or None at generation 1; an integer >= 1 at generation >= 2. `bind.account`
    (FIX3-II r2 §4.2): absent, or a non-empty string."""
    if etype not in CONTENT_TYPES:
        raise WfRejected("malformed", f"{etype!r} is not a bind event type")
    if not isinstance(content, dict):
        raise WfRejected("malformed", "content must be a JSON object")
    want = _SCHEMA_KEYS[etype]
    missing = sorted(want - set(content))
    extra = sorted(set(content) - want - _OPTIONAL_KEYS.get(etype, frozenset()) - _INJECTED)
    if missing:
        raise WfRejected("malformed", f"{etype} content missing {missing}")
    if extra:
        raise WfRejected("malformed", f"{etype} content has unknown keys {extra}")
    if content["role"] not in ARS_ROLES:
        raise WfRejected("malformed", f"{etype} role {content['role']!r} is not one of {list(ARS_ROLES)}")
    if not _nonempty_str(content["session"]):
        raise WfRejected("malformed", f"{etype} session must be a non-empty string")
    if etype == "bind":
        gen = content["generation"]
        if not _is_int(gen) or gen < 1:
            raise WfRejected("malformed", "bind generation must be an integer >= 1")
        if not _str_list(content["tools"]) or not _str_list(content["paths"]):
            raise WfRejected("malformed", "bind tools and paths must be lists of strings")
        if not _is_int(content["grant_seq"]) or content["grant_seq"] < 1:
            raise WfRejected("malformed", "bind grant_seq must be an integer >= 1")
        rs = content.get("replaces_seq")
        if gen == 1 and rs is not None:
            raise WfRejected("malformed", "bind replaces_seq must be absent or null at generation 1")
        if gen >= 2 and (not _is_int(rs) or rs < 1):
            raise WfRejected("malformed", "bind replaces_seq must be an integer >= 1 at generation >= 2")
        if "account" in content and not _nonempty_str(content["account"]):
            raise WfRejected("malformed", "bind account must be a non-empty string")
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


# ---------------------------------------------------------------------------------------------- type rules (v1 §3)

def check_ack_assignment(conn: sqlite3.Connection, job: str, *, role: str, session: str, content: dict) -> None:
    """`ack-assignment` acknowledges the role's CURRENT binding: content role == author role; content session ==
    author session == binding session; `bind_seq` == the binding's bind_seq; not already acknowledged. Else
    rejected(invalid-ack). In implicit mode there is no assignment to acknowledge (rejected(invalid-ack))."""
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
    """`stop-confirmed` names a live binding. Explicit mode: the role's current binding has exactly content.session
    and content.generation and is not already stopped. Implicit mode: role builder and session == the implicit
    builder session, not already stopped. Anything else is rejected(invalid-stop)."""
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
    """§4.1 l.182 binding table rows (explicit mode), current binding per role in ARS_ROLES order; [] in implicit
    mode. S2 §6 adds `replacements` (replacement binds for the role), `replaces_seq`, `tools`, `paths`."""
    table = active_roles(conn, job)
    out: list[dict] = []
    for role in ARS_ROLES:
        b = table.get(role)
        if b is None:
            continue
        out.append({"role": b.role, "session": b.session, "generation": b.generation, "bind_seq": b.bind_seq,
                    "acknowledged": b.ack_seq is not None, "ack_seq": b.ack_seq,
                    "stopped": b.stopped_seq is not None, "stopped_seq": b.stopped_seq,
                    "reconciled": reconciled.get(role) if isinstance(reconciled, dict) else None,
                    "replacements": replacement_count(conn, job, role), "replaces_seq": b.replaces_seq,
                    "tools": list(b.tools), "paths": list(b.paths)})
    return out
