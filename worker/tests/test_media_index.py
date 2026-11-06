import uuid
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from shortpost_worker.devices import ADB
from shortpost_worker.errors import WorkerError

def test_stage_media_requires_this_exact_video_to_be_indexed(tmp_path,monkeypatch):
    video=tmp_path/'test.mp4';video.write_bytes(b'video');identifier=str(uuid.uuid4());calls=[]
    a=ADB('adb','127.0.0.1:1');a.run=Mock(return_value='')
    def shell(*args,**kw):
        calls.append(args)
        if args[0]=='stat':return '5'
        if args[:2]==('content','query'):return '_display_name='+identifier+'.mp4, duration=10000'
        return ''
    a.shell=shell
    assert a.stage_media(video,identifier,SimpleNamespace(check=lambda:None)).endswith(identifier+'.mp4')
    scan=next(x for x in calls if x[:2]==('content','call'));assert scan[-1].startswith('/storage/emulated/0/')
    assert any(x[:2]==('content','query') and identifier in x[-1] for x in calls)

def test_failed_media_index_prevents_opening_upload(tmp_path,monkeypatch):
    video=tmp_path/'test.mp4';video.write_bytes(b'video');a=ADB('adb','127.0.0.1:1');a.run=Mock(return_value='')
    a.shell=lambda *args,**kw:'5' if args[0]=='stat' else 'No result found.'
    ticks=iter([0,31]);monkeypatch.setattr('shortpost_worker.devices.time.monotonic',lambda:next(ticks))
    with pytest.raises(WorkerError,match='android_media_not_indexed'):a.stage_media(video,str(uuid.uuid4()),SimpleNamespace(check=lambda:None))

def test_mumu15_cli_crash_still_requires_exact_indexed_row(tmp_path):
    video=tmp_path/'test.mp4';video.write_bytes(b'video');identifier=str(uuid.uuid4());a=ADB('adb','device');a.run=Mock(return_value='');calls=[]
    def shell(*args,**kw):
        calls.append(args)
        if args[0]=='stat':return '5'
        if args[:2]==('content','query'):raise WorkerError('device_command_failed')
        if args[:2]==('sh','-c'):return 'Row: 0 _display_name='+identifier+'.mp4, duration=10000\n'
        return ''
    a.shell=shell
    assert a.stage_media(video,identifier,SimpleNamespace(check=lambda:None)).endswith(identifier+'.mp4')
    assert any(args[:2]==('sh','-c') for args in calls)
