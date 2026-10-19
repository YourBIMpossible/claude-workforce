# WF-019 — Proposed tests for PARTIAL / NONE cases (Plexy, proposals only)

Base: `2dedc9424abae7930bd82a75c45c235600f0ebcd`. Nothing here is implemented except the one expected-failure test in `tools/wf/tests/test_wf019_gaps.py` (branch `wf019/plexy`). All sketches use the existing `test_wf.Base` harness (fresh `WF_TEST_TMP`, `run_wf`, `new_job`, `activate`, `intent`) unless noted.

Each proposal has a name, setup and assertion, plus:
- **Kind A:** existing behaviour, untested. The test should pass as written.
- **Kind B:** behaviour absent or contradicting the spec. The test would fail today and needs a product change or an owner decision first.

## F1 — corpus pin independent of checkout line endings (test fix; my S4G test)
- `test_gate_corpus_replay.test_corpus_is_unchanged_from_main`: hash the file's bytes with CRLF normalised to LF (`data.replace(b"\r\n", b"\n")`) before comparing with `MAIN_CORPUS_SHA256`. Alternatively, add a `.gitattributes` line `tools/wf/tests/data/*.json text eol=lf`.
- Kind A once fixed.
- Evidence: CRLF checkout `7536da5c…`, LF `103e0e14…`, full-suite failure in step 1.
- Note: the gate-equals-grant-check replay in the same file already passes, because it parses JSON.

## T03 — never in force right after `no-provenance`
- `test_T03_rejected_activation_leaves_no_authority`: propose; activate with `--turn ""`.
- Assert:
  - `rejected(no-provenance)`
  - `status j` authority is `proposed`, not `in-force`
  - `grant-check` denies with `authority-proposed`
  - head seq unchanged
- Kind A.

## T05 — one effect on a plain repeat
- `test_T05_same_intent_twice_one_row`: `intent(msg_id="M1")` twice.
- Assert:
  - second outcome `DUPLICATE-IGNORED`
  - `dbm.rows` count and head unchanged
  - `verify` clean
- Kind A. "Logged" stays the returned outcome unless the owner defines a persisted log.

## T07 — durable message processed from the ledger on next read
- `test_T07_event_read_back_by_a_fresh_process`: append a handoff in one `wf` process; start a fresh `python -I wf.py status`/`digest` process.
- Assert the handoff (seq, content) is present and the chain verifies.
- Kind A for durability. The doorbell part is spec-deferred (F10, owner).

## T10 — authority widening via handoff actions
- `test_T10_handoff_action_outside_activated_grant_is_widening`: activate a grant with actions `["write"]`; append a handoff with `actions: ["deploy"]` and an in-scope `write_scope`.
- Assert `rejected(widening)` (validate.py:175-180) and head unchanged.
- Kind A.

## T11 — reconcile accurate after mid-job revocation/cancel
- `test_T11_reconcile_after_revoke_classifies_in_flight_work`: builder writes a work file with intent + receipt; owner revokes; run `wf reconcile j`.
- Assert:
  - the report classifies the file as the receipt says (matches-receipt)
  - gate `decide` for the next Write is `authority-revoked`
- Kind A. The hook-level half belongs in the test_gate_hook_deployed style.

## T13 / T17 / T18a / T15 — hook-level denies on a fixture
- `test_hook_stale_session_denied_recorded`, `test_hook_unbound_session_in_scope_denied_recorded`, `test_hook_path_not_granted_denied_recorded`, `test_hook_authority_revoked_denied_recorded`: reuse `HookWithRealGatecore` from test_gate_hook_deployed.
- Assert:
  - exit 2
  - stdout `{"decision":"block","reason":r}`
  - gate row (decision, reason)
  - target absent
- Kind A. Live evidence already exists (WF-017), but not in the repo.

## T16 — second trigger for the same interruption after close
- `test_T16_same_interruption_cannot_reopen_after_close`: open, reconcile and close a recovery citing owner turn X; begin again citing the same interruption.
- Assert a refusal or no-op with one generation bump in total.
- Kind B unless "same interruption" gets an identity (owner/spec definition needed).

## T19 — blocker raised on a gate fault
- Proposal depends on F9: define whether "blocker raised" means a gate-log/audit record or a ledger blocker row (the gate never writes the ledger).
- Kind B pending owner.

