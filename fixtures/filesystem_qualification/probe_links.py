"""Filesystem qualification probe: link non-following in Workforce's three scanning surfaces.

Surfaces (product code under test, unchanged):
  baseline   wfcore.changeset.baseline      -> limitation "skipped link or reparse point: <rel>"
  export     wfcore.export.check            -> problem    "unexpected export link: <name>"
  ledgerdir  wfcore.commands.verify         -> ledger_directory.problems "unexpected ledger-directory file: <name>@"
                                               (a junction is not a symlink to Python: observed form is recorded)

Link kinds, each its own evidence class (never conflated):
  linux-symlink      POSIX os.symlink (file and directory)
  windows-symlink    os.symlink on win32 (needs SeCreateSymbolicLinkPrivilege or Developer Mode: NOT enabled here)
  windows-junction   cmd /c mklink /J (directory junction, no privilege needed)
A kind that this host cannot produce is reported `not-applicable` (wrong OS) or `skip` (OS right, creation failed,
exact error text kept) with the exact repeatable command for a capable host. A skip is never evidence.

Observation mechanism (executable, not a hash alone):
  1. sys.addaudithook records every Python-level `open`, `os.scandir`, `os.listdir`, `os.remove`, `os.unlink`,
     `os.rename`, `os.replace`, `os.rmdir`, `os.mkdir`, `shutil.*` and `sqlite3.connect` event raised while the
     surface runs; any event whose path resolves under the synthetic target directory is a traversal/read/write.
  2. POSIX canary: the target sentinel is chmod 000 during the call; a read attempt would surface as an
     "unreadable" problem in the product output or an OSError (recorded), independent of the hook.
  3. Target bytes (sha256), identity (st_dev, st_ino, st_size, st_mtime_ns) and directory listing before and after.
Claim made when all three agree: "the target was not opened, listed, or changed by Python-level file operations
during the call". Not claimed: kernel-level access, behaviour on another OS or filesystem, anything about the
platform's own link resolution.

Stdlib only. Run from the repository root with:  python -I fixtures/filesystem_qualification/probe_links.py
Writes fixtures/filesystem_qualification/results/<platform>-<rev12>.json (and prints a summary). Fixtures live in a
fresh directory under fixtures/tmp/ (git-ignored) and are left in place for inspection; nothing outside it is written.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Any, Callable

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
WF_DIR = os.path.join(REPO, "tools", "wf")
sys.path.insert(0, WF_DIR)
sys.path.insert(0, os.path.join(WF_DIR, "tests"))
WIN = sys.platform == "win32"

SCHEMA_VERSION = 1
PRODUCT_WRITES_UNDER = ("ledger", "jobs", "pending")  # the product may legitimately write only inside its own tree


# ---------------------------------------------------------------- environment

def git(*args: str) -> str:
    try:
        return subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def fs_type(path: str) -> str:
    """Filesystem type of the mount holding `path`: /proc/mounts on Linux, GetVolumeInformationW on Windows."""
    path = os.path.realpath(path)
    if WIN:
        root = os.path.splitdrive(path)[0] + "\\"
        buf = ctypes.create_unicode_buffer(64)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(root, None, 0, None, None, None, buf, 64)
        return buf.value if ok else "unknown"
    try:
        best = ("", "unknown")
        with open("/proc/mounts", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 3 and path.startswith(parts[1]) and len(parts[1]) > len(best[0]):
                    best = (parts[1], parts[2])
        return best[1]
    except OSError:
        return "unknown"


def privilege_state() -> dict[str, Any]:
    if WIN:
        try:
            admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # noqa: BLE001
            admin = None
        dev_mode = None
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock") as k:
                dev_mode = bool(winreg.QueryValueEx(k, "AllowDevelopmentWithoutDevLicense")[0])
        except OSError:
            dev_mode = False
        return {"is_admin": admin, "developer_mode_read_only": dev_mode, "note": "read only; nothing enabled"}
    return {"uid": os.getuid(), "euid": os.geteuid(), "protected_symlinks": _read("/proc/sys/fs/protected_symlinks")}


def _read(p: str) -> str | None:
    try:
        with open(p, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def environment(scratch: str) -> dict[str, Any]:
    head = git("rev-parse", "HEAD")
    return {
        "revision": {"head": head, "head12": head[:12], "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
                     "dirty": bool(git("status", "--porcelain", "--", "tools")),
                     "manifest_line": _manifest_line()},
        "host": {"platform": platform.platform(), "system": platform.system(), "release": platform.release(),
                 "machine": platform.machine(), "node": platform.node(), "sys_platform": sys.platform,
                 "windows_build": sys.getwindowsversion().build if WIN else None},
        "python": {"version": platform.python_version(), "implementation": platform.python_implementation(),
                   "sqlite": sqlite3.sqlite_version, "isolated": bool(sys.flags.isolated)},
        "filesystem": {"scratch": scratch, "type": fs_type(scratch), "case_insensitive": _case_insensitive(scratch)},
        "privilege": privilege_state(),
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _manifest_line() -> str | None:
    p = os.path.join(REPO, "MANIFEST.sha256")
    try:
        with open(p, encoding="utf-8") as fh:
            first = fh.readline().strip()
        return first[:120]
    except OSError:
        return None


def _case_insensitive(d: str) -> bool | None:
    try:
        p = os.path.join(d, "CaseProbe.tmp")
        with open(p, "x", encoding="utf-8"):
            pass
        try:
            return os.path.exists(os.path.join(d, "caseprobe.tmp"))
        finally:
            os.remove(p)
    except OSError:
        return None


# ---------------------------------------------------------------- link capability

def try_symlink(src: str, dst: str, is_dir: bool) -> tuple[bool, str | None]:
    try:
        os.symlink(src, dst, target_is_directory=is_dir)
        return True, None
    except (OSError, NotImplementedError, AttributeError) as exc:
        err = getattr(exc, "winerror", None)
        return False, f"{type(exc).__name__}: {exc}" + (f" (WinError {err})" if err else "")


def try_junction(src: str, dst: str) -> tuple[bool, str | None]:
    if not WIN:
        return False, "not-applicable: junctions exist only on Windows/NTFS"
    p = subprocess.run(["cmd", "/c", "mklink", "/J", dst, src], capture_output=True, text=True)
    if p.returncode == 0 and os.path.lexists(dst):
        return True, None
    return False, f"mklink /J rc {p.returncode}: {(p.stderr or p.stdout).strip()}"


JUNCTION_COMMAND = 'cmd /c mklink /J "<link>" "<target-dir>"'
SYMLINK_COMMAND = "python -c \"import os; os.symlink(r'<target>', r'<link>', target_is_directory=<bool>)\""


def capability(scratch: str) -> dict[str, Any]:
    d = os.path.join(scratch, "capability"); os.makedirs(d)
    tgt_f = os.path.join(d, "target.txt"); _write(tgt_f, b"cap\n")
    tgt_d = os.path.join(d, "target-dir"); os.makedirs(tgt_d)
    out: dict[str, Any] = {}
    ok, why = try_symlink(tgt_f, os.path.join(d, "sym-file"), False)
    out["symlink_file"] = {"created": ok, "error": why}
    ok, why = try_symlink(tgt_d, os.path.join(d, "sym-dir"), True)
    out["symlink_dir"] = {"created": ok, "error": why}
    ok, why = try_junction(tgt_d, os.path.join(d, "junction"))
    out["junction"] = {"created": ok, "error": why}
    out["without_privilege_change"] = True
    out["repeat_commands"] = {"symlink": SYMLINK_COMMAND, "junction": JUNCTION_COMMAND}
    return out


# ---------------------------------------------------------------- observation mechanism

WATCHED = ("open", "os.scandir", "os.listdir", "os.remove", "os.unlink", "os.rename", "os.replace", "os.rmdir",
           "os.mkdir", "os.chmod", "os.truncate", "shutil.copyfile", "shutil.copytree", "shutil.rmtree",
           "shutil.move", "sqlite3.connect")


class Watch:
    """One process-wide audit hook; armed only around the call under test. Records watched events whose first
    path-like argument resolves under `target_dir`, and every event whose path lies outside the scratch tree."""

    def __init__(self, scratch: str) -> None:
        self.scratch = os.path.realpath(scratch)
        self.armed = False
        self.target: str | None = None
        self.hits: list[dict[str, Any]] = []
        self.outside: list[dict[str, Any]] = []
        self.count = 0
        sys.addaudithook(self._hook)

    def _hook(self, event: str, args: tuple) -> None:
        if not self.armed or event not in WATCHED:
            return
        self.count += 1
        p = _first_path(args)
        if p is None:
            return
        rp = os.path.realpath(p)
        if self.target and _under(rp, self.target):
            self.hits.append({"event": event, "path": p})
        elif not _under(rp, self.scratch) and not _under(rp, os.path.realpath(REPO)) and not _is_runtime_path(rp):
            self.outside.append({"event": event, "path": p})

    def run(self, target: str, fn: Callable[[], Any]) -> tuple[Any, dict[str, Any]]:
        self.target = os.path.realpath(target)
        self.hits, self.outside, self.count = [], [], 0
        self.armed = True
        try:
            result = fn()
        finally:
            self.armed = False
        return result, {"watched_events_total": self.count, "target_events": list(self.hits),
                        "outside_scratch_events": list(self.outside)}


def _first_path(args: tuple) -> str | None:
    for a in args:
        if isinstance(a, (str, bytes, os.PathLike)):
            try:
                return os.fsdecode(os.fspath(a))
            except TypeError:
                return None
    return None


def _under(p: str, root: str) -> bool:
    try:
        return os.path.commonpath([p, root]) == root
    except ValueError:
        return False


def _is_runtime_path(rp: str) -> bool:
    """Interpreter/stdlib paths (bytecode caches, encodings) are not filesystem evidence."""
    pref = (sys.prefix, sys.base_prefix, getattr(sys, "_stdlib_dir", "") or "", tempfile.gettempdir())
    return any(pre and _under(rp, os.path.realpath(pre)) for pre in pref) or rp.endswith((".pyc", ".pyo"))


def snapshot(target_dir: str, sentinel: str) -> dict[str, Any]:
    st = os.lstat(sentinel)
    with open(sentinel, "rb") as fh:
        data = fh.read()
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": st.st_size, "st_dev": st.st_dev, "st_ino": st.st_ino,
            "st_mtime_ns": st.st_mtime_ns, "listing": sorted(os.listdir(target_dir))}


class Canary:
    """POSIX: make the sentinel unreadable during the call. Windows: not applied (ACL changes are out of scope)."""

    def __init__(self, sentinel: str) -> None:
        self.sentinel = sentinel
        self.applied = False

    def __enter__(self) -> "Canary":
        if not WIN and os.geteuid() != 0:
            try:
                os.chmod(self.sentinel, 0)
                self.applied = True
            except OSError:
                self.applied = False
        return self

    def __exit__(self, *exc: object) -> None:
        if self.applied:
            os.chmod(self.sentinel, stat.S_IRUSR | stat.S_IWUSR)


# ---------------------------------------------------------------- surfaces

def _write(path: str, data: bytes) -> None:
    with open(path, "xb") as fh:
        fh.write(data)


def _contract() -> dict:
    from test_wf import CONTRACT  # existing suite's contract; same shape wf validates
    return dict(CONTRACT)


def make_ledger(case_dir: str) -> tuple[str, str]:
    """A real ledger with one job (wf job new + a grant, in-process); returns (db_path, contract_path)."""
    import wf
    cpath = os.path.join(case_dir, "contract.json")
    with open(cpath, "w", encoding="utf-8") as fh:
        json.dump(_contract(), fh)
    db = os.path.join(case_dir, "ledger", "l.db")
    code, res = wf.run(["--db", db, "--role", "coordinator", "--session", "C1", "job", "new", "j", "--contract", cpath])
    if code != 0:
        raise RuntimeError(f"wf job new failed: {res}")
    return db, cpath


def surface_baseline(case_dir: str, link_maker: Callable[[str, str], tuple[bool, str | None]], target_dir: str
                     ) -> tuple[Callable[[], Any], Callable[[Any], tuple[bool, list[str]]], str]:
    """Link placed at tools/wf/link inside the boundary (contract read_scope tools/wf/**)."""
    from wfcore import changeset
    root = os.path.join(case_dir, "boundary"); os.makedirs(os.path.join(root, "tools", "wf"))
    os.makedirs(os.path.join(root, "ledger"))
    _write(os.path.join(root, "tools", "wf", "real.py"), b"print('real')\n")
    link = os.path.join(root, "tools", "wf", "link")
    ok, why = link_maker(target_dir, link)
    if not ok:
        raise LinkUnavailable(why or "link not created")
    contract = _contract()

    def call() -> Any:
        return changeset.baseline(root, contract)

    def judge(b: Any) -> tuple[bool, list[str]]:
        rel = [k for k in b["entries"] if k.startswith("tools/wf/link")]
        lim = [x for x in b["limitations"] if x == "skipped link or reparse point: tools/wf/link"]
        out = [f"problems={b['problems']}", f"entries_under_link={rel}", f"limitation_hits={lim}",
               f"real_hashed={'tools/wf/real.py' in b['entries']}"]
        return bool(lim) and not rel and not b["problems"] and "tools/wf/real.py" in b["entries"], out

    return call, judge, link


def surface_export(case_dir: str, link_maker: Callable[[str, str], tuple[bool, str | None]], target: str
                   ) -> tuple[Callable[[], Any], Callable[[Any], tuple[bool, list[str]]], str]:
    """Link placed inside jobs/j/export next to the manifest; export.check must report it as a link, not open it."""
    from wfcore import export, paths
    db, _ = make_ledger(case_dir)
    out = paths.export_dir(db, "j")
    link = os.path.join(out, "link-entry")
    ok, why = link_maker(target, link)
    if not ok:
        raise LinkUnavailable(why or "link not created")

    def call() -> Any:
        return export.check(db, "j")

    def judge(problems: Any) -> tuple[bool, list[str]]:
        hits = [p for p in problems if p == "unexpected export link: link-entry"]
        others = [p for p in problems if p not in hits]
        return bool(hits) and not others, [f"problems={problems}"]

    return call, judge, link


def surface_ledgerdir(case_dir: str, link_maker: Callable[[str, str], tuple[bool, str | None]], target: str
                      ) -> tuple[Callable[[], Any], Callable[[Any], tuple[bool, list[str]]], str]:
    """Link placed beside the ledger database; verify's ledger_directory must name it and keep neighbors empty."""
    from wfcore import commands, paths
    db, _ = make_ledger(case_dir)
    link = os.path.join(paths.ledger_dir(db), "link.db")
    ok, why = link_maker(target, link)
    if not ok:
        raise LinkUnavailable(why or "link not created")

    def call() -> Any:
        return commands.verify(db, "j")

    def judge(v: Any) -> tuple[bool, list[str]]:
        ld = v.get("ledger_directory", {})
        probs = ld.get("problems", [])
        named = [p for p in probs if p.startswith("unexpected ledger-directory file: link.db")]
        out = [f"ledger_directory={ld}", f"clean={v.get('clean')}", f"job_problems={v['jobs']['j']['problems']}"]
        return len(named) == 1 and len(probs) == 1 and ld.get("neighbors") == [] and v.get("clean") is False, out

    return call, judge, link


class LinkUnavailable(Exception):
    pass


SURFACES = {"baseline": surface_baseline, "export": surface_export, "ledgerdir": surface_ledgerdir}
KINDS = {
    "linux-symlink": {"os": "posix", "repeat": SYMLINK_COMMAND},
    "windows-symlink": {"os": "win32", "repeat": SYMLINK_COMMAND},
    "windows-junction": {"os": "win32", "repeat": JUNCTION_COMMAND},
}


def link_maker_for(kind: str, target_is_dir: bool) -> Callable[[str, str], tuple[bool, str | None]]:
    if kind == "windows-junction":
        return lambda src, dst: try_junction(src, dst)
    return lambda src, dst: try_symlink(src, dst, target_is_dir)


# ---------------------------------------------------------------- cases

def run_case(scratch: str, watch: Watch, kind: str, surface: str) -> dict[str, Any]:
    case_id = f"{kind}/{surface}"
    rec: dict[str, Any] = {"id": case_id, "kind": kind, "surface": surface, "repeat_command": KINDS[kind]["repeat"]}
    want_os = KINDS[kind]["os"]
    if (want_os == "win32") != WIN:
        rec.update(status="not-applicable", reason=f"host is {sys.platform}; {kind} needs {want_os}")
        return rec
    case_dir = os.path.join(scratch, kind, surface); os.makedirs(case_dir)
    # synthetic target: a directory with a sentinel file (directory for baseline/junction; file link for others)
    target_dir = os.path.join(case_dir, "external-target"); os.makedirs(target_dir)
    sentinel = os.path.join(target_dir, "secret.txt")
    planted = f"planted {case_id} {os.urandom(8).hex()}\n".encode()
    _write(sentinel, planted)
    dir_target = surface == "baseline" or kind == "windows-junction"
    target = target_dir if dir_target else sentinel
    rec["target"] = {"path": target, "is_directory": dir_target, "sentinel": sentinel}
    try:
        call, judge, link = SURFACES[surface](case_dir, link_maker_for(kind, dir_target), target)
    except LinkUnavailable as exc:
        rec.update(status="skip", reason=str(exc), skip_is_evidence=False)
        return rec
    rec["link"] = {"path": link, "lexists": os.path.lexists(link), "islink": os.path.islink(link),
                   "isjunction": os.path.isjunction(link) if hasattr(os.path, "isjunction") else None}
    before = snapshot(target_dir, sentinel)
    error = None
    with Canary(sentinel) as canary:
        try:
            result, obs = watch.run(target_dir, call)
        except Exception as exc:  # noqa: BLE001 — a crash is a finding, recorded verbatim
            result, obs = None, {"watched_events_total": watch.count, "target_events": list(watch.hits),
                                 "outside_scratch_events": list(watch.outside)}
            error = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    after = snapshot(target_dir, sentinel)
    rec["observation"] = {
        "audit_hook": obs, "canary_chmod_000_applied": canary.applied,
        "before": before, "after": after,
        "bytes_unchanged": before["sha256"] == after["sha256"] and before["size"] == after["size"],
        "identity_unchanged": (before["st_dev"], before["st_ino"], before["st_mtime_ns"]) ==
                              (after["st_dev"], after["st_ino"], after["st_mtime_ns"]),
        "listing_unchanged": before["listing"] == after["listing"],
        "link_still_present": os.path.lexists(link),
    }
    if error:
        rec.update(status="fail", reason=f"surface raised: {error}", product_output=None)
        return rec
    reported_ok, details = judge(result)
    not_traversed = not obs["target_events"]
    unchanged = rec["observation"]["bytes_unchanged"] and rec["observation"]["identity_unchanged"] and \
        rec["observation"]["listing_unchanged"]
    leaked = planted.decode().strip() in json.dumps(result, default=str)
    rec["product_output"] = details
    rec["checks"] = {"link_reported_appropriately": reported_ok, "target_not_accessed_by_python_file_ops": not_traversed,
                     "target_bytes_identity_listing_unchanged": unchanged, "sentinel_content_absent_from_output": not leaked,
                     "link_left_in_place": rec["observation"]["link_still_present"]}
    rec["status"] = "pass" if all(rec["checks"].values()) else "fail"
    rec["claim"] = ("the link was reported and the target was not opened, listed or changed by Python-level file "
                    "operations during the call (audit hook + canary + before/after identity); no claim about "
                    "kernel-level access or about another OS/filesystem")
    return rec


def control_case(scratch: str, watch: Watch) -> dict[str, Any]:
    """Negative control: a deliberate read-through of a link to the sentinel. The audit hook must record it and the
    POSIX canary must make it fail; otherwise the mechanism cannot support any "not accessed" statement."""
    d = os.path.join(scratch, "control"); os.makedirs(d)
    target_dir = os.path.join(d, "external-target"); os.makedirs(target_dir)
    sentinel = os.path.join(target_dir, "secret.txt"); _write(sentinel, b"control\n")
    link = os.path.join(d, "link"); ok, why = try_symlink(sentinel, link, False)
    rec: dict[str, Any] = {"id": "control/read-through-link", "kind": "control", "surface": "mechanism"}
    if not ok:
        rec.update(status="skip", reason=why); return rec

    def read_it() -> str:
        with open(link, "rb") as fh:  # follows the link on purpose
            return fh.read().decode()

    with Canary(sentinel) as canary:
        try:
            result, obs = watch.run(target_dir, read_it)
            outcome = {"raised": None, "read": result}
        except OSError as exc:
            obs = {"watched_events_total": watch.count, "target_events": list(watch.hits),
                   "outside_scratch_events": list(watch.outside)}
            outcome = {"raised": type(exc).__name__, "read": None}
    detected = bool(obs["target_events"])
    canary_fired = outcome["raised"] is not None if canary.applied else None
    rec.update(status="pass" if detected and (canary_fired or canary_fired is None) else "fail",
               observation={"audit_hook": obs, "canary_chmod_000_applied": canary.applied, "canary_fired": canary_fired,
                            "outcome": outcome},
               claim="the hook records a Python-level open of the target reached through a link"
                     + ("; the canary blocks it" if canary_fired else ""))
    return rec


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(HERE, "results"), help="results directory")
    ap.add_argument("--scratch", default=None, help="fresh directory to create fixtures in (default: fixtures/tmp/fsq-<ts>)")
    ap.add_argument("--kinds", default=",".join(KINDS), help="comma list of link kinds to attempt")
    ap.add_argument("--surfaces", default=",".join(SURFACES), help="comma list of surfaces")
    args = ap.parse_args(argv)
    scratch = args.scratch or os.path.join(REPO, "fixtures", "tmp", "fsq-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
                                           + "-" + os.urandom(2).hex())
    scratch = os.path.abspath(scratch)  # link targets are absolute: a relative scratch would dangle
    if os.path.exists(scratch):
        print(f"refused: scratch {scratch} exists; pass a fresh --scratch", file=sys.stderr)
        return 2
    os.makedirs(scratch)
    os.environ["WF_TEST_TMP"] = scratch  # test_wf import convention; nothing else reads it here
    env = environment(scratch)
    cap = capability(scratch)
    watch = Watch(scratch)
    cases = [control_case(scratch, watch)]
    cases += [run_case(scratch, watch, k, s) for k in args.kinds.split(",") for s in args.surfaces.split(",")]
    summary = {st: sum(1 for c in cases if c["status"] == st) for st in ("pass", "fail", "skip", "not-applicable")}
    report = {"schema": SCHEMA_VERSION, "probe": os.path.relpath(__file__, REPO).replace("\\", "/"),
              "environment": env, "link_capability": cap, "cases": cases, "summary": summary,
              "qualification_scope": {
                  "evidence_for": sorted({c["kind"] for c in cases if c["status"] in ("pass", "fail") and c["kind"] != "control"}),
                  "not_tested": sorted({c["kind"] for c in cases if c["status"] in ("skip", "not-applicable")}),
                  "mechanism_control": cases[0]["status"],
                  "statement": "evidence applies only to the listed host/filesystem/revision; Linux output is not "
                               "Windows qualification and a skip or not-applicable entry is not evidence"}}
    os.makedirs(args.out, exist_ok=True)
    name = f"{sys.platform}-{env['revision']['head12'] or 'norev'}.json"
    path = os.path.join(args.out, name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, indent=1, sort_keys=True, default=str); fh.write("\n")
    print(f"revision {env['revision']['head12']} on {env['host']['platform']} fs={env['filesystem']['type']}")
    print(f"capability: symlink_file={cap['symlink_file']['created']} symlink_dir={cap['symlink_dir']['created']} "
          f"junction={cap['junction']['created']}")
    for c in cases:
        print(f"  {c['status']:<14} {c['id']:<28} {c.get('reason', '')}")
    print(f"summary {summary}  -> {os.path.relpath(path, REPO)}")
    return 1 if summary["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
