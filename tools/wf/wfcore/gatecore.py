"""Gate decision core (B2 slice 4a, lane S4G; spec FIRST-RELEASE-SPEC.md Rev 5.3 §4.2 steps 1-5, §4.3, §8 T13, T15,
T17, T18, T25). Governing text: INTERFACE-CONTRACT-S4A.md §1 (payload), §2 (rules 0-8), §6 (signatures), §8
(decisions), frozen in the WorkForce collab folder `contracts/b2s4a` (SHA256SUMS verified). Contracts v1, S2 and S3
stay in force.

`decide(payload, ...)` answers one Claude Code PreToolUse payload with allow / deny and a reason. It is a pure
read-only function over the ledger: the ledger is opened with a read-only URI inside one read transaction and is never
written; nothing else on disk is written. It reuses the slice 1-3 decision logic unchanged - `grant.authority`,
`grant.check`, `bind.active_roles`, `reconcile.recovery_open`, `db.verify_chain` - and adds no second authority logic.
Any exception inside `decide` becomes `deny` / `gate-error` (fail closed).

Rules, first match decides (§2): 0 payload shape (and unresolvable target) -> 1 protected paths (preventable
channel, everyone; a configured gate file by identity, FIX2 #4) -> 2 ledger health (unreadable, locked, identity, chain
tail over the last 50 rows of every touched job; D-S4-4) -> 3 job resolution -> 4 per-job state for every touched job
(recovery-open, cancelled, authority-<state>; `none`/`proposed` deny only for a job the session is bound to, D-S4-1;
then, on the preventable channel only, job-closed, AMENDMENT S4A-A6) -> 5 preventable: a session with no current
binding but a replaced or stopped one (stale-session / not-current-binding, any target, T13), then for a bound
session `grant.check(action="write")` -> 6 preventable, unbound session into a
job's write scope or `jobs/<job>/` (unbound-session) -> 7 shell channel: allow `shell-not-preventable` with
SHELL_LIMITATION; the command is never parsed, only its sha256 and length are kept (D-S4-2) -> 8 a target outside
the boundary root: `deny` `out-of-scope` for a session bound to a job (T25 allow-list, AMENDMENT S4A-A4), else
`allow` `outside-boundary`; everything else `allow` `not-mediated` (other tools, D-S4-5, and in-root paths that
belong to no job).

Reading choices recorded for review (no signature change):
* Rule 2 runs for every preventable and shell decision, including a preventable target outside the boundary (strict
  first-match order, fail closed); a healthy ledger then lets such a target reach rule 4 and rule 8.
* Rule 4 by binding applies to the shell channel and to every preventable decision of a bound session, including
  a target outside the boundary (S4A-A4). A write outside the boundary by an unbound session touches no job (not a
  Workforce path; no claim about that work). A bound session may write only where its grant allows, and a grant
  names boundary-relative paths only, so a bound session's target outside the boundary is `out-of-scope` (T25).
* Rule 5 checks, for an in-boundary target, the jobs the session is bound to whose scope or `jobs/<job>/` tree holds
  the target (or all of its bound jobs when none does), and allows when `grant.check` allows for one of them.
* `stale-session` = the session held an earlier binding of a role whose current binding is another session or a
  higher generation (T13, replaced); `not-current-binding` = the session is the current binding's session but that
  binding is stop-confirmed. Neither session counts as bound for rules 4 and 7. A session that is bound to no job
  and holds such a binding on any job is denied every preventable target - its old job's paths, in-root paths in no
  job and targets outside the boundary alike (T13 reads "attempts a hooked write", not boundary-limited); `stale-session`
  wins when it holds both kinds. Those jobs are touched, so rules 2 and 4 apply to them first. A session bound to
  some job is decided by rules 5/8 as before, whatever it holds elsewhere.
* A drive mismatch, a `..` that leaves the root, a link / reparse-point component inside the root, or a pathpolicy
  problem (device name, stream syntax, trailing dot) is `unresolvable-path`.
* Rule 1 gate files (FIX2 #4): a target is a gate file when its normalised absolute path, its normcased `realpath` or
  its file identity equals a configured gate file's. The identity is (st_dev, st_ino) of the deepest existing ancestor
  by `os.stat`, which follows symlinks, junctions, 8.3 short names, the extended-length prefix and UNC admin-share
  spellings to the one file, plus the normcased names below it. So an alias of a gate file not yet written is caught
  through an aliased directory, and a hard link to a gate file is caught too. An identity that cannot be resolved (an
  OS error other than not-found, on the target or on a gate file) is treated as the gate file: `protected-path`.
* Rule 1 does no network I/O (WF-106 review F1). A gate file is a local file, so a UNC spelling whose host is not this
  machine, and a device path other than a drive form, is never stat'ed or realpath'ed: rule 1 passes it and rule 0
  decides it as before (`unresolvable-path` across anchors). An admin share of this machine (`X$` on localhost,
  127.0.0.1, ::1 or the host name, any case; no DNS) is mapped lexically to its drive and then resolved locally.
* Rule 1 resolves only where rule 0 would let a target through (WF-106 review F2, AMENDMENT FIX2-A9 section 2): after
  that mapping, a target whose drive or anchor is not the boundary root's (another drive letter, a mapped network
  drive, a subst drive, the device form of another drive) is compared by spelling only, with no I/O, and rule 0
  denies it `unresolvable-path`. The extended-length and device forms of a drive count as that drive, and the root
  goes through the same mapping (a root spelled as this machine's admin share is on that drive).
* Rule 1 walks a target on the root's anchor link by link before resolving it (FIX2 batch Z, Z6; AMENDMENT FIX2-A17
  as corrected by A18, A19 and A20; spec :194). Each component is `lstat`ed; a link's target is read with
  `readlink`, never followed. A remote target (a UNC host that is not this machine by batch S's names, in any
  spelling, including the GLOBALROOT Mup device path) ends the walk with no I/O under the link, and rule 1 fails
  closed (A20), unless rule 0 refuses the target anyway because its first link lies under the root. When the walk
  meets no remote target, rule 1 resolves the target as before (`realpath`, identity), so no decision is weaker than
  before. Local targets are followed: drive paths, extended-length and volume device forms, and this machine's
  shares (admin shares mapped to their drive). A drive letter mapped to a network share is followed too, a known
  limit (A19). A chain longer than `_MAX_LINK_HOPS` is a loop, and a target with a doubled device prefix cannot be
  traversed: both are unresolvable, so treated as the gate file (fail closed).
* `job-closed` (AMENDMENT S4A-A6) is checked once recovery-open, cancelled and authority-<state> have been checked for
  every touched job, so those states win on any touched job, not only on an earlier-sorted one. It applies to every
  session that touches the closed job: the bound session (whatever its target, as authority-<state> does), an unbound
  session writing in the job's scope or `jobs/<job>/`, and a session that holds only a replaced or stopped binding
  there (formerly stale-session / not-current-binding).

Claim limits: decisions on fixture payloads and fixture ledgers only. Nothing here registers or invokes a hook, and
no write is claimed to be prevented in Claude Code (that needs slice 4b). Shell commands are recorded as invocations
only (SHELL_LIMITATION).
"""
from __future__ import annotations

