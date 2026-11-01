import uuid,hashlib
from datetime import datetime,timezone,timedelta
from pathlib import Path
import pytest
from cryptography.fernet import Fernet
from shortpost_worker.config import Config,DeviceConfig
from shortpost_worker.journal import Journal
from shortpost_worker.api import Api

class Cipher:
    def __init__(self,key=None):self.fernet=Fernet(key or Fernet.generate_key())
    def encrypt(self,value):return self.fernet.encrypt(value.encode()).decode()
    def decrypt(self,value):return self.fernet.decrypt(value.encode()).decode()

@pytest.fixture
def cipher():return Cipher()
@pytest.fixture
def config(tmp_path):
    return Config(worker_id=uuid.uuid4(),state_dir=tmp_path,max_parallel=2,allow_publish=True,
        devices=[DeviceConfig(external_id='mumu-01',instance_index=0,android_id='android-identity-01',profiles={'tiktok':Path('profile.json')})])
@pytest.fixture
def journal(tmp_path,cipher):
    value=Journal(tmp_path/'journal.sqlite3',cipher)
    yield value
    value.close()
@pytest.fixture
def assignment():
    job_id=str(uuid.uuid4());account_id=str(uuid.uuid4());clip_id=str(uuid.uuid4())
    return {'job':{'id':job_id,'account_id':account_id,'clip_id':clip_id,'status':'assigned','attempts':1,'parameters':{}},
        'account':{'id':account_id,'platform':'tiktok','username':'example','timezone':'UTC'},
        'device':{'id':str(uuid.uuid4()),'external_id':'mumu-01'},
        'content':{'id':clip_id,'passport':'ENTERTAINMENT-REACTION-000001','title':'Test','description':'Test caption',
            'duration_seconds':20,'sha256':hashlib.sha256(b'test-video').hexdigest()},
        'lease_token':'lease-secret-must-be-encrypted-123456789',
        'lease_expires_at':(datetime.now(timezone.utc)+timedelta(seconds=180)).isoformat()}
