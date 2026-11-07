"""Durable, user-reviewed YouTube discovery. Accepting only queues source analysis."""
import re
import uuid
from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit, parse_qs
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from app.db import db_session, now
from app.models import Setting, Source, Clip
from app.security import roles, idempotent, lock
from app.editorial import uid, queue_requests

router = APIRouter()
DB = Depends(db_session, scope='function')
ADMIN = Depends(roles('admin'))
FARM = Depends(roles('farm', 'admin'))
ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
CAPACITY = 24


def video_id(url):
    try:
        u = urlsplit(url or '')
        if u.scheme != 'https' or u.username or u.password or u.port not in (None, 443): return None
        if u.hostname == 'youtu.be': value = u.path.strip('/').split('/')[0]
        elif u.hostname in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
            value = parse_qs(u.query).get('v', [''])[0] if u.path == '/watch' else u.path.split('/')[2] if u.path.startswith(('/shorts/', '/live/')) else ''
        else: return None
        return value if ID.fullmatch(value) else None
    except (ValueError, IndexError, TypeError): return None


def rows(s, prefix):
    return list(s.scalars(select(Setting).where(Setting.key.startswith(prefix))))


def save(s, key, value):
    row = s.get(Setting, key)
    if row: row.value = value
    else: s.add(Setting(key=key, value=value))
    s.flush()
    return value


def config(s, user):
    row = s.get(Setting, f'source_discovery:{user}')
    return dict(row.value) if row else {'enabled': True, 'interval_hours': 6, 'preferences': '', 'status': 'waiting'}


def picks(s, user):
    return [dict(r.value) for r in rows(s, f'source_pick:{user}:')]


def farm_jobs(s):
    heartbeat = s.get(Setting, 'farm_heartbeat')
    return heartbeat.value.get('jobs', []) if heartbeat else []


def seeds(s, user):
    jobs = [j for j in farm_jobs(s) if j.get('user_id') == user and video_id(j.get('url'))]
    for r in rows(s, 'miniapp_farm:'):
        v = r.value
        if v.get('user_id') == user and video_id(v.get('url')):
            jobs.append({'url': v['url'], 'title': v.get('title', ''), 'created': v.get('created_at', '')})
    # Prefer the rich completed source over a later bare queue receipt.
    result = {}
    for j in sorted(jobs, key=lambda j: j.get('created', ''), reverse=True):
        key = video_id(j['url'])
        if key not in result or not result[key]['title']:
            result[key] = {'id': key, 'title': str(j.get('title') or '')[:300], 'url': 'https://www.youtube.com/watch?v=' + key}
    return list(result.values())[:30]


def excluded(s):
    ids = {video_id(v) for v in s.scalars(select(Source.origin))}
    ids.update(video_id(j.get('url')) for j in farm_jobs(s))
    ids.update(video_id(r.value.get('url')) for r in rows(s, 'miniapp_farm:'))
    ids.update(r.key.split(':')[-1] for r in rows(s, 'source_taken:'))
    return sorted(i for i in ids if i)


def snapshot(s, user):
    if not user: return None
    cfg = config(s, user)
    items = sorted(picks(s, user), key=lambda x: x['found_at'], reverse=True)
    cfg['has_seeds'] = bool(seeds(s, user))
    cfg['counts'] = {status: sum(v['status'] == status for v in items) for status in ('pending', 'accepted', 'rejected')}
    cfg['items'] = items[:200]
    cfg['history_limited'] = len(items) > 200
    cfg.pop('run_id', None)
    cfg.pop('lease_until', None)
    return cfg


class Preferences(BaseModel):
    enabled: bool = True
    interval_hours: Literal[6, 12, 24] = 6
    preferences: str = Field(default='', max_length=1000)


@router.post('/api/v1/miniapp/discovery/settings')
@idempotent
def settings(data: Preferences, request: Request, s=DB, actor=ADMIN):
    user = uid(actor); lock(s, f'discovery:{user}')
    cfg = config(s, user); changed = cfg.get('preferences') != data.preferences
    cfg.update(data.model_dump())
    if changed or not cfg.get('next_at'): cfg['requested_at'] = now().isoformat()
    return save(s, f'source_discovery:{user}', cfg)


