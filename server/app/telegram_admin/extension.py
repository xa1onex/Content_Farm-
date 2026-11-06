"""Standalone aiogram 3 extension. It needs only aiogram and the Python standard library.
install(dispatcher, '/etc/shortpost/telegram.json') registers panel callbacks and
message middleware without starting another getUpdates consumer.
"""
import asyncio,html,json,secrets,time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from aiogram import BaseMiddleware,F,types

class AdminExtension:
    def __init__(self,config):
        self.config=config
        self.pending={}
        self.forms={}
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
        await message.answer('Контентферма и автопостинг\nПришли видео или ссылку — я создам ролики и передам их в очередь публикаций.',
            reply_markup=self.buttons([[('Состояние системы','overview'),('Создать ролики','create')],
                [('Контентферма','farm'),('Автопостинг','scheduler')],
                [('Аккаунты','accounts:0'),('Ролики','content:0')],
                [('ПК и эмуляторы','fleet'),('Проблемы','problems')],
                [('Очередь','jobs:0'),('Результаты','publications:0')]]))
    async def dashboard(self,message,section='overview'):
        data=await self.api('GET','/api/v1/operations/overview')
        if 'error' in data:
            await message.answer('Не удалось получить состояние сервера. Попробую при следующем обновлении.');return
        farm=data['farm'];sched=data['scheduler'];lines=[]
        if section=='overview':
            lines=['Состояние системы',
              'Контентферма: '+('работает' if farm['online'] else 'нет связи'),
              'Планировщик: '+('нет связи' if not sched['online'] else 'работает' if sched['enabled'] else 'пауза'),
              f"Готовых роликов: {data['ready_clips']}",
              f"Готовых аккаунтов: {sum(a['ready'] for a in data['accounts'])} из {len(data['accounts'])}",
              f"Публикаций за 24 часа: {data['published_24h']}",
              'Разгон аккаунтов: 1 → 1 → 2 → 3 ролика/день',
              'Один ролик — один раз на каждой платформе.']
            if data['problems']:lines+=['','Требует внимания:']+[p['message']+(' · '+p['account'] if p.get('account') else '') for p in data['problems'][:5]]
            pool=data.get('available_by_platform',{})
            if pool:lines+=['Свободно: Instagram '+str(pool.get('instagram',0))+' · TikTok '+str(pool.get('tiktok',0))+' · Shorts '+str(pool.get('youtube',0))]
        elif section=='farm':
            counts=farm.get('counts',{})
            lines=['Контентферма: '+('работает' if farm['online'] else 'нет связи'),
              'Создаю: '+str(counts.get('processing',0)), 'В очереди: '+str(counts.get('queued',0)),
              'Ожидают автоматического повтора: '+str(counts.get('retry_wait',0)),
              'Завершено: '+str(counts.get('completed',0)),
              'С ошибкой: '+str(counts.get('failed',0)+counts.get('needs_retry',0)),
              'Google Диск: '+('доступен для сохранения' if farm.get('drive',{}).get('ready') else 'проверяется или требует внимания'),
              'Мои задания: /status · Мои файлы: /files']
            for job in farm.get('active',[])[:5]:lines.append(job.get('stage','Обработка видео'))
        elif section=='fleet':
            lines=['ПК и эмуляторы']
            for worker in data['workers']:
                lines.append(worker['name']+': '+('на связи' if worker['online'] else 'нет связи')+(' · автопостинг запущен' if worker['running'] else ' · автопостинг остановлен'))
            for device in data['devices']:
                health=device['health'];network=health.get('network',{})
                lines.append(device['name']+': '+('доступен' if health.get('online') and device.get('fresh') else 'нет свежей проверки'))
                lines.append('Прокси: '+('проверен' if network.get('ready') and device.get('network_fresh') else 'нет успешной свежей проверки'))
        else:
            lines=['Текущие проблемы']+[p['message']+(' · '+p['account'] if p.get('account') else '') for p in data['problems']]
            if not data['problems']:lines.append('Всё работает.')
        await message.answer(html.escape('\n'.join(lines))[:3900],parse_mode='HTML',reply_markup=self.buttons([[('Обновить',section),('Меню','menu')]]))
    async def show(self,message,section,offset=0):
        paths={'report':'analytics/report','settings':'settings'}
        path='/api/v1/'+paths.get(section,section)
        if section not in ('report','settings'):path+=f'?limit=8&offset={offset}'
        result=await self.api('GET',path)
        rows=[];lines=[]
        if section=='accounts':rows.append([('Добавить аккаунт','add_account')])
        names={'accounts':'Аккаунты','content':'Готовые ролики','jobs':'Очередь публикаций',
          'publications':'Опубликовано','schedules':'Расписание','events':'Последние события','workers':'ПК','devices':'Эмуляторы'}
        statuses={'active':'работает','paused':'пауза','attention':'нужна проверка','ready':'готов',
          'scheduled':'запланировано','assigned':'передано на ПК','processing':'публикуется',
          'published':'опубликовано','failed':'ошибка','unknown':'результат требует проверки',
          'retry_wait':'ожидает повтора','cancelled':'отменено','draft':'проверяется'}
        if 'error' in result:
            await message.answer('Не удалось получить данные сервера.');return
        lines.append(names.get(section,'Система'))
        for item in result.get('items',[]):
            if section=='accounts':
                lines.append(item['platform']+' · @'+item['username']+' · '+statuses.get(item['status'],item['status'])+f" · сегодня {item.get('effective_daily_limit',item['daily_limit'])}/день")
                rows.append([('@'+item['username']+' · управление','account_view:'+item['id'])])
            elif section=='content':
                lines.append(item['title'][:180]+' · '+statuses.get(item['status'],item['status']))
            elif section=='jobs':
                lines.append(statuses.get(item['status'],item['status'])+' · '+item['scheduled_at'][:16].replace('T',' ')+' UTC')
                if item['status'] in ('failed','paused','retry_wait','scheduled','pending'):
                    rows.append([('Отменить задание '+item['id'][:6],'job:'+item['id'])])
            elif section=='publications':
                lines.append(item.get('url') or 'Публикация подтверждена')
            elif section=='events':
                lines.append(item.get('detail',{}).get('message','Событие системы: '+item['kind']))
            elif section in ('workers','devices'):
                lines.append(item['name'])
            elif section=='schedules':lines.append(item['local_date']+' · расписание создано')
        if not result.get('items'):lines.append('Пока нет записей.')
        if section not in ('report','settings'):
            rows.append([('Назад',f'{section}:{max(0,offset-8)}'),('Далее',f'{section}:{offset+8}')])
        rows.append([('Меню','menu')])
        await message.answer(html.escape('\n'.join(lines))[:3900],parse_mode='HTML',reply_markup=self.buttons(rows))
    async def review(self,message,user_id,method,path,body):
        stamp=time.monotonic()
        self.pending={k:v for k,v in self.pending.items() if v['expires']>stamp}
        if len(self.pending)>=1000:
            await message.answer('Слишком много ожидающих подтверждений. Попробуйте позже.');return
        key=secrets.token_hex(12)
        self.pending[key]={'user':user_id,'method':method,'path':path,'body':body,
            'expires':stamp+600,'idempotency_key':secrets.token_hex(16)}
        if path.endswith('/control'):preview='Включить автоматическое расписание?' if body.get('enabled') else 'Приостановить новые публикации? Уже начатые завершатся.'
        elif path.endswith('/generate'):preview='Обновить расписание из готовых роликов?'
        elif '/accounts/' in path:preview='Сохранить настройки аккаунта: '+', '.join(('публикаций в день: '+str(v)) if k=='daily_limit' else ('состояние: '+('работает' if v=='active' else 'пауза')) for k,v in body.items())
        elif path.endswith('/action'):preview='Отменить это задание?'
        else:preview='Сохранить изменения?'
        await message.answer(html.escape(preview),parse_mode='HTML',
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
    async def form_message(self,message):
        form=self.forms.pop(message.from_user.id,None)
        if not self.allowed(message) or not form or form['expires']<time.monotonic():return
        username=(message.text or '').strip().lstrip('@')
        import re
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',username):
            await message.answer('Имя не распознано. Открой «Аккаунты → Добавить аккаунт» и укажи имя без ссылки.');return
        result=await self.api('POST','/api/v1/accounts',{'name':username,'username':username,'platform':form['platform'],
          'worker_id':form['worker_id'],'device_id':form['device_id'],'status':'paused','daily_limit':3,'warmup_enabled':True,'timezone':'Europe/Moscow'})
        await message.answer('Аккаунт добавлен. Его готовность видна в состоянии системы.' if 'error' not in result else 'Не удалось добавить аккаунт: возможно, он уже привязан к этому эмулятору.',reply_markup=self.buttons([[('Состояние','overview'),('Аккаунты','accounts:0')]]))
    async def message(self,message):
        if not self.allowed(message):
            await message.answer('Доступ запрещён.');return
        text=message.text or ''
        parts=text.split(maxsplit=1)
        command=parts[1] if len(parts)>1 else ''
        if not command or command=='menu':await self.menu(message);return
        if command in ('overview','farm','fleet','problems'):
            await self.dashboard(message,command);return
        if command=='help':
            await message.answer('Открыть управление: /panel\nСоздать ролики: пришли видео или ссылку.\nМои задания: /status\nМои файлы: /files\nВ меню можно управлять аккаунтами, расписанием и получать сообщения о проблемах.');return
        if command=='notify_off':
            await self.review(message,message.from_user.id,'POST','/api/v1/telegram/subscriptions',{'chat_id':message.chat.id,'enabled':False});return
        if command in ('report','accounts','content','jobs','workers','devices','events','settings','schedules','publications'):
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
        if payload=='add_account':
            await message.answer('Выбери платформу.',reply_markup=self.buttons([[('Instagram','add_platform:instagram'),('TikTok','add_platform:tiktok'),('YouTube','add_platform:youtube')]]));return
        if payload.startswith('add_platform:'):
            platform=payload.split(':')[1]
            devices=await self.api('GET','/api/v1/devices?limit=100')
            self.forms[query.from_user.id]={'platform':platform,'expires':time.monotonic()+600}
            rows=[[(d['name'],'add_device:'+d['id'])] for d in devices.get('items',[]) if d['enabled']]
            await message.answer('Выбери эмулятор, где выполнен вход в этот аккаунт.' if rows else 'Сначала нужно подключить эмулятор.',reply_markup=self.buttons(rows+[[('Меню','menu')]]));return
        if payload.startswith('add_device:'):
            form=self.forms.get(query.from_user.id)
            if not form or form['expires']<time.monotonic():await message.answer('Начни добавление аккаунта заново.');return
            devices=await self.api('GET','/api/v1/devices?limit=100')
            selected=next((d for d in devices.get('items',[]) if d['id']==payload.split(':')[1]),None)
            if not selected:await message.answer('Эмулятор недоступен.');return
            form.update(device_id=selected['id'],worker_id=selected['worker_id'])
            from aiogram.types import ForceReply
            sent=await message.answer('Напиши имя аккаунта без ссылки, например snezniy_mir. Аккаунт будет добавлен на паузе до проверки.',reply_markup=ForceReply(selective=True))
            form['prompt_id']=sent.message_id
            return
        if payload=='create':
            await message.answer('Пришли сюда ссылку YouTube, ссылку на видео в нашей папке Google Диска или видеофайл до 20 МБ. Создание роликов, сохранение и передача в автопостинг выполняются автоматически.');return
        if payload in ('overview','farm','fleet','problems'):
            await self.dashboard(message,payload);return
        if payload.startswith('account_view:'):
            identifier=payload.split(':',1)[1]
            account=await self.api('GET','/api/v1/accounts/'+identifier)
            if 'error' in account:await message.answer('Аккаунт недоступен.');return
            await message.answer(html.escape('@'+account['username']+' · '+account['platform']+f"\nСегодня: {account.get('effective_daily_limit',account['daily_limit'])} роликов\nРазгон: 1 → 1 → 2 → 3\nЧасовой пояс: {account['timezone']}"),reply_markup=self.buttons([[('Пауза' if account['status']=='active' else 'Включить','account:'+identifier)],[('Максимум 1','limit:'+identifier+':1'),('Максимум 2','limit:'+identifier+':2'),('Максимум 3','limit:'+identifier+':3')],[('Аккаунты','accounts:0')]]));return
        if payload.startswith('limit:'):
            _,identifier,limit=payload.split(':')
            await self.review(message,query.from_user.id,'PATCH','/api/v1/accounts/'+identifier,{'daily_limit':int(limit)});return
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
            await message.answer('Не удалось сохранить изменения: '+html.escape(str(result['error']))[:500] if 'error' in result else 'Готово. Изменения сохранены.',reply_markup=self.buttons([[('Состояние','overview'),('Меню','menu')]]));return
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
        if section in ('report','accounts','content','jobs','workers','devices','events','settings','schedules','publications'):
            await self.show(message,section,int(args[0]) if args else 0)

class AdminMiddleware(BaseMiddleware):
    def __init__(self,extension):self.extension=extension
    async def __call__(self,handler,event,data):
        if isinstance(event,types.Message) and event.from_user and event.reply_to_message:
            form=self.extension.forms.get(event.from_user.id)
            if form and form.get('prompt_id')==event.reply_to_message.message_id:return await self.extension.form_message(event)
        if isinstance(event,types.Message) and event.text and event.text.split()[0].split('@')[0] in ('/ap','/autopost','/panel'):
            return await self.extension.message(event)
        if isinstance(event,types.CallbackQuery) and event.data and event.data.startswith('ap:'):
            return await self.extension.callback(event)
        return await handler(event,data)

def install(dispatcher,config_path='/etc/shortpost/telegram.json'):
    config=json.loads(Path(config_path).read_text())
    extension=AdminExtension(config)
    middleware=AdminMiddleware(extension)
    dispatcher.message.outer_middleware(middleware)
    # A real handler makes aiogram include callback_query in allowed_updates.
    # Middleware alone is invisible to resolve_used_update_types().
    dispatcher.callback_query.register(extension.callback,F.data.startswith('ap:'))
    dispatcher.startup.register(extension.startup)
    dispatcher.shutdown.register(extension.shutdown)
    return extension
