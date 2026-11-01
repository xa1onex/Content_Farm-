import asyncio,copy,json,time,uuid
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
import httpx,pytest
from shortpost_worker.api import Api
from shortpost_worker.engine import Engine,Guard
from shortpost_worker.errors import LeaseLost,UIError

class Backend:
    def __init__(self,config,assignment):
        self.config=config;self.assignment=assignment;self.status='assigned';self.results=[];self.events=[];self.cache={};self.lose_result_response=False
    def handler(self,request):
        path=request.url.path;body=json.loads(request.content) if request.content else {}
        key=request.headers.get('Idempotency-Key')
        if key in self.cache:return httpx.Response(200,json=self.cache[key])
        self.events.append((request.method,path,body))
        if path.endswith('/heartbeat'):result={'worker_id':str(self.config.worker_id),'server_time':datetime.now(timezone.utc).isoformat()}
        elif path.endswith('/ack'):
            self.status='processing';result={'job_id':self.assignment['job']['id'],'status':'processing','lease_expires_at':(datetime.now(timezone.utc)+timedelta(seconds=180)).isoformat()}
        elif path.endswith('/media-ticket'):result={'url':str(request.url).rsplit('/',1)[0]+'/video','ticket':'private-ticket','expires_at_unix':time.time()+180}
        elif path.endswith('/video'):return httpx.Response(200,content=b'test-video',headers={'Content-Type':'video/mp4','Content-Length':'10'})
        elif path.endswith('/progress'):result={'job_id':self.assignment['job']['id'],'status':'processing','lease_expires_at':(datetime.now(timezone.utc)+timedelta(seconds=180)).isoformat()}
        elif path.endswith('/result'):
            self.results.append(body);self.status='published' if body['outcome']=='published' else ('unknown' if body['outcome']=='unknown' else 'failed')
            result={'job_id':self.assignment['job']['id'],'status':self.status}
            if self.status=='published':result['publication_id']='10000000-0000-4000-8000-000000000001'
            self.cache[key]=result
            if self.lose_result_response:
                self.lose_result_response=False;raise httpx.ReadError('response lost after server commit')
        elif path.endswith('/snapshots'):result={'id':str(uuid.uuid4()),**body}
        elif request.method=='GET':result={**self.assignment['job'],'status':self.status}
        elif path.endswith('/claim'):result=self.assignment
        else:raise AssertionError(path)
        if key:self.cache[key]=result
        return httpx.Response(200,json=result)

class Devices:
    def __init__(self,config):self.config=config;self.locks={'mumu-01':asyncio.Lock()}
    def heartbeat_devices(self):return [{'worker_id':str(self.config.worker_id),'external_id':'mumu-01','name':'Test','capabilities':{'platforms':['tiktok']}}]
    def configured(self,id):return self.config.devices[0]
    def connect(self,*args):return SimpleNamespace(serial='127.0.0.1:16384')
class Session:
    def __init__(self,*args):pass
    def open(self):return object()
    def close(self):pass
class Publisher:
    def __init__(self,counter,*,fail_after=False,fail_before=False):
        self.counter=counter;self.fail_after=fail_after;self.fail_before=fail_before
        self.adb=SimpleNamespace(remove_media=lambda _:None)
    def verify_account(self,a):
        if self.fail_before:raise UIError('account_mismatch')
    def prepare_media(self,path):return '/sdcard/Movies/ShortPost/test.mp4'
    def open_upload(self):pass
    def fill_metadata(self):pass
    def submit(self,before):
        before();self.counter.append('submit')
        if self.fail_after:raise UIError('publication_id_unavailable')
    def verify_publication(self):return {'external_id':'1234567890123456789','url':'https://www.tiktok.com/@example/video/1234567890123456789'}
    def evidence(self,*args):pass
    def collect_metrics(self,*args):return {'views':42,'likes':2}

def setup(config,journal,assignment,monkeypatch,**options):
    backend=Backend(config,assignment);api=Api(config,'private-worker-token',journal,httpx.MockTransport(backend.handler))
    counter=[];publisher=Publisher(counter,**options)
    engine=Engine(config,journal,api,Devices(config),lambda *args:publisher,Session)
    monkeypatch.setattr('shortpost_worker.engine.probe_video',lambda *args:None)
    journal.save_assignment(assignment)
    return backend,api,engine,counter

@pytest.mark.asyncio
async def test_full_cycle_and_delayed_metrics(config,journal,assignment,monkeypatch):
    backend,api,engine,clicks=setup(config,journal,assignment,monkeypatch)
    try:
        await engine.process(assignment)
        assert clicks==['submit'] and backend.status=='published'
        row=journal.jobs()[0];assert row['irreversible_at'] and row['publication_id']
        assert len(journal.due_metrics(float('inf')))==2
        with journal.transaction() as db:db.execute('UPDATE metrics SET due=0')
        await engine.collect_due_metrics()
        assert len([event for event in backend.events if event[1].endswith('/snapshots')])==2
        assert not journal.due_metrics(float('inf'))
    finally:await api.close()

@pytest.mark.asyncio
async def test_error_before_submit_is_safe_failure(config,journal,assignment,monkeypatch):
    backend,api,engine,clicks=setup(config,journal,assignment,monkeypatch,fail_before=True)
    try:
        await engine.process(assignment)
        assert not clicks and backend.results[0]['outcome']=='safe_failure'
        assert backend.results[0]['no_publish_attempted'] is True
        assert journal.control('paused')=='1'
    finally:await api.close()

