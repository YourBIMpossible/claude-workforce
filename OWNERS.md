# OWNERS — owned paths per lane (owned-path checker reads this table)

Rules: a lane PR may change only paths whose owner is that lane. Everything not listed is owned by D (frozen for A-C).
A needed change outside the owned set is reported in the PR body as BLOCKED, never made. Nobody edits this file,
MANIFEST.sha256, FIRST-RELEASE-SPEC.md, 04-DECISIONS.md or protocol/PROTOCOL.md except lane D.
Glob syntax: fnmatch on forward-slash repo-relative paths; `**` matches any depth.

| Path | Owner |
|---|---|
| tools/wf/wfcore/validate.py | A |
| tools/wf/tests/test_fix2_validation.py | F2BP |
| tools/wf/tests/test_fix2_export.py | F2GL |
| tools/wf/tests/test_fix2_decisions.py | F2RP |
| tools/wf/tests/test_t24_accept_declarations.py | A |
| tools/wf/wfcore/pathpolicy.py | B |
| tools/wf/tests/test_t35_path_policy.py | B |
| tools/wf/tests/test_pathpolicy_hardening.py | B |
| tools/wf/tests/test_t35_remaining_requirements.py | B |
| tools/wf/tests/requirements_t35_unmet.py | B |
| tools/wf/wfcore/changeset.py | S2B |
| tools/wf/tests/test_t35_changeset.py | S2B |
| tools/wf/tests/test_t35_stray_files.py | A |
| tools/wf/tests/test_transaction_sequence_requirements.py | C |
| tools/wf/tests/test_export_temp_preservation.py | A |
| tools/wf/wfcore/checkpointcmp.py | C |
| tools/wf/tests/test_t36_verify_checkpoint.py | C |
| tools/wf/tests/test_checkpoint_clarity.py | C |
| tools/wf/wfcore/export.py | F2YL |
| tools/wf/tests/test_export_tmp_preserve.py | A |
| tools/wf/tests/test_t35_windows_resolution.py | D |
| fixtures/filesystem_qualification/** | FQ |
| tools/qualification/foundation_selector/** | FS |
| fixtures/pilot_rehearsal/** | PR |
| tools/wf/wfcore/bind.py | S2B |
| tools/wf/tests/test_t32_bind.py | S2B |
| tools/wf/tests/test_t33_replacement.py | S2B |
| tools/wf/wfcore/reconcile.py | S4L |
| tools/wf/tests/test_t06_reconcile.py | S4L |
| tools/wf/tests/test_t16a_recover_begin.py | S2R |
| tools/wf/tests/test_t30_recover_close.py | S2R |
| tools/wf/tests/test_t30_recovery_faults.py | S2C |
| tools/wf/wfcore/grant.py | S4G |
| tools/wf/tests/test_t34_grant_lifecycle.py | S4G |
| tools/wf/tests/test_grant_check_corpus.py | S3G |
| tools/wf/tests/data/grant_check_corpus.json | S3G |
| tools/wf/wfcore/observe.py | S4L |
| tools/wf/tests/test_t14_observe.py | S4L |
| tools/wf/tests/test_t34_cli_matrix.py | S3M |
| tools/wf/wfcore/gatecore.py | S4G |
| tools/wf/tests/test_gate_decide.py | S4G |
| tools/wf/tests/test_fix2_gate.py | F2SP |
| tools/wf/tests/test_gate_corpus_replay.py | S4G |
| tools/wf/wfcore/gatelog.py | S4L |
| tools/wf/gate/wf_gate_hook.py | S4L |
| tools/wf/tests/test_gatelog.py | S4L |
| tools/wf/tests/test_gate_hook_deployed.py | S4L |
| tools/wf/tests/test_t19_gate_faults.py | S4M |
| tools/wf/wfcore/backup.py | S5B |
| tools/wf/tests/test_fix2_backup.py | F2CP |
| tools/wf/tests/test_t37_backup.py | S5B |
| tools/wf/tests/test_t30_race_secondwriter_identity.py | S5R |
| tools/wf/tests/test_t37_t36_matrix.py | S5M |
| tools/wf/tests/test_fix1_errors.py | FXL |
| tools/wf/tests/test_fix2_resolve.py | F2AL |
| tools/wf/tests/test_fix1_ids.py | FXL |
| tools/wf/tests/test_fix1_paths.py | FXM |
| tools/wf/tests/test_fix2_scratch_names.py | F2TL |
| tools/wf/wfcore/manifest.py | F2YL |
| tools/wf/tests/test_wf020_adversarial.py | F2YL |
| tools/wf/tests/test_fixy_export.py | F2YL |
| tools/wf/tests/test_fixz_hardening.py | F2ZL |
| tools/wf/tests/test_fixz_links.py | F2ZP |
| tools/wf/tests/test_fixz_wording.py | F2ZZ |
| tools/wf/tests/test_fix2_wording.py | F2HZ |
| tools/wf/tests/test_wf020_*.py | W20 |
| fixtures/results/wf020-lazy/** | W20 |
| tools/wf/tests/test_wf021_*.py | W21 |
| fixtures/results/wf021-mybuddy/** | W21 |
| fixtures/results/wf022-zen/** | W22 |
| tools/wf/tests/test_wf019_*.py | W19 |
| tools/wf/tests/test_wf026_*.py | W19 |
| fixtures/results/wf019-plexy/** | W19 |
| tools/wf/tests/test_wf029_*.py | W29 |
| fixtures/results/wf029-lazy/** | W29 |
| tools/wf/tests/test_wf030_*.py | W30 |
| tools/wf/tests/test_wf034_*.py | W34 |
| fixtures/results/wf047-plexy/** | W47 |
| ** | D |

Lane A owns only the body of `accept_declarations` inside validate.py; other functions in that file are frozen
(enforced by review, not by this checker). B2 slice 1 exception (registered 2026-10-15 22:45, same worker Zen): the S1R
delivery may additionally carry the additive validate.py edit specified verbatim in INTERFACE-CONTRACT.md §7; the S1R
checker run is expected to report exactly one path outside the lane (`tools/wf/wfcore/validate.py`, owner A) and that
path is accepted only if its diff equals the §7 text (verified by the integrator's exact-head review). No other path.

## Sprint 2026-10-15 registration (owner grant "THREE-HOUR WORKFORCE DELIVERY SPRINT")

| Lane | Worker | Branch | Scope added |
|---|---|---|---|
| A | Zen | `sprint/zen-export-temp-preservation` | `tools/wf/wfcore/export.py` (whole file) + `tools/wf/tests/test_export_temp_preservation.py` (new; delivered filename); the earlier A rows (validate.py `accept_declarations`, test_t24) stay registered and are NOT in this sprint's scope |
| B | Plexy | `sprint/plexy-pathpolicy-hardening` (worker-chosen, delivered as PR #11; `lane/b-path-policy-hardening` unused) | B rows + `tools/wf/tests/test_pathpolicy_hardening.py` (new, delivered) |
| C | MyBuddy | `sprint/mybuddy-checkpoint-clarity` (worker-chosen, delivered as PR #10; `lane/c-checkpoint-wording` unused) | C rows + `tools/wf/tests/test_checkpoint_clarity.py` (new, delivered) |
| B2 | Plexy | worker-chosen (follow-on, owner direction 2026-10-15) | `tools/wf/tests/test_t35_remaining_requirements.py` (new, delivered PR #15) and `tools/wf/tests/requirements_t35_unmet.py` (new, separately runnable requirement tests; owner correction 2026-10-15 ~14:50) only |
| C2 | MyBuddy | worker-chosen (follow-on, owner direction 2026-10-15) | `tools/wf/tests/test_transaction_sequence_requirements.py` (new) only |
| B3 (A1+A2) | Plexy | worker-chosen (owner grant 2026-10-15 ~15:05 "T35 REQUIREMENT REPAIRS A1-A3 + T28") | `tools/wf/wfcore/changeset.py` (new), `tools/wf/wfcore/pathpolicy.py`, `tools/wf/tests/test_t35_changeset.py` (new), B test rows; proposed patches for `commands.py` / `test_wf.py` go to White as diffs |
| A2 (A3) | Zen | worker-chosen (same grant) | `tools/wf/wfcore/export.py`, `tools/wf/tests/test_t35_stray_files.py` (new) |
| C3 (T28 2+4) | MyBuddy | worker-chosen (same grant) | `tools/wf/tests/test_transaction_sequence_requirements.py` only |
| W (wiring) | White | `sprint/white-a3-verify-listing` (A3 verify listing, first), `sprint/white-a123-wiring` (A1/A2 job-new + accept, after Plexy's changeset.py) | `tools/wf/tests/test_t35_verify_ledger_dir.py` (new; R3b moved in from `requirements_t35_unmet.py`, whose superseded `test_LIMIT_..._ledger_folder_...` observation in `test_t35_remaining_requirements.py` is removed by the same delivery: the exact verify key-set pins in `test_checkpoint_clarity.py` and `test_t36_verify_checkpoint.py` gain the new `ledger_directory` key (requirement-based correction: the pin fixed the T36 verify shape, not a spec requirement; no checkpoint key is added); integrator move per owner grant "move each repaired requirement assertion into normal discovery in the same delivery"); `tools/wf/wfcore/commands.py` (job_new baseline payload, accept changed-set + P4 coordination wiring, verify ledger-dir listing), shared integration tests (`tools/wf/tests/test_t35_wiring.py`, new: `wf job new` boundary refusal and baseline capture through the CLI; `test_wf.py` T05b fresh-ledger fixture moved from `ledger2/` to `fresh/ledger/` with the justification in its docstring, plus the A2 P4 -> `unclassified` assertion corrections from Plexy's proposed diff in `test_spec_T35_path_classes_P1_to_P5` and `test_T08_full_flow_status_accept_close`); `sprint/white-r3a-move` (integrator move when Zen's A3 landed: `requirements_t35_unmet.py` -> explanatory empty register, the two R3 present-behaviour pins in `test_t35_remaining_requirements.py` corrected to the implemented requirement, both B-owned, register only), OWNERS/MANIFEST |

Branch names are registration only; the checker validates paths by lane letter (`--lane A|B|C`), not branch names.

## Registration 2026-10-15 ~21:20 (owner grant "APPROVE AND EXECUTE — B2 SLICE 1 + PREPARATION INTAKE")

Preparation-intake namespaces (register only; no execution or merge authority is implied by a row):

| Lane | Worker | Branch | Scope added |
|---|---|---|---|
| FQ | MyBuddy | `prep/mybuddy-filesystem-qualification` (PR #25, head 01b105b) | `fixtures/filesystem_qualification/**` (probe, README, results) |
| FS | Plexy | `prep/plexy-foundation-verification` (local commit c795890, to be pushed unchanged) | `tools/qualification/foundation_selector/**` (selector, mapping, self-tests, runs) |
| PR | Zen | `prep/zen-disposable-rehearsal` (head ad3fdd9) | `fixtures/pilot_rehearsal/**` (rehearsal harness, self-test, README) |

B2 slice 1 (bind / reconcile / recover-begin; interface contract INTERFACE-CONTRACT.md frozen by White 2026-10-15 22:40 and published as the GitHub dispatch issue before dependent implementation):

| Lane | Worker | Branch | Scope added |
|---|---|---|---|
| S1B | Plexy | worker-chosen | `tools/wf/wfcore/bind.py` (new), `tools/wf/tests/test_t32_bind.py` (new) only |
| S1R | Zen | worker-chosen | `tools/wf/wfcore/reconcile.py` (new), `tools/wf/tests/test_t06_reconcile.py` (new), `tools/wf/tests/test_t16a_recover_begin.py` (new), plus the additive validate.py edit specified verbatim in INTERFACE-CONTRACT.md §7 (six `ROLE_FOR_TYPE` entries, three `APPEND_TYPES` entries, two dispatch `elif` branches in `validate_content`; no other line of validate.py; registered 2026-10-15 22:45 as an exact scope extension of lane A's existing validate.py row — the contract identified this as the one out-of-lane dependency for required event validation) |
| S1W | White | `sprint/white-b2s1-wiring` | `tools/wf/wfcore/commands.py`, `tools/wf/wf.py` (wiring + CLI registration, lane D paths acted on by the integrator), shared CLI end-to-end tests (`tools/wf/tests/test_b2s1_wiring.py`, new, named in INTERFACE-CONTRACT.md §8), OWNERS/MANIFEST |

Workers do not edit shared wiring files; a needed change outside the owned set is reported in the PR body as BLOCKED, never made.

## Registration 2026-10-16 (owner grant "APPROVE AND EXECUTE — WORKFORCE B2 SLICE 2")

B2 slice 2 (recovery closure + replacement; INTERFACE-CONTRACT-S2.md frozen by White 2026-10-16, published in the WorkForce collab folder `contracts/b2s2/` and mirrored on the GitHub dispatch issue). Slice-1 rows re-labelled to the same workers' slice-2 lanes (S1B→S2B Plexy, S1R→S2R Zen); lane-B rows `changeset.py` / `test_t35_changeset.py` re-labelled S2B (same worker, Plexy; D-S1-5 option B). Registration only; no validation disabled.

| Lane | Worker | Branch | Scope |
|---|---|---|---|
| S2B | Plexy | worker-chosen, from main e628353 or later | `tools/wf/wfcore/bind.py`, `tools/wf/tests/test_t32_bind.py`, `tools/wf/tests/test_t33_replacement.py` (new), `tools/wf/wfcore/changeset.py`, `tools/wf/tests/test_t35_changeset.py` |
| S2R | Zen | worker-chosen, from main e628353 or later | `tools/wf/wfcore/reconcile.py`, `tools/wf/tests/test_t06_reconcile.py`, `tools/wf/tests/test_t16a_recover_begin.py`, `tools/wf/tests/test_t30_recover_close.py` (new) + the additive validate.py edit of INTERFACE-CONTRACT-S2.md §7 (registered exception, same rule as slice 1: the S2R checker run reports exactly `tools/wf/wfcore/validate.py` outside the lane; accepted only if its diff equals the §7 text) |
| S2C | MyBuddy | worker-chosen, from main e628353 or later | `tools/wf/tests/test_t30_recovery_faults.py` (new) only |
| S2W | White | `sprint/white-b2s2-wiring` | `tools/wf/wfcore/commands.py`, `tools/wf/wf.py`, `tools/wf/wfcore/faults.py` (KNOWN names), `tools/wf/tests/test_b2s2_wiring.py` (new), OWNERS.md, MANIFEST.sha256 (lane D) |

## Registration 2026-10-17 (owner grant "APPROVE AND EXECUTE — WORKFORCE B2 SLICE 3")

B2 slice 3 (grant lifecycle and invalidation, grant-check, cancel, observe, log-intervention; INTERFACE-CONTRACT-S3.md frozen by White 2026-10-17, published in the WorkForce collab folder `contracts/b2s3/`, mirrored to MyBuddy and on the GitHub dispatch issue). Registration only; no validation disabled.

| Lane | Worker | Branch | Scope |
|---|---|---|---|
| S3G | Plexy | worker-chosen, from current main | `tools/wf/wfcore/grant.py`, `tools/wf/tests/test_t34_grant_lifecycle.py`, `tools/wf/tests/test_grant_check_corpus.py`, `tools/wf/tests/data/grant_check_corpus.json` (all new) + the additive validate.py edit of INTERFACE-CONTRACT-S3.md section 7 (registered exception: the S3G checker run reports exactly `tools/wf/wfcore/validate.py` outside the lane; accepted only if its diff equals the section 7 text) |
| S3O | Zen | worker-chosen, from current main | `tools/wf/wfcore/observe.py`, `tools/wf/tests/test_t14_observe.py` (new) |
| S3M | MyBuddy | worker-chosen, from current main | `tools/wf/tests/test_t34_cli_matrix.py` (new) only |
| S3W | White | `sprint/white-b2s3-wiring` | `tools/wf/wfcore/commands.py`, `tools/wf/wf.py`, `tools/wf/tests/test_b2s3_wiring.py` (new), OWNERS.md, MANIFEST.sha256 (lane D) |

## Registration 2026-10-17 (owner grant "APPROVE AND EXECUTE — WORKFORCE B2 SLICE 4a")

B2 slice 4a (the gate, build and fixture verification only; INTERFACE-CONTRACT-S4A.md frozen by White 2026-10-17, published in the WorkForce collab folder `contracts/b2s4a/`, mirrored to MyBuddy and on the GitHub dispatch issue). Rows `reconcile.py`, `observe.py`, `test_t06_reconcile.py`, `test_t14_observe.py` re-labelled S4L (same worker, Zen; keyword-parameter consumer edits only). Registration only; no validation disabled. No live hook, settings or profile write is authorised by this registration.

| Lane | Worker | Branch | Scope |
|---|---|---|---|
| S4G | Plexy | worker-chosen, from current main | `tools/wf/wfcore/gatecore.py`, `tools/wf/tests/test_gate_decide.py`, `tools/wf/tests/test_gate_corpus_replay.py` (new) |
| S4L | Zen | worker-chosen, from current main | `tools/wf/wfcore/gatelog.py`, `tools/wf/gate/wf_gate_hook.py`, `tools/wf/tests/test_gatelog.py`, `tools/wf/tests/test_gate_hook_deployed.py` (new); `tools/wf/wfcore/reconcile.py`, `tools/wf/wfcore/observe.py`, `tools/wf/tests/test_t06_reconcile.py`, `tools/wf/tests/test_t14_observe.py` (section 5/6 keyword parameters) |
| S4M | MyBuddy | worker-chosen, from current main | `tools/wf/tests/test_t19_gate_faults.py` (new) only |
| S4W | White | `sprint/white-b2s4a-wiring` | `tools/wf/wfcore/commands.py`, `tools/wf/wf.py`, `tools/wf/tests/test_b2s4a_wiring.py` (new), OWNERS.md, MANIFEST.sha256 (lane D) |

## Registration 2026-10-17 (owner pre-approval "PRE-APPROVE WORKFORCE SLICES 4b AND 5" — slice 5)

B2 slice 5 (backup, restored-copy verification, T30 a-race/f/h, T36 re-check, T37 export tail; fixture-only; INTERFACE-CONTRACT-S5.md frozen by White 2026-10-17, published in `contracts/b2s5/`). Registration only. No ledger schema or transaction change is registered: S5W adds one test-only fault name in `faults.py` and its call site in `commands.py` (both White/D files).

| Lane | Worker | Branch | Scope |
|---|---|---|---|
| S5B | Plexy | worker-chosen, after its slice-4a task | `tools/wf/wfcore/backup.py`, `tools/wf/tests/test_t37_backup.py` (new) |
| S5R | Zen | worker-chosen, after its slice-4a task | `tools/wf/tests/test_t30_race_secondwriter_identity.py` (new) |
| S5M | MyBuddy | worker-chosen, after its slice-4a task | `tools/wf/tests/test_t37_t36_matrix.py` (new) |
| S5W | White | `sprint/white-b2s5-wiring` (after slice 4a merges) | `tools/wf/wfcore/commands.py`, `tools/wf/wf.py`, `tools/wf/wfcore/faults.py`, `tools/wf/docs/RESTORE-RUNBOOK.md` (new), `tools/wf/tests/test_b2s5_wiring.py` (new), OWNERS.md, MANIFEST.sha256 |

## Registration 2026-10-17 (slice 4a AMENDMENT S4A-A2, same worker Plexy)

`tools/wf/wfcore/grant.py` and `tools/wf/tests/test_t34_grant_lifecycle.py` re-labelled S3G → S4G for the AMENDMENT S4A-A2 role-path fix (grant.check compares the target with the role's ARS paths/actions and the binding's declared paths). `tools/wf/tests/data/grant_check_corpus.json` stays S3G and must not change. Registration only.

## Registration 2026-10-17 (slice 4b T25 correction, AMENDMENT S4A-A4, integrator White)

FIRST-RELEASE-SPEC.md T25 and the owner's 4b pre-approval package require Deny for a bound session's hooked write outside the boundary; INTERFACE-CONTRACT-S4A §2 rule 8 / §7 T25 had specified allow `outside-boundary` for every session. AMENDMENT S4A-A4 restores the spec: a bound session's target outside the boundary is deny `out-of-scope`; an unbound session's stays allow `outside-boundary`. The S4G and S4L paths below are acted on by the integrator for this correction only (lane deviation recorded here; Plexy's S4G lane and Zen's S4L lane are otherwise unchanged).

| Lane | Worker | Branch | Scope |
|---|---|---|---|
| S4bW | White | `sprint/white-b2s4b-t25` | `tools/wf/wfcore/gatecore.py` (rule 8 + REASONS `out-of-scope`), `tools/wf/tests/test_gate_decide.py` (T25 test, REASONS test), `tools/wf/tests/test_gate_hook_deployed.py` (one deployed T25 case), OWNERS.md, MANIFEST.sha256 |

## Registration 2026-10-18 (slice 4b T13 outside-write variant, owner-decided deny, integrator White)

Adopted: the owner answered "deny" on 4B-PROCEDURE §10 item 9 (2026-10-18, D-027, AMENDMENT S4A-A5), covering both replaced and stop-confirmed sessions. A session with no current binding but a replaced (`stale-session`) or stop-confirmed (`not-current-binding`) binding on any job is denied every preventable target, including targets outside the boundary and in-root paths in no job (FIRST-RELEASE-SPEC T13, not boundary-limited); on the PR #53 head such writes are allowed `outside-boundary` / `not-mediated`. Current-binding behaviour, never-bound sessions and shell rule 7 are unchanged; no OS-containment or identity-authentication claim. Integrator lane deviation as in the registration above.

| Lane | Worker | Branch | Scope |
|---|---|---|---|
| S4bW | White | `sprint/white-b2s4b-t13-outside` | `tools/wf/wfcore/gatecore.py` (rule 5 identity check before rules 6/8), `tools/wf/tests/test_gate_decide.py` (four T13 tests), OWNERS.md, MANIFEST.sha256 |

## Registration 2026-10-18 (slice 4b gate hook stdout, AMENDMENT S4A-A6, worker MyBuddy, integrator White)

Build-7 Q1b showed Claude Code rejecting the hook's allow stdout (`"decision": "allow"`, "Hook JSON output validation failed"). AMENDMENT S4A-A6 fixes the stdout contract: allow writes exactly `{"reason": r}`, deny writes exactly `{"decision": "block", "reason": r}`; exit 0 allows, exit 2 denies; never `approve`/`allow`, `hookSpecificOutput` or `permissionDecision` on stdout. The decision record moves to the last stderr line (`wf gate: record <json>`). Decisions, reasons and gatecore are unchanged. The S4L paths below are edited by MyBuddy under WF-014 (lane deviation recorded here; Zen's S4L lane is otherwise unchanged); the S4M path is MyBuddy's own lane. Verified by White on Windows: the named test fails on 12a22f5 and passes on ad9dbc8.

| Lane | Worker | Branch | Scope |
|---|---|---|---|
| S4M | MyBuddy | `sprint/mybuddy-wf014-gate-stdout` (PR #55, head ad9dbc8) | `tools/wf/gate/wf_gate_hook.py` (`_emit`), `tools/wf/tests/test_gate_hook_deployed.py` (A6 conformance on every run + named test), `tools/wf/tests/test_t19_gate_faults.py` (record read from stderr) |
| S4bW | White | main | OWNERS.md, MANIFEST.sha256 |

## Registration 2026-10-19 (owner grant "fix batch approved", package FIX1; coordinator Zen "Workforce Dev" while White is out)

Package text: WorkForce collab `contracts/b2fix1/PACKAGE-FIX1.md` (frozen with SHA256SUMS). Base: main 2dedc94 plus this registration commit.

| Lane | Worker | Branch | Scope |
|---|---|---|---|
| FXL | Lazy | `fix1/lazy-errors` (WF-023), then `fix1/lazy-ids` stacked on it (WF-025) | the FXL rows above; relabelled from A (validate.py), S4G (gatecore.py, test_gate_decide.py) and D (wf.py, commands.py, canon.py, markers.py, manifest.py, txn.py, README.md, .gitattributes) for this package only |
| FXM | MyBuddy | `fix1/mybuddy-paths` (WF-024) | the FXM rows above; relabelled from B (pathpolicy.py), S4L (reconcile.py), S4G (grant.py), S5B (backup.py), S2B (changeset.py) for this package only |
| W20 / W21 / W22 | Lazy / MyBuddy / Zen | `wf020/lazy`, `wf021/mybuddy`, `wf022/zen` (existing evidence branches) | registers the long-run evidence files (WF-020 F20) |

Rows are restored to their previous owners when FIX1 is integrated, in the same commit that updates MANIFEST.sha256.

## Integration 2026-10-19 (owner grant G1 "Approve FIX1 integration", typed in the coordinator session; integrator Zen "Workforce Dev", lane D)

Branch `fix1/integration` combines `fix1/lazy-ids` (FXL, includes `fix1/lazy-errors`) and `fix1/mybuddy-paths` (FXM). The FIX1-only relabels are restored to their previous owners (validate.py A; gatecore.py, test_gate_decide.py, grant.py S4G; pathpolicy.py B; reconcile.py S4L; backup.py S5B; changeset.py S2B); the FXL rows for lane-D files (wf.py, commands.py, canon.py, markers.py, manifest.py, txn.py, tools/wf/README.md, .gitattributes) are removed, so they fall back to D. The new test files stay registered (FXL, FXM) and the evidence rows W20/W21/W22 stay.
Integrator edits under G1: the only paths outside lane D are the two lane-B test files, where AMENDMENT FIX1-A2.3 pins three tests (`test_pathpolicy_hardening.py` x2, `test_t35_path_policy.py` x1) to the Windows case rule with `mock.patch.object(pathpolicy, "CASE_FOLDING", True)`. In lane D: `tools/wf/docs/RESTORE-RUNBOOK.md` gains `--db` (FIX1 I3) and MANIFEST.sha256 is regenerated over the integrated tree.
Record correction (WF-031 review): the registration note above planned the relabel restore "in the same commit that updates MANIFEST.sha256"; in fact the relabels were restored in 7f3337b and MANIFEST.sha256 was regenerated in 38a1f43, and again after the review merges below.
Review merges (independent review WF-027): `fix1/lazy-ids` 5c47587 (review R: R1, S1-S6) and `fix1/mybuddy-paths` 3a6dd00 (review R2/R3) are merged on top of 38a1f43, followed by this text fix and a regenerated MANIFEST.sha256.

## Registration 2026-10-19 (package FIX2 batch T; decided by the Foreman under the owner's delegation; coordinator Zen "Workforce Dev" while White is out)

Package text: WorkForce collab `contracts/b2fix2/PACKAGE-FIX2.md` (frozen with SHA256SUMS). Base: main `4321f16` (the PR #56 merge commit; its tree equals `fix1/integration` dfd6e85) plus this registration commit.

| Lane | Worker | Branch | Scope |
|---|---|---|---|
| F2TL | Lazy | `fix2/lazy-t40` | the F2TL rows above (FIX2 #40); relabelled from D (test_wf.py) for this batch only |
| F2TP | Plexy | `fix2/plexy-t-hygiene` | the F2TP rows above (FIX2 #38, #43, #49); relabelled from S4G (test_gate_decide.py), S5B (test_t37_backup.py), FXL (test_fix1_ids.py) and D (test_t35_wiring.py) for this batch only |

Rows go back to their previous owners when batch T is integrated.

## Integration 2026-10-19 (FIX2 batch T into `fix2/integration`; coordinator Zen "Workforce Dev", lane D)

Branch `fix2/integration` (from main `4321f16`) merges `fix2/plexy-t-hygiene` `5133101` (F2TP) and `fix2/lazy-t40` `9a4e70d` (F2TL), each VERIFIED (independent review MyBuddy WF-091, Linux MyBuddy, Windows Zen). The batch-T relabels are restored: test_gate_decide.py S4G, test_t37_backup.py S5B, test_fix1_ids.py FXL; the test_t35_wiring.py and test_wf.py rows are removed, so they fall back to D. The new test file `test_fix2_scratch_names.py` stays registered to F2TL. MANIFEST.sha256 is regenerated at the cumulative head before the PR.

## Registration 2026-10-19 (package FIX2 batch B, AMENDMENT FIX2-A2; decided by the Foreman under the owner's delegation; coordinator Zen "Workforce Dev" while White is out)

Package text: WorkForce collab `contracts/b2fix2/` (PACKAGE-FIX2.md with AMENDMENT-FIX2-A2.md, frozen with SHA256SUMS). Base: main `4321f16` plus this registration commit.

| Lane | Worker | Branch | Scope |
|---|---|---|---|
| F2BP | Plexy | `fix2/plexy-b-validation` | the F2BP rows above (FIX2 #2, #19, #20, #30, #33); relabelled from A (validate.py), FXL (test_fix1_ids.py, for #30's `CASE_FOLDING` patches at :286-295) and D (commands.py, wf.py) for this batch only |

Rows go back to their previous owners when batch B is integrated.

## Integration 2026-10-19 (FIX2 batch B into `fix2/integration`; coordinator Zen "Workforce Dev", lane D)

`fix2/integration` merges `fix2/plexy-b-validation` `fc16399` (F2BP), VERIFIED (independent review Lazy WF-099, Linux MyBuddy WF-100, Windows Zen). The merge's OWNERS.md conflict (both registrations append a section) is resolved as the T-restored file plus batch B's registration exactly as `fix2/b-registration` `a03f8ed` wrote it; this commit then restores the batch-B relabels: validate.py A, test_fix1_ids.py FXL; the commands.py and wf.py rows are removed, so they fall back to D. The new test file `test_fix2_validation.py` stays registered to F2BP.

## Integration 2026-10-19 (G2/G2b evidence branches, PR #57 merged as `52253dc`; recorded here per review WF-093 N1; coordinator Zen "Workforce Dev", lane D)

Decided by the Foreman under the owner's delegation; merged by the Foreman on the owner's direct instruction. `evidence/integration` (from main `4321f16`) merged exactly these pinned heads: `wf019/plexy` `2f49efc`, `wf021b/mybuddy` `76de7dc`, `wf029/lazy` `96c0756`, `wf030/mybuddy` `1ddc840`, `wf034/lazy` `0ec48b6`, `wf022/zen` `c92340c`, `wf047/plexy` `13cf6b4` (G2) and `wf020b/lazy` `b3edb9b` (G2b). The rows W19/W29/W30/W34/W47 were added from Plexy's WF-054 draft OWNERS-G2.md. Exception: `wf019/plexy` edits four existing test files docstring-only (`test_wf.py`, `test_t30_recovery_faults.py`, `test_t34_cli_matrix.py`, `test_t35_remaining_requirements.py`), accepted because their ASTs with docstrings stripped are equal at `4321f16` and `b77c628` (MyBuddy WF-093 check 5). Lane W20 owns the reworked `wf020b/lazy` tests and W21 the `wf021b/mybuddy` tests: the FIX1 registration table above still names the earlier branches `wf020/lazy` and `wf021/mybuddy`, which G2/G2b superseded.

## Integrator edit 2026-10-19 (AMENDMENT FIX2-A6: W20 pins superseded by FIX2 #2; coordinator Zen "Workforce Dev", lane D)

The cumulative suite at `d0200e0` (main `52253dc` with batches T and B) errored in two lane-W20 characterization tests that pinned the pre-FIX2 behaviour FIX2 #2 changes: `test_wf020_adversarial.ContentEncodings.test_duplicate_keys_last_one_wins_silently` and `.test_large_event_body_from_a_file_is_committed`. Under A6 the integrator flips them to assert the new behaviour, renamed `test_duplicate_keys_are_refused` and `test_large_event_body_from_a_file_is_bounded` (`rejected(malformed)`, no row written, the ledger verifies; a large but legal body under the bound is still committed, per review WF-104 M1 and AMENDMENT FIX2-A8). This is the only edit to a W20 path; it is reviewed in WF-104 with the other integration commits.

## Registration 2026-10-19 (package FIX2 batch C, AMENDMENT FIX2-A3; decided by the Foreman under the owner's delegation; coordinator Zen "Workforce Dev" while White is out)

Package text: WorkForce collab `contracts/b2fix2/` (PACKAGE-FIX2.md with AMENDMENT-FIX2-A3.md, frozen with SHA256SUMS). Base: main `4321f16` plus this registration commit.

| Lane | Worker | Branch | Scope |
|---|---|---|---|
| F2CP | Plexy | `fix2/plexy-c-backup` | the F2CP rows above (FIX2 #3, #22+#31, #23, #26+#32, #28, #50); relabelled from S5B (backup.py) and D (commands.py, for #50 at about :1224 only) for this batch only |

Rows go back to their previous owners when batch C is integrated.

## Integration 2026-10-19 (FIX2 batch C into `fix2/integration`, merge window 2; coordinator Zen "Workforce Dev", lane D)

`fix2/integration` merges `fix2/plexy-c-backup` `8b91192` (F2CP), VERIFIED (independent review MyBuddy WF-105 and delta WF-108, Linux 658 OK, Windows Zen 658 OK). AMENDMENT FIX2-A5 widened F2CP's commands.py scope from #50 only to #50 and #3, both in `backup_cmd` (review WF-105 N3). The relabels are restored: backup.py S5B; the commands.py row is removed (back to D). The new test file `test_fix2_backup.py` stays registered to F2CP. Integrator edits under A5 section 3 (lanes W30/W29, recorded here): the expected-failure decorators of `test_wf030_backup_edges` `test_1c_…` (W30-F1), `test_3a_…` (W30-F3) and `test_2b_…` (W30-F2, its flip shown on Linux by WF-105) and of `test_wf029_store` `test_busy_copy_check_is_a_lock_timeout_refusal` (W29-F1) are removed, and the pin `test_busy_copy_check_today_is_an_internal_error` (today's exit 4, superseded by #28) is removed. `test_5b_…` (W30-F4) stays an expected failure.

## Registration 2026-10-19 (package FIX2 batch S, AMENDMENT FIX2-A7; decided by the Foreman under the owner's delegation; coordinator Zen "Workforce Dev" while White is out)

Package text: WorkForce collab `contracts/b2fix2/` (PACKAGE-FIX2.md with AMENDMENT-FIX2-A7.md) and `contracts/b2s4a/AMENDMENTS.md` S4A-A6 (frozen with SHA256SUMS). Base: `fix2/integration` `7d76863` (main `52253dc` plus FIX2 batches T and B) plus this registration commit.

| Lane | Worker | Branch | Scope |
|---|---|---|---|
| F2SP | Plexy | `fix2/plexy-s-gate` | the F2SP rows above (FIX2 #4, #5, #56; test_gate_decide.py :40/:559 to `self.scratch`); relabelled from S4G (gatecore.py, test_gate_decide.py) and D (commands.py, for #5's `gate_print_install` only) for this batch only |

Rows go back to their previous owners when batch S is integrated.

## Integration 2026-10-19 (FIX2 batch S into `fix2/integration`, merge window 2; coordinator Zen "Workforce Dev", lane D)

`fix2/integration` merges `fix2/plexy-s-gate` `3ee0344` (F2SP: #4 with review F1/F2, #5, #56 per S4A-A6, S-add), VERIFIED (independent review MyBuddy WF-110, Linux 930 OK, Windows Zen 930 OK). The relabels are restored: gatecore.py S4G, test_gate_decide.py S4G; the commands.py row is removed (back to D). The new test file `test_fix2_gate.py` stays registered to F2SP. Deferred to batch Z with a batch-D doc line: rule 1 follows a link on the root's anchor before rule 0's link refusal (WF-110 S1).

## Registration 2026-10-19 (package FIX2 batch A, AMENDMENT FIX2-A4; decided by the Foreman under the owner's delegation; coordinator Zen "Workforce Dev" while White is out)

Package text: WorkForce collab `contracts/b2fix2/` (PACKAGE-FIX2.md with AMENDMENT-FIX2-A4.md, frozen with SHA256SUMS). Base: main `4321f16` plus this registration commit.

| Lane | Worker | Branch | Scope |
|---|---|---|---|
| F2AL | Lazy | `fix2/lazy-a-resolve` | the F2AL rows above (FIX2 #1, #18, #21, #46, #47, #48); relabelled from D (markers.py, txn.py; wf.py for #21's auto-resolve handler at about :257-261 only, AMENDMENT FIX2-A9) for this batch only |

Rows go back to their previous owners when batch A is integrated.

## Integration 2026-10-19 (FIX2 batch A into `fix2/integration`, merge window 2; coordinator Zen "Workforce Dev", lane D)

`fix2/integration` merges `fix2/lazy-a-resolve` `52f7e64` (F2AL: #1, #18, #21 in wf.py per A9, #46, #47, #48, and the WF-109 M1 conflict-marker fix including probe B), VERIFIED (independent review Plexy WF-109 and its delta review, Linux MyBuddy WF-111 663 OK, Windows Zen 663 OK). The relabels are restored: the markers.py, txn.py and wf.py rows are removed (back to D). The new test file `test_fix2_resolve.py` stays registered to F2AL.

## Integrator edit 2026-10-19 (AMENDMENT FIX2-A13: W20 tests meeting FIX2 batch A #1; coordinator Zen "Workforce Dev", lane D)

The cumulative Windows suite at `d2f4579` (window 2: C+S+A) failed `test_wf020_txn.ResolveOutcomes.test_incomplete_conflict_marker_stays_uncertain` (it pinned the pre-FIX2 reason text "conflict marker incomplete: 'attempted_msg_id'"; batch A #1 unified the reason to "conflict marker malformed: … attempted_msg_id …", the behaviour, STILL-UNCERTAIN with the marker kept, unchanged) and reported an unexpected success for `test_empty_conflict_payload_marker_not_dropped` (lane W20 finding F8, fixed by batch A #1). Under A13 the integrator asserts the new reason wording while still requiring the missing field to be named, and removes the F8 expected-failure decorator. These are the only edits to that W20 file.

## Registration 2026-10-19 (package FIX2 batch G, AMENDMENT FIX2-A10; decided by the Foreman under the owner's delegation; coordinator Zen "Workforce Dev" while White is out)

Package text: WorkForce collab `contracts/b2fix2/` (PACKAGE-FIX2.md with AMENDMENT-FIX2-A10.md, frozen with SHA256SUMS). Base: `fix2/integration` `ebfd6fc` (main `52253dc` plus FIX2 batches T and B and the A6/A8 integrator edits) plus this registration commit.

| Lane | Worker | Branch | Scope |
|---|---|---|---|
| F2GL | Lazy | `fix2/lazy-g-export` | the F2GL rows above (FIX2 #45 option (b), #51, #54, and #61's export.py `_STAGING` anchor); relabelled from A (export.py), D (txn.py, for the export refresh call only; test_b1_gaps.py, for #54 only) and W20 (test_wf020_adversarial.py, for the ExportRefreshRace expected-failure decorators and the WF-064 S1 one-line change only) for this batch only |

Rows go back to their previous owners when batch G is integrated.

## Integration 2026-10-19 (FIX2 batch G into `fix2/integration`, merge window 2; coordinator Zen "Workforce Dev", lane D)

`fix2/integration` merges `fix2/lazy-g-export` `c6d0dc7` (F2GL: #45 option (b), #51, #54, #61's `_STAGING` anchor, and R5 under AMENDMENT FIX2-A11/A12), reviewed (independent review Plexy WF-114 PASS; Windows Zen 907 OK, Windows Plexy 907 OK). Linux at `c6d0dc7` waits for MyBuddy and is required before the window-2 PR (Foreman decision (a)). The relabels are restored: export.py A; the txn.py, test_b1_gaps.py and test_wf020_adversarial.py rows are removed (back to D, D and W20). The new test file `test_fix2_export.py` stays registered to F2GL. Recorded outside-lane exception: `tools/wf/tests/test_transaction_sequence_requirements.py` (owner C) changed inside `Tracked` and at :350-351 only, as AMENDMENT FIX2-A11/A12 allow.

## Registration 2026-10-19 (package FIX2 batch H, AMENDMENT FIX2-A15; decided by the Foreman under the owner's delegation; coordinator Zen "Workforce Dev" while White is out)

Package text: WorkForce collab `contracts/b2fix2/` (PACKAGE-FIX2.md with AMENDMENT-FIX2-A15.md, frozen with SHA256SUMS). Base: `fix2/integration` `288cdba` (main `74006c4` plus FIX2 batches C, S, A, D and G) plus this registration commit.

| Lane | Worker | Branch | Scope |
|---|---|---|---|
| F2HZ | Zen | `fix2/zen-h-wording` | the F2HZ rows above (FIX2 #39 with #53, #41, and A4's duplicate-key path quoting); relabelled from D (txn.py, for `write_contracts_update` only; commands.py, for `_unique_keys`, the digest's unreconciled-role entry and the `job abandon` call only) and W20 (test_wf020_txn.py, for `test_abandon_then_abandon_again` only) for this batch only |

Rows go back to their previous owners when batch H is integrated. The new test file `test_fix2_wording.py` stays registered to F2HZ.

## Integration 2026-10-20 (FIX2 batch H into `fix2/integration`, merge window 3; coordinator Zen "Workforce Dev", lane D)

`fix2/integration` (from main `e4d4dd8`, PR #59) merges `fix2/zen-h-wording` `475cc7d` (F2HZ: #39 with #53, #41, A4 N3), VERIFIED (independent review Lazy WF-120 PASS, which is also the W20 author confirmation for `test_abandon_then_abandon_again`; Windows Zen 973 OK, Lazy 973 OK; Linux MyBuddy WF-122 973 OK). The relabels are restored: the txn.py, commands.py and test_wf020_txn.py rows are removed (back to D, D and the `test_wf020_*.py` W20 glob). The new test file `test_fix2_wording.py` stays registered to F2HZ. Recorded per Lazy WF-120 N2: the two module-level helpers `commands._key_path` and `commands._replacement_blocker` were added within A15's scope, each reached only from its A15 place (`_unique_keys`; the digest's unreconciled-role entry).

## Registration 2026-10-19 (package FIX2 batch R, AMENDMENT FIX2-A14; decided by the Foreman under the owner's delegation; coordinator Zen "Workforce Dev" while White is out)

Package text: WorkForce collab `contracts/b2fix2/` (PACKAGE-FIX2.md with AMENDMENT-FIX2-A14.md, frozen with SHA256SUMS), with AMENDMENT S1-G1 (`contracts/b2s1/`) and AMENDMENT S3-A3 (`contracts/b2s3/AMENDMENTS.md`). Base: `fix2/integration` `288cdba` (main `74006c4` plus FIX2 batches C, S, A, D and G) plus this registration commit.

| Lane | Worker | Branch | Scope |
|---|---|---|---|
| F2RP | Plexy | `fix2/plexy-r-decisions` | the F2RP rows above (FIX2 #35 option (A) in validate.py; #36 option (A) with #9 in observe.py; #37 option (A) with #8 and #52 in reconcile.py); relabelled from A (validate.py), S4L (reconcile.py, observe.py, test_t14_observe.py), S2R (test_t16a_recover_begin.py) D (test_b2s3_wiring.py, for the observe log-path assertion only) and W21 (test_wf021_fs_semantics.py, for row 36's observe test only, AMENDMENT FIX2-A16) for this batch only |

Rows go back to their previous owners when batch R is integrated. The new test file `test_fix2_decisions.py` stays registered to F2RP.

## Integration 2026-10-20 (FIX2 batch R into `fix2/integration`, merge window 3; coordinator Zen "Workforce Dev", lane D)

`fix2/integration` merges `fix2/plexy-r-decisions` `2b2ce25` (F2RP: #35 option (A); #36 option (A) with #9 under AMENDMENT S3-A3; #37 option (A) with #8 and #52 under AMENDMENT S1-G1; A16's row-36 test), VERIFIED (independent review Lazy WF-123 PASS; Windows Zen 981 OK, Plexy 981 OK, Lazy 981 OK; Linux MyBuddy WF-124 981 OK). The relabels are restored: validate.py A; reconcile.py, observe.py and test_t14_observe.py S4L; test_t16a_recover_begin.py S2R; the test_b2s3_wiring.py and test_wf021_fs_semantics.py rows are removed (back to D and the `test_wf021_*.py` W21 glob). The new test file `test_fix2_decisions.py` stays registered to F2RP. Recorded: the W21 author confirmation of the row-36 replacement (MyBuddy, AMENDMENT FIX2-A16); per Lazy WF-123 N1, the module-level helper `reconcile._gate_log_problem` (and its `gatelog` import) was added within #37's scope, reached only from `recoverable_exceptions`.

## Registration 2026-10-20 (package FIX2 batch Z, AMENDMENT FIX2-A17; decided by the Foreman under the owner's delegation; coordinator Zen "Workforce Dev" while White is out)

Package text: WorkForce collab `contracts/b2fix2/` (PACKAGE-FIX2.md with AMENDMENT-FIX2-A17.md, frozen with SHA256SUMS). Base: main `61854c7` (PR #60; every approved FIX2 batch) plus this registration commit.

| Lane | Worker | Branch | Scope |
|---|---|---|---|
| F2ZL | Lazy | `fix2/lazy-z-hardening` | Z2 (test_wf020_adversarial.py ExportRefreshRace docstrings only), Z3 (export.py: the bounded sharing-violation retry in `_write_atomic`'s replace and `check`'s reads only), Z7 (test only), and the new test_fixz_hardening.py; relabelled from A (export.py) and W20 (test_wf020_adversarial.py) |
| F2ZP | Plexy | `fix2/plexy-z-links` | Z6 (gatecore.py: rule 1's gate-file identity resolution only, spec :194) and the new test_fixz_links.py; relabelled from S4G (gatecore.py) |
| F2ZZ | Zen | `fix2/zen-z-wording` | Z1 (commands.py `_replacement_blocker` wording only), Z4 (validate.py: an intent's `sha_before` format only), Z5 (test_wf021_fs_semantics.py: the row-36 test name only), and the new test_fixz_wording.py; relabelled from A (validate.py), D (commands.py) and W21 (test_wf021_fs_semantics.py) |

Rows go back to their previous owners when batch Z is integrated. The new test files stay registered to their lanes.

## Integration 2026-10-20 (FIX2 batch Z into `fix2/integration`, merge window 4; coordinator Zen "Workforce Dev", lane D)

`fix2/integration` (from main `61854c7`, PR #60) merges `fix2/lazy-z-hardening` `fb8ec0c` (F2ZL: Z2, Z3, Z7), `fix2/zen-z-wording` `ea7482c` (F2ZZ: Z1, Z4, Z5) and `fix2/plexy-z-links` `1a3be16` (F2ZP: Z6 under AMENDMENTs FIX2-A18, A19, A20 and A21), each VERIFIED (independent reviews Plexy WF-130, Plexy WF-134, Lazy WF-132/WF-135/WF-135b; exact-head Windows runs per the Foreman's rule; Linux MyBuddy 36c96167 WF-131, WF-136/WF-137). The relabels are restored: validate.py A, export.py A, gatecore.py S4G; the test_wf020_adversarial.py, commands.py and test_wf021_fs_semantics.py rows are removed (back to the W20 glob, D and the W21 glob). The new test files stay registered: test_fixz_hardening.py F2ZL, test_fixz_wording.py F2ZZ, test_fixz_links.py F2ZP. Recorded for later (not in batch Z): Lazy WF-127 F1 (the read retry cannot fire through `open`), Plexy WF-130 R1/R2, the stale `_race` comment at test_wf020_adversarial.py:292, Plexy WF-134's two notes.

## Registration 2026-10-20 (package FIX2 batch Y, AMENDMENT FIX2-A22; decided by the Foreman under the owner's delegation (W-NIGHT); coordinator Zen "Workforce Dev" while White is out)

Package text: WorkForce collab `contracts/b2fix2/` (PACKAGE-FIX2.md with AMENDMENT-FIX2-A22.md, frozen with SHA256SUMS). Base: main `7777711` (PR #61; FIX2 including batch Z) plus this registration commit.

| Lane | Worker | Branch | Scope |
|---|---|---|---|
| F2YL | Lazy | `fix2/lazy-y-export` | the F2YL rows above (Y1 the read retry that can fire, Y2 the per-refresh retry budget, Y3 an atomic retried manifest write, Y4 the stale `_race` comment only in test_wf020_adversarial.py); relabelled from A (export.py), D (manifest.py) and W20 (test_wf020_adversarial.py) for this batch only |

Rows go back to their previous owners when batch Y is integrated. The new test file `test_fixy_export.py` stays registered to F2YL.
