# Foundation verification selector (prep; not acceptance, not a gate)

Prepared by Plexy, 2026-10-16, on `prep/plexy-foundation-verification` from main `7711356b37a79b89e92afbd0d02359e938aff3f2`
(code `48e46f2`, tools/wf rev12 `22e71ebc5178`). Namespace `tools/qualification/foundation_selector/` only; nothing
else in the repository is touched. White owns acceptance, ownership registration and integration.

## What it does

`mapping.json` maps every FIRST-RELEASE-SPEC section 8 acceptance case (T01-T37, lines 276-312) to the exact
existing test ids that assert its expected outcome, with the source of each mapping (spec line, the published
coverage record `fixtures/results/t09-closure-9d2210ee082a/coverage-map-t09-closure.md`, test file:line, decision
ids). A test is mapped only where its docstring or assertions name the requirement's expected outcome; a name match
alone never mapped anything. Each row has one status:

| status | meaning | runner verdict when its tests all pass |
|---|---|---|
| `mapped` | every expected element of the row is asserted by the named tests | PASS |
| `partial` | some element is unasserted; each one is listed under `missing` with the precise missing assertion and the smallest proposed test | PASS-PARTIAL (never complete) |
| `missing` | no mechanism exists in the product; the missing assertion and smallest test are listed | MISSING |
| `out-of-layer` | gate (E/I) or procedural: not verifiable by `wf` tests in this repository | OUT-OF-LAYER |

`runner.py` validates the mapping statically (every id must exist as a `test*` method in `tools/wf/tests`, no
duplicates, no empty mapped rows, every partial/missing row names its gap), then runs exactly the selected ids in
one child process (`child.py`, `python -I -B -X utf8`, fresh `WF_TEST_TMP` under `fixtures/tmp/selector-*`, logs
inside the run dir) and writes `results.json` with per-test outcomes, per-requirement verdicts and one runner result:

- a skipped or expected-failure test is never a pass (requirement INCOMPLETE);
- a failing, erroring, unexpectedly succeeding or not-run test fails the requirement and the runner;
- an empty selection, a validation error or a child that does not finish is a FAIL;
- `complete` is true only when every requirement is PASS, which cannot happen while any row is partial or missing.

Exit codes: 0 PASS, 1 FAIL, 3 INCOMPLETE.

```
python -I -B tools/qualification/foundation_selector/runner.py                 # full recorded run
python -I -B tools/qualification/foundation_selector/runner.py --validate-only
python -I -B tools/qualification/foundation_selector/runner.py --requirement T35 --requirement T36
python -I -B -m unittest discover -s tools/qualification/foundation_selector -p "selftest_*.py" -v
```

## Mapping summary (main 7711356)

37 rows; 210 unique test ids selected (235 references; a test backing several rows is run once).

| verdict class | rows |
|---|---|
| mapped (PASS when green) | T01 T02 T03 T04 T05 T08 T09 T10 T21 T23 T24 T26 T27 T28 T31 |
| partial (PASS-PARTIAL) | T18 (b only; gate half out of layer) · T19 (locked/tampered asserted; corrupt file, unexpected identity, open recovery not) · T20 (close rules asserted; "nothing delivered" and no-evidence refusals not asserted by name) · T29 (direct activate / wrong hash asserted; contract-amended void not) · T35 (P3 separate authorization owner-limited; links not resolved; 8.3 names environment) · T36 ((d)/(e) retained-copy property procedural) · T37 (export asserted; backup/restore absent) |
| missing (no mechanism) | T06 reconcile · T07 doorbell · T14 observe · T16 recovery twice · T22 observe · T30 recovery · T32 bind/ARS · T33 replacement · T34 invalidation (expiry stored, not enforced) |
| out-of-layer (gate / procedural) | T11 T12 T13 T15 T17 T25 |

The precise missing assertion and smallest proposed test for every partial and missing row are in `mapping.json`
under `missing`; the run's `results.json` repeats them next to the verdict so absent evidence stays visible.

## Recorded baseline run

`runs/20261016T033701Z-7711356b37a7-1346/` (Windows 11 Pro 10.0.26300, Python 3.14.6, dirty tracked files: none):
selected 210, executed 210, ok 210, fail/error 0, skipped/expected-failure 0 → runner result **INCOMPLETE**
(15 PASS, 7 PASS-PARTIAL, 9 MISSING, 6 OUT-OF-LAYER). `complete: false`. The earlier directory
`runs/20261016T033519Z-7711356b37a7/` is the first attempt, recorded FAIL "child did not finish": the child aborted on a
cp1252 console encoding while printing a docstring; the fix (`-X utf8` + stdout reconfigure) is in this revision and
the failed run is kept as evidence of the fail-closed path.

## Limits

- The mapping is a reading of docstrings and assertions at main 7711356; it does not re-derive what each test proves
  (that is the published coverage record's job) and it claims no coverage word. White's coverage record of
  2026-10-13 predates lanes A-C, B2/B3, C2/C3 and the A1-A3 repairs; rows whose rating moved (T09, T24, T27, T31,
  T35, T36) carry the newer tests with file:line sources and the older rating cited for contrast.
- `child.py` runs the selected tests in one process; tests that spawn `wf` subprocesses keep doing so (their own
  design). Timing-bound tests (T27/T28 families) remain host-sensitive; a timing failure is reported as FAIL, never
  retried or masked.
- No production module, shared fixture, normal discovery pattern, governing document, OWNERS or MANIFEST is changed.
  `selftest_*.py` is outside `tools/wf/tests` and outside the `test_*.py` pattern.
