from datetime import timedelta
import pytest
from fastapi import HTTPException
from sqlalchemy import select,delete,update
from sqlalchemy.exc import IntegrityError
from app.db import now,transaction
from app.models import Account,Clip,ClipPlatformUse,Job,Publication
from app.scheduler.service import create_job

def consume_and_delete_history(seeded,status='published'):
    with transaction() as s:
        job=s.get(Job,seeded['job']);job.status=status;s.flush()
        assert s.get(ClipPlatformUse,(job.clip_id,job.platform))
        s.delete(job)

def test_deleting_history_does_not_make_clip_reusable(seeded):
    consume_and_delete_history(seeded)
    with transaction() as s:
        with pytest.raises(HTTPException) as error:
            create_job(s,s.get(Account,seeded['account']),s.get(Clip,seeded['clip']),now()+timedelta(minutes=5))
        assert error.value.status_code==409
    with pytest.raises(IntegrityError):
        with transaction() as s:
            s.add(Job(platform='tiktok',clip_id=seeded['clip'],account_id=seeded['account'],local_date=now().date(),slot=2,scheduled_at=now()))
            s.flush()

def test_reimport_same_file_new_clip_id_cannot_reset_usage(seeded):
    consume_and_delete_history(seeded)
    with transaction() as s:
        s.delete(s.get(Clip,seeded['clip']));s.flush()
        clip=Clip(passport='REIMPORTED-000001',drive_file_id='new-drive',sha256='a'*64,title='renamed',category_id=seeded['category'],format_id=seeded['format'],duration_seconds=20,status='ready',verified_at=now())
        s.add(clip);s.flush();new_id=clip.id
    with pytest.raises(IntegrityError):
        with transaction() as s:
            s.add(Job(platform='tiktok',clip_id=new_id,account_id=seeded['account'],local_date=now().date(),slot=2,scheduled_at=now()));s.flush()

def test_other_platform_stays_available_after_use(seeded):
    consume_and_delete_history(seeded)
    from app.content.usage import used_on_platform
    with transaction() as s:
        assert not s.scalar(select(Clip.id).where(Clip.id==seeded['clip'],~used_on_platform('tiktok')))
        assert s.scalar(select(Clip.id).where(Clip.id==seeded['clip'],~used_on_platform('instagram')))
        account=Account(platform='instagram',name='Other',username='other');s.add(account);s.flush()
        s.add(Job(platform='instagram',clip_id=seeded['clip'],account_id=account.id,local_date=now().date(),slot=1,scheduled_at=now()));s.flush()

def test_usage_cannot_be_cleared_or_edited(seeded):
    consume_and_delete_history(seeded)
    for statement in (delete(ClipPlatformUse),update(ClipPlatformUse).values(sha256='b'*64)):
        with pytest.raises(IntegrityError):
            with transaction() as s:s.execute(statement)

def test_publication_insert_also_remembers_use(seeded):
    with transaction() as s:
        s.add(Publication(job_id=seeded['job'],clip_id=seeded['clip'],account_id=seeded['account'],external_id='published-id',url='https://example.test/post',published_at=now()));s.flush()
        assert s.get(ClipPlatformUse,(seeded['clip'],'tiktok'))
        s.execute(delete(Publication));s.delete(s.get(Job,seeded['job']))
    with transaction() as s:assert s.get(ClipPlatformUse,(seeded['clip'],'tiktok'))

def test_published_job_cannot_be_reset_for_another_attempt(seeded):
    with transaction() as s:
        job=s.get(Job,seeded['job']);job.status='published';s.flush()
        job.status='failed';s.flush()
    with pytest.raises(IntegrityError):
        with transaction() as s:s.get(Job,seeded['job']).status='scheduled';s.flush()

def test_claim_checks_usage_on_its_own_platform_only(seeded):
    from app.dispatcher.service import claim
    import uuid
    with transaction() as s:
        s.add(ClipPlatformUse(clip_id=seeded['clip'],platform='instagram',sha256='a'*64,job_id=uuid.uuid4()));s.flush()
        assert claim(s,seeded['worker'])['job']['id']==seeded['job']
