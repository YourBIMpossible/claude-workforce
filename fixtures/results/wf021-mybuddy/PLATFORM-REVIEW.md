# PLATFORM-REVIEW — tools/wf at 2dedc94 (Linux vs Windows)

WF-021 step 2 (MyBuddy, 2026-10-18). Commit `2dedc9424abae7930bd82a75c45c235600f0ebcd`. Evidence only; no product file changed.

## Method and provenance
- Scope: `tools/wf/wf.py`, `tools/wf/wfcore/*.py` (21 modules), `tools/wf/gate/wf_gate_hook.py`. Every construct on the WF-021 list (path, newline, case, ACL, lock, `os.replace`, `fsync`, signal, tempfile) was grepped and the matching slices read; line numbers are from the files at the commit above.
- Coverage: `tools/wf/tests/*.py` was searched for platform guards (`skipUnless`, `skipIf`, `skipTest`, `sys.platform`, `os.name`, `_winapi`, `os.symlink`). **L+W** = no guard (runs on both); **L** / **W** = effectively one platform.
- The first pass was produced by a read-only review agent working inside this MyBuddy session; Linux behaviour marked "verified" was run by that agent with read-only probes on scratch trees outside the checkout. Windows behaviour is from documented CPython/SQLite semantics and was **not run** here.
- **Re-verified by MyBuddy** on scratch fixtures under `fixtures/tmp/` (Linux, Python 3.14.4, SQLite 3.46.1): rows **#3, #16, #17, #19, #20, #43** and the `test_target_paths` root cause. Probe output is in the section *Re-verification* at the end. All other rows are the review's reading and are **not re-verified** by MyBuddy.

