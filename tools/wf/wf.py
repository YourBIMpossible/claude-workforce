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


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wf", description="Workforce ledger: record events, verify the chain.")
    p.add_argument("--db", help=f"ledger path (default {paths.DEFAULT_DB})")
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

    ap = sp.add_parser("append"); ap.add_argument("job"); ap.add_argument("--type", required=True)
    ap.add_argument("--content", required=True, help="JSON file or inline JSON")
    ap.add_argument("--reply-to"); ap.add_argument("--grant-ref")

    for name in ("status", "digest", "checkpoint"):
        sp.add_parser(name).add_argument("job")
    v = sp.add_parser("verify"); v.add_argument("job", nargs="?")
    v.add_argument("--checkpoint", metavar="SEQ:HASH",
                   help="independently retained checkpoint (seq:self) to compare with the chain (spec 3C)")
    sp.add_parser("export", help="regenerate jobs\\<job>\\export (ledger-read-only, export-writing)").add_argument("job")
    ac = sp.add_parser("accept"); ac.add_argument("job"); ac.add_argument("--results", required=True)
    cl = sp.add_parser("close"); cl.add_argument("job"); cl.add_argument("--intervention", required=True)
    rs = sp.add_parser("resolve"); rs.add_argument("job"); rs.add_argument("seq", type=int)
    rs.add_argument("--attempt"); rs.add_argument("--disposition")
    return p


def _need(args: argparse.Namespace, *fields: str) -> None:
    for f in fields:
        v = getattr(args, f)
        if not isinstance(v, str) or not v.strip():
            raise WfUsage(f"--{f} is required for this command")


def run(argv: list[str]) -> tuple[int, dict]:
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
    db = paths.db_path(args.db)
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
        elif args.cmd == "verify":
            cp = checkpointcmp.parse(args.checkpoint, args.job) if args.checkpoint is not None else None
            res = commands.verify(db, args.job, checkpoint=cp)
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
    res["msg_id"] = msg_id if args.cmd in ("job", "grant", "append", "accept", "close") else None
    return EXIT_OK, res


def main() -> int:
    argv = sys.argv[1:]
    want_json = "--json" in argv
    try:
        code, res = run(argv)
    except SystemExit as exc:  # argparse
        return int(exc.code or 0) if isinstance(exc.code, int) else EXIT_USAGE
    if want_json:
        sys.stdout.write(json.dumps(res, indent=2, sort_keys=True, default=str) + "\n")
    else:
        if "block_text" in res:
            sys.stdout.write(res["block_text"] + "\n")
        elif res.get("ok", True):
            keys = [k for k in ("outcome", "job", "seq", "self", "attempt_id", "commit_attempts", "axes", "head",
                                "clean", "decision", "block_hash") if k in res]
            sys.stdout.write(" ".join(f"{k}={json.dumps(res[k], default=str)}" for k in keys) + "\n")
        else:
            sys.stdout.write(f"{res.get('code', 'error')}: {res.get('error', res.get('outcome'))}\n")
            if "action" in res:
                sys.stdout.write(res["action"] + "\n")
    sys.stdout.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
