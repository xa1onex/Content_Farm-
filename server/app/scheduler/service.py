import hashlib, random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select, func, exists
from app.db import now
from app.models import (Account, AccountCategory, AccountFormat, AccountTopic, Clip, Job,
    SchedulePlan, Worker, Device, Setting, Publication, Snapshot)
from app.errors import require,event
from app.content.service import ready

UTC=timezone.utc
RESERVED=('pending','scheduled','assigned','processing','published','retry_wait','unknown','paused','failed')

def warmup_day(account, day):
    started=getattr(account,'operational_started_at',None)
    if not started:return None
    return max(1,(day-started.astimezone(ZoneInfo(account.timezone)).date()).days+1)

def daily_quota(account,day):
    if not getattr(account,'warmup_enabled',False):return account.daily_limit
    age=warmup_day(account,day) or 1
    return min(account.daily_limit,1 if age<=2 else 2 if age==3 else 3)

def start_ready_accounts(s):
    from app.operations import account_blockers
    for account in s.scalars(select(Account).where(Account.status=='active',Account.warmup_enabled.is_(True),Account.operational_started_at.is_(None)).with_for_update(skip_locked=True)):
        worker=s.get(Worker,account.worker_id) if account.worker_id else None
        device=s.get(Device,account.device_id) if account.device_id else None
        if not account_blockers(account,worker,device):account.operational_started_at=now()
    s.flush()

def minute(value):
    h,m=map(int,value.split(':'));return h*60+m

def local_instants(day,account):
    tz=ZoneInfo(account.timezone)
    for window in account.windows:
        for m in range(minute(window['start']),minute(window['end'])):
            naive=datetime.combine(day,datetime.min.time())+timedelta(minutes=m)
            dt=naive.replace(tzinfo=tz,fold=0)
            utc=dt.astimezone(UTC)
            # Skip nonexistent local times at DST transitions; use first occurrence of a fold.
            if utc.astimezone(tz).replace(tzinfo=None)==naive:yield utc

def times(day,account,weights=None,not_before=None):
    candidates=[dt for dt in local_instants(day,account) if not_before is None or dt>=not_before]
    if not candidates or daily_quota(account,day)==0:return []
    rng=random.Random(int.from_bytes(hashlib.sha256(f'{account.id}:{day}'.encode()).digest()[:8],'big'))
    n=daily_quota(account,day);chosen=[]
    # Stratified jitter retains coverage and makes day-to-day schedules variable.
    for slot in range(n):
        center=min(len(candidates)-1,int((slot+0.5)*len(candidates)/n))
        radius=account.jitter_minutes
        options=candidates[max(0,center-radius):min(len(candidates),center+radius+1)]
        rng.shuffle(options)
        if weights:
            options.sort(key=lambda dt:weights.get(dt.astimezone(ZoneInfo(account.timezone)).hour,1)*rng.uniform(.8,1.2),reverse=True)
        candidate=next((dt for dt in options if all(abs((dt-other).total_seconds())>=account.min_interval_minutes*60 for other in chosen)),None)
        if candidate:chosen.append(candidate)
    return sorted(chosen)

def adaptive_weights(s,account):
    # Earliest 24..48h snapshot avoids favouring older publications; >=10 independent posts/hour.
    pubs=list(s.scalars(select(Publication).where(Publication.account_id==account.id,
        Publication.published_at>=now()-timedelta(days=60),Publication.published_at<=now()-timedelta(hours=24))))
    by_hour={}
    for pub in pubs:
        snapshot=s.scalar(select(Snapshot).where(Snapshot.publication_id==pub.id,
            Snapshot.measured_at>=pub.published_at+timedelta(hours=24),
            Snapshot.measured_at<=pub.published_at+timedelta(hours=48),Snapshot.views.is_not(None))
            .order_by(Snapshot.measured_at).limit(1))
        if snapshot:
            h=pub.published_at.astimezone(ZoneInfo(account.timezone)).hour
            by_hour.setdefault(h,[]).append(snapshot.views)
    valid={h:sum(v)/len(v) for h,v in by_hour.items() if len(v)>=10}
    if len(valid)<2:return {}
    baseline=sum(valid.values())/len(valid)
    return {h:min(1.25,max(.75,v/max(1,baseline))) for h,v in valid.items()}

