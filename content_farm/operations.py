"""Non-blocking reporting and bounded recovery of completed media."""
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
import shutil
import time
from config import JOB_DIR, AUTOPOST_ENABLED

RETRY_DELAYS=(60,180,600,1800,3600)

def recovery_changes(state, timestamp=None):
    count=state.get('pipeline_retry_count',0)+1
    if count>len(RETRY_DELAYS):
        return {'status':'needs_retry','pipeline_retry_count':count,'stage':'Нужна проверка сохранения или доставки'}
    return {'status':'retry_wait','pipeline_retry_count':count,
      'retry_at':(timestamp if timestamp is not None else time.time())+RETRY_DELAYS[count-1],
      'stage':'Ожидаю автоматического повтора сохранения или доставки'}

def snapshot():
    counts=Counter();active=[]
    for path in JOB_DIR.glob('*/state.json'):
        try:state=json.loads(path.read_text())
        except (OSError,ValueError):continue
        if state.get('status')=='test' or state.get('id','').startswith('smoke-'):continue
        counts[state['status']]+=1
        if state['status'] in ('queued','processing','retry_wait','interrupted'):
            active.append({'id':state['id'],'stage':state.get('stage','Обработка видео')})
    return {'counts':dict(counts),'active':active[:20],'disk_free_gb':round(shutil.disk_usage(JOB_DIR).free/1024**3,2)}

def drive_check():
    from drive_helper import DriveStorage
    try:
        storage=DriveStorage();storage.check_writable()
        return {'ready':True,'at':datetime.now(timezone.utc).isoformat()}
    except Exception as exc:
        return {'ready':False,'code':type(exc).__name__,'at':datetime.now(timezone.utc).isoformat()}

def send_report(payload):
    import uuid
    from autopost import Client
    client=Client()
    try:
        response=client.session.post(client.base+'/farm/heartbeat',json=payload,
          headers={'Idempotency-Key':uuid.uuid4().hex},timeout=(5,15))
        response.raise_for_status()
    finally:client.close()

async def report_loop():
    if not AUTOPOST_ENABLED:return
    drive={};last_check=0
    while True:
        try:
            if time.monotonic()-last_check>300:
                # A slow Drive operation must not suppress the farm liveness signal.
                if 'check' not in locals() or check.done():
                    check=asyncio.create_task(asyncio.to_thread(drive_check));last_check=time.monotonic()
            if 'check' in locals() and check.done():drive=check.result()
            payload=await asyncio.to_thread(snapshot);payload['drive']=drive
            await asyncio.to_thread(send_report,payload)
        except Exception:
            import logging
            logging.getLogger(__name__).warning('operations_report_failed')
        await asyncio.sleep(30)
