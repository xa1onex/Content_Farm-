"""Build a Linux gateway policy. Credentials stay in memory; nothing is logged."""
import ipaddress
import re


def build(devices):
    """Return secret sing-box config, nft guard and reserved DHCP configuration.

    Each entry supplies external_id, address, mac and a decrypted proxy record.
    The caller must protect the resulting configuration as a secret.
    """
    if not devices:
        raise ValueError('At least one device is required')
    seen = {key: set() for key in ('external_id', 'address', 'mac')}
    selected = []
    for device in devices:
        identifier = device['external_id']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identifier):
            raise ValueError('Invalid device identifier')
        address = ipaddress.IPv4Address(device['address'])
        if address not in ipaddress.IPv4Network('10.77.0.0/24') or not 2 <= address.packed[-1] <= 253:
            raise ValueError('Device address must be inside the isolated device range')
        mac = device['mac'].lower()
        if not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', mac) or int(mac[:2], 16) & 1:
            raise ValueError('Invalid device MAC')
        record = device['proxy']
        # An IP endpoint prevents bootstrap DNS from selecting a different route.
        ipaddress.IPv4Address(record['server'])
        if record.get('type') != 'socks5' or not 1 <= int(record['port']) <= 65535:
            raise ValueError('Invalid SOCKS5 endpoint')
        for key in ('username', 'password'):
            value = record[key]
            if not isinstance(value, str) or not 1 <= len(value.encode('utf-8')) <= 255:
                raise ValueError('Invalid SOCKS5 credentials')
        clean = {'external_id': identifier, 'address': str(address), 'mac': mac, 'proxy': record}
        for key in seen:
            if clean[key] in seen[key]:
                raise ValueError('Duplicate device mapping')
            seen[key].add(clean[key])
        selected.append(clean)
    selected.sort(key=lambda entry: entry['external_id'])
    addresses = [entry['address'] + '/32' for entry in selected]
    outbounds, dns_servers, dns_rules, route_rules = [], [], [], [
        {'source_ip_cidr': addresses, 'invert': True, 'action': 'reject'},
        {'ip_version': 6, 'action': 'reject'},
        {'port': 53, 'action': 'hijack-dns'},
        {'ip_is_private': True, 'action': 'reject'},
    ]
    for entry in selected:
        tag, proxy = entry['external_id'], entry['proxy']
        source = [entry['address'] + '/32']
        outbounds.append({'type': 'socks', 'tag': tag, 'server': proxy['server'],
            'server_port': int(proxy['port']), 'version': '5', 'username': proxy['username'],
            'password': proxy['password'], 'bind_interface': 'eth0'})
        dns_servers.append({'type': 'https', 'tag': 'dns-' + tag, 'server': '1.1.1.1',
            'path': '/dns-query', 'tls': {'enabled': True, 'server_name': 'cloudflare-dns.com'},
            'detour': tag})
        dns_rules.append({'source_ip_cidr': source, 'server': 'dns-' + tag})
        route_rules.append({'source_ip_cidr': source, 'action': 'route', 'outbound': tag})
    route_rules.append({'action': 'reject'})
    config = {'log': {'level': 'error', 'timestamp': True},
        'dns': {'servers': dns_servers, 'rules': dns_rules, 'final': dns_servers[0]['tag'],
                'strategy': 'ipv4_only'},
        'inbounds': [
            {'type': 'tun', 'tag': 'android', 'interface_name': 'sp-tun',
             'address': ['172.31.255.1/30'], 'mtu': 1500, 'auto_route': False, 'stack': 'gvisor'},
            {'type': 'direct', 'tag': 'dns-in', 'listen': '10.77.0.1', 'listen_port': 53,
             'override_address': '1.1.1.1', 'override_port': 53}],
        'outbounds': outbounds, 'route': {'rules': route_rules}}
    allowed = ', '.join(entry['address'] for entry in selected)
    guard = '''flush table inet shortpost_guard
table inet shortpost_guard {
  chain input {
    type filter hook input priority 0; policy accept;
    iifname "eth1" meta nfproto ipv6 counter drop
    iifname "eth1" udp sport 68 udp dport 67 counter accept
    iifname "eth1" ip saddr != { ALLOWED } counter drop
    iifname "eth1" udp dport 53 counter accept
    iifname "eth1" tcp dport 53 counter accept
    iifname "eth1" ip protocol icmp counter accept
    iifname "eth1" counter drop
  }
  chain forward {
    type filter hook forward priority 0; policy drop;
    iifname "eth1" oifname "sp-tun" ip saddr { ALLOWED } meta l4proto { tcp, udp } counter accept
    iifname "sp-tun" oifname "eth1" ip daddr { ALLOWED } counter accept
  }
}
'''.replace('ALLOWED', allowed)
    dhcp = ['port=0', 'interface=eth1', 'bind-interfaces', 'dhcp-authoritative',
            'dhcp-range=10.77.0.0,static,255.255.255.0,12h', 'dhcp-ignore=tag:!known',
            'dhcp-option=option:router,10.77.0.1', 'dhcp-option=option:dns-server,10.77.0.1']
    dhcp.extend('dhcp-host={mac},{address},{external_id},12h'.format(**entry) for entry in selected)
    return config, guard, '\n'.join(dhcp) + '\n'
