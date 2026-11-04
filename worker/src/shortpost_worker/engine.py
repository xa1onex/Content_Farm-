import asyncio,logging,time,uuid
from datetime import datetime
from pathlib import Path
from .models import Assignment
from .errors import WorkerError,LeaseLost,StopRequested,UIError
from .devices import Devices,probe_video
from .appium_session import Session
from .platforms import PUBLISHERS
from .platforms.profile import Profile
log=logging.getLogger(__name__)

class Guard:
    def __init__(self,api,config,expires):
        self.api=api;self.config=config;self.deadline=0.;self.lost=False
        self.absolute=time.monotonic()+config.max_processing_seconds
        self.update(expires)
    def update(self,expires):
        value=datetime.fromisoformat(str(expires).replace('Z','+00:00'))
        if not value.tzinfo:raise WorkerError('lease_deadline_without_timezone')
        self.deadline=min(self.absolute,time.monotonic()+value.timestamp()-self.api.clock.now())
    def check(self):
        if self.lost or self.api.auth_failed.is_set() or time.monotonic()+self.config.lease_safety_seconds>=self.deadline:
            raise LeaseLost()

class ReadGuard:
    def __init__(self,api,seconds=120):self.api=api;self.deadline=time.monotonic()+seconds
    def check(self):
        if self.api.auth_failed.is_set() or time.monotonic()>self.deadline:raise LeaseLost()

def lease_event(assignment,**extra):
    return {'event_id':uuid.uuid4().hex,'lease_token':assignment.lease_token,**extra}

