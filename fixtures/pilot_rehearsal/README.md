# Disposable Workforce CLI rehearsal (TEST FIXTURES; not a pilot)

Groundwork for the proposed supervised pilot (FIRST-RELEASE-SPEC.md section 2A, posture E1 + P-SUPERVISED, **not
authorized to run**). It drives the real `tools/wf/wf.py` through the workflow the current CLI supports on a fresh
synthetic boundary and judges outcomes. It launches no worker, installs no hook, injects no fault, exercises no
recovery, touches no real ledger and does not start P-SUPERVISED. Nothing here is an enforcement, identity or R1
acceptance claim.

## Run

```
python -I -B fixtures/pilot_rehearsal/rehearse.py --root fixtures/tmp/pilot_rehearsal-<stamp> --runs 2
python -I -B -m unittest discover -s fixtures/pilot_rehearsal -p "selftest_*.py" -v
```

`--root` must not exist; each run gets `<root>/run-<n>/boundary/` (ledger at `boundary/ledger/l.db`, so the
changed-set boundary is `boundary/`) and writes `<root>/run-<n>/transcript.json`: every command's argv, exit code
and JSON result, the exact `git rev-parse HEAD`, the interpreter version, all outcome checks and a verdict. Nothing
is deleted; `fixtures/tmp/` is git-ignored.

## What one run exercises (per job, in the order the CLI supports)

1. Baseline files materialised before job creation: `src/baseline.txt` (read_scope, hashed into the baseline) and
   `docs/present.md` (presence_only, never opened).
2. `job new` (coordinator session `RH-C1`): outcome COMMITTED, contract pin equals the canonical sha, baseline
   entries captured.
3. `grant propose` → block hash; `grant activate` with the hash prefix and a quote/turn labelled TEST FIXTURE.
4. Builder session `RH-B1`: `intent`, the write into `jobs/<job>/work/a.txt`, `receipt` (sha_after), `delivered`;
   `status` axes: execution `delivered`, acceptance `pending`, authority `in-force`.
5. Positive job `rhpos`: the builder session is refused as reviewer (same-session); reviewer `RH-R1` accepts with
   `contract_sha256` + `baseline` declarations and `changed_paths` → decision `accepted`, `a.txt` classified P1, no
   undeclared observed change; `verify` clean; `checkpoint`; `verify --checkpoint seq:self` clean; `digest` not
   closed; `close` COMMITTED; `digest` closed; an append after close is rejected; `verify` clean.
6. Negative job `rhneg`: same flow, the reviewer submits `AC1 pass:false` → decision `rejected` naming AC1,
   acceptance axis `rejected`, `close` refused, digest not closed, chain still clean. The accept command exits 0
   (a rejection is a recorded event): the rehearsal judges the decision, not the exit code.
7. Whole-ledger `verify` clean (ledger-directory listing included).

Two runs with distinct roots: 30 commands and 37 checks each on the current CLI.

## Reuse, not copies

Contract and grant shapes follow `tools/wf/tests/test_wf.py` (`CONTRACT`, `GRANT`, `DECLARATIONS`); the harness is
stdlib only and runs the CLI as a subprocess instead of importing the test harness, so it works outside the unit
suite and records exit codes as the CLI returns them.

## Pilot prerequisites the current CLI does not provide (exact list)

Observed on main `7711356` (tools/wf rev12). Each item is absent or explicitly out of scope today; none is
rehearsed here.

1. **Gate hook / enforcement (E1).** No `wf` command installs or exercises the pinned user-level gate; T13, T15,
   T17–T19, T25 are B3-stage (spec section 13). The rehearsal cannot deny anything.
2. **Session binding and identity.** `--session` is a declared string; `wf bind` and authenticated identity are not
   implemented (04-DECISIONS.md "bind, gate, pilot ... out of scope"). Same-session refusal compares declared ids only.
3. **Owner authorization.** `grant activate` accepts any `--owner-hash` prefix with any non-empty `--quote`/`--turn`;
   provenance is recorded, not verified. The rehearsal's confirmation is a labelled TEST FIXTURE.
4. **Worker launch and the attempt supervisor.** `wf` records events; nothing launches, contains or observes a
   worker (`wf observe` contact/recover deferred, T14/T22).
5. **Recovery.** `wf recover` (section 3B) and the T30 fixtures are not exercised; `resolve` exists for
   commit-uncertain markers only and needs fault injection under `fixtures/tmp` to reach.
6. **Backup / restore / restored-chain verification** (`wf backup`, D-023): missing-implementation.
7. **Replay of a scripted job end to end (B4)** and the **live pilot with staged interruption (B5)**: no fixture or
   command; B5 additionally needs the owner's pilot selection and stance contract (section 15.4).
8. **Export freshness after every commit** is reported, not guaranteed (a failed refresh is not retried); the
   rehearsal only observes a clean export at its verify points.
9. **Outside-boundary detection.** The changed set covers only the static prefixes of `read_scope`, `watch` and
   `presence_only` inside `boundary/`; links and junctions are skipped, not resolved.
10. **Concurrency under N workers** (T20, T27 beyond the unit fixtures) is not rehearsed; one builder, one reviewer.

Ownership of `fixtures/pilot_rehearsal/` is not registered in OWNERS.md; White registers it before integration.
