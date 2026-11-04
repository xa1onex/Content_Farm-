param([switch]$Live)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$User = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$PowerShell = Join-Path $PSHOME 'powershell.exe'
$AppiumArgs = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + (Join-Path $Repo 'scripts\start-appium.ps1') + '"'
$WorkerArgs = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + (Join-Path $Repo 'scripts\start-worker.ps1') + '"'
if ($Live) { $WorkerArgs += ' -Live' }
foreach ($Item in @(@('ShortPost Appium',$AppiumArgs),@('ShortPost Worker',$WorkerArgs))) {
    $Action = New-ScheduledTaskAction -Execute $PowerShell -Argument $Item[1] -WorkingDirectory $Repo
    $Existing = Get-ScheduledTask -TaskName $Item[0] -ErrorAction SilentlyContinue
    if ($Existing -and $Existing.State -eq 'Running') {
        if ($Item[0] -eq 'ShortPost Appium' -and $Existing.Actions[0].Arguments -eq $Item[1]) { continue }
        throw ('Stop this ShortPost task before changing its mode: ' + $Item[0])
    }
    Register-ScheduledTask -TaskName $Item[0] -Action $Action -Principal $Principal -Trigger $Trigger -Settings $Settings -Force | Out-Null
}
Start-ScheduledTask -TaskName 'ShortPost Appium'
Start-ScheduledTask -TaskName 'ShortPost Worker'
Write-Output 'ShortPost startup registered and started for the current Windows user.'
