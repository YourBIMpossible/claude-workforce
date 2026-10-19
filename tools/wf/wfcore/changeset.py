"""Changed-set capture and comparison inside the monitored boundary (spec section 4.4, T35 repair A1; lane B).

Owner decisions (2026-10-15 grant "T35 REQUIREMENT REPAIRS A1-A3 + T28"): the baseline is captured by `wf job new`
and stored in the chain-protected contract event payload (commands.job_new, White); `wf accept` compares the
same walk now against it and classifies the union of supplied and observed changes (commands.accept, White).

Interfaces (fixed by White's wiring; keep the signatures):
  boundary_root(db_path) -> str | None     parent of the ledger directory when its basename is "ledger" (the same
                                           rule as paths.jobs_dir); None = no boundary = the caller's blocker.
  baseline(root, contract) -> dict         {"method", "scopes", "entries", "problems", "limitations"}
  compare(root, contract, baseline) -> dict {"added", "modified", "deleted", "problems", "limitations"}

What is walked, and nothing else: the static directory prefix (the components before the first glob character) of
every `read_scope`, `watch`, `presence_only` and `write_scope` glob (S2 A-2: the write scope is walked too, D-S1-5
option B), under `root`, with `os.walk(followlinks=False)`. Never the
whole tree, never the ledger directory (`<ledger_rel>/`), never a job export directory (`jobs/<job>/export/`): both
are the spec 4.4 P4 coordination set, checked by `wf verify`, not hashed. Every directory or file entry that is a
symlink or a Windows reparse point (junction) is skipped and recorded under `limitations`, never followed. A glob
with no static prefix (`**`, `*`) cannot be walked without scanning the whole root and is a `problem` (fail closed).

Entries: a file matching a `presence_only` glob is recorded as {"present": true} and is NEVER opened, hashed or
sized; a file matching a `read_scope`, `watch` or `write_scope` glob is recorded as {"sha256", "size"}, so an
unrecorded P1 (write-scope) change appears in `compare` as added / modified / deleted (S2 A-2); a file matching none
is not recorded. Matching uses pathpolicy's resolution and glob rules.
A baseline captured before A-2 holds no write-scope entries: `compare` then reports every present write-scope file
as `added` - correct and honest; a fixture that needs the new form regenerates the baseline.
An unreadable file is a `problem` (fail closed), never skipped. Paths are repo-relative forward-slash strings.
A file name that is not valid Unicode in a recorded scope (POSIX: undecodable bytes, which `os.walk` surrogate-
escapes; Windows: an NTFS name holding a lone UTF-16 surrogate) is a `problem` ("file name is not valid Unicode
(undecodable bytes or a lone surrogate) inside the boundary: <name>"), never an entry (FIX1 P4, WF-021 F6: the
surrogate key made the canonical hash raise UnicodeEncodeError, a traceback instead of a refusal). Every text the walk
records (problem or limitation: skipped link, unresolvable, unreadable) shows such a name escaped by `_shown` (review
R2/R3): undecodable bytes as `\\xNN` on POSIX, lone surrogates as `\\udNNN` on Windows, so no text holds a surrogate.

`compare`: the same walk now against `baseline["entries"]`: added = now and not in baseline, deleted = baseline and
not now, modified = in both with a different sha256 or size (presence-only entries are never "modified"). A
baseline that is not a dict, lacks a dict `entries`, or holds a malformed entry is a `problem`. A declared
manifest path that did not exist at capture is not a deletion (fixtures declare manifests without materialising
them; White's wiring states this).

Claim limits (recorded in `limitations`): outside-boundary changes are not detected; links and junctions are not
resolved (skipped); a change made between the walk and the ledger write is not observed; nothing is read outside
`root`.
"""
from __future__ import annotations

import os
import stat

from . import paths, pathpolicy
from .canon import sha256_file

__all__ = ["boundary_root", "baseline", "compare", "walk_entries", "METHOD"]

METHOD = ("static-prefix walk of read_scope/watch/presence_only/write_scope under the boundary root; sha256+size "
          "for read_scope/watch/write_scope files; existence only for presence_only files (never opened); symlinks "
          "and reparse points skipped; the ledger directory and jobs/*/export/ never walked")
