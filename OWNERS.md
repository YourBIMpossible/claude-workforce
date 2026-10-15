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
| tools/wf/wfcore/changeset.py | B |
| tools/wf/tests/test_t35_changeset.py | B |
| tools/wf/tests/test_t35_stray_files.py | A |
| tools/wf/tests/test_transaction_sequence_requirements.py | C |
| tools/wf/tests/test_export_temp_preservation.py | A |
| tools/wf/wfcore/checkpointcmp.py | C |
| tools/wf/tests/test_t36_verify_checkpoint.py | C |
| tools/wf/tests/test_checkpoint_clarity.py | C |
| tools/wf/wfcore/export.py | A |
| tools/wf/tests/test_export_tmp_preserve.py | A |
| tools/wf/tests/test_t35_windows_resolution.py | D |
| ** | D |

Lane A owns only the body of `accept_declarations` inside validate.py; other functions in that file are frozen
(enforced by review, not by this checker).

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
| W (wiring) | White | `sprint/white-a3-verify-listing` (A3 verify listing, first), `sprint/white-a123-wiring` (A1/A2 job-new + accept, after Plexy's changeset.py) | `tools/wf/tests/test_t35_verify_ledger_dir.py` (new; R3b moved in from `requirements_t35_unmet.py`, whose superseded `test_LIMIT_..._ledger_folder_...` observation in `test_t35_remaining_requirements.py` is removed by the same delivery: the exact verify key-set pins in `test_checkpoint_clarity.py` and `test_t36_verify_checkpoint.py` gain the new `ledger_directory` key (requirement-based correction: the pin fixed the T36 verify shape, not a spec requirement; no checkpoint key is added); integrator move per owner grant "move each repaired requirement assertion into normal discovery in the same delivery"); `tools/wf/wfcore/commands.py` (job_new baseline payload, accept changed-set + P4 coordination wiring, verify ledger-dir listing), shared integration tests (`tools/wf/tests/test_t35_wiring.py`, new: `wf job new` boundary refusal and baseline capture through the CLI; `test_wf.py` T05b fresh-ledger fixture moved from `ledger2/` to `fresh/ledger/` with the justification in its docstring, plus fixture corrections from Plexy's diff), OWNERS/MANIFEST |

Branch names are registration only; the checker validates paths by lane letter (`--lane A|B|C`), not branch names.
