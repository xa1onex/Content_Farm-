import struct
from unittest.mock import Mock
from shortpost_worker.health import network,device_health


def test_missing_proxy_identity_cannot_report_success():
    adb=Mock()
    assert not network(adb,None)['ready']
    adb.shell.assert_not_called()


def test_wrong_exit_is_rejected_even_if_tcp_works():
    adb=Mock();adb.shell.side_effect=['HTTP/1.1 200 OK\n\n203.0.113.8','']
    assert not network(adb,'203.0.113.9')['ready']


def test_device_identity_failure_does_not_probe_network(config,monkeypatch):
    config.devices[0].proxy_expected_ip='203.0.113.9'
    config.devices[0].adb_endpoint='127.0.0.1:16384'
    monkeypatch.setattr('shortpost_worker.health.ADB.run',lambda *a,**kw:'device')
    monkeypatch.setattr('shortpost_worker.health.ADB.identity',lambda *a:'wrong-device')
    probe=Mock();monkeypatch.setattr('shortpost_worker.health.network',probe)
    report=device_health(config,config.devices[0])
    assert report['online'] and not report['identity_verified']
    probe.assert_not_called()
