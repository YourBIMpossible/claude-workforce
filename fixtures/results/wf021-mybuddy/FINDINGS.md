# WF-021 findings (MyBuddy, Ubuntu, tools/wf at 2dedc94)

Row numbers refer to `PLATFORM-REVIEW.md` in this folder. "Re-verified" means MyBuddy ran the probe or test itself on scratch fixtures. Each finding has exactly one disposition.

| # | finding | platform | evidence | disposition |
|---|---|---|---|---|
| F1 | Row 16: a scope glob with upper case (e.g. `Docs/**`) is looked up lower-cased. The real directory is never walked, and the baseline reports no problem and no limitation. Changes there go undetected (fail-open). | Linux | re-verified; `test_wf021_case.py::CaseOnLinux::test_row16_observed_upper_case_scope_glob_walks_nothing_and_reports_nothing` | **PROPOSED** (PLATFORM-REVIEW.md row 16): walk the on-disk name, or report a problem when a scope prefix does not exist as written |
| F2 | Row 17: reconcile lower-cases recorded paths and hashes the lower-cased path. `New.txt` gets `sha_now = None` while the file exists (fail-open in classification). | Linux | re-verified; `test_wf021_case.py::...::test_row17_observed_reconcile_lower_cases_paths_and_cannot_hash_upper_case_files` | **PROPOSED** (row 17): keep the on-disk form for I/O and fold only for comparison |
| F3 | Row 3: the drive-less rooted Windows path `\jobs\j\work\a.txt` resolves in-root. On Windows it means `C:\jobs\…`, which is outside the boundary, so a bound session's write there is allowed where `out-of-scope` is due. | Windows (string-level, re-verified on Linux) | `test_wf021_paths.py::GatePathStrings::test_row3_observed_driveless_rooted_windows_path_resolves_inside_the_root` | **PROPOSED** (row 3): treat a leading `\` without a drive as rooted on the cwd's drive, or as unresolvable |
| F4 | Row 20: `grant.check` exclusions use case-sensitive `fnmatchcase`. `jobs/j/work/SECRET/…` passes the exclusion `jobs/j/work/secret/**`. On NTFS this is the same directory. | Windows | re-verified (glob level); `test_wf021_case.py::...::test_row20_grant_globs_are_case_sensitive_on_every_platform` | **PROPOSED** (row 20): fold case for exclusions on case-insensitive volumes (deny side only) |
| F5 | Row 19: pathpolicy classification folds case, so `JOBS/J/WORK/a.txt`, a different directory on ext4, is classified P1 allowed. | Linux | re-verified; `test_wf021_case.py::...::test_row19_observed_case_variant_directory_classified_P1_allowed` | **NEEDS OWNER**: whether the NTFS-oriented folding is the intended policy on a case-sensitive host |
| F6 | Row 43: a non-UTF-8 file name in a hashed scope is walked as a surrogate key with no problem reported. Hashing then raises UnicodeEncodeError, and `wf job new` ends in a traceback instead of a structured refusal. | Linux | re-verified; `test_wf021_fs_semantics.py::...::test_row43_observed_non_utf8_file_name_reaches_wf_job_new_as_an_uncaught_unicode_error` | **PROPOSED** (row 43): report such names as baseline problems (fail closed with a reason) |
| F7 | Step-1 failure: `test_gate_decide.py::PathsAndFailClosed::test_target_paths` fails on Linux. It passes Windows backslashes in a relative path; on POSIX that is one file name and the gate correctly fails closed. | Linux | step1-suite.txt, step7-suite.txt; `test_wf021_paths.py::...::test_row1_...` | **NOT A DEFECT** in the product. The test needs a per-platform split, which is outside MyBuddy's lane (file owned by S4G/S4bW) |
| F8 | Row 32: Windows name rules apply on Linux. `aux.c` and names containing `:` are refused by baseline and gate (availability cost, fail closed). | Linux | re-verified; `test_wf021_paths.py::WindowsNameRulesOnLinux::test_row32_...` | **NEEDS OWNER**: whether portable-name rules should apply on Linux hosts |
| F9 | Row 74: the backup file gets mode 0o777 & ~umask, so it has execute bits and is world-readable under a 022 umask. | Linux | re-verified; `test_wf021_fs_semantics.py::...::test_row74_...` (0o755 under umask 022) | **PROPOSED** (row 74): create the backup with mode 0o600 |
| F10 | Row 23: gate files are compared by `normcase(abspath)` without `realpath`, so an alias of the hook path (a symlink on Linux, an 8.3 name or junction on Windows) escapes protected-path rule 1. | both | review reading, **not re-verified** | **PROPOSED** (row 23): compare `realpath` forms |
| F11 | Row 27: job ids differing only in case (or by a trailing `.`) share directories on Windows. | Windows | review reading, **not re-verified** | **PROPOSED** (row 27): reject case-insensitive duplicates and trailing dots in job ids |
| F12 | Row 80: the print-install snippet says `python -I <path>`. Ubuntu ships `python3`, and a path with a space is not quoted. | Linux | review reading, **not re-verified** via the CLI | **PROPOSED** (row 80): use `sys.executable` or the configured interpreter, quoted |
| F13 | Step 6: busy give-up on Linux is 5.000 s internal and 5.045–5.077 s process wall time, against the reported Windows figure of ≈7.4 s. | both | step6-busy.json | **NOT A DEFECT** on Linux (meets the bound). The Windows gap is recorded, not explained, because the Windows method was not in my inputs |
| F14 | Step 4: 20,906 attempts, 1 rejection (lock-timeout, 0.005 %), 0 lost, 0 duplicated, chains valid. | Linux | step4-concurrency.json | **NOT A DEFECT** |
| F15 | Step 5: 30 kills (29 actual), 0 corrupt, every resolve agreed with the ledger. Two export staging files preserved by mid-refresh kills stay reported until removed by the owner. | Linux | step5-interrupt.json | **NOT A DEFECT** (designed: preserved, never deleted) |

