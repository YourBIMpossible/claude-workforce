"""The broker wire (PACKAGE-FIX3-II, lane F3-BRK; INTERFACE-CONTRACT-FIX3-II r2 sections 1.1-1.4, 1.7 and 1.10).

One request and one response per connection. A frame is a 4-byte unsigned big-endian length N, then N bytes of UTF-8
holding one JSON object. Both sides decode strictly: invalid UTF-8, a top level that is not an object, a duplicate key
at any depth, NaN or Infinity, a lone surrogate, a missing or unknown top-level field, or a field of the wrong type is
refused (section 1.1), so neither side ever acts on a request or response it had to guess at.

The bounds are module constants; no environment variable or option changes them, and in-process tests may patch them
(section 1.4).

Imports (section 6.1): stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import struct
from typing import Any

PROTOCOL_VERSION = 1
HEADER = struct.Struct(">I")
MAX_REQUEST_BYTES = 3 * 1024 * 1024    # section 1.4 and section 8 D3: a 1 MiB input can take about 2 MiB once escaped
MAX_RESPONSE_BYTES = 8 * 1024 * 1024   # section 1.4: past it, `result` is cut to TRUNCATED_KEYS and `truncated` set

#: Section 1.2: the file-valued options. The client sends their text in `inputs`, never as argv options.
FILE_OPTIONS = ("--content", "--contract", "--grant", "--results", "--exceptions")
#: Section 1.4: the keys a truncated `result` keeps (SII:156).
TRUNCATED_KEYS = ("ok", "code", "outcome", "job", "seq", "self", "msg_id")
OPS = ("run", "stop")
EXIT_CODES = range(0, 5)

REQUEST_FIELDS = frozenset({"v", "op", "argv", "inputs", "wfcore"})
RESPONSE_FIELDS = frozenset({"v", "exit", "result", "peer", "broker", "truncated"})

_HEX64 = re.compile(r"[0-9a-f]{64}")
_PIPE = re.compile(r"\\\\\.\\pipe\\[A-Za-z0-9._-]{1,200}")
SUN_PATH_MAX = 107  # sun_path holds 108 bytes, one of them the terminating NUL
#: Section 1.10, for messages: what an address of each platform looks like.
ADDRESS_FORM = {"win32": "named-pipe address (\\\\.\\pipe\\ and 1-200 of A-Z a-z 0-9 . _ -)",
                "linux": "socket path (absolute, at most 107 bytes of UTF-8)"}

#: The broker log's `cmd`: the command paths of `wf._parser()` (a test pins this to the parser). A command path that is
#: not one of them is logged as null, so the log never holds a client's argv text (section 3.5).
COMMANDS = frozenset({
    "job new", "job abandon", "grant propose", "grant activate", "grant revoke", "grant expire", "append", "status",
    "digest", "checkpoint", "verify", "backup", "export", "accept", "close", "resolve", "bind", "reconcile",
    "ack-reconcile", "grant-check", "cancel", "log-intervention", "gate print-install", "observe", "recover begin",
    "recover reconcile", "recover close", "recover decide", "whoami",
})
_GROUPS = frozenset(c.split(" ", 1)[0] for c in COMMANDS if " " in c)
#: validate.py's job-id rule (a test pins the pattern), so the log's `job` is an id, never other argv text.
_JOB = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


class WireError(ValueError):
    """A frame or a JSON body that breaks section 1.1 or 1.2/1.3; the text says which rule."""


class FrameTooLarge(WireError):
    """A frame header over the bound: the body is not read (section 1.8 step 1)."""


# --- encoding -------------------------------------------------------------------------------------------------------

def frame(body: bytes) -> bytes:
    return HEADER.pack(len(body)) + body


def encode_request(obj: dict) -> bytes:
    """Section 1.1: the request body (not framed), compact, non-ASCII kept, strict UTF-8."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def encode_response(obj: dict) -> bytes:
    """Section 1.1: the response body (not framed), ASCII, sorted, `default=str` as `main()` prints."""
    return json.dumps(obj, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str,
                      allow_nan=False).encode("ascii")


