# WF-022 pilot rehearsal: acceptance-list draft (FIRST-RELEASE-SPEC §12, candidate (b))

Status: **DRAFT for the owner, not an agreed acceptance list.** §12 says the pilot is chosen "only after current-state inspection and an agreed acceptance list (owner-only)". This draft is the input to that owner decision. It authorizes nothing. Rehearsal only, not a pilot.

## Job

- **Name:** `wf022-docfix`. A documentation-consistency repair inside a copied folder.
- **Boundary root:** `%LOCALAPPDATA%\wf022\` (disposable; the ledger sits at `ledger\wf.db` under it).
- **Write scope:** `scope/**`, three copied documents:

| Scope file | Repository source at `2dedc942` | Pristine sha256 |
|---|---|---|
| `scope/README.md` | `tools/wf/README.md` | `7a73bdaa1e9e9b5ff031202a908f15d0f16b83856fe96c4d0ebf5a9ebddcb294` |
| `scope/PROTOCOL.md` | `protocol/PROTOCOL.md` | `7d10f02709cd548002a9992b9787301555491590c943d8fbf656d498c2a118d2` |
| `scope/RESTORE-RUNBOOK.md` | `tools/wf/docs/RESTORE-RUNBOOK.md` | `66b3e5c2a37ceae746ce6c887a64e761b5e9eda2d50248171f4e2724aa0b2cb4` |

- **Read-only reference:** `pristine/` (byte copies, never edited).
- **Built by:** `fixtures/results/wf022-zen/make_scope.py`. The record of what was built is `scope-record.json` under the boundary root.

## The five planted inconsistencies (the repair targets)

| Id | File:line | Kind | Correct text | Planted text |
|---|---|---|---|---|
| I1 | scope/README.md:53 | wrong file name | `` `wfcore\db.py` `` | `` `wfcore\dbase.py` `` |
| I2 | scope/README.md:30 | wrong command name | `` | `resolve <job> <seq> `` | `` | `settle <job> <seq> `` |
| I3 | scope/PROTOCOL.md:86-87 | stale heading reference | `README, "Attempt ids and pending-write markers"` | `README, "Attempt identifiers and pending markers"` |
| I4 | scope/RESTORE-RUNBOOK.md:22 | wrong option name | `verify --restored <restored copy>` | `verify --restore <restored copy>` |
| I5 | scope/PROTOCOL.md:5 | wrong file name | `` `tools/wf/README.md` `` | `` `tools/wf/READ-ME.md` `` |

## Acceptance criteria

Each criterion is one objectively checkable check. `check-acceptance.ps1` (next to this file) runs AC1–AC6 read-only from the boundary root and prints `ACn True|False`; exit 0 only when all hold. It was validated on 2026-10-18: all six print False on the planted scope and True on the pristine copy. The reviewer records each result in the `accept --results` file. `wf accept` checks only what that file claims; it does not run the checks (README, Identity and limits).

| Id | Criterion | How it is checked |
|---|---|---|
| AC1 | README names the storage module by its real file name (I1) | `` `wfcore\db.py` `` appears once and `dbase.py` never, in scope/README.md |
| AC2 | Every command in README's command table is a real `wf` subcommand (I2) | the row starting `` | `resolve <job> <seq> `` appears once and `` `settle `` never |
| AC3 | PROTOCOL's quoted README section names an existing README heading (I3) | PROTOCOL contains `README, "Attempt ids and pending-write markers"` (a line break is allowed) and README has the heading `## Attempt ids and pending-write markers…` |
| AC4 | The runbook's verify option exists in the CLI (I4) | `verify --restored <restored copy>` appears once and `verify --restore <` never; `wf verify --help` lists `--restored PATH` |
| AC5 | PROTOCOL names the README by its real repository path (I5) | `` `tools/wf/README.md` `` appears once and `READ-ME.md` never |
| AC6 | Nothing else in the scope changed | each scope file's SHA-256 equals its pristine copy's |
| AC7 | Nothing outside the write scope changed | the `wf accept` changeset: `changed_paths` lists only `scope/**`, and `undeclared` is empty |

AC1–AC5 are each satisfied by exactly one restored line. AC6 makes the repair unambiguous: a fix that is "correct" in spirit but not byte-identical fails it. That is deliberate, so acceptance is objective. AC7 uses the delivered changeset (spec §4.4) rather than a manual listing.

## §12 criteria, one by one

| §12 criterion | How this job meets it | Evidence |
|---|---|---|
| local | Scope, ledger and boundary root are on Zen under `%LOCALAPPDATA%\wf022`; no network | this document; RUNBOOK-EXECUTED.md |
| reversible | The scope is a copy; `pristine/` and the repository originals are never written; deleting the root undoes everything | `scope-record.json` (repo and pristine hashes equal) |
| disposable copy as scope | `scope/**` holds copies of three repository files | table above |
| objectively checkable acceptance | AC1–AC7 are command checks with a True/False result; AC6 is a hash equality | table above |
| no secrets | The three documents contain no credentials; the ledger holds only fixture text | `make_scope.py` source list |
| not AIEP/BIMpossible production | Workforce's own documentation, copied; no AIEP or BIMpossible path | file list |
| small enough for one builder context | 3 files, 284 lines, 5 one-line edits | `wc -l` at step 2 |
| includes a step whose result can be ambiguous after a kill | The repair is recorded as one intent → edit → receipt per inconsistency. A kill after an intent and before its receipt leaves an intent with no receipt and a file that may or may not be edited. That is exactly what `wf reconcile` classifies (attempt-without-completion / matches-receipt / matches-baseline). Rehearsed in step 4. | RUNBOOK-EXECUTED.md, interruption section |

## Open for the owner (not decided here)

- Whether candidate (b) is the pilot (§12: owner-only).
- Whether AC6's byte-identity standard is wanted, or a looser "only the five lines changed" check.
- Who plays which role in the real pilot (coordinator, builder, reviewer sessions; spec §2) and the grant's exact quote.
- Whether `pristine/` is visible to the builder; a builder that can read it can "repair" by copying.
