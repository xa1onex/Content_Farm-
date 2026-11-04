[CmdletBinding(DefaultParameterSetName = 'Local')]
param(
    [Parameter(Mandatory = $true)][string]$Username,
    [Parameter(Mandatory = $true, ParameterSetName = 'Local')][string]$Video,
    [Parameter(Mandatory = $true, ParameterSetName = 'Emulator')]
    [ValidatePattern('^/sdcard/Movies/ShortPost/([a-f0-9-]{36})/\1\.mp4$')][string]$StagedVideo,
    [Parameter(Mandatory = $true, ParameterSetName = 'Emulator')]
    [ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedSha256,
    [Parameter(ParameterSetName = 'Emulator')]
    [ValidatePattern('^127\.0\.0\.1:[0-9]{1,5}$')][string]$AdbEndpoint = '127.0.0.1:16384',
    [string]$ExternalId = 'mumu-01'
)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$ScratchFile = $null
$ResultCode = 1
try {
    if ($PSCmdlet.ParameterSetName -eq 'Emulator') {
        $SdkRoot = $env:ANDROID_HOME
        if (-not $SdkRoot) { $SdkRoot = Join-Path $env:LOCALAPPDATA 'Android\Sdk' }
        $Adb = Join-Path $SdkRoot 'platform-tools\adb.exe'
        if (-not (Test-Path -LiteralPath $Adb)) { throw 'Android SDK adb.exe is missing; check ANDROID_HOME.' }
        $Scratch = Join-Path $env:LOCALAPPDATA 'ShortPost\diagnostic-inputs'
        New-Item -ItemType Directory -Force -Path $Scratch | Out-Null
        $ScratchFile = Join-Path $Scratch (([guid]::NewGuid().ToString()) + '.mp4')
        & $Adb -s $AdbEndpoint pull $StagedVideo $ScratchFile
        if ($LASTEXITCODE -ne 0) { throw 'Could not copy the prepared diagnostic video from MuMu.' }
        if ((Get-FileHash -LiteralPath $ScratchFile -Algorithm SHA256).Hash -ne $ExpectedSha256) {
            throw 'Diagnostic video checksum does not match. Test stopped.'
        }
        $VideoPath = $ScratchFile
    } else {
        $VideoPath = (Resolve-Path -LiteralPath $Video).Path
    }
    if (-not (Test-Path -LiteralPath $VideoPath -PathType Leaf)) { throw 'Select an existing MP4 video.' }
    Set-Location $Repo
    $Preset = Join-Path $Repo 'worker\profiles\instagram-452.0.0.0.43-ru-RU.navigation.json'
    $LocalProfile = Join-Path $Repo 'worker\profiles\instagram-452.0.0.0.43-ru-RU.navigation.local.json'
    if (-not (Test-Path -LiteralPath $LocalProfile)) {
        Copy-Item -LiteralPath $Preset -Destination $LocalProfile
    }
    $Worker = Join-Path $Repo '.venv-worker\Scripts\shortpost-worker.exe'
    & $Worker test-navigation --external-id $ExternalId --platform instagram --username $Username --video $VideoPath --profile $LocalProfile --report-server
    $ResultCode = $LASTEXITCODE
} finally {
    if ($ScratchFile -and (Test-Path -LiteralPath $ScratchFile)) { Remove-Item -LiteralPath $ScratchFile }
}
exit $ResultCode
