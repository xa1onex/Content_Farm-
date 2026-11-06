import secrets
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import select, func, exists, and_
from app.config import settings
from app.db import now
from app.models import Job, Worker, Device, Account, Clip, Attempt, JobEvent, Publication, Setting
from app.errors import require, event
from app.security import digest
from app.api.common import record

ACTIVE=('assigned','processing','unknown')

def worker_ready(worker):
    return worker.enabled and worker.heartbeat_at and worker.heartbeat_at>now()-timedelta(seconds=settings().heartbeat_timeout)

def claim(s,worker_id,job_id=None):
    from app.content.usage import used_on_platform
    from app.content.moderation import rejected
    w=require(s,Worker,worker_id,locked=True)
    if not worker_ready(w):raise HTTPException(409,'Worker must be enabled and send a heartbeat')
    control=s.get(Setting,'scheduler')
    if control and not control.value.get('enabled',True):return {'job':None}
    busy_count=s.scalar(select(func.count()).select_from(Job).where(Job.worker_id==w.id,Job.status.in_(ACTIVE)))
    if busy_count>=w.max_parallel:return {'job':None}
    busy_devices=select(Job.device_id).where(Job.status.in_(ACTIVE),Job.device_id.is_not(None))
    q=(select(Job).join(Account,Account.id==Job.account_id).join(Clip,Clip.id==Job.clip_id)
        .join(Device,Device.id==Account.device_id)
        .where(Job.status.in_(['scheduled','retry_wait']),Job.scheduled_at<=now(),
            Job.attempts<Job.max_attempts,Account.status=='active',Account.worker_id==w.id,
            Device.worker_id==w.id,Device.enabled.is_(True),
            Device.id.not_in(busy_devices),~rejected(),Clip.status=='ready',Clip.verified_at.is_not(None),~used_on_platform(Job.platform))
        .order_by(Job.scheduled_at,Job.id).with_for_update(of=Job,skip_locked=True))
    if job_id is not None:q=q.where(Job.id==job_id)
    candidates=list(s.scalars(q.limit(100)))
    job=None
    for candidate in candidates:
        from app.campaigns import release_ready
        if not release_ready(s,candidate):continue
        account=require(s,Account,candidate.account_id,locked=True)
        device=s.scalar(select(Device).where(Device.id==account.device_id).with_for_update(skip_locked=True))
        if not device:continue
        if s.scalar(select(Job.id).where(Job.device_id==device.id,Job.status.in_(ACTIVE)).limit(1)):continue
        if 'runtime' in w.capabilities:
            from app.operations import account_blockers
            if account_blockers(account,w,device):continue
        allowed=device.capabilities.get('platforms') or w.capabilities.get('platforms')
        if allowed and account.platform not in allowed:continue
        from app.scheduler.service import enforce_execution_limits,compatible
        if not compatible(s,account,s.get(Clip,candidate.clip_id)):continue
        if not enforce_execution_limits(s,account,candidate):continue
        job=candidate
        break
    if not job:return {'job':None}
    raw=secrets.token_urlsafe(40)
    job.status='assigned';job.worker_id=w.id;job.device_id=device.id
    job.lease_hash=digest(raw);job.lease_until=now()+timedelta(seconds=settings().lease_seconds)
    job.attempts+=1;job.processing_started_at=None
    s.add(Attempt(job_id=job.id,number=job.attempts,worker_id=w.id))
    s.flush()
    clip=require(s,Clip,job.clip_id)
    return {'job':record(job),'lease_token':raw,
        'lease_expires_at':job.lease_until,'account':{'id':account.id,'platform':account.platform,
        'username':account.username,'timezone':account.timezone},
        'device':{'id':device.id,'external_id':device.external_id},
        'content':{'id':clip.id,'passport':clip.passport,'title':clip.title,
        'description':clip.description,'sha256':clip.sha256,'duration_seconds':clip.duration_seconds}}

def fenced(s,job_id,worker_id,lease_token,allow_terminal=False):
    job=require(s,Job,job_id,locked=True)
    if job.worker_id!=worker_id or not job.lease_hash or not secrets.compare_digest(job.lease_hash,digest(lease_token)):
        raise HTTPException(409,'Lease is stale or belongs to another worker')
    if not allow_terminal:
        if job.status not in ('assigned','processing') or not job.lease_until or job.lease_until<=now():
            raise HTTPException(409,'Lease expired or job is not executable; never publish')
    return job

def worker_event(s,job,worker_id,data,kind):
    prior=s.scalar(select(JobEvent).where(JobEvent.worker_id==worker_id,JobEvent.event_id==data.event_id))
    payload=data.model_dump(mode='json',exclude={'lease_token'})
    if prior:
        if prior.job_id!=job.id or prior.kind!=kind or prior.payload!=payload:
            raise HTTPException(409,'Event ID reused for a different event')
        return False
    s.add(JobEvent(job_id=job.id,worker_id=worker_id,event_id=data.event_id,kind=kind,payload=payload))
    return True

def attempt(s,job):
    return s.scalar(select(Attempt).where(Attempt.job_id==job.id,Attempt.number==job.attempts))

