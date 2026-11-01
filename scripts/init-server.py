"""Initialize a NEW deployment only. Never overwrites production configuration."""
import argparse,base64,json,secrets
from pathlib import Path
parser=argparse.ArgumentParser();parser.add_argument('--public-url',required=True)
args=parser.parse_args()
if not args.public_url.startswith('https://'):raise SystemExit('HTTPS public URL required')
root=Path(__file__).resolve().parents[1]/'server'
if (root/'.env').exists():raise SystemExit('.env already exists; leaving it unchanged')
private=root/'secrets';private.mkdir(mode=0o700,exist_ok=True)
password=secrets.token_urlsafe(32);key=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
text=f'POSTGRES_PASSWORD={password}\nDATABASE_URL=postgresql+psycopg://shortpost_owner:{password}@db:5432/shortpost\nENCRYPTION_KEY={key}\nPUBLIC_BASE_URL={args.public_url.rstrip("/")}\nDRIVE_CREDENTIALS_FILE=/run/secrets/drive_credentials\nDRIVE_AUTH_TYPE=oauth\n'
path=root/'.env';path.write_text(text);path.chmod(0o600)
for name in ('drive.json','telegram.json'):
 path=private/name
 if not path.exists():path.write_text('{}')
print('Private new-server config created. Drive/Telegram integrations remain unconfigured.')
