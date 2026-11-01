"""Private Google Drive storage. OAuth for My Drive, service accounts for shared drives."""
import json
import mimetypes
import os
from pathlib import Path
import httplib2
from google.auth.transport.requests import Request
from google.oauth2 import credentials, service_account
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from config import (GDRIVE_FOLDER_ID, GDRIVE_TOKEN_FILE, GDRIVE_SERVICE_FILE,
                    MAX_INPUT_BYTES)

SCOPES = ['https://www.googleapis.com/auth/drive']

class DriveConfigurationError(RuntimeError):
    pass

def save_private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd,0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        json.dump(value, out, ensure_ascii=False, indent=2)
    os.replace(temp, path)

class DriveStorage:
    def __init__(self):
        if not GDRIVE_FOLDER_ID:
            raise DriveConfigurationError('Не настроена папка Google Диска.')
        self.oauth = GDRIVE_TOKEN_FILE.exists()
        if self.oauth:
            creds = credentials.Credentials.from_authorized_user_file(GDRIVE_TOKEN_FILE, SCOPES)
            if not creds.valid:
                if not creds.refresh_token:
                    raise DriveConfigurationError('Нужна повторная авторизация Google Диска.')
                creds.refresh(Request())
                save_private_json(GDRIVE_TOKEN_FILE, json.loads(creds.to_json()))
        elif GDRIVE_SERVICE_FILE.exists():
            creds = service_account.Credentials.from_service_account_file(
                GDRIVE_SERVICE_FILE, scopes=SCOPES)
        else:
            raise DriveConfigurationError('Нужна авторизация Google Диска.')
        transport = httplib2.Http(timeout=90)
        # Drive uses 308 for an incomplete resumable upload, handled by
        # googleapiclient. It has no Location header and is not a redirect.
        transport.redirect_codes = frozenset(transport.redirect_codes) - {308}
        self.service = build('drive', 'v3',
            http=AuthorizedHttp(creds, http=transport), cache_discovery=False)
        self.root = self.service.files().get(fileId=GDRIVE_FOLDER_ID,
            supportsAllDrives=True,
            fields='id,name,mimeType,driveId,capabilities(canAddChildren)').execute(num_retries=3)
        if self.root['mimeType'] != 'application/vnd.google-apps.folder':
            raise DriveConfigurationError('GDRIVE_FOLDER_ID должен указывать на папку.')

    def check_writable(self):
        if not self.root.get('capabilities', {}).get('canAddChildren'):
            raise DriveConfigurationError('Нет доступа на запись в папку Google Диска.')
        if not self.oauth and not self.root.get('driveId'):
            raise DriveConfigurationError(
                'Для личного Google Диска нужна OAuth-авторизация владельца. '
                'Сервисный аккаунт может записывать файлы только в общий диск Workspace.')

    def folder(self, name, parent):
        safe = name.replace('\\', '\\\\').replace("'", "\\'")
        result = self.service.files().list(
            q=f"'{parent}' in parents and name='{safe}' and "
              "mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields='files(id)', pageSize=100, supportsAllDrives=True,
            includeItemsFromAllDrives=True).execute(num_retries=3)
        if result.get('files'):
            return result['files'][0]['id']
        return self.service.files().create(body={'name':name,
            'mimeType':'application/vnd.google-apps.folder','parents':[parent]},
            fields='id', supportsAllDrives=True).execute(num_retries=3)['id']

    def job_folder(self, user_id, job_id):
        self.check_writable()
        user_folder = self.folder(f'user_{user_id}', GDRIVE_FOLDER_ID)
        return self.folder(job_id, user_folder)

    def upload(self, path, parent, file_id=None):
        """Deterministic names allow retrying an interrupted archive without duplicates."""
        path = Path(path)
        if not file_id:
            safe = path.name.replace('\\', '\\\\').replace("'", "\\'")
            rows = self.service.files().list(
                q=f"'{parent}' in parents and name='{safe}' and trashed=false",
                fields='files(id)', supportsAllDrives=True,
                includeItemsFromAllDrives=True).execute(num_retries=3).get('files', [])
            file_id = rows[0]['id'] if rows else None
        media = MediaFileUpload(str(path), mimetype=mimetypes.guess_type(path)[0] or
            'application/octet-stream', chunksize=8*1024**2, resumable=True)
        if file_id:
            req = self.service.files().update(fileId=file_id, media_body=media,
                supportsAllDrives=True, fields='id,webViewLink')
        else:
            req = self.service.files().create(body={'name':path.name,'parents':[parent]},
                media_body=media, supportsAllDrives=True, fields='id,webViewLink')
        result = None
        while result is None:
            _, result = req.next_chunk(num_retries=3)
        return {'id':result['id'], 'link':result.get('webViewLink') or
                f"https://drive.google.com/file/d/{result['id']}/view"}

    def metadata(self, file_id):
        info = self.service.files().get(fileId=file_id, supportsAllDrives=True,
            fields='id,name,mimeType,size,parents,trashed,capabilities(canDownload)').execute(num_retries=3)
        if info.get('trashed'):
            raise ValueError('Файл удалён с Google Диска.')
        parents = info.get('parents', [])
        seen = set()
        for _ in range(50):
            if GDRIVE_FOLDER_ID in parents:
                return info
            if not parents:
                break
            parent = parents[0]
            if parent in seen:
                break
            seen.add(parent)
            parents = self.service.files().get(fileId=parent, supportsAllDrives=True,
                fields='parents').execute(num_retries=3).get('parents', [])
        raise ValueError('Видео должно находиться в настроенной папке бота на Google Диске.')

    def download(self, file_id, destination):
        info = self.metadata(file_id)
        if not info.get('mimeType','').startswith('video/'):
            raise ValueError('На Google Диске нужно выбрать видеофайл.')
        if int(info.get('size',0)) > MAX_INPUT_BYTES:
            raise ValueError('Исходное видео слишком большое.')
        if not info.get('capabilities',{}).get('canDownload',True):
            raise ValueError('Владелец запретил скачивание этого файла.')
        try:
            with open(destination, 'wb') as out:
                req = self.service.files().get_media(fileId=file_id, supportsAllDrives=True)
                download = MediaIoBaseDownload(out, req, chunksize=8*1024**2)
                done = False
                while not done:
                    _, done = download.next_chunk(num_retries=3)
                    if out.tell() > MAX_INPUT_BYTES:
                        raise ValueError('Исходное видео слишком большое.')
        except Exception:
            Path(destination).unlink(missing_ok=True)
            raise
        return info

def archive_job(job_dir):
    job_dir = Path(job_dir)
    state_path = job_dir / 'state.json'
    state = json.loads(state_path.read_text())
    drive = DriveStorage()
    folder = state.get('drive_folder_id') or drive.job_folder(state['user_id'], state['id'])
    state['drive_folder_id'] = folder
    state.setdefault('drive_files', {})
    save_private_json(state_path, state)
    files = [p for p in job_dir.glob('source.*') if p.stem=='source'] + list(job_dir.glob('short_*.mp4'))
    files += [job_dir / n for n in ['transcript.json','selection.json','manifest.json']
              if (job_dir / n).exists()]
    files += list(job_dir.glob('subs_*.ass'))
    for path in files:
        if path.name in state['drive_files']:
            continue
        record = drive.upload(path, folder)
        state['drive_files'][path.name] = record
        save_private_json(state_path, state)
    state['archived'] = True
    state.pop('archive_error', None)
    save_private_json(state_path, state)
    return state
