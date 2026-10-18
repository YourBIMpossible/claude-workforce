"""Workforce PreToolUse gate hook: single file (B2 slice 4a, lane S4L; INTERFACE-CONTRACT-S4A section 4 and
amendment S4A-A1).

Deployed by copying this file, with `wf_gate.json` beside it, into a hooks directory outside the checkout. It runs
as `python -I <hooks dir>\\wf_gate_hook.py`: it does not depend on PYTHONPATH, the environment or the cwd. Steps:
1. read its config from its own directory (missing or invalid: deny `gate-config`);
2. check its own file hash against `hook_sha256` and the `wfcore` tree against `wfcore_sha256` (mismatch: deny
   `gate-integrity`; a modified hook or library never decides);
3. apply the guarded probe switch when `probe_fault` is configured (S4A-A1; otherwise exactly as section 4);
4. put `wf_dir` first on sys.path and import `wfcore.gatecore` and `wfcore.gatelog`;
5. read the payload from stdin, decide, record (section 3 write-failure rule), and answer in Claude Code's
   hook-output schema (AMENDMENT S4A-A6): stdout is exactly one JSON line, `{"reason": "<wf reason>"}` on allow or
   `{"decision": "block", "reason": "<wf reason>"}` on deny. Exit 0 = allow, 2 = deny; the exit code comes from the
   internal decision, never from stdout. stdout never carries `"approve"`, `"allow"`, `hookSpecificOutput` /
   `permissionDecision` or any other key: the gate only declines to object, so Claude Code's own permission prompt
   still applies. The wf record `{"decision", "reason", "channel", "logged"}` (plus `limitation` for the shell
   channel, `probe_fault` when a probe is active) is the last stderr line, `wf gate: record <json>`; on deny the
   first stderr line is `wf gate: deny <reason>`.

Denials before the decision core runs (`gate-config`, `gate-integrity`) are not written to the gate log: the
library that writes it is either unknown or unverified at that point. Their record carries `"logged": false`.
The S4A-A1 forced-error probe raises on purpose and so writes no stdout line and no record (its outcome belongs to
Claude Code's `onFailure`).

This file never registers itself, edits settings, or writes anywhere except through `wfcore.gatelog`.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from typing import Any, TextIO

CONFIG_NAME = "wf_gate.json"
EXIT_ALLOW, EXIT_DENY = 0, 2
PREVENTABLE_TOOLS = ("Write", "Edit", "NotebookEdit")
SHELL_TOOLS = ("Bash", "PowerShell")
PROBE_FAULTS = ("error", "timeout")
PROBE_SLEEP_DEFAULT_S = 30.0
_REQUIRED = ("root", "db", "wf_dir", "wfcore_sha256", "read_timeout_s", "hook_sha256")


class _Deny(Exception):
    """A decision this file makes itself, before or around the decision core."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def wfcore_sha256(wf_dir: str) -> str:
    """SHA-256 over the `wf_dir/wfcore/*.py` tree: for each file in sorted file-name order, the line
    `"<sha256>  <file name>\\n"`, concatenated and hashed as UTF-8 (the same text `wf gate print-install` uses)."""
    core = os.path.join(wf_dir, "wfcore")
    lines = [f"{_sha256_file(os.path.join(core, name))}  {name}\n"
             for name in sorted(os.listdir(core)) if name.endswith(".py")]
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def _channel(payload: Any) -> str:
    tool = payload.get("tool_name") if isinstance(payload, dict) else None
    return "preventable" if tool in PREVENTABLE_TOOLS else "shell" if tool in SHELL_TOOLS else "other"


def _is_hex64(v: Any) -> bool:
    return isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v)


def _is_pos_number(v: Any) -> bool:
    return not isinstance(v, bool) and isinstance(v, (int, float)) and v == v and 0 < v < float("inf")


def _load_config(here: str) -> dict:
    path = os.path.join(here, CONFIG_NAME)
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except FileNotFoundError:
        raise _Deny("gate-config", f"config not found: {path}") from None
    except (OSError, ValueError) as exc:
        raise _Deny("gate-config", f"config unreadable: {type(exc).__name__}: {exc}") from None
    if not isinstance(cfg, dict):
        raise _Deny("gate-config", "config must be a JSON object")
    missing = [k for k in _REQUIRED if k not in cfg]
    if missing:
        raise _Deny("gate-config", f"config missing {missing}")
    for key in ("root", "db", "wf_dir"):
        if not isinstance(cfg[key], str) or not os.path.isabs(cfg[key]):
            raise _Deny("gate-config", f"config {key} must be an absolute path")
    for key in ("wfcore_sha256", "hook_sha256"):
        if not _is_hex64(cfg[key]):
            raise _Deny("gate-config", f"config {key} must be 64 lowercase hex characters")
    if not _is_pos_number(cfg["read_timeout_s"]):
        raise _Deny("gate-config", "config read_timeout_s must be a number above 0")
    return cfg


