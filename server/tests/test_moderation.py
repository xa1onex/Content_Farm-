import uuid
from sqlalchemy import select
from app.db import transaction,now
from app.models import Job,Clip,Setting
from app.content.moderation import blocked
from conftest import auth

from test_miniapp import telegram_config,init_data

def rate(client,clip,rating,user=123):
    session=client.post('/api/v1/miniapp/session',json={'init_data':init_data(uid=user)}).json()
    return client.post('/api/v1/miniapp/content/'+str(clip)+'/feedback',headers={'Authorization':'Bearer '+session['token'],'Idempotency-Key':uuid.uuid4().hex},json={'rating':rating})

def test_dislike_pauses_waiting_jobs_and_blocks_every_platform(client,seeded,telegram_config):
    r=rate(client,seeded['clip'],-1);assert r.status_code==200,r.text
    with transaction() as s:
        assert blocked(s,seeded['clip']);job=s.get(Job,seeded['job']);assert job.status=='paused'
        assert job.result['error_code']=='editorial_rejected'
    r=client.post('/api/v1/jobs/claim',headers=auth('worker'),json={});assert r.status_code==200 and r.json().get('job') is None
    from datetime import timedelta
    moved=client.post('/api/v1/jobs/'+str(seeded['job'])+'/action',headers=auth(),json={'action':'reschedule','scheduled_at':(now()+timedelta(hours=1)).isoformat(),'reason':'Try rescheduling rejected content'})
    assert moved.status_code==409 and 'дизлайком' in moved.json()['detail']
    from app.scheduler.service import create_job
    from app.models import Account
    from fastapi import HTTPException
    import pytest
    with transaction() as s:
        with pytest.raises(HTTPException,match='дизлайком'):create_job(s,s.get(Account,seeded['account']),s.get(Clip,seeded['clip']),now())

def test_late_dislike_revokes_immediate_submit_permission_even_with_same_request_key(client,seeded,telegram_config):
    r=client.post('/api/v1/jobs/claim',headers=auth('worker'),json={});lease=r.json()['lease_token']
    body={'event_id':uuid.uuid4().hex,'lease_token':lease};base='/api/v1/jobs/'+str(seeded['job'])
    assert client.post(base+'/ack',headers=auth('worker'),json=body).status_code==200
    headers=auth('worker',key='fresh-permission-every-time')
    assert client.post(base+'/authorize-submit',headers=headers,json=body).json()['allowed']
    assert rate(client,seeded['clip'],-1).status_code==200
    denied=client.post(base+'/authorize-submit',headers=headers,json=body)
    assert denied.status_code==200 and denied.json()=={'allowed':False,'reason':'editorial_rejected'}
    assert client.post(base+'/media-ticket',headers=auth('worker'),json=body).status_code==409
    with transaction() as s:assert s.get(Job,seeded['job']).status=='processing'

def test_another_like_or_comment_cannot_override_existing_dislike(client,seeded,telegram_config):
    assert rate(client,seeded['clip'],-1).status_code==200
    assert rate(client,seeded['clip'],1,user=124).status_code==200
    session=client.post('/api/v1/miniapp/session',json={'init_data':init_data()}).json()
    r=client.post('/api/v1/miniapp/content/'+str(seeded['clip'])+'/feedback',headers={'Authorization':'Bearer '+session['token'],'Idempotency-Key':uuid.uuid4().hex},json={'comment':'Плохая нарезка'})
    assert r.status_code==200
    with transaction() as s:assert blocked(s,seeded['clip'])
    assert rate(client,seeded['clip'],1).status_code==200
    with transaction() as s:
        assert not blocked(s,seeded['clip']);assert s.get(Job,seeded['job']).status=='paused'
