"""Read-only observations and approximate end-to-end timing for the serial farm."""
import json,math,statistics,subprocess,time
from datetime import datetime,timezone
from pathlib import Path

ACTIVE={'receiving','queued','processing','retry_wait','interrupted'}
def iso(stamp):return datetime.fromtimestamp(stamp,timezone.utc).isoformat()
def number(value,default=0):
    try:
        v=float(value);return v if math.isfinite(v) and v>=0 else default
    except (TypeError,ValueError):return default

def observe(folder,metadata=None):
    folder=Path(folder);state=json.loads((folder/'state.json').read_text())
    row={'id':state['id'],'status':state['status'],'created':state.get('created',''),'retry_at':state.get('retry_at',0),
         'duration':number((metadata or {}).get('duration')),'requested':state.get('clip_count') or 8}
    source=next((p for p in folder.glob('source.*') if p.stem=='source' and p.suffix in ('.mp4','.mkv','.webm','.mov')),None)
    row['source_ready']=bool(source)
    if source:
        try:
            out=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(source)],capture_output=True,text=True,timeout=8,check=True)
            row['duration']=number(json.loads(out.stdout)['format']['duration'])
        except (subprocess.SubprocessError,OSError,ValueError,KeyError):pass
    parts=sorted(folder.glob('transcript_part_*.json'));row['parts']=len(parts);row['part_times']=[p.stat().st_mtime for p in parts]
    row['transcribed']=bool((folder/'transcript.json').exists())
    selection=folder/'selection.json';manifest=folder/'manifest.json'
    row['selected']=None
    if selection.exists():
        try:row['selected']=len(json.loads(selection.read_text()))
        except (OSError,ValueError):pass
    renders=[folder/receipt.get('file','missing') for receipt in state.get('clip_outputs',{}).values() if (folder/receipt.get('file','missing')).is_file()]
    row['rendered']=len(renders);row['render_times']=sorted(p.stat().st_mtime for p in renders)
    if manifest.exists():
        try:
            data=json.loads(manifest.read_text());clips=data.get('clips',[]);row['selected']=len(clips);row['rendered']=len(clips);row['duration']=number(data.get('duration'),row['duration'])
        except (OSError,ValueError):pass
    row['manifest']=manifest.exists();row['archived']=bool(state.get('archived'));row['registered']=bool(state.get('registered'));row['delivered']=bool(state.get('delivered'))
    row['delivery_count']=len(state.get('delivered_files',[]));row['source_kind']=state.get('source',{}).get('kind')
    return row

def measured_ratio(rows):
    values=[]
    for row in rows:
        stamps=row.get('part_times',[])
        values.extend((b-a)/600 for a,b in zip(stamps,stamps[1:]) if 10<=b-a<=3600)
    return statistics.median(values[-20:]) if values else None

def remaining(row,ratio=None,render_rate=None):
    duration=number(row.get('duration'));known=bool(duration)
    duration=duration or 3600
    measured=ratio is not None
    low_rate,high_rate=(max(.05,ratio*.8),ratio*1.3) if measured else (.35,1.1)
    low=high=0
    source_ready=row.get('source_ready');transcribed=row.get('transcribed') or row.get('manifest')
    selected=row.get('selected');count=selected if selected is not None else row.get('requested',8)
    if not source_ready and not row.get('manifest'):low+=90;high+=600
    if not transcribed:
        left=max(0,duration-min(duration,row.get('parts',0)*600))
        # Within a chunk progress is unknown; do not count the whole recording again.
        low+=left*low_rate;high+=left*high_rate
    if selected is None and not row.get('manifest'):low+=60;high+=300
    unfinished=max(0,count-row.get('rendered',0))
    low+=unfinished*(max(10,render_rate*.75) if render_rate else 45)
    high+=unfinished*(render_rate*1.5 if render_rate else 300)
    if not row.get('archived'):low+=90;high+=600
    if not row.get('registered'):low+=15;high+=120
    if not row.get('delivered'):low+=max(1,count-row.get('delivery_count',0))*10;high+=max(1,count-row.get('delivery_count',0))*45
    if not known and not row.get('manifest'):
        # Unknown input duration needs a deliberately broad provisional range.
        low*=.5;high*=3
    if row.get('manifest'):stage='upload' if not row.get('archived') else 'register' if not row.get('registered') else 'deliver'
    elif selected is not None:stage='render'
    elif transcribed:stage='analyze'
    elif source_ready:stage='transcribe'
    else:stage='download'
    return max(30,round(low)),max(60,round(high)),stage,'measured' if measured and known else 'rough'

def estimate(rows,at=None,previous_ratio=None):
    at=time.time() if at is None else at
    live=[r for r in rows if r['status'] in ACTIVE]
    # Processing always goes first; pending jobs follow the accepted FIFO order.
    live.sort(key=lambda r:(r['status']!='processing',max(number(r.get('retry_at')),at) if r['status']=='retry_wait' else at,r.get('created',''),r['id']))
    ratio=measured_ratio(rows) or previous_ratio
    render_samples=[]
    for row in rows:
        times=row.get('render_times',[])
        render_samples.extend(b-a for a,b in zip(times,times[1:]) if 10<=b-a<=1800)
    render_rate=statistics.median(render_samples) if render_samples else None
    wait_low=wait_high=0;items=[]
    for pos,row in enumerate(live):
        lo,hi,stage,confidence=remaining(row,ratio,render_rate)
        retry_delay=max(0,number(row.get('retry_at'))-at) if row['status']=='retry_wait' else 0
        wait_low=max(wait_low,retry_delay);wait_high=max(wait_high,retry_delay)
        item={'job_id':row['id'],'queue_position':pos+1,'stage':stage if row['status']=='processing' else 'queued',
              'wait_min_seconds':round(wait_low),'wait_max_seconds':round(wait_high),
              'remaining_min_seconds':round(wait_low+lo),'remaining_max_seconds':round(wait_high+hi),
              'finish_earliest':iso(at+wait_low+lo),'finish_latest':iso(at+wait_high+hi),'confidence':confidence,
              'source_duration_seconds':round(number(row.get('duration'))),'parts_done':row.get('parts',0),
              'parts_total':math.ceil(number(row.get('duration'))/600) if row.get('duration') else None}
        items.append(item);wait_low+=lo;wait_high+=hi
    return {'at':iso(at),'items':items}

def human_range(lo,hi):
    def duration(seconds):
        minutes=max(1,math.ceil(seconds/300)*5)
        return (str(minutes)+' мин') if minutes<60 else str(minutes//60)+' ч'+(' '+str(minutes%60)+' мин' if minutes%60 else '')
    return duration(lo)+'–'+duration(hi)
