# WF-019 — Acceptance traceability T01–T37 against tools/wf at 2dedc94 (Plexy, read-only)

Base: claude-workforce `2dedc9424abae7930bd82a75c45c235600f0ebcd`. Spec: `FIRST-RELEASE-SPEC.md` Rev 5.3 §8 (lines 270–315) and §9 (317–343). Host: Windows 10.0.26300.9711, Python 3.14.6.

**Run evidence (step 1).** `python -I -B -m unittest discover -s tools/wf/tests -p "test_*.py"` → `Ran 568 tests in 225.714s` / `FAILED (failures=1, skipped=5)`; the -v re-run gives the same result (`Ran 568 tests in 227.223s`).
- The single failure is F1, a checkout line-ending defect in a test.
- The 5 skips are platform skips.
- PASS below means every listed test reported `ok` in that -v run. Exceptions are named.

**How tests were matched.** Matching is by behaviour, never by name. In `test_wf.py`, `test_T01`–`test_T10` and `test_T27`–`test_T31` use the older FB1.4 numbering, which differs from spec §8 (see F7). Methods named `test_spec_Txx` do use spec numbering.

**Coverage classes**
- **FULL:** every clause of Expected is asserted.
- **PARTIAL:** the missing clause is named.
- **FIXTURE-ONLY:** asserted only in-process on the decision core (`gatecore.decide`) or on hand-built rows, never through the hook or the CLI.
- **NONE:** no test.

**Scope of every row**
- Every test here runs on fixtures. Nothing in the repo runs in live Claude Code.
- Live 4b evidence (WF-017: live-t13-new/old/stopped, t15, t17, 27/27 AGREE) exists outside the repo, on White's F: drive. It is cited where relevant, not counted as repo coverage.
- G-05 (caller identity) is never tested. Session ids are fixture strings.

## Status codes used in the table

| code | meaning |
|---|---|
| R | Reject |
| D | Deny |
| Dt | Detect |
| P | Proc (procedural) |
| NSS | spec says "needs G-05; if unsupported: not satisfied as specified" |
| LIVE | live-pilot acceptance additionally requires observing this case live (spec:314) |

## Per-case traceability

