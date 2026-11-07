import uuid
from datetime import timedelta
import pytest
from sqlalchemy import select
from app.db import transaction, now
from app.models import Setting, Clip
from app.discovery import video_id, save, config, snapshot
from conftest import auth
from test_miniapp import telegram_config
from test_workspace import tgheaders

FIRST='abcdefghijk';SECOND='12345678901'

def seed():
    with transaction() as s:
        save(s,'farm_heartbeat',{'jobs':[{'id':'source000001','user_id':123,'title':'Интервью актёра о съёмках','url':'https://youtu.be/'+FIRST,'status':'completed'}]})

def start(client):
    r=client.post('/api/v1/farm/discovery/123/start',headers=auth('farm'),json={});assert r.status_code==200,r.text
    return r.json()

def deliver(client,context,identifier=SECOND):
    body={'run_id':context['run_id'],'profile':'Интервью','queries':['интересное интервью'],'searched_ids':[identifier], 'candidates':[{'id':identifier,'title':'Другой выпуск','channel':'Канал','duration':1200,'score':90,'reason':'Похожий формат; содержание нужно проверить'}]}
    r=client.post('/api/v1/farm/discovery/123/result',headers=auth('farm'),json=body);assert r.status_code==200,r.text
    return r

def test_normalizes_only_actual_youtube_links():
    for url in ['https://youtu.be/'+FIRST,'https://youtube.com/watch?v='+FIRST+'&t=3','https://m.youtube.com/shorts/'+FIRST]:assert video_id(url)==FIRST
    for url in ['https://evil.com/watch?v='+FIRST,'https://youtube.com.evil.com/watch?v='+FIRST,'https://u:p@youtube.com/watch?v='+FIRST,'file:///a','https://youtu.be/no','https://youtube.com:123/watch?v='+FIRST]:assert video_id(url) is None

def test_discovery_requires_auth_and_role(client):
    assert client.get('/api/v1/farm/discovery').status_code==401
    assert client.post('/api/v1/miniapp/discovery/refresh',headers=auth('farm'),json={}).status_code==403
    assert client.post('/api/v1/miniapp/discovery/refresh',headers=auth(),json={}).status_code==403

def test_accept_is_owned_exactly_once_and_only_analysis(client,telegram_config):
    seed();context=start(client);assert context['started'];assert FIRST in context['excluded_ids'];deliver(client,context)
    path='/api/v1/miniapp/discovery/'+SECOND+'/decision'
    assert client.post(path,headers=tgheaders(client,124),json={'decision':'accepted'}).status_code==404
    h=tgheaders(client);r=client.post(path,headers=h,json={'decision':'accepted','comment':'Нужны живые истории'});assert r.status_code==200,r.text
    assert client.post(path,headers=h,json={'decision':'accepted','comment':'Нужны живые истории'}).json()==r.json()
    assert client.post(path,headers=tgheaders(client),json={'decision':'accepted'}).json()['request_id']==r.json()['request_id']
    with transaction() as s:
        requests=list(s.scalars(select(Setting).where(Setting.key.like('miniapp_farm:%'))));assert len(requests)==1
        assert requests[0].value['mode']=='analyze' and requests[0].value['clip_count'] is None
        assert not list(s.scalars(select(Setting).where(Setting.key.like('review_approved:%'))))

def test_comment_and_rejection_never_queue_and_do_not_resurface(client,telegram_config):
    seed();context=start(client);deliver(client,context)
    path='/api/v1/miniapp/discovery/'+SECOND
    assert client.post(path+'/feedback',headers=tgheaders(client),json={'comment':'Не нужно интервью спортсменов'}).status_code==200
    assert client.post(path+'/decision',headers=tgheaders(client),json={'decision':'rejected'}).status_code==200
    with transaction() as s:
        assert not list(s.scalars(select(Setting).where(Setting.key.like('miniapp_farm:%'))))
        cfg=config(s,123);cfg['next_at']=(now()-timedelta(seconds=1)).isoformat();save(s,'source_discovery:123',cfg)
    context=start(client);assert SECOND in context['excluded_ids'];assert context['source_feedback'][0]['comment']=='Не нужно интервью спортсменов'
    assert deliver(client,context).json()['added']==0
    with transaction() as s:assert snapshot(s,123)['counts']['rejected']==1

