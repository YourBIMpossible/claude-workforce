"""wf — record-and-verify ledger CLI (FB1.4 R1 slice). Run with `python -I tools\\wf\\wf.py ...`."""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I omits the script dir

from wfcore import checkpointcmp, commands, paths  # noqa: E402
from wfcore.db import OpLog, WfError, WfUsage  # noqa: E402
from wfcore.faults import FaultsNotAllowed, load as load_faults  # noqa: E402

EXIT_OK, EXIT_ERR, EXIT_USAGE = 0, 1, 2
EXIT_INTERNAL = 4  # PACKAGE-FIX1 E1 / A1.1: an unexpected exception; not a refusal (1, 2) nor the fault-seam kill (3)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wf", description="Workforce ledger: record events, verify the chain.")
    p.add_argument("--db", help="ledger path (required: give --db <path> or set WF_DB; there is no default)")
    p.add_argument("--session", default=os.environ.get("WF_SESSION", ""), help="session id (or WF_SESSION)")
    p.add_argument("--role", default=os.environ.get("WF_ROLE", ""), help="author role (or WF_ROLE)")
    p.add_argument("--generation", type=int, default=1)
    p.add_argument("--msg-id", help="message id (default: random)")
    p.add_argument("--json", action="store_true", help="JSON result on stdout")
    p.add_argument("--oplog", action="store_true", help="include the operation log in the result")
    sp = p.add_subparsers(dest="cmd", required=True)

    job = sp.add_parser("job").add_subparsers(dest="sub", required=True)
    jn = job.add_parser("new"); jn.add_argument("job"); jn.add_argument("--contract", required=True)
    ja = job.add_parser("abandon"); ja.add_argument("job"); ja.add_argument("--reason", required=True)

    grant = sp.add_parser("grant").add_subparsers(dest="sub", required=True)
    gp = grant.add_parser("propose"); gp.add_argument("job"); gp.add_argument("--grant", required=True)
    ga = grant.add_parser("activate"); ga.add_argument("job")
    ga.add_argument("--owner-hash", required=True); ga.add_argument("--quote", default=""); ga.add_argument("--turn", default="")
    # B2 slice 3 (INTERFACE-CONTRACT-S3 sections 1-5): lifecycle ends, grant-check, cancel, interventions, observe
    for _name in ("revoke", "expire"):
        _g = grant.add_parser(_name); _g.add_argument("job")
        _g.add_argument("--reason", required=True); _g.add_argument("--turn", required=True, help="owner turn naming the job")

    ap = sp.add_parser("append"); ap.add_argument("job"); ap.add_argument("--type", required=True)
    ap.add_argument("--content", required=True, help="JSON file or inline JSON")
    ap.add_argument("--reply-to"); ap.add_argument("--grant-ref")

    for name in ("status", "digest", "checkpoint"):
        sp.add_parser(name).add_argument("job")
    v = sp.add_parser("verify"); v.add_argument("job", nargs="?")
    v.add_argument("--gate-file", help="deployed hook file to compare (slice 4a: reported, never repaired)")
    v.add_argument("--gate-sha", help="expected sha256 of --gate-file")
    v.add_argument("--checkpoint", metavar="SEQ:HASH",
                   help="independently retained checkpoint (seq:self) to compare with the chain (spec 3C)")
    # B2 slice 5 (INTERFACE-CONTRACT-S5 section 2): read-only check of a restored copy (restore is an owner procedure)
    v.add_argument("--restored", metavar="PATH", help="verify a restored copy at PATH (read-only; see RESTORE-RUNBOOK.md)")
    v.add_argument("--job", dest="restored_job", metavar="J", help="job for --checkpoint with --restored")
    # B2 slice 5 (INTERFACE-CONTRACT-S5 section 1): engine backup to a new file; coordinator or owner; ledger read-only
    bk = sp.add_parser("backup", help="engine backup of the ledger to a NEW file outside the ledger directory, then verify it")
    bk.add_argument("job", nargs="?"); bk.add_argument("--all", dest="all_jobs", action="store_true")
    bk.add_argument("--to", dest="dest", required=True, metavar="PATH")
    bk.add_argument("--writer-stopped", action="store_true",
                    help="the caller states every writer is stopped: head equality with the source is then asserted")
    sp.add_parser("export", help="regenerate jobs\\<job>\\export (ledger-read-only, export-writing)").add_argument("job")
    ac = sp.add_parser("accept"); ac.add_argument("job"); ac.add_argument("--results", required=True)
    cl = sp.add_parser("close"); cl.add_argument("job"); cl.add_argument("--intervention", required=True)
    rs = sp.add_parser("resolve"); rs.add_argument("job"); rs.add_argument("seq", type=int)
    rs.add_argument("--attempt"); rs.add_argument("--disposition")
    # B2 slice 1 (INTERFACE-CONTRACT 6): bind / reconcile / ack-reconcile / recover begin. The global --role and
    # --session name the invoking coordinator; the assignment being recorded uses --bind-role/--bind-session.
    bd = sp.add_parser("bind", help="record a role assignment checked against the activated grant's ARS (spec 2B)")
    bd.add_argument("job"); bd.add_argument("--bind-role", required=True, choices=("coordinator", "builder", "reviewer"))
    bd.add_argument("--bind-session", required=True); bd.add_argument("--bind-generation", type=int, default=1)
    bd.add_argument("--tool", action="append", default=None, help="declared tool (repeatable; default: contract allowed_tools)")
    bd.add_argument("--path", action="append", default=None, help="declared path glob (repeatable; default: contract scope)")
    rc = sp.add_parser("reconcile", help="compare in-scope files with baseline, receipts and intents; append the report")
    rc.add_argument("job"); rc.add_argument("--target-role", default="builder")
    ak = sp.add_parser("ack-reconcile", help="acknowledge a reconcile report (same as append --type ack-reconcile)")
    ak.add_argument("job"); ak.add_argument("--report", type=int, required=True, metavar="SEQ")
    ak.add_argument("--exceptions", required=True, help="JSON list of the report's exception ids (exactly)")
    ak.add_argument("--note", default=None)
    gc = sp.add_parser("grant-check", help="pure authority check (exit 0 allow, 1 deny); same logic as the mutation guard")
    gc.add_argument("job"); gc.add_argument("--check-role", required=True)
    gc.add_argument("--action", required=True); gc.add_argument("--path", required=True)
    cn = sp.add_parser("cancel", help="record the owner's cancellation; later work through wf is refused")
    cn.add_argument("job"); cn.add_argument("--reason", required=True); cn.add_argument("--turn", required=True)
    li = sp.add_parser("log-intervention", help="append an intervention entry (minutes: integer or 'unknown')")
    li.add_argument("job"); li.add_argument("--label", required=True)
    li.add_argument("--necessity", required=True, choices=("avoidable", "necessary", "unknown"))
    li.add_argument("--minutes", required=True); li.add_argument("--note", default="")
    gt = sp.add_parser("gate").add_subparsers(dest="sub", required=True)
    gpi = gt.add_parser("print-install", help="PRINT ONLY the gate install record and settings snippet (no install)")
    gpi.add_argument("--hooks-dir", required=True); gpi.add_argument("--root", required=True)
    gpi.add_argument("--wf-dir", default=None)
    ob = sp.add_parser("observe", help="read-only supervision report; writes only its bounded observation log")
    ob.add_argument("job")
    rv = sp.add_parser("recover").add_subparsers(dest="sub", required=True)
    rb = rv.add_parser("begin"); rb.add_argument("job"); rb.add_argument("--reason", required=True)
    rb.add_argument("--turn", required=True, help="owner turn text naming the job")
    # B2 slice 2 (INTERFACE-CONTRACT-S2 section 4): recovery closure and the 6.2 step-4 decision
    rr = rv.add_parser("reconcile", help="acknowledge each recoverable exception listed by the open recovery (idempotent)")
    rr.add_argument("job"); rr.add_argument("--turn", required=True, help="owner turn text naming the job")
    rc2 = rv.add_parser("close", help="close the open recovery once every exception is acknowledged and the chain verifies")
    rc2.add_argument("job"); rc2.add_argument("--turn", required=True, help="owner turn text naming the job")
    rd = rv.add_parser("decide", help="record the 6.2 step-4 recovery decision for the builder role (resume | restore | abandon)")
    rd.add_argument("job"); rd.add_argument("--decision", required=True, choices=("resume", "restore", "abandon"))
    rd.add_argument("--restore-path", action="append", default=None, help="report path to return to baseline (repeatable; owner procedure)")
    rd.add_argument("--unresolved", action="append", default=None, metavar="PATH=DISPOSITION",
                    help="disposition for an unresolvable report path: owner-inspected | restore | abandon-path (repeatable)")
    rd.add_argument("--turn", required=True, help="owner turn text naming the job"); rd.add_argument("--note", default=None)
    return p


