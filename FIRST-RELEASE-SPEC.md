# Workforce — FIRST-RELEASE-SPEC (Rev 5.3)

2026-10-11 · Output 2 of 4 · **Planning state.** A specification, not a grant and not a roadmap (CURRENT-STATE.md is the only roadmap). Nothing here was built, installed or run.
Naming: **R1** = Release 1. Requirements are written zero-padded (R01–R23). Evidence tags and gap IDs (G-nn) are defined in BUILD-VERSUS-REUSE.md §0 and GAP-AND-DECISION-REGISTER.md.
Rev 5.3 (2026-10-11; targeted correction, Rev 5.2 preserved in history\rev5.2\): the qualification payload splits Q2 into Q2-PY (Python), Q2-SQL (SQLite) and Q2-CC (Claude Code/hook) with an explicit dependency map; approval semantics are separated (unapproved additions never expand the grant, owner restriction or cancellation is immediate, conflicting or unclear approving turns leave affected work inactive); hash-verification provenance corrected; the three governing documents were read and compatibility recorded (SOURCE-MAP). No change to the storage or guarantee design. Rev 5.2 (2026-10-11; bounded update, Rev 5.1 preserved in history\rev5.1\): added §13A (continuous R1 release-grant structure, proposed, NOT IN FORCE) and §15 (continuous progression for future multi-stage grants, proposed); Q1b wording split into three claims (explicit denial, unexpected error, timeout); placement wording records the owner's statement as owner-reported, not independently verified; B0 failure handling pauses only dependents. No change to the storage or guarantee design. Rev 5.1 (2026-10-11; targeted corrections, Rev 5 preserved in history\rev5\): retained-checkpoint, backup-validity, caller-identification and placement wording corrected; no design change. Rev 5 (2026-10-11) changes over Rev 4 (Rev 4 preserved in history\rev4\): (A) sequence identity independent of event type (§3, T31); (B) approved roles/permissions/delegation separated from session assignments, with invalidation and renewal rules (§2B, T32–T34); (C) acceptance compares changed paths against permitted-change classes, not manifest existence (§4.4, T35); (D) integrity ladder: chain consistency, accidental-corruption detection, tamper evidence, authenticity, with the retained-head limits (§3C, T36); (E) recovery requirements preserved and translated to the selected storage design (§3A, §3B, T27, T28, T30, T37). Storage: S-DB (SQLite, rollback-journal mode) recommended and provisional, S-FILE retained as corrected fallback (STORAGE-COMPARISON.md). The Rev 4 file-lock procedure (job lock file, claims, markers, snapshots) is **retired for S-DB** and kept only in history\rev4 and STORAGE-COMPARISON §6. Rev 4/3/2 history: Rev 4 redesigned stale-lock recovery as one unit; Rev 3 made grant ordering explicit and narrowed the release claim; Rev 2 added guarantee layers, a write transaction, the monitored boundary, T27–T28 and no bind override.
Version: **Rev 5.3** (2026-10-11). The copy in `F:\Claude-Workforce\FIRST-RELEASE-SPEC.md` is authoritative (SOURCE-MAP). Copies in `F:\Claude-Tools\reports` are delivery copies; `history\rev4\` and any Rev 1–4 text are historical and must not be used.
Runtime: approach C (thin ledger + gate) is the owner's **provisional preferred first-release design** (owner direction, turn 3, Decision 1). Its storage mechanism is **not irrevocably selected**: S-DB is recommended (provisional), S-FILE is the fallback. That is not implementation approval and not acceptance of any untested guarantee; Temporal remains an alternative (BUILD-VERSUS-REUSE.md). No migration is promised to be effortless.
Stage: foundation planning. Implementation is paused; no build, probe or pilot is authorized by this document. Reading this document is not a test: every case in §8 is **NOT RUN**.

## 1. Purpose and boundary

R1 proves one thing end to end: **an authorized job can be intaked, executed by a separate worker, accepted with evidence by someone other than the builder, interrupted, and recovered by hand with no duplicate or unauthorized action observed within the declared checks and monitored boundary (§4.3), while the owner's interventions are counted.**

R1 is two things; do not conflate them:
- **R1 build** — the ledger/validator, `wf` CLI, gate hook and fixtures. Needs its own implementation grant (not given).
- **R1 pilot** — one real, small job run through the built system, with one staged interruption.

**R1 is accepted when** every required case in §8 passes on fixtures, one real pilot job is accepted by an independent reviewer session with evidence-linked claims, one staged interruption on that pilot was recovered by hand per §6.2 with no duplicate or unauthorized action observed within the declared checks and the §4.3 monitored boundary, and the intervention log for the pilot is complete. Passing R1 establishes *manual* recovery of one local reversible job. It does not establish whole-machine containment, unattended operation, target-environment readiness, or any capability qualification (R20). Absence of an observed violation outside the monitored boundary is not evidence of absence.

Out of R1 (see §10): automatic recovery, scheduler, standing grants, queues, parallel workers, cross-session messaging, roster growth, improvement lane, runtime portability proof.

## 2. Roles (three; ceiling honored)

| Role | Is | Writes | Cannot |
|---|---|---|---|
| **Owner** | The human. Only source of authority. | Chat turns; owner-only actions (§11) | — |
| **Coordinator** | One Claude session. Intakes jobs, drafts contract and grants for owner activation, assigns, reconciles, closes. | Ledger via `wf` | Self-issue or widen a grant; accept its own builder's work; adopt rules |
| **Builder** | A separate Claude session. Executes the contract. | Job write scope + its own scratch; ledger via `wf` | Touch anything outside scope; hand off or widen scope; self-accept |
| **Reviewer** | A third, fresh session. Forms its view from contract, baseline, diff and evidence **before** reading builder narrative. | Review/acceptance records via `wf` | Be the builder's session; edit the work |

Same-model review is not human independence (R14). R1 states this in every acceptance record; it does not claim otherwise.

## 2B. Approved roles versus current assignments (defect B)

Two different things, recorded separately:
- **Approved Role Set (ARS)** — what the owner approves. Part of the grant and covered by the Grant Confirmation Block hash (§3, grant ordering). Fields: (1) **roles** allowed in the job (coordinator, builder, reviewer); (2) per role, the **permitted tools and action classes** and **path globs**, each a subset of the grant's actions/resources; (3) **session requirements** (reviewer is a fresh session and not the builder's; at most one live session per role); (4) **delegation rules**: whether replacement of a role's session is allowed, the maximum number of replacements (R1 pilot: a stated number, 0 if none), the conditions (only after `stop-confirmed` and a completed reconcile and a first-event `ack-reconcile`; replacement permissions ⊆ the replaced session's), and the expiry. The ARS names **no session ids**; ids do not exist at approval time.
- **Assignment** — a current session binding: `(job, role, session_id, generation)`, recorded by `wf bind`. Derived from events; it carries no authority of its own. Authority comes only from an activated, in-force grant whose ARS covers the assignment.
A quoted owner approval is **provenance evidence, not authenticated authority** (layer O): nothing verifies it is genuine. Explicit owner confirmation of the exact block hash before execution is preserved (§3 steps 1–4); no assignment or tool action precedes `activated`.

| Assignment event | Allowed under the original grant? | Needs renewed approval (new block, new hash) when |
|---|---|---|
| **Initial** coordinator/builder assignment (generation 1) | Yes, if the role is in the ARS, no live session holds it, the session's tools/paths ⊆ the ARS, and the grant is `activated` and in force. The session confirms itself with an `ack-assignment` event naming the same session id; the digest shows the binding table so the owner can spot a wrong binding | The role is not in the ARS; the tool or path set is wider than the ARS; a second concurrent session of the role is requested |
| **Reviewer** assignment | Yes, if the reviewer role is in the ARS, the job is `delivered`, the session is not the builder's session, and its permissions are read-only plus review records | Reviewer not in the ARS, or asked to edit work, or the same session as the builder |
| **Replacement after confirmed termination** (generation+1) | Yes **only if** the ARS delegation rules allow it, the replacement count is within the maximum, `stop-confirmed` and a completed `wf reconcile` exist, and replacement permissions ⊆ the replaced session's | Rules disallow replacement; count exceeded; permissions or scope wider; the contract or ARS differs from the activated one |
| **Replacement with termination unconfirmed** | **Never** in R1 (no override). Not renewable by approval either: outside R1 (§10) | — |
| **Generation change** | Bumps only through `wf bind` (above). Raising a generation edits nothing else and never widens authority | Any accompanying change to roles, tools or paths |

**Approval invalidation (explicit).** The grant leaves `activated` for `voided`, `revoked` or `expired`, and the gate and `wf grant-check` treat it as no grant on the next call, when any of these is recorded: contract amendment (the block hash no longer matches the recomputed hash); amendment to actions, resources, exclusions, ARS or delegation rules; revocation or cancellation (effect at once); expiry; a handoff or request wider than the ARS (also rejected under §5 rule 6); a change of environment or pilot stance; a replacement outside the delegation rules. Existing bindings stay recorded but carry no authority while the grant is not in force. After a renewed block is confirmed, existing bindings remain valid only if their role, tools and paths are inside the new ARS; otherwise the role is rebound at generation+1 under the rebinding rules above (including `stop-confirmed` where a live session is replaced). A quote that predates the current block hash never carries over.

## 2A. Guarantee layers — what each claim rests on

No claim in this spec rests on more than the layer named for it. Four layers, kept separate:

| Layer | Supplies | Does **not** supply | Trust it needs |
|---|---|---|---|
| **V — record validation** (`wf`, `wf verify`) | Form, required fields, hash chain, uniqueness, message rules, derived status | Proof that a field's value is true. `author`, `session_id`, `generation` are caller-supplied text | Only that `wf` is the code path used; a worker with shell access can bypass it by writing to the database or files directly, and `wf verify` then *detects* edits to existing events (not a whole-chain rewrite without a retained checkpoint, §3C) |
| **I — caller identification** | Which session made a hooked tool call, **if** hook input carries a session id (G-05, not tested) | Identity for shell activity; identity for sessions the gate never sees | The Claude Code hook mechanism; unverified |
| **O — owner-approval verification** | A grant carries a **provenance reference** (verbatim quote + turn reference) recorded by the coordinator. That reference is *pending provenance* and gives no authority. Authority begins only at owner confirmation of the exact job/grant scope (§3, grant ordering) | Proof the quoted approval is genuine. No tool here can check it | The owner confirming the Grant Confirmation Block, and reading the digest |
| **E — action enforcement** (gate hook) | Allow/deny on hooked file-write tools (and logging of shell attempts) | Any control on shell writes; any protection of the gate from a session that can edit the user's own files | The pinned hook staying installed and unmodified; unverified timeout behavior (G-12) |

Rule: each acceptance case in §8 names its layer(s). "Prevented" appears only for layer E on hooked tools, or for layer V rejecting a malformed or conflicting **submission through `wf`**. It never means authenticity of a claim and never covers the shell channel.

## 3. Records (all append-only, immutable once written)

**Storage design: S-DB (recommended, provisional; STORAGE-COMPARISON.md).** The authoritative ledger is one SQLite database in the default rollback-journal mode, `ledger/ledger.db`, opened only by `wf` (and read-only by the gate and `wf observe`/`wf status`). The gate's own log is a second database, `runtime/gate/gate.db`, whose only writer is the gate. WAL mode is **not** used (avoids the documented WAL-reset bug range and keeps one journal file; STORAGE-COMPARISON §2). Everything in this section is a specification; none of it was run. If S-FILE is selected instead, STORAGE-COMPARISON §6 gives the corrections and Rev 4 §3A/§3B (history\rev4) the procedure.

Job-scoped files stay under `jobs/<job-id>/` (placement: RECORD-AND-LAYOUT-PROPOSAL.md): `export/` (derived), closeout bundle. The ledger rows are authoritative; exports are derived.

| Record | Content | Written by |
|---|---|---|
| `contracts` row / `contract.json` export | R07 fields: exact owner request (verbatim quote + source turn reference), authorized interpretation, baseline (file manifest with SHA-256 for every in-scope path, plus git revision if a repo exists, plus uncommitted-state flag), outcome, acceptance criteria (each with a runnable check and expected result), read scope, write scope, **watch list**, **presence-only list**, allowed tools per role, limits, exclusions, evidence list, closeout/recovery rules, observe thresholds (placeholders), `environment` = "disposable local scope". Its SHA-256 is **pinned** in every later event. | Coordinator, once; amendments are new versions, never edits |
| `events` table | One row per event. Columns: `job`, `seq`, `type`, `msg_id`, `author_role`, `session_id`, `generation`, `reply_to`, `grant_ref`, `ts`, `content` (JSON), `content_sha256`, `prev`, `self`. Types in §4. | `wf` only |
| Grant events (type `grant`, keyed by grant id) | `proposed` → `quoted` (coordinator records the owner turn: verbatim quote + turn reference; **pending provenance, not in force**) → `activated` (owner confirmation of the Grant Confirmation Block, below) → `revoked`/`expired`/`voided`. Fields: actions, resources (path globs), exclusions, **ARS and delegation rules (§2B)**, expiry, revocation rule, source turn, block hash. | Coordinator proposes and quotes; `activated` requires the block-hash match below |
| `intervention` events (`interventions.jsonl` is an export) | job, date, labels (decision / restart / relay / approval / clarification / correction / other), necessary-or-avoidable, owner minutes (**number as reported by the owner, or `unknown`**), note, recorded-by | Coordinator or owner via `wf` |
| `gate.db` rows | Gate's own record: `attempt` on PreToolUse, `completed` on PostToolUse, with tool, target, decision, reason; per-session hash chain. | Gate hook only |

**Sequence identity (defect A).** The key of an event is `(job, seq)` and nothing else. `PRIMARY KEY (job, seq)` makes a second row at one sequence impossible **regardless of `type`**; the type is an ordinary column. Two events of different types that try to take the same sequence cannot both commit: either they are serialized by the write reservation (§3A step 1) so the second reads the new head and takes `seq+1`, or, if a writer bypassed the reservation, the second insert fails with a constraint error, rolls back, and raises a blocker with the chain unchanged. `UNIQUE (job, prev)` additionally prevents two successors of one predecessor. `BEFORE UPDATE`/`BEFORE DELETE` triggers on `events` abort any change to an existing row: these stop mistakes by `wf` or another honest tool and **do not stop** a party who can open the database and drop the triggers (that is the integrity ladder's job, §3C). Test: T31.

**Grant ordering (unmistakable).** A coordinator's recorded quotation is **pending provenance**. Execution may start only after the owner has confirmed the exact job/grant scope through this supervised approval procedure:

1. `wf grant propose` prints a **Grant Confirmation Block**: grant id, contract SHA-256, actions, path globs, exclusions, **ARS and delegation rules**, expiry, revocation rule. The block has its own SHA-256 (the *block hash*).
2. The owner approves in a chat turn *after the block is displayed*. This may be the original job approval, **not a second routine approval**, provided that turn follows display of the block and names the grant id and the first 8 characters of the block hash (or the block is shown inside the approval request and the owner's reply refers to it unambiguously). Otherwise one short confirm turn is needed.
3. `wf grant quote` records that turn (verbatim + reference) → state `quoted`. The gate and `wf grant-check` treat `quoted` as **no grant**.
4. `wf grant activate` succeeds only if the recorded quote text or the owner's referenced block contains the grant id and the block hash, and the block hash still equals the hash recomputed from the current contract and grant (a contract or ARS amendment after approval voids it). Otherwise it stays `quoted` and execution is blocked (T29, T34).
5. `wf digest` lists every activated grant with its quote, block hash and the current binding table so the owner can spot a wrong binding. Genuineness of the quote remains layer O: procedural, not machine-verified.

**Status is derived, never stored.** `wf status` computes four independent axes from events: **execution** (queued · active · interrupted · delivered · abandoned), **acceptance** (pending · accepted · rejected), **review** (none · fresh-review-recorded), **authority** (no-grant · in-force · revoked · voided · expired · cancelled). Delivered is not accepted; local verification is not target-environment acceptance (R08). There is no stored snapshot to go stale.

## 3A. Write transaction (many sessions call `wf`; the engine serializes, Workforce checks)

`wf` is one code path, not one process. Concurrent calls are serialized by the database's single-writer rule, taken explicitly. Every mutating `wf` command runs these steps in **one connection and one transaction**:

1. **Reserve.** Open the connection with a bounded busy timeout and run `BEGIN IMMEDIATE` (takes the write reservation at the start `[DOC* S5]`; one writer at a time `[DOC S2]`). If the reservation is not obtained within the timeout the command fails with a blocker. It never proceeds unserialized, never retries forever, and there is no lock file to break.
2. **Recovery gate.** Query for an open recovery on the job (§3B). If one is open, roll back and refuse. (`wf recover` commands are the only exception.)
3. **Chain check and head.** Read the job's head `(seq, self)` and verify the chain tail (and the full chain when the job is small; the verification scope per command is a B1 decision with the cost measured at Q3). An invalid chain (§3B classes) → roll back, refuse, blocker.
4. **Validate** the proposed event against that head and the §5 rules (role, binding vs the ARS, generation, `prev`, duplicates).
5. **Insert** with `seq = head+1`, `prev = head.self`, `self = SHA-256(canonical event)`. A constraint violation (primary key, `UNIQUE(job, prev)`, trigger) → roll back and return a structured `rejected(constraint)` result carrying the constraint error (D-017). This result is the visible failure of the attempt; it writes no ledger row and does not replace the claimant-conflict blocker row (section 5 rule 5), any separately specified persistent-blocker duty, or the prevention of dependent work (section 5 rule 2).
6. **Commit.** Commit is the single atomicity point: the event, and any derived row if one is ever added, become visible together or not at all `[DOC S1]`. Durability depends on the synchronous setting and the volume honoring flushes (`PRAGMA synchronous` default FULL in rollback mode is to be confirmed `[DOC* S3]`; G-25).
7. **After commit (outside the transaction):** refresh `jobs/<job>/export/` if configured. A failed or missing export is a *recoverable exception* (§3B), not a ledger fault; the export is never authority.

Derived state: none stored in R1. Status, bindings and grant state are computed by queries inside a read transaction, so the Rev 4 snapshot, its head-mismatch check and `wf repair` do not exist for S-DB. If a cached column is ever added it must be written in the same transaction as the event that changes it (otherwise the Rev 4 lost-update problem returns), and `wf verify` recomputes it.

Single writer connection per command: **no function opens a second write connection or calls `BEGIN IMMEDIATE` again while one is held** (that would self-block on the writer's own reservation; the Rev 4 "recovery re-acquires its own held lock" defect in database form). Helpers take the open connection as an argument (`repair`-style helpers are `*_core(conn)`). Test: T30(f).

R1 limits: one active job, one coordinator, one builder, one reviewer, plus read-only callers (gate, `wf observe`, `wf status`). Serialization is global across jobs (one database writer), so cross-job lost updates cannot occur; cross-job invariants (for example one session bound in two jobs) are not enforced in R1.

Crash points:

| Crash point | Left behind | Detect / recover |
|---|---|---|
| Before step 6 (commit) | A hot rollback journal | Next opener rolls the transaction back automatically `[DOC S1]`; no event exists; nothing to undo. `wf` never deletes or renames the journal `[DOC S4]`. If the engine cannot open the database (permissions, locking error), the command fails with a blocker for the owner |
| During commit | Either the prior or the new state | Atomic commit `[DOC S1]`; verify with Q4. A torn *file* is the engine's recovery problem, not a Workforce procedure |
| After commit, before step 7 | Stale or missing export | `wf verify` reports a stale export (recoverable exception); `wf export` regenerates |
| Writer alive but hung, holding the reservation | Other writers time out | Bounded wait → blocker; the owner stops that process (owner act); nothing is broken or reclaimed by `wf` |
| Hung reader (gate or `wf status`) delaying a writer's commit | Writer waits `[DOC S2, S6]` | Bounded wait → blocker; the gate itself denies on its own timeout rather than hang |

Not tested: all of the above on this host (G-24, G-25, G-26). If any of Q2-PY, Q2-SQL, Q2-CC or Q3–Q8 fails: pause the failed probe and only its dependents (payload 8.1: Q2-SQL needs Q2-PY; Q3 and Q5 need Q2-PY and Q2-SQL; Q4, Q6 and Q7 need Q3; Q8 needs Q2-PY; Q1 and Q1b need Q2-CC and Q2-PY), diagnosis, and an explicit owner disposition; S-FILE (STORAGE-COMPARISON §5–§6) only by a new owner decision.

## 3B. Interrupted-operation and integrity recovery (manual, owner-present)

What replaces Rev 4's stale-lock recovery. Most of Rev 4 §3B existed to repair a hand-built lock; the engine now owns lock release and rollback. What remains is recovery *of meaning*: knowing the ledger is intact, and not letting ordinary work resume until a recovery that was begun is finished. **Preserved requirements** (owner paste #4 §3E), each mapped to a test:
1. Exclusive execution ownership before replacement: `stop-confirmed` and completed reconcile before any `wf bind` that replaces a session (§2B, §4.1). T16, T33.
2. Reconciliation before retry: `ack-reconcile` first (§6.2). T06.
3. No replacement override when termination is unconfirmed: no override exists; **recovery never creates, satisfies or substitutes `stop-confirmed`**, never binds, never widens or activates a grant. T30(i).
4. A recovery operation never reacquires its own held write reservation (§3A single-connection rule). T30(f).
5. Invalid chain distinguished from recoverable exceptions (below). T30(g), T36.
6. Per-job state or correctly serialized snapshots: no snapshots; derived by query (§3A). T27, T28.
7. Ordinary operations are blocked until an incomplete recovery is reconciled (below). T30(e).

**Commands** (`wf recover`, Coordinator, only with an owner turn that names the job; the turn is cited in every event it writes; never invoked by another command, never automatic):
- `wf recover begin <job> --reason …`: in one transaction, verify the chain; if valid, append `recovery-opened` (reason, owner turn, exception list found now). If a recovery is already open the command refuses (two recoverers racing: the write reservation serializes them; the second reads the open recovery and refuses).
- `wf recover reconcile <job>`: lists each recoverable exception and records an `exception-acknowledged` event per item (idempotent; safe to run again).
- `wf recover close <job>`: succeeds only if every listed exception is acknowledged and the chain verifies again; appends `recovery-closed`.
- `wf recover … --resume`: not a separate mode. State is in the database, so any of the three commands continues from what is recorded, in any order that its own preconditions allow. A recoverer that died leaves nothing to adopt or reclaim.

**Recovery open** for a job = the latest `recovery-opened` has no later `recovery-closed`. While open, §3A step 2 refuses every ordinary mutation (including `bind`, `append`, `accept`, `close`), the gate denies bound sessions for that job's paths, and `wf status` shows `recovery-open`. Only the three `wf recover` commands and read-only commands run.

**Chain classes (used at `begin`, at `close`, in every §3A step 3, and by `wf verify`):**
- **Invalid chain — hard blocker.** Sequence gap; `prev` or `self` hash mismatch; an event that fails schema or whose `content_sha256` does not match; engine `PRAGMA integrity_check` not "ok" `[DOC* S3]`; a head lower than, or a hash different from, an independently retained checkpoint (§3C); the database file replaced by another file between commands (identity mismatch). Effect: nothing is appended (appending would launder the defect), no repair runs, `recovery-opened` is **not** written (it cannot be appended safely), every mutating command and the gate refuse by their own chain check, and only the owner decides. Restoring from a backup is an **owner procedure with all processes stopped**, not a `wf` command in R1 (spec §10); the damaged file is preserved, not deleted.
- **Valid chain with recoverable exceptions — recovery proceeds and lists each one:** stale or missing exports; orphan export/backup temp files; an unreadable gate-log tail (gate.db is separate: its own chain, same classes); events whose follow-up non-transactional action (an export, a receipt file) did not happen. These are acknowledged one by one; none is silently repaired.
- Truncation of the tail is undetectable without an independently retained checkpoint (§3C); recovery does not change that.

**Limits stated honestly.** (1) A hung but live writer is a stop-the-process owner act; `wf` has no force path. (2) If the engine fails to roll back a hot journal, `wf` cannot fix it and must not delete the journal `[DOC S4]`. (3) All of §3A–§3B rests on engine behavior and Windows file locking that were **not tested** here (G-24, G-25, G-26, G-27); this is specified design under a pending qualification, not tested behavior.

**Audit sequence for reading back:** `recovery-opened` → `exception-acknowledged` (zero or more) → `recovery-closed`. A `recovery-opened` without `recovery-closed` is an exception shown by `wf verify` and `wf digest`.

## 3C. Integrity ladder (defect D) — what each level can and cannot show

| Level | Means | Supplied by | Does **not** show |
|---|---|---|---|
| 1. **Internal chain consistency** | Each event's `self` recomputes from its content and `prev` chains to the previous event; no sequence gap | `wf verify` | That the chain is the original: a party who can rewrite the **whole** chain (all rows, in order) can recompute every hash and produce a consistent chain |
| 2. **Accidental-corruption detection** | A flipped bit, truncated page or partial write in content shows as a hash mismatch or an engine structure error | Level 1 plus `PRAGMA integrity_check` (structure, not truth) `[DOC* S3]` | Deliberate edits that recompute hashes; correctness of what was recorded; corruption the engine's check cannot see |
| 3. **Tamper evidence** | A change to history, or deletion of the tail, is visible because the verifier compares the chain with an **independently retained checkpoint** `(job, seq, self)`: the current chain must contain that seq with that hash. Rewriting events at or before the checkpoint changes the hash at the checkpoint; deleting the tail drops below the checkpoint seq | A checkpoint retained **outside the party's write reach** (below) | Events after the last checkpoint; a rewriter who also controls the retained copy; anything about authorship |
| 4. **Authenticity** | Who actually wrote an event, and that the quoted owner approval is genuine | **Not supplied in R1.** Would need a signing key in separate custody and a verification step | — R1 makes no authenticity claim; `author`, `session_id`, `generation` are caller-supplied text (R09) |

**Retained checkpoint.** `wf digest` prints the head `(seq, self)` for each job plus the database file's size. **Who retains it (proposed, owner decision G-28):** the owner authors a checkpoint message in the owner-facing chat, containing the job id, `seq` and `self` copied from the digest (a value the owner types or pastes in the owner's own turn). A local note is a lesser alternative and is **not** assumed out of reach of workers running under the same account. **What the checkpoint supports:** `wf verify --checkpoint` can show that the current chain contains that `seq` with that `self` hash, so truncation below that `seq` and rewriting at or before it are detectable against that owner-authored value. **What it does not prove:** that events after that `seq` are unaltered; authorship or authenticity of any event; that the chat record itself is unaltered (it is held by a service the owner does not fully control); that the value was copied from an untampered state; that no worker can read it; or anything about custody of the database. **When:** at grant activation, at each acceptance, and at closeout; more often is better. **What it adds:** truncation and whole-chain-rewrite become detectable up to the last retained point. **Remaining trust limits:** (1) the retained copy is only as independent as its custody: a chat transcript is stored by a service the owner does not fully control; a local file is on the same machine and, unless shown otherwise, readable and writable by the same account as the workers; (2) events after the last checkpoint are unprotected until the next one; (3) if the rewriter also alters the retained copy, nothing detects it; (4) detection happens only when `wf verify` is run against the retained value; (5) none of this is independent custody of the database. **No independent-custody claim is made from hashes alone.** Test: T36.

**Backup and restore (integrity limits).** Backups use the engine's backup interface or a copy taken with no writer `[DOC* S5, S4]`; copying during an open transaction is a documented corruption cause. **Backup validity** is established by (a) the backup's own `PRAGMA integrity_check`, (b) its own hash chain verifying to its own head, and (c) its head `(job, seq, self)` existing in the source's history. **Equality of the backup head with the source head** is asserted only when the writer is stopped and the compared state is fixed; while a writer runs the source head may have advanced. A restored file is also verified against the retained checkpoint before use. A backup on the same disk protects against file damage, not against loss of the disk. Restore is an owner procedure (§3B); testing it is Q6.

## 4. Interfaces

### 4.1 `wf` CLI (Python stdlib, `python -I`; single code path to the ledger, serialized by the §3A write transaction)

| Command | Who | Effect |
|---|---|---|
| `wf job new <contract.json>` | Coordinator | Validates required fields; computes baseline manifest; pins contract hash; inserts the contract and the first event in one transaction (§3A); `contract.json` is an export |
| `wf grant propose\|quote\|activate\|revoke\|expire` | Coordinator (`quote` records the owner turn, pending provenance; `activate` needs the block-hash match of §3; revoke/cancel may be recorded by anyone and takes effect at once) | Grant events |
| `wf grant-check <job> --role --action --path` | Anyone / gate | Pure check: grant exists, activated with source turn, in force, covers action and path, not widening contract scope. Exit 0 allow / 1 deny + reason |
| `wf bind <job> --role --session <id>` | Coordinator | New binding, `generation+1`. **Refuses** if the grant is not `activated`, or the role, tools or paths fall outside the ARS (§2B), or if a prior binding exists without a `stop-confirmed` event and a completed `wf reconcile` showing no outstanding operation. **There is no override in R1.** Replacing an executor whose status is unresolved is outside R1 (§10), can never satisfy T16 or the §6.2 recovery proof, and would need its own owner-authorized exceptional-risk procedure |
| `wf append <job> --type …` | Role-checked | Message, intent, receipt, blocker, handoff, delivered, stop-confirmed, recovery-decision, ack-reconcile, ack-assignment (recovery events are written only by `wf recover`). Applies §5 message rules |
| `wf status <job>` | Anyone | Four-axis derived status |
| `wf observe <job>` | Anyone | **Read-only** supervision report (§7). Writes only its own bounded log |
| `wf recover begin\|reconcile\|close <job>` | Coordinator, owner turn cited | §3B only: appends `recovery-opened`, `exception-acknowledged`, `recovery-closed`. Never invoked by another command. Refuses on an invalid chain. No force and no `stop-confirmed` substitute |
| `wf export <job>` / `wf backup <path>` | Coordinator or owner | `export`: derived JSON Lines + text digest with hash recorded (never authority). `backup`: engine backup interface only (not a file copy); refuses to overwrite an existing file; verification against the retained checkpoint is a separate `wf verify --checkpoint` |
| `wf reconcile <job>` | Coordinator | Compares scope files, receipts, intents and gate-log attempts; emits an exceptions report (§6) |
| `wf accept <job>` | Reviewer | Records acceptance or rejection (§8 rules) |
| `wf close <job>` | Coordinator | Refused unless accepted-or-abandoned, intervention declaration present, `wf verify` clean |
| `wf log-intervention <job>` | Coordinator or owner | Appends an intervention entry |
| `wf cancel <job>` | Anyone | Records owner cancellation/revocation; gate denies the very next tool call |
| `wf verify [<job>]` | Anyone | Integrity check by the ladder of §3C: chain, engine `integrity_check`, optional `--checkpoint seq:hash` supplied by the owner. The output states which level was checked and what it does not show |
| `wf digest [<job>\|--all]` | Anyone | Owner surface (R16): Done / Active / Needs-owner (decision queue, each with recommendation), evidence links, chain head `(seq, self)` for the owner's retained checkpoint, ARS and binding table, intervention totals as recorded |

### 4.2 Gate hook (PreToolUse + PostToolUse)

- **Matches** the file-write tools and both shell tools (`Write|Edit|NotebookEdit|Bash|PowerShell`; exact tool-name list confirmed at B0) `[DOC* Pg4]`. Windows: both shell tools, backslash paths normalized before comparison.
- **Decision logic** (deny-by-default for sessions bound to an active job; fail-closed on any error):
  1. Open `ledger/ledger.db` read-only in a read transaction with its own short timeout (the gate never writes the ledger). Deny if the database is unreadable, locked past the timeout, at an unexpected file identity, if the chain tail fails its check, or if the job has an open recovery (for sessions touching its paths). Derive grant and binding state from events in that same read transaction; there is no snapshot to be stale.
  2. Session bound to an active job → allow only if the job has an activated, unrevoked, unexpired grant covering action and path (same logic as `wf grant-check`; byte-identical corpus tested) and the session id equals the current binding for its role and generation.
  3. Session **not** bound but touching a job's write scope or ledger paths → deny.
  4. Direct Write/Edit to `ledger/**`, `jobs/**/export/**`, `runtime/**` and the gate itself → deny for everyone (the ledger has one writer, `wf`; `runtime/gate/gate.db` has one writer, the gate).
  5. Cancellation or revocation recorded → deny everything for that job.
- **Shell channel**: not preventable by text matching `[DOC* Pg3]` `[REC hooks/README.md]`. The gate logs shell attempts. Detection is limited to the monitored boundary in §4.3, and every acceptance record states that limit.
- **Pinned install** under rule C1 (posture E1): a copied, single-file hook in `C:\Users\<USER>\.claude\hooks\`, registered in the profile's settings template in the same change; never referenced from the Workforce working tree `[REC]`. **E1 is custody against accident and misdirection, not independent custody.** A worker with shell access runs as the same user and can edit the hook, its registration or the settings file. The gate-file hash checked at closeout detects that afterwards and cannot stop it mid-job. E1 is acceptable for a narrowly supervised prototype only. It is **not** strict containment and **not** sufficient for unattended operation, which needs a posture where the worker cannot write the gate (Decision 3).
- **Preconditions to verify before relying on it** (G-05, G-07, G-12): the hook input carries a usable session id; local Claude Code version supports `onFailure: "block"` (v2.1.295+) so a timeout cannot silently allow `[DOC* Pg4]`; hook `session_id` for subagents. Version inspection does not demonstrate fail-closed behavior, and neither does an intentional exit code 2 (an explicit denial decision, documented as blocking). Fail-closed behavior is shown only by the confined Q1b tests of unexpected hook error (Q1b-b) and timeout (Q1b-c), each claimed separately (OPT-Q1B, strikable from the grant); otherwise it stays NOT RUN and blocks reliance on the gate before B3. If G-05 fails: the caller-identification control is unsupported; T01 (identity part), T13, T17, T32 and the gate parts of T34 are recorded "not satisfied as specified: control unsupported", not relabeled detection; a single-builder rule plus owner-confirmed stop is an option only after an owner decision (QUALIFICATION-PROPOSAL §5.2).

### 4.3 Monitored boundary and the pilot stance

**Detection claims cover exactly this boundary and nothing else:** (1) every path in the contract's write scope and read scope, (2) every path in a contract-declared `watch` list (e.g. the job's parent folder, the gate file, the profile settings file), (3) the job's ledger and runtime paths. The baseline manifest hashes (1)–(3); `wf accept` compares the final manifest, receipts and gate log over that set. A write to any path outside it **is not detected**, and acceptance records say so in those words. Scope-diff proves "nothing changed inside the monitored boundary beyond the receipts", never "nothing changed anywhere".

A disposable working folder does not stop a process from writing outside it. The pilot contract must choose one stance explicitly (these names are for the pilot only; "S1" elsewhere in the planning package refers to the watchdog experiment and is unrelated):

- **P-CONTAINED — real filesystem containment** (a restricted account or an OS sandbox whose scope is verified). No such mechanism is established (G-06 is unresolved) and none is specified for R1.
- **P-SUPERVISED — supervised, detect-only.** The owner is present, scope is a disposable copy, no secrets are reachable, and acceptance claims are limited to the monitored boundary. **Proposed first-pilot posture, together with enforcement posture E1** (owner direction, turn 3, Decision 3): a pinned user-level gate, owner-supervised execution, disposable local scope, detection limited to the declared monitored boundary. This does **not** establish independent custody, whole-machine containment, or suitability for unattended or production-adjacent work, and **no pilot is authorized now**. Before any later automatic recovery or unattended work, enforcement must be revisited and a qualified protection boundary required.

The stance is part of Decision 3 (register): E1 + P-SUPERVISED proposed; not an authorization to run.

### 4.4 Acceptance comparison (defect C) — changed paths against permitted changes

`wf accept` does **not** require every manifest file to exist or to be unchanged. It computes the set of **changed paths** inside the monitored boundary (§4.3) and checks each against the permitted classes below. A path change is: **added** (absent in baseline, present now), **modified** (present in both, hash differs), **deleted** (present in baseline, absent now), or **renamed** (reported as a delete plus an add).

| Class | Paths | Rule at acceptance |
|---|---|---|
| **P1 builder-authorized** | Contract write scope (globs), minus P2/P3 | Add/modify/delete allowed. Each **consequential** change should have a builder `intent`/`receipt` (sha before/after); a change with no receipt is reported as *unrecorded change* (not rejected by itself; the reviewer decides, §6.2 step 3 classes). A deletion of a baseline file is allowed only if the path is in P1 |
| **P2 read-only** | Contract read scope, and anything in the manifest not in P1 | Must be **identical to baseline** (hash). Any add, modify or delete → violation, acceptance rejected |
| **P3 watched configuration** | `watch` list: the gate file, profile settings file, hook registration, job parent folder, contract/grant files | Must be **unchanged** unless the contract names the path as separately authorized; an authorized change must be listed in the contract and is reported as such. Any other change → violation |
| **P4 expected coordination updates** | Ledger database and its journal, `runtime/gate/gate.db`, `jobs/<job>/export/`, closeout bundle, `runtime/observe/<job>.log` | Allowed to change; **not** compared by hash against baseline. Checked instead by `wf verify` (chain, retained checkpoint, gate-log chain). Added or deleted files in the ledger/runtime folders other than the listed ones → violation |
| **P5 outside the monitored boundary** | Everything else | **No detection claim.** Not compared; the acceptance record says so in those words (§4.3). The comparison never expands the boundary silently; widening it is a contract amendment (voids the approval, §2B) |

Rules:
1. **Manifest completeness is not the test.** A missing P1 file is not a failure; a missing P2/P3 file is a deletion violation.
2. **Path resolution (defect C, cases).** Before any comparison every path is resolved with the same function in `wf job new` (baseline), `wf accept` and the gate: absolute, drive letter normalized, separators normalized, `.`/`..` collapsed, **Windows case-insensitive comparison**, trailing dots/spaces and short (8.3) names normalized where the platform resolves them, and **links/junctions resolved**: a path that resolves outside the boundary is P5 and is reported as *resolves outside boundary*, never treated as in-scope. A path that cannot be resolved (denied, too long, device name, alternate data stream syntax) is a violation (fail closed), not skipped. Whether the host's filesystem resolves these as assumed is NOT-TESTED (G-13, G-29); the function is specified, the behavior unverified.
3. **Secrets.** The manifest hashes file *contents* only for paths in P1–P3 that the contract marks as comparable; paths the contract marks **presence-only** are compared by existence, size and timestamp and never read. No comparison reads or stores the content of a path listed as secret-bearing; a path whose classification is unknown is presence-only.
4. **Result per path** is one of: *allowed (P1)*, *unchanged (P2/P3)*, *authorized config change*, *coordination (P4, verified by verify)*, *violation*, *outside boundary (not detected)*. Acceptance requires zero *violation*. The report lists every non-trivial row; it is evidence for the reviewer, not a verdict by itself.
5. The check proves "no change inside the monitored boundary beyond the permitted classes and the receipts"; it never proves "nothing changed anywhere" (§4.3). Test: T35.

## 5. Message and ownership rules (R10, R11)

Carried from handoff FINAL v3 §6, implemented in `wf append` and `wf verify`:
1. **Per-message disposition** recorded as a separate event: `received` → `accepted` | `rejected(reason)`.
2. **Duplicate rule:** same `msg_id` **and** same `content_sha256` → ignore, log `duplicate-ignored`. Same `msg_id`, different content → `rejected(conflict)`, raise exception, no dependent work proceeds.
3. **Missing field** (author, session, generation, job, type, `prev`) → `rejected(malformed)`. When the missing field is a required command-line option of the `wf` CLI (author, session), the CLI reports a usage error (exit 2, result code `usage`) before an entry exists (T09, D-017); an entry that reaches the engine with the field missing or empty is `rejected(malformed)` as stated.
4. **Stale sender:** `generation` below the current binding → `rejected(stale)`; its content is retained as evidence, never acted on.
5. **Two claimants** for one role/generation → both blocked, exception to coordinator, then owner.
6. **Handoff** with scope or authority wider than the active grant/contract → `rejected(widening)`.
7. **Grant without a cited source owner turn** → `rejected(no-provenance)`; it never reaches `quoted`. **A quoted grant without block-hash match never reaches `activated`** (`rejected(unbound-approval)`). A citation is a provenance reference only: nothing verifies the quoted approval is genuine (layer O).
8. **Author and session fields are procedural attribution, not authentication** (R09). Layer V enforces consistency and form; caller identification comes only from the gate's session id for hooked tool calls (if G-05 holds); the rest is the owner reading the digest (§2A).
9. **Files are records, notifications are doorbells.** In R1 the doorbell is the owner (or coordinator in the owner's presence) telling a session to read a job packet. That manual relay is logged as an intervention labelled `relay`; measuring it is intentional.

## 6. Normal flow and failure flow

### 6.1 Normal flow
1. **Intake.** Owner states the request in chat. Coordinator drafts `contract.json` (verbatim quote, baseline, acceptance checks, scope, tools per role), runs `wf job new`.
2. **Grant check.** Coordinator proposes a grant and shows the Grant Confirmation Block; the owner approves (the original approval suffices if it follows the block, §3 grant ordering); coordinator records `quoted`, then `activated` only on block-hash match. Until `activated`, no assignment (step 3) and no tool action is permitted. `wf grant-check` dry-runs the contract's own actions to catch contract/grant mismatch before assignment.
3. **Assignment.** `wf bind` builder (gen 1) with the builder's session id, inside the Approved Role Set (§2B); the session confirms with `ack-assignment`; reviewer bound later under the same rules. The owner opens the builder session once and points it to the job packet (logged `relay`).
4. **Execution.** Builder works inside write scope. Gate allows/denies and logs attempts and completions. Builder writes `intent` before and `receipt` after consequential steps (file path, sha before/after). Builder posts `delivered` with claims → evidence refs.
5. **Acceptance.** Fresh reviewer session records an independent view (`review-view` event) from contract, baseline, diff and evidence, **then** reads builder narrative, **then** runs the contract's checks itself. `wf accept` requires: contract hash matches pin; every changed path passes the §4.4 comparison (permitted-change classes, not manifest existence); every criterion has a reviewer-run result; reviewer session ≠ builder session; `environment` named; limits stated. Otherwise rejected with reason.
6. **Intervention log.** Coordinator lists every owner intervention seen in the record; owner confirms or corrects at closeout. Minutes: owner-reported number or `unknown` (never estimated by an agent from message counts).
7. **Closeout.** `wf close` (needs clean `wf verify`). `wf digest` produces the owner summary with chain head hash.

### 6.2 Failure flow — manual interruption recovery (the R1 recovery proof)
1. **Detect.** Owner or coordinator notices no progress → `wf observe` classifies *active / slow / silent / blocked* (read-only).
2. **Stop the old executor.** Owner closes the builder session (an owner or delegated act). Coordinator records `stop-confirmed`: how, when, and an owner-reported check that no child process or outstanding operation from that session remains. Without it `wf bind` refuses to advance the generation, and R1 has no override. A generation edit alone is not fencing (R13). If termination cannot be confirmed the job stays `interrupted`; it is not replaced.
3. **Reconcile before any retry (R12).** `wf reconcile` compares: baseline manifest, receipts, intents, gate-log attempts without completions, and the actual files now on disk. Each in-scope path is classified: *matches receipt* · *matches baseline (not executed)* · *matches neither (unrecorded change)* · *attempt without completion (ambiguous)*. Missing completion does not prove nonexecution.
4. **Decide.** Coordinator writes `recovery-decision`: resume (new session, gen+1) · restore path X to baseline · abandon. Ambiguous items go to the owner via the digest.
5. **Resume.** `wf bind` gen+1, only inside the ARS delegation rules (§2B). The new builder's first event must be `ack-reconcile` listing each exception it understood. The gate now denies the old session id (T13).
6. **Intervention logged** (label `restart`; avoidable-or-necessary; minutes or `unknown`).

### 6.3 Other failure paths (abbreviated)
- **Cancel / revoke mid-job:** `wf cancel` → gate denies next tool call; in-flight call is not interrupted `[INF]`; reconcile shows what completed.
- **Gate/ledger failure:** deny, surface to owner as a blocker; never allow-on-error. Timeout behavior depends on version (G-12).
- **Verify failure:** job cannot be accepted or closed; exception raised.
- **Reviewer finds a §4.4 violation:** acceptance rejected; the violating change is preserved as evidence and becomes a fixture candidate (R21).

## 7. Observation-only supervision — the boundary (R13)

`wf observe` **may**: read events, the gate log, scope file hashes/timestamps; classify; write its own bounded log (`runtime/observe/<job>.log`, size-capped, overwritten ring).
It **may not**: contact any session, send any message, stop/resume/replace anything, write bindings or grants, edit job files, read session transcripts, or run on a schedule.
Anything outside that list is *contact* or *recover*, which are separate capabilities, qualified separately, and need their own owner grant after R1 (the planning package's watchdog experiment, S1 there). Observation success never implies recovery permission. Thresholds (silent = N minutes) are contract parameters and placeholders until measured. Scheduling `wf observe` is a post-R1 step: Desktop scheduled tasks stall on prompts, run only while the app is open, and cannot message desktop sessions `[DOC Pg1]`; a Task Scheduler trigger would need an owner-approved task and a lease lock `[DOC Pg10]` `[NS Pg10]`.

## 8. Acceptance cases (specified; **none run**)

Layers are those of §2A: **V** record validation · **I** caller identification · **O** owner-approval verification · **E** gate enforcement. Result column: **Reject/Deny** = refused at that layer for hooked tools or `wf` submissions only · **Detect** = found after the fact within the §4.3 boundary · **Proc** = procedural only. All required for R1 build acceptance unless marked.

| ID | Case | Expected | Layer | Result |
|---|---|---|---|---|
| T01 | Two sessions claim builder (or foreman) role | Both blocked; exception; no dependent work | V (I if G-05) | Reject |
| T02 | Same message number appears twice (number reuse) | Never two events at one sequence number: same `msg_id`+content → `duplicate-ignored`, different content → `rejected(conflict)`; the sequence key is `(job, seq)` regardless of type (see T31) | V | Reject |
| T03 | Grant claims owner approval, no source turn | `rejected(no-provenance)`; never in force. A cited turn is not verified genuine; the owner confirms in the digest | V; O is procedural | Reject / Proc |
| T04 | Superseded session sends a new instruction | `rejected(stale)`; kept as evidence | V | Reject |
| T05 | Same message received twice | One effect, `duplicate-ignored` logged | V | Reject |
| T06 | Worker restarts after changing files, before reporting | Reconcile classifies; no retry before `ack-reconcile` | V | Reject / Detect |
| T07 | Doorbell missed, durable message remains | Message processed from ledger on next read; no loss | V | Reject |
| T08 | Same ID, different content | `rejected(conflict)` | V | Reject |
| T09 | Entry missing author or session fields | `rejected(malformed)` when an entry whose author or session field is missing or empty reaches the engine (section 5 rule 3; the same label applies to a missing field in a submitted payload or contract); an explicit CLI usage error (exit 2, result code `usage`, error naming the missing option) when the author or session is a required command-line option that was not supplied, so that no entry is formed (D-017). Either outcome leaves the ledger unchanged. The usage path is a command-line input check only and is not the rule for malformed submissions generally | V | Reject |
| T10 | Handoff tries to widen scope or authority | `rejected(widening)` | V | Reject |
| T11 | Owner cancellation/revocation arrives mid-job | Next hooked tool call denied; reconcile accurate | E | Deny |
| T12 | Builder session closed (closed-session recovery, manual) | Recovery from record only; no automation claimed | — | Proc |
| T13 | Superseded session attempts a hooked write after replacement | Denied by gate (needs G-05; if unsupported: not satisfied as specified, not detection) | I + E | Deny |
| T14 | Slow-active vs silent vs blocked builder | `wf observe` distinguishes using gate log + events | V | Detect |
| T15 | Hooked action attempted under revoked grant | Denied; logged | E | Deny |
| T16 | Recovery/replacement triggered twice for the same interruption | Second is refused/no-op; one generation bump; no override path exists; replacement without `stop-confirmed` and completed reconcile refused | V | Reject |
| T17 | Unbound session writes (hooked tools) into a job's scope or ledger | Denied (needs G-05; if unsupported: not satisfied as specified, not detection) | I + E | Deny |
| T18 | Write outside write scope — (a) via Write/Edit, (b) via shell | (a) denied; (b) detected at `wf accept` **only inside the §4.3 boundary and per the §4.4 classes**; outside it, not detected and the record says so | (a) E · (b) V over boundary | Deny / Detect (bounded) |
| T19 | Gate or ledger database corrupt, locked, unreadable, at an unexpected identity, or recovery open | Gate and `wf` deny, blocker raised; never allow. Gate read has its own timeout and denies on expiry | E + V | Deny |
| T20 | Closeout without evidence-linked claims; or "delivered" treated as accepted | `wf accept`/`wf close` refuse | V | Reject |
| T21 | Closeout without intervention declaration; owner minutes absent | Refuse until entry or explicit `none`; minutes may be `unknown`, never agent-estimated | V | Reject |
| T22 | `wf observe` asked to write elsewhere / contact / resume | Refuses; own log only | V | Reject |
| T23 | Event row edited, deleted mid-chain, or reordered; triggers dropped then rows edited | `wf verify` fails (chain). Whole-chain rewrite is **not** caught here — see T36 | V | Detect |
| T24 | Acceptance cites wrong contract hash / baseline / env; reviewer = builder session (as declared) | Rejected. Same-session check compares declared ids | V | Reject |
| T25 | Hooked write into AIEP or any out-of-scope repo path by a bound session | Denied (allow-list); not a claim about unbound sessions' own work or the shell | E | Deny |
| T26 | `wf digest` content | Shows Done / Active / Needs-owner with recommendation, evidence links, activated grants with quotes and block hash, binding table, ARS, head `(seq, self)` for the retained checkpoint; no raw relay | V | Detect |
| T27 | Concurrency: N simultaneous `wf append` calls on one job; simultaneous mutations on two jobs; N simultaneous gate-log writes in one session; a reader holding a read transaction while a writer commits | Distinct consecutive `seq`, valid `prev` chain, no lost or duplicated event, for ledger **and** gate log; writers serialize through `BEGIN IMMEDIATE`; a writer that cannot get the reservation within the busy timeout fails visibly with a structured `lock-timeout` result returned to the caller, and a writer whose COMMIT does not complete within the held-phase bound fails visibly with a structured `commit-timeout` result; neither failure adds a committed ledger row (D-017); the claimant-conflict blocker row (section 5 rule 5), any separately specified persistent-blocker duty and the prevention of dependent work (section 5 rule 2) are unchanged by this clause; no function opens a second write connection; reader delay is bounded or the gate denies | V (§3A) | Reject |
| T28 | Process killed (a) before commit, (b) during commit, (c) after commit before export refresh; hot journal present at next open; export stale | (a),(b) next open rolls back to a valid prior state, no partial event, `wf` never deletes or moves a journal file; (c) valid chain, stale export reported as a recoverable exception, `wf export` regenerates. No lock file exists to go stale; a hung live writer yields a bounded-wait blocker, never a force | V + engine | Reject / Detect |
| T29 | Grant `quoted` but quote lacks grant id/block hash; or contract amended after approval; or coordinator tries `activate` directly | Stays `quoted`/voided (`rejected(unbound-approval)`); gate and `grant-check` treat it as no grant; no assignment | V + E; O procedural | Reject / Deny |
| T30 | Recovery (fault-injected fixtures): (a) `recover begin` while a recovery is already open, and two recoverers racing; (b) recoverer killed after `recovery-opened`, at each of: before reconcile, mid-reconcile, after reconcile, before close; then the commands re-run; (c) ordinary commands (`append`, `bind`, `accept`, `close`) and the gate during open recovery and after close; (d) `recover close` with an unacknowledged exception or an invalid chain; (e) `recover` command invoked from another command; (f) a recovery helper that tries to open a second write connection; (g) invalid chain (gap, hash mismatch, bad `content_sha256`, `integrity_check` failure, head below retained checkpoint, file identity changed) vs recoverable exceptions (stale/missing export, orphan temp, gate-log tail); (h) database replaced between commands; (i) recovery on a job with no `stop-confirmed` | (a) exactly one `recovery-opened`; the second refuses; (b) every re-run reaches the same final state with no duplicate `recovery-opened`/`closed` and each exception acknowledged once; (c) every ordinary command and the gate refuse while open, all succeed after close; (d) close refuses; (e) refused: only an owner-cited `wf recover` run opens; (f) caught by the single-connection rule (test fails if a second `BEGIN IMMEDIATE` is attempted on the held reservation); (g) invalid: nothing appended, no `recovery-opened`, blocker, ordinary commands refuse by their own chain check; recoverable: recovery proceeds and lists each exception; (h) identity mismatch → invalid-chain class; (i) recovery events are never accepted as `stop-confirmed`; `wf bind` still refuses; no override | V | Reject / Detect |
| T31 | **Sequence identity (A):** two commands with *different event types* (e.g. `message` and `grant`) simultaneously target the same next sequence; and a direct insert at an existing `(job, seq)` with a different type | Exactly one row at that `(job, seq)`; the loser either takes `seq+1` after reading the new head or fails with a constraint error, rolls back, and returns a structured `rejected(constraint)` result carrying the constraint error to the caller (D-017); the failed attempt writes no ledger row; this result does not replace the claimant-conflict blocker row (section 5 rule 5) or any separately specified persistent-blocker or dependent-work-prevention duty; chain unchanged and valid; no reliance on filename uniqueness | V | Reject |
| T32 | **Approved roles vs assignments (B):** bind a session to a role not in the ARS; with tools/paths wider than the ARS; a second live session in a role; reviewer = builder's session; a bind before the grant is `activated` | Each refused with a reason; no assignment event written; the gate treats the unbound session as unbound | V + E | Reject / Deny |
| T33 | **Replacement rules (B/E):** replacement (a) with `stop-confirmed` + completed reconcile + first-event `ack-reconcile`; (b) with the delegation rule disallowing replacement; (c) count exceeded; (d) replacement permissions wider than the replaced session's; (e) termination unconfirmed; (f) generation raised without a bind | (a) allowed at generation+1, ARS unchanged; (b),(c),(d) refused and need a renewed block; (e) refused with no override; (f) no such operation exists | V | Reject |
| T34 | **Approval invalidation (B):** contract amended; ARS or actions amended; revocation; expiry; environment/stance change; renewed block confirmed while bindings exist | Grant leaves `activated` at once (gate and `grant-check` deny on the next call); existing bindings carry no authority; a quote predating the current block hash never carries over; after renewal, bindings inside the new ARS stay, others must rebind at generation+1 | V + E; O procedural | Reject / Deny |
| T35 | **Acceptance comparison (C):** additions, modifications and deletions in each class P1–P5; a manifest file missing from P1; a P2 path modified, added or deleted; a P3 path changed with and without separate authorization; expected P4 updates; unexpected files in ledger/runtime folders; path-resolution cases (case differences, `..`, links/junctions, short names, trailing dots, alternate stream syntax, unresolvable paths); a presence-only path | Per §4.4: only P1 changes pass; P2/P3 violations reject; P4 verified by `wf verify`; unresolvable path is a violation; a path resolving outside the boundary is reported as outside boundary and not as in scope; presence-only paths are never read; the record states that outside-boundary changes are not detected | V over boundary | Reject / Detect (bounded) |
| T36 | **Integrity ladder (D):** (a) flip a bit in a stored event; (b) rewrite the whole chain consistently (recompute all hashes); (c) delete the tail; (d) the same (b)/(c) with an independently retained checkpoint supplied to `wf verify`; (e) the checkpoint itself altered | (a) detected (level 1–2); (b) and (c) **pass `wf verify` without a checkpoint** and the output says the chain is internally consistent only; (d) detected; (e) not detected, and the output states the retained value is trusted; no output ever uses "authentic" or "tamper-proof" | V | Detect (bounded) |
| T37 | **Backup, restore, export:** backup via the engine's backup interface while a writer is active; restore into a separate location with all processes stopped; restored file verified against the retained checkpoint; stale/missing export; an export tampered with after generation | Backup passes its own integrity_check, its own chain verifies, and its head is present in source history (head equality with the source asserted only with the writer stopped); restored chain verifies against the retained head or is reported as not matching; the damaged file is never deleted; export is regenerable and never authority; tampered export is reported by hash recorded at export | V | Detect |

**Live-pilot acceptance additionally requires** one staged interruption exercised on the real pilot (owner-authorized at that time) with §6.2 followed, and T06/T12/T16 observed live, not only on fixtures.
Fixtures T01–T11 are the handoff's eleven required protocol cases, in handoff order. T12–T30 are Rev 1–4 additions (T27, T28, T30 rewritten for S-DB in Rev 5; T02, T16, T18, T19, T23, T26 adjusted). T31–T37 are Rev 5 additions for defects A–E. Deferred automation cannot satisfy a required case. **Every case is NOT RUN.** Reading a case is not a test. Under S-FILE (fallback) T27, T28, T30 revert to their Rev 4 text (history\rev4) with the §6 corrections of STORAGE-COMPARISON.

## 9. Requirements-to-tests mapping

| Req | R1 coverage | Tests / note |
|---|---|---|
| R01 Outcome | Pilot job | T20, T21; pilot acceptance |
| R02 Horizon | Out (horizon) | — |
| R03 Boundaries | Partial | T25; Workforce/AIEP split in layout doc |
| R04 Planning gate | Governance | Not testable; R1 build needs a separate grant |
| R05 Vertical slice | Covered | Whole of R1 |
| R06 Two lanes | Partial | Contract pinned (T24); improvement lane deferred |
| R07 Job contract | Covered | `wf job new` field validation; T24, T35 |
| R08 Status axes | Covered | T20; derived status |
| R09 Enforcement | **Partial** | Layers V/I/O/E (§2A). T11, T13, T15, T17–T19, T25, T32, T34 deny for hooked tools; shell channel detect-only inside the §4.3 boundary and the §4.4 classes (T18, T35); O is procedural; integrity limits T36 |
| R10 Ownership | Covered | T01, T04, T13, T17, T27, T30(i), T31–T34 |
| R11 Messages | Covered | T02, T05, T07–T10 |
| R12 Reconcile first | Covered | T06, T12, T16, T30, T33 |
| R13 Watchdog | Observe only | T14, T22; contact/recover deferred |
| R14 Quality | Partial | T20, T24; fresh-reviewer order is procedural; not human independence |
| R15 Measurement | Covered (record) | T21; no claims from one job |
| R16 Owner surface | Covered (basic) | T26 |
| R17 Permissions | Partial | Role tool lists in contract + existing permission rules; "no bypass" is procedural |
| R18 Workforce | Constraint | 3 roles used; no roster |
| R19 Modes | Managed only | Parallel excluded |
| R20 Qualification | Evidence only | R1 qualifies nothing; k=5/x=1 not applied |
| R21 Evolution | Partial | Failures kept as events; fixtures from failures |
| R22 Ambition | Governance | The parking lot is CURRENT-STATE *Needs your call / Captured* (owner direction, turn 3); no WORKLOG migration |
| R23 Continuity | Layout doc | RECORD-AND-LAYOUT-PROPOSAL.md; backup/restore/export T37 |

## 10. Exclusions

Automatic recovery or replacement; any replacement while the old executor's termination is unconfirmed (no `bind` override); scheduler/Task Scheduler/Desktop scheduled tasks; contact capability; standing grants; queues and work selection; parallel builders; cross-session messaging (`send_message`, doorbells) — optionally addable later under a separate grant and the `crossSessionInbound` setting `[DOC* Pg5]`; roster beyond three roles; improvement lane and promotion; runtime portability proof; Temporal or other engines; headless `claude -p` orchestration; Revit binaries, databases or any AIEP/BIMpossible production scope; target-environment acceptance beyond the disposable local scope; any claim of unattended operation; SQLite WAL mode; any `wf` restore command (restore is an owner procedure); signing or any authenticity claim (§3C level 4); automatic migration between S-DB and S-FILE.

## 11. Dependencies (all unverified locally — G-07)

Python 3 with the standard `sqlite3` module (the owner's pinned hooks are `.py`, so Python is present `[REC]`; Python and bundled SQLite versions not inspected, G-24) · PowerShell 7+ (confirmed by environment description) · Claude Code with hooks and the version-gated fields above · a local NTFS volume with reliable file locks and flush (G-25; observed fixed NTFS, not tested). Under S-FILE the primitives of G-13 instead · git optional (baseline falls back to manifest hashing) · no network, no installs, no credentials, no spend.

## 12. Pilot job selection (pending; not selected here)

Criteria: local; reversible; disposable copy as scope; objectively checkable acceptance; no secrets; not AIEP/BIMpossible production; small enough for one builder context; includes at least one step whose result can be ambiguous after a kill (to make reconciliation real). Candidates (unchosen): (a) a fixture-backed validator for a small data/record format, (b) a documentation-consistency repair inside a copied folder. Choose only after current-state inspection and an agreed acceptance list (owner-only).

## 13. Build order (proposal; no durations claimed)

| Step | Delivers | Exit evidence |
|---|---|---|
| B0 | Owner-authorized qualification probes Q1–Q8 (QUALIFICATION-PROPOSAL.md; NOT IN FORCE until the owner issues the grant): hook input fields (G-05), versions (G-07, G-24), SQLite contention/crash/backup behavior (G-25–G-27), hook unexpected-error and timeout behavior under `onFailure` (G-12, Q1b), path-resolution behavior (G-29). A failed probe pauses it and its dependents; independent probes continue (payload 8.1) | Written probe results; owner disposition on T13/T17 (supported, or "not satisfied as specified") and on the storage design (S-DB confirmed, or an explicit owner disposition per QUALIFICATION-PROPOSAL §5.1; S-FILE only by a new decision) |
| B1 | Schema, constraints, validator, write transaction (§3A), recovery commands (§3B), integrity ladder (§3C), `wf verify`, `wf append` rules | Fixtures T01–T05, T07–T10, T23, T24, T27–T37 pass (T28, T30 with fault injection) |
| B2 | `wf` status/bind/reconcile/observe/accept (§4.4)/close/digest/log-intervention/export/backup | T06, T11, T12, T14, T16, T20–T22, T26, T32–T35, T37 pass |
| B3 | Gate hook (pinned, C1) | T13, T15, T17–T19, T25 pass |
| B4 | Fixture replay of a scripted job end to end (no live workers) | Replay accepted, interruption replay reconciled |
| B5 | Live pilot with staged interruption | §8 live-pilot acceptance; intervention log complete |

## 13A. Proposed continuous R1 release-grant structure (**NOT IN FORCE**; a proposal for a later owner decision)

Purpose: let the owner approve the R1 build once for several named stages, so that stages advance on evidence, not on repeated approvals. This is a structure, not a grant: it authorizes nothing, creates no obligation to issue it, and is separate from the qualification grant (which has no repair, retry or review-fix-retest authority; QUALIFICATION-PROPOSAL payload §7).

**Prerequisites (owner-only, before such a grant can be issued):** qualification results delivered and dispositions recorded; D-008 (storage) decided; mission adoption decided (CURRENT-STATE Decision 2); retained-checkpoint mechanism decided (G-28); the R1 grant itself (§14 item 3). B3 additionally needs the profile change that installs the gate (§14 item 4: touches `F:\Claude-Profile` and `~\.claude\hooks`; mandatory protection applies) and B5 additionally needs the pilot job and stance contract (§12, §14 item 6). A stage whose extra owner-only authorization is not included in the grant is outside the grant; reaching it is the end of the grant's scope, not an approval gate invented mid-run.

**Grant contents (each named, hashed and displayed like Q-GRANT-1):** grant id; the Approved Role Set (§2B); stages and their acceptance conditions (table below); the limits in the second table (owner-set, G-30); the escalation list (§15.3); the owner-participation manifest (§15.4); expiry and revocation; approval semantics (only predefined options; nothing in the approving turn widens the payload).

| Stage | Acceptance conditions (all must hold; evidence = the cases named in §13) | Evidence checkpoint (written, no acknowledgment requested) | On pass | Authority beyond the grant needed |
|---|---|---|---|---|
| B1 | T01–T05, T07–T10, T23, T24, T27–T37 as scoped for B1 pass (T28, T30 with fault injection) | Result file per case with command, output and hash | B2 starts automatically | none |
| B2 | T06, T11, T12, T14, T16, T20–T22, T26, T32–T35, T37 pass | same | B3 starts if its profile authorization is part of the grant; otherwise B4 starts (record-and-detect posture; no gate reliance) | B3 only: the profile gate-installation authorization |
| B3 | T13, T15, T17–T19, T25 pass; Q1 supported and Q1b-b/Q1b-c demonstrated (else stage not satisfied as specified) | same | B4 starts automatically | profile change (owner-only; mandatory protection not bypassed) |
| B4 | Fixture replay of a scripted job accepted; interruption replay reconciled; no live workers | replay report | B5 starts only if the pilot and stance contract are part of the grant | none |
| B5 | Live pilot with staged interruption: §8 live-pilot acceptance; intervention log complete | intervention log + acceptance report | grant ends; report | pilot selection, stance contract, owner participation (§15.4) |

| Limit (proposed; owner sets the values with the grant, G-30) | Applies to | Proposal |
|---|---|---|
| Fix-retest cycles per failing acceptance case | in-scope repair of the builder's own change | at most N (owner-set; suggestion 3) |
| Retries of a transient tool or environment failure | one action | at most M (owner-set; suggestion 2), no changed settings |
| Time cap per stage | whole stage | owner-set (no duration is claimed here); exceeding it pauses that branch |
| Review-fix-retest | one scoped review per stage of that stage's own changes; findings that are required for acceptance or safe execution are fixed in scope and retested within the cycle limit | findings that are independent improvements or future possibilities are captured, not fixed |
| Scope | paths and tools of the Approved Role Set only | anything else is a scope change (§15.3) |

If a stage fails after its limits: that branch pauses, evidence is preserved, and any stage that depends on it does not start; stages that do not depend on it continue (§15.2). Nothing in this table applies to the qualification grant's probes.

## 14. Remaining owner-only actions

Decided by the owner in turn 3 and unchanged in Rev 5 (recorded as proposals in the register until the owner places them): approach C provisional preferred design; mission authority and layout (brief authoritative, NORTHSTAR a derived copy, CURRENT-STATE the only roadmap and parking lot); E1 + P-SUPERVISED proposed, no pilot authorized.

1. Adopt `01-PROJECT-BRIEF.md` and the derived `NORTHSTAR.md`: both files are present in the folder (the owner reports having placed the Rev 4 documents there; owner-reported, not independently verified); the brief's Status line reads pending adoption, NORTHSTAR reads `status: active`; no owner turn on record identifies the adopted text. Resolution is owner-only; nothing is edited to match (SOURCE-MAP).
2. Place `CURRENT-STATE.md`, `SOURCE-MAP.md` and the decision entries.
3. Issue an R1 build grant (separate from planning authority); none exists.
4. Authorize the profile change that installs the gate under rule C1 (touches `F:\Claude-Profile` and `~\.claude\hooks`, outside Workforce).
5. Decide the proposed qualification grant (QUALIFICATION-PROPOSAL.md; NOT IN FORCE; one grant, Q1–Q8; payload Rev 5.3, prefix `fee261ebe464`; the Rev 5.2 prefix `2938d272cac6` and the Rev 5.1 prefix `154c828f75ca` are SUPERSEDED). Physical participation (opening the Q1 session and up to five Q1b sessions) is listed in payload section 11. Confirm or change the storage selection (S-DB provisional). Decide where the independent head/checkpoint is retained (G-28); proposed: an owner-authored checkpoint message in the owner-facing chat.
6. Select and authorize a pilot job and its stance contract; none is authorized.
7. Before automatic recovery or unattended work: revisit enforcement and require a qualified protection boundary.
8. Be the doorbell and stop/close the builder in the staged interruption (or delegate that explicitly); give owner minutes or leave `unknown`.
9. Any later scheduler, messaging, watchdog contact/recovery: separate grants.

## 15. Continuous progression (proposed operating plan for future grants; **NOT IN FORCE**)

**15.1 Applicability.** Applies only to a future grant that explicitly authorizes several named stages (for example §13A), and only to the extent that grant says so. It is not retroactive: Q-GRANT-1 contains no repair, retry or time-extension authority, and its failed-probe rule is payload 8.1. Nothing here edits a permanent governing instruction (§15.6).

**15.2 Rules.**
1. A stage advances automatically when its acceptance conditions pass and authority remains valid (grant in force, not expired or revoked, scope unchanged).
2. A stage boundary is an evidence checkpoint, not an approval gate, unless the grant or a mandatory governing or tool rule requires approval there.
3. No owner approval is invented for routine implementation, required tests, scoped review, documentation, or authorized in-scope repair. Any proposed additional approval names the exact authority, risk or governing requirement behind it; one that cannot is not requested.
4. A proposed implementation grant includes bounded repair, retry and time limits and review-fix-retest authority (§13A). These are not read into the qualification grant.
5. When a branch is blocked, only dependent work pauses. Independent authorized work continues unless the problem compromises the whole safety envelope (defined in each grant; in Q-GRANT-1 it is payload 8.1).
6. Optional improvements are captured in CURRENT-STATE.md (the only parking lot) without expanding active scope. Progress is recorded without asking for acknowledgment.
7. Nonurgent owner decisions are batched into one consolidated closeout. Cancellation, revocation and urgent safety issues take effect immediately and are never batched.
8. Mandatory platform and tool approvals (for example a project-hook trust prompt or a tool permission prompt) are respected and never bypassed; the affected action waits and independent work continues.

**15.3 Escalation list (the only reasons to stop and ask).** (1) Authority missing, expired or revoked. (2) A scope change or a change to governing policy. (3) A consequential security, data or destructive risk. (4) An acceptance condition that cannot be met within the grant. (5) An owner-only action. (6) A mandatory platform or tool approval. Anything else is decided inside the grant, with the call and its runner-up named in the report. Owner questions that do arise are batched to at most three, each with a recommended choice (the governing planning documents, read in Rev 5.3, state the same rule).

**15.4 Owner-participation manifest, identified before execution.** Each grant lists, before any action, every expected owner action in two classes: (a) *physical participation* that creates no authority (opening a session, giving a scripted instruction, answering a mandatory platform prompt, being the doorbell and stopping the builder in a staged interruption); (b) *decisions needing new authority* (a disposition after a failure, a scope or policy change, a profile change, a pilot selection). Class (a) is scheduled with the grant; class (b) occurs only under a §15.3 condition and is batched (§15.2 rule 7). The qualification manifest is payload section 11.

**15.5 Evidence.** Each checkpoint records what was run, the observed result and the date or hash that makes it current; evidence older than the last change is re-run or marked not re-verified.

**15.6 Governing instructions.** If a permanent governing instruction must change to allow this, the change is prepared as a draft only (`PERMANENT-INSTRUCTIONS-AMENDMENT.draft.md`), not applied, and decided by the owner. The three governing documents named by the package (PROJECT-INSTRUCTIONS.md, 03-PLANNING-METHOD.md, PROJECT-CHECKPOINT-PROCEDURE.md) were read for Rev 5.3 (SOURCE-MAP records source, hashes and the provenance caveat). Compatibility checked: stage boundaries are evidence checkpoints, not approval gates, when the grant covers the stages; independent work continues around blockers; escalation is limited to the listed items; owner decisions are batched to at most three, each with a recommended choice (§15.3); the checkpoint rewrites CURRENT-STATE and never edits the brief or permanent rules without owner approval. Not claimed: that the worktree copies are the planning project's current text, or full compatibility beyond these points (G-01).
