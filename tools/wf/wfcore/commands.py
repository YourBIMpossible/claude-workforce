"""wf sub-commands (FB1.4 D4–D6). Each returns a JSON-able dict; errors raise WfError subclasses."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from typing import Any

from . import db as dbm
from . import checkpointcmp, export, markers, pathpolicy, paths, txn
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


def _append(db: str, job: str, faults: Faults, oplog: OpLog, *, etype: str, content: dict, msg_id: str,
            role: str, session: str, generation: int, reply_to: str | None = None, grant_ref: str | None = None,
            pre_insert=None, allow_no_contract: bool = False, allow_abandoned: bool = False) -> dict[str, Any]:
    check_job_id(job)

    def build(conn: sqlite3.Connection, head_seq: int, head_self: str) -> dict[str, Any]:
        csha, ccontent = _ctx(conn, job)
        if etype == "contract":
            csha = sha256_obj(content["contract"])
            ccontent = content["contract"]
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

    res = _append(db, job, faults, oplog, etype="contract", content={"contract": contract}, msg_id=msg_id,
                  role=role, session=session, generation=generation, pre_insert=pre_insert, allow_no_contract=True)
    res["contract_sha256"] = csha
    return res


def job_abandon(db: str, job: str, reason: str, oplog: OpLog) -> dict[str, Any]:
    check_job_id(job)
    if not reason.strip():
        raise WfUsage("--reason is required")
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
    grant = _load_json(grant_src)
    if not isinstance(grant, dict):
        raise WfRejected("malformed", "grant must be a JSON object")
    content = dict(grant)
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
        text, h = _block(job, json.loads(row["content"]), res["seq"])
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
        g = _latest_grant(conn, job)
        if g is None or g.get("state") != "proposed":
            raise WfRefused("no proposed grant to activate")
        text, h = _block(job, g, g["_seq"])
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
            "contract_sha256": c["sha256"], "events": len(rows), "pending_markers": marks}


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
    finally:
        conn.close()
    marks = markers.describe(db, job)
    axes = _axes(rows, c, marks)
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
                                   for ax, ts in axis_types.items()}}
    return {"job": job, "closed": closed, "contract_sha256": c["sha256"], "events": len(rows), "counts": counts,
            "axes": axes, "pending_markers": marks,
            "head": {"seq": head["seq"] if head else 0, "self": head["self"] if head else None,
                     "db_size_bytes": db_size,
                     "note": "copy (job, seq, self) into an owner turn as the retained checkpoint"},
            "done": done, "active": active, "needs_owner": needs, "grants": grants,
            "ars": [{"grant_id": g["grant_id"], "activation_seq": g["activation_seq"], "ars": g["ars"]} for g in grants],
            "bindings": {"source": BINDING_SOURCE, "rows": list(bindings.values())},
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
                              "head": {"seq": rows[-1]["seq"], "self": rows[-1]["self"]} if rows else {"seq": 0}}
                receipts[j] = _last_receipts(rows)
                chains[j] = [(r["seq"], r["self"]) for r in rows]
    finally:
        conn.close()
    for j, v in per_job.items():
        v["export"] = export.check(db, j)
        v["pending_markers"] = markers.describe(db, j)
        v["receipts"] = _compare_receipts(db, receipts[j])
    clean = not problems["schema"] and not problems["integrity"] and all(
        not v["problems"] and not v["export"] and not v["pending_markers"] for v in per_job.values())
    result = {"db": os.path.abspath(db), "ledger_problems": problems, "jobs": per_job, "clean": clean,
              "ladder": "level-1 record-and-verify: hash chain, pins, constraints, export manifest; "
                        "not an authenticity claim"}
    if checkpoint is not None:
        result["checkpoint_job"] = job
        result = checkpointcmp.compare(result, checkpoint, chains)
    return result


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
        classes = pathpolicy.classify([str(p) for p in results["changed_paths"]], contract)
        violations = pathpolicy.violations(classes)  # reason strings (lane B)
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
                   "changed_paths": classes, "builder_session": ",".join(sorted(builder_sessions)),
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


# ---------------------------------------------------------------- resolve

def resolve(db: str, job: str, seq: int, attempt: str | None, disposition: str | None, oplog: OpLog) -> dict[str, Any]:
    check_job_id(job)
    if attempt is not None and not re.fullmatch(r"[0-9a-f]{32}", attempt):
        raise WfUsage("--attempt must be 32 hex characters")
    return txn.resolve(db, job, seq, attempt, oplog, disposition=disposition)
