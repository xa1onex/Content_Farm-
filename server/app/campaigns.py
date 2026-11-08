"""Ordered release waves wait for confirmed publication, skipping proved no-send failures."""
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
    previous=v.get('previous_job_id');seen={str(job.id)}
    while previous:
        if previous in seen:return False
        seen.add(previous)
        predecessor=s.get(Job,uuid.UUID(previous))
        if not predecessor:return False
        if predecessor.status=='published':
            stamp=s.scalar(select(Publication.published_at).where(Publication.job_id==predecessor.id))
            return bool(stamp and now()>=stamp+timedelta(seconds=v['delay_seconds']))
        result=predecessor.result or {}
        # Only explicit proof of no submission permits the next account to run.
        # Unknown/active attempts and historical no-republish quarantines still stop the wave.
        if predecessor.status!='failed' or not result.get('no_publish_attempted') or result.get('no_republish'):
            return False
        link=s.get(Setting,'campaign_job:'+previous)
        if not link or link.value.get('campaign_id')!=v['campaign_id']:return False
        previous=link.value.get('previous_job_id')
    return True
