"""Read models for the control room. Forecasts never alter publication schedules."""
import math,statistics,uuid
from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select,func,exists,literal,cast,String,or_
from app.db import now
from app.models import (Account,AccountCategory,AccountTopic,AccountFormat,Clip,Job,Publication,
                        Snapshot,Setting,Worker,SchedulePlan)
from app.scheduler.service import daily_quota

TZ=ZoneInfo('Europe/Moscow')
PLATFORMS=('instagram','tiktok','youtube')
WAITING={'pending','scheduled','assigned','processing','retry_wait'}


def user_id(actor):
    return int(str(actor.id).split(':')[1]) if str(actor.id).startswith('telegram:') else None


def markers(s):
    return {r.key:r.value for r in s.scalars(select(Setting).where(or_(
        Setting.key.like('review_approved:%'),Setting.key.like('review_deleted:%'),Setting.key.like('review_rejected:%'))))}


def decision(clip,flags):
    key=str(clip.id)
    if 'review_deleted:'+key in flags or clip.status=='archived':return 'deleted'
    if 'review_rejected:'+key in flags:return 'rejected'
    return 'approved' if 'review_approved:'+key in flags else 'pending'


def latest_metrics(s):
    # Exactly one latest non-null reading per publication; an absent reading is not zero.
    rows=s.execute(select(Snapshot.publication_id,Snapshot.views,Snapshot.measured_at)
        .where(Snapshot.views.is_not(None)).distinct(Snapshot.publication_id)
        .order_by(Snapshot.publication_id,Snapshot.measured_at.desc(),Snapshot.id.desc())).all()
    return {r.publication_id:{'views':r.views,'at':r.measured_at} for r in rows}


def library(s,user=None,offset=0,limit=60,q='',filter='all',platform=None):
    approved=exists(select(1).where(Setting.key==literal('review_approved:')+cast(Clip.id,String)))
    deleted=exists(select(1).where(Setting.key==literal('review_deleted:')+cast(Clip.id,String)))
    rejected=exists(select(1).where(Setting.key==literal('review_rejected:')+cast(Clip.id,String)))
    rules=[]
    if filter=='deleted':rules.append(or_(deleted,Clip.status=='archived'))
    else:rules.extend([~deleted,Clip.status!='archived'])
    if filter=='pending':rules.extend([~approved,~rejected])
    if filter=='approved':rules.extend([approved,~rejected])
    if filter=='rejected':rules.append(rejected)
    if filter=='queued':rules.append(exists(select(1).where(Job.clip_id==Clip.id,Job.status.in_(WAITING))))
    if filter=='published':rules.append(exists(select(1).where(Publication.clip_id==Clip.id)))
    if q:rules.append(or_(Clip.title.ilike('%'+q.replace('%','\\%').replace('_','\\_')+'%'),Clip.passport.ilike('%'+q+'%')))
    if platform:rules.append(Clip.platforms.contains([platform]))
    total=s.scalar(select(func.count()).select_from(Clip).where(*rules))
    clips=list(s.scalars(select(Clip).where(*rules).order_by(Clip.created_at.desc(),Clip.id.desc()).offset(offset).limit(limit)))
    return {'items':serialize_clips(s,clips,user),'total':total,'offset':offset,'next_offset':offset+len(clips),
            'has_more':offset+len(clips)<total}


def serialize_clips(s,clips,user):
    from app.models import ClipPlatformUse
    ids=[c.id for c in clips]
    if not ids:return []
    flags=markers(s);uses={(r.clip_id,r.platform) for r in s.scalars(select(ClipPlatformUse).where(ClipPlatformUse.clip_id.in_(ids)))}
    jobs=defaultdict(list);publications=defaultdict(list)
    for j in s.scalars(select(Job).where(Job.clip_id.in_(ids))):jobs[j.clip_id].append(j)
    for p in s.scalars(select(Publication).where(Publication.clip_id.in_(ids))):publications[p.clip_id].append(p)
    keys=['editorial_feedback:'+str(i)+':'+str(user) for i in ids]
    feedback={r.value['clip_id']:r.value for r in s.scalars(select(Setting).where(Setting.key.in_(keys)))}
    metrics=latest_metrics(s);items=[]
    for c in clips:
        d=decision(c,flags);reserved={j.platform for j in jobs[c.id]};reads=[metrics[p.id] for p in publications[c.id] if p.id in metrics]
        f=feedback.get(str(c.id),{})
        transcript=(c.extra or {}).get('transcript')
        spoken={'text':transcript['text'],'language':transcript.get('language')} if isinstance(transcript,dict) and isinstance(transcript.get('text'),str) and transcript['text'].strip() else None
        items.append({'id':str(c.id),'passport':c.passport,'title':c.title,'description':c.description,
          'created_at':c.created_at.isoformat(),'duration_seconds':c.duration_seconds,'status':c.status,'decision':d,
          'transcript':spoken,
          'feedback':{'rating':f.get('rating',0),'comment':f.get('comment',''),'at':f.get('at')},
          'platforms':{p:('used' if (c.id,p) in uses else 'reserved' if p in reserved else 'review_pending' if d!='approved' else 'available' if c.status=='ready' and p in c.platforms else 'unavailable') for p in PLATFORMS},
          'published_count':len(publications[c.id]),'planned_count':sum(j.status in WAITING for j in jobs[c.id]),
          'views':sum(r['views'] for r in reads) if reads else None,
          'can_approve':d not in ('deleted',) and c.status=='ready' and c.verified_at is not None})
    return items


