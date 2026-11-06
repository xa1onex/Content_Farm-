import asyncio,json
from aiogram import types,Dispatcher
from app.telegram_admin.extension import AdminExtension,AdminMiddleware,install
from app.db import now

def message(text='/ap',uid=123):
    return types.Message(message_id=1,date=now(),chat=types.Chat(id=uid,type='private'),
        from_user=types.User(id=uid,is_bot=False,first_name='Test'),text=text)

def test_explicit_all_users_and_allowlist():
    assert AdminExtension({'allow_all':True}).allowed(message(uid=456))
    assert not AdminExtension({'admin_ids':[123]}).allowed(message(uid=456))
    assert AdminExtension({'admin_ids':[123]}).allowed(message())

def test_extension_preserves_existing_bot_handlers():
    extension=AdminExtension({'allow_all':True})
    middleware=AdminMiddleware(extension)
    called=[]
    async def legacy(event,data):called.append(event.text);return 'legacy'
    result=asyncio.run(middleware(legacy,message('https://youtube.com/watch?v=123'),{}))
    assert result=='legacy' and called

def test_mutation_requires_confirmation(monkeypatch):
    extension=AdminExtension({'allow_all':True})
    calls=[];answers=[]
    async def api(*args,**kwargs):calls.append(args);return {}
    async def answer(self,text,**kwargs):answers.append((text,kwargs))
    monkeypatch.setattr(extension,'api',api)
    monkeypatch.setattr(types.Message,'answer',answer)
    monkeypatch.setattr(types.Message,'edit_text',answer)
    asyncio.run(extension.message(message('/ap worker_add {"name":"Worker A"}')))
    assert calls==[] and len(extension.pending)==1
    pending=next(iter(extension.pending.values()))
    assert pending['user']==123 and pending['path']=='/api/v1/workers'
    assert 'Подтвердить' in str(answers[0][1]['reply_markup'])

def test_install_does_not_add_polling_consumer(tmp_path):
    config=tmp_path/'telegram.json';config.write_text(json.dumps({'api_url':'http://127.0.0.1:8108','api_token':'test','allow_all':True}))
    dispatcher=Dispatcher();install(dispatcher,str(config))
    assert dispatcher.startup.handlers and dispatcher.shutdown.handlers
    assert 'callback_query' in dispatcher.resolve_used_update_types()

def test_panel_button_reaches_handler_through_dispatcher(tmp_path,monkeypatch):
    from aiogram import Bot
    config=tmp_path/'telegram.json';config.write_text(json.dumps({'allow_all':True}))
    dispatcher=Dispatcher();install(dispatcher,str(config));answers=[];acks=[]
    async def answer(self,text,**kwargs):answers.append(text)
    async def ack(self,*args,**kwargs):acks.append(self.id)
    monkeypatch.setattr(types.Message,'answer',answer)
    monkeypatch.setattr(types.Message,'edit_text',answer)
    monkeypatch.setattr(types.CallbackQuery,'answer',ack)
    query=types.CallbackQuery(id='button-test',from_user=message().from_user,
        chat_instance='private-chat',message=message(),data='ap:create')
    async def run():
        bot=Bot('123:test-token')
        try:await dispatcher.feed_update(bot,types.Update(update_id=1,callback_query=query))
        finally:await bot.session.close()
    asyncio.run(run())
    assert acks==['button-test'] and len(answers)==1
    assert 'Пришли в этот чат' in answers[0]

def test_panel_does_not_swallow_other_callback_handlers(tmp_path,monkeypatch):
    from aiogram import Bot,F
    config=tmp_path/'telegram.json';config.write_text(json.dumps({'allow_all':True}))
    dispatcher=Dispatcher();install(dispatcher,str(config));seen=[]
    async def legacy(query):seen.append(query.data)
    dispatcher.callback_query.register(legacy,F.data=='legacy:ping')
    query=types.CallbackQuery(id='legacy-test',from_user=message().from_user,
        chat_instance='private-chat',message=message(),data='legacy:ping')
    async def run():
        bot=Bot('123:test-token')
        try:await dispatcher.feed_update(bot,types.Update(update_id=2,callback_query=query))
        finally:await bot.session.close()
    asyncio.run(run())
    assert seen==['legacy:ping']