| ID | Spec result / marks | Implementing tests (file::Class.method) | Run | Coverage | Missing / limit |
|---|---|---|---|---|---|
| T01 | R (V; I if G-05) | test_wf::T03Validate.test_T04_message_rules (:161-168) · TConflict C1–C5 (:562-686) · test_t32_bind::BindPreconditions.test_duplicate_and_foreign_bind (:333) · test_b2s1_wiring::B2S1.test_T32_* (:128,:161) · test_t33_replacement::T33a.test_T33a_foreign_session… (:206) | PASS | FULL (V) | I/G-05 not exercised. TConflict.test_C5b (:688-702) records a case where the conflict goes unrecorded ("NOT a T01 pass"), i.e. a bounded marker-failure path. Exception reaching digest `needs_owner` not asserted. |
| T02 | R (V) | test_wf::T03Validate.test_T04_message_rules (:151-152) · test_wf::SpecMapGaps.test_spec_T31_insert_at_existing_seq_refused_by_schema (:322) · test_b1_gaps::B1Gaps.test_T31_different_types_race_for_same_seq (:292) · test_transaction_sequence_requirements::TxSeq.test_R3/R4 (:214,:252) | PASS | FULL | No row count at :151-152. |
| T03 | R / P (V; O procedural) | test_wf::T03Validate.test_T05_grant_ordering (:184-186) · test_T03_contract_required_fields (:141-145) | PASS | PARTIAL | "Never in force" not read right after the rejection. O (genuineness) procedural. **Unmapped in §9.** |
| T04 | R (V) | test_wf::T03Validate.test_T04_message_rules (:154-157) · test_t33_replacement::T33a (:186) · test_b2s2_wiring::B2S2.test_recover_decide_preconditions_and_resume_path (:206-221) · test_t32_bind::ActorCheck.test_matrix_in_explicit_mode (:427-431) | PASS | PARTIAL | "Kept as evidence" not asserted. A rejection writes no row; the predicate needs owner interpretation (test_b1_gaps.py:4). See F9. |
| T05 | R (V) | test_wf::T03Validate.test_T04_message_rules (:151) · TConflict.test_C4 (:646-649) | PASS | PARTIAL | "One effect" (unchanged row count) asserted only for the conflict-row repeat. "Logged" is only the returned outcome `DUPLICATE-IGNORED`. |
| T06 | R / Dt (V), LIVE | test_b2s1_wiring::B2S1.test_T06_reconcile_ack_cycle (:199-248), test_T06_no_report_and_implicit_builder (:255) · test_b2s2_wiring::B2S2.test_recover_decide_preconditions_and_resume_path (:168-225) · test_t33_replacement::T33a (:163-190) · test_t06_reconcile (module harness, "not evidence for the wiring" :10-14) | PASS | FULL (fixtures) | Live observation not done (LIVE). |
| T07 | R (V) | none | — | **NONE** | No doorbell mechanism; R1 doorbell is the owner (spec:237); doorbells deferred (spec:347). "Processed from ledger on next read" has no dedicated test. See F10. |
| T08 | R (V) | test_wf::T03Validate.test_T04_message_rules (:152) | PASS | FULL | One assertion. |
| T09 | R (V) | test_engine_missing_fields::T09EngineMissingFields (:27-61) · test_engine_missing_fields_closure::T09Closure G1–G4 (:50-149) · test_b1_gaps::B1Gaps.test_T09_missing_role_or_session_exact_result_and_storage_rule (:110-151) · test_wf::SpecMapGaps.test_spec_T09_missing_author_or_session (:283) | PASS | FULL (author/session); PARTIAL (payload/contract path) | G4 calls validate directly, so "ledger unchanged" is not asserted on the payload/contract path. Stale docstring test_b1_gaps.py:116-117 (D-017 settled usage). |
| T10 | R (V) | test_wf::T03Validate.test_T04_message_rules (:158-160) · test_T07_grant_resource_widening (:215-220) | PASS | PARTIAL | Authority branch untested: handoff `actions` outside the activated grant → `widening` exists at wfcore/validate.py:175-180, but no test reaches it. |
| T11 | D (E) | test_gate_decide::JobState.test_cancelled (:281), test_T15_revoked_grant_denies_hooked_write (:256) · CLI halves test_b2s3_wiring::B2S3 (:59-152), test_t34_cli_matrix (:150,:213,:227) | PASS | PARTIAL / FIXTURE-ONLY (gate) | Gate deny is core-only; test_gate_hook_deployed has no cancel/revoke case. "Reconcile accurate" after cancel/revoke not asserted (:144-145 checks exit 0 only). |
| T12 | P, LIVE | none (adjacent: test_b2s2_wiring::B2S2 (:70-225), test_t33_replacement::T33a) | — | **NONE** | Procedural; live only. Not satisfiable by fixtures. See F10. |
| T13 | D (I+E), NSS | test_gate_decide::BoundAndUnbound.test_T13_* (:356, :371, :405, :430, :455) · ledger companion test_t33_replacement::T33a (:206) | PASS | FIXTURE-ONLY | No hook-level `stale-session` / `not-current-binding` test in the repo; G-05 untested. Live: WF-017 live-t13-old 5× `stale-session`, live-t13-stopped 5× `not-current-binding`. |
| T14 | Dt (V) | test_t14_observe::Classify (:100-142, gate half :241-276) · test_b2s3_wiring::B2S3.test_observe_read_only_bounded_log (:163) · test_b2s4a_wiring::B2S4A.test_observe_gate_log_available_only_when_verified (:118) | PASS | FULL (module) / PARTIAL (CLI) | Slow/silent and gate-log classification never driven through `wf observe`. Thresholds are placeholders. Log path diverges from §7 (F4). |
| T15 | D (E) | test_gate_decide::JobState.test_T15_revoked_grant_denies_hooked_write (:256) · PayloadAndChannels.test_T25_… (:163) · ledger half test_t34_cli_matrix::T34CliMatrix.test_T15_work_events_refused_under_revoked_grant (:213) · test_b2s3_wiring::B2S3 (:59) | PASS | PARTIAL | "Logged" not asserted for a revoked deny in the repo (core writes no log; no hook test uses `authority-revoked`). Live: WF-017 live-t15 3× `authority-revoked`, hook `logged: true`. |
| T16 | R (V), LIVE | test_t30_recover_close::RecoveryClosure.test_T16_second_trigger… (:215) · test_t16a_recover_begin (:114) · test_b2s2_wiring (:117) · test_t30_recovery_faults::T30RecoveryFaults.test_T30a_… (:171) · test_t33_replacement::T33a.test_T33a_same_decision_cannot_be_cited_twice (:215), T33Preconditions (:325-379) | PASS | PARTIAL | Second trigger refused only while a recovery is open. "Same interruption" after close is not distinguished from a new one (:221). Live not done. |
| T17 | D (I+E), NSS | test_gate_decide::BoundAndUnbound.test_T17_… (:349), ProtectedPaths (:175,:190) · test_gate_hook_deployed::HookWithRealGatecore.test_protected_path_deny (:484) · test_t33_replacement::T33Preconditions.test_T33f_… (:335) | PASS | PARTIAL | Ledger half hook-level (fixture). Scope half (`unbound-session`) core-only in the repo. G-05 untested. Live: WF-017 live-t17 `unbound-session` ×2, `protected-path`. |
| T18 | (a) D (E) · (b) Dt bounded (V over boundary) | (a) test_gate_decide::BoundAndUnbound.test_T18a_… (:325) · (b) test_t35_changeset::EndToEndWithWiring.test_R1_… (:311), ChangesetModule (:76) · not-detected record: test_t35_path_policy (:96, :186), test_pathpolicy_hardening (:151,:229), test_gate_decide::ShellChannel (:486,:502) | PASS | (a) FIXTURE-ONLY · (b) PARTIAL | No real shell tool invoked. End to end only an undeclared deletion is detected via `wf accept`; add/modify only at module level. Live: WF-017 live-t13-new `path-not-granted`/`path-excluded`. |
| T19 | D (E+V) | test_t19_gate_faults::T19GateFaults (19 tests, :237-409) · test_gate_decide::LedgerHealth (:198-226), JobState.test_recovery_open (:287) · `wf` half: test_wf::T08Flow.test_T10_verify_detects_tamper (:275), test_t30_recover_close (:142), test_b2s1_wiring (:309) | PASS, 4 platform SKIPs (identity st_ino, POSIX locks, mode 000 = linux-only; win32 ACL out of scope) | PARTIAL | "Blocker raised" never asserted (no blocker-row check; F9). Identity check core-only (hook config has no identity field by contract). Permission-denied not covered on Windows. |
| T20 | R (V) | test_wf::T08Flow.test_T08_full_flow_status_accept_close (:238-239, close before accept) | PASS | PARTIAL | No test of a claim without evidence links. Validation exists (validate.py:187-189 `rejected(malformed)` "has no evidence refs"), untested. No test of accept with nothing delivered. |
| T21 | R (V) | test_wf::SpecMapGaps.test_spec_T21_close_needs_intervention_declaration (:295) · minutes `unknown`: test_b2s3_wiring (:139-150), test_t34_cli_matrix (:247-251) · non-integer refused: test_t34_grant_lifecycle::Validators.test_schemas (:451-457), test_b2s3_wiring (:154) · test_t34_grant_lifecycle::Validators.test_intervention_summary_never_imputes (:473) | PASS | PARTIAL | "Never agent-estimated" is shown only as schema rejection plus no imputation; who supplied the minutes is not testable in R1. |
| T22 | R (V) | test_b2s3_wiring::B2S3.test_observe_read_only_bounded_log (:185-190) · test_t14_observe::ModuleT22 (:422-441), ObserveLog (:320-400) | PASS | FULL | Log path diverges from §7 (F4). |
| T23 | Dt (V) | test_wf::T08Flow.test_T10_verify_detects_tamper (:265) · test_wf::SpecMapGaps.test_spec_T23_delete_mid_chain_detected (:313) · test_b1_gaps::B1Gaps.test_T23_reordered_rows_detected (:154) | PASS | FULL | Edit case lives under an old-numbered name (F7). **Unmapped in §9.** |
| T24 | R (V) | test_t24_accept_declarations::T24Unit (:35-104), T24Flow (:145-197) · test_wf::SpecMapGaps.test_spec_T24_wrong_environment_rejected (:305) · test_wf::T08Flow (:236-237) · test_t32_bind::BindPreconditions (:341,:351) | PASS | FULL | — |
| T25 | D (E) | test_gate_decide::PayloadAndChannels.test_T25_bound_session_outside_boundary_is_out_of_scope (:150), BoundAndUnbound (:398) · test_gate_hook_deployed::HookWithRealGatecore.test_T25_bound_session_outside_boundary_denied_recorded (:496), test_outside_boundary_allow_recorded (:489) | PASS | FULL (outside-boundary, hook-level fixture) | In-repo out-of-scope half core-only (= T18a). Live: WF-017 live-t13-new 3× `out-of-scope`, live-t17 control allow `outside-boundary`. |
| T26 | Dt (V) | test_digest_t26::T26Digest a–j (:40-213) · explicit-bind rows: test_t32_bind::DigestRows (:615), test_b2s1_wiring (:111), test_t33_replacement (:192) | PASS | FULL | — |
| T27 | R (V §3A) | test_b1_gaps::B1Gaps.test_T27_n_simultaneous_appends_one_job (:181), test_T27_simultaneous_mutations_two_jobs (:220) · TxSeq.test_R1/R2/R5 (:131,:170,:290) · test_gatelog::GateLog.test_concurrent_writers_keep_one_chain (:228) · test_wf::SpecMapGaps.test_connect_wait_counts_against_acquisition_bound (:362) · test_wf::T27Markers.test_T29_commit_timeout_give_up (:422), test_T29b (:437) · TConflict C1–C5b | PASS | FULL except two parts | N gate-log writes are 6 threads with different sessions, not one session. "Commit-timeout adds no committed row" asserted only for the conflict case (C2a :595). |
| T28 | R / Dt (V + engine) | (a) test_b1_gaps (:248), test_crash_export::CrashExport.test_X2 (:198), TxSeq T28Journal.test_R6 (:369) · (b) proxies test_wf (:478), test_digest_t26 (:198), TConflict.test_C2b (:609) · (c) test_crash_export (:117), test_wf (:457), test_export_cmd::ExportCmd AC1–AC6 (:57-166), test_t16a_recover_begin (:85) | PASS | (a) FULL · (b) PARTIAL · (c) FULL | Kills are the `os._exit` fault seam (D-018), not OS kills. No mid-COMMIT kill. "No lock file exists" has no test. Hung-writer blocker and "never a force" not asserted. **Unmapped in §9.** |
| T29 | R / D (V+E; O procedural) | test_wf::T03Validate.test_T05_grant_ordering (:181-183 wrong owner-hash) · test_t34_grant_lifecycle::AuthorityStates.test_revision_mismatch_variants (:170) · test_t34_cli_matrix (:185, :268, :274) · test_gate_decide::JobState.test_D_S4_1_… (:294) · test_grant_check_corpus::Corpus (:51,:63) · test_t32_bind::BindPreconditions.test_no_grant_and_proposed_grant (:302) · **test_wf019_gaps::T29QuoteContent (expectedFailure)** | PASS (+1 expected failure) | PARTIAL | Quote lacking grant id and block hash still activates (F2, D-022 retained). Contract amendment fixture-only (tamper_pin). Coordinator direct `activate` not testable (O procedural). **Unmapped in §9.** |
| T30 | R / Dt (V) | (a) test_t30_recovery_faults::T30RecoveryFaults.test_T30a_… (:171), test_t30_race_secondwriter_identity::T30aRace (:92) · (b) T30RecoveryFaults (:187-222) · (c) test_b2s1_wiring (:269,:309), test_gate_decide (:287), test_t19_gate_faults (:354), test_b2s2_wiring (:139) · (d) test_b2s2_wiring (:91,:148), test_t30_recover_close (:158) · (e) test_b2s1_wiring (:274-277), test_t16a_recover_begin (:126) · (f) T30fSecondWriter (:130), test_b2s5_wiring (:197,:225) · (g) test_b2s1_wiring (:328), test_t16a_recover_begin (:85-112, :141) · (h) T30hReplacement (:173-212) | PASS | a FULL · b FULL (seam) · c PARTIAL · d FULL · e PARTIAL · f FULL · g PARTIAL · h PARTIAL · **i NONE** | (c) after close only `intent` re-run; accept/bind/close and gate-after-close not asserted. (e) proxy only. (g) only content tamper in the recovery path, and the gate-log tail is asserted NOT an exception (:112), against the spec (F3). (h) stated limit, no identity field (D-S5-1). (i) no test that recovery events are never accepted as `stop-confirmed`. |
| T31 | R (V) | test_b1_gaps (:292) · TxSeq.test_R3/R4 (:214,:252) · test_wf::SpecMapGaps.test_spec_T31_… (:322) | PASS | FULL | "No reliance on filename uniqueness" not proved (test_b1_gaps.py:301). |
| T32 | R / D (V+E) | test_t32_bind::BindPreconditions (:302-351) · test_b2s1_wiring::B2S1.test_T32_* (:135-166) | PASS | FULL (V) / PARTIAL (E) | Nothing ties the gate to a refused bind (generic unbound-session gate tests only). |
| T33 | R (V) | test_t33_replacement::T33a (:163-215), T33Delegation (:239-251), T33d (:308-313), T33Preconditions (:325-379) · test_b2s2_wiring (:168) | PASS | a PARTIAL · b–d PARTIAL · e FULL · f PARTIAL | (a) "ARS unchanged" not asserted; decision row hand-built. (b–d) "needs a renewed block" (success after renewal) not asserted. (f) proxy (unbound), absence of the verb not checked. |
| T34 | R / D (V+E; O procedural) | test_t34_cli_matrix::T34CliMatrix (:150-200, :274) · test_gate_decide (:256-272) · test_t34_grant_lifecycle (:164-300) | PASS | PARTIAL | Environment/stance change: **NONE** (no command in R1, declared NOT RUN test_t34_cli_matrix.py:18-19). Contract amendment fixture-only. "Rebind at generation+1" and "old quote never carries over" (retry with old hash) not asserted. |
| T35 | R / Dt bounded (V over boundary) | test_t35_changeset, test_t35_path_policy, test_t35_remaining_requirements, test_t35_stray_files, test_t35_verify_ledger_dir, test_t35_windows_resolution, test_t35_wiring, test_pathpolicy_hardening (see WF-019 evidence notes) | PASS (platform skips inside W1–W4 when fixtures cannot be created; none skipped on this host) | PARTIAL | Change kinds per class only for P1/P2 (+ presence-only deletion). P3 "with separate authorization" fail-closed only (no field). Links and junctions recorded, never resolved (stated limit). requirements_t35_unmet.py is an empty register (0 tests, by design). |
| T36 | Dt bounded (V) | test_t37_t36_matrix::T36ViaVerify / T36ViaVerifyCopy (:135-253) · test_t36_verify_checkpoint::T36Checkpoint (:109-232) · test_b1_gaps (:358,:385,:408) · test_checkpoint_clarity::CheckpointClarity (:105,:115) | PASS | FULL | — |
| T37 | Dt (V) | test_t37_backup::T37Backup (:105-297) · test_b2s5_wiring (:59-174) · test_t37_t36_matrix::T37ExportTail (:288-327) | PASS | FULL (fixture) | Real restore is an owner procedure (runbook), not tested; copies verified only. |