def supply(s,accounts,stamp):
    """Match unique per-platform clips to account filters, including fixed reservations."""
    from app.content.usage import used_on_platform
    flags=markers(s);plans=[];pools={};reservations=defaultdict(list);filters={a.id:{} for a in accounts}
    for model,field in [(AccountCategory,'category_id'),(AccountTopic,'topic_id'),(AccountFormat,'format_id')]:
        for row in s.scalars(select(model).where(model.account_id.in_([a.id for a in accounts]))):
            filters[row.account_id].setdefault(field,set()).add(getattr(row,field))
    def fits(clip,a):return all(not values or getattr(clip,key) in values for key,values in filters[a.id].items())
    for platform in PLATFORMS:
        pool=list(s.scalars(select(Clip).where(Clip.status=='ready',Clip.verified_at.is_not(None),
              Clip.platforms.contains([platform]),~used_on_platform(platform))))
        pool=[c for c in pool if decision(c,flags)=='approved'];ids={c.id:c for c in pool}
        existing=list(s.scalars(select(Job).where(Job.platform==platform)))
        reserved={j.clip_id for j in existing}
        pools[platform]={c.id:c for c in pool if c.id not in reserved}
        for j in existing:
            if j.status in WAITING and j.clip_id in ids and j.account_id in filters:
                reservations[(platform,j.local_date,j.account_id)].append(ids[j.clip_id])
    counts={p:len(pools[p])+sum(len(v) for (plat,_,_),v in reservations.items() if plat==p) for p in PLATFORMS}
    pubs=list(s.scalars(select(Publication).where(Publication.published_at>=stamp-timedelta(days=2))))
    quota_accounts=[a for a in accounts if a.status=='active' and a.worker_id and a.device_id]
    contiguous=True;coverage=0.;stockout=None
    for offset in range(7):
        date=stamp.astimezone(TZ).date()+timedelta(days=offset);by_platform={};needed=filled=0
        for platform in PLATFORMS:
            slots=[];fixed=0;demand=0
            for a in [a for a in quota_accounts if a.platform==platform]:
                day=stamp.astimezone(ZoneInfo(a.timezone)).date()+timedelta(days=offset)
                done=sum(p.account_id==a.id and p.published_at.astimezone(ZoneInfo(a.timezone)).date()==day for p in pubs)
                required=max(0,daily_quota(a,day)-done);demand+=required
                reserved=[c for c in reservations.get((platform,day,a.id),[]) if fits(c,a)]
                ready=min(required,len(reserved));fixed+=ready;slots.extend([a]*(required-ready))
            # Augmenting paths avoid overestimating stock for narrowly filtered accounts.
            matching={}
            def assign(slot,seen):
                for cid,clip in pools[platform].items():
                    if cid in seen or not fits(clip,slot):continue
                    seen.add(cid)
                    if cid not in matching or assign(matching[cid],seen):matching[cid]=slot;return True
                return False
            for a in sorted(slots,key=lambda a:sum(fits(c,a) for c in pools[platform].values())):assign(a,set())
            count=fixed+len(matching)
            for cid in matching:pools[platform].pop(cid)
            by_platform[platform]={'needed':demand,'covered':count};needed+=demand;filled+=count
        plans.append({'date':date.isoformat(),'needed':needed,'covered':filled,'shortage':needed-filled,'platforms':by_platform})
        if needed and contiguous:
            if filled==needed:coverage+=1
            else:coverage+=filled/needed;contiguous=False;stockout=date.isoformat()
    active=bool(quota_accounts)
    return {'days':round(coverage,2) if active else None,'tone':('green' if coverage>=3 else 'yellow' if coverage>=2 else 'red') if active else 'neutral',
            'available_by_platform':counts,'calendar':plans,'first_shortage_date':stockout,'active_accounts':len(quota_accounts),
            'basis':'approved_unique_per_platform_with_account_filters','horizon_days':7}


