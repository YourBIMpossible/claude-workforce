"""wf — record-and-verify ledger CLI (FB1.4 R1 slice). Run with `python -I tools\\wf\\wf.py ...`."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import uuid

WF_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WF_DIR)  # -I omits the script dir

from wfcore import checkpointcmp, client, commands, paths, peer, wire  # noqa: E402
from wfcore.db import OpLog, WfError, WfRefused, WfRejected, WfUsage  # noqa: E402
from wfcore.faults import FaultsNotAllowed, load as load_faults  # noqa: E402

EXIT_OK, EXIT_ERR, EXIT_USAGE = 0, 1, 2
EXIT_INTERNAL = 4  # PACKAGE-FIX1 E1 / A1.1: an unexpected exception; not a refusal (1, 2) nor the fault-seam kill (3)

# FIX3 fix 2 (SPEC §2, design 2-i): every global option that takes a value (--db, --broker, --session, --role,
# --generation, --msg-id) may be given once, before the command or after it. A repeat in either position, or across
# both, is refused by run(); it is never resolved as "last wins" or "first wins". The flags (--json, --oplog) may repeat.
_GIVEN = "_wf_given"  # namespace attribute: each store of a global value option, in command-line order

# FIX3-II (INTERFACE-CONTRACT-FIX3-II r2 section 3.5): the commands a worker account may not run through the broker
# (SII:191-194). `verify` joins them when it names a path (--restored, --gate-file, --gate-sha).
_NOT_BROKERED = frozenset({"job abandon", "resolve", "export", "checkpoint", "observe", "backup", "gate print-install"})


class _StoreOnce(argparse.Action):
    """FIX3 fix 2: store the value as argparse's default action does, and record the store under _GIVEN so that run()
    can refuse an option given more than once. Defaults (WF_SESSION, WF_ROLE) are set without calling the action, so
    they never count; a value that only reads like an option (a --note text) is not stored here, so it never counts."""

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        given = getattr(namespace, _GIVEN, None)
        if given is None:
            given = []
            setattr(namespace, _GIVEN, given)
        given.append(self.option_strings[0])


def _parser() -> argparse.ArgumentParser:
    # FIX3 fix 2: allow_abbrev=False, so an abbreviation (--ro) cannot stand for --role and hide a repeat. The
    # subcommands' own options keep their abbreviations.
    p = argparse.ArgumentParser(prog="wf", description="Workforce ledger: record events, verify the chain.",
                                allow_abbrev=False)
    p.add_argument("--db", action=_StoreOnce,
                   help="ledger path (required: give --db <path> or set WF_DB; there is no default)")
    p.add_argument("--broker", action=_StoreOnce,
                   help="broker address (or WF_BROKER): send the command to the account-checking broker, not --db")
    p.add_argument("--session", action=_StoreOnce, default=os.environ.get("WF_SESSION", ""),
                   help="session id (or WF_SESSION)")
    p.add_argument("--role", action=_StoreOnce, default=os.environ.get("WF_ROLE", ""), help="author role (or WF_ROLE)")
    p.add_argument("--generation", action=_StoreOnce, type=int, default=1)
    p.add_argument("--msg-id", action=_StoreOnce, help="message id (default: random)")
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
    bd.add_argument("--bind-account", default=None,
                    help="the OS account id the bound session runs as (FIX3-II section 4.2; checked against the grant's map)")
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
    # FIX3-II section 4.5: the account wf runs as (direct) or the broker read (brokered); with a job, its map entries
    wh = sp.add_parser("whoami", help="print the account this call runs as; with a job, the roles and sessions it holds")
    wh.add_argument("job", nargs="?")
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


def _usage(text: str) -> tuple[int, dict]:
    return EXIT_USAGE, {"ok": False, "code": "usage", "error": f"rejected(usage): {text}"}


def _undecodable_refusal(argv: list[str]) -> tuple[int, dict] | None:
    bad = _undecodable_arg(argv)
    if bad is not None:  # refused before any command runs; the argument itself is not echoed
        return _usage(f"argument {bad} holds undecodable bytes (a lone surrogate)")
    return None


def _parse(argv: list[str]) -> tuple[argparse.ArgumentParser, argparse.Namespace, list[str]]:
    """Parse the command line, then any global options after the command (FIX3 fix 2). Returns the parser, the
    namespace and fix 2's record of each global value option given, in order. An argparse error raises SystemExit."""
    parser = _parser()
    args, rest = parser.parse_known_args(argv)
    given: list[str] = vars(args).pop(_GIVEN, [])
    if rest:  # global options after the command: re-parsed, and counted with the leading ones (FIX3 fix 2)
        glob = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
        for a in parser._actions:
            if a.option_strings and a.dest not in ("help",):
                if isinstance(a, argparse._StoreTrueAction):
                    glob.add_argument(*a.option_strings, dest=a.dest, action="store_true", default=argparse.SUPPRESS)
                else:
                    glob.add_argument(*a.option_strings, dest=a.dest, type=a.type, action=_StoreOnce,
                                      default=argparse.SUPPRESS)
        extra = glob.parse_args(rest)
        given += vars(extra).pop(_GIVEN, [])
        for k, v in vars(extra).items():
            setattr(args, k, v)
    return parser, args, given


