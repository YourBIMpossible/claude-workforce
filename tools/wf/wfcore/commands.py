"""wf sub-commands (FB1.4 D4–D6). Each returns a JSON-able dict; errors raise WfError subclasses."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from typing import Any

from . import db as dbm
from . import bind, changeset, checkpointcmp, export, grant, markers, observe, pathpolicy, paths, reconcile, txn
from .canon import canonical, sha256_file, sha256_obj, sha256_text
from .db import OpLog, WfRefused, WfRejected, WfUsage
from .faults import Faults
from . import validate
from .validate import (APPEND_TYPES, build_row, check_job_id, now_ts, validate_contract)

_HEX = re.compile(r"^[0-9a-f]+$")


def _load_json(path_or_text: str) -> Any:
    if os.path.isfile(path_or_text):
        try:
            with open(path_or_text, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except ValueError as exc:
            raise WfRejected("malformed", f"{path_or_text}: {exc}")
    try:
        return json.loads(path_or_text)
    except ValueError as exc:
        raise WfUsage(f"not a file and not JSON: {path_or_text[:80]!r} ({exc})")


def _ctx(conn: sqlite3.Connection, job: str) -> tuple[str | None, dict | None]:
    c = dbm.contract(conn, job)
    if c is None:
        return None, None
    return c["sha256"], json.loads(c["content"])


RECOVERY_TYPES = ("recovery-opened", "exception-acknowledged", "recovery-closed")


def _now() -> str:
    """INTERFACE-CONTRACT-S3 A1.4: the instant passed to grant.authority, YYYY-MM-DDTHH:MM:SSZ."""
    return validate.now_ts()[:19] + "Z"


def _author_binding(conn: sqlite3.Connection, job: str, role: str) -> dict[str, Any] | None:
    """The author's explicit binding as grant.mutation_guard / grant.check expect it, or None (implicit mode)."""
    b = bind.active_roles(conn, job).get(role)
    if b is None:
        return None
    return {"role": b.role, "tools": list(getattr(b, "tools", ()) or ()), "paths": list(getattr(b, "paths", ()) or ())}


def _guard(conn: sqlite3.Connection, job: str, *, etype: str, role: str, session: str, generation: int,
           msg_id: str, contract_sha: str | None) -> list[dict[str, Any]]:
    """B2 slice 1 (INTERFACE-CONTRACT 3-6, spec 3A step 2 and 5 rules 4-5): runs inside the write transaction
    before build_row for every role-bearing mutation. (1) recovery gate: while a recovery is open only
    `recovery-opened` itself passes (recover reconcile/close are outside the slice); (2) explicit binding check
    (a no-op for a job without `wf bind` events = implicit mode, today's observed-header behaviour);
    (3) unreconciled check (only `ack-reconcile` passes). The existing build_row checks remain mandatory and
    run afterwards. Returns the rows read so callers do not read them twice."""
    rows = dbm.rows(conn, job)
    if etype not in RECOVERY_TYPES:  # INTERFACE-CONTRACT-S2 section 6: only the recovery rows pass the gate
        opened = reconcile.recovery_open(rows)
        if opened is not None:
            raise WfRejected("recovery-open", f"recovery is open since seq {opened}; only `wf recover` and read-only "
                             "commands run (spec 3B)", opened_seq=opened)
    if etype != "contract":
        # INTERFACE-CONTRACT-S3 section 3: one authority() for this guard and for grant-check, on every call.
        grant.mutation_guard(rows, job=job, now=_now(), etype=etype, binding=_author_binding(conn, job, role))
        bind.check_actor(conn, job, role=role, session=session, generation=generation, etype=etype, msg_id=msg_id,
                         contract_sha=contract_sha or "")
        reconcile.check_actor(rows, role=role, etype=etype)
    return rows


def _reconciled(st: dict[str, Any]) -> bool | None:
    """Digest flag for one role: None before any reconcile-report, else the negation of `unreconciled` (contract 3)."""
    if st["latest_report_seq"] is None:
        return None
    return not st["unreconciled"]


def _binding_session(conn: sqlite3.Connection, job: str, role: str) -> tuple[str | None, int | None]:
    """(session, generation) the ledger currently attributes to `role`: the explicit binding when the job is in
    explicit mode, else the implicit observed one (builder only; other roles have no implicit holder)."""
    b = bind.active_roles(conn, job).get(role)
    if b is not None:
        return b.session, b.generation
    st = validate.existing_state(conn, job)
    if role == "builder" and st["builder_session"]:
        return st["builder_session"], st["gen"].get("builder")
    return None, None


def _append(db: str, job: str, faults: Faults, oplog: OpLog, *, etype: str, content: dict, msg_id: str,
            role: str, session: str, generation: int, reply_to: str | None = None, grant_ref: str | None = None,
            pre_insert=None, allow_no_contract: bool = False, allow_abandoned: bool = False) -> dict[str, Any]:
    check_job_id(job)

    def build(conn: sqlite3.Connection, head_seq: int, head_self: str) -> dict[str, Any]:
        csha, ccontent = _ctx(conn, job)
        if etype == "contract":
            csha = sha256_obj(content["contract"])
            ccontent = content["contract"]
        rows = _guard(conn, job, etype=etype, role=role, session=session, generation=generation, msg_id=msg_id,
                      contract_sha=csha)
        if etype == "ack-assignment":
            bind.check_ack_assignment(conn, job, role=role, session=session, content=content)
        elif etype == "stop-confirmed":
            bind.check_stop_confirmed(conn, job, content=content)
        elif etype == "ack-reconcile":
            bsess, _ = _binding_session(conn, job, role)
            reconcile.validate_ack(rows, role=role, session=session, binding_session=bsess, content=content)
        return build_row(conn, job, head_seq, head_self, etype=etype, msg_id=msg_id, author_role=role,
                         session_id=session, generation=generation, reply_to=reply_to, grant_ref=grant_ref,
                         content=content, contract_sha=csha, contract_content=ccontent)

    return txn.write_event(db, job, faults, oplog, build=build, pre_insert=pre_insert, session=session,
                           allow_no_contract=allow_no_contract, allow_abandoned=allow_abandoned)


# ---------------------------------------------------------------- job

def job_new(db: str, job: str, contract_src: str, msg_id: str, role: str, session: str, generation: int,
            faults: Faults, oplog: OpLog) -> dict[str, Any]:
    check_job_id(job)
    contract = _load_json(contract_src)
    validate_contract(contract)
    csha = sha256_obj(contract)

    def pre_insert(conn: sqlite3.Connection) -> None:
        if dbm.contract(conn, job) is not None:
            raise WfRefused(f"job {job} already exists")
        conn.execute("INSERT INTO contracts (job, sha256, content, created_ts) VALUES (?,?,?,?)",
                     (job, csha, canonical(contract), now_ts()))

    root = changeset.boundary_root(db)
    if root is None:  # A1 (owner grant 2026-10-15): no boundary -> exact blocker, no arbitrary scan, no job
        raise WfRefused(f"changed-set boundary cannot be established: the ledger directory of {db!r} is not named "
                        f"'ledger' (no event recorded)")
    base = changeset.baseline(root, contract)
    if base.get("problems"):
        raise WfRefused("changed-set baseline could not be captured (fail closed, no event recorded): "
                        + "; ".join(map(str, base["problems"])))
    # The baseline rides in the contract event's content beside the contract (chain-protected; the contract sha
    # and the contract-first-event rule are unchanged; validate_content still requires only "contract").
    res = _append(db, job, faults, oplog, etype="contract", content={"contract": contract, "baseline_manifest": base},
                  msg_id=msg_id, role=role, session=session, generation=generation, pre_insert=pre_insert,
                  allow_no_contract=True)
    res["contract_sha256"] = csha
    res["baseline_entries"] = len(base.get("entries", {}))
    return res


def job_abandon(db: str, job: str, reason: str, oplog: OpLog) -> dict[str, Any]:
    check_job_id(job)
    if not reason.strip():
        raise WfUsage("--reason is required")
    conn = dbm.connect(db, create=False)
    try:
        with dbm.read_snapshot(conn):
            opened = reconcile.recovery_open(dbm.rows(conn, job))
    finally:
        conn.close()
    if opened is not None:  # spec 3B: every ordinary mutation refuses while a recovery is open; checked here
        raise WfRejected("recovery-open", f"recovery is open since seq {opened}; abandon refused",  # before the
                         opened_seq=opened)                                                        # contracts update
    res = txn.write_contracts_update(
        db, job, oplog,
        "UPDATE contracts SET abandoned_ts=?, abandon_reason=? WHERE job=? AND abandoned_ts IS NULL",
        (now_ts(), reason, job))
    res.update(job=job, note="contracts row updated; events and markers untouched")
    return res


