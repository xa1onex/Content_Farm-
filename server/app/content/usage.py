from sqlalchemy import exists,or_,select
from app.models import Clip,ClipPlatformUse,Job

def used_on_platform(platform):
    return exists(select(1).where(ClipPlatformUse.platform==platform,
        or_(ClipPlatformUse.clip_id==Clip.id,ClipPlatformUse.sha256==Clip.sha256))).correlate(Clip,Job)
