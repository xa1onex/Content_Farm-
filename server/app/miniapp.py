import json,time,uuid
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit,quote
from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import FileResponse,StreamingResponse
from pydantic import BaseModel,Field
from sqlalchemy import select
from app.db import db_session,now
from app.security import roles,idempotent,lock
from app.models import Account,Clip,ClipPlatformUse,Device,Event,Job,Publication,Setting
from app.api.common import record
from app.accounts.service import account_record
from app.operations import overview
from app.miniapp_auth import validate_init_data,issue_session

router=APIRouter();DB=Depends(db_session,scope='function');ADMIN=Depends(roles('admin'))
assets=Path(__file__).parent/'web'

@router.get('/miniapp',include_in_schema=False)
@router.get('/miniapp/',include_in_schema=False)
def shell():return FileResponse(assets/'index.html',headers={'Cache-Control':'no-store'})

@router.get('/miniapp/assets/{name}',include_in_schema=False)
def asset(name:str):
    if name not in ('app.js','style.css'):raise HTTPException(404)
    return FileResponse(assets/name,headers={'Cache-Control':'no-cache'})

@router.get('/api/v1/miniapp/content/{clip_id}/preview')
def preview(clip_id:uuid.UUID,s=DB,actor=ADMIN):
    from app.errors import require
    from app.integrations import drive
    clip=require(s,Clip,clip_id)
    info,chunks=drive.download(clip.drive_file_id,max_bytes=128*1024**2)
    # The page fetches with its Authorization header and creates a local video URL.
    # No admin session, Google credential or public Drive sharing is put in a URL.
    return StreamingResponse(chunks,media_type=info['mimeType'],headers={
        'Content-Length':str(info['size']),'Cache-Control':'no-store',
        'X-Content-Type-Options':'nosniff','Content-Disposition':'inline'})

class InitData(BaseModel):
    init_data:str=Field(min_length=1,max_length=12000)

