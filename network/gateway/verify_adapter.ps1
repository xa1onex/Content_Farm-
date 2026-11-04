$ErrorActionPreference = 'Stop'
$env:PSModulePath = (Join-Path $PSHOME 'Modules') + [IO.Path]::PathSeparator + $env:PSModulePath
$Root = Join-Path $env:ProgramData 'ShortPost\network-gateway'
$Owned = Get-Content (Join-Path $Root 'adapter.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$Adapter = Get-NetAdapter -Name 'ShortPost-LAN'
if ([string]$Adapter.InterfaceGuid -ne $Owned.guid -or $Adapter.InterfaceDescription -notlike 'TAP-Windows Adapter V9*') {
    throw 'The gateway adapter changed. Refusing to use a different network.'
}
$DefaultRoutes = @(Get-NetRoute -InterfaceIndex $Adapter.ifIndex -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue)
if ($DefaultRoutes.Count) { throw 'The isolated adapter must not be a Windows default gateway.' }
$Bridge = Get-NetAdapterBinding -Name $Adapter.Name -ComponentID 'oracle_MuMuNxNetLwf'
if (-not $Bridge.Enabled) { throw 'The MuMu bridge binding is unavailable.' }
$Others = @(Get-NetAdapter | Where-Object { $_.Name -ne $Adapter.Name } | ForEach-Object { $_.Name })
foreach ($Binary in @('MuMuNxHeadless.exe', 'VBoxNetNAT.exe')) {
    $Label = "ShortPost MuMu no external NAT $Binary"
    $Rule = Get-NetFirewallRule -DisplayName $Label -ErrorAction SilentlyContinue
    if (-not $Rule -or $Rule.Group -ne 'ShortPost Gateway') {
        throw 'The MuMu external NAT guard is missing.'
    }
    Set-NetFirewallRule -InputObject $Rule -InterfaceAlias $Others -Enabled True -Action Block
}
