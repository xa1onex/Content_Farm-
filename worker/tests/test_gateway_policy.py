import copy

import pytest

from shortpost_worker.gateway_policy import build


def devices():
    return [{'external_id': 'phone-01', 'address': '10.77.0.10', 'mac': '02:00:00:00:00:01',
             'proxy': {'type': 'socks5', 'server': '192.0.2.1', 'port': 1080,
                       'username': 'test-user', 'password': 'private-test-password'}},
            {'external_id': 'phone-02', 'address': '10.77.0.11', 'mac': '02:00:00:00:00:02',
             'proxy': {'type': 'socks5', 'server': '192.0.2.2', 'port': 1081,
                       'username': 'other-user', 'password': 'other-password'}}]


def test_each_device_uses_its_own_proxy_for_traffic_and_dns():
    config, guard, dhcp = build(devices())
    assert {item['type'] for item in config['outbounds']} == {'socks'}
    for device in devices():
        identifier = device['external_id']
        route = next(rule for rule in config['route']['rules'] if rule.get('outbound') == identifier)
        assert route['source_ip_cidr'] == [device['address'] + '/32']
        server = next(server for server in config['dns']['servers'] if server['tag'] == 'dns-' + identifier)
        assert server['detour'] == identifier
        assert server['type'] == 'https'
        assert any(rule['source_ip_cidr'] == [device['address'] + '/32'] and
                   rule['server'] == server['tag'] for rule in config['dns']['rules'])
        assert device['mac'] + ',' + device['address'] in dhcp
    assert 'private-test-password' not in guard + dhcp


def test_unknown_devices_and_ipv6_are_rejected_without_direct_fallback():
    config, guard, dhcp = build(devices())
    assert config['route']['rules'][0]['invert'] is True
    assert config['route']['rules'][0]['action'] == 'reject'
    assert config['route']['rules'][1] == {'ip_version': 6, 'action': 'reject'}
    assert config['route']['rules'][-1] == {'action': 'reject'}
    assert 'policy drop' in guard
    assert 'oifname "eth0"' not in guard
    assert 'dhcp-ignore=tag:!known' in dhcp
    assert 'option:router,10.77.0.1' in dhcp


@pytest.mark.parametrize('field,value', [
    ('address', '192.168.1.3'), ('address', '10.77.0.1'), ('address', '10.77.0.254'),
    ('address', '::1'), ('mac', '01:00:00:00:00:00'), ('external_id', '../phone'),
])
def test_invalid_or_reserved_device_mapping_is_rejected(field, value):
    rows = devices()
    rows[0][field] = value
    with pytest.raises(ValueError):
        build(rows)


@pytest.mark.parametrize('field', ['external_id', 'address', 'mac'])
def test_duplicate_mapping_is_rejected(field):
    rows = devices()
    rows[1][field] = rows[0][field]
    with pytest.raises(ValueError):
        build(rows)


def test_build_does_not_modify_records_or_log_credentials(capsys):
    rows = devices()
    before = copy.deepcopy(rows)
    build(rows)
    assert rows == before
    assert capsys.readouterr().out == ''
