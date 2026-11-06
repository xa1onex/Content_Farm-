"""Pinned-model GPU speech worker. No publication, emulator or cloud credentials."""
import argparse,hashlib,json,math,os,re,subprocess,time,wave
from pathlib import Path

def validate_result(data,duration):
    if not isinstance(data,dict) or not isinstance(data.get('segments'),list) or len(data['segments'])>2000:raise ValueError('Invalid transcription')
    if not re.fullmatch(r'[a-z]{2,3}',data.get('language','')):raise ValueError('Invalid language')
    if not isinstance(data.get('text'),str) or len(data['text'])>150000:raise ValueError('Invalid text')
    for segment in data['segments']:
        if not isinstance(segment.get('text'),str):raise ValueError('Missing segment text')
        for node in [segment,*segment.get('words',[])]:
            a,b=float(node['start']),float(node['end'])
            if not all(math.isfinite(x) for x in (a,b)) or not 0<=a<=b<=duration+2:raise ValueError('Invalid timestamps')
    return data

def run(root,token):
    import portalocker,psutil
    if not re.fullmatch(r'[a-f0-9]{12}-[0-9]{4}',token):raise ValueError('Invalid task token')
    root=Path(root).resolve();request=json.loads((root/(token+'.request.json')).read_text())
    audio=root/(token+'.wav');output=root/(token+'.result.json')
    if request.get('model')!='base' or request.get('operation')!='transcribe':raise ValueError('Unsupported compute operation')
    if audio.stat().st_size>25*1024**2:raise ValueError('Audio too large')
    expected=hashlib.sha256(audio.read_bytes()).hexdigest()
    if expected!=request['sha256']:raise ValueError('Audio checksum mismatch')
    with wave.open(str(audio)) as stream:
        duration=stream.getnframes()/stream.getframerate()
        if stream.getframerate()!=16000 or stream.getnchannels()!=1 or stream.getsampwidth()!=2 or not 0<duration<=605:raise ValueError('Unsupported audio')
    language=request.get('language')
    if language is not None and not re.fullmatch(r'[a-z]{2,3}',language):raise ValueError('Invalid language')
    with portalocker.Lock(str(root/'gpu.lock'),timeout=0):
        if psutil.virtual_memory().available<2*1024**3:raise RuntimeError('pc_memory_busy')
        import torch,whisper
        torch.set_num_threads(2)
        if not torch.cuda.is_available():raise RuntimeError('cuda_unavailable')
        if torch.cuda.mem_get_info()[0]<1536*1024**2:raise RuntimeError('gpu_memory_busy')
        if output.exists():
            saved=json.loads(output.read_text())
            if saved.get('_compute',{}).get('input_sha256')==expected:return {'cached':True,**saved['_compute']}
        model=whisper.load_model('base',device='cuda',download_root=str(root/'models'))
        started=time.monotonic()
        result=model.transcribe(str(audio),word_timestamps=True,fp16=True,verbose=None,
            condition_on_previous_text=False,language=language)
        elapsed=time.monotonic()-started
        result=json.loads(json.dumps(result,default=lambda x:x.item(),allow_nan=False));validate_result(result,duration)
        result['_compute']={'backend':'cuda','model':'base','input_sha256':expected,'audio_seconds':duration,'inference_seconds':round(elapsed,3),'gpu':torch.cuda.get_device_name(0)}
        temp=output.with_suffix('.tmp');temp.write_text(json.dumps(result,ensure_ascii=False,allow_nan=False),encoding='utf-8');temp.replace(output)
        return result['_compute']

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--task',required=True);args=parser.parse_args()
    try:print(json.dumps({'ok':True,**run(args.root,args.task)}))
    except Exception as exc:
        print(json.dumps({'ok':False,'error':str(exc)[:160],'type':type(exc).__name__}));raise SystemExit(1)
if __name__=='__main__':main()
