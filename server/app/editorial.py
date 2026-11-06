"""Durable farm requests and editorial preference examples; no model weight training."""
import uuid
from urllib.parse import urlsplit
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel,Field
from sqlalchemy import select
from app.db import db_session,now
from app.models import Setting,Clip
from app.security import roles,idempotent,lock
from app.errors import require
router=APIRouter();DB=Depends(db_session,scope='function');ADMIN=Depends(roles('admin'));FARM=Depends(roles('farm','admin'))
def uid(actor):
    if not str(actor.id).startswith('telegram:'):raise HTTPException(403,'Открой форму через Telegram')
    return int(str(actor.id).split(':')[1])
def queue_requests(s,user,urls,count=None,parent=None,retry=None):
    values=list(dict.fromkeys(v.strip() for v in urls))
    if not 1<=len(values)<=10:raise HTTPException(422,'За один раз можно добавить от 1 до 10 ссылок')
    for value in values:
        if len(value)>2048:raise HTTPException(422,'Ссылка слишком длинная')
        u=urlsplit(value)
        try:port=u.port
        except ValueError:raise HTTPException(422,'Некорректный адрес')
        if u.scheme!='https' or u.username or u.password or port not in (None,443) or u.hostname not in ('youtube.com','www.youtube.com','m.youtube.com','youtu.be','drive.google.com'):raise HTTPException(422,'Нужны ссылки YouTube или Google Диска')
    lock(s,'miniapp-farm:'+str(user))
    pending=[v.value for v in s.scalars(select(Setting).where(Setting.key.like('miniapp_farm:%'))) if v.value.get('user_id')==user and v.value.get('status') in ('pending','leased')]
    heartbeat=s.get(Setting,'farm_heartbeat')
    active={j['id'] for j in (heartbeat.value.get('jobs',[]) if heartbeat else []) if j.get('user_id')==user and j.get('status') in ('receiving','queued','processing','retry_wait','interrupted')}
    if len(pending)+len(active)+len(values)>10:raise HTTPException(409,'Можно держать до 10 заданий одновременно. Сейчас свободно мест: '+str(max(0,10-len(pending)-len(active))))
    if not parent and not retry and any(v.get('url') in values and not v.get('parent_job_id') for v in pending):raise HTTPException(409,'Эта ссылка уже передаётся в обработку')
    result=[]
    for value in values:
        identifier=str(uuid.uuid4());s.add(Setting(key='miniapp_farm:'+identifier,value={'user_id':user,'url':value,'clip_count':count,'parent_job_id':parent,'retry_job_id':retry,'status':'pending','created_at':now().isoformat(),'attempts':0}))
        result.append({'id':identifier,'status':'pending'})
    return result
class BatchInput(BaseModel):
    urls:list[str]=Field(min_length=1,max_length=10)
    clip_count:int|None=Field(default=None,ge=1,le=20)
@router.post('/api/v1/miniapp/farm/batch',status_code=201)
@idempotent
def batch(data:BatchInput,request:Request,s=DB,actor=ADMIN):return {'items':queue_requests(s,uid(actor),data.urls,data.clip_count)}
class MoreInput(BaseModel):
    clip_count:int=Field(default=3,ge=1,le=20)
def own_job(s,user,job_id):
    heartbeat=s.get(Setting,'farm_heartbeat')
    job=next((j for j in (heartbeat.value.get('jobs',[]) if heartbeat else []) if j.get('id')==job_id and j.get('user_id')==user),None)
    if not job:raise HTTPException(404,'Задание не найдено или отчёт фермы ещё не обновился')
    return job
@router.post('/api/v1/miniapp/farm/jobs/{job_id}/more',status_code=201)
@idempotent
def more(job_id:str,data:MoreInput,request:Request,s=DB,actor=ADMIN):
    user=uid(actor);job=own_job(s,user,job_id)
    if job['status'] not in ('completed',):raise HTTPException(409,'Сначала дождись завершения исходного задания')
    return queue_requests(s,user,[job['url']],data.clip_count,parent=job_id)[0]
@router.post('/api/v1/miniapp/farm/jobs/{job_id}/retry',status_code=201)
@idempotent
def retry(job_id:str,request:Request,s=DB,actor=ADMIN):
    user=uid(actor);job=own_job(s,user,job_id)
    if job['status'] not in ('failed','needs_retry'):raise HTTPException(409,'Задание не требует ручного повтора')
    return queue_requests(s,user,[job['url']],job.get('clip_count'),retry=job_id)[0]
class FeedbackInput(BaseModel):
    rating:int|None=Field(default=None,ge=-1,le=1)
    comment:str|None=Field(default=None,max_length=1000)
def feedback_record(s,clip,user,data):
    if data.rating is None and data.comment is None:raise HTTPException(422,'Добавь оценку или комментарий')
    key='editorial_feedback:'+str(clip.id)+':'+str(user);lock(s,key);row=s.get(Setting,key)
    value=dict(row.value) if row else {'clip_id':str(clip.id),'user_id':user,'rating':0,'comment':''}
    value.update(data.model_dump(exclude_none=True));value['at']=now().isoformat()
    if row:row.value=value
    else:s.add(Setting(key=key,value=value))
    s.flush()
    from app.content.moderation import blocked,pause_waiting
    if blocked(s,clip.id):pause_waiting(s,clip.id)
    return value
@router.post('/api/v1/miniapp/content/{clip_id}/feedback')
@idempotent
def feedback(clip_id:uuid.UUID,data:FeedbackInput,request:Request,s=DB,actor=ADMIN):return feedback_record(s,require(s,Clip,clip_id),uid(actor),data)
class BotFeedback(FeedbackInput):
    passport:str=Field(pattern=r'^CF-[A-F0-9]{32}$')
    user_id:int=Field(gt=0)
@router.post('/api/v1/telegram/editorial-feedback')
@idempotent
def bot_feedback(data:BotFeedback,request:Request,s=DB,actor=ADMIN):
    clip=s.scalar(select(Clip).where(Clip.passport==data.passport))
    if not clip:raise HTTPException(404,'Ролик ещё не зарегистрирован для автопостинга. Попробуй позже.')
    return feedback_record(s,clip,data.user_id,FeedbackInput(rating=data.rating,comment=data.comment))
@router.get('/api/v1/farm/editorial-feedback')
def examples(s=DB,actor=FARM):
    rows=sorted([x.value for x in s.scalars(select(Setting).where(Setting.key.like('editorial_feedback:%')))],key=lambda x:x.get('at',''),reverse=True)[:40];items=[]
    for v in rows:
        clip=s.get(Clip,uuid.UUID(v['clip_id']))
        if clip:items.append({'title':clip.title,'description':clip.description,'rating':v['rating'],'comment':v.get('comment','')[:700],'duration':clip.duration_seconds})
    return {'items':items,'method':'editorial_memory'}
