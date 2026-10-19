"""Write transaction (FB1.4 D3, §4.2–4.4) and `wf resolve` (§4.4b)."""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Any, Callable

from . import db as dbm
from . import export, markers
from .db import (OpLog, T_ACQ_BOUND, T_HELD_BOUND, T_RESOLVE_BOUND, SLEEP_STEP, WfCommitTimeout,
                 WfCommitUncertain, WfRefused, WfRejected, begin_immediate, is_busy)
from .faults import Faults
from .validate import DuplicateIgnored, WfClaimantConflict, build_row, conflict_row, conflict_row_from_marker


class WriteResult(dict):
    """{"outcome": "COMMITTED"|"DUPLICATE-IGNORED", "seq", "self", "attempt_id", "commit_attempts", "cleared", "ops", "faults_used"}"""


def _close(conn: sqlite3.Connection | None, oplog: OpLog) -> None:
    if conn is None:
        return
    try:
        conn.close()
        oplog.add("close")
    except sqlite3.Error as exc:
        oplog.add("close_error", error=str(exc))


def _rollback_error(oplog: OpLog, holder: dict[str, Any], rexc: sqlite3.Error) -> None:
    """PACKAGE-FIX1 E5 / FIX2 #18, #47: a failing ROLLBACK where nothing durable was written is recorded beside the
    decided outcome (`rollback_error`), never raised; the caller still closes the connection."""
    oplog.add("rollback_error", error=str(rexc))
    holder["rollback_error"] = f"{getattr(rexc, 'sqlite_errorname', None) or type(rexc).__name__}: {rexc}"


def _remaining(t_start: float, bound: float, oplog: OpLog) -> float:
    """The acquisition bound covers connection setup and BEGIN IMMEDIATE together (one T_ACQ window), so a
    connect-time wait shortens the reservation wait instead of adding to it."""
    used = time.monotonic() - t_start
    oplog.add("connect", elapsed=round(used, 6))
    return max(0.0, bound - used)


def _conflict_info(conflict: WfClaimantConflict | None, outcome: str) -> dict[str, Any]:
    """Extra result fields for a conflict attempt (FB1.4-A4 §3); empty for an ordinary row."""
    if conflict is None:
        return {}
    return {"conflict_outcome": outcome, "role": conflict.role, "generation": conflict.generation,
            "holder_session": conflict.holder_session, "claimant_session": conflict.claimant_session,
            "attempted_type": conflict.attempted_type, "attempted_msg_id": conflict.attempted_msg_id}


def _conflict_unrecorded(conn: sqlite3.Connection, oplog: OpLog, conflict: WfClaimantConflict, why: str) -> WfRejected:
    """Guarantee boundary (A4 §3/§13a): no marker and no COMMIT -> nothing durable. ROLLBACK, report honestly."""
    try:
        conn.execute("ROLLBACK")
        oplog.add("rollback")
    except sqlite3.Error as rexc:
        oplog.add("rollback_error", error=str(rexc))
    oplog.add("conflict_unrecorded", why=why)
    return WfRejected("claimant-conflict",
                      f"{why}; conflict between holder {conflict.holder_session} and claimant "
                      f"{conflict.claimant_session} ({conflict.role} generation {conflict.generation}) is NOT recorded",
                      **_conflict_info(conflict, "conflict-unrecorded"))


