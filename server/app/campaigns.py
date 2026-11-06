"""Ordered release waves wait for actual confirmed publication, not planned time."""
import uuid
from datetime import timedelta
from app.db import now
from app.models import Setting,Job,Publication
from sqlalchemy import select

def release_ready(s,job):
    row=s.get(Setting,'campaign_job:'+str(job.id))
    if not row:return True
    v=row.value
    campaign=s.get(Setting,'release_campaign:'+v['campaign_id'])
    if not campaign or not campaign.value.get('enabled'):return False
    previous=v.get('previous_job_id')
    if not previous:return True
    predecessor=s.get(Job,uuid.UUID(previous))
    if not predecessor or predecessor.status!='published':return False
    stamp=s.scalar(select(Publication.published_at).where(Publication.job_id==predecessor.id))
    return bool(stamp and now()>=stamp+timedelta(seconds=v['delay_seconds']))