Not given a Linux test, with reasons: rows 48/50 (would need monkeypatching inside product modules; left as review findings), 15/27/44/45 (Windows-only behaviour), 80 (host-dependent).

## Addendum — WF-021 review F1 (Lazy WF-038), re-based on `fix1/integration` dfd6e85 (branch `wf021b/mybuddy`)

The table above is the pre-FIX1 record (base 2dedc94; branch `wf021/mybuddy` 88ea43f). Status after FIX1, checked by the
re-based tests in `test_wf021_*.py` at this branch (Ubuntu), with Lazy's WF-038 check 5:

| # | status after FIX1 (dfd6e85) | evidence on this branch |
|---|---|---|
| F1 | fixed (P1) | `test_row16_upper_case_scope_glob_walks_the_directory_as_written` (Linux) |
| F2 | fixed (P1) | `test_row17_reconcile_keeps_the_written_case_and_hashes_the_file` (Linux) |
| F3 | fixed (FXL I7 + review R1) | `test_row3_driveless_rooted_path_is_rooted_at_the_cwd_drive` (every platform) |
| F4 | fixed (P3, `grant._excluded`) | `test_row20_…` now tests `_excluded` (review L38-S3); folds on Windows, exact on Linux |
| F5 | decided by P1: case-sensitive on Linux, folded on Windows | `test_row19_…` (every platform, platform rule) |
| F6 | fixed (P4 + review R2/R3) | `test_row43_non_utf8_file_name_is_a_structured_refusal_at_job_new` (Linux) |
| F7 | fixed (FXL I8 test split) | `test_row1_…` (POSIX) |
| F8 | **open**, NEEDS OWNER (not in FIX1) | `test_row32_…` (POSIX), unchanged |
| F9 | fixed (P5, 0o600) | `test_row74_backup_file_is_owner_read_write_only` (POSIX) |
| F10, F11, F12 | F11 fixed (FXL I1); F10, F12 **open** (not in FIX1) | review readings, unchanged |
| F13 | NOT A DEFECT (unchanged) | step6-busy.json |
| F14 | NOT A DEFECT; contention now measured (below) | review-f1-concurrency-oplog.json |
| F15 | NOT A DEFECT; T28(b) now covered (below) | review-f1-interrupt.json |
| F16 (new, Lazy L38-S4, row 45) | **open**, route to FIX2: on Windows, plain-text (no `--json`) output with non-cp1252 text and a piped stdout raises UnicodeEncodeError in `wf.main` **after** the row committed; FIX1 E1 does not cover it (the write is outside `run()`). Re-verified by Lazy on Windows; not reproducible here (Linux stdout is UTF-8). | Lazy WF-038 L38-S4 |

**F14 contention (review L38-S1).** One 300 s repetition, 8 writers, every attempt with `wf --oplog`:
`6946 attempts, 6946 committed, 0 rejected, 0 lost, 0 duplicated, 0 unreported, seq consecutive, verify clean`.
- BEGIN IMMEDIATE waited for the reservation in **6519 / 6946 attempts (93.9 %)**: median 13 tries, max 368 (≈3.7 s at
  the 10 ms step, under the 5 s bound); 0 give-ups.
- COMMIT retried on busy in **61 / 6946 attempts (0.88 %)**: max 3 tries.
- So the 0-lost / 0-duplicated result held under near-constant reservation contention, not in an idle ledger. (The
  pre-FIX1 three repetitions, 20,906 attempts, had one lock-timeout; this repetition had none.)

**F15 interruption (review L38-S2).** 40 SIGKILLs, seed 21, four windows of 10 (`review-f1-interrupt.json`):

| window | outcome after kill | after `wf resolve` |
|---|---|---|
| random (uniform over the 0.070 s typical append) | 6 rolled back, no marker; 3 committed, no marker; 1 exited before the kill (committed) | — |
| in-txn (delay-precommit: after INSERT + marker, before COMMIT) | 10 rolled back, marker left | 10 NOT-COMMITTED |
| **in-commit (T28(b))** | **10 rolled back, marker left** | **10 NOT-COMMITTED** |
| post-commit (delay-postcommit: after COMMIT, before marker delete) | 10 committed, marker left | 10 COMMITTED |

- **in-commit** is new: the harness holds a SHARED read lock across the writer's COMMIT, so COMMIT cannot take its
  EXCLUSIVE lock and the writer is inside txn.py's COMMIT retry loop when it is killed (0.15-1.47 s after its committing
  marker appeared, all within the 2 s held bound). Two no-kill controls under the same reader show that loop in the
  operation log (199 `commit_busy` entries over 2.0 s, then `commit-timeout`). No product fault seam was used. Result:
  the journal rolls the transaction back at the next open, the committing marker remains, the next writer would refuse
  (`uncertain-write-pending`), and `wf resolve` answers NOT-COMMITTED. T28(a), (b) and (c) are now each covered.
- 0 corrupt; final chain 27 rows, consecutive, no pending marker.
- The final `wf verify` is `clean: false` **only** because of two preserved export staging files
  (`export temp file preserved: contract.json.<pid>-<hex>.tmp`, `head.json.<pid>-<hex>.tmp`), left by kills inside the
  post-commit hold during the export refresh. That is the designed behaviour (staging is preserved, never deleted, and
  reported until the owner removes it); chain, integrity and markers are clean. The pre-FIX1 campaign ended the same way.
