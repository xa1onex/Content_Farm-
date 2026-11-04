import hashlib
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, Query, Header
from fastapi.responses import StreamingResponse
from sqlalchemy import select,func
from app.db import db_session,now
from app.config import settings
from app.models import *
from app.schemas import *
from app.security import roles,idempotent,new_token,digest
from app.errors import require,event
from app.api.common import record,page
from app.content.service import register
from app.accounts.service import account_record,save_account,patch_account
from app.dispatcher import service as queue
from app.scheduler import service as scheduler
from app.analytics.service import aggregate
from app.integrations import drive
from app.workers import media

router=APIRouter(prefix='/api/v1')
DB=Depends(db_session,scope='function')
ADMIN=Depends(roles('admin'))
FARM=Depends(roles('admin','farm'))
WORKER=Depends(roles('worker'))
READER=Depends(roles('admin','worker'))

@router.get('/health')
def health(s=DB):
    s.execute(select(1))
    tick=s.get(Setting,'scheduler_heartbeat')
    return {'status':'ok','environment':settings().environment,'database':'ok','scheduler_last_tick':tick.value if tick else None}

@router.get('/taxonomy/{kind}')
def list_taxonomy(kind:Literal['categories','topics','formats'],limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),s=DB,actor=FARM):
    return page(s,{'categories':Category,'topics':Topic,'formats':Format}[kind],limit,offset)
@router.post('/taxonomy/{kind}',status_code=201)
@idempotent
def add_taxonomy(kind:Literal['categories','topics','formats'],data:TaxonomyIn,request:Request,s=DB,actor=ADMIN):
    item={'categories':Category,'topics':Topic,'formats':Format}[kind](**data.model_dump())
    s.add(item);s.flush();return record(item)

@router.get('/sources')
def sources(external_id:str|None=None,limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),s=DB,actor=FARM):return page(s,Source,limit,offset,[Source.external_id==external_id] if external_id else [])
@router.post('/sources',status_code=201)
@idempotent
def add_source(data:SourceIn,request:Request,s=DB,actor=FARM):
    item=Source(**data.model_dump());s.add(item);s.flush();return record(item)
@router.get('/sources/{source_id}')
def source(source_id:UUID,s=DB,actor=FARM):return record(require(s,Source,source_id))

@router.get('/content')
def content(status:Literal['draft','ready','unavailable','archived']|None=None,category_id:UUID|None=None,drive_file_id:str|None=None,
    limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),sha256:str|None=Query(default=None,pattern=r'^[0-9a-f]{64}$'),s=DB,actor=FARM):
    filters=[]
    if sha256:filters.append(Clip.sha256==sha256)
    if status:filters.append(Clip.status==status)
    if category_id:filters.append(Clip.category_id==category_id)
    if drive_file_id:filters.append(Clip.drive_file_id==drive_file_id)
    return page(s,Clip,limit,offset,filters)
@router.post('/content',status_code=201)
@idempotent
def add_content(data:ClipIn,request:Request,s=DB,actor=FARM):return record(register(s,data))
@router.post('/content/batch',status_code=201)
@idempotent
def batch_content(data:list[ClipIn],request:Request,s=DB,actor=FARM):
    if not 1<=len(data)<=100:raise HTTPException(422,'Batch size must be 1..100')
    return {'items':[record(register(s,item)) for item in data]}
@router.get('/content/{clip_id}')
def get_content(clip_id:UUID,s=DB,actor=FARM):return record(require(s,Clip,clip_id))
@router.patch('/content/{clip_id}')
@idempotent
def patch_content(clip_id:UUID,data:ClipPatch,request:Request,s=DB,actor=FARM):
    item=require(s,Clip,clip_id,locked=True)
    if data.status=='ready' and not item.verified_at:raise HTTPException(409,'Verify the Drive file first')
    for key,value in data.model_dump(exclude_unset=True).items():setattr(item,key,value)
    s.flush();return record(item)
@router.post('/content/{clip_id}/verify')
@idempotent
def verify_content(clip_id:UUID,request:Request,s=DB,actor=FARM):
    item=require(s,Clip,clip_id,locked=True)
    try:
        info=drive.metadata(item.drive_file_id)
        sha=info.get('sha256Checksum') or drive.sha256(item.drive_file_id)
        if item.sha256 and sha and sha!=item.sha256:raise drive.DriveError('drive_hash_mismatch')
        if sha:item.sha256=sha
        item.technical=info;item.verified_at=now();item.status='ready';s.flush()
        return {'verified':True,'content':record(item)}
    except drive.DriveError as exc:
        item.status='unavailable';item.verified_at=None
        event(s,'drive_unavailable',f'drive:{item.id}:{now().date()}',{'clip_id':str(item.id),'code':exc.code})
        return {'verified':False,'error':exc.code,'content':record(item)}
