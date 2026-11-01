from datetime import timedelta
import uuid
from app.db import transaction,now
from app.models import Job,Publication,Snapshot
from conftest import auth

def test_analytics_uses_latest_snapshot_instead_of_summing_counters(client,seeded):
    with transaction() as s:
        s.get(Job,seeded['job']).status='published'
        pub=Publication(job_id=seeded['job'],clip_id=seeded['clip'],account_id=seeded['account'],
            external_id='mock-analytics',published_at=now()-timedelta(days=2))
        s.add(pub);s.flush()
        s.add_all([Snapshot(publication_id=pub.id,event_id=uuid.uuid4().hex,measured_at=now()-timedelta(days=1),views=100),
            Snapshot(publication_id=pub.id,event_id=uuid.uuid4().hex,measured_at=now()-timedelta(minutes=1),views=150)])
    for group in ('account','platform','category','format','source','clip','hour','day','week','month'):
        response=client.get('/api/v1/analytics/report',params={'group_by':group},headers=auth())
        assert response.status_code==200
        assert response.json()['items'][0]['views']==150
        assert response.json()['items'][0]['likes'] is None
