from sqlalchemy import select,delete
from fastapi import HTTPException
from app.models import Account,Device,AccountCategory,AccountTopic,AccountFormat,Job
from app.schemas import AccountIn
from app.errors import require
from app.api.common import record

relations={'category_ids':(AccountCategory,'category_id'),'topic_ids':(AccountTopic,'topic_id'),
           'format_ids':(AccountFormat,'format_id')}
def account_record(s,a):
    result=record(a)
    from app.scheduler.service import daily_quota,warmup_day
    from app.db import now
    from zoneinfo import ZoneInfo
    day=now().astimezone(ZoneInfo(a.timezone)).date()
    result['effective_daily_limit']=daily_quota(a,day)
    result['warmup_day']=warmup_day(a,day)
    for name,(model,field) in relations.items():
        result[name]=list(s.scalars(select(getattr(model,field)).where(model.account_id==a.id)))
    return result

def save_account(s,data,account=None):
    values=data.model_dump()
    lists={name:values.pop(name) for name in relations}
    if values.get('device_id'):
        device=require(s,Device,values['device_id'])
        if device.worker_id!=values['worker_id']:
            raise HTTPException(422,'Device belongs to another worker')
    if account:
        busy=s.scalar(select(Job.id).where(Job.account_id==account.id,
            Job.status.in_(['assigned','processing','unknown'])).limit(1))
        if busy and any(getattr(account,f)!=values[f] for f in ('worker_id','device_id','timezone')):
            raise HTTPException(409,'Cannot move account/timezone while execution or reconciliation is pending')
        for key,value in values.items():setattr(account,key,value)
    else:
        account=Account(**values);s.add(account);s.flush()
    for name,(model,field) in relations.items():
        s.execute(delete(model).where(model.account_id==account.id))
        for value in set(lists[name]):s.add(model(account_id=account.id,**{field:value}))
    s.flush()
    return account

def patch_account(s,account,patch):
    full=account_record(s,account)
    full={k:v for k,v in full.items() if k in AccountIn.model_fields}
    updates=patch.model_dump(exclude_unset=True)
    full.update(updates)
    # deleted is a soft deletion; keep historical relations intact.
    deleted=full['status']=='deleted'
    if deleted:full['status']='paused'
    data=AccountIn.model_validate(full)
    result=save_account(s,data,account)
    if deleted:result.status='deleted'
    return result