@router.get('/content/{clip_id}/drive')
def drive_info(clip_id:UUID,s=DB,actor=FARM):
    return drive.metadata(require(s,Clip,clip_id).drive_file_id)

@router.get('/accounts')
def accounts(platform:Platform|None=None,status:str|None=None,limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),s=DB,actor=ADMIN):
    q=select(Account).order_by(Account.created_at.desc(),Account.id)
    if platform:q=q.where(Account.platform==platform)
    if status:q=q.where(Account.status==status)
    return {'items':[account_record(s,a) for a in s.scalars(q.limit(limit).offset(offset))],'limit':limit,'offset':offset}
@router.post('/accounts',status_code=201)
@idempotent
def add_account(data:AccountIn,request:Request,s=DB,actor=ADMIN):return account_record(s,save_account(s,data))
@router.patch('/accounts/bulk')
@idempotent
def bulk_accounts(data:AccountBulk,request:Request,s=DB,actor=ADMIN):
    return {'items':[account_record(s,patch_account(s,require(s,Account,id,locked=True),data.changes)) for id in sorted(set(data.ids))]}
@router.get('/accounts/{account_id}')
def account(account_id:UUID,s=DB,actor=ADMIN):return account_record(s,require(s,Account,account_id))
@router.patch('/accounts/{account_id}')
@idempotent
def patch_one_account(account_id:UUID,data:AccountPatch,request:Request,s=DB,actor=ADMIN):
    return account_record(s,patch_account(s,require(s,Account,account_id,locked=True),data))

@router.get('/workers')
def workers(limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),s=DB,actor=ADMIN):return page(s,Worker,limit,offset)
@router.post('/workers',status_code=201)
@idempotent
def add_worker(data:WorkerIn,request:Request,s=DB,actor=ADMIN):
    item=Worker(**data.model_dump());s.add(item);s.flush();raw=new_token()
    s.add(ApiToken(token_hash=digest(raw),role='worker',worker_id=item.id,label=data.name))
    return {'worker':record(item),'token':raw}
@router.patch('/workers/{worker_id}')
@idempotent
def patch_worker(worker_id:UUID,data:WorkerPatch,request:Request,s=DB,actor=ADMIN):
    item=require(s,Worker,worker_id,locked=True)
    for key,value in data.model_dump(exclude_unset=True).items():
        if value is None:raise HTTPException(422,'Null values are not allowed')
        setattr(item,key,value)
    return record(item)
@router.post('/workers/heartbeat',response_model=HeartbeatOut,response_model_exclude_none=True)
@idempotent
def heartbeat(data:Heartbeat,request:Request,s=DB,actor=WORKER):
    item=require(s,Worker,actor.worker_id,locked=True)
    if not item.enabled:raise HTTPException(403,'Worker is disabled')
    item.heartbeat_at=now();item.capabilities=data.capabilities
    for device in data.devices:
        if device.worker_id!=item.id:raise HTTPException(403,'Device worker mismatch')
        existing=s.scalar(select(Device).where(Device.worker_id==item.id,Device.external_id==device.external_id))
        if not existing:s.add(Device(**device.model_dump()))
        else:
            # Workers may report capabilities and name; only admins can enable/disable devices.
            existing.name=device.name;existing.capabilities=device.capabilities
    s.flush();return {'worker_id':item.id,'server_time':now(),'heartbeat_interval_seconds':30,
        'lease_seconds':settings().lease_seconds}

@router.get('/devices')
def devices(limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),s=DB,actor=READER):
    return page(s,Device,limit,offset,[Device.worker_id==actor.worker_id] if actor.role=='worker' else [])
@router.post('/devices',status_code=201)
@idempotent
def add_device(data:DeviceIn,request:Request,s=DB,actor=ADMIN):
    item=Device(**data.model_dump());s.add(item);s.flush();return record(item)

@router.get('/jobs')
def jobs(status:str|None=None,account_id:UUID|None=None,limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),s=DB,actor=READER):
    filters=[]
    if status:filters.append(Job.status==status)
    if account_id:filters.append(Job.account_id==account_id)
    if actor.role=='worker':filters.append(Job.worker_id==actor.worker_id)
    return page(s,Job,limit,offset,filters)