def publication_timing(s,jobs,pubs,stamp):
    durations=[(p.published_at-j.processing_started_at).total_seconds() for p in pubs for j in jobs
        if p.job_id==j.id and j.processing_started_at and 30<=(p.published_at-j.processing_started_at).total_seconds()<=1800]
    typical=statistics.median(durations[-40:]) if durations else 300
    low=max(90,round(typical*.7));high=min(1800,max(300,round(typical*1.5+60)))
    actual={p.job_id:p.published_at for p in pubs};ends={};busy={};result={}
    account_map={a.id:a for a in s.scalars(select(Account))}
    workers={i:a.worker_id for i,a in account_map.items()}
    worker_map={w.id:w for w in s.scalars(select(Worker))}
    from app.content.usage import used_on_platform
    from app.content.moderation import blocked as moderation_blocked
    consumed={p:set(s.scalars(select(Clip.id).where(used_on_platform(p)))) for p in PLATFORMS}
    uncertain={j.worker_id for j in jobs if j.status=='unknown' and j.worker_id}
    campaigns={r.key.split(':',1)[1]:r.value for r in s.scalars(select(Setting).where(Setting.key.like('release_campaign:%')))}
    control=s.get(Setting,'scheduler')
    gates={r.key.split(':',1)[1]:r.value for r in s.scalars(select(Setting).where(Setting.key.like('campaign_job:%')))}
    all_jobs={str(j.id):j for j in jobs}
    for j in sorted(jobs,key=lambda j:(j.status not in ('processing','assigned'),j.scheduled_at,j.created_at)):
        if j.status not in WAITING:continue
        gate=gates.get(str(j.id),{});previous=gate.get('previous_job_id');blocked=None
        account=account_map.get(j.account_id);resource=j.worker_id or workers.get(j.account_id);worker=worker_map.get(resource)
        if j.status not in ('processing','assigned'):
            if control and not control.value.get('enabled',True):blocked='scheduler_paused'
            elif not account or account.status!='active':blocked='account_paused'
            elif resource in uncertain:blocked='worker_uncertain'
            elif not worker or not worker.enabled or not worker.heartbeat_at or stamp-worker.heartbeat_at>timedelta(seconds=120):blocked='worker_offline'
            elif j.clip_id in consumed[j.platform]:blocked='content_used'
            elif moderation_blocked(s,j.clip_id):blocked='moderation_required'
            elif j.attempts>=j.max_attempts:blocked='attempt_limit'
            elif gate.get('campaign_id') and not campaigns.get(gate['campaign_id'],{}).get('enabled'):blocked='campaign_paused'
        if previous and (previous not in all_jobs or all_jobs[previous].status in ('unknown','failed','paused','cancelled') or all_jobs[previous].status=='published' and uuid.UUID(previous) not in actual or result.get(previous,{}).get('blocked')):blocked='previous_publication_unconfirmed'
        if blocked:
            result[str(j.id)]={'blocked':blocked,'start_earliest':None,'finish_latest':None};continue
        resource=j.worker_id or workers.get(j.account_id)
        start=max(stamp,j.scheduled_at,busy.get(resource,stamp))
        if previous:
            pred=actual.get(uuid.UUID(previous)) or ends.get(previous)
            if pred:start=max(start,pred+timedelta(seconds=gate.get('delay_seconds',0)))
        if j.status=='processing' and j.processing_started_at:
            earliest=max(stamp+timedelta(seconds=15),j.processing_started_at+timedelta(seconds=low))
            latest=max(earliest+timedelta(seconds=30),j.processing_started_at+timedelta(seconds=high))
        else:earliest=start+timedelta(seconds=low);latest=start+timedelta(seconds=high)
        ends[str(j.id)]=latest;busy[resource]=latest+timedelta(seconds=40)
        result[str(j.id)]={'start_earliest':start.isoformat(),'finish_earliest':earliest.isoformat(),'finish_latest':latest.isoformat(),
          'overdue_seconds':max(0,int((stamp-j.scheduled_at).total_seconds())),'blocked':None}
    return result,{'low_seconds':low,'high_seconds':high,'sample_size':len(durations),'confidence':'measured' if durations else 'rough'}


