$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$env:APPIUM_HOME = Join-Path $env:LOCALAPPDATA 'ShortPost\appium'
$Appium = Join-Path $Repo 'worker\tools\node_modules\.bin\appium.cmd'
if (-not (Test-Path $Appium)) { throw 'Run setup-windows.ps1 -InstallAppium first, or use your existing Appium 2 server.' }
$Folder = Join-Path $env:LOCALAPPDATA 'ShortPost\logs'
New-Item -ItemType Directory -Force -Path $Folder | Out-Null
$Log = Join-Path $Folder 'appium.log'
while ($true) {
    # Reuse the already running local server; never terminate somebody else's process.
    $Available = $false
    try {
        $Status = Invoke-RestMethod -Uri 'http://127.0.0.1:4723/status' -TimeoutSec 4
        $Available = [bool]$Status.value.ready
    } catch {}
    if ($Available) { Start-Sleep -Seconds 15; continue }
    if ((Test-Path $Log) -and (Get-Item $Log).Length -gt 2MB) { Move-Item $Log ($Log+'.previous') -Force }
    & $Appium --address 127.0.0.1 --port 4723 --log-level warn *>> $Log
    Start-Sleep -Seconds 15
}
