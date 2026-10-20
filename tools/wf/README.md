# wf — Workforce ledger CLI (FB1.4 R1 record-and-verify slice)

`wf` records a job's events in a hash-chained SQLite ledger and verifies them. It **records and verifies**; it does
not gate tools by itself (the gate hook is separate), enforce identity, sandbox anything, or replay jobs; recovery is
the owner-driven `wf recover` procedure below. Python 3 stdlib only; always run with
`python -I`.

The gate requires Claude Code v2.1.295 or later, for `onFailure: block`: the settings snippet that
`wf gate print-install` prints sets it, and on an older version a timed-out or failing gate hook lets the tool call
through (spec §4.2, G-12).

```
python -I F:\Claude-Workforce\tools\wf\wf.py --db PATH [--role R --session S] [--generation N] [--msg-id ID] [--json] [--oplog] <command>
```

`--db` is required: give `--db PATH` or set `WF_DB` (`--db` wins when both are given). There is no default ledger;
without either, every command answers `rejected(usage): --db required` with exit 2 (FIX1 I3: a forgotten `--db` must not
write the real ledger). `--role`/`--session` may come from `WF_ROLE`/`WF_SESSION`. Exit codes: `0` ok, `1`
error/refused/rejected, `2` usage, ambiguous resolve, or fault seam refused, `3` a fixture fault-seam kill, `4`
`internal-error` (an unexpected exception, reported as one result instead of a traceback).

## Commands

