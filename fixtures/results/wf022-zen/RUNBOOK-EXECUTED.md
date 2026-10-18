# WF-022 pilot rehearsal: executed runbook (FIRST-RELEASE-SPEC §12 candidate (b))

**Rehearsal, not a pilot.**
- Executed on Zen, 2026-10-18, by the agent in session "Workforce Dev", playing every role.
- Every **OWNER TURN** below was **SIMULATED** by the agent; in a pilot the owner performs it.
- Scratch ledger only (`%LOCALAPPDATA%\wf022\ledger\wf.db`). Nothing installed, no gate, no real ledger, no demo-002.
- Tree: claude-workforce `2dedc9424abae7930bd82a75c45c235600f0ebcd`. Windows 10.0.26300.9711, PowerShell 5.1.26100.9711, CPython 3.14.4 64-bit (pinned build).

## 0. Setup (worker, unelevated)

1. Build the disposable scope: `python -I -B fixtures\results\wf022-zen\make_scope.py <repo> %LOCALAPPDATA%\wf022`. It refuses if the root exists. See ACCEPTANCE-LIST-DRAFT.md for the planted inconsistencies I1-I5.
2. Boundary rule: `wf` takes the boundary root as the parent of the ledger folder when that folder is named `ledger`. So the ledger must be `<root>\ledger\wf.db`, and contract paths are relative to `<root>`.
3. **Pass `--db <root>\ledger\wf.db` on EVERY command.** The default `--db` is the repository's real ledger (`wf.py --help`: "default …\ledger\ledger.db"; finding WF022-F2).
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

Every command runs as `python -I -B tools\wf\wf.py --db <root>\ledger\wf.db --role <role> --session <session> --json <command>`; `<root>` = `C:\Users\<user>\AppData\Local\wf022`. Exit codes, result fields and wall times are copied from `<root>\flow-log.jsonl` by a renderer, not transcribed by hand.

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