def _check_integrity(hook_file: str, cfg: dict) -> None:
    try:
        own = _sha256_file(hook_file)
    except OSError as exc:
        raise _Deny("gate-integrity", f"hook file unreadable: {exc}") from None
    if own != cfg["hook_sha256"]:
        raise _Deny("gate-integrity", "hook file hash differs from hook_sha256")
    try:
        core = wfcore_sha256(cfg["wf_dir"])
    except OSError as exc:
        raise _Deny("gate-integrity", f"wfcore tree unreadable: {exc}") from None
    if core != cfg["wfcore_sha256"]:
        raise _Deny("gate-integrity", "wfcore tree hash differs from wfcore_sha256")


def _inside(path: str, parent: str) -> bool:
    p = os.path.normcase(os.path.realpath(path))
    q = os.path.normcase(os.path.realpath(parent)).rstrip("\\/") + os.sep
    return p.startswith(q) or p == q.rstrip(os.sep)


def _probe_config(cfg: dict, payload: Any) -> str | None:
    """S4A-A1, the checks that need no decision core: the active probe value, or None when the config has no
    `probe_fault`. A probe that is present but not honoured (unknown value, no absolute `probe_root`, bad
    `probe_sleep_s`, payload cwd outside `probe_root`) is a `gate-config` deny, never ignored. Runs after the
    self-hash check has passed."""
    if "probe_fault" not in cfg:
        return None
    value = cfg["probe_fault"]
    if value not in PROBE_FAULTS:
        raise _Deny("gate-config", f"unknown probe_fault value {value!r}")
    root = cfg.get("probe_root")
    if not isinstance(root, str) or not os.path.isabs(root):
        raise _Deny("gate-config", "probe_fault requires an absolute probe_root")
    if "probe_sleep_s" in cfg and not _is_pos_number(cfg["probe_sleep_s"]):
        raise _Deny("gate-config", "probe_fault: probe_sleep_s must be a number above 0")
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    if not isinstance(cwd, str) or not os.path.isabs(cwd) or not _inside(cwd, root):
        raise _Deny("gate-config", "probe_fault: payload cwd is not inside probe_root")
    return value


def _probe_targets(cfg: dict, payload: Any, gatecore: Any) -> None:
    """S4A-A1 (b), second half: every target path resolves inside `probe_root`, else `gate-config`."""
    targets, problem = gatecore.target_paths(payload, cfg["root"])
    if problem is not None:
        raise _Deny("gate-config", f"probe_fault: target paths unresolvable ({problem})")
    for rel in targets:
        if not _inside(os.path.join(cfg["root"], rel), cfg["probe_root"]):
            raise _Deny("gate-config", "probe_fault: a target path is not inside probe_root")