# ---------------------------------------------------------------- grant

def _block(job: str, grant: dict, proposed_seq: int) -> tuple[str, str]:
    """Grant Confirmation Block. Deterministic in (job, grant fields, contract pin, proposed position) so the
    owner's hash stays valid for a repeatable demonstration on a fresh ledger; it carries no timestamp or
    row hash. Activation still binds to the latest *proposed* grant row of this job."""
    block = {"job": job, "grant_id": grant["grant_id"], "actions": grant["actions"], "resources": grant["resources"],
             "exclusions": grant.get("exclusions", []), "ars": grant["ars"], "delegation": grant.get("delegation", {}),
             "expiry": grant["expiry"], "revocation_rule": grant["revocation_rule"],
             "contract_sha256": grant["contract_sha256"], "proposed_seq": proposed_seq}
    text = canonical(block)
    return text, sha256_text(text)


def _render_block(text: str, h: str) -> str:
    b = json.loads(text)
    lines = ["GRANT CONFIRMATION BLOCK", f"job: {b['job']}", f"grant_id: {b['grant_id']}",
             "actions: " + ", ".join(b["actions"]), "resources: " + ", ".join(b["resources"]),
             "exclusions: " + (", ".join(b["exclusions"]) or "(none)"), f"ars: {canonical(b['ars'])}",
             f"delegation: {canonical(b['delegation'])}", f"expiry: {b['expiry']}",
             f"revocation_rule: {b['revocation_rule']}", f"contract_sha256: {b['contract_sha256']}",
             f"proposed at seq {b['proposed_seq']}",
             f"block_hash: {h}",
             "To activate, the owner confirms in a chat turn naming the grant id and at least 8 hash characters."]
    return "\n".join(lines)


def grant_propose(db: str, job: str, grant_src: str, msg_id: str, role: str, session: str, generation: int,
                  faults: Faults, oplog: OpLog) -> dict[str, Any]:
    proposal = _load_json(grant_src)
    if not isinstance(proposal, dict):
        raise WfRejected("malformed", "grant must be a JSON object")
    content = dict(proposal)
    content["state"] = "proposed"
    content.pop("block_hash", None)
    res = _append(db, job, faults, oplog, etype="grant", content=content, msg_id=msg_id, role=role,
                  session=session, generation=generation)
    if res.get("outcome") == "COMMITTED":
        conn = dbm.connect(db, create=False)
        try:
            with dbm.read_snapshot(conn):
                row = dbm.row_at(conn, job, res["seq"])
        finally:
            conn.close()
        if row is None:  # pragma: no cover — the row was just committed
            raise WfRefused(f"proposed grant row {job}/{res['seq']} not readable")
        text, h = grant.block(job, json.loads(row["content"]), res["seq"])
        res.update(block=json.loads(text), block_hash=h, block_text=_render_block(text, h))
    return res


def _latest_grant(conn: sqlite3.Connection, job: str) -> dict | None:
    for r in reversed(dbm.rows(conn, job)):
        if r["type"] == "grant":
            d = json.loads(r["content"])
            d["_seq"], d["_self"] = r["seq"], r["self"]
            return d
    return None


def grant_activate(db: str, job: str, owner_hash: str, quote: str, turn: str, msg_id: str, role: str,
                   session: str, generation: int, faults: Faults, oplog: OpLog) -> dict[str, Any]:
    if not turn or not turn.strip():
        raise WfRejected("no-provenance", "--turn (owner confirmation turn reference) is required")
    if not quote or not quote.strip():
        raise WfRejected("no-provenance", "--quote (verbatim owner confirmation) is required")
    oh = (owner_hash or "").strip().lower()
    if len(oh) < 8 or not _HEX.match(oh):
        raise WfRejected("unbound-approval", "owner hash must be at least 8 hex characters")
    holder: dict[str, Any] = {}

    def build(conn: sqlite3.Connection, head_seq: int, head_self: str) -> dict[str, Any]:
        _guard(conn, job, etype="grant", role=role, session=session, generation=generation, msg_id=msg_id,
               contract_sha=_ctx(conn, job)[0])
        g = _latest_grant(conn, job)
        if g is None or g.get("state") != "proposed":
            raise WfRefused("no proposed grant to activate")
        text, h = grant.block(job, g, g["_seq"])
        if not h.startswith(oh):
            raise WfRejected("unbound-approval", f"owner hash {oh[:12]} does not match block hash {h[:12]}")
        content = {k: v for k, v in g.items() if not k.startswith("_") and k != "contract_sha256"}
        content.update(state="activated", block_hash=h, source_turn=turn, quote=quote, proposed_seq=g["_seq"])
        holder["block_hash"] = h
        csha, ccontent = _ctx(conn, job)
        return build_row(conn, job, head_seq, head_self, etype="grant", msg_id=msg_id, author_role=role,
                         session_id=session, generation=generation, reply_to=None, grant_ref=g["grant_id"],
                         content=content, contract_sha=csha, contract_content=ccontent)

    res = txn.write_event(db, job, faults, oplog, build=build, session=session)
    res.update(holder)
    return res


# ---------------------------------------------------------------- append

def append(db: str, job: str, etype: str, content_src: str, msg_id: str, role: str, session: str,
           generation: int, reply_to: str | None, grant_ref: str | None, faults: Faults, oplog: OpLog) -> dict[str, Any]:
    if etype not in APPEND_TYPES:
        raise WfUsage(f"--type must be one of {', '.join(APPEND_TYPES)}")
    content = _load_json(content_src)
    if not isinstance(content, dict):
        raise WfRejected("malformed", "content must be a JSON object")
    return _append(db, job, faults, oplog, etype=etype, content=content, msg_id=msg_id, role=role,
                   session=session, generation=generation, reply_to=reply_to, grant_ref=grant_ref)


# ---------------------------------------------------------------- status / digest

def _axes(rows: list[dict], c: dict | None, marks: list[dict]) -> dict[str, str]:
    execution = "queued"
    acceptance = "pending"
    review = "none"
    authority = "no-grant"
    blocked_roles: set[str] = set()
    for r in rows:
        t = r["type"]
        content = json.loads(r["content"])
        if t in ("intent", "receipt", "handoff"):
            execution = "active"
        elif t == "blocker" and content.get("reason") == validate.CONFLICT_REASON:
            blocked_roles.add(content.get("role"))
        elif t == "blocker":
            execution = "interrupted"
        elif t == "delivered":
            execution = "delivered"
        elif t == "accept":
            review = "fresh-review-recorded"
            acceptance = content.get("decision", "pending")
        elif t == "grant":
            authority = "in-force" if content.get("state") == "activated" else "proposed"
    if blocked_roles:
        execution = "blocked"  # FB1.4-A4 §3: a claimant conflict blocks the role for every session and generation
    recovering = reconcile.execution_axis(rows)
    if recovering is not None:
        execution = recovering  # "recovering" (spec 3B: recovery open; T16-a)
    if c and c["abandoned_ts"]:
        execution = "abandoned"
    if marks:
        execution += "+uncertain-write"
    axes = {"execution": execution, "acceptance": acceptance, "review": review, "authority": authority}
    if blocked_roles:
        axes["blocked_roles"] = sorted(blocked_roles)
    return axes


def status(db: str, job: str) -> dict[str, Any]:
    conn = dbm.connect(db, create=False)
    try:
        with dbm.read_snapshot(conn):
            c = dbm.contract(conn, job)
            if c is None:
                raise WfRefused(f"job {job} not found")
            rows = dbm.rows(conn, job)
    finally:
        conn.close()
    marks = markers.describe(db, job)
    head = rows[-1] if rows else None
    closed = any(r["type"] == "close" for r in rows)
    return {"job": job, "axes": _axes(rows, c, marks), "closed": closed,
            "head": {"seq": head["seq"], "self": head["self"]} if head else {"seq": 0, "self": None},
            "contract_sha256": c["sha256"], "events": len(rows), "pending_markers": marks,
            "recovery": _recovery_state(rows), "authority": _authority_view(job, rows)}


