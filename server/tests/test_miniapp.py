import hashlib,hmac,json,time
from urllib.parse import urlencode
import pytest
from pydantic import SecretStr
from app.config import settings
from app.db import transaction
from app.models import ClipPlatformUse
from conftest import auth

@pytest.fixture
def telegram_config(monkeypatch):
    monkeypatch.setattr(settings(),'telegram_bot_token',SecretStr('123:test-miniapp'))
    monkeypatch.setattr(settings(),'telegram_allow_all',True)
    return settings()

def init_data(uid=123,stamp=None):
    values={'auth_date':str(stamp if stamp is not None else int(time.time())),'query_id':'test-query','user':json.dumps({'id':uid,'first_name':'Tester'})}
    key=hmac.new(b'WebAppData',b'123:test-miniapp',hashlib.sha256).digest()
    values['hash']=hmac.new(key,'\n'.join(k+'='+v for k,v in sorted(values.items())).encode(),hashlib.sha256).hexdigest()
    return urlencode(values)

def login(client,raw=None):return client.post('/api/v1/miniapp/session',json={'init_data':raw or init_data()})

def test_miniapp_requires_signed_telegram_login(client,telegram_config):
    assert client.get('/miniapp').status_code==200
    assert client.get('/api/v1/miniapp/state').status_code==401
    assert login(client,init_data().replace('Tester','Attacker')).status_code==401
    assert login(client,init_data(stamp=int(time.time())-7200)).status_code==401
    assert login(client,init_data()+'&auth_date=1').status_code==401
    r=login(client);assert r.status_code==200
    token=r.json()['token'];assert token.startswith('tma.')
    assert client.get('/api/v1/miniapp/state',headers={'Authorization':'Bearer '+token}).status_code==200
    assert 'test-miniapp' not in r.text

def test_allowlist_is_checked_on_login_and_session(client,telegram_config,monkeypatch):
    r=login(client);token=r.json()['token']
    monkeypatch.setattr(settings(),'telegram_allow_all',False)
    monkeypatch.setattr(settings(),'telegram_admin_ids',[999])
    assert login(client).status_code==403
    assert client.get('/api/v1/miniapp/state',headers={'Authorization':'Bearer '+token}).status_code==403

def test_dashboard_keeps_used_platform_visible(client,seeded):
    import uuid
    with transaction() as s:s.add(ClipPlatformUse(clip_id=seeded['clip'],platform='instagram',sha256='a'*64,job_id=uuid.uuid4()))
    r=client.get('/api/v1/miniapp/state',headers=auth());assert r.status_code==200
    item=next(c for c in r.json()['content'] if c['id']==str(seeded['clip']))
    assert item['platforms']=={'instagram':'used','tiktok':'reserved','youtube':'available'}
    assert 'lease_hash' not in r.text and 'token_hash' not in r.text

def test_source_request_is_idempotent_and_receipt_fenced(client,telegram_config):
    token=login(client).json()['token'];headers={'Authorization':'Bearer '+token,'Idempotency-Key':'same-source-request'}
    body={'url':'https://youtu.be/test-video'}
    first=client.post('/api/v1/miniapp/farm',json=body,headers=headers);assert first.status_code==201
    second=client.post('/api/v1/miniapp/farm',json=body,headers=headers);assert second.json()==first.json()
    lease=client.post('/api/v1/telegram/farm-requests/claim',headers=auth()).json()['request']
    assert lease['id']==first.json()['id'] and lease['user_id']==123
    path='/api/v1/telegram/farm-requests/'+lease['id']+'/result'
    assert client.post(path,json={'lease':'wrong','job_id':'abc'},headers=auth()).status_code==409
    assert client.post(path,json={'lease':lease['lease'],'job_id':'abc123'},headers=auth()).json()['status']=='submitted'
    assert client.post('/api/v1/telegram/farm-requests/claim',headers=auth()).json()['request'] is None
    assert client.post('/api/v1/miniapp/farm',json={'url':'https://evil.test/video'},headers={**headers,'Idempotency-Key':'bad-source-url'}).status_code==422

def test_forged_session_still_consumes_rate_limit(client,telegram_config,monkeypatch):
    monkeypatch.setattr(settings(),'rate_limit_per_minute',2)
    headers={'Authorization':'Bearer tma.invalid'}
    assert client.get('/api/v1/miniapp/state',headers=headers).status_code==401
    assert client.get('/api/v1/miniapp/state',headers=headers).status_code==401
    assert client.get('/api/v1/miniapp/state',headers=headers).status_code==429

