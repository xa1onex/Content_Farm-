import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import fcntl
import html
import json
import logging
import os
from pathlib import Path
import time
import uuid

from aiogram import Bot, Dispatcher, BaseMiddleware, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile

from config import BASE_DIR, JOB_DIR, MAX_QUEUE_SIZE, TELEGRAM_INPUT_BYTES, AUTOPOST_ENABLED
from drive_helper import archive_job, save_private_json
from processor import process_job, ProcessingError, TransientProcessingError
from autopost import register_job
from sources import parse_source
from operations import recovery_changes, report_loop

log = logging.getLogger(__name__)
dp = Dispatcher()
queue = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
busy_users = set()
scheduled = set()
user_locks = defaultdict(asyncio.Lock)
last_submit = {}
control = None

class PrivateChatMiddleware(BaseMiddleware):
    async def __call__(self,handler,event,data):
        if not event.from_user:
            return
        if event.chat.type!='private':
            await event.answer('Отправь сообщение в личный чат с ботом.')
            return
        return await handler(event,data)

dp.message.outer_middleware(PrivateChatMiddleware())

class ProgressReporter:
    def __init__(self,loop,message):
        self.loop,self.message = loop,message
        self.done = False
        self.last_edit = 0.0
        self.lock = asyncio.Lock()

    async def edit(self,text,final=False):
        async with self.lock:
            if (self.done and not final) or (not final and time.monotonic()-self.last_edit<3):
                return
            self.last_edit = time.monotonic()
            if self.message:
                try:
                    await self.message.edit_text(text)
                except TelegramAPIError:
                    pass

    def notify(self,text):
        asyncio.run_coroutine_threadsafe(self.edit(text),self.loop)

def load_state(job_dir):
    return json.loads((Path(job_dir)/'state.json').read_text())

def update_state(job_dir, **changes):
    state = load_state(job_dir)
    state.update(changes)
    state['updated_at']=datetime.now(timezone.utc).isoformat()
    save_private_json(Path(job_dir)/'state.json',state)
    return state

def user_jobs(user_id):
    jobs = []
    for path in JOB_DIR.glob('*/state.json'):
        try:
            state = json.loads(path.read_text())
            if state.get('user_id')==user_id:
                jobs.append((path.parent,state))
        except (OSError,ValueError):
            log.warning('Cannot read job state: %s',path)
    return sorted(jobs,key=lambda item:item[1].get('created',''),reverse=True)

async def reply(bot, chat_id, text, **kwargs):
    for attempt in range(3):
        try:
            return await bot.send_message(chat_id,text,**kwargs)
        except TelegramRetryAfter as exc:
            if attempt==2:
                raise
            await asyncio.sleep(min(exc.retry_after,60))

async def deliver(bot, job_dir):
    state = load_state(job_dir)
    manifest = json.loads((job_dir/'manifest.json').read_text())
    delivered = set(state.get('delivered_files',[]))
    errors = False
    for clip in manifest['clips']:
        if clip['file'] in delivered:
            continue
        local = job_dir/clip['file']
        record = state.get('drive_files',{}).get(clip['file'])
        if not local.exists() and record:
            from drive_helper import DriveStorage
            await asyncio.to_thread(lambda:DriveStorage().download(record['id'],local))
        caption = html.escape(clip['description'])
        if clip.get('id'):caption += '\nID: <code>'+html.escape(clip['id'])+'</code>'
        if record:
            caption += f'\n<a href="{html.escape(record["link"],quote=True)}">Сохранено на Google Диске</a>'
        try:
            for attempt in range(3):
                try:
                    sent = await bot.send_video(state['chat_id'],FSInputFile(local),
                        caption=caption,supports_streaming=True,width=1080,height=1920,
                        duration=round(clip['end_time']-clip['start_time']),request_timeout=300)
                    state.setdefault('telegram_files',{})[clip['file']] = sent.video.file_id
                    delivered.add(clip['file'])
                    state['delivered_files'] = sorted(delivered)
                    save_private_json(job_dir/'state.json',state)
                    break
                except TelegramRetryAfter as exc:
                    if attempt==2:
                        raise
                    await asyncio.sleep(min(exc.retry_after,60))
        except (TelegramAPIError,OSError) as exc:
            log.error('Delivery failed for job %s: %s',state['id'],type(exc).__name__)
            errors = True
    update_state(job_dir,delivered=not errors and len(delivered)==len(manifest['clips']))

async def download_telegram(bot,job_dir,source):
    incoming = job_dir/'source.incoming'
    try:
        await bot.download(source['value'],destination=incoming,timeout=120)
        if source.get('size') and incoming.stat().st_size!=source['size']:
            raise ProcessingError('Исходный файл Telegram скачался не полностью.')
        os.replace(incoming,job_dir/'source.mp4')
    except BaseException:
        incoming.unlink(missing_ok=True)
        raise

