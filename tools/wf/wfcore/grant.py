"""Grant lifecycle, authority state, command-authority matrix and grant-check (B2 slice 3, lane S3G; spec
FIRST-RELEASE-SPEC.md Rev 5.3 §2B l.48, §4.1 `grant revoke|expire`, `grant-check`, `cancel`, `log-intervention`,
§6.2 steps 1 and 6, §6.3, §8 T15 T21 T29 T34).

Governing text: INTERFACE-CONTRACT-S3.md (§1 event schemas, §2 authority state, §3 matrix, §4 grant-check, §5 cancel
and intervention, §6 signatures) with AMENDMENT S3-A1 (CATEGORY keys are event types; cancel is category O;
`--check-role`; `now` format), frozen in the WorkForce collab folder `contracts/b2s3` (SHA256SUMS verified). The v1 and
S2 contracts stay in force where S3 does not change them.

One decision function: `authority(rows, job=..., now=...)` derives the state from the rows passed in (read in the
caller's transaction or snapshot) and `now` on every call; nothing is cached. `mutation_guard` (every role-bearing
mutation) and `check` (`wf grant-check`) both call it first, so a ledger that grant-check denies is the same ledger the
guard sees. Pure functions: nothing here reads the filesystem or writes rows.

States, in derivation order (§2): `cancelled` (any `job-cancelled` row) -> `none` (no grant row) -> latest grant row
`proposed`: `voided` if an earlier revision was activated (D-S3-3: a new proposal ends authority at once), else
`proposed` -> latest grant row `activated` (the current revision): `malformed` / revision-mismatch when its block hash
does not reproduce from the proposed row it cites AND from its own fields, or its contract pin differs from the
contract row; `malformed` / expiry-malformed; `revoked`; `expired` (explicit row, then time `now >= expiry`);
`malformed` for any other missing / ill-typed field; else `in-force`.

Matrix (§3, S3-A1): R commands write no row and are never guarded; O (owner control and recovery, including the
first cancellation) always passes this guard and keeps its own preconditions; L (`grant`) is refused only after
cancellation (renewal stays possible from every other state); W (mediated work) passes in `none`/`proposed` (D-S3-1,
legacy recording - grant-check still denies there), in `in-force` unless the author's explicit binding lies outside
the current revision's ARS (`binding-outside-ars`), and is refused `authority-<state>` / `cancelled` otherwise.

Claim limits: cancellation and revocation deny later work recorded through `wf`; they stop no process and prevent no
effect outside this CLI (CANCEL_LIMITATION). Paths are compared as strings (pathpolicy normalisation, fnmatch globs);
no filesystem state is read.
"""
from __future__ import annotations

import fnmatch
import json
import re
import time
from typing import Any

from . import pathpolicy
from .canon import canonical, sha256_obj, sha256_text
from .db import WfRejected

__all__ = ["CONTENT_TYPES", "AUTH_STATES", "CATEGORY", "CANCEL_LIMITATION", "NECESSITY", "block", "authority",
           "mutation_guard", "check", "validate_revoke", "validate_cancel", "validate_content",
           "intervention_summary"]

CONTENT_TYPES: tuple[str, ...] = ("grant-revoked", "grant-expired", "job-cancelled", "intervention")
AUTH_STATES: tuple[str, ...] = ("none", "proposed", "in-force", "expired", "revoked", "voided", "malformed",
                                "cancelled")
#: §3 matrix keyed by event type, exactly as AMENDMENT S3-A1 item 1 lists it. R-category commands write no row and
#: are never guarded; `contract` is never passed to the guard.
CATEGORY: dict[str, str] = {
    **{t: "O" for t in ("grant-revoked", "grant-expired", "job-cancelled", "intervention", "close", "recovery-opened",
                        "exception-acknowledged", "recovery-closed", "recovery-decision", "stop-confirmed",
                        "ack-reconcile", "blocker", "reconcile-report")},
    "grant": "L",
    **{t: "W" for t in ("intent", "receipt", "handoff", "delivered", "ack-assignment", "bind", "accept")},
}
#: §5, exact text (owner item 3).
CANCEL_LIMITATION = ("cancellation denies later work recorded through wf; it does not stop running processes or "
                     "prevent effects outside this CLI; no enforcement on real workers is claimed")
