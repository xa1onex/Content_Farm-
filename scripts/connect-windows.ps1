[CmdletBinding()]
param([string]$ConnectionFile = (Join-Path $PSScriptRoot 'connection.json'), [switch]$Disconnect, [switch]$Elevated)
$ErrorActionPreference = 'Stop'
$ControlRoot = Join-Path $env:ProgramData 'ShortPost\windows-control'
$SshConfig = Join-Path $env:ProgramData 'ssh\sshd_config'
$StateFile = Join-Path $ControlRoot 'state.json'
$Marker = '# SHORTPOST WINDOWS CONTROL V1'
$Utf8 = New-Object System.Text.UTF8Encoding($false)
$BootstrapFolder = Join-Path $env:LOCALAPPDATA 'ShortPost\windows-control-bootstrap'
New-Item -ItemType Directory -Path $BootstrapFolder -Force | Out-Null
$TranscriptPath = Join-Path $BootstrapFolder ("connect-$PID.log")
$TranscriptStarted = $false
try {
    Start-Transcript -Path $TranscriptPath -Append | Out-Null
    $TranscriptStarted = $true
} catch { }

function Send-BootstrapStatus([string]$Stage, [string]$Message = '', [hashtable]$Diagnostics = @{}) {
    $Python = Join-Path $env:WINDIR 'System32\Content_Farm-\.venv-worker\Scripts\python.exe'
    $Reporter = Join-Path $PSScriptRoot 'report-windows-control.py'
    if ((Test-Path -LiteralPath $Python) -and (Test-Path -LiteralPath $Reporter)) {
        $Payload = @{ stage = $Stage; message = $Message; diagnostics = $Diagnostics } | ConvertTo-Json -Depth 6 -Compress
        $Encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Payload))
        & $Python $Reporter --payload-base64 $Encoded
    }
}

$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$Principal = New-Object Security.Principal.WindowsPrincipal($Identity)
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    if ($Elevated) { throw 'Windows did not grant administrator rights.' }
    Send-BootstrapStatus 'awaiting_windows_confirmation'
    Write-Host 'Confirm the Windows administrator request to connect this PC.'
    try {
        $Quote = [char]34
        $Launch = '-NoProfile -ExecutionPolicy Bypass -File ' + $Quote + $PSCommandPath + $Quote + ' -ConnectionFile ' + $Quote + $ConnectionFile + $Quote + ' -Elevated'
        if ($Disconnect) { $Launch += ' -Disconnect' }
        $Child = Start-Process -FilePath powershell.exe -Verb RunAs -ArgumentList $Launch -PassThru -Wait
        if ($Child.ExitCode -ne 0) {
            Write-Host 'Connection did not finish. Details were saved and sent to the operator where possible.' -ForegroundColor Red
            Write-Host ('Log: ' + $TranscriptPath)
            Read-Host 'Leave this window open. Press Enter only to close it'
        }
        exit $Child.ExitCode
    } catch {
        Send-BootstrapStatus 'windows_confirmation_failed' 'Windows did not launch the elevated installer.'
        Write-Host 'Windows did not launch the administrator window. Status was sent to the operator where possible.' -ForegroundColor Red
        Read-Host 'Leave this window open. Press Enter only to close it'
        exit 1
    }
}

function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $([IO.Path]::GetFileName($Executable)) (exit $LASTEXITCODE)" }
}

function Protect-Folder([string]$Folder) {
    Set-SshResourceAcl $Folder -Directory
}

