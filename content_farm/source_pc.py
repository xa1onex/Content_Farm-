"""Fallback acquisition via the user's configured Windows compute connection."""
import base64,hashlib,json,re,time,socket
from pathlib import Path
from config import BASE_DIR
from compute_bridge import PC

def download_public_youtube(url,job_dir):
    job_dir=Path(job_dir);config=json.loads((BASE_DIR/'compute-connection.json').read_text());root=config['remote_root']
    with PC(config) as pc:
        code='import runpy,json;ns=runpy.run_path('+repr(root+'/source_download.py')+');print(json.dumps(ns["download"]('+repr(root)+','+repr(job_dir.name)+','+repr(url)+'),ensure_ascii=True))'
        info=pc.execute(code,timeout=1800)
        if not 0<info['size']<=2*1024**3 or not re.fullmatch(r'[a-f0-9]{64}',info['sha256']):raise ValueError('Invalid source receipt')
        path=job_dir/'source.pc.incoming';digest=hashlib.sha256()
        try:
            code="import os,sys,msvcrt;from pathlib import Path;msvcrt.setmode(sys.stdout.fileno(),os.O_BINARY);f=Path("+repr(info['path'])+").open('rb');import shutil;shutil.copyfileobj(f,sys.stdout.buffer,65536);sys.stdout.buffer.flush();f.close()"
            encoded=base64.b64encode(code.encode()).decode()
            cmd=config['python']+' -c "import base64;exec(base64.b64decode(\''+encoded+'\'))"'
            channel=pc.client.get_transport().open_session(timeout=10);channel.exec_command(cmd);channel.shutdown_write();channel.settimeout(5)
            received=0;deadline=time.monotonic()+1800
            try:
                with path.open('wb') as f:
                    while True:
                        if time.monotonic()>deadline:raise TimeoutError('PC source transfer timed out')
                        try:data=channel.recv(256*1024)
                        except socket.timeout:continue
                        if not data:break
                        received+=len(data)
                        if received>info['size']:raise ValueError('PC source exceeds receipt')
                        f.write(data);digest.update(data)
                if channel.recv_exit_status()!=0 or received!=info['size']:raise ValueError('Incomplete PC source transfer')
            finally:channel.close()
            if digest.hexdigest()!=info['sha256']:raise ValueError('PC source checksum mismatch')
            final=job_dir/'source.mp4';path.replace(final)
            return final,{'title':info['title'],'url':url,'downloaded_on':'pc'}
        except BaseException:path.unlink(missing_ok=True);raise
