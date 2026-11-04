import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from shortpost_worker.devices import ADB,MuMu,Devices,probe_video
from shortpost_worker.errors import WorkerError
from shortpost_worker.config import Config,DeviceConfig

def test_adb_version_parser_uses_actual_package_output():
    adb=ADB('adb','127.0.0.1:16384');adb.shell=Mock(return_value='versionName=36.4.3\n versionCode=123')
    assert adb.version('com.example.app')=='36.4.3'

def test_staged_jobs_have_separate_gallery_albums(tmp_path):
    video=tmp_path/'clip.mp4';video.write_bytes(b'video')
    adb=ADB('adb','device');adb.run=Mock()
    adb.shell=Mock(side_effect=lambda *args,**kwargs:'5' if args[0]=='stat' else '')
    first='00000000-0000-4000-8000-000000000001'
    second='00000000-0000-4000-8000-000000000002'
    a=adb.stage_media(video,first,Mock());b=adb.stage_media(video,second,Mock())
    assert a==f'/sdcard/Movies/ShortPost/{first}/{first}.mp4'
    assert Path(a).parent!=Path(b).parent

def test_incomplete_android_copy_is_rejected_before_gallery_scan(tmp_path):
    video=tmp_path/'clip.mp4';video.write_bytes(b'video')
    adb=ADB('adb','device');adb.run=Mock();adb.shell=Mock(return_value='4')
    with pytest.raises(WorkerError,match='android_media_size_mismatch'):
        adb.stage_media(video,'00000000-0000-4000-8000-000000000001',Mock())
    assert not any(c.args[0]=='am' for c in adb.shell.call_args_list)

def test_cleanup_cannot_remove_other_jobs_or_general_gallery_files():
    adb=ADB('adb','device');adb.shell=Mock()
    first='00000000-0000-4000-8000-000000000001'
    second='00000000-0000-4000-8000-000000000002'
    for path in ['/sdcard/DCIM/clip.mp4',f'/sdcard/Movies/ShortPost/{first}/{second}.mp4',f'/sdcard/Movies/ShortPost/{first}/../clip.mp4']:
        adb.remove_media(path)
    adb.shell.assert_not_called()
    adb.remove_media(f'/sdcard/Movies/ShortPost/{first}/{first}.mp4')
    assert adb.shell.call_args_list[0].args==('rm','-f',f'/sdcard/Movies/ShortPost/{first}/{first}.mp4')
    assert adb.shell.call_args_list[-1].args==('rmdir',f'/sdcard/Movies/ShortPost/{first}')

def test_mumu_reads_real_port_instead_of_guessing(monkeypatch):
    monkeypatch.setattr('shortpost_worker.devices.command',lambda args:json.dumps({'0':{'index':'0','adb_port':17321,'adb_host_ip':'127.0.0.1','is_android_started':True}}))
    assert MuMu('MuMuManager.exe').endpoint(0)=='127.0.0.1:17321'

def test_android_identity_mismatch_stops_mapping(config,journal,monkeypatch):
    devices=Devices(config,journal)
    monkeypatch.setattr('shortpost_worker.devices.command',lambda args:'connected')
    monkeypatch.setattr(ADB,'run',lambda *args,**kwargs:'device')
    monkeypatch.setattr(ADB,'shell',lambda self,*args,**kwargs:'1' if args[0]=='getprop' else 'different-android-id')
    config.devices[0].adb_endpoint='127.0.0.1:17321'
    with pytest.raises(WorkerError,match='device_identity_mismatch'):devices.connect(config.devices[0])
    assert not journal.db.execute('SELECT * FROM mappings').fetchall()

@pytest.mark.parametrize('duration',['nan','-1','N/A'])
def test_corrupt_video_metadata_is_rejected(monkeypatch,duration,tmp_path):
    value={'format':{'duration':duration,'format_name':'mov,mp4'},'streams':[{'codec_type':'video','width':1080,'height':1920}]}
    monkeypatch.setattr('shortpost_worker.devices.command',lambda *args,**kwargs:json.dumps(value))
    with pytest.raises(WorkerError):probe_video(tmp_path/'v.mp4','ffprobe',20)

def test_config_rejects_plain_http_and_duplicate_devices(tmp_path):
    with pytest.raises(ValueError):Config(worker_id='00000000-0000-4000-8000-000000000001',api_url='http://server/api/v1',state_dir=tmp_path)
    device={'external_id':'mumu-01','instance_index':0,'android_id':'identity'}
    with pytest.raises(ValueError):Config(worker_id='00000000-0000-4000-8000-000000000001',devices=[device,device],state_dir=tmp_path)


def test_pinned_isolated_endpoint_survives_mumu_nat_port_change(config,journal,monkeypatch):
    config.mumu_manager=Path('MuMuManager.exe')
    config.devices[0].adb_endpoint='127.0.0.1:16384'
    monkeypatch.setattr(MuMu,'info',lambda *a:{'is_android_started':True})
    monkeypatch.setattr(MuMu,'endpoint',lambda *a:'127.0.0.1:16385')
    monkeypatch.setattr('shortpost_worker.devices.command',lambda *a,**kw:'connected')
    monkeypatch.setattr(ADB,'run',lambda *a,**kw:'device')
    monkeypatch.setattr(ADB,'shell',lambda self,*args,**kw:'1' if args[0]=='getprop' else config.devices[0].android_id)
    assert Devices(config,journal).connect(config.devices[0]).serial=='127.0.0.1:16384'