def snapshot(s,actor,legacy):
    stamp=now();midnight=datetime.combine(stamp.astimezone(TZ).date(),datetime.min.time(),tzinfo=TZ)
    start=midnight.astimezone(timezone.utc);end=start+timedelta(days=1)
    accounts=list(s.scalars(select(Account).where(Account.status!='deleted')))
    pubs=list(s.scalars(select(Publication).order_by(Publication.published_at.desc())))
    jobs=list(s.scalars(select(Job).order_by(Job.scheduled_at)));metrics=latest_metrics(s);flags=markers(s)
    clips_today=s.scalar(select(func.count()).select_from(Clip).where(Clip.created_at>=start,Clip.created_at<end))
    timing,estimate=publication_timing(s,jobs,pubs,stamp)
    queues=[];titles=dict(s.execute(select(Clip.id,Clip.title)).all());by_account={a.id:a for a in accounts}
    for j in jobs:
        a=by_account.get(j.account_id)
        queues.append({'id':str(j.id),'clip_id':str(j.clip_id),'account_id':str(j.account_id),'account':a.username if a else 'Архивный аккаунт',
            'platform':j.platform,'title':titles.get(j.clip_id,'Ролик'),'status':j.status,'scheduled_at':j.scheduled_at.isoformat(),
            'started_at':j.processing_started_at.isoformat() if j.processing_started_at else None,
            'published_at':next((p.published_at.isoformat() for p in pubs if p.job_id==j.id),None),
            'attempts':j.attempts,'timing':timing.get(str(j.id)),
            'error_code':(j.result or {}).get('error_code') if j.status in ('failed','unknown','paused') else None})
    account_rows=[];health={a['id']:a for a in legacy['accounts']}
    for a in accounts:
        ap=[p for p in pubs if p.account_id==a.id];aj=[j for j in jobs if j.account_id==a.id];reads=[metrics[p.id] for p in ap if p.id in metrics]
        h=health.get(str(a.id),{});reasons=h.get('reasons',[])
        if 'device_standby' in reasons:reasons=[r for r in reasons if r not in ('profile_missing','profile_unverified','proxy_unverified')]
        failures=[j for j in aj if j.status=='unknown' or j.status=='failed' and not (j.result or {}).get('no_republish') and j.scheduled_at>=start];future=[j for j in aj if j.status in WAITING]
        account_rows.append({'id':str(a.id),'username':a.username,'name':a.name,'platform':a.platform,'status':a.status,
            'device':h.get('device'),'health':'ready' if h.get('ready') else 'standby' if reasons==['device_standby'] else 'attention',
            'reasons':reasons,'published_total':len(ap),'published_today':sum(start<=p.published_at<end for p in ap),
            'views':sum(r['views'] for r in reads) if reads else None,'measured_publications':len(reads),
            'metrics_at':max((r['at'] for r in reads),default=None),'planned_count':len(future),'errors':len(failures),
            'next_at':min((j.scheduled_at for j in future),default=None),'daily_quota':daily_quota(a,stamp.astimezone(ZoneInfo(a.timezone)).date()),
            'timezone':a.timezone,'windows':a.windows})
    from app.operations import alertable_problems
    incidents=[v for v in alertable_problems(legacy).values() if v.get('code')!='publication_unknown']
    for j in jobs:
        if j.status=='unknown' or j.status=='failed' and not (j.result or {}).get('no_republish') and j.scheduled_at>=start:
            a=by_account.get(j.account_id)
            incidents.append({'code':'publication_unknown' if j.status=='unknown' else 'publication_failed','message':'Нужно проверить результат публикации' if j.status=='unknown' else 'Ошибка публикации','account':a.username if a else None,'job_id':str(j.id)})
    readings=list(metrics.values());total_views=sum(r['views'] for r in readings) if readings else None
    decision_counts={'pending':0,'approved':0,'rejected':0,'deleted':0}
    for c in s.scalars(select(Clip)):decision_counts[decision(c,flags)]+=1
    return {'workspace':{'timezone':'Europe/Moscow','date':midnight.date().isoformat(),'at':stamp.isoformat(),
        'today':{'cut':clips_today,'published':sum(start<=p.published_at<end for p in pubs),'waiting':sum(j.status in WAITING for j in jobs),
            'planned_today':sum(j.local_date==midnight.date() and j.status in WAITING for j in jobs)},
        'views':{'total':total_views,'measured_publications':len(readings),'total_publications':len(pubs),'at':max((r['at'] for r in readings),default=None)},
        'moderation':decision_counts,'issues':incidents,'issue_count':len(incidents),
        'queue':queues[-500:],'accounts':account_rows,'supply':supply(s,accounts,stamp),'publication_estimate':estimate},
        'library':library(s,user_id(actor)),
        'publication_details':[{'id':str(p.id),'url':p.url,'account':by_account[p.account_id].username if p.account_id in by_account else 'Архивный аккаунт',
          'title':titles.get(p.clip_id,'Ролик'),'platform':by_account[p.account_id].platform if p.account_id in by_account else None,
          'published_at':p.published_at.isoformat(),'views':metrics.get(p.id,{}).get('views'),'measured_at':metrics.get(p.id,{}).get('at')} for p in pubs[:200]]}
