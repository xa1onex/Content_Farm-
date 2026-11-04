import asyncio,logging
from datetime import timedelta
from sqlalchemy import select
from aiogram.exceptions import TelegramAPIError
from app.db import transaction,now
from app.models import Event,TelegramSubscriber,NotificationDelivery
from sqlalchemy.dialects.postgresql import insert
log=logging.getLogger(__name__)

def enqueue():
    with transaction() as s:
        events=list(s.scalars(select(Event).where(Event.created_at>now()-timedelta(days=1))))
        subscribers=list(s.scalars(select(TelegramSubscriber).where(TelegramSubscriber.enabled.is_(True))))
        for event in events:
            for subscriber in subscribers:
                if event.created_at<subscriber.created_at:continue
                s.execute(insert(NotificationDelivery).values(event_id=event.id,chat_id=subscriber.chat_id)
                    .on_conflict_do_nothing())



def notification_text(ev):
    import html
    from app.operations import LABELS
    detail=ev.detail or {}
    if ev.kind=='publication_published':
        platform={'instagram':'Instagram','tiktok':'TikTok','youtube':'YouTube Shorts'}.get(detail.get('platform'),'Платформа')
        text='✅ '+platform+' · @'+detail.get('username','')+'\nРолик опубликован.'
        if detail.get('url'):text+='\n'+detail['url']
    elif ev.kind in ('system_problem','system_recovered'):
        prefix='✅ Восстановлено: ' if ev.kind=='system_recovered' else '⚠️ '
        text=prefix+detail.get('message',LABELS.get(detail.get('code'),'Требуется проверка системы'))
        if detail.get('account'):text+=' · '+detail['account']
    else:
        labels={'worker_offline':'Нет связи с ПК','drive_unavailable':'Нет доступа к ролику на Google Диске',
          'content_shortage':'Для расписания не хватает новых роликов','insufficient_content':'Для расписания не хватает новых роликов','publication_failed':'Не удалось опубликовать ролик','unknown_publication':'Нужно проверить результат публикации','scheduler_error':'Ошибка планировщика',
          'publication_unknown':'Нужно проверить результат публикации'}
        text='⚠️ '+labels.get(ev.kind,'Заданию требуется внимание')
    return html.escape(text)+'\nОткрыть состояние: /panel'
