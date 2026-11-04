"""Probe a SOCKS5 endpoint without changing routing or exposing credentials."""
import argparse
import base64
from datetime import datetime, timezone
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import struct


class ProbeError(Exception):
    def __init__(self, code):
        self.code = code


def exact(stream, size):
    result = b''
    while len(result) < size:
        chunk = stream.recv(size - len(result))
        if not chunk:
            raise ProbeError('proxy_connection_closed')
        result += chunk
    return result


def address(stream, kind):
    if kind == 1:
        host = socket.inet_ntop(socket.AF_INET, exact(stream, 4))
    elif kind == 4:
        host = socket.inet_ntop(socket.AF_INET6, exact(stream, 16))
    elif kind == 3:
        host = exact(stream, exact(stream, 1)[0]).decode('ascii')
    else:
        raise ProbeError('proxy_invalid_address')
    return host, struct.unpack('!H', exact(stream, 2))[0]


def authenticate(record):
    stream = socket.create_connection((record['server'], int(record['port'])), timeout=10)
    try:
        stream.settimeout(10)
        # Offer username/password only; a server ignoring authentication fails.
        stream.sendall(b'\x05\x01\x02')
        if exact(stream, 2) != b'\x05\x02':
            raise ProbeError('proxy_auth_method_rejected')
        user = record['username'].encode('utf-8')
        password = record['password'].encode('utf-8')
        if not 0 < len(user) <= 255 or not 0 < len(password) <= 255:
            raise ProbeError('proxy_credentials_invalid')
        stream.sendall(b'\x01' + bytes([len(user)]) + user + bytes([len(password)]) + password)
        if exact(stream, 2) != b'\x01\x00':
            raise ProbeError('proxy_authentication_failed')
        return stream
    except BaseException:
        stream.close()
        raise


def request(stream, command, host, port):
    try:
        packed = ipaddress.ip_address(host).packed
        encoded = bytes([1 if len(packed) == 4 else 4]) + packed
    except ValueError:
        name = host.encode('idna')
        if len(name) > 255:
            raise ProbeError('proxy_invalid_target') from None
        encoded = b'\x03' + bytes([len(name)]) + name
    stream.sendall(b'\x05' + bytes([command]) + b'\x00' + encoded + struct.pack('!H', port))
    version, reply, reserved, kind = exact(stream, 4)
    if version != 5 or reserved != 0:
        raise ProbeError('proxy_invalid_reply')
    if reply:
        raise ProbeError('proxy_command_rejected_' + str(reply))
    return address(stream, kind)


def https_ip(stream):
    request(stream, 1, 'api.ipify.org', 443)
    context = ssl.create_default_context()
    with context.wrap_socket(stream, server_hostname='api.ipify.org') as tls:
        tls.sendall(b'GET / HTTP/1.1\r\nHost: api.ipify.org\r\nConnection: close\r\n\r\n')
        response = http.client.HTTPResponse(tls)
        response.begin()
        if response.status != 200:
            raise ProbeError('proxy_https_status_invalid')
        value = response.read(128).decode('ascii').strip()
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            raise ProbeError('proxy_exit_ip_invalid') from None


def udp_dns(record):
    result = {'udp_associate_accepted': False, 'udp_roundtrip_verified': False}
    try:
        with authenticate(record) as control:
            host, port = request(control, 3, '0.0.0.0', 0)
            result['udp_associate_accepted'] = True
            if host in ('0.0.0.0', '::'):
                host = record['server']
            if port == 0:
                raise ProbeError('proxy_udp_relay_invalid')
            family = socket.AF_INET6 if ':' in host else socket.AF_INET
            with socket.socket(family, socket.SOCK_DGRAM) as datagram:
                datagram.settimeout(8)
                transaction = os.urandom(2)
                query = transaction + struct.pack('!HHHHH', 0x0100, 1, 0, 0, 0)
                query += b'\x07example\x03com\x00' + struct.pack('!HH', 1, 1)
                packet = b'\x00\x00\x00\x01' + socket.inet_aton('1.1.1.1') + struct.pack('!H', 53) + query
                datagram.connect((host, port))
                datagram.send(packet)
                response = datagram.recv(4096)
                if not response.startswith(b'\x00\x00\x00\x01') or len(response) < 22:
                    raise ProbeError('proxy_udp_reply_invalid')
                if response[4:8] != socket.inet_aton('1.1.1.1') or response[8:10] != struct.pack('!H', 53):
                    raise ProbeError('proxy_udp_reply_invalid')
                dns = response[10:]
                flags = struct.unpack('!H', dns[2:4])[0]
                if dns[:2] != transaction or not flags & 0x8000 or flags & 0x000f:
                    raise ProbeError('proxy_udp_dns_invalid')
                result['udp_roundtrip_verified'] = True
    except ProbeError as error:
        result['udp_error'] = error.code
    except socket.timeout:
        result['udp_error'] = 'proxy_udp_timeout'
    except (OSError, UnicodeError, ValueError):
        result['udp_error'] = 'proxy_udp_network_error'
    return result


def probe(record):
    result = {'tested_at': datetime.now(timezone.utc).isoformat(), 'proxy_type': 'socks5',
              'authenticated': False, 'https_verified': False, 'device_routing_tested': False}
    try:
        with authenticate(record) as stream:
            result['authenticated'] = True
            result['exit_ip'] = https_ip(stream)
            result['https_verified'] = True
    except ProbeError as error:
        result['tcp_error'] = error.code
    except ssl.SSLError:
        result['tcp_error'] = 'proxy_tls_verification_failed'
    except (OSError, UnicodeError, ValueError, http.client.HTTPException):
        result['tcp_error'] = 'proxy_network_error'
    if result['https_verified']:
        result.update(udp_dns(record))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proxy-id', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', args.proxy_id):
        raise SystemExit('Invalid proxy identifier')
    from shortpost_worker.crypto import DPAPI
    state = Path(os.environ['LOCALAPPDATA']) / 'ShortPost'
    try:
        encrypted = (state / 'proxy-vault' / (args.proxy_id + '.dpapi')).read_bytes()
        record = json.loads(DPAPI().decrypt(base64.b64encode(encrypted).decode('ascii')))
        result = probe(record)
    except Exception:
        raise SystemExit('Proxy check failed; sensitive details omitted') from None
    folder = state / 'proxy-checks'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (args.proxy_id + '.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
    return 0 if result['https_verified'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
