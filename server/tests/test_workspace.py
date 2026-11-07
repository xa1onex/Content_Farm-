import uuid
from datetime import datetime,timedelta,timezone
from sqlalchemy import select
from app.db import transaction,now
from app.models import Clip,Job,Setting,Account,Publication,Snapshot,ClipPlatformUse
from app.workspace import supply,latest_metrics,publication_timing
from conftest import auth
from test_miniapp import telegram_config,init_data

def moderate(client,ids,decision):return client.post('/api/v1/miniapp/moderation/batch',headers=auth(),json={'clip_ids':[str(i) for i in ids],'decision':decision})
def tgheaders(client,user=123):
    r=client.post('/api/v1/miniapp/session',json={'init_data':init_data(uid=user)});assert r.status_code==200,r.text
    return {'Authorization':'Bearer '+r.json()['token'],'Idempotency-Key':uuid.uuid4().hex}
def test_bulk_moderation_is_atomic_and_restore_requires_new_approval(client,seeded):
    ids=[seeded['clip'],uuid.uuid4()]
    assert moderate(client,ids,'approved').status_code==404
    with transaction() as s:assert s.get(Setting,'review_approved:'+str(ids[0])) is None
    assert moderate(client,ids[:1],'approved').status_code==200
    assert moderate(client,ids[:1],'deleted').status_code==200
    assert moderate(client,ids[:1],'approved').status_code==409
    with transaction() as s:
        assert s.get(Job,seeded['job']).status=='paused';assert s.get(Clip,seeded['clip']).status=='archived'
        assert s.get(Setting,'review_approved:'+str(ids[0])) is None
    assert moderate(client,ids[:1],'restore').status_code==200
    data=client.get('/api/v1/miniapp/library?filter=pending',headers=auth()).json()
    assert data['items'][0]['decision']=='pending'
def test_pagination_and_filters_include_entire_library_and_preserve_platform_use(client,seeded):
    with transaction() as s:
        for i in range(66):s.add(Clip(passport='CF-'+uuid.uuid4().hex.upper(),drive_file_id='file'+str(i),title='New '+str(i),category_id=seeded['category'],format_id=seeded['format'],duration_seconds=20,status='ready',verified_at=now(),sha256=f'{i+10:064x}'))
        s.add(ClipPlatformUse(clip_id=seeded['clip'],platform='instagram',sha256='a'*64,job_id=uuid.uuid4()))
    p1=client.get('/api/v1/miniapp/library?limit=60',headers=auth()).json();p2=client.get('/api/v1/miniapp/library?offset=60',headers=auth()).json()
    assert p1['total']==67 and p1['has_more'] and len(p2['items'])==7
    assert not set(c['id'] for c in p1['items'])&set(c['id'] for c in p2['items'])
    assert moderate(client,[seeded['clip']],'deleted').status_code==200
    data=client.get('/api/v1/miniapp/library?filter=deleted',headers=auth()).json()
    assert data['total']==1 and data['items'][0]['platforms']['instagram']=='used'
def test_latest_views_do_not_double_count_or_turn_missing_into_zero(client,seeded):
    initial=client.get('/api/v1/miniapp/state',headers=auth()).json()['workspace']
    assert initial['views']['total'] is None
    with transaction() as s:
        p=Publication(job_id=seeded['job'],clip_id=seeded['clip'],account_id=seeded['account'],published_at=now(),external_id='test-reel',url='https://example.com/reel');s.add(p);s.flush();pid=p.id
        s.add_all([Snapshot(publication_id=pid,event_id=uuid.uuid4().hex,views=10,measured_at=now()-timedelta(minutes=2)),Snapshot(publication_id=pid,event_id=uuid.uuid4().hex,views=20,measured_at=now()-timedelta(minutes=1)),Snapshot(publication_id=pid,event_id=uuid.uuid4().hex,views=None,measured_at=now())])
    data=client.get('/api/v1/miniapp/state',headers=auth()).json()['workspace']
    assert data['views']['total']==20 and data['views']['measured_publications']==1
    assert data['today']['published']==1

