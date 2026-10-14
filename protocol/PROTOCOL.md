# Workforce protocol v1 (R1 record-and-verify slice)

Status: v1, as implemented by `tools/wf` under FB1.4 (sha256 bae4b01dfdf0…). Successor to handoff §6 **by
reference**: where this file and handoff §6 differ, this file describes what `wf` does today; §6 remains the
design history and is not copied here. Commands, bounds and storage details live in `tools/wf/README.md`.

## 1. What the protocol is

A job is an append-only, hash-chained sequence of events in a SQLite ledger. Every agent action that matters to
the job is recorded as one event through `wf`. The protocol makes records consistent and tamper-evident. It does
**not** gate tools, sandbox processes, or authenticate anyone.

## 2. Messages (events)

Every event carries: `job`, `seq` (head+1), `prev` (hash of the previous event), `self` (hash of this event),
`type`, `msg_id`, `author_role`, `session_id`, `generation` (integer ≥ 1), optional `reply_to` and `grant_ref`,
`content` (JSON object, canonical form), and the contract pin (contract SHA-256) added by `wf`.

| Type | Written by | Content |
|---|---|---|
| contract | coordinator | The job contract (owner quote with source turn, interpretation, baseline, outcome, acceptance criteria, read/write/watch/presence-only scopes, allowed tools, limits, exclusions, environment `disposable local scope`). Must be event 1. |
| grant | coordinator | Grant fields; state `proposed`, then a second grant event `activated` with the owner hash, quote and turn. |
| intent | builder | `path`, `sha_before`, `note` — before a write. |
| receipt | builder | `path`, `sha_before`, `sha_after`, `note` — after a write. |
| blocker | coordinator, builder, reviewer | Free-form blocker report. |
| handoff | builder, coordinator | Handoff note. |
| delivered | builder | `claims[]`, each `{claim, evidence[]}` with non-empty evidence. |
| accept | reviewer | `decision`, `reason`, `criteria`, `environment`, `limits`, `changed_paths`, `builder_session`. |
| close | coordinator | `intervention`, `basis` (`accepted` or `abandoned`), `verify` summary. |

Message rules:

1. **Idempotency.** A repeated `msg_id` with identical content is `DUPLICATE-IGNORED` (no row, exit 0). The same
   `msg_id` with different content is rejected.
2. **Order.** The contract is first. After `close`, the job accepts no event.
3. **Generation.** A role's generation may not go backwards. The first session to write at (role, generation)
   owns it; another session at the same pair is rejected (`claimant-conflict`).
4. **Chain.** `prev` must equal the head's `self`; `(job, prev)` is unique, so two writers cannot both extend the
   same head. Rows cannot be updated or deleted (triggers).

## 3. Ownership

- **Coordinator** owns the contract, grants and close.
- **Builder** owns intent, receipt and delivered: the record of what it changed and what it claims.
- **Reviewer** owns acceptance. A session that wrote any builder event for the job cannot record `accept`
  (`same-session`). Acceptance requires a prior `delivered`.
- **Owner** (human) supplies the request quote and confirms grants by hash. The owner never writes events
  directly; their words enter as quoted fields with a source turn.

**Identity is typed, not authenticated.** Role, session and generation are values the caller supplies. `wf` checks
them for consistency (role per type, generation binding, same-session refusal). Any process that can run `wf` can
type any role. Separation of builder and reviewer therefore rests on how sessions are launched (FB1.4 §7: a
separate headless session with its own token and a fixed entry point), not on `wf`.

## 4. Grants

1. `grant propose` validates that the grant's resources lie inside the contract's write scope and records it as
   `proposed`. It prints a **Grant Confirmation Block**: job, grant id, actions, resources, exclusions, ARS,
   delegation, expiry, revocation rule, contract SHA-256 and the proposal position.
2. The owner reads the block and supplies its hash (at least 8 hex characters).
3. `grant activate` records `activated` only if the supplied hash prefix matches the block hash, with the owner's
   quote and turn.

Limits in v1: the block is deterministic, so the same hash activates an identical proposal on a fresh ledger;
the hash proves the activator had the block, not who they are. Appends are **not** gated on an activated grant.
`status` reports authority as `no-grant`, `proposed` or `in-force`, and the reviewer judges it.

## 5. Acceptance and close

- `accept` compares the results file with the contract: every acceptance criterion present with `pass: true`, no
  unknown criterion, and each changed path classified P1 write scope, P2 presence-only, P3 watch, P4 outside every
  scope, P5 excluded. Decision is `accepted` only with all criteria passing and no P4/P5 path; otherwise
  `rejected`. `wf` records the reviewer's claims; it does not re-run checks.
- `close` is refused unless `verify` is clean, no pending-write marker exists, and either acceptance is
  `accepted` or the job was abandoned (`job abandon`). The close event records the basis.

Status axes: execution (`queued`, `active`, `interrupted`, `delivered`, `abandoned`, with `+uncertain-write` when
markers exist), acceptance (`pending`, `accepted`, `rejected`), review (`none`, `fresh-review-recorded`) and
authority (above).

## 6. Reconciliation

- **Uncertain writes.** Each write leaves a pending-write marker until its outcome is known. A marker that cannot
  be proved committed or aborted blocks further writes to that job (fail-closed) until `wf resolve` decides
  `COMMITTED`, `NOT-COMMITTED` or `CONFLICT`. `STILL-UNCERTAIN` and `RESERVATION-NOT-OBTAINED` change nothing.
  An operator `--disposition` adds a note and never decides. Details: README, "Attempt ids and pending-write
  markers".
- **Verification.** `verify` rechecks schema, integrity, every hash and the export copy. `checkpoint` records
  (seq, self) at a point in time; keeping that file elsewhere lets a later verify detect a rewritten history.
- **Exports.** `jobs/<job>/export/` is a derived copy refreshed after each write; the ledger is authoritative and
  `verify` reports drift.

## 7. Out of scope in v1

Tool gating, replay and recovery of interrupted jobs, bind/handoff enforcement between sessions, multi-machine
ledgers, and any form of authentication.
