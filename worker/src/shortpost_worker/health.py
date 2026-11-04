"""Fresh per-device diagnostics; no proxy credentials are read or reported."""
from datetime import datetime, timezone
import ipaddress
import os
import re
import shlex
import struct
import time
from .devices import MuMu

_restarts={}
from .devices import ADB, command
from .platforms.profile import Profile


def timestamp():return datetime.now(timezone.utc).isoformat()

def network(adb, expected):
    result={'at':timestamp(),'ready':False}
    if not expected:return result
    try:
        ipaddress.ip_address(expected)
        payload='GET /?shortpost='+os.urandom(8).hex()+' HTTP/1.1\r\nHost: api.ipify.org\r\nConnection: close\r\n\r\n'
        escaped=''.join('\\%03o'%v for v in payload.encode())
        raw=adb.shell('sh','-c',"printf '"+escaped+"' | toybox nc -4 -w 8 -W 8 -q 8 api.ipify.org 80",timeout=15)
        # adb text output normalizes CRLF on Windows.
        body=re.split(r'\r?\n\r?\n',raw,maxsplit=1)[1].strip()
        tcp=raw.startswith('HTTP/1.1 200 ') and body==expected
        # Binary STUN is transferred as hex, avoiding Windows text decoding loss.
        transaction=os.urandom(12);request=b'\x00\x01\x00\x00\x21\x12\xa4\x42'+transaction
        escaped=''.join('\\%03o'%v for v in request)
        raw=adb.shell('sh','-c',"printf '"+escaped+"' | toybox nc -4 -u -w 8 -W 4 -q 4 stun.l.google.com 19302 | toybox od -An -v -tx1",timeout=15)
        response=bytes.fromhex(raw);udp=False
        if response[:2]==b'\x01\x01' and response[4:8]==request[4:8] and response[8:20]==transaction and len(response)==20+int.from_bytes(response[2:4],'big'):
            pos=20
            while pos+4<=len(response):
                kind,size=struct.unpack('!HH',response[pos:pos+4]);value=response[pos+4:pos+4+size];pos+=4+((size+3)//4)*4
                if kind==0x0020 and size==8 and value[:2]==b'\x00\x01':
                    actual=str(ipaddress.ip_address(bytes(a^b for a,b in zip(value[4:8],request[4:8]))))
                    udp=actual==expected
        result.update(tcp_verified=tcp,udp_verified=udp,ready=tcp and udp)
    except Exception:pass
    return result


def device_health(config, device):
    health={'at':timestamp(),'online':False,'identity_verified':False,'profiles':{},'network':{'at':timestamp(),'ready':False}}
    for platform,path in device.profiles.items():
        try:
            profile=Profile.load(path)
            health['profiles'][platform]={'verified':profile.navigation_verified and not profile.navigation_only,'version':profile.app_version}
        except Exception:health['profiles'][platform]={'verified':False}
    if not device.adb_endpoint:return health
    try:
        # Reconnect only this endpoint; never restart the shared ADB server.
        adb=ADB(config.adb_path,device.adb_endpoint)
        try:online=adb.run('get-state',timeout=4)=='device'
        except Exception:online=False
        if not online:
            if config.mumu_manager and time.monotonic()-_restarts.get(device.external_id,-1000)>300:
                _restarts[device.external_id]=time.monotonic()
                manager=MuMu(config.mumu_manager)
                if not manager.info(device.instance_index).get('is_android_started'):manager.launch(device.instance_index)
            command([config.adb_path,'disconnect',device.adb_endpoint],timeout=5)
            command([config.adb_path,'connect',device.adb_endpoint],timeout=8)
        health['online']=adb.run('get-state',timeout=4)=='device'
        health['identity_verified']=health['online'] and adb.identity()==device.android_id
        if not health['identity_verified']:return health
        for platform,path in device.profiles.items():
            profile=Profile.load(path)
            health['profiles'][platform]['verified'] &= adb.version(profile.package)==profile.app_version
        health['network']=network(adb,device.proxy_expected_ip)
    except Exception:pass
    return health