def acknowledge(s,job_id,worker_id,data):
    job=fenced(s,job_id,worker_id,data.lease_token,allow_terminal=True)
    fresh=worker_event(s,job,worker_id,data,'ack')
    if not fresh:
        if job.status=='processing' and (not job.lease_until or job.lease_until<=now()):
            raise HTTPException(409,'Lease expired; do not publish')
        return {'job_id':job.id,'status':job.status,'lease_expires_at':job.lease_until}
    fenced(s,job_id,worker_id,data.lease_token)
    if job.status!='assigned':raise HTTPException(409,'Only an assigned job can be acknowledged')
    from app.content.moderation import require_allowed
    require_allowed(s,job.clip_id)
    job.status='processing';job.processing_started_at=now()
    job.lease_until=now()+timedelta(seconds=settings().lease_seconds)
    attempt(s,job).status='processing'
    return {'job_id':job.id,'status':job.status,'lease_expires_at':job.lease_until}

def progress(s,job_id,worker_id,data):
    job=fenced(s,job_id,worker_id,data.lease_token,allow_terminal=True)
    if not worker_event(s,job,worker_id,data,'progress'):
        return {'job_id':job.id,'status':job.status,'lease_expires_at':job.lease_until}
    fenced(s,job_id,worker_id,data.lease_token)
    if job.status!='processing':raise HTTPException(409,'Acknowledge before processing')
    from app.content.moderation import require_allowed
    require_allowed(s,job.clip_id)
    deadline=job.processing_started_at+timedelta(seconds=settings().max_processing_seconds)
    if deadline<=now():raise HTTPException(409,'Maximum processing time reached; stop and reconcile')
    job.lease_until=min(now()+timedelta(seconds=settings().lease_seconds),deadline)
    return {'job_id':job.id,'status':job.status,'lease_expires_at':job.lease_until}

def publish(s,job,external_id,url,published_at):
    if s.scalar(select(Publication.id).where(Publication.job_id==job.id)):
        raise HTTPException(409,'Publication already exists')
    publication=Publication(job_id=job.id,clip_id=job.clip_id,account_id=job.account_id,
        external_id=external_id,url=url,published_at=published_at)
    s.add(publication);job.status='published';job.lease_until=None
    account=require(s,Account,job.account_id,locked=True)
    if not account.last_published_at or published_at>account.last_published_at:account.last_published_at=published_at
    s.flush()
    event(s,'publication_published','published:'+str(job.id),
      {'platform':account.platform,'username':account.username,'url':url},'info')
    return publication

def result(s,job_id,worker_id,data):
    job=fenced(s,job_id,worker_id,data.lease_token,allow_terminal=True)
    if not worker_event(s,job,worker_id,data,'result'):
        pub=s.scalar(select(Publication).where(Publication.job_id==job.id))
        return {'job_id':job.id,'status':job.status,'publication_id':pub.id if pub else None}
    # A late result from the same fenced attempt may reconcile unknown, never a new attempt.
    if job.status not in ('processing','unknown'):
        raise HTTPException(409,'Result requires processing or unknown state')
    if data.outcome=='published':
        pub=publish(s,job,data.external_id,data.url,data.published_at)
        pub_id=pub.id
    elif data.outcome=='safe_failure':
        if job.status=='unknown':raise HTTPException(409,'Unknown outcome requires operator reconciliation')
        transient={'network_error','drive_unavailable','media_download_failed','rate_limited',
          'device_command_failed','adb_connect_timeout','mumu_boot_timeout','appium_unavailable','worker_restarted'}
        retry=data.error_code in transient and job.attempts<job.max_attempts
        editorial=data.error_code=='editorial_rejected'
        job.status='paused' if editorial else 'retry_wait' if retry else 'failed'
        job.lease_until=None;pub_id=None
        if retry:job.scheduled_at=now()+timedelta(seconds=min(600,60*2**job.attempts))
        if data.error_code in ('captcha','platform_block','login_required'):
            require(s,Account,job.account_id,locked=True).status='attention'
        if not retry and not editorial:event(s,'publication_failed',f'failed:{job.id}:{job.attempts}',
            {'job_id':str(job.id),'code':data.error_code})
    else:
        job.status='unknown';job.lease_until=None;pub_id=None
        event(s,'unknown_publication',f'unknown:{job.id}:{job.attempts}',{'job_id':str(job.id)})
    job.result=data.model_dump(mode='json',exclude={'lease_token'})
    att=attempt(s,job)
    if att:
        att.status=job.status;att.finished_at=now();att.error_code=data.error_code;att.error_detail=data.error_detail
    return {'job_id':job.id,'status':job.status,'publication_id':pub_id}

def reap(s):
    jobs=list(s.scalars(select(Job).where(Job.status.in_(['assigned','processing']),Job.lease_until<=now())
        .with_for_update(skip_locked=True)))
    for job in jobs:
        att=attempt(s,job)
        old=job.status
        if old=='assigned':
            job.status='retry_wait' if job.attempts<job.max_attempts else 'failed'
            job.scheduled_at=now()+timedelta(seconds=min(300,30*2**job.attempts))
            job.lease_hash=None;job.worker_id=None;job.device_id=None
        else:
            job.status='unknown'
            event(s,'unknown_publication',f'unknown:{job.id}:{job.attempts}',{'job_id':str(job.id)})
        job.lease_until=None
        if att:
            att.status=job.status;att.finished_at=now();att.error_code='lease_expired'
    return len(jobs)
