"""`wf observe` support (B2 slice 3, lane S3O; INTERFACE-CONTRACT-S3 sections 5 and 6, spec 4.1, 6.2 step 1, 7).

Read-only supervision: `classify` labels a job from recorded ledger rows only, and `append_log` keeps the bounded
observation log `jobs/<job>/observe/observe.log` (JSON lines, newest LOG_MAX_ENTRIES). Nothing here contacts,
resumes, launches, binds or recovers a worker, and nothing writes the ledger (owner item 4). The only file this
module writes is that log, through a staging file in the same directory and `os.replace`.

Imports are limited to the frozen set (section 6): stdlib and `.paths` (jobs dir). It must not import `.commands`,
`.txn`, `.bind`, `.reconcile` or `.grant`.
"""
from __future__ import annotations

import json
import math
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any

from . import paths

CLASSES: tuple[str, ...] = ("active", "slow", "silent", "blocked", "unknown")
DEFAULT_THRESHOLDS: dict[str, float] = {"slow_s": 600.0, "silent_s": 1800.0}
LOG_MAX_ENTRIES = 500
GATE_UNAVAILABLE: str = "gate evidence unavailable in this release: tool activity between events is not observed"

RECORDED_ONLY = "classification uses recorded events only; a session can be working without writing events"
SILENT_NOT_STOPPED = "silent does not establish that the process stopped"
# W-category execution events (section 5 "blocked" rule).
EXECUTION_TYPES: tuple[str, ...] = ("intent", "receipt", "handoff", "delivered", "blocker")
LOG_NAME = "observe.log"

# Same job-id rule as validate.check_job_id; repeated here because section 6 limits `.validate` to now_ts.
_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d{1,6})?Z$")
_DEFAULTS_TEXT = "slow_s=600, silent_s=1800"


# ---------------------------------------------------------------- thresholds

