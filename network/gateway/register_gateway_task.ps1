param([Parameter(Mandatory = $true)][string]$PythonExe)
$ErrorActionPreference = 'Stop'
$env:PSModulePath = (Join-Path $PSHOME 'Modules') + [IO.Path]::PathSeparator + $env:PSModulePath
$Root = Join-Path $env:ProgramData 'ShortPost\network-gateway'
$Name = 'ShortPost Network Gateway'
$Description = 'ShortPost per-emulator SOCKS5 gateway; owned by Content_Farm'
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw 'Python is not installed.' }
foreach ($File in @('start_gateway.py', 'verify_adapter.ps1', 'adapter.json', 'gateway.qcow2', 'seed.iso')) {
    if (-not (Test-Path -LiteralPath (Join-Path $Root $File))) { throw "Missing gateway prerequisite: $File" }
}
$Existing = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
if ($Existing -and $Existing.Description -ne $Description) { throw 'The scheduled task name is already in use.' }
$Sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument ('"' + (Join-Path $Root 'start_gateway.py') + '"')
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $Sid
$Principal = New-ScheduledTaskPrincipal -UserId $Sid -LogonType Interactive -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName $Name -Description $Description -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null
Write-Output 'Gateway automatic startup registered.'