def _emit(stdout: TextIO, stderr: TextIO, record: dict, stderr_lines: list[str]) -> int:
    """AMENDMENT S4A-A6: stdout in Claude Code's hook-output schema, the wf record as the last stderr line, and the
    exit code from the internal decision. Only "block" is ever written as a top-level `decision`."""
    allow = record["decision"] == "allow"
    answer = {"reason": record["reason"]} if allow else {"decision": "block", "reason": record["reason"]}
    stdout.write(json.dumps(answer, sort_keys=True) + "\n")
    stdout.flush()
    for line in [*stderr_lines, "wf gate: record " + json.dumps(record, sort_keys=True)]:
        stderr.write(line.encode("ascii", "backslashreplace").decode("ascii") + "\n")
    stderr.flush()
    return EXIT_ALLOW if allow else EXIT_DENY


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main(argv: list[str] | None = None, stdin: TextIO | None = None) -> int:
    """Run the hook once. `argv` is accepted for the frozen signature and ignored (the hook takes no options);
    `stdin` defaults to the process's standard input, read as UTF-8 bytes."""
    del argv
    stdout, stderr = sys.stdout, sys.stderr
    if stdin is None:
        raw = sys.stdin.buffer.read()
    else:
        text = stdin.read()
        raw = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    try:
        payload: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        payload = None
    channel = _channel(payload)

    hook_file = os.path.abspath(__file__)
    here = os.path.dirname(hook_file)
    try:
        cfg = _load_config(here)
        _check_integrity(hook_file, cfg)
        probe = _probe_config(cfg, payload)
        # Import from source only: the verified hash covers wfcore/*.py, not __pycache__, so no cached bytecode is
        # read (the prefix directory never exists) and none is written.
        no_bytecode = os.path.join(here, ".wf-gate-no-bytecode")
        if os.path.lexists(no_bytecode):
            raise _Deny("gate-integrity", f"unexpected bytecode directory beside the hook: {no_bytecode}")
        sys.dont_write_bytecode = True
        sys.pycache_prefix = no_bytecode
        if cfg["wf_dir"] not in sys.path:
            sys.path.insert(0, cfg["wf_dir"])
        try:
            from wfcore import gatecore, gatelog  # noqa: PLC0415 - imported only after the integrity check
        except ImportError as exc:
            raise _Deny("gate-config", f"wfcore gate modules not importable from wf_dir: {exc}") from None
        if probe is not None:
            _probe_targets(cfg, payload, gatecore)
    except _Deny as d:
        return _emit(stdout, stderr, {"decision": "deny", "reason": d.reason, "channel": channel, "logged": False},
                     [f"wf gate: deny {d.reason}: {d.detail}", "wf gate: this denial is not in the gate log"])

    if probe == "error":
        raise RuntimeError("wf gate probe: forced error")
    if probe == "timeout":
        time.sleep(float(cfg.get("probe_sleep_s", PROBE_SLEEP_DEFAULT_S)))

    now = _now()
    if not isinstance(payload, dict):
        decision: dict = {"decision": "deny", "reason": "malformed-payload", "channel": channel, "session": None,
                          "tool_name": None, "jobs": [], "paths": [], "command_sha256": None, "command_length": None,
                          "limitation": None}
    else:
        try:
            decision = dict(gatecore.decide(payload, root=cfg["root"], db_path=cfg["db"], now=now,
                                            gate_files=[hook_file, os.path.join(here, CONFIG_NAME)],
                                            read_timeout_s=float(cfg["read_timeout_s"])))
        except Exception as exc:  # noqa: BLE001 - decide fails closed itself; this is the last guard
            decision = {"decision": "deny", "reason": "gate-error", "channel": channel,
                        "session": payload.get("session_id"), "tool_name": payload.get("tool_name"), "jobs": [],
                        "paths": [], "command_sha256": None, "command_length": None, "limitation": None,
                        "error": f"{type(exc).__name__}: {exc}"}
        if decision.get("decision") not in ("allow", "deny") or not isinstance(decision.get("reason"), str)                 or not decision["reason"]:
            decision = {"decision": "deny", "reason": "gate-error", "channel": channel,
                        "session": payload.get("session_id"), "tool_name": payload.get("tool_name"), "jobs": [],
                        "paths": [], "command_sha256": None, "command_length": None, "limitation": None,
                        "error": "decision core returned an unexpected shape"}
    decision["channel"] = decision.get("channel") or channel
    if probe is not None:
        decision["probe_fault"] = probe

    lines: list[str] = []
    logged = True
    try:
        gatelog.record(cfg["root"], decision, payload_sha256=hashlib.sha256(raw).hexdigest(), ts=now)
    except Exception as exc:  # noqa: BLE001 - section 3 write-failure rule applies to any recording failure
        logged = False
        if decision["decision"] == "allow" and decision["channel"] == "preventable":
            decision = dict(decision, decision="deny", reason="audit-unavailable")
            lines.append("wf gate: gate log write failed; a preventable allow never proceeds unrecorded")
        elif decision["decision"] == "allow":
            lines.append("wf gate: gate log write failed; this channel is not preventable, so the attempt proceeds "
                         "unrecorded and wf verify / wf digest will show 'gate log incomplete'")
        else:
            lines.append("wf gate: gate log write failed; the deny stands")
        lines.append(f"wf gate: {type(exc).__name__}: {exc}")
        if not gatelog.record_failure(cfg["root"], decision, exc, ts=now):
            lines.append("wf gate: the audit-failures file could not be written either")

    out = {"decision": decision["decision"], "reason": decision["reason"], "channel": decision["channel"],
           "logged": logged}
    if decision.get("limitation"):
        out["limitation"] = decision["limitation"]
        lines.append(f"wf gate: {decision['limitation']}")
    if probe is not None:
        out["probe_fault"] = probe
    if out["decision"] == "deny":
        lines.insert(0, f"wf gate: deny {out['reason']}")
    return _emit(stdout, stderr, out, lines)


if __name__ == "__main__":
    sys.exit(main())
