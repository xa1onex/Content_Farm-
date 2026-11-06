import os,tomllib
from pathlib import Path
from typing import Literal
from uuid import UUID
from urllib.parse import urlsplit
from pydantic import BaseModel,ConfigDict,Field,model_validator

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')
class DeviceConfig(Strict):
    external_id:str=Field(min_length=1,max_length=120,pattern=r'^[A-Za-z0-9_-]+$')
    instance_index:int=Field(ge=0,le=999)
    android_id:str=Field(min_length=8,max_length=100)
    enabled:bool=True
    proxy_expected_ip:str|None=None
    adb_endpoint:str|None=None
    appium_url:str='http://127.0.0.1:4723'
    system_port:int=Field(default=8200,ge=1024,le=65535)
    profiles:dict[str,Path]=Field(default_factory=dict)
    @model_validator(mode='after')
    def local(self):
        if urlsplit(self.appium_url).hostname not in ('127.0.0.1','localhost','::1'):
            raise ValueError('Appium must listen on loopback')
        if self.proxy_expected_ip:
            import ipaddress
            ipaddress.IPv4Address(self.proxy_expected_ip)
        if set(self.profiles)-{'tiktok','instagram','youtube'}:raise ValueError('Unsupported platform profile')
        if self.adb_endpoint and not self.adb_endpoint.startswith('127.0.0.1:'):
            raise ValueError('ADB endpoint must be loopback')
        return self
class Config(Strict):
    api_url:str='https://origin.xdoublegroup.online:8443/api/v1'
    worker_id:UUID
    state_dir:Path=Field(default_factory=lambda:Path(os.environ.get('LOCALAPPDATA',Path.home()/'.local/share'))/'ShortPost')
    sequential_emulators:bool=False
    managed_instance_indices:list[int]=Field(default_factory=list,max_length=10)
    max_parallel:int=Field(default=1,ge=1,le=10)
    adb_path:Path=Path('adb')
    mumu_manager:Path|None=None
    ffprobe_path:Path=Path('ffprobe')
    min_free_ram_gb:float=Field(default=2,ge=.5)
    min_free_disk_gb:float=Field(default=5,ge=1)
    allow_publish:bool=False
    auto_install_apps:bool=True
    play_install_timeout_seconds:int=Field(default=600,ge=30,le=1800)
    request_timeout_seconds:int=Field(default=15,ge=3,le=30)
    lease_safety_seconds:int=Field(default=20,ge=10,le=60)
    progress_seconds:int=Field(default=30,ge=20,le=60)
    max_processing_seconds:int=Field(default=1800,ge=60,le=1800)
    devices:list[DeviceConfig]=Field(default_factory=list,max_length=10)
    @model_validator(mode='after')
    def validate_config(self):
        if self.sequential_emulators:
            if self.max_parallel!=1 or not self.mumu_manager:raise ValueError('Sequential mode requires MuMu and max_parallel=1')
            if not self.managed_instance_indices or len(set(self.managed_instance_indices))!=len(self.managed_instance_indices):raise ValueError('Set unique managed instance indices')
            if any(d.instance_index not in self.managed_instance_indices for d in self.devices):raise ValueError('Device is outside managed instances')
        url=urlsplit(self.api_url)
        if url.scheme!='https' or url.username or url.password or url.query or url.fragment or url.path.rstrip('/')!='/api/v1':
            raise ValueError('Set an HTTPS /api/v1 URL without credentials or query')
        for field in ('external_id','instance_index','system_port','android_id'):
            values=[getattr(d,field) for d in self.devices]
            if len(values)!=len(set(values)):raise ValueError(f'Duplicate device {field}')
        self.api_url=self.api_url.rstrip('/')
        self.state_dir=self.state_dir.expanduser().resolve()
        return self

def load(path):
    path=Path(path).resolve()
    config=Config.model_validate(tomllib.loads(path.read_text(encoding='utf-8')))
    for device in config.devices:
        device.profiles={k:(v if v.is_absolute() else path.parent/v).resolve() for k,v in device.profiles.items()}
    config.state_dir.mkdir(parents=True,exist_ok=True)
    return config