NOT_DETECTED = pathpolicy.NOT_DETECTED
_LIMITS: tuple[str, ...] = (
    NOT_DETECTED,
    "links and junctions are skipped, not resolved; whether the platform resolves them is not established here",
    "the ledger directory and every jobs/<job>/export/ directory are the P4 coordination set, checked by wf verify "
    "and never hashed here",
    "a change made between this walk and the ledger write is not observed",
)
_GLOB_CHARS = "*?["
_SCOPE_KEYS = ("read_scope", "watch", "presence_only", "write_scope")  # S2 A-2: write_scope walked


def boundary_root(db_path: str) -> str | None:
    """Parent of the ledger directory when that directory is named "ledger"; else None (no boundary)."""
    d = paths.ledger_dir(db_path)
    if os.path.basename(d).lower() != "ledger":
        return None
    return os.path.dirname(d)


def _is_link(path: str) -> bool:
    """True for a symlink or a Windows reparse point (junction, mount point); never follows it."""
    try:
        st = os.lstat(path)
    except OSError:
        return True  # unreadable entry: treat as not traversable (recorded by the caller)
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _escape_name(rel: str, *, windows: bool) -> str:
    """`rel` with every lone surrogate escaped, by an explicit per-platform rule (review R3; never by os.fsencode's
    error handler, which is surrogateescape on POSIX but surrogatepass on Windows). POSIX: a surrogate U+DC80..U+DCFF
    is an undecodable byte that `os.walk` surrogate-escaped, shown as `\\xNN`. Windows: NTFS names are UTF-16, so
    every lone surrogate is shown as `\\udNNN`. Any other lone surrogate is shown as `\\udNNN` on both. Total: never
    raises, and the result always encodes as UTF-8."""
    out: list[str] = []
    for ch in rel:
        code = ord(ch)
        if 0xD800 <= code <= 0xDFFF:
            out.append(f"\\x{code - 0xDC00:02x}" if not windows and 0xDC80 <= code <= 0xDCFF else f"\\u{code:04x}")
        else:
            out.append(ch)
    return "".join(out)


def _shown(rel: str) -> str:
    """The form of a walked name used in every recorded text: the name itself, or `_escape_name` for this platform
    when it holds a lone surrogate (review R2: a raw surrogate in a limitation made canon.canonical raise)."""
    try:
        rel.encode("utf-8")
    except UnicodeEncodeError:
        return _escape_name(rel, windows=os.name == "nt")
    return rel


def _static_prefix(glob: str) -> str:
    """Components of a normalized (forward-slash) glob before the first component holding a glob character."""
    out: list[str] = []
    for comp in glob.split("/"):
        if any(ch in comp for ch in _GLOB_CHARS):
            break
        out.append(comp)
    return "/".join(out)


def _coordination_prefix(rel: str) -> bool:
    """True when `rel` (forward slash, as walked) is inside the ledger directory or a job export directory. The
    ledger component is matched in any case, the same rule as `boundary_root` and paths.jobs_dir (a `LEDGER`
    directory is the ledger there); FIX1 P1 stopped lower-casing scope globs, so this keeps it unwalked."""
    parts = rel.split("/")
    if parts[0].lower() == "ledger":
        return True
    return len(parts) >= 3 and parts[0] == "jobs" and parts[2] == "export"


