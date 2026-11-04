import argparse,asyncio,getpass,json,os,sys,uuid
from pathlib import Path
import portalocker
from pydantic import ValidationError
from .config import load
from .crypto import DPAPI,TokenStore
from .journal import Journal
from .api import Api
from .engine import Engine
from .devices import command,ADB,MuMu,discover_mumu
from .diagnostics import report
from .errors import WorkerError
from .logs import configure

def parser():
    p=argparse.ArgumentParser(prog='shortpost-worker')
    p.add_argument('--config',type=Path,default=Path('worker/config.local.toml'))
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('doctor');sub.add_parser('connection');sub.add_parser('emulators')
    sub.add_parser('status');sub.add_parser('pause');sub.add_parser('resume');sub.add_parser('stop')
    token=sub.add_parser('token');token.add_argument('action',choices=['set'])
    run=sub.add_parser('run');run.add_argument('--live',action='store_true')
    once=sub.add_parser('run-once');once.add_argument('--job-id',required=True,type=uuid.UUID)
    once.add_argument('--external-id',required=True);once.add_argument('--profile',required=True,type=Path)
    once.add_argument('--live',action='store_true',required=True,help='Publish exactly the requested server job')
    init=sub.add_parser('init');init.add_argument('--worker-id',required=True,type=uuid.UUID)
    bind=sub.add_parser('bind');bind.add_argument('--external-id',required=True)
    bind.add_argument('--instance',required=True,type=int);bind.add_argument('--serial',required=True)
    for name in ('inspect-ui','record-locator'):
        node=sub.add_parser(name);node.add_argument('--external-id',required=True)
        if name=='record-locator':node.add_argument('--output',required=True,type=Path)
    prepare=sub.add_parser('provision-apps')
    targets=prepare.add_mutually_exclusive_group(required=True)
    targets.add_argument('--all',action='store_true')
    targets.add_argument('--external-id')
    prepare.add_argument('--check-only',action='store_true')
    nav=sub.add_parser('test-navigation');nav.add_argument('--external-id',required=True)
    nav.add_argument('--platform',required=True,choices=['tiktok','instagram','youtube'])
    nav.add_argument('--username',required=True);nav.add_argument('--video',required=True,type=Path)
    nav.add_argument('--profile',type=Path,help='Diagnostic profile override; does not change device configuration')
    nav.add_argument('--report-server',action='store_true',help='Send successful diagnostic metadata in a Worker heartbeat; no job or publication is created')
    return p

async def report_navigation(config,journal,result):
    token=TokenStore(config.state_dir/'token.dpapi',journal.cipher).read()
    api=Api(config,token,journal)
    try:
        devices=Engine(config,journal,api).devices.heartbeat_devices()
        await api.heartbeat(devices,capabilities={'navigation_test':result})
        print('Navigation report sent to server.')
    finally:await api.close()

async def run_once(config,journal,args):
    from .platforms.profile import Profile
    selected=next((d for d in config.devices if d.external_id==args.external_id and d.enabled),None)
    if selected is None:raise WorkerError('device_unavailable')
    path=args.profile.resolve();profile=Profile.load(path)
    if profile.navigation_only or not profile.navigation_verified:raise WorkerError('profile_not_validated')
    # The explicit --live switch authorizes only this process and this job.
    # Neither TOML nor the persistent Worker admission/pause controls are changed.
    runtime=config.model_copy(deep=True,update={'allow_publish':True,'max_parallel':1,
        'auto_install_apps':False,'devices':[selected.model_copy(deep=True,update={'profiles':{profile.platform:path}})]})
    token=TokenStore(config.state_dir/'token.dpapi',journal.cipher).read()
    api=Api(runtime,token,journal)
    try:print(json.dumps(await Engine(runtime,journal,api).run_once(args.job_id),indent=2))
    finally:await api.close()

async def connected(config,journal,command_name,live=False):
    token=TokenStore(config.state_dir/'token.dpapi',journal.cipher).read()
    api=Api(config,token,journal)
    try:
        if command_name=='connection':
            devices=Engine(config,journal,api).devices.heartbeat_devices()
            result=await api.heartbeat(devices)
            print(json.dumps({'connected':True,'worker_id':result['worker_id'],'server_time':result['server_time'],
                'devices':len((await api.get('/devices'))['items'])},indent=2))
        else:await Engine(config,journal,api).run(live=live)
    finally:await api.close()

