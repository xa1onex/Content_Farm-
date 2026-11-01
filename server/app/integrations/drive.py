from pathlib import Path
import requests
import hashlib
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import credentials, service_account
from app.config import settings

class DriveError(Exception):
    def __init__(self,code='drive_unavailable'):
        self.code=code
        super().__init__(code)

def session():
    cfg=settings()
    path=Path(cfg.drive_credentials_file)
    if not path.is_file():raise DriveError('drive_not_configured')
    try:
        if cfg.drive_auth_type=='service_account':
            creds=service_account.Credentials.from_service_account_file(str(path),
                scopes=['https://www.googleapis.com/auth/drive.readonly'])
        else:
            # Preserve the granted OAuth scopes; no writes are made by this adapter.
            creds=credentials.Credentials.from_authorized_user_file(str(path))
        return AuthorizedSession(creds,refresh_timeout=15,max_refresh_attempts=1)
    except (ValueError,OSError):raise DriveError('drive_invalid_credentials')

def get_info(client,file_id):
    try:
        response=client.get(f'https://www.googleapis.com/drive/v3/files/{file_id}',
            params={'supportsAllDrives':'true',
                'fields':'id,name,mimeType,size,trashed,sha256Checksum,capabilities(canDownload)'},timeout=20)
        if response.status_code!=200:raise DriveError('drive_file_inaccessible')
        info=response.json()
        if info.get('trashed') or not info.get('capabilities',{}).get('canDownload',True):
            raise DriveError('drive_file_unavailable')
        if not info.get('mimeType','').startswith('video/'):raise DriveError('drive_not_video')
        if not 0<int(info.get('size',0))<=2*1024**3:raise DriveError('drive_invalid_size')
        return info
    except DriveError:raise
    except Exception:raise DriveError()

def metadata(file_id):
    with session() as client:return get_info(client,file_id)

def download(file_id):
    client=session()
    try:
        info=get_info(client,file_id)
        response=client.get(f'https://www.googleapis.com/drive/v3/files/{file_id}',
            params={'alt':'media','supportsAllDrives':'true'},stream=True,timeout=(10,30))
        if response.status_code!=200:raise DriveError('drive_download_failed')
    except Exception as exc:
        client.close()
        if isinstance(exc,DriveError):raise
        raise DriveError()
    def chunks():
        try:
            total=0
            for chunk in response.iter_content(256*1024):
                if chunk:
                    total+=len(chunk)
                    if total>2*1024**3:raise DriveError('drive_invalid_size')
                    yield chunk
            if total!=int(info['size']):raise DriveError('drive_truncated_download')
        finally:
            response.close();client.close()
    return info,chunks()


def sha256(file_id):
    info,chunks=download(file_id)
    checksum=hashlib.sha256()
    for chunk in chunks:checksum.update(chunk)
    return checksum.hexdigest()