def _threshold_problem(src: Any) -> str | None:
    """Why `src` is not a usable {"slow_s", "silent_s"} pair, or None when it is."""
    if not isinstance(src, dict):
        return "not an object"
    for key in ("slow_s", "silent_s"):
        if key not in src:
            return f"{key} missing"
        v = src[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return f"{key} is not a number"
        if not math.isfinite(v) or v <= 0:
            return f"{key} must be a finite number above 0"
    if not src["slow_s"] < src["silent_s"]:
        return "slow_s must be below silent_s"
    return None


def thresholds(contract: dict) -> tuple[dict, list[str]]:
    """(thresholds, uncertainty notes) from the contract's `observe_thresholds`. Absent or invalid values fall back
    to DEFAULT_THRESHOLDS, and the note names the defaults. Keys other than slow_s/silent_s are ignored."""
    src = contract.get("observe_thresholds") if isinstance(contract, dict) else None
    if src is None:
        return dict(DEFAULT_THRESHOLDS), [f"observe_thresholds absent from the contract; defaults used: {_DEFAULTS_TEXT}"]
    problem = _threshold_problem(src)
    if problem is not None:
        return dict(DEFAULT_THRESHOLDS), [f"observe_thresholds invalid ({problem}); defaults used: {_DEFAULTS_TEXT}"]
    return {"slow_s": float(src["slow_s"]), "silent_s": float(src["silent_s"])}, []


# ---------------------------------------------------------------- classification

def _parse_ts(value: Any) -> datetime | None:
    """`YYYY-MM-DDTHH:MM:SSZ` with optional fractional seconds (ledger rows carry milliseconds) -> aware UTC."""
    if not isinstance(value, str):
        return None
    m = _TS.match(value)
    if not m:
        return None
    try:
        base = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    frac = m.group(2)
    if frac:
        base = base.replace(microsecond=int(frac[1:].ljust(6, "0")))
    return base


def _evidence(row: dict) -> dict:
    return {"seq": row.get("seq"), "type": row.get("type"), "ts": row.get("ts"),
            "role": row.get("author_role"), "session": row.get("session_id")}


def _latest(rows: list[dict], pred: Any) -> dict | None:
    best = None
    for r in rows:
        if pred(r) and isinstance(r.get("seq"), int) and (best is None or r["seq"] > best["seq"]):
            best = r
    return best


def _fmt_s(v: float) -> str:
    return f"{v:g}"


def classify(rows: list[dict], *, now: str, thresholds: dict) -> dict:
    """Section 5 rules, from recorded rows only:
    `blocked` when the latest execution event (intent/receipt/handoff/delivered/blocker) is a `blocker`; else
    `unknown` when no builder-authored event exists; else by the age of the latest builder event: below slow_s
    `active`, below silent_s `slow`, otherwise `silent`. `thresholds` that are not a valid pair fall back to the
    defaults with a note. The gate statement and the recorded-events caveat are always present."""
    uncertainty = [RECORDED_ONLY]
    problem = _threshold_problem(thresholds)
    if problem is None:
        th = {"slow_s": float(thresholds["slow_s"]), "silent_s": float(thresholds["silent_s"])}
    else:
        th = dict(DEFAULT_THRESHOLDS)
        uncertainty.append(f"thresholds invalid ({problem}); defaults used: {_DEFAULTS_TEXT}")

    def result(cls: str, evidence: list[dict], basis: str) -> dict:
        return {"class": cls, "evidence": evidence, "basis": basis, "uncertainty": uncertainty,
                "gate_log": {"available": False, "statement": GATE_UNAVAILABLE}, "thresholds": th, "now": now}

    execution = _latest(rows, lambda r: r.get("type") in EXECUTION_TYPES)
    if execution is not None and execution.get("type") == "blocker":
        return result("blocked", [_evidence(execution)],
                      f"the latest execution event (seq {execution['seq']}) is a blocker")

    builder = _latest(rows, lambda r: r.get("author_role") == "builder")
    if builder is None:
        return result("unknown", [], "no event authored by the builder role is recorded")

    ev = [_evidence(builder)]
    now_dt, at = _parse_ts(now), _parse_ts(builder.get("ts"))
    if now_dt is None:
        uncertainty.append(f"now is not a UTC instant in YYYY-MM-DDTHH:MM:SSZ form: {now!r}")
        return result("unknown", ev, "the age of the latest builder event cannot be computed")
    if at is None:
        uncertainty.append(f"the latest builder event (seq {builder['seq']}) has an unreadable timestamp")
        return result("unknown", ev, "the age of the latest builder event cannot be computed")

    age = (now_dt - at).total_seconds()
    if age < -1.0:  # `now` is truncated to seconds and rows carry milliseconds, so up to 1 s early is expected
        uncertainty.append(f"the latest builder event is {_fmt_s(-age)} s later than now; its age is taken as 0")
    age = max(age, 0.0)
    where = (f"the latest builder event (seq {builder['seq']}, {builder.get('type')}) is {_fmt_s(age)} s old; "
             f"active below {_fmt_s(th['slow_s'])} s, slow below {_fmt_s(th['silent_s'])} s, silent from there")
    if age < th["slow_s"]:
        return result("active", ev, where)
    if age < th["silent_s"]:
        return result("slow", ev, where)
    uncertainty.append(SILENT_NOT_STOPPED)
    return result("silent", ev, where)


# ---------------------------------------------------------------- bounded observation log

def _check_job(job: str) -> None:
    if not isinstance(job, str) or not _JOB_ID.match(job):
        raise ValueError(f"invalid job id for the observation log: {job!r}")


def log_path(db_path: str, job: str) -> str:
    """`jobs/<job>/observe/observe.log` under the boundary root (`paths.jobs_dir`)."""
    _check_job(job)
    return os.path.join(paths.jobs_dir(db_path), job, "observe", LOG_NAME)


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _is_link(p: str) -> bool:
    isjunction = getattr(os.path, "isjunction", None)
    return os.path.islink(p) or bool(isjunction and isjunction(p))


def _contained(db_path: str, job: str) -> None:
    """Refuse to write when `jobs/<job>` or `jobs/<job>/observe` is a link or junction, or resolves anywhere other
    than its own place under the jobs dir: the log is never written outside `jobs/<job>/observe/`."""
    root = os.path.realpath(paths.jobs_dir(db_path))
    for rel in (job, os.path.join(job, "observe")):
        p = os.path.join(paths.jobs_dir(db_path), rel)
        if not os.path.lexists(p):
            return
        if _is_link(p) or not os.path.isdir(p) or not _same(p, os.path.join(root, rel)):
            raise ValueError(f"observation log refused: {p} is not a plain directory under the jobs dir")


def append_log(db_path: str, job: str, entry: dict) -> str:
    """Append one JSON line and keep the newest LOG_MAX_ENTRIES lines. The new content is staged in
    `observe.log.<pid>-<hex8>.tmp` in the same directory (exclusive create, fsync) and moved over the log with
    `os.replace`; if that fails, this writer removes its own staging file and re-raises. Returns the log path.
    Writes nothing else: no ledger row, no other file."""
    if not isinstance(entry, dict):
        raise TypeError("observation log entry must be a dict")
    line = json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    path = log_path(db_path, job)
    _contained(db_path, job)
    if os.path.lexists(path) and (_is_link(path) or not os.path.isfile(path)):
        raise ValueError(f"observation log refused: {path} is not a regular file")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _contained(db_path, job)

    kept: list[str] = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            kept = [ln for ln in fh.read().splitlines() if ln.strip()]
    kept.append(line)
    kept = kept[-LOG_MAX_ENTRIES:]

    tmp = f"{path}.{os.getpid()}-{secrets.token_hex(4)}.tmp"
    try:
        with open(tmp, "x", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(kept) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            if os.path.lexists(tmp):
                os.unlink(tmp)
        except OSError:
            pass  # best effort: the staging file stays and is visible in a directory listing
        raise
    return path
