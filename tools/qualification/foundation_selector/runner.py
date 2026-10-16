"""Foundation verification selector (prep, Plexy 2026-10-16): run exactly the tests mapping.json names, per
FIRST-RELEASE-SPEC section 8 requirement, and write structured results that never overstate.

    python -I -B tools/qualification/foundation_selector/runner.py [--mapping M] [--tests-dir D] [--out-root R]
                                                                    [--validate-only] [--requirement T35 ...]

Creates `<out-root>/<UTC>-<head>/` (default out-root: `tools/qualification/foundation_selector/runs/`, refused if
the directory exists) holding `validation.json`, `tests-output.txt`, `child-results.json`, `results.json`.
A fresh `WF_TEST_TMP` (`fixtures/tmp/selector-<UTC>-<rand>`) and `WF_TEST_LOGDIR` (inside the run dir) are set for
the child, so no shared fixture is touched.

Result rules (results.json):
  per test      ok | FAIL | ERROR | skipped | expected failure | unexpected success | not-run
  per requirement verdict
    PASS         status mapped, every named test ok
    PASS-PARTIAL status partial, every named test ok (missing assertions remain listed; never "complete")
    FAIL         any named test FAIL / ERROR / unexpected success / not-run
    INCOMPLETE   no FAIL but at least one named test skipped or expected-failure (a skip is not a pass)
    MISSING      status missing (no test exists; the missing assertion and smallest test are listed)
    OUT-OF-LAYER status out-of-layer (gate / procedural; not verifiable in this repository)
  runner result
    FAIL         validation failed, empty selection, child did not finish, or any requirement FAIL
    INCOMPLETE   no FAIL but any INCOMPLETE / MISSING / PASS-PARTIAL requirement, or any mapped test not executed
    PASS         every requirement PASS   (not reachable while any requirement is missing or partial)
Exit code: 0 PASS, 1 FAIL, 3 INCOMPLETE. `complete` is true only on PASS. Empty selection is FAIL, never PASS.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import secrets
import subprocess
import sys
from collections.abc import Mapping, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)
from validate import Validation, load_mapping, validate  # noqa: E402

DEFAULT_MAPPING = os.path.join(HERE, "mapping.json")
DEFAULT_TESTS = os.path.join(REPO, "tools", "wf", "tests")
DEFAULT_OUT = os.path.join(HERE, "runs")
CHILD = os.path.join(HERE, "child.py")
CHILD_TIMEOUT_S = 3600

PASSING = {"ok"}
FAILING = {"FAIL", "ERROR", "unexpected success", "not-run"}
INCOMPLETE = {"skipped", "expected failure"}


def utc() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def git(*args: str) -> str:
    try:
        p = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=False, timeout=60)
        return p.stdout.strip() if p.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def baseline_info() -> dict:
    head = git("rev-parse", "HEAD")
    dirty = git("status", "--porcelain", "--untracked-files=no")
    return {"head": head or None, "branch": git("branch", "--show-current") or None,
            "dirty_tracked_files": [ln[3:] for ln in dirty.splitlines()] if dirty else [],
            "python": sys.version.split()[0], "platform": sys.platform}


def requirement_verdict(status: str, outcomes: Mapping[str, str], tests: Sequence[str]) -> str:
    if status == "out-of-layer":
        return "OUT-OF-LAYER"
    if status == "missing":
        return "MISSING"
    got = [outcomes.get(t, "not-run") for t in tests]
    if not got or any(g in FAILING for g in got):
        return "FAIL"
    if any(g in INCOMPLETE for g in got):
        return "INCOMPLETE"
    return "PASS" if status == "mapped" else "PASS-PARTIAL"


def run_child(tests_dir: str, names: Sequence[str], run_dir: str, env: Mapping[str, str]) -> tuple[int, dict]:
    out = os.path.join(run_dir, "child-results.json")
    # -X utf8: the verbose unittest stream carries test docstrings (arrows, dashes); a cp1252 console would abort
    # the child mid-run and the run would be recorded as "child did not finish" (observed on the first run).
    cmd = [sys.executable, "-I", "-B", "-X", "utf8", CHILD, "--tests-dir", tests_dir, "--out", out, "--names", *names]
    with open(os.path.join(run_dir, "command.txt"), "x", encoding="utf-8") as fh:
        json.dump({"argv": cmd, "env_set": sorted(k for k in env if k.startswith("WF_"))}, fh, indent=1)
    try:
        p = subprocess.run(cmd, cwd=REPO, env=dict(env), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=CHILD_TIMEOUT_S, check=False)
        rc, text = p.returncode, p.stdout + ("\n--- stderr ---\n" + p.stderr if p.stderr else "")
    except subprocess.TimeoutExpired as exc:
        rc, text = 124, (exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")) \
            + f"\nTIMEOUT after {CHILD_TIMEOUT_S}s\n"
    with open(os.path.join(run_dir, "tests-output.txt"), "x", encoding="utf-8") as fh:
        fh.write(text)
    child: dict = {}
    if os.path.isfile(out):
        with open(out, encoding="utf-8") as fh:
            child = json.load(fh)
    return rc, child


def assemble(mapping: dict, val: Validation, child: dict, child_rc: int, selection: Sequence[str]) -> dict:
    outcomes: dict[str, str] = dict(child.get("outcomes", {})) if child else {}
    for t in selection:
        outcomes.setdefault(t, "not-run")
    per_req: dict[str, dict] = {}
    for rid, row in mapping["requirements"].items():
        tests = list(row.get("tests", []))
        verdict = requirement_verdict(row["status"], outcomes, tests)
        per_req[rid] = {"status": row["status"], "verdict": verdict, "layer": row.get("layer"),
                        "spec_line": row.get("spec_line"), "tests": {t: outcomes.get(t, "not-run") for t in tests},
                        "missing": row.get("missing", []), "sources": row.get("sources", [])}
    counts = {"selected": len(selection), "executed": sum(1 for t in selection if outcomes.get(t) not in (None, "not-run")),
              "ok": sum(1 for t in selection if outcomes.get(t) == "ok"),
              "fail_or_error": sum(1 for t in selection if outcomes.get(t) in FAILING),
              "skipped_or_expected_failure": sum(1 for t in selection if outcomes.get(t) in INCOMPLETE)}
    verdicts = [r["verdict"] for r in per_req.values()]
    if not val.ok:
        result = "FAIL"; why = "mapping validation failed"
    elif not selection:
        result = "FAIL"; why = "empty selection is not completion"
    elif not child or child_rc == 124:
        result = "FAIL"; why = "child did not finish"
    elif any(v == "FAIL" for v in verdicts):
        result = "FAIL"; why = "a requirement has a failing, erroring or not-run test"
    elif counts["executed"] != counts["selected"]:
        result = "FAIL"; why = "a selected test was not executed"
    elif any(v in ("INCOMPLETE", "MISSING", "PASS-PARTIAL") for v in verdicts):
        result = "INCOMPLETE"; why = "skipped tests, missing requirements or partial mappings remain"
    else:
        result = "PASS"; why = "every requirement PASS"
    return {"label": "foundation-verification selector (prep); not acceptance, not a gate result",
            "result": result, "complete": result == "PASS", "why": why, "counts": counts,
            "requirement_verdicts": {v: sorted(k for k, r in per_req.items() if r["verdict"] == v)
                                     for v in ("PASS", "PASS-PARTIAL", "FAIL", "INCOMPLETE", "MISSING", "OUT-OF-LAYER")},
            "requirements": per_req, "tests": dict(sorted(outcomes.items())), "validation_errors": list(val.errors)}


def main(argv: Sequence[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mapping", default=DEFAULT_MAPPING)
    ap.add_argument("--tests-dir", default=DEFAULT_TESTS)
    ap.add_argument("--out-root", default=DEFAULT_OUT)
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--requirement", action="append", default=[], help="restrict the selection to these ids")
    a = ap.parse_args(list(argv))
    mapping = load_mapping(a.mapping)
    if a.requirement:
        unknown = [r for r in a.requirement if r not in mapping.get("requirements", {})]
        if unknown:
            print(f"FAIL: unknown requirement id(s): {unknown}")
            return 1
        mapping = {**mapping, "requirements": {k: v for k, v in mapping["requirements"].items() if k in a.requirement}}
    val = validate(mapping, a.tests_dir)
    base = baseline_info()
    stamp = utc()
    run_dir = os.path.join(a.out_root, f"{stamp}-{(base['head'] or 'nohead')[:12]}-{secrets.token_hex(2)}")
    os.makedirs(run_dir, exist_ok=False)
    with open(os.path.join(run_dir, "validation.json"), "x", encoding="utf-8") as fh:
        json.dump({"ok": val.ok, "errors": list(val.errors), "selection": list(val.selection), "baseline": base,
                   "mapping_sha256": _sha256(a.mapping)}, fh, indent=1, sort_keys=True)
    if not val.ok:
        doc = assemble(mapping, val, {}, 1, val.selection)
        _write(run_dir, doc, base)
        print("FAIL: mapping validation errors:\n  " + "\n  ".join(val.errors))
        return 1
    if a.validate_only:
        print(f"validation OK: {len(val.selection)} tests selected; run dir {run_dir}")
        return 0
    env = {k: v for k, v in os.environ.items() if k.upper() not in ("WF_TEST_TMP", "WF_TEST_LOGDIR", "WF_TEST_FAULTS")}
    tmp = os.path.join(REPO, "fixtures", "tmp", f"selector-{stamp}-{secrets.token_hex(3)}")
    env["WF_TEST_TMP"] = tmp
    env["WF_TEST_LOGDIR"] = os.path.join(run_dir, "logs")
    rc, child = (1, {}) if not val.selection else run_child(a.tests_dir, val.selection, run_dir, env)
    doc = assemble(mapping, val, child, rc, val.selection)
    doc["child_exit_code"] = rc
    doc["wf_test_tmp"] = tmp
    _write(run_dir, doc, base)
    c = doc["counts"]
    print(f"{doc['result']}: {doc['why']}; selected {c['selected']}, executed {c['executed']}, ok {c['ok']}, "
          f"fail/error {c['fail_or_error']}, skipped/expected-failure {c['skipped_or_expected_failure']}")
    for v, ids in doc["requirement_verdicts"].items():
        if ids:
            print(f"  {v}: {', '.join(ids)}")
    print(f"run dir: {run_dir}")
    return {"PASS": 0, "FAIL": 1}.get(doc["result"], 3)


def _sha256(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def _write(run_dir: str, doc: dict, base: dict) -> None:
    doc["baseline"] = base
    doc["finished_utc"] = utc()
    with open(os.path.join(run_dir, "results.json"), "x", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