def main():
    args=parser().parse_args()
    try:
        if args.command=='init':
            if args.config.exists():raise RuntimeError('Config exists; edit it instead of overwriting')
            args.config.parent.mkdir(parents=True,exist_ok=True)
            text='api_url = "https://origin.xdoublegroup.online:8443/api/v1"\nworker_id = '+json.dumps(str(args.worker_id))+'\nmax_parallel = 1\nallow_publish = false\n'
            found=discover_mumu()
            if len(found)==1:text+='mumu_manager = '+json.dumps(str(found[0]))+'\n'
            args.config.write_text(text,encoding='utf-8');print('Config created:',args.config);return
        config=load(args.config)
        if args.command=='doctor':
            print(json.dumps(report(config),indent=2,ensure_ascii=False));return
        if args.command=='emulators':
            if config.mumu_manager:print(json.dumps(MuMu(config.mumu_manager).list(),indent=2,ensure_ascii=False))
            else:
                print('MuMu manager paths:',*[str(p) for p in discover_mumu()],sep='\n')
                print(command([config.adb_path,'devices','-l']))
            return
        cipher=DPAPI()
        if args.command=='token':
            TokenStore(config.state_dir/'token.dpapi',cipher).save(getpass.getpass('Worker bearer token (hidden): '))
            print('Token stored using current-user Windows DPAPI.');return
        configure(config.state_dir/'logs')
        journal=Journal(config.state_dir/'journal.sqlite3',cipher)
        try:
            if args.command in ('pause','resume','stop'):
                journal.control('stop' if args.command=='stop' else 'paused','0' if args.command=='resume' else '1')
                print('Control saved:',args.command);return
            if args.command=='status':
                # IDs and stages only; encrypted assignments, tokens and result payloads are not printed.
                print(json.dumps({'paused':journal.control('paused')=='1',
                    'jobs':[{k:r[k] for k in ('job_id','attempt','device','stage','irreversible_at','updated')} for r in journal.jobs()],
                    'outbox_pending':len(journal.pending()),'analytics_pending':len(journal.due_metrics(float('inf')))},indent=2));return
            with portalocker.Lock(config.state_dir/'worker.lock',timeout=0):
                if args.command=='run-once':asyncio.run(run_once(config,journal,args));return
                if args.command=='provision-apps':
                    from .provisioning import prepare_devices
                    result=prepare_devices(config,journal,external_id=args.external_id,check_only=args.check_only)
                    if not all(d['ready'] for d in result['devices']):raise SystemExit(2)
                    return
                if args.command=='bind':
                    if args.instance<0:raise RuntimeError('Instance index must be nonnegative')
                    from .config import DeviceConfig
                    if not args.serial.startswith('127.0.0.1:'):raise RuntimeError('ADB serial must be a loopback endpoint')
                    command([config.adb_path,'connect',args.serial])
                    identity=ADB(config.adb_path,args.serial).identity()
                    device=DeviceConfig(external_id=args.external_id,instance_index=args.instance,android_id=identity,
                        adb_endpoint=args.serial,system_port=8200+len(config.devices))
                    if any(d.external_id==device.external_id or d.instance_index==device.instance_index or d.android_id==identity for d in config.devices):
                        raise RuntimeError('Device/index/Android ID already bound; review config and journal before reassigning')
                    journal.mapping(device.external_id,device.instance_index,identity,args.serial)
                    text='\n[[devices]]\nexternal_id = '+json.dumps(device.external_id)+'\ninstance_index = '+str(device.instance_index)+'\nandroid_id = '+json.dumps(identity)+'\nadb_endpoint = '+json.dumps(args.serial)+'\nsystem_port = '+str(device.system_port)+'\n'
                    with args.config.open('a',encoding='utf-8') as out:out.write(text)
                    print('Stable mapping saved. Configure [devices.profiles] before live execution.');return
                if args.command in ('inspect-ui','record-locator'):
                    from .calibration import inspect
                    inspect(config,journal,args.external_id,getattr(args,'output',None));return
                if args.command=='test-navigation':
                    from .calibration import test_navigation
                    result=test_navigation(config,journal,args.external_id,args.platform,args.username,args.video,profile_path=args.profile)
                    if args.report_server:asyncio.run(report_navigation(config,journal,result))
                    return
                if args.command=='run' and args.live and not config.allow_publish:
                    raise RuntimeError('Live mode requires explicit allow_publish=true in local config')
                asyncio.run(connected(config,journal,args.command,live=getattr(args,'live',False)))
        finally:journal.close()
    except ValidationError:raise SystemExit('Invalid configuration or server contract; sensitive input omitted')
    except portalocker.exceptions.LockException:raise SystemExit('Worker/diagnostics already running under this Windows user')
    except WorkerError as exc:raise SystemExit(f'{exc.code} (HTTP {exc.status})')
    except (RuntimeError,ValueError,OSError) as exc:
        # Never include raw network exception representations or API response bodies.
        raise SystemExit(str(exc))
if __name__=='__main__':main()
