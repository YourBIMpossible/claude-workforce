# WF-022 pilot rehearsal: executed runbook (FIRST-RELEASE-SPEC §12 candidate (b))

**Rehearsal, not a pilot.**
- Executed on Zen, 2026-10-18, by the agent in session "Workforce Dev", playing every role.
- Every **OWNER TURN** below was **SIMULATED** by the agent; in a pilot the owner performs it.
- Scratch ledger only (`%LOCALAPPDATA%\wf022\ledger\wf.db`). Nothing installed, no gate, no real ledger, no demo-002.
- Tree: claude-workforce `2dedc9424abae7930bd82a75c45c235600f0ebcd`. Windows 10.0.26300.9711, PowerShell 5.1.26100.9711, CPython 3.14.4 64-bit (pinned build).

## 0. Setup (worker, unelevated)

1. Build the disposable scope: `python -I -B fixtures\results\wf022-zen\make_scope.py <repo> %LOCALAPPDATA%\wf022`. It refuses if the root exists. See ACCEPTANCE-LIST-DRAFT.md for the planted inconsistencies I1-I5.
2. Boundary rule: `wf` takes the boundary root as the parent of the ledger folder when that folder is named `ledger`. So the ledger must be `<root>\ledger\wf.db`, and contract paths are relative to `<root>`.
3. **Pass `--db <root>\ledger\wf.db` on EVERY command.** The default `--db` is the repository's real ledger (`wf.py --help`: "default …\ledger\ledger.db"; finding WF022-F2). (True at base 2dedc94. After FIX1 I3, `wf` refuses any command without `--db` or `WF_DB`: exit 2, `rejected(usage)`.)
4. Driver used for this run: `python -I -B fixtures\results\wf022-zen\run_flow.py <repo> <root> wf022-docfix`. It runs each call as its own `python -I -B tools\wf\wf.py --db … --role … --session … --json …` process and logs it to `<root>\flow-log.jsonl`.

Contract (`<root>\build\contract.json`), essentials:
- `write_scope ["scope/**"]`; `read_scope ["pristine/**"]`; `exclusions ["pristine/**"]`;
- `acceptance_criteria` AC1-AC7;
- `allowed_tools {builder: [Write, Edit], reviewer: [Read]}`.
- **`read_scope` means "must stay unchanged" (P2)**, so a write-scope path must never be listed in it (attempt 3).

Grant (`<root>\build\grant.json`): `G-docfix`, `actions [write]`, `resources [scope/**]`, and an ARS with these roles:
- coordinator: Read, bind, `scope/**`;
- builder: Write and Edit, write, `scope/**`;
- reviewer: Read, review, `scope/**` and `pristine/**`;
- replacement allowed, max 1.

## 1. Executed flow (attempt 4, the one that completed)

Every command runs as `python -I -B tools\wf\wf.py --db <root>\ledger\wf.db --role <role> --session <session> --json <command>`; `<root>` = `C:\Users\<user>\AppData\Local\wf022`. Exit codes, result fields and wall times are copied from `<root>\flow-log.jsonl` by `render_table.py` (in this folder), not transcribed by hand.

