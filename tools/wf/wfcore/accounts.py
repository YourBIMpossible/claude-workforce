"""The account map and the caller check (PACKAGE-FIX3-II, lane F3-ACC; INTERFACE-CONTRACT-FIX3-II r2 sections 4.1, 4.3
and 4.4; SII:66-116).

A grant's `ars.accounts` maps the worker roles to OS account ids: `{"builder": [<id>, …], "reviewer": [<id>, …]}`.
The map of the latest activated grant is in force (it stays in force after that grant is revoked, expires or a new
proposal voids it), and each session's `bind` row names the one account it runs as. `check_caller` decides a
brokered claim of a role and a session from the account the broker read from the connection, before any other check
(section 3.4); direct mode passes no caller and nothing here runs.

Pure: every function reads only the rows or the grant it is given. Imports (section 4.3): stdlib, `.db`, `.peer`
and `.bind`; never `.commands`, `.txn`, `.broker`, `.client` or `.wire`. `bind` does not import this module.
"""
from __future__ import annotations

import json
from typing import Any

from . import peer
from .bind import ARS_ROLES
from .db import WfRejected

#: The roles a map may name, and the only roles a worker account may claim (SII:70).
MAPPED_ROLES: tuple[str, ...] = ("builder", "reviewer")

AccountMap = dict[str, tuple[str, ...]]


def _content(r: dict[str, Any]) -> dict[str, Any]:
    try:
        c = json.loads(r["content"])
    except (TypeError, ValueError):
        return {}
    return c if isinstance(c, dict) else {}


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _structure(ars: object, platform: str | None) -> AccountMap | None:
    """Section 4.1 rules 1-6. `platform` None means the host's, read only when an id is checked (rule 5)."""
    if not isinstance(ars, dict) or "accounts" not in ars:
        return None                                                                   # 1: no map
    acc = ars["accounts"]
    if not isinstance(acc, dict) or not acc:
        raise WfRejected("ars", "ars.accounts must be a non-empty object")           # 2
    for role in acc:
        if role not in MAPPED_ROLES:
            raise WfRejected("ars", f"ars.accounts key {role!r} is not one of {list(MAPPED_ROLES)}")  # 3
    for role, ids in acc.items():
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            raise WfRejected("ars", f"ars.accounts[{role!r}] must be a non-empty list of id strings")  # 4
    if platform is None:
        try:
            platform = peer.host_platform()
        except OSError as exc:
            raise WfRejected("ars", f"cannot read this host's platform: {exc}") from exc
    for role, ids in acc.items():
        for i in ids:
            if not peer.is_account_id(i, platform):
                raise WfRejected("ars", f"ars.accounts[{role!r}] id {i!r} is not in the {platform} account form")  # 5
    seen: set[str] = set()
    for role, ids in acc.items():
        for i in ids:
            if i in seen:
                raise WfRejected("ars", f"ars.accounts id {i!r} appears twice")       # 6
            seen.add(i)
    return {role: tuple(ids) for role, ids in acc.items()}


def parse_accounts(ars: object, *, own_account: str, platform: str) -> AccountMap | None:
    """Section 4.1: the map of a grant's `ars`, or None when it has none. Every defect is `rejected(ars)`, checked in
    order: not an object or empty; a key other than builder/reviewer; a value that is not a non-empty list of strings;
    an id not in `platform`'s form; an id twice anywhere; the own account (the proposing account) in the map."""
    m = _structure(ars, platform)
    if m is None:
        return None
    for role, ids in m.items():
        if own_account in ids:
            raise WfRejected("ars", f"ars.accounts[{role!r}] holds this process's own account; the owner account "
                                    "is never a worker account")                     # 7
    return m


def map_of(grant: dict | None) -> AccountMap | None:
    """Section 4.3: a structural read of a stored grant's `ars.accounts` (section 4.1 rules 1-6); a defect is
    `rejected(ars)`. None for no grant, or a grant with no map."""
    if not isinstance(grant, dict):
        return None
    return _structure(grant.get("ars"), None)


def map_in_force(rows: list[dict[str, Any]]) -> AccountMap | None:
    """Section 4.3: `map_of` of the latest `grant` row whose content `state` is "activated". A proposed grant's map
    has no force (SII:80); revoke, expiry and a later proposal leave it in force."""
    for r in reversed(rows):
        if r["type"] == "grant":
            c = _content(r)
            if c.get("state") == "activated":
                return map_of(c)
    return None


def bound_sessions(rows: list[dict[str, Any]], account: str) -> list[tuple[str, str, int, int]]:
    """Section 4.3: `(role, session, generation, bind_seq)` for every `bind` row, at any generation, whose `account`
    equals `account`, with the binding table's filters (bind.py `active_roles`): author coordinator, a role in
    ARS_ROLES, a non-empty session, an int generation."""
    out: list[tuple[str, str, int, int]] = []
    for r in rows:
        if r["type"] != "bind" or r["author_role"] != "coordinator":
            continue
        c = _content(r)
        role, session, gen = c.get("role"), c.get("session"), c.get("generation")
        if role not in ARS_ROLES or not isinstance(session, str) or not session.strip() or not _is_int(gen):
            continue
        if c.get("account") == account:
            out.append((role, session, gen, r["seq"]))
    return out


def _mismatch(caller: peer.Caller, role: str, session: str, why: str) -> WfRejected:
    return WfRejected("caller-mismatch", f"account {caller.peer.account} may not claim role {role!r} session "
                                         f"{session!r}: {why}",
                      account=caller.peer.account, claimed_role=role, claimed_session=session)


def check_caller(rows: list[dict[str, Any]], caller: peer.Caller | None, *, role: str, session: str) -> None:
    """Section 4.4, the first matching row decides (SII:101-108). Raises `rejected(caller-mismatch)` or
    `rejected(caller-unattributed)`; returns when the claim passes, and the existing checks then decide.

    1. no caller (direct mode): no check.
    2. the own account, a role other than builder and reviewer: pass, with nothing read.
    3. the own account claims builder or reviewer: caller-mismatch.
    4. a worker account with no map in force, or in no list of it: caller-unattributed.
    5. a mapped worker claims a role other than builder and reviewer: caller-mismatch.
    6. a mapped worker claims builder or reviewer: pass only if the account is in map[role] and a `bind` row ties it
       to that role and the claimed session; otherwise caller-mismatch."""
    if caller is None:
        return
    if caller.own:
        if role in MAPPED_ROLES:
            raise _mismatch(caller, role, session, "the owner account runs no worker role")
        return
    account = caller.peer.account
    m = map_in_force(rows)
    if m is None or not any(account in ids for ids in m.values()):
        raise WfRejected("caller-unattributed", f"account {account} is in no account map in force for this job",
                         account=account)
    if role not in MAPPED_ROLES:
        raise _mismatch(caller, role, session, "a worker account claims only builder or reviewer")
    if account not in m.get(role, ()):
        raise _mismatch(caller, role, session, f"the account is not in ars.accounts[{role!r}]")
    if not any(b_role == role and b_session == session for b_role, b_session, _, _ in bound_sessions(rows, account)):
        raise _mismatch(caller, role, session, "no bind row ties the account to that session")
