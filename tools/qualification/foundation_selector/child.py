"""Child test process: loads the named test ids from `--tests-dir` and records one outcome per test id.

Run only by runner.py as `python -I -B child.py --tests-dir D --out results.json --names id...`.
Outcomes: ok | FAIL | ERROR | skipped | expected failure | unexpected success | not-run (named but never
started, e.g. a load error). Exit 0 only when every named test ran and passed; a load error of any name is an
ERROR for that name. The verbose unittest text goes to stdout (captured by the runner into tests-output.txt).
"""
from __future__ import annotations

import argparse
import json
import sys
import unittest


class Recording(unittest.TextTestResult):
    _RANK = {"ok": 0, "skipped": 1, "expected failure": 1, "unexpected success": 2, "FAIL": 3, "ERROR": 4}

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.outcomes: dict[str, str] = {}

    def _set(self, test, outcome):
        tid = test.id()
        prev = self.outcomes.get(tid)
        if prev is None or self._RANK[outcome] > self._RANK[prev]:
            self.outcomes[tid] = outcome

    def addSuccess(self, test):
        super().addSuccess(test); self._set(test, "ok")

    def addFailure(self, test, err):
        super().addFailure(test, err); self._set(test, "FAIL")

    def addError(self, test, err):
        super().addError(test, err); self._set(test, "ERROR")

    def addSkip(self, test, reason):
        super().addSkip(test, reason); self._set(test, "skipped")

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err); self._set(test, "expected failure")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test); self._set(test, "unexpected success")

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None and err[0] is not None:
            self._set(test, "FAIL" if issubclass(err[0], test.failureException) else "ERROR")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tests-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--names", nargs="+", required=True)
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # docstrings in the verbose stream are not ASCII
    except (AttributeError, ValueError):
        pass
    sys.path.insert(0, a.tests_dir)
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    load_errors: dict[str, str] = {}
    for name in a.names:
        try:
            suite.addTests(loader.loadTestsFromName(name))
        except Exception as exc:  # noqa: BLE001 - every load failure is recorded against its name
            load_errors[name] = f"{type(exc).__name__}: {exc}"
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2, resultclass=Recording)
    result = runner.run(suite)
    outcomes = dict(getattr(result, "outcomes", {}))
    for name in a.names:
        if name not in outcomes:
            outcomes[name] = "ERROR" if name in load_errors else "not-run"
    doc = {"names": list(a.names), "tests_run": result.testsRun, "outcomes": dict(sorted(outcomes.items())),
           "load_errors": load_errors, "failures": len(result.failures), "errors": len(result.errors),
           "skipped": len(result.skipped), "expected_failures": len(result.expectedFailures),
           "unexpected_successes": len(result.unexpectedSuccesses)}
    with open(a.out, "x", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
    ok = result.wasSuccessful() and not load_errors and all(v == "ok" for v in outcomes.values())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