def response(exit_code: int, result: dict, *, peer_account: str | None, broker_account: str,
             broker_wfcore: str) -> tuple[bytes, int, bool]:
    """The framed response for `result`, the exit code it carries, and whether it was truncated. Over
    MAX_RESPONSE_BYTES, `result` is cut to the TRUNCATED_KEYS it holds and `truncated` is true (section 1.4). A result
    that cannot be encoded at all (a non-finite number) is answered as the internal error it is, exit 4, so a response
    is always formed."""
    def build(res: dict, truncated: bool) -> bytes:
        return encode_response({"v": PROTOCOL_VERSION, "exit": exit_code, "result": res,
                                "peer": None if peer_account is None else {"account": peer_account},
                                "broker": {"account": broker_account, "wfcore": broker_wfcore},
                                "truncated": truncated})
    try:
        body = build(result, False)
    except ValueError as exc:  # allow_nan=False: a NaN or Infinity in the result
        exit_code = 4
        result = {"ok": False, "code": "internal-error", "error": f"ValueError: response not encodable: {exc}"}
        body = build(result, False)
    if len(body) <= MAX_RESPONSE_BYTES:
        return frame(body), exit_code, False
    return frame(build({k: result[k] for k in TRUNCATED_KEYS if k in result}, True)), exit_code, True


# --- strict decoding --------------------------------------------------------------------------------------------------

def _no_constant(name: str) -> Any:
    raise WireError(f"{name} is not JSON")


def _pairs(pairs: list[tuple[str, Any]]) -> dict:
    out: dict = {}
    for k, v in pairs:
        if k in out:
            raise WireError(f"duplicate key {k!r}")
        out[k] = v
    return out


def _check_text(obj: Any) -> None:
    """A lone surrogate (from a `\\ud800` escape) in any key or string, and a float that overflowed to infinity.
    Iterative, so a deeply nested body is checked in full and never raises RecursionError (WF-199 N1)."""
    stack = [obj]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError:
                raise WireError("a string holds a lone surrogate") from None
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise WireError("a number is not finite")
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)


def decode(body: bytes) -> dict:
    """Section 1.1: one JSON object from strict UTF-8, with no duplicate key, no NaN/Infinity and no lone surrogate."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WireError(f"not valid UTF-8: {exc.reason} at byte {exc.start}") from None
    try:
        obj = json.loads(text, object_pairs_hook=_pairs, parse_constant=_no_constant)
    except WireError:
        raise
    except ValueError as exc:  # JSONDecodeError
        raise WireError(f"not JSON: {exc}") from None
    except RecursionError:
        raise WireError("not JSON: nested too deeply") from None
    if not isinstance(obj, dict):
        raise WireError(f"the top level is a JSON {type(obj).__name__}, not an object")
    _check_text(obj)
    return obj


def _fields(obj: dict, want: frozenset, what: str) -> None:
    missing = sorted(want - obj.keys())
    unknown = sorted(obj.keys() - want)
    if missing or unknown:
        parts = ([f"missing {', '.join(missing)}"] if missing else []) + ([f"unknown {', '.join(unknown)}"] if unknown else [])
        raise WireError(f"{what} fields: {'; '.join(parts)}")


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def is_hex64(v: Any) -> bool:
    return isinstance(v, str) and _HEX64.fullmatch(v) is not None


def check_request(obj: dict) -> dict:
    """Section 1.2, the shape only: the five fields, their types, `v` exactly 1 (section 1.7), and an empty `argv` and
    `inputs` for a stop. What `argv` must hold is checked by `run_brokered` from its parse (section 3.5)."""
    _fields(obj, REQUEST_FIELDS, "request")
    if not _is_int(obj["v"]):
        raise WireError("v must be an integer")
    if obj["v"] != PROTOCOL_VERSION:
        raise WireError(f"v {obj['v']} is not {PROTOCOL_VERSION}")
    if obj["op"] not in OPS:
        raise WireError(f"op must be one of {', '.join(OPS)}")
    argv, inputs = obj["argv"], obj["inputs"]
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise WireError("argv must be an array of strings")
    if not isinstance(inputs, dict) or not all(isinstance(v, str) for v in inputs.values()):
        raise WireError("inputs must be an object of strings")
    unknown = sorted(set(inputs) - set(FILE_OPTIONS))
    if unknown:
        raise WireError(f"inputs keys must be file options ({', '.join(FILE_OPTIONS)}), not {', '.join(unknown)}")
    if not is_hex64(obj["wfcore"]):
        raise WireError("wfcore must be 64 lower-case hex characters")
    if obj["op"] == "stop" and (argv or inputs):
        raise WireError("a stop request carries an empty argv and empty inputs")
    return obj


def check_response(obj: dict) -> dict:
    """Section 1.3: the six fields and their types."""
    _fields(obj, RESPONSE_FIELDS, "response")
    if obj["v"] != PROTOCOL_VERSION or not _is_int(obj["v"]):
        raise WireError(f"v must be {PROTOCOL_VERSION}")
    if not _is_int(obj["exit"]) or obj["exit"] not in EXIT_CODES:
        raise WireError("exit must be an integer from 0 to 4")
    if not isinstance(obj["result"], dict):
        raise WireError("result must be an object")
    peer = obj["peer"]
    if peer is not None and (not isinstance(peer, dict) or set(peer) != {"account"}
                             or not isinstance(peer["account"], str)):
        raise WireError("peer must be null or {\"account\": <string>}")
    broker = obj["broker"]
    if (not isinstance(broker, dict) or set(broker) != {"account", "wfcore"} or not isinstance(broker["account"], str)
            or not is_hex64(broker["wfcore"])):
        raise WireError("broker must be {\"account\": <string>, \"wfcore\": <64 hex>}")
    if not isinstance(obj["truncated"], bool):
        raise WireError("truncated must be a boolean")
    return obj


def read_frame(channel: Any, deadline: float, limit: int) -> bytes:
    """One frame's body from `channel`. A header over `limit` raises FrameTooLarge before the body is read; the
    channel's EOFError, TimeoutError and OSError pass through."""
    (n,) = HEADER.unpack(channel.recv_exact(HEADER.size, deadline))
    if n > limit:
        raise FrameTooLarge(f"frame of {n} bytes is over the {limit}-byte bound")
    return channel.recv_exact(n, deadline) if n else b""


