[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][guid]$JobId,
    [string]$ExternalId = 'mumu-01'
)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
$ReelsProfile = Join-Path $Repo 'worker\profiles\instagram-452.0.0.0.43-ru-RU.reels.json'
$Worker = Join-Path $Repo '.venv-worker\Scripts\shortpost-worker.exe'
& $Worker run-once --job-id ($JobId.ToString()) --external-id $ExternalId --profile $ReelsProfile --live
exit $LASTEXITCODE
