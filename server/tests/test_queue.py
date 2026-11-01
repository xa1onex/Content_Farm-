import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
import pytest
from fastapi import HTTPException
from sqlalchemy import select,func
from app.db import transaction,now,engine
from app.models import Job,Worker,Publication,Device,Account,Clip,Attempt
from app.dispatcher.service import claim,acknowledge,progress,result,reap
from app.schemas import LeaseEvent,Progress,Result
from app.security import digest

def event(token,**kwargs):return dict(event_id=uuid.uuid4().hex,lease_token=token,**kwargs)

def test_concurrent_same_worker_gets_one_job(seeded):
    barrier=Barrier(2)
    def run():
        barrier.wait()
        with transaction() as s:return claim(s,seeded['worker'])
    with ThreadPoolExecutor(2) as pool:responses=list(pool.map(lambda _:run(),range(2)))
    assert sum(bool(x['job']) for x in responses)==1
    with transaction() as s:assert s.scalar(select(func.count()).select_from(Attempt))==1

def test_two_workers_cannot_claim_same_job(seeded):
    barrier=Barrier(2)
    def run(worker):
        barrier.wait()
        with transaction() as s:return claim(s,worker)
    with ThreadPoolExecutor(2) as pool:responses=list(pool.map(run,[seeded['worker'],seeded['worker2']]))
    assert sum(bool(x['job']) for x in responses)==1
    assert responses[1]['job'] is None

def test_expired_unacknowledged_lease_is_safe_to_retry(seeded):
    with transaction() as s:raw=claim(s,seeded['worker'])['lease_token']
    with transaction() as s:s.get(Job,seeded['job']).lease_until=now()-timedelta(seconds=1)
    with transaction() as s:
        assert reap(s)==1
        job=s.get(Job,seeded['job']);assert job.status=='retry_wait';assert job.lease_hash is None
        job.scheduled_at=now()-timedelta(seconds=1)
    with transaction() as s:
        new=claim(s,seeded['worker']);assert new['lease_token']!=raw
        with pytest.raises(HTTPException):acknowledge(s,seeded['job'],seeded['worker'],LeaseEvent(**event(raw)))

def test_expired_processing_becomes_unknown_and_is_not_reissued(seeded):
    with transaction() as s:
        raw=claim(s,seeded['worker'])['lease_token']
        acknowledge(s,seeded['job'],seeded['worker'],LeaseEvent(**event(raw)))
    engine.dispose()  # New connection pool simulates process restart; durable state remains.
    with transaction() as s:s.get(Job,seeded['job']).lease_until=now()-timedelta(seconds=1)
    with transaction() as s:
        reap(s);assert s.get(Job,seeded['job']).status=='unknown'
        assert claim(s,seeded['worker'])['job'] is None

def test_full_mock_worker_cycle_and_event_idempotency(seeded):
    with transaction() as s:raw=claim(s,seeded['worker'])['lease_token']
    ack=LeaseEvent(**event(raw))
    with transaction() as s:assert acknowledge(s,seeded['job'],seeded['worker'],ack)['status']=='processing'
    with transaction() as s:assert acknowledge(s,seeded['job'],seeded['worker'],ack)['status']=='processing'
    with transaction() as s:assert progress(s,seeded['job'],seeded['worker'],Progress(**event(raw,phase='uploading')))['status']=='processing'
    data=Result(**event(raw,outcome='published',external_id='mock-publication',published_at=now()))
    with transaction() as s:first=result(s,seeded['job'],seeded['worker'],data)
    with transaction() as s:
        second=result(s,seeded['job'],seeded['worker'],data)
        assert first==second
        assert s.scalar(select(func.count()).select_from(Publication))==1

def test_late_success_reconciles_same_unknown_attempt(seeded):
    with transaction() as s:
        raw=claim(s,seeded['worker'])['lease_token']
        acknowledge(s,seeded['job'],seeded['worker'],LeaseEvent(**event(raw)))
        s.get(Job,seeded['job']).lease_until=now()-timedelta(seconds=1)
    with transaction() as s:reap(s)
    with transaction() as s:
        response=result(s,seeded['job'],seeded['worker'],Result(**event(raw,outcome='published',external_id='late-post',published_at=now())))
        assert response['status']=='published'

def test_safe_failure_is_explicit_and_pauses_captcha_account(seeded):
    with transaction() as s:
        raw=claim(s,seeded['worker'])['lease_token'];acknowledge(s,seeded['job'],seeded['worker'],LeaseEvent(**event(raw)))
        response=result(s,seeded['job'],seeded['worker'],Result(**event(raw,outcome='safe_failure',error_code='captcha',no_publish_attempted=True)))
        assert response['status']=='failed';assert s.get(Account,seeded['account']).status=='attention'

def test_wrong_worker_lease_is_rejected(seeded):
    with transaction() as s:raw=claim(s,seeded['worker'])['lease_token']
    with transaction() as s:
        with pytest.raises(HTTPException):acknowledge(s,seeded['job'],seeded['worker2'],LeaseEvent(**event(raw)))

def test_no_ack_no_result(seeded):
    with transaction() as s:raw=claim(s,seeded['worker'])['lease_token']
    with transaction() as s:
        with pytest.raises(HTTPException):result(s,seeded['job'],seeded['worker'],Result(**event(raw,outcome='published',external_id='bad',published_at=now())))

def test_offline_worker_cannot_claim(seeded):
    with transaction() as s:s.get(Worker,seeded['worker']).heartbeat_at=now()-timedelta(hours=1)
    with transaction() as s:
        with pytest.raises(HTTPException):claim(s,seeded['worker'])

def test_worker_max_parallel_enforced(seeded):
    with transaction() as s:
        s.get(Worker,seeded['worker']).max_parallel=1
        assert claim(s,seeded['worker'])['job']
        assert claim(s,seeded['worker'])['job'] is None

def test_new_server_process_recovers_persisted_processing_as_unknown(seeded):
    import subprocess,sys
    with transaction() as s:
        token=claim(s,seeded['worker'])['lease_token']
        acknowledge(s,seeded['job'],seeded['worker'],LeaseEvent(**event(token)))
        s.get(Job,seeded['job']).lease_until=now()-timedelta(seconds=1)
    code='from app.db import transaction; from app.dispatcher.service import reap;\nwith transaction() as s: assert reap(s)==1'
    process=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=20)
    assert process.returncode==0,process.stderr
    with transaction() as s:assert s.get(Job,seeded['job']).status=='unknown'