def _need(args: argparse.Namespace, *fields: str) -> None:
    for f in fields:
        v = getattr(args, f)
        if not isinstance(v, str) or not v.strip():
            raise WfUsage(f"--{f} is required for this command")


def _undecodable_arg(argv: list[str]) -> int | None:
    """FIX2 #19: the 1-based position of the first argument holding a lone surrogate, which is how Python carries
    POSIX argv bytes that do not decode; None when every argument is valid text."""
    for i, arg in enumerate(argv, 1):
        if any("\ud800" <= ch <= "\udfff" for ch in arg):
            return i
    return None


def run(argv: list[str]) -> tuple[int, dict]:
    bad = _undecodable_arg(argv)
    if bad is not None:  # refused before any command runs; the argument itself is not echoed
        return EXIT_USAGE, {"ok": False, "code": "usage",
                            "error": f"rejected(usage): argument {bad} holds undecodable bytes (a lone surrogate)"}
    parser = _parser()
    args, rest = parser.parse_known_args(argv)
    if rest:
        glob = argparse.ArgumentParser(add_help=False)
        for a in parser._actions:
            if a.option_strings and a.dest not in ("help",):
                if isinstance(a, argparse._StoreTrueAction):
                    glob.add_argument(*a.option_strings, dest=a.dest, action="store_true", default=argparse.SUPPRESS)
                else:
                    glob.add_argument(*a.option_strings, dest=a.dest, type=a.type, default=argparse.SUPPRESS)
        extra = glob.parse_args(rest)
        for k, v in vars(extra).items():
            setattr(args, k, v)
    # PACKAGE-FIX1 I3: no implicit default ledger. A command without --db and without WF_DB would otherwise read or
    # write <repo>/ledger/ledger.db silently.
    db_arg = args.db or os.environ.get("WF_DB") or None
    if not db_arg or not db_arg.strip():
        return EXIT_USAGE, {"ok": False, "code": "usage",
                            "error": "rejected(usage): --db required (give --db <path> or set WF_DB)"}
    db = paths.db_path(db_arg)
    oplog = OpLog()
    msg_id = args.msg_id or uuid.uuid4().hex
    common = dict(msg_id=msg_id, role=args.role, session=args.session, generation=args.generation, oplog=oplog)
    try:
        faults = load_faults(db)
    except FaultsNotAllowed as exc:
        return EXIT_USAGE, {"ok": False, "code": "faults-not-allowed", "error": str(exc)}
    try:
        if args.cmd == "job" and args.sub == "new":
            _need(args, "role", "session")
            res = commands.job_new(db, args.job, args.contract, faults=faults, **common)
        elif args.cmd == "job":
            res = commands.job_abandon(db, args.job, args.reason, oplog)
        elif args.cmd == "grant" and args.sub in ("revoke", "expire"):
            _need(args, "role", "session")
            fn = commands.grant_revoke if args.sub == "revoke" else commands.grant_expire
            res = fn(db, args.job, reason=args.reason, turn=args.turn, faults=faults, **common)
        elif args.cmd == "grant-check":
            res = commands.grant_check_cmd(db, args.job, check_role=args.check_role, action=args.action, path=args.path)
            if not res.get("allow"):
                return EXIT_ERR, {**res, "ok": False, "code": "deny", "db": db, "msg_id": None}
        elif args.cmd == "cancel":
            _need(args, "role", "session")
            res = commands.cancel(db, args.job, reason=args.reason, turn=args.turn, faults=faults, **common)
        elif args.cmd == "log-intervention":
            _need(args, "role", "session")
            res = commands.log_intervention(db, args.job, label=args.label, necessity=args.necessity,
                                            minutes=args.minutes, note=args.note, faults=faults, **common)
        elif args.cmd == "gate":
            res = commands.gate_print_install(hooks_dir=args.hooks_dir, root=args.root, db=db, wf_dir=args.wf_dir)
        elif args.cmd == "observe":
            res = commands.observe_cmd(db, args.job)
        elif args.cmd == "grant" and args.sub == "propose":
            _need(args, "role", "session")
            res = commands.grant_propose(db, args.job, args.grant, faults=faults, **common)
        elif args.cmd == "grant":
            _need(args, "role", "session")
            res = commands.grant_activate(db, args.job, args.owner_hash, args.quote, args.turn, faults=faults, **common)
        elif args.cmd == "append":
            _need(args, "role", "session")
            res = commands.append(db, args.job, args.type, args.content, reply_to=args.reply_to,
                                  grant_ref=args.grant_ref, faults=faults, **common)
        elif args.cmd == "status":
            res = commands.status(db, args.job)
        elif args.cmd == "digest":
            res = commands.digest(db, args.job)
        elif args.cmd == "verify" and args.restored is not None:
            if args.gate_file is not None or args.gate_sha is not None:
                raise WfUsage("--gate-file/--gate-sha do not apply to --restored")
            if args.job and args.restored_job and args.job != args.restored_job:
                raise WfUsage("give the job once (positional or --job)")
            res = commands.verify_restored(args.restored, args.restored_job or args.job, args.checkpoint)
        elif args.cmd == "verify":
            if args.restored_job is not None:
                raise WfUsage("--job applies only with --restored")
            cp = checkpointcmp.parse(args.checkpoint, args.job) if args.checkpoint is not None else None
            res = commands.verify(db, args.job, checkpoint=cp, gate_file=args.gate_file, gate_sha=args.gate_sha)
        elif args.cmd == "backup":
            _need(args, "role")
            res = commands.backup_cmd(db, args.job, all_jobs=args.all_jobs, dest=args.dest,
                                      writer_stopped=args.writer_stopped, role=args.role)
        elif args.cmd == "checkpoint":
            res = commands.checkpoint(db, args.job)
        elif args.cmd == "export":
            res = commands.export_cmd(db, args.job)
            if not res["ok"]:  # refresh error or a remaining export problem: exit 1, ledger untouched
                return EXIT_ERR, {**res, "db": db, "msg_id": None}
        elif args.cmd == "accept":
            _need(args, "role", "session")
            res = commands.accept(db, args.job, args.results, faults=faults, **common)
        elif args.cmd == "close":
            _need(args, "role", "session")
            res = commands.close(db, args.job, args.intervention, faults=faults, **common)
        elif args.cmd == "resolve":
            res = commands.resolve(db, args.job, args.seq, args.attempt, args.disposition, oplog)
        elif args.cmd == "bind":
            _need(args, "role", "session")
            res = commands.bind_cmd(db, args.job, bind_role=args.bind_role, bind_session=args.bind_session,
                                    bind_generation=args.bind_generation, tools=args.tool, paths_=args.path,
                                    faults=faults, **common)
        elif args.cmd == "reconcile":
            _need(args, "role", "session")
            res = commands.reconcile_cmd(db, args.job, target_role=args.target_role, faults=faults, **common)
        elif args.cmd == "ack-reconcile":
            _need(args, "role", "session")
            res = commands.ack_reconcile(db, args.job, report_seq=args.report, exceptions_src=args.exceptions,
                                         note=args.note, faults=faults, **common)
        elif args.cmd == "recover" and args.sub == "begin":
            _need(args, "role", "session")
            res = commands.recover_begin(db, args.job, reason=args.reason, turn=args.turn, faults=faults, **common)
        elif args.cmd == "recover" and args.sub == "reconcile":
            _need(args, "role", "session")
            res = commands.recover_reconcile(db, args.job, turn=args.turn, faults=faults, **common)
        elif args.cmd == "recover" and args.sub == "close":
            _need(args, "role", "session")
            res = commands.recover_close(db, args.job, turn=args.turn, faults=faults, **common)
        elif args.cmd == "recover":  # decide
            _need(args, "role", "session")
            res = commands.recover_decide(db, args.job, decision=args.decision, restore_paths=args.restore_path,
                                          unresolved=args.unresolved, turn=args.turn, note=args.note, faults=faults,
                                          **common)
        else:  # pragma: no cover
            raise WfUsage("unknown command")
    except WfError as exc:
        out = {"ok": False, "code": exc.code, "error": str(exc), **{k: v for k, v in exc.info.items() if k != "ops"}}
        if args.oplog:
            out["ops"] = exc.info.get("ops", oplog.ops)
        code = exc.exit_code
        if exc.code == "commit-uncertain":
            out["action"] = f"wf resolve {out.get('job', '?')} {out.get('seq', '?')} --attempt {out.get('attempt_id', '?')}"
            # §4.4b: the originating command invokes resolve once with its own attempt id. An injected
            # commit-error leaves the marker for the fixture to resolve explicitly (TC-3/TC-6 setups).
            injected = any(f.startswith("commit-error") for f in out.get("faults_used", []))
            if injected:
                out["auto_resolve"] = {"outcome": "SKIPPED", "reason": "injected commit-error fault"}
            elif out.get("job") and out.get("seq") and out.get("attempt_id"):
                try:
                    out["auto_resolve"] = commands.resolve(db, out["job"], int(out["seq"]), out["attempt_id"], None, OpLog())
                except WfError as rexc:
                    out["auto_resolve"] = {"outcome": "ERROR", "error": str(rexc)}
        if isinstance(res_amb := out.get("outcome"), str) and res_amb == "AMBIGUOUS":
            code = EXIT_USAGE
        return code, out
    res = dict(res)
    res.setdefault("ok", True)
    if res.get("outcome") == "AMBIGUOUS":
        res["ok"] = False
        return EXIT_USAGE, res
    if res.get("outcome") in ("STILL-UNCERTAIN", "RESERVATION-NOT-OBTAINED"):
        res["ok"] = False
        return EXIT_ERR, res
    if not args.oplog:
        res.pop("ops", None)
    res["db"] = res.get("db", db)
    res["msg_id"] = (msg_id if args.cmd in ("job", "grant", "append", "accept", "close", "bind", "reconcile",
                                            "ack-reconcile", "recover", "cancel", "log-intervention") else None)
    return EXIT_OK, res


