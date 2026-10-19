# WF-029 COVERAGE (Lazy)
Each WF-020 REMAINING-GAPS row (80), mapped from 2dedc94 to line numbers at fix1/lazy-ids 5c47587 and at 96fb5c4 (difflib per file; 8 rows remapped by text where the WF-020 inventory line number was wrong). For each row: whether that line executed in the full suite (stdlib sys.monitoring, in-process; subprocess-run code is not seen), at base 5c47587 and with the test_wf029_* files. Test docstrings cite the 96fb5c4 line numbers.
Base 5c47587: 4 of 79 rows with a location executed. After: 62 of 79. Row 75 has no location (closed by FIX1 E3). The same counts held on the earlier base 96fb5c4 (wf029/lazy a5bf9ad).

| row | file:line (5c47587) | line at 96fb5c4 | 2dedc94 line | label | base | after |
|---|---|---|---|---|---|---|
| 1 | commands.py:245 | 219 | 157 | WfRefused `job <j> already exists` | no | no |
| 2 | commands.py:255 | 229 | 167 | WfRefused `changed-set baseline could not be captured (fail closed...)` | no | yes |
| 3 | commands.py:336 | 310 | 245 | WfRefused `proposed grant row ... not readable` | no | yes |
| 4 | commands.py:727 | 701 | 636 | WfUsage `--checkpoint requires a job argument` | no | yes |
| 5 | commands.py:801 | 775 | 710 | problem `ledger directory unreadable: <Exc>`; `clean:false` | no | yes |
| 6 | commands.py:904 | 878 | 813 | reason `changed-set boundary cannot be established ... fail closed` => decision  | no | yes |
| 7 | commands.py:913 | 887 | 822 | reason `_NO_BASELINE` (`baseline evidence missing ... fail closed`) => decision  | no | yes |
| 8 | commands.py:918 | 892 | 827 | reason `changed-set comparison incomplete (fail closed)` => decision rejected | no | yes |
| 9 | commands.py:937 | 911 | 846 | WfRefused `job has no contract` | no | no |
| 10 | commands.py:973 | 947 | 882 | decision `rejected`, reason `criteria failed or missing: ...` | no | yes |
| 11 | commands.py:976 | 950 | 885 | decision `rejected`, reason `results name criteria not in the contract` | no | yes |
| 12 | commands.py:1007 | 981 | 916 | WfRefused `close refused: pending markers present` | yes | yes |
| 13 | commands.py:1050 | 1024 | 959 | WfRefused `job has no contract` | no | no |
| 14 | commands.py:1081 | 1055 | 990 | WfRefused `changed-set boundary cannot be established ... (no event recorded)` | no | yes |
| 15 | commands.py:1089 | 1063 | 998 | WfRefused `job has no contract` | no | no |
| 16 | commands.py:1094 | 1068 | 1003 | WfRefused `_NO_BASELINE` | no | yes |
| 17 | commands.py:1142 | 1116 | 1051 | WfRefused `job has no contract` | no | no |
| 18 | commands.py:1180 | 1154 | 1089 | WfRefused `second-writer probe could not open a second connection` | no | yes |
| 19 | commands.py:1192 | 1166 | 1101 | WfRefused `second-writer probe failed for another reason`, second_writer=error | no | yes |
| 20 | commands.py:1199 | 1173 | 1108 | WfRefused `second-writer: ... obtained the reservation`, second_writer=acquired | no | yes |
| 21 | commands.py:1229 | 1203 | 1138 | WfRefused `ledger not readable (...)` | no | yes |
| 22 | commands.py:1274 | 1248 | 1183 | WfRefused `backup module not present on this tree` | no | yes |
| 23 | commands.py:1341 | 1315 | 1250 | WfRefused `job has no contract` | no | no |
| 24 | commands.py:1371 | 1345 | 1280 | WfRefused `job has no contract` | no | no |
| 25 | commands.py:1438 | 1412 | 1347 | WfRefused `job has no contract` | no | no |
| 26 | commands.py:1473 | 1447 | 1382 | WfRefused `job has no contract` | no | no |
| 27 | commands.py:1527 | 1501 | 1436 | WfRefused `job has no contract` | no | no |
| 28 | commands.py:1584 | 1558 | 1493 | WfRefused `observation log not written: ...` | no | yes |
| 29 | bind.py:345 | 345 | 345 | rejected(unbound-session) | no | no |
| 30 | bind.py:347 | 347 | 347 | rejected(stopped-session) | no | no |
| 31 | bind.py:540 | 540 | 540 | rejected(invalid-stop) | no | no |
| 32 | grant.py:212 | 212 | 212 | state malformed / field:block_hash | no | no |
| 33 | db.py:202 | 202 | 202 | WfLockTimeout (code lock-timeout) "reservation not obtained" | no | yes |
| 34 | txn.py:169 | 169 | 169 | WfRejected conflict-unrecorded "COMMIT raised <name>" | no | yes |
| 35 | txn.py:170 | 170 | 170 | WfCommitUncertain "COMMIT raised <name>" | no | yes |
| 36 | txn.py:178 | 178 | 178 | WfRejected conflict-unrecorded "COMMIT busy past N s, no marker" | no | yes |
| 37 | txn.py:192 | 192 | 192 | WfCommitUncertain "give-up: ROLLBACK raised" (conflict-pending) | no | yes |
| 38 | txn.py:186 | 186 | 186 | oplog marker_conflict_pending_failed (non-fatal) | no | yes |
| 39 | txn.py:314 | 314 | 314 | WfCommitUncertain "COMMIT raised ..." | no | yes |
| 40 | txn.py:387 | 387 | 386 | RESERVATION-NOT-OBTAINED (connect) | no | yes |
| 41 | txn.py:395 | 395 | 394 | RESERVATION-NOT-OBTAINED (begin) | no | yes |
| 42 | txn.py:402 | 402 | 401 | outcome ALREADY-CLEARED | no | yes |
| 43 | txn.py:416 | 416 | 415 | STILL-UNCERTAIN "resolve budget exceeded" / "reservation not held at decision" | no | yes |
| 44 | txn.py:420 | 420 | 419 | STILL-UNCERTAIN "chain invalid" | no | yes |
| 45 | txn.py:431 | 431 | 430 | STILL-UNCERTAIN "quick_check not ok" | no | yes |
| 46 | txn.py:447 | 447 | 446 | STILL-UNCERTAIN "conflict marker self does not match..." | no | yes |
| 47 | txn.py:471 | 471 | 470 | STILL-UNCERTAIN "conflict row re-record did not COMMIT..." | no | yes |
| 48 | txn.py:479 | 479 | 478 | STILL-UNCERTAIN "marker vanished before delete..." | no | yes |
| 49 | txn.py:483 | 483 | 482 | STILL-UNCERTAIN "delete failed: ..." | no | yes |
| 50 | backup.py:87 | 87 | 87 | re-raise busy OperationalError (WfLockTimeout path) | no | yes |
| 51 | backup.py:112 | 112 | 112 | WfRefused "dest-exists" (race) | no | yes |
| 52 | backup.py:114 | 114 | 114 | WfRefused "dest-parent-missing" (race) | no | yes |
| 53 | backup.py:121 | 121 | 121 | WfError "engine backup failed ..." | no | yes |
| 54 | backup.py:141 | 141 | 141 | result head_in_source false | no | no |
| 55 | reconcile.py:508 | 508 | 508 | rejected(malformed) "chain_head must be {seq, self}" | no | yes |
| 56 | wf.py:286 | 286 | 279 | `int(exc.code or 0)`; non-int code -> EXIT_USAGE | yes | yes |
| 57 | wf.py:244 | 244 | 237 | WfUsage "unknown command" (pragma no cover) | no | no |
| 58 | wf.py:260 | 260 | 253 | auto_resolve {"outcome":"ERROR"} | no | yes |
| 59 | validate.py:397 | 387 | 367 | rejected(role-blocked) accept refused while roles blocked | no | no |
| 60 | export.py:128 | 128 | 128 | same label, foreign dead pid | no | yes |
| 61 | export.py:190 | 190 | 190 | problem "unexpected export entry: <name>" | no | yes |
| 62 | export.py:192 | 192 | 192 | same label on OSError while classifying entry | no | yes |
| 63 | manifest.py:32 | 32 | 27 | ValueError "sha256 must be 64 lowercase hex" | no | yes |
| 64 | manifest.py:68 | 68 | 59 | ManifestMalformed "whitespace-only line" | no | yes |
| 65 | pathpolicy.py:115 | 115 | 115 | resolve problem "not a string" | no | yes |
| 66 | changeset.py:134 | 134 | 134 | limitation "skipped link or reparse point: <rel>" (file entry) | no | yes |
| 67 | changeset.py:155 | 155 | 155 | same limitation (scope prefix itself is a link) | no | yes |
| 68 | changeset.py:138 | 138 | 138 | problem "unresolvable path inside the boundary: <rel> (<why>)" | no | yes |
| 69 | changeset.py:77 | 77 | 77 | `_is_link` True on lstat OSError | no | yes |
| 70 | commands.py:255 | 229 | 167 | WfRefused "changed-set baseline could not be captured (fail closed...)" | no | yes |
| 71 | markers.py:156 | 156 | 386 | UncertainWritePending "marker malformed" | no | yes |
| 72 | markers.py:57 | 57 | 288 | PermissionError re-raised after 0.5 s | no | yes |
| 73 | markers.py:81 | 81 | 311 | PermissionError re-raised after 0.5 s in read() | no | yes |
| 74 | markers.py:211 | 211 | 411 | describe() state label "unreadable" | yes | yes |
| 75 | gone | gone | 301 | json ValueError propagates (no handler) | - | - |
| 76 | faults.py:41 | 41 | 34 | ValueError from `int(arg)` in `sleep_ms` | yes | yes |
| 77 | gatecore.py:345 | 344 | 337 | malformed-payload | no | yes |
| 78 | gatecore.py:217 | 216 | 209 | ledger-unreadable | no | yes |
| 79 | gatecore.py:358 | 357 | 350 | ledger-unreadable | no | yes |
| 80 | gatecore.py:389 | 388 | 381 | ledger-locked / ledger-unreadable | no | yes |
