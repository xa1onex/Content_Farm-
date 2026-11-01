"""Optional real FastAPI/PostgreSQL contract tests. No production or real UI."""
import os
if not os.environ.get('SHORTPOST_INTEGRATION_DATABASE_URL'):
    import pytest
    pytest.skip('Set SHORTPOST_INTEGRATION_DATABASE_URL to the separate shortpost_test database',allow_module_level=True)
import sys,uuid,hashlib
from pathlib import Path
from datetime import timedelta
import pytest,httpx
os.environ['DATABASE_URL']=os.environ['SHORTPOST_INTEGRATION_DATABASE_URL']
os.environ['ENVIRONMENT']='test'
os.environ['PUBLIC_BASE_URL']='https://shortpost.test'
os.environ['ENCRYPTION_KEY']='YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE='
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'server'))
from app.main import app
from app.db import engine as db_engine,transaction,now,Base
from app.models import Category,Format,Worker,Device,Account,Clip,Job,ApiToken,Publication
from app.security import digest
from sqlalchemy import text,select,func
from shortpost_worker.api import Api
from shortpost_worker.engine import Engine
from test_engine import Devices,Session,Publisher

@pytest.fixture
def real_server(config,journal,monkeypatch):
    with db_engine.begin() as conn:
        if conn.scalar(text('SELECT current_database()'))!='shortpost_test':
            raise RuntimeError('Integration test refuses any database other than shortpost_test')
        conn.execute(text('TRUNCATE '+','.join('"'+t+'"' for t in Base.metadata.tables)+' RESTART IDENTITY CASCADE'))
    with transaction() as s:
        cat=Category(code='ENTERTAINMENT',name='Test');fmt=Format(code='REACTION',name='Test')
        worker=Worker(id=config.worker_id,name='Test Worker',heartbeat_at=now())
        s.add_all([cat,fmt,worker]);s.flush()
        device=Device(worker_id=worker.id,external_id='mumu-01',name='Test');s.add(device);s.flush()
        account=Account(platform='tiktok',name='Test',username='example',status='active',worker_id=worker.id,device_id=device.id,
            timezone='UTC',windows=[{'start':'00:00','end':'23:59'}],min_interval_minutes=1)
        s.add(account);s.flush()
        clip=Clip(passport='ENTERTAINMENT-REACTION-000001',drive_file_id='integration-only-file',title='Test',description='Test caption',
            category_id=cat.id,format_id=fmt.id,duration_seconds=20,sha256=hashlib.sha256(b'test-video').hexdigest(),status='ready',verified_at=now())
        s.add(clip);s.flush()
        job=Job(clip_id=clip.id,account_id=account.id,local_date=now().date(),slot=1,scheduled_at=now()-timedelta(seconds=1))
        s.add_all([job,ApiToken(token_hash=digest('integration-worker-token'),role='worker',worker_id=worker.id,label='Test')]);s.flush()
        job_id=job.id
    config.api_url='https://shortpost.test/api/v1'
    api=Api(config,'integration-worker-token',journal,httpx.ASGITransport(app=app))
    monkeypatch.setattr('app.integrations.drive.download',lambda _:({'mimeType':'video/mp4','size':'10'},iter([b'test-video'])))
    monkeypatch.setattr('shortpost_worker.engine.probe_video',lambda *args:None)
    return api,job_id

@pytest.mark.asyncio
async def test_real_backend_worker_full_cycle(config,journal,real_server):
    api,job_id=real_server;clicks=[];publisher=Publisher(clicks)
    worker=Engine(config,journal,api,Devices(config),lambda *args:publisher,Session)
    try:
        await api.heartbeat(worker.devices.heartbeat_devices())
        assignment=await api.claim()
        assert assignment['job']['id']==str(job_id)
        await worker.process(assignment)
        with transaction() as s:
            assert s.get(Job,job_id).status=='published'
            assert s.scalar(select(func.count()).select_from(Publication))==1
        assert clicks==['submit'] and journal.jobs()[0]['publication_id']
        idle=await api.claim()
        assert not idle.get('job')  # Actual server serializes the idle response as {}.
    finally:await api.close()

@pytest.mark.asyncio
async def test_real_backend_unknown_is_never_republished(config,journal,real_server):
    api,job_id=real_server;clicks=[];publisher=Publisher(clicks,fail_after=True)
    worker=Engine(config,journal,api,Devices(config),lambda *args:publisher,Session)
    try:
        assignment=await api.claim();await worker.process(assignment);await worker.recover()
        with transaction() as s:
            assert s.get(Job,job_id).status=='unknown'
            assert s.scalar(select(func.count()).select_from(Publication))==0
        assert clicks==['submit']
    finally:await api.close()
