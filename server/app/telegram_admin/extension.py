"""Standalone aiogram 3 extension. It needs only aiogram and the Python standard library.
install(dispatcher, '/etc/shortpost/telegram.json') attaches middleware without changing
existing message handlers and without starting another getUpdates consumer.
"""
import asyncio,html,json,secrets,time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from aiogram import BaseMiddleware,types

class AdminExtension:
    def __init__(self,config):
        self.config=config
        self.pending={}
    def allowed(self,event):
        user=event.from_user
        chat=event.chat if isinstance(event,types.Message) else (event.message.chat if event.message else None)
        return user and chat and chat.type=='private' and (
            self.config.get('allow_all',False) or user.id in self.config.get('admin_ids',[]))
    async def api(self,method,path,body=None,key=None):
        payload=json.dumps(body).encode() if body is not None else (b'{}' if method=='POST' else None)
        headers={'Authorization':'Bearer '+self.config['api_token'],'Content-Type':'application/json'}
        if method in ('POST','PATCH','DELETE'):headers['Idempotency-Key']=key or secrets.token_hex(16)
        def run():
            req=Request(self.config['api_url'].rstrip('/')+path,data=payload,headers=headers,method=method)
            try:
                with urlopen(req,timeout=45) as res:return json.load(res)
            except HTTPError as exc:
                try:detail=json.loads(exc.read()).get('detail',str(exc.code))
                except Exception:detail=str(exc.code)
                return {'error':str(detail),'http_status':exc.code}
            except (URLError,TimeoutError):return {'error':'Backend недоступен; результат операции проверьте по тому же ключу запроса.'}
        return await asyncio.to_thread(run)
    def buttons(self,rows):
        return types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text=label,callback_data='ap:'+value) for label,value in row] for row in rows])
    async def menu(self,message):
        await self.api('POST','/api/v1/telegram/subscriptions',{'chat_id':message.chat.id,'enabled':True})
        mode='Доступ: временно всем пользователям.' if self.config.get('allow_all') else 'Доступ: разрешённые Telegram ID.'
        await message.answer('Автопостинг. '+mode+'\nУведомления включены для этого чата. /ap help — команды.',
            reply_markup=self.buttons([[('Статистика','report'),('Аккаунты','accounts:0')],
                [('Контент','content:0'),('Расписание','schedules:0')],[('Очередь','jobs:0'),('Workers','workers:0')],
                [('Устройства','devices:0'),('Ошибки','events:0')],[('Настройки','settings'),('Планировщик','scheduler')]]))
    async def show(self,message,section,offset=0):
        paths={'report':'analytics/report','settings':'settings'}
        path='/api/v1/'+paths.get(section,section)
        if section not in ('report','settings'):path+=f'?limit=8&offset={offset}'
        result=await self.api('GET',path)
        text=json.dumps(result,ensure_ascii=False,indent=2)
        rows=[]
        if section=='accounts':
            for item in result.get('items',[]):
                rows.append([(item['username']+' — '+item['status'],'account:'+item['id'])])
        if section=='jobs':
            for item in result.get('items',[]):
                if item['status'] not in ('published','assigned','processing'):
                    rows.append([(item['id'][:8]+' — '+item['status'],'job:'+item['id'])])
        if section not in ('report','settings'):
            rows.append([('Назад',f'{section}:{max(0,offset-8)}'),('Далее',f'{section}:{offset+8}')])
        rows.append([('Меню','menu')])
        await message.answer('<pre>'+html.escape(text[:3300])+'</pre>',parse_mode='HTML',reply_markup=self.buttons(rows))
    async def review(self,message,user_id,method,path,body):
        stamp=time.monotonic()
        self.pending={k:v for k,v in self.pending.items() if v['expires']>stamp}
        if len(self.pending)>=1000:
            await message.answer('Слишком много ожидающих подтверждений. Попробуйте позже.');return
        key=secrets.token_hex(12)
        self.pending[key]={'user':user_id,'method':method,'path':path,'body':body,
            'expires':stamp+600,'idempotency_key':secrets.token_hex(16)}
        preview=json.dumps(body,ensure_ascii=False,indent=2)
        await message.answer(html.escape(method+' '+path)+'\n<pre>'+html.escape(preview[:3000])+'</pre>',parse_mode='HTML',
            reply_markup=self.buttons([[('Подтвердить','confirm:'+key),('Отмена','discard:'+key)]]))
    async def notifications(self,bot):
        while True:
            try:
                result=await self.api('POST','/api/v1/telegram/notifications/claim',{})
                delivery=result.get('delivery')
                if delivery:
                    from aiogram.exceptions import TelegramAPIError
                    delivered=False
                    try:
                        await bot.send_message(delivery['chat_id'],delivery['text'])
                        delivered=True
                    except TelegramAPIError:pass
                    await self.api('POST',f"/api/v1/telegram/notifications/{delivery['event_id']}/{delivery['chat_id']}/result",
                        {'attempt':delivery['attempt'],'delivered':delivered})
            except Exception:
                import logging
                logging.getLogger(__name__).warning('autopost_notification_failed')
            await asyncio.sleep(5)
    async def startup(self,bot,**kwargs):
        self.notification_task=asyncio.create_task(self.notifications(bot))
    async def shutdown(self,**kwargs):
        task=getattr(self,'notification_task',None)
        if task:
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass
    async def message(self,message):
        if not self.allowed(message):
            await message.answer('Доступ запрещён.');return
        text=message.text or ''
        parts=text.split(maxsplit=1)
        command=parts[1] if len(parts)>1 else ''
        if not command or command=='menu':await self.menu(message);return
        if command=='help':
            await message.answer('Команды:\n/ap menu\n/ap report\n/ap accounts\n/ap content\n/ap jobs\n/ap workers\n/ap devices\n/ap events\n/ap settings\n'
                '/ap account_add {JSON AccountIn}\n/ap account_edit UUID {JSON AccountPatch}\n'
                '/ap worker_add {"name":"Worker A","max_parallel":10}\n/ap device_add {JSON DeviceIn}\n'
                '/ap job_action UUID {JSON JobAction}\n/ap schedule_generate\n/ap scheduler_on\n/ap scheduler_off\n'
                '/ap notify_off\n/ap api POST|PATCH /api/v1/... {JSON}\n'
                'Любое изменение требует кнопки подтверждения. JSON-схемы: /openapi.json и WORKER_API_SPEC.md.');return
        if command=='notify_off':
            await self.review(message,message.from_user.id,'POST','/api/v1/telegram/subscriptions',{'chat_id':message.chat.id,'enabled':False});return
        if command in ('report','accounts','content','jobs','workers','devices','events','settings','schedules'):
            await self.show(message,command);return
        if command in ('scheduler_on','scheduler_off','schedule_generate'):
            path='/api/v1/schedules/'+('generate' if command=='schedule_generate' else 'control')
            body={} if command=='schedule_generate' else {'enabled':command=='scheduler_on'}
            await self.review(message,message.from_user.id,'POST',path,body);return
        try:
            name,*args=command.split(maxsplit=1)
            rest=args[0] if args else ''
            if name in ('account_add','worker_add','device_add'):
                path='/api/v1/'+{'account_add':'accounts','worker_add':'workers','device_add':'devices'}[name]
                method='POST';body=json.loads(rest)
            elif name in ('account_edit','job_action'):
                identifier,payload=rest.split(maxsplit=1)
                from uuid import UUID
                identifier=str(UUID(identifier))
                path=f'/api/v1/accounts/{identifier}' if name=='account_edit' else f'/api/v1/jobs/{identifier}/action'
                method='PATCH' if name=='account_edit' else 'POST';body=json.loads(payload)
            elif name=='api':
                method,path,payload=rest.split(maxsplit=2)
                if method not in ('POST','PATCH') or not path.startswith('/api/v1/') or '..' in path or '?' in path:
                    raise ValueError('Invalid method/path')
                body=json.loads(payload)
            else:raise ValueError('Unknown command')
            await self.review(message,message.from_user.id,method,path,body)
        except (ValueError,TypeError):await message.answer('Некорректная команда или JSON. /ap help')
    async def callback(self,query):
        if not self.allowed(query):await query.answer('Доступ запрещён',show_alert=True);return
        await query.answer()
        payload=(query.data or '')[3:]
        message=query.message
        if payload=='menu':await self.menu(message);return
        if payload=='scheduler':
            await message.answer('Планировщик: остановка не прерывает задания, уже принятые Worker.',
                reply_markup=self.buttons([[('Запустить','scheduler_on'),('Остановить','scheduler_off')],[('Сформировать','generate')]]));return
        if payload in ('scheduler_on','scheduler_off','generate'):
            await self.review(message,query.from_user.id,'POST','/api/v1/schedules/'+('generate' if payload=='generate' else 'control'),
                {} if payload=='generate' else {'enabled':payload=='scheduler_on'});return
        if payload.startswith(('confirm:','discard:')):
            key=payload.split(':',1)[1];item=self.pending.get(key)
            if not item or item['expires']<time.monotonic() or item['user']!=query.from_user.id:
                await message.answer('Подтверждение истекло или принадлежит другому пользователю.');return
            if payload.startswith('discard:'):
                self.pending.pop(key,None);await message.answer('Отменено.');return
            result=await self.api(item['method'],item['path'],item['body'],item['idempotency_key'])
            if 'error' not in result:self.pending.pop(key,None)
            await message.answer('<pre>'+html.escape(json.dumps(result,ensure_ascii=False,indent=2)[:3500])+'</pre>',parse_mode='HTML');return
        if payload.startswith('account:'):
            identifier=payload.split(':',1)[1]
            account=await self.api('GET','/api/v1/accounts/'+identifier)
            status='paused' if account.get('status')=='active' else 'active'
            await self.review(message,query.from_user.id,'PATCH','/api/v1/accounts/'+identifier,{'status':status});return
        if payload.startswith('job:'):
            identifier=payload.split(':',1)[1]
            job=await self.api('GET','/api/v1/jobs/'+identifier)
            if job.get('status')=='unknown':
                await message.answer('Результат неизвестен. Проверьте аккаунт и используйте /ap job_action UUID с resolve_published либо resolve_not_published и reason.');return
            await self.review(message,query.from_user.id,'POST','/api/v1/jobs/'+identifier+'/action',{'action':'cancel','reason':'Telegram operator cancellation'});return
        section,*args=payload.split(':')
        if section in ('report','accounts','content','jobs','workers','devices','events','settings','schedules'):
            await self.show(message,section,int(args[0]) if args else 0)

class AdminMiddleware(BaseMiddleware):
    def __init__(self,extension):self.extension=extension
    async def __call__(self,handler,event,data):
        if isinstance(event,types.Message) and event.text and event.text.split()[0].split('@')[0] in ('/ap','/autopost'):
            return await self.extension.message(event)
        if isinstance(event,types.CallbackQuery) and event.data and event.data.startswith('ap:'):
            return await self.extension.callback(event)
        return await handler(event,data)

def install(dispatcher,config_path='/etc/shortpost/telegram.json'):
    config=json.loads(Path(config_path).read_text())
    extension=AdminExtension(config)
    middleware=AdminMiddleware(extension)
    dispatcher.message.outer_middleware(middleware)
    dispatcher.callback_query.outer_middleware(middleware)
    dispatcher.startup.register(extension.startup)
    dispatcher.shutdown.register(extension.shutdown)
    return extension
