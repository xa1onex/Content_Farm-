[CmdletBinding()]
param([switch]$Live, [switch]$IncludeAppium)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$User = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
function Register-ShortPostTask([string]$Name,[string]$Script,[string]$Extra) {
    if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) { throw "Task $Name already exists. Review it before replacing." }
    $Arguments = '-NoProfile -File "' + (Join-Path $Repo $Script) + '"' + $Extra
    $Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $Arguments -WorkingDirectory $Repo
    Register-ScheduledTask -TaskName $Name -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings | Out-Null
}
if ($IncludeAppium) { Register-ShortPostTask 'ShortPost Appium' 'scripts\start-appium.ps1' '' }
$Extra = ''
if ($Live) { $Extra = ' -Live' }
Register-ShortPostTask 'ShortPost Worker' 'scripts\start-worker.ps1' $Extra
Write-Host 'Registered for interactive user logon; no Session 0 Windows service was created.'
