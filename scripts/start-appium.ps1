$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$env:APPIUM_HOME = Join-Path $env:LOCALAPPDATA 'ShortPost\appium'
$Appium = Join-Path $Repo 'worker\tools\node_modules\.bin\appium.cmd'
if (-not (Test-Path $Appium)) { throw 'Run setup-windows.ps1 -InstallAppium first, or use your existing Appium 2 server.' }
& $Appium --address 127.0.0.1 --port 4723 --log-level warn
exit $LASTEXITCODE