def write_event(db_path: str, job: str, faults: Faults, oplog: OpLog, *,
                build: Callable[[sqlite3.Connection, int, str], dict[str, Any]],
                pre_insert: Callable[[sqlite3.Connection], None] | None = None,
                contract_sha: str = "", session: str = "",
                allow_no_contract: bool = False, allow_abandoned: bool = False, skip_chain: bool = False,
                acq_bound: float = T_ACQ_BOUND, held_bound: float = T_HELD_BOUND) -> WriteResult:
    """One connection, one transaction. `build(conn, head_seq, head_self)` returns the validated row
    (raises WfRejected / DuplicateIgnored). Raises WfLockTimeout, WfCommitTimeout, WfCommitUncertain,
    WfRefused, WfRejected."""
    t_start = time.monotonic()
    conn = dbm.connect(db_path, bound=acq_bound)
    attempt_id = ""
    ref: markers.MarkerRef | None = None
    marker: dict[str, Any] | None = None
    try:
        t_begin = begin_immediate(conn, oplog, _remaining(t_start, acq_bound, oplog))
        attempt_id = markers.new_attempt_id()
        oplog.add("attempt_id", attempt_id=attempt_id)
        cleared = markers.guard(conn, db_path, job, oplog, bypass=faults.arg("guard-bypass") if faults.has("guard-bypass") else "")
        if faults.has("guard-bypass"):
            faults.mark("guard-bypass")
        # recovery gate: no recovery mechanism in this slice; abandoned jobs refuse new events except the
        # closing record (basis "abandoned"), which the close command enables explicitly
        c = dbm.contract(conn, job)
        if c is None and not allow_no_contract:
            raise WfRefused(f"job {job} has no contract")
        if c is not None and c["abandoned_ts"] and not allow_abandoned:
            raise WfRefused(f"job {job} is abandoned ({c['abandon_reason']})")
        if not skip_chain:
            # held-phase check is bounded: head link + head row hashes (full chain = `wf verify`)
            hs, _ = dbm.head(conn, job)
            problems = dbm.verify_chain(conn, job, from_seq=max(1, hs - 1)) if hs else []
            oplog.add("chain_check", problems=len(problems), from_seq=max(1, hs - 1))
            if problems:
                raise WfRefused("invalid-chain: " + "; ".join(problems[:5]), problems=problems)
        head_seq, head_self = dbm.head(conn, job)
        oplog.add("head", seq=head_seq)
        # FB1.4-A4 §3: a second claimant is recorded as a conflict blocker at the same seq, inside this same
        # reservation; the attempted event is never written. `conflict` gates every deviation below.
        conflict: WfClaimantConflict | None = None
        try:
            row = build(conn, head_seq, head_self)
        except WfClaimantConflict as cexc:
            conflict = cexc
            row = conflict_row(cexc, job, head_seq, head_self)
            oplog.add("conflict_detected", role=cexc.role, generation=cexc.generation,
                      holder_session=cexc.holder_session, claimant_session=cexc.claimant_session, seq=row["seq"])
        if pre_insert is not None and conflict is None:
            pre_insert(conn)
        try:
            conn.execute(
                "INSERT INTO events (job,seq,type,msg_id,author_role,session_id,generation,reply_to,grant_ref,ts,content,content_sha256,prev,self)"
                " VALUES (:job,:seq,:type,:msg_id,:author_role,:session_id,:generation,:reply_to,:grant_ref,:ts,:content,:content_sha256,:prev,:self)",
                row)
        except sqlite3.IntegrityError as exc:
            raise WfRejected("constraint", str(exc))
        oplog.add("insert", seq=row["seq"])
        ref = markers.MarkerRef(markers.paths.pending_dir(db_path, job), row["seq"], attempt_id)
        marker = markers.make(job, row["seq"], attempt_id, row["self"], row["prev"],
                              "committing", 1, session, db_path, contract_sha or row["content"][:0])
        if conflict is not None:
            marker["conflict"] = conflict.conflict_content()
            marker["row_ts"] = row["ts"]
        try:
            if faults.has("marker-unwritable"):
                faults.mark("marker-unwritable")
                raise OSError("injected marker-unwritable")
            markers.write_atomic(ref, marker)
        except OSError as exc:
            oplog.add("marker_write_failed", error=str(exc))
            if conflict is None:
                raise WfRefused(f"marker-unwritable: {exc}")
            # conflict row only: COMMIT marker-less within T_HELD_BOUND; the row is the durable indication
            ref = None
        if ref is not None:
            oplog.add("marker_written", path=ref.path, state="committing", rev=1)
        if faults.has("kill-before-commit"):
            faults.mark("kill-before-commit")
            oplog.add("fault_kill_before_commit")
            os._exit(3)
        faults.sleep_ms("delay-precommit")
        # ---- COMMIT phase ----
        deadline = t_begin + held_bound
        attempts = 0
        while True:
            attempts += 1
            try:
                if faults.has("commit-error"):
                    arg = faults.arg("commit-error")
                    faults.mark("commit-error")
                    if arg == "after-commit":
                        conn.execute("COMMIT")
                        oplog.add("commit", attempts=attempts)
                        raise WfCommitUncertain("injected error after COMMIT", job=job, seq=row["seq"],
                                                attempt_id=attempt_id, self=row["self"], prev=row["prev"])
                    if conflict is not None and ref is None:
                        raise _conflict_unrecorded(conn, oplog, conflict, f"COMMIT raised {arg} (injected, non-BUSY)")
                    raise WfCommitUncertain(f"COMMIT raised {arg} (injected, non-BUSY)", job=job, seq=row["seq"],
                                            attempt_id=attempt_id, self=row["self"], prev=row["prev"], error=arg,
                                            **_conflict_info(conflict, "commit-uncertain"))
                conn.execute("COMMIT")
                oplog.add("commit", attempts=attempts)
                break
            except sqlite3.OperationalError as exc:
                if not is_busy(exc):
                    oplog.add("commit_error", error=str(exc))
                    ename = getattr(exc, "sqlite_errorname", type(exc).__name__)
                    if conflict is not None and ref is None:
                        raise _conflict_unrecorded(conn, oplog, conflict, f"COMMIT raised {ename}")
                    raise WfCommitUncertain(f"COMMIT raised {ename}", job=job, seq=row["seq"], attempt_id=attempt_id,
                                            self=row["self"], prev=row["prev"], **_conflict_info(conflict, "commit-uncertain"))
                now = time.monotonic()
                oplog.add("commit_busy", attempts=attempts, elapsed=round(now - t_begin, 6))
                if conn.in_transaction and now < deadline:
                    time.sleep(min(SLEEP_STEP, max(0.0, deadline - now)))
                    continue
                if conflict is not None and ref is None:
                    raise _conflict_unrecorded(conn, oplog, conflict, f"COMMIT busy past {held_bound} s, no marker")
                if conflict is not None:
                    # ---- conflict give-up: keep the marker (conflict-pending), ROLLBACK, never delete ----
                    try:
                        marker.update(state="conflict-pending", rev=2)
                        markers.write_atomic(ref, marker)
                        oplog.add("marker_conflict_pending", path=ref.path, rev=2)
                    except OSError as mexc:
                        oplog.add("marker_conflict_pending_failed", error=str(mexc))  # committing marker still stands
                    try:
                        conn.execute("ROLLBACK")
                        oplog.add("rollback")
                    except sqlite3.Error as rexc:
                        oplog.add("rollback_error", error=str(rexc))
                        raise WfCommitUncertain("give-up: ROLLBACK raised", job=job, seq=row["seq"], attempt_id=attempt_id,
                                                self=row["self"], prev=row["prev"], **_conflict_info(conflict, "conflict-pending"))
                    elapsed = time.monotonic() - t_begin
                    in_txn = conn.in_transaction
                    _close(conn, oplog)
                    raise WfCommitTimeout(f"COMMIT not achieved within {held_bound} s (busy {attempts} attempts); "
                                          f"conflict marker left {ref.path}",
                                          bound=held_bound, elapsed=elapsed, attempts=attempts, attempt_id=attempt_id,
                                          in_transaction=in_txn, marker_path=ref.path, faults_used=list(faults.used),
                                          ops=oplog.ops, **_conflict_info(conflict, "conflict-pending"))
                # ---- give-up sequence ----
                try:
                    markers.write_atomic(ref, markers.make(job, row["seq"], attempt_id, row["self"], row["prev"],
                                                           "aborting", 2, session, db_path, contract_sha))
                    oplog.add("marker_aborting", path=ref.path, rev=2)
                except OSError as mexc:
                    oplog.add("marker_aborting_failed", error=str(mexc))
                    try:
                        conn.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                    raise WfCommitUncertain("give-up: could not mark own marker aborting", job=job, seq=row["seq"],
                                            attempt_id=attempt_id, self=row["self"], prev=row["prev"])
                try:
                    conn.execute("ROLLBACK")
                    oplog.add("rollback")
                except sqlite3.Error as rexc:
                    oplog.add("rollback_error", error=str(rexc))
                    raise WfCommitUncertain("give-up: ROLLBACK raised", job=job, seq=row["seq"],
                                            attempt_id=attempt_id, self=row["self"], prev=row["prev"])
                faults.sleep_ms("delay-aborting")
                removed = markers.delete(ref)
                oplog.add("marker_delete", path=ref.path, removed=removed)
                elapsed = time.monotonic() - t_begin
                in_txn = conn.in_transaction
                _close(conn, oplog)
                raise WfCommitTimeout(f"COMMIT not achieved within {held_bound} s (busy {attempts} attempts)",
                                      bound=held_bound, elapsed=elapsed, attempts=attempts, attempt_id=attempt_id,
                                      in_transaction=in_txn, marker_path=ref.path, faults_used=list(faults.used),
                                      ops=oplog.ops)
        # ---- after COMMIT ----
        faults.sleep_ms("delay-postcommit")
        marker_left = None
        if ref is None:
            oplog.add("commit_no_marker", seq=row["seq"])
        else:
            try:
                removed = markers.delete(ref)
                oplog.add("marker_delete", path=ref.path, removed=removed)
            except OSError as dexc:
                # The row is committed; a lingering committing marker whose self matches the row is cleared by the
                # next guard as `committed`. Never report a committed write as failed.
                oplog.add("marker_delete_failed", path=ref.path, error=str(dexc))
                marker_left = ref.path
        _close(conn, oplog)
        conn = None  # type: ignore[assignment]
        export_note = export.refresh(db_path, job)
        if conflict is not None:
            outcome = "COMMITTED" if ref is not None else "COMMITTED-NO-MARKER"
            ref = None  # the generic handler below must not touch a marker that is already gone
            raise WfRejected("claimant-conflict", f"{conflict.role} generation {conflict.generation} is held by "
                             f"{conflict.holder_session}; conflict recorded at seq {row['seq']}, both sessions blocked",
                             conflict_seq=row["seq"], conflict_self=row["self"], attempt_id=attempt_id,
                             commit_attempts=attempts, export=export_note, **_conflict_info(conflict, outcome))
        res = WriteResult(outcome="COMMITTED", seq=row["seq"], self=row["self"], attempt_id=attempt_id,
                          commit_attempts=attempts, cleared=cleared, ops=oplog.ops, faults_used=list(faults.used),
                          export=export_note)
        if marker_left:
            res["marker_left"] = marker_left
        return res
    except DuplicateIgnored as dup:
        failed: dict[str, Any] = {}
        try:
            dbm.rollback(conn)
        except sqlite3.Error as rexc:  # FIX2 #47: nothing was written, so DUPLICATE-IGNORED stands
            _rollback_error(oplog, failed, rexc)
        finally:  # FIX2 #47: the write lock is never left held
            _close(conn, oplog)
        return WriteResult(outcome="DUPLICATE-IGNORED", msg_id=str(dup), attempt_id=attempt_id, ops=oplog.ops,
                           faults_used=list(faults.used), **failed)
    except WfCommitUncertain as exc:
        exc.info["ops"] = oplog.ops
        exc.info["faults_used"] = list(faults.used)
        exc.info["marker_path"] = ref.path if ref else None
        try:
            conn.close()
            oplog.add("close_after_uncertain")
        except sqlite3.Error as cexc:
            oplog.add("close_error", error=str(cexc))
        raise
    except (WfCommitTimeout,):
        raise
    except BaseException as exc:  # FIX2 #48: KeyboardInterrupt and SystemExit also roll back and close
        # REJECTED-UNCOMMITTED: anything before the first COMMIT attempt; marker (if any) is ours and uncommitted
        undeleted: OSError | None = None
        if conn is not None:
            try:
                # Review WF-109 M1: our claimant-conflict marker is never deleted without its row (FB1.4-A4 §3). As the
                # conflict give-up path does, it is kept as `conflict-pending` (best effort) and `wf resolve` re-records
                # the row. `ref` is set only after `conflict` is, so the check below never reads an unbound name.
                keep = ref is not None and conflict is not None
                if keep and marker is not None:
                    try:
                        marker.update(state="conflict-pending", rev=2)
                        markers.write_atomic(ref, marker)
                        oplog.add("marker_conflict_pending", path=ref.path, rev=2)
                    except OSError as mexc:
                        oplog.add("marker_conflict_pending_failed", error=str(mexc))  # the committing marker stands
                try:
                    dbm.rollback(conn)
                    oplog.add("rollback")
                except sqlite3.Error as rexc:
                    oplog.add("rollback_error", error=str(rexc))
                if ref is not None and not keep:
                    try:  # FIX2 #46: a failing delete of our own marker is reported, never an exit 4
                        markers.delete(ref)
                        oplog.add("marker_delete", path=ref.path, removed=True)
                    except OSError as dexc:
                        oplog.add("marker_delete_failed", path=ref.path, error=str(dexc))
                        undeleted = dexc
            finally:  # FIX2 #46: the connection, and the reservation it holds, is always closed
                _close(conn, oplog)
        if undeleted is not None and ref is not None and isinstance(exc, Exception):
            raise WfRefused(f"marker-unwritable: own marker {ref.path} not deleted "
                            f"({type(undeleted).__name__}: {undeleted}) after: {exc}", marker_path=ref.path) from exc
        raise  # FIX2 #48: the original exception, unchanged


