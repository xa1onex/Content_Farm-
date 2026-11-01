[CmdletBinding()]
param([switch]$Live, [string]$Config)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
if (-not $Config) { $Config = Join-Path $Repo 'worker\config.local.toml' }
$Executable = Join-Path $Repo '.venv-worker\Scripts\shortpost-worker.exe'
if (-not (Test-Path $Executable)) { throw 'Run setup-windows.ps1 first' }
if ($Live) { & $Executable --config $Config run --live }
else { & $Executable --config $Config run }
exit $LASTEXITCODE
