"""Operational view and incident transitions. Never interpret old diagnostics as live health."""
from datetime import datetime, timedelta
from uuid import uuid4
from sqlalchemy import select, func
from app.db import now
from app.models import Account, Worker, Device, Clip, Job, Publication, Setting
from app.config import settings
from app.errors import event

LABELS = {
 'worker_offline': 'Нет связи с ПК', 'device_offline': 'Эмулятор недоступен',
 'identity_mismatch': 'Изменился эмулятор: нужна проверка привязки',
 'proxy_unavailable': 'Прокси не прошёл проверку', 'proxy_unverified': 'Нет свежей проверки прокси',
 'profile_missing': 'Не настроен сценарий публикации', 'profile_unverified': 'Сценарий публикации ещё не проверен',
 'publishing_disabled': 'Публикации на ПК выключены', 'worker_paused': 'Автопостинг на ПК приостановлен',
 'worker_not_running': 'Постоянный автопостинг на ПК не запущен',
 'account_paused': 'Аккаунт на паузе', 'account_attention': 'Аккаунту нужна проверка',
 'device_disabled': 'Эмулятор отключён', 'binding_missing': 'Аккаунт не привязан к эмулятору',
 'farm_offline': 'Нет связи с контентфермой', 'farm_errors': 'Есть незавершённые задания контентфермы',
 'drive_unavailable': 'Нет доступа к Google Диску', 'scheduler_offline': 'Планировщик не отвечает',
 'scheduler_paused': 'Планировщик на паузе', 'content_empty': 'Готовые ролики закончились',
 'publication_unknown': 'Нужно проверить результат публикации',
}

def fresh(value, seconds=120):
    try:
        stamp=datetime.fromisoformat(value)
        return timedelta(seconds=-30) <= now()-stamp <= timedelta(seconds=seconds)
    except (ValueError, TypeError):
        return False

def account_blockers(account, worker, device):
    reasons=[]
    if account.status!='active': reasons.append('account_paused' if account.status=='paused' else 'account_attention')
    if not worker or not device: return reasons+['binding_missing']
    if not worker.enabled or not worker.heartbeat_at or not fresh(worker.heartbeat_at.isoformat(), settings().heartbeat_timeout):
        reasons.append('worker_offline')
    runtime=worker.capabilities.get('runtime', {})
    if not runtime.get('running'): reasons.append('worker_not_running')
    if not runtime.get('allow_publish'): reasons.append('publishing_disabled')
    if runtime.get('paused'): reasons.append('worker_paused')
    if not device.enabled: reasons.append('device_disabled')
    health=device.capabilities.get('health', {})
    if not fresh(health.get('at')) or not health.get('online'): reasons.append('device_offline')
    elif not health.get('identity_verified'): reasons.append('identity_mismatch')
    profiles=health.get('profiles', {})
    if account.platform not in profiles: reasons.append('profile_missing')
    elif not profiles[account.platform].get('verified'): reasons.append('profile_unverified')
    network=health.get('network', {})
    if not fresh(network.get('at'),180): reasons.append('proxy_unverified')
    elif not network.get('ready'): reasons.append('proxy_unavailable')
    return reasons

def overview(s):
    workers={w.id:w for w in s.scalars(select(Worker))}
    devices={d.id:d for d in s.scalars(select(Device))}
    accounts=[]
    for account in s.scalars(select(Account).where(Account.status!='deleted').order_by(Account.created_at)):
        reasons=account_blockers(account,workers.get(account.worker_id),devices.get(account.device_id))
        accounts.append({'id':str(account.id),'username':account.username,'platform':account.platform,
          'status':account.status,'daily_limit':account.daily_limit,'timezone':account.timezone,
          'ready':not reasons,'reasons':reasons,'device':devices[account.device_id].name if account.device_id in devices else None})
    counts={str(status):count for status,count in s.execute(select(Job.status,func.count()).group_by(Job.status))}
    ready=s.scalar(select(func.count()).select_from(Clip).where(Clip.status=='ready',Clip.verified_at.is_not(None)))
    farm=s.get(Setting,'farm_heartbeat')
    farm_data=dict(farm.value) if farm else {}
    farm_data['online']=fresh(farm_data.get('at'),180)
    scheduler=s.get(Setting,'scheduler_heartbeat');control=s.get(Setting,'scheduler')
    scheduler_data={'enabled':control.value.get('enabled',True) if control else True,
      'online':fresh(scheduler.value.get('at')) if scheduler else False}
    problems=[]
    for code,condition in [('farm_offline',not farm_data['online']),
        ('farm_errors',farm_data.get('counts',{}).get('needs_retry',0)+farm_data.get('counts',{}).get('failed',0)>0),
        ('drive_unavailable',farm_data.get('drive',{}).get('ready') is False),
        ('scheduler_offline',not scheduler_data['online']),('scheduler_paused',not scheduler_data['enabled']),
        ('content_empty',not ready),('publication_unknown',counts.get('unknown',0)>0)]:
        if condition:problems.append({'code':code,'message':LABELS[code]})
    for account in accounts:
        for code in account['reasons']:
            problems.append({'code':code,'account':account['username'],'message':LABELS[code]})
    return {'at':now().isoformat(),'farm':farm_data,'scheduler':scheduler_data,'ready_clips':ready,
      'accounts':accounts,'workers':[{'id':str(w.id),'name':w.name,'online':bool(w.heartbeat_at and fresh(w.heartbeat_at.isoformat(),settings().heartbeat_timeout)),
        'running':bool(w.capabilities.get('runtime',{}).get('running'))} for w in workers.values()],
      'devices':[{'name':d.name,'external_id':d.external_id,'health':d.capabilities.get('health',{}),'fresh':fresh(d.capabilities.get('health',{}).get('at')),'network_fresh':fresh(d.capabilities.get('health',{}).get('network',{}).get('at'),180)} for d in devices.values()],
      'jobs':counts,'published_24h':s.scalar(select(func.count()).select_from(Publication).where(Publication.published_at>=now()-timedelta(hours=24))),
      'problems':problems,'labels':LABELS}

def monitor(s):
    """One alert per outage, one recovery. Repeated heartbeats do not create new incidents."""
    snapshot=overview(s)
    current={}
    for problem in snapshot['problems']:
        if problem['code'] in ('account_paused','publishing_disabled','worker_not_running','profile_missing','profile_unverified','scheduler_paused','proxy_unverified'):
            continue  # Setup state stays in the dashboard, not repeated notifications.
        key=problem['code']+':'+problem.get('account','system')
        current[key]=problem
    row=s.get(Setting,'operations_incidents')
    previous=row.value if row else {}
    for key,problem in current.items():
        if key not in previous:
            event(s,'system_problem','ops:'+uuid4().hex,problem)
    for key,problem in previous.items():
        if key not in current:event(s,'system_recovered','ops:'+uuid4().hex,problem,'info')
    if row:row.value=current
    else:s.add(Setting(key='operations_incidents',value=current))