**Totals (37 cases, each counted by its weakest clause).**

| class | count | cases |
|---|---|---|
| FULL | 10 | T01 (at V), T02, T06 (fixtures), T08, T22, T23, T24, T26, T36, T37 (fixture) |
| PARTIAL | 24 | T03, T04, T05, T09, T10, T11, T14, T15, T16, T17, T18, T19, T20, T21, T25, T27, T28, T29, T30, T31, T32, T33, T34, T35 |
| FIXTURE-ONLY | 1 | T13 |
| NONE | 2 | T07, T12 |

Sub-clauses with no test inside PARTIAL cases: T30(i), T34 environment/stance, T28 "no lock file".

**Spec marks.**
- **Not satisfied as specified unless G-05 holds:** T13, T17. G-05 is untested.
- **Procedural:** T12; the O layer of T03, T29 and T34.
- **Live required:** T06, T12, T16 (spec:314).
- **Stale header:** §8's "none run" (spec:270) and "Every case is NOT RUN" (spec:315) no longer describe the repo; 563 tests pass at 2dedc94 (F5).

## Step 3 — §9 requirements-to-tests mapping vs step 2

- **Cases cited in §9 with no implementing test:**
  - T07 (R11) and T12 (R12): NONE.
  - T34 cited under R09: its environment/stance clause has no test.
  - T11, T13 and T17, cited under R09 as "deny for hooked tools": core or fixture only in the repo; the hook-level deny exists only for protected-path and T25.
