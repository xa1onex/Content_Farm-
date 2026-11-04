[CmdletBinding()]
param([string]$ConnectionFile = (Join-Path $PSScriptRoot 'connection.json'), [switch]$Disconnect)
$ErrorActionPreference = 'Stop'
$ControlRoot = Join-Path $env:ProgramData 'ShortPost\windows-control'
$SshConfig = Join-Path $env:ProgramData 'ssh\sshd_config'
$StateFile = Join-Path $ControlRoot 'state.json'
$Marker = '# SHORTPOST WINDOWS CONTROL V1'
$Utf8 = New-Object System.Text.UTF8Encoding($false)
$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$Principal = New-Object Security.Principal.WindowsPrincipal($Identity)
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Start CONNECT-WINDOWS.cmd and approve the Windows administrator prompt.'
}

function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $([IO.Path]::GetFileName($Executable)) (exit $LASTEXITCODE)" }
}

function Protect-Folder([string]$Folder) {
    Invoke-Checked 'icacls.exe' @($Folder, '/inheritance:r', '/grant:r', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F')
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
    Remove-Item -LiteralPath $StateFile -ErrorAction SilentlyContinue
}

try {
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
        }
        if ($ExistingConfig) { Copy-Item -LiteralPath $SshConfig -Destination (Join-Path $ControlRoot 'sshd_config.before') }
        [IO.File]::WriteAllText($StateFile, ($Saved | ConvertTo-Json), $Utf8)
        try {
            $Capability = Get-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0'
            if ($Capability.State -ne 'Installed') {
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
            [IO.File]::WriteAllText($SshConfig, ($ConfigText + "`n"), $Utf8)
            Invoke-Checked $Sshd @('-t', '-f', $SshConfig)
            Set-Service sshd -StartupType Manual
            Start-Service sshd
        } catch {
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
    while ($true) {
        & $Ssh -F NUL -N -T -i $TunnelKey -p $Connection.server_port -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$KnownHosts" -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -R $Forward $Destination
        Write-Host "Tunnel exited ($LASTEXITCODE). Reconnecting in 10 seconds; Ctrl+C to stop."
        Start-Sleep -Seconds 10
    }
} catch {
    Write-Host ('Connection failed: ' + $_.Exception.Message) -ForegroundColor Red
    Read-Host 'Press Enter to close'
    exit 1
}
