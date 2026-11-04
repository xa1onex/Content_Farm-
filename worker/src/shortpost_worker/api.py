import asyncio,json,time,email.utils
from collections import deque
from datetime import datetime,timezone
from urllib.parse import urlsplit
import httpx
from .errors import WorkerError

class Clock:
    def __init__(self):self.anchor=time.monotonic();self.utc=time.time();self.synced=False
    def sync(self,value):
        dt=datetime.fromisoformat(value.replace('Z','+00:00'))
        if not dt.tzinfo:raise WorkerError('server_time_missing_timezone')
        self.anchor=time.monotonic();self.utc=dt.timestamp();self.synced=True
    def now(self):return self.utc+time.monotonic()-self.anchor
    def iso(self):return datetime.fromtimestamp(self.now(),timezone.utc).isoformat()

class RateLimiter:
    def __init__(self,limit=90,period=60):self.limit=limit;self.period=period;self.calls=deque();self.lock=asyncio.Lock()
    async def acquire(self):
        async with self.lock:
            at=time.monotonic()
            while self.calls and self.calls[0]<=at-self.period:self.calls.popleft()
            if len(self.calls)>=self.limit:
                await asyncio.sleep(max(0,self.calls[0]+self.period-at))
                at=time.monotonic()
                while self.calls and self.calls[0]<=at-self.period:self.calls.popleft()
            self.calls.append(time.monotonic())

class Api:
    def __init__(self,config,token,journal,transport=None):
        self.config=config;self.journal=journal;self.clock=Clock();self.limiter=RateLimiter()
        self.client=httpx.AsyncClient(base_url=config.api_url+'/',headers={'Authorization':'Bearer '+token},
            verify=True,follow_redirects=False,timeout=config.request_timeout_seconds,
            transport=transport,limits=httpx.Limits(max_connections=20,max_keepalive_connections=10))
        self.auth_failed=asyncio.Event();self.blocked_until=0.0
    def retry_after(self,response):
        value=response.headers.get('Retry-After','60')
        try:return max(1,min(3600,float(value)))
        except ValueError:
            try:return max(1,min(3600,email.utils.parsedate_to_datetime(value).timestamp()-time.time()))
            except Exception:return 60
    async def _request(self,method,path,*,content=None,key=None):
        await self.limiter.acquire()
        if time.monotonic()<self.blocked_until:raise WorkerError('rate_limited',status=429,retry_after=self.blocked_until-time.monotonic())
        try:
            headers={'Content-Type':'application/json'}
            if key:headers['Idempotency-Key']=key
            response=await self.client.request(method,path.lstrip('/'),content=content,headers=headers)
        except httpx.HTTPError:raise WorkerError('network_error') from None
        if response.status_code in (401,403):
            self.auth_failed.set();raise WorkerError('authorization_failed',status=response.status_code)
        if response.status_code==429:
            delay=self.retry_after(response);self.blocked_until=time.monotonic()+delay
            raise WorkerError('rate_limited',status=429,retry_after=delay)
        if response.status_code>=400:raise WorkerError('api_error',status=response.status_code)
        try:return response.json()
        except ValueError:raise WorkerError('invalid_server_json') from None
    async def get(self,path):return await self._request('GET',path)
    async def send_operation(self,operation):
        if operation['state']=='done':return self.journal.open(operation['response'])
        body=json.dumps(self.journal.open(operation['body']),sort_keys=True,separators=(',',':')).encode()
        result=await self._request('POST',operation['path'],content=body,key=operation['id'])
        try:self.journal.finish(operation,result)
        except ValueError:raise WorkerError('invalid_server_contract') from None
        return result
    async def mutate(self,kind,path,body,slot=None):
        operation=self.journal.operation(kind,path,body,slot)
        try:return await self.send_operation(operation)
        except WorkerError as exc:
            if exc.status in (400,404,409,413,422):self.journal.reject(operation)
            raise
    async def claim(self,job_id=None):
        pending=self.journal.pending('claim')
        if pending:
            if job_id is not None and self.journal.open(pending[0]['body']).get('job_id')!=str(job_id):
                raise WorkerError('pending_claim_requires_recovery')
            return await self.send_operation(pending[0])
        return await self.mutate('claim','/jobs/claim',{'job_id':str(job_id)} if job_id is not None else {})
    async def heartbeat(self,devices,*,capabilities=None):
        pending=self.journal.pending('heartbeat')
        if pending:
            response=await self.send_operation(pending[0])
            # A replayed heartbeat is not a fresh server clock observation.
            self.clock.synced=False
        response=await self.mutate('heartbeat','/workers/heartbeat',
            {'capabilities':dict(capabilities or {})|{'worker_version':'0.1.0'},'devices':devices})
        self.clock.sync(response['server_time'])
        if response.get('worker_id')!=str(self.config.worker_id):raise WorkerError('worker_token_identity_mismatch')
        return response
    async def download(self,path,ticket,destination,expected_sha,guard):
        import hashlib,os
        destination=destination.resolve();temporary=destination.with_suffix('.part')
        await self.limiter.acquire();guard.check()
        try:
            async with self.client.stream('GET',path.lstrip('/'),headers={'X-Media-Ticket':ticket},timeout=httpx.Timeout(30,connect=10)) as response:
                if response.status_code!=200:raise WorkerError('media_download_failed',status=response.status_code)
                if not response.headers.get('Content-Type','').split(';')[0].startswith('video/'):
                    raise WorkerError('unsupported_format')
                try:size=int(response.headers['Content-Length'])
                except (ValueError,KeyError):raise WorkerError('media_size_missing')
                if not 0<size<=2*1024**3:raise WorkerError('media_invalid_size')
                checksum=hashlib.sha256();total=0
                with temporary.open('wb') as out:
                    async for chunk in response.aiter_bytes(256*1024):
                        guard.check();total+=len(chunk)
                        if total>size:raise WorkerError('media_size_mismatch')
                        checksum.update(chunk);out.write(chunk)
                    out.flush();os.fsync(out.fileno())
                if total!=size:raise WorkerError('media_size_mismatch')
                if expected_sha and checksum.hexdigest()!=expected_sha:raise WorkerError('file_hash_mismatch')
                os.replace(temporary,destination)
                return destination
        except httpx.HTTPError:raise WorkerError('drive_unavailable') from None
        finally:temporary.unlink(missing_ok=True)
    def media_path(self,url,job_id):
        actual=urlsplit(url);base=urlsplit(self.config.api_url)
        expected=base.path.rstrip('/')+f'/jobs/{job_id}/video'
        if (actual.scheme,actual.netloc,actual.path)!=(base.scheme,base.netloc,expected) or actual.query or actual.fragment:
            raise WorkerError('untrusted_media_url')
        return f'/jobs/{job_id}/video'
    async def close(self):await self.client.aclose()
