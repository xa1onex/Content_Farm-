import uuid
from datetime import timedelta
from app.db import transaction,now
from app.models import Setting,Job,Publication,Account
from app.campaigns import release_ready
from app.scheduler.service import daily_quota

def test_waves_require_enabled_campaign_and_real_previous_publication(seeded):
    with transaction() as s:
        j=s.get(Job,seeded['job']);s.add(Setting(key='release_campaign:test',value={'enabled':False}))
        row=Setting(key='campaign_job:'+str(j.id),value={'campaign_id':'test','previous_job_id':None,'delay_seconds':60});s.add(row);s.flush()
        assert not release_ready(s,j)
        s.get(Setting,'release_campaign:test').value={'enabled':True};assert release_ready(s,j)
        row.value={'campaign_id':'test','previous_job_id':str(uuid.uuid4()),'delay_seconds':60};assert not release_ready(s,j)
        row.value={'campaign_id':'test','previous_job_id':str(j.id),'delay_seconds':60};assert not release_ready(s,j)
        # A scheduled timestamp or uncertain result cannot unlock a follower.
        j.status='unknown';assert not release_ready(s,j)

def test_daily_campaign_override_is_limited_to_specific_dates(seeded):
    with transaction() as s:
        a=s.get(Account,seeded['account']);a.warmup_enabled=True;a.operational_started_at=now();a.daily_limit=3
        day=now().date();a.extra={'daily_overrides':{day.isoformat():2}}
        assert daily_quota(a,day)==2
        assert daily_quota(a,day+timedelta(days=1))==1

def test_delay_starts_at_confirmed_publication_and_is_never_compressed(seeded):
    with transaction() as s:
        j=s.get(Job,seeded['job']);j.status='published'
        pub=Publication(job_id=j.id,clip_id=j.clip_id,account_id=j.account_id,external_id='confirmed',published_at=now());s.add(pub)
        s.add(Setting(key='release_campaign:test',value={'enabled':True}))
        s.add(Setting(key='campaign_job:'+str(j.id),value={'campaign_id':'test','previous_job_id':str(j.id),'delay_seconds':120}));s.flush()
        assert not release_ready(s,j)
        pub.published_at=now()-timedelta(seconds=121);s.flush();assert release_ready(s,j)
