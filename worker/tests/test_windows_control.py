"""Test protection of existing Windows SSH without installing or starting a service."""
import json
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize('payload_mode', [False, True])
def test_bootstrap_report_only_posts_status_without_leaking_worker_token(tmp_path, monkeypatch, payload_mode):
    import importlib.util
    import uuid
    from types import SimpleNamespace
    import httpx
    script = Path(__file__).resolve().parents[2] / 'scripts' / 'report-windows-control.py'
    spec = importlib.util.spec_from_file_location('bootstrap_reporter', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setenv('WINDIR', str(tmp_path / 'Windows'))
    config = SimpleNamespace(state_dir=tmp_path, api_url='https://example.invalid/api/v1', worker_id=uuid.uuid4())
    monkeypatch.setattr(module, 'load', lambda *a: config)
    monkeypatch.setattr(module, 'DPAPI', lambda: object())
    monkeypatch.setattr(module, 'TokenStore', lambda *a: SimpleNamespace(read=lambda: 'private-worker-token'))
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={'worker_id': str(config.worker_id)})

    client = httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(module.httpx, 'Client', lambda **kwargs: client)
    message = 'Не удалось запустить службу "OpenSSH SSH Server (sshd)".\nСледующая строка.'
    argv = ['report', '--stage', 'installer_failed', '--message', message]
    if payload_mode:
        import base64
        data = {'stage': 'installer_failed', 'message': message,
                'diagnostics': {'events': [{'id': 4, 'message': 'Permissions error', 'token': 'omit-me'}], 'unknown': 'omit-me'}}
        argv = ['report', '--payload-base64', base64.b64encode(json.dumps(data).encode()).decode()]
    monkeypatch.setattr(module.sys, 'argv', argv)
    assert module.main() == 0
    assert len(requests) == 1 and requests[0].url.path == '/api/v1/workers/heartbeat'
    body = json.loads(requests[0].content)
    assert body['devices'] == []
    assert body['capabilities']['windows_control_bootstrap']['stage'] == 'installer_failed'
    assert body['capabilities']['windows_control_bootstrap']['message'] == message
    assert b'private-worker-token' not in requests[0].content
    assert b'omit-me' not in requests[0].content
    local = tmp_path / 'ShortPost' / 'windows-control-bootstrap' / 'latest.json'
    assert 'private-worker-token' not in local.read_text(encoding='utf-8')


@pytest.mark.skipif(os.name != 'nt', reason='Windows OpenSSH owner and ACL checks')
def test_ssh_resources_are_owned_by_administrators_and_only_system_and_admins_have_access(tmp_path):
    script = Path(__file__).resolve().parents[2] / 'scripts' / 'connect-windows.ps1'
    folder = tmp_path / 'SSH folder with spaces'
    folder.mkdir()
    key = folder / 'host_key'
    key.write_text('Synthetic key material; this test checks ownership and permissions only.')
    code = '''
$ErrorActionPreference='Stop'
$t=$null; $e=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($env:SHORTPOST_CONTROL_SCRIPT,[ref]$t,[ref]$e)
$fn=$ast.Find({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Set-SshResourceAcl'},$true)
. ([scriptblock]::Create($fn.Extent.Text))
Set-SshResourceAcl $env:SHORTPOST_ACL_FOLDER -Directory
Set-SshResourceAcl $env:SHORTPOST_ACL_KEY
foreach ($path in @($env:SHORTPOST_ACL_FOLDER,$env:SHORTPOST_ACL_KEY)) {
 $acl=Get-Acl -LiteralPath $path
 if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne 'S-1-5-32-544') { throw 'Incorrect owner' }
 if (-not $acl.AreAccessRulesProtected) { throw 'Unexpected inheritance' }
 $rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
 if ($rules.Count -ne 2) { throw 'Unexpected ACL entries' }
 foreach ($rule in $rules) {
  if ($rule.IdentityReference.Value -notin @('S-1-5-18','S-1-5-32-544')) { throw 'Unexpected principal' }
  if ($rule.FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl) { throw 'Missing service access' }
 }
}
'''
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', code],
        capture_output=True, text=True, timeout=20,
        env=dict(os.environ, SHORTPOST_CONTROL_SCRIPT=str(script), SHORTPOST_ACL_FOLDER=str(folder), SHORTPOST_ACL_KEY=str(key)))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell to Python status transport')
