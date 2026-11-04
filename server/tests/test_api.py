import uuid
from datetime import timedelta
from sqlalchemy import select,func
from app.db import transaction,now
from app.models import Job,Publication,Snapshot,Clip
from app.integrations.drive import DriveError
from conftest import auth

def test_authentication_and_roles(client,seeded):
    assert client.get('/api/v1/accounts').status_code==401
    assert client.get('/api/v1/accounts',headers=auth('farm')).status_code==403
    assert client.get('/api/v1/accounts',headers=auth()).status_code==200
    assert client.get('/api/v1/content',headers=auth('worker')).status_code==403

def test_api_idempotency_and_conflicting_reuse(client):
    headers=auth(key='stable-request-key')
    data={'code':'ENTERTAINMENT','name':'Entertainment'}
    a=client.post('/api/v1/taxonomy/categories',json=data,headers=headers)
    b=client.post('/api/v1/taxonomy/categories',json=data,headers=headers)
    assert a.status_code==b.status_code==201
    assert a.json()==b.json()
    assert client.post('/api/v1/taxonomy/categories',json=data|{'name':'other'},headers=headers).status_code==409

def test_missing_idempotency_and_invalid_input(client):
    assert client.post('/api/v1/taxonomy/categories',json={'code':'VALID','name':'Test'},headers={'Authorization':'Bearer admin-test-token'}).status_code==400
    assert client.post('/api/v1/taxonomy/categories',json={'code':'invalid code','name':'Test'},headers=auth()).status_code==422

def test_content_registration_and_atomic_batch(client,seeded):
    body={'drive_file_id':'different-drive-file','title':'New','category_id':str(seeded['category']),
        'format_id':str(seeded['format']),'duration_seconds':20}
    # Supplied passport already reserves sequence 1; use an explicit distinct ID in this fixture.
    body['passport']='ENTERTAINMENT-REACTION-000090'
    response=client.post('/api/v1/content',json=body,headers=auth('farm'))
    assert response.status_code==201
    assert response.json()['status']=='draft'
    before=client.get('/api/v1/content',headers=auth('farm')).json()['items']
    data=[body|{'passport':'ENTERTAINMENT-REACTION-000091','drive_file_id':'batch-first'},body]
    assert client.post('/api/v1/content/batch',json=data,headers=auth('farm')).status_code==409
    assert len(client.get('/api/v1/content',headers=auth('farm')).json()['items'])==len(before)

def test_unverified_content_cannot_be_set_ready(client,seeded):
    with transaction() as s:
        c=s.get(Clip,seeded['clip']);c.status='draft';c.verified_at=None
    assert client.patch('/api/v1/content/'+str(seeded['clip']),json={'status':'ready'},headers=auth()).status_code==409

def test_drive_unavailable_persists_unavailable_state(client,seeded,monkeypatch):
    def unavailable(*_):raise DriveError('drive_unavailable')
    monkeypatch.setattr('app.integrations.drive.metadata',unavailable)
    res=client.post(f'/api/v1/content/{seeded["clip"]}/verify',headers=auth('farm'))
    assert res.status_code==200
    assert res.json()['verified'] is False
    with transaction() as s:assert s.get(Clip,seeded['clip']).status=='unavailable'

def test_drive_verify_success_with_mock(client,seeded,monkeypatch):
    monkeypatch.setattr('app.integrations.drive.metadata',lambda _: {'id':'drive-file-one','mimeType':'video/mp4','size':'120','sha256Checksum':'a'*64})
    res=client.post(f'/api/v1/content/{seeded["clip"]}/verify',headers=auth('farm'))
    assert res.status_code==200 and res.json()['verified']

def test_full_http_worker_and_statistics_cycle(client,seeded):
    headers=auth('worker')
    heartbeat=client.post('/api/v1/workers/heartbeat',json={'capabilities':{},'devices':[]},headers=headers)
    assert heartbeat.status_code==200
    response=client.post('/api/v1/jobs/claim',headers=auth('worker'))
    assert response.status_code==200
    payload=response.json();job=payload['job']['id'];token=payload['lease_token']
    lease={'event_id':uuid.uuid4().hex,'lease_token':token}
    assert client.post(f'/api/v1/jobs/{job}/ack',json=lease,headers=auth('worker')).json()['status']=='processing'
    ticket=client.post(f'/api/v1/jobs/{job}/media-ticket',json=lease,headers=auth('worker'))
    assert ticket.status_code==200 and 'ticket' in ticket.json()
    data=lease|{'event_id':uuid.uuid4().hex,'outcome':'published','external_id':'mock-http-post','published_at':now().isoformat()}
    result=client.post(f'/api/v1/jobs/{job}/result',json=data,headers=auth('worker'))
    assert result.status_code==200 and result.json()['status']=='published'
    publication=result.json()['publication_id']
    statistics={'publication_id':publication,'event_id':uuid.uuid4().hex,'measured_at':now().isoformat(),'views':50,'likes':3}
    a=client.post('/api/v1/analytics/snapshots',json=statistics,headers=auth('worker'))
    b=client.post('/api/v1/analytics/snapshots',json=statistics,headers=auth('worker'))
    assert a.status_code==b.status_code==201 and a.json()==b.json()
    report=client.get('/api/v1/analytics/report',headers=auth()).json()
    assert report['items'][0]['views']==50
    assert report['items'][0]['shares'] is None
    assert client.get(f'/api/v1/jobs/{job}/video',headers=auth('worker')|{'X-Media-Ticket':ticket.json()['ticket']}).status_code==403

