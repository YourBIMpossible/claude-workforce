# WF-047 — Extended dry rehearsal of the slice-6 pilot flow on the FIX1 head (Plexy)

**Rehearsal, not a pilot.**
- Run: 2026-10-19 by the agent, playing every role. This is the **review-F1 run** (after Lazy's WF-049 review, findings L49-S1..S6). It replaces the first run (63 calls, branch commit `c174faa`).
- Every **OWNER TURN** is **SIMULATED** and marked so in the ledger texts.
- Disposable fixture only:
  - boundary root `<repo>\fixtures\tmp\wf047-f1\` (the only place the test fault seam is honoured);
  - ledger `<root>\ledger\wf.db`;
  - a copy of the root, `<repo>\fixtures\tmp\wf047-f1-x4b\`, for the deleted-last-row case;
  - no gate, no profile or settings change, no real ledger.
- **Tree:** claude-workforce `fix1/integration` `dfd6e850ba2e210c0988ba190612bb822b1d5c0b` (worktree on branch `wf047/plexy`). Windows 10.0.26300, CPython 3.14.6.
- **Inputs:**
  - scope builder and acceptance checker copied unchanged from `wf022/zen` `c92340c` (`make_scope.py`, `check-acceptance.ps1`);
  - contract and grant = the inner objects of my WF-044 `PILOT-CONTRACT-DRAFT.json` / `PILOT-GRANT-DRAFT.json`, with the placeholders filled by SIMULATED values (`contract-as-run.json`, `grant-as-run.json`; byte-for-byte the same values as the first run).
- **Driver:** `rehearse_pilot.py`. Each call is one `python -I -B tools\wf\wf.py --db <root>\ledger\wf.db --role R --session S --generation N --json …` process.
  - Each call is logged with its **expectation** to `flow-log.jsonl` (copied here). The expectation is the exit code and refusal code, **plus the result fields the text below relies on**: `clean`, `exceptions`, `decision`, `outcome`, `report_seq`/`stop_seq`, `checkpoint.status`, and refusal-message parts (L49-S2).
  - A result that differs from its expectation in any of these is flagged UNEXPECTED. The failing fields are logged in `field_failures`.
  - `check-acceptance.ps1` must report exactly AC1..AC6 as True/False with exit 0 or 1, or the run stops (the WF-040 L40-S1 `run_checks`, ported).
  - The tables below are rendered from that log by `render_tables.py` (`python -I -B render_tables.py flow-log.jsonl <out.md>`; UTF-8, L49-S6), not typed.

## Outcome

- **81 `wf` calls, 0 UNEXPECTED; 57 of them also carry result-field checks.** Every refusal met was planned and came with the planned code and message.
- 25.75 s of summed `wf` wall time. Owner minutes `unknown` (simulated).
- **Job:** `accept` → `accepted` (seq 32), `close` COMMITTED (seq 33), `export` ok.
- **Retained checkpoints:** four were taken: after recovery 1 (seq 11), after recovery 2 (seq 21), before close (seq 32), and after close (seq 33). At close, all four were re-verified against the closed chain, each `clean: true`, `match` (rows 77–80).
- **Digest:**
  - `closed: true`;
  - counts: contract 1 · grant 2 · bind 4 · intent 5 · receipt 5 · reconcile-report 3 · ack-reconcile 2 · recovery-opened 2 · recovery-closed 2 · recovery-decision 2 · stop-confirmed 2 · delivered 1 · accept 1 · close 1;
  - head `seq 33, self 2ca6f04db2897ecea4b98ba80fc64baccae1b056ebd020992c1c1aed88992aaf`;
  - `needs_owner`: one item, "unreconciled builder" for report 29. This is the deliberate replacement-max probe (H-max), see R47-5.
- **Acceptance:** `check-acceptance.ps1` AC1–AC6 True (exit 0). `scope/**` is byte-identical to `pristine/**`.

## What the task asked for, and what happened

| Item | Result | Log rows |
|---|---|---|
| 1. The WF-022 flow on dfd6e85 with the WF-044 draft shapes, job `pilot-docfix-1` | Ran end to end (phases A, B, H, I). | 3–11, 60–81 |
| 1. `--db` everywhere (FIX1 I3) | 80 of 81 calls carry `--db`. The one without (row 2, deliberate) is refused `exit 2`, `rejected(usage): --db required (give --db <path> or set WF_DB)`. | 2, end summary |
| 1. Overlap warning on the draft scopes (FIX1 I4) | Did not fire: `job new` returned no `warnings` key (read `pristine/**` vs write `scope/**`). | 3–4 |
| 2a. Replacement: stop-confirmed → reconcile → `resume` → generation-2 bind | Done, in the order r3 line 11 proposes (L49-S1):<br>- the stop line is refused `uncertain-write-pending` straight after the kill (19);<br>- it is accepted after `wf resolve` and before `recover begin` (23, seq 9);<br>- **one** ordinary reconcile after `recover close` (31, report seq 12) is post-stop, and `recover decide resume` cites it (33: `report_seq` 12, `stop_seq` 9);<br>- the stopped B1 is refused `stopped-session` (32);<br>- B2 is bound at generation 2 (34), and B1 is then `stale` (37);<br>- B2's first act is `ack-reconcile` of report 12 (38), then it re-records the I2 receipt (39).<br>The first run needed two reconciles and left one report unacknowledged. | 19–39 |
| 2b. Second trigger (T16) and the replacement limit | **Second-trigger refusals:**<br>- a second `recover begin` while open → `rejected(recovery-open)` (25, 50);<br>- a second replacement at generation 2 → `rejected(bound)` (35);<br>- a generation-3 replacement without a new stop → `rejected(termination-unconfirmed)` (36). bind.py checks the stop (:412) before the max (:429), so this row does **not** show the max.<br>**The max itself (L49-S3):** after delivery, B2 is stop-confirmed (63), a post-stop reconcile (64, exceptions `[]`) and `recover decide resume` (65) are recorded. The generation-3 bind is then refused `rejected(delegation): replacement count 1 for builder has reached max 1` (66). | 25, 35, 36, 50, 63–66 |
| 2c. Checkpoint | **Retained checkpoints (L49-S4):**<br>- after each recovery (29–30 and 55–56), before close (70–71) and after close (75–76), each verified at once;<br>- at close, every retained checkpoint is re-verified: `clean: true`, `match` (77–80). For the earlier ones the statement names the uncovered tail, e.g. "the head is now seq 33 and events 12-33 are not covered by this checkpoint" (77).<br>**Altered value:** `32:000…0` → `clean: false`, `mismatch`, exit 0 (72).<br>**Deleted last row, on a copy** (Lazy X4b): delete trigger dropped, seq 33 deleted, trigger restored, export regenerated (82–83). Then:<br>- plain `verify` stays `clean: true` (84), the documented limit;<br>- `verify --checkpoint 33:…` → `clean: false`, `missing`, "does not contain seq 33" (85);<br>- `verify --restored` → `clean: true` without a checkpoint (86), and `clean: false`, `missing` with it (87). | 29–30, 55–56, 70–87 |
| 3. Interruption and recovery as in WF-022 step 4 | Same shape twice (C/D, and G as extra coverage). Kill during a receipt append held by `delay-precommit:8000` after its marker appeared. Then:<br>- marker left; `uncertain-write-pending` probe;<br>- `verify` `clean: false`; `resolve … --attempt` → NOT-COMMITTED; `verify` `clean: true`;<br>- `recover begin` (exceptions `[]`); ordinary `reconcile` refused while open;<br>- `recover reconcile` (`seq: null`); `recover close`;<br>- ordinary `reconcile` → `X1 attempt-without-completion` on the interrupted file.<br>In G, a stop line tried during the open recovery is refused `recovery-open` (52; Lazy X3). | 12–31, 42–59 |

## Differences from WF-022's RUNBOOK-EXECUTED

| # | WF-022 (2dedc94) | Here (dfd6e85) | Cause |
|---|---|---|---|
| 1 | `--db` silently defaulted to the repository ledger (WF022-F2) | No `--db` and no `WF_DB` → `exit 2`, `rejected(usage)` (row 2) | FIX1 I3 |
| 2 | A case-variant job id was not checked (the S1 order was untested) | `job new PILOT-DOCFIX-1` **while job pilot-docfix-1 had a committing marker** → `rejected(malformed): job id 'PILOT-DOCFIX-1' equals existing job 'pilot-docfix-1' case-insensitively` (row 15), not `uncertain-write-pending`. The marker was still present afterwards (row 16). | FIX1 I1 + review S1 |
| 3 | No overlap check | `job new` checks the overlap; none on these scopes (row 4) | FIX1 I4 |
| 4 | — | No call exited 4 (E1 `internal-error`). Every refusal is a structured result. | FIX1 E1 |
| 5 | The builder itself acknowledged X1 | X1 (after interruption 1) is acknowledged by the **replacement** builder B2 against the single post-stop report 12 (row 38). | rehearsal addition 2a |

All other recovery behaviour matches RUNBOOK-EXECUTED row for row: marker, probe refusal, `clean: false`, `resolve` NOT-COMMITTED with the next event reusing the unclaimed seq (here the stop line takes seq 9, row 23), empty recovery exception list, `recovery-open` refusal, `recover reconcile` `seq: null`, and X1 found by the ordinary reconcile (WF022-F7, decided reading (a)).

## Findings

There were **no unexpected results**, so no product finding is filed. Six observations matter for the pilot run sheet:

| # | Observation | Disposition |
|---|---|---|
| R47-1 | `verify --checkpoint` with a **mismatching** retained value exits **0**. The mismatch is in `clean: false` and the checkpoint `status: mismatch` (row 72). This is by design (checkpointcmp: "the exit code is left to the caller"), but an owner or coordinator who checks only the exit code would miss it. A lost tail shows the same way, as `missing` (85, 87). | NOT A DEFECT (product). The run sheet reads `clean` and `checkpoint.status`, not the exit code (OWNER-DECISIONS r3 line 2). |
| R47-2 | `recover decide` must cite a reconcile report taken **after** the stop. The stop line can be recorded only after `wf resolve` and outside an open recovery (rows 19, 23, 52). In that order a single reconcile serves (31 → 33). | NOT A DEFECT; run-sheet order (r3 line 11; corrected by WF-049 L49-S1) |
| R47-3 | `recover begin` lists no exceptions for a killed receipt append (rows 24, 49). The gap appears only in the ordinary reconcile after `recover close` (31, 57). This matches decided WF022-F7 reading (a) (recover = chain/export; reconcile = intent/receipt/attempt). | NOT A DEFECT (decided); spec sentence proposed in WF-042 item 7 |
| R47-4 | The first run's table renderer crashed writing `→` to a cp1252 console (`python -I` ignores `PYTHONIOENCODING`). `render_tables.py` now writes UTF-8 itself, to a file argument or to a reconfigured stdout (L49-S6). | Fixed (tooling) |
| R47-5 | The replacement-max probe (63–66) leaves report 29 unacknowledged for good: B2 is stopped and no generation-3 builder may be bound. `close` does not require the acknowledgement, and the digest lists it under `needs_owner`. Its recommendation ("the bound session runs `wf ack-reconcile` …") names a session that can no longer write. | NOT A DEFECT for the pilot: the probe is coverage only. **The pilot run sheet must not include it.** The digest wording is noted for the coordinator. |
| R47-6 | The checkpoint status words differ by command. `verify --checkpoint` says `match` / `mismatch` / `missing`, and `verify --restored … --checkpoint` says `matches` / `differs` / `missing` (backup.py:185). Only `missing` is shared (85, 87). | NOT A DEFECT; a run sheet that checks a restored copy must read the restored vocabulary. Noted for the coordinator. |

## Review F1 (WF-049) — what changed from the first run

| Finding | Change | Rows |
|---|---|---|
| L49-S1 | The stop line is probed straight after the kill (refused `uncertain-write-pending`), recorded after `wf resolve` and before `recover begin`, and probed during the second open recovery (refused `recovery-open`). One post-stop reconcile replaces the first run's two. | 19, 23, 31, 33, 52 |
| L49-S2 | Expectations carry result fields (57 calls). `check-acceptance.ps1` must report AC1..AC6 or the run stops. In scratch copies of the driver, a missing script, a banner-only stand-in (exit 5), and a stand-in printing only AC1–AC5 each stopped the run. | all; 68 |
| L49-S3 | The replacement max is reached and refused with its own message. Phase G is renamed "second interruption (extra coverage)"; gap (b) is rows 25, 35, 36, 50 and 63–66. | 63–66 |
| L49-S4 | A checkpoint after each recovery; all retained checkpoints re-verified at close; the deleted-last-row case on a copy. | 29–30, 55–56, 77–87 |
| L49-S6 | `render_tables.py` writes UTF-8. | — |

## Phase tables (rendered from `flow-log.jsonl`)

### Setup

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 1 | harness | scope built by make_scope.py (copied from wf022/zen c92340c) | — | {"repo_files": {"tools/wf/README.md": "34176bd887938f26f829ca2b46905920995ab68085e1674e80876fccfe095969", "protocol/PROTOCOL.md": "7d10f02709cd548002a9992b9787301555491590c943d8fbf656d498c2a118d2", "tools/wf/docs/REST... | — | — | |

### A. FIX1 checks

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 2 | coordinator / C-pilot / g1 | `wf status pilot-docfix-1 (no --db)` | 2 | ok=False, code=usage, error=rejected(usage): --db required (give --db <path> or set WF_DB) | exit 2 code usage → yes | 0.29 | FIX1 I3: no --db and no WF_DB -> refused before anything is read or written |
| 4 | harness | FIX1 I4 overlap warning on the draft scopes | — | {"warnings_key_present": false, "warnings": null} | — | — | |

### A. Create

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 3 | coordinator / C-pilot / g1 | `wf job new pilot-docfix-1 --contract C:\src\claude-workforce\.claude\worktrees\wf047...` | 0 | ok=True, outcome=COMMITTED, seq=1 | exit 0; outcome='COMMITTED'; seq=1 → yes | 0.361 | **OWNER TURN (SIMULATED)**: OT1 contract: the owner's request and acceptance list (SIMULATED by the agent in rehearsal WF-047; in the p... |

### A. Grant

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 5 | coordinator / C-pilot / g1 | `wf grant propose pilot-docfix-1 --grant C:\src\claude-workforce\.claude\worktrees\wf047...` | 0 | ok=True, outcome=COMMITTED, seq=2 | exit 0 → yes | 0.328 |  |
| 6 | coordinator / C-pilot / g1 | `wf grant activate pilot-docfix-1 --owner-hash 9348d979 --quote approve G-pilot-docfix 9348d979 --turn owner turn: gra...` | 0 | ok=True, outcome=COMMITTED, seq=3 | exit 0; outcome='COMMITTED' → yes | 0.329 | **OWNER TURN (SIMULATED)**: OT2 grant confirmation line: 'approve G-pilot-docfix 9348d979' (SIMULATED by the agent in rehearsal WF-047;... |

### A. Bind

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 7 | coordinator / C-pilot / g1 | `wf bind pilot-docfix-1 --bind-role coordinator --bind-session C-pilot --bind-generation 1 --path scope/**` | 0 | ok=True, outcome=COMMITTED, seq=4, binding={'generation': 1, 'role': 'coordinator', 'session': 'C-pilot'} | exit 0; binding.role='coordinator' → yes | 0.332 |  |
| 8 | coordinator / C-pilot / g1 | `wf bind pilot-docfix-1 --bind-role builder --bind-session B1-pilot --bind-generation 1 --tool Write --tool Edit --pat...` | 0 | ok=True, outcome=COMMITTED, seq=5, binding={'generation': 1, 'role': 'builder', 'session': 'B1-pilot'} | exit 0; binding.session='B1-pilot'; binding.generation=1 → yes | 0.334 |  |
| 9 | coordinator / C-pilot / g1 | `wf status pilot-docfix-1` | 0 | ok=True, pending_markers=0, execution=queued | exit 0; pending_markers=[] → yes | 0.292 |  |

### B. Repair I1

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 10 | builder / B1-pilot / g1 | `wf append pilot-docfix-1 --type intent --content {"path": "scope/README.md", "sha_before": "f16a...` | 0 | ok=True, outcome=COMMITTED, seq=6 | exit 0; outcome='COMMITTED' → yes | 0.332 |  |
| 11 | builder / B1-pilot / g1 | `wf append pilot-docfix-1 --type receipt --content {"path": "scope/README.md", "sha_before": "f16a...` | 0 | ok=True, outcome=COMMITTED, seq=7 | exit 0; outcome='COMMITTED' → yes | 0.332 |  |

### C. Interruption 1

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 12 | builder / B1-pilot / g1 | `wf append pilot-docfix-1 --type intent --content {"path": "scope/README.md", "sha_before": "1b9c...` | 0 | ok=True, outcome=COMMITTED, seq=8 | exit 0; outcome='COMMITTED' → yes | 0.333 |  |
| 13 | builder / B1-pilot / g1 | `wf append pilot-docfix-1 --type receipt (killed)` | 1 |  | killed after the committing marker appeared → yes | 0.596 | staged kill: WF_TEST_FAULTS=delay-precommit:8000, marker after 0.293s, killed (TerminateProcess) |
| 14 | harness | after kill 1 | — | {"markers": ["9-66e20fbad3acc557ad36138a3823a4da.json"]} | — | — | |

### C. FIX1 S1 clash probe

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 15 | coordinator / C-pilot / g1 | `wf job new PILOT-DOCFIX-1 --contract C:\src\claude-workforce\.claude\worktrees\wf047...` | 1 | ok=False, code=rejected, error=rejected(malformed): job id 'PILOT-DOCFIX-1' equals existing job 'p... | exit 1 rejected(malformed); error=~'case-insensitively' → yes | 0.295 | FIX1 I1 + review S1: a case-variant id is refused rejected(malformed) even while the clashing job has a com... |
| 16 | harness | marker still present after the clash probe | — | {"markers": ["9-66e20fbad3acc557ad36138a3823a4da.json"]} | — | — | |

### D. Recovery 1 (stop B1 after resolve, one post-stop reconcile)

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 17 | coordinator / C-pilot / g1 | `wf status pilot-docfix-1` | 0 | ok=True, pending_markers=1, execution=active+uncertain-write | exit 0 → yes | 0.294 |  |
| 18 | builder / PROBE / g1 | `wf append pilot-docfix-1 --type blocker --content {"reason":"probe"}` | 1 | ok=False, code=uncertain-write-pending, seq=9, error=uncertain-write pending: pilot-docfix-1/9/66e20fbad3acc557ad36138a3... | exit 1 code uncertain-write-pending → yes | 0.29 | any append is refused while a write is uncertain |
| 19 | coordinator / C-pilot / g1 | `wf append pilot-docfix-1 --type stop-confirmed --content {"role": "builder", "session": "B1-pilot", "gen...` | 1 | ok=False, code=uncertain-write-pending, seq=9, error=uncertain-write pending: pilot-docfix-1/9/66e20fbad3acc557ad36138a3... | exit 1 code uncertain-write-pending → yes | 0.287 | L49-S1 (Lazy X1): the owner's stop line cannot be recorded straight after the kill |
| 20 | coordinator / C-pilot / g1 | `wf verify pilot-docfix-1` | 0 | ok=True, clean=False | exit 0; clean=False → yes | 0.291 | clean:false while the marker exists |
| 21 | coordinator / C-pilot / g1 | `wf resolve pilot-docfix-1 9 --attempt 66e20fbad3acc557ad36138a3823a4da` | 0 | ok=True, outcome=NOT-COMMITTED, seq=9 | exit 0; outcome='NOT-COMMITTED' → yes | 0.29 | **OWNER TURN (SIMULATED)**: owner accepts COMMITTED or NOT-COMMITTED for the uncertain write (SIMULATED by the agent in rehearsal WF-04... |
| 22 | coordinator / C-pilot / g1 | `wf verify pilot-docfix-1` | 0 | ok=True, clean=True | exit 0; clean=True → yes | 0.289 |  |
| 23 | coordinator / C-pilot / g1 | `wf append pilot-docfix-1 --type stop-confirmed --content {"role": "builder", "session": "B1-pilot", "gen...` | 0 | ok=True, outcome=COMMITTED, seq=9 | exit 0; outcome='COMMITTED' → yes | 0.325 | **OWNER TURN (SIMULATED)**: (e) stop-confirmed: no child process / outstanding operation (SIMULATED by the agent in rehearsal WF-047; i... |
| 24 | coordinator / C-pilot / g1 | `wf recover begin pilot-docfix-1 --reason staged kill during a receipt append (1) --turn owner turn for job pilot-docf...` | 0 | ok=True, outcome=COMMITTED, seq=10, exceptions=[] | exit 0; outcome='COMMITTED'; exceptions=[] → yes | 0.334 | **OWNER TURN (SIMULATED)**: owner turn for job pilot-docfix-1: recover 1 (SIMULATED by the agent in rehearsal WF-047; in the pilot the ... |
| 25 | coordinator / C-pilot / g1 | `wf recover begin pilot-docfix-1 --reason second trigger for the same interruption --turn owner turn for job pilot-doc...` | 1 | ok=False, code=rejected, error=rejected(recovery-open): recovery is already open since seq 10 | exit 1 rejected(recovery-open) → yes | 0.295 | T16: a second recovery trigger for the same interruption is refused |
| 26 | coordinator / C-pilot / g1 | `wf reconcile pilot-docfix-1` | 1 | ok=False, code=rejected, error=rejected(recovery-open): recovery is open since seq 10; only `wf re... | exit 1 rejected(recovery-open) → yes | 0.289 | ordinary reconcile is refused while the recovery is open (as WF-022 row 18) |
| 27 | coordinator / C-pilot / g1 | `wf recover reconcile pilot-docfix-1 --turn owner turn for job pilot-docfix-1: recover 1 (S...` | 0 | ok=True, seq=None | exit 0 → yes | 0.288 | **OWNER TURN (SIMULATED)**: owner turn for job pilot-docfix-1: recover 1 (SIMULATED by the agent in rehearsal WF-047; in the pilot the ... |
| 28 | coordinator / C-pilot / g1 | `wf recover close pilot-docfix-1 --turn owner turn for job pilot-docfix-1: recover 1 (S...` | 0 | ok=True, outcome=COMMITTED, seq=11 | exit 0; outcome='COMMITTED' → yes | 0.332 | **OWNER TURN (SIMULATED)**: owner turn for job pilot-docfix-1: recover 1 (SIMULATED by the agent in rehearsal WF-047; in the pilot the ... |
| 31 | coordinator / C-pilot / g1 | `wf reconcile pilot-docfix-1` | 0 | ok=True, outcome=COMMITTED, seq=12, exceptions=[{'class': 'attempt-without-completion', 'id': 'X1', 'path': 'scope... | exit 0; outcome='COMMITTED'; exceptions=[{'class': 'attempt-without-completion', 'id': 'X1', 'path': 'scope/README.md'}] → yes | 0.328 | the one post-stop reconcile; recover decide cites it (L49-S1: no pre-stop report) |

### D. Retained checkpoint after recovery 1

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 29 | coordinator / C-pilot / g1 | `wf checkpoint pilot-docfix-1` | 0 | ok=True, seq=11 | exit 0 → yes | 0.293 | **OWNER TURN (SIMULATED)**: retained checkpoint (after recovery 1): the owner pastes 'pilot-docfix-1 <seq>:<self>' (SIMULATED by the ag... |
| 30 | coordinator / C-pilot / g1 | `wf verify pilot-docfix-1 --checkpoint 11:fd2e74a68212373e1cd2581f23a9ec7198c592a4bfcd...` | 0 | ok=True, clean=True, checkpoint.status=match (seq 11, head 11) | exit 0; clean=True; checkpoint.status='match' → yes | 0.292 | retained checkpoint after recovery 1 |

### E. Replacement (WF-044 gap a)

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 32 | builder / B1-pilot / g1 | `wf append pilot-docfix-1 --type blocker --content {"reason":"after stop"}` | 1 | ok=False, code=rejected, error=rejected(stopped-session): session 'B1-pilot' was stopped for role ... | exit 1 rejected(stopped-session) → yes | 0.292 | the stopped builder can no longer write |
| 33 | coordinator / C-pilot / g1 | `wf recover decide pilot-docfix-1 --decision resume --turn owner: resume pilot-docfix-1 with a replacement...` | 0 | ok=True, outcome=COMMITTED, decision=resume, seq=13, report_seq=12, stop_seq=9 | exit 0; decision='resume'; report_seq=12; stop_seq=9 → yes | 0.327 | **OWNER TURN (SIMULATED)**: recovery decision 'resume' (SIMULATED by the agent in rehearsal WF-047; in the pilot the owner types this) |
| 34 | coordinator / C-pilot / g1 | `wf bind pilot-docfix-1 --bind-role builder --bind-session B2-pilot --bind-generation 2 --tool Write --tool Edit --pat...` | 0 | ok=True, outcome=COMMITTED, seq=14, binding={'generation': 2, 'role': 'builder', 'session': 'B2-pilot'} | exit 0; binding.session='B2-pilot'; binding.generation=2 → yes | 0.336 |  |
| 37 | builder / B1-pilot / g1 | `wf append pilot-docfix-1 --type blocker --content {"reason":"stale"}` | 1 | ok=False, code=rejected, error=rejected(stale): generation 1 below the bound generation 2 for role... | exit 1 rejected(stale) → yes | 0.288 | T04: the replaced session is stale |
| 38 | builder / B2-pilot / g2 | `wf ack-reconcile pilot-docfix-1 --report 12 --exceptions ["X1"] --note X: the I2 edit is on disk (sha matches the int...` | 0 | ok=True, outcome=COMMITTED, seq=15 | exit 0; outcome='COMMITTED' → yes | 0.33 | D-X1 judgement (who decides is an owner choice, OWNER-DECISIONS line 7) |
| 39 | builder / B2-pilot / g2 | `wf append pilot-docfix-1 --type receipt --content {"path": "scope/README.md", "sha_before": "1b9c...` | 0 | ok=True, outcome=COMMITTED, seq=16 | exit 0; outcome='COMMITTED' → yes | 0.33 |  |

### E. Second replacement triggers (T16; WF-044 gap b)

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 35 | coordinator / C-pilot / g1 | `wf bind pilot-docfix-1 --bind-role builder --bind-session B3-pilot --bind-generation 2 --tool Write --tool Edit --pat...` | 1 | ok=False, code=rejected, error=rejected(bound): role builder is already bound at generation 2 (bin... | exit 1 rejected(bound) → yes | 0.289 | T16: a second replacement at the same generation is refused |
| 36 | coordinator / C-pilot / g1 | `wf bind pilot-docfix-1 --bind-role builder --bind-session B3-pilot --bind-generation 3 --tool Write --tool Edit --pat...` | 1 | ok=False, code=rejected, error=rejected(termination-unconfirmed): session 'B2-pilot' (generation 2... | exit 1 rejected(termination-unconfirmed) → yes | 0.295 | T16: a further replacement without a new stop-confirmed is refused (the stop check comes first, bind.py:412... |

### F. Repair I3

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 40 | builder / B2-pilot / g2 | `wf append pilot-docfix-1 --type intent --content {"path": "scope/PROTOCOL.md", "sha_before": "58...` | 0 | ok=True, outcome=COMMITTED, seq=17 | exit 0; outcome='COMMITTED' → yes | 0.336 |  |
| 41 | builder / B2-pilot / g2 | `wf append pilot-docfix-1 --type receipt --content {"path": "scope/PROTOCOL.md", "sha_before": "58...` | 0 | ok=True, outcome=COMMITTED, seq=18 | exit 0; outcome='COMMITTED' → yes | 0.333 |  |

### G. Second interruption (extra coverage)

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 42 | builder / B2-pilot / g2 | `wf append pilot-docfix-1 --type intent --content {"path": "scope/RESTORE-RUNBOOK.md", "sha_befor...` | 0 | ok=True, outcome=COMMITTED, seq=19 | exit 0; outcome='COMMITTED' → yes | 0.331 |  |
| 43 | builder / B2-pilot / g2 | `wf append pilot-docfix-1 --type receipt (killed)` | 1 |  | killed after the committing marker appeared → yes | 0.597 | staged kill: WF_TEST_FAULTS=delay-precommit:8000, marker after 0.293s, killed (TerminateProcess) |

### G. Recovery 2 (extra coverage)

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 44 | coordinator / C-pilot / g1 | `wf status pilot-docfix-1` | 0 | ok=True, pending_markers=1, execution=active+uncertain-write | exit 0 → yes | 0.303 |  |
| 45 | builder / PROBE / g1 | `wf append pilot-docfix-1 --type blocker --content {"reason":"probe"}` | 1 | ok=False, code=uncertain-write-pending, seq=20, error=uncertain-write pending: pilot-docfix-1/20/18f8334b443d0ccaae003d0a... | exit 1 code uncertain-write-pending → yes | 0.283 | any append is refused while a write is uncertain |
| 46 | coordinator / C-pilot / g1 | `wf verify pilot-docfix-1` | 0 | ok=True, clean=False | exit 0; clean=False → yes | 0.291 | clean:false while the marker exists |
| 47 | coordinator / C-pilot / g1 | `wf resolve pilot-docfix-1 20 --attempt 18f8334b443d0ccaae003d0af68284f5` | 0 | ok=True, outcome=NOT-COMMITTED, seq=20 | exit 0; outcome='NOT-COMMITTED' → yes | 0.292 | **OWNER TURN (SIMULATED)**: owner accepts COMMITTED or NOT-COMMITTED for the uncertain write (SIMULATED by the agent in rehearsal WF-04... |
| 48 | coordinator / C-pilot / g1 | `wf verify pilot-docfix-1` | 0 | ok=True, clean=True | exit 0; clean=True → yes | 0.294 |  |
| 49 | coordinator / C-pilot / g1 | `wf recover begin pilot-docfix-1 --reason staged kill during a receipt append (2) --turn owner turn for job pilot-docf...` | 0 | ok=True, outcome=COMMITTED, seq=20, exceptions=[] | exit 0; outcome='COMMITTED'; exceptions=[] → yes | 0.331 | **OWNER TURN (SIMULATED)**: owner turn for job pilot-docfix-1: recover 2 (SIMULATED by the agent in rehearsal WF-047; in the pilot the ... |
| 50 | coordinator / C-pilot / g1 | `wf recover begin pilot-docfix-1 --reason second trigger for the same interruption --turn owner turn for job pilot-doc...` | 1 | ok=False, code=rejected, error=rejected(recovery-open): recovery is already open since seq 20 | exit 1 rejected(recovery-open) → yes | 0.291 | T16: a second recovery trigger for the same interruption is refused |
| 51 | coordinator / C-pilot / g1 | `wf reconcile pilot-docfix-1` | 1 | ok=False, code=rejected, error=rejected(recovery-open): recovery is open since seq 20; only `wf re... | exit 1 rejected(recovery-open) → yes | 0.284 | ordinary reconcile is refused while the recovery is open (as WF-022 row 18) |
| 52 | coordinator / C-pilot / g1 | `wf append pilot-docfix-1 --type stop-confirmed --content {"role": "builder", "session": "B2-pilot", "gen...` | 1 | ok=False, code=rejected, error=rejected(recovery-open): recovery is open since seq 20; only `wf re... | exit 1 rejected(recovery-open) → yes | 0.289 | L49-S1 (Lazy X3): the owner's stop line cannot be recorded during an open recovery |
| 53 | coordinator / C-pilot / g1 | `wf recover reconcile pilot-docfix-1 --turn owner turn for job pilot-docfix-1: recover 2 (S...` | 0 | ok=True, seq=None | exit 0 → yes | 0.284 | **OWNER TURN (SIMULATED)**: owner turn for job pilot-docfix-1: recover 2 (SIMULATED by the agent in rehearsal WF-047; in the pilot the ... |
| 54 | coordinator / C-pilot / g1 | `wf recover close pilot-docfix-1 --turn owner turn for job pilot-docfix-1: recover 2 (S...` | 0 | ok=True, outcome=COMMITTED, seq=21 | exit 0; outcome='COMMITTED' → yes | 0.329 | **OWNER TURN (SIMULATED)**: owner turn for job pilot-docfix-1: recover 2 (SIMULATED by the agent in rehearsal WF-047; in the pilot the ... |
| 57 | coordinator / C-pilot / g1 | `wf reconcile pilot-docfix-1` | 0 | ok=True, outcome=COMMITTED, seq=22, exceptions=[{'class': 'attempt-without-completion', 'id': 'X1', 'path': 'scope... | exit 0; outcome='COMMITTED'; exceptions=[{'class': 'attempt-without-completion', 'id': 'X1', 'path': 'scope/RESTORE-RUNBOOK.md'}] → yes | 0.326 |  |
| 58 | builder / B2-pilot / g2 | `wf ack-reconcile pilot-docfix-1 --report 22 --exceptions ["X1"] --note X: the I4 edit is on disk; the receipt was los...` | 0 | ok=True, outcome=COMMITTED, seq=23 | exit 0; outcome='COMMITTED' → yes | 0.325 |  |
| 59 | builder / B2-pilot / g2 | `wf append pilot-docfix-1 --type receipt --content {"path": "scope/RESTORE-RUNBOOK.md", "sha_befor...` | 0 | ok=True, outcome=COMMITTED, seq=24 | exit 0; outcome='COMMITTED' → yes | 0.33 |  |

### G. Retained checkpoint after recovery 2

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 55 | coordinator / C-pilot / g1 | `wf checkpoint pilot-docfix-1` | 0 | ok=True, seq=21 | exit 0 → yes | 0.291 | **OWNER TURN (SIMULATED)**: retained checkpoint (after recovery 2): the owner pastes 'pilot-docfix-1 <seq>:<self>' (SIMULATED by the ag... |
| 56 | coordinator / C-pilot / g1 | `wf verify pilot-docfix-1 --checkpoint 21:9bf22e7920dff75fe3713921b08c3a306ee3ac5c05f4...` | 0 | ok=True, clean=True, checkpoint.status=match (seq 21, head 21) | exit 0; clean=True; checkpoint.status='match' → yes | 0.295 | retained checkpoint after recovery 2 |

### H. Repair I5

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 60 | builder / B2-pilot / g2 | `wf append pilot-docfix-1 --type intent --content {"path": "scope/PROTOCOL.md", "sha_before": "57...` | 0 | ok=True, outcome=COMMITTED, seq=25 | exit 0; outcome='COMMITTED' → yes | 0.333 |  |
| 61 | builder / B2-pilot / g2 | `wf append pilot-docfix-1 --type receipt --content {"path": "scope/PROTOCOL.md", "sha_before": "57...` | 0 | ok=True, outcome=COMMITTED, seq=26 | exit 0; outcome='COMMITTED' → yes | 0.328 |  |

### H. Deliver

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 62 | builder / B2-pilot / g2 | `wf append pilot-docfix-1 --type delivered --content {"claims": [{"claim": "I1-I5 restored after two...` | 0 | ok=True, outcome=COMMITTED, seq=27 | exit 0; outcome='COMMITTED' → yes | 0.333 |  |

### H. Replacement max reached (WF-044 gap b)

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 63 | coordinator / C-pilot / g1 | `wf append pilot-docfix-1 --type stop-confirmed --content {"role": "builder", "session": "B2-pilot", "gen...` | 0 | ok=True, outcome=COMMITTED, seq=28 | exit 0; outcome='COMMITTED' → yes | 0.326 | **OWNER TURN (SIMULATED)**: (e) stop-confirmed for B2 (SIMULATED by the agent in rehearsal WF-047; in the pilot the owner types this) |
| 64 | coordinator / C-pilot / g1 | `wf reconcile pilot-docfix-1` | 0 | ok=True, outcome=COMMITTED, seq=29, exceptions=[] | exit 0; outcome='COMMITTED'; exceptions=[] → yes | 0.331 | post-stop reconcile for B2 |
| 65 | coordinator / C-pilot / g1 | `wf recover decide pilot-docfix-1 --decision resume --turn owner: resume pilot-docfix-1 with a second repl...` | 0 | ok=True, outcome=COMMITTED, decision=resume, seq=30, report_seq=29, stop_seq=28 | exit 0; decision='resume'; report_seq=29; stop_seq=28 → yes | 0.333 | **OWNER TURN (SIMULATED)**: recovery decision 'resume' for B2 (SIMULATED by the agent in rehearsal WF-047; in the pilot the owner types... |
| 66 | coordinator / C-pilot / g1 | `wf bind pilot-docfix-1 --bind-role builder --bind-session B3-pilot --bind-generation 3 --tool Write --tool Edit --pat...` | 1 | ok=False, code=rejected, error=rejected(delegation): replacement count 1 for builder has reached m... | exit 1 rejected(delegation); error=~'replacement count 1 for builder has reached max 1' → yes | 0.292 | WF-044 gap b / L49-S3: with a stop, a post-stop report and a resume decision, the grant's replacement.max (... |

### H. Review

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 67 | coordinator / C-pilot / g1 | `wf bind pilot-docfix-1 --bind-role reviewer --bind-session R1-pilot --bind-generation 1 --tool Read --path scope/** -...` | 0 | ok=True, outcome=COMMITTED, seq=31, binding={'generation': 1, 'role': 'reviewer', 'session': 'R1-pilot'} | exit 0; binding.role='reviewer' → yes | 0.337 |  |
| 68 | harness | check-acceptance.ps1 (reviewer; AC1..AC6 complete or the run stops) | — | {"exit": 0, "AC1": "True", "AC2": "True", "AC3": "True", "AC4": "True", "AC5": "True", "AC6": "True"} | — | — | |

### H. Accept

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 69 | reviewer / R1-pilot / g1 | `wf accept pilot-docfix-1 --results C:\src\claude-workforce\.claude\worktrees\wf047...` | 0 | ok=True, outcome=COMMITTED, decision=accepted, seq=32 | exit 0; outcome='COMMITTED'; decision='accepted' → yes | 0.346 |  |

### I. Checkpoint (WF-044 gap c)

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 70 | coordinator / C-pilot / g1 | `wf checkpoint pilot-docfix-1` | 0 | ok=True, seq=32 | exit 0 → yes | 0.293 | **OWNER TURN (SIMULATED)**: retained checkpoint (before close): the owner pastes 'pilot-docfix-1 <seq>:<self>' (SIMULATED by the agent ... |
| 71 | coordinator / C-pilot / g1 | `wf verify pilot-docfix-1 --checkpoint 32:3863debe35141ff3004a2f9d357fc6e7c5e32eb3b2d5...` | 0 | ok=True, clean=True, checkpoint.status=match (seq 32, head 32) | exit 0; clean=True; checkpoint.status='match' → yes | 0.294 | retained checkpoint before close |
| 72 | coordinator / C-pilot / g1 | `wf verify pilot-docfix-1 --checkpoint 32:00000000000000000000000000000000000000000000...` | 0 | ok=True, clean=False, checkpoint.status=mismatch (seq 32, head 32) | exit 0; clean=False; checkpoint.status='mismatch' → yes | 0.294 | an altered retained value is reported as a mismatch (clean false) with exit 0 |

### I. Close

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 73 | coordinator / C-pilot / g1 | `wf close pilot-docfix-1 --intervention owner-present recovery twice (resolve, recover ...` | 0 | ok=True, outcome=COMMITTED, seq=33 | exit 0; outcome='COMMITTED' → yes | 0.341 | **OWNER TURN (SIMULATED)**: OT4 intervention declaration, minutes unknown (SIMULATED by the agent in rehearsal WF-047; in the pilot the... |
| 74 | coordinator / C-pilot / g1 | `wf export pilot-docfix-1` | 0 | ok=True, code=ok | exit 0; code='ok' → yes | 0.315 |  |
| 75 | coordinator / C-pilot / g1 | `wf checkpoint pilot-docfix-1` | 0 | ok=True, seq=33 | exit 0 → yes | 0.294 | **OWNER TURN (SIMULATED)**: retained checkpoint (final, after close): the owner pastes 'pilot-docfix-1 <seq>:<self>' (SIMULATED by the ... |
| 76 | coordinator / C-pilot / g1 | `wf verify pilot-docfix-1 --checkpoint 33:2ca6f04db2897ecea4b98ba80fc64baccae1b056ebd0...` | 0 | ok=True, clean=True, checkpoint.status=match (seq 33, head 33) | exit 0; clean=True; checkpoint.status='match' → yes | 0.295 | retained checkpoint final, after close |
| 81 | coordinator / C-pilot / g1 | `wf digest pilot-docfix-1` | 0 | ok=True, pending_markers=0, execution=delivered | exit 0; pending_markers=[] → yes | 0.296 | **OWNER TURN (SIMULATED)**: OT5 the owner reads the digest (SIMULATED by the agent in rehearsal WF-047; in the pilot the owner types this) |

### I. Re-verify every retained checkpoint at close (G-28)

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 77 | coordinator / C-pilot / g1 | `wf verify pilot-docfix-1 --checkpoint 11:fd2e74a68212373e1cd2581f23a9ec7198c592a4bfcd...` | 0 | ok=True, clean=True, checkpoint.status=match (seq 11, head 33) | exit 0; clean=True; checkpoint.status='match' → yes | 0.289 | G-28 at close: re-verify the retained checkpoint 'after recovery 1' against the closed chain (L49-S4) |
| 78 | coordinator / C-pilot / g1 | `wf verify pilot-docfix-1 --checkpoint 21:9bf22e7920dff75fe3713921b08c3a306ee3ac5c05f4...` | 0 | ok=True, clean=True, checkpoint.status=match (seq 21, head 33) | exit 0; clean=True; checkpoint.status='match' → yes | 0.292 | G-28 at close: re-verify the retained checkpoint 'after recovery 2' against the closed chain (L49-S4) |
| 79 | coordinator / C-pilot / g1 | `wf verify pilot-docfix-1 --checkpoint 32:3863debe35141ff3004a2f9d357fc6e7c5e32eb3b2d5...` | 0 | ok=True, clean=True, checkpoint.status=match (seq 32, head 33) | exit 0; clean=True; checkpoint.status='match' → yes | 0.293 | G-28 at close: re-verify the retained checkpoint 'before close' against the closed chain (L49-S4) |
| 80 | coordinator / C-pilot / g1 | `wf verify pilot-docfix-1 --checkpoint 33:2ca6f04db2897ecea4b98ba80fc64baccae1b056ebd0...` | 0 | ok=True, clean=True, checkpoint.status=match (seq 33, head 33) | exit 0; clean=True; checkpoint.status='match' → yes | 0.29 | G-28 at close: re-verify the retained checkpoint 'final, after close' against the closed chain (L49-S4) |

### I. Deleted last row, on a copy of the root

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 82 | harness | copy of the root: delete trigger dropped, last row deleted, trigger restored ... | — | {"copy": "wf047-f1-x4b", "deleted_seq": 33, "final_checkpoint_seq": 33, "trigger_restored": true} | — | — | |
| 83 | coordinator / C-pilot / g1 | `wf export pilot-docfix-1 (--db: the copy)` | 0 | ok=True, code=ok | exit 0; code='ok' → yes | 0.311 | export regenerated on the copy, so the export manifest matches the shortened chain |
| 84 | coordinator / C-pilot / g1 | `wf verify pilot-docfix-1 (--db: the copy)` | 0 | ok=True, clean=True | exit 0; clean=True → yes | 0.303 | documented limit: plain verify cannot see a deleted last row |
| 85 | coordinator / C-pilot / g1 | `wf verify pilot-docfix-1 --checkpoint 33:2ca6f04db2897ecea4b98ba80fc64baccae1b056ebd0... (--db: the copy)` | 0 | ok=True, clean=False, checkpoint.status=missing (seq 33, head 32) | exit 0; clean=False; checkpoint.status='missing' → yes | 0.299 | the retained final checkpoint reports the lost tail |
| 86 | coordinator / C-pilot / g1 | `wf verify --restored C:\src\claude-workforce\.claude\worktrees\wf047... (--db: the copy)` | 0 | ok=True, clean=True, error=None | exit 0; clean=True → yes | 0.348 | restored-copy check without a checkpoint: the same limit |
| 87 | coordinator / C-pilot / g1 | `wf verify --restored C:\src\claude-workforce\.claude\worktrees\wf047... --job pilot-docfix-1 --checkpoint 33:2ca6f04d...` | 0 | ok=True, clean=False, error=None, checkpoint.status=missing (seq 33, head 32) | exit 0; clean=False; checkpoint.status='missing' → yes | 0.351 | restored-copy check with the retained final checkpoint |

### End

| # | Role / session / gen | Command | Exit | Result | Expected | s | Owner turn / note |
|---|---|---|---|---|---|---|---|
| 88 | harness | summary | — | {"calls": 81, "calls_with_field_checks": 57, "without_db": [2], "retained_checkpoints": ["after recovery 1", "after recovery 2", "before close", "final, after close"], "unexpected": [], "scope_equals_pristine": true} | — | — | |