def test_status_transport_preserves_cyrillic_quotes_and_line_breaks(tmp_path):
    import sys
    script = Path(__file__).resolve().parents[2] / 'scripts' / 'connect-windows.ps1'
    output = tmp_path / 'payload.json'
    reporter = tmp_path / 'reporter.py'
    reporter.write_text('import base64,json,os,sys\nfrom pathlib import Path\n'
        'assert sys.argv[1]=="--payload-base64" and len(sys.argv)==3\n'
        'Path(os.environ["SHORTPOST_TEST_PAYLOAD"]).write_bytes(base64.b64decode(sys.argv[2]))\n')
    message = 'Не удалось запустить службу "OpenSSH SSH Server (sshd)".\nСтрока 2.'
    code = '''
$ErrorActionPreference='Stop'
$t=$null; $e=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($env:SHORTPOST_CONTROL_SCRIPT,[ref]$t,[ref]$e)
$fn=$ast.Find({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Send-BootstrapStatus'},$true)
. ([scriptblock]::Create($fn.Extent.Text))
function Join-Path($Path,$ChildPath) {
 if ($ChildPath -like '*python.exe') { return $env:SHORTPOST_TEST_PYTHON }
 return $env:SHORTPOST_TEST_REPORTER
}
Send-BootstrapStatus 'installer_failed' $env:SHORTPOST_TEST_MESSAGE @{service_status='Stopped'}
exit $LASTEXITCODE
'''
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', code],
        capture_output=True, text=True, timeout=20,
        env=dict(os.environ, SHORTPOST_CONTROL_SCRIPT=str(script), SHORTPOST_TEST_PYTHON=sys.executable,
                 SHORTPOST_TEST_REPORTER=str(reporter), SHORTPOST_TEST_PAYLOAD=str(output), SHORTPOST_TEST_MESSAGE=message))
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(output.read_text(encoding='utf-8'))
    assert data == {'stage': 'installer_failed', 'message': message, 'diagnostics': {'service_status': 'Stopped'}}


@pytest.mark.skipif(os.name != 'nt', reason='Real Windows CMD launcher')
def test_cmd_launcher_executes_companion_script_from_a_directory_with_spaces(tmp_path):
    import shutil
    folder = tmp_path / 'Connection folder with spaces'
    folder.mkdir()
    script = Path(__file__).resolve().parents[2] / 'scripts' / 'connect-windows.cmd'
    target = folder / script.name
    shutil.copyfile(script, target)
    (folder / 'connect-windows.ps1').write_text("Write-Output 'SHORTPOST_LAUNCH_OK'; exit 0\n")
    result = subprocess.run([os.environ['COMSPEC'], '/d', '/c', str(target)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'SHORTPOST_LAUNCH_OK' in result.stdout


def test_single_file_connector_requires_pinned_https_and_checks_package_before_execution():
    import base64
    import importlib.util
    import re
    script = Path(__file__).resolve().parents[2] / 'scripts' / 'build-windows-connector.py'
    spec = importlib.util.spec_from_file_location('connector_builder', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    url = 'https://github.com/example/repo/raw/' + 'a' * 40 + '/downloads/package.zip'
    output = module.build(url, 'b' * 64)
    encoded = re.search(r'-EncodedCommand ([A-Za-z0-9+/=]+)', output)[1]
    decoded = base64.b64decode(encoded).decode('utf-16le')
    assert url in decoded and 'b' * 64 in decoded
    assert decoded.index('Get-FileHash') < decoded.index('Expand-Archive') < decoded.index("& (Join-Path")
    if os.name == 'nt':
        code = ('$tokens=$null; $errors=$null; '
                '[System.Management.Automation.Language.Parser]::ParseInput($env:SHORTPOST_BOOTSTRAP_PAYLOAD, '
                '[ref]$tokens, [ref]$errors) | Out-Null; if ($errors.Count) { exit 1 }')
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', code],
            env=dict(os.environ, SHORTPOST_BOOTSTRAP_PAYLOAD=decoded), capture_output=True, timeout=20)
        assert result.returncode == 0
    for bad in (url.replace('/raw/' + 'a' * 40, '/raw/main'), url.replace('https:', 'http:'),
                url.replace('github.com', 'untrusted.example')):
        with pytest.raises(ValueError):
            module.build(bad, 'b' * 64)


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
