import uuid
from datetime import datetime, date
from sqlalchemy import (String, Text, Integer, BigInteger, Float, Boolean, DateTime,
    Date, ForeignKey, ForeignKeyConstraint, UniqueConstraint, CheckConstraint, Index, Sequence, text)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base, now

class Identity:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)

class Category(Identity, Base):
    __tablename__='categories'
    code: Mapped[str] = mapped_column(String(40),unique=True)
    name: Mapped[str] = mapped_column(String(120))
class Topic(Identity, Base):
    __tablename__='topics'
    code: Mapped[str] = mapped_column(String(40),unique=True)
    name: Mapped[str] = mapped_column(String(120))
class Format(Identity, Base):
    __tablename__='formats'
    code: Mapped[str] = mapped_column(String(40),unique=True)
    name: Mapped[str] = mapped_column(String(120))
class Source(Identity, Base):
    __tablename__='sources'
    external_id: Mapped[str] = mapped_column(String(180),unique=True)
    title: Mapped[str] = mapped_column(String(400))
    origin: Mapped[str] = mapped_column(Text,default='')
    category_id: Mapped[uuid.UUID|None] = mapped_column(ForeignKey('categories.id'))
    topic_id: Mapped[uuid.UUID|None] = mapped_column(ForeignKey('topics.id'))
    extra: Mapped[dict] = mapped_column(JSONB,default=dict)

clip_sequence = Sequence('clip_passport_seq')
class Clip(Identity, Base):
    __tablename__='clips'
    passport: Mapped[str] = mapped_column(String(120),unique=True)
    source_id: Mapped[uuid.UUID|None] = mapped_column(ForeignKey('sources.id'),index=True)
    drive_file_id: Mapped[str] = mapped_column(String(200),unique=True)
    sha256: Mapped[str|None] = mapped_column(String(64),unique=True)
    title: Mapped[str] = mapped_column(String(400))
    description: Mapped[str] = mapped_column(Text,default='')
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('categories.id'),index=True)
    topic_id: Mapped[uuid.UUID|None] = mapped_column(ForeignKey('topics.id'),index=True)
    format_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('formats.id'),index=True)
    duration_seconds: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20),default='draft',index=True)
    platforms: Mapped[list] = mapped_column(JSONB,default=lambda:['tiktok','instagram','youtube'])
    extra: Mapped[dict] = mapped_column(JSONB,default=dict)
    technical: Mapped[dict] = mapped_column(JSONB,default=dict)
    verified_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True))
    __table_args__=(CheckConstraint("status IN ('draft','ready','unavailable','archived')"),
        CheckConstraint('duration_seconds>0'),CheckConstraint("sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'"))

class ClipPlatformUse(Base):
    """Permanent tombstone; deliberately independent of deletable history."""
    __tablename__='clip_platform_uses'
    clip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),primary_key=True)
    platform: Mapped[str] = mapped_column(String(20),primary_key=True)
    sha256: Mapped[str|None] = mapped_column(String(64))
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),nullable=False)
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,server_default=text('now()'))
    __table_args__=(UniqueConstraint('sha256','platform',name='uq_used_hash_platform'),)

class Worker(Identity, Base):
    __tablename__='workers'
    name: Mapped[str] = mapped_column(String(160),unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean,default=True)
    max_parallel: Mapped[int] = mapped_column(Integer,default=10)
    heartbeat_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True))
    capabilities: Mapped[dict] = mapped_column(JSONB,default=dict)
    __table_args__=(CheckConstraint('max_parallel BETWEEN 1 AND 100'),)
class Device(Identity, Base):
    __tablename__='devices'
    worker_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workers.id'),index=True)
    external_id: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(160))
    enabled: Mapped[bool] = mapped_column(Boolean,default=True)
    capabilities: Mapped[dict] = mapped_column(JSONB,default=dict)
    __table_args__=(UniqueConstraint('worker_id','external_id'),UniqueConstraint('id','worker_id'))
