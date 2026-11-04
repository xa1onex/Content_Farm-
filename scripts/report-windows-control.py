"""Send a small bootstrap status using this PC's existing Worker identity."""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import uuid

import httpx
from shortpost_worker.config import load
from shortpost_worker.crypto import DPAPI, TokenStore


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--stage')
    source.add_argument('--payload-base64')
    parser.add_argument('--message', default='')
    args = parser.parse_args()
    data = {'stage': args.stage, 'message': args.message}
    if args.payload_base64:
        try:
            if len(args.payload_base64) > 40000:
                raise ValueError('Payload too large')
            data = json.loads(base64.b64decode(args.payload_base64, validate=True).decode('utf-8-sig'))
            if not isinstance(data, dict) or not isinstance(data.get('stage'), str):
                raise ValueError('Invalid status')
        except (ValueError, UnicodeError):
            raise SystemExit('Invalid bootstrap status payload') from None
    report = {'schema_version': 1, 'host_platform': 'win32',
              'stage': str(data['stage'])[:80], 'message': str(data.get('message', ''))[:1500],
              'tested_at': datetime.now(timezone.utc).isoformat()}
    diagnostics = data.get('diagnostics')
    if isinstance(diagnostics, dict):
        report['diagnostics'] = {}
        for key in ('service_status', 'service_path'):
            if key in diagnostics:
                report['diagnostics'][key] = str(diagnostics[key])[:500]
        for key, fields in (('events', ('id', 'message')), ('resource_acls', ('resource', 'owner', 'sddl'))):
            if isinstance(diagnostics.get(key), list):
                report['diagnostics'][key] = [
                    {field: str(row[field])[:700] for field in fields if field in row}
                    for row in diagnostics[key][:6] if isinstance(row, dict)]
    folder = Path(os.environ['LOCALAPPDATA']) / 'ShortPost' / 'windows-control-bootstrap'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'latest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    # This report contains installer statuses only, never process environments,
    # raw configuration files, credentials, native command output, or transcripts.
    repo = Path(os.environ['WINDIR']) / 'System32' / 'Content_Farm-'
    try:
        config = load(repo / 'worker' / 'config.local.toml')
        token = TokenStore(config.state_dir / 'token.dpapi', DPAPI()).read()
        with httpx.Client(timeout=15, verify=True, follow_redirects=False) as client:
            response = client.post(config.api_url + '/workers/heartbeat',
                headers={'Authorization': 'Bearer ' + token, 'Idempotency-Key': uuid.uuid4().hex},
                json={'capabilities': {'windows_control_bootstrap': report}, 'devices': []})
            response.raise_for_status()
            if response.json().get('worker_id') != str(config.worker_id):
                raise ValueError('Worker identity mismatch')
        print('Windows control status sent to operator.')
    except Exception:
        print('Status saved locally; server delivery unavailable.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
