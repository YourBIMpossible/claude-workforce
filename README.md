# claude-workforce — development checkout of the Workforce `wf` ledger tool

Private. Source of truth: the authoritative local record on the owner's machine (not this repository). `main` here
is the development baseline; merged files flow back to the authoritative record through the integrator only.
Workers never push to `main`. One PR per lane from `lane/<name>`; no force-push; no rebase of another lane's branch.

## Baseline
Initial commit = tools/wf manifest `9d2210ee082a` (20 files, hashes in `MANIFEST.sha256`; the same list as
`fixtures/results/t09-closure-9d2210ee082a/manifest.txt`). Tag `baseline-9d2210ee082a`.
Tag `dispatch-ready` = the seam commit that the three lanes start from (its manifest is recorded in MANIFEST.sha256
on that commit and in each lane's issue).

## Run the suite
```
python -I -B -m unittest discover -s tools/wf/tests -p "test_*.py" -v
```
Set `WF_TEST_TMP` to a fresh empty directory (default `fixtures/tmp/unittest` under the repo, ignored by git) and
`WF_TEST_LOGDIR` if you want run summaries somewhere other than `tools/wf/tests/logs/` (ignored). The suite was
developed and recorded on Windows only. A Linux run that fails for platform reasons is a BLOCKED result for your
lane: report it with the output in the PR body, do not patch around it.

## Lanes (see OWNERS.md; checker: `python -I tools/check_owned_paths.py --lane A --base dispatch-ready --head HEAD`)
| Lane | Branch | Deliverable | Owned paths |
|---|---|---|---|
| A | lane/a-t24-accept-declarations | T24 accept declaration checks | wfcore/validate.py (`accept_declarations` body), tests/test_t24_accept_declarations.py |
| B | lane/b-t35-path-policy | T35 path policy (P2/P3, resolution) | wfcore/pathpolicy.py, tests/test_t35_path_policy.py |
| C | lane/c-t36-verify-checkpoint | T36 `verify --checkpoint seq:hash` comparison | wfcore/checkpointcmp.py, tests/test_t36_verify_checkpoint.py |
| D | main (integrator, local) | seam, publication, Windows resolution evidence, integration | everything else |

Acceptance source: `FIRST-RELEASE-SPEC.md` (rows T24 / T35 / T36, section 4.4), `04-DECISIONS.md` (D-019 wording),
`protocol/PROTOCOL.md`. These three documents and `MANIFEST.sha256` / `OWNERS.md` are frozen for lanes A-C;
a discrepancy is reported in the PR body.

## Publication notes
Product files (tools/wf/**) are byte-identical to the authoritative record. Documents are publication copies:
`FIRST-RELEASE-SPEC.md` has one redaction (a user-profile path replaced by `C:\Users\<USER>\...`), so its hash
differs from the local original cited in older records (local sha256 409c40b5...). The other documents are unredacted
copies. Runtime data, ledgers, jobs, private evidence and planning records are not published.
