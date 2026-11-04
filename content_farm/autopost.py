"""Register archived clips in ShortPost; stable receipts make retries idempotent."""
import json
import uuid
from pathlib import Path
from urllib.parse import urlsplit
import requests
from config import (AUTOPOST_ENABLED, AUTOPOST_API_URL, AUTOPOST_TOKEN_FILE,
                    AUTOPOST_CATEGORY_ID, AUTOPOST_FORMAT_ID)
from drive_helper_json import save_private_json

NAMESPACE = uuid.UUID('d8475c99-f8c2-4ed7-afbe-764bb368e599')

class RegistrationError(RuntimeError):
    pass

class Client:
    def __init__(self, job_dir=None):
        url = urlsplit(AUTOPOST_API_URL)
        if url.scheme != 'https' or url.username or url.password or url.path.rstrip('/') != '/api/v1':
            raise RegistrationError('Нужен HTTPS-адрес API автопостинга.')
        self.base = AUTOPOST_API_URL.rstrip('/')
        token = json.loads(Path(AUTOPOST_TOKEN_FILE).read_text())['farm_token']
        self.session = requests.Session()
        self.session.headers['Authorization'] = 'Bearer ' + token
        self.job_dir = Path(job_dir) if job_dir else None
    def close(self): self.session.close()
    def get(self, path, **params):
        response = self.session.get(self.base + path, params=params, timeout=(10, 60))
        if response.status_code != 200:
            raise RegistrationError(f'API автопостинга недоступен: HTTP {response.status_code}.')
        return response.json()
    def post(self, path, body, slot):
        receipt_path = self.job_dir / 'autopost_requests.json'
        receipts = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        prior = receipts.get(slot)
        if prior:
            if prior['path'] != path or prior['body'] != body:
                raise RegistrationError('Параметры сохранённой регистрации изменились; нужна сверка.')
            if 'response' in prior:
                return prior['response']
        else:
            prior = {'path':path,'body':body,
                     'key':uuid.uuid5(NAMESPACE, self.job_dir.name + ':' + slot).hex}
            receipts[slot] = prior
            save_private_json(receipt_path, receipts)
        response = self.session.post(self.base + path, json=prior['body'],
            headers={'Idempotency-Key':prior['key']}, timeout=(10, 120))
        if response.status_code == 409:
            raise RegistrationError('Совпадение идентификатора/содержимого: нужна сверка с сервером.')
        if response.status_code not in (200, 201):
            raise RegistrationError(f'Не удалось зарегистрировать ролик: HTTP {response.status_code}.')
        prior['response'] = response.json()
        save_private_json(receipt_path, receipts)
        return prior['response']


def find_existing_clip(sha256):
    if not AUTOPOST_ENABLED:
        return None
    client = Client()
    try:
        rows = client.get('/content', sha256=sha256)['items']
        return next((r for r in rows if r.get('sha256') == sha256), None)
    finally:
        client.close()


def register_job(job_dir):
    if not AUTOPOST_ENABLED:
        return {'enabled':False,'registered':False}
    if not AUTOPOST_CATEGORY_ID or not AUTOPOST_FORMAT_ID:
        raise RegistrationError('Не настроены категория и формат автопостинга.')
    job_dir = Path(job_dir)
    state_path = job_dir / 'state.json'
    state = json.loads(state_path.read_text())
    if not state.get('archived'):
        raise RegistrationError('Сначала нужно сохранить ролики на Google Диске.')
    manifest = json.loads((job_dir / 'manifest.json').read_text())
    client = Client(job_dir)
    try:
        external = 'clipper:' + state['id']
        rows = client.get('/sources', external_id=external)['items']
        source = rows[0] if rows else client.post('/sources', {
            'external_id':external,'title':manifest.get('title') or external,
            'origin':state['source'].get('value','') if state['source']['kind']=='youtube' else '',
            'category_id':AUTOPOST_CATEGORY_ID}, 'source')
        registered = []
        for clip in manifest['clips']:
            record = state['drive_files'][clip['file']]
            rows = client.get('/content',sha256=clip['sha256'])['items']
            existing = next((r for r in rows if r.get('sha256')==clip['sha256']),None)
            if existing is None:
                payload = {'passport':clip['id'],'source_id':source['id'],
                    'drive_file_id':record['id'],'sha256':clip['sha256'],
                    'title':clip.get('description') or 'Интересный момент',
                    'description':clip.get('description',''),
                    'category_id':AUTOPOST_CATEGORY_ID,'format_id':AUTOPOST_FORMAT_ID,
                    'duration_seconds':clip['duration_seconds'],
                    'extra':{'farm':'tg-video-clipper','farm_job_id':state['id'],
                             'file':clip['file'],'clip_id':clip['id']}}
                try:
                    existing = client.post('/content',payload,'content:'+clip['id'])
                except RegistrationError:
                    rows = client.get('/content',sha256=clip['sha256'])['items']
                    existing = next((r for r in rows if r.get('sha256')==clip['sha256']),None)
                    if existing is None:raise
            if existing['status']!='ready' or not existing.get('verified_at'):
                # A failed verification can be retried with a new persisted request slot.
                receipts_path = job_dir/'autopost_requests.json'
                receipts = json.loads(receipts_path.read_text()) if receipts_path.exists() else {}
                prefix = 'verify:'+existing['id']+':'
                pending = next((k for k,v in receipts.items() if k.startswith(prefix) and 'response' not in v),None)
                slot = pending or prefix+str(sum(k.startswith(prefix) for k in receipts))
                verified = client.post('/content/'+existing['id']+'/verify',{},slot)
                if not verified.get('verified'):
                    raise RegistrationError('Сервер не подтвердил доступ или SHA-256 файла Google Drive.')
                existing = verified['content']
            if existing['sha256'] != clip['sha256']:
                raise RegistrationError('Содержимое зарегистрированного ролика не совпало.')
            registered.append({'clip_id':clip['id'],'id':existing['id'],
                               'passport':existing['passport'],'drive_file_id':existing['drive_file_id']})
        state = json.loads(state_path.read_text())
        state['registered'] = len(registered)==len(manifest['clips']) and bool(registered)
        state['autopost_clips'] = registered
        state.pop('autopost_error',None)
        save_private_json(state_path,state)
        return {'enabled':True,'registered':state['registered'],'clips':registered}
    finally:
        client.close()
