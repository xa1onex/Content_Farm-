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
            s.add(Job(clip_id=seeded['clip'],account_id=seeded['account'],local_date=now().date(),slot=2,scheduled_at=now()))
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