@router.post('/jobs',status_code=201)
@idempotent
def add_job(data:JobIn,request:Request,s=DB,actor=ADMIN):
    account=require(s,Account,data.account_id,locked=True)
    if data.scheduled_at<now()-timedelta(minutes=1):raise HTTPException(422,'Cannot schedule in the past')
    return record(scheduler.create_job(s,account,require(s,Clip,data.clip_id),data.scheduled_at,data.parameters))
@router.post('/jobs/claim',response_model=AssignmentOut,response_model_exclude_none=True)
@idempotent
def claim_job(request:Request,data:ClaimInput|None=None,s=DB,actor=WORKER):
    return queue.claim(s,actor.worker_id,data.job_id if data else None)
@router.get('/jobs/{job_id}')
def get_job(job_id:UUID,s=DB,actor=READER):
    item=require(s,Job,job_id)
    if actor.role=='worker' and item.worker_id!=actor.worker_id:raise HTTPException(403,'Job belongs to another worker')
    return record(item)
@router.get('/jobs/{job_id}/history')
def job_history(job_id:UUID,limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),s=DB,actor=ADMIN):
    require(s,Job,job_id)
    return {'attempts':page(s,Attempt,limit,offset,[Attempt.job_id==job_id]),
        'events':page(s,JobEvent,limit,offset,[JobEvent.job_id==job_id])}
@router.post('/jobs/{job_id}/ack',response_model=LeaseOut,response_model_exclude_none=True)
@idempotent
def acknowledge_job(job_id:UUID,data:LeaseEvent,request:Request,s=DB,actor=WORKER):return queue.acknowledge(s,job_id,actor.worker_id,data)
@router.post('/jobs/{job_id}/progress',response_model=LeaseOut,response_model_exclude_none=True)
@idempotent
def progress_job(job_id:UUID,data:Progress,request:Request,s=DB,actor=WORKER):return queue.progress(s,job_id,actor.worker_id,data)
@router.post('/jobs/{job_id}/result',response_model=ResultOut,response_model_exclude_none=True)
@idempotent
def result_job(job_id:UUID,data:Result,request:Request,s=DB,actor=WORKER):return queue.result(s,job_id,actor.worker_id,data)
@router.post('/jobs/{job_id}/media-ticket',response_model=MediaTicketOut,response_model_exclude_none=True)
@idempotent
def media_ticket(job_id:UUID,data:LeaseEvent,request:Request,s=DB,actor=WORKER):
    job=queue.fenced(s,job_id,actor.worker_id,data.lease_token)
    return media.issue(job,actor.worker_id)
