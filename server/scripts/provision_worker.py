"""Server-side onboarding only. Admin credentials never need to reach Windows."""
import argparse,json,os,re,uuid
from pathlib import Path
import httpx

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--name',required=True)
    parser.add_argument('--max-parallel',type=int,default=1)
    parser.add_argument('--bootstrap-file',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise SystemExit('Output exists; refusing to overwrite credentials')
    pending=args.output.with_suffix('.request.json')
    expected={'name':args.name,'max_parallel':args.max_parallel,'capabilities':{}}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    if pending.exists():
        operation=json.loads(pending.read_text())
        if operation['body']!=expected:raise SystemExit('Pending onboarding request has different parameters')
    else:
        operation={'key':uuid.uuid4().hex,'body':expected}
        fd=os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as out:
            json.dump(operation,out);out.flush();os.fsync(out.fileno())
    credentials=json.loads(args.bootstrap_file.read_text())
    with httpx.Client(base_url='http://127.0.0.1:8108/api/v1',timeout=20,trust_env=False) as client:
        response=client.post('/workers',json=operation['body'],
            headers={'Authorization':'Bearer '+credentials['admin_token'],'Idempotency-Key':operation['key']})
        response.raise_for_status();payload=response.json()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as out:
        json.dump({'worker_id':payload['worker']['id'],'worker_token':payload['token']},out);out.flush();os.fsync(out.fileno())
    pending.unlink()
    print('Private onboarding file saved:',args.output)
    print('Worker ID:',payload['worker']['id'])
if __name__=='__main__':main()