## Root cause: `test_gate_decide.py::PathsAndFailClosed::test_target_paths` fails on Linux (step-1 failure)
Input `{"file_path": "jobs\\j\\work\\b.txt"}` with a POSIX `cwd`:
- `gatecore._split_abs` (gatecore.py:100) switches the whole string to Windows form as soon as it contains a `\`. It finds no drive, no UNC and no leading `/`, returns None, so the path is relative.
- `_absolute` (gatecore.py:131) picks `/` from the cwd. The joined string is `/…/root/jobs\j\work\b.txt`.
- `_to_root_relative` calls `_split_abs` again. The `\` converts every `/` to `\`, no anchor matches, and the result is `"unresolvable-path"`.

**Product behaviour on Linux is right.** On POSIX, `jobs\j\work\b.txt` is one file name in the cwd, not `jobs/j/work/b.txt`. `decide` turns `unresolvable-path` into a deny, so the gate fails closed. The test pins a Windows-only expectation. Suggested split: on Linux assert `([], "unresolvable-path")` for the backslash form and `(["jobs/j/work/b.txt"], None)` for the forward-slash form; on Windows keep the current assertion. The neighbouring `test_unresolvable_paths` passes on Linux but pins Windows naming rules there (`CON`, `a.txt:stream` and `a.txt.` are legal Linux names, yet are denied).

## Findings table

### A. Separators, drive letters, absolute and rooted forms
| # | file:line | construct | Linux | Windows | test (file::method, platforms) | risk |
|---|---|---|---|---|---|---|
| 1 | gatecore.py:98-110 | `_split_abs`: any `\` switches the whole path to Windows form | POSIX path with `\` in a name → no anchor → `unresolvable-path` deny | drive/UNC parsed; `/x` without `\` = POSIX anchor → mismatch → unresolvable | test_gate_decide.py::PathsAndFailClosed::test_target_paths (L+W; **fails on L**); ::test_unresolvable_paths (L+W) | low: fail closed on both |
| 2 | gatecore.py:128-132 | `_absolute`: separator chosen from cwd form | `/`; `a\b` stays one component | `\` | test_gate_decide.py::PathsAndFailClosed::test_target_paths (L+W, fails L) | low |
| 3 | gatecore.py:100-110, 128-132 | drive-less rooted path `\jobs\j\work\a.txt` | joined to cwd, contains `\` → unresolvable (deny) | treated as relative to cwd: cwd/root `C:\root` → `(["jobs/j/work/a.txt"], None)` (re-verified with Windows-form strings). Windows resolves it to `C:\jobs\…` (drive root, outside the boundary), so a bound session is allowed where `out-of-scope` is due. Whether Claude Code ever sends such paths: not verified | NO TEST | **high on Windows** / none on Linux |
| 4 | gatecore.py:101, 158 | drive letter upper-cased; anchors compared lower-cased; drive-relative `C:x` | not produced by POSIX payloads | drive mismatch → unresolvable; `C:x` → `:` in component → unresolvable | test_gate_decide.py::PathsAndFailClosed::test_unresolvable_paths (drive subcase W) | low |
| 5 | gatecore.py:103-107 | UNC `\\server\share`, `\\?\` | n/a | UNC anchor lower-cased; `\\?\C:\…` anchor ≠ `C:` → unresolvable (fail closed) | NO TEST | low |
| 6 | pathpolicy.py:120-160 | `resolve`: `/`→`\`, split on `\`, drive/UNC/rooted refused, `..` collapsed | a `\` inside a Linux file name is split as a separator (a different path is classified) | intended | test_t35_path_policy.py::T35Classes::test_separators_and_dot_collapse; test_pathpolicy_hardening.py::NormalizationBypass::test_mixed_separators_and_case_in_both_path_and_glob (L+W) | low |
| 7 | pathpolicy.py:163-179 | `_norm_glob`: glob `/`→`\`, lower-cased | as #6 | intended | test_pathpolicy_hardening.py::MalformedContract::test_scope_globs_normalize_separators_case_and_trailing_separator (L+W) | low |
| 8 | pathpolicy.py:76; commands.py:799 | `relpath(...).replace("\\","/")` | relpath gives `/`; a real `\` in a directory name becomes `/` | relpath gives `\`, converted | test_t35_changeset.py::CoordinationSemantics::test_coordination_globs_are_the_spec_P4_row (L+W) | none |
| 9 | changeset.py:161 | `relpath(dirpath, root).replace("\\","/")` | a directory name containing `\` is recorded as nested components | normal | NO TEST | low |
| 10 | reconcile.py:123-134 | `_relate`: `\`→`/`, refuses `/…` and `X:`, `posixpath.normpath` | `\` in a recorded path treated as separator | intended | test_t06_reconcile.py::ReportClassification::test_A4_unresolvable_paths_are_matches_neither_with_null_shas_and_exact_limitation (L+W) | low |
| 11 | commands.py:758-759 | `_compare_receipts`: `normpath(join(base, rel))`, `os.path.isabs(rel)` | `C:\x` not absolute → joined under base → "file absent" | `C:\x` absolute → "outside the ledger root"; `/x` not absolute on 3.13+ → joins to drive root → `is_under` false | test_wf.py::SpecMapGaps::test_verify_compares_receipts_to_work_files (L+W, relative only) | low: informational |
| 12 | wf_gate_hook.py:99, 141, 146 | `os.path.isabs` on config paths, `probe_root`, payload cwd | only `/…` absolute; Windows-form config → `gate-config` deny | `F:\…` absolute; `/x` not absolute on 3.13+ | test_gate_hook_deployed.py::HookConfigAndIntegrity::test_invalid_configs_are_gate_config (L+W; `"relative/root"` only) | low: fail closed |
| 13 | commands.py:1187-1191 | `_file_uri`: `file:` + abspath with `\`→`/`; `%` not escaped | `file:/home/…` | `file:/F:/…`; a UNC ledger → `file://server/share/…` (SQLite rejection not verified) | test_b2s5_wiring.py::B2S5Wiring::test_s5w_backup_cli_copies_and_verifies (L+W, local paths) | low |
| 14 | gatecore.py:206; gatelog.py:175; backup.py:62 | `Path(abspath).as_uri() + "?mode=ro"` | `file:///home/…` percent-encoded | `file:///F:/…`; UNC form version-dependent | test_gate_decide.py::LedgerHealth::test_missing_and_not_sqlite (L+W, local) | low |
| 15 | (all path joins) | MAX_PATH with long fixture paths + `.<pid>-<hex8>.tmp` | no 260-char limit | OSError if LongPathsEnabled off (not verified) | NO TEST | low |

