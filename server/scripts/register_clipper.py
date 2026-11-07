"""One-way metadata importer; never changes files/services of the existing content farm."""
import argparse,json,os,uuid
from pathlib import Path
import httpx

NAMESPACE=uuid.UUID('d8475c99-f8c2-4ed7-afbe-764bb368e599')
def key(value):return uuid.uuid5(NAMESPACE,value).hex

def clip_payload(clip,state,source_id,category_id,format_id,topic_id=None):
    stored=state.get('drive_files',{}).get(clip['file'])
    if not stored:return None
    return {'source_id':source_id,'drive_file_id':stored['id'],
        'title':clip.get('title') or clip.get('description','')[:200] or clip['file'],
        'description':clip.get('description',''),'category_id':category_id,'format_id':format_id,'topic_id':topic_id,
        'duration_seconds':clip['end_time']-clip['start_time'],
        'extra':{'farm':'tg-video-clipper','farm_job_id':state['id'],'file':clip['file'],**({'transcript':clip['transcript']} if clip.get('transcript',{}).get('text') else {})}}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--api-url',required=True);parser.add_argument('--job-dir',type=Path,required=True)
    parser.add_argument('--category-id',required=True);parser.add_argument('--format-id',required=True)
    parser.add_argument('--topic-id');parser.add_argument('--verify',action='store_true')
    args=parser.parse_args()
    if not args.api_url.startswith('https://'):raise SystemExit('Use HTTPS')
    token=os.environ.get('FARM_API_TOKEN')
    if not token:raise SystemExit('Set FARM_API_TOKEN')
    state=json.loads((args.job_dir/'state.json').read_text());manifest=json.loads((args.job_dir/'manifest.json').read_text())
    with httpx.Client(base_url=args.api_url,headers={'Authorization':'Bearer '+token},timeout=180) as client:
        def post(path,body,event):
            res=client.post('/api/v1'+path,json=body,headers={'Idempotency-Key':key(event)})
            res.raise_for_status();return res.json()
        external='clipper:'+state['id']
        res=client.get('/api/v1/sources',params={'external_id':external});res.raise_for_status()
        found=res.json()['items']
        source=found[0] if found else post('/sources',{'external_id':external,'title':manifest.get('title') or external,
            'origin':json.dumps(state.get('source',{}),ensure_ascii=False),'category_id':args.category_id,'topic_id':args.topic_id},external)
        registered=[]
        for clip in manifest['clips']:
            payload=clip_payload(clip,state,source['id'],args.category_id,args.format_id,args.topic_id)
            if not payload:continue
            res=client.get('/api/v1/content',params={'drive_file_id':payload['drive_file_id']});res.raise_for_status()
            found=res.json()['items']
            item=found[0] if found else post('/content',payload,'clip:'+payload['drive_file_id'])
            if args.verify:post('/content/'+item['id']+'/verify',{},'verify:'+item['id'])
            registered.append({'id':item['id'],'passport':item['passport']})
        print(json.dumps({'registered':registered},ensure_ascii=False))
if __name__=='__main__':main()
