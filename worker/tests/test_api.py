import asyncio,hashlib,json
from datetime import datetime,timezone
import httpx,pytest
from shortpost_worker.api import Api,RateLimiter
from shortpost_worker.errors import WorkerError

class Guard:
    def check(self):pass

@pytest.mark.asyncio
async def test_network_retry_preserves_exact_key_body(config,journal):
    seen=[]
    def handler(request):
        seen.append((request.headers['Idempotency-Key'],request.content))
        if len(seen)==1:raise httpx.ConnectError('network down')
        return httpx.Response(200,json={'status':'processing'})
    api=Api(config,'worker-token',journal,httpx.MockTransport(handler))
    try:
        with pytest.raises(WorkerError):await api.mutate('ack','/jobs/id/ack',{'event_id':'stable-event','lease_token':'secret'},'ack:id:1')
        await api.send_operation(journal.pending()[0])
        assert seen[0]==seen[1]
    finally:await api.close()

@pytest.mark.parametrize('status',[401,403,409,429,503])
@pytest.mark.asyncio
async def test_status_classification(config,journal,status):
    api=Api(config,'worker-token',journal,httpx.MockTransport(lambda _:httpx.Response(status,json={'detail':'not exposed'},headers={'Retry-After':'2'})))
    try:
        with pytest.raises(WorkerError) as error:await api.get('/jobs')
        assert error.value.status==status
        assert api.auth_failed.is_set()==(status in (401,403))
        assert 'not exposed' not in str(error.value)
        if status==429:assert error.value.retry_after==2
    finally:await api.close()

@pytest.mark.asyncio
async def test_stream_download_hash_and_atomic_rename(config,journal,tmp_path):
    data=b'test-video'
    api=Api(config,'worker-token',journal,httpx.MockTransport(lambda _:httpx.Response(200,content=data,
        headers={'Content-Type':'video/mp4','Content-Length':str(len(data))})))
    try:
        path=tmp_path/'video.mp4'
        await api.download('/jobs/id/video','ticket',path,hashlib.sha256(data).hexdigest(),Guard())
        assert path.read_bytes()==data and not path.with_suffix('.part').exists()
    finally:await api.close()

@pytest.mark.parametrize('headers,expected_sha',[
    ({'Content-Type':'video/mp4','Content-Length':'999'},None),
    ({'Content-Type':'text/html','Content-Length':'10'},None),
    ({'Content-Type':'video/mp4','Content-Length':'10'},'a'*64),
])
@pytest.mark.asyncio
async def test_corrupt_download_is_never_kept(config,journal,tmp_path,headers,expected_sha):
    api=Api(config,'worker-token',journal,httpx.MockTransport(lambda _:httpx.Response(200,content=b'test-video',headers=headers)))
    try:
        path=tmp_path/'video.mp4'
        with pytest.raises(WorkerError):await api.download('/jobs/id/video','ticket',path,expected_sha,Guard())
        assert not path.exists() and not path.with_suffix('.part').exists()
    finally:await api.close()

@pytest.mark.asyncio
async def test_media_never_follows_redirect(config,journal,tmp_path):
    seen=[]
    def handler(req):seen.append(req.url);return httpx.Response(302,headers={'Location':'https://evil.example'})
    api=Api(config,'secret-worker-token',journal,httpx.MockTransport(handler))
    try:
        with pytest.raises(WorkerError):await api.download('/jobs/id/video','ticket',tmp_path/'v.mp4',None,Guard())
        assert len(seen)==1
        with pytest.raises(WorkerError):api.media_path('https://evil.example/video','id')
    finally:await api.close()

@pytest.mark.asyncio
async def test_limiter_waits(config,journal):
    limiter=RateLimiter(1,.03)
    started=asyncio.get_running_loop().time();await limiter.acquire();await limiter.acquire()
    assert asyncio.get_running_loop().time()-started>=.02

@pytest.mark.asyncio
async def test_heartbeat_syncs_server_time_and_identity(config,journal):
    api=Api(config,'token',journal,httpx.MockTransport(lambda _:httpx.Response(200,json={
        'worker_id':str(config.worker_id),'server_time':'2026-11-01T12:00:00+00:00','lease_seconds':180})))
    try:
        await api.heartbeat([])
        assert api.clock.synced and abs(api.clock.now()-1793534400)<2
    finally:await api.close()

@pytest.mark.asyncio
async def test_idle_server_response_can_omit_job(config,journal):
    api=Api(config,'token',journal,httpx.MockTransport(lambda _:httpx.Response(200,json={})))
    try:assert not (await api.claim()).get('job')
    finally:await api.close()