def test_stock_matches_unique_clips_to_quota_and_excludes_unreviewed_and_consumed(client,seeded):
    fixed=datetime(2026,11,7,4,tzinfo=timezone.utc)
    with transaction() as s:
        a=s.get(Account,seeded['account']);a.daily_limit=1;a.timezone='Europe/Moscow'
        s.delete(s.get(Job,seeded['job']));s.flush()
        s.add(Setting(key='review_approved:'+str(seeded['clip']),value={}))
        for i in range(3):
            c=Clip(passport='CF-'+uuid.uuid4().hex.upper(),drive_file_id='stock'+str(i),title='Stock',category_id=seeded['category'],format_id=seeded['format'],duration_seconds=20,status='ready',verified_at=now(),sha256=f'{i+20:064x}')
            s.add(c);s.flush()
            if i<2:s.add(Setting(key='review_approved:'+str(c.id),value={}))
        s.flush();r=supply(s,[a],fixed);assert r['days']==3 and r['tone']=='green',r
        s.add(ClipPlatformUse(clip_id=seeded['clip'],platform='tiktok',sha256='a'*64,job_id=uuid.uuid4()));s.flush()
        r=supply(s,[a],fixed);assert r['days']==2 and r['tone']=='yellow',r
        first=s.scalar(select(Setting).where(Setting.key.like('review_approved:%'),Setting.key!='review_approved:'+str(seeded['clip'])))
        s.delete(first);s.flush();r=supply(s,[a],fixed);assert r['days']==1 and r['tone']=='red',r

def test_campaign_blockage_propagates_and_serial_forecast_does_not_overlap(client,seeded):
    with transaction() as s:
        a=s.get(Account,seeded['account']);first=s.get(Job,seeded['job']);first.status='unknown'
        chain=[first]
        for i in range(2):
            c=Clip(passport='CF-'+uuid.uuid4().hex.upper(),drive_file_id='chain'+str(i),title='Chain',category_id=seeded['category'],format_id=seeded['format'],duration_seconds=20);s.add(c);s.flush()
            j=Job(platform=a.platform,clip_id=c.id,account_id=a.id,local_date=first.local_date,slot=i+2,scheduled_at=first.scheduled_at+timedelta(minutes=i+1));s.add(j);s.flush()
            s.add(Setting(key='campaign_job:'+str(j.id),value={'previous_job_id':str(chain[-1].id),'delay_seconds':60}));chain.append(j)
        s.flush();result,_=publication_timing(s,chain,[],now())
        assert all(result[str(j.id)]['blocked'] for j in chain[1:])
        first.status='scheduled';s.flush();result,_=publication_timing(s,chain,[],now())
        assert result[str(chain[2].id)]['start_earliest']>result[str(chain[1].id)]['finish_latest']

def test_analyze_then_render_is_owned_and_durably_idempotent(client,telegram_config):
    h=tgheaders(client);url='https://youtu.be/abcdefghijk'
    r=client.post('/api/v1/miniapp/farm/analyze',headers=h,json={'urls':[url]});assert r.status_code==201,r.text
    assert client.post('/api/v1/miniapp/farm/analyze',headers=h,json={'urls':[url]}).json()==r.json()
    lease=client.post('/api/v1/telegram/farm-requests/claim',headers=auth()).json()['request'];assert lease['mode']=='analyze'
    with transaction() as s:s.add(Setting(key='farm_heartbeat',value={'jobs':[{'id':'abc123abc123','user_id':123,'url':url,'status':'analysis_ready'}]}))
    path='/api/v1/miniapp/farm/jobs/abc123abc123/render';r=client.post(path,headers=tgheaders(client,124),json={'clip_count':5});assert r.status_code==404
    h2=tgheaders(client);r=client.post(path,headers=h2,json={'clip_count':5});assert r.status_code==201,r.text
    assert client.post(path,headers=h2,json={'clip_count':5}).json()==r.json()
    assert client.post(path,headers=tgheaders(client),json={'clip_count':5}).status_code==409
    with transaction() as s:
        render=[v.value for v in s.scalars(select(Setting).where(Setting.key.like('miniapp_farm:%'))) if v.value.get('mode')=='render'][0]
        assert render['render_job_id']=='abc123abc123' and render['clip_count']==5

def test_library_returns_spoken_text_separately_from_title_description_and_private_extra(client,seeded):
    with transaction() as s:
        c=s.get(Clip,seeded['clip']);c.title='Заголовок';c.description='Описание публикации'
        c.extra={'farm_job_id':'abc123abc123','private_key':'not-for-dashboard','transcript':{
            'text':'Первая реплика.\n\nВторая реплика <текст>.','language':'ru','start_seconds':100,
            'end_seconds':120,'internal':'not-for-dashboard'}}
    response=client.get('/api/v1/miniapp/library?filter=all',headers=auth());assert response.status_code==200
    row=response.json()['items'][0]
    assert row['transcript']=={'text':'Первая реплика.\n\nВторая реплика <текст>.','language':'ru'}
    assert row['description']=='Описание публикации' and 'not-for-dashboard' not in response.text
    assert client.get('/api/v1/miniapp/library').status_code==401

def test_missing_transcript_is_explicit_and_does_not_use_caption_as_speech(client,seeded):
    response=client.get('/api/v1/miniapp/library',headers=auth()).json()
    assert response['items'][0]['transcript'] is None