def _once_refusal(given: list[str]) -> tuple[int, dict] | None:
    repeated = [opt for opt in dict.fromkeys(given) if given.count(opt) > 1]  # in order of first appearance
    if repeated:  # FIX3 fix 2: refused before any command runs; neither value is used
        noun = "option" if len(repeated) == 1 else "options"
        return _usage(f"{noun} {', '.join(repeated)} given more than once")
    return None


def _env_value(name: str) -> str | None:
    """An environment value, or None when it is unset, empty or whitespace only (FIX3-II section 1.9, as wf.py:183)."""
    value = os.environ.get(name)
    return value if value is not None and value.strip() else None


def run(argv: list[str]) -> tuple[int, dict]:
    refusal = _undecodable_refusal(argv)
    if refusal is not None:
        return refusal
    parser, args, given = _parse(argv)
    refusal = _once_refusal(given)
    if refusal is not None:
        return refusal
    # FIX3-II section 1.9: the route. --db or --broker, else WF_DB or WF_BROKER; never both of a pair. An empty --db
    # falls back to WF_DB, as before.
    if args.db and args.broker is not None:
        return _usage("give --db or --broker, not both")
    if args.db:
        db_arg = args.db
    elif args.broker is not None:
        return _run_via_broker(parser, args, args.broker)
    else:
        env_db, env_broker = _env_value("WF_DB"), _env_value("WF_BROKER")
        if env_db is not None and env_broker is not None:
            return _usage("WF_DB and WF_BROKER are both set (give --db or --broker)")
        if env_broker is not None:
            return _run_via_broker(parser, args, env_broker)
        db_arg = env_db
    # PACKAGE-FIX1 I3: no implicit default ledger. A command without --db and without WF_DB would otherwise read or
    # write <repo>/ledger/ledger.db silently.
    if not db_arg or not db_arg.strip():
        return _usage("--db required (give --db <path> or set WF_DB)")
    return _dispatch(args, paths.db_path(db_arg))


# --- FIX3-II: the broker route (client side) and the broker's run target ------------------------------------------------