def write_contracts_update(db_path: str, job: str, oplog: OpLog, sql: str, params: tuple) -> dict[str, Any]:
    """Bounded UPDATE on contracts only (job abandon). No event, no marker; idempotent so an uncertain
    outcome is reported and may simply be re-run."""
    t_start = time.monotonic()
    conn = dbm.connect(db_path, create=False)
    try:
        t_begin = begin_immediate(conn, oplog, _remaining(t_start, T_ACQ_BOUND, oplog))
        cur = conn.execute(sql, params)
        if cur.rowcount != 1:
            raise WfRefused(f"job {job} not found")
        deadline = t_begin + T_HELD_BOUND
        attempts = 0
        while True:
            attempts += 1
            try:
                conn.execute("COMMIT")
                oplog.add("commit", attempts=attempts)
                break
            except sqlite3.OperationalError as exc:
                if not is_busy(exc):
                    raise WfCommitUncertain(f"COMMIT raised {exc}", job=job)
                now = time.monotonic()
                if conn.in_transaction and now < deadline:
                    time.sleep(min(SLEEP_STEP, max(0.0, deadline - now)))
                    continue
                conn.execute("ROLLBACK")
                raise WfCommitTimeout(f"COMMIT not achieved within {T_HELD_BOUND} s", bound=T_HELD_BOUND,
                                      elapsed=now - t_begin, attempts=attempts)
        return {"outcome": "COMMITTED", "commit_attempts": attempts, "ops": oplog.ops}
    except Exception:
        try:
            dbm.rollback(conn)
        except sqlite3.Error:
            pass
        raise
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