def test_panel_reports_readiness_without_raw_json(monkeypatch):
    extension=AdminExtension({'allow_all':True});answers=[]
    async def api(*args,**kwargs):return {'farm':{'online':True},'scheduler':{'online':True,'enabled':True},
        'ready_clips':9,'accounts':[{'ready':False}],'published_24h':0,
        'problems':[{'message':'Нет связи с ПК','account':'example'}]}
    async def answer(self,text,**kwargs):answers.append(text)
    monkeypatch.setattr(extension,'api',api);monkeypatch.setattr(types.Message,'answer',answer)
    asyncio.run(extension.dashboard(message()))
    assert 'Готовых роликов: 9' in answers[0]
    assert 'Нет связи с ПК · example' in answers[0]
    assert '<pre>' not in answers[0]

def test_callback_navigation_edits_existing_message(monkeypatch):
    extension=AdminExtension({'allow_all':True});edits=[];new=[]
    async def edit(self,text,**kwargs):edits.append((self.message_id,text));return self
    async def answer(self,text,**kwargs):new.append(text);return self
    async def ack(self,*args,**kwargs):pass
    monkeypatch.setattr(types.Message,'edit_text',edit);monkeypatch.setattr(types.Message,'answer',answer)
    monkeypatch.setattr(types.CallbackQuery,'answer',ack)
    query=types.CallbackQuery(id='nav',from_user=message().from_user,chat_instance='private',message=message(),data='ap:create')
    asyncio.run(extension.callback(query));asyncio.run(extension.callback(query))
    assert len(edits)==2 and not new
    assert all(x[0]==1 for x in edits)

def test_repeated_panel_command_reuses_anchor(monkeypatch):
    extension=AdminExtension({'allow_all':True});new=[];edits=[]
    async def api(*a,**k):return {}
    async def answer(self,text,**kwargs):new.append(text);return self
    async def edit(self,text,**kwargs):edits.append(text);return self
    monkeypatch.setattr(extension,'api',api);monkeypatch.setattr(types.Message,'answer',answer);monkeypatch.setattr(types.Message,'edit_text',edit)
    asyncio.run(extension.menu(message()));asyncio.run(extension.menu(message()))
    assert len(new)==1 and len(edits)==1

def test_unchanged_panel_does_not_create_extra_message(monkeypatch):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import EditMessageText
    from app.telegram_admin.extension import PanelMessage
    async def edit(self,text,**kwargs):raise TelegramBadRequest(method=EditMessageText(chat_id=123,message_id=1,text=text),message='message is not modified')
    monkeypatch.setattr(types.Message,'edit_text',edit)
    target=message();assert asyncio.run(PanelMessage(target).answer('same'))==target

def test_panel_sets_personal_webapp_menu(monkeypatch):
    from aiogram import Bot
    extension=AdminExtension({'allow_all':True,'miniapp_url':'https://example.org/miniapp'})
    calls=[]
    async def api(*args,**kwargs):return {}
    async def answer(self,*args,**kwargs):return self
    async def set_menu(self,**kwargs):calls.append(kwargs);return True
    monkeypatch.setattr(extension,'api',api)
    monkeypatch.setattr(types.Message,'answer',answer)
    monkeypatch.setattr(Bot,'set_chat_menu_button',set_menu)
    async def run():
        bot=Bot('123:test-token')
        try:await extension.menu(message().as_(bot))
        finally:await bot.session.close()
    asyncio.run(run())
    assert calls[0]['chat_id']==123
    assert calls[0]['menu_button'].web_app.url=='https://example.org/miniapp'
