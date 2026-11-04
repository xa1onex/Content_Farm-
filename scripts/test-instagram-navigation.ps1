[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Username,
    [Parameter(Mandatory = $true)][string]$Video,
    [string]$ExternalId = 'mumu-01'
)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$VideoPath = (Resolve-Path -LiteralPath $Video).Path
if (-not (Test-Path -LiteralPath $VideoPath -PathType Leaf)) { throw 'Select an existing MP4 video.' }
Set-Location $Repo
$Preset = Join-Path $Repo 'worker\profiles\instagram-452.0.0.0.43-ru-RU.navigation.json'
$LocalProfile = Join-Path $Repo 'worker\profiles\instagram-452.0.0.0.43-ru-RU.navigation.local.json'
if (-not (Test-Path -LiteralPath $LocalProfile)) {
    Copy-Item -LiteralPath $Preset -Destination $LocalProfile
}
$Worker = Join-Path $Repo '.venv-worker\Scripts\shortpost-worker.exe'
& $Worker test-navigation --external-id $ExternalId --platform instagram --username $Username --video $VideoPath --profile $LocalProfile
exit $LASTEXITCODE
