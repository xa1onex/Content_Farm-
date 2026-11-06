import json,time,uuid
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import FileResponse
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
    data['content']=[{'id':str(c.id),'title':c.title,'duration_seconds':c.duration_seconds,'status':c.status,
        'platforms':{p:('used' if (c.id,p) in uses else 'reserved' if (c.id,p) in reserved else 'available' if c.status=='ready' and p in c.platforms else 'unavailable') for p in ('instagram','tiktok','youtube')}} for c in clips]
    data['queue']=[{'id':str(j.id),'title':titles.get(j.clip_id,'Ролик'),'account':users[j.account_id].username if j.account_id in users else 'Архивный аккаунт',
        'platform':j.platform,'status':j.status,'scheduled_at':j.scheduled_at,'attempts':j.attempts} for j in jobs]
    data['publications']=[{'url':p.url,'title':titles.get(p.clip_id,'Ролик'),'account':users[p.account_id].username if p.account_id in users else 'Архивный аккаунт','published_at':p.published_at} for p in pubs]
    data['device_options']=[{'id':str(d.id),'name':d.name,'worker_id':str(d.worker_id)} for d in s.scalars(select(Device).where(Device.enabled))]
    data['events']=[{'kind':e.kind,'at':e.created_at,'severity':e.level} for e in s.scalars(select(Event).order_by(Event.created_at.desc()).limit(30))]
    if str(actor.id).startswith('telegram:'):
        uid=int(str(actor.id).split(':')[1])
        requests=[dict(v.value,id=v.key.split(':')[-1]) for v in s.scalars(select(Setting).where(Setting.key.like('miniapp_farm:%'))) if v.value.get('user_id')==uid]
        data['requests']=sorted(requests,key=lambda v:v['created_at'])[-20:]
    else:data['requests']=[]
    return data

class SourceInput(BaseModel):
    url:str=Field(min_length=10,max_length=2048)

@router.post('/api/v1/miniapp/farm',status_code=201)
@idempotent
def farm_request(data:SourceInput,request:Request,s=DB,actor=ADMIN):
    if not str(actor.id).startswith('telegram:'):raise HTTPException(403,'Открой форму через Telegram')
    uid=int(str(actor.id).split(':')[1]);url=urlsplit(data.url.strip())
    if url.scheme!='https' or url.username or url.password or url.hostname not in ('youtube.com','www.youtube.com','m.youtube.com','youtu.be','drive.google.com'):
        raise HTTPException(422,'Нужна ссылка YouTube или Google Диска')
    lock(s,'miniapp-farm:'+str(uid))
    rows=list(s.scalars(select(Setting).where(Setting.key.like('miniapp_farm:%'))))
    if any(v.value.get('user_id')==uid and v.value.get('status') in ('pending','leased') for v in rows):raise HTTPException(409,'Предыдущая ссылка уже передаётся в обработку')
    identifier=str(uuid.uuid4());value={'user_id':uid,'url':data.url.strip(),'status':'pending','created_at':now().isoformat(),'attempts':0}
    s.add(Setting(key='miniapp_farm:'+identifier,value=value))
    return {'id':identifier,'status':'pending'}

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