@router.post('/api/v1/miniapp/discovery/refresh')
@idempotent
def refresh(request: Request, s=DB, actor=ADMIN):
    user = uid(actor); lock(s, f'discovery:{user}'); cfg = config(s, user)
    if not seeds(s, user): raise HTTPException(409, 'Добавь хотя бы один исходник — по нему определим тематику')
    if not cfg['enabled']: raise HTTPException(409, 'Сначала включи автоматический подбор')
    if cfg.get('status') == 'running' and (cfg.get('lease_until') or '') > now().isoformat(): return {'status': 'running'}
    if sum(v['status'] == 'pending' for v in picks(s, user)) >= CAPACITY: raise HTTPException(409, 'Сначала разбери предложенные исходники: на проверке уже 24 видео')
    if cfg.get('requested_at') and (now() - datetime.fromisoformat(cfg['requested_at'])).total_seconds() < 180:
        if cfg['requested_at'] > cfg.get('last_started_at', ''): return {'status': 'queued'}
        raise HTTPException(429, 'Повторный поиск можно запускать раз в 3 минуты', headers={'Retry-After': '180'})
    cfg.update(requested_at=now().isoformat(), status='queued')
    save(s, f'source_discovery:{user}', cfg)
    return {'status': 'queued'}


class Decision(BaseModel):
    decision: Literal['accepted', 'rejected']
    comment: str | None = Field(default=None, max_length=1000)


def own_pick(s, user, identifier):
    if not ID.fullmatch(identifier): raise HTTPException(404, 'Исходник не найден')
    row = s.get(Setting, f'source_pick:{user}:{identifier}')
    if not row: raise HTTPException(404, 'Исходник не найден')
    return dict(row.value)


@router.post('/api/v1/miniapp/discovery/{identifier}/decision')
@idempotent
def decide(identifier: str, data: Decision, request: Request, s=DB, actor=ADMIN):
    user = uid(actor); lock(s, f'discovery:{user}'); lock(s, f'source-take:{identifier}')
    value = own_pick(s, user, identifier)
    if value['status'] != 'pending':
        if value['status'] == data.decision: return value
        raise HTTPException(409, 'Решение уже принято. Повторно запускать исходник нельзя.')
    if data.decision == 'accepted':
        lock(s, f'miniapp-farm:{user}')
        if identifier in excluded(s): raise HTTPException(409, 'Этот исходник уже был взят в работу. Повтор не запущен.')
        queued = queue_requests(s, user, [value['url']], mode='analyze')[0]
        value['request_id'] = queued['id']
        save(s, f'source_taken:{identifier}', {'user_id': user, 'request_id': queued['id'], 'at': now().isoformat()})
    if data.comment is not None: value['comment'] = data.comment
    value.update(status=data.decision, decided_at=now().isoformat())
    return save(s, f'source_pick:{user}:{identifier}', value)


class Comment(BaseModel):
    comment: str = Field(max_length=1000)


@router.post('/api/v1/miniapp/discovery/{identifier}/feedback')
@idempotent
def feedback(identifier: str, data: Comment, request: Request, s=DB, actor=ADMIN):
    user = uid(actor); lock(s, f'discovery:{user}'); value = own_pick(s, user, identifier)
    value.update(comment=data.comment, commented_at=now().isoformat())
    return save(s, f'source_pick:{user}:{identifier}', value)


def owners(s):
    users = {j.get('user_id') for j in farm_jobs(s)}
    users.update(r.value.get('user_id') for r in rows(s, 'miniapp_farm:'))
    users.update(int(r.key.split(':')[1]) for r in rows(s, 'source_discovery:'))
    return sorted(u for u in users if isinstance(u, int) and u > 0)


@router.get('/api/v1/farm/discovery')
def tasks(s=DB, actor=FARM):
    return {'at': now().isoformat(), 'items': [{'user_id': u, 'pending': sum(v['status']=='pending' for v in picks(s,u)), **{k: v for k, v in config(s, u).items() if k in ('enabled', 'status', 'next_at', 'requested_at', 'last_started_at', 'lease_until')}} for u in owners(s) if seeds(s, u)]}


