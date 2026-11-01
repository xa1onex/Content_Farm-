"""OAuth with PKCE for a headless server; never publish the OAuth tokens."""
import argparse
import json
import secrets
from urllib.parse import parse_qs, urlparse
from google_auth_oauthlib.flow import InstalledAppFlow
from config import BASE_DIR, GDRIVE_CLIENT_FILE, GDRIVE_TOKEN_FILE
from drive_helper import DriveStorage, SCOPES, save_private_json

PENDING = BASE_DIR/'.drive_oauth_pending.json'
REDIRECT = 'http://localhost:8765/'

def auth_url():
    if not GDRIVE_CLIENT_FILE.exists():
        raise RuntimeError('Нужен OAuth client JSON типа Desktop app: oauth_client.json')
    state = secrets.token_urlsafe(32)
    flow = InstalledAppFlow.from_client_secrets_file(GDRIVE_CLIENT_FILE,SCOPES,
        state=state,autogenerate_code_verifier=True)
    flow.redirect_uri = REDIRECT
    url,_ = flow.authorization_url(access_type='offline',prompt='consent')
    save_private_json(PENDING,{'state':state,'code_verifier':flow.code_verifier})
    print('Открой в своём браузере:\n'+url)
    print('\nПосле входа браузер перейдёт на localhost:8765. Если страница недоступна, '
          'это нормально: скопируй полный адрес из адресной строки.\n'
          'На сервере выполни: python setup_drive.py --complete\n'
          'Вставь этот адрес в закрытый запрос ввода. Адрес содержит одноразовый код; '
          'не публикуй его.')

def complete():
    import getpass
    saved = json.loads(PENDING.read_text())
    response = getpass.getpass('Адрес localhost после входа Google (ввод скрыт): ').strip()
    parsed = urlparse(response)
    if (parsed.scheme,parsed.hostname,parsed.port,parsed.path)!=('http','localhost',8765,'/'):
        raise ValueError('Неожиданный адрес перенаправления OAuth.')
    query = parse_qs(parsed.query)
    if not secrets.compare_digest(query.get('state',[''])[0],saved['state']):
        raise ValueError('OAuth state не совпадает. Начни авторизацию заново.')
    code = query.get('code',[''])[0]
    if not code:
        raise ValueError('В адресе отсутствует код авторизации Google.')
    flow = InstalledAppFlow.from_client_secrets_file(GDRIVE_CLIENT_FILE,SCOPES,
        state=saved['state'],code_verifier=saved['code_verifier'])
    flow.redirect_uri = REDIRECT
    flow.fetch_token(code=code)
    if not flow.credentials.refresh_token:
        raise RuntimeError('Google не выдал refresh token. Повтори авторизацию с prompt=consent.')
    save_private_json(GDRIVE_TOKEN_FILE,json.loads(flow.credentials.to_json()))
    PENDING.unlink(missing_ok=True)
    check()

def check():
    drive = DriveStorage()
    drive.check_writable()
    print('Google Диск подключён. Папка:',drive.root['name'])

if __name__=='__main__':
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--auth-url',action='store_true')
    group.add_argument('--complete',action='store_true')
    group.add_argument('--check',action='store_true')
    args = parser.parse_args()
    try:
        auth_url() if args.auth_url else complete() if args.complete else check()
    except Exception as exc:
        print(f'Не удалось подключить Диск: {exc}')
        raise SystemExit(1)