### B. Case sensitivity
| # | file:line | construct | Linux | Windows | test | risk |
|---|---|---|---|---|---|---|
| 16 | changeset.py:116-118, 151-152 (globs lower-cased via pathpolicy normalisation) | static walk prefix taken from the lower-cased glob, then looked up on disk | scope `Docs/**` looks up `docs`, which does not exist → **nothing walked, no problem, no limitation** (re-verified: `entries == {}`, `problems == []`; lower-case control captures normally). P1/P2/P3 changes there go undetected | case-insensitive lookup finds it; recorded directory part lower-cased | NO TEST (fixture globs are lower case) | **high on Linux**: fail-open in baseline/compare |
| 17 | reconcile.py:134, 163 | `_relate` lower-cases, `_sha_now` opens the lower-cased path | `jobs/j/work/New.txt` → `jobs/j/work/new.txt`, `sha_now = None` (re-verified). An added upper-case file with no intent/receipt can come out `matches-baseline`, so **no exception is raised** (classification path: review's reading, not re-run end-to-end) | case-insensitive open works | NO TEST | **high on Linux**: fail-open in reconcile |
| 18 | reconcile.py:323 | baseline key lookup by `k.lower() == rel` | two files differing only in case collapse to the first | same file anyway | NO TEST | low |
| 19 | pathpolicy.py:160, 206-212 | classification on case-folded forms | `JOBS/J/WORK/a.txt` (a different directory on ext4) is P1 **allowed** (re-verified) | correct for NTFS | test_t35_path_policy.py::T35Classes::test_case_differences_resolve_to_the_same_class; test_pathpolicy_hardening.py::...::test_mixed_separators_and_case_in_both_path_and_glob (L+W; pin folding on L) | medium on Linux: P1 widening; policy decision |
| 20 | grant.py:116-118, 284-291 | `grant.check`: resources and exclusions via case-sensitive `fnmatchcase` | correct (distinct directories) | `jobs/j/work/SECRET/key.txt` matches resource `jobs/j/work/**` but not exclusion `jobs/j/work/secret/**` (re-verified: True / False) → allowed by gate rule 5 although NTFS writes into the excluded directory; `wf accept` would later flag it via pathpolicy's folded P5 | NO TEST (grant_check_corpus.json has no case variants) | **high on Windows** / none on Linux |
| 21 | gatecore.py:164-165 | root containment `casefold` for Windows anchors only | case-sensitive | `casefold` ≠ NTFS upcase table (`ß`→`ss`) | NO TEST | low |
| 22 | gatecore.py:218-223, 230-231 | `_fold_match` / `_in_job` casefold (deny side) | case variants (`Ledger/…`) denied / attributed to j: over-deny | correct | test_gate_decide.py::ProtectedPaths::test_rule1_protected_paths_everyone (L+W) | low |
| 23 | gatecore.py:332-333 | gate files compared with `normcase(abspath)`, no realpath | a symlinked alias of the hook path escapes rule 1 | 8.3 / junction aliases escape rule 1 (not verified) | test_gate_decide.py::ProtectedPaths::test_rule1_protected_paths_everyone (L+W; upper-case variant W) | medium: alias bypass on both |
| 24 | paths.py:14, 17-20 | `normcase(realpath(abspath))`; `is_under` | case-sensitive | case-folded | test_wf.py::T27Markers::test_T31_faults_not_allowed_on_product_path (L+W) | low |
| 25 | paths.py:37; changeset.py:67 | `basename(...).lower() == "ledger"` | `LEDGER`/`Ledger` accepted as the ledger/boundary marker | matches NTFS | test_t35_changeset.py::ChangesetModule::test_boundary_root_rule (L+W, lower only) | low |
| 26 | validate.py:123-129 | `_path_in_scope` widening check case-folded | `Jobs/J/**` passes widening vs `jobs/j/**`; enforcement in grant.check is case-sensitive (no grant gained) | correct | test_wf.py::T03Validate::test_T07_grant_resource_widening (L+W) | low |
| 27 | validate.py:65; paths.py:31-32, 42-43; markers.py:31; observe.py:43, 206 | job id regex allows mixed case, trailing `.`, device names | `J` and `j` are distinct directories | `J`/`j` share `pending/`, `export/`, `observe/` (guard reads the other job's markers; exports overwrite); `j.` aliases `j`; `CON`/`nul` are devices (not verified) | NO TEST | medium on Windows / none on Linux |
| 28 | backup.py:45-55 | `_inside`: `normcase(realpath)` + `commonpath` | case-sensitive | case-folded; different drives → ValueError → not inside | test_t37_backup.py::T37Backup::test_refusals_write_nothing (L+W; upper-case variant W) | low |
| 29 | observe.py:209-210; wf_gate_hook.py:124-127 | `_same` / `_inside` with `normcase(realpath)` | case-sensitive | case-folded | test_t14_observe.py::ObserveLog::test_log_that_is_not_a_regular_file_refused; test_gate_hook_deployed.py::HookFlowTestCore::test_probe_target_outside_probe_root_is_gate_config (L+W) | low |
| 30 | commands.py:703-704 | ledger-dir allow-list exact names | exact case | case-variant stray reported as stray | test_t35_verify_ledger_dir.py::...::test_clean_ledger_directory_has_no_directory_problems (L+W) | none |
| 31 | bind.py:122; grant.py:118; reconcile.py:252 | `fnmatchcase` on ARS/role/binding globs | case-sensitive | case-sensitive (stricter than NTFS; over-deny only) | test_grant_check_corpus.py (L+W) | none |

### C. Windows name rules applied on Linux
| # | file:line | construct | Linux | Windows | test | risk |
|---|---|---|---|---|---|---|
| 32 | pathpolicy.py:142-152, 95-102 | refuses `:`, `<>"|?*`, trailing dot/space, device names (with or without extension), 8.3 `~n` | legal Linux names fail closed: `jobs/j/work/aux.c` → changeset baseline problem (so `wf job new`/accept are refused) and gate `unresolvable-path` deny; `10:30.log` likewise | intended | test_t35_path_policy.py::T35Classes::test_device_names_and_unresolved_forms_fail_closed, ::test_alternate_data_stream_syntax_fails_closed; test_pathpolicy_hardening.py::...::test_regression_com0_lpt0_and_superscript_device_names_were_P1 (L+W) | medium on Linux: availability, fail closed |

### D. Links, junctions, reparse points
| # | file:line | construct | Linux | Windows | test | risk |
|---|---|---|---|---|---|---|
| 33 | gatecore.py:135-148 | `_link_component`: `lstat`, `S_ISLNK` or reparse attribute | symlinks only; bind mounts not detected | symlinks and junctions | test_gate_decide.py::PathsAndFailClosed::test_link_component_is_unresolvable (L; W skips without privilege); junction: NO TEST | low L / medium W |
| 34 | changeset.py:72-81, 154, 160, 167 | `_is_link` + `os.walk(followlinks=False)` | symlinks skipped and recorded | junctions via reparse attribute | test_t35_changeset.py::ChangesetModule::test_symlink_is_skipped_and_recorded_never_followed, ::test_A2_link_under_write_scope_is_never_followed (L; W needs privilege); junction: NO TEST | low L / medium W |
| 35 | reconcile.py:137-156, 314 | `_is_link` / `_crosses_link` | symlink branch | junction branch | test_t06_reconcile.py::ReportClassification::test_A4_path_crossing_a_junction_is_unresolvable_and_never_followed (W only); Linux symlink: NO TEST | medium on Linux: untested |
| 36 | observe.py:213-227, 240 | `_is_link` = `islink` or `isjunction`; `_contained` | `isjunction` False; symlink branch | junction branch | test_t14_observe.py::ObserveLog::test_junctioned_job_dir_refused (W only); Linux symlink: NO TEST | medium on Linux: untested |
| 37 | export.py:183-187 | `DirEntry.is_symlink() or is_junction()` | `is_junction` False | junction detected | test_t35_stray_files.py::ExportStrayFiles::test_symlink_entry_reported_without_following (L); junction: NO TEST | low |
| 38 | commands.py:715-719 | ledger-dir listing `is_symlink()`, then `is_dir(follow_symlinks=False)` | symlink → `@` | junction reported as directory `/` (still a problem) | test_t35_verify_ledger_dir.py::...::test_symlink_in_ledger_directory_is_reported_and_not_followed (L) | low |
| 39 | markers.py:64 | marker staging `"w"` (not exclusive) at `<path>.tmp-<attempt>` | follows a planted symlink (random name) | symlinks need privilege | NO TEST | low |
| 40 | export.py:52; observe.py:254 | staging opened `"x"` (`O_CREAT|O_EXCL`) | refuses existing name or symlink | `CREATE_NEW` refuses | test_export_temp_preservation.py::...::test_write_atomic_never_touches_fixed_tmp (L+W), ::test_symlink_at_fixed_tmp_not_followed_or_overwritten (L) | none |

### E. Newlines and encodings
| # | file:line | construct | Linux | Windows | test | risk |
|---|---|---|---|---|---|---|
| 41 | markers.py:64, 135; export.py:52; observe.py:254; gatelog.py:167; manifest.py:41 | writes `encoding="utf-8", newline="\n"` | LF | LF | test_wf.py::TManifest::test_TM2_three_field_lines_verified; test_export_cmd.py::ExportCmd::test_AC1_stale_events_repaired (L+W) | none |
| 42 | export.py:225-226; manifest.py:46, 62; observe.py:247-248; markers.py:75; gatelog.py:195 | text reads, default `newline=None` | CRLF translated on read | same | test_t37_t36_matrix.py::T37ExportTail::test_T37_export_tampered_after_generation_reported_by_recorded_hash (L+W) | none |
| 43 | canon.py:13, 17 + changeset.py:160-173 | `canonical(ensure_ascii=False)`, then `sha256_text` (`.encode("utf-8")`) | re-verified: a non-UTF-8 file name in a walked scope becomes the surrogate key `jobs/j/work/\udcff.txt` with **no problem reported**; hashing the canonical baseline then raises UnicodeEncodeError, and `wf.run(["job","new",…])` lets it escape (only WfError is caught, wf.py:238) → traceback, no job created. (The first-pass reading put the error inside the walk; it is in the hashing step.) | file names are UTF-16; lone surrogates rare | test_wf021_fs_semantics.py::ModesAndNamesOnLinux::test_row43_observed_non_utf8_file_name_reaches_wf_job_new_as_an_uncaught_unicode_error (L) | medium on Linux |
| 44 | commands.py:25; wf_gate_hook.py:87-88 | JSON read `encoding="utf-8"` (BOM rejected) | rare | BOM from PowerShell 5/Notepad → `rejected(malformed)` / `gate-config` deny | test_gate_hook_deployed.py::HookConfigAndIntegrity::test_invalid_configs_are_gate_config (L+W, no BOM case) | low L / medium W |
| 45 | wf.py:282-293 (non-ASCII via commands.py:214-224, 247) | `sys.stdout.write` text mode | UTF-8 | piped stdout uses the ANSI code page; `-I` ignores `PYTHONUTF8`/`PYTHONIOENCODING` → UnicodeEncodeError on non-cp1252 text after the grant row commits; CRLF | NO TEST | low L / medium W |
| 46 | wf_gate_hook.py:166-169, 184 | stdout JSON (ASCII), stderr ascii+backslashreplace, stdin bytes | LF | CRLF; parsers use `splitlines` | test_gate_hook_deployed.py::HookFlowTestCore::test_stdout_conforms_to_claude_code_hook_schema (L+W) | low |
| 47 | wf_gate_hook.py:54-68; commands.py:1514-1517 | `wfcore_sha256` / `hook_sha256` over raw bytes | LF checkout | `autocrlf` checkout hashes differ from Linux; each host computes its own via print-install | test_gate_hook_deployed.py::HookConfigAndIntegrity::test_wfcore_sha256_matches_the_print_install_algorithm (L+W) | low |

### F. Replace, unlink, sharing violations, retry loops
| # | file:line | construct | Linux | Windows | test | risk |
|---|---|---|---|---|---|---|
| 48 | markers.py:46-58, 68, 96 | `_retry_share`: PermissionError retried 0.5 s around replace/remove | only real EACCES/EPERM → 0.5 s, then raised | WinError 32/5 while another process holds the marker open | NO TEST | low L / medium W |
| 49 | markers.py:71-82 | `read` retries PermissionError | never in practice | delete-pending/replace windows | NO TEST | low |
| 50 | markers.py:160, 165 | `delete(ref)` without try in `guard` | unlink succeeds | PermissionError after retry → non-Wf exception → traceback from `wf append` | NO TEST | low L / medium W |
| 51 | export.py:57 | `os.replace(tmp, path)`, no retry | succeeds, even over an open file | fails (WinError 5) if a reader holds the target or on a concurrent refresh → note `error`; committed row stays | test_export_temp_preservation.py::...::test_failed_refresh_leftover_reported_next_run, ::test_lost_publish_race_discards_own_staging_and_reports_error, ::test_refresh_failure_after_commit_keeps_row_committed (L+W, injected PermissionError(32)); ::test_concurrent_writers_do_not_collide (L+W) | low L / medium W |
| 52 | observe.py:258-265 | `os.replace` no retry; own staging removed on failure | succeeds | sharing violation raised (refusal at commands.py:1492) | test_t14_observe.py::ObserveLog::test_failed_replace_keeps_old_log_and_leaves_no_staging_file (L+W, mocked) | low |
| 53 | markers.py:133-136; gatelog.py:167 | append mode `"a"` | O_APPEND atomic for small writes | CRT seek-then-write not atomic across processes | test_gatelog.py::GateLog::test_failures_file_and_unlogged_attempts (L+W, single writer) | low |
| 54 | manifest.py:39-42 | `manifest.txt` written in place | a concurrent reader can see a truncated manifest (reported malformed) | same | NO TEST | low |

### G. Durability (fsync)
| # | file:line | construct | Linux | Windows | test | risk |
|---|---|---|---|---|---|---|
| 55 | markers.py:64-68; export.py:55-57; observe.py:256-258 | `flush` + `fsync(file)` then `os.replace`; **no directory fsync** | on ext4 the rename can be lost on power loss | directory fsync impossible; MoveFileEx without WRITE_THROUGH | NO TEST (power loss) | low: the committed row is the durable indication |
| 56 | db.py:120-121 | `journal_mode=DELETE`, `synchronous=FULL` (not EXTRA) | journal unlink at commit not directory-synced; SQLite documents EXTRA closes this | similar (not verified) | test_wf.py::T02Schema::test_T02_schema_constraints_and_immutability (L+W) | low |
| 57 | manifest.py:41; gatelog.py:167; markers.py:135 | writes with no fsync | page cache | lazy write | NO TEST | low |

### H. SQLite locking, busy handling, journal, URIs
| # | file:line | construct | Linux | Windows | test | risk |
|---|---|---|---|---|---|---|
| 58 | db.py:108-129, 132-146 | `connect(timeout=0)` + `busy_timeout=0`, manual `retry_busy` (10 ms) | POSIX fcntl byte locks | LockFileEx | test_wf.py::SpecMapGaps::test_connect_wait_counts_against_acquisition_bound; test_wf.py::T27Markers::test_T29b_reads_wait_for_write_lock (L+W) | low |
| 59 | db.py:177-179 | `is_busy` by errname or "locked" text | same | same | test_transaction_sequence_requirements.py::TxSeq::test_R1_two_jobs_contended_exact_committed_set (L+W) | low |
| 60 | db.py:182-204 | bounded `BEGIN IMMEDIATE` | same | same | test_b1_gaps.py::B1Gaps::test_T27_n_simultaneous_appends_one_job (L+W) | low |
| 61 | db.py:285-287 | refuses WAL | same | same | test_wf.py::T02Schema::... (L+W) | none |
| 62 | (process) | POSIX pitfall: closing any fd on the db drops the process's locks; no product code opens the ledger file while a connection is open | n/a | n/a | — | none |
| 63 | db.py:120 + hot-journal recovery | rollback at next open | unlink | unlink may hit sharing retries inside SQLite | test_b1_gaps.py::B1Gaps::test_T28_hot_journal_at_next_open_rolls_back; test_transaction_sequence_requirements.py::T28Journal::test_R6_..., ::test_R7_... (L+W) | low |
| 64 | gatecore.py:202-215, 357 | read-only URI, `timeout=read_timeout_s`; "locked"/"busy" → `ledger-locked` | busy-handler sleep granularity depends on libsqlite3 build (not verified) | `Sleep(ms)` | test_gate_decide.py::LedgerHealth::test_locked_past_the_timeout (L+W); test_t19_gate_faults.py::...::test_T19_locked_past_timeout_linux (L) / _win32 (W) | low |
| 65 | gatelog.py:133-141 | `connect(timeout=3)`, `executescript`, `BEGIN IMMEDIATE` | same | same | test_gatelog.py::GateLog::test_concurrent_writers_keep_one_chain (L+W) | low |
| 66 | gatelog.py:174-188; backup.py:58-63; commands.py:1133 | read-only URIs | same | see #13/#14 | test_gatelog.py::GateLog::test_read_functions_write_nothing; test_t37_backup.py::T37Backup::test_result_shape_and_engine_copy (L+W) | low |
| 67 | commands.py:1077-1112 | second-writer probe (`timeout=0`) | busy expected | busy expected | test_t30_race_secondwriter_identity.py::T30fSecondWriter::...; test_b2s5_wiring.py::...::test_s5w_recover_second_writer_refuses_and_appends_nothing (L+W) | low |
| 68 | backup.py:116-119 | engine backup into a pre-created empty file | same | same | test_t37_backup.py::T37Backup::test_backup_while_second_process_writes (L+W) | low |

### I. File identity
| # | file:line | construct | Linux | Windows | test | risk |
|---|---|---|---|---|---|---|
| 69 | gatecore.py:194-197, 346-353 | `ledger_identity` = `st_dev`, `st_ino`, `st_size` | inode; delete-then-create can reuse the inode (undetected; observed by MyBuddy in WF-009 F1 on ext4) | volume serial + file index | test_gate_decide.py::LedgerHealth::test_identity (L+W); test_t19_gate_faults.py::...::test_T19_identity_change_core_linux (L) / _win32 (W); test_t30_race_secondwriter_identity.py::T30hReplacement::test_detected_with_the_gate_identity (L+W) | low |

### J. PID liveness, process exit, signals
| # | file:line | construct | Linux | Windows | test | risk |
|---|---|---|---|---|---|---|
| 70 | export.py:76-99 | `_pid_alive` (the only `sys.platform` branch): Windows `OpenProcess`/`GetExitCodeProcess`; else `os.kill(pid, 0)` | ESRCH → dead; EPERM → alive; zombie = alive; a pid in another PID namespace reads dead (an active writer's staging could be reported orphan; report only) | ACCESS_DENIED → alive; 259 ambiguous (errs alive); return value unchecked | own pid only: test_export_temp_preservation.py::...::test_active_staging_of_this_process_not_reported; test_t35_stray_files.py::...::test_legitimate_inventory_is_clean_and_active_staging_not_stray (L+W). **Foreign pid: NO TEST** | medium: each platform's branch untested |
| 71 | export.py:47, 117-130 | staging name carries `os.getpid()` | pids up to pid_max | multiples of 4 | as #70 | low |
| 72 | txn.py:140; commands.py:1208 | `os._exit(3)` fault kill points | locks released at death | handles closed | test_wf.py::TConflict::test_C2b_kill_before_commit_then_resolve; test_t30_recovery_faults.py::...::test_T30b_kill_after_open (L+W) | none |
| 73 | (scope) | no signal handlers; `os.kill` only in #70 | — | — | — | none |

### K. Permissions
| # | file:line | construct | Linux | Windows | test | risk |
|---|---|---|---|---|---|---|
| 74 | backup.py:110 | `os.open(O_WRONLY|O_CREAT|O_EXCL)` default mode 0o777 | backup gets execute bits (0o775 under umask 002) and is world-readable | ACL inherited | NO TEST | low |
| 75 | (default creation) db/journal/markers/exports | umask mode (typically 0644, world-readable) | ACL inherited | — | NO TEST | low |
| 76 | gatecore.py:202-210 | unreadable ledger → `ledger-unreadable` | `chmod 000` denies unless root | ACL deny needed | test_t19_gate_faults.py::...::test_T19_unreadable_permission_denied_linux (L); _win32 (always skipTest) | low L / medium W (never verified on W) |

### L. Time and clocks
| # | file:line | construct | Linux | Windows | test | risk |
|---|---|---|---|---|---|---|
| 77 | db.py:101-105, 134-146, 184-204; txn.py:34, 143-175; markers.py:51-58 | `time.monotonic` bounds, `sleep(0.010)`/`0.002` | CLOCK_MONOTONIC | ≤3.12 ~15.6 ms resolution; 3.13+ QPC (not verified); bounds hold, retries coarser | test_wf.py::T27Markers::test_T29_commit_timeout_give_up (L+W) | low |
| 78 | validate.py:69-70; markers.py:129; wf_gate_hook.py:175 | `time.time`/`gmtime` → ms timestamps | µs | coarser on old builds | test_t14_observe.py::Classify::test_future_event_noted_not_negative (L+W) | low |
| 79 | reconcile.py:223-230; observe.py:81-95; grant.py:110; bind.py:193 | ISO parsing | locale-independent | same | test_t06_reconcile.py::GateAttempts::... (L+W) | none |

### M. Interpreter, subprocess, environment
| # | file:line | construct | Linux | Windows | test | risk |
|---|---|---|---|---|---|---|
| 80 | commands.py:1522-1523 | print-install snippet `f"python -I {hook_dst}"` | `python` often absent on Ubuntu (`python3`); unquoted path breaks on spaces | launcher/PATH; space breaks it | test_b2s4a_wiring.py::B2S4A::test_print_install_writes_nothing (L+W; command string not asserted) → NO TEST | medium on Linux |
| 81 | wf.py:22-23; faults.py:49 | `WF_SESSION`, `WF_ROLE`, `WF_TEST_FAULTS` | names case-sensitive | case-insensitive | test_wf.py::T27Markers::test_T31_faults_not_allowed_on_product_path (L+W) | low |
| 82 | wf_gate_hook.py:202-208; wf.py:10 | `-I`, `pycache_prefix`, `sys.path` insert | same | same | test_gate_hook_deployed.py::HookConfigAndIntegrity::test_unexpected_bytecode_directory_is_gate_integrity (L+W) | none |
| 83 | (product) | no `subprocess`/`sys.executable` in product code | — | — | — | none |

### N. Version-gated APIs
| # | file:line | construct | Linux | Windows | test | risk |
|---|---|---|---|---|---|---|
| 84 | gatecore.py:146; changeset.py:80; reconcile.py:144 | `getattr(st, "st_file_attributes", 0)` | 0 | reparse bit honoured | see #33-35 | low |
| 85 | observe.py:214; export.py:183 | `os.path.isjunction` (getattr-guarded) / `DirEntry.is_junction()` (3.12+) | False | real | see #36-37 | none (3.12+ already required by db.py:116 `autocommit=`) |

## Uncovered assumptions worth a Linux test (inputs to step 3)
1. #16 changeset upper-case scope (high, Linux).
2. #17 reconcile case folding (high, Linux).
3. `test_target_paths` split per platform (existing test; outside MyBuddy's lane, reported only).
4. #3 drive-less rooted Windows path (high, Windows; testable on Linux with Windows-form strings).
5. #20 exclusion case (high, Windows; testable on Linux at the `grant` level).
6. #35 reconcile symlink (Linux twin of the Windows-only junction test).
7. #36 observe symlink (Linux twin).
8. #70 foreign pid liveness.
9. #43 non-UTF-8 file name → traceback instead of a WfError.
10. #32 legal Linux names fail closed (pin the availability cost).
11. #19 P1 case widening (policy; current tests pin it).
12. #80 print-install interpreter/quoting.
13. #48/#50 marker retry behaviour.
14. #74 backup file mode.
15. #12 Windows-form hook config on Linux → `gate-config`.

## Re-verification (MyBuddy, scratch fixtures under fixtures/tmp, 2026-10-18)
```
#16 entries: {} problems: [] limitations: [<4 standard limitation lines; none names the unwalked scope>]
#16 control lower: ['docs2/a.txt']
#17 relate: jobs/j/work/new.txt sha_now: None
#3: (['jobs/j/work/a.txt'], None)        # gatecore.target_paths({"file_path": "\\jobs\\j\\work\\a.txt"}, cwd/root "C:\\root")
#20 _glob_in resource: True exclusion: False
#19 classify upper: P1 ['JOBS/J/WORK/a.txt'] result allowed, reason "in write scope (P1)"
test_target_paths (step 1): AssertionError: Tuples differ: ([], 'unresolvable-path') != (['jobs/j/work/b.txt'], None)
```

## Addendum — WF-021 review F1 (Lazy WF-038), re-based on `fix1/integration` dfd6e85
The rows above describe 2dedc94. On branch `wf021b/mybuddy` (from dfd6e85) the characterisation tests pin the
post-FIX1 behaviour and name the FIX1 item: rows **3** (I7 + R1, every platform), **16, 17** (P1, case-sensitive FS
only), **18** (P1, every platform), **19** (P1, every platform, platform rule), **20** (P3, now `grant._excluded`, every
platform), **43** (P4 + R2/R3, byte-name filesystems), **74** (P5, POSIX). Rows 1, 12, 25, 32, 35, 36, 70 are unchanged
by FIX1.
Skips are now per test (review L38-M1): rows 18, 19, 20, 25 and **70** run on every platform (row 70 covers the
`_pid_alive` branch of the host it runs on, so the "Foreign pid: NO TEST" gap above is closed on both platforms once the
Windows run is made); rows 35/36 skip only when a symlink cannot be created (probe); 16/17 need a case-sensitive
filesystem; 1, 12, 32 need POSIX path rules; 43 needs byte file names; 74 needs POSIX modes.
New row (from Lazy WF-038 L38-S4, Windows, not re-verified here): **45** — `wf.main` writes plain-text results with the
console encoding; with a piped stdout on Windows (cp1252) a non-encodable character raises UnicodeEncodeError after the
command committed (FINDINGS F16). Row 51 re-verified by Lazy on Windows (`PermissionError` note, row kept).
Campaign evidence for this branch: `review-f1-concurrency-oplog.json` (contention) and `review-f1-interrupt.json`
(adds the in-commit window, T28(b)); summarised in FINDINGS.md, addendum.
