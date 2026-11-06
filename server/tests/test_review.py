import hashlib,time,uuid
from app.db import transaction
from app.models import Setting,Job,Clip
from app.content.moderation import blocked

TOKEN='review-test-'+('x'*40)
def link(user=123,expires=None):
    with transaction() as s:s.add(Setting(key='review_link:'+hashlib.sha256(TOKEN.encode()).hexdigest(),value={'user_id':user,'expires':expires or time.time()+3600}))
def headers():return {'X-Review-Key':TOKEN}

def test_review_capability_is_scoped_and_expires(client,seeded):
    assert client.get('/api/v1/review').status_code==401
    link(expires=time.time()-1)
    assert client.get('/api/v1/review',headers=headers()).status_code==401
    assert client.get('/api/v1/miniapp/state',headers=headers()).status_code==401
    assert client.get('/review').status_code==200

def test_review_decisions_persist_and_feed_editorial_memory(client,seeded):
    link();base='/api/v1/review/'+str(seeded['clip'])
    assert len(client.get('/api/v1/review',headers=headers()).json()['items'])==1
    r=client.post(base,headers=headers(),json={'decision':'approved','comment':'Хорошая развязка'})
    assert r.status_code==200 and r.json()['items'][0]['decision']=='approved'
    r=client.post(base,headers=headers(),json={'decision':'deleted','comment':'Начало затянуто'})
    assert r.status_code==200 and r.json()['items'][0]['decision']=='deleted'
    with transaction() as s:
        assert s.get(Clip,seeded['clip']).status=='archived'
        assert s.get(Job,seeded['job']).status=='paused'
        assert blocked(s,seeded['clip'])
        f=s.get(Setting,'editorial_feedback:'+str(seeded['clip'])+':123').value
        assert f['rating']==-1 and f['comment']=='Начало затянуто'
    assert client.post(base,headers=headers(),json={'decision':'approved'}).status_code==409
    assert client.post(base,headers=headers(),json={'comment':'Без развязки'}).status_code==200
    assert client.post(base,headers=headers(),json={'comment':'a'*1001}).status_code==422
    # Even later direct positive feedback must not resurrect deleted content.
    from app.editorial import feedback_record,FeedbackInput
    with transaction() as s:
        feedback_record(s,s.get(Clip,seeded['clip']),123,FeedbackInput(rating=1))
        assert blocked(s,seeded['clip'])

def test_review_policy_holds_unapproved_content_until_explicit_decision(client,seeded):
    link()
    with transaction() as s:s.add(Setting(key='review_policy',value={'enabled':True}))
    with transaction() as s:assert blocked(s,seeded['clip'])
    from conftest import auth
    assert client.get('/api/v1/miniapp/state',headers=auth()).json()['content'][0]['platforms']['tiktok']=='review_pending'
    assert client.post('/api/v1/jobs/claim',headers=auth('worker'),json={}).json().get('job') is None
    r=client.post('/api/v1/review/'+str(seeded['clip']),headers=headers(),json={'decision':'approved'})
    assert r.status_code==200
    with transaction() as s:assert not blocked(s,seeded['clip'])
    assert client.post('/api/v1/jobs/claim',headers=auth('worker'),json={}).json().get('job')