| # | Phase | Role / session | Command | Exit | Result | Seconds | Owner turn |
|---|---|---|---|---|---|---|---|
| 1 | create | coordinator / C-wf022 | `job new wf022-docfix --contract <root>\build\contract.json` | 0 | ok=True, outcome=COMMITTED, seq=1 | 0.319 | **OWNER TURN**: contract: the owner's request and acceptance list (owner_request.quote/source_turn) |
| 2 | grant | coordinator / C-wf022 | `grant propose wf022-docfix --grant <root>\build\grant.json` | 0 | ok=True, outcome=COMMITTED, seq=2, block_hash=e464f9251f14... | 0.301 |  |
| 3 | grant | coordinator / C-wf022 | `grant activate wf022-docfix --owner-hash e464f925 --quote approve G-docfix e464f925 --turn "<owner turn text>"` | 0 | ok=True, outcome=COMMITTED, seq=3, block_hash=e464f9251f14... | 0.266 | **OWNER TURN**: the owner reads the Grant Confirmation Block and returns its hash prefix + quote (SIMULATED) |
| 4 | bind | coordinator / C-wf022 | `bind wf022-docfix --bind-role coordinator --bind-session C-wf022 --bind-generation 1 --path scope/**` | 0 | ok=True, outcome=COMMITTED, seq=4 | 0.278 |  |
| 5 | bind | coordinator / C-wf022 | `bind wf022-docfix --bind-role builder --bind-session B-wf022 --bind-generation 1 --tool Write --tool Edit --path scope/**` | 0 | ok=True, outcome=COMMITTED, seq=5 | 0.278 |  |
| 6 | status | coordinator / C-wf022 | `status wf022-docfix` | 0 | ok=True | 0.224 |  |
| 7 | repair | builder / B-wf022 | `append wf022-docfix --type intent --content {"path": "scope/README.md", "sha_before": "ca27a9cc6db49f1...}` | 0 | ok=True, outcome=COMMITTED, seq=6 | 0.267 |  |
| 8 | repair | builder / B-wf022 | `append wf022-docfix --type receipt --content {"path": "scope/README.md", "sha_before": "ca27a9cc6db49f1...}` | 0 | ok=True, outcome=COMMITTED, seq=7 | 0.272 |  |
| 9 | repair | builder / B-wf022 | `append wf022-docfix --type intent --content {"path": "scope/README.md", "sha_before": "ec94812d0b213db...}` | 0 | ok=True, outcome=COMMITTED, seq=8 | 0.262 |  |
| 10 | repair | builder / B-wf022 | `append wf022-docfix --type receipt --content {"path": "scope/README.md", "sha_before": "ec94812d0b213db...}` | 0 | ok=True, outcome=COMMITTED, seq=9 | 0.274 |  |
| 11 | repair | builder / B-wf022 | `append wf022-docfix --type intent --content {"path": "scope/PROTOCOL.md", "sha_before": "589ce65564f5f...}` | 0 | ok=True, outcome=COMMITTED, seq=10 | 0.28 |  |
| 12 | repair | builder / B-wf022 | `append wf022-docfix --type receipt --content {"path": "scope/PROTOCOL.md", "sha_before": "589ce65564f5f...}` | 0 | ok=True, outcome=COMMITTED, seq=11 | 0.266 |  |
| 13 | repair | builder / B-wf022 | `append wf022-docfix --type intent --content {"path": "scope/RESTORE-RUNBOOK.md", "sha_before": "e4b168...}` | 0 | ok=True, outcome=COMMITTED, seq=12 | 0.297 |  |
| 14 | repair | builder / B-wf022 | `append wf022-docfix --type receipt --content {"path": "scope/RESTORE-RUNBOOK.md", "sha_before": "e4b168...}` | 0 | ok=True, outcome=COMMITTED, seq=13 | 0.277 |  |
| 15 | repair | builder / B-wf022 | `append wf022-docfix --type intent --content {"path": "scope/PROTOCOL.md", "sha_before": "57628fad2422d...}` | 0 | ok=True, outcome=COMMITTED, seq=14 | 0.261 |  |
| 16 | repair | builder / B-wf022 | `append wf022-docfix --type receipt --content {"path": "scope/PROTOCOL.md", "sha_before": "57628fad2422d...}` | 0 | ok=True, outcome=COMMITTED, seq=15 | 0.272 |  |
| 17 | deliver | builder / B-wf022 | `append wf022-docfix --type delivered --content {"claims": [{"claim": "I1-I5 restored; scope equals pristi...}` | 0 | ok=True, outcome=COMMITTED, seq=16 | 0.278 |  |
| 18 | bind | coordinator / C-wf022 | `bind wf022-docfix --bind-role reviewer --bind-session R-wf022 --bind-generation 1 --tool Read --path scope/** --path pristine/**` | 0 | ok=True, outcome=COMMITTED, seq=17 | 0.27 |  |
| 19 | review | reviewer / R-wf022 | `check-acceptance.ps1` | 0 | AC1=True, AC2=True, AC3=True, AC4=True, AC5=True, AC6=True |  |  |
| 20 | accept | reviewer / R-wf022 | `accept wf022-docfix --results <root>\build\results.json` | 0 | ok=True, outcome=COMMITTED, decision=accepted, seq=18 | 0.344 |  |
| 21 | verify | coordinator / C-wf022 | `verify wf022-docfix` | 0 | ok=True, clean=True | 0.22 |  |
| 22 | checkpoint | coordinator / C-wf022 | `checkpoint wf022-docfix` | 0 | ok=True, seq=18 | 0.222 | **OWNER TURN**: copy the head (seq, self) into an owner turn as the retained checkpoint (spec 3C) |
| 23 | close | coordinator / C-wf022 | `close wf022-docfix --intervention none (rehearsal: no owner intervention during execution)` | 0 | ok=True, outcome=COMMITTED, seq=19 | 0.29 | **OWNER TURN**: intervention declaration: the owner states minutes or 'none' (T21); SIMULATED as none |
| 24 | export | coordinator / C-wf022 | `export wf022-docfix` | 0 | ok=True, code=ok | 0.261 |  |
| 25 | verify | coordinator / C-wf022 | `verify wf022-docfix` | 0 | ok=True, clean=True | 0.217 |  |
| 26 | digest | coordinator / C-wf022 | `digest wf022-docfix` | 0 | ok=True | 0.259 | **OWNER TURN**: the owner reads the digest (Done / Active / Needs-owner) |