class Account(Identity, Base):
    __tablename__='accounts'
    platform: Mapped[str] = mapped_column(String(20),index=True)
    name: Mapped[str] = mapped_column(String(160))
    username: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20),default='paused',index=True)
    worker_id: Mapped[uuid.UUID|None] = mapped_column(ForeignKey('workers.id'),index=True)
    device_id: Mapped[uuid.UUID|None] = mapped_column(ForeignKey('devices.id'),index=True)
    timezone: Mapped[str] = mapped_column(String(80),default='Europe/Moscow')
    daily_limit: Mapped[int] = mapped_column(Integer,default=3)
    warmup_enabled: Mapped[bool] = mapped_column(Boolean,default=True,server_default=text('true'))
    operational_started_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True))
    windows: Mapped[list] = mapped_column(JSONB,default=lambda:[{'start':'08:00','end':'20:00'}])
    min_interval_minutes: Mapped[int] = mapped_column(Integer,default=60)
    jitter_minutes: Mapped[int] = mapped_column(Integer,default=20)
    adaptive: Mapped[bool] = mapped_column(Boolean,default=False)
    last_published_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True))
    extra: Mapped[dict] = mapped_column(JSONB,default=dict)
    __table_args__=(UniqueConstraint('platform','username'),
        UniqueConstraint('id','platform',name='uq_account_platform_identity'),
        ForeignKeyConstraint(['device_id','worker_id'],['devices.id','devices.worker_id']),
        CheckConstraint('device_id IS NULL OR worker_id IS NOT NULL'),
        CheckConstraint("platform IN ('tiktok','instagram','youtube')"),
        CheckConstraint("status IN ('active','paused','attention','deleted')"),
        CheckConstraint('daily_limit BETWEEN 0 AND 50'),
        CheckConstraint('min_interval_minutes>=1'),
        Index('one_platform_per_device','device_id','platform',unique=True,
            postgresql_where=text("status <> 'deleted' AND device_id IS NOT NULL")))
class AccountCategory(Base):
    __tablename__='account_categories'
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('accounts.id'),primary_key=True)
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('categories.id'),primary_key=True)
class AccountTopic(Base):
    __tablename__='account_topics'
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('accounts.id'),primary_key=True)
    topic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('topics.id'),primary_key=True)
class AccountFormat(Base):
    __tablename__='account_formats'
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('accounts.id'),primary_key=True)
    format_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('formats.id'),primary_key=True)

class SchedulePlan(Identity, Base):
    __tablename__='schedule_plans'
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('accounts.id'),index=True)
    local_date: Mapped[date] = mapped_column(Date)
    config: Mapped[dict] = mapped_column(JSONB)
    __table_args__=(UniqueConstraint('account_id','local_date'),)
class Job(Identity, Base):
    __tablename__='jobs'
    platform: Mapped[str] = mapped_column(String(20))
    clip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('clips.id'),index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('accounts.id'),index=True)
    plan_id: Mapped[uuid.UUID|None] = mapped_column(ForeignKey('schedule_plans.id'))
    local_date: Mapped[date] = mapped_column(Date)
    slot: Mapped[int] = mapped_column(Integer)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),index=True)
    status: Mapped[str] = mapped_column(String(20),default='scheduled',index=True)
    worker_id: Mapped[uuid.UUID|None] = mapped_column(ForeignKey('workers.id'))
    device_id: Mapped[uuid.UUID|None] = mapped_column(ForeignKey('devices.id'))
    lease_hash: Mapped[str|None] = mapped_column(String(64))
    lease_until: Mapped[datetime|None] = mapped_column(DateTime(timezone=True),index=True)
    processing_started_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer,default=0)
    max_attempts: Mapped[int] = mapped_column(Integer,default=3)
    parameters: Mapped[dict] = mapped_column(JSONB,default=dict)
    result: Mapped[dict] = mapped_column(JSONB,default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now,onupdate=now)
    __table_args__=(UniqueConstraint('account_id','clip_id',name='uq_no_repeat_clip_account'),
        UniqueConstraint('clip_id','platform',name='uq_clip_platform_once'),
        ForeignKeyConstraint(['account_id','platform'],['accounts.id','accounts.platform'],name='fk_job_account_platform'),
        UniqueConstraint('id','account_id','clip_id',name='uq_job_publication_identity'),
        UniqueConstraint('account_id','local_date','slot',name='uq_account_daily_slot'),
        CheckConstraint("status IN ('pending','scheduled','assigned','processing','published','failed','cancelled','retry_wait','unknown','paused')"),
        Index('queue_due','status','scheduled_at'),
        Index('busy_device','device_id',unique=True,
            postgresql_where=text("status IN ('assigned','processing','unknown') AND device_id IS NOT NULL")))