@router.post('/api/v1/farm/discovery/{user}/start')
@idempotent
def start(user: int, request: Request, s=DB, actor=FARM):
    lock(s, f'discovery:{user}'); cfg = config(s, user); source_seeds = seeds(s, user); stamp = now().isoformat()
    if not cfg['enabled'] or not source_seeds: return {'started': False}
    if (cfg.get('lease_until') or '') > stamp: return {'started': False}
    manual = cfg.get('requested_at', '') > cfg.get('last_started_at', '')
    if not manual and cfg.get('next_at', '') > stamp: return {'started': False}
    existing = picks(s, user)
    if sum(v['status'] == 'pending' for v in existing) >= CAPACITY: return {'started': False, 'reason': 'review_full'}
    cfg.update(status='running', run_id=str(uuid.uuid4()), last_started_at=stamp, lease_until=(now()+timedelta(minutes=20)).isoformat(), last_error=None)
    save(s, f'source_discovery:{user}', cfg)
    memory = [{'title': v['title'], 'status': v['status'], 'comment': v.get('comment', '')} for v in sorted(existing, key=lambda v: v.get('commented_at', v.get('decided_at', v['found_at'])), reverse=True) if v['status'] != 'pending' or v.get('comment')][:40]
    clip_memory = []
    for r in sorted(rows(s, 'editorial_feedback:'), key=lambda r: r.value.get('at', ''), reverse=True):
        if r.value.get('user_id') != user: continue
        clip = s.get(Clip, uuid.UUID(r.value['clip_id']))
        if clip:
            transcript = (clip.extra or {}).get('transcript') or {}
            clip_memory.append({'title': clip.title, 'rating': r.value.get('rating'), 'comment': r.value.get('comment', ''), 'speech': str(transcript.get('text', '') if isinstance(transcript, dict) else transcript)[:700]})
        if len(clip_memory) >= 15: break
    return {'started': True, 'run_id': cfg['run_id'], 'preferences': cfg['preferences'], 'seeds': source_seeds, 'source_feedback': memory, 'clip_feedback': clip_memory, 'excluded_ids': sorted(set(excluded(s)) | {v['id'] for v in existing}), 'limit': min(8, CAPACITY-sum(v['status']=='pending' for v in existing))}


class Candidate(BaseModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{11}$')
    title: str = Field(min_length=1, max_length=400)
    channel: str = Field(default='', max_length=200)
    duration: int = Field(ge=180, le=21600)
    views: int | None = Field(default=None, ge=0)
    reason: str = Field(min_length=1, max_length=1000)
    score: int = Field(ge=0, le=100)


class Result(BaseModel):
    run_id: uuid.UUID
    profile: str = Field(default='', max_length=1500)
    queries: list[str] = Field(default_factory=list, max_length=3)
    searched_ids: list[str] = Field(default_factory=list, max_length=40)
    candidates: list[Candidate] = Field(default_factory=list, max_length=8)
    error: Literal['search_unavailable', 'analysis_unavailable', 'service_error'] | None = None


@router.post('/api/v1/farm/discovery/{user}/result')
@idempotent
def result(user: int, data: Result, request: Request, s=DB, actor=FARM):
    lock(s, f'discovery:{user}'); cfg = config(s, user)
    if cfg.get('run_id') != str(data.run_id) or cfg.get('status') != 'running': raise HTTPException(409, 'Подбор уже завершён или заменён новым запуском')
    blocked = set(excluded(s)); existing = picks(s, user)
    slots = min(8, CAPACITY-sum(v['status']=='pending' for v in existing)); added = 0
    if not data.error:
        for candidate in data.candidates:
            key = f'source_pick:{user}:{candidate.id}'
            if added >= slots: break
            if candidate.id in blocked or candidate.id not in data.searched_ids or s.get(Setting, key): continue
            save(s, key, {**candidate.model_dump(), 'url': 'https://www.youtube.com/watch?v='+candidate.id, 'status': 'pending', 'comment': '', 'found_at': now().isoformat()}); added += 1
    cfg.update(status='error' if data.error else 'completed', last_error=data.error, last_completed_at=now().isoformat(), next_at=(now()+timedelta(minutes=30) if data.error else now()+timedelta(hours=cfg['interval_hours'])).isoformat(), lease_until=None, last_added=added)
    if not data.error: cfg.update(profile=data.profile, queries=[q[:180] for q in data.queries])
    save(s, f'source_discovery:{user}', cfg)
    return {'added': added, 'status': cfg['status']}
