import json,sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from shortpost_worker.journal import Journal

def test_wal_full_sync_and_encrypted_assignment(journal,assignment):
    journal.save_assignment(assignment)
    assert journal.db.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
    assert journal.db.execute('PRAGMA synchronous').fetchone()[0]==2
    for row in journal.db.execute('SELECT assignment FROM jobs'):
        assert assignment['lease_token'] not in row[0]
    assert journal.open(journal.jobs()[0]['assignment'])==assignment

def test_irreversible_commit_survives_reopen(tmp_path,cipher,assignment):
    path=tmp_path/'durable.db';j=Journal(path,cipher);j.save_assignment(assignment)
    j.mark_irreversible(assignment['job']['id'],1);j.close()
    j=Journal(path,cipher)
    try:
        assert j.jobs()[0]['irreversible_at'] is not None
        with pytest.raises(RuntimeError):j.mark_irreversible(assignment['job']['id'],1)
    finally:j.close()

def test_outbox_exact_payload_and_key_reused(journal):
    a=journal.operation('ack','/jobs/x/ack',{'lease_token':'secret'},'ack:x:1')
    b=journal.operation('ack','/jobs/x/ack',{'lease_token':'secret'},'ack:x:1')
    assert a['id']==b['id'] and journal.open(a['body'])=={'lease_token':'secret'}
    assert 'secret' not in a['body']
    with pytest.raises(ValueError):journal.operation('ack','/jobs/x/ack',{'lease_token':'different'},'ack:x:1')

def test_claim_response_and_assignment_are_atomic(journal,assignment):
    op=journal.operation('claim','/jobs/claim',{})
    journal.finish(op,assignment)
    assert len(journal.jobs())==1 and not journal.pending('claim')
    assert journal.open(journal.operation('claim','/jobs/claim',{},op['slot'])['response'])==assignment

def test_result_cannot_be_rewritten(journal,assignment):
    journal.save_assignment(assignment);identifier=assignment['job']['id']
    journal.record_result(identifier,1,{'outcome':'unknown'})
    with pytest.raises(RuntimeError):journal.record_result(identifier,1,{'outcome':'safe_failure'})

def test_same_submit_is_committed_only_once_under_concurrency(journal,assignment):
    journal.save_assignment(assignment)
    def run():
        try:journal.mark_irreversible(assignment['job']['id'],1);return True
        except RuntimeError:return False
    with ThreadPoolExecutor(2) as pool:assert sum(pool.map(lambda _:run(),range(2)))==1

def test_device_serial_may_change_but_android_identity_cannot(journal):
    journal.mapping('mumu-01',0,'android-id','127.0.0.1:10000')
    journal.mapping('mumu-01',0,'android-id','127.0.0.1:12000')
    assert journal.db.execute('SELECT serial FROM mappings').fetchone()[0]=='127.0.0.1:12000'
    with pytest.raises(RuntimeError):journal.mapping('mumu-01',0,'wrong-id','127.0.0.1:12000')

def test_unknown_blocks_device_until_reconciliation(journal,assignment):
    journal.save_assignment(assignment);identifier=assignment['job']['id']
    journal.stage(identifier,1,'unknown');assert 'mumu-01' in journal.blocked_devices()
    journal.stage(identifier,1,'reconciled');assert not journal.blocked_devices()