# ---------------------------------------------------------------- resolve (§4.4b)

def select_marker(db_path: str, job: str, seq: int, attempt_id: str | None) -> tuple[str, markers.MarkerRef | None, list[markers.MarkerRef]]:
    """Returns (status, ref, candidates). status: 'ok' | 'AMBIGUOUS' | 'ALREADY-CLEARED'."""
    at_seq = [r for r in markers.list_markers(db_path, job) if r.seq == seq]
    if attempt_id:
        ref = markers.MarkerRef(markers.paths.pending_dir(db_path, job), seq, attempt_id)
        return ("ok", ref, at_seq)
    if len(at_seq) == 1:
        return ("ok", at_seq[0], at_seq)
    if not at_seq:
        return ("ALREADY-CLEARED", None, at_seq)
    return ("AMBIGUOUS", None, at_seq)


def resolve(db_path: str, job: str, seq: int, attempt_id: str | None, oplog: OpLog,
            disposition: str | None = None, resolve_bound: float | None = None) -> dict[str, Any]:
    bound = T_RESOLVE_BOUND if resolve_bound is None else resolve_bound
    status, ref, at_seq = select_marker(db_path, job, seq, attempt_id)
    result: dict[str, Any] = {"job": job, "seq": seq, "attempt_id": ref.attempt_id if ref else None,
                              "others_at_seq": [r.attempt_id for r in at_seq if not ref or r.attempt_id != ref.attempt_id]}
    if status != "ok":
        result["outcome"] = status
        if status == "AMBIGUOUS":
            result["candidates"] = [{"attempt_id": d["attempt_id"], "state": d["state"], "rev": d["rev"]}
                                    for d in markers.describe(db_path, job) if d["seq"] == seq]
        result["ops"] = oplog.ops
        return result
    assert ref is not None
    if disposition is not None:
        m = markers.read_checked(ref)
        if m is None:
            result["outcome"] = "ALREADY-CLEARED"
            result["ops"] = oplog.ops
            return result
        m["rev"] = int(m.get("rev", 1)) + 1
        m.setdefault("dispositions", []).append({"ts": time.time(), "text": disposition})
        markers.write_atomic(ref, m)
        markers.append_log(ref, {"outcome": "DISPOSITION", "text": disposition, "rev": m["rev"]})
        result.update(outcome="DISPOSITION", rev=m["rev"], ops=oplog.ops)
        return result
    m0 = markers.read_checked(ref)
    if m0 is None:
        result["outcome"] = "ALREADY-CLEARED"
        result["ops"] = oplog.ops
        return result
    result["m0"] = {"state": m0.get("state"), "rev": m0.get("rev")}
    t_start = time.monotonic()
    try:
        conn = dbm.connect(db_path, create=False)
    except dbm.WfLockTimeout as exc:
        result.update(outcome="RESERVATION-NOT-OBTAINED", detail=str(exc),
                      note="no change; connection setup stayed busy", ops=oplog.ops)
        markers.append_log(ref, {"outcome": "RESERVATION-NOT-OBTAINED"})
        return result
    try:
        try:
            t_begin = begin_immediate(conn, oplog, _remaining(t_start, T_ACQ_BOUND, oplog))
        except dbm.WfLockTimeout as exc:
            result.update(outcome="RESERVATION-NOT-OBTAINED", detail=str(exc),
                          note="no change; an originator still holding the reservation looks like this", ops=oplog.ops)
            markers.append_log(ref, {"outcome": "RESERVATION-NOT-OBTAINED"})
            return result
        m1 = markers.read_checked(ref)
        if m1 is None:
            try:
                dbm.rollback(conn)
            except sqlite3.Error as rexc:  # FIX2 #18: as E5; nothing was written, ALREADY-CLEARED stands
                _rollback_error(oplog, result, rexc)
            result.update(outcome="ALREADY-CLEARED", ops=oplog.ops)
            return result
        result["m1"] = {"state": m1.get("state"), "rev": m1.get("rev")}
        if (m1.get("rev"), m1.get("state")) != (m0.get("rev"), m0.get("state")):
            result["note"] = "marker changed between M0 and M1; decision from M1"
        row = dbm.row_at(conn, job, seq)
        head_seq, head_self = dbm.head(conn, job)
        problems = dbm.verify_chain(conn, job)
        in_txn = conn.in_transaction
        elapsed = time.monotonic() - t_begin
        result.update(head_seq=head_seq, chain_problems=problems, in_transaction_at_decision=in_txn,
                      resolve_elapsed=round(elapsed, 6))
        outcome: str
        if elapsed > bound or not in_txn:
            outcome = "STILL-UNCERTAIN"
            result["reason"] = "resolve budget exceeded" if elapsed > bound else "reservation not held at decision"
        elif problems:
            outcome = "STILL-UNCERTAIN"
            result["reason"] = "chain invalid"
        elif row is not None and row["self"] == m1.get("self"):
            outcome = "COMMITTED"
        elif row is not None:
            outcome = "CONFLICT"
            result["row_self"] = row["self"]
        elif head_seq == seq - 1 and head_self == m1.get("prev"):
            qc = dbm.integrity(conn, quick=True)
            result["quick_check"] = qc or ["ok"]
            outcome = "NOT-COMMITTED" if not qc else "STILL-UNCERTAIN"
            if qc:
                result["reason"] = "quick_check not ok"
        else:
            outcome = "STILL-UNCERTAIN"
            result["reason"] = f"head {head_seq} vs seq {seq} without a matching row"
        problem = markers.conflict_problem(m1)
        if problem is not None and outcome in ("CONFLICT", "NOT-COMMITTED"):
            # FIX2 #1 (WF-020 F8): a conflict marker with a malformed payload is never decided as an ordinary attempt
            # and deleted; it is kept (fail closed). Only its row present with its `self` (COMMITTED) clears it.
            outcome = "STILL-UNCERTAIN"
            result.update(reason=f"conflict marker malformed: {problem}; kept, not resolved as an ordinary attempt",
                          conflict_problem=problem)
        re_recorded = False
        if outcome == "NOT-COMMITTED" and m1.get("conflict"):
            # FB1.4-A4 §3: a conflict marker is never deleted without its row present. Re-insert the row from
            # the marker under this reservation and COMMIT within the resolve budget; delete only afterwards.
            try:
                crow = conflict_row_from_marker(m1)
            except (KeyError, TypeError) as exc:
                outcome = "STILL-UNCERTAIN"
                result["reason"] = f"conflict marker incomplete: {exc}"
            else:
                if crow["self"] != m1.get("self"):
                    outcome = "STILL-UNCERTAIN"
                    result["reason"] = "conflict marker self does not match its recorded fields"
                else:
                    conn.execute(
                        "INSERT INTO events (job,seq,type,msg_id,author_role,session_id,generation,reply_to,grant_ref,ts,content,content_sha256,prev,self)"
                        " VALUES (:job,:seq,:type,:msg_id,:author_role,:session_id,:generation,:reply_to,:grant_ref,:ts,:content,:content_sha256,:prev,:self)",
                        crow)
                    oplog.add("conflict_reinsert", seq=crow["seq"])
                    cdeadline = t_begin + bound
                    cattempts = 0
                    while True:
                        cattempts += 1
                        try:
                            conn.execute("COMMIT")
                            oplog.add("commit", attempts=cattempts)
                            re_recorded = True
                            break
                        except sqlite3.OperationalError as exc:
                            if not is_busy(exc) or not conn.in_transaction or time.monotonic() >= cdeadline:
                                oplog.add("conflict_reinsert_failed", error=str(exc), attempts=cattempts)
                                break
                            time.sleep(min(SLEEP_STEP, max(0.0, cdeadline - time.monotonic())))
                    if not re_recorded:
                        try:
                            dbm.rollback(conn)
                        except sqlite3.Error as rexc:  # FIX2 #18: as E5; the outcome is STILL-UNCERTAIN either way
                            _rollback_error(oplog, result, rexc)
                        outcome = "STILL-UNCERTAIN"
                        result["reason"] = "conflict row re-record did not COMMIT within the resolve budget"
                    else:
                        result["re_recorded"] = {"seq": crow["seq"], "self": crow["self"]}
        if outcome in ("COMMITTED", "CONFLICT", "NOT-COMMITTED"):
            try:
                removed = markers.delete(ref)
                oplog.add("marker_delete", path=ref.path, removed=removed)
                if not removed:
                    outcome = "STILL-UNCERTAIN"
                    result["reason"] = "marker vanished before delete under reservation"
            except OSError as exc:
                outcome = "STILL-UNCERTAIN"
                result["reason"] = f"delete failed: {exc}"
        if not re_recorded:
            # PACKAGE-FIX1 E5 / AMENDMENT A1.2: nothing was written in this transaction, so a failing ROLLBACK
            # does not change the decided outcome; it is recorded, and the finally block still closes the connection.
            try:
                dbm.rollback(conn)
                oplog.add("rollback")
            except sqlite3.Error as rexc:
                _rollback_error(oplog, result, rexc)
        result["outcome"] = outcome
        markers.append_log(ref, {"outcome": outcome, "m0": result.get("m0"), "m1": result.get("m1"),
                                 "head_seq": head_seq, "reason": result.get("reason"),
                                 "re_recorded": result.get("re_recorded"),
                                 "others_at_seq": result["others_at_seq"],
                                 **({"rollback_error": result["rollback_error"]} if "rollback_error" in result else {})})
        if outcome == "COMMITTED" or re_recorded:
            # the originating write never reached its export refresh; bring the export up to the committed row
            conn.close()
            conn = None
            result["export"] = export.refresh(db_path, job)
        result["ops"] = oplog.ops
        return result
    finally:
        if conn is not None:
            try:
                dbm.rollback(conn)
            except sqlite3.Error:
                pass
            try:  # E5: a failed ROLLBACK must not leave the connection (and its reservation) open
                conn.close()
            except sqlite3.Error:
                pass