import fnmatch
import hashlib
import errno
import json
import ntpath
import os
import pathlib
import re
import socket
import sqlite3
import stat
from typing import Any

from . import bind, grant, pathpolicy, reconcile
from . import db as dbm

__all__ = ["PREVENTABLE_TOOLS", "SHELL_TOOLS", "PROTECTED_GLOBS", "SHELL_LIMITATION", "REASONS", "CHAIN_TAIL_ROWS",
           "target_paths", "ledger_identity", "decide"]

PREVENTABLE_TOOLS: tuple[str, ...] = ("Write", "Edit", "NotebookEdit")
SHELL_TOOLS: tuple[str, ...] = ("Bash", "PowerShell")
PROTECTED_GLOBS: tuple[str, ...] = ("ledger/**", "jobs/*/export/**", "runtime/**")
#: §2 rule 7, exact text (owner item 3).
SHELL_LIMITATION = ("shell commands are recorded as invocations only; their filesystem effects are not parsed, "
                    "prevented or comprehensively detected; changes inside the monitored boundary are detected later "
                    "by wf reconcile/accept only")
CHAIN_TAIL_ROWS = 50  # D-S4-4
_GRANT_CHECK_REASONS: tuple[str, ...] = ("allowed", "action-not-granted", "path-not-granted", "path-excluded",
                                         "role-not-in-ars", "unbound-role", "binding-outside-ars",
                                         "action-outside-role", "path-outside-role", "path-outside-binding")  # S4A-A2