**Outcome:**
- `accept` decision `accepted` (seq 18); `close` COMMITTED (seq 19); `verify` `clean: true` before and after export.
- `digest`: `closed: true`, `needs_owner: []`, counts contract 1 · grant 2 · bind 3 · intent 5 · receipt 5 · delivered 1 · accept 1 · close 1.
- Head `seq 19, self 4119ab582779957dd21b50c40cc647cca38281f8772733228618c0545603a9c0`.
- Sum of the 25 `wf` wall times: 6.75 s; the acceptance script time was not recorded. Owner time is in OWNER-STEPS.md, as `unknown`.

**Ordering note:** the retained checkpoint was taken at seq 18, before `close`. The owner's retained checkpoint should be the **final** digest head (seq 19), copied after `close`.

## 2. Owner turns in this flow (all SIMULATED here)

| # | When | What the owner does | Recorded where |
|---|---|---|---|
| OT1 | before `job new` | Approves the job: request quote, acceptance list, scope (§12: owner-only choice and agreed acceptance list) | contract `owner_request.quote` / `source_turn` |
| OT2 | after `grant propose` | Reads the Grant Confirmation Block (`<root>\build\grant-block.txt`) and replies with its hash prefix (≥ 8 hex) and an approving quote | `grant activate --owner-hash --quote --turn` |
| OT3 | after `accept` | Copies the final head (seq, self) into an owner turn as the retained checkpoint (§3C) | owner's own record (outside the ledger) |
| OT4 | at `close` | Declares interventions: minutes or `none`; minutes may be `unknown`, never estimated by an agent (T21) | `close --intervention` |
| OT5 | at the end | Reads the digest (Done / Active / Needs-owner) | `digest` output |

## 3. Refusals met on the way (attempts 1-3, preserved as evidence)

| Attempt | Root (preserved) | Refused or rejected at | Exact message | Cause | Change for the next attempt |
|---|---|---|---|---|---|
| 1 | `%LOCALAPPDATA%\wf022-attempt1-bind-refused` | `bind … --bind-role coordinator` (no `--path`), exit 1 | `rejected(ars): path 'pristine/**' is not inside the ARS paths ['scope/**']` | Without `--path`, coordinator and reviewer declare the contract's `read_scope` and the builder its `write_scope` (`commands._scope_defaults`, commands.py:933-941); the ARS coordinator role covers `scope/**` only | Declare `--path` explicitly for every role (finding WF022-F3) |
| 2 | `%LOCALAPPDATA%\wf022-attempt2-reviewer-before-delivery` | `bind … --bind-role reviewer` before `delivered`, exit 1 | `rejected(reviewer-precondition): a reviewer is bound only after delivery` | Spec §2B: a reviewer is assigned once the job is `delivered` | Bind the reviewer after `append --type delivered` (finding WF022-F4) |
| 3 | `%LOCALAPPDATA%\wf022-attempt3-readscope-overlap-rejected` | `accept`, exit 0 but `decision: rejected`; `close` then refused: `close refused: acceptance is 'rejected' and job not abandoned` | accept classes: `scope/*.md` → `P2`, `read-only path changed (P2)`, `violation` | Contract listed `scope/**` in both `read_scope` and `write_scope`; spec §4.4 (line 213) defines P1 as "write scope … minus P2/P3", so read-only wins | Contract `read_scope ["pristine/**"]` only (finding WF022-F5). The rejected job was closed by the abandon path (below) |

