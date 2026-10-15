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
| A | Zen | `lane/a-export-tmp-preserve` | `tools/wf/wfcore/export.py` (whole file) + `tools/wf/tests/test_export_tmp_preserve.py` (new); the earlier A rows (validate.py `accept_declarations`, test_t24) stay registered and are NOT in this sprint's scope |
| B | Plexy | `sprint/plexy-pathpolicy-hardening` (worker-chosen, delivered as PR #11; `lane/b-path-policy-hardening` unused) | B rows + `tools/wf/tests/test_pathpolicy_hardening.py` (new, delivered) |
| C | MyBuddy | `sprint/mybuddy-checkpoint-clarity` (worker-chosen, delivered as PR #10; `lane/c-checkpoint-wording` unused) | C rows + `tools/wf/tests/test_checkpoint_clarity.py` (new, delivered) |

Branch names are registration only; the checker validates paths by lane letter (`--lane A|B|C`), not branch names.