def clean_archived_job(job_dir):
    state = load_state(job_dir)
    if not state.get('archived') or not state.get('delivered') or (AUTOPOST_ENABLED and not state.get('registered')):
        return
    # State, links, manifest and captions remain for /files and recovery.
    for path in job_dir.iterdir():
        if path.is_file() and path.name not in {'state.json','manifest.json','autopost_requests.json'}:
            path.unlink(missing_ok=True)

async def worker(bot):
    loop = asyncio.get_running_loop()
    while True:
        job_dir = await queue.get()
        state = load_state(job_dir)
        status_message = None
        reporter = ProgressReporter(loop,None)
        try:
            update_state(job_dir,status='processing',error=None,stage='Создаю ролики')
            try:
                status_message = await reply(bot,state['chat_id'],f'⏳ Задание <code>{state["id"]}</code>: начинаю обработку.')
            except TelegramAPIError:
                log.warning('Cannot send status for %s',state['id'])
            reporter.message = status_message
            if not (job_dir/'manifest.json').exists():
                if state['source']['kind']=='telegram' and not (job_dir/'source.mp4').exists():
                    await download_telegram(bot,job_dir,state['source'])
                await asyncio.to_thread(process_job,job_dir,reporter.notify)
            update_state(job_dir,stage='Сохраняю ролики на Google Диск')
            reporter.notify('Сохраняю исходник, ролики и субтитры на Google Диске…')
            try:
                state = await asyncio.to_thread(archive_job,job_dir)
            except Exception as exc:
                log.exception('Archive failed for %s',state['id'])
                state = update_state(job_dir,archived=False,archive_error=type(exc).__name__)
            if state.get('archived') and AUTOPOST_ENABLED:
                update_state(job_dir,stage='Проверяю ролики для автопостинга')
                reporter.notify('Регистрирую ID роликов и проверяю доступ для автопостинга…')
                try:
                    await asyncio.to_thread(register_job,job_dir)
                except Exception as exc:
                    log.error('Registration failed for %s: %s',state['id'],type(exc).__name__)
                    update_state(job_dir,registered=False,autopost_error=type(exc).__name__)
            update_state(job_dir,stage='Доставляю результаты в Telegram')
            reporter.notify('Отправляю готовые ролики…')
            await deliver(bot,job_dir)
            state = load_state(job_dir)
            reporter.done = True
            if state.get('archived') and state.get('delivered') and (not AUTOPOST_ENABLED or state.get('registered')):
                update_state(job_dir,status='completed')
                clean_archived_job(job_dir)
                await reporter.edit('✅ Ролики отправлены. Исходник, субтитры и результаты сохранены на Google Диске.',final=True)
            else:
                retry_state=update_state(job_dir,**recovery_changes(state))
                issue = ('Google Диск пока недоступен' if not state.get('archived') else
                         'регистрация для автопостинга не завершена' if AUTOPOST_ENABLED and not state.get('registered') else
                         'не все ролики доставлены в Telegram')
                await reporter.edit(f'⚠️ Ролики созданы, но {issue}. Файлы сохранены на сервере.\n' +
                           ('Сохранение и доставка повторятся автоматически.' if retry_state['status']=='retry_wait' else f'Нужна проверка. Повтор: /retry {state["id"]}'),final=True)
        except asyncio.CancelledError:
            update_state(job_dir,status='interrupted')
            raise
        except TransientProcessingError as exc:
            count = state.get('auto_retry_count',0)+1
            reporter.done = True
            if count<=5:
                delay = [60,180,600,1800,3600][count-1]
                update_state(job_dir,status='retry_wait',error='gemini_temporarily_unavailable',
                             auto_retry_count=count,retry_at=time.time()+delay)
                await reporter.edit(f'⏳ Gemini временно перегружен. Исходник и расшифровка сохранены. '
                                    f'Повторю автоматически через {delay//60} мин. ID: <code>{state["id"]}</code>',final=True)
            else:
                update_state(job_dir,status='needs_retry',error='gemini_temporarily_unavailable',auto_retry_count=count)
                await reporter.edit(f'⚠️ Gemini пока недоступен. Все файлы сохранены. Повтор: /retry {state["id"]}',final=True)
        except Exception as exc:
            log.exception('Job %s failed',state['id'])
            if (job_dir/'manifest.json').exists():
                state=update_state(job_dir,error=type(exc).__name__,**recovery_changes(load_state(job_dir)))
            else:
                state=update_state(job_dir,status='failed',error=type(exc).__name__)
            reporter.done = True
            message = str(exc) if isinstance(exc,(ProcessingError,ValueError)) else 'Не удалось обработать видео. Подробности сохранены в журнале.'
            error_text = ('⏳ Ролики сохранены. Повторю доставку автоматически.' if state['status']=='retry_wait' else f'❌ {html.escape(message)}\nПовторить: /retry {state["id"]}')
            if status_message:
                await reporter.edit(error_text,final=True)
            else:
                try:
                    await reply(bot,state['chat_id'],error_text)
                except TelegramAPIError:
                    pass
        finally:
            busy_users.discard(state['user_id'])
            scheduled.discard(state['id'])
            queue.task_done()

