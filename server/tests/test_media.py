import uuid
from datetime import timedelta
from app.db import transaction,now
from app.models import Job
from conftest import auth

def test_authenticated_streaming_media_and_temporary_authorization(client,seeded,monkeypatch):
    payload=b'test-video-content'
    monkeypatch.setattr('app.integrations.drive.download',lambda _:({'mimeType':'video/mp4','size':str(len(payload))},iter([payload])))
    claim=client.post('/api/v1/jobs/claim',headers=auth('worker')).json()
    job=claim['job']['id'];lease={'event_id':uuid.uuid4().hex,'lease_token':claim['lease_token']}
    ticket=client.post(f'/api/v1/jobs/{job}/media-ticket',json=lease,headers=auth('worker')).json()['ticket']
    res=client.get(f'/api/v1/jobs/{job}/video',headers=auth('worker')|{'X-Media-Ticket':ticket})
    assert res.status_code==200 and res.content==payload
    assert client.get(f'/api/v1/jobs/{job}/video',headers=auth('worker2')|{'X-Media-Ticket':ticket}).status_code==403
    assert client.get(f'/api/v1/jobs/{job}/video',headers=auth('worker')|{'X-Media-Ticket':'invalid'}).status_code==403
    with transaction() as s:s.get(Job,seeded['job']).lease_until=now()-timedelta(seconds=1)
    assert client.get(f'/api/v1/jobs/{job}/video',headers=auth('worker')|{'X-Media-Ticket':ticket}).status_code==403

def test_unconfigured_drive_reports_real_error(client,seeded,monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings(),'drive_credentials_file','/nonexistent-shortpost-credential.json')
    response=client.get(f'/api/v1/content/{seeded["clip"]}/drive',headers=auth())
    assert response.status_code==503 and response.json()['detail']=='drive_not_configured'
