from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from app.models import Event

def require(s,model,id,locked=False):
    q=select(model).where(model.id==id)
    if locked:q=q.with_for_update()
    item=s.scalar(q)
    if item is None:raise HTTPException(404,f'{model.__name__} not found')
    return item

def event(s,kind,key,detail=None,level='warning'):
    s.execute(insert(Event).values(kind=kind,dedup_key=key,detail=detail or {},level=level)
        .on_conflict_do_nothing(index_elements=['dedup_key']))
