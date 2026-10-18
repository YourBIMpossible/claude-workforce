# OWNER-STEPS — owner turns and worker commands per phase (WF-022 rehearsal)

This document lists where the owner is needed, and how many worker commands run between those points, for the first pilot job. It is built from the two rehearsal flow logs:
- `%LOCALAPPDATA%\wf022\flow-log.jsonl`: attempt 4, the uninterrupted flow, 26 logged calls.
- `<repo>\fixtures\tmp\wf022-int\flow-log.jsonl`: the interrupted flow, 41 logged calls.

**Owner minutes are `unknown` everywhere.** The rehearsal simulated every owner turn, so it measured none, and none is estimated. Worker wall time is the measured sum of the `wf` call times in each phase. It does not include agent thinking time or the time a human takes to read.

Status: REHEARSAL, NOT A PILOT. Every OWNER TURN below was typed by the agent and marked SIMULATED in the log.

## A. Uninterrupted flow (attempt 4, job `wf022-docfix`)

| # | Phase | Owner turn (what the owner does) | Owner minutes | Worker commands (role) | Refused | Worker `wf` wall time (s) |
|---|---|---|---|---|---|---|
| A0 | Pick the job and approve the acceptance list | Choose the first pilot job (§12 candidate (b) or another). Read `ACCEPTANCE-LIST-DRAFT.md`, then change or approve AC1–AC7 and its open items. This happens outside `wf`. | unknown | 0 | 0 | 0 |
| A1 | Contract | **OT1**: the owner's request in their own words. It goes into `owner_request.quote` and `source_turn`. | unknown | 1 `job new` (coordinator) | 0 | 0.32 |
| A2 | Grant | **OT2**: read the Grant Confirmation Block that `grant propose` prints, then reply with its hash prefix and an approval quote. The coordinator passes these to `grant activate --owner-hash --quote --turn`. | unknown | 2 `grant propose`, `grant activate` (coordinator) | 0 | 0.57 |
| A3 | Bind | none | — | 3 `bind` coordinator and builder, plus `status` (coordinator). The coordinator bind needs an explicit `--path` (WF022-F3). | 0 | 1.05 |
| A4 | Repair | none | — | 10 `append` (5 × intent → edit → receipt) (builder) | 0 | 2.73 |
| A5 | Deliver | none | — | 1 `append --type delivered` (builder) | 0 | 0.28 |
| A6 | Review | none | — | 1 `bind` reviewer (coordinator, only after delivered: WF022-F4), 1 `check-acceptance.ps1` (reviewer, not a `wf` call), 1 `accept --results` (reviewer) | 0 | 0.34 (accept) |
| A7 | Checkpoint | **OT3**: copy the head (seq, self) from `checkpoint` into an owner turn as the retained checkpoint (spec §3C). | unknown | 1 `checkpoint`, 1 `verify` (coordinator) | 0 | 0.44 |
| A8 | Close | **OT4**: the intervention declaration (T21), giving minutes or `none`. In the rehearsal this was `none`. | unknown | 1 `close` (coordinator) | 0 | 0.29 |
| A9 | Export and verify | none | — | 1 `export`, 1 `verify` (coordinator) | 0 | 0.48 |
| A10 | Digest | **OT5**: read the digest (Done / Active / Needs-owner). | unknown | 1 `digest` (coordinator) | 0 | 0.26 |

**Totals, uninterrupted:**
- Owner: 5 owner turns inside `wf` (OT1–OT5) plus the A0 decision outside it. Owner minutes `unknown`.
- Worker: 25 `wf` calls and 1 check script, none refused. Measured `wf` wall time is 6.75 s in total.

The interrupted job (section B) did not run A7 `checkpoint`. Its driver omitted it, so in section B OT3 does not occur.

## B. Interrupted flow (job `wf022-int`): extra owner turns caused by a kill during an append

Phases A0–A3 and the first repair ran as in section A. These rows are the extra steps.

| # | Phase | Owner turn | Owner minutes | Worker commands (role) | Refused | Worker `wf` wall time (s) |
|---|---|---|---|---|---|---|
| B1 | Kill (the fault) | none. In a pilot the owner or the machine notices the dead process. | — | 1 `append --type receipt`, killed (builder) | 1 (killed) | 3.03 |
| B2 | Diagnose | none. The record says what is wrong. | — | `status` (marker, `uncertain-write`), a probe `append` (refused `uncertain-write-pending`), `verify` (`clean:false`) | 1 | 0.72 |
| B3 | Resolve the uncertain write | **OT-R1**: owner-present decision on the pending write (`resolve <job> <seq> --attempt ID`). `wf` reports COMMITTED or NOT-COMMITTED from the database; the owner turn authorises the act. | unknown | `resolve`, `status`, `verify` (coordinator) | 0 | 0.66 |
| B4 | Open the recovery | **OT-R2**: an owner turn that names the job, cited by `recover begin --reason --turn`. | unknown | `recover begin`, `status` (coordinator) | 0 | 0.51 |
| B5 | (wrong order, refused) | none | — | plain `reconcile` while the recovery is open → `rejected(recovery-open)` | 1 | 0.19 |
| B6 | Reconcile and close the recovery | **OT-R3** and **OT-R4**: owner turns cited by `recover reconcile --turn` and `recover close --turn`. | unknown | `recover reconcile`, `recover close`, `status` (coordinator) | 0 | 0.72 |
| B7 | Role reconcile | none | — | `reconcile` → report seq 11 with `X1 attempt-without-completion` (coordinator) | 0 | 0.28 |
| B8 | Acknowledge X1 | **Decision D-X1** (not a `--turn` argument): someone inspects `scope/README.md` and decides whether the lost receipt's edit is on disk. Here it was, and it matched pristine. The builder records `ack-reconcile --report 11 --exceptions ["X1"] --note …`. Whether this must be the owner or can be the builder is open (see below). | unknown | a builder `append` refused `rejected(unreconciled)`, `ack-reconcile` (builder), `status`; plus 1 malformed `append` of mine refused with `usage` | 2 | 0.97 |
| B9 | Re-record and continue | none | — | `append --type receipt` (builder), then I3–I5, delivered, reviewer bind, accept, verify | 0 | 0.27 + finish |
| B10 | Close | **OT4′**: the intervention declaration. It is no longer `none`: an owner-present recovery with minutes `unknown`. | unknown | `close`, `export`, `verify`, `digest` (coordinator) | 0 | (in finish 3.74) |

**Totals, interrupted flow:**
- Owner: OT1, OT2, OT-R1..R4, D-X1, OT4′ and OT5. That is 4 owner turns more than section A (R1–R4) plus one inspection decision (D-X1), with no OT3.
- Worker: 41 logged calls, 5 of them refused or killed: the kill, the probe, `recovery-open`, `unreconciled`, and my `usage` slip.

## C. Open items for the owner

1. **Who decides D-X1?** The ledger accepts the builder's `ack-reconcile`. Whether a pilot requires the owner to make, or co-sign, the "the edit is on disk" judgement is a process choice. Nothing in `wf` enforces it either way.
2. **WF022-F7 (NEEDS OWNER):** `recover begin` listed no exceptions, yet the plain `reconcile` after the recovery reported X1. The answer decides whether B7–B8 can ever be folded into B4–B6.
3. **Checkpoint cadence:** section A took one checkpoint (OT3) before close, and section B took none. The owner picks when a pilot takes them.
4. **Owner minutes:** only a real pilot can fill these in. The rehearsal deliberately leaves them `unknown`.