- **Cases with tests but no §9 row:** T03, T23, T28, T29.
- **Header claim:** §9 does not claim run status, but §8's header says none run (see F5).

## Step 5 — 4b reliance claims checkable from the repo

- **Hook.** `tools/wf/gate/wf_gate_hook.py` git blob sha256 = `b486f824b232a110e43e5a6905314c611d6b81109ea11ab4f96e978bc4785abf`. This equals 4B-PROCEDURE §3 and WF-017.
- **wfcore.** Hashed in the S4A-A3 frozen form (sorted `wfcore/*.py`, 21 files, `"<sha>  <name>\n"` lines) from git blob bytes = `0b7fa70272ac1d97a3f7acc41edb05c814eaa3d46c0d9c4b4efd182b5ea609b6`. This also equals the recorded value.
- **CRLF caveat.** The same files read from this CRLF checkout give hook `b24dde34…` and wfcore `817cd384…`. A Windows checkout under `core.autocrlf=true` therefore does not reproduce the installed hashes; the installed bytes are the LF blob bytes.
- **Not checkable from the repo:** `fixtures/probe-4b/` is absent at 2dedc94. The staged config (`44b184bd…`), BUILD-MANIFEST, registration-group.json, the interpreter (`7ca24f26…`) and the gate.db rows all need `F:\Claude-Workforce\fixtures\probe-4b\` on White's F: drive.

## Findings

| # | Finding | Disposition |
|---|---|---|
| F1 | `test_gate_corpus_replay.test_corpus_is_unchanged_from_main` hashes working-tree bytes. It fails on a fresh `core.autocrlf=true` checkout: `7536da5c…` vs pinned LF `103e0e14…`. The repo has no .gitattributes. My own S4G test; no product behaviour involved. | PROPOSED (PROPOSED-TESTS.md §F1) |
| F2 | T29: a quote lacking grant id and block hash still activates (commands.py:264-265 only requires a non-empty quote). | NEEDS OWNER. Already retained by D-022; demonstrated by `test_wf019_gaps.py` (expectedFailure). |
| F3 | T30(g): the spec lists the gate-log tail as a recoverable exception, but the frozen b2s1 contract chose `gate_log: null` "no gate in R1", and test_t16a_recover_begin.py:112 asserts it is not an exception. A gate now exists (slices 4a/4b). | NEEDS OWNER (spec vs contract after the gate landed) |
| F4 | Observe log path is `jobs/<job>/observe/observe.log` (observe.py:204-206; tests test_t14_observe.py:314-318, test_b2s3_wiring.py:177). Spec §7 says `runtime/observe/<job>.log`. | NEEDS OWNER (spec or contract text) |
| F5 | §8 header "none run" / "Every case is NOT RUN" is stale against 563 passing tests. | NEEDS OWNER (spec edit) |
| F6 | §9 omits T03, T23, T28 and T29, and cites T07 and T12, which have no tests. | NEEDS OWNER (spec edit) |
| F7 | `test_wf.py` uses old FB1.4 ids (test_T01–T10 :103-265; test_T27–T31 :379-497) that collide with spec ids and with spec-numbered tests in test_b1_gaps.py. This misleads traceability. | PROPOSED (rename, or a docstring map in test_wf.py; product test file, outside WF-019's new-files rule) |
| F8 | Existing behaviour with no test: T10 actions widening (validate.py:175-180), T20 empty evidence (validate.py:187-189), T03 never-in-force after rejection, T05 unchanged row count on a plain repeat, T30(i), T28 no lock file, T32 gate after a refused bind, T33(b–d) renewal success, T34 old-hash retry. | PROPOSED (PROPOSED-TESTS.md) |
| F9 | Predicates needing interpretation: T04 "kept as evidence" (a rejection writes no row); T19 "blocker raised" (the gate is read-only on the ledger by design). | NEEDS OWNER |
| F10 | No R1 mechanism, so the required case cannot pass on fixtures: T07 (doorbell deferred), T12 (procedural, live), T34 environment/stance (no command). | NEEDS OWNER (R1 decision: exclude, defer or accept) |
| F11 | Stale docstrings: test_t30_recovery_faults.py:20 ("(f) … (h) … NOT RUN", now covered by test_t30_race_secondwriter_identity); test_t35_remaining_requirements.py:17-21 (R3a "stays failing" in a now-empty register); test_t34_cli_matrix.py:18-19 ("gate halves of T15/T34 (slice 4)" NOT RUN, now in test_gate_decide). | PROPOSED (docstring updates; outside WF-019's new-files rule) |
