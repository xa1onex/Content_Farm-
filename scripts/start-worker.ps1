param([switch]$Live, [string]$Config)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
$Sdk = Join-Path $env:LOCALAPPDATA 'Android\Sdk'
$env:ANDROID_HOME = $Sdk
$env:Path = (Join-Path $Sdk 'platform-tools') + ';' + $env:Path
$Python = Join-Path $Repo '.venv-worker\Scripts\python.exe'
if (-not $Config) { $Config = Join-Path $Repo 'worker\config.local.toml' }
$Arguments = @('-m','shortpost_worker','--config',$Config,'run')
if ($Live) { $Arguments += '--live' }
$Folder = Join-Path $env:LOCALAPPDATA 'ShortPost\logs'
New-Item -ItemType Directory -Force -Path $Folder | Out-Null
$Log = Join-Path $Folder 'worker.log'
while ($true) {
    if ((Test-Path $Log) -and (Get-Item $Log).Length -gt 2MB) { Move-Item $Log ($Log+'.previous') -Force }
    & $Python @Arguments *>> $Log
    if ($LASTEXITCODE -eq 0) { exit 0 }
    Start-Sleep -Seconds 30
}
