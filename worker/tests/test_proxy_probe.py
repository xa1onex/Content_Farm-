import io
import socket
from unittest.mock import Mock, patch

import pytest

from shortpost_worker import proxy_probe as probe


RECORD = {'server': '127.0.0.1', 'port': 1080, 'username': 'test', 'password': 'secret'}


def test_fragmented_reads_and_closed_connection():
    stream = Mock()
    stream.recv.side_effect = [b'a', b'b', b'']
    assert probe.exact(stream, 2) == b'ab'
    with pytest.raises(probe.ProbeError) as error:
        probe.exact(stream, 1)
    assert error.value.code == 'proxy_connection_closed'


@pytest.mark.parametrize('reply,code', [
    (b'\x05\x00', 'proxy_auth_method_rejected'),
    (b'\x05\x02\x01\x01', 'proxy_authentication_failed'),
])
def test_authentication_rejection_closes_socket(reply, code):
    stream = Mock()
    stream.recv.side_effect = io.BytesIO(reply).read
    with patch.object(probe.socket, 'create_connection', return_value=stream) as connect:
        with pytest.raises(probe.ProbeError) as error:
            probe.authenticate(RECORD)
    assert error.value.code == code
    connect.assert_called_once_with(('127.0.0.1', 1080), timeout=10)
    stream.close.assert_called_once()


def test_connect_uses_remote_dns():
    stream = Mock()
    stream.recv.side_effect = io.BytesIO(b'\x05\x00\x00\x01\x00\x00\x00\x00\x00\x01').read
    with patch.object(probe.socket, 'getaddrinfo', side_effect=AssertionError('local DNS forbidden')):
        assert probe.request(stream, 1, 'api.ipify.org', 443) == ('0.0.0.0', 1)
    stream.sendall.assert_called_once_with(b'\x05\x01\x00\x03\x0dapi.ipify.org\x01\xbb')


def test_failed_proxy_never_retries_direct_or_discloses_error():
    with patch.object(probe, 'authenticate', side_effect=OSError('secret details')) as connect, \
         patch.object(probe, 'https_ip') as https, patch.object(probe, 'udp_dns') as udp:
        result = probe.probe(RECORD)
    connect.assert_called_once_with(RECORD)
    https.assert_not_called()
    udp.assert_not_called()
    assert result['tcp_error'] == 'proxy_network_error'
    assert result['https_verified'] is False
    assert result['device_routing_tested'] is False
    assert 'secret' not in str(result)


def test_udp_association_alone_is_not_roundtrip_support():
    control = Mock()
    control.__enter__ = Mock(return_value=control)
    control.__exit__ = Mock(return_value=False)
    datagram = Mock()
    datagram.__enter__ = Mock(return_value=datagram)
    datagram.__exit__ = Mock(return_value=False)
    datagram.recv.side_effect = socket.timeout()
    with patch.object(probe, 'authenticate', return_value=control), \
         patch.object(probe, 'request', return_value=('127.0.0.1', 12000)), \
         patch.object(probe.socket, 'socket', return_value=datagram):
        result = probe.udp_dns(RECORD)
    assert result == {'udp_associate_accepted': True, 'udp_roundtrip_verified': False,
                      'udp_error': 'proxy_udp_timeout'}
    datagram.connect.assert_called_once_with(('127.0.0.1', 12000))