def test_management_snapshot_contains_text_and_safe_drive_link(client,seeded):
    from app.models import Clip,Job
    with transaction() as s:
        s.get(Clip,seeded['clip']).description='Текст будущей публикации'
        s.add(Clip(passport='ENTERTAINMENT-REACTION-000002',drive_file_id='file/with?query',title='Encoded Drive ID',category_id=seeded['category'],format_id=seeded['format'],duration_seconds=12))
        s.get(Job,seeded['job']).result={'error_code':'publication_not_confirmed','private_detail':'never-return-this'}
    data=client.get('/api/v1/miniapp/state',headers=auth()).json()
    clip=next(x for x in data['content'] if x['id']==str(seeded['clip']))
    assert clip['description']=='Текст будущей публикации'
    assert clip['drive_url']=='https://drive.google.com/file/d/drive-file-one/view'
    assert next(x for x in data['content'] if x['title']=='Encoded Drive ID')['drive_url']=='https://drive.google.com/file/d/file%2Fwith%3Fquery/view'
    job=next(x for x in data['queue'] if x['id']==str(seeded['job']))
    assert job['clip_id']==str(seeded['clip']) and job['account_id']==str(seeded['account'])
    assert job['error_code']=='publication_not_confirmed' and 'never-return-this' not in str(data)

def test_signed_dashboard_can_edit_text_pause_and_move_job(client,seeded,telegram_config):
    from datetime import datetime,timedelta,timezone
    token=login(client).json()['token']
    def headers():return {'Authorization':'Bearer '+token,'Idempotency-Key':__import__('uuid').uuid4().hex}
    r=client.patch('/api/v1/content/'+str(seeded['clip']),headers=headers(),json={'title':'Новое название','description':'Описание без ID'})
    assert r.status_code==200 and r.json()['description']=='Описание без ID'
    path='/api/v1/jobs/'+str(seeded['job'])+'/action'
    r=client.post(path,headers=headers(),json={'action':'pause','reason':'Пауза через панель'})
    assert r.status_code==200 and r.json()['status']=='paused'
    when=(datetime.now(timezone.utc)+timedelta(days=1)).replace(hour=12,minute=0,second=0,microsecond=0)
    r=client.post(path,headers=headers(),json={'action':'reschedule','reason':'Перенос через панель','scheduled_at':when.isoformat()})
    assert r.status_code==200 and r.json()['status']=='scheduled'

def test_dashboard_cannot_restart_uncertain_publication(client,seeded,telegram_config):
    from app.models import Job
    with transaction() as s:s.get(Job,seeded['job']).status='unknown'
    token=login(client).json()['token']
    r=client.post('/api/v1/jobs/'+str(seeded['job'])+'/action',headers={'Authorization':'Bearer '+token,'Idempotency-Key':'unknown-never-retry'},json={'action':'pause','reason':'Пауза через панель'})
    assert r.status_code==409
    with transaction() as s:assert s.get(Job,seeded['job']).status=='unknown'

def test_private_video_preview_requires_login_and_streams_only_media(client,seeded,telegram_config,monkeypatch):
    from app.integrations import drive
    calls=[]
    def download(file_id,max_bytes):
        calls.append((file_id,max_bytes));return {'mimeType':'video/mp4','size':'12'},iter([b'test-preview'])
    monkeypatch.setattr(drive,'download',download)
    path='/api/v1/miniapp/content/'+str(seeded['clip'])+'/preview'
    assert client.get(path).status_code==401 and not calls
    token=login(client).json()['token']
    r=client.get(path,headers={'Authorization':'Bearer '+token})
    assert r.status_code==200 and r.content==b'test-preview'
    assert r.headers['content-type']=='video/mp4'
    assert r.headers['cache-control']=='no-store'
    assert calls==[('drive-file-one',128*1024**2)]

def test_preview_limit_closes_google_connection_before_downloading(client,seeded,monkeypatch):
    from app.integrations import drive
    class FakeSession:
        closed=False
        def close(self):self.closed=True
        def get(self,*args,**kwargs):raise AssertionError('Oversized media must not be requested')
    fake=FakeSession()
    monkeypatch.setattr(drive,'session',lambda:fake)
    monkeypatch.setattr(drive,'get_info',lambda *args:{'size':str(129*1024**2),'mimeType':'video/mp4'})
    r=client.get('/api/v1/miniapp/content/'+str(seeded['clip'])+'/preview',headers=auth())
    assert r.status_code==503 and r.json()['detail']=='preview_too_large'
    assert fake.closed
