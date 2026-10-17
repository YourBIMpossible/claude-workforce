# Restore runbook: owner procedure (B2 slice 5, INTERFACE-CONTRACT-S5 section 2)

Restore is an **owner procedure**. There is no `wf restore`, and `wf` never overwrites, repairs or deletes a ledger file. `wf` only checks copies, read-only.

## Before you need it
1. **Take backups with `wf backup`.**
   ```
   python -I tools\wf\wf.py --role owner backup --all --to <new file outside the ledger directory>
   ```
   - The command refuses an existing destination, a missing parent directory, or a destination inside the ledger directory. A refusal writes nothing.
   - Add `--writer-stopped` only when every `wf` process and session is stopped. Head equality with the live ledger is asserted only then.
2. **Retain a checkpoint independently.**
   - Run `wf checkpoint <job>` and keep the `seq:self` value somewhere the ledger's writers cannot change, such as a note outside the repository.
   - Without a retained checkpoint, a consistent whole-chain rewrite or a tail deletion is **not detectable**.

## Restoring
1. **Stop every process** that can write the ledger: all `wf` commands, worker sessions and gate hooks.
2. **Preserve the damaged file.** Leave the live ledger exactly where it is. Do not move, edit or delete it. It is evidence.
3. **Restore into a separate location.** Copy the chosen backup to a new path outside the live ledger directory.
4. **Verify the restored copy with the retained checkpoint.**
   ```
   python -I tools\wf\wf.py verify --restored <restored copy> --job <job> --checkpoint <seq:self>
   ```
   Accept it only when **all** of these hold:
   - `clean` is true;
   - `integrity` is true;
   - every `chains` entry is empty;
   - `checkpoint.status` is `matches`.

   A status of `differs` means the copy was rewritten. A status of `missing` means the copy is truncated before the checkpoint.
5. **Without a checkpoint**, the result says the copy is "internally consistent only". Treat that as unproven, not as a match.
6. **Switching the live ledger to the verified copy is the owner's own action**, taken with every process still stopped. Keep the damaged file and the backup. Record what you did in the job's intervention log once `wf` runs again.

## Limits
- A checkpoint you supply is trusted as supplied. An altered checkpoint is not detectable by `wf`.
- `wf verify --restored` exits 0 whenever it could run. Read `clean` and `checkpoint.status`; the exit code alone does not accept a copy.
- Manual recovery does not establish unattended operation.