@dp.message(CommandStart())
async def start(message:types.Message):
    if control:
        await control.menu(message)
        return
    await message.answer('Пришли ссылку на YouTube или видеофайл из папки бота на Google Диске. '
        'Можно также отправить видео в Telegram (до 20 МБ).\n\n'
        'Я выберу до трёх законченных моментов, сделаю вертикальные ролики с субтитрами '
        'и сохраню исходник и результаты на Google Диске.\n'
        '/status — твои задания\n/files — ссылки на твои результаты\n/retry ID — повторить задание')

@dp.message(Command('status'))
async def status(message:types.Message):
    jobs = user_jobs(message.from_user.id)[:5]
    if not jobs:
        await message.answer('У тебя пока нет заданий. Пришли видео или ссылку.')
        return
    labels = {'queued':'в очереди','processing':'обрабатывается','completed':'готово',
        'failed':'ошибка','needs_retry':'нужен повтор сохранения/отправки',
        'interrupted':'возобновляется','receiving':'получаю файл','retry_wait':'ожидает автоматического повтора'}
    await message.answer('\n'.join(f'<code>{s["id"]}</code>: {labels.get(s["status"],s["status"])}' for _,s in jobs))

@dp.message(Command('files'))
async def files(message:types.Message):
    lines = []
    for _,state in user_jobs(message.from_user.id)[:5]:
        for name,record in state.get('drive_files',{}).items():
            if name.startswith(('short_','CF-')) and name.endswith('.mp4'):
                lines.append(f'<a href="{html.escape(record["link"],quote=True)}">{state["id"]} · {name}</a>')
    await message.answer('\n'.join(lines) if lines else 'На Google Диске пока нет сохранённых результатов твоих заданий.')

@dp.message(Command('retry'))
async def retry(message:types.Message):
    parts = (message.text or '').split()
    if len(parts)!=2 or len(parts[1])!=12 or any(c not in '0123456789abcdef' for c in parts[1]):
        await message.answer('Используй /retry ID. ID задания можно найти через /status.')
        return
    async with user_locks[message.from_user.id]:
        job_dir = JOB_DIR/parts[1]
        if not (job_dir/'state.json').exists() or load_state(job_dir).get('user_id')!=message.from_user.id:
            await message.answer('Твоё задание с таким ID не найдено.')
            return
        if message.from_user.id in busy_users or parts[1] in scheduled:
            await message.answer('Твоё задание уже в очереди или обрабатывается.')
            return
        if queue.full():
            await message.answer('Очередь заполнена. Попробуй позже.')
            return
        if load_state(job_dir).get('status')=='completed':
            await message.answer('Задание уже сохранено и доставлено. Ссылки: /files')
            return
        update_state(job_dir,status='queued',retry_at=None,auto_retry_count=0)
        busy_users.add(message.from_user.id);scheduled.add(parts[1])
        queue.put_nowait(job_dir)
        await message.answer(f'Задание <code>{parts[1]}</code> добавлено в очередь.')

