[CmdletBinding()]
param([switch]$LocalOnly)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Repo '.venv-worker\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Run setup-windows.ps1 first.' }
Push-Location $Repo
try {
    $Arguments = @('-m', 'shortpost_worker.network_preflight')
    if (-not $LocalOnly) { $Arguments += '--report-server' }
    & $Python @Arguments
    $ResultCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $ResultCode
