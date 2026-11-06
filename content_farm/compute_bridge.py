"""Checkpoint prefetch over the existing pinned Windows SSH tunnel.

Only future speech chunks are prefetched; the server owns the current chunk,
merging, editing, final clip IDs, uploads and publication. A lost PC leaves the
existing CPU path unchanged. Audio and results are checksum checked.
"""
import base64,hashlib,json,logging,math,os,re,shutil,subprocess,time
from datetime import datetime,timezone
from pathlib import Path
import paramiko
from config import JOB_DIR,BASE_DIR
from drive_helper_json import save_private_json
log=logging.getLogger(__name__)

def stamp():return datetime.now(timezone.utc).isoformat()
def validate_result(data,duration):
    if not isinstance(data,dict) or not isinstance(data.get('segments'),list) or len(data['segments'])>2000:raise ValueError('Invalid transcription')
    if not re.fullmatch(r'[a-z]{2,3}',data.get('language','')) or not isinstance(data.get('text'),str) or len(data['text'])>150000:raise ValueError('Invalid transcription metadata')
    for segment in data['segments']:
        if not isinstance(segment.get('text'),str):raise ValueError('Missing text')
        for node in [segment,*segment.get('words',[])]:
            a,b=float(node['start']),float(node['end'])
            if not all(math.isfinite(v) for v in (a,b)) or not 0<=a<=b<=duration+2:raise ValueError('Invalid word timestamps')
    return data

class PC:
    def __init__(self,config):self.config=config;self.client=None
    def __enter__(self):
        c=self.config;self.client=paramiko.SSHClient();self.client.load_host_keys(str(c['known_hosts']))
        self.client.set_missing_host_key_policy(paramiko.RejectPolicy())
        self.client.connect('127.0.0.1',port=c['port'],username=c['username'],key_filename=str(c['key']),
            allow_agent=False,look_for_keys=False,timeout=8,banner_timeout=10,auth_timeout=10)
        return self
    def __exit__(self,*args):
        if self.client:self.client.close()
    def execute(self,code,timeout=900,content=None):
        encoded=base64.b64encode(code.encode()).decode()
        # All command arguments are fixed paths and base64. No source URLs or captions execute.
        cmd=self.config['python']+' -c "import base64;exec(base64.b64decode(\''+encoded+'\'))"'
        channel=self.client.get_transport().open_session(timeout=10);channel.exec_command(cmd)
        if content is not None:channel.sendall(content)
        channel.shutdown_write()
        deadline=time.monotonic()+timeout;out=bytearray();err=bytearray()
        try:
            while True:
                while channel.recv_ready():out.extend(channel.recv(65536))
                while channel.recv_stderr_ready():err.extend(channel.recv_stderr(65536))
                if len(out)>12*1024**2 or len(err)>1024**2:raise RuntimeError('Compute output limit')
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():break
                if time.monotonic()>deadline:raise TimeoutError('Compute timed out')
                time.sleep(.1)
            if channel.recv_exit_status()!=0:raise RuntimeError('GPU task failed: '+out.decode(errors='replace')[-400:])
            return json.loads(out.decode('utf-8').strip().splitlines()[-1])
        finally:channel.close()
    def write_remote(self,name,content):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',name):raise ValueError('Unsafe filename')
        root=self.config['remote_root']
        code="import sys,base64,json;from pathlib import Path;root=Path("+repr(root)+");root.mkdir(parents=True,exist_ok=True);data=base64.b64decode(sys.stdin.buffer.read(40*1024**2));assert len(data)<=25*1024**2;target=root/"+repr(name)+";temp=target.with_suffix('.transfer');temp.write_bytes(data);temp.replace(target);print(json.dumps({'bytes':len(data)}))"
        return self.execute(code,timeout=120,content=base64.b64encode(content))
    def transcribe(self,audio,folder,index,language):
        token=folder.name+f'-{index:04d}';root=self.config['remote_root'];target=root+'/'+token
        sha=hashlib.sha256(audio.read_bytes()).hexdigest()
        self.write_remote(token+'.wav',audio.read_bytes())
        self.write_remote(token+'.request.json',json.dumps({'operation':'transcribe','model':'base','sha256':sha,'language':language}).encode())
        code='import runpy,sys;sys.argv='+repr(['gpu_compute.py','--root',root,'--task',token])+';runpy.run_path('+repr(root+'/gpu_compute.py')+",run_name='__main__')"
        status=self.execute(code)
        if not status.get('ok'):raise RuntimeError('GPU rejected task')
        code="import json;from pathlib import Path;p=Path("+repr(target+'.result.json')+");assert p.stat().st_size<=8*1024**2;print(json.dumps(json.loads(p.read_text(encoding='utf-8')),ensure_ascii=True))"
        data=self.execute(code,timeout=30)
        with __import__('wave').open(str(audio)) as f:duration=f.getnframes()/f.getframerate()
        validate_result(data,duration)
        if data.get('_compute',{}).get('input_sha256')!=sha:raise ValueError('Result checksum binding mismatch')
        code="import json;from pathlib import Path;root=Path("+repr(root)+");[(root/name).unlink(missing_ok=True) for name in "+repr([token+s for s in ('.wav','.request.json','.result.json')])+"];print(json.dumps({'cleaned':True}))"
        self.execute(code,timeout=15)
        return data

