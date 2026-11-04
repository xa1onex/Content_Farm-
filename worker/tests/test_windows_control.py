"""Test protection of existing Windows SSH without installing or starting a service."""
import json
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell native quoting')
def test_installer_generates_an_unencrypted_host_key_with_windows_powershell(tmp_path):
    script = Path(__file__).resolve().parents[2] / 'scripts' / 'connect-windows.ps1'
    key = tmp_path / 'host key with spaces'
    code = '''
$ErrorActionPreference = 'Stop'
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($env:SHORTPOST_CONTROL_SCRIPT, [ref]$tokens, [ref]$errors)
$fn=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-Checked'}, $true)
. ([scriptblock]::Create($fn.Extent.Text))
$keygen=Join-Path $env:WINDIR 'System32\\OpenSSH\\ssh-keygen.exe'
Invoke-Checked $keygen @('-q', '-t', 'ed25519', '-N', '""', '-f', $env:SHORTPOST_TEST_HOST_KEY)
'''
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', code],
        capture_output=True, text=True, timeout=20,
        env=dict(os.environ, SHORTPOST_CONTROL_SCRIPT=str(script), SHORTPOST_TEST_HOST_KEY=str(key)))
    assert result.returncode == 0, result.stdout + result.stderr
    assert key.exists() and key.with_suffix('.pub').exists()
    keygen = Path(os.environ['WINDIR']) / 'System32' / 'OpenSSH' / 'ssh-keygen.exe'
    result = subprocess.run([str(keygen), '-y', '-P', '', '-f', str(key)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and result.stdout.startswith('ssh-ed25519 ')


@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell installer guard')
def test_existing_ssh_config_is_preserved_before_installation(tmp_path):
    scripts = Path(__file__).resolve().parents[2] / 'scripts'
    program_data = tmp_path / 'ProgramData'
    profile = tmp_path / 'User'
    ssh_config = program_data / 'ssh' / 'sshd_config'
    ssh_config.parent.mkdir(parents=True)
    original = b'# Existing unrelated Windows SSH configuration\nPort 2222\n'
    ssh_config.write_bytes(original)
    tunnel_key = profile / '.ssh' / 'shortpost-appium'
    tunnel_key.parent.mkdir(parents=True)
    tunnel_key.write_text('Not a real key. The guard must run before SSH is invoked.')
    connection = tmp_path / 'connection.json'
    connection.write_text(json.dumps({
        'schema_version': 1, 'server_host': 'example.invalid', 'server_port': 22,
        'server_user': 'shortpost-windows', 'server_host_key': 'ssh-ed25519 AAAA',
        'operator_public_key': 'ssh-ed25519 AAAA operator', 'windows_user': os.environ['USERNAME'].lower(),
        'tunnel_key_name': 'shortpost-appium', 'reverse_port': 14724, 'windows_port': 22222,
    }))
    harness = tmp_path / 'guard.ps1'
    harness.write_text('''
$ErrorActionPreference = 'Stop'
function Read-Host { return '' }
function Get-Service { return $null }
function Get-WindowsCapability { throw 'UNEXPECTED_INSTALL_ATTEMPT' }
function Add-WindowsCapability { throw 'UNEXPECTED_INSTALL_ATTEMPT' }
function Set-Service { throw 'UNEXPECTED_SERVICE_CHANGE' }
function Start-Service { throw 'UNEXPECTED_SERVICE_CHANGE' }
function Stop-Service { throw 'UNEXPECTED_SERVICE_CHANGE' }
& $env:SHORTPOST_CONTROL_SCRIPT -ConnectionFile $env:SHORTPOST_CONNECTION
exit $LASTEXITCODE
''')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
        '-ExecutionPolicy', 'Bypass', '-File', str(harness)], capture_output=True, text=True,
        env=dict(os.environ, ProgramData=str(program_data), USERPROFILE=str(profile),
                 SHORTPOST_CONTROL_SCRIPT=str(scripts / 'connect-windows.ps1'),
                 SHORTPOST_CONNECTION=str(connection)), timeout=30)
    assert result.returncode != 0
    assert 'Existing Windows SSH setup detected' in result.stdout + result.stderr
    assert 'UNEXPECTED_' not in result.stdout + result.stderr
    assert ssh_config.read_bytes() == original
    assert not (program_data / 'ShortPost').exists()
