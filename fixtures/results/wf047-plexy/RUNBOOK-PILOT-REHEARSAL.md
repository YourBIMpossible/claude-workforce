# WF-047 — Extended dry rehearsal of the slice-6 pilot flow on the FIX1 head (Plexy)

**Rehearsal, not a pilot.**
- Run: 2026-10-19 by the agent in session "Workforce Dev", playing every role.
- Every **OWNER TURN** is **SIMULATED** and marked so in the ledger texts.
- Disposable fixture only:
  - boundary root `<repo>\fixtures\tmp\wf047-<UTC>\` (the only place the test fault seam is honoured);
  - ledger `<root>\ledger\wf.db`;
  - no gate, no profile or settings change, no real ledger.
- **Tree:** claude-workforce `fix1/integration` `dfd6e850ba2e210c0988ba190612bb822b1d5c0b` (worktree on branch `wf047/plexy`). Windows 10.0.26300.9711, CPython 3.14.6.
- **Inputs:**
  - scope builder and acceptance checker copied unchanged from `wf022/zen` `c92340c` (`make_scope.py`, `check-acceptance.ps1`);
  - contract and grant = the inner objects of my WF-044 `PILOT-CONTRACT-DRAFT.json` / `PILOT-GRANT-DRAFT.json`, with the placeholders filled by SIMULATED values (`contract-as-run.json`, `grant-as-run.json`).
- **Driver:** `rehearse_pilot.py`. Each call is one `python -I -B tools\wf\wf.py --db <root>\ledger\wf.db --role R --session S --generation N --json …` process. It is logged with its **expectation** to `flow-log.jsonl` (copied here); a result that differs from its expectation is flagged UNEXPECTED. The tables below are rendered from that log by `render_tables.py`, not typed.

## Outcome

- **63 `wf` calls, 0 UNEXPECTED.** Every refusal met was planned and came with the planned code.
- 21.34 s of summed `wf` wall time. Owner minutes `unknown` (simulated).
- **Job:** `accept` → `accepted` (seq 30), `close` COMMITTED (seq 31), `export` ok.
- **Verify:** `verify --checkpoint 31:f3277f60…` → `clean: true`, checkpoint status `match`.
- **Digest:**
  - `closed: true`, `needs_owner: []`;
  - counts: contract 1 · grant 2 · bind 4 · intent 5 · receipt 5 · reconcile-report 3 · ack-reconcile 2 · recovery-opened 2 · recovery-closed 2 · recovery-decision 1 · stop-confirmed 1 · delivered 1 · accept 1 · close 1;
  - head `seq 31, self f3277f60b4ec8931818a8bb160f92ed0600f8d64984f9b682d9fec085dae6f38`.
- **Acceptance:** `check-acceptance.ps1` AC1–AC6 True (exit 0). `scope/**` is byte-identical to `pristine/**`.

## What the task asked for, and what happened

| Item | Result | Log rows |
|---|---|---|
| 1. The WF-022 flow on dfd6e85 with the WF-044 draft shapes, job `pilot-docfix-1` | Ran end to end (phases A, B, H, I). | 3–11, 55–68 |
| 1. `--db` everywhere (FIX1 I3) | 62 of 63 calls carry `--db`. The one without (row 2, deliberate) is refused `exit 2`, `rejected(usage): --db required (give --db <path> or set WF_DB)`. | 2, end summary |
| 1. Overlap warning on the draft scopes (FIX1 I4) | Did not fire: `job new` returned no `warnings` key (read `pristine/**` vs write `scope/**`). | 3–4 |
| 2a. Replacement: stop-confirmed → reconcile → `resume` → generation-2 bind | Done (rows 28–37). The stopped B1 is refused `stopped-session` (29). A **post-stop** reconcile (30, report seq 13) is required by `recover decide` (31). B2 is bound at generation 2 (32), B1 is then `stale` (35), and B2's first act is `ack-reconcile` of report 13 at generation 2 (36). B2 then re-records the I2 receipt (37). | 28–37 |
| 2b. Second trigger (T16) | Second staged interruption (G, rows 40–54) after the first recovery. Three second-trigger refusals were observed live: a second `recover begin` while open → `rejected(recovery-open)` (23 and 48); a second replacement at generation 2 → `rejected(bound)` (33); a further replacement at generation 3 without a new stop → `rejected(termination-unconfirmed)` (34). One generation bump in total. | 23, 33, 34, 48 |
| 2c. Checkpoint | `checkpoint` (61) → `verify --checkpoint 30:993fb64c…` → `clean: true`, `match` (62). The altered value `30:000…0` → `clean: false`, status `mismatch`, problem "ladder level reached: level 3 (retained-checkpoint comparison), mismatch" (63). The final checkpoint after `close` (66) is verified `match` (67). | 61–67 |
| 3. Interruption and recovery as in WF-022 step 4 | Same shape twice (C/D and G). Kill during a receipt append held by `delay-precommit:8000` after its marker appeared. Then: marker left; `uncertain-write-pending` probe; `verify clean:false`; `resolve … --attempt` → NOT-COMMITTED; `verify clean:true`; `recover begin` (exceptions `[]`); ordinary `reconcile` refused while open; `recover reconcile` (`seq: null`); `recover close`; ordinary `reconcile` → `X1 attempt-without-completion`. | 12–27, 40–52 |

## Differences from WF-022's RUNBOOK-EXECUTED

| # | WF-022 (2dedc94) | Here (dfd6e85) | Cause |
|---|---|---|---|
| 1 | `--db` silently defaulted to the repository ledger (WF022-F2) | No `--db` and no `WF_DB` → `exit 2`, `rejected(usage)` (row 2) | FIX1 I3 |
| 2 | A case-variant job id was not checked (the S1 order was untested) | `job new PILOT-DOCFIX-1` **while job pilot-docfix-1 had a committing marker** → `rejected(malformed): job id 'PILOT-DOCFIX-1' equals existing job 'pilot-docfix-1' case-insensitively` (row 15), not `uncertain-write-pending`. The marker `9-3b52e798….json` was still present afterwards (row 16). | FIX1 I1 + review S1 |
| 3 | No overlap check | `job new` checks the overlap; none on these scopes (row 4) | FIX1 I4 |
| 4 | — | No call exited 4 (E1 `internal-error`). Every refusal is a structured result. | FIX1 E1 |
| 5 | The builder itself acknowledged X1 | X1 (after interruption 1) is acknowledged by the **replacement** builder B2 against the post-stop report 13. Report 11 (pre-stop) was left unacknowledged, and the replacement rule requires the post-stop one. | rehearsal addition 2a |

All other recovery behaviour matches RUNBOOK-EXECUTED row for row: marker, probe refusal, `clean:false`, `resolve` NOT-COMMITTED with the next event reusing the unclaimed seq, empty recovery exception list, `recovery-open` refusal, `recover reconcile` `seq: null`, and X1 found by the ordinary reconcile (WF022-F7, decided reading (a)).

## Findings

There were **no unexpected refusals**, so no product finding is filed. Four observations matter for the pilot run sheet:

| # | Observation | Disposition |
|---|---|---|
| R47-1 | `verify --checkpoint` with a **mismatching** retained value exits **0**. The mismatch is in `clean: false` and the checkpoint `status: mismatch` (row 63). This is by design (checkpointcmp: "the exit code is left to the caller"), but an owner or coordinator who checks only the exit code would miss it. | NOT A DEFECT (product). The run sheet must read `clean` and `checkpoint.status`, not the exit code (OWNER-DECISIONS change 3). |
| R47-2 | The stop-confirmed must come **before** the reconcile that `recover decide` cites. A report taken before the stop (row 27, seq 11) does not count, so this flow needed a second reconcile (row 30). Ordering stop-confirmed immediately after the kill (before recovery or the first reconcile) saves a reconcile and an unacknowledged report. | NOT A DEFECT; run-sheet order (OWNER-DECISIONS change 2) |
| R47-3 | `recover begin` lists no exceptions for a killed receipt append (rows 22, 47). The gap appears only in the ordinary reconcile after `recover close`. This matches decided WF022-F7 reading (a) (recover = chain/export; reconcile = intent/receipt/attempt). | NOT A DEFECT (decided); spec sentence proposed in WF-042 item 7 |
| R47-4 | My own table renderer crashed writing `→` to a cp1252 console. `python -I` ignores `PYTHONIOENCODING`, so `-X utf8` was needed. Same class as open finding F16 (WF-021 row 45) but in rehearsal tooling only. | NOT A DEFECT (tooling; noted as corroboration of F16) |

## Does OWNER-DECISIONS-SLICE6 need changes? Yes: three additions

These are text proposals for the accepted WF-044 document. I have not edited it.
1. **New lines for the recovery owner turns.** OWNER-DECISIONS lists the stop confirmation (line 9) but not the four other owner turns that every interruption needs (rows 20, 22, 25, 26, 31; OWNER-STEPS §B called them OT-R1..R4). Proposed, after line 9:
   > Resolve the uncertain write of job `<job_id>` at seq `<n>`, attempt `<id>`; I accept the outcome `wf resolve` reports.

   > Owner turn for job `<job_id>`: open the recovery for `<reason>`. *(cited by `recover begin`; the same form, with "reconcile" and "close", for `recover reconcile` and `recover close`. Each must name the job.)*

   > Recovery decision for job `<job_id>`: resume with a replacement builder `<B2>` at generation 2.
2. **Ordering in line 9 (stop confirmation).** Add: "Give this line straight after the staged kill. The coordinator then runs `wf reconcile`, and `recover decide` cites that post-stop report" (R47-2).
3. **Checkpoint check in line 2 (G-28) or the run sheet.** Add: "A retained checkpoint is checked with `wf verify <job> --checkpoint <seq>:<self>`; read `clean` and `checkpoint.status` (`match` / `mismatch` / `missing`), because the command exits 0 either way" (R47-1). Also: "take the final checkpoint **after** `close`" (rows 66–67; WF-022's ordering note).

No change is needed to lines 1, 3–8 or 10, nor to the contract and grant drafts. They validated and ran unchanged apart from the placeholders.

## Phase tables (rendered from `flow-log.jsonl`)

### Setup

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 1 | harness | scope built by make_scope.py (copied from wf022/zen c92340c) | — | {"repo_files": {"tools/wf/README.md": "34176bd887938f26f829ca2b46905920995ab68085e1674e80876fccfe095969", "protocol/PROTOCOL.md": "7d10f02709cd548002a9992b9787301555491590c943d8fbf656d498c2a118d2", "tools/wf/docs/REST... | — | — | |

### A. FIX1 checks

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 2 | coordinator / C-pilot / g1 | `wf status pilot-docfix-1 (no --db)` | 2 | ok=False, code=usage, error=rejected(usage): --db required (give --db <path> or set WF_DB) | exit 2 code usage → yes | 0.292 | FIX1 I3: no --db and no WF_DB -> refused before anything is read or written |
| 4 | harness | FIX1 I4 overlap warning on the draft scopes | — | {"warnings_key_present": false, "warnings": null} | — | — | |

### A. Create

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 3 | coordinator / C-pilot / g1 | `wf job new pilot-docfix-1 --contract C:\src\claude-workforce\.claude\worktrees\wf047...` | 0 | ok=True, outcome=COMMITTED, seq=1 | exit 0 → yes | 0.393 | **OWNER TURN (SIMULATED)**: OT1 contract: the owner's request and acceptance list (SIMULATED by the agent in rehearsal WF-047; in the p... |

### A. Grant

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 5 | coordinator / C-pilot / g1 | `wf grant propose pilot-docfix-1 --grant C:\src\claude-workforce\.claude\worktrees\wf047...` | 0 | ok=True, outcome=COMMITTED, seq=2 | exit 0 → yes | 0.344 |  |
| 6 | coordinator / C-pilot / g1 | `wf grant activate pilot-docfix-1 --owner-hash 9348d979 --quote approve G-pilot-docfix 9348d979 --turn owner turn: gra...` | 0 | ok=True, outcome=COMMITTED, seq=3 | exit 0 → yes | 0.352 | **OWNER TURN (SIMULATED)**: OT2 grant confirmation line: 'approve G-pilot-docfix 9348d979' (SIMULATED by the agent in rehearsal WF-047;... |

### A. Bind

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 7 | coordinator / C-pilot / g1 | `wf bind pilot-docfix-1 --bind-role coordinator --bind-session C-pilot --bind-generation 1 --path scope/**` | 0 | ok=True, outcome=COMMITTED, seq=4, binding={'generation': 1, 'role': 'coordinator', 'session': 'C-pilot'} | exit 0 → yes | 0.353 |  |
| 8 | coordinator / C-pilot / g1 | `wf bind pilot-docfix-1 --bind-role builder --bind-session B1-pilot --bind-generation 1 --tool Write --tool Edit --pat...` | 0 | ok=True, outcome=COMMITTED, seq=5, binding={'generation': 1, 'role': 'builder', 'session': 'B1-pilot'} | exit 0 → yes | 0.366 |  |
| 9 | coordinator / C-pilot / g1 | `wf status pilot-docfix-1` | 0 | ok=True, pending_markers=0, execution=queued | exit 0 → yes | 0.327 |  |

### B. Repair I1

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 10 | builder / B1-pilot / g1 | `wf append pilot-docfix-1 --type intent --content {"path": "scope/README.md", "sha_before": "f16a...` | 0 | ok=True, outcome=COMMITTED, seq=6 | exit 0 → yes | 0.37 |  |
| 11 | builder / B1-pilot / g1 | `wf append pilot-docfix-1 --type receipt --content {"path": "scope/README.md", "sha_before": "f16a...` | 0 | ok=True, outcome=COMMITTED, seq=7 | exit 0 → yes | 0.348 |  |

### C. Interruption 1

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 12 | builder / B1-pilot / g1 | `wf append pilot-docfix-1 --type intent --content {"path": "scope/README.md", "sha_before": "1b9c...` | 0 | ok=True, outcome=COMMITTED, seq=8 | exit 0 → yes | 0.351 |  |
| 13 | builder / B1-pilot / g1 | `wf append pilot-docfix-1 --type receipt (killed)` | 1 |  | killed after the committing marker appeared → yes | 0.652 | staged kill: WF_TEST_FAULTS=delay-precommit:8000, marker after 0.348s, killed (TerminateProcess) |
| 14 | harness | after kill 1 | — | {"markers": ["9-3b52e79881907b14904d2e0327ba5c4c.json"]} | — | — | |

### C. FIX1 S1 clash probe

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 15 | coordinator / C-pilot / g1 | `wf job new PILOT-DOCFIX-1 --contract C:\src\claude-workforce\.claude\worktrees\wf047...` | 1 | ok=False, code=rejected, error=rejected(malformed): job id 'PILOT-DOCFIX-1' equals existing job 'p... | exit 1 rejected(malformed) → yes | 0.303 | FIX1 I1 + review S1: a case-variant id is refused rejected(malformed) even while the clashing job has a com... |
| 16 | harness | marker still present after the clash probe | — | {"markers": ["9-3b52e79881907b14904d2e0327ba5c4c.json"]} | — | — | |

### D. Recovery 1

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 17 | coordinator / C-pilot / g1 | `wf status pilot-docfix-1` | 0 | ok=True, pending_markers=1, execution=active+uncertain-write | exit 0 → yes | 0.302 |  |
| 18 | builder / PROBE / g1 | `wf append pilot-docfix-1 --type blocker --content {"reason":"probe"}` | 1 | ok=False, code=uncertain-write-pending, seq=9, error=uncertain-write pending: pilot-docfix-1/9/3b52e79881907b14904d2e032... | exit 1 code uncertain-write-pending → yes | 0.303 | any append is refused while a write is uncertain |
| 19 | coordinator / C-pilot / g1 | `wf verify pilot-docfix-1` | 0 | ok=True, clean=False | exit 0 → yes | 0.3 | expected clean:false while the marker exists |
| 20 | coordinator / C-pilot / g1 | `wf resolve pilot-docfix-1 9 --attempt 3b52e79881907b14904d2e0327ba5c4c` | 0 | ok=True, outcome=NOT-COMMITTED, seq=9 | exit 0 → yes | 0.298 | **OWNER TURN (SIMULATED)**: owner-present decision on the uncertain write (SIMULATED by the agent in rehearsal WF-047; in the pilot the... |
| 21 | coordinator / C-pilot / g1 | `wf verify pilot-docfix-1` | 0 | ok=True, clean=True | exit 0 → yes | 0.3 |  |
| 22 | coordinator / C-pilot / g1 | `wf recover begin pilot-docfix-1 --reason staged kill during a receipt append (1) --turn owner turn for job pilot-docf...` | 0 | ok=True, outcome=COMMITTED, seq=9, exceptions=[] | exit 0 → yes | 0.349 | **OWNER TURN (SIMULATED)**: owner turn for job pilot-docfix-1: recover 1 (SIMULATED by the agent in rehearsal WF-047; in the pilot the ... |
| 23 | coordinator / C-pilot / g1 | `wf recover begin pilot-docfix-1 --reason second trigger for the same interruption --turn owner turn for job pilot-doc...` | 1 | ok=False, code=rejected, error=rejected(recovery-open): recovery is already open since seq 9 | exit 1 rejected(recovery-open) → yes | 0.302 | T16: a second recovery trigger for the same interruption is refused |
| 24 | coordinator / C-pilot / g1 | `wf reconcile pilot-docfix-1` | 1 | ok=False, code=rejected, error=rejected(recovery-open): recovery is open since seq 9; only `wf rec... | exit 1 rejected(recovery-open) → yes | 0.296 | ordinary reconcile is refused while the recovery is open (as WF-022 row 18) |
| 25 | coordinator / C-pilot / g1 | `wf recover reconcile pilot-docfix-1 --turn owner turn for job pilot-docfix-1: recover 1 (S...` | 0 | ok=True, seq=None | exit 0 → yes | 0.292 | **OWNER TURN (SIMULATED)**: owner turn for job pilot-docfix-1: recover 1 (SIMULATED by the agent in rehearsal WF-047; in the pilot the ... |
| 26 | coordinator / C-pilot / g1 | `wf recover close pilot-docfix-1 --turn owner turn for job pilot-docfix-1: recover 1 (S...` | 0 | ok=True, outcome=COMMITTED, seq=10 | exit 0 → yes | 0.35 | **OWNER TURN (SIMULATED)**: owner turn for job pilot-docfix-1: recover 1 (SIMULATED by the agent in rehearsal WF-047; in the pilot the ... |
| 27 | coordinator / C-pilot / g1 | `wf reconcile pilot-docfix-1` | 0 | ok=True, outcome=COMMITTED, seq=11, exceptions=[{'class': 'attempt-without-completion', 'id': 'X1', 'path': 'scope... | exit 0 → yes | 0.343 | ordinary reconcile after recover close (WF022-F7 reading a: role-level gaps live here) |

### E. Replacement (WF-044 gap a)

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 28 | coordinator / C-pilot / g1 | `wf append pilot-docfix-1 --type stop-confirmed --content {"role": "builder", "session": "B1-pilot", "gen...` | 0 | ok=True, outcome=COMMITTED, seq=12 | exit 0 → yes | 0.368 | **OWNER TURN (SIMULATED)**: (e) stop-confirmed: no child process / outstanding operation (SIMULATED by the agent in rehearsal WF-047; i... |
| 29 | builder / B1-pilot / g1 | `wf append pilot-docfix-1 --type blocker --content {"reason":"after stop"}` | 1 | ok=False, code=rejected, error=rejected(stopped-session): session 'B1-pilot' was stopped for role ... | exit 1 rejected(stopped-session) → yes | 0.295 | the stopped builder can no longer write |
| 30 | coordinator / C-pilot / g1 | `wf reconcile pilot-docfix-1` | 0 | ok=True, outcome=COMMITTED, seq=13, exceptions=[{'class': 'attempt-without-completion', 'id': 'X1', 'path': 'scope... | exit 0 → yes | 0.35 | post-stop reconcile report (needed by decide) |
| 31 | coordinator / C-pilot / g1 | `wf recover decide pilot-docfix-1 --decision resume --turn owner: resume pilot-docfix-1 with a replacement...` | 0 | ok=True, outcome=COMMITTED, decision=resume, seq=14 | exit 0 → yes | 0.33 | **OWNER TURN (SIMULATED)**: recovery decision 'resume' (SIMULATED by the agent in rehearsal WF-047; in the pilot the owner types this) |
| 32 | coordinator / C-pilot / g1 | `wf bind pilot-docfix-1 --bind-role builder --bind-session B2-pilot --bind-generation 2 --tool Write --tool Edit --pat...` | 0 | ok=True, outcome=COMMITTED, seq=15, binding={'generation': 2, 'role': 'builder', 'session': 'B2-pilot'} | exit 0 → yes | 0.341 |  |
| 35 | builder / B1-pilot / g1 | `wf append pilot-docfix-1 --type blocker --content {"reason":"stale"}` | 1 | ok=False, code=rejected, error=rejected(stale): generation 1 below the bound generation 2 for role... | exit 1 rejected(stale) → yes | 0.298 | T04: the replaced session is stale |
| 36 | builder / B2-pilot / g2 | `wf ack-reconcile pilot-docfix-1 --report 13 --exceptions ["X1"] --note X: the I2 edit is on disk (sha matches the int...` | 0 | ok=True, outcome=COMMITTED, seq=16 | exit 0 → yes | 0.35 | D-X1 judgement (who decides is an owner choice, OWNER-DECISIONS line 5) |
| 37 | builder / B2-pilot / g2 | `wf append pilot-docfix-1 --type receipt --content {"path": "scope/README.md", "sha_before": "1b9c...` | 0 | ok=True, outcome=COMMITTED, seq=17 | exit 0 → yes | 0.349 |  |

### E. Second replacement triggers (T16)

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 33 | coordinator / C-pilot / g1 | `wf bind pilot-docfix-1 --bind-role builder --bind-session B3-pilot --bind-generation 2 --tool Write --tool Edit --pat...` | 1 | ok=False, code=rejected, error=rejected(bound): role builder is already bound at generation 2 (bin... | exit 1 rejected(bound) → yes | 0.294 | T16: a second replacement at the same generation is refused |
| 34 | coordinator / C-pilot / g1 | `wf bind pilot-docfix-1 --bind-role builder --bind-session B3-pilot --bind-generation 3 --tool Write --tool Edit --pat...` | 1 | ok=False, code=rejected, error=rejected(termination-unconfirmed): session 'B2-pilot' (generation 2... | exit 1 rejected(termination-unconfirmed) → yes | 0.308 | T16: a further replacement without a new stop-confirmed is refused (one generation bump) |

### F. Repair I3

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 38 | builder / B2-pilot / g2 | `wf append pilot-docfix-1 --type intent --content {"path": "scope/PROTOCOL.md", "sha_before": "58...` | 0 | ok=True, outcome=COMMITTED, seq=18 | exit 0 → yes | 0.362 |  |
| 39 | builder / B2-pilot / g2 | `wf append pilot-docfix-1 --type receipt --content {"path": "scope/PROTOCOL.md", "sha_before": "58...` | 0 | ok=True, outcome=COMMITTED, seq=19 | exit 0 → yes | 0.349 |  |

### G. Interruption 2 (WF-044 gap b)

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 40 | builder / B2-pilot / g2 | `wf append pilot-docfix-1 --type intent --content {"path": "scope/RESTORE-RUNBOOK.md", "sha_befor...` | 0 | ok=True, outcome=COMMITTED, seq=20 | exit 0 → yes | 0.356 |  |
| 41 | builder / B2-pilot / g2 | `wf append pilot-docfix-1 --type receipt (killed)` | 1 |  | killed after the committing marker appeared → yes | 0.616 | staged kill: WF_TEST_FAULTS=delay-precommit:8000, marker after 0.313s, killed (TerminateProcess) |

### G. Recovery 2

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 42 | coordinator / C-pilot / g1 | `wf status pilot-docfix-1` | 0 | ok=True, pending_markers=1, execution=active+uncertain-write | exit 0 → yes | 0.311 |  |
| 43 | builder / PROBE / g1 | `wf append pilot-docfix-1 --type blocker --content {"reason":"probe"}` | 1 | ok=False, code=uncertain-write-pending, seq=21, error=uncertain-write pending: pilot-docfix-1/21/0d996b8c691b154f20bcae58... | exit 1 code uncertain-write-pending → yes | 0.291 | any append is refused while a write is uncertain |
| 44 | coordinator / C-pilot / g1 | `wf verify pilot-docfix-1` | 0 | ok=True, clean=False | exit 0 → yes | 0.3 | expected clean:false while the marker exists |
| 45 | coordinator / C-pilot / g1 | `wf resolve pilot-docfix-1 21 --attempt 0d996b8c691b154f20bcae5830f42569` | 0 | ok=True, outcome=NOT-COMMITTED, seq=21 | exit 0 → yes | 0.301 | **OWNER TURN (SIMULATED)**: owner-present decision on the uncertain write (SIMULATED by the agent in rehearsal WF-047; in the pilot the... |
| 46 | coordinator / C-pilot / g1 | `wf verify pilot-docfix-1` | 0 | ok=True, clean=True | exit 0 → yes | 0.314 |  |
| 47 | coordinator / C-pilot / g1 | `wf recover begin pilot-docfix-1 --reason staged kill during a receipt append (2) --turn owner turn for job pilot-docf...` | 0 | ok=True, outcome=COMMITTED, seq=21, exceptions=[] | exit 0 → yes | 0.373 | **OWNER TURN (SIMULATED)**: owner turn for job pilot-docfix-1: recover 2 (SIMULATED by the agent in rehearsal WF-047; in the pilot the ... |
| 48 | coordinator / C-pilot / g1 | `wf recover begin pilot-docfix-1 --reason second trigger for the same interruption --turn owner turn for job pilot-doc...` | 1 | ok=False, code=rejected, error=rejected(recovery-open): recovery is already open since seq 21 | exit 1 rejected(recovery-open) → yes | 0.3 | T16: a second recovery trigger for the same interruption is refused |
| 49 | coordinator / C-pilot / g1 | `wf reconcile pilot-docfix-1` | 1 | ok=False, code=rejected, error=rejected(recovery-open): recovery is open since seq 21; only `wf re... | exit 1 rejected(recovery-open) → yes | 0.296 | ordinary reconcile is refused while the recovery is open (as WF-022 row 18) |
| 50 | coordinator / C-pilot / g1 | `wf recover reconcile pilot-docfix-1 --turn owner turn for job pilot-docfix-1: recover 2 (S...` | 0 | ok=True, seq=None | exit 0 → yes | 0.304 | **OWNER TURN (SIMULATED)**: owner turn for job pilot-docfix-1: recover 2 (SIMULATED by the agent in rehearsal WF-047; in the pilot the ... |
| 51 | coordinator / C-pilot / g1 | `wf recover close pilot-docfix-1 --turn owner turn for job pilot-docfix-1: recover 2 (S...` | 0 | ok=True, outcome=COMMITTED, seq=22 | exit 0 → yes | 0.355 | **OWNER TURN (SIMULATED)**: owner turn for job pilot-docfix-1: recover 2 (SIMULATED by the agent in rehearsal WF-047; in the pilot the ... |
| 52 | coordinator / C-pilot / g1 | `wf reconcile pilot-docfix-1` | 0 | ok=True, outcome=COMMITTED, seq=23, exceptions=[{'class': 'attempt-without-completion', 'id': 'X1', 'path': 'scope... | exit 0 → yes | 0.353 |  |
| 53 | builder / B2-pilot / g2 | `wf ack-reconcile pilot-docfix-1 --report 23 --exceptions ["X1"] --note X: the I4 edit is on disk; the receipt was los...` | 0 | ok=True, outcome=COMMITTED, seq=24 | exit 0 → yes | 0.353 |  |
| 54 | builder / B2-pilot / g2 | `wf append pilot-docfix-1 --type receipt --content {"path": "scope/RESTORE-RUNBOOK.md", "sha_befor...` | 0 | ok=True, outcome=COMMITTED, seq=25 | exit 0 → yes | 0.353 |  |

### H. Repair I5

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 55 | builder / B2-pilot / g2 | `wf append pilot-docfix-1 --type intent --content {"path": "scope/PROTOCOL.md", "sha_before": "57...` | 0 | ok=True, outcome=COMMITTED, seq=26 | exit 0 → yes | 0.352 |  |
| 56 | builder / B2-pilot / g2 | `wf append pilot-docfix-1 --type receipt --content {"path": "scope/PROTOCOL.md", "sha_before": "57...` | 0 | ok=True, outcome=COMMITTED, seq=27 | exit 0 → yes | 0.346 |  |

### H. Deliver

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 57 | builder / B2-pilot / g2 | `wf append pilot-docfix-1 --type delivered --content {"claims": [{"claim": "I1-I5 restored after two...` | 0 | ok=True, outcome=COMMITTED, seq=28 | exit 0 → yes | 0.346 |  |

### H. Review

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 58 | coordinator / C-pilot / g1 | `wf bind pilot-docfix-1 --bind-role reviewer --bind-session R1-pilot --bind-generation 1 --tool Read --path scope/** -...` | 0 | ok=True, outcome=COMMITTED, seq=29, binding={'generation': 1, 'role': 'reviewer', 'session': 'R1-pilot'} | exit 0 → yes | 0.346 |  |
| 59 | harness | check-acceptance.ps1 (reviewer) | — | {"exit": 0, "AC1": "True", "AC2": "True", "AC3": "True", "AC4": "True", "AC5": "True", "AC6": "True"} | — | — | |

### H. Accept

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 60 | reviewer / R1-pilot / g1 | `wf accept pilot-docfix-1 --results C:\src\claude-workforce\.claude\worktrees\wf047...` | 0 | ok=True, outcome=COMMITTED, decision=accepted, seq=30 | exit 0 → yes | 0.357 |  |

### I. Checkpoint (WF-044 gap c)

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 61 | coordinator / C-pilot / g1 | `wf checkpoint pilot-docfix-1` | 0 | ok=True, seq=30 | exit 0 → yes | 0.299 | **OWNER TURN (SIMULATED)**: OT3 the owner pastes 'pilot-docfix-1 <seq>:<self>' as the retained checkpoint (SIMULATED by the agent in re... |
| 62 | coordinator / C-pilot / g1 | `wf verify pilot-docfix-1 --checkpoint 30:993fb64ccbbf94bdd2d61086480b8e19160599139ad6...` | 0 | ok=True, clean=True, checkpoint={'disclaimer': 'not an authenticity claim', 'expected_self': '993fb... | exit 0 → yes | 0.295 | retained checkpoint matches the chain |
| 63 | coordinator / C-pilot / g1 | `wf verify pilot-docfix-1 --checkpoint 30:00000000000000000000000000000000000000000000...` | 0 | ok=True, clean=False, checkpoint={'disclaimer': 'not an authenticity claim', 'expected_self': '00000... | exit 0 → yes | 0.31 | an altered retained value is reported as a mismatch (clean false) |

### I. Close

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 64 | coordinator / C-pilot / g1 | `wf close pilot-docfix-1 --intervention owner-present recovery twice (resolve, recover ...` | 0 | ok=True, outcome=COMMITTED, seq=31 | exit 0 → yes | 0.362 | **OWNER TURN (SIMULATED)**: OT4 intervention declaration, minutes unknown (SIMULATED by the agent in rehearsal WF-047; in the pilot the... |
| 65 | coordinator / C-pilot / g1 | `wf export pilot-docfix-1` | 0 | ok=True, code=ok | exit 0 → yes | 0.328 |  |
| 66 | coordinator / C-pilot / g1 | `wf checkpoint pilot-docfix-1` | 0 | ok=True, seq=31 | exit 0 → yes | 0.298 | **OWNER TURN (SIMULATED)**: OT3' final retained checkpoint after close (SIMULATED by the agent in rehearsal WF-047; in the pilot the ow... |
| 67 | coordinator / C-pilot / g1 | `wf verify pilot-docfix-1 --checkpoint 31:f3277f60b4ec8931818a8bb160f92ed0600f8d64984f...` | 0 | ok=True, clean=True, checkpoint={'disclaimer': 'not an authenticity claim', 'expected_self': 'f3277... | exit 0 → yes | 0.305 |  |
| 68 | coordinator / C-pilot / g1 | `wf digest pilot-docfix-1` | 0 | ok=True, pending_markers=0, execution=delivered | exit 0 → yes | 0.335 | **OWNER TURN (SIMULATED)**: OT5 the owner reads the digest (SIMULATED by the agent in rehearsal WF-047; in the pilot the owner types this) |

### End

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 69 | harness | summary | — | {"calls": 63, "without_db": [2], "unexpected": [], "scope_equals_pristine": true} | — | — | |
