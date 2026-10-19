<#
  WF-022 acceptance checks AC1-AC6 for the wf022-docfix rehearsal job (ACCEPTANCE-LIST-DRAFT.md).
  Read-only. Run in Windows PowerShell 5.1:  powershell -NoProfile -File check-acceptance.ps1 -Root <boundary root> [-Scope scope]
  Prints one line per criterion, "ACn True|False", and exits 0 only when all are True. AC7 is read from `wf accept`.
#>
param([Parameter(Mandatory)][string]$Root, [string]$Scope = 'scope')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$s = Join-Path $Root $Scope; $p = Join-Path $Root 'pristine'
function Raw([string]$f) { "$(Get-Content -LiteralPath (Join-Path $s $f) -Raw)" }
function Count([string]$f, [string]$lit) { @(Select-String -LiteralPath (Join-Path $s $f) -SimpleMatch -Pattern $lit).Count }
$r = [ordered]@{
    AC1 = ((Count 'README.md' '`wfcore\db.py`') -eq 1) -and ((Count 'README.md' 'dbase.py') -eq 0)
    AC2 = ((Count 'README.md' '`settle ') -eq 0) -and ((Count 'README.md' '| `resolve <job> <seq>') -eq 1)
    AC3 = ((Raw 'PROTOCOL.md') -match 'README, "Attempt ids and pending-write\s+markers"') -and ((Raw 'README.md') -match '(?m)^## Attempt ids and pending-write markers')
    AC4 = ((Count 'RESTORE-RUNBOOK.md' 'verify --restored <restored copy>') -eq 1) -and ((Count 'RESTORE-RUNBOOK.md' 'verify --restore <') -eq 0)
    AC5 = ((Count 'PROTOCOL.md' '`tools/wf/README.md`') -eq 1) -and ((Count 'PROTOCOL.md' 'READ-ME.md') -eq 0)
    AC6 = (@('README.md', 'PROTOCOL.md', 'RESTORE-RUNBOOK.md' | Where-Object { (Get-FileHash -LiteralPath (Join-Path $s $_)).Hash -ne (Get-FileHash -LiteralPath (Join-Path $p $_)).Hash }).Count -eq 0)
}
foreach ($k in $r.Keys) { "$k $($r[$k])" }
if (@($r.Values | Where-Object { -not $_ }).Count) { exit 1 } else { exit 0 }
