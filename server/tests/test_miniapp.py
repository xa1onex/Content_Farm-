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

def test_batch_sources_and_more_requests_are_durable_and_owned(client,telegram_config):
    from app.models import Setting
    token=login(client).json()['token']
    def headers():return {'Authorization':'Bearer '+token,'Idempotency-Key':__import__('uuid').uuid4().hex}
    h=headers();body={'urls':['https://youtu.be/abcdefghijk','https://youtu.be/01234567890'],'clip_count':8}
    r=client.post('/api/v1/miniapp/farm/batch',json=body,headers=h);assert r.status_code==201 and len(r.json()['items'])==2
    assert client.post('/api/v1/miniapp/farm/batch',json=body,headers=h).json()==r.json()
    with transaction() as s:
        rows=list(s.scalars(__import__('sqlalchemy').select(Setting).where(Setting.key.like('miniapp_farm:%'))))
        assert len(rows)==2 and all(v.value['clip_count']==8 for v in rows)
        s.add(Setting(key='farm_heartbeat',value={'jobs':[{'id':'abc123abc123','user_id':123,'status':'completed','url':body['urls'][0]}]}))
    r=client.post('/api/v1/miniapp/farm/jobs/abc123abc123/more',json={'clip_count':5},headers=headers());assert r.status_code==201
    r=client.post('/api/v1/miniapp/farm/jobs/missing/more',json={'clip_count':5},headers=headers());assert r.status_code==404
    lease=client.post('/api/v1/telegram/farm-requests/claim',headers=auth()).json()['request'];assert lease['user_id']==123 and lease.get('clip_count') in (5,8)

def test_invalid_batch_is_atomic(client,telegram_config):
    from app.models import Setting
    from sqlalchemy import select
    token=login(client).json()['token']
    r=client.post('/api/v1/miniapp/farm/batch',headers={'Authorization':'Bearer '+token,'Idempotency-Key':'atomic-bad-batch'},json={'urls':['https://youtu.be/abcdefghijk','https://evil.test/file']})
    assert r.status_code==422
    with transaction() as s:assert not list(s.scalars(select(Setting).where(Setting.key.like('miniapp_farm:%'))))

def test_ratings_and_comments_are_remembered_without_erasing_each_other(client,seeded,telegram_config):
    token=login(client).json()['token']
    def headers():return {'Authorization':'Bearer '+token,'Idempotency-Key':__import__('uuid').uuid4().hex}
    path='/api/v1/miniapp/content/'+str(seeded['clip'])+'/feedback'
    assert client.post(path,json={'rating':-1},headers=headers()).status_code==200
    r=client.post(path,json={'comment':'Обрезали концовку истории'},headers=headers());assert r.status_code==200 and r.json()['rating']==-1
    rows=client.get('/api/v1/farm/editorial-feedback',headers=auth('farm')).json()['items']
    assert len(rows)==1 and rows[0]['rating']==-1 and 'концовку' in rows[0]['comment']
    r=client.get('/api/v1/miniapp/state',headers=headers());assert len(r.json()['feedback'])==1
    r=client.post(path,json={'rating':1},headers=headers());assert r.json()['comment']=='Обрезали концовку истории'
    assert client.post(path,json={'rating':3},headers=headers()).status_code==422

def test_batch_capacity_includes_processing_jobs(client,telegram_config):
    from app.models import Setting
    with transaction() as s:s.add(Setting(key='farm_heartbeat',value={'jobs':[{'id':'abc','user_id':123,'status':'processing'}]}))
    token=login(client).json()['token']
    r=client.post('/api/v1/miniapp/farm/batch',headers={'Authorization':'Bearer '+token,'Idempotency-Key':'processing-capacity'},json={'urls':['https://youtu.be/test'+str(i) for i in range(10)]})
    assert r.status_code==409 and '9' in r.json()['detail']

def test_farm_progress_is_authenticated_scoped_and_expires(client,seeded,telegram_config):
    from datetime import timedelta
    from app.db import transaction,now
    from app.models import Setting
    from conftest import auth
    stamp=now();user=123
    # Use the actual signed test user from the login fixture.
    session=login(client).json();user=session['user']['id']
    item={'job_id':'a'*12,'queue_position':1,'stage':'transcribe','wait_min_seconds':0,'wait_max_seconds':0,
        'remaining_min_seconds':600,'remaining_max_seconds':1200,'finish_earliest':(stamp+timedelta(seconds=600)).isoformat(),
        'finish_latest':(stamp+timedelta(seconds=1200)).isoformat(),'confidence':'measured','source_duration_seconds':3600,'parts_done':3,'parts_total':6}
    payload={'at':stamp.isoformat(),'items':[item,dict(item,job_id='b'*12,remaining_max_seconds=9999)]}
    assert client.post('/api/v1/farm/progress',json=payload).status_code==401
    assert client.post('/api/v1/farm/progress',headers=auth('worker'),json=payload).status_code==403
    assert client.post('/api/v1/farm/progress',headers=auth('farm'),json=payload).status_code==200
    with transaction() as s:
        s.add(Setting(key='farm_heartbeat',value={'at':stamp.isoformat(),'jobs':[
            {'id':'a'*12,'user_id':user,'status':'processing'},{'id':'b'*12,'user_id':user+1,'status':'queued'}]}))
    headers={'Authorization':'Bearer '+session['token']}
    data=client.get('/api/v1/miniapp/state',headers=headers).json()
    assert len(data['farm_jobs'])==1 and data['farm_jobs'][0]['eta']['parts_done']==3
    assert data['farm_timing']['remaining_max_seconds']==1200 and data['farm_timing']['job_count']==1
    with transaction() as s:
        value=dict(s.get(Setting,'farm_progress').value);value['received_at']=(stamp-timedelta(minutes=4)).isoformat();s.get(Setting,'farm_progress').value=value
    data=client.get('/api/v1/miniapp/state',headers=headers).json()
    assert data['farm_jobs'][0]['eta'] is None and not data['farm_timing']['fresh']

def test_invalid_farm_timing_range_rejected(client):
    from app.db import now
    from conftest import auth
    item={'job_id':'a'*12,'queue_position':1,'stage':'render','wait_min_seconds':0,'wait_max_seconds':0,
          'remaining_min_seconds':1200,'remaining_max_seconds':600,'finish_earliest':now().isoformat(),
          'finish_latest':now().isoformat(),'confidence':'rough','source_duration_seconds':0,'parts_done':0,'parts_total':None}
    assert client.post('/api/v1/farm/progress',headers=auth('farm'),json={'at':now().isoformat(),'items':[item]}).status_code==422
