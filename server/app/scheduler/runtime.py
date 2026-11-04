import logging,time,signal,json
from datetime import timedelta
from sqlalchemy import select,delete
from app.db import transaction,now
from app.models import Worker,Setting,RateBucket,Idempotency,Job
from app.errors import event
from app.scheduler.service import generate
from app.dispatcher.service import reap
from app.security import lock
from app.config import settings
log=logging.getLogger('scheduler')
logging.basicConfig(level=logging.INFO,format='%(message)s')
running=True

def stop(*_):
    global running
    running=False
signal.signal(signal.SIGTERM,stop)
signal.signal(signal.SIGINT,stop)

def tick():
    with transaction() as s:
        lock(s,'scheduler_tick')
        expired=reap(s)
        from app.scheduler.service import start_ready_accounts
        start_ready_accounts(s)
        first=generate(s)
        # Plan next day per-account timezone, avoiding a single server timezone boundary.
        from zoneinfo import ZoneInfo
        from app.models import Account
        created=first['created']
        for account in s.scalars(select(Account).where(Account.status=='active')):
            tomorrow=now().astimezone(ZoneInfo(account.timezone)).date()+timedelta(days=1)
            created+=generate(s,tomorrow,[account.id])['created']
        heartbeat=s.get(Setting,'scheduler_heartbeat')
        value={'at':now().isoformat(),'expired_leases':expired,'created_jobs':created}
        if heartbeat:heartbeat.value=value
        else:s.add(Setting(key='scheduler_heartbeat',value=value))
        from app.operations import monitor
        s.flush()
        monitor(s)
        # Idempotency records retained 30 days; job/event identities remain permanent.
        s.execute(delete(RateBucket).where(RateBucket.minute<int(time.time()//60)-5))
        s.execute(delete(Idempotency).where(Idempotency.created_at<now()-timedelta(days=30)))
        return value

def main():
    while running:
        try:log.info(json.dumps({'component':'scheduler',**tick()}))
        except Exception as exc:
            log.error(json.dumps({'component':'scheduler','error':type(exc).__name__}))
            try:
                with transaction() as s:event(s,'scheduler_error',f'scheduler:{int(time.time()//300)}',{'error':type(exc).__name__},'critical')
            except Exception:pass
        for _ in range(30):
            if not running:break
            time.sleep(1)
if __name__=='__main__':main()