def compatible(s,account,clip):
    if account.platform not in clip.platforms:return False
    for model,field in [(AccountCategory,'category_id'),(AccountTopic,'topic_id'),(AccountFormat,'format_id')]:
        allowed=list(s.scalars(select(getattr(model,field)).where(model.account_id==account.id)))
        if allowed and getattr(clip,field) not in allowed:return False
    return True

def validate_slot(s,account,dt,exclude_id=None):
    if account.status!='active':raise HTTPException(409,'Account is not active')
    tz=ZoneInfo(account.timezone);local=dt.astimezone(tz)
    if not any(minute(w['start'])<=local.hour*60+local.minute<minute(w['end']) for w in account.windows):
        raise HTTPException(409,'Time is outside the account publication windows')
    start=datetime.combine(local.date(),datetime.min.time(),tzinfo=tz).astimezone(UTC)
    end=datetime.combine(local.date()+timedelta(days=1),datetime.min.time(),tzinfo=tz).astimezone(UTC)
    conditions=[Job.account_id==account.id,Job.status.in_(RESERVED)]
    if exclude_id:conditions.append(Job.id!=exclude_id)
    count=s.scalar(select(func.count()).select_from(Job).where(*conditions,Job.scheduled_at>=start,Job.scheduled_at<end))
    if count>=daily_quota(account,local.date()):raise HTTPException(409,'Daily publication limit reached')
    nearby=s.scalar(select(Job.id).where(*conditions,
        Job.scheduled_at>dt-timedelta(minutes=account.min_interval_minutes),
        Job.scheduled_at<dt+timedelta(minutes=account.min_interval_minutes)).limit(1))
    if nearby:raise HTTPException(409,'Minimum interval between publications would be violated')
    if account.last_published_at and abs((dt-account.last_published_at).total_seconds())<account.min_interval_minutes*60:
        raise HTTPException(409,'Minimum interval since last publication would be violated')
    return local.date()

def create_job(s,account,clip,dt,parameters=None,plan_id=None,slot=None):
    ready(s,clip)
    if not compatible(s,account,clip):raise HTTPException(409,'Clip is incompatible with account filters/platform')
    from app.security import lock
    lock(s,'platform_clip:'+account.platform+':'+str(clip.id))
    from app.content.usage import used_on_platform
    if s.scalar(select(Clip.id).where(Clip.id==clip.id,used_on_platform(account.platform))):
        raise HTTPException(409,'Clip permanently used on this platform')
    if s.scalar(select(Job.id).where(Job.clip_id==clip.id,Job.platform==account.platform).limit(1)):
        raise HTTPException(409,'Clip is already reserved or published on this platform')
    day=validate_slot(s,account,dt)
    if slot is None:
        slot=(s.scalar(select(func.max(Job.slot)).where(Job.account_id==account.id,Job.local_date==day)) or 0)+1
    job=Job(platform=account.platform,clip_id=clip.id,account_id=account.id,scheduled_at=dt,local_date=day,slot=slot,
        plan_id=plan_id,parameters=parameters or {})
    s.add(job);s.flush();return job

def enforce_execution_limits(s,account,job):
    local=now().astimezone(ZoneInfo(account.timezone))
    if local.date()!=job.local_date:
        job.status='paused'
        event(s,'missed_schedule',f'missed:{job.id}',{'job_id':str(job.id)})
        return False
    try:
        # Pending reservations have already been validated. Check actual publication times at execution.
        if not any(minute(w['start'])<=local.hour*60+local.minute<minute(w['end']) for w in account.windows):
            return False
        start=datetime.combine(local.date(),datetime.min.time(),tzinfo=ZoneInfo(account.timezone)).astimezone(UTC)
        count=s.scalar(select(func.count()).select_from(Publication).where(Publication.account_id==account.id,
            Publication.published_at>=start,Publication.published_at<=now()))
        if count>=daily_quota(account,local.date()):return False
        if account.last_published_at and now()-account.last_published_at<timedelta(minutes=account.min_interval_minutes):
            return False
        return True
    except Exception:raise

