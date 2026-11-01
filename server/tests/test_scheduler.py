from datetime import date,timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select,delete,func
import pytest
from fastapi import HTTPException
from app.db import transaction,now
from app.models import Account,Clip,Job,Event,AccountCategory,Category
from app.scheduler.service import times,generate,create_job,local_instants

def test_schedule_varies_by_day_and_respects_intervals(seeded):
    with transaction() as s:
        a=s.get(Account,seeded['account']);a.windows=[{'start':'08:00','end':'20:00'}]
        a.min_interval_minutes=60;a.jitter_minutes=20
        x=times(date(2026,11,2),a);y=times(date(2026,11,3),a)
        assert len(x)==5
        assert all(8<=dt.hour<20 for dt in x)
        assert all((b-a).total_seconds()>=3600 for a,b in zip(x,x[1:]))
        assert [v.time() for v in x]!=[v.time() for v in y]

def test_infeasible_schedule_never_violates_minimum(seeded):
    with transaction() as s:
        a=s.get(Account,seeded['account']);a.windows=[{'start':'08:00','end':'09:00'}]
        a.min_interval_minutes=60
        x=times(date(2026,11,2),a)
        assert len(x)<=1

def test_nonexistent_dst_times_are_skipped(seeded):
    with transaction() as s:
        a=s.get(Account,seeded['account']);a.timezone='Europe/Helsinki';a.windows=[{'start':'03:00','end':'04:00'}]
        assert list(local_instants(date(2026,3,29),a))==[]

def test_shortage_warns_without_reusing_clip(seeded):
    with transaction() as s:
        result=generate(s,now().date()+timedelta(days=1),[seeded['account']])
        assert result['created']==0
        assert any('insufficient_content' in x for x in result['warnings'])
        assert s.scalar(select(func.count()).select_from(Job))==1
        assert s.scalar(select(Event).where(Event.kind=='insufficient_content'))

def test_distribution_and_repeated_planner_are_idempotent(seeded):
    with transaction() as s:
        for i in range(6):s.add(Clip(passport=f'ENTERTAINMENT-REACTION-{i+2:06}',drive_file_id=f'file-{i}',
            title=f'Clip {i}',category_id=seeded['category'],format_id=seeded['format'],duration_seconds=15,
            status='ready',verified_at=now()))
    day=now().date()+timedelta(days=1)
    with transaction() as s:first=generate(s,day,[seeded['account']]);assert first['created']==5
    with transaction() as s:
        assert generate(s,day,[seeded['account']])['created']==0
        ids=list(s.scalars(select(Job.clip_id).where(Job.local_date==day)))
        assert len(ids)==len(set(ids))==5

def test_filters_reject_wrong_category(seeded):
    with transaction() as s:
        category=Category(code='EDUCATION',name='Education');s.add(category);s.flush()
        s.add(AccountCategory(account_id=seeded['account'],category_id=category.id));s.flush()
        with pytest.raises(HTTPException):create_job(s,s.get(Account,seeded['account']),s.get(Clip,seeded['clip']),now()+timedelta(hours=1))

def test_daily_limit_and_minimum_interval(seeded):
    with transaction() as s:
        a=s.get(Account,seeded['account']);a.daily_limit=1
        from app.scheduler.service import validate_slot
        with pytest.raises(HTTPException):validate_slot(s,a,now()+timedelta(minutes=30))
        a.daily_limit=5;a.min_interval_minutes=60
        with pytest.raises(HTTPException):validate_slot(s,a,now()+timedelta(minutes=30))
