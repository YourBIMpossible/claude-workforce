# OWNERS — owned paths per lane (owned-path checker reads this table)

Rules: a lane PR may change only paths whose owner is that lane. Everything not listed is owned by D (frozen for A-C).
A needed change outside the owned set is reported in the PR body as BLOCKED, never made. Nobody edits this file,
MANIFEST.sha256, FIRST-RELEASE-SPEC.md, 04-DECISIONS.md or protocol/PROTOCOL.md except lane D.
Glob syntax: fnmatch on forward-slash repo-relative paths; `**` matches any depth.

| Path | Owner |
|---|---|
| tools/wf/wfcore/validate.py | A |
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
| tools/wf/wfcore/export.py | A |
| tools/wf/tests/test_export_tmp_preserve.py | A |
| tools/wf/tests/test_t35_windows_resolution.py | D |
| fixtures/filesystem_qualification/** | FQ |
| tools/qualification/foundation_selector/** | FS |
| fixtures/pilot_rehearsal/** | PR |
| tools/wf/wfcore/bind.py | S2B |
| tools/wf/tests/test_t32_bind.py | S2B |
| tools/wf/tests/test_t33_replacement.py | S2B |
| tools/wf/wfcore/reconcile.py | S2R |
| tools/wf/tests/test_t06_reconcile.py | S2R |
| tools/wf/tests/test_t16a_recover_begin.py | S2R |
| tools/wf/tests/test_t30_recover_close.py | S2R |
| tools/wf/tests/test_t30_recovery_faults.py | S2C |
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

B2 slice 2 (recovery closure + replacement; INTERFACE-CONTRACT-S2.md frozen by White 2026-10-16, published in the WorkForce collab folder `contracts2s2\` and mirrored on the GitHub dispatch issue). Slice-1 rows re-labelled to the same workers' slice-2 lanes (S1B→S2B Plexy, S1R→S2R Zen); lane-B rows `changeset.py` / `test_t35_changeset.py` re-labelled S2B (same worker, Plexy; D-S1-5 option B). Registration only; no validation disabled.

| Lane | Worker | Branch | Scope |
|---|---|---|---|
| S2B | Plexy | worker-chosen, from main 3590a9a | `tools/wf/wfcore/bind.py`, `tools/wf/tests/test_t32_bind.py`, `tools/wf/tests/test_t33_replacement.py` (new), `tools/wf/wfcore/changeset.py`, `tools/wf/tests/test_t35_changeset.py` |
| S2R | Zen | worker-chosen, from main 3590a9a | `tools/wf/wfcore/reconcile.py`, `tools/wf/tests/test_t06_reconcile.py`, `tools/wf/tests/test_t16a_recover_begin.py`, `tools/wf/tests/test_t30_recover_close.py` (new) + the additive validate.py edit of INTERFACE-CONTRACT-S2.md §7 (registered exception, same rule as slice 1: the S2R checker run reports exactly `tools/wf/wfcore/validate.py` outside the lane; accepted only if its diff equals the §7 text) |
| S2C | MyBuddy | worker-chosen, from main 3590a9a | `tools/wf/tests/test_t30_recovery_faults.py` (new) only |
| S2W | White | `sprint/white-b2s2-wiring` | `tools/wf/wfcore/commands.py`, `tools/wf/wf.py`, `tools/wf/wfcore/faults.py` (KNOWN names), `tools/wf/tests/test_b2s2_wiring.py` (new), OWNERS.md, MANIFEST.sha256 (lane D) |