def test_worker_isolation_and_no_hash_disclosure(client,seeded):
    client.post('/api/v1/jobs/claim',headers=auth('worker'))
    assert client.get(f'/api/v1/jobs/{seeded["job"]}',headers=auth('worker2')).status_code==403
    listed=client.get('/api/v1/jobs',headers=auth()).json()
    assert 'lease_hash' not in listed['items'][0]
    tokens=client.get('/api/v1/tokens',headers=auth()).json()
    assert all('token_hash' not in item for item in tokens['items'])

def test_requested_claim_does_not_fall_back_to_another_job(client,seeded):
    missing=client.post('/api/v1/jobs/claim',json={'job_id':str(uuid.uuid4())},headers=auth('worker'))
    assert missing.status_code==200 and missing.json().get('job') is None
    with transaction() as s:assert s.get(Job,seeded['job']).status=='scheduled'
    selected=client.post('/api/v1/jobs/claim',json={'job_id':str(seeded['job'])},headers=auth('worker'))
    assert selected.status_code==200 and selected.json()['job']['id']==str(seeded['job'])

def test_requested_claim_preserves_worker_and_account_restrictions(client,seeded):
    from app.models import Account
    data={'job_id':str(seeded['job'])}
    other=client.post('/api/v1/jobs/claim',json=data,headers=auth('worker2'))
    assert other.status_code==200 and other.json().get('job') is None
    with transaction() as s:s.get(Account,seeded['account']).status='paused'
    paused=client.post('/api/v1/jobs/claim',json=data,headers=auth('worker'))
    assert paused.status_code==200 and paused.json().get('job') is None
    with transaction() as s:assert s.get(Job,seeded['job']).status=='scheduled'

def test_unknown_requires_operator_reconciliation(client,seeded):
    with transaction() as s:s.get(Job,seeded['job']).status='unknown'
    path=f'/api/v1/jobs/{seeded["job"]}/action'
    assert client.post(path,json={'action':'retry','reason':'test retry','scheduled_at':(now()+timedelta(hours=1)).isoformat()},headers=auth()).status_code==409
    assert client.post(path,json={'action':'resolve_not_published','reason':'Operator verified account history'},headers=auth()).status_code==200
    assert client.post(path,json={'action':'retry','reason':'Safe retry after verification','scheduled_at':(now()+timedelta(hours=1)).isoformat()},headers=auth()).status_code==200

def test_rate_limiter(client,monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings(),'rate_limit_per_minute',2)
    assert client.get('/api/v1/accounts',headers=auth()).status_code==200
    assert client.get('/api/v1/accounts',headers=auth()).status_code==200
    assert client.get('/api/v1/accounts',headers=auth()).status_code==429

def test_invalid_account_timezone(client):
    res=client.post('/api/v1/accounts',json={'platform':'tiktok','name':'x','username':'x','timezone':'bad/tz'},headers=auth())
    assert res.status_code==422

def test_auto_passport_skips_an_existing_external_id(client,seeded):
    body={'drive_file_id':'auto-new-drive-file','title':'Auto ID','category_id':str(seeded['category']),
        'format_id':str(seeded['format']),'duration_seconds':20}
    response=client.post('/api/v1/content',json=body,headers=auth('farm'))
    assert response.status_code==201
    assert response.json()['passport']=='ENTERTAINMENT-REACTION-000002'

def test_content_lookup_by_sha256_is_exact(client,seeded):
    yes=client.get('/api/v1/content',params={'sha256':'a'*64},headers=auth('farm'))
    assert yes.status_code==200 and len(yes.json()['items'])==1
    no=client.get('/api/v1/content',params={'sha256':'b'*64},headers=auth('farm'))
    assert no.status_code==200 and no.json()['items']==[]
    assert client.get('/api/v1/content',params={'sha256':'invalid'},headers=auth('farm')).status_code==422
