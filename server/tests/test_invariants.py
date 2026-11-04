from datetime import timedelta
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from app.db import transaction,now
from app.models import Job,Clip,Account,Publication

def test_database_forbids_duplicate_job_even_cancelled(seeded):
    with transaction() as s:s.get(Job,seeded['job']).status='cancelled'
    with pytest.raises(IntegrityError):
        with transaction() as s:
            s.add(Job(platform='tiktok',clip_id=seeded['clip'],account_id=seeded['account'],local_date=now().date(),slot=2,scheduled_at=now()))
            s.flush()

def test_database_forbids_duplicate_hash(seeded):
    with pytest.raises(IntegrityError):
        with transaction() as s:
            s.add(Clip(passport='OTHER-REACTION-000002',drive_file_id='different-file',sha256='a'*64,
                title='duplicate',category_id=seeded['category'],format_id=seeded['format'],duration_seconds=1))
            s.flush()

def test_passport_immutable_at_database_layer(seeded):
    with pytest.raises(IntegrityError):
        with transaction() as s:s.get(Clip,seeded['clip']).passport='CHANGED-ID-000001';s.flush()

def test_category_change_preserves_passport(seeded):
    from app.models import Category
    with transaction() as s:
        category=Category(code='EDUCATION',name='Education');s.add(category);s.flush()
        clip=s.get(Clip,seeded['clip']);original=clip.passport;clip.category_id=category.id;s.flush()
        assert clip.passport==original

def test_hash_and_drive_identity_immutable(seeded):
    for field,value in [('sha256','b'*64),('drive_file_id','new-drive-file')]:
        with pytest.raises(IntegrityError):
            with transaction() as s:setattr(s.get(Clip,seeded['clip']),field,value);s.flush()

def test_device_worker_foreign_key_enforced(seeded):
    with pytest.raises(IntegrityError):
        with transaction() as s:s.get(Account,seeded['account']).worker_id=seeded['worker2'];s.flush()

def test_job_identity_immutable(seeded):
    import uuid
    with pytest.raises(IntegrityError):
        with transaction() as s:s.get(Job,seeded['job']).clip_id=uuid.uuid4();s.flush()


def test_clip_is_reserved_across_accounts_on_same_platform_but_allowed_on_another(seeded):
    from sqlalchemy import select
    with transaction() as s:
        same=Account(platform='tiktok',name='Second',username='second',status='active',warmup_enabled=False)
        other=Account(platform='instagram',name='Other',username='other',status='active',warmup_enabled=False)
        s.add_all([same,other]);s.flush();same_id=same.id;other_id=other.id
    with pytest.raises(IntegrityError):
        with transaction() as s:
            s.add(Job(platform='tiktok',clip_id=seeded['clip'],account_id=same_id,local_date=now().date(),slot=1,scheduled_at=now()))
            s.flush()
    with transaction() as s:
        s.add(Job(platform='instagram',clip_id=seeded['clip'],account_id=other_id,local_date=now().date(),slot=1,scheduled_at=now()))
        s.flush()
        assert len(list(s.scalars(select(Job).where(Job.clip_id==seeded['clip']))))==2


def test_job_cannot_lie_about_account_platform(seeded):
    with pytest.raises(IntegrityError):
        with transaction() as s:
            s.get(Job,seeded['job']).platform='youtube';s.flush()
