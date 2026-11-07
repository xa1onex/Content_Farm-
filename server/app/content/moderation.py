"""Publication decisions are independent of audience ratings and AI feedback."""
from sqlalchemy import select,exists,cast,String,literal
from fastapi import HTTPException
from app.models import Setting,Clip,Job


def rejected():
    return exists(select(1).where(Setting.key.in_([
        literal('review_deleted:')+cast(Clip.id,String),
        literal('review_rejected:')+cast(Clip.id,String)]) )).correlate(Clip,Job)


def awaiting(s,clip_id):
    policy=s.get(Setting,'review_policy')
    return bool(policy and policy.value.get('enabled') and not s.get(Setting,'review_approved:'+str(clip_id)))


def blocked(s,clip_id):
    return bool(s.get(Setting,'review_deleted:'+str(clip_id)) or
                s.get(Setting,'review_rejected:'+str(clip_id)) or awaiting(s,clip_id))


def require_allowed(s,clip_id):
    if blocked(s,clip_id):
        raise HTTPException(409,'Ролик ожидает одобрения или исключён из публикации решением модератора')


def pause_waiting(s,clip_id,reason='moderation_required'):
    for job in s.scalars(select(Job).where(Job.clip_id==clip_id,Job.status.in_(['pending','scheduled','retry_wait'])).with_for_update()):
        job.status='paused';job.result={**(job.result or {}),'error_code':reason}