## T20 — evidence-linked claims and delivered ≠ accepted
- `test_T20_delivered_claim_without_evidence_rejected`: `delivered` with `claims:[{"claim":"c","evidence":[]}]`.
  - Assert `rejected(malformed)` "has no evidence refs" (validate.py:187-189) and head unchanged.
  - Kind A.
- `test_T20_accept_with_nothing_delivered_refused`: accept before any delivered event.
  - Assert a refusal, nothing appended.
  - Kind A or B (verify the current message first).

## T21 — minutes provenance
- Not testable in R1: who typed the minutes is not recorded beyond the session string.
- Proposal: none. Owner note only.

## T27 — gate-log writes in one session; commit-timeout writes nothing
- `test_T27_gatelog_N_processes_same_session_one_chain`: N subprocesses append to one gate.db with the same session id.
  - Assert consecutive seq, a valid chain and N rows.
  - Kind A.
- `test_T27_commit_timeout_leaves_no_row`: reuse the `test_T29_commit_timeout_give_up` setup.
  - Assert `row_at(next_seq)` is None and head unchanged.
  - Kind A.

## T28 — no lock file; hung writer
- `test_T28_no_lock_file_ever_created`: after a full flow, plus after a killed-before-commit fault, list the ledger directory.
  - Assert only `l.db` (and a hot `-journal` only right after the kill).
  - Kind A.
- Hung-writer blocker / "never a force": Kind B (needs a defined blocker artefact).

## T29 — quote content (F2)
- Implemented as `test_wf019_gaps.py::T29QuoteContent.test_T29_activate_with_quote_lacking_grant_id_and_block_hash_stays_unbound` (expectedFailure).
- Observed: `COMMITTED`, authority `in-force`. Kind B, owner decision D-022.

## T30(c), (e), (g), (i)
- `test_T30c_bind_accept_close_and_gate_succeed_after_recovery_close`: after `recover close`:
  - Assert bind (a new reviewer), accept and close succeed.
  - Assert gate `decide` allows a builder in-scope write.
  - Kind A.
- `test_T30e_recover_not_reachable_from_another_command`: assert no `commands.*` function other than the recover verbs calls `recover_begin` / `recover_close` (AST scan of commands.py). Kind A.
- `test_T30g_invalid_chain_classes_in_recovery`: gap (insert at seq+2 with triggers dropped), bad `content_sha256`, `integrity_check` failure (page corruption on a fixture copy).
  - Assert `rejected(invalid-chain)`, nothing appended, no `recovery-opened`.
  - Kind A.
  - The gate-log tail exception is Kind B (F3).
- `test_T30i_recovery_events_never_accepted_as_stop_confirmed`: run a full recovery cycle without `stop-confirmed`; attempt a replacement bind at g2.
  - Assert `termination-unconfirmed`.
  - Kind A.

## T31 — no reliance on filename uniqueness
- Demonstrated structurally: the ledger has no per-event files.
- Proposal: an AST/grep test asserting that no `wfcore` module creates per-event files under `jobs/*/` except the export directory.
- Kind A.

## T32 — gate treats a refused bind as unbound
- `test_T32_refused_bind_leaves_session_unbound_at_gate`: bind a session with tools wider than the ARS (refused); then gate `decide` for that session writing in scope.
- Assert `unbound-session`.
- Kind A.

## T33(a), (b–d), (f)
- `test_T33a_ars_unchanged_after_replacement`: compare the grant ARS before and after a g2 replacement. Kind A.
- `test_T33bcd_renewed_block_then_replacement_allowed`: after a refusal, renew the grant with replacement permitted, then replace. Assert success at g2. Kind A.
- `test_T33f_no_generation_bump_verb`: assert the argparse surface has no command that raises generation without a bind. Kind A.

## T34 — environment/stance; rebind; old quote
- Environment/stance change: no command in R1. Kind B (F10).
- `test_T34_binding_outside_new_ars_must_rebind_at_g_plus_1`: renew with a narrower ARS.
  - Assert the old binding gets `binding-outside-ars`.
  - Assert rebind at g+1 succeeds after `stop-confirmed`.
  - Kind A.
- `test_T34_old_quote_hash_rejected_after_renewal`: after amendment, activate with the old block-hash prefix.
  - Assert `rejected(unbound-approval)`.
  - Kind A.

## T35 — remaining
- P3 "with separate authorization": Kind B (no authorization field in the contract schema).
- Links/junctions resolution: Kind B (stated limit, not resolved).

## T04 "kept as evidence", T12, T07 doorbell
- Owner interpretation or live procedure (F9, F10). No fixture test proposed.
