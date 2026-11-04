"""Build a single CMD that downloads and verifies a pinned connection package."""
import argparse
import base64
from pathlib import Path
import re
from urllib.parse import urlsplit


def build(package_url, sha256):
    parsed = urlsplit(package_url)
    if (parsed.scheme != 'https' or parsed.hostname != 'github.com' or parsed.query or parsed.fragment
            or not re.fullmatch(r'/[\w-]+/[\w.-]+/raw/[0-9a-f]{40}/downloads/[\w.-]+\.zip', parsed.path)):
        raise ValueError('Use a pinned HTTPS GitHub package URL')
    if not re.fullmatch(r'[0-9a-f]{64}', sha256):
        raise ValueError('SHA256 is required')
    payload = f"""
$ErrorActionPreference='Stop'
try {{
 $folder=Join-Path $env:LOCALAPPDATA 'ShortPost\\windows-control-installer-v2'
 New-Item -ItemType Directory -Path $folder -Force | Out-Null
 $package=Join-Path $folder 'package.zip'
 [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
 Write-Host 'Downloading Windows connection files...'
 Invoke-WebRequest -UseBasicParsing -Uri '{package_url}' -OutFile $package
 if ((Get-FileHash -LiteralPath $package -Algorithm SHA256).Hash -ne '{sha256}') {{ throw 'Downloaded package checksum mismatch. Installation stopped.' }}
 Expand-Archive -LiteralPath $package -DestinationPath $folder -Force
 & (Join-Path $folder 'connect-windows.ps1')
 exit $LASTEXITCODE
}} catch {{
 Write-Host ('Connection startup failed: '+$_.Exception.Message) -ForegroundColor Red
 Read-Host 'Leave this window open. Press Enter only to close it'
 exit 1
}}
"""
    encoded = base64.b64encode(payload.encode('utf-16le')).decode('ascii')
    command = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand ' + encoded
    if len(command) > 7000:
        raise ValueError('Launcher exceeds the Windows CMD length budget')
    return ('@echo off\r\nsetlocal\r\necho Starting ShortPost Windows connection...\r\n' + command +
            '\r\nif errorlevel 1 (\r\n echo Connection did not finish. Keep this window open.\r\n pause\r\n)\r\nendlocal\r\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-url', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.write_bytes(build(args.package_url, args.sha256).encode('ascii'))