def test_leases_capacity_and_stale_results(client):
    seed();one=start(client);assert start(client)['started'] is False
    # Expire the old lease: a new fenced run owns completion.
    with transaction() as s:
        cfg=config(s,123);cfg['lease_until']=(now()-timedelta(seconds=1)).isoformat();save(s,'source_discovery:123',cfg)
    two=start(client);assert two['run_id']!=one['run_id']
    r=client.post('/api/v1/farm/discovery/123/result',headers=auth('farm'),json={'run_id':one['run_id']});assert r.status_code==409
    assert deliver(client,two).json()['added']==1
    assert start(client)['started'] is False

def test_results_must_be_retrieved_ids_and_exclude_processed_at_commit(client):
    seed();context=start(client)
    body={'run_id':context['run_id'],'searched_ids':[], 'candidates':[{'id':SECOND,'title':'Fabricated','duration':200,'score':99,'reason':'Test'}]}
    r=client.post('/api/v1/farm/discovery/123/result',headers=auth('farm'),json=body);assert r.status_code==200 and r.json()['added']==0

def test_comments_and_preferences_feed_context_without_cross_user_leak(client,telegram_config,seeded):
    seed()
    with transaction() as s:
        save(s,'editorial_feedback:'+str(seeded['clip'])+':123',{'clip_id':str(seeded['clip']),'user_id':123,'rating':-1,'comment':'Мало смысла','at':now().isoformat()})
        save(s,'editorial_feedback:'+str(seeded['clip'])+':124',{'clip_id':str(seeded['clip']),'user_id':124,'rating':1,'comment':'PRIVATE OTHER USER','at':now().isoformat()})
    h=tgheaders(client);r=client.post('/api/v1/miniapp/discovery/settings',headers=h,json={'enabled':True,'interval_hours':12,'preferences':'Живые истории'});assert r.status_code==200,r.text
    context=start(client);assert context['preferences']=='Живые истории';assert len(context['clip_feedback'])==1;assert context['clip_feedback'][0]['comment']=='Мало смысла'

def test_global_acceptance_prevents_same_source_for_other_user(client,telegram_config):
    seed();deliver(client,start(client))
    with transaction() as s:
        value=dict(s.get(Setting,'source_pick:123:'+SECOND).value);save(s,'source_pick:124:'+SECOND,value)
    path='/api/v1/miniapp/discovery/'+SECOND+'/decision'
    assert client.post(path,headers=tgheaders(client),json={'decision':'accepted'}).status_code==200
    assert client.post(path,headers=tgheaders(client,124),json={'decision':'accepted'}).status_code==409

def test_error_backoff_and_disabled_picker(client,telegram_config):
    seed();context=start(client)
    r=client.post('/api/v1/farm/discovery/123/result',headers=auth('farm'),json={'run_id':context['run_id'],'error':'search_unavailable'});assert r.status_code==200
    assert start(client)['started'] is False
    r=client.post('/api/v1/miniapp/discovery/settings',headers=tgheaders(client),json={'enabled':False,'interval_hours':6});assert r.status_code==200
    assert start(client)['started'] is False
    assert client.post('/api/v1/miniapp/discovery/refresh',headers=tgheaders(client),json={}).status_code==409

def test_24_pending_cap_leaves_choices_untouched(client):
    seed()
    with transaction() as s:
        for i in range(24):save(s,'source_pick:123:'+str(i).zfill(11),{'id':str(i).zfill(11),'title':'Existing','status':'pending','found_at':now().isoformat()})
    assert start(client)=={'started':False,'reason':'review_full'}

def test_manual_search_cooldown_is_reported_honestly(client,telegram_config):
    seed();h=tgheaders(client)
    assert client.post('/api/v1/miniapp/discovery/refresh',headers=h,json={}).status_code==200
    context=start(client);deliver(client,context)
    r=client.post('/api/v1/miniapp/discovery/refresh',headers=tgheaders(client),json={})
    assert r.status_code==429 and r.headers['Retry-After']=='180'

def test_source_started_during_search_is_excluded_again(client):
    seed();context=start(client)
    with transaction() as s:save(s,'miniapp_farm:manual-request',{'user_id':124,'url':'https://youtu.be/'+SECOND,'status':'submitted','created_at':now().isoformat()})
    assert deliver(client,context).json()['added']==0
