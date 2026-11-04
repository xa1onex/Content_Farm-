"""Send a small bootstrap status using this PC's existing Worker identity."""
import argparse
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
    parser.add_argument('--stage', required=True)
    parser.add_argument('--message', default='')
    args = parser.parse_args()
    report = {'schema_version': 1, 'host_platform': 'win32',
              'stage': args.stage[:80], 'message': args.message[:1500],
              'tested_at': datetime.now(timezone.utc).isoformat()}
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
