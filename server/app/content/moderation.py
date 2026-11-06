"""A negative editorial rating vetoes every new platform publication."""
from sqlalchemy import select,exists,cast,String
from fastapi import HTTPException
from app.models import Setting,Clip,Job

def rejected():
    return exists(select(1).where(Setting.key.like('editorial_feedback:%'),
        Setting.value['clip_id'].astext==cast(Clip.id,String),Setting.value['rating'].as_integer()==-1)).correlate(Clip,Job)

def blocked(s,clip_id):
    return bool(s.scalar(select(Clip.id).where(Clip.id==clip_id,rejected())))

def require_allowed(s,clip_id):
    if blocked(s,clip_id):raise HTTPException(409,'Ролик отклонён дизлайком: публикация запрещена')

def pause_waiting(s,clip_id):
    for job in s.scalars(select(Job).where(Job.clip_id==clip_id,Job.status.in_(['scheduled','retry_wait'])).with_for_update()):
        job.status='paused';job.result={**(job.result or {}),'error_code':'editorial_rejected'}