def _subcommands(p: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    return next((a for a in p._actions if isinstance(a, argparse._SubParsersAction)), None)


def _command_parser(parser: argparse.ArgumentParser,
                    args: argparse.Namespace) -> tuple[argparse.ArgumentParser, list[str]]:
    """The parser of the typed command, and its path (`["grant", "activate"]`)."""
    sub = _subcommands(parser).choices[args.cmd]
    path = [args.cmd]
    group = _subcommands(sub)
    if group is not None:
        sub = group.choices[args.sub]
        path.append(args.sub)
    return sub, path


def _file_actions(cmd_parser: argparse.ArgumentParser) -> list[argparse.Action]:
    """The command's file-valued options (section 1.2's FILE_OPTIONS that it takes)."""
    return [a for a in cmd_parser._actions if a.option_strings and a.option_strings[0] in wire.FILE_OPTIONS]


def _input_text(value: str) -> str:
    """Section 1.5: a file the caller names is checked by `_load_json` in the caller's own account, then read once
    more and sent as text; any other value is sent unchanged (not converted), for the broker to parse as inline JSON.
    Either way the 1 MiB input bound is the client's first (section 1.4; WF-199 S1): over it, nothing is sent."""
    if not os.path.isfile(value):
        commands._check_size(len(value.encode("utf-8", "surrogatepass")), "inline JSON")  # _load_json's own measure
        return value
    commands._load_json(value)  # the size bound, strict UTF-8 and strict JSON, with today's texts (commands.py:89-97)
    with open(value, "rb") as fh:
        data = fh.read(commands.MAX_JSON_INPUT_BYTES + 1)
    if len(data) > commands.MAX_JSON_INPUT_BYTES:
        raise WfRejected("malformed", f"{value}: changed while being read: over {commands.MAX_JSON_INPUT_BYTES} bytes")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WfRejected("malformed", f"{value}: changed while being read: not valid UTF-8 ({exc.reason})") from None


def _request_argv(args: argparse.Namespace, cmd_parser: argparse.ArgumentParser, path: list[str],
                  msg_id: str) -> list[str]:
    """Section 1.9's argv: the globals as --opt=value, the command path, the positionals, then the command's options as
    --opt=value (repeatable ones repeated, flags bare). --db, --broker and --json stay with the client, and the
    file-valued options travel in `inputs`."""
    argv = [f"--role={args.role}", f"--session={args.session}", f"--generation={args.generation}", f"--msg-id={msg_id}"]
    if args.oplog:
        argv.append("--oplog")
    argv += path
    options: list[str] = []
    for a in cmd_parser._actions:
        if isinstance(a, argparse._HelpAction):
            continue
        value = getattr(args, a.dest)
        if not a.option_strings:  # a positional
            if value is not None:
                argv.append(str(value))
        elif a.option_strings[0] in wire.FILE_OPTIONS:
            continue
        elif isinstance(a, argparse._StoreTrueAction):
            if value:
                options.append(a.option_strings[0])
        elif isinstance(a, argparse._AppendAction):
            options += [f"{a.option_strings[0]}={v}" for v in value or ()]
        elif value is not None and value != a.default:
            options.append(f"{a.option_strings[0]}={value}")
    return argv + options


def _quiet_parse(argv: list[str]) -> tuple[argparse.ArgumentParser, argparse.Namespace, list[str]] | str:
    """`_parse` with argparse's own output captured: the parse, or argparse's message when it would have exited."""
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            return _parse(argv)
    except SystemExit:
        lines = err.getvalue().strip().splitlines()
        return lines[-1] if lines else "help was requested"


def _run_via_broker(parser: argparse.ArgumentParser, args: argparse.Namespace, address: str) -> tuple[int, dict]:
    """FIX3-II sections 1.2, 1.5, 1.6 and 1.9: build the request and send it. No ledger is opened and no `commands`
    call is made, except `_load_json` on a file the caller names (section 1.5)."""
    try:
        platform = peer.host_platform()
    except OSError as exc:
        return _usage(f"--broker: {exc}")
    if not wire.is_address(address, platform):
        return _usage(f"--broker {address!r} is not a {wire.ADDRESS_FORM[platform]}")
    cmd_parser, path = _command_parser(parser, args)
    msg_id = args.msg_id or uuid.uuid4().hex  # section 1.6: fixed before any byte is sent
    inputs: dict[str, str] = {}
    texts: dict[str, str] = {}
    try:
        for a in _file_actions(cmd_parser):
            inputs[a.option_strings[0]] = texts[a.dest] = _input_text(getattr(args, a.dest))
    except WfError as exc:
        return exc.exit_code, {"ok": False, "code": exc.code, "error": str(exc), **exc.info}
    argv = _request_argv(args, cmd_parser, path, msg_id)
    # Section 1.9 (binding): the broker's parse of argv plus the inputs must give this namespace, less --db, --broker
    # and --json, with msg_id fixed. Checked here, so a value that would not survive the trip is never sent.
    back = _quiet_parse(argv + [f"{opt}={text}" for opt, text in inputs.items()])
    want = {k: v for k, v in vars(args).items() if k not in ("db", "broker", "json")} | {"msg_id": msg_id} | texts
    if isinstance(back, str) or {k: v for k, v in vars(back[1]).items() if k not in ("db", "broker", "json")} != want:
        return EXIT_ERR, wire.rejected("wire", "the command does not parse back unchanged from its request form "
                                               "(a value that reads like an option?); nothing was sent")
    request = {"v": wire.PROTOCOL_VERSION, "op": "run", "argv": argv, "inputs": inputs,
               "wfcore": wire.wfcore_sha256(WF_DIR)}
    return client.call(address, request)


def run_brokered(argv: list[str], inputs: dict[str, str], *, db: str, caller: peer.Caller) -> tuple[int, dict]:
    """FIX3-II section 3.5: the broker's run target. In order: the parse (an error is `rejected(wire)`); section 1.2's
    argv rules (`rejected(wire)`; a repeat is the once rule's usage); section 1.5's inputs checks (`rejected(wire)`);
    a not-brokered command from a worker account (`rejected(not-brokered)`); then the same dispatch as `run`, with
    `caller`. It never raises SystemExit."""
    refusal = _undecodable_refusal(argv)
    if refusal is not None:
        return refusal
    refs = wire.file_option_refs(argv)
    if refs:  # AMENDMENT-FIX3-II-A1 item 2: a file option's text travels only in `inputs`
        return EXIT_ERR, wire.rejected("wire", f"argv names file option(s) {', '.join(refs)}; their text goes in inputs")
    parsed = _quiet_parse(argv + [f"{opt}={text}" for opt, text in inputs.items()])
    if isinstance(parsed, str):
        return EXIT_ERR, wire.rejected("wire", f"argv does not parse: {parsed}")
    parser, args, given = parsed
    refusal = _once_refusal(given)
    if refusal is not None:
        return refusal
    kept = [opt for opt, kept_ in (("--db", args.db is not None), ("--broker", args.broker is not None),
                                   ("--json", args.json)) if kept_]
    if kept:  # section 1.2: the client keeps these
        return EXIT_ERR, wire.rejected("wire", f"argv carries {', '.join(kept)}, which the client keeps")
    missing = [opt for opt in ("--role", "--session", "--generation", "--msg-id") if opt not in given]
    if missing:  # section 1.2: never a parser default for a value the client left out
        return EXIT_ERR, wire.rejected("wire", f"argv lacks {', '.join(missing)}")
    cmd_parser, path = _command_parser(parser, args)
    takes = {a.option_strings[0]: a.dest for a in _file_actions(cmd_parser)}
    if set(inputs) != set(takes):
        return EXIT_ERR, wire.rejected("wire", f"inputs hold {', '.join(sorted(inputs)) or 'none'}; the command takes "
                                               f"{', '.join(sorted(takes)) or 'none'}")
    for opt, dest in takes.items():
        if getattr(args, dest) != inputs[opt]:
            return EXIT_ERR, wire.rejected("wire", f"the parsed {opt} differs from its input")
    command = " ".join(path)
    if not caller.own and (command in _NOT_BROKERED or (command == "verify" and (
            args.restored is not None or args.gate_file is not None or args.gate_sha is not None))):
        return EXIT_ERR, wire.rejected("not-brokered", f"{command} is not served to a worker account; the owner "
                                                       f"account runs it", command=command)
    return _dispatch(args, db, caller)


def _whoami(args: argparse.Namespace, db: str, caller: peer.Caller | None) -> dict:
    """FIX3-II section 4.5. Reads only; with a job, `commands.whoami_job`'s keys for the account."""
    if caller is not None:
        account = caller.peer.account
        res = {"ok": True, "mode": "broker", "account": account, "own": caller.own,
               "broker": {"account": caller.broker_account, "wfcore": caller.broker_wfcore}}
    else:
        try:
            account = peer.own_account()
        except OSError as exc:
            raise WfRefused(f"cannot read this process's account: {exc}") from exc
        res = {"ok": True, "mode": "direct", "account": account, "note": "direct mode: no account check"}
    if args.job is not None:
        res.update(commands.whoami_job(db, args.job, account))
    return res


def _dispatch(args: argparse.Namespace, db: str, caller: peer.Caller | None = None) -> tuple[int, dict]:
    """Run the parsed command on `db`. Direct mode (`caller` None) calls `commands` exactly as at ff3cd2f
    (FIX3-II section 3.3, S1): `caller` joins `common` only when set, and `bind_account` only when given."""
    oplog = OpLog()
    msg_id = args.msg_id or uuid.uuid4().hex
    common = dict(msg_id=msg_id, role=args.role, session=args.session, generation=args.generation, oplog=oplog)
    if caller is not None:
        common["caller"] = caller
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
            account = {} if args.bind_account is None else {"bind_account": args.bind_account}  # section 4.2 (S1)
            res = commands.bind_cmd(db, args.job, bind_role=args.bind_role, bind_session=args.bind_session,
                                    bind_generation=args.bind_generation, tools=args.tool, paths_=args.path,
                                    faults=faults, **common, **account)
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
        elif args.cmd == "whoami":
            res = _whoami(args, db, caller)
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
            # D5 S1 (WF-242): `resolve` is not brokered for a worker account (_NOT_BROKERED), so a brokered worker's
            # call does not run it either; the result keeps its `action` line, for the owner's account to run.
            brokered_worker = caller is not None and not caller.own
            if injected:
                out["auto_resolve"] = {"outcome": "SKIPPED", "reason": "injected commit-error fault"}
            elif not brokered_worker and out.get("job") and out.get("seq") and out.get("attempt_id"):
                try:
                    out["auto_resolve"] = commands.resolve(db, out["job"], int(out["seq"]), out["attempt_id"], None, OpLog())
                except WfError as rexc:
                    out["auto_resolve"] = {"outcome": "ERROR", "error": str(rexc)}
                except Exception as rexc:  # noqa: BLE001 -- FIX2 #21: recorded beside the uncertain result, never replaces it
                    out["auto_resolve"] = {"outcome": "ERROR", "error": f"{type(rexc).__name__}: {rexc}"}
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
            if "mode" in res:  # FIX3-II section 3.5 and AMENDMENT A3: whoami's keys, for a whoami result only
                keys += [k for k in ("mode", "account", "own", "broker", "map_in_force", "roles", "sessions", "note")
                         if k in res]
            sys.stdout.write(" ".join(f"{k}={json.dumps(res[k], default=str)}" for k in keys) + "\n")
        else:
            sys.stdout.write(f"{res.get('code', 'error')}: {res.get('error', res.get('outcome'))}\n")
            if "action" in res:
                sys.stdout.write(res["action"] + "\n")
        if res.get("truncated") is True:  # FIX3-II section 1.4, WF-232 S1: a cut broker result is never shown as whole
            sys.stdout.write("truncated: the broker cut this result to its key fields; it was over the response bound "
                             "(section 1.4)\n")
    sys.stdout.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