| Command | Role | Effect |
|---|---|---|
| `job new <job> --contract FILE` | coordinator | Validates the contract (R07 fields), stores it pinned by SHA-256, writes event 1 (`contract`). |
| `job abandon <job> --reason TEXT` | — | Marks the contract abandoned (bounded UPDATE, no event). A second run changes nothing and is refused `job <job> already abandoned`. |
| `grant propose <job> --grant FILE` | coordinator | Records a `grant` event in state `proposed`; prints the Grant Confirmation Block and its hash. Resources must lie inside the contract write scope. |
| `grant activate <job> --owner-hash H [--quote Q] [--turn T]` | coordinator | Records the grant as `activated` if `H` (≥ 8 hex chars, prefix match) equals the block hash. |
| `append <job> --type intent\|receipt\|blocker\|handoff\|delivered --content FILE\|JSON` | builder (handoff/blocker: any listed in `validate.ROLE_FOR_TYPE`) | Appends one event. |
| `status <job>` | — | Head, contract pin, axes (execution, acceptance, review, authority), pending markers. Read-only. |
| `digest <job>` | — | Owner surface (spec T26), read-only; ledger fields, `head` and `db_size_bytes` come from one read snapshot, pending markers from the marker files read after it: `head` (seq, self, `db_size_bytes`) to copy into an owner turn as the retained checkpoint; `done`; `active` (axes, latest event seq per axis); `needs_owner` (decision queue, each item with `recommendation` and `evidence`); `grants` (each activated grant's verbatim quote, source turn, block hash, proposed and activation seq, current state); `ars`; `bindings`; `evidence` (paths cited by receipts, delivered claims or an `evidence` field, with seq; listed, never opened); plus counts, contract pin, axes and pending markers. No raw event content is relayed. **Limits:** `bindings` is the `wf bind` table when the job has explicit bindings, otherwise the table observed from event headers (`source` says which); role, session and generation are caller-supplied text and the digest does not verify identity. |
| `verify [job]` | — | Schema/constraints, `integrity_check`, chain (prev/self/content hashes, canonical JSON, contract pin), export consistency, pending markers. `clean: true` only when all are empty. Read-only. `--checkpoint SEQ:HASH` compares the chain with an independently retained checkpoint (spec §3C level 3): `match`, `mismatch` or `missing`. `--restored PATH [--job J --checkpoint SEQ:HASH]` checks a restored copy read-only (docs\RESTORE-RUNBOOK.md): checkpoint status `matches`, `differs` or `missing`. Same three outcomes, two vocabularies (FIX2 #42). |
| `export <job>` | — | Regenerates `jobs\<job>\export\` (events.jsonl, head.json, contract.json, manifest.txt) under the write lock: the refresh takes `BEGIN IMMEDIATE` across its snapshot and its file writes, then rolls back (FIX2 #45 (b)), and re-checks it. Ledger-read-only, export-writing: no event, no ledger write, no marker; writes only under the export directory. Unknown job → `refused`; a refresh failure or remaining export problem → exit 1, code `export-error`. |
| `checkpoint <job>` | — | Writes `jobs\<job>\export\checkpoint.json` = (job, seq, self, contract pin). Keep it elsewhere; a later `verify` plus comparison detects a rewrite of anything up to that seq. |
| `accept <job> --results FILE` | reviewer | Records `accept` with decision `accepted` only when every contract criterion is present with `pass: true`, no changed path is outside scope (P4) or excluded (P5), and no unknown criterion is named; otherwise `rejected`. Refused (no event) if the session wrote any builder event (`same-session`) or nothing was delivered. |
| `close <job> --intervention TEXT` | coordinator | Refused unless verify is clean, no marker is pending, and acceptance is `accepted` **or** the job is abandoned (the abandoned-close rule). Writes `close`; the job then rejects further events. |
| `resolve <job> <seq> [--attempt ID] [--disposition TEXT]` | — | Decides an uncertain write (below). |
| `bind <job> --bind-role R --bind-session S [--bind-generation N] [--tool T]... [--path GLOB]...` | coordinator | Records a role assignment checked against the activated grant's ARS (spec 2B); the bound session answers with `append --type ack-assignment`. The `--tool` list is checked against the ARS here but is not enforced per tool call by the gate; restrict a session's tools with a Claude Code permission rule (spec R17). |
| `reconcile <job> [--target-role builder]` | coordinator | Compares in-scope files with the baseline, receipts and intents and appends a `reconcile-report`; the role acknowledges it with `ack-reconcile <job> --report SEQ --exceptions JSON`. |
| `recover begin\|reconcile\|close <job> --turn T` | coordinator (owner turn naming the job) | Opens a recovery (the chain is verified first), acknowledges each recoverable exception it lists, and closes it once all are acknowledged and the chain verifies. Every ordinary mutation refuses while a recovery is open. |
| `recover decide <job> --decision resume\|restore\|abandon --turn T` | coordinator | Records the builder-role decision after a confirmed stop (spec 6.2 step 4). |

**Recovery and reconcile are different paths.** `wf recover` handles chain and export exceptions (spec §3B): a broken
or stale export, an open recovery, the recoverable exceptions `recover begin` lists. `wf reconcile` handles the work
itself (spec §6 / R12): intents without receipts, receipts that do not match the files, attempts without completion.
**Replacing a session** (the replacement path): the coordinator records `append --type stop-confirmed` for the old
session, runs `wf reconcile`, records `wf recover decide --decision resume|restore|abandon`, then binds the new
session with `wf bind ... --bind-generation N+1` (within the grant's ARS replacement rule). The old session's bindings
are then replaced or stopped and the gate denies its writes (T13).

**Job ids at `job new`** (FIX1 I1). A job id names directories (`jobs\<job>\`, `ledger\pending\<job>\`), so besides the
`[A-Za-z0-9][A-Za-z0-9._-]{0,63}` grammar, `job new` refuses `rejected(malformed)` an id that ends in a dot or a space,
contains `..`, is a Windows device name (`CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9`, any case, with or
without an extension), or equals an existing id case-insensitively. This holds on every platform. A second `job new`
with the exact existing id answers `job <job> already exists`.

**Scope overlap warning** (FIX1 I4). When a contract's `read_scope` and `write_scope` can name the same files (one
glob's literal prefix lies inside the other's), `job new` still records the job and adds
`"warnings": ["read_scope overlaps write_scope: <read> ~ <write>, ..."]` to its result.

Event rules (`validate.build_row`): role allowed for the type; contract first; contract pin added to every event;
`msg_id` replay with identical content → `DUPLICATE-IGNORED` (exit 0, no row), with different content → rejected;
generation may not go backwards; one session per (role, generation); a closed job accepts nothing.

**Claimant conflict (FB1.4-A4 §3).** A second session writing for a (role, generation) another session already holds is
not simply rejected: inside the same `BEGIN IMMEDIATE` reservation that detected it, `wf` records a conflict blocker at
`seq = head+1` (type `blocker`, the rejected claimant's role/session/generation, `msg_id`
`claimant-conflict:<attempted msg_id>`, content `{reason, role, generation, holder_session, claimant_session,
attempted_type, attempted_msg_id}` plus the contract pin). The attempted event is never written. The command exits 1
with `rejected(claimant-conflict)`, `conflict_seq` and `conflict_outcome` (`COMMITTED`, `COMMITTED-NO-MARKER`,
`DUPLICATE-IGNORED` for a repeat of the same attempt, `commit-uncertain`, `conflict-pending`, `conflict-unrecorded`).
Effect: the role is **blocked** for every session and every generation (`rejected(role-blocked)`), `accept` is refused
`role-blocked` while any conflict stands (no dependent work), `status`/`digest` show execution `blocked` with
`blocked_roles`, and `close` is refused until `job abandon`. Conflict rows feed neither the session bind nor
`builder_session`. A user-written `blocker` with reason `claimant-conflict`, or any `msg_id` starting with
`claimant-conflict:`, is rejected `reserved-reason`. There is no unblocking in this slice.

## Storage (D1)

`events` is `WITHOUT ROWID`, `PRIMARY KEY (job, seq)`, `UNIQUE (job, prev)`, with triggers that abort any UPDATE or
DELETE. Rollback journal (`journal_mode=DELETE`, WAL refused by verify), `synchronous=FULL`, `busy_timeout=0`; every
wait is an explicit bounded loop in `wfcore\db.py`. Each write refreshes `jobs\<job>\export\` (events.jsonl,
head.json, manifest.txt with three fields per line).

## Bounds and what they do not guarantee

| Bound | Value | Meaning |
|---|---|---|
| `T_ACQ_BOUND` | 5.0 s | Waiting for `BEGIN IMMEDIATE` (reservation), connection setup, and a read's SHARED lock. Exceeded → `lock-timeout`, nothing written. |
| `T_HELD_BOUND` | 2.0 s | Retrying `COMMIT` while readers hold SHARED. Exceeded → give-up sequence → `commit-timeout`, nothing written (proved by the marker protocol). |
| `T_RESOLVE_BOUND` | 10.0 s | Budget for one `resolve` decision under the reservation. Exceeded → `STILL-UNCERTAIN`. |

Not guaranteed: progress under sustained contention (a writer can time out repeatedly; it reports, it never waits
forever); wall-clock timing on a loaded machine; anything about processes that bypass `wf` and open the database
directly.

**Bounded reads.** `status`, `digest`, `verify`, export check and the proposal-block read take the SHARED lock inside
a consistent snapshot (`db.read_snapshot`, BEGIN DEFERRED with a bounded retry, ROLLBACK at exit) instead of failing
instantly on `busy_timeout=0`; export refresh takes the write reservation instead (FIX2 #45 (b)), so it waits for
writers within `T_ACQ_BOUND` and never blocks readers. Trade-off: while a read holds SHARED, a concurrent writer
cannot COMMIT and retries within `T_HELD_BOUND`, so a long `verify` on a large ledger can push concurrent writers into
`commit-timeout`. `verify` keeps its database reads in the snapshot and does export and marker checks after closing.
Export refresh after a committed write never raises: a refresh failure is reported in the result, and the committed
outcome stands. `wf export <job>` regenerates the export on demand (ledger-read-only, export-writing); commit status
and export status stay distinct, and freshness after every commit is not guaranteed by it (open).

## Attempt ids and pending-write markers (§4.4a)

Every write attempt gets a random 128-bit attempt id. Before the first `COMMIT`, under the reservation, `wf` writes
`ledger\pending\<job>\<seq>-<attempt>.json` (atomic replace) with state `committing`, rev 1, the row's `self`
and `prev`. Outcomes:

- **COMMIT succeeds** → the marker is deleted. If that delete fails, the result says `marker_left`; the next guard
  clears it because the row with the same `self` exists.
- **COMMIT stays busy past `T_HELD_BOUND`** → give-up: marker rewritten to `aborting` (rev 2), ROLLBACK, marker
  deleted, `commit-timeout`. If the marker cannot be marked aborting or ROLLBACK raises, the outcome is
  `commit-uncertain`.
- **COMMIT raises a non-busy error** → `commit-uncertain`; the marker stays; the command runs `resolve` once with its
  own attempt id and reports `auto_resolve`.

**Conflict markers (FB1.4-A4 §3).** The marker written for a conflict row carries `conflict` (the row content) and
`row_ts`. For that marker only: busy past `T_HELD_BOUND` → the marker is rewritten to state `conflict-pending`
(rev 2), ROLLBACK, `commit-timeout`, marker **kept** (the guard treats `conflict-pending` like `committing`); marker
unwritable → the conflict row is COMMITted **without a marker** within `T_HELD_BOUND` (`COMMITTED-NO-MARKER`; the row
itself is the durable indication; oplog `marker_write_failed`, `commit_no_marker`); marker unwritable **and** COMMIT
fails → nothing durable, exit 1 `conflict-unrecorded` naming holder and claimant. That last case is the accepted T01
guarantee boundary (A4 §13a): reported, not enforced. Every other event keeps the marker-before-COMMIT rule.

**Guard.** Every write runs the guard under its own `BEGIN IMMEDIATE`, before reading the head. Per marker of that
job: unreadable or malformed → refuse (`uncertain-write-pending`); a row at that seq with the marker's `self` →
clear (committed); state `aborting` with no row → clear; otherwise refuse. Markers are deleted only by exact filename.
A `committing` marker with no row is never assumed aborted: it fails closed until `resolve` decides it.

**Resolve (§4.4b).** Selects the marker by seq (and `--attempt` when several exist: otherwise `AMBIGUOUS`, exit 2).
Reads it (M0), takes the reservation within `T_ACQ_BOUND` (else `RESERVATION-NOT-OBTAINED`, no change), re-reads it
(M1, decision from M1), then: row with matching `self` → `COMMITTED`; a different row → `CONFLICT`; head is seq−1
with the marker's `prev` and `quick_check` ok → `NOT-COMMITTED`; anything else, a chain problem, the budget exceeded,
or the reservation not held → `STILL-UNCERTAIN` (marker kept). The first three delete the marker under the
reservation. `NOT-COMMITTED` on a marker carrying `conflict` first re-inserts the conflict row from the marker and
COMMITs it within `T_RESOLVE_BOUND` (`re_recorded` in the result), then deletes the marker; a conflict marker is
never deleted without its row present (if the re-record cannot COMMIT → `STILL-UNCERTAIN`). `--disposition TEXT` records an operator note on the marker (rev+1) without deciding. Each step is
logged to `<seq>-<attempt>.resolve.log`.

## Grant Confirmation Block

The block is deterministic: job, grant id, actions, resources, exclusions, ARS, delegation, expiry, revocation rule,
the contract SHA-256 from the stored grant row, and the seq at which the grant was proposed. It contains no timestamp
and no row hash, so the owner supplies the hash once and a rerun on a fresh ledger (same contract, same grant, same
position) reproduces it. Consequence: the same hash activates an identical proposal on any fresh ledger. The hash is
a consistency check that the owner saw this exact block, not an authentication of the owner.

## Fault seam (test-only)

`WF_TEST_FAULTS` (fixtures and unit tests only) injects delays, COMMIT errors, guard bypasses, a pre-commit kill and
`marker-unwritable` (the marker write raises OSError). It is honoured only
when the ledger is under `fixtures\tmp\`; for any other path `wf` refuses to run (exit 2, `faults-not-allowed`).
Auto-resolve is skipped for injected COMMIT errors so fixtures can resolve explicitly.

## Identity and limits

- `--role`, `--session` and `--generation` are typed fields checked for consistency. They do not authenticate anyone.
  `same-session` refusal compares typed values only.
- `accept` checks what the results file claims against the contract. It does not run the checks.
- `ledger\pending\<job>` is shared by every ledger in the same directory (`ledger.db` and `demo.db`): two ledgers
  with the same job id there would see each other's markers (fail-closed, but confusing). Keep job ids distinct.
- Connection setup and `BEGIN IMMEDIATE` share one `T_ACQ_BOUND` window (connect wait is counted).
- Case folding follows `os.path.normcase`: Windows folds case; POSIX hosts, including case-insensitive volumes such as
  default macOS APFS, compare case-sensitively. R1 supports Windows and Linux.
- Windows name rules apply on every platform by design (fail closed, portability; FIX2 #6, kept): path resolution
  refuses a path component that is a Windows device name (with or without an extension, for example `aux.c`), contains
  `:`, or ends in a dot or a space, on Linux as on Windows; job ids follow the same rule (FIX1 I1, above). On Linux
  this refuses some names the filesystem would accept (an availability cost, WF-021 F8).
- A claimant conflict blocks a role on typed values; it is a recorded fact, not an authenticated one. The conflict
  record is guaranteed only when at least one durable write (row or marker) succeeds in the attempt; the double
  failure `conflict-unrecorded` is reported and leaves the holder unblocked (A4 §13a, accepted).
- Gate limit for unbound sessions (G-4, documented): a session never bound to a job is denied the protected paths every
  session is denied (`ledger\**`, `jobs\*\export\**`, `runtime\**` and the gate files, rule 1) and targets inside some
  job's write scope or `jobs\<job>\` (`unbound-session`, or the job's state, for example `job-closed`); other in-root
  paths are allowed. Grant `exclusions` are applied by `grant.check` (`grant.py:297`), which only bound sessions reach,
  so an excluded path outside every write scope and `jobs\<job>\` stays writable for unbound sessions: keep protected
  files (for example a pilot's `pristine\**`) under `jobs\<job>\`. A replaced or stop-confirmed session is denied every
  preventable target (D-027).

## Tests

```
python -I -m unittest discover -s F:\Claude-Workforce\tools\wf\tests
python -I F:\Claude-Workforce\fixtures\run_fixture.py all
```

Unit-test logs go to `WF_TEST_LOGDIR` (default `tools\wf\tests\logs`); ledgers to `WF_TEST_TMP` (default
`fixtures\tmp\unittest`, must be under `fixtures\tmp`). Fixture results go to `fixtures\results\<T>.json`.