@router.post('/api/v1/miniapp/session')
def login(data:InitData,request:Request):
    # Keep session creation under the same per-IP limiter as API requests.
    from sqlalchemy.dialects.postgresql import insert
    from app.models import RateBucket
    from app.db import transaction
    from app.security import digest
    minute=int(time.time()//60);key='miniapp-login:'+digest(request.client.host if request.client else 'unknown')
    with transaction() as s:
        count=s.scalar(insert(RateBucket).values(key=key,minute=minute,count=1).on_conflict_do_update(index_elements=['key','minute'],set_={'count':RateBucket.count+1}).returning(RateBucket.count))
    if count>30:raise HTTPException(429,'Слишком много попыток. Подожди минуту.')
    return issue_session(validate_init_data(data.init_data))

@router.get('/api/v1/miniapp/state')
def state(s=DB,actor=ADMIN):
    data=overview(s)
    accounts=list(s.scalars(select(Account).where(Account.status!='deleted').order_by(Account.created_at)))
    clips=list(s.scalars(select(Clip).order_by(Clip.created_at.desc()).limit(200)))
    jobs=list(s.scalars(select(Job).order_by(Job.created_at.desc()).limit(200)))
    pubs=list(s.scalars(select(Publication).order_by(Publication.published_at.desc()).limit(100)))
    users={a.id:a for a in accounts};titles={c.id:c.title for c in clips}
    uses={(u.clip_id,u.platform) for u in s.scalars(select(ClipPlatformUse))}
    reserved={(j.clip_id,j.platform) for j in s.scalars(select(Job))}
    data['account_details']=[account_record(s,a) for a in accounts]
    from app.content.moderation import rejected
    rejected_ids=set(s.scalars(select(Clip.id).where(rejected())))
    policy=s.get(Setting,'review_policy');review_required=bool(policy and policy.value.get('enabled'))
    approvals={v.key.split(':',1)[1] for v in s.scalars(select(Setting).where(Setting.key.like('review_approved:%')))}
    data['content']=[{'id':str(c.id),'title':c.title,'description':c.description,'drive_url':'https://drive.google.com/file/d/'+quote(c.drive_file_id,safe='')+'/view','duration_seconds':c.duration_seconds,'status':c.status,'rejected':c.id in rejected_ids,
        'platforms':{p:('used' if (c.id,p) in uses else 'rejected' if c.id in rejected_ids else 'review_pending' if review_required and str(c.id) not in approvals else 'reserved' if (c.id,p) in reserved else 'available' if c.status=='ready' and p in c.platforms else 'unavailable') for p in ('instagram','tiktok','youtube')}} for c in clips]
    data['queue']=[{'id':str(j.id),'title':titles.get(j.clip_id,'Ролик'),'account':users[j.account_id].username if j.account_id in users else 'Архивный аккаунт',
        'platform':j.platform,'status':j.status,'scheduled_at':j.scheduled_at,'attempts':j.attempts,'clip_id':str(j.clip_id),'account_id':str(j.account_id),'error_code':str((j.result or {}).get('error_code',''))[:100]} for j in jobs]
    data['publications']=[{'url':p.url,'title':titles.get(p.clip_id,'Ролик'),'account':users[p.account_id].username if p.account_id in users else 'Архивный аккаунт','published_at':p.published_at} for p in pubs]
    data['device_options']=[{'id':str(d.id),'name':d.name,'worker_id':str(d.worker_id)} for d in s.scalars(select(Device).where(Device.enabled))]
    data['events']=[{'kind':e.kind,'at':e.created_at,'severity':e.level} for e in s.scalars(select(Event).order_by(Event.created_at.desc()).limit(30))]
    if str(actor.id).startswith('telegram:'):
        uid=int(str(actor.id).split(':')[1])
        requests=[dict(v.value,id=v.key.split(':')[-1]) for v in s.scalars(select(Setting).where(Setting.key.like('miniapp_farm:%'))) if v.value.get('user_id')==uid]
        data['requests']=sorted(requests,key=lambda v:v['created_at'])[-20:]
    else:data['requests']=[]
    heartbeat_jobs=data['farm'].pop('jobs',[])
    user=int(str(actor.id).split(':')[1]) if str(actor.id).startswith('telegram:') else None
    preflight=s.get(Setting,'mass_test_preflight')
    data['preflight']=preflight.value if preflight else None
    data['farm_jobs']=[j for j in heartbeat_jobs if user is None or j.get('user_id')==user]
    progress=s.get(Setting,'farm_progress')
    from app.operations import fresh
    progress=progress.value if progress else {}
    timing_fresh=data['farm']['online'] and fresh(progress.get('received_at'),120) and fresh(progress.get('at'),180)
    timing={item['job_id']:item for item in progress.get('items',[])} if timing_fresh else {}
    data['farm_jobs']=[dict(j,eta=timing.get(j['id'])) for j in data['farm_jobs']]
    # Aggregate only the signed-in user's jobs; their waits already include work ahead.
    active_etas=[j['eta'] for j in data['farm_jobs'] if j.get('eta')]
    data['farm_timing']={'fresh':timing_fresh,'at':progress.get('at'),
        'remaining_min_seconds':max((e['remaining_min_seconds'] for e in active_etas),default=0),
        'remaining_max_seconds':max((e['remaining_max_seconds'] for e in active_etas),default=0),
        'job_count':len(active_etas)}
    data['feedback']=[v.value for v in s.scalars(select(Setting).where(Setting.key.like('editorial_feedback:%'))) if v.value.get('user_id')==user]
    from app.workspace import snapshot
    data.update(snapshot(s,actor,data))
    from app.discovery import snapshot as discovery_snapshot
    data['discovery']=discovery_snapshot(s,user)
    return data

class SourceInput(BaseModel):
    url:str=Field(min_length=10,max_length=2048)
    clip_count:int|None=Field(default=None,ge=1,le=20)

@router.post('/api/v1/miniapp/farm',status_code=201)
@idempotent
def farm_request(data:SourceInput,request:Request,s=DB,actor=ADMIN):
    from app.editorial import queue_requests,uid
    return queue_requests(s,uid(actor),[data.url],data.clip_count)[0]

@router.post('/api/v1/telegram/farm-requests/claim')
@idempotent
def claim_request(request:Request,s=DB,actor=ADMIN):
    rows=s.scalars(select(Setting).where(Setting.key.like('miniapp_farm:%')).order_by(Setting.key).with_for_update(skip_locked=True))
    for row in rows:
        v=dict(row.value)
        if v.get('status') not in ('pending','leased') or v.get('lease_until',0)>time.time():continue
        if v.get('attempts',0)>=5:v['status']='failed';row.value=v;continue
        v.update(status='leased',lease=str(uuid.uuid4()),lease_until=time.time()+90,attempts=v.get('attempts',0)+1);row.value=v
        return {'request':dict(v,id=row.key.split(':')[-1])}
    return {'request':None}

class Receipt(BaseModel):
    lease:str
    job_id:str|None=None
    error:str|None=Field(default=None,max_length=200)

@router.post('/api/v1/telegram/farm-requests/{identifier}/result')
@idempotent
def request_result(identifier:uuid.UUID,data:Receipt,request:Request,s=DB,actor=ADMIN):
    row=s.get(Setting,'miniapp_farm:'+str(identifier),with_for_update=True)
    if not row or row.value.get('lease')!=data.lease:raise HTTPException(409,'Запрос уже передан повторно')
    if not data.job_id and not data.error:raise HTTPException(422,'Missing result')
    value=dict(row.value);value.update(status='failed' if data.error else 'submitted',job_id=data.job_id,error=data.error);row.value=value
    return {'status':value['status']}