@dp.message()
async def submit(message:types.Message,bot:Bot,request_id=None):
    if not message.from_user or message.chat.type!='private':
        await message.answer('Отправь видео в личный чат с ботом.')
        return
    async with user_locks[message.from_user.id]:
        requested_id=uuid.UUID(request_id).hex[:12] if request_id else None
        if requested_id and (JOB_DIR/requested_id/'state.json').exists():
            previous=load_state(JOB_DIR/requested_id)
            if previous.get('miniapp_request_id')!=request_id or previous['user_id']!=message.from_user.id:raise RuntimeError('Request identity conflict')
            return {'job_id':requested_id,'status':previous['status']}
        if message.from_user.id in busy_users:
            await message.answer('У тебя уже есть задание в очереди или обработке. Посмотреть: /status')
            return
        if queue.full():
            await message.answer('Очередь заполнена. Попробуй позже.')
            return
        cooldown = 60-(time.monotonic()-last_submit.get(message.from_user.id,0))
        if cooldown>0:
            await message.answer(f'Следующее задание можно отправить через {math_ceil(cooldown)} сек.')
            return
        upload = message.video or message.document
        if upload:
            if not (upload.mime_type or '').startswith('video/'):
                await message.answer('Пришли видеофайл или ссылку.')
                return
            if not upload.file_size or upload.file_size>TELEGRAM_INPUT_BYTES:
                await message.answer('В Telegram можно загрузить до 20 МБ. Для большого исходника пришли ссылку YouTube или Google Диска.')
                return
            source = {'kind':'telegram','value':upload.file_id,'size':upload.file_size}
        else:
            try:
                kind,value = parse_source(message.text or '')
            except ValueError as exc:
                await message.answer(html.escape(str(exc)))
                return
            source = {'kind':kind,'value':value}
        job_id = requested_id or uuid.uuid4().hex[:12]
        job_dir = JOB_DIR/job_id
        job_dir.mkdir(parents=True,mode=0o700)
        state = {'id':job_id,'user_id':message.from_user.id,'chat_id':message.chat.id,
            'source':source,'status':'receiving','created':datetime.now(timezone.utc).isoformat(),
            'archived':False,'delivered':False,'miniapp_request_id':request_id}
        save_private_json(job_dir/'state.json',state)
        busy_users.add(message.from_user.id)
        try:
            if upload:
                await download_telegram(bot,job_dir,source)
            update_state(job_dir,status='queued')
            scheduled.add(job_id)
            queue.put_nowait(job_dir)
            last_submit[message.from_user.id]=time.monotonic()
        except Exception as exc:
            busy_users.discard(message.from_user.id)
            update_state(job_dir,status='failed',error=type(exc).__name__)
            await message.answer('Не удалось получить видео. Попробуй отправить ещё раз.')
            return
        await message.answer(f'Принято. Задание <code>{job_id}</code>, перед ним в очереди: {max(0,queue.qsize()-1)}.')
        return {'job_id':job_id,'status':'queued'}

def math_ceil(value):
    return int(value)+int(value>int(value))

async def delayed_retries():
    while True:
        for job_dir,state in user_jobs_all():
            if (state.get('status')=='retry_wait' and (state.get('retry_at') or 0)<=time.time()
                    and state['user_id'] not in busy_users and state['id'] not in scheduled and not queue.full()):
                update_state(job_dir,status='queued')
                busy_users.add(state['user_id']);scheduled.add(state['id'])
                queue.put_nowait(job_dir)
        await asyncio.sleep(5)

async def main():
    global control
    panel_config=Path(os.getenv('TELEGRAM_EXTENSION_CONFIG',str(BASE_DIR/'telegram_admin.json')))
    if panel_config.exists():
        try:
            from telegram_admin_extension import install
        except ModuleNotFoundError as exc:
            if exc.name!='telegram_admin_extension':raise
            import sys
            sys.path.insert(0,str(BASE_DIR.parent/'server'))
            from app.telegram_admin.extension import install
        control=install(dp,str(panel_config))
        control.source_handler=submit
    token = os.getenv('BOT_TOKEN')
    if not token:
        raise RuntimeError('BOT_TOKEN is not set')
    JOB_DIR.mkdir(parents=True,exist_ok=True)
    lock = open(BASE_DIR/'.bot.lock','w')
    try:
        fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('Another bot instance is already running')
    bot = Bot(token=token,default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    task = asyncio.create_task(worker(bot))
    retry_task = None
    operations_task=asyncio.create_task(report_loop())
    try:
        await bot.set_my_commands([
            types.BotCommand(command='panel',description='Состояние и управление системой'),
            types.BotCommand(command='start',description='Как пользоваться ботом'),
            types.BotCommand(command='status',description='Мои задания'),
            types.BotCommand(command='files',description='Мои результаты на Google Диске'),
            types.BotCommand(command='retry',description='Повторить задание по ID')])
        for job_dir,state in reversed(user_jobs_all()):
            if state.get('status') in {'receiving','queued','processing','interrupted'}:
                busy_users.add(state['user_id']);scheduled.add(state['id'])
                await queue.put(job_dir)
        retry_task = asyncio.create_task(delayed_retries())
        await dp.start_polling(bot,close_bot_session=False)
    finally:
        operations_task.cancel()
        try:await operations_task
        except asyncio.CancelledError:pass
        if retry_task:
            retry_task.cancel()
            try:await retry_task
            except asyncio.CancelledError:pass
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await bot.session.close()
        lock.close()

def user_jobs_all():
    jobs = []
    for path in JOB_DIR.glob('*/state.json'):
        try:
            jobs.append((path.parent,json.loads(path.read_text())))
        except (OSError,ValueError):
            log.warning('Invalid state: %s',path)
    return sorted(jobs,key=lambda item:item[1].get('created',''),reverse=True)

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    # HTTP libraries can otherwise put credential-bearing URLs into debug output.
    logging.getLogger('httpx').setLevel(logging.WARNING)
    asyncio.run(main())