NECESSITY: tuple[str, ...] = ("avoidable", "necessary", "unknown")

_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA_KEYS: dict[str, frozenset[str]] = {
    "grant-revoked": frozenset({"grant_id", "block_hash", "reason", "owner_turn"}),
    "grant-expired": frozenset({"grant_id", "block_hash", "reason", "owner_turn"}),
    "job-cancelled": frozenset({"reason", "owner_turn"}),
    "intervention": frozenset({"label", "necessity", "minutes", "note"}),
}
_INJECTED = frozenset({"contract_sha256"})
#: Fields of an activated revision checked after the lifecycle states (§2 step 4, "any other required field").
_REVISION_FIELDS: tuple[tuple[str, Any], ...] = (
    ("grant_id", str), ("actions", list), ("resources", list), ("exclusions", list), ("ars", dict),
    ("delegation", dict), ("revocation_rule", str), ("block_hash", str), ("proposed_seq", int),
    ("contract_sha256", str), ("source_turn", str), ("quote", str))
_W_REFUSAL = {"expired": "authority-expired", "revoked": "authority-revoked", "voided": "authority-voided",
              "malformed": "authority-malformed", "cancelled": "cancelled"}


# ---------------------------------------------------------------------------------------------- helpers

def _content(r: dict[str, Any]) -> dict[str, Any]:
    try:
        c = json.loads(r["content"])
    except (TypeError, ValueError, KeyError):
        return {}
    return c if isinstance(c, dict) else {}


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _nonempty_str(v: object) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _str_list(v: object) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def _instant(v: object) -> bool:
    """Exactly YYYY-MM-DDTHH:MM:SSZ and a real UTC instant."""
    if not isinstance(v, str) or not _INSTANT.match(v):
        return False
    try:
        time.strptime(v, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def _glob_in(item: str, globs: list[str] | tuple[str, ...]) -> bool:
    """The `bind.declared_subset` rule: equal to a glob or `fnmatch.fnmatchcase`-matched by one (strings only)."""
    return any(item == g or fnmatch.fnmatchcase(item, g) for g in globs if isinstance(g, str))


def _within(declared: object, allowed: object) -> bool:
    return _str_list(declared) and _str_list(allowed) and all(_glob_in(d, allowed) for d in declared)  # type: ignore[arg-type]


def _contract_pin(rows: list[dict[str, Any]]) -> str | None:
    """sha256 of the contract recorded in the job's first row (recomputed, not read from the injected pin)."""
    if not rows or rows[0].get("type") != "contract":
        return None
    c = _content(rows[0]).get("contract")
    return sha256_obj(c) if isinstance(c, dict) else None


def _explicit_mode(rows: list[dict[str, Any]]) -> bool:
    return any(r.get("type") == "bind" for r in rows)


# ---------------------------------------------------------------------------------------------- §2

def block(job: str, grant: dict, proposed_seq: int) -> tuple[str, str]:
    """Grant Confirmation Block (canonical text, sha256): byte-identical to `commands._block` - deterministic in the
    job, the grant fields, the contract pin and the proposed position; no timestamp or row hash."""
    b = {"job": job, "grant_id": grant["grant_id"], "actions": grant["actions"], "resources": grant["resources"],
         "exclusions": grant.get("exclusions", []), "ars": grant["ars"], "delegation": grant.get("delegation", {}),
         "expiry": grant["expiry"], "revocation_rule": grant["revocation_rule"],
         "contract_sha256": grant["contract_sha256"], "proposed_seq": proposed_seq}
    text = canonical(b)
    return text, sha256_text(text)


def _result(state: str, reason: str | None, cur: dict | None = None, act_seq: int | None = None) -> dict:
    cur = cur if isinstance(cur, dict) else None
    return {"state": state, "reason": reason,
            "grant_id": cur.get("grant_id") if cur else None,
            "block_hash": cur.get("block_hash") if cur else None,
            "proposed_seq": cur.get("proposed_seq") if cur else None,
            "activation_seq": act_seq,
            "expiry": cur.get("expiry") if cur else None,
            "grant": cur}


def authority(rows: list[dict], *, job: str, now: str) -> dict:
    """§2 authority state of `job` at instant `now` (YYYY-MM-DDTHH:MM:SSZ) from `rows` (one transaction/snapshot).
    Returns {"state", "reason", "grant_id", "block_hash", "proposed_seq", "activation_seq", "expiry", "grant"}."""
    if any(r.get("type") == "job-cancelled" for r in rows):
        return _result("cancelled", "job-cancelled")
    grants = [r for r in rows if r.get("type") == "grant"]
    if not grants:
        return _result("none", "no-grant")
    latest = grants[-1]
    lc = _content(latest)
    if lc.get("state") != "activated":
        earlier = any(_content(g).get("state") == "activated" for g in grants[:-1])
        if lc.get("state") == "proposed":
            return _result("voided", "new-proposal", lc) if earlier else _result("proposed", "not-activated", lc)
        return _result("malformed", "grant-state", lc)
    act_seq = latest["seq"]
    if not _instant(now):
        return _result("malformed", "now-malformed", lc, act_seq)
    # revision identity: the block hash reproduces from the proposed row it cites and from its own fields, and its
    # contract pin is the contract row's (owner item 2: revision-mismatched authority is denial)
    pseq = lc.get("proposed_seq")
    prop = next((g for g in grants if g.get("seq") == pseq), None) if _is_int(pseq) else None
    pc = _content(prop) if prop is not None else {}
    try:
        from_proposal = block(job, pc, pseq)[1] if prop is not None and pc.get("state") == "proposed" else None
        own = dict(lc, contract_sha256=pc.get("contract_sha256", lc.get("contract_sha256")))
        from_own = block(job, own, pseq)[1]
    except (KeyError, TypeError, ValueError):
        return _result("malformed", "revision-mismatch", lc, act_seq)
    if (not _nonempty_str(lc.get("block_hash")) or from_proposal != lc["block_hash"] or from_own != lc["block_hash"]
            or lc.get("contract_sha256") != _contract_pin(rows)):
        return _result("malformed", "revision-mismatch", lc, act_seq)
    if not _instant(lc.get("expiry")):
        return _result("malformed", "expiry-malformed", lc, act_seq)
    gid, bh = lc.get("grant_id"), lc.get("block_hash")
    later = [r for r in rows if r.get("seq", 0) > act_seq]
    names = (lambda r: _content(r).get("grant_id") == gid and _content(r).get("block_hash") == bh)
    if any(r.get("type") == "grant-revoked" and names(r) for r in later):
        return _result("revoked", "revoked", lc, act_seq)
    if any(r.get("type") == "grant-expired" and names(r) for r in later):
        return _result("expired", "explicit", lc, act_seq)
    if now >= lc["expiry"]:
        return _result("expired", "time", lc, act_seq)
    for name, typ in _REVISION_FIELDS:
        v = lc.get(name)
        ok = _is_int(v) if typ is int else isinstance(v, typ)
        if ok and typ is list:
            ok = _str_list(v)
        if not ok:
            return _result("malformed", f"field:{name}", lc, act_seq)
    if not _HEX64.match(lc["block_hash"]):
        return _result("malformed", "field:block_hash", lc, act_seq)
    return _result("in-force", None, lc, act_seq)


# ---------------------------------------------------------------------------------------------- §3

def _binding_in_ars(binding: dict | None, cur: dict | None) -> bool:
    """True when there is nothing to check (no binding, or the revision's ARS has no `roles` object) or the binding's
    role is in the ARS with recorded tools/paths inside the role spec (same rule as `bind.declared_subset`)."""
    if not isinstance(binding, dict) or not isinstance(cur, dict):
        return True
    ars = cur.get("ars")
    roles = ars.get("roles") if isinstance(ars, dict) else None
    if not isinstance(roles, dict):
        return True
    spec = roles.get(binding.get("role"))
    if not isinstance(spec, dict):
        return False
    return (_within(list(binding.get("tools") or []), spec.get("tools"))
            and _within(list(binding.get("paths") or []), spec.get("paths")))


def mutation_guard(rows: list[dict], *, job: str, now: str, etype: str, binding: dict | None) -> None:
    """§3 / S3-A1 matrix for one role-bearing mutation. `binding` = {"role", "tools", "paths"} of the author's explicit
    binding, or None. O passes; L refused only when cancelled; W per the table (D-S3-1 in none/proposed) and refused
    `binding-outside-ars` when in force but the author's binding lies outside the current revision's ARS. An event
    type outside CATEGORY is rejected(malformed)."""
    cat = CATEGORY.get(etype)
    if cat is None:
        raise WfRejected("malformed", f"event type {etype!r} has no authority category (S3-A1)")
    if cat == "O":
        return
    auth = authority(rows, job=job, now=now)
    state = auth["state"]
    if cat == "L":
        if state == "cancelled":
            raise WfRejected("cancelled", "the job is cancelled; grant renewal is refused (D-S3-2)")
        return
    if state in ("none", "proposed"):
        return  # D-S3-1: legacy recording; grant-check still denies
    if state == "in-force":
        if not _binding_in_ars(binding, auth["grant"]):
            raise WfRejected("binding-outside-ars", f"the author's binding for role {binding.get('role')!r} lies "
                                                    "outside the current revision's ARS; a new bind is required")
        return
    raise WfRejected(_W_REFUSAL[state], f"authority is {state} ({auth['reason']}); mediated work is refused")


# ---------------------------------------------------------------------------------------------- §4

def _deny(reason: str, auth: dict) -> dict:
    return {"allow": False, "reason": reason, "authority": auth["state"], "grant_id": auth["grant_id"],
            "block_hash": auth["block_hash"]}


def check(rows: list[dict], *, job: str, now: str, role: str, action: str, path: str, contract: dict,
          binding: dict | None) -> dict:
    """§4 grant-check: {"allow", "reason", "authority", "grant_id", "block_hash"}; the first failing reason in order:
    authority in-force (else the state name) -> action granted -> path relative, inside the contract write_scope and
    matched by a revision resource -> not excluded -> ARS role (if the ARS has `roles`) -> action in the role's ARS
    `actions` (`action-outside-role`) -> path matched by the role's ARS `paths` (`path-outside-role`) -> in explicit
    mode a binding for the role with recorded tools/paths inside the ARS role spec -> path matched by the binding's
    declared `paths` (`path-outside-binding`). The three role/binding target checks are AMENDMENT S4A-A2; an ARS
    without a `roles` object keeps the S3 behaviour."""
    auth = authority(rows, job=job, now=now)
    if auth["state"] != "in-force":
        return _deny(auth["state"], auth)
    cur = auth["grant"]
    if not isinstance(action, str) or action not in cur["actions"]:
        return _deny("action-not-granted", auth)
    rel = None
    if isinstance(path, str):
        r = pathpolicy.resolve(path)
        if r.problem is None and not r.outside and r.resolved:
            rel = r.resolved.replace("\\", "/")
    ws = contract.get("write_scope") if isinstance(contract, dict) else None
    if rel is None or not _str_list(ws) or not _glob_in(rel, ws) or not _glob_in(rel, cur["resources"]):
        return _deny("path-not-granted", auth)
    if _glob_in(rel, cur.get("exclusions") or []):
        return _deny("path-excluded", auth)
    ars = cur.get("ars")
    roles = ars.get("roles") if isinstance(ars, dict) else None
    if isinstance(roles, dict):
        if role not in roles:
            return _deny("role-not-in-ars", auth)
        spec = roles[role] if isinstance(roles[role], dict) else {}
        if not _str_list(spec.get("actions")) or action not in spec["actions"]:      # S4A-A2
            return _deny("action-outside-role", auth)
        if not _str_list(spec.get("paths")) or not _glob_in(rel, spec["paths"]):     # S4A-A2
            return _deny("path-outside-role", auth)
        if _explicit_mode(rows):
            if not isinstance(binding, dict) or binding.get("role") != role:
                return _deny("unbound-role", auth)
            if not _binding_in_ars(binding, cur):
                return _deny("binding-outside-ars", auth)
            paths = binding.get("paths")
            if not _str_list(paths) or not _glob_in(rel, paths):                     # S4A-A2
                return _deny("path-outside-binding", auth)
    return {"allow": True, "reason": "allowed", "authority": auth["state"], "grant_id": auth["grant_id"],
            "block_hash": auth["block_hash"]}


# ---------------------------------------------------------------------------------------------- validators (§1, §5)

def _provenance(content: dict, job: str) -> None:
    turn = content.get("owner_turn")
    if not _nonempty_str(turn) or job not in turn:
        raise WfRejected("no-provenance", f"owner_turn must be a non-empty owner turn that names the job {job!r}")


def validate_revoke(rows: list[dict], *, job: str, now: str, content: dict) -> None:
    """`grant-revoked` and `grant-expired`: schema (malformed) -> owner turn names the job (no-provenance) -> a
    current revision is in force (no-active-grant, D-S3-4) -> the content names exactly that revision's grant_id and
    block_hash (malformed)."""
    validate_content("grant-revoked", content, {}, None)
    _provenance(content, job)
    auth = authority(rows, job=job, now=now)
    if auth["state"] != "in-force":
        raise WfRejected("no-active-grant", f"nothing to end: authority is {auth['state']} ({auth['reason']})")
    if content["grant_id"] != auth["grant_id"] or content["block_hash"] != auth["block_hash"]:
        raise WfRejected("malformed", f"the content must name the current revision {auth['grant_id']!r} "
                                      f"{str(auth['block_hash'])[:12]}")


def validate_cancel(rows: list[dict], *, job: str, content: dict) -> None:
    """`job-cancelled`: schema (malformed) -> not already cancelled (cancelled, S3-A1 item 2) -> owner turn names the
    job (no-provenance). A first cancellation is valid in every authority state (category O)."""
    validate_content("job-cancelled", content, {}, None)
    if any(r.get("type") == "job-cancelled" for r in rows):
        raise WfRejected("cancelled", "the job is already cancelled")
    _provenance(content, job)


def validate_content(etype: str, content: dict, contract: dict, grant_state: dict | None) -> None:
    """§1 schemas, exact keys (plus the injected `contract_sha256`); any defect is rejected(malformed)."""
    if etype not in CONTENT_TYPES:
        raise WfRejected("malformed", f"{etype!r} is not a grant-lifecycle event type")
    if not isinstance(content, dict):
        raise WfRejected("malformed", "content must be a JSON object")
    want = _SCHEMA_KEYS[etype]
    missing = sorted(want - set(content))
    extra = sorted(set(content) - want - _INJECTED)
    if missing:
        raise WfRejected("malformed", f"{etype} content missing {missing}")
    if extra:
        raise WfRejected("malformed", f"{etype} content has unknown keys {extra}")
    if etype in ("grant-revoked", "grant-expired"):
        if not _nonempty_str(content["grant_id"]):
            raise WfRejected("malformed", f"{etype} grant_id must be a non-empty string")
        if not isinstance(content["block_hash"], str) or not _HEX64.match(content["block_hash"]):
            raise WfRejected("malformed", f"{etype} block_hash must be 64 lowercase hex")
    if etype in ("grant-revoked", "grant-expired", "job-cancelled"):
        for k in ("reason", "owner_turn"):
            if not _nonempty_str(content[k]):
                raise WfRejected("malformed", f"{etype} {k} must be a non-empty string")
        return
    if not _nonempty_str(content["label"]):
        raise WfRejected("malformed", "intervention label must be a non-empty string")
    if content["necessity"] not in NECESSITY:
        raise WfRejected("malformed", f"intervention necessity must be one of {list(NECESSITY)}")
    m = content["minutes"]
    if not (m == "unknown" or (_is_int(m) and m >= 0)):
        raise WfRejected("malformed", "intervention minutes must be an integer >= 0 or \"unknown\" (never estimated)")
    if not isinstance(content["note"], str):
        raise WfRejected("malformed", "intervention note must be a string")


def intervention_summary(rows: list[dict]) -> dict:
    """{"count", "minutes_known", "unknown_count", "entries"}: known minutes summed, `unknown` counted separately and
    never imputed (§5)."""
    entries: list[dict] = []
    known = unknown = 0
    for r in rows:
        if r.get("type") != "intervention":
            continue
        c = _content(r)
        m = c.get("minutes")
        if _is_int(m) and m >= 0:
            known += m
        else:
            unknown += 1
        entries.append({"seq": r.get("seq"), "ts": r.get("ts"), "label": c.get("label"),
                        "necessity": c.get("necessity"), "minutes": m, "note": c.get("note"),
                        "author_role": r.get("author_role"), "session": r.get("session_id")})
    return {"count": len(entries), "minutes_known": known, "unknown_count": unknown, "entries": entries}
