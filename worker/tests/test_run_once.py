import json,uuid
from pathlib import Path
import httpx,pytest
from shortpost_worker.api import Api
from shortpost_worker.engine import Engine
from shortpost_worker.errors import WorkerError
from test_engine import Backend,Devices,Publisher,Session
from test_platforms import profile

def setup_once(config,journal,assignment,monkeypatch,**options):
    local=config.state_dir/'test-profile.json';local.write_text(profile().model_dump_json(),encoding='utf-8')
    config.devices[0].profiles={'tiktok':local}
    backend=Backend(config,assignment);api=Api(config,'token',journal,httpx.MockTransport(backend.handler))
    devices=Devices(config);devices.resources_ok=lambda:True
    clicks=[];publisher=Publisher(clicks,**options)
    engine=Engine(config,journal,api,devices,lambda *args:publisher,Session)
    monkeypatch.setattr('shortpost_worker.engine.probe_video',lambda *args:None)
    return backend,api,engine,clicks

@pytest.mark.asyncio
async def test_run_once_claims_only_requested_job_and_does_not_repeat_completed_job(config,journal,assignment,monkeypatch):
    backend,api,engine,clicks=setup_once(config,journal,assignment,monkeypatch)
    target=assignment['job']['id']
    try:
        result=await engine.run_once(target)
        assert result['status']=='published' and clicks==['submit']
        claims=[event for event in backend.events if event[1].endswith('/claim')]
        assert len(claims)==1 and claims[0][2]=={'job_id':target}
        repeated=await engine.run_once(target)
        assert repeated['already_completed'] and clicks==['submit']
        assert len([event for event in backend.events if event[1].endswith('/claim')])==1
    finally:await api.close()

@pytest.mark.asyncio
async def test_unknown_one_shot_blocks_even_after_operator_resumes(config,journal,assignment,monkeypatch):
    backend,api,engine,clicks=setup_once(config,journal,assignment,monkeypatch,fail_after=True)
    try:
        with pytest.raises(WorkerError,match='one_shot_not_published'):await engine.run_once(assignment['job']['id'])
        assert backend.status=='unknown' and clicks==['submit']
        journal.control('paused','0')
        with pytest.raises(WorkerError,match='unfinished_jobs_require_recovery'):await engine.run_once(assignment['job']['id'])
        assert clicks==['submit'] and len([e for e in backend.events if e[1].endswith('/claim')])==1
    finally:await api.close()

@pytest.mark.asyncio
async def test_different_job_is_not_acknowledged_or_executed(config,journal,assignment,monkeypatch):
    backend,api,engine,clicks=setup_once(config,journal,assignment,monkeypatch)
    try:
        with pytest.raises(WorkerError,match='requested_job_mismatch'):await engine.run_once(str(uuid.uuid4()))
        assert not clicks and not any(e[1].endswith('/ack') for e in backend.events)
    finally:await api.close()

@pytest.mark.asyncio
async def test_local_pause_and_resource_limits_prevent_claim(config,journal,assignment,monkeypatch):
    backend,api,engine,clicks=setup_once(config,journal,assignment,monkeypatch)
    try:
        journal.control('paused','1')
        with pytest.raises(WorkerError,match='operator_paused'):await engine.run_once(assignment['job']['id'])
        assert not backend.events
        journal.control('paused','0');engine.devices.resources_ok=lambda:False
        with pytest.raises(WorkerError,match='resources_unavailable'):await engine.run_once(assignment['job']['id'])
        assert not clicks and not any(e[1].endswith('/claim') for e in backend.events)
    finally:await api.close()

def test_one_shot_cli_requires_explicit_live_and_exact_job():
    from shortpost_worker.cli import parser
    command=['run-once','--job-id',str(uuid.uuid4()),'--external-id','mumu-01','--profile','profile.json']
    with pytest.raises(SystemExit):parser().parse_args(command)
    args=parser().parse_args(command+['--live'])
    assert args.live and args.profile==Path('profile.json')
