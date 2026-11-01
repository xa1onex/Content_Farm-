from sqlalchemy import select, func, extract, and_
from app.models import Publication, Snapshot, Account, Clip

def aggregate(s,group,start,end):
    latest=(select(Snapshot,func.row_number().over(partition_by=Snapshot.publication_id,
        order_by=Snapshot.measured_at.desc()).label('rn')).where(Snapshot.measured_at<=end).subquery())
    mapping={'account':Account.id,'platform':Account.platform,'category':Clip.category_id,
        'format':Clip.format_id,'source':Clip.source_id,'clip':Clip.id,
        'hour':extract('hour',func.timezone(Account.timezone,Publication.published_at)),
        'day':func.date_trunc('day',Publication.published_at),
        'week':func.date_trunc('week',Publication.published_at),
        'month':func.date_trunc('month',Publication.published_at)}
    key=mapping[group]
    metrics=['views','likes','comments','shares','saves','follower_delta']
    query=(select(key.label('group'),func.count(Publication.id).label('publications'),
        func.count(latest.c.id).label('measured_publications'),
        *[func.sum(getattr(latest.c,m)).label(m) for m in metrics],
        func.avg(latest.c.retention).label('retention'))
        .select_from(Publication).join(Account,Account.id==Publication.account_id)
        .join(Clip,Clip.id==Publication.clip_id)
        .outerjoin(latest,and_(latest.c.publication_id==Publication.id,latest.c.rn==1))
        .where(Publication.published_at>=start,Publication.published_at<end)
        .group_by(key).order_by(key))
    return {'group_by':group,'start':start,'end':end,'items':[dict(r._mapping) for r in s.execute(query)],
        'method':'Latest snapshot per publication at or before end; missing metrics remain null; day/week/month UTC, hour in account timezone'}