**Abandon path, rehearsed on attempt 3's job:**
- `wf job abandon wf022-docfix --reason <text>` → exit 0, COMMITTED.
- `wf close wf022-docfix --intervention <text>` → exit 0, COMMITTED (the abandoned-close rule).
- `wf verify` → `clean: true`; `wf status` → `execution: abandoned, acceptance: rejected`.
- Wall times were not recorded for these four calls.

Lesson for a pilot: a contract mistake found at `accept` cannot be repaired in place (the contract row is immutable). The owner abandons, closes and starts a new job, with a new contract and a new grant approval (OT1/OT2 again).

## Step 4: interruption (kill during an append) and recovery

**Setup.** A second job, `wf022-int`, on a fresh scope made by `make_scope.py`. The root is `<repo>\fixtures\tmp\wf022-int`, because the test-only fault seam is honoured only there. The contract and grant have the same shape as attempt 4 (`run_interruption.py`).

**Interruption.** I1 was repaired normally. For I2:
- the builder wrote the intent (seq 8) and edited `scope/README.md` on disk;
- the receipt append then ran with `WF_TEST_FAULTS=delay-precommit:8000`, which holds the append for 8 s after the pending-write marker is written and before COMMIT;
- `taskkill /F /T` killed it after 2.5 s.

The seam makes "kill during an append" deterministic. A real kill can land anywhere: before the marker, inside COMMIT, or after it.

**Recovery.** Each command was decided from the previous output and run one logged call at a time with `wfcall.py`. `finish_interruption.py` then completed the job.

Every call follows (`<root>` = the boundary root). Owner turns are SIMULATED, as in step 3.

