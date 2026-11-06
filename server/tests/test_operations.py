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

def test_incidents_require_persistence_and_recovery_is_not_a_message(seeded,monkeypatch):
    import app.operations as ops
    clock=now();monkeypatch.setattr(ops,'now',lambda:clock)
    with transaction() as s:
        s.flush();monitor(s);s.flush()
        assert s.scalar(select(func.count()).select_from(Event))==0
        clock+=timedelta(seconds=121);monitor(s);s.flush()
        before=s.scalar(select(func.count()).select_from(Event));assert before>0
        monitor(s);s.flush();assert s.scalar(select(func.count()).select_from(Event))==before
        # Missing old problem evidence never emits a green recovery message.
        monkeypatch.setattr(ops,'alertable_problems',lambda snapshot:{})
        monitor(s);s.flush()
        assert s.scalar(select(func.count()).select_from(Event).where(Event.kind=='system_recovered'))==0

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

def test_unchecked_identity_and_network_are_not_failures(client,seeded):
    with transaction() as s:
        s.get(Worker,seeded['worker']).capabilities={'runtime':{'running':True,'allow_publish':True}}
        s.get(Device,seeded['device']).capabilities={'health':{'at':now().isoformat(),'online':True,
            'identity_verified':False,'network':{'at':now().isoformat(),'ready':False}}}
    reasons=client.get('/api/v1/operations/overview',headers=auth()).json()['accounts'][0]['reasons']
    assert 'identity_unverified' in reasons and 'proxy_unverified' in reasons
    assert 'identity_mismatch' not in reasons and 'proxy_unavailable' not in reasons

def test_paused_worker_has_no_device_or_proxy_alerts(client,seeded):
    from app.operations import alertable_problems,overview
    with transaction() as s:
        s.get(Worker,seeded['worker']).capabilities={'runtime':{'running':True,'allow_publish':True,'paused':True}}
        s.get(Device,seeded['device']).capabilities={'health':{'at':now().isoformat(),'online':False,'network':{'at':now().isoformat(),'ready':False}}}
        assert not any(p.get('account') for p in alertable_problems(overview(s)).values())

def test_recovery_and_internal_audit_events_are_never_delivered(client,seeded):
    from app.models import TelegramSubscriber,NotificationDelivery
    with transaction() as s:
        sub=TelegramSubscriber(chat_id=123);s.add(sub);s.flush()
        for kind in ('system_recovered','internal_diagnostic'):
            ev=Event(kind=kind,dedup_key=kind,detail={'code':'proxy_unavailable'});s.add(ev);s.flush()
            s.add(NotificationDelivery(event_id=ev.id,chat_id=123))
    r=client.post('/api/v1/telegram/notifications/claim',headers=auth(),json={})
    assert r.status_code==200 and r.json()['delivery'] is None
    with transaction() as s:
        assert all(d.status=='suppressed' for d in s.scalars(select(NotificationDelivery)))

def test_pending_resolved_problem_is_not_delivered(client,seeded):
    from app.models import TelegramSubscriber,NotificationDelivery
    with transaction() as s:
        s.add(TelegramSubscriber(chat_id=123));s.flush()
        ev=Event(kind='system_problem',dedup_key='old-false-proxy',detail={'code':'proxy_unavailable','account':'test'});s.add(ev);s.flush()
        s.add(NotificationDelivery(event_id=ev.id,chat_id=123))
    r=client.post('/api/v1/telegram/notifications/claim',headers=auth(),json={})
    assert r.status_code==200 and r.json()['delivery'] is None

def test_flapping_outage_has_one_alert_per_hour(seeded,monkeypatch):
    import app.operations as ops
    clock=now();monkeypatch.setattr(ops,'now',lambda:clock)
    problems={'proxy_unavailable:test':{'code':'proxy_unavailable','account':'test','message':'Proxy failed'}}
    monkeypatch.setattr(ops,'alertable_problems',lambda snapshot:dict(problems))
    with transaction() as s:
        monitor(s);clock+=timedelta(seconds=121);monitor(s);s.flush()
        assert s.scalar(select(func.count()).select_from(Event))==1
        problems.clear();monitor(s)
        problems['proxy_unavailable:test']={'code':'proxy_unavailable','account':'test'}
        monitor(s);clock+=timedelta(seconds=121);monitor(s);s.flush()
        assert s.scalar(select(func.count()).select_from(Event))==1

def test_publication_notification_survives_operational_alert_mute(client,seeded):
    from app.models import TelegramSubscriber
    with transaction() as s:
        sub=TelegramSubscriber(chat_id=123);s.add(sub);s.flush()
        s.add(Setting(key='operations_notifications_paused',value={'enabled':True}))
        s.add(Event(kind='publication_published',dedup_key='published-proof',detail={'platform':'instagram','username':'test','url':'https://www.instagram.com/reel/confirmed/'}))
    r=client.post('/api/v1/telegram/notifications/claim',headers=auth(),json={})
    assert r.status_code==200 and 'Ролик опубликован' in r.json()['delivery']['text']

def test_existing_bot_farm_menu_includes_whole_pipeline_timing(client,seeded):
    stamp=now().isoformat()
    item={'job_id':'a'*12,'stage':'transcribe','remaining_min_seconds':600,'remaining_max_seconds':1200,'parts_done':3,'parts_total':6}
    with transaction() as s:
        s.add(Setting(key='farm_heartbeat',value={'at':stamp,'active':[{'id':'a'*12,'stage':'Обработка'}]}))
        s.add(Setting(key='farm_progress',value={'at':stamp,'received_at':stamp,'items':[item]}))
    data=client.get('/api/v1/operations/overview',headers=auth()).json()
    text='\n'.join(j['stage'] for j in data['farm']['active'])
    assert '3/6 частей' in text and '10 мин–20 мин' in text and 'Вся очередь' in text
