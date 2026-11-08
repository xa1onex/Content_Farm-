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
        follower=new_follower(s,j,seeded)
        s.add(Setting(key='campaign_job:'+str(follower.id),value={'campaign_id':'test','previous_job_id':str(j.id),'delay_seconds':120}));s.flush()
        assert not release_ready(s,follower)
        pub.published_at=now()-timedelta(seconds=121);s.flush();assert release_ready(s,follower)

def test_quarantined_uncertain_job_can_never_be_retried(client,seeded):
    from conftest import auth
    with transaction() as s:
        j=s.get(Job,seeded['job']);j.status='cancelled';j.result={'outcome':'unknown','no_republish':True}
    r=client.post('/api/v1/jobs/'+str(seeded['job'])+'/action',headers=auth(),json={'action':'retry','scheduled_at':(now()+timedelta(hours=1)).isoformat(),'reason':'Try repeating quarantined publication'})
    assert r.status_code==409 and 'quarantined' in r.json()['detail']


def new_follower(s,previous,seeded):
    from app.models import Clip
    c=Clip(passport='CF-'+uuid.uuid4().hex.upper(),drive_file_id=uuid.uuid4().hex,title='Next',category_id=seeded['category'],format_id=seeded['format'],duration_seconds=30)
    s.add(c);s.flush()
    job=Job(platform=previous.platform,clip_id=c.id,account_id=previous.account_id,local_date=previous.local_date,slot=previous.slot+1,scheduled_at=previous.scheduled_at+timedelta(minutes=2));s.add(job);s.flush();return job


def test_proven_no_send_failure_does_not_block_following_accounts(seeded):
    with transaction() as s:
        first=s.get(Job,seeded['job']);first.status='failed';first.result={'outcome':'safe_failure','no_publish_attempted':True}
        child=new_follower(s,first,seeded)
        s.add(Setting(key='release_campaign:test',value={'enabled':True}))
        s.add(Setting(key='campaign_job:'+str(first.id),value={'campaign_id':'test','previous_job_id':None,'delay_seconds':60}))
        s.add(Setting(key='campaign_job:'+str(child.id),value={'campaign_id':'test','previous_job_id':str(first.id),'delay_seconds':120}));s.flush()
        assert release_ready(s,child)
        first.result={'outcome':'unknown','no_republish':True,'no_publish_attempted':True};assert not release_ready(s,child)
        first.result={'outcome':'safe_failure'};assert not release_ready(s,child)
        first.status='unknown';first.result={'no_publish_attempted':True};assert not release_ready(s,child)


def test_skipping_safe_failure_keeps_delay_from_last_confirmed_publication(seeded):
    with transaction() as s:
        first=s.get(Job,seeded['job']);first.status='published'
        pub=Publication(job_id=first.id,clip_id=first.clip_id,account_id=first.account_id,external_id='confirmed',published_at=now());s.add(pub)
        failed=new_follower(s,first,seeded);failed.status='failed';failed.result={'no_publish_attempted':True}
        child=new_follower(s,failed,seeded)
        s.add(Setting(key='release_campaign:test',value={'enabled':True}))
        for j,previous in [(failed,first),(child,failed)]:s.add(Setting(key='campaign_job:'+str(j.id),value={'campaign_id':'test','previous_job_id':str(previous.id),'delay_seconds':120}))
        s.flush();assert not release_ready(s,child)
        pub.published_at=now()-timedelta(seconds=121);s.flush();assert release_ready(s,child)
