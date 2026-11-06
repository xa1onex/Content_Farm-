import asyncio,json,os,re,shlex,shutil,subprocess,time,math
from pathlib import Path
import psutil
from .errors import WorkerError

def command(argv,timeout=20):
    try:
        result=subprocess.run([str(a) for a in argv],capture_output=True,text=True,encoding='utf-8',errors='replace',
            timeout=timeout,check=False,creationflags=0x08000000 if os.name=='nt' else 0)
    except (OSError,subprocess.TimeoutExpired):raise WorkerError('device_command_failed') from None
    if result.returncode:raise WorkerError('device_command_failed')
    return result.stdout.strip()

def discover_mumu():
    roots=[]
    if os.name=='nt':
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE,winreg.HKEY_CURRENT_USER):
            for uninstall in (r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'):
                try:
                    with winreg.OpenKey(hive,uninstall) as parent:
                        for i in range(winreg.QueryInfoKey(parent)[0]):
                            try:
                                with winreg.OpenKey(parent,winreg.EnumKey(parent,i)) as key:
                                    name=winreg.QueryValueEx(key,'DisplayName')[0]
                                    if 'mumu' in name.lower():roots.append(Path(winreg.QueryValueEx(key,'InstallLocation')[0]))
                            except OSError:continue
                except OSError:continue
    for environment in ('ProgramFiles','ProgramFiles(x86)','LOCALAPPDATA'):
        if os.environ.get(environment):
            base=Path(os.environ[environment])
            roots.extend(base/name for name in ('Netease/MuMuPlayer-12.0','Netease/MuMuPlayer','MuMuPlayer','MuMuPlayerGlobal'))
    found=[]
    for root in roots:
        for relative in ('shell/MuMuManager.exe','nx_main/MuMuManager.exe','nx_main/mumu-cli.exe','MuMuManager.exe'):
            path=root/relative
            if path.is_file() and path not in found:found.append(path)
    return found

class MuMu:
    def __init__(self,executable):self.executable=Path(executable)
    def list(self):
        text=command([self.executable,'info','-v','all'])
        try:data=json.loads(text[text.index('{'):]) if not text.startswith('[') else json.loads(text)
        except (ValueError,TypeError):raise WorkerError('mumu_cli_format_unsupported') from None
        if isinstance(data,dict) and 'index' in data:data=[data]
        elif isinstance(data,dict):data=[dict(v,index=str(v.get('index',k))) for k,v in data.items() if isinstance(v,dict)]
        if not isinstance(data,list):raise WorkerError('mumu_cli_format_unsupported')
        return data
    def info(self,index):
        matches=[v for v in self.list() if str(v.get('index'))==str(index)]
        if len(matches)!=1:raise WorkerError('mumu_instance_missing')
        return matches[0]
    def launch(self,index):command([self.executable,'control','-v',index,'launch'],timeout=30)
    def shutdown(self,index):
        command([self.executable,'control','-v',index,'shutdown'],timeout=30)
        deadline=time.monotonic()+45
        while self.info(index).get('is_process_started'):
            if time.monotonic()>deadline:raise WorkerError('mumu_shutdown_timeout')
            time.sleep(1)
    def endpoint(self,index):
        item=self.info(index)
        port=item.get('adb_port')
        if not port:raise WorkerError('mumu_adb_not_ready')
        if not 1024<=int(port)<=65535:raise WorkerError('mumu_adb_invalid_port')
        if item.get('adb_host_ip','127.0.0.1') not in ('127.0.0.1','localhost'):raise WorkerError('mumu_nonlocal_adb')
        return f'127.0.0.1:{port}'

class ADB:
    def __init__(self,executable,serial):self.executable=executable;self.serial=serial
    def run(self,*args,timeout=20):return command([self.executable,'-s',self.serial,*args],timeout=timeout)
    def shell(self,*args,timeout=20):return self.run('shell',' '.join(shlex.quote(str(a)) for a in args),timeout=timeout)
    def identity(self):
        identity=self.shell('settings','get','secure','android_id').strip()
        if identity in ('null','') or len(identity)<8:raise WorkerError('android_identity_missing')
        return identity
    def version(self,package):
        result=self.shell('dumpsys','package',package)
        match=re.search(r'\bversionName=([^\s]+)',result)
        if not match:raise WorkerError('app_not_installed')
        return match.group(1)
    def stage_media(self,local_path,job_id,guard):
        from uuid import UUID
        filename=str(UUID(str(job_id)))+'.mp4'
        folder='/sdcard/Movies/ShortPost/'+filename[:-4]
        destination=folder+'/'+filename
        guard.check();self.shell('mkdir','-p',folder)
        self.run('push',str(local_path),destination,timeout=120)
        guard.check()
        size=self.shell('stat','-c','%s',destination)
        if int(size)!=Path(local_path).stat().st_size:raise WorkerError('android_media_size_mismatch')
        self.shell('am','broadcast','-a','android.intent.action.MEDIA_SCANNER_SCAN_FILE','-d','file://'+destination)
        # MuMu Android 12 acknowledges the broadcast without indexing the file.
        # Ask the provider directly, then prove this exact UUID reached the video index.
        canonical='/storage/emulated/0/'+destination.removeprefix('/sdcard/')
        try:self.shell('content','call','--uri','content://media','--method','scan_file','--arg',canonical)
        except WorkerError:pass  # Older providers can still complete the broadcast asynchronously.
        deadline=time.monotonic()+30
        while True:
            guard.check()
            query=('content','query','--uri','content://media/external/video/media',
                '--projection','_display_name:duration','--where',"_display_name='"+filename+"'")
            try:indexed=self.shell(*query)
            except WorkerError:
                # MuMu Android 15 can print a valid row, then crash its content CLI.
                # Ignore only that CLI exit status; the exact UUID and duration below remain mandatory.
                indexed=self.shell('sh','-c',' '.join(shlex.quote(v) for v in query)+'; true')
            if '_display_name='+filename in indexed and re.search(r'duration=[1-9][0-9]*',indexed):break
            if time.monotonic()>deadline:raise WorkerError('android_media_not_indexed')
            time.sleep(.5)
        return destination
    def remove_media(self,path):
        nested=re.fullmatch(r'/sdcard/Movies/ShortPost/([a-f0-9-]{36})/\1\.mp4',path)
        legacy=re.fullmatch(r'/sdcard/Movies/ShortPost/[a-f0-9-]{36}\.mp4',path)
        if nested or legacy:
            self.shell('rm','-f',path)
            self.shell('am','broadcast','-a','android.intent.action.MEDIA_SCANNER_SCAN_FILE','-d','file://'+path)
            if nested:self.shell('rmdir',path.rsplit('/',1)[0])

class Devices:
    def __init__(self,config,journal):
        self.config=config;self.journal=journal
        self.locks={d.external_id:asyncio.Lock() for d in config.devices}
        self.serials={}
    def configured(self,external_id):
        device=next((d for d in self.config.devices if d.external_id==external_id and d.enabled),None)
        if not device:raise WorkerError('device_unavailable')
        return device
    def connect(self,device,guard=None,start=True):
        if guard:guard.check()
        endpoint=device.adb_endpoint
        if self.config.mumu_manager:
            manager=MuMu(self.config.mumu_manager)
            info=manager.info(device.instance_index)
            if not info.get('is_android_started',False) and start:
                manager.launch(device.instance_index)
            deadline=time.monotonic()+90
            while True:
                if guard:guard.check()
                try:
                    discovered=device.adb_endpoint or manager.endpoint(device.instance_index)
                    # A configured loopback bridge belongs to the isolated network.
                    # MuMu may allocate a different NAT port after restart.
                    endpoint=device.adb_endpoint or discovered
                    break
                except WorkerError:
                    if time.monotonic()>deadline:raise WorkerError('mumu_boot_timeout')
                    time.sleep(2)
        if not endpoint:raise WorkerError('adb_endpoint_not_configured')
        try:command([self.config.adb_path,'disconnect',endpoint])
        except WorkerError:pass
        command([self.config.adb_path,'connect',endpoint])
        adb=ADB(self.config.adb_path,endpoint)
        deadline=time.monotonic()+60;reconnect_at=time.monotonic()+5
        while True:
            if guard:guard.check()
            try:
                if adb.run('get-state')=='device' and adb.shell('getprop','sys.boot_completed')=='1':break
            except WorkerError:pass
            if time.monotonic()>deadline:raise WorkerError('adb_connect_timeout')
            if time.monotonic()>=reconnect_at:
                try:
                    command([self.config.adb_path,'disconnect',endpoint]);command([self.config.adb_path,'connect',endpoint])
                except WorkerError:pass
                reconnect_at=time.monotonic()+5
            time.sleep(2)
        if adb.identity()!=device.android_id:raise WorkerError('device_identity_mismatch')
        self.journal.mapping(device.external_id,device.instance_index,device.android_id,endpoint)
        self.serials[device.external_id]=endpoint
        return adb
    def exclusive(self,device):
        if not self.config.sequential_emulators:return
        manager=MuMu(self.config.mumu_manager)
        # Never switch while any unresolved or executing publication exists.
        if self.journal.jobs(unfinished=True):raise WorkerError('unfinished_jobs_block_switch')
        running=[v for v in manager.list() if v.get('is_process_started')]
        if any(int(v['index']) not in self.config.managed_instance_indices for v in running):
            raise WorkerError('unmanaged_emulator_running')
        for item in running:
            if int(item['index'])!=device.instance_index:manager.shutdown(int(item['index']))
    def finish(self,device):
        if self.config.sequential_emulators and not self.journal.jobs(unfinished=True):
            MuMu(self.config.mumu_manager).shutdown(device.instance_index)
    def resources_ok(self):
        memory=psutil.virtual_memory()
        free=shutil.disk_usage(self.config.state_dir).free
        return memory.available>=self.config.min_free_ram_gb*1024**3 and free>=self.config.min_free_disk_gb*1024**3
    def heartbeat_devices(self):
        # Configured enabled resources only. Account bindings are still controlled by the server.
        return [{'worker_id':str(self.config.worker_id),'external_id':d.external_id,'name':f'MuMu {d.instance_index}',
            'enabled':True,'capabilities':{'platforms':list(d.profiles)}} for d in self.config.devices if d.enabled]

def probe_video(path,ffprobe,expected_duration):
    result=command([ffprobe,'-v','error','-show_entries','format=format_name,duration:stream=codec_type,width,height','-of','json',path],timeout=30)
    try:
        data=json.loads(result);duration=float(data['format']['duration'])
        names=set(data['format']['format_name'].split(','))
        video=next(s for s in data['streams'] if s.get('codec_type')=='video')
        if not names.intersection({'mp4','mov'}) or not math.isfinite(duration) or duration<=0 or int(video.get('width',0))<=0 or int(video.get('height',0))<=0:
            raise ValueError()
        if abs(duration-expected_duration)>max(2,expected_duration*.1):raise ValueError()
    except (ValueError,KeyError,StopIteration):raise WorkerError('unsupported_format') from None
    return data
