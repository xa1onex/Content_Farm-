import uuid
from app.db import transaction
from app.models import Job,Setting
from app.content.moderation import blocked
from conftest import auth
from test_miniapp import telegram_config,init_data

def session_headers(client,user=123):
    session=client.post('/api/v1/miniapp/session',json={'init_data':init_data(uid=user)}).json()
    return {'Authorization':'Bearer '+session['token'],'Idempotency-Key':uuid.uuid4().hex}
def rate(client,clip,rating,user=123):
    return client.post('/api/v1/miniapp/content/'+str(clip)+'/feedback',headers=session_headers(client,user),json={'rating':rating})
def test_dislike_is_personal_feedback_and_does_not_pause_approved_content(client,seeded,telegram_config):
    with transaction() as s:
        s.add(Setting(key='review_policy',value={'enabled':True}));s.add(Setting(key='review_approved:'+str(seeded['clip']),value={}))
    assert rate(client,seeded['clip'],-1).status_code==200
    with transaction() as s:
        assert not blocked(s,seeded['clip']);assert s.get(Job,seeded['job']).status=='scheduled'
    assert client.post('/api/v1/jobs/claim',headers=auth('worker'),json={}).json().get('job')
def test_late_dislike_does_not_revoke_submission_but_explicit_moderation_does(client,seeded,telegram_config):
    r=client.post('/api/v1/jobs/claim',headers=auth('worker'),json={});lease=r.json()['lease_token']
    body={'event_id':uuid.uuid4().hex,'lease_token':lease};base='/api/v1/jobs/'+str(seeded['job'])
    assert client.post(base+'/ack',headers=auth('worker'),json=body).status_code==200
    headers=auth('worker',key='fresh-permission-every-time')
    assert client.post(base+'/authorize-submit',headers=headers,json=body).json()['allowed']
    assert rate(client,seeded['clip'],-1).status_code==200
    assert client.post(base+'/authorize-submit',headers=headers,json=body).json()['allowed']
    r=client.post('/api/v1/miniapp/moderation/batch',headers=auth(),json={'clip_ids':[str(seeded['clip'])],'decision':'rejected'})
    assert r.status_code==200,r.text
    assert client.post(base+'/authorize-submit',headers=headers,json=body).json()['allowed'] is False
    with transaction() as s:assert s.get(Job,seeded['job']).status=='processing'
def test_like_cannot_approve_pending_or_restore_deleted_content(client,seeded,telegram_config):
    with transaction() as s:s.add(Setting(key='review_policy',value={'enabled':True}))
    assert rate(client,seeded['clip'],1).status_code==200
    with transaction() as s:assert blocked(s,seeded['clip'])
    assert client.post('/api/v1/miniapp/moderation/batch',headers=auth(),json={'clip_ids':[str(seeded['clip'])],'decision':'deleted'}).status_code==200
    assert rate(client,seeded['clip'],1,user=124).status_code==200
    with transaction() as s:assert blocked(s,seeded['clip'])
