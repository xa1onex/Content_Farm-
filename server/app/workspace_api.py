"""Authenticated library moderation and the two-step source workflow."""
import uuid
from typing import Literal
from datetime import timedelta
from fastapi import APIRouter,Depends,HTTPException,Request,Query
from pydantic import BaseModel,Field
from sqlalchemy import select,delete,func
from app.db import db_session,now
from app.models import Clip,Setting,Job,Account
from app.security import roles,idempotent,lock
from app import workspace
from app.editorial import queue_requests,uid,own_job

router=APIRouter();DB=Depends(db_session,scope='function');ADMIN=Depends(roles('admin'))

@router.get('/api/v1/miniapp/library')
def content(q:str=Query('',max_length=200),filter:Literal['all','pending','approved','rejected','deleted','queued','published']='all',
            platform:Literal['instagram','tiktok','youtube']|None=None,offset:int=Query(0,ge=0,le=100000),
            limit:int=Query(60,ge=1,le=100),s=DB,actor=ADMIN):
    return workspace.library(s,workspace.user_id(actor),offset,limit,q,filter,platform)

class Moderation(BaseModel):
    clip_ids:list[uuid.UUID]=Field(min_length=1,max_length=100)
    decision:Literal['approved','rejected','deleted','pending','restore']

@router.post('/api/v1/miniapp/moderation/batch')
@idempotent
def moderate(data:Moderation,request:Request,s=DB,actor=ADMIN):
    ids=sorted(set(data.clip_ids));clips=[]
    for identifier in ids:
        lock(s,'review:'+str(identifier));c=s.get(Clip,identifier,with_for_update=True)
        if not c:raise HTTPException(404,'Один из выбранных роликов не найден')
        if data.decision=='approved' and (c.status!='ready' or c.verified_at is None or s.get(Setting,'review_deleted:'+str(c.id))):
            raise HTTPException(409,'Одобрить можно только готовый ролик. Из корзины сначала восстанови его на проверку.')
        clips.append(c)
    from app.content.moderation import pause_waiting
    updated=[];attention=[]
    for c in clips:
        key=str(c.id);value={'user_id':workspace.user_id(actor),'actor':str(actor.id),'at':now().isoformat()}
        if data.decision=='approved':
            old=s.get(Setting,'review_approved:'+key)
            if old:old.value=value
            else:s.add(Setting(key='review_approved:'+key,value=value))
            s.execute(delete(Setting).where(Setting.key=='review_rejected:'+key));s.flush()
            # Only restore never-attempted editorial pauses, never uncertain sends.
            from app.scheduler.service import validate_slot,local_instants
            from zoneinfo import ZoneInfo
            for job in s.scalars(select(Job).where(Job.clip_id==c.id,Job.status=='paused',Job.attempts==0).with_for_update()):
                if (job.result or {}).get('error_code') not in ('editorial_rejected','moderation_required','moderation_rejected','moderation_deleted'):continue
                account=s.get(Account,job.account_id,with_for_update=True)
                if account.status!='active':continue
                candidates=[job.scheduled_at] if job.scheduled_at>now() else []
                if not candidates:
                    today=now().astimezone(ZoneInfo(account.timezone)).date()
                    for offset in range(8):
                        candidates.extend(t for t in local_instants(today+timedelta(days=offset),account) if t>=now()+timedelta(minutes=2))
                for stamp in candidates:
                    try:day=validate_slot(s,account,stamp,exclude_id=job.id)
                    except HTTPException:continue
                    if day!=job.local_date:
                        job.slot=(s.scalar(select(func.max(Job.slot)).where(Job.account_id==account.id,Job.local_date==day)) or 0)+1
                    job.scheduled_at=stamp;job.local_date=day;job.status='scheduled';job.result={};break
                else:attention.append(str(job.id))
        else:
            s.execute(delete(Setting).where(Setting.key=='review_approved:'+key))
            if data.decision=='restore':
                s.execute(delete(Setting).where(Setting.key.in_(['review_deleted:'+key,'review_rejected:'+key])))
                c.status='ready' if c.verified_at and c.sha256 else 'draft'
            elif data.decision in ('rejected','deleted'):
                marker='review_'+data.decision+':'+key;old=s.get(Setting,marker)
                if old:old.value=value
                else:s.add(Setting(key=marker,value=value))
                if data.decision=='deleted':c.status='archived'
            else:s.execute(delete(Setting).where(Setting.key=='review_rejected:'+key))
            pause_waiting(s,c.id,'moderation_'+data.decision)
        updated.append(str(c.id))
    s.flush()
    return {'updated':updated,'decision':data.decision,'attention_jobs':attention,'automatic_planning':True}

class Analyze(BaseModel):
    urls:list[str]=Field(min_length=1,max_length=10)

@router.post('/api/v1/miniapp/farm/analyze',status_code=201)
@idempotent
def analyze(data:Analyze,request:Request,s=DB,actor=ADMIN):
    return {'items':queue_requests(s,uid(actor),data.urls,mode='analyze')}

class Render(BaseModel):
    clip_count:int=Field(ge=1,le=20)

@router.post('/api/v1/miniapp/farm/jobs/{job_id}/render',status_code=201)
@idempotent
def render(job_id:str,data:Render,request:Request,s=DB,actor=ADMIN):
    user=uid(actor);lock(s,'miniapp-farm:'+str(user));job=own_job(s,user,job_id)
    if any(r.value.get('render_job_id')==job_id and r.value.get('status') in ('pending','leased') for r in s.scalars(select(Setting).where(Setting.key.like('miniapp_farm:%')))):
        raise HTTPException(409,'Нарезка этого исходника уже передаётся на ферму')
    if job['status']!='analysis_ready':raise HTTPException(409,'Сначала дождись завершения анализа исходника')
    return queue_requests(s,user,[job['url']],data.clip_count,mode='render',render=job_id)[0]