def _authority_view(job: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    a = grant.authority(rows, job=job, now=_now())
    out = {"state": a.get("state"), "reason": a.get("reason"), "grant_id": a.get("grant_id"),
           "block_hash": a.get("block_hash"), "expiry": a.get("expiry")}
    if a.get("state") == "cancelled":
        out["limitation"] = grant.CANCEL_LIMITATION
    return out


def _recovery_state(rows: list[dict[str, Any]]) -> dict[str, Any]:
    opened = reconcile.recovery_open(rows)
    if opened is None:
        return {"open": False}
    content = json.loads(next(r["content"] for r in rows if r["seq"] == opened))
    pending = reconcile.pending_exceptions(rows)
    listed = content.get("exceptions") or []
    return {"open": True, "opened_seq": opened, "reason": content.get("reason"),
            "exceptions": len(listed), "acknowledged": len(listed) - len(pending), "pending": len(pending)}


def export_cmd(db: str, job: str) -> dict[str, Any]:
    """`wf export <job>`: ledger-read-only, export-writing. Refuses an unknown job exactly as `status` does
    (before any directory is created), then regenerates jobs\\<job>\\export via export.refresh (bounded read
    snapshot, temp file + os.replace, writes only under paths.export_dir) and re-checks it with export.check.
    Never opens a write transaction, never appends a row, never touches markers or head."""
    conn = dbm.connect(db, create=False)
    try:
        with dbm.read_snapshot(conn):
            if dbm.contract(conn, job) is None:
                raise WfRefused(f"job {job} not found")
    finally:
        conn.close()
    note = export.refresh(db, job)
    problems = export.check(db, job) if "error" not in note else []
    ok = "error" not in note and not problems
    out: dict[str, Any] = {"ok": ok, "code": "ok" if ok else "export-error", "job": job, "export": note,
                           "problems": problems}
    if not ok:
        out["error"] = note.get("error") or "; ".join(problems)
    return out


IDENTITY_NOTE = ("role, session_id and generation are caller-supplied text; the digest reports them as recorded "
                 "and does not verify identity")
BINDING_SOURCE = "observed-event-headers (no wf bind in R1)"
BINDING_SOURCE_EXPLICIT = "wf-bind-events"  # B2 slice 1: the job has a `wf bind` table (INTERFACE-CONTRACT 3)


def _ref(job: str, seq: int) -> str:
    return f"ledger:{job}#{seq}"


def _evidence_refs(etype: str, content: dict) -> list[str]:
    """Repo-relative paths an event cites as evidence. Listed, never opened."""
    out: list[str] = []

    def add(v: Any) -> None:
        if isinstance(v, str) and v.strip():
            out.append(v)
        elif isinstance(v, list):
            for x in v:
                add(x)

    if etype == "receipt":
        add(content.get("path"))
    if etype == "delivered" and isinstance(content.get("claims"), list):
        for claim in content["claims"]:
            if isinstance(claim, dict):
                add(claim.get("evidence"))
    add(content.get("evidence"))
    return list(dict.fromkeys(out))


def digest(db: str, job: str) -> dict[str, Any]:
    """Owner surface (spec §6.1 R16, T26): Done / Active / Needs-owner with recommendations, evidence links,
    activated grants with quote and block hash, ARS, binding table and the head for the retained checkpoint.
    Ledger fields, the head and db_size_bytes come from one read snapshot (its SHARED lock blocks any commit
    under the rollback journal); pending markers are read afterwards from the marker files. Carries no
    authority and relays no raw event content."""
    conn = dbm.connect(db, create=False)
    try:
        with dbm.read_snapshot(conn):
            c = dbm.contract(conn, job)
            if c is None:
                raise WfRefused(f"job {job} not found")
            rows = dbm.rows(conn, job)
            db_size = os.path.getsize(db)
            explicit = bind.active_roles(conn, job)
            # INTERFACE-CONTRACT 3 digest: reconciled is bool | None -- None while the role has no reconcile-report yet.
            explicit_rows = bind.digest_rows(conn, job, {r: _reconciled(reconcile.state(rows, r))
                                                         for r in explicit}) if explicit else []
    finally:
        conn.close()
    marks = markers.describe(db, job)
    axes = _axes(rows, c, marks)
    recovery = _recovery_state(rows)
    head = rows[-1] if rows else None
    closed = any(r["type"] == "close" for r in rows)

    counts: dict[str, int] = {}
    done: list[dict[str, Any]] = []
    grants: list[dict[str, Any]] = []
    grant_state: dict[str, dict[str, Any]] = {}
    bindings: dict[tuple[str, str, int], dict[str, Any]] = {}
    evidence: list[dict[str, Any]] = []
    latest: dict[str, int] = {}
    last_accept: dict[str, Any] | None = None
    delivered_seq: int | None = None
    blocker_seq: int | None = None
    conflict_seqs: list[int] = []
    for r in rows:
        t, seq = r["type"], r["seq"]
        content = json.loads(r["content"])
        counts[t] = counts.get(t, 0) + 1
        latest[t] = seq
        key = (r["author_role"], r["session_id"], r["generation"])
        b = bindings.setdefault(key, {"role": key[0], "session": key[1], "generation": key[2],
                                      "first_seq": seq, "last_seq": seq, "events": 0})
        b["last_seq"] = seq
        b["events"] += 1
        for p in _evidence_refs(t, content):
            evidence.append({"path": p, "seq": seq, "type": t})
        if t == "grant":
            gid = str(content.get("grant_id", ""))
            grant_state[gid] = {"state": content.get("state"), "seq": seq}
            if content.get("state") == "activated":
                grants.append({"grant_id": gid, "quote": content.get("quote"), "source_turn": content.get("source_turn"),
                               "block_hash": content.get("block_hash"), "proposed_seq": content.get("proposed_seq"),
                               "activation_seq": seq, "ars": content.get("ars")})
                done.append({"item": f"grant {gid} activated", "seq": seq, "evidence": [_ref(job, seq)]})
        elif t == "delivered":
            delivered_seq = seq
            done.append({"item": "delivered", "seq": seq, "evidence": [_ref(job, seq)]})
        elif t == "accept":
            last_accept = {"seq": seq, "decision": content.get("decision"), "reason": content.get("reason", "")}
            done.append({"item": f"review recorded: {content.get('decision')}", "seq": seq,
                         "evidence": [_ref(job, seq)]})
        elif t == "close":
            done.append({"item": f"closed ({content.get('basis')})", "seq": seq, "evidence": [_ref(job, seq)]})
        elif t == "blocker" and content.get("reason") == validate.CONFLICT_REASON:
            conflict_seqs.append(seq)
        elif t == "blocker":
            blocker_seq = seq
        elif t in ("intent", "receipt", "handoff"):
            blocker_seq = None
    for g in grants:
        g["current_state"] = grant_state.get(g["grant_id"], {}).get("state")

    needs: list[dict[str, Any]] = []

    def need(item: str, reason: str, reco: str, ev: list[str]) -> None:
        needs.append({"item": item, "reason": reason, "recommendation": reco, "evidence": ev})

    for m in marks:
        need(f"pending write marker seq {m['seq']}", f"marker state {m['state']}: the write outcome is uncertain",
             f"run `wf resolve {job} {m['seq']}` before any further write", [m["path"]])
    if axes.get("blocked_roles"):
        need("claimant conflict", "roles blocked: " + ", ".join(map(str, axes["blocked_roles"])),
             "settle the conflicting role assignment (owner-only); no dependent work until then",
             [_ref(job, s) for s in conflict_seqs])
    authority = _authority_view(job, rows)
    if authority["state"] == "cancelled":
        need("cancelled", "the job was cancelled; later work recorded through wf is refused",
             "abandon and close the job, or start a new job (D-S3-2). " + grant.CANCEL_LIMITATION, [])
    elif authority["state"] in ("expired", "revoked", "voided", "malformed"):
        need(f"authority {authority['state']}", f"grant authority is {authority['state']} ({authority['reason']}); "
             "mediated work is refused", "renew: `wf grant propose`, then the owner confirms and `wf grant activate`",
             [])
    if recovery["open"]:
        need("recovery open", f"recovery opened at seq {recovery['opened_seq']} ({recovery['reason']}); ordinary "
             f"mutations refuse; {recovery['pending']} of {recovery['exceptions']} exceptions unacknowledged",
             f"owner-cited `wf recover reconcile {job} --turn ...` then `wf recover close {job} --turn ...`",
             [_ref(job, recovery["opened_seq"])])
    # INTERFACE-CONTRACT-S2 A-4 / section 5: an unresolvable path stays an inspection limitation after the ack
    # until a recovery-decision names it with a permitted disposition.
    bstate = reconcile.state(rows, "builder")
    if bstate["report"] is not None:
        unres = reconcile.unresolvable_paths(bstate["report"])
        if unres:
            covered: set[str] = set()
            for r in rows:
                if r["type"] == "recovery-decision":
                    dc = json.loads(r["content"])
                    if dc.get("report_seq") == bstate["latest_report_seq"]:
                        covered |= {u.get("path") for u in dc.get("unresolved") or [] if isinstance(u, dict)}
            missing = [u for u in unres if u not in covered]
            if missing:
                need("inspection limitation: unresolvable path(s)",
                     "the latest reconcile report could not relate these paths to the boundary; their filesystem "
                     "state is unknown and acknowledgement does not make it known: " + ", ".join(missing),
                     f"inspect and record `wf recover decide {job} --decision ... --unresolved <path>=<disposition>`",
                     [_ref(job, bstate["latest_report_seq"])])
    for role_name, b in explicit.items():
        rstate = reconcile.state(rows, role_name)
        if rstate["unreconciled"]:
            need(f"unreconciled {role_name}", f"reconcile report pending acknowledgement by session {b.session}",
                 f"the bound session runs `wf ack-reconcile {job} --report <seq> --exceptions <ids>`",
                 [_ref(job, rstate["latest_report_seq"])])
    if not closed and not c["abandoned_ts"]:
        if not grant_state:
            need("no grant", "the job has a contract but no grant",
                 "coordinator proposes a grant; the owner then confirms its Grant Confirmation Block",
                 [_ref(job, rows[0]["seq"])] if rows else [])
        for gid, g in grant_state.items():
            if g["state"] != "activated":
                need(f"grant {gid} {g['state']}", "grant not activated; execution is not authorized",
                     "confirm the Grant Confirmation Block (grant id and block hash) in an owner turn, then "
                     "`wf grant activate`", [_ref(job, g["seq"])])
        if blocker_seq is not None and axes["execution"].startswith("interrupted"):
            need("interrupted", "a blocker is the latest execution event",
                 "decide: resume the work or abandon the job", [_ref(job, blocker_seq)])
        if delivered_seq is not None and (last_accept is None or last_accept["seq"] < delivered_seq):
            need("review", "delivered without a fresh review",
                 "run an independent review session and record it with `wf accept`", [_ref(job, delivered_seq)])
        elif last_accept and last_accept["decision"] == "rejected":
            need("rejected", last_accept["reason"] or "review rejected the delivery",
                 "rework and re-deliver, or abandon the job", [_ref(job, last_accept["seq"])])
        elif last_accept and last_accept["decision"] == "accepted":
            need("close", "accepted but not closed",
                 "close the job with an intervention declaration (`wf close --intervention`)",
                 [_ref(job, last_accept["seq"])])
    elif c["abandoned_ts"] and not closed:
        need("close", "abandoned but not closed", "close the job (`wf close --intervention`)",
             [_ref(job, head["seq"])] if head else [])

    axis_types = {"execution": ("intent", "receipt", "handoff", "blocker", "delivered"),
                  "authority": ("grant",), "review": ("accept",)}
    active = {"axes": axes,
              "latest_event_seq": {ax: max((latest[t] for t in ts if t in latest), default=None)
                                   for ax, ts in axis_types.items()},
              "recovery": dict(recovery, audit=reconcile.recovery_audit(rows)),
              # INTERFACE-CONTRACT-S3 A2: nested under `active` (T26h pins the top-level keys); counts only, no notes
              # (the digest relays no raw event content)
              "authority": authority,
              "interventions": {k: v for k, v in grant.intervention_summary(rows).items() if k != "entries"}}
    return {"job": job, "closed": closed, "contract_sha256": c["sha256"], "events": len(rows), "counts": counts,
            "axes": axes, "pending_markers": marks,
            "head": {"seq": head["seq"] if head else 0, "self": head["self"] if head else None,
                     "db_size_bytes": db_size,
                     "note": "copy (job, seq, self) into an owner turn as the retained checkpoint"},
            "done": done, "active": active, "needs_owner": needs, "grants": grants,
            "ars": [{"grant_id": g["grant_id"], "activation_seq": g["activation_seq"], "ars": g["ars"]} for g in grants],
            "bindings": ({"source": BINDING_SOURCE_EXPLICIT, "rows": explicit_rows} if explicit
                         else {"source": BINDING_SOURCE, "rows": list(bindings.values())}),
            "evidence": evidence, "identity_note": IDENTITY_NOTE}


# ---------------------------------------------------------------- verify / checkpoint

def verify(db: str, job: str | None, checkpoint: tuple[int, str] | None = None) -> dict[str, Any]:
    """Integrity ladder (spec section 3C). `checkpoint` = retained (seq, self) for `job`, parsed by
    checkpointcmp.parse; its comparison is delegated to checkpointcmp.compare (lane C) and is never skipped
    silently: before lane C lands, a supplied checkpoint is refused."""
    if checkpoint is not None and not job:
        raise WfUsage("--checkpoint requires a job argument")
    conn = dbm.connect(db, create=False)
    try:
        with dbm.read_snapshot(conn):
            problems: dict[str, list[str]] = {"schema": dbm.constraints_present(conn), "integrity": dbm.integrity(conn)}
            jobs = [job] if job else dbm.jobs(conn)
            per_job: dict[str, Any] = {}
            receipts: dict[str, dict[str, dict[str, Any]]] = {}
            chains: dict[str, list[tuple[int, str]]] = {}
            for j in jobs:
                c = dbm.contract(conn, j)
                jp: list[str] = []
                if c is None:
                    jp.append("no contract row")
                elif sha256_text(c["content"]) != c["sha256"]:
                    jp.append("contract content does not match its sha256")
                jp += dbm.verify_chain(conn, j)
                rows = dbm.rows(conn, j)
                if rows and rows[0]["type"] != "contract":
                    jp.append("first event is not the contract event")
                per_job[j] = {"problems": jp,
                              "head": {"seq": rows[-1]["seq"], "self": rows[-1]["self"]} if rows else {"seq": 0},
                              # spec 3B l.145: an open recovery is an exception shown by verify, not a chain fault
                              "recovery_open": reconcile.recovery_open(rows)}
                receipts[j] = _last_receipts(rows)
                chains[j] = [(r["seq"], r["self"]) for r in rows]
    finally:
        conn.close()
    dir_problems, neighbors = _ledger_dir_listing(db)  # A3: ledger-directory listing, report only
    for j, v in per_job.items():
        v["export"] = export.check(db, j)
        v["pending_markers"] = markers.describe(db, j)
        v["receipts"] = _compare_receipts(db, receipts[j])
    clean = not problems["schema"] and not problems["integrity"] and not dir_problems and all(
        not v["problems"] and not v["export"] and not v["pending_markers"] for v in per_job.values())
    result = {"db": os.path.abspath(db), "ledger_problems": problems,
              "ledger_directory": {"problems": dir_problems, "neighbors": neighbors}, "jobs": per_job, "clean": clean,
              "ladder": "level-1 record-and-verify: hash chain, pins, constraints, export manifest; "
                        "internal consistency only; not an authenticity claim"}
    if checkpoint is not None:
        result["checkpoint_job"] = job
        result = checkpointcmp.compare(result, checkpoint, chains)
    return result


_SQLITE_HEADER = b"SQLite format 3\x00"


def _ledger_dir_listing(db: str) -> tuple[list[str], list[str]]:
    """A3 (spec 4.4 P4 row): list the ledger directory, report only. Legitimate: the database, its `-journal`,
    `pending/`. A neighbouring file with the SQLite header (and its `-journal`) is another ledger sharing the
    directory: informational, not stray. Everything else is `unexpected ledger-directory file: <name>` (a
    directory or link gets a trailing `/` or `@`). Nothing is deleted, moved, normalized or followed."""
    d = paths.ledger_dir(db)
    name = os.path.basename(os.path.abspath(db))
    ok = {name, name + "-journal", "pending"}
    problems: list[str] = []
    neighbors: list[str] = []
    try:
        entries = sorted(os.scandir(d), key=lambda e: e.name)
    except OSError as exc:
        return [f"ledger directory unreadable: {exc.__class__.__name__}"], neighbors
    names = {e.name for e in entries}
    for e in entries:
        if e.name in ok:
            continue
        if e.is_symlink():
            problems.append(f"unexpected ledger-directory file: {e.name}@")
            continue
        if e.is_dir(follow_symlinks=False):
            problems.append(f"unexpected ledger-directory file: {e.name}/")
            continue
        if _is_sqlite(e.path):
            neighbors.append(e.name)
            continue
        stem = e.name[: -len("-journal")] if e.name.endswith("-journal") else None
        if stem and stem in names and _is_sqlite(os.path.join(d, stem)):
            neighbors.append(e.name)
            continue
        problems.append(f"unexpected ledger-directory file: {e.name}")
    return problems, neighbors


def _is_sqlite(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(len(_SQLITE_HEADER)) == _SQLITE_HEADER
    except OSError:
        return False


def _last_receipts(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Latest receipt per recorded path (seq order)."""
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r["type"] == "receipt":
            c = json.loads(r["content"]) if isinstance(r["content"], str) else r["content"]
            out[str(c.get("path", ""))] = {"seq": r["seq"], "sha_after": c.get("sha_after", "")}
    return out


def _compare_receipts(db: str, last: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Changed paths recorded by receipts, with the file's current sha256 against the latest receipt's sha_after.
    Informational (does not affect `clean`): lets a confined reviewer compare a work file to its receipt without
    hashing tools. Paths resolve against the ledger's root (the parent of `jobs`); a path that escapes it is not read."""
    base = os.path.dirname(paths.jobs_dir(db))
    out = []
    for rel, rec in sorted(last.items()):
        item: dict[str, Any] = {"path": rel, "receipt_seq": rec["seq"], "sha_after": rec["sha_after"]}
        full = os.path.normpath(os.path.join(base, rel))
        if os.path.isabs(rel) or not paths.is_under(full, base):
            item.update(sha_now=None, match=False, note="path outside the ledger root; not read")
        elif not os.path.isfile(full):
            item.update(sha_now="", match=rec["sha_after"] == "", note="file absent")
        else:
            try:
                now = sha256_file(full)
                item.update(sha_now=now, match=now == rec["sha_after"])
            except OSError as exc:
                item.update(sha_now=None, match=False, note=f"unreadable: {exc}")
        out.append(item)
    return out


def checkpoint(db: str, job: str) -> dict[str, Any]:
    st = status(db, job)
    cp = {"job": job, "seq": st["head"]["seq"], "self": st["head"]["self"], "ts": now_ts(),
          "contract_sha256": st["contract_sha256"]}
    out = paths.export_dir(db, job)
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "checkpoint.json")
    export._write_atomic(path, canonical(cp) + "\n")
    cp["path"] = path
    cp["note"] = "retained checkpoint (job, seq, self); compare with a later verify to detect rewrite"
    return cp


# ---------------------------------------------------------------- accept (§4.4)

classify_paths = pathpolicy.classify  # compatibility alias (test_wf.py); policy lives in pathpolicy (lane B)


_NO_BASELINE = "baseline evidence missing (job predates changed-set capture); fail closed"


def _coordination(db: str, job: str) -> list[str] | None:
    """Spec 4.4 P4 coordination set for this ledger, relative to the boundary root; None when no boundary."""
    root = changeset.boundary_root(db)
    if root is None:
        return None
    ledger_rel = os.path.relpath(paths.ledger_dir(db), root).replace("\\", "/")
    return pathpolicy.coordination_globs(job, ledger_rel, os.path.basename(os.path.abspath(db)))


def _observed_changes(db: str, job: str, contract: dict, rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    """A1: changed set observed inside the boundary since `wf job new` (baseline in the contract event payload),
    Deletions come from the captured baseline only: a declared manifest path that never existed at `wf job new`
    is not a deletion (fixtures declare manifests without materialising them).
    Returns (observed, reasons); a non-empty `reasons` rejects the acceptance (fail closed). Presence-only paths
    are never read or hashed (changeset.baseline/compare); nothing outside the boundary is touched."""
    observed: dict[str, Any] = {"added": [], "modified": [], "deleted": [], "problems": [], "limitations": []}
    reasons: list[str] = []
    root = changeset.boundary_root(db)
    if root is None:
        return observed, ["changed-set boundary cannot be established (ledger directory not named 'ledger'); fail closed"]
    first = rows[0] if rows else None
    base = None
    if first is not None and first["type"] == "contract":
        try:
            base = json.loads(first["content"]).get("baseline_manifest")
        except (ValueError, AttributeError):
            base = None
    if not isinstance(base, dict) or not isinstance(base.get("entries"), dict):
        return observed, [_NO_BASELINE]
    cmp = changeset.compare(root, contract, base)
    for k in ("added", "modified", "deleted", "problems", "limitations"):
        observed[k] = [str(x) for x in cmp.get(k, [])]
    if observed["problems"]:
        reasons.append("changed-set comparison incomplete (fail closed): " + "; ".join(observed["problems"]))
    return observed, reasons


def accept(db: str, job: str, results_src: str, msg_id: str, role: str, session: str, generation: int,
           faults: Faults, oplog: OpLog) -> dict[str, Any]:
    if role != "reviewer":
        raise WfRejected("role", f"accept may not be written by role {role}")
    results = _load_json(results_src)
    if not isinstance(results, dict) or not isinstance(results.get("criteria"), list):
        raise WfRefused("malformed results: object with 'criteria' list required (no event recorded)")
    for k in ("environment", "limits", "changed_paths"):
        if k not in results:
            raise WfRefused(f"malformed results: missing {k!r} (no event recorded)")
    holder: dict[str, Any] = {}

    def build(conn: sqlite3.Connection, head_seq: int, head_self: str) -> dict[str, Any]:
        csha, contract = _ctx(conn, job)
        if contract is None:
            raise WfRefused("job has no contract")
        _guard(conn, job, etype="accept", role=role, session=session, generation=generation, msg_id=msg_id,
               contract_sha=csha)
        blocked = validate.existing_state(conn, job)["blocked_roles"]
        if blocked:  # FB1.4-A4 §3: no dependent work while a claimant conflict stands
            raise WfRejected("role-blocked", f"accept refused while roles are blocked: {sorted(blocked)}")
        rows = dbm.rows(conn, job)
        builder_sessions = {r["session_id"] for r in rows if r["author_role"] == "builder" and not validate.is_conflict_row(r)}
        if session in builder_sessions:
            raise WfRefused("same-session: the reviewer session also wrote builder events (no event recorded)")
        if not any(r["type"] == "delivered" for r in rows):
            raise WfRefused("nothing delivered yet")
        want = {c["id"]: c for c in contract["acceptance_criteria"]}
        got = {c.get("id"): c for c in results["criteria"] if isinstance(c, dict)}
        crit_out = []
        all_pass = True
        for cid, spec in want.items():
            r = got.get(cid)
            ok = bool(r and r.get("pass") is True)
            all_pass &= ok
            crit_out.append({"id": cid, "check": spec["check"], "expected": spec["expected"],
                             "actual": None if r is None else r.get("actual"), "pass": ok,
                             "missing": r is None})
        extra = [k for k in got if k not in want]
        supplied = [str(p) for p in results["changed_paths"]]
        observed, changeset_reasons = _observed_changes(db, job, contract, rows)
        # The reviewer's list can never hide an observed change: classification runs over the union (A1).
        changed = list(dict.fromkeys(supplied + observed["added"] + observed["modified"] + observed["deleted"]))
        classes = pathpolicy.classify(changed, contract, coordination=_coordination(db, job))
        violations = list(changeset_reasons) + pathpolicy.violations(classes)  # reason strings (lane B)
        declaration_reasons = validate.accept_declarations(results, contract, csha)  # T24 (lane A)
        env_ok = str(results["environment"]) == str(contract.get("environment"))
        decision = ("accepted" if all_pass and not violations and not extra and env_ok and not declaration_reasons
                    else "rejected")
        reasons = list(declaration_reasons)
        if not all_pass:
            reasons.append("criteria failed or missing: " + ", ".join(c["id"] for c in crit_out if not c["pass"]))
        reasons.extend(violations)
        if extra:
            reasons.append("results name criteria not in the contract: " + ", ".join(map(str, extra)))
        if not env_ok:
            reasons.append(f"results environment {results['environment']!r} does not match the contract's "
                           f"{contract.get('environment')!r}")
        content = {"decision": decision, "reason": "; ".join(reasons), "criteria": crit_out,
                   "environment": str(results["environment"]), "limits": str(results["limits"]),
                   "changed_paths": classes, "changed_paths_supplied": supplied, "observed_changes": observed,
                   "undeclared": sorted(set(observed["added"] + observed["modified"] + observed["deleted"]) - set(supplied)),
                   "builder_session": ",".join(sorted(builder_sessions)),
                   "results_sha256": sha256_obj(results)}
        holder.update(decision=decision, reasons=reasons, classes=classes)
        return build_row(conn, job, head_seq, head_self, etype="accept", msg_id=msg_id, author_role=role,
                         session_id=session, generation=generation, reply_to=None, grant_ref=None,
                         content=content, contract_sha=csha, contract_content=contract)

    res = txn.write_event(db, job, faults, oplog, build=build, session=session)
    res.update(holder)
    return res


# ---------------------------------------------------------------- close

def close(db: str, job: str, intervention: str, msg_id: str, role: str, session: str, generation: int,
          faults: Faults, oplog: OpLog) -> dict[str, Any]:
    if not intervention.strip():
        raise WfUsage("--intervention is required (use 'none' when there was none)")
    v = verify(db, job)
    jv = v["jobs"].get(job, {})
    if v["ledger_problems"]["schema"] or v["ledger_problems"]["integrity"] or jv.get("problems") or jv.get("export"):
        raise WfRefused("close refused: verify not clean", verify=v)
    if jv.get("pending_markers"):
        raise WfRefused("close refused: pending markers present", markers=jv["pending_markers"])
    st = status(db, job)
    basis: str
    if st["axes"]["execution"].startswith("abandoned"):
        basis = "abandoned"
    elif st["axes"]["acceptance"] == "accepted":
        basis = "accepted"
    else:
        raise WfRefused(f"close refused: acceptance is {st['axes']['acceptance']!r} and job not abandoned")
    content = {"intervention": intervention, "basis": basis, "verify": "clean",
               "head_at_close": st["head"]}
    return _append(db, job, faults, oplog, etype="close", content=content, msg_id=msg_id, role=role,
                   session=session, generation=generation, allow_abandoned=(basis == "abandoned"))


# ---------------------------------------------------------------- B2 slice 1: bind / reconcile / recover begin

def _scope_defaults(contract: dict, role: str, tools: list[str] | None, paths_: list[str] | None) -> tuple[list[str], list[str]]:
    """INTERFACE-CONTRACT 3: declared tools/paths default to the contract's allowed_tools[role] and the write
    scope (builder) or read scope (coordinator, reviewer)."""
    t = list(tools) if tools else list(contract.get("allowed_tools", {}).get(role, []))
    if paths_:
        p = list(paths_)
    else:
        p = list(contract.get("write_scope", [])) if role == "builder" else list(contract.get("read_scope", []))
    return t, p


def bind_cmd(db: str, job: str, *, bind_role: str, bind_session: str, bind_generation: int,
             tools: list[str] | None, paths_: list[str] | None, msg_id: str, role: str, session: str,
             generation: int, faults: Faults, oplog: OpLog) -> dict[str, Any]:
    """`wf bind <job>` (spec 2B l.42-43, 4.1 l.169; T32). Coordinator only. All preconditions are checked by
    bind.validate_bind inside the write transaction; a refusal writes nothing."""
    check_job_id(job)
    if role != "coordinator":
        raise WfRejected("role", f"bind may not be written by role {role}")
    if not bind_session or not bind_session.strip():
        raise WfUsage("--bind-session is required")
    holder: dict[str, Any] = {}

    def build(conn: sqlite3.Connection, head_seq: int, head_self: str) -> dict[str, Any]:
        csha, contract = _ctx(conn, job)
        if contract is None:
            raise WfRefused("job has no contract")
        _guard(conn, job, etype="bind", role=role, session=session, generation=generation, msg_id=msg_id,
               contract_sha=csha)
        auth = grant.authority(dbm.rows(conn, job), job=job, now=_now())
        g = _latest_grant(conn, job) if auth["state"] == "in-force" else None  # S3 section 3: bind needs authority in force
        t, p = _scope_defaults(contract, bind_role, tools, paths_)
        content = bind.validate_bind(conn, job, role=bind_role, session=bind_session, generation=bind_generation,
                                     tools=t, paths=p, author_session=session, grant=g,
                                     grant_seq=g["_seq"] if g else None,
                                     state=validate.existing_state(conn, job))
        holder.update(binding={"role": bind_role, "session": bind_session, "generation": bind_generation})
        return build_row(conn, job, head_seq, head_self, etype="bind", msg_id=msg_id, author_role=role,
                         session_id=session, generation=generation, reply_to=None, grant_ref=None,
                         content=content, contract_sha=csha, contract_content=contract)

    res = txn.write_event(db, job, faults, oplog, build=build, session=session)
    res.update(holder)
    return res


def reconcile_cmd(db: str, job: str, *, target_role: str, msg_id: str, role: str, session: str, generation: int,
                  faults: Faults, oplog: OpLog) -> dict[str, Any]:
    """`wf reconcile <job>` (spec 4.1 l.178, 6.2 step 3; T06). Coordinator only; compares the in-scope files
    with the baseline, receipts and intents and appends one `reconcile-report` row. Nothing on disk changes."""
    check_job_id(job)
    if role != "coordinator":
        raise WfRejected("role", f"reconcile may not be written by role {role}")
    if target_role != "builder":
        raise WfRejected("role", f"nothing to reconcile for read-only role {target_role!r} (only the builder writes)")
    root = changeset.boundary_root(db)
    if root is None:
        raise WfRefused(f"changed-set boundary cannot be established: the ledger directory of {db!r} is not named "
                        f"'ledger' (no event recorded)")
    holder: dict[str, Any] = {}

    def build(conn: sqlite3.Connection, head_seq: int, head_self: str) -> dict[str, Any]:
        csha, contract = _ctx(conn, job)
        if contract is None:
            raise WfRefused("job has no contract")
        rows = _guard(conn, job, etype="reconcile-report", role=role, session=session, generation=generation,
                      msg_id=msg_id, contract_sha=csha)
        baseline = json.loads(rows[0]["content"]).get("baseline_manifest") if rows else None
        if not isinstance(baseline, dict):
            raise WfRefused(_NO_BASELINE)
        bsess, bgen = _binding_session(conn, job, target_role)
        binding = (bsess, bgen) if bsess is not None and bgen is not None else None
        content = reconcile.compute_report(root, contract, baseline, rows, role=target_role, binding=binding,
                                           head_seq=head_seq)
        holder.update(exceptions=content.get("exceptions", []), through_seq=content.get("through_seq"))
        return build_row(conn, job, head_seq, head_self, etype="reconcile-report", msg_id=msg_id, author_role=role,
                         session_id=session, generation=generation, reply_to=None, grant_ref=None,
                         content=content, contract_sha=csha, contract_content=contract)

    res = txn.write_event(db, job, faults, oplog, build=build, session=session)
    res.update(holder)
    return res


def ack_reconcile(db: str, job: str, *, report_seq: int, exceptions_src: str, note: str | None, msg_id: str,
                  role: str, session: str, generation: int, faults: Faults, oplog: OpLog) -> dict[str, Any]:
    """`wf ack-reconcile <job>` — the explicit acknowledgement verb (owner disposition); identical to
    `wf append --type ack-reconcile` with the content assembled from the flags. Validity is checked in _append."""
    ids = _load_json(exceptions_src)
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise WfRejected("malformed", "--exceptions must be a JSON list of exception ids")
    content: dict[str, Any] = {"role": role, "session": session, "report_seq": report_seq, "exceptions": ids}
    if note:
        content["note"] = note
    return _append(db, job, faults, oplog, etype="ack-reconcile", content=content, msg_id=msg_id, role=role,
                   session=session, generation=generation)


def recover_begin(db: str, job: str, *, reason: str, turn: str, msg_id: str, role: str, session: str,
                  generation: int, faults: Faults, oplog: OpLog) -> dict[str, Any]:
    """`wf recover begin <job>` (spec 3B l.131-136; T16-a). Coordinator only. Recoverable exceptions are
    collected read-only before the transaction (export state, orphan temp files; no gate log in R1); inside the
    transaction the whole chain is verified and, only if valid, one `recovery-opened` row is appended. An
    invalid chain appends nothing. `recover reconcile` / `recover close` are outside this slice."""
    check_job_id(job)
    if role != "coordinator":
        raise WfRejected("role", f"recover begin may not be written by role {role}")
    if not reason or not reason.strip():
        raise WfUsage("--reason is required")
    if not turn or not turn.strip():
        raise WfRejected("no-provenance", "--turn (owner turn naming the job) is required")
    exceptions = reconcile.recoverable_exceptions(db, job)
    holder: dict[str, Any] = {}

    def build(conn: sqlite3.Connection, head_seq: int, head_self: str) -> dict[str, Any]:
        csha, contract = _ctx(conn, job)
        if contract is None:
            raise WfRefused("job has no contract")
        rows = _guard(conn, job, etype="recovery-opened", role=role, session=session, generation=generation,
                      msg_id=msg_id, contract_sha=csha)
        content = {"reason": reason, "owner_turn": turn, "exceptions": exceptions,
                   "chain_head": {"seq": head_seq, "self": head_self}}
        reconcile.validate_recover_begin(rows, job=job, content=content)
        problems = reconcile.chain_problems(conn, job)
        if problems:
            raise WfRejected("invalid-chain", "chain invalid; recovery not opened (nothing appended): "
                             + "; ".join(problems[:5]), problems=problems)
        holder.update(exceptions=exceptions)
        return build_row(conn, job, head_seq, head_self, etype="recovery-opened", msg_id=msg_id, author_role=role,
                         session_id=session, generation=generation, reply_to=None, grant_ref=None,
                         content=content, contract_sha=csha, contract_content=contract)

    res = txn.write_event(db, job, faults, oplog, build=build, session=session)
    res.update(holder)
    _recover_kill(faults, oplog, "recover-kill-after-open")
    return res


def _recover_kill(faults: Faults, oplog: OpLog, name: str, *, when: int | None = None, count: int | None = None) -> None:
    """INTERFACE-CONTRACT-S2 section 4 fault points (T30 b): exit the process after the named transaction committed.
    `recover-kill-mid-reconcile:<k>` fires once `count` acknowledgements have committed in this run."""
    if not faults.has(name):
        return
    if when is not None and count is not None:
        try:
            k = int(faults.arg(name) or "0")
        except ValueError:
            k = 0
        if count != k:
            return
    faults.mark(name)
    oplog.add(f"fault_{name.replace('-', '_')}")
    os._exit(3)


def _recovery_turn(job: str, turn: str) -> str:
    if not turn or not turn.strip():
        raise WfRejected("no-provenance", "--turn (owner turn naming the job) is required")
    if job not in turn:
        raise WfRejected("no-provenance", f"the owner turn must name the job {job!r}")
    return turn


def recover_reconcile(db: str, job: str, *, turn: str, msg_id: str, role: str, session: str, generation: int,
                      faults: Faults, oplog: OpLog) -> dict[str, Any]:
    """`wf recover reconcile <job>` (spec 3B; INTERFACE-CONTRACT-S2 section 4). Coordinator only. Appends one
    `exception-acknowledged` row per exception the open `recovery-opened` row lists and no earlier row acknowledges;
    one write transaction per item, so a re-run after a kill appends only the missing ones (idempotent)."""
    check_job_id(job)
    if role != "coordinator":
        raise WfRejected("role", f"recover reconcile may not be written by role {role}")
    _recovery_turn(job, turn)
    conn = dbm.connect(db, create=False)
    try:
        with dbm.read_snapshot(conn):
            rows = dbm.rows(conn, job)
    finally:
        conn.close()
    opened = reconcile.recovery_open(rows)
    if opened is None:
        raise WfRejected("no-recovery", "no recovery is open for this job")
    pending = reconcile.pending_exceptions(rows)
    already = sorted(reconcile.acknowledged(rows, opened))
    done: list[str] = []
    last: dict[str, Any] = {"seq": None}
    _recover_kill(faults, oplog, "recover-kill-mid-reconcile", when=1, count=0)  # k = 0: die before any acknowledgement
    for item in pending:
        content = {"opened_seq": opened, "id": item["id"], "class": item["class"], "detail": item["detail"],
                   "owner_turn": turn}
        item_msg = f"{msg_id}-{item['id']}" if len(pending) > 1 else msg_id

        def build(conn: sqlite3.Connection, head_seq: int, head_self: str, content=content, item_msg=item_msg) -> dict[str, Any]:
            csha, contract = _ctx(conn, job)
            if contract is None:
                raise WfRefused("job has no contract")
            rs = _guard(conn, job, etype="exception-acknowledged", role=role, session=session, generation=generation,
                        msg_id=item_msg, contract_sha=csha)
            reconcile.validate_exception_ack(rs, job=job, content=content)
            return build_row(conn, job, head_seq, head_self, etype="exception-acknowledged", msg_id=item_msg,
                             author_role=role, session_id=session, generation=generation, reply_to=None,
                             grant_ref=None, content=content, contract_sha=csha, contract_content=contract)

        last = txn.write_event(db, job, faults, oplog, build=build, session=session)
        done.append(item["id"])
        _recover_kill(faults, oplog, "recover-kill-mid-reconcile", when=1, count=len(done))
    res = dict(last)
    res.update(ok=True, opened_seq=opened, acknowledged=done, already=already, msg_id=msg_id)
    return res


def recover_close(db: str, job: str, *, turn: str, msg_id: str, role: str, session: str, generation: int,
                  faults: Faults, oplog: OpLog) -> dict[str, Any]:
    """`wf recover close <job>` (spec 3B; INTERFACE-CONTRACT-S2 section 4; T30 d). Coordinator only. One transaction:
    open recovery, every listed exception acknowledged, whole chain valid -> `recovery-closed`; else nothing appended."""
    check_job_id(job)
    if role != "coordinator":
        raise WfRejected("role", f"recover close may not be written by role {role}")
    _recovery_turn(job, turn)
    _recover_kill(faults, oplog, "recover-kill-before-close")
    holder: dict[str, Any] = {}

    def build(conn: sqlite3.Connection, head_seq: int, head_self: str) -> dict[str, Any]:
        csha, contract = _ctx(conn, job)
        if contract is None:
            raise WfRefused("job has no contract")
        rows = _guard(conn, job, etype="recovery-closed", role=role, session=session, generation=generation,
                      msg_id=msg_id, contract_sha=csha)
        opened = reconcile.recovery_open(rows)
        if opened is None:
            raise WfRejected("no-recovery", "no recovery is open for this job")
        acked = sorted(reconcile.acknowledged(rows, opened))
        content = {"opened_seq": opened, "acknowledged": acked, "chain_head": {"seq": head_seq, "self": head_self},
                   "owner_turn": turn}
        reconcile.validate_recover_close(rows, job=job, content=content)
        problems = reconcile.chain_problems(conn, job)
        if problems:
            raise WfRejected("invalid-chain", "chain invalid; recovery not closed (nothing appended): "
                             + "; ".join(problems[:5]), problems=problems)
        holder.update(opened_seq=opened, acknowledged=acked)
        return build_row(conn, job, head_seq, head_self, etype="recovery-closed", msg_id=msg_id, author_role=role,
                         session_id=session, generation=generation, reply_to=None, grant_ref=None,
                         content=content, contract_sha=csha, contract_content=contract)

    res = txn.write_event(db, job, faults, oplog, build=build, session=session)
    res.update(holder)
    return res


def _parse_unresolved(items: list[str] | None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for it in items or []:
        path, sep, disp = it.rpartition("=")
        if not sep or not path or not disp:
            raise WfUsage(f"--unresolved expects PATH=DISPOSITION, got {it!r}")
        out.append({"path": path, "disposition": disp})
    return out


def _stopped_seq(conn: sqlite3.Connection, job: str, rows: list[dict[str, Any]], role_name: str,
                 bsess: str | None) -> int | None:
    """seq of the stop-confirmed row for the role's current session (explicit binding or implicit holder)."""
    table = bind.active_roles(conn, job)
    if role_name in table:
        return table[role_name].stopped_seq
    if bsess is None:
        return None
    for r in reversed(rows):
        if r["type"] == "stop-confirmed":
            c = json.loads(r["content"])
            if c.get("role") == role_name and c.get("session") == bsess:
                return r["seq"]
    return None


def recover_decide(db: str, job: str, *, decision: str, restore_paths: list[str] | None, unresolved: list[str] | None,
                   turn: str, note: str | None, msg_id: str, role: str, session: str, generation: int,
                   faults: Faults, oplog: OpLog) -> dict[str, Any]:
    """`wf recover decide <job>` (spec 6.2 step 4; INTERFACE-CONTRACT-S2 sections 1, 3, 5). Coordinator only; an
    ordinary mutation (refused while a recovery is open). Records resume | restore | abandon for the builder role
    against the latest reconcile report and the stop-confirmed row, naming every unresolvable path's disposition.
    `wf` writes no file: restore is an owner procedure recorded here."""
    check_job_id(job)
    if role != "coordinator":
        raise WfRejected("role", f"recover decide may not be written by role {role}")
    _recovery_turn(job, turn)
    unres = _parse_unresolved(unresolved)
    holder: dict[str, Any] = {}

    def build(conn: sqlite3.Connection, head_seq: int, head_self: str) -> dict[str, Any]:
        csha, contract = _ctx(conn, job)
        if contract is None:
            raise WfRefused("job has no contract")
        rows = _guard(conn, job, etype="recovery-decision", role=role, session=session, generation=generation,
                      msg_id=msg_id, contract_sha=csha)
        bsess, bgen = _binding_session(conn, job, "builder")
        binding = (bsess, bgen) if bsess is not None and bgen is not None else None
        stopped = _stopped_seq(conn, job, rows, "builder", bsess)
        st = reconcile.state(rows, "builder")
        content: dict[str, Any] = {"decision": decision, "role": "builder", "report_seq": st["latest_report_seq"],
                                   "stop_seq": stopped, "restore_paths": list(restore_paths or []),
                                   "unresolved": unres, "owner_turn": turn}
        if note:
            content["note"] = note
        reconcile.validate_recovery_decision(rows, job=job, binding=binding, stopped_seq=stopped, content=content)
        holder.update(decision=decision, report_seq=st["latest_report_seq"], stop_seq=stopped)
        return build_row(conn, job, head_seq, head_self, etype="recovery-decision", msg_id=msg_id, author_role=role,
                         session_id=session, generation=generation, reply_to=None, grant_ref=None,
                         content=content, contract_sha=csha, contract_content=contract)

    res = txn.write_event(db, job, faults, oplog, build=build, session=session)
    res.update(holder)
    return res


# ---------------------------------------------------------------- slice 3: lifecycle, grant-check, cancel, observe

def _lifecycle_end(db: str, job: str, *, etype: str, reason: str, turn: str, msg_id: str, role: str, session: str,
                   generation: int, faults: Faults, oplog: OpLog) -> dict[str, Any]:
    """`wf grant revoke|expire` (spec 2B l.48, 4.1; INTERFACE-CONTRACT-S3 sections 1-3). Ends the current in-force
    revision at once; renewal stays possible (propose + owner confirmation + activate)."""
    check_job_id(job)
    holder: dict[str, Any] = {}

    def build(conn: sqlite3.Connection, head_seq: int, head_self: str) -> dict[str, Any]:
        csha, contract = _ctx(conn, job)
        if contract is None:
            raise WfRefused("job has no contract")
        rows = _guard(conn, job, etype=etype, role=role, session=session, generation=generation, msg_id=msg_id,
                      contract_sha=csha)
        a = grant.authority(rows, job=job, now=_now())
        content = {"grant_id": a.get("grant_id"), "block_hash": a.get("block_hash"), "reason": reason,
                   "owner_turn": turn}
        grant.validate_revoke(rows, job=job, now=_now(), content=content)
        holder.update(grant_id=a.get("grant_id"), block_hash=a.get("block_hash"))
        return build_row(conn, job, head_seq, head_self, etype=etype, msg_id=msg_id, author_role=role,
                         session_id=session, generation=generation, reply_to=None, grant_ref=a.get("grant_id"),
                         content=content, contract_sha=csha, contract_content=contract)

    res = txn.write_event(db, job, faults, oplog, build=build, session=session)
    res.update(holder)
    return res


def grant_revoke(db: str, job: str, *, reason: str, turn: str, **kw: Any) -> dict[str, Any]:
    return _lifecycle_end(db, job, etype="grant-revoked", reason=reason, turn=turn, **kw)


def grant_expire(db: str, job: str, *, reason: str, turn: str, **kw: Any) -> dict[str, Any]:
    return _lifecycle_end(db, job, etype="grant-expired", reason=reason, turn=turn, **kw)


def grant_check_cmd(db: str, job: str, *, check_role: str, action: str, path: str) -> dict[str, Any]:
    """`wf grant-check` (spec 4.1; INTERFACE-CONTRACT-S3 section 4): pure, read-only; same authority() as the guard."""
    check_job_id(job)
    conn = dbm.connect(db, create=False)
    try:
        with dbm.read_snapshot(conn):
            c = dbm.contract(conn, job)
            if c is None:
                raise WfRefused(f"job {job} not found")
            rows = dbm.rows(conn, job)
            binding = _author_binding(conn, job, check_role)
    finally:
        conn.close()
    contract = json.loads(c["content"])
    res = grant.check(rows, job=job, now=_now(), role=check_role, action=action, path=path, contract=contract,
                      binding=binding)
    return dict(res, job=job, role=check_role, action=action, path=path)


def cancel(db: str, job: str, *, reason: str, turn: str, msg_id: str, role: str, session: str, generation: int,
           faults: Faults, oplog: OpLog) -> dict[str, Any]:
    """`wf cancel` (spec 4.1, 6.3; INTERFACE-CONTRACT-S3 section 5). Records the owner's cancellation."""
    check_job_id(job)

    def build(conn: sqlite3.Connection, head_seq: int, head_self: str) -> dict[str, Any]:
        csha, contract = _ctx(conn, job)
        if contract is None:
            raise WfRefused("job has no contract")
        rows = _guard(conn, job, etype="job-cancelled", role=role, session=session, generation=generation,
                      msg_id=msg_id, contract_sha=csha)
        content = {"reason": reason, "owner_turn": turn}
        grant.validate_cancel(rows, job=job, content=content)
        return build_row(conn, job, head_seq, head_self, etype="job-cancelled", msg_id=msg_id, author_role=role,
                         session_id=session, generation=generation, reply_to=None, grant_ref=None,
                         content=content, contract_sha=csha, contract_content=contract)

    res = txn.write_event(db, job, faults, oplog, build=build, session=session)
    res.update(limitation=grant.CANCEL_LIMITATION)
    return res


def log_intervention(db: str, job: str, *, label: str, necessity: str, minutes: str, note: str, msg_id: str,
                     role: str, session: str, generation: int, faults: Faults, oplog: OpLog) -> dict[str, Any]:
    """`wf log-intervention` (spec 4.1, 6.2 step 6). Minutes are an integer or `unknown`, never estimated."""
    m: int | str
    if minutes == "unknown":
        m = "unknown"
    else:
        try:
            m = int(minutes)
        except ValueError:
            raise WfUsage("--minutes must be a non-negative integer or 'unknown'") from None
    content = {"label": label, "necessity": necessity, "minutes": m, "note": note or ""}
    return _append(db, job, faults, oplog, etype="intervention", content=content, msg_id=msg_id, role=role,
                   session=session, generation=generation)


def observe_cmd(db: str, job: str) -> dict[str, Any]:
    """`wf observe` (spec 4.1, 6.2 step 1, 7; INTERFACE-CONTRACT-S3 section 5). Read-only on the ledger; its only
    write is the bounded observation log. No contact, resume, launch or recovery."""
    check_job_id(job)
    conn = dbm.connect(db, create=False)
    try:
        with dbm.read_snapshot(conn):
            c = dbm.contract(conn, job)
            if c is None:
                raise WfRefused(f"job {job} not found")
            rows = dbm.rows(conn, job)
    finally:
        conn.close()
    contract = json.loads(c["content"])
    th, notes = observe.thresholds(contract if isinstance(contract, dict) else {})
    now = _now()
    res = observe.classify(rows, now=now, thresholds=th)
    res = dict(res)
    res["uncertainty"] = list(res.get("uncertainty", [])) + [n for n in notes if n not in res.get("uncertainty", [])]
    res["authority"] = _authority_view(job, rows)
    entry = {"job": job, "now": now, "class": res.get("class"), "evidence_seqs": [e.get("seq") for e in res.get("evidence", [])],
             "authority": res["authority"]["state"]}
    try:
        res["log"] = observe.append_log(db, job, entry)
    except (ValueError, TypeError, OSError) as exc:  # S3O refusals (link/junction, non-regular log, bad entry): fail closed
        raise WfRefused(f"observation log not written: {exc}") from exc
    res["job"] = job
    return res


# ---------------------------------------------------------------- resolve

def resolve(db: str, job: str, seq: int, attempt: str | None, disposition: str | None, oplog: OpLog) -> dict[str, Any]:
    check_job_id(job)
    if attempt is not None and not re.fullmatch(r"[0-9a-f]{32}", attempt):
        raise WfUsage("--attempt must be 32 hex characters")
    return txn.resolve(db, job, seq, attempt, oplog, disposition=disposition)