# --- refusals, versions, addresses, the log's command ------------------------------------------------------------------

def rejected(name: str, text: str, **info: Any) -> dict:
    """Section 5: the WfRejected body, `rejected(<name>): <text>` plus info keys."""
    return {"ok": False, "code": "rejected", "error": f"rejected({name}): {text}", **info}


def wfcore_sha256(wf_dir: str) -> str:
    """Section 1.7: the gate's recipe (wf_gate_hook.py:62-68): for each `wf_dir/wfcore/*.py` in sorted file-name order,
    the line `"<sha256 of the file>  <name>\\n"`; the lines concatenated, encoded as UTF-8 and hashed."""
    core = os.path.join(wf_dir, "wfcore")
    lines = []
    for name in sorted(os.listdir(core)):
        if name.endswith(".py"):
            h = hashlib.sha256()
            with open(os.path.join(core, name), "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 16), b""):
                    h.update(chunk)
            lines.append(f"{h.hexdigest()}  {name}\n")
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def is_address(address: Any, platform: str) -> bool:
    """Section 1.10: a named pipe `\\\\.\\pipe\\` plus 1-200 of [A-Za-z0-9._-] on win32; an absolute path of at most
    107 bytes of UTF-8 on linux."""
    if not isinstance(address, str):
        return False
    if platform == "win32":
        return _PIPE.fullmatch(address) is not None
    if platform == "linux":
        try:
            size = len(address.encode("utf-8"))
        except UnicodeEncodeError:
            return False
        return address.startswith("/") and "\0" not in address and 1 < size <= SUN_PATH_MAX
    return False


def file_option_refs(argv: list[str]) -> list[str]:
    """A1 item 2: the argv tokens that name a file option, in full or abbreviated (a subcommand's parser takes an
    unambiguous prefix of its option), in order. Their text belongs in `inputs`; `run_brokered` refuses them."""
    refs = []
    for a in argv:
        name = a.split("=", 1)[0]
        if len(name) > 2 and name.startswith("--") and any(opt.startswith(name) for opt in FILE_OPTIONS):
            refs.append(name)
    return refs


def describe(argv: list[str]) -> tuple[str | None, str | None]:
    """The broker log's `cmd` and `job` from a request argv in section 1.9's form (globals, the command path, the
    positionals, the options). Each is null unless it is a known command path or a job id, so no other text is
    logged."""
    i = 0
    while i < len(argv) and argv[i].startswith("-"):  # the globals, each `--opt=value` or a bare flag
        i += 1
    if i == len(argv):
        return None, None
    cmd, i = argv[i], i + 1
    if cmd in _GROUPS:
        if i == len(argv):
            return None, None
        cmd, i = f"{cmd} {argv[i]}", i + 1
    if cmd not in COMMANDS:
        return None, None
    job = argv[i] if i < len(argv) and not argv[i].startswith("-") and _JOB.match(argv[i]) else None
    return cmd, job
