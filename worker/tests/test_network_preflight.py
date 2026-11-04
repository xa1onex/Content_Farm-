import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from shortpost_worker import network_preflight as probe
from shortpost_worker.errors import WorkerError


def test_bridge_probe_reads_only_advertised_flag_and_omits_other_settings(monkeypatch):
    calls = []

    def command(argv, **kwargs):
        calls.append(argv)
        if argv[-1] == '--help':
            return 'setting: --all_writable read all writable settings'
        return json.dumps({'net_bridge_open': 'false', 'net_bridge_gateway': '10.0.0.1',
                           'proxy_password': 'secret-must-not-leave-pc', 'phone_imei': 'private'})

    monkeypatch.setattr(probe, 'command', command)
    result = probe.bridge_settings('MuMuManager.exe', 0)
    assert calls == [['MuMuManager.exe', 'setting', '--help'],
                     ['MuMuManager.exe', 'setting', '-v', '0', '--all_writable']]
    assert result == {'status': 'ok', 'settings': {
        'net_bridge_open': 'false', 'net_bridge_gateway': '10.0.0.1'}}
    assert 'secret' not in json.dumps(result)


def test_unknown_cli_never_attempts_settings_command(monkeypatch):
    command = Mock(return_value='--all_writable_new --set --value')
    monkeypatch.setattr(probe, 'command', command)
    assert probe.bridge_settings('manager', 0) == {'status': 'unsupported_cli'}
    command.assert_called_once_with(['manager', 'setting', '--help'])


def test_host_report_omits_unrequested_fields_and_raw_errors(monkeypatch):
    monkeypatch.setattr(probe, 'command', lambda *a, **k: json.dumps({
        'adapters': [{'index': 4, 'name': 'Ethernet', 'mac': 'private'}],
        'username': 'private', 'environment': {'TOKEN': 'secret'}}))
    result = probe.host_network()
    assert result['adapters'] == [{'index': 4, 'name': 'Ethernet'}]
    assert 'private' not in json.dumps(result) and 'secret' not in json.dumps(result)

    def unavailable(*args, **kwargs):
        raise WorkerError('private-error-payload')

    monkeypatch.setattr(probe, 'command', unavailable)
    assert probe.host_network() == {'status': 'unavailable'}
    assert probe.bridge_settings('manager', 0) == {'status': 'unavailable'}


def test_preflight_does_not_claim_proxy_or_isolation_success(config, monkeypatch):
    monkeypatch.setattr(probe, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(probe.platform, 'win32_edition', lambda: 'Professional')
    monkeypatch.setattr(probe, 'host_network', lambda: {'status': 'ok'})
    monkeypatch.setattr(probe, 'discover_mumu', lambda: ['manager'])
    manager = Mock()
    manager.list.return_value = [{'index': '0', 'pid': 42, 'phone_imei': 'private'}]
    monkeypatch.setattr(probe, 'MuMu', lambda *a: manager)
    monkeypatch.setattr(probe, 'bridge_settings', lambda *a: {'status': 'ok', 'settings': {}})
    monkeypatch.setattr(probe.psutil, 'process_iter', lambda *a: [
        SimpleNamespace(info={'name': 'MuMuVMMHeadless.exe', 'pid': 42}),
        SimpleNamespace(info={'name': 'unrelated.exe', 'pid': 5})])
    result = probe.collect(config)
    assert result['read_only'] is True and result['network_changed'] is False
    assert result['proxy_tested'] is result['isolation_verified'] is result['udp_verified'] is False
    assert result['mumu']['instances'] == [{'index': '0', 'pid': 42}]
    assert result['devices'][0]['external_id'] == 'mumu-01'
    assert result['mumu_processes'] == [{'name': 'MuMuVMMHeadless.exe', 'pid': 42}]
    manager.launch.assert_not_called()
    assert 'private' not in json.dumps(result)


@pytest.mark.asyncio
async def test_report_uses_only_ordinary_worker_heartbeat(config, journal, monkeypatch):
    api = Mock(heartbeat=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr(probe, 'Api', lambda *a: api)
    monkeypatch.setattr(probe, 'TokenStore', lambda *a: SimpleNamespace(read=lambda: 'private-token'))
    report = {'read_only': True, 'isolation_verified': False}
    await probe.send_report(config, journal, report)
    assert api.heartbeat.await_args.kwargs == {'capabilities': {'network_preflight': report}}
    api.claim.assert_not_called()
    api.close.assert_awaited_once()


def test_powershell_wrapper_parses_on_windows():
    import base64
    import os
    import subprocess
    from pathlib import Path

    if os.name != 'nt':
        pytest.skip('PowerShell parser is checked on the Windows CI runner')
    script = Path(__file__).resolve().parents[2] / 'scripts' / 'check-emulator-network.ps1'
    host_script = base64.b64encode(probe.HOST_SCRIPT.encode('utf-8')).decode('ascii')
    parser_script = (
        '$tokens=$null; $errors=$null; '
        "[System.Management.Automation.Language.Parser]::ParseFile('check-emulator-network.ps1', [ref]$tokens, [ref]$errors) | Out-Null; "
        'if ($errors.Count) { exit 1 }; '
        f"$source=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{host_script}')); "
        '[System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors) | Out-Null; '
        'if ($errors.Count) { exit 1 }'
    )
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand',
        base64.b64encode(parser_script.encode('utf-16le')).decode('ascii')],
        cwd=script.parent, capture_output=True)
    assert result.returncode == 0
