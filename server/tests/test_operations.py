from datetime import timedelta
from sqlalchemy import select,func
from app.db import transaction,now
from app.models import Worker,Device,Setting,Event
from app.operations import monitor
from conftest import auth

def test_farm_reports_are_scoped_and_dashboard_explains_readiness(client,seeded):
    assert client.get('/api/v1/operations/overview',headers=auth('worker')).status_code==403
    assert client.post('/api/v1/farm/heartbeat',headers=auth('worker'),json={}).status_code==403
    r=client.post('/api/v1/farm/heartbeat',headers=auth('farm'),json={'counts':{'completed':3},'active':[],'disk_free_gb':20,'drive':{'ready':True}})
    assert r.status_code==200,r.text
    data=client.get('/api/v1/operations/overview',headers=auth()).json()
    assert data['farm']['online'] and data['farm']['counts']['completed']==3
    assert 'profile_missing' in data['accounts'][0]['reasons']
    assert data['accounts'][0]['ready'] is False

def test_incident_dedup_and_recovery_are_transitions(seeded):
    with transaction() as s:
        worker=s.get(Worker,seeded['worker']);worker.heartbeat_at=now()-timedelta(hours=1)
        s.flush();monitor(s);s.flush()
        before=s.scalar(select(func.count()).select_from(Event));monitor(s);s.flush()
        assert s.scalar(select(func.count()).select_from(Event))==before
        worker.heartbeat_at=now();s.flush();monitor(s);s.flush()
        recoveries=list(s.scalars(select(Event).where(Event.kind=='system_recovered')))
        assert any(e.detail['code']=='worker_offline' for e in recoveries)

def test_stale_network_never_makes_account_ready(client,seeded):
    with transaction() as s:
        s.get(Worker,seeded['worker']).capabilities={'runtime':{'running':True,'allow_publish':True,'paused':False}}
        s.get(Device,seeded['device']).capabilities={'health':{'at':now().isoformat(),'online':True,'identity_verified':True,
          'profiles':{'tiktok':{'verified':True}},'network':{'at':(now()-timedelta(hours=1)).isoformat(),'ready':True}}}
    data=client.get('/api/v1/operations/overview',headers=auth()).json()
    assert 'proxy_unverified' in data['accounts'][0]['reasons']
    claim=client.post('/api/v1/jobs/claim',headers=auth('worker'),json={})
    assert claim.status_code==200 and claim.json().get('job') is None

def test_global_pause_blocks_new_claims_but_does_not_change_jobs(client,seeded):
    client.post('/api/v1/schedules/control',headers=auth(),json={'enabled':False})
    r=client.post('/api/v1/jobs/claim',headers=auth('worker'),json={})
    assert r.status_code==200 and r.json().get('job') is None


def test_warmup_starts_only_when_fully_ready_and_never_resets(seeded):
    from app.models import Account
    from app.scheduler.service import start_ready_accounts,daily_quota
    from zoneinfo import ZoneInfo
    with transaction() as s:
        a=s.get(Account,seeded['account']);a.warmup_enabled=True;a.daily_limit=3
        start_ready_accounts(s);assert a.operational_started_at is None
        s.get(Worker,seeded['worker']).capabilities={'runtime':{'running':True,'allow_publish':True,'paused':False}}
        s.get(Device,seeded['device']).capabilities={'health':{'at':now().isoformat(),'online':True,'identity_verified':True,
          'profiles':{'tiktok':{'verified':True}},'network':{'at':now().isoformat(),'ready':True}}}
        s.flush();start_ready_accounts(s)
        started=a.operational_started_at;assert started
        day=started.astimezone(ZoneInfo(a.timezone)).date()
        assert [daily_quota(a,day+timedelta(days=i)) for i in range(6)]==[1,1,2,3,3,3]
        a.status='paused';s.flush();start_ready_accounts(s)
        assert a.operational_started_at==started
        a.status='active';s.flush();start_ready_accounts(s)
        assert a.operational_started_at==started


def test_verified_publication_resolution_clears_unknown_outcome(client,seeded):
    from app.models import Job
    with transaction() as s:
        job=s.get(Job,seeded['job']);job.status='unknown';job.worker_id=seeded['worker'];job.device_id=seeded['device']
        job.result={'outcome':'unknown','error_code':'worker_internal_error'}
    result=client.post('/api/v1/jobs/'+str(seeded['job'])+'/action',headers=auth(),json={
      'action':'resolve_published','external_id':'confirmed-post','url':'https://www.tiktok.com/@test/video/1234567890123456',
      'published_at':now().isoformat(),'reason':'Verified author, complete caption and new link; no repeated submission'})
    assert result.status_code==200,result.text
    assert result.json()['status']=='published' and result.json()['result']['outcome']=='published'
    assert result.json()['result']['operator_verified'] is True
