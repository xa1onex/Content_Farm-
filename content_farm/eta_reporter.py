"""Separate telemetry service: never starts jobs, edits states, or sends Telegram messages."""
import json,logging,time
from pathlib import Path
from config import JOB_DIR,BASE_DIR
from autopost import Client
from eta import observe,estimate,measured_ratio,ACTIVE
log=logging.getLogger(__name__)

def source_metadata(state,cache):
    from urllib.parse import urlsplit
    source=state.get('source',{})
    if source.get('kind')!='youtube':return {}
    value=source.get('value','')
    if urlsplit(value).hostname not in ('youtube.com','www.youtube.com','m.youtube.com','youtu.be'):return {}
    old=cache.get(value)
    if old and time.time()-old['at']<(86400 if old['data'].get('duration') else 600):return old['data']
    from yt_dlp import YoutubeDL
    import os,shutil
    class Quiet:
        def debug(self,*args):pass
        def warning(self,*args):pass
        def error(self,*args):pass
    options={'quiet':True,'no_warnings':True,'skip_download':True,'noplaylist':True,'socket_timeout':8,'retries':0,'extractor_retries':0,'logger':Quiet(),'js_runtimes':{'node':{'path':shutil.which('node') or '/usr/bin/node'}}}
    if os.getenv('YOUTUBE_COOKIE_FILE'):options['cookiefile']=str(BASE_DIR/os.environ['YOUTUBE_COOKIE_FILE'])
    try:
        with YoutubeDL(options) as dl:info=dl.extract_info(value,download=False)
        data={'duration':info.get('duration')} if info else {}
    except Exception:data={}
    cache[value]={'at':time.time(),'data':data};return data

def collect(cache):
    rows=[]
    for p in JOB_DIR.glob('*/state.json'):
        try:
            state=json.loads(p.read_text())
            if state.get('status') not in ACTIVE:continue
            source=next((v for v in p.parent.glob('source.*') if v.stem=='source' and v.suffix in ('.mp4','.mkv','.webm','.mov')),None)
            metadata={} if source else source_metadata(state,cache)
            rows.append(observe(p.parent,metadata))
        except (OSError,ValueError,KeyError):log.warning('estimate_observation_failed')
    ratio=measured_ratio(rows)
    if ratio:cache['_transcription_ratio']=ratio
    return estimate(rows,previous_ratio=cache.get('_transcription_ratio'))

def main():
    logging.basicConfig(level=logging.INFO)
    cache_file=BASE_DIR/'.eta-metadata-cache.json'
    try:cache=json.loads(cache_file.read_text())
    except (OSError,ValueError):cache={}
    while True:
        client=None
        try:
            data=collect(cache)
            from drive_helper_json import save_private_json
            save_private_json(cache_file,cache)
            client=Client()
            r=client.session.post(client.base+'/farm/progress',json=data,timeout=(5,15));r.raise_for_status()
            log.info('Farm timing refreshed: %s active jobs',len(data['items']))
        except Exception:log.warning('farm_timing_report_failed')
        finally:
            if client:client.close()
        time.sleep(30)
if __name__=='__main__':main()