| # | Phase | Role / session | Command | Exit | Result | Seconds | Owner turn |
|---|---|---|---|---|---|---|---|
| 1 | create | coordinator / C-wf022 | `job new wf022-int --contract <root>\build\contract.json` | 0 | ok=True, outcome=COMMITTED, seq=1 | 0.343 | **OWNER TURN**: contract (OT1) |
| 2 | grant | coordinator / C-wf022 | `grant propose wf022-int --grant <root>\build\grant.json` | 0 | ok=True, outcome=COMMITTED, seq=2, block_hash=75a2cd896db5... | 0.32 |  |
| 3 | grant | coordinator / C-wf022 | `grant activate wf022-int --owner-hash 75a2cd89 --quote approve G-docfix 75a2cd89 --turn "<owner turn text>"` | 0 | ok=True, outcome=COMMITTED, seq=3, block_hash=75a2cd896db5... | 0.334 | **OWNER TURN**: grant block hash + quote (OT2) |
| 4 | bind | coordinator / C-wf022 | `bind wf022-int --bind-role coordinator --bind-session C-wf022 --bind-generation 1 --path scope/**` | 0 | ok=True, outcome=COMMITTED, seq=4 | 0.32 |  |
| 5 | bind | coordinator / C-wf022 | `bind wf022-int --bind-role builder --bind-session B-wf022 --bind-generation 1 --tool Write --tool Edit --path scope/**` | 0 | ok=True, outcome=COMMITTED, seq=5 | 0.289 |  |
| 6 | repair | builder / B-wf022 | `append wf022-int --type intent --content {"path": "scope/README.md", "sha_before": "ca27a9cc6db49f1...}` | 0 | ok=True, outcome=COMMITTED, seq=6 | 0.314 |  |
| 7 | repair | builder / B-wf022 | `append wf022-int --type receipt --content {"path": "scope/README.md", "sha_before": "ca27a9cc6db49f1...}` | 0 | ok=True, outcome=COMMITTED, seq=7 | 0.309 |  |
| 8 | repair | builder / B-wf022 | `append wf022-int --type intent --content {"path": "scope/README.md", "sha_before": "ec94812d0b213db...}` | 0 | ok=True, outcome=COMMITTED, seq=8 | 0.295 |  |
| 9 | kill | builder / B-wf022 | `append wf022-int --type receipt --content {"path": "scope/README.md", "sha_before": "ec94812d0b213db...}` | 1 | stdout=, taskkill_rc=0, taskkill=SUCCESS: The process with PID 64236 (child process of PID 63848) has been terminated. | 3.027 |  |
| 10 | recover-1 | coordinator / C-wf022 | `status wf022-int` | 0 | ok=True, pending_markers=[1 marker: seq 9, attempt 31f895d8…, state committing], axes: execution active + uncertain-write | 0.274 |  |
| 11 | recover-1 | builder / B-wf022 | `append wf022-int --type intent --content {"path":"scope/PROTOCOL.md","sha_before":"x","note":"probe"}` | 1 | ok=False, code=uncertain-write-pending, seq=9, error=uncertain-write pending: wf022-int/9/31f895d8e36bba719b48cc70c831b292 | 0.235 |  |
| 12 | recover-1 | coordinator / C-wf022 | `verify wf022-int` | 0 | ok=True, clean=False | 0.213 |  |
| 13 | recover-2 | coordinator / C-wf022 | `resolve wf022-int 9 --attempt 31f895d8e36bba719b48cc70c831b292` | 0 | ok=True, outcome=NOT-COMMITTED, seq=9 | 0.213 | **OWNER TURN**: owner-present: decide the uncertain write (spec 3B / README 4.4a) |
| 14 | recover-2 | coordinator / C-wf022 | `status wf022-int` | 0 | ok=True, pending_markers=[], axes={'acceptance': 'pending', 'authority': 'in-force', 'execution': 'active', 'review': 'none'} | 0.221 |  |
| 15 | recover-2 | coordinator / C-wf022 | `verify wf022-int` | 0 | ok=True, clean=True | 0.225 |  |
| 16 | recover-3 | coordinator / C-wf022 | `recover begin wf022-int --reason builder append killed mid-write (receipt for I2 not committed) --turn "<owner turn text>"` | 0 | ok=True, outcome=COMMITTED, seq=9, exceptions=[] | 0.282 | **OWNER TURN**: recover begin needs an owner turn naming the job (spec 3B) |
| 17 | recover-3 | coordinator / C-wf022 | `status wf022-int` | 0 | ok=True, pending_markers=[], axes={'acceptance': 'pending', 'authority': 'in-force', 'execution': 'recovering', 'review': 'none'} | 0.223 |  |
| 18 | recover-4 | coordinator / C-wf022 | `reconcile wf022-int` | 1 | ok=False, code=rejected, error=rejected(recovery-open): recovery is open since seq 9; only `wf recover` and read-only com | 0.191 |  |
| 19 | recover-5 | coordinator / C-wf022 | `recover reconcile wf022-int --turn "<owner turn text>"` | 0 | ok=True, seq=None | 0.193 | **OWNER TURN**: owner turn naming the job |
| 20 | recover-5 | coordinator / C-wf022 | `recover close wf022-int --turn "<owner turn text>"` | 0 | ok=True, outcome=COMMITTED, seq=10 | 0.287 | **OWNER TURN**: owner turn naming the job |
| 21 | recover-5 | coordinator / C-wf022 | `status wf022-int` | 0 | ok=True, pending_markers=[], axes={'acceptance': 'pending', 'authority': 'in-force', 'execution': 'active', 'review': 'none'} | 0.243 |  |
| 22 | recover-6 | coordinator / C-wf022 | `reconcile wf022-int` | 0 | ok=True, outcome=COMMITTED, seq=11, exceptions=[{'class': 'attempt-without-completion', 'id': 'X1', 'path': 'scope/README.md'}] | 0.281 |  |
| 23 | recover-7 | builder / B-wf022 | `append wf022-int --type intent --content {"path":"scope/README.md","sha_before":"x","note":"before ...}` | 1 | ok=False, code=rejected, error=rejected(unreconciled): role builder has an unacknowledged reconcile report at seq 11; onl | 0.267 |  |
| 24 | recover-7 | builder / B-wf022 | `ack-reconcile wf022-int --report 11 --exceptions ["X1"] --note X1: the I2 edit is on disk (scope/README.md sha 7a73bdaa... = pristine); receipt was lost in the killed append` | 0 | ok=True, outcome=COMMITTED, seq=12 | 0.266 |  |
| 25 | recover-7 | builder / B-wf022 | `append wf022-int --type receipt --content {"path":"scope/README.md","sha_before":"ec94812d0b213db076...}` | 2 | ok=False, code=usage, error=not a file and not JSON: '{"path":"scope/README.md","sha_before":"ec94812d0b213db076a6456f | 0.217 |  |
| 26 | recover-7 | coordinator / C-wf022 | `status wf022-int` | 0 | ok=True, pending_markers=[], axes={'acceptance': 'pending', 'authority': 'in-force', 'execution': 'active', 'review': 'none'} | 0.216 |  |
| 27 | recover-8 | builder / B-wf022 | `append wf022-int --type receipt --content {"path": "scope/README.md", "sha_before": "ec94812d0b213db...}` | 0 | ok=True, outcome=COMMITTED, seq=13 | 0.274 |  |
| 28 | finish | builder / B-wf022 | `append wf022-int --type intent --content {"path": "scope/PROTOCOL.md", "sha_before": "589ce65564f5f...}` | 0 | ok=True, outcome=COMMITTED, seq=14 | 0.25 |  |
| 29 | finish | builder / B-wf022 | `append wf022-int --type receipt --content {"path": "scope/PROTOCOL.md", "sha_before": "589ce65564f5f...}` | 0 | ok=True, outcome=COMMITTED, seq=15 | 0.264 |  |
| 30 | finish | builder / B-wf022 | `append wf022-int --type intent --content {"path": "scope/RESTORE-RUNBOOK.md", "sha_before": "e4b168...}` | 0 | ok=True, outcome=COMMITTED, seq=16 | 0.291 |  |
| 31 | finish | builder / B-wf022 | `append wf022-int --type receipt --content {"path": "scope/RESTORE-RUNBOOK.md", "sha_before": "e4b168...}` | 0 | ok=True, outcome=COMMITTED, seq=17 | 0.249 |  |
| 32 | finish | builder / B-wf022 | `append wf022-int --type intent --content {"path": "scope/PROTOCOL.md", "sha_before": "57628fad2422d...}` | 0 | ok=True, outcome=COMMITTED, seq=18 | 0.288 |  |
| 33 | finish | builder / B-wf022 | `append wf022-int --type receipt --content {"path": "scope/PROTOCOL.md", "sha_before": "57628fad2422d...}` | 0 | ok=True, outcome=COMMITTED, seq=19 | 0.262 |  |
| 34 | finish | builder / B-wf022 | `append wf022-int --type delivered --content {"claims": [{"claim": "I1-I5 restored after an interrupted...}` | 0 | ok=True, outcome=COMMITTED, seq=20 | 0.311 |  |
| 35 | finish | coordinator / C-wf022 | `bind wf022-int --bind-role reviewer --bind-session R-wf022 --bind-generation 1 --tool Read --path scope/** --path pristine/**` | 0 | ok=True, outcome=COMMITTED, seq=21 | 0.248 |  |
| 36 | finish | reviewer / R-wf022 | `accept wf022-int --results <root>\build\results.json` | 0 | ok=True, outcome=COMMITTED, decision=accepted, seq=22 | 0.324 |  |
| 37 | finish | coordinator / C-wf022 | `verify wf022-int` | 0 | ok=True, clean=True | 0.238 |  |
| 38 | finish | coordinator / C-wf022 | `close wf022-int --intervention owner-present recovery after a killed append: resolve, recover begin/reconcile/close (rehearsal, SIMULATED)` | 0 | ok=True, outcome=COMMITTED, seq=23 | 0.307 | **OWNER TURN**: intervention declaration: the recovery was an owner-present intervention (T21); minutes unknown |
| 39 | finish | coordinator / C-wf022 | `export wf022-int` | 0 | ok=True, code=ok | 0.251 |  |
| 40 | finish | coordinator / C-wf022 | `verify wf022-int` | 0 | ok=True, clean=True | 0.234 |  |
| 41 | finish | coordinator / C-wf022 | `digest wf022-int` | 0 | ok=True | 0.22 | **OWNER TURN**: the owner reads the digest |

