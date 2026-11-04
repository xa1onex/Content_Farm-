from datetime import datetime, date, time, timezone
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Platform=Literal['tiktok','instagram','youtube']
class Input(BaseModel):
    model_config=ConfigDict(extra='forbid')
class TaxonomyIn(Input):
    code: str = Field(pattern=r'^[A-Z][A-Z0-9_]{0,39}$')
    name: str = Field(min_length=1,max_length=120)
class SourceIn(Input):
    external_id: str = Field(min_length=1,max_length=180)
    title: str = Field(min_length=1,max_length=400)
    origin: str = Field(default='',max_length=2000)
    category_id: UUID|None=None
    topic_id: UUID|None=None
    extra: dict=Field(default_factory=dict)
class ClipIn(Input):
    passport: str|None=Field(default=None,pattern=r'^[A-Z][A-Z0-9_-]{2,119}$')
    source_id: UUID|None=None
    drive_file_id: str=Field(min_length=5,max_length=200,pattern=r'^[A-Za-z0-9_-]+$')
    sha256: str|None=Field(default=None,pattern=r'^[0-9a-f]{64}$')
    title: str=Field(min_length=1,max_length=400)
    description: str=Field(default='',max_length=10000)
    category_id: UUID
    topic_id: UUID|None=None
    format_id: UUID
    duration_seconds: float=Field(gt=0,le=86400)
    platforms: list[Platform]=Field(default_factory=lambda:['tiktok','instagram','youtube'],min_length=1)
    extra: dict=Field(default_factory=dict)
    technical: dict=Field(default_factory=dict)
class ClipPatch(Input):
    title: str|None=Field(default=None,min_length=1,max_length=400)
    description: str|None=Field(default=None,max_length=10000)
    category_id: UUID|None=None
    topic_id: UUID|None=None
    format_id: UUID|None=None
    sha256: str|None=Field(default=None,pattern=r'^[0-9a-f]{64}$')
    status: Literal['draft','ready','unavailable','archived']|None=None
    platforms: list[Platform]|None=Field(default=None,min_length=1)
    extra: dict|None=None
    technical: dict|None=None
    @model_validator(mode='after')
    def no_nulls(self):
        for field in self.model_fields_set-{'topic_id'}:
            if getattr(self,field) is None:raise ValueError(f'{field} cannot be null')
        return self
class Window(Input):
    start: str=Field(pattern=r'^([01][0-9]|2[0-3]):[0-5][0-9]$')
    end: str=Field(pattern=r'^([01][0-9]|2[0-3]):[0-5][0-9]$')
    @model_validator(mode='after')
    def order(self):
        if self.start>=self.end:raise ValueError('Window start must precede end; split overnight windows')
        return self
class AccountIn(Input):
    platform: Platform
    name: str=Field(min_length=1,max_length=160)
    username: str=Field(min_length=1,max_length=160)
    status: Literal['active','paused','attention']='paused'
    worker_id: UUID|None=None
    device_id: UUID|None=None
    timezone: str='Europe/Moscow'
    warmup_enabled: bool=True
    daily_limit: int=Field(default=3,ge=0,le=50)
    windows: list[Window]=Field(default_factory=lambda:[Window(start='08:00',end='20:00')],min_length=1,max_length=10)
    min_interval_minutes: int=Field(default=60,ge=1,le=1440)
    jitter_minutes: int=Field(default=20,ge=0,le=120)
    adaptive: bool=False
    category_ids: list[UUID]=Field(default_factory=list)
    topic_ids: list[UUID]=Field(default_factory=list)
    format_ids: list[UUID]=Field(default_factory=list)
    extra: dict=Field(default_factory=dict)
    @field_validator('timezone')
    @classmethod
    def tz(cls,value):
        try:ZoneInfo(value)
        except ZoneInfoNotFoundError:raise ValueError('Unknown IANA timezone')
        return value
    @field_validator('username')
    @classmethod
    def normalized_username(cls,value):return value.strip().lstrip('@').lower()
    @model_validator(mode='after')
    def validate_windows(self):
        ordered=sorted(self.windows,key=lambda w:w.start)
        if any(a.end>b.start for a,b in zip(ordered,ordered[1:])):
            raise ValueError('Time windows overlap')
        if self.device_id and not self.worker_id:raise ValueError('device_id requires worker_id')
        return self
class AccountPatch(Input):
    name: str|None=None
    username: str|None=None
    status: Literal['active','paused','attention','deleted']|None=None
    worker_id: UUID|None=None
    device_id: UUID|None=None
    timezone: str|None=None
    warmup_enabled: bool|None=None
    daily_limit: int|None=None
    windows: list[Window]|None=None
    min_interval_minutes: int|None=None
    jitter_minutes: int|None=None
    adaptive: bool|None=None
    category_ids: list[UUID]|None=None
    topic_ids: list[UUID]|None=None
    format_ids: list[UUID]|None=None
    extra: dict|None=None
class AccountBulk(Input):
    ids: list[UUID]=Field(min_length=1,max_length=100)
    changes: AccountPatch
class WorkerIn(Input):
    name: str=Field(min_length=1,max_length=160)
    max_parallel: int=Field(default=10,ge=1,le=100)
    capabilities: dict=Field(default_factory=dict)
class WorkerPatch(Input):
    enabled: bool|None=None
    max_parallel: int|None=Field(default=None,ge=1,le=100)
class DeviceIn(Input):
    worker_id: UUID
    external_id: str=Field(min_length=1,max_length=120)
    name: str=Field(min_length=1,max_length=160)
    enabled: bool=True
    capabilities: dict=Field(default_factory=dict)
