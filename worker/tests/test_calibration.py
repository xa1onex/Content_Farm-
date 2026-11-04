import hashlib,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from shortpost_worker import calibration
from shortpost_worker.cli import parser
from shortpost_worker.platforms.instagram import InstagramPublisher


def test_diagnostic_profile_override_never_submits_or_changes_device_binding(config,journal,tmp_path,monkeypatch):
    preset=Path(__file__).resolve().parents[1]/'profiles'/'instagram-452.0.0.0.43-ru-RU.navigation.json'
    local=tmp_path/'instagram.local.json';local.write_bytes(preset.read_bytes())
    original_profiles=dict(config.devices[0].profiles)
    devices=Mock();devices.configured.return_value=config.devices[0]
    devices.connect.return_value=SimpleNamespace(serial='127.0.0.1:16384',remove_media=Mock())
    session=Mock();publisher=Mock(spec=InstagramPublisher)
    publisher.prepare_media.return_value='/sdcard/Movies/ShortPost/test/test.mp4'
    monkeypatch.setattr(calibration,'Devices',lambda *args:devices)
    monkeypatch.setattr(calibration,'Session',lambda *args:session)
    monkeypatch.setitem(calibration.PUBLISHERS,'instagram',lambda *args:publisher)
    monkeypatch.setattr('shortpost_worker.devices.command',lambda *args:json.dumps({'format':{'duration':'3.0'}}))
    video=tmp_path/'clip.mp4';video.write_bytes(b'diagnostic')
    result=calibration.test_navigation(config,journal,'mumu-01','instagram','example',video,profile_path=local)
    publisher.verify_account.assert_called_once()
    publisher.prepare_media.assert_called_once_with(video)
    publisher.open_upload.assert_called_once();publisher.fill_metadata.assert_called_once()
    publisher.find.assert_called_once();publisher.submit.assert_not_called()
    publisher.verify_publication.assert_not_called();session.close.assert_called_once()
    devices.connect.return_value.remove_media.assert_called_once_with(publisher.prepare_media.return_value)
    assert config.devices[0].profiles==original_profiles
    saved=json.loads(local.read_text(encoding='utf-8'))
    assert saved['navigation_verified'] is True and saved['navigation_only'] is True
    assert json.loads(preset.read_text(encoding='utf-8'))['navigation_verified'] is False
    assert result['input_sha256']==hashlib.sha256(b'diagnostic').hexdigest()
    assert result['stopped_before_publish'] is True and result['published'] is False
    assert result['username']=='example' and 'lease_token' not in result


def test_cli_accepts_diagnostic_profile_override():
    args=parser().parse_args(['test-navigation','--external-id','mumu-01','--platform','instagram',
        '--username','example','--video','clip.mp4','--profile','instagram.local.json','--report-server'])
    assert args.profile==Path('instagram.local.json') and args.report_server
