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