class Heartbeat(Input):
    capabilities: dict=Field(default_factory=dict)
    devices: list[DeviceIn]=Field(default_factory=list,max_length=100)
class ClaimInput(Input):
    job_id: UUID|None=None
class LeaseEvent(Input):
    event_id: str=Field(min_length=8,max_length=100)
    lease_token: str=Field(min_length=20,max_length=200)
class Progress(LeaseEvent):
    phase: str=Field(min_length=1,max_length=80)
    detail: str=Field(default='',max_length=4000)
class Result(LeaseEvent):
    outcome: Literal['published','safe_failure','unknown']
    external_id: str|None=Field(default=None,min_length=1,max_length=200)
    url: str|None=Field(default=None,max_length=2000,pattern=r'^https://')
    published_at: datetime|None=None
    error_code: str|None=Field(default=None,max_length=80)
    error_detail: str|None=Field(default=None,max_length=4000)
    no_publish_attempted: bool=False
    @field_validator('published_at')
    @classmethod
    def aware(cls,v):
        if v and (not v.tzinfo or v.utcoffset() is None):raise ValueError('Timezone required')
        return v
    @model_validator(mode='after')
    def outcome_fields(self):
        if self.outcome=='published' and (not self.external_id or not self.published_at):
            raise ValueError('published requires external_id and published_at')
        if self.outcome=='safe_failure' and (not self.no_publish_attempted or not self.error_code):
            raise ValueError('safe_failure requires no_publish_attempted=true and error_code')
        if self.published_at and self.published_at>datetime.now(timezone.utc):
            raise ValueError('published_at cannot be in the future')
        return self
class JobIn(Input):
    clip_id: UUID
    account_id: UUID
    scheduled_at: datetime
    parameters: dict=Field(default_factory=dict)
    @field_validator('scheduled_at')
    @classmethod
    def aware(cls,v):
        if not v.tzinfo or v.utcoffset() is None:raise ValueError('Timezone required')
        return v
class JobAction(Input):
    action: Literal['cancel','pause','retry','reschedule','resolve_published','resolve_not_published']
    scheduled_at: datetime|None=None
    external_id: str|None=Field(default=None,max_length=200)
    url: str|None=Field(default=None,max_length=2000,pattern=r'^https://')
    published_at: datetime|None=None
    reason: str=Field(min_length=5,max_length=1000)
    @field_validator('scheduled_at','published_at')
    @classmethod
    def aware(cls,v):
        if v and (not v.tzinfo or v.utcoffset() is None):raise ValueError('Timezone required')
        return v
class SnapshotIn(Input):
    publication_id: UUID
    event_id: str=Field(min_length=8,max_length=100)
    measured_at: datetime
    views: int|None=Field(default=None,ge=0)
    likes: int|None=Field(default=None,ge=0)
    comments: int|None=Field(default=None,ge=0)
    shares: int|None=Field(default=None,ge=0)
    saves: int|None=Field(default=None,ge=0)
    follower_delta: int|None=None
    retention: float|None=Field(default=None,ge=0,le=1)
    @field_validator('measured_at')
    @classmethod
    def aware(cls,v):
        if not v.tzinfo or v.utcoffset() is None:raise ValueError('Timezone required')
        if v>datetime.now(timezone.utc):raise ValueError('Measurement cannot be in future')
        return v
class SchedulerControl(Input):
    enabled: bool
class GenerateIn(Input):
    local_date: date|None=None
    account_ids: list[UUID]|None=Field(default=None,max_length=100)
class TokenIn(Input):
    role: Literal['admin','farm']
    label: str=Field(min_length=1,max_length=160)

class DevicePatch(Input):
    name:str|None=Field(default=None,min_length=1,max_length=160)
    enabled:bool|None=None
    capabilities:dict|None=None
class SourcePatch(Input):
    title:str|None=Field(default=None,min_length=1,max_length=400)
    origin:str|None=Field(default=None,max_length=2000)
    category_id:UUID|None=None
    topic_id:UUID|None=None
    extra:dict|None=None

JobStatus=Literal['pending','scheduled','assigned','processing','published','failed','cancelled','retry_wait','unknown','paused']
class JobOut(BaseModel):
    id:UUID
    created_at:datetime
    clip_id:UUID
    account_id:UUID
    plan_id:UUID|None=None
    local_date:date
    slot:int
    scheduled_at:datetime
    status:JobStatus
    worker_id:UUID|None=None
    device_id:UUID|None=None
    lease_until:datetime|None=None
    processing_started_at:datetime|None=None
    attempts:int
    max_attempts:int
    parameters:dict
    result:dict
    updated_at:datetime
class WorkerAccountOut(BaseModel):
    id:UUID
    platform:Platform
    username:str
    timezone:str
class WorkerDeviceOut(BaseModel):
    id:UUID
    external_id:str
class WorkerContentOut(BaseModel):
    id:UUID
    passport:str
    title:str
    description:str
    sha256:str|None=None
    duration_seconds:float
class AssignmentOut(BaseModel):
    job:JobOut|None=None
    lease_token:str|None=None
    lease_expires_at:datetime|None=None
    account:WorkerAccountOut|None=None
    device:WorkerDeviceOut|None=None
    content:WorkerContentOut|None=None
class LeaseOut(BaseModel):
    job_id:UUID
    status:JobStatus
    lease_expires_at:datetime|None=None
class ResultOut(BaseModel):
    job_id:UUID
    status:JobStatus
    publication_id:UUID|None=None
class HeartbeatOut(BaseModel):
    worker_id:UUID
    server_time:datetime
    heartbeat_interval_seconds:int
    lease_seconds:int
class MediaTicketOut(BaseModel):
    url:str
    ticket:str
    expires_at_unix:int
    required_headers:list[str]