def walk_entries(root: str, contract: dict) -> tuple[dict[str, dict], list[str], list[str]]:
    """(entries, problems, limitations) for the scoped walk under `root`. Never reads outside `root`."""
    problems: list[str] = []
    limitations: list[str] = list(_LIMITS)
    entries: dict[str, dict] = {}
    scopes, scope_problems = pathpolicy._scopes(contract)
    problems.extend(f"contract scopes: {p}" for p in scope_problems)
    if scope_problems:
        return entries, problems, limitations
    root_abs = os.path.abspath(root)
    presence = scopes["presence_only"]
    hashed = scopes["read_scope"] + scopes["watch"] + scopes["write_scope"]
    prefixes: list[str] = []
    for key in _SCOPE_KEYS:
        for gg in scopes[key]:
            g = gg.replace("\\", "/")
            prefix = _static_prefix(g)
            if not prefix:
                problems.append(f"{key}: glob {g!r} has no static prefix; walking the whole boundary is not allowed")
                continue
            if _coordination_prefix(prefix):
                limitations.append(f"{key}: {g!r} lies in the coordination set and is not walked")
                continue
            if prefix not in prefixes:
                prefixes.append(prefix)
    seen: set[str] = set()

    def record(abs_path: str, rel: str) -> None:
        if rel in seen or _coordination_prefix(rel):
            return
        seen.add(rel)
        shown = _shown(rel)
        if _is_link(abs_path):
            limitations.append(f"skipped link or reparse point: {shown}")
            return
        r = pathpolicy.resolve(rel)
        if r.problem is not None or r.outside or r.cmp is None:
            problems.append(f"unresolvable path inside the boundary: {shown} ({r.problem or 'outside'})")
            return
        is_presence = pathpolicy._match(r.cmp, presence)
        if not is_presence and not pathpolicy._match(r.cmp, hashed):
            return
        if shown != rel:
            problems.append(f"file name is not valid Unicode (undecodable bytes or a lone surrogate) inside the "
                            f"boundary: {shown}")
            return
        if is_presence:
            entries[rel] = {"present": True}  # never opened
            return
        try:
            entries[rel] = {"sha256": sha256_file(abs_path), "size": os.path.getsize(abs_path)}
        except OSError as exc:
            problems.append(f"unreadable: {shown} ({type(exc).__name__})")

    for prefix in prefixes:
        abs_prefix = os.path.join(root_abs, *prefix.split("/"))
        if not os.path.lexists(abs_prefix):
            continue
        if _is_link(abs_prefix):
            limitations.append(f"skipped link or reparse point: {_shown(prefix)}")
            continue
        if os.path.isfile(abs_prefix):
            record(abs_prefix, prefix)
            continue
        for dirpath, dirnames, filenames in os.walk(abs_prefix, followlinks=False):
            rel_dir = os.path.relpath(dirpath, root_abs).replace("\\", "/")
            keep: list[str] = []
            for d in sorted(dirnames):
                rel_d = f"{rel_dir}/{d}"
                if _coordination_prefix(rel_d):
                    continue
                if _is_link(os.path.join(dirpath, d)):
                    limitations.append(f"skipped link or reparse point: {_shown(rel_d)}")
                    continue
                keep.append(d)
            dirnames[:] = keep
            for f in sorted(filenames):
                record(os.path.join(dirpath, f), f"{rel_dir}/{f}")
    return dict(sorted(entries.items())), problems, limitations


def _scopes_view(contract: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for key in _SCOPE_KEYS:
        v = contract.get(key, []) if isinstance(contract, dict) else []
        out[key] = [x for x in v if isinstance(x, str)] if isinstance(v, list) else []
    return out


def baseline(root: str, contract: dict) -> dict:
    """Capture the baseline of the scoped boundary walk (see module docstring)."""
    if not isinstance(contract, dict):
        return {"method": METHOD, "scopes": _scopes_view(contract), "entries": {},
                "problems": ["contract is not an object"], "limitations": list(_LIMITS)}
    entries, problems, limitations = walk_entries(root, contract)
    return {"method": METHOD, "scopes": _scopes_view(contract), "entries": entries, "problems": problems,
            "limitations": limitations}


def _entry_ok(e: object) -> bool:
    if not isinstance(e, dict):
        return False
    if e.get("present") is True:
        return True
    return isinstance(e.get("sha256"), str) and isinstance(e.get("size"), int)


def compare(root: str, contract: dict, baseline: dict) -> dict:
    """Added / modified / deleted paths now versus `baseline`; problems non-empty = the caller fails closed."""
    out: dict = {"added": [], "modified": [], "deleted": [], "problems": [], "limitations": []}
    if not isinstance(baseline, dict) or not isinstance(baseline.get("entries"), dict):
        out["problems"].append("baseline malformed: not an object with an 'entries' object")
        return out
    old: dict[str, dict] = {}
    for rel, e in baseline["entries"].items():
        if not isinstance(rel, str) or not _entry_ok(e):
            out["problems"].append(f"baseline malformed: entry {rel!r}")
            continue
        old[rel] = e
    if out["problems"]:
        return out
    now, problems, limitations = walk_entries(root, contract)
    out["problems"].extend(problems)
    out["limitations"] = limitations
    for rel in sorted(now):
        if rel not in old:
            out["added"].append(rel)
            continue
        a, b = old[rel], now[rel]
        if a.get("present") is True or b.get("present") is True:
            continue  # presence-only: existence is the whole comparison
        if a.get("sha256") != b.get("sha256") or a.get("size") != b.get("size"):
            out["modified"].append(rel)
    out["deleted"] = sorted(rel for rel in old if rel not in now)
    return out
