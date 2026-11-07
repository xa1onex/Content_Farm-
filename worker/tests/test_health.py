import struct
from unittest.mock import Mock
from shortpost_worker.health import network,device_health


def test_missing_proxy_identity_cannot_report_success():
    adb=Mock()
    assert not network(adb,None)['ready']
    adb.shell.assert_not_called()


def test_wrong_exit_is_rejected_even_if_tcp_works():
    adb=Mock();adb.shell.side_effect=[(b'HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\n203.0.113.8').hex(),'']
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


def test_tcp_and_udp_both_must_match_expected_exit(monkeypatch):
    from shortpost_worker import health
    transaction=b'x'*12
    monkeypatch.setattr(health.os,'urandom',lambda n:transaction if n==12 else b'n'*n)
    cookie=b'\x21\x12\xa4\x42'
    address=bytes(a^b for a,b in zip(bytes([203,0,113,9]),cookie))
    attribute=struct.pack('!HH',0x0020,8)+b'\x00\x01\x00\x00'+address
    stun=b'\x01\x01'+struct.pack('!H',len(attribute))+cookie+transaction+attribute
    http=b'HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\n203.0.113.9'
    adb=Mock();adb.shell.side_effect=[http.hex(),stun.hex()]
    result=network(adb,'203.0.113.9')
    assert result['ready'] and result['tcp_verified'] and result['udp_verified']

def test_disconnected_device_does_not_claim_identity_or_proxy_failure(config,monkeypatch):
    config.sequential_emulators=True;config.devices[0].adb_endpoint='127.0.0.1:16384'
    def fail(*args,**kwargs):raise RuntimeError('boot in progress')
    monkeypatch.setattr('shortpost_worker.health.ADB.run',fail)
    monkeypatch.setattr('shortpost_worker.health.command',lambda *a,**kw:'')
    probe=Mock();monkeypatch.setattr('shortpost_worker.health.network',probe)
    report=device_health(config,config.devices[0])
    assert not report['identity_checked'] and not report['identity_verified']
    probe.assert_not_called()

def test_explicit_direct_mode_can_check_network_without_proxy():
    adb=Mock();adb.shell.return_value=(b'HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\n203.0.113.9').hex()
    report=network(adb,None,'direct');assert report['ready'] and report['mode']=='direct'

def test_http_connect_requires_tcp_match_and_blocked_udp():
    adb=Mock();adb.shell.side_effect=[(b'HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\n203.0.113.9').hex(),'']
    report=network(adb,'203.0.113.9','http');assert report['ready'] and report['udp_blocked']
    adb.shell.side_effect=[(b'HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\n203.0.113.8').hex(),'']
    assert not network(adb,'203.0.113.9','http')['ready']

def test_cached_adb_transport_is_not_live_without_a_shell_reply(config,monkeypatch):
    config.devices[0].adb_endpoint='127.0.0.1:16384'
    monkeypatch.setattr('shortpost_worker.health.ADB.run',lambda *a,**kw:'device')
    def disconnected(*args):raise RuntimeError('closed transport')
    monkeypatch.setattr('shortpost_worker.health.ADB.identity',disconnected)
    r=device_health(config,config.devices[0]);assert not r['online'] and not r['identity_checked']


def test_android_proxy_probe_keeps_stdin_open_until_http_reply_arrives():
    # Actual Android 12/proxy behavior: immediate stdin EOF gives an empty reply,
    # while holding the write side open returns the correctly proxied HTTP body.
    response=b'HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\n203.0.113.9'
    def android_shell(*args,**kwargs):
        command=args[-1]
        if 'api.ipify.org 80' in command:
            return response.hex() if 'sleep 8' in command else ''
        return ''  # UDP remains blocked for HTTP CONNECT proxies.
    adb=Mock();adb.shell.side_effect=android_shell
    result=network(adb,'203.0.113.9','http')
    assert result['checked'] and result['ready'] and result['tcp_verified'] and result['udp_blocked']