def generate(s,day=None,account_ids=None):
    control=s.get(Setting,'scheduler')
    if control and not control.value.get('enabled',True):return {'created':0,'warnings':['scheduler_paused']}
    query=select(Account).where(Account.status=='active').order_by(Account.id).with_for_update(skip_locked=True)
    if account_ids is not None:query=query.where(Account.id.in_(account_ids))
    created=0;warnings=[]
    for account in s.scalars(query):
        if account.warmup_enabled and not account.operational_started_at:
            warnings.append(f'{account.id}:awaiting_readiness');continue
        target=day or now().astimezone(ZoneInfo(account.timezone)).date()
        if target<now().astimezone(ZoneInfo(account.timezone)).date() or target>now().astimezone(ZoneInfo(account.timezone)).date()+timedelta(days=7):
            raise HTTPException(422,'Planning date must be within the next 7 days')
        if not account.worker_id or not account.device_id:
            warnings.append(f'{account.id}:unbound_account');continue
        worker=require(s,Worker,account.worker_id)
        device=require(s,Device,account.device_id)
        if not worker.enabled or not device.enabled:
            warnings.append(f'{account.id}:resources_disabled');continue
        plan=s.scalar(select(SchedulePlan).where(SchedulePlan.account_id==account.id,SchedulePlan.local_date==target))
        if not plan:
            weights=adaptive_weights(s,account) if account.adaptive else {}
            cutoff=now()+timedelta(minutes=2) if target==now().astimezone(ZoneInfo(account.timezone)).date() else None
            instants=times(target,account,weights,not_before=cutoff)
            if account.warmup_enabled and warmup_day(account,target)==1 and cutoff and daily_quota(account,target)>0:
                # The first working day begins now, even if the usual morning slot passed.
                first=next((dt for dt in local_instants(target,account) if dt>=cutoff),None)
                instants=[first] if first else []
            plan=SchedulePlan(account_id=account.id,local_date=target,config={
                'times':[dt.isoformat() for dt in instants],'timezone':account.timezone,
                'daily_limit':daily_quota(account,target),'weights':weights})
            s.add(plan);s.flush()
        for slot,stamp in enumerate(plan.config['times'],1):
            dt=datetime.fromisoformat(stamp)
            if dt<=now():continue
            if s.scalar(select(Job.id).where(Job.account_id==account.id,Job.local_date==target,Job.slot==slot)):continue
            used=select(Job.clip_id).where(Job.platform==account.platform)
            # Balance the number of previous assignments, then prefer older ready content.
            usage=select(func.count()).select_from(Job).where(Job.clip_id==Clip.id).correlate(Clip).scalar_subquery()
            from app.content.usage import used_on_platform
            query=select(Clip).where(Clip.status=='ready',Clip.verified_at.is_not(None),Clip.id.not_in(used),~used_on_platform(account.platform))
            candidates=s.scalars(query.order_by(usage,Clip.created_at,Clip.id))
            clip=next((c for c in candidates if compatible(s,account,c)),None)
            if not clip:
                warnings.append(f'{account.id}:insufficient_content')
                event(s,'insufficient_content',f'shortage:{account.id}:{target}',{'account_id':str(account.id),'date':str(target)})
                break
            try:
                create_job(s,account,clip,dt,plan_id=plan.id,slot=slot);created+=1
            except HTTPException as exc:
                warnings.append(f'{account.id}:{exc.detail}')
        if len(plan.config['times'])<daily_quota(account,target):
            event(s,'schedule_infeasible',f'infeasible:{account.id}:{target}',{'account_id':str(account.id),'date':str(target)})
    return {'created':created,'warnings':warnings}