function Set-SshResourceAcl([string]$Path, [switch]$Directory) {
    $Admins = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
    $System = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
    if ($Directory) {
        $Acl = New-Object Security.AccessControl.DirectorySecurity
        $Inheritance = [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
    } else {
        $Acl = New-Object Security.AccessControl.FileSecurity
        $Inheritance = [Security.AccessControl.InheritanceFlags]::None
    }
    $Acl.SetAccessRuleProtection($true, $false)
    $Acl.SetOwner($Admins)
    foreach ($Sid in @($Admins, $System)) {
        $Rule = New-Object Security.AccessControl.FileSystemAccessRule($Sid, [Security.AccessControl.FileSystemRights]::FullControl, $Inheritance, [Security.AccessControl.PropagationFlags]::None, [Security.AccessControl.AccessControlType]::Allow)
        $Acl.AddAccessRule($Rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $Acl
}

function Get-SshServiceDiagnostics {
    $Details = @{}
    $Service = Get-Service sshd -ErrorAction SilentlyContinue
    if ($Service) { $Details.service_status = [string]$Service.Status }
    try { $Details.service_path = [string](Get-CimInstance Win32_Service -Filter "Name='sshd'" -ErrorAction Stop).PathName } catch { }
    $Details.events = @()
    foreach ($Log in @('OpenSSH/Operational', 'OpenSSH/Admin')) {
        $Details.events += @(Get-WinEvent -FilterHashtable @{ LogName = $Log; StartTime = (Get-Date).AddMinutes(-3) } -MaxEvents 3 -ErrorAction SilentlyContinue | ForEach-Object { @{ id = $_.Id; message = ([string]$_.Message).Substring(0, [Math]::Min(700, ([string]$_.Message).Length)) } })
    }
    $Details.resource_acls = @()
    foreach ($Resource in @($ControlRoot, (Join-Path $ControlRoot 'ssh_host_ed25519_key'), (Split-Path -Parent $SshConfig), (Join-Path (Split-Path -Parent $SshConfig) 'logs'), $SshConfig)) {
        if (Test-Path -LiteralPath $Resource) {
            $Acl = Get-Acl -LiteralPath $Resource
            $Details.resource_acls += @{ resource = $Resource; owner = [string]$Acl.Owner; sddl = [string]$Acl.Sddl }
        }
    }
    return $Details
}

function Read-OwnedState {
    if (-not (Test-Path -LiteralPath $StateFile)) { return $null }
    $Saved = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
    if ($Saved.schema_version -ne 1) { throw 'Unknown control state. Installation stopped.' }
    return $Saved
}

function Restore-Original($Saved) {
    Stop-Service sshd -ErrorAction SilentlyContinue
    if ($Saved.config_existed) {
        Copy-Item -LiteralPath (Join-Path $ControlRoot 'sshd_config.before') -Destination $SshConfig -Force
    } elseif (Test-Path -LiteralPath $SshConfig) {
        Remove-Item -LiteralPath $SshConfig
    }
    if ($Saved.service_existed) {
        Set-Service sshd -StartupType $Saved.start_type
        if ($Saved.was_running) { Start-Service sshd }
    } else {
        Set-Service sshd -StartupType Disabled
    }
    if (-not $Saved.firewall_existed) {
        Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue | Disable-NetFirewallRule
    }
    foreach ($Backup in @(@{ path = (Split-Path -Parent $SshConfig); sddl = $Saved.ssh_directory_sddl }, @{ path = (Join-Path (Split-Path -Parent $SshConfig) 'logs'); sddl = $Saved.logs_directory_sddl })) {
        if ($Backup.sddl -and (Test-Path -LiteralPath $Backup.path)) {
            $Acl = New-Object Security.AccessControl.DirectorySecurity
            $Acl.SetSecurityDescriptorSddlForm($Backup.sddl)
            Set-Acl -LiteralPath $Backup.path -AclObject $Acl
        }
    }
    Remove-Item -LiteralPath $StateFile -ErrorAction SilentlyContinue
}

try {
    $StartupDiagnostics = @{}
    Send-BootstrapStatus 'installer_started'
    $Saved = Read-OwnedState
    if ($Disconnect) {
        if (-not $Saved) { throw 'No ShortPost Windows control installation found.' }
        if ((Get-Content -LiteralPath $SshConfig -Raw) -notlike "$Marker*") {
            throw 'SSH configuration was changed externally. Automatic restore stopped.'
        }
        Restore-Original $Saved
        Write-Host 'Windows control disabled. Original SSH configuration restored.'
        Read-Host 'Press Enter to close'
        exit 0
    }
    $Connection = Get-Content -LiteralPath $ConnectionFile -Raw | ConvertFrom-Json
    if ($Connection.schema_version -ne 1 -or $Connection.server_host -notmatch '^[a-zA-Z0-9.-]+$' -or
        $Connection.server_user -notmatch '^[a-z][a-z0-9-]+$' -or
        $Connection.windows_user -notmatch '^[a-z][a-z0-9_-]+$' -or
        $Connection.tunnel_key_name -notmatch '^[a-zA-Z0-9_-]+$' -or
        $Connection.operator_public_key -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]+(?: [a-zA-Z0-9_-]+)?$' -or
        $Connection.server_host_key -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]+$') {
        throw 'Invalid connection package.'
    }
    foreach ($Port in @($Connection.server_port, $Connection.reverse_port, $Connection.windows_port)) {
        if ($Port -isnot [int] -and $Port -isnot [long]) { throw 'Invalid port in connection package.' }
        if ($Port -lt 1 -or $Port -gt 65535) { throw 'Invalid port in connection package.' }
    }
    if ($Connection.reverse_port -lt 1024 -or $Connection.windows_port -lt 1024) {
        throw 'Control ports must be unprivileged ports.'
    }
    if ($env:USERNAME.ToLowerInvariant() -ne $Connection.windows_user) {
        throw 'Use the same Windows account used for the ShortPost Worker; do not enter another administrator account.'
    }
    $TunnelKey = Join-Path $env:USERPROFILE ('.ssh\' + $Connection.tunnel_key_name)
    if (-not (Test-Path -LiteralPath $TunnelKey)) { throw 'Existing ShortPost tunnel key is missing under this Windows user.' }
    $Ssh = Join-Path $env:WINDIR 'System32\OpenSSH\ssh.exe'
    $Sshd = Join-Path $env:WINDIR 'System32\OpenSSH\sshd.exe'
    $Keygen = Join-Path $env:WINDIR 'System32\OpenSSH\ssh-keygen.exe'
    if (-not (Test-Path -LiteralPath $Ssh)) { throw 'Windows OpenSSH client is missing.' }
    if (-not $Saved) {
        $ExistingService = Get-Service sshd -ErrorAction SilentlyContinue
        $ExistingConfig = Test-Path -LiteralPath $SshConfig
        if ($ExistingConfig -or ($ExistingService -and $ExistingService.Status -eq 'Running')) {
            throw 'Existing Windows SSH setup detected. It has been left unchanged; operator review is required.'
        }
        if (Get-NetTCPConnection -LocalPort $Connection.windows_port -State Listen -ErrorAction SilentlyContinue) {
            throw 'Windows control port is already in use. Installation stopped.'
        }
        New-Item -ItemType Directory -Path $ControlRoot -Force | Out-Null
        Protect-Folder $ControlRoot
        $Saved = [ordered]@{
            schema_version = 1
            config_existed = [bool]$ExistingConfig
            service_existed = [bool]$ExistingService
            start_type = $(if ($ExistingService) { [string]$ExistingService.StartType } else { 'Disabled' })
            was_running = [bool]($ExistingService -and $ExistingService.Status -eq 'Running')
            firewall_existed = [bool](Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue)
            ssh_directory_sddl = $(if (Test-Path -LiteralPath (Split-Path -Parent $SshConfig)) { (Get-Acl -LiteralPath (Split-Path -Parent $SshConfig)).Sddl } else { $null })
            logs_directory_sddl = $(if (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $SshConfig) 'logs')) { (Get-Acl -LiteralPath (Join-Path (Split-Path -Parent $SshConfig) 'logs')).Sddl } else { $null })
        }
        if ($ExistingConfig) { Copy-Item -LiteralPath $SshConfig -Destination (Join-Path $ControlRoot 'sshd_config.before') }
        [IO.File]::WriteAllText($StateFile, ($Saved | ConvertTo-Json), $Utf8)
        try {
            $Capability = Get-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0'
            if ($Capability.State -ne 'Installed') {
                Send-BootstrapStatus 'installing_windows_component'
                Write-Host 'Installing the Microsoft Windows OpenSSH Server component. This may take several minutes.'
                $Installation = Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0'
                if ($Installation.RestartNeeded) { throw 'Windows requires a restart to finish OpenSSH installation.' }
            }
            if (-not (Test-Path -LiteralPath $Sshd)) { throw 'Windows OpenSSH Server is unavailable after installation.' }
            if (-not $Saved.firewall_existed) {
                Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue | Disable-NetFirewallRule
            }
            $HostKey = Join-Path $ControlRoot 'ssh_host_ed25519_key'
            if (-not (Test-Path -LiteralPath $HostKey)) {
                # Windows PowerShell 5 drops empty native arguments; explicit quotes preserve an empty passphrase.
                Invoke-Checked $Keygen @('-q', '-t', 'ed25519', '-N', '""', '-f', $HostKey)
            }
            $AuthorizedKeys = Join-Path $ControlRoot 'authorized_keys'
            [IO.File]::WriteAllText($AuthorizedKeys, ($Connection.operator_public_key + "`n"), $Utf8)
            Set-SshResourceAcl $HostKey
            Set-SshResourceAcl $AuthorizedKeys
            $ConfigText = @"
$Marker
Port $($Connection.windows_port)
AddressFamily inet
ListenAddress 127.0.0.1
HostKey "$($HostKey.Replace('\', '/'))"
AuthorizedKeysFile "$($AuthorizedKeys.Replace('\', '/'))"
AllowUsers $($Connection.windows_user)
AuthenticationMethods publickey
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
AllowTcpForwarding no
AllowAgentForwarding no
X11Forwarding no
LogLevel INFO
"@
            New-Item -ItemType Directory -Path (Split-Path -Parent $SshConfig) -Force | Out-Null
            New-Item -ItemType Directory -Path (Join-Path (Split-Path -Parent $SshConfig) 'logs') -Force | Out-Null
            Protect-Folder (Split-Path -Parent $SshConfig)
            Protect-Folder (Join-Path (Split-Path -Parent $SshConfig) 'logs')
            [IO.File]::WriteAllText($SshConfig, ($ConfigText + "`n"), $Utf8)
            Set-SshResourceAcl $SshConfig
            Invoke-Checked $Sshd @('-t', '-f', $SshConfig)
            Set-Service sshd -StartupType Manual
            Start-Service sshd
        } catch {
            try { $StartupDiagnostics = Get-SshServiceDiagnostics } catch { }
            if (Get-Service sshd -ErrorAction SilentlyContinue) { Restore-Original $Saved }
            else { Remove-Item -LiteralPath $StateFile -ErrorAction SilentlyContinue }
            throw
        }
    } else {
        if ((Get-Content -LiteralPath $SshConfig -Raw) -notlike "$Marker*") {
            throw 'SSH configuration was changed externally. Installation stopped.'
        }
        Start-Service sshd
    }
    $Listeners = @()
    for ($Attempt = 0; $Attempt -lt 10; $Attempt++) {
        $Listeners = @(Get-NetTCPConnection -LocalPort $Connection.windows_port -State Listen -ErrorAction SilentlyContinue)
        if ($Listeners.Count) { break }
        Start-Sleep -Milliseconds 500
    }
    if ($Listeners.Count -ne 1 -or $Listeners[0].LocalAddress -ne '127.0.0.1') {
        Stop-Service sshd
        throw 'SSH listener is not confined to localhost. Service stopped.'
    }
    $KnownHosts = Join-Path $ControlRoot 'server_known_hosts'
    $HostLabel = $Connection.server_host
    if ($Connection.server_port -ne 22) { $HostLabel = '[' + $HostLabel + ']:' + $Connection.server_port }
    [IO.File]::WriteAllText($KnownHosts, ($HostLabel + ' ' + $Connection.server_host_key + "`n"), $Utf8)
    $Forward = '127.0.0.1:' + $Connection.reverse_port + ':127.0.0.1:' + $Connection.windows_port
    $Destination = $Connection.server_user + '@' + $Connection.server_host
    Write-Host 'Connecting Windows control. Keep this window open. Ctrl+C disconnects the tunnel.'
    Write-Host 'Only the pinned operator key can log in; Windows SSH listens on localhost only.'
    Send-BootstrapStatus 'connecting_tunnel'
    while ($true) {
        & $Ssh -F NUL -N -T -i $TunnelKey -p $Connection.server_port -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$KnownHosts" -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -R $Forward $Destination
        $TunnelExitCode = $LASTEXITCODE
        Send-BootstrapStatus 'tunnel_exited' ("SSH exit code: $TunnelExitCode")
        Write-Host "Tunnel exited ($TunnelExitCode). Reconnecting in 10 seconds; Ctrl+C to stop."
        Start-Sleep -Seconds 10
    }
} catch {
    Write-Host ('Connection failed: ' + $_.Exception.Message) -ForegroundColor Red
    Send-BootstrapStatus 'installer_failed' $_.Exception.Message $StartupDiagnostics
    Write-Host ('Log: ' + $TranscriptPath)
    Read-Host 'Leave this window open. Press Enter only to close it'
    exit 1
} finally {
    if ($TranscriptStarted) { Stop-Transcript -ErrorAction SilentlyContinue | Out-Null }
}
