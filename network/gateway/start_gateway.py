"""Scheduled Windows launcher; run from the protected gateway state directory."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys


def main():
    root = Path(os.environ['ProgramData']) / 'ShortPost' / 'network-gateway'
    executable = root / 'qemu' / 'qemu-system-x86_64.exe'
    for path in (executable, root / 'gateway.qcow2', root / 'seed.iso', root / 'adapter.json'):
        if not path.is_file():
            raise SystemExit('Gateway prerequisite is missing')
    adapter = json.loads((root / 'adapter.json').read_text(encoding='utf-8-sig'))
    if adapter.get('name') != 'ShortPost-LAN' or adapter.get('created_for') != 'ShortPostGateway':
        raise SystemExit('Gateway adapter ownership is not recorded')
    check = subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
        '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
        str(root / 'verify_adapter.ps1')], stdin=subprocess.DEVNULL, capture_output=True, timeout=40)
    if check.returncode:
        raise SystemExit('Gateway adapter verification failed; routing was not changed')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 10022))
    arguments = [str(executable), '-name', 'ShortPostGateway', '-accel', 'tcg,thread=multi',
        '-machine', 'q35', '-cpu', 'max', '-smp', '2', '-m', '512',
        '-drive', 'file=' + str(root / 'gateway.qcow2') + ',format=qcow2,if=virtio',
        '-drive', 'file=' + str(root / 'seed.iso') + ',format=raw,media=cdrom',
        '-netdev', 'user,id=wan,hostfwd=tcp:127.0.0.1:10022-:22',
        '-device', 'virtio-net-pci,netdev=wan,mac=52:54:00:77:00:01',
        '-netdev', 'tap,id=lan,ifname=ShortPost-LAN,script=no,downscript=no',
        '-device', 'virtio-net-pci,netdev=lan,mac=52:54:00:77:00:02',
        '-display', 'none', '-serial', 'file:' + str(root / 'serial.log'), '-monitor', 'none']
    with (root / 'qemu.stdout').open('ab') as output, (root / 'qemu.stderr').open('ab') as error:
        process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=output, stderr=error)
        (root / 'qemu.pid').write_text(str(process.pid), encoding='ascii')
        return process.wait()


if __name__ == '__main__':
    sys.exit(main())
