import json,os,platform,shutil,subprocess,sys
from pathlib import Path
import httpx,psutil
from .devices import command,discover_mumu,MuMu

def report(config):
    data={'os':platform.platform(),'python':sys.version.split()[0], 'windows':os.name=='nt',
        'cpu_logical':psutil.cpu_count(),'ram_gb':round(psutil.virtual_memory().total/1024**3,2),
        'ram_available_gb':round(psutil.virtual_memory().available/1024**3,2),
        'disk_free_gb':round(shutil.disk_usage(config.state_dir).free/1024**3,2),
        'mumu_candidates':[str(p) for p in discover_mumu()],
        'configured_devices':len(config.devices),'allow_publish':config.allow_publish}
    for name,args in {'adb':[config.adb_path,'version'],'node':['node','--version'],
        'ffprobe':[config.ffprobe_path,'-version']}.items():
        try:data[name]=command(args).splitlines()[0]
        except Exception:data[name]='not_available'
    if os.name=='nt':
        try:
            output=command(['powershell','-NoProfile','-NonInteractive','-Command',
                'Get-CimInstance Win32_Processor | Select-Object Name,VirtualizationFirmwareEnabled,VMMonitorModeExtensions | ConvertTo-Json -Compress'])
            data['virtualization']=json.loads(output)
        except Exception:data['virtualization']='not_available'
    try:
        response=httpx.get(config.api_url+'/health',verify=True,timeout=15,follow_redirects=False)
        data['server_https']=response.status_code==200
    except httpx.HTTPError:data['server_https']=False
    endpoints={d.appium_url for d in config.devices} or {'http://127.0.0.1:4723'}
    data['appium']={}
    for endpoint in endpoints:
        try:
            res=httpx.get(endpoint.rstrip('/')+'/status',timeout=3,trust_env=False)
            version=res.json().get('value',{}).get('build',{}).get('version','unknown')
            data['appium'][endpoint]={'status':res.status_code,'version':version,'appium2':version.startswith('2.')}
        except Exception:data['appium'][endpoint]={'status':'not_available'}
    return data
