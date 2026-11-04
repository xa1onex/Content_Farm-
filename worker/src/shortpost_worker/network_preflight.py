"""Read network prerequisites; never configure networking or contact a proxy."""
import argparse
import asyncio
import json
import os
import platform
import re
from datetime import datetime, timezone
from pathlib import Path

import portalocker
import psutil

from .api import Api
from .config import load
from .crypto import DPAPI, TokenStore
from .devices import Devices, MuMu, command, discover_mumu
from .errors import WorkerError
from .journal import Journal

BRIDGE_KEYS = (
    'net_bridge_open', 'net_bridge_card', 'net_bridge_ip_mode',
    'net_bridge_ip_addr', 'net_bridge_subnet_mask', 'net_bridge_gateway',
    'net_bridge_dns1', 'net_bridge_dns2',
)
HOST_FIELDS = {
    'adapters': ('name', 'description', 'index', 'status', 'hardware'),
    'ipv4': ('index', 'address', 'prefix'),
    'routes': ('index', 'next_hop', 'metric'),
    'processors': ('virtualization_firmware', 'vm_monitor'),
    'computer': ('hypervisor_present',),
}
# No account names, MAC addresses, process command lines, environment dumps,
# proxy configuration, or credentials are requested from Windows.
HOST_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$r = @{}
$r.adapters = @(Get-NetAdapter | Select-Object @{n='name';e={$_.Name}}, @{n='description';e={$_.InterfaceDescription}}, @{n='index';e={$_.ifIndex}}, @{n='status';e={[string]$_.Status}}, @{n='hardware';e={$_.HardwareInterface}})
$r.ipv4 = @(Get-NetIPAddress -AddressFamily IPv4 | Select-Object @{n='index';e={$_.InterfaceIndex}}, @{n='address';e={$_.IPAddress}}, @{n='prefix';e={$_.PrefixLength}})
$r.routes = @(Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' | Select-Object @{n='index';e={$_.InterfaceIndex}}, @{n='next_hop';e={$_.NextHop}}, @{n='metric';e={$_.RouteMetric}})
$r.processors = @(Get-CimInstance Win32_Processor | Select-Object @{n='virtualization_firmware';e={$_.VirtualizationFirmwareEnabled}}, @{n='vm_monitor';e={$_.VMMonitorModeExtensions}})
$r.computer = @(Get-CimInstance Win32_ComputerSystem | Select-Object @{n='hypervisor_present';e={$_.HypervisorPresent}})
$r | ConvertTo-Json -Depth 4 -Compress
"""


def scalar(value):
    if isinstance(value, str):
        return value[:256]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return None


def host_network():
    try:
        raw = json.loads(command(['powershell.exe', '-NoProfile', '-NonInteractive',
                                  '-Command', HOST_SCRIPT], timeout=30).lstrip('\ufeff'))
        result = {'status': 'ok'}
        for section, keys in HOST_FIELDS.items():
            rows = raw.get(section, [])
            if not isinstance(rows, list):
                rows = [rows]
            result[section] = [{key: scalar(row[key]) for key in keys if key in row}
                               for row in rows[:64] if isinstance(row, dict)]
        return result
    except (WorkerError, ValueError, AttributeError):
        return {'status': 'unavailable'}


def bridge_settings(executable, index):
    """Only use a read flag advertised by this installed CLI's help."""
    try:
        help_text = command([executable, 'setting', '--help'])
        if not re.search(r'(?<![\w-])--all_writable(?![\w-])', help_text):
            return {'status': 'unsupported_cli'}
        raw = command([executable, 'setting', '-v', str(index), '--all_writable'])
        data = json.loads(raw[raw.index('{'):])
        if not isinstance(data, dict):
            return {'status': 'unsupported_format'}
        selected = {key: scalar(data[key]) for key in BRIDGE_KEYS if key in data}
        return {'status': 'ok' if selected else 'unsupported_format', 'settings': selected}
    except (WorkerError, ValueError):
        return {'status': 'unavailable'}


def collect(config):
    if os.name != 'nt':
        raise WorkerError('network_preflight_requires_windows')
    candidates = [config.mumu_manager] if config.mumu_manager else discover_mumu()
    executable = candidates[0] if len(candidates) == 1 else None
    result = {
        'schema_version': 1, 'tested_at': datetime.now(timezone.utc).isoformat(),
        'host_platform': 'win32', 'windows_edition': platform.win32_edition(),
        'read_only': True, 'network_changed': False,
        'proxy_tested': False, 'isolation_verified': False, 'udp_verified': False,
        'host': host_network(), 'mumu': {'status': 'not_selected'}, 'devices': [],
    }
    if executable:
        try:
            instances = MuMu(executable).list()
            result['mumu'] = {'status': 'ok', 'instances': [
                {key: scalar(row[key]) for key in ('index', 'is_android_started',
                 'is_process_started', 'adb_port', 'hyperv_enabled', 'pid') if key in row}
                for row in instances[:64]]}
        except WorkerError:
            result['mumu'] = {'status': 'unavailable'}
    for device in config.devices:
        if device.enabled:
            result['devices'].append({
                'external_id': device.external_id, 'instance_index': device.instance_index,
                'bridge': bridge_settings(executable, device.instance_index) if executable
                          else {'status': 'not_selected'},
            })
    processes = []
    for process in psutil.process_iter(['name', 'pid']):
        try:
            name = process.info['name'] or ''
            if name.lower().startswith(('mumu', 'nemu')):
                processes.append({'name': name[:128], 'pid': process.info['pid']})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    result['mumu_processes'] = processes[:64]
    return result


async def send_report(config, journal, result):
    token = TokenStore(config.state_dir / 'token.dpapi', journal.cipher).read()
    api = Api(config, token, journal)
    try:
        await api.heartbeat(Devices(config, journal).heartbeat_devices(),
                            capabilities={'network_preflight': result})
    finally:
        await api.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('worker/config.local.toml'))
    parser.add_argument('--report-server', action='store_true')
    args = parser.parse_args()
    try:
        config = load(args.config)
        result = collect(config)
        folder = config.state_dir / 'network-preflight'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'latest.json'
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Network preflight report:', path)
        if args.report_server:
            with portalocker.Lock(config.state_dir / 'worker.lock', timeout=0):
                journal = Journal(config.state_dir / 'journal.sqlite3', DPAPI())
                try:
                    asyncio.run(send_report(config, journal, result))
                finally:
                    journal.close()
            print('Network preflight report sent to server. No network settings changed.')
    except portalocker.exceptions.LockException:
        raise SystemExit('Worker/diagnostics already running under this Windows user')
    except (WorkerError, OSError, ValueError):
        raise SystemExit('Network preflight failed; sensitive details omitted') from None


if __name__ == '__main__':
    main()