@router.get('/jobs/{job_id}/video')
def video(job_id:UUID,x_media_ticket:str=Header(),s=DB,actor=WORKER):
    job=require(s,Job,job_id)
    if job.worker_id!=actor.worker_id or job.status not in ('assigned','processing') or not job.lease_until or job.lease_until<=now():
        raise HTTPException(403,'Active lease required')
    media.verify(x_media_ticket,job,actor.worker_id)
    info,chunks=drive.download(require(s,Clip,job.clip_id).drive_file_id)
    return StreamingResponse(chunks,media_type=info['mimeType'],headers={
        'Content-Length':info['size'],'Content-Disposition':f'attachment; filename="{job.clip_id}.mp4"',
        'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

@router.post('/jobs/{job_id}/action')
@idempotent
def job_action(job_id:UUID,data:JobAction,request:Request,s=DB,actor=ADMIN):
    job=require(s,Job,job_id,locked=True)
    account=require(s,Account,job.account_id,locked=True)
    if job.status=='published':raise HTTPException(409,'Published jobs are immutable')
    if job.status in ('assigned','processing'):
        raise HTTPException(409,'Wait for worker result or lease expiry; active work cannot be cancelled')
    if data.action.startswith('resolve_'):
        if job.status!='unknown':raise HTTPException(409,'Only unknown jobs can be reconciled')
        if data.action=='resolve_published':
            if not data.external_id or not data.published_at:raise HTTPException(422,'external_id and published_at required')
            if data.published_at>now():raise HTTPException(422,'Publication cannot be in the future')
            queue.publish(s,job,data.external_id,data.url,data.published_at)
        else:
            job.status='failed';job.result={'no_publish_attempted':True,'operator_verified':True,'reason':data.reason}
            job.lease_hash=None;job.lease_until=None
    elif job.status=='unknown':raise HTTPException(409,'Unknown job requires reconciliation first')
    elif data.action=='cancel':job.status='cancelled'
    elif data.action=='pause':job.status='paused'
    elif data.action in ('retry','reschedule'):
        if job.status=='failed' and not job.result.get('no_publish_attempted') and not job.result.get('operator_verified'):
            # Assigned lease expiry is safe; no ack was recorded.
            att=queue.attempt(s,job)
            if not att or att.error_code!='lease_expired':raise HTTPException(409,'No proof that retry is safe')
        if not data.scheduled_at:raise HTTPException(422,'scheduled_at is required')
        if data.scheduled_at<now():raise HTTPException(422,'Cannot reschedule in the past')
        day=scheduler.validate_slot(s,account,data.scheduled_at,exclude_id=job.id)
        slot=(s.scalar(select(func.max(Job.slot)).where(Job.account_id==account.id,Job.local_date==day)) or 0)+1
        job.local_date=day;job.slot=slot;job.scheduled_at=data.scheduled_at
        job.status='scheduled';job.lease_hash=None;job.lease_until=None;job.worker_id=None;job.device_id=None
        job.max_attempts=max(job.max_attempts,job.attempts+1)
    s.add(Audit(actor=str(actor.id),action=f'job:{data.action}',detail={'job_id':str(job.id),'reason':data.reason}))
    s.flush();return record(job)

@router.get('/schedules')
def schedules(limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),s=DB,actor=ADMIN):return page(s,SchedulePlan,limit,offset)
@router.post('/schedules/generate')
@idempotent
def generate(data:GenerateIn,request:Request,s=DB,actor=ADMIN):return scheduler.generate(s,data.local_date,data.account_ids)
@router.post('/schedules/control')
@idempotent
def control(data:SchedulerControl,request:Request,s=DB,actor=ADMIN):
    setting=s.get(Setting,'scheduler')
    if not setting:s.add(Setting(key='scheduler',value=data.model_dump()))
    else:setting.value=data.model_dump()
    return data.model_dump()

@router.get('/publications')
def publications(account_id:UUID|None=None,limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),s=DB,actor=READER):
    filters=[]
    if account_id:filters.append(Publication.account_id==account_id)
    if actor.role=='worker':filters.append(Publication.account_id.in_(select(Account.id).where(Account.worker_id==actor.worker_id)))
    return page(s,Publication,limit,offset,filters)
@router.post('/analytics/snapshots',status_code=201)
@idempotent
def snapshot(data:SnapshotIn,request:Request,s=DB,actor=READER):
    pub=require(s,Publication,data.publication_id)
    account=require(s,Account,pub.account_id)
    if actor.role=='worker' and account.worker_id!=actor.worker_id:raise HTTPException(403,'Publication belongs to another worker')
    if data.measured_at<pub.published_at:raise HTTPException(422,'Measurement precedes publication')
    prior=s.scalar(select(Snapshot).where(Snapshot.event_id==data.event_id))
    values=data.model_dump()
    if prior:
        if any(getattr(prior,k)!=v for k,v in values.items()):raise HTTPException(409,'Conflicting statistics event')
        return record(prior)
    item=Snapshot(**values);s.add(item);s.flush();return record(item)
@router.get('/analytics/snapshots')
def snapshots(publication_id:UUID|None=None,limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),s=DB,actor=ADMIN):
    return page(s,Snapshot,limit,offset,[Snapshot.publication_id==publication_id] if publication_id else [])
@router.get('/analytics/report')
def report(group_by:Literal['account','platform','category','format','source','clip','hour','day','week','month']='platform',
    start:datetime|None=None,end:datetime|None=None,s=DB,actor=ADMIN):
    end=end or now();start=start or end-timedelta(days=7)
    if not start.tzinfo or not end.tzinfo or start>=end or end-start>timedelta(days=366):
        raise HTTPException(422,'Provide aware start < end, maximum 366 days')
    return aggregate(s,group_by,start,end)
@router.get('/events')
def events(limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),s=DB,actor=ADMIN):return page(s,Event,limit,offset)
@router.get('/audit')
def audit(limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),s=DB,actor=ADMIN):return page(s,Audit,limit,offset)
@router.get('/settings')
def get_settings(s=DB,actor=ADMIN):return {'items':[{'key':x.key,'value':x.value} for x in s.scalars(select(Setting))]}
@router.get('/tokens')
def tokens(limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),s=DB,actor=ADMIN):return page(s,ApiToken,limit,offset)
@router.post('/tokens',status_code=201)
@idempotent
def issue_token(data:TokenIn,request:Request,s=DB,actor=ADMIN):
    raw=new_token();item=ApiToken(token_hash=digest(raw),**data.model_dump());s.add(item);s.flush()
    return {'id':item.id,'token':raw,'role':item.role}
