# Filesystem qualification — link non-following (baseline scan, export inventory, ledger-directory verify)

Prep by MyBuddy (2026-10-16) for White's Windows re-execution. Namespace proposed, registration pending (OWNERS.md).
Product code is untouched; the probe drives the existing surfaces and records what they do with real links.

## What it answers
For each link kind × surface: is the link **reported** (not silently dropped), and is the external target
**not traversed, read or modified** while the surface runs? Evidence classes are kept apart:

| Kind | Host | Fixture | Repeat command |
|---|---|---|---|
| `linux-symlink` | POSIX | `os.symlink` (file and dir) | `python -c "import os; os.symlink(r'<target>', r'<link>', target_is_directory=<bool>)"` |
| `windows-symlink` | win32 | `os.symlink` — needs SeCreateSymbolicLinkPrivilege or Developer Mode; **not enabled by the probe**; failure recorded as `skip` with the WinError | same |
| `windows-junction` | win32 | `cmd /c mklink /J` (no privilege) | `cmd /c mklink /J "<link>" "<target-dir>"` |

Surfaces: `changeset.baseline` (`skipped link or reparse point: tools/wf/link`), `export.check`
(`unexpected export link: link-entry`), `commands.verify` (`ledger_directory.problems` names `link.db@`; for a
junction Python does not report `is_symlink`, so the observed form — `link.db/` or `link.db@` — is recorded, and the
pass criterion is: exactly one problem naming the entry, `neighbors == []`, nothing traversed).

## Observation mechanism (executable; a hash alone is not used to claim "never read")
1. `sys.addaudithook` armed only around the call: `open`, `os.scandir`, `os.listdir`, `os.remove/unlink/rename/
   replace/rmdir/mkdir/chmod/truncate`, `shutil.*`, `sqlite3.connect`. Any event whose path resolves under the
   synthetic target directory is a hit. Events outside the scratch tree are listed too.
2. POSIX canary: sentinel `chmod 000` during the call (not on Windows; ACLs are out of scope).
3. Sentinel bytes (sha256), identity (`st_dev`, `st_ino`, `st_mtime_ns`) and target listing before/after.
4. Negative control `control/read-through-link`: a deliberate read through a link must be caught by the hook and
   blocked by the canary; if the control fails the run exits 1 and no "not accessed" statement is supportable.

Claim when all agree: *the target was not opened, listed or changed by Python-level file operations during the
call*. Not claimed: kernel-level access; behaviour on another OS/filesystem; Windows qualification from Linux output.

## Run (from the repository root; stdlib only)
```
python -I fixtures/filesystem_qualification/probe_links.py
```
Options: `--scratch <fresh dir>` (default `fixtures/tmp/fsq-<utc>-<hex>`, git-ignored, left in place for inspection),
`--out <dir>` (default `results/` here), `--kinds`, `--surfaces`. Exit 0 = no case failed; 1 = a failure or a
mechanism-control failure; 2 = refused (scratch exists). Result file: `results/<sys.platform>-<rev12>.json`,
schema 1: `environment` (revision/head, branch, dirty flag, OS, filesystem type, Python, SQLite, privilege state
read-only), `link_capability` (what this account could create, exact errors), `cases[]` (status
`pass|fail|skip|not-applicable`, reason, product output, observation, checks, claim, repeat command),
`qualification_scope` (`evidence_for`, `not_tested`, `mechanism_control`).

## Windows re-execution (White)
Same command, Windows 11 / Python 3.14, as the normal (non-elevated) account, Developer Mode untouched. Expected:
`windows-junction/*` run; `windows-symlink/*` either run (if the account already holds the privilege) or `skip`
with `WinError 1314` recorded; `linux-symlink/*` `not-applicable`. Commit the resulting `results/win32-<rev12>.json`
beside the Linux file. Rows T35 (G-13, G-29) in the coverage map can cite the per-kind `status` only; `skip` and
`not-applicable` are not evidence.

## Existing tests this complements (not duplicated)
`test_t35_changeset.py::test_symlink_is_skipped_and_recorded_never_followed`, `test_t35_stray_files.py::
test_symlink_entry_reported_without_following`, `test_t35_verify_ledger_dir.py::
test_symlink_in_ledger_directory_is_reported_and_not_followed`, `test_export_temp_preservation.py::
test_symlink_at_fixed_tmp_not_followed_or_overwritten` (all `skipTest` where symlinks cannot be created — the probe
records that capability and the exact reason instead), `test_t35_windows_resolution.py` (NTFS resolution facts and
`mklink /J` junction fixture; win32 only; W2 8.3 names stay skipped — nothing enabled).
