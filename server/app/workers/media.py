import json,time,secrets
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from app.config import settings
from app.security import digest

def issue(job,worker_id):
    expires=int(min(time.time()+300,job.lease_until.timestamp()))
    payload={'job':str(job.id),'worker':str(worker_id),'lease_hash':job.lease_hash,'expires':expires,
        'nonce':secrets.token_hex(12)}
    ticket=Fernet(settings().encryption_key.get_secret_value().encode()).encrypt(json.dumps(payload).encode()).decode()
    return {'url':f'{settings().public_base_url}/api/v1/jobs/{job.id}/video',
        'ticket':ticket,'expires_at_unix':expires,'required_headers':['Authorization','X-Media-Ticket']}

def verify(ticket,job,worker_id):
    try:
        data=json.loads(Fernet(settings().encryption_key.get_secret_value().encode()).decrypt(ticket.encode()))
        if data['job']!=str(job.id) or data['worker']!=str(worker_id) or data['lease_hash']!=job.lease_hash or data['expires']<=time.time():
            raise ValueError()
    except (InvalidToken,ValueError,KeyError,TypeError):raise HTTPException(403,'Invalid or expired media ticket')
