[CmdletBinding()]
param([switch]$InstallAppium)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
$PythonLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($PythonLauncher) { $Python = 'py'; $PythonArgs = @('-3') }
else { $Python = 'python'; $PythonArgs = @() }
& $Python @PythonArgs -c 'import sys; assert sys.version_info >= (3,12), "Python 3.12+ required"'
if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12+ from python.org, then repeat.' }
$Venv = Join-Path $Repo '.venv-worker'
if (-not (Test-Path "$Venv\Scripts\python.exe")) {
    & $Python @PythonArgs -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
}
& "$Venv\Scripts\python.exe" -m pip install -r "$Repo\worker\requirements.lock"
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& "$Venv\Scripts\python.exe" -m pip install --no-deps -e "$Repo\worker"
if ($LASTEXITCODE -ne 0) { throw 'Worker installation failed' }
$State = Join-Path $env:LOCALAPPDATA 'ShortPost'
New-Item -ItemType Directory -Force -Path $State | Out-Null
$Identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls $State '/inheritance:r' '/grant:r' "${Identity}:(OI)(CI)F" 'SYSTEM:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not restrict permissions on the new ShortPost state directory' }
if ($InstallAppium) {
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'Install Node.js LTS first. Existing Node is reused.' }
    Push-Location "$Repo\worker\tools"
    try {
        & npm.cmd ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Appium installation failed' }
        $env:APPIUM_HOME = Join-Path $State 'appium'
        & '.\node_modules\.bin\appium.cmd' driver list --installed
        if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect installed Appium drivers' }
        $Driver = Join-Path $env:APPIUM_HOME 'node_modules\appium-uiautomator2-driver\package.json'
        if (-not (Test-Path $Driver)) {
            & '.\node_modules\.bin\appium.cmd' driver install 'uiautomator2@4.2.9'
            if ($LASTEXITCODE -ne 0) { throw 'UiAutomator2 driver installation failed' }
        } elseif ((Get-Content $Driver -Raw | ConvertFrom-Json).version -ne '4.2.9') {
            throw 'A different UiAutomator2 version exists in this APPIUM_HOME; review it before replacing.'
        }
    } finally { Pop-Location }
}
Write-Host 'Installed. Next: see worker/SETUP_WINDOWS.md for init, token, device binding and safe navigation.'
Write-Host 'MuMu, Android SDK/JDK and FFmpeg are not silently installed. Doctor lists missing prerequisites.'