class Engine:
    def __init__(self,config,journal,api,devices=None,publisher_factory=None,session_factory=Session):
        self.config=config;self.journal=journal;self.api=api
        self.devices=devices or Devices(config,journal)
        self.publisher_factory=publisher_factory;self.session_factory=session_factory
        self.tasks=set();self.stopping=False;self.executing=set()
        self.device_health={};self.live=False
        self.media_dir=config.state_dir/'media';self.media_dir.mkdir(parents=True,exist_ok=True)
    async def renew(self,assignment,guard):
        pending=None
        while True:
            await asyncio.sleep(self.config.progress_seconds)
            try:
                if pending is None:
                    payload=lease_event(assignment,phase='executing',detail='Worker lease renewal')
                    pending=self.journal.operation('progress',f'/jobs/{assignment.job.id}/progress',payload)
                response=await self.api.send_operation(pending)
                if response.get('status')!='processing':guard.lost=True;return
                guard.update(response['lease_expires_at']);guard.check();pending=None
            except WorkerError as exc:
                if exc.status in (400,401,403,404,409,422):guard.lost=True;return
                # Keep retrying the SAME event/key only while the old lease remains valid.
                try:guard.check()
                except LeaseLost:return
    async def report(self,row):
        payload=self.journal.open(row['result'])
        response=await self.api.mutate('result',f'/jobs/{row["job_id"]}/result',payload,
            slot=f'result:{row["job_id"]}:{row["attempt"]}')
        self.journal.result_acknowledged(row['job_id'],row['attempt'],response)
        return response
    def platform(self,assignment,device,driver,adb,guard):
        if self.publisher_factory:return self.publisher_factory(assignment,device,driver,adb,guard)
        path=device.profiles.get(assignment.account.platform)
        if not path:raise UIError('profile_not_configured')
        profile=Profile.load(path)
        return PUBLISHERS[assignment.account.platform](profile,driver,adb,guard,self.config.state_dir/'artifacts')
    async def process(self,raw):
        assignment=Assignment.model_validate(raw)
        identifier=str(assignment.job.id);attempt=assignment.job.attempts
        if (identifier,attempt) in self.executing:return
        row=self.journal.job(identifier,attempt)
        if not row:self.journal.save_assignment(assignment.model_dump(mode='json'))
        row=self.journal.job(identifier,attempt)
        if row['result']:
            try:await self.report(row)
            except WorkerError:pass
            return
        if row['irreversible_at'] is not None:
            raise RuntimeError('Recovered potentially irreversible job must never enter execution')
        guard=Guard(self.api,self.config,assignment.lease_expires_at)
        self.executing.add((identifier,attempt))
        renewal=None;session=None;publisher=None;android_path=None;device_lock=None;locked=False
        local=self.media_dir/(identifier+'.mp4')
        try:
            guard.check()
            ack_body=lease_event(assignment)
            # Reuse a previously journaled ACK payload if the operation was interrupted.
            slot=f'ack:{identifier}:{attempt}'
            existing=next((op for op in self.journal.pending('ack') if op['slot']==slot),None)
            if existing:ack_body=self.journal.open(existing['body'])
            response=await self.api.mutate('ack',f'/jobs/{identifier}/ack',ack_body,slot=slot)
            if response.get('status')!='processing' or not response.get('lease_expires_at'):raise LeaseLost()
            guard.update(response['lease_expires_at']);guard.check()
            self.journal.stage(identifier,attempt,'processing')
            renewal=asyncio.create_task(self.renew(assignment,guard))
            device=self.devices.configured(assignment.device.external_id)
            if self.journal.control('paused')=='1':raise WorkerError('operator_paused')
            device_lock=self.devices.locks[device.external_id]
            await device_lock.acquire();locked=True
            guard.check()
            ticket=await self.api.mutate('ticket',f'/jobs/{identifier}/media-ticket',lease_event(assignment))
            path=self.api.media_path(ticket['url'],identifier)
            await self.api.download(path,ticket['ticket'],local,assignment.content.sha256,guard)
            await asyncio.to_thread(probe_video,local,self.config.ffprobe_path,assignment.content.duration_seconds)
            adb=await asyncio.to_thread(self.devices.connect,device,guard)
            session=self.session_factory(device,adb.serial)
            driver=await asyncio.to_thread(session.open)
            publisher=self.platform(assignment,device,driver,adb,guard)
            await asyncio.to_thread(publisher.verify_account,assignment)
            android_path=await asyncio.to_thread(publisher.prepare_media,local)
            await asyncio.to_thread(publisher.open_upload)
            await asyncio.to_thread(publisher.fill_metadata)
            self.journal.stage(identifier,attempt,'prepared')
            if not self.config.allow_publish:raise WorkerError('publishing_not_enabled')
            await asyncio.to_thread(publisher.submit,lambda:self.journal.mark_irreversible(identifier,attempt))
            proof=await asyncio.to_thread(publisher.verify_publication)
            payload=lease_event(assignment,outcome='published',external_id=proof['external_id'],url=proof['url'],published_at=self.api.clock.iso())
            self.journal.record_result(identifier,attempt,payload)
            await self.report(self.journal.job(identifier,attempt))
        except (Exception,asyncio.CancelledError) as exc:
            code=exc.code if isinstance(exc,WorkerError) else ('worker_cancelled' if isinstance(exc,asyncio.CancelledError) else 'worker_internal_error')
            log.error('job_error code=%s job=%s attempt=%s',code,identifier,attempt)
            current=self.journal.job(identifier,attempt)
            if publisher:
                try:await asyncio.to_thread(publisher.evidence,identifier,attempt,code)
                except Exception:pass
            if not current['result']:
                uncertain=current['irreversible_at'] is not None
                payload=lease_event(assignment,outcome='unknown' if uncertain else 'safe_failure',
                    error_code=code,error_detail='See private local diagnostics',**({} if uncertain else {'no_publish_attempted':True}))
                self.journal.record_result(identifier,attempt,payload)
            try:await self.report(self.journal.job(identifier,attempt))
            except WorkerError as error:
                # The server may already have converted processing to unknown. Do not requeue locally.
                log.warning('result_pending code=%s status=%s job=%s',error.code,error.status,identifier)
            if isinstance(exc,UIError):self.journal.control('paused','1')
        finally:
            if renewal:
                renewal.cancel()
                try:await renewal
                except (asyncio.CancelledError,Exception):pass
            if session:
                if android_path and publisher and self.journal.job(identifier,attempt)['stage'] in ('published','failed'):
                    try:await asyncio.to_thread(publisher.adb.remove_media,android_path)
                    except Exception:pass
                await asyncio.to_thread(session.close)
            local.unlink(missing_ok=True)
            if locked:device_lock.release()
            self.executing.discard((identifier,attempt))
    async def recover(self,exclude=None):
        # Reconcile a lost claim response first; finish() atomically imports assignments.
        for op in self.journal.pending('claim'):
            try:await self.api.send_operation(op)
            except WorkerError:raise
        for row in self.journal.jobs(unfinished=True):
            if exclude and row['job_id'] in exclude:continue
            remote=await self.api.get(f'/jobs/{row["job_id"]}')
            status=remote['status']
            if status=='published':
                if row['result'] and self.journal.open(row['result']).get('outcome')=='published':
                    try:await self.report(row)
                    except WorkerError:pass
                else:self.journal.stage(row['job_id'],row['attempt'],'published')
                continue
            if status in ('failed','cancelled','paused'):
                if row['irreversible_at'] is None or remote.get('result',{}).get('operator_verified'):
                    self.journal.stage(row['job_id'],row['attempt'],'reconciled')
                continue
            if remote.get('attempts')!=row['attempt']:
                self.journal.stage(row['job_id'],row['attempt'],'reconciled');continue
            if status in ('scheduled','retry_wait','assigned') and row['irreversible_at'] is None:
                self.journal.stage(row['job_id'],row['attempt'],'abandoned');continue
            if row['result']:
                try:await self.report(row)
                except WorkerError:log.warning('recovery_result_pending job=%s',row['job_id'])
                continue
            assignment=Assignment.model_validate(self.journal.open(row['assignment']))
            if status in ('scheduled','retry_wait','assigned') and row['irreversible_at'] is None:
                self.journal.stage(row['job_id'],row['attempt'],'abandoned');continue
            unknown=row['irreversible_at'] is not None or status=='unknown'
            payload=lease_event(assignment,outcome='unknown' if unknown else 'safe_failure',
                error_code='worker_restarted',error_detail='Recovered without repeating UI execution',
                **({} if unknown else {'no_publish_attempted':True}))
            self.journal.record_result(row['job_id'],row['attempt'],payload)
            try:await self.report(self.journal.job(row['job_id'],row['attempt']))
            except WorkerError:log.warning('recovery_requires_operator job=%s',row['job_id'])
    async def flush_results(self):
        active={getattr(task,'shortpost_job',None) for task in self.tasks}
        for row in self.journal.jobs(unfinished=True):
            if row['stage']=='result_pending' and row['result'] and row['job_id'] not in active:
                try:await self.report(row)
                except WorkerError:pass
    async def send_heartbeat(self):
        devices=self.devices.heartbeat_devices()
        for device in devices:
            device['capabilities']['health']=self.device_health.get(device['external_id'],{})
        return await self.api.heartbeat(devices,capabilities={'runtime':{'running':True,
          'live':self.live,'allow_publish':self.live and self.config.allow_publish,
          'paused':self.journal.control('paused')=='1'}})
    async def health_loop(self):
        from .health import device_health
        while not self.stopping:
            enabled=[d for d in self.config.devices if d.enabled]
            results=await asyncio.gather(*(asyncio.to_thread(device_health,self.config,d) for d in enabled),return_exceptions=True)
            self.device_health={d.external_id:(value if isinstance(value,dict) else {}) for d,value in zip(enabled,results)}
            await asyncio.sleep(60)
    async def heartbeat_loop(self):
        while not self.stopping:
            try:await self.send_heartbeat()
            except WorkerError as exc:log.warning('heartbeat_error code=%s status=%s',exc.code,exc.status)
            await asyncio.sleep(30)
    async def analytics_loop(self):
        while not self.stopping:
            try:await self.collect_due_metrics()
            except WorkerError as exc:log.warning('metrics_error code=%s',exc.code)
            await asyncio.sleep(60)
    async def collect_due_metrics(self):
        for task in self.journal.due_metrics(self.api.clock.now()):
            slot=f'metrics:{task["publication_id"]}:{task["hours"]}'
            operation=self.journal.find_operation(slot)
            if operation:
                if operation['state']=='done':
                    self.journal.metric_state(task['publication_id'],task['hours'],True);continue
                if operation['state']=='pending':
                    try:
                        await self.api.send_operation(operation)
                        self.journal.metric_state(task['publication_id'],task['hours'],True)
                    except WorkerError:self.journal.metric_state(task['publication_id'],task['hours'],False)
                    continue
            assignment=Assignment.model_validate(self.journal.open(task['assignment']))
            external=assignment.device.external_id
            if external in self.journal.blocked_devices():continue
            device=self.devices.configured(external)
            lock=self.devices.locks[external]
            if lock.locked():continue
            async with lock:
                session=None
                try:
                    guard=ReadGuard(self.api)
                    adb=await asyncio.to_thread(self.devices.connect,device,guard)
                    session=self.session_factory(device,adb.serial);driver=await asyncio.to_thread(session.open)
                    publisher=self.platform(assignment,device,driver,adb,guard)
                    metrics=await asyncio.to_thread(publisher.collect_metrics,assignment,task['external_id'],task['url'])
                    if not metrics:raise WorkerError('metrics_unavailable')
                    event=uuid.uuid5(uuid.NAMESPACE_URL,f'shortpost:{task["publication_id"]}:{task["hours"]}').hex
                    slot=f'metrics:{task["publication_id"]}:{task["hours"]}'
                    pending=next((p for p in self.journal.pending('snapshot') if p['slot']==slot),None)
                    payload=self.journal.open(pending['body']) if pending else {'publication_id':task['publication_id'],
                        'event_id':event,'measured_at':self.api.clock.iso(),**metrics}
                    await self.api.mutate('snapshot','/analytics/snapshots',payload,slot=slot)
                    self.journal.metric_state(task['publication_id'],task['hours'],True)
                except Exception as exc:
                    code=exc.code if isinstance(exc,WorkerError) else 'metrics_internal_error'
                    log.warning('metrics_error code=%s publication=%s',code,task['publication_id'])
                    self.journal.metric_state(task['publication_id'],task['hours'],False)
                finally:
                    if session:await asyncio.to_thread(session.close)
    async def prepare_apps(self):
        # Recovery takes priority. An unresolved publication must never be disturbed by Play UI.
        if not self.config.auto_install_apps or self.journal.jobs(unfinished=True):return
        if not any(d.enabled for d in self.config.devices):return
        from .provisioning import prepare_devices
        result=await asyncio.to_thread(prepare_devices,self.config,self.journal,
            stop_requested=lambda:self.api.auth_failed.is_set() or self.journal.control('stop')=='1')
        if not all(d['ready'] for d in result['devices']):raise WorkerError('required_apps_not_ready')

    async def run_once(self,job_id):
        if not self.config.allow_publish:raise WorkerError('publishing_not_enabled')
        if self.journal.control('paused')=='1' or self.journal.control('stop')=='1':raise WorkerError('operator_paused')
        enabled=[d for d in self.config.devices if d.enabled]
        if not enabled or not all(d.profiles for d in enabled):raise WorkerError('no_validated_device_profiles')
        for device in enabled:
            for platform,path in device.profiles.items():
                profile=Profile.load(path)
                if profile.platform!=platform or profile.navigation_only or not profile.navigation_verified:raise WorkerError('profile_not_validated')
        await self.api.heartbeat(self.devices.heartbeat_devices())
        await self.recover();await self.flush_results()
        if self.journal.jobs(unfinished=True):raise WorkerError('unfinished_jobs_require_recovery')
        completed=next((r for r in self.journal.jobs() if r['job_id']==str(job_id) and r['stage']=='published'),None)
        if completed:return {'job_id':str(job_id),'status':'published','publication_id':completed['publication_id'],'already_completed':True}
        if not self.devices.resources_ok():raise WorkerError('resources_unavailable')
        response=await self.api.claim(job_id)
        if not response.get('job'):raise WorkerError('requested_job_not_available')
        assignment=Assignment.model_validate(response)
        if str(assignment.job.id)!=str(job_id):raise WorkerError('requested_job_mismatch')
        self.devices.configured(assignment.device.external_id)
        heartbeat=asyncio.create_task(self.heartbeat_loop())
        try:
            await self.process(response)
            row=self.journal.job(str(assignment.job.id),assignment.job.attempts)
            if row['stage']!='published':raise WorkerError('one_shot_not_published')
            return {'job_id':str(assignment.job.id),'status':'published','publication_id':row['publication_id']}
        finally:
            self.stopping=True;heartbeat.cancel()
            try:await heartbeat
            except asyncio.CancelledError:pass

    async def run(self,live=False):
        if live:
            enabled=[d for d in self.config.devices if d.enabled]
            if not enabled or not all(d.profiles for d in enabled):raise WorkerError('no_validated_device_profiles')
            for device in enabled:
                for platform,path in device.profiles.items():
                    profile=Profile.load(path)
                    if profile.navigation_only:raise WorkerError('profile_navigation_only')
                    if profile.platform!=platform or not profile.navigation_verified:
                        raise WorkerError('profile_not_validated')
        self.live=live
        self.journal.control('stop','0')
        await self.send_heartbeat()
        await self.recover()
        heartbeat=asyncio.create_task(self.heartbeat_loop())
        health=asyncio.create_task(self.health_loop())
        analytics=None
        last_recovery=time.monotonic()
        try:
            await self.prepare_apps()
            analytics=asyncio.create_task(self.analytics_loop())
            while self.journal.control('stop')!='1' and not self.api.auth_failed.is_set():
                if time.monotonic()-last_recovery>60:
                    try:await self.recover({getattr(t,'shortpost_job',None) for t in self.tasks})
                    except WorkerError:pass
                    last_recovery=time.monotonic()
                await self.flush_results()
                self.tasks={t for t in self.tasks if not t.done()}
                can_claim=live and self.config.allow_publish and self.api.clock.synced and self.journal.control('paused')!='1' and self.devices.resources_ok()
                # Unknown local devices block admission until operator reconciliation; API has no device-filtered claim.
                if can_claim and len(self.tasks)<self.config.max_parallel:
                    blocked=self.journal.blocked_devices()-{getattr(t,'shortpost_device',None) for t in self.tasks}
                    if blocked:
                        await asyncio.sleep(5);continue
                    try:
                        response=await self.api.claim()
                        if response.get('job'):
                            assignment=Assignment.model_validate(response)
                            task=asyncio.create_task(self.process(response));task.shortpost_job=str(assignment.job.id)
                            task.shortpost_device=assignment.device.external_id;self.tasks.add(task)
                        else:await asyncio.sleep(15)
                    except WorkerError as exc:
                        log.warning('claim_error code=%s status=%s',exc.code,exc.status)
                        await asyncio.sleep(min(60,exc.retry_after or 10))
                else:await asyncio.sleep(2)
        finally:
            # Keep heartbeats/renewals alive until all active publication attempts finish.
            if self.tasks:await asyncio.gather(*self.tasks,return_exceptions=True)
            self.stopping=True
            for task in (heartbeat,analytics,health):
                if task is None:continue
                task.cancel()
                try:await task
                except asyncio.CancelledError:pass
