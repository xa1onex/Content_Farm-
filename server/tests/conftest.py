import os,uuid
from datetime import timedelta
import pytest
from sqlalchemy import text
from fastapi.testclient import TestClient
from app.db import engine,transaction,now
from app.models import *
from app.security import digest
from app.main import app

@pytest.fixture(autouse=True)
def clean_database():
    with engine.begin() as connection:
        name=connection.scalar(text('SELECT current_database()'))
        if name!='shortpost_test':raise RuntimeError('Tests only run against shortpost_test')
        names=','.join('"'+t+'"' for t in Base.metadata.tables)
        connection.execute(text('TRUNCATE '+names+' RESTART IDENTITY CASCADE'))
        connection.execute(text('ALTER SEQUENCE clip_passport_seq RESTART WITH 1'))
    yield

@pytest.fixture
def client():
    with transaction() as s:
        for role in ('admin','farm'):
            s.add(ApiToken(token_hash=digest(role+'-test-token'),role=role,label=role))
    with TestClient(app,raise_server_exceptions=False) as client:yield client

def auth(role='admin',key=None):
    return {'Authorization':'Bearer '+role+'-test-token','Idempotency-Key':key or uuid.uuid4().hex}

@pytest.fixture
def seeded():
    with transaction() as s:
        cat=Category(code='ENTERTAINMENT',name='Entertainment');fmt=Format(code='REACTION',name='Reaction')
        topic=Topic(code='GAMING',name='Gaming')
        w=Worker(name='Worker A',heartbeat_at=now());w2=Worker(name='Worker B',heartbeat_at=now())
        s.add_all([cat,fmt,topic,w,w2]);s.flush()
        device=Device(worker_id=w.id,external_id='mumu-1',name='MuMu 1');s.add(device);s.flush()
        account=Account(platform='tiktok',name='Test',username='test',worker_id=w.id,device_id=device.id,
            timezone='UTC',status='active',daily_limit=5,warmup_enabled=False,windows=[{'start':'00:00','end':'23:59'}],min_interval_minutes=1)
        s.add(account);s.flush()
        clip=Clip(passport='ENTERTAINMENT-REACTION-000001',drive_file_id='drive-file-one',
            title='Test clip',category_id=cat.id,topic_id=topic.id,format_id=fmt.id,duration_seconds=20,
            status='ready',verified_at=now(),sha256='a'*64)
        s.add(clip);s.flush()
        job=Job(platform='tiktok',clip_id=clip.id,account_id=account.id,local_date=now().date(),slot=1,
            scheduled_at=now()-timedelta(seconds=2));s.add(job)
        s.add_all([ApiToken(token_hash=digest('worker-test-token'),role='worker',label='w',worker_id=w.id),
            ApiToken(token_hash=digest('worker2-test-token'),role='worker',label='w2',worker_id=w2.id)])
        s.flush()
        return dict(category=cat.id,format=fmt.id,topic=topic.id,worker=w.id,worker2=w2.id,
            device=device.id,account=account.id,clip=clip.id,job=job.id)