def _tolerant_output() -> None:
    """FIX2 #33: write plain text in each stream's own encoding with errors="backslashreplace", so no command exits
    non-zero because of output encoding, above all after a commit. `--json` output is ASCII already (json.dumps)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")


def main() -> int:
    _tolerant_output()
    argv = sys.argv[1:]
    want_json = "--json" in argv
    try:
        code, res = run(argv)
    except SystemExit as exc:  # argparse
        return int(exc.code or 0) if isinstance(exc.code, int) else EXIT_USAGE
    except Exception as exc:  # noqa: BLE001 -- E1: a crash is reported as one result, never a traceback
        code, res = EXIT_INTERNAL, {"ok": False, "code": "internal-error", "error": f"{type(exc).__name__}: {exc}"}
    if want_json:
        sys.stdout.write(json.dumps(res, indent=2, sort_keys=True, default=str) + "\n")
    else:
        if "block_text" in res:
            sys.stdout.write(res["block_text"] + "\n")
        elif res.get("ok", True):
            keys = [k for k in ("outcome", "job", "seq", "self", "attempt_id", "commit_attempts", "axes", "head",
                                "clean", "decision", "block_hash", "binding", "exceptions", "recovery") if k in res]
            sys.stdout.write(" ".join(f"{k}={json.dumps(res[k], default=str)}" for k in keys) + "\n")
        else:
            sys.stdout.write(f"{res.get('code', 'error')}: {res.get('error', res.get('outcome'))}\n")
            if "action" in res:
                sys.stdout.write(res["action"] + "\n")
    sys.stdout.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