#: Every reason string `decide` returns (§2), including the grant.check reasons rule 5 passes through.
REASONS: tuple[str, ...] = (
    "malformed-payload", "unresolvable-path", "protected-path",
    "ledger-unreadable", "ledger-locked", "ledger-identity", "chain-tail",
    "recovery-open", "cancelled", "authority-none", "authority-proposed", "authority-expired", "authority-revoked",
    "authority-voided", "authority-malformed", "job-closed",  # AMENDMENT S4A-A6
    "stale-session", "not-current-binding", *_GRANT_CHECK_REASONS,
    "unbound-session", "shell-not-preventable", "out-of-scope", "outside-boundary", "not-mediated", "gate-error",
    # PACKAGE-FIX1 I6: reasons only the hook emits (config and integrity checks, audit write failure); listed here so
    # REASONS is the one complete vocabulary of gate reasons
    "gate-config", "gate-integrity", "audit-unavailable")

_PATH_KEY = {"Write": "file_path", "Edit": "file_path", "NotebookEdit": "notebook_path"}
_DRIVE = re.compile(r"^[A-Za-z]:")


class _Deny(Exception):
    def __init__(self, reason: str, jobs: list[str] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.jobs = jobs or []


# ---------------------------------------------------------------------------------------------- paths (§1)

def _split_abs(p: str) -> tuple[str, list[str], bool] | None:
    """(anchor, components, windows) for an absolute path, `.` dropped, `..` kept; None if not absolute."""
    s = p.replace("/", "\\") if (_DRIVE.match(p) or p.startswith("\\\\") or "\\" in p) else p
    if _DRIVE.match(s) and len(s) >= 3 and s[2] == "\\":
        return s[:2].upper(), [c for c in s[3:].split("\\") if c not in ("", ".")], True
    if s.startswith("\\\\"):
        parts = [c for c in s[2:].split("\\") if c]
        if len(parts) < 2:
            return None
        return "\\\\" + "\\".join(parts[:2]).lower(), [c for c in parts[2:] if c != "."], True
    if s.startswith("/"):
        return "/", [c for c in s.split("/") if c not in ("", ".")], False
    return None


def _collapse(parts: list[str]) -> tuple[list[str], bool, bool]:
    """(collapsed, had_dotdot, escaped_anchor)."""
    out: list[str] = []
    had = False
    for c in parts:
        if c == "..":
            had = True
            if not out:
                return out, True, True
            out.pop()
        else:
            out.append(c)
    return out, had, False


def _absolute(raw: str, cwd: str) -> str:
    # AMENDMENT FIX1-A2 I7: a Windows drive-less rooted path (exactly one leading `\` or `/`) is rooted at the cwd's
    # drive, not joined to the cwd; _to_root_relative then decides inside or outside as usual. Windows reads ANY two
    # leading separators (`\\`, `//`, `/\`, `\/`) as UNC, so those keep their earlier answer (review WF-027 R1).
    if _DRIVE.match(cwd) and raw[:1] in ("\\", "/") and raw[1:2] not in ("\\", "/"):
        return cwd[:2] + raw.replace("/", "\\")
    if _split_abs(raw) is not None:
        return raw
    sep = "\\" if (_DRIVE.match(cwd) or "\\" in cwd) else "/"
    return cwd.rstrip("\\/") + sep + raw


def _link_component(root: str, rel_parts: list[str]) -> bool:
    """A symlink or reparse point among the existing components under the root (read-only lstat; never followed)."""
    cur = root
    for c in rel_parts:
        cur = os.path.join(cur, c)
        try:
            st = os.lstat(cur)
        except OSError:
            return False  # does not exist (yet): nothing further to follow
        if stat.S_ISLNK(st.st_mode):
            return True
        if getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            return True
    return False


_NOT_FOUND = (FileNotFoundError, NotADirectoryError)


def _identity(path: str) -> tuple:
    """The file identity of `path` for rule 1: ("id", st_dev, st_ino, normcased names below) of its deepest existing
    ancestor, by `os.stat` (it follows links, so every alias of one file or directory gives one identity); ("path",
    normcased absolute path) when no ancestor exists or the filesystem reports no file id. Raises OSError / ValueError
    when the path cannot be resolved; the caller fails closed."""
    cur = path
    below: list[str] = []
    while True:
        try:
            st = os.stat(cur)
        except _NOT_FOUND:
            head, name = os.path.split(cur)
            if head == cur or not head:
                return ("path", os.path.normcase(os.path.abspath(path)))
            if name:
                below.append(os.path.normcase(name))
            cur = head
            continue
        if not st.st_ino:
            return ("path", os.path.normcase(os.path.abspath(path)))
        return ("id", st.st_dev, st.st_ino, tuple(reversed(below)))


_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_ADMIN_SHARE = re.compile(r"[A-Za-z]\$")
_DRIVE_ONLY = re.compile(r"[A-Za-z]:")


def _local_hosts() -> frozenset[str]:
    """Names of this machine for UNC spellings, case-folded: the loopback names and socket.gethostname() (no DNS)."""
    try:
        return _LOCAL_HOSTS | {socket.gethostname().casefold()}
    except OSError:
        return _LOCAL_HOSTS


def _local_form(path: str) -> str | None:
    r"""WF-106 review F1: the spelling of `path` that rule 1 may resolve on this machine, decided without I/O.

    On Windows, a UNC spelling (\\host\share\..., \\?\UNC\host\share\..., \\.\UNC\host\share\..., either separator)
    whose host is this machine and whose share is a drive's admin share `X$` maps to `X:\...`. Any other UNC spelling,
    and any device path other than \\?\X:\... or \\.\X:\..., is None: it is never resolved here. Every other path, and
    every path on POSIX, is returned unchanged."""
    if os.name != "nt":
        return path
    s = path.replace("/", "\\")
    if not s.startswith("\\\\"):
        return path
    parts = s[2:].split("\\")
    if parts[0] in ("?", "."):
        if len(parts) > 1 and _DRIVE_ONLY.fullmatch(parts[1]):
            return path
        if len(parts) < 2 or parts[1].upper() != "UNC":
            return None
        parts = parts[2:]
    host, share = (parts + ["", ""])[:2]
    if _ADMIN_SHARE.fullmatch(share) and host.casefold() in _local_hosts():
        return share[0].upper() + ":\\" + "\\".join(parts[2:])
    return None


def _anchor(path: str) -> str | None:
    r"""The case-folded drive or anchor of an absolute path, lexically: "x:" for X:\..., \\?\X:\... and \\.\X:\...,
    "\\host\share" for a UNC path, "/" on POSIX; None for a path that is not absolute."""
    split = _split_abs(path)
    if split is None:
        return None
    anchor = split[0]
    if re.fullmatch(r"\\\\[?.]\\[A-Za-z]:", anchor):
        anchor = anchor[-2:]
    return anchor.casefold()


#: Z6: the longest chain of links the walk follows; a longer one is a loop (the usual SYMLOOP_MAX).
_MAX_LINK_HOPS = 40
_MUP = "GLOBALROOT\\DEVICE\\MUP\\"


def _link_target(text: str, link_dir: str) -> str | None:
    r"""Z6 (AMENDMENT FIX2-A18): where a link's target text points, decided from the text, without touching the
    target. None for a remote target, a UNC host that is not this machine in any spelling (\\host\...,
    \\?\UNC\host\..., \\.\UNC\host\..., \\?\GLOBALROOT\Device\Mup\host\...): the walk stops there. Otherwise the
    absolute path the walk continues on: this machine's admin shares map to their drive, as `_local_form` maps them;
    this machine's other shares, local device forms (\\?\X:\..., \\?\Volume{GUID}\...) and drive paths are followed
    as today. A drive letter mapped to a network share is a drive path here: a known limit (A19). Raises OSError for
    a target with a doubled device prefix (how a junction stored as \\?\X:\... reads back): Windows cannot traverse
    it, so it is unresolvable, as `os.stat` reports it (A20, Lazy WF-132 N1)."""
    s = text.replace("/", "\\")
    if s.startswith("\\\\?\\\\\\?\\"):
        raise OSError(errno.EINVAL, "link target with a doubled device prefix", text)
    unc: str | None = None
    if s.startswith(("\\\\?\\", "\\\\.\\", "\\??\\")):
        rest = s[4:]
        if rest.upper().startswith("UNC\\"):
            unc = rest[4:]
        elif rest.upper().startswith(_MUP):
            unc = rest[len(_MUP):]
        else:
            return s  # a local device form
    elif s.startswith("\\\\"):
        unc = s[2:]
    if unc is not None:
        host = unc.split("\\", 1)[0]
        if host.casefold() not in _local_hosts():
            return None  # remote: no I/O on it
        return _local_form("\\\\" + unc) or "\\\\" + unc  # this machine: an admin share maps to its drive
    if not ntpath.isabs(s):
        s = os.path.join(link_dir, s)
    return os.path.normpath(s)


def _is_link(st: os.stat_result) -> bool:
    """A link `os.stat` would follow: a symbolic link, or a name-surrogate reparse point (a junction)."""
    if stat.S_ISLNK(st.st_mode):
        return True
    reparse = getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(reparse and getattr(st, "st_reparse_tag", 0) & 0x20000000)


def _walk_links(path: str) -> tuple[bool, str | None]:
    """Z6 (spec :194; AMENDMENT FIX2-A20): walk `path` link by link: each component is `lstat`ed and a link's target
    is read with `readlink`, never followed. Returns (remote, first): `remote` is True when a link's target is remote
    (`_link_target`), and then nothing under that link is touched; `first` is the first link met on `path` itself, or
    None. The walk ends where a component does not exist, or at a link `readlink` cannot read. Raises OSError past
    `_MAX_LINK_HOPS` links (ELOOP) and for a target Windows cannot traverse; the caller fails closed. On POSIX, where
    no link target is a UNC path, it returns (False, None) with no I/O."""
    if os.name != "nt":
        return False, None
    cur, hops, first = path, 0, None
    while True:
        drive, root, tail = ntpath.splitroot(cur)
        parts = [c for c in tail.split("\\") if c not in ("", ".")]
        here = drive + root
        for i, name in enumerate(parts):
            here = os.path.join(here, name)
            try:
                st = os.lstat(here)
            except _NOT_FOUND:
                return False, first
            if not _is_link(st):
                continue
            first = first or here
            hops += 1
            if hops > _MAX_LINK_HOPS:
                raise OSError(errno.ELOOP, "too many levels of links", path)
            try:
                text = os.readlink(here)
            except (OSError, ValueError):
                return False, first
            nxt = _link_target(text, os.path.dirname(here))
            if nxt is None:
                return True, first
            cur = os.path.normpath(os.path.join(nxt, *parts[i + 1:]))
            break
        else:
            return False, first


def _below(path: str, root: str) -> bool:
    """Lexically, the absolute `path` lies below the absolute `root` (case-folded where the platform folds case)."""
    p, r = os.path.normcase(os.path.normpath(path)), os.path.normcase(os.path.normpath(root))
    return p.startswith(r.rstrip(os.sep) + os.sep)


def _is_gate_file(target: str, gate_files: list[str] | None, root: str) -> bool:
    """Rule 1 (FIX2 #4): the absolute `target` names a configured gate file by spelling, `realpath` or identity. An
    identity that cannot be resolved is treated as the gate file (fail closed). A target with no local form (a remote
    UNC or device spelling, WF-106 review F1) or whose local form is not on the boundary root's anchor (WF-106 review
    F2, AMENDMENT FIX2-A9 section 2: rule 0 denies it) is compared by spelling only, with no I/O. A target whose path
    holds a link to a remote target (Z6, spec :194) is never resolved: the link-by-link walk stops there and rule 1
    fails closed (AMENDMENT FIX2-A20), unless rule 0 refuses the target anyway because its first link lies under the
    root (`unresolvable-path`); rule 0's link check, run here only then, lstats no path through a link."""
    gates = [g for g in gate_files or [] if isinstance(g, str)]
    if not gates:
        return False
    spelled = os.path.normcase(os.path.abspath(target))
    if any(os.path.normcase(os.path.abspath(g)) == spelled for g in gates):
        return True
    local = _local_form(target)
    if local is None:
        return False
    root_abs = root if _split_abs(root) is not None else os.path.abspath(root)
    root_local = _local_form(root_abs) or root_abs
    if _anchor(local) != _anchor(root_local):
        return False
    try:
        remote, first = _walk_links(local)
        if remote:
            return not (first is not None and _below(first, root_local)
                        and _to_root_relative(target, root)[1] == "unresolvable-path")
        real, ident = os.path.normcase(os.path.realpath(local)), _identity(local)
        for g in gates:
            g_local = _local_form(g)
            if g_local is not None and (os.path.normcase(os.path.realpath(g_local)) == real
                                        or _identity(g_local) == ident):
                return True
        return False
    except (OSError, ValueError):
        return True


def _to_root_relative(target: str, root: str) -> tuple[str | None, str | None]:
    """(relative "a/b" path, None) inside the root; (None, "outside-boundary"); (None, "unresolvable-path")."""
    t = _split_abs(target)
    r = _split_abs(os.path.abspath(root) if _split_abs(root) is None else root)
    if t is None or r is None:
        return None, "unresolvable-path"
    (ta, tparts, twin), (ra, rparts, rwin) = t, r
    if twin != rwin or ta.lower() != ra.lower():
        return None, "unresolvable-path"  # drive / anchor mismatch
    tcol, had_dotdot, escaped = _collapse(tparts)
    rcol, _, _ = _collapse(rparts)
    if escaped:
        return None, "unresolvable-path"
    fold = (lambda x: x.casefold()) if rwin else (lambda x: x)
    inside = len(tcol) > len(rcol) and [fold(c) for c in tcol[:len(rcol)]] == [fold(c) for c in rcol]
    if not inside:
        return None, ("unresolvable-path" if had_dotdot else "outside-boundary")
    rel_parts = tcol[len(rcol):]
    rel = "/".join(rel_parts)
    res = pathpolicy.resolve(rel)
    if res.problem is not None or res.outside:
        return None, "unresolvable-path"
    if _link_component(root, rel_parts):
        return None, "unresolvable-path"
    return rel, None


def target_paths(payload: dict, root: str) -> tuple[list[str], str | None]:
    """Preventable-channel targets as boundary-root-relative "a/b" paths, and a problem: None, "outside-boundary"
    (every target lies outside the root), "unresolvable-path", or "malformed-payload"."""
    tool = payload.get("tool_name") if isinstance(payload, dict) else None
    key = _PATH_KEY.get(tool) if isinstance(tool, str) else None
    ti = payload.get("tool_input") if isinstance(payload, dict) else None
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    if key is None or not isinstance(ti, dict) or not isinstance(cwd, str):
        return [], "malformed-payload"
    raw = ti.get(key)
    if not isinstance(raw, str) or not raw.strip():
        return [], "malformed-payload"
    rel, problem = _to_root_relative(_absolute(raw, cwd), root)
    return ([rel] if rel is not None else []), problem


def ledger_identity(db_path: str) -> dict:
    """{"dev", "ino", "size"} of the ledger file (os.stat), for the install record and fixtures."""
    st = os.stat(db_path)
    return {"dev": st.st_dev, "ino": st.st_ino, "size": st.st_size}


# ---------------------------------------------------------------------------------------------- ledger (rule 2)

def _open_ro(db_path: str, timeout: float) -> sqlite3.Connection:
    if not os.path.isfile(db_path):
        raise _Deny("ledger-unreadable")
    try:
        conn = sqlite3.connect(pathlib.Path(os.path.abspath(db_path)).as_uri() + "?mode=ro", uri=True,
                               timeout=timeout, isolation_level=None)
    except sqlite3.Error:
        raise _Deny("ledger-unreadable") from None
    return conn


def _sql_reason(exc: sqlite3.Error) -> str:
    text = str(exc).lower()
    return "ledger-locked" if ("locked" in text or "busy" in text) else "ledger-unreadable"


def _fold_match(rel: str, glob: str) -> bool:
    """Case-folded glob match, also matching the directory a trailing `/**` names. Used only on the deny side (a
    protected path, a job's scope for rules 3/6), so folding can only widen a denial: a case variant of a protected or
    job path never escapes on a case-insensitive filesystem. Allows still go through grant.check unchanged."""
    r, g = rel.casefold(), glob.replace("\\", "/").casefold()
    return r == g or fnmatch.fnmatchcase(r, g) or (g.endswith("/**") and fnmatch.fnmatchcase(r, g[:-3]))


def _glob(rel: str, globs: object) -> bool:
    return isinstance(globs, list) and any(isinstance(g, str) and _fold_match(rel, g) for g in globs)


def _in_job(rel: str, job: str, contract: dict) -> bool:
    return rel.casefold().startswith(f"jobs/{job}/".casefold()) or _glob(rel, contract.get("write_scope"))


def _content(r: dict) -> dict:
    try:
        c = json.loads(r["content"])
    except (TypeError, ValueError):
        return {}
    return c if isinstance(c, dict) else {}


def _implicit_builder(rows: list[dict]) -> str | None:
    """The implicit builder session (last builder-authored row, conflict blockers excluded)."""
    s = None
    for r in rows:
        if r["author_role"] == "builder" and not (r["type"] == "blocker"
                                                  and _content(r).get("reason") == "claimant-conflict"):
            s = r["session_id"]
    return s


def _session_on_job(conn: sqlite3.Connection, job: str, rows: list[dict], session: str) -> tuple[str, str | None]:
    """("bound", role) | ("stale", role) | ("stopped", role) | ("none", None) for `session` on `job`."""
    table = bind.active_roles(conn, job)
    if table:
        for role, b in table.items():
            if b.session == session:
                return ("stopped", role) if b.stopped_seq is not None else ("bound", role)
        for r in rows:
            if r["type"] == "bind" and r["author_role"] == "coordinator":
                c = _content(r)
                if c.get("session") == session and c.get("role") in table:
                    return "stale", c["role"]
        return "none", None
    if _implicit_builder(rows) == session:
        return "bound", "builder"
    return "none", None


# ---------------------------------------------------------------------------------------------- decide (§2)

def _base(payload: Any) -> dict:
    p = payload if isinstance(payload, dict) else {}
    tool = p.get("tool_name") if isinstance(p.get("tool_name"), str) else None
    channel = ("preventable" if tool in PREVENTABLE_TOOLS else "shell" if tool in SHELL_TOOLS else "other")
    return {"decision": "deny", "reason": "gate-error", "channel": channel,
            "session": p.get("session_id") if isinstance(p.get("session_id"), str) else None,
            "tool_name": tool, "jobs": [], "paths": [], "command_sha256": None, "command_length": None,
            "limitation": SHELL_LIMITATION if channel == "shell" else None}


def _shape_ok(p: Any) -> bool:
    if not isinstance(p, dict):
        return False
    for k in ("session_id", "hook_event_name", "tool_name", "cwd"):
        if not isinstance(p.get(k), str) or not p[k].strip():
            return False
    if p["hook_event_name"] != "PreToolUse" or not isinstance(p.get("tool_input"), dict):
        return False
    if p["tool_name"] in SHELL_TOOLS and not isinstance(p["tool_input"].get("command"), str):
        return False
    return True


def decide(payload: dict, *, root: str, db_path: str, now: str, gate_files: list[str], read_timeout_s: float = 2.0,
           expected_identity: dict | None = None) -> dict:
    """§2 decision for one PreToolUse payload. Never raises; any internal error is deny / gate-error."""
    out = _base(payload)
    try:
        return _decide(payload, out, root=root, db_path=db_path, now=now, gate_files=gate_files,
                       read_timeout_s=read_timeout_s, expected_identity=expected_identity)
    except _Deny as d:
        out.update(decision="deny", reason=d.reason)
        if d.jobs:
            out["jobs"] = d.jobs
        return out
    except Exception:  # noqa: BLE001 - fail closed (§2 last sentence)
        out.update(decision="deny", reason="gate-error")
        return out


def _decide(payload: dict, out: dict, *, root: str, db_path: str, now: str, gate_files: list[str],
            read_timeout_s: float, expected_identity: dict | None) -> dict:
    # rule 0: payload shape
    if not _shape_ok(payload):
        raise _Deny("malformed-payload")
    channel, session = out["channel"], payload["session_id"]
    if channel == "shell":
        cmd = payload["tool_input"]["command"]
        out.update(command_sha256=hashlib.sha256(cmd.encode("utf-8")).hexdigest(), command_length=len(cmd))
    if channel == "other":
        out.update(decision="allow", reason="not-mediated")  # rule 8, D-S4-5
        return out
    targets: list[str] = []
    outside = False
    if channel == "preventable":
        raw = payload["tool_input"].get(_PATH_KEY[payload["tool_name"]])
        if not isinstance(raw, str) or not raw.strip():
            raise _Deny("malformed-payload")
        # rule 1 (gate files): compared before any boundary classification, by spelling (`..`, case, separators),
        # realpath and file identity (FIX2 #4: any alias of a gate file), failing closed; identity I/O only on the
        # boundary root's anchor (WF-106 review F1/F2)
        if _is_gate_file(_absolute(raw, payload["cwd"]), gate_files, root):
            raise _Deny("protected-path")
        targets, problem = target_paths(payload, root)
        if problem == "malformed-payload":
            raise _Deny("malformed-payload")
        if problem == "unresolvable-path":
            raise _Deny("unresolvable-path")
        outside = problem == "outside-boundary"
        out["paths"] = list(targets)
        # rule 1 (protected globs, everyone)
        if any(_fold_match(t, g) for t in targets for g in PROTECTED_GLOBS):
            raise _Deny("protected-path")
    # rule 2: ledger health (read-only, one read transaction, bounded wait)
    if expected_identity is not None:
        try:
            ident = ledger_identity(db_path)
        except OSError:
            raise _Deny("ledger-unreadable") from None
        if (ident["dev"] != expected_identity.get("dev") or ident["ino"] != expected_identity.get("ino")
                or ident["size"] < int(expected_identity.get("size", 0))):
            raise _Deny("ledger-identity")
    conn = _open_ro(db_path, read_timeout_s)
    try:
        try:
            conn.execute("BEGIN")
            jobs = dbm.jobs(conn)
            ledger: dict[str, tuple[list[dict], dict]] = {}
            for j in jobs:
                c = dbm.contract(conn, j)
                contract = json.loads(c["content"]) if c else {}
                ledger[j] = (dbm.rows(conn, j), contract if isinstance(contract, dict) else {})
        except sqlite3.Error as exc:
            raise _Deny(_sql_reason(exc)) from None
        # rule 3: job resolution
        by_path = sorted({j for j, (_, ctr) in ledger.items() for t in targets if _in_job(t, j, ctr)})
        status = {j: _session_on_job(conn, j, rows, session) for j, (rows, _) in ledger.items()}
        bound = sorted(j for j, (s, _) in status.items() if s == "bound")
        held = sorted(j for j, (s, _) in status.items() if s in ("stale", "stopped"))
        touched = sorted(set(by_path) | (set(bound) if (channel == "shell" or targets or outside) else set())
                         | (set(held) if channel == "preventable" and not bound else set()))
        out["jobs"] = touched
        # rule 2 (cont.): chain tail of every touched job (D-S4-4)
        try:
            for j in touched:
                head = dbm.head(conn, j)[0]
                if head and dbm.verify_chain(conn, j, from_seq=max(1, head - CHAIN_TAIL_ROWS + 1)):
                    raise _Deny("chain-tail", touched)
        except sqlite3.Error as exc:
            raise _Deny(_sql_reason(exc), touched) from None
        # rule 4: per-job state
        for j in touched:
            rows, _ = ledger[j]
            if reconcile.recovery_open(rows) is not None:
                raise _Deny("recovery-open", touched)
            state = grant.authority(rows, job=j, now=now)["state"]
            if state == "cancelled":
                raise _Deny("cancelled", touched)
            if state in ("revoked", "expired", "voided", "malformed"):
                raise _Deny(f"authority-{state}", touched)
            if state in ("none", "proposed") and j in bound:
                raise _Deny(f"authority-{state}", touched)
        # rule 4 (AMENDMENT S4A-A6): a closed job, preventable channel only; the shell channel is unchanged (rule 7)
        if channel == "preventable" and any(r["type"] == "close" for j in touched for r in ledger[j][0]):
            raise _Deny("job-closed", touched)
        # rule 7: shell channel (rule 4 has applied to the session's bound jobs)
        if channel == "shell":
            out.update(decision="allow", reason="shell-not-preventable")
            return out
        # rule 5 (identity): a session whose every binding is replaced or stop-confirmed makes no hooked write,
        # inside or outside the boundary (T13)
        if held and not bound:
            stale = any(status[j][0] == "stale" for j in held)
            raise _Deny("stale-session" if stale else "not-current-binding", touched)
        if outside and not targets:
            if bound:
                raise _Deny("out-of-scope", touched)  # rule 8, T25: a bound session stays inside its grant
            out.update(decision="allow", reason="outside-boundary")  # rule 8: unbound, not a Workforce path
            return out
        for t in targets:
            owners = [j for j in by_path if _in_job(t, j, ledger[j][1])]
            # rule 5: bound session, grant.check on the session's job
            if bound:
                cands = [j for j in bound if _in_job(t, j, ledger[j][1])] or bound
                reasons = []
                for j in cands:
                    rows, contract = ledger[j]
                    role = status[j][1]
                    b = bind.active_roles(conn, j).get(role) if role else None
                    binding = {"role": role, "tools": list(b.tools), "paths": list(b.paths)} if b else None
                    res = grant.check(rows, job=j, now=now, role=role, action="write", path=t, contract=contract,
                                      binding=binding)
                    if res["allow"]:
                        break
                    reasons.append(res["reason"])
                else:
                    raise _Deny(reasons[0], touched)
                continue
            # rule 6: unbound session into a job's write scope or jobs/<job>/
            if owners:
                raise _Deny("unbound-session", touched)
        out.update(decision="allow", reason="allowed" if bound else "not-mediated")  # rule 5 allow / rule 8
        return out
    finally:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        conn.close()
