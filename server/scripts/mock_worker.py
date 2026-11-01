"""HTTP Mock Worker. Refuses production and performs no social platform operations."""
import argparse,hashlib,os,time,uuid
from datetime import datetime,timezone
from urllib.parse import urlsplit
import httpx

def execute(base_url,token,*,download=True):
    url=urlsplit(base_url)
    if url.hostname not in ('127.0.0.1','localhost'):
        raise RuntimeError('Mock Worker only connects to a loopback test API')
    with httpx.Client(base_url=base_url,headers={'Authorization':'Bearer '+token},timeout=45,trust_env=False) as client:
        health=client.get('/api/v1/health');health.raise_for_status()
        if health.json().get('environment')!='test':raise RuntimeError('Mock Worker refuses a production server')
        def post(path,body=None):
            response=client.post('/api/v1'+path,json=body or {},headers={'Idempotency-Key':uuid.uuid4().hex})
            response.raise_for_status();return response.json()
        post('/workers/heartbeat',{'capabilities':{'mock':True},'devices':[]})
        assignment=post('/jobs/claim')
        if assignment['job'] is None:return {'status':'idle'}
        job=assignment['job']['id'];lease=assignment['lease_token']
        def event(**data):return {'event_id':uuid.uuid4().hex,'lease_token':lease,**data}
        ticket=post(f'/jobs/{job}/media-ticket',event())
        if download:
            payload=client.get(f'/api/v1/jobs/{job}/video',headers={'X-Media-Ticket':ticket['ticket']})
            payload.raise_for_status()
            expected=assignment['content'].get('sha256')
            if expected and hashlib.sha256(payload.content).hexdigest()!=expected:raise RuntimeError('SHA-256 mismatch')
        ack=post(f'/jobs/{job}/ack',event())
        if ack['status']!='processing':raise RuntimeError('No processing acknowledgement; abort')
        post(f'/jobs/{job}/progress',event(phase='mock_simulation',detail='No real publication'))
        result=post(f'/jobs/{job}/result',event(outcome='published',external_id='mock-'+uuid.uuid4().hex,
            published_at=datetime.now(timezone.utc).isoformat()))
        post('/analytics/snapshots',{'publication_id':result['publication_id'],'event_id':uuid.uuid4().hex,
            'measured_at':datetime.now(timezone.utc).isoformat(),'views':123,'likes':4})
        return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--base-url',required=True)
    parser.add_argument('--skip-download',action='store_true',help='Use when Drive is deliberately unavailable in a test')
    args=parser.parse_args();token=os.environ.get('MOCK_WORKER_TOKEN')
    if not token:raise SystemExit('Set MOCK_WORKER_TOKEN')
    print(execute(args.base_url,token,download=not args.skip_download))
if __name__=='__main__':main()
