"""Public YouTube download on Windows. No browser cookies or account extraction."""
import hashlib,json,math,re,shutil
from pathlib import Path
from urllib.parse import urlsplit

def download(root,job_id,url):
    if not re.fullmatch(r'[a-f0-9]{12}',job_id):raise ValueError('Invalid job')
    u=urlsplit(url)
    if u.scheme!='https' or u.hostname not in ('www.youtube.com','youtube.com','m.youtube.com','youtu.be') or u.username or u.password:raise ValueError('Unsupported source')
    from yt_dlp import YoutubeDL
    target=Path(root)/'downloads'/job_id;target.mkdir(parents=True,exist_ok=True)
    class Quiet:
        def debug(self,*a):pass
        def warning(self,*a):pass
        def error(self,*a):pass
    def progress(data):
        if data.get('downloaded_bytes',0)>2*1024**3:raise ValueError('Source exceeds 2 GB')
    options={'format':'bestvideo[height<=720]+bestaudio/best[height<=720]','outtmpl':str(target/'source.%(ext)s'),
        'merge_output_format':'mp4','quiet':True,'no_warnings':True,'logger':Quiet(),'noplaylist':True,
        'max_filesize':2*1024**3,'socket_timeout':20,'retries':2,'fragment_retries':2,'concurrent_fragment_downloads':1,
        'progress_hooks':[progress],'js_runtimes':{'node':{'path':shutil.which('node') or r'C:\Program Files\nodejs\node.exe'}}}
    with YoutubeDL(options) as dl:
        info=dl.extract_info(url,download=False)
        duration=float(info.get('duration',0))
        if info.get('is_live') or not math.isfinite(duration) or not 15<=duration<=21600:raise ValueError('Unsupported source duration')
        info=dl.extract_info(url,download=True)
    paths=[p for p in target.glob('source.*') if p.stem=='source' and p.suffix in ('.mp4','.mkv','.webm','.mov')]
    if len(paths)!=1 or not 0<paths[0].stat().st_size<=2*1024**3:raise ValueError('Invalid source file')
    path=paths[0];digest=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024**2),b''):digest.update(chunk)
    return {'path':str(path),'size':path.stat().st_size,'sha256':digest.hexdigest(),'title':info.get('title','Видео'),'url':url,'duration':duration}