**What the record showed after the kill:**
- The ledger head stayed at seq 8. No partial event was written.
- A pending-write marker was left at `ledger/pending/wf022-int/9-31f895d8….json`, in state `committing`.
- `status` reported `execution: active+uncertain-write`.
- Every further append was refused with `uncertain-write-pending` (row 11), and `verify` returned `clean: false`.
- The I2 edit was on disk (`scope/README.md` = pristine `7a73bdaa…`) but had no receipt.
- `resolve wf022-int 9 --attempt …` decided the write was NOT-COMMITTED: the marker was cleared and `verify` came back clean. Seq 9 had never been committed, so the next event (`recovery-opened`) took seq 9. The chain has no gap.
- `recover begin` opened the recovery (seq 9) with an **empty** exception list.
- While the recovery was open, ordinary `reconcile` was refused with `rejected(recovery-open)` (row 18).
- `recover reconcile` had nothing to acknowledge (`seq: null`), and `recover close` closed the recovery (seq 10).
- The ordinary `reconcile` that followed found `X1 attempt-without-completion scope/README.md` (seq 11): the intent at seq 8 had no receipt.
- The builder was refused with `rejected(unreconciled)` until it ran `ack-reconcile --report 11 --exceptions ["X1"]` (seq 12).
- The builder then re-recorded the I2 receipt against the file as inspected (seq 13).
- Row 25 is my own malformed `--content` (a truncated sha, so neither a file nor JSON). It was refused with `usage` and wrote nothing.

