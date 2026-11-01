from sqlalchemy import inspect, select
from fastapi import HTTPException

def record(item):
    return {c.key:getattr(item,c.key) for c in inspect(item).mapper.column_attrs if c.key not in ('lease_hash','token_hash')}
def page(s,model,limit=50,offset=0,conditions=()):
    q=select(model).where(*conditions).order_by(model.created_at.desc(),model.id.desc())
    return {'items':[record(x) for x in s.scalars(q.limit(limit).offset(offset))],
        'limit':limit,'offset':offset}
