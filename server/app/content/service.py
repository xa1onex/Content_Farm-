from sqlalchemy import select
from fastapi import HTTPException
from app.models import Clip, Category, Format, clip_sequence
from app.errors import require

def register(s,data):
    category=require(s,Category,data.category_id)
    fmt=require(s,Format,data.format_id)
    values=data.model_dump()
    if not data.passport:
        while True:
            number=s.scalar(clip_sequence.next_value())
            candidate=f'{category.code}-{fmt.code}-{number:06d}'
            if not s.scalar(select(Clip.id).where(Clip.passport==candidate)):
                values['passport']=candidate;break
    clip=Clip(**values,status='draft')
    s.add(clip);s.flush()
    return clip

def ready(s,clip):
    from app.content.moderation import require_allowed
    require_allowed(s,clip.id)
    if clip.status!='ready' or clip.verified_at is None:
        raise HTTPException(409,'Clip must be verified in Google Drive and ready')
