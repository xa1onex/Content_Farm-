"""Scoped editorial review links: never grant admin or publication authority."""
import hashlib,time,uuid
from pathlib import Path
from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import FileResponse,StreamingResponse
from pydantic import BaseModel,Field
from sqlalchemy import select
from app.db import db_session,now
from app.models import Clip,Setting,ClipPlatformUse,Job,Account
from app.editorial import FeedbackInput,feedback_record
from app.security import lock
router=APIRouter();DB=Depends(db_session,scope='function')

@router.get('/review',include_in_schema=False)
def page():
    return FileResponse(Path(__file__).parent/'web/review.html',headers={'Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'})

def grant(request:Request,s=DB):
    token=request.headers.get('X-Review-Key','')
    if not 30<=len(token)<=150:raise HTTPException(401,'Открой личную ссылку на проверку роликов')
    row=s.get(Setting,'review_link:'+hashlib.sha256(token.encode()).hexdigest())
    if not row or row.value.get('expires',0)<time.time():raise HTTPException(401,'Ссылка истекла. Нужна новая ссылка на проверку.')
    return row.value

def eligible(s,clip):
    deleted=s.get(Setting,'review_deleted:'+str(clip.id))
    if clip.status!='ready' and not deleted:raise HTTPException(409,'Ролик ещё не готов к проверке')

def items(s,user):
    deleted={x.key.split(':',1)[1] for x in s.scalars(select(Setting).where(Setting.key.like('review_deleted:%')))}
    feedback={v.value['clip_id']:v.value for v in s.scalars(select(Setting).where(Setting.key.like('editorial_feedback:%'))) if v.value.get('user_id')==user}
    approvals={x.key.split(':',1)[1] for x in s.scalars(select(Setting).where(Setting.key.like('review_approved:%')))}
    uses={}
    for v in s.scalars(select(ClipPlatformUse)):uses.setdefault(str(v.clip_id),[]).append(v.platform)
    from app.content.moderation import rejected
    rejected_ids=set(s.scalars(select(Clip.id).where(rejected())))
    releases={}
    for job,account in s.execute(select(Job,Account).join(Account,Account.id==Job.account_id).order_by(Job.created_at)):
        if s.get(Setting,'campaign_job:'+str(job.id)):releases[str(job.clip_id)]={'at':job.scheduled_at.isoformat(),'account':account.username,'status':job.status}
    out=[]
    for c in s.scalars(select(Clip).order_by(Clip.created_at.desc())):
        if c.status!='ready' and str(c.id) not in deleted:continue
        used=uses.get(str(c.id),[])
        if all(p in used for p in c.platforms):continue
        f=feedback.get(str(c.id),{});decision='deleted' if str(c.id) in deleted else 'approved' if str(c.id) in approvals and c.id not in rejected_ids else 'rejected' if c.id in rejected_ids else 'pending'
        out.append({'id':str(c.id),'title':c.title,'description':c.description,'seconds':c.duration_seconds,'decision':decision,'comment':f.get('comment',''),'used':used,'created_at':c.created_at.isoformat(),'release':releases.get(str(c.id))})
    return out

@router.get('/api/v1/review')
def state(request:Request,s=DB):
    g=grant(request,s);return {'items':items(s,g['user_id']),'expires':g['expires']}

class Decision(BaseModel):
    decision:str|None=Field(default=None,pattern='^(approved|deleted)$')
    comment:str|None=Field(default=None,max_length=1000)

@router.post('/api/v1/review/{clip_id}')
def decide(clip_id:uuid.UUID,data:Decision,request:Request,s=DB):
    g=grant(request,s)
    if data.decision is None and data.comment is None:raise HTTPException(422,'Добавь решение или комментарий')
    lock(s,'review:'+str(clip_id));clip=s.get(Clip,clip_id)
    if not clip:raise HTTPException(404,'Ролик не найден')
    eligible(s,clip)
    if data.decision=='approved' and s.get(Setting,'review_deleted:'+str(clip.id)):raise HTTPException(409,'Ролик удалён из выпуска. Восстановление требует отдельного действия.')
    if data.decision=='approved':
        key='review_approved:'+str(clip.id)
        if not s.get(Setting,key):s.add(Setting(key=key,value={'user_id':g['user_id'],'at':now().isoformat()}))
        s.flush()
    if data.comment is not None:feedback_record(s,clip,g['user_id'],FeedbackInput(comment=data.comment))
    if data.decision=='deleted':
        key='review_deleted:'+str(clip.id)
        if not s.get(Setting,key):s.add(Setting(key=key,value={'user_id':g['user_id'],'at':now().isoformat()}))
        clip.status='archived'
        from app.content.moderation import pause_waiting
        pause_waiting(s,clip.id,'moderation_deleted')
    s.flush()
    return {'saved':True,'items':items(s,g['user_id'])}

@router.get('/api/v1/review/{clip_id}/video')
def video(clip_id:uuid.UUID,request:Request,s=DB):
    grant(request,s);clip=s.get(Clip,clip_id)
    if not clip:raise HTTPException(404,'Ролик не найден')
    eligible(s,clip)
    from app.integrations.drive import download
    info,chunks=download(clip.drive_file_id,max_bytes=128*1024**2)
    return StreamingResponse(chunks,media_type=info['mimeType'],headers={'Content-Length':str(info['size']),'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'})