@pytest.mark.asyncio
async def test_error_after_submit_is_unknown_and_never_resubmitted(config,journal,assignment,monkeypatch):
    backend,api,engine,clicks=setup(config,journal,assignment,monkeypatch,fail_after=True)
    try:
        await engine.process(assignment)
        assert clicks==['submit'] and backend.status=='unknown'
        await engine.process(assignment)
        assert clicks==['submit'] and backend.results[0]['outcome']=='unknown'
        assert 'mumu-01' in journal.blocked_devices()
    finally:await api.close()

@pytest.mark.asyncio
async def test_crash_after_submit_recovers_without_ui(config,journal,assignment,monkeypatch):
    backend,api,engine,clicks=setup(config,journal,assignment,monkeypatch)
    backend.status='processing';journal.mark_irreversible(assignment['job']['id'],1)
    try:
        await engine.recover()
        assert not clicks and backend.results[0]['outcome']=='unknown'
    finally:await api.close()

@pytest.mark.asyncio
async def test_crash_before_submit_recovers_explicit_safe_failure(config,journal,assignment,monkeypatch):
    backend,api,engine,clicks=setup(config,journal,assignment,monkeypatch)
    backend.status='processing';journal.stage(assignment['job']['id'],1,'prepared')
    try:
        await engine.recover()
        assert not clicks and backend.results[0]['outcome']=='safe_failure'
    finally:await api.close()

@pytest.mark.asyncio
async def test_lost_published_response_is_replayed_without_submit(config,journal,assignment,monkeypatch):
    backend,api,engine,clicks=setup(config,journal,assignment,monkeypatch)
    backend.lose_result_response=True
    try:
        await engine.process(assignment)
        await engine.recover()
        assert clicks==['submit'] and len(backend.results)==1
        assert journal.jobs()[0]['publication_id']
        assert len(journal.due_metrics(float('inf')))==2
    finally:await api.close()

@pytest.mark.asyncio
async def test_expired_lease_never_starts_ui(config,journal,assignment,monkeypatch):
    assignment['lease_expires_at']=(datetime.now(timezone.utc)-timedelta(seconds=10)).isoformat()
    backend,api,engine,clicks=setup(config,journal,assignment,monkeypatch)
    try:
        await engine.process(assignment)
        assert not clicks
    finally:await api.close()

def test_guard_checks_absolute_and_server_deadline(config,journal):
    api=Api(config,'token',journal,httpx.MockTransport(lambda _:httpx.Response(200,json={})))
    guard=Guard(api,config,(datetime.now(timezone.utc)+timedelta(seconds=180)).isoformat())
    guard.check();guard.deadline=time.monotonic()+1
    with pytest.raises(LeaseLost):guard.check()
    guard.deadline=time.monotonic()+180;api.auth_failed.set()
    with pytest.raises(LeaseLost):guard.check()
    asyncio.run(api.close())

@pytest.mark.asyncio
async def test_metric_outbox_replays_before_rescraping(config,journal,assignment,monkeypatch):
    backend,api,engine,clicks=setup(config,journal,assignment,monkeypatch)
    try:
        await engine.process(assignment)
        due=journal.due_metrics(float('inf'))[0]
        payload={'publication_id':due['publication_id'],'event_id':uuid.uuid4().hex,'measured_at':api.clock.iso(),'views':17}
        slot=f'metrics:{due["publication_id"]}:{due["hours"]}'
        operation=journal.operation('snapshot','/analytics/snapshots',payload,slot)
        await api.send_operation(operation)
        with journal.transaction() as db:db.execute('UPDATE metrics SET due=0 WHERE hours=?',(due['hours'],))
        engine.publisher_factory=lambda *args:(_ for _ in ()).throw(AssertionError('must not rescrape'))
        await engine.collect_due_metrics()
        assert len([e for e in backend.events if e[1].endswith('/snapshots')])==1
    finally:await api.close()

@pytest.mark.asyncio
async def test_concurrent_duplicate_assignment_has_one_submit(config,journal,assignment,monkeypatch):
    backend,api,engine,clicks=setup(config,journal,assignment,monkeypatch)
    try:
        await asyncio.gather(engine.process(assignment),engine.process(copy.deepcopy(assignment)))
        assert clicks==['submit']
    finally:await api.close()

@pytest.mark.asyncio
async def test_same_emulator_lock_includes_session_cleanup(config,journal,assignment,monkeypatch):
    backend,api,engine,clicks=setup(config,journal,assignment,monkeypatch)
    active={'count':0,'max':0}
    class ObservedPublisher(Publisher):
        def verify_account(self,a):
            active['count']+=1;active['max']=max(active['max'],active['count']);time.sleep(.02)
    class ObservedSession(Session):
        def close(self):time.sleep(.03);active['count']-=1
    engine.publisher_factory=lambda *args:ObservedPublisher(clicks)
    engine.session_factory=ObservedSession
    second=copy.deepcopy(assignment);second['job']['id']=str(uuid.uuid4())
    try:
        await asyncio.gather(engine.process(assignment),engine.process(second))
        assert active['max']==1 and active['count']==0 and len(clicks)==2
    finally:await api.close()