**What needed a human decision.** In a pilot, these points need the owner present:
1. **Whether the killed write happened** (`resolve`, row 13). The marker alone cannot say. The owner, or the coordinator under an owner turn, picks the attempt. `resolve` reads the database and reports the truth (here NOT-COMMITTED); it does not guess.
2. **Opening, reconciling and closing the recovery** (rows 16, 19, 20). Each `wf recover` command needs an owner turn that names the job (spec §3B).
3. **Acknowledging X1** (row 24). Someone has to look at the file and say what the lost receipt would have said. Here the edit was on disk and matched pristine, so the receipt was re-recorded. Had the edit been absent, the right move would have been a new intent → edit → receipt instead.
4. **The intervention declaration at close** (row 38). This job is closed with an owner-present intervention, not "none", and the minutes are `unknown`.

No step needed a product-code change or a force option, and none exists.

**End state:**
- `accept` returned `decision: accepted` (seq 22) and `close` committed (seq 23).
- `export` succeeded, and `verify` was clean after it.
- The digest showed `closed: true` and head `self 2f46b843…3d64`.
- `check-acceptance.ps1` returned AC1–AC6 True.
- `scope/` is byte-identical to `pristine/` (7d10f027…, 7a73bdaa…, 66b3e5c2…).

**Finding from this step, WF022-F7, NEEDS OWNER.** `recover begin` recorded an empty exception list and `recover reconcile` had nothing to acknowledge. Yet the ordinary `reconcile` run straight after `recover close` reported `attempt-without-completion` for the intent whose receipt was lost.
- Spec §3B line 140 counts "events whose follow-up non-transactional action (an export, a receipt file) did not happen" as recoverable exceptions that the recovery lists.
- Two readings fit:
  - (a) the recovery's list covers only ledger/export-level exceptions, and role-level attempt gaps belong to the ordinary reconcile. Then the result is correct, but the README should say so.
  - (b) the recovery should have listed X1.
- The owner picks the reading. Nothing was changed.

## Status after FIX1 (added 2026-10-19 after the independent review WF-040, Lazy)
- Lazy re-ran both flows independently. They reproduce at base 2dedc94 and run unchanged on `fix1/integration` dfd6e85.
- **WF022-F1** (CRLF corpus check): fixed by FIX1 I2 (`.gitattributes`).
- **WF022-F2** (`--db` silently defaulting to the real ledger): fixed by FIX1 I3 (`--db` or `WF_DB` required).
- **WF022-F5** (read/write scope overlap): mitigated by FIX1 I4. `job new` now warns.
- **WF022-F6** (README out of date): fixed by FIX1 I5.
- **WF022-F7** (recover vs reconcile): closed by the coordinator decision of 2026-10-19 01:35Z (reading (a), NOT A DEFECT) and FIX1 I5's README sentence. A matching spec sentence is proposed in WF-042.
- The drivers now find `check-acceptance.ps1` beside themselves and stop loudly if it does not report AC1–AC6 (L40-S1).