class Attempt(Identity, Base):
    __tablename__='attempts'
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('jobs.id'),index=True)
    number: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workers.id'))
    status: Mapped[str] = mapped_column(String(30),default='assigned')
    finished_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str|None] = mapped_column(String(80))
    error_detail: Mapped[str|None] = mapped_column(Text)
    __table_args__=(UniqueConstraint('job_id','number'),)
class JobEvent(Identity, Base):
    __tablename__='job_events'
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('jobs.id'),index=True)
    worker_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workers.id'))
    event_id: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONB,default=dict)
    __table_args__=(UniqueConstraint('worker_id','event_id'),)
class Publication(Identity, Base):
    __tablename__='publications'
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('jobs.id'),unique=True)
    clip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('clips.id'),index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('accounts.id'),index=True)
    external_id: Mapped[str] = mapped_column(String(200))
    url: Mapped[str|None] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),index=True)
    __table_args__=(UniqueConstraint('account_id','clip_id'),UniqueConstraint('account_id','external_id'),
        ForeignKeyConstraint(['job_id','account_id','clip_id'],['jobs.id','jobs.account_id','jobs.clip_id']))
class Snapshot(Identity, Base):
    __tablename__='snapshots'
    publication_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('publications.id'),index=True)
    event_id: Mapped[str] = mapped_column(String(100),unique=True)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),index=True)
    views: Mapped[int|None] = mapped_column(BigInteger)
    likes: Mapped[int|None] = mapped_column(BigInteger)
    comments: Mapped[int|None] = mapped_column(BigInteger)
    shares: Mapped[int|None] = mapped_column(BigInteger)
    saves: Mapped[int|None] = mapped_column(BigInteger)
    follower_delta: Mapped[int|None] = mapped_column(BigInteger)
    retention: Mapped[float|None] = mapped_column(Float)
    __table_args__=(UniqueConstraint('publication_id','measured_at'),
        CheckConstraint('views>=0 AND likes>=0 AND comments>=0 AND shares>=0 AND saves>=0'),
        CheckConstraint('retention BETWEEN 0 AND 1'))
class ApiToken(Identity, Base):
    __tablename__='api_tokens'
    token_hash: Mapped[str] = mapped_column(String(64),unique=True,index=True)
    role: Mapped[str] = mapped_column(String(20))
    label: Mapped[str] = mapped_column(String(160))
    worker_id: Mapped[uuid.UUID|None] = mapped_column(ForeignKey('workers.id'))
    enabled: Mapped[bool] = mapped_column(Boolean,default=True)
    __table_args__=(CheckConstraint("role IN ('admin','farm','worker')"),)
class Idempotency(Base):
    __tablename__='idempotency'
    scope: Mapped[str] = mapped_column(String(250),primary_key=True)
    key: Mapped[str] = mapped_column(String(100),primary_key=True)
    digest: Mapped[str] = mapped_column(String(64))
    response: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now)
class RateBucket(Base):
    __tablename__='rate_buckets'
    key: Mapped[str] = mapped_column(String(100),primary_key=True)
    minute: Mapped[int] = mapped_column(BigInteger,primary_key=True)
    count: Mapped[int] = mapped_column(Integer,default=0)
class Setting(Base):
    __tablename__='settings'
    key: Mapped[str] = mapped_column(String(100),primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)
class Event(Identity, Base):
    __tablename__='events'
    level: Mapped[str] = mapped_column(String(20),default='warning')
    kind: Mapped[str] = mapped_column(String(80),index=True)
    dedup_key: Mapped[str] = mapped_column(String(200),unique=True)
    detail: Mapped[dict] = mapped_column(JSONB,default=dict)
    notified_at: Mapped[datetime|None] = mapped_column(DateTime(timezone=True))
class Audit(Identity, Base):
    __tablename__='audit'
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(250))
    detail: Mapped[dict] = mapped_column(JSONB,default=dict)

class TelegramSubscriber(Identity, Base):
    __tablename__='telegram_subscribers'
    chat_id: Mapped[int] = mapped_column(BigInteger,unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean,default=True)
class NotificationDelivery(Base):
    __tablename__='notification_deliveries'
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('events.id'),primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger,primary_key=True)
    status: Mapped[str] = mapped_column(String(20),default='pending')
    attempts: Mapped[int] = mapped_column(Integer,default=0)
    next_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now,index=True)