@router.post('/tokens/{token_id}/revoke')
@idempotent
def revoke_token(token_id:UUID,request:Request,s=DB,actor=ADMIN):
    item=require(s,ApiToken,token_id,locked=True);item.enabled=False
    return {'id':item.id,'enabled':False}

class SubscriptionIn(Input):
    chat_id:int
    enabled:bool
@router.post('/telegram/subscriptions')
@idempotent
def subscribe(data:SubscriptionIn,request:Request,s=DB,actor=ADMIN):
    from app.security import lock
    lock(s,f'telegram_subscription:{data.chat_id}')
    item=s.scalar(select(TelegramSubscriber).where(TelegramSubscriber.chat_id==data.chat_id))
    if item:item.enabled=data.enabled
    else:s.add(TelegramSubscriber(**data.model_dump()))
    return data.model_dump()
@router.post('/telegram/notifications/claim')
@idempotent
def claim_notification(request:Request,s=DB,actor=ADMIN):
    from app.telegram_admin.notifications import enqueue
    enqueue()
    delivery=s.scalar(select(NotificationDelivery).where(NotificationDelivery.status.in_(['pending','sending']),
        NotificationDelivery.attempts<10,NotificationDelivery.next_at<=now()).order_by(NotificationDelivery.next_at)
        .with_for_update(skip_locked=True).limit(1))
    if not delivery:return {'delivery':None}
    delivery.status='sending';delivery.attempts+=1;delivery.next_at=now()+timedelta(seconds=90)
    ev=s.get(Event,delivery.event_id)
    return {'delivery':{'event_id':ev.id,'chat_id':delivery.chat_id,'attempt':delivery.attempts,
        'text':f'Автопостинг: {ev.kind}\n{ev.detail}'}}
class NotificationResult(Input):
    attempt:int
    delivered:bool
@router.post('/telegram/notifications/{event_id}/{chat_id}/result')
@idempotent
def notification_result(event_id:UUID,chat_id:int,data:NotificationResult,request:Request,s=DB,actor=ADMIN):
    item=s.get(NotificationDelivery,(event_id,chat_id),with_for_update=True)
    if not item:raise HTTPException(404,'Delivery not found')
    if item.attempts!=data.attempt:raise HTTPException(409,'Stale notification attempt')
    item.status='sent' if data.delivered else ('failed' if item.attempts>=10 else 'pending')
    return {'status':item.status}

@router.patch('/devices/{device_id}')
@idempotent
def patch_device(device_id:UUID,data:DevicePatch,request:Request,s=DB,actor=ADMIN):
    item=require(s,Device,device_id,locked=True)
    for key,value in data.model_dump(exclude_unset=True).items():
        if value is None:raise HTTPException(422,'Null values are not allowed')
        setattr(item,key,value)
    s.flush();return record(item)
@router.patch('/sources/{source_id}')
@idempotent
def patch_source(source_id:UUID,data:SourcePatch,request:Request,s=DB,actor=FARM):
    item=require(s,Source,source_id,locked=True)
    for key,value in data.model_dump(exclude_unset=True).items():
        if value is None and key not in ('category_id','topic_id'):raise HTTPException(422,'Null value is not allowed')
        setattr(item,key,value)
    s.flush();return record(item)
@router.get('/health/ready')
def readiness(s=DB):
    tick=s.get(Setting,'scheduler_heartbeat')
    if not tick or now()-datetime.fromisoformat(tick.value['at'])>timedelta(seconds=120):
        raise HTTPException(503,'Scheduler heartbeat is stale')
    return {'status':'ok','scheduler':tick.value}

@router.post('/workers/{worker_id}/token')
@idempotent
def rotate_worker_token(worker_id:UUID,request:Request,s=DB,actor=ADMIN):
    worker=require(s,Worker,worker_id,locked=True)
    for token in s.scalars(select(ApiToken).where(ApiToken.worker_id==worker_id)):token.enabled=False
    raw=new_token();item=ApiToken(token_hash=digest(raw),role='worker',worker_id=worker_id,label=worker.name)
    s.add(item);s.flush();return {'worker_id':worker_id,'token_id':item.id,'token':raw}