def tasks(folder):
    state=json.loads((folder/'state.json').read_text())
    if state.get('status')!='processing' or (folder/'transcript.json').exists() or (folder/'manifest.json').exists():return None
    source=next((p for p in folder.glob('source.*') if p.stem=='source' and p.suffix in ('.mp4','.mkv','.webm','.mov')),None)
    if not source:return None
    result=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(source)],capture_output=True,text=True,timeout=15,check=True)
    duration=float(json.loads(result.stdout)['format']['duration'])
    total=math.ceil(duration/600)
    if not math.isfinite(duration) or not 15<=duration<=21600:raise ValueError('Invalid duration')
    missing=[i for i in range(total) if not (folder/f'transcript_part_{i:04d}.json').exists()]
    if not missing:return None
    # A missing checkpoint may already be running on the CPU. Usually prefetch
    # one slot ahead. If an earlier GPU checkpoint has exactly the same PCM hash
    # as the CPU's current WAV, its cursor is known and the first missing slot is safe.
    index=missing[1] if len(missing)>1 else None
    cpu_audio=folder/'speech_chunk.wav'
    receipt=folder/'compute-progress.json'
    if cpu_audio.exists() and receipt.exists():
        try:
            marker=json.loads(receipt.read_text());done=int(marker['part'])
            cached=json.loads((folder/f'transcript_part_{done:04d}.json').read_text())
            if done<missing[0] and cached.get('_compute',{}).get('input_sha256')==hashlib.sha256(cpu_audio.read_bytes()).hexdigest():index=missing[0]
        except (OSError,ValueError,KeyError,TypeError):pass
    if index is None:return None
    language=None
    first=folder/'transcript_part_0000.json'
    if first.exists():language=json.loads(first.read_text()).get('language')
    if not language:return None  # Preserve the server's established source language.
    return source,duration,index,language

def prefetch(pc,folder,work):
    job=tasks(folder)
    if not job:return False
    source,duration,index,language=job;begin=index*600;offset=max(0,begin-2)
    length=min(duration,begin+602)-offset;audio=work/(folder.name+f'-{index:04d}.wav')
    started=time.monotonic()
    try:
        subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-y','-threads','1','-protocol_whitelist','file,pipe',
            '-ss',str(offset),'-i',str(source),'-t',str(length),'-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(audio)],capture_output=True,timeout=60,check=True)
        data=pc.transcribe(audio,folder,index,language)
        # The server may have caught up while the PC was working. Keep its existing output.
        target=folder/f'transcript_part_{index:04d}.json'
        state=json.loads((folder/'state.json').read_text())
        if state.get('status')!='processing' or (folder/'transcript.json').exists():return False
        temp=work/(target.name+'.'+folder.name+'.ready');save_private_json(temp,data)
        accepted=True
        try:os.link(temp,target)
        except FileExistsError:accepted=False
        finally:temp.unlink(missing_ok=True)
        if not accepted:return False
        record={'at':stamp(),'part':index,'backend':'cuda','audio_seconds':length,'transfer_and_inference_seconds':round(time.monotonic()-started,2),'inference_seconds':data['_compute']['inference_seconds']}
        save_private_json(folder/'compute-progress.json',record)
        log.info('GPU checkpoint ready job=%s part=%s elapsed=%.1f',folder.name,index,time.monotonic()-started)
        return True
    finally:audio.unlink(missing_ok=True)

def main():
    logging.basicConfig(level=logging.INFO);config=json.loads((BASE_DIR/'compute-connection.json').read_text());work=BASE_DIR/'compute-transfer';work.mkdir(exist_ok=True)
    while True:
        try:
            selected=None
            for folder in JOB_DIR.iterdir():
                if re.fullmatch(r'[a-f0-9]{12}',folder.name) and (folder/'state.json').exists() and tasks(folder):selected=folder;break
            if selected:
                with PC(config) as pc:prefetch(pc,selected,work)
            else:time.sleep(10)
        except Exception as exc:
            # The original server CPU path continues; never stop or restart the farm.
            log.warning('pc_compute_unavailable type=%s',type(exc).__name__);time.sleep(30)
if __name__=='__main__':main()
